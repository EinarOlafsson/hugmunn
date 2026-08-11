"""Main window: model/server controls on the left, conversation on the right."""

from __future__ import annotations

import html as html_mod
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QKeySequence, QShortcut, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..core import autonomy as autonomykit
from ..core import cleanup
from ..core import credentials
from ..core import effort as effortkit
from ..core import context as contextkit
from ..core import providers
from ..core import setup_llama
from ..config import ModelSpec, Settings
from ..core import plugins
from ..core import skills as skillkit
from ..core.agent import Agent
from ..core.client import LlamaClient
from ..core.providers import Provider
from ..core.server import ServerManager
from . import style, theme
from .download_dialog import DownloadDialog
from .login_dialog import LoginDialog
from .resource_bar import ResourceBar
from .chat import AssistantBlock, Notice, ThinkingCard, ToolCard, Transcript, UserBubble
from .workers import AgentWorker, DownloadWorker, ServerWorker


class ApprovalDialog(QDialog):
    """Confirm a mutating tool call. Shows exactly what will happen."""

    def __init__(self, name: str, summary: str, arguments: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Approve tool call")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        headline = {
            "run_command": "The model wants to run a shell command:",
            "write_file": "The model wants to write a file:",
        }.get(name, f"The model wants to call <b>{html_mod.escape(name)}</b>:")
        head = QLabel(headline)
        head.setWordWrap(True)
        layout.addWidget(head)

        target = QLabel(f"<code>{html_mod.escape(summary)}</code>")
        target.setWordWrap(True)
        target.setStyleSheet(f"background:{style.CODE_BG}; padding:8px; border-radius:6px;")
        layout.addWidget(target)

        if name == "write_file":
            preview = QPlainTextEdit(arguments.get("content", ""))
            preview.setReadOnly(True)
            preview.setMinimumHeight(260)
            layout.addWidget(QLabel("Content to be written:"))
            layout.addWidget(preview)

        buttons = QDialogButtonBox()
        deny = buttons.addButton("Deny", QDialogButtonBox.ButtonRole.RejectRole)
        allow = buttons.addButton("Allow", QDialogButtonBox.ButtonRole.AcceptRole)
        allow.setObjectName("primary")
        deny.setDefault(True)  # the safe option is the one Enter picks
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class Composer(QTextEdit):
    """Input box. Enter sends, Shift+Enter inserts a newline."""

    def __init__(self, on_send, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("composer")
        self.setPlaceholderText("Ask something…   (Enter to send, Shift+Enter for a newline)")
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setMaximumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._on_send = on_send

    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        if enter and not shift:
            self._on_send()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("localagent")
        self.resize(1180, 820)

        self.settings = Settings.load()
        self.server = ServerManager()
        self.history: list[dict[str, Any]] = []

        self._skills = skillkit.load_all()
        # First run (None) takes the default-on set; an explicit empty list is
        # a real user choice and must survive a restart.
        self._enabled_skills: set[str] = (
            skillkit.default_keys(self._skills)
            if self.settings.enabled_skills is None
            else set(self.settings.enabled_skills)
        )
        self._plugins = plugins.discover()
        self._enabled_plugins: set[str] = set(self.settings.enabled_plugins)

        self._server_worker: ServerWorker | None = None
        self._agent_worker: AgentWorker | None = None
        self._download_worker: DownloadWorker | None = None
        self._assistant: AssistantBlock | None = None
        self._thinking: ThinkingCard | None = None
        self._tool_cards: dict[str, ToolCard] = {}
        # Providers we have already offered a sign-in dialog for this session.
        self._offered_login: set[Provider] = set()

        # Restore any cloud catalogue we can reach without blocking startup —
        # the stored list is refreshed in Settings, not on every launch.
        self._build_ui()
        self._refresh_models()
        self._sync_controls()
        # Deferred so the window is on screen before anything modal appears:
        # a dialog over a blank grey rectangle reads as a crash on startup.
        QTimer.singleShot(400, self._offer_runtime_setup)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_conversation())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 880])
        self.setCentralWidget(splitter)
        self._build_menus()

        QShortcut(QKeySequence("Ctrl+L"), self, self._new_conversation)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._send)

    def _build_menus(self) -> None:
        bar = self.menuBar()

        app_menu = bar.addMenu("&localagent")
        settings = QAction("Settings…", self)
        settings.setShortcut(QKeySequence("Ctrl+,"))
        settings.triggered.connect(self._open_settings)
        app_menu.addAction(settings)
        runtime = QAction("Set up llama-server…", self)
        runtime.setToolTip("Point localagent at the binary that serves local "
                           "models, or find out how to build one.")
        runtime.triggered.connect(self._setup_runtime)
        app_menu.addAction(runtime)

        locate = QAction("Find my models…", self)
        locate.setToolTip("Point at a folder of .gguf files and record every "
                          "model found in it.")
        locate.triggered.connect(self._find_models)
        app_menu.addAction(locate)
        app_menu.addSeparator()

        # Cleanup is reachable without opening Settings: the moment you want
        # it is the moment something is already too slow to go hunting.
        for action_key in cleanup.ACTIONS:
            title, _ = cleanup.CONFIRMATIONS[action_key]
            act = QAction(f"{title}…", self)
            act.triggered.connect(lambda _=False, a=action_key: self._quick_cleanup(a))
            app_menu.addAction(act)

        app_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        app_menu.addAction(quit_action)

        view = bar.addMenu("&View")
        self._theme_actions: dict[str, QAction] = {}
        for name in ("system", *theme.THEMES):
            act = QAction(theme.LABELS[name], self)
            act.setCheckable(True)
            act.setChecked(name == self.settings.theme)
            act.triggered.connect(lambda _=False, n=name: self.apply_theme(n))
            view.addAction(act)
            self._theme_actions[name] = act

        accounts = bar.addMenu("&Accounts")
        for provider in providers.CLOUD:
            act = QAction(f"Sign in to {providers.LABELS[provider]}…", self)
            act.triggered.connect(lambda _=False, p=provider: self._sign_in(p))
            accounts.addAction(act)

    def _build_sidebar(self) -> QWidget:
        """The controls column, inside a scroll area.

        It has to scroll. The column is fifteen sections tall and wants about
        1400px; a 1080p screen gives it rather less, and Qt resolves that
        shortfall by compressing children below their size hints. Widgets with
        a fixed height -- the four resource meters -- cannot compress, so they
        are simply drawn outside their frame, on top of whatever is next to
        them. Scrolling is the only arrangement in which nothing can overlap
        at any window size.
        """
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroller.setMinimumWidth(286)   # 270 for content, plus the scrollbar

        panel = QFrame()
        panel.setObjectName("sidebar")
        panel.setMinimumWidth(270)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # Two levels, because the three lists are chosen on different grounds:
        # a local model by what fits in VRAM, a cloud model by price and
        # capability. One flat dropdown of thirty entries makes both harder.
        layout.addWidget(self._heading("Provider"))
        self.provider_combo = QComboBox()
        for provider in Provider:
            self.provider_combo.addItem(providers.LABELS[provider], provider.value)
        self.provider_combo.setCurrentIndex(
            max(0, self.provider_combo.findData(self.settings.provider))
        )
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)

        layout.addWidget(self._heading("Model"))
        self.model_combo = QComboBox()
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        layout.addWidget(self.model_combo)

        # Never subtle. A cloud model means the conversation leaves the
        # machine, and that is the one property of a model choice the user
        # cannot see from the answer it produces.
        self.privacy_label = QLabel()
        self.privacy_label.setObjectName("cloud")
        self.privacy_label.setWordWrap(True)
        layout.addWidget(self.privacy_label)

        self.model_blurb = QLabel()
        self.model_blurb.setObjectName("blurb")
        self.model_blurb.setWordWrap(True)
        layout.addWidget(self.model_blurb)

        self.server_button = QPushButton("Start server")
        self.server_button.setObjectName("primary")
        self.server_button.clicked.connect(self._toggle_server)
        layout.addWidget(self.server_button)

        self.server_status = QLabel("Stopped")
        self.server_status.setObjectName("status")
        self.server_status.setWordWrap(True)
        layout.addWidget(self.server_status)

        layout.addSpacing(10)
        layout.addWidget(self._heading("Working directory"))
        self.workdir_label = QLabel()
        self.workdir_label.setObjectName("blurb")
        self.workdir_label.setWordWrap(True)
        layout.addWidget(self.workdir_label)
        pick = QPushButton("Change…")
        pick.clicked.connect(self._pick_workdir)
        layout.addWidget(pick)

        layout.addSpacing(10)
        layout.addWidget(self._heading("Tools"))
        self.tools_check = QCheckBox("Enable file and shell tools")
        self.tools_check.setChecked(self.settings.tools_enabled)
        self.tools_check.toggled.connect(self._on_tools_toggled)
        layout.addWidget(self.tools_check)

        self.auto_reads_check = QCheckBox("Auto-approve read-only tools")
        self.auto_reads_check.setChecked(self.settings.auto_approve_reads)
        self.auto_reads_check.setToolTip(
            "Writes and shell commands always ask, regardless of this setting."
        )
        self.auto_reads_check.toggled.connect(self._on_auto_reads_toggled)
        layout.addWidget(self.auto_reads_check)

        layout.addSpacing(10)
        layout.addWidget(self._heading("Effort"))
        self.effort_combo = QComboBox()
        for level in effortkit.Effort:
            self.effort_combo.addItem(effortkit.LABELS[level], int(level))
        self.effort_combo.setCurrentIndex(
            max(0, self.effort_combo.findData(self.settings.effort_level))
        )
        self.effort_combo.currentIndexChanged.connect(self._on_effort_changed)
        layout.addWidget(self.effort_combo)
        self.effort_blurb = QLabel()
        self.effort_blurb.setObjectName("blurb")
        self.effort_blurb.setWordWrap(True)
        layout.addWidget(self.effort_blurb)

        layout.addSpacing(10)
        layout.addWidget(self._heading("Autonomy"))
        self.autonomy_combo = QComboBox()
        for level in autonomykit.Autonomy:
            self.autonomy_combo.addItem(autonomykit.LABELS[level], int(level))
        self.autonomy_combo.setCurrentIndex(
            max(0, self.autonomy_combo.findData(self.settings.autonomy_level))
        )
        self.autonomy_combo.currentIndexChanged.connect(self._on_autonomy_changed)
        layout.addWidget(self.autonomy_combo)
        self.autonomy_blurb = QLabel()
        self.autonomy_blurb.setObjectName("blurb")
        self.autonomy_blurb.setWordWrap(True)
        layout.addWidget(self.autonomy_blurb)
        self._on_effort_changed()
        self._on_autonomy_changed()

        layout.addSpacing(10)
        layout.addWidget(self._heading("Context"))
        row = QHBoxLayout()
        self.context_spin = QSpinBox()
        self.context_spin.setRange(2048, 1_048_576)
        self.context_spin.setSingleStep(4096)
        self.context_spin.setGroupSeparatorShown(True)
        self.context_spin.setSuffix(" tokens")
        self.context_spin.setToolTip(
            "The context window to ask the model for.\n"
            "Applied at launch — llama.cpp allocates the KV cache once, when\n"
            "the model loads, so changing it needs a server restart.\n"
            "Bigger costs VRAM and prompt-processing time."
        )
        self.context_spin.valueChanged.connect(self._on_context_size_changed)
        row.addWidget(self.context_spin, 1)
        reset = QPushButton("Default")
        reset.setToolTip("Use this model's own context size.")
        reset.clicked.connect(self._reset_context_size)
        row.addWidget(reset)
        layout.addLayout(row)

        self.context_meter = QProgressBar()
        self.context_meter.setRange(0, 100)
        self.context_meter.setFormat("%p% of context used")
        self.context_meter.setToolTip(
            "How much of the window this conversation occupies, including\n"
            "the system prompt, enabled skills and tool schemas."
        )
        layout.addWidget(self.context_meter)

        self.context_note = QLabel()
        self.context_note.setObjectName("blurb")
        self.context_note.setWordWrap(True)
        layout.addWidget(self.context_note)

        self.context_combo = QComboBox()
        for level in contextkit.Strategy:
            self.context_combo.addItem(contextkit.LABELS[level], int(level))
        self.context_combo.setCurrentIndex(
            max(0, self.context_combo.findData(self.settings.context_strategy)))
        self.context_combo.currentIndexChanged.connect(self._on_context_strategy)
        layout.addWidget(self.context_combo)

        self.context_strategy_blurb = QLabel()
        self.context_strategy_blurb.setObjectName("blurb")
        self.context_strategy_blurb.setWordWrap(True)
        layout.addWidget(self.context_strategy_blurb)
        self._on_context_strategy()

        layout.addSpacing(10)
        layout.addWidget(self._heading("Skills"))
        self.skills_button = QPushButton()
        self.skills_button.setToolTip(
            "Instruction packs appended to the system prompt.\n"
            "Grouped by category; Core is on by default."
        )
        self.skills_button.setMenu(self._build_skills_menu())
        layout.addWidget(self.skills_button)

        self.skills_summary = QLabel()
        self.skills_summary.setObjectName("blurb")
        self.skills_summary.setWordWrap(True)
        layout.addWidget(self.skills_summary)
        self._update_skills_button()

        layout.addSpacing(10)
        layout.addWidget(self._heading("Custom tools"))
        self.plugins_button = QPushButton()
        self.plugins_button.setToolTip(
            f"Python tools you or a model wrote, from\n{plugins.PLUGIN_DIR}\n\n"
            "Off until you switch one on — read the file first. Unlike\n"
            "run_command, an enabled tool runs unreviewed thereafter."
        )
        self.plugins_button.setMenu(self._build_plugins_menu())
        layout.addWidget(self.plugins_button)

        self.plugins_summary = QLabel()
        self.plugins_summary.setObjectName("blurb")
        self.plugins_summary.setWordWrap(True)
        layout.addWidget(self.plugins_summary)
        self._update_plugins_button()

        layout.addSpacing(10)
        layout.addWidget(self._heading("System prompt"))
        self.system_edit = QPlainTextEdit(self.settings.system_prompt)
        self.system_edit.setMaximumHeight(120)
        self.system_edit.textChanged.connect(self._on_system_changed)
        layout.addWidget(self.system_edit)

        layout.addStretch(1)
        # Meters sit at the bottom: they are ambient status, not a control,
        # and the model/skill controls above them are what gets used.
        layout.addWidget(self._heading("System"))
        self.resources = ResourceBar()
        layout.addWidget(self.resources)

        self.download_progress = QProgressBar()
        self.download_progress.setVisible(False)
        self.download_progress.setTextVisible(True)
        layout.addWidget(self.download_progress)

        self.download_note = QLabel()
        self.download_note.setObjectName("blurb")
        self.download_note.setWordWrap(True)
        self.download_note.setVisible(False)
        layout.addWidget(self.download_note)

        new_chat = QPushButton("New conversation  (Ctrl+L)")
        new_chat.clicked.connect(self._new_conversation)
        layout.addWidget(new_chat)

        self._update_workdir_label()
        scroller.setWidget(panel)
        return scroller

    def _build_conversation(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.transcript = Transcript()
        layout.addWidget(self.transcript, 1)

        bar = QFrame()
        bar.setStyleSheet(f"border-top: 1px solid {style.BORDER};")
        self.composer_bar = bar  # restyled on a theme change
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 10, 16, 12)
        bar_layout.setSpacing(8)

        self.composer = Composer(self._send)
        bar_layout.addWidget(self.composer, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(6)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self._send)
        buttons.addWidget(self.send_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("danger")
        self.stop_button.clicked.connect(self._cancel)
        self.stop_button.setVisible(False)
        buttons.addWidget(self.stop_button)
        bar_layout.addLayout(buttons)

        layout.addWidget(bar)

        self.stats = QLabel("")
        self.stats.setObjectName("status")
        self.stats.setContentsMargins(18, 0, 18, 8)
        layout.addWidget(self.stats)
        return page

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("heading")
        return label

    # -------------------------------------------------------------- downloads

    def _offer_download(self, spec: ModelSpec) -> None:
        """Ask where the weights should go, then fetch them."""
        if not spec.repo or not spec.files:
            QMessageBox.information(
                self, "No download configured",
                f"{spec.label} has no download source recorded. Fetch it manually "
                f"with ~/.claude/models/scripts/download.sh.",
            )
            return

        dialog = DownloadDialog(spec, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # "I already have it" / "Scan a folder" — record what was found and
        # skip the download. Possibly for a model other than the one this
        # dialog was opened for, which is the usual case on a second machine.
        located = dialog.located()
        if located:
            for key, path in located.items():
                config.set_model_path(key, path)
            self.settings.save()
            self._refresh_models()
            # Select something that is now actually usable, preferring the
            # model the user pointed at over the one the dialog was for.
            for key in located:
                index = self.model_combo.findData(key)
                if index >= 0:
                    self.model_combo.setCurrentIndex(index)
                    break
            if len(located) == 1:
                key, path = next(iter(located.items()))
                label = (config.by_key(key) or spec).label
                self.download_note.setText(f"{label} located at {path.parent}")
            else:
                self.download_note.setText(
                    f"{len(located)} models located: "
                    + ", ".join((config.by_key(k) or spec).label for k in located)
                )
            self.download_note.setVisible(True)
            return

        worker = DownloadWorker(spec, dialog.destination(), self,
                                targets=spec.expected_files())
        worker.progress.connect(self._on_download_progress)
        worker.finished_ok.connect(lambda d, s=spec: self._on_download_done(s, d))
        worker.failed.connect(self._on_download_failed)
        worker.finished.connect(lambda: setattr(self, "_download_worker", None))
        self._download_worker = worker

        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(0)
        self.download_progress.setVisible(True)
        self.download_note.setText(f"Starting {spec.label}…")
        self.download_note.setVisible(True)
        worker.start()

    def _on_download_progress(self, p) -> None:
        self.download_progress.setValue(int(p.percent))
        self.download_progress.setFormat(
            f"{p.overall_downloaded / 1e9:.1f} / {p.overall_total / 1e9:.1f} GB  (%p%)"
        )
        self.download_note.setText(
            f"file {p.file_index} of {p.file_count} · {p.filename}"
        )

    def _on_download_done(self, spec: ModelSpec, destination: str) -> None:
        self.download_progress.setVisible(False)
        # Record where the weights actually landed, so the launch gets a
        # --model override and the user's disk choice survives a restart.
        primary = Path(destination) / Path(spec.files[0])
        if primary.is_file():
            config.set_model_path(spec.key, primary)
            self.settings.save()
        self._refresh_models()
        if spec.is_available():
            self.download_note.setText(f"{spec.label} ready — weights at {primary.parent}")
            index = self.model_combo.findData(spec.key)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        else:
            missing = spec.missing_shards(primary)
            self.download_note.setText(
                f"Downloaded to {destination}, but {len(missing)} file(s) are "
                f"still missing: {', '.join(m.name for m in missing[:3])}"
            )

    def _on_download_failed(self, message: str) -> None:
        self.download_progress.setVisible(False)
        self.download_note.setText("")
        self.download_note.setVisible(False)
        QMessageBox.critical(self, "Download failed", message)

    # ---------------------------------------------------------------- skills

    def _build_skills_menu(self) -> QMenu:
        """Category submenus of checkable skills, plus bulk actions."""
        menu = QMenu(self)
        self._skill_actions: dict[str, QAction] = {}

        for category, group in skillkit.by_category(self._skills).items():
            submenu = menu.addMenu(category)
            for skill in group:
                action = QAction(f"{skill.name}   (~{skill.approx_tokens} tok)", self)
                action.setCheckable(True)
                action.setChecked(skill.key in self._enabled_skills)
                tip = skill.description
                if skill.when:
                    tip += f"\n\nApplies when: {skill.when}"
                action.setToolTip(tip)
                action.toggled.connect(
                    lambda checked, k=skill.key: self._on_skill_toggled(k, checked)
                )
                submenu.addAction(action)
                self._skill_actions[skill.key] = action

        menu.addSeparator()
        for label, keys in (
            ("Enable all", {s.key for s in self._skills}),
            ("Defaults only", skillkit.default_keys(self._skills)),
            ("Disable all", set()),
        ):
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, k=keys: self._set_skills(k))
            menu.addAction(act)
        return menu

    # --------------------------------------------------------------- plugins

    def _build_plugins_menu(self) -> QMenu:
        menu = QMenu(self)
        self._plugin_actions: dict[str, QAction] = {}

        ok = [r for r in self._plugins if r.ok]
        broken = [r for r in self._plugins if not r.ok]

        if not self._plugins:
            act = QAction("No custom tools found", self)
            act.setEnabled(False)
            menu.addAction(act)
        for result in ok:
            action = QAction(f"{result.tool.name}   ({result.path.name})", self)
            action.setCheckable(True)
            action.setChecked(result.tool.name in self._enabled_plugins)
            action.setToolTip(result.tool.description[:200])
            action.toggled.connect(
                lambda checked, n=result.tool.name: self._on_plugin_toggled(n, checked)
            )
            menu.addAction(action)
        if broken:
            menu.addSeparator()
            for result in broken:
                act = QAction(f"⚠ {result.path.name}: {result.error[:60]}", self)
                act.setEnabled(False)
                menu.addAction(act)

        menu.addSeparator()
        rescan = QAction("Rescan directory", self)
        rescan.triggered.connect(self._rescan_plugins)
        menu.addAction(rescan)
        return menu

    def _on_plugin_toggled(self, name: str, checked: bool) -> None:
        if checked:
            self._enabled_plugins.add(name)
        else:
            self._enabled_plugins.discard(name)
        self.settings.enabled_plugins = sorted(self._enabled_plugins)
        self.settings.save()
        self._update_plugins_button()

    def _rescan_plugins(self) -> None:
        """Pick up a tool written during this session without a restart."""
        self._plugins = plugins.discover()
        self.plugins_button.setMenu(self._build_plugins_menu())
        self._update_plugins_button()

    def _active_plugins(self) -> list:
        return plugins.loaded_tools(self._plugins, self._enabled_plugins)

    def _update_plugins_button(self) -> None:
        ok = [r for r in self._plugins if r.ok]
        broken = [r for r in self._plugins if not r.ok]
        active = self._active_plugins()
        self.plugins_button.setText(f"{len(active)} of {len(ok)} enabled  ▾")
        if not self._plugins:
            self.plugins_summary.setText("None found. Ask a model to write one.")
        elif active:
            note = ", ".join(t.name for t in active)
            self.plugins_summary.setText(f"Active: {note}")
        else:
            note = f"{len(ok)} available, none enabled."
            if broken:
                note += f" {len(broken)} failed to load."
            self.plugins_summary.setText(note)

    def _on_skill_toggled(self, key: str, checked: bool) -> None:
        if checked:
            self._enabled_skills.add(key)
        else:
            self._enabled_skills.discard(key)
        self._persist_skills()
        self._update_skills_button()

    def _set_skills(self, keys: set[str]) -> None:
        self._enabled_skills = set(keys)
        for key, action in self._skill_actions.items():
            action.blockSignals(True)          # don't re-enter _on_skill_toggled
            action.setChecked(key in self._enabled_skills)
            action.blockSignals(False)
        self._persist_skills()
        self._update_skills_button()

    def _persist_skills(self) -> None:
        self.settings.enabled_skills = sorted(self._enabled_skills)
        self.settings.save()

    def _active_skills(self) -> list[skillkit.Skill]:
        return [s for s in self._skills if s.key in self._enabled_skills]

    def _update_skills_button(self) -> None:
        active = self._active_skills()
        self.skills_button.setText(f"{len(active)} of {len(self._skills)} enabled  ▾")
        if not active:
            self.skills_summary.setText("No skills active.")
            return
        cost = skillkit.total_tokens(active)
        names = ", ".join(s.name for s in active)
        text = f"~{cost} tokens per request · {names}"

        # Skills + tool schemas + the effort block form a fixed preamble on
        # every request. If that eats the context the model 400s before the
        # conversation starts, so surface it here rather than at send time.
        spec = self._current_spec()
        ctx = spec.context_tokens if spec else 0
        if ctx:
            preamble = cost + self._tool_schema_tokens() + self._effort_tokens()
            share = 100 * preamble / ctx
            if share >= 60:
                text = (
                    f"\u26a0 {preamble:,} tokens of skills + tools is {share:.0f}% of "
                    f"this model's {ctx // 1024}K context before any conversation. "
                    f"Disable skills, or pick a longer-context model.\n\n" + text
                )
        self.skills_summary.setText(text)

    def _tool_schema_tokens(self) -> int:
        if not self._tools_active():
            return 0
        import json

        from ..core import tools as toolkit

        schemas = toolkit.schemas() + [t.schema() for t in self._active_plugins()]
        return len(json.dumps(schemas)) // 4

    def _effort_tokens(self) -> int:
        return len(effortkit.instructions(self._effort())) // 4

    # ------------------------------------------------------------- model list

    def _provider(self) -> Provider:
        return Provider(self.provider_combo.currentData())

    def _on_provider_changed(self) -> None:
        self.settings.provider = self._provider().value
        self.settings.save()
        self._refresh_models()

    def refresh_models(self) -> None:
        """Public: the settings dialog calls this after a sign-in."""
        self._refresh_models()

    def _refresh_models(self) -> None:
        if self._provider() != Provider.LOCAL:
            self._refresh_cloud_models()
            return
        spec_zero = config.ModelSpec("", "", "", 0, "")  # sort key fallback
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for spec in config.REGISTRY:
            ready = spec.readiness()
            available = ready.can_launch
            size = f"  ⬇ {spec.download_gb:.0f} GB" if spec.download_gb else ""
            if available:
                label = spec.label
            elif ready.weights:
                # The weights are there; something else is missing. Saying
                # "not downloaded" here sends the user to re-fetch 87 GB.
                label = f"{spec.label}  — {ready.explain()}"
            else:
                label = f"{spec.label}  — not downloaded{size}"
            self.model_combo.addItem(label, spec.key)
            item = self.model_combo.model().item(self.model_combo.count() - 1)
            # Selectable even when absent: picking one offers to download it.
            # A disabled item cannot be clicked, so the offer would be
            # unreachable — which is the whole point of listing it.
            item.setEnabled(True)
            if not available:
                item.setForeground(QColor(style.TEXT_DIM))
        # Prefer the remembered model, then any downloaded one, so a fresh
        # install does not open on a model it would immediately offer to fetch.
        wanted = self.model_combo.findData(self.settings.model_key)
        downloaded = [i for i in range(self.model_combo.count())
                      if (spec := config.by_key(self.model_combo.itemData(i)))
                      and spec.is_available()]
        if wanted >= 0 and config.by_key(self.settings.model_key) \
                and config.by_key(self.settings.model_key).is_available():
            self.model_combo.setCurrentIndex(wanted)
        elif downloaded:
            self.model_combo.setCurrentIndex(downloaded[0])
        elif self.model_combo.count():
            # Nothing downloaded at all. Land on the smallest model rather than
            # whatever happens to be first, so a fresh install is offered the
            # cheapest useful download instead of a 102 GB one.
            smallest = min(
                range(self.model_combo.count()),
                key=lambda i: (config.by_key(self.model_combo.itemData(i)) or spec_zero).download_gb,
            )
            self.model_combo.setCurrentIndex(smallest)
        self.model_combo.blockSignals(False)
        self._on_model_changed()

    def _refresh_cloud_models(self) -> None:
        """Fill the second dropdown from one provider's catalogue."""
        provider = self._provider()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in providers.models_for(provider):
            self.model_combo.addItem(model.label, model.key)
            self.model_combo.setItemData(
                self.model_combo.count() - 1,
                f"{model.id}\n{model.blurb}\n{model.context:,} token context",
                Qt.ItemDataRole.ToolTipRole,
            )
        remembered = self.settings.cloud_models.get(provider.value, "")
        index = self.model_combo.findData(f"{provider.value}:{remembered}")
        self.model_combo.setCurrentIndex(max(0, index))
        self.model_combo.blockSignals(False)

        if not credentials.is_signed_in(provider) and provider not in self._offered_login:
            # The list shown before sign-in is a fallback, so offer the dialog
            # rather than letting the first send fail with a 401. Once per
            # provider: _sign_in refreshes this list when it closes, so
            # cancelling would otherwise reopen the dialog forever.
            self._offered_login.add(provider)
            self._sign_in(provider)
        self._on_model_changed()

    def _current_spec(self):
        """The selected model: a local ``ModelSpec`` or a cloud ``CloudModel``.

        Both answer ``label``/``blurb``/``context_tokens``/``is_available()``,
        which is everything the sidebar reads, so callers do not branch.
        """
        key = self.model_combo.currentData()
        if not key:
            return None
        if self._provider() == Provider.LOCAL:
            return config.by_key(key)
        return providers.by_key(key)

    def _is_cloud(self) -> bool:
        return self._provider() != Provider.LOCAL

    def _on_model_changed(self) -> None:
        spec = self._current_spec()
        provider = self._provider()

        if spec is None:
            self.model_blurb.setText("")
            self.privacy_label.setText("")
            self._sync_controls()
            return

        if provider == Provider.LOCAL:
            ready = spec.readiness()
            if not ready.weights and self._download_worker is None:
                self._offer_download(spec)
            self.settings.model_key = spec.key
            self.privacy_label.setText("")
        else:
            self.settings.cloud_models[provider.value] = spec.id
            self.privacy_label.setText(
                f"⚠ Runs on {providers.LABELS[provider]}'s servers. Prompts and "
                f"tool results are sent to them."
            )
        self.settings.save()

        note = spec.blurb
        if spec.ram_gb:
            note += f"  Needs ~{spec.ram_gb} GB free RAM."
        if provider == Provider.LOCAL:
            ready = spec.readiness()
            if ready.explain() != "ready":
                note += f"\n\n\u26a0 {ready.explain()}"
            if ready.weights:
                note += f"\n\nWeights: {spec.model_path}"
        if provider != Provider.LOCAL:
            note += f"  {spec.context // 1000}K context."
        self.model_blurb.setText(note)
        self._sync_tools_for_model(spec)
        if hasattr(self, 'context_spin'):
            self._sync_context_controls()
        if hasattr(self, 'skills_summary'):
            self._update_skills_button()
        if hasattr(self, 'effort_blurb'):
            self._on_effort_changed()
            self._on_autonomy_changed()
        if not self.server.is_running:
            self.server_status.setText("Stopped")
        self._sync_controls()

    # -------------------------------------------------- accounts and themes

    def _sign_in(self, provider: Provider) -> None:
        dialog = LoginDialog(provider, self)
        dialog.exec()
        if credentials.is_signed_in(provider):
            # Signing out later should be allowed to prompt again.
            self._offered_login.discard(provider)
        if self._provider() == provider:
            self._refresh_cloud_models()
        self._sync_controls()

    def apply_theme(self, name: str) -> None:
        """Switch theme and repaint everything that captured a colour.

        The stylesheet covers the widgets Qt styles for us. The ones that
        build their own colour strings — the transcript's rendered HTML, the
        resource meters, the top border of the composer bar — captured the
        old palette when they were constructed and have to be told, which is
        why each exposes a restyle method rather than being reconstructed.
        """
        theme.set_active(name)
        self.settings.theme = name
        self.settings.save()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.stylesheet())
        for key, action in getattr(self, "_theme_actions", {}).items():
            action.blockSignals(True)
            action.setChecked(key == name)
            action.blockSignals(False)
        self.transcript.restyle()
        self.resources.restyle()
        self.composer_bar.setStyleSheet(f"border-top: 1px solid {theme.active()['border']};")

    def _offer_runtime_setup(self) -> None:
        """On a machine with weights and no binary, offer to build one.

        Asked once per launch and only when it is actually the blocker: there
        are weights to run, and nothing to run them with. Staying silent would
        leave the user with a model list where nothing starts and no
        indication of why.
        """
        if self._is_cloud() or config.find_runtime() is not None:
            return
        if not any(spec.has_weights() for spec in config.REGISTRY):
            return   # nothing downloaded yet; the binary is not the problem
        checks = setup_llama.preflight()
        answer = QMessageBox.question(
            self, "Set up llama-server?",
            "You have model weights on this machine but no <b>llama-server</b> "
            "to run them with.\n\nIt is part of llama.cpp and has to be built "
            "for this machine's CPU and GPU, which is why copying the weights "
            "across was not enough.\n\n" + checks.summary() + "\n\nSet it up now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._setup_runtime()

    def _setup_runtime(self) -> None:
        from .runtime_dialog import RuntimeDialog

        if RuntimeDialog(self).exec() == QDialog.DialogCode.Accepted:
            self.settings.save()
            self._refresh_models()

    def _find_models(self) -> None:
        """Scan a folder and record every model in it, in one step.

        Reachable without first triggering a download dialog: on a second
        machine the weights arrive by copy, and having to open the offer-to-
        download flow for the wrong model in order to say "actually they are
        over here" is what made this confusing.
        """
        chosen = QFileDialog.getExistingDirectory(
            self, "Folder holding your model weights", str(config.MODELS_ROOT))
        if not chosen:
            return
        found = config.scan_for_weights(chosen)
        if not found:
            QMessageBox.information(
                self, "No models found",
                f"No complete model weights under:\n{chosen}\n\n"
                "Looked three levels down for .gguf files matching a model in "
                "the list. A sharded model needs all of its parts present.",
            )
            return
        for key, path in found.items():
            config.set_model_path(key, path)
        self.settings.save()
        self._refresh_models()
        QMessageBox.information(
            self, f"Found {len(found)} model(s)",
            "\n".join(f"{config.by_key(k).label}\n    {v}" for k, v in found.items()),
        )

    def _open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        SettingsDialog(self, self).exec()

    def _quick_cleanup(self, action: str) -> None:
        title, explanation = cleanup.CONFIRMATIONS[action]
        if action == "disk":
            entries = cleanup.disk_report()
            QMessageBox.information(
                self, title,
                "\n".join(e.summary() for e in entries) or "No readable drives.",
            )
            return
        answer = QMessageBox.question(
            self, title, explanation,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        if action == "ram":
            result = cleanup.clear_ram()
        elif action == "vram":
            result = cleanup.clear_vram(self.server)
            self._sync_controls()
        else:
            result = cleanup.clear_cpu()
        QMessageBox.information(self, title, result.summary())

    # ------------------------------------------------------------ server ctl

    def _toggle_server(self) -> None:
        if self.server.is_running:
            self.server.stop()
            self.server_status.setText("Stopped")
            self._sync_controls()
            return

        spec = self._current_spec()
        if spec is None:
            return
        ready = spec.readiness()
        if not ready.can_launch:
            if ready.weights and not ready.runtime:
                # Weights present, nothing to run them with. Offer the fix
                # rather than restating the problem.
                answer = QMessageBox.question(
                    self, "No llama-server on this machine",
                    f"{spec.label}\n\nThe weights are here:\n{spec.model_path}\n\n"
                    f"What is missing is llama-server, the llama.cpp binary that "
                    f"actually serves them. It has to be built for this machine, "
                    f"which is why copying the weights across was not enough.\n\n"
                    f"Set it up now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._setup_runtime()
            elif ready.weights:
                QMessageBox.warning(
                    self, "Cannot start yet",
                    f"{spec.label}\n\nThe weights are here:\n{spec.model_path}\n\n"
                    f"{ready.explain()}",
                )
            else:
                QMessageBox.warning(
                    self, "Not downloaded",
                    f"{spec.label} has not finished downloading yet.",
                )
            return
        if spec.ram_gb:
            free = config.free_ram_gb()
            if free and free < spec.ram_gb:
                proceed = QMessageBox.question(
                    self, "Low memory",
                    f"{spec.label} needs about {spec.ram_gb} GB of free RAM but only "
                    f"{free} GB is available.\n\nLoading it now will likely swap and "
                    f"become unusably slow.\n\nStart anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if proceed != QMessageBox.StandardButton.Yes:
                    return

        self.server_button.setEnabled(False)
        self.server_status.setText("Starting…")
        worker = ServerWorker(self.server, spec, self)
        worker.progress.connect(self.server_status.setText)
        worker.ready.connect(self._on_server_ready)
        worker.failed.connect(self._on_server_failed)
        worker.finished.connect(lambda: setattr(self, "_server_worker", None))
        self._server_worker = worker
        worker.start()

    def _on_server_ready(self, model_name: str) -> None:
        """Report where the model is actually running, not just that it started.

        "Ready" and "ready but entirely on the CPU" look identical and feel
        very different, and the second is the single most likely reason a
        model is unexpectedly slow. llama.cpp says which in its startup log;
        nothing was reading it.
        """
        status = f"Ready · {model_name}"
        placement = setup_llama.offload_from_log(self.server.log_tail)
        if placement:
            status += f"\n{placement}"
        self.server_status.setText(status)

        if placement.startswith("0 layers"):
            info = setup_llama.runtime_info()
            if not info.has_gpu and setup_llama.preflight().has_gpu:
                # The build cannot use the card at all -- which localagent may
                # have caused, by building without the CUDA toolkit present.
                QMessageBox.warning(
                    self, "Running on the CPU",
                    f"{model_name} loaded, but llama-server is a <b>CPU-only "
                    f"build</b> and cannot use your GPU. That is why it is slow.\n\n"
                    f"This happens when the CUDA toolkit was not installed at "
                    f"build time. Install it and rebuild:\n\n"
                    f"    sudo apt install nvidia-cuda-toolkit\n\n"
                    f"then localagent → Set up llama-server… and build again.",
                )
        self._sync_controls()

    def _on_server_failed(self, message: str) -> None:
        self.server_status.setText("Failed")
        self._sync_controls()
        QMessageBox.critical(self, "Server failed to start", message)

    # ------------------------------------------------------------- settings

    def _pick_workdir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Working directory", self.settings.workdir)
        if chosen:
            self.settings.workdir = chosen
            self.settings.save()
            self._update_workdir_label()

    def _update_workdir_label(self) -> None:
        path = Path(self.settings.workdir)
        self.workdir_label.setText(str(path))
        self.workdir_label.setToolTip(str(path))

    def _sync_tools_for_model(self, spec: ModelSpec) -> None:
        """Force tools off for models whose tool calling is unreliable.

        The saved preference is left untouched, so selecting a stock model
        again restores whatever the user had chosen.
        """
        if spec.tools_reliable:
            self.tools_check.setEnabled(True)
            self.tools_check.setToolTip("")
            self.tools_check.blockSignals(True)
            self.tools_check.setChecked(self.settings.tools_enabled)
            self.tools_check.blockSignals(False)
            return

        self.tools_check.blockSignals(True)
        self.tools_check.setChecked(False)
        self.tools_check.blockSignals(False)
        self.tools_check.setEnabled(False)
        self.tools_check.setToolTip(
            "Disabled for abliterated models. Removing refusal directions from "
            "the weights also degrades structured output, so tool calls come "
            "back malformed or as plain text — the call silently never runs."
        )

    def _tools_active(self) -> bool:
        spec = self._current_spec()
        return self.settings.tools_enabled and (spec is None or spec.tools_reliable)

    # ------------------------------------------------- effort and autonomy

    def _effort(self) -> effortkit.Effort:
        return effortkit.Effort(self.effort_combo.currentData())

    def _autonomy(self) -> autonomykit.Autonomy:
        return autonomykit.Autonomy(self.autonomy_combo.currentData())

    def _on_effort_changed(self) -> None:
        level = self._effort()
        self.settings.effort_level = int(level)
        self.settings.save()
        note = effortkit.BLURBS[level]
        if effortkit.grants_subagents(level):
            note += "  Grants the spawn_agent tool."
        # On a cloud model the tier is not only prompt text — it sets a real
        # thinking budget — so say which, or the two look like the same dial.
        note += "  " + effortkit.cloud_note(level, self._provider().value)
        self.effort_blurb.setText(note)
        if hasattr(self, 'skills_summary'):
            self._update_skills_button()

    def _on_autonomy_changed(self) -> None:
        level = self._autonomy()
        self.settings.autonomy_level = int(level)
        self.settings.save()
        self.autonomy_blurb.setText(
            autonomykit.BLURBS[level] + "  "
            + autonomykit.cloud_note(level, self._provider().value)
        )

    # ------------------------------------------------------------- context

    def _context_budget(self) -> contextkit.Budget:
        """The room this conversation actually has.

        The window minus what is re-sent every request -- system prompt,
        skills, tool schemas -- minus somewhere to put the reply.
        """
        spec = self._current_spec()
        limit = spec.context_tokens if spec else 0
        if spec is not None and not self._is_cloud():
            limit = spec.effective_ctx_size
        preamble = (
            contextkit.estimate_tokens(self.settings.system_prompt)
            + skillkit.total_tokens(self._active_skills())
            + self._tool_schema_tokens()
            + self._effort_tokens()
        )
        return contextkit.Budget(limit or 8192, preamble,
                                 self.settings.reserve_output)

    def _context_strategy(self) -> contextkit.Strategy:
        return contextkit.Strategy(self.context_combo.currentData())

    def _on_context_size_changed(self, value: int) -> None:
        spec = self._current_spec()
        if spec is None or self._is_cloud():
            return
        config.set_context_size(spec.key, value if value != spec.ctx_size else None)
        self.settings.save()
        self._update_context_meter()
        if self.server.is_running:
            self.context_note.setText(
                "Restart the server to apply — the KV cache is allocated at load.")

    def _reset_context_size(self) -> None:
        spec = self._current_spec()
        if spec is None or self._is_cloud():
            return
        config.set_context_size(spec.key, None)
        self.settings.save()
        self._sync_context_controls()

    def _on_context_strategy(self) -> None:
        level = self._context_strategy()
        self.settings.context_strategy = int(level)
        self.settings.save()
        self.context_strategy_blurb.setText(contextkit.BLURBS[level])

    def _sync_context_controls(self) -> None:
        """Point the controls at whichever model is selected."""
        spec = self._current_spec()
        cloud = self._is_cloud()
        self.context_spin.setEnabled(spec is not None and not cloud)
        if spec is None:
            return
        self.context_spin.blockSignals(True)
        self.context_spin.setValue(
            spec.context if cloud else spec.effective_ctx_size)
        self.context_spin.blockSignals(False)
        if cloud:
            self.context_spin.setToolTip(
                "Fixed by the provider for this model; it cannot be changed here.")
        self._update_context_meter()

    def _update_context_meter(self) -> None:
        budget = self._context_budget()
        used = contextkit.total_tokens(self.history)
        percent = int(100 * used / budget.available) if budget.available else 100
        self.context_meter.setValue(min(100, percent))
        detail = (f"{used:,} of {budget.available:,} usable "
                  f"({budget.preamble:,} for skills and tools)")
        if budget.available <= 0:
            detail = (f"\u26a0 The system prompt, skills and tool schemas come to "
                      f"{budget.preamble:,} tokens, which is the whole window. "
                      f"Disable skills or raise the context size.")
        self.context_note.setText(detail)

    def _on_tools_toggled(self, enabled: bool) -> None:
        self.settings.tools_enabled = enabled
        self.settings.save()

    def _on_auto_reads_toggled(self, enabled: bool) -> None:
        self.settings.auto_approve_reads = enabled
        self.settings.save()

    def _on_system_changed(self) -> None:
        self.settings.system_prompt = self.system_edit.toPlainText()
        self.settings.save()

    def _new_conversation(self) -> None:
        if self._agent_worker is not None:
            return
        self.history.clear()
        self.transcript.clear()
        self.stats.clear()
        self._update_context_meter()

    # ------------------------------------------------------------ the turn

    def _ready_to_send(self) -> bool:
        """A cloud model needs a key; a local one needs a running server."""
        if self._is_cloud():
            return credentials.is_signed_in(self._provider()) and self._current_spec() is not None
        return self.server.is_running

    def _sync_controls(self) -> None:
        cloud = self._is_cloud()
        running = self.server.is_running
        busy = self._agent_worker is not None

        # There is no process to start for a cloud model, so the control that
        # starts one is hidden rather than disabled — a greyed button invites
        # the question of what would un-grey it.
        self.server_button.setVisible(not cloud)
        self.server_status.setVisible(not cloud)
        self.server_button.setEnabled(not busy)
        self.server_button.setText("Stop server" if running else "Start server")

        self.send_button.setEnabled(self._ready_to_send() and not busy)
        self.send_button.setVisible(not busy)
        self.stop_button.setVisible(busy)
        self.provider_combo.setEnabled(not busy)
        self.model_combo.setEnabled(not busy and (cloud or not running))
        self.composer.setEnabled(not busy)

    def _build_client(self):
        """The client for the selected model. One call site, three providers."""
        spec = self._current_spec()
        if spec is None:
            return None
        if not self._is_cloud():
            return LlamaClient(spec.base_url)
        from ..core import cloud

        provider = self._provider()
        key = credentials.load(provider)
        if not key:
            return None
        return cloud.build(spec, key, self.settings.effort_level)

    def _send(self) -> None:
        if self._agent_worker is not None or not self._ready_to_send():
            return
        text = self.composer.toPlainText().strip()
        if not text:
            return

        client = self._build_client()
        if client is None:
            QMessageBox.warning(
                self, "Not signed in",
                f"Sign in to {providers.LABELS[self._provider()]} before sending.",
            )
            return
        self.composer.clear()

        self.transcript.add(UserBubble(text))
        self.history.append({"role": "user", "content": text})

        # Fit the conversation before sending. Doing it here rather than
        # inside the agent means the transcript can say what was lost, which
        # a silently-shortened history cannot.
        outcome = contextkit.compress(
            self.history, self._context_budget(), self._context_strategy(),
            summarise=self._summarise_span,
        )
        if outcome.overflowed:
            self.transcript.add(Notice(outcome.note, style.ERR))
            self.transcript.follow()
            self._update_context_meter()
            return
        if outcome.dropped:
            self.history[:] = outcome.history
            self.transcript.add(Notice(outcome.note, style.WARN))
            self.transcript.follow()

        spec = self._current_spec()
        assert spec is not None
        agent = Agent(
            client=client,
            workdir=self.settings.workdir,
            system_prompt=self.settings.system_prompt,
            use_tools=self._tools_active(),
            auto_approve_reads=self.settings.auto_approve_reads,
            max_iterations=self.settings.max_tool_iterations,
            active_skills=self._active_skills(),
            extra_tools=self._active_plugins(),
            effort=self._effort(),
            autonomy=self._autonomy(),
        )

        self._thinking = None
        self._assistant = None
        self._tool_cards.clear()

        worker = AgentWorker(agent, self.history, self)
        worker.reasoning.connect(self._on_reasoning)
        worker.content.connect(self._on_content)
        worker.tool_start.connect(self._on_tool_start)
        worker.tool_result.connect(self._on_tool_result)
        worker.tool_denied.connect(self._on_tool_denied)
        worker.approval_requested.connect(self._on_approval_requested)
        worker.turn_finished.connect(self._on_turn_finished)
        worker.failed.connect(self._on_turn_failed)
        worker.finished.connect(self._on_worker_done)
        self._agent_worker = worker
        self.stats.setText("Generating…")
        self._sync_controls()
        worker.start()

    def _summarise_span(self, messages: list[dict[str, Any]]) -> str:
        """Ask the current model to summarise the turns being dropped.

        Synchronous and on the GUI thread, which is acceptable only because it
        happens between turns rather than during one, and a failure falls back
        to plain dropping rather than losing the message.
        """
        client = self._build_client()
        if client is None:
            return ""
        parts = []
        for event in client.stream(contextkit.summary_request(messages),
                                   max_tokens=800):
            if event.kind == "content":
                parts.append(event.text)
            elif event.kind == "error":
                return ""
        return "".join(parts)

    def _cancel(self) -> None:
        if self._agent_worker is not None:
            self._agent_worker.cancel()
            self.stats.setText("Cancelling…")

    # ---- streamed events (GUI thread) ----

    def _on_reasoning(self, text: str) -> None:
        if self._thinking is None:
            self._thinking = ThinkingCard()
            self.transcript.add(self._thinking)
        self._thinking.append(text)
        self.transcript.follow()

    def _on_content(self, text: str) -> None:
        if self._thinking is not None:
            self._thinking.finish()
            self._thinking = None
        if self._assistant is None:
            self._assistant = AssistantBlock()
            self.transcript.add(self._assistant)
        self._assistant.append(text)
        self.transcript.follow()

    def _on_tool_start(self, name: str, summary: str, call_id: str) -> None:
        if self._thinking is not None:
            self._thinking.finish()
            self._thinking = None
        if self._assistant is not None:
            self._assistant.finish()
            self._assistant = None
        # Keyed by name+summary, not call_id: the result and denial signals
        # carry only those two fields, so this is what can be matched later.
        card = ToolCard(name, summary)
        self._tool_cards[f"{name}:{summary}"] = card
        self.transcript.add(card)
        self.transcript.follow()

    def _on_tool_result(self, name: str, summary: str, output: str) -> None:
        card = self._tool_cards.get(f"{name}:{summary}") or self._last_card()
        if card is not None:
            card.set_result(output)
        self.transcript.follow()

    def _on_tool_denied(self, name: str, summary: str) -> None:
        card = self._tool_cards.get(f"{name}:{summary}") or self._last_card()
        if card is not None:
            card.set_denied()

    def _last_card(self) -> ToolCard | None:
        return next(reversed(list(self._tool_cards.values())), None) if self._tool_cards else None

    def _on_approval_requested(self, name: str, summary: str, arguments: dict) -> None:
        dialog = ApprovalDialog(name, summary, arguments, self)
        allowed = dialog.exec() == QDialog.DialogCode.Accepted
        if self._agent_worker is not None:
            self._agent_worker.provide_approval(allowed)

    def _on_turn_finished(self, timings: dict) -> None:
        self._update_context_meter()
        if self._thinking is not None:
            self._thinking.finish()
            self._thinking = None
        if self._assistant is not None:
            self._assistant.finish()
            self._assistant = None
        gen = timings.get("predicted_per_second")
        prompt = timings.get("prompt_per_second")
        if gen:
            parts = [f"{gen:.1f} tok/s generated"]
            if prompt:
                parts.append(f"{prompt:.0f} tok/s prompt")
            self.stats.setText(" · ".join(parts))
        else:
            self.stats.clear()

    def _on_turn_failed(self, message: str) -> None:
        self.transcript.add(Notice(message, style.ERR))
        self.transcript.follow()
        self.stats.clear()

    def _on_worker_done(self) -> None:
        self._agent_worker = None
        self._sync_controls()
        QTimer.singleShot(0, self.composer.setFocus)

    # ---------------------------------------------------------------- close

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        if self._agent_worker is not None:
            self._agent_worker.cancel()
            self._agent_worker.wait(3000)
        self.server.stop()
        self.settings.save()
        super().closeEvent(event)
