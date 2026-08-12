"""Main window: model/server controls on the left, conversation on the right."""

from __future__ import annotations

import html as html_mod
import json
import threading
import time
from pathlib import Path
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QKeySequence, QShortcut, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QListWidget, QListWidgetItem, QTabWidget, QTextBrowser, QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..core import autonomy as autonomykit
from ..core import cleanup
from ..core import commands as commandkit
from ..core import credentials
from ..core import effort as effortkit
from ..core import context as contextkit
from ..core import prompts as promptkit
from ..core import persistence as persistkit
from ..core import providers
from ..core import review
from ..core import sessions as sessionkit
from ..core import setup_llama
from ..core import tools as toolkit
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
from .model_picker import describe, paint_item, style_closed_combo, text_colour
from .resource_bar import ResourceBar
from .chat import AssistantBlock, Notice, ThinkingCard, ToolCard, Transcript, UserBubble
from .workers import AgentWorker, CatalogueWorker, DownloadWorker, ServerWorker


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
            # A diff, not the whole new file. Three hundred lines of which
            # four differ is not something anyone reads; it is something
            # everyone clicks Allow on, which is not approval.
            change = review.preview(arguments.get("path", ""),
                                    arguments.get("content", ""))
            head.setText(f"<b>{html_mod.escape(change.headline())}</b>")
            body = QTextBrowser()
            body.setMinimumHeight(300)
            body.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
            body.setHtml(review.html(change, theme.active()))
            layout.addWidget(QLabel("Diff:" if change.exists else "New file:"))
            layout.addWidget(body, 1)
            if change.is_noop:
                allow_anyway = QLabel(
                    "Nothing would change. Allowing this is harmless but "
                    "pointless.")
                allow_anyway.setObjectName("blurb")
                layout.addWidget(allow_anyway)

        buttons = QDialogButtonBox()
        deny = buttons.addButton("Deny", QDialogButtonBox.ButtonRole.RejectRole)
        allow = buttons.addButton("Allow", QDialogButtonBox.ButtonRole.AcceptRole)
        allow.setObjectName("primary")
        deny.setDefault(True)  # the safe option is the one Enter picks
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class Composer(QTextEdit):
    """Input box. Enter sends, Shift+Enter inserts a newline.

    Its height comes from the splitter it lives in rather than from anything
    here. An earlier version put a six-pixel drag strip along its own top
    edge, which was both hard to hit and in competition with selecting the
    first line of text; a splitter handle is a real affordance that Qt already
    draws, and it is where anyone would look for one.
    """

    MIN_HEIGHT = 56

    def __init__(self, on_send, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("composer")
        self.setPlaceholderText("Ask something…   (Enter to send, Shift+Enter for a newline)")
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self._on_send = on_send

    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        if enter and not shift:
            self._on_send()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    #: Seconds a context summary may take before it is abandoned. It runs on
    #: the thread driving the turn, which on the desktop is the GUI thread.
    SUMMARY_TIMEOUT = 45.0

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("hugmunn")
        self.resize(1180, 820)

        self.settings = Settings.load()
        self.server = ServerManager()
        self.history: list[dict[str, Any]] = []
        self.session = sessionkit.Session(
            id=sessionkit.new_id(), started=time.time(), updated=time.time())

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
        self._goal: str = ""
        # Held for the length of a turn, whichever client started it. The
        # desktop tracked its own worker and the browser tracked its own lock,
        # and neither knew about the other -- so a message sent from a phone
        # while the desktop was mid-turn had both appending to one history.
        self._turn_lock = threading.Lock()
        self._remote = None          # core.webserver.RemoteServer, when on
        self._bridge = None          # ui.remote_bridge.RemoteBridge
        self._catalogue_worker = None

        # Restore any cloud catalogue we can reach without blocking startup —
        # the stored list is refreshed in Settings, not on every launch.
        self._build_ui()
        self._refresh_models()
        self._sync_controls()
        # Deferred so the window is on screen before anything modal appears:
        # a dialog over a blank grey rectangle reads as a crash on startup.
        QTimer.singleShot(300, self._offer_restore)
        QTimer.singleShot(700, self._offer_runtime_setup)

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

        app_menu = bar.addMenu("&hugmunn")
        settings = QAction("Settings…", self)
        settings.setShortcut(QKeySequence("Ctrl+,"))
        settings.triggered.connect(self._open_settings)
        app_menu.addAction(settings)
        self.recent_menu = app_menu.addMenu("Recent conversations")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        app_menu.addSeparator()

        runtime = QAction("Set up llama-server…", self)
        runtime.setToolTip("Point hugmunn at the binary that serves local "
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
        accounts.addSeparator()
        refresh = QAction("Refresh model lists", self)
        refresh.setToolTip("Ask each provider what your key can reach now.")
        refresh.triggered.connect(self._refresh_catalogues)
        accounts.addAction(refresh)

    def _build_sidebar(self) -> QWidget:
        """Tabs for the controls, with the meters pinned below them.

        The meters are not a setting. They are what tells you whether the
        machine can take another model right now, and status you have to open
        a tab to find is status nobody looks at. So they sit outside the tab
        stack, always on screen, which is where they were asked to be.
        """
        """The controls column, inside a scroll area.

        It has to scroll. The column is fifteen sections tall and wants about
        1400px; a 1080p screen gives it rather less, and Qt resolves that
        shortfall by compressing children below their size hints. Widgets with
        a fixed height -- the four resource meters -- cannot compress, so they
        are simply drawn outside their frame, on top of whatever is next to
        them. Scrolling is the only arrangement in which nothing can overlap
        at any window size.
        """
        # Tabs, because fifteen sections stacked in one column meant scrolling
        # past the model picker to reach the system prompt, and nothing was
        # ever in view at the same time as the thing it affects.
        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setObjectName("sidebarTabs")
        self.sidebar_tabs.setMinimumWidth(300)
        self.sidebar_tabs.setDocumentMode(True)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

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

        self.freedom_label = QLabel()
        self.freedom_label.setObjectName("blurb")
        self.freedom_label.setWordWrap(True)
        layout.addWidget(self.freedom_label)

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

        layout.addStretch(1)
        scroller.setWidget(panel)
        self.sidebar_tabs.addTab(scroller, "Model")

        panel, layout, scroller = self._new_tab()
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
        layout.addWidget(self._heading("Persistence"))
        self.persistence_combo = QComboBox()
        for level in persistkit.Persistence:
            self.persistence_combo.addItem(persistkit.LABELS[level], int(level))
        self.persistence_combo.setCurrentIndex(
            max(0, self.persistence_combo.findData(self.settings.persistence_level)))
        self.persistence_combo.setToolTip(
            "How hard the loop keeps going before it stops.\n\n"
            "Separate from Effort, which is how carefully the model thinks\n"
            "inside one answer. A tedious migration is low effort and high\n"
            "persistence; a hard question is the reverse."
        )
        self.persistence_combo.currentIndexChanged.connect(self._on_persistence_changed)
        layout.addWidget(self.persistence_combo)
        self.persistence_blurb = QLabel()
        self.persistence_blurb.setObjectName("blurb")
        self.persistence_blurb.setWordWrap(True)
        layout.addWidget(self.persistence_blurb)

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
        self._on_persistence_changed()
        self._on_autonomy_changed()

        layout.addStretch(1)
        scroller.setWidget(panel)
        self.sidebar_tabs.addTab(scroller, "Run")

        panel, layout, scroller = self._new_tab()
        layout.addWidget(self._heading("Reasoning"))
        self.thinking_combo = QComboBox()
        self.thinking_combo.addItem("Off — answer directly", False)
        self.thinking_combo.addItem("On — think first", True)
        self.thinking_combo.setToolTip(
            "Qwen3 templates default this ON, so a model left alone thinks\n"
            "before every answer. Sent per request, so changing it takes\n"
            "effect on the next message with no server restart.\n\n"
            "Thinking helps on hard reasoning; it costs time on everything\n"
            "else, and on the abliterated builds it has been observed to\n"
            "reintroduce refusals that are absent with it off."
        )
        self.thinking_combo.currentIndexChanged.connect(self._on_thinking_changed)
        layout.addWidget(self.thinking_combo)

        self.thinking_blurb = QLabel()
        self.thinking_blurb.setObjectName("blurb")
        self.thinking_blurb.setWordWrap(True)
        layout.addWidget(self.thinking_blurb)

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

        layout.addStretch(1)
        scroller.setWidget(panel)
        self.sidebar_tabs.addTab(scroller, "Context")

        panel, layout, scroller = self._new_tab()
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
        self.prompt_combo = QComboBox()
        for preset in promptkit.PRESETS:
            self.prompt_combo.addItem(preset.label, preset.key)
        self.prompt_combo.addItem("Custom", "custom")
        self.prompt_combo.setToolTip(
            "The largest lever this app has over how a model behaves.\n\n"
            "Assistant framing is part of what brings refusals back on the\n"
            "abliterated builds — Minimal and None stop adding a persona\n"
            "that was never asked for."
        )
        self.prompt_combo.currentIndexChanged.connect(self._on_preset_chosen)
        layout.addWidget(self.prompt_combo)

        self.prompt_note = QLabel()
        self.prompt_note.setObjectName("blurb")
        self.prompt_note.setWordWrap(True)
        layout.addWidget(self.prompt_note)

        self.system_edit = QPlainTextEdit(self.settings.system_prompt)
        self.system_edit.setMaximumHeight(120)
        self.system_edit.textChanged.connect(self._on_system_changed)
        layout.addWidget(self.system_edit)

        layout.addStretch(1)
        scroller.setWidget(panel)
        self.sidebar_tabs.addTab(scroller, "Tools")

        self.sidebar_tabs.addTab(self._build_sessions_tab(), "Sessions")

        panel, layout, scroller = self._new_tab()
        layout.addWidget(self._heading("System prompt"))
        layout.addStretch(1)
        scroller.setWidget(panel)
        self.sidebar_tabs.addTab(scroller, "System")

        self._update_workdir_label()
        self._sync_preset_combo()

        # The column: tabs on top, always-visible status underneath.
        column = QWidget()
        outer = QVBoxLayout(column)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.sidebar_tabs, 1)

        footer = QFrame()
        footer.setObjectName("sidebar")
        strip = QVBoxLayout(footer)
        strip.setContentsMargins(12, 8, 12, 10)
        strip.setSpacing(6)
        self.resources = ResourceBar()
        strip.addWidget(self.resources)

        self.download_progress = QProgressBar()
        self.download_progress.setVisible(False)
        self.download_progress.setTextVisible(True)
        strip.addWidget(self.download_progress)

        self.download_note = QLabel()
        self.download_note.setObjectName("blurb")
        self.download_note.setWordWrap(True)
        self.download_note.setVisible(False)
        strip.addWidget(self.download_note)

        new_chat = QPushButton("New conversation  (Ctrl+L)")
        new_chat.clicked.connect(self._new_conversation)
        strip.addWidget(new_chat)
        outer.addWidget(footer)
        return column

    def _build_sessions_tab(self) -> QWidget:
        """Saved conversations, resumable with everything they were run under.

        Not just the messages: the model, the provider, the autonomy tier,
        effort, persistence, reasoning, context size, skills and system
        prompt. Restoring the words without the settings gives a conversation
        that reads the same and behaves differently, which is worse than not
        restoring it -- the difference is invisible until an answer is wrong.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(self._heading("Sessions"))
        note = QLabel(
            "Every conversation is saved as it happens. Resuming one restores "
            "its settings too, so it continues as it ran."
        )
        note.setObjectName("blurb")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.sessions_list = QListWidget()
        self.sessions_list.setAlternatingRowColors(False)
        self.sessions_list.itemDoubleClicked.connect(
            lambda item: self._resume_selected())
        self.sessions_list.currentItemChanged.connect(self._on_session_selected)
        layout.addWidget(self.sessions_list, 1)

        self.session_detail = QLabel()
        self.session_detail.setObjectName("blurb")
        self.session_detail.setWordWrap(True)
        self.session_detail.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.session_detail)

        row = QHBoxLayout()
        resume = QPushButton("Resume")
        resume.setObjectName("primary")
        resume.clicked.connect(self._resume_selected)
        row.addWidget(resume)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_sessions)
        row.addWidget(refresh)
        delete = QPushButton("Delete")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete_selected)
        row.addWidget(delete)
        layout.addLayout(row)

        self._refresh_sessions()
        return page

    def _refresh_sessions(self) -> None:
        self.sessions_list.clear()
        for session in sessionkit.recent(60):
            item = QListWidgetItem(session.title)
            item.setData(Qt.ItemDataRole.UserRole, session)
            marker = "" if session.closed_cleanly else "  ⚠"
            item.setText(f"{session.title}{marker}\n"
                         f"    {session.turns} turn(s) · {session.age_phrase()}"
                         f" · {session.model_label or session.model_key or '—'}")
            self.sessions_list.addItem(item)
        if not self.sessions_list.count():
            placeholder = QListWidgetItem("No saved conversations yet.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.sessions_list.addItem(placeholder)

    def _selected_session(self):
        item = self.sessions_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_session_selected(self, current, previous) -> None:
        session = self._selected_session()
        if session is None:
            self.session_detail.setText("")
            return
        settings = session.settings or {}
        bits = [f"<b>{html_mod.escape(session.title)}</b>",
                f"{session.turns} turn(s), {session.age_phrase()}"]
        if session.model_label:
            bits.append(f"Model: {html_mod.escape(session.model_label)}")
        if settings:
            bits.append(
                "Autonomy {autonomy}, effort {effort}, persistence "
                "{persistence}, reasoning {thinking}".format(
                    autonomy=settings.get("autonomy_level", "?"),
                    effort=settings.get("effort_level", "?"),
                    persistence=settings.get("persistence_level", "?"),
                    thinking="on" if settings.get("thinking") else "off"))
        if not session.closed_cleanly:
            bits.append("<i>Ended without closing — this one crashed.</i>")
        self.session_detail.setText("<br>".join(bits))

    def _resume_selected(self) -> None:
        session = self._selected_session()
        if session is not None:
            self._restore(session)

    def _delete_selected(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        if QMessageBox.question(
            self, "Delete conversation",
            f"Delete this conversation permanently?\n\n{session.title}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        sessionkit.delete(session)
        self._refresh_sessions()

    def _new_tab(self):
        """A fresh scrollable page for the sidebar. Each tab scrolls on its
        own, so a long one cannot squeeze a short one."""
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel = QFrame()
        panel.setObjectName("sidebar")
        panel.setMinimumWidth(270)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        return panel, layout, scroller

    def _build_conversation(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Transcript above, composer below, with a handle between them. The
        # handle is how the message box is resized -- drag it up for a stack
        # trace, down for a one-liner -- and the split is remembered.
        self.conversation_split = QSplitter(Qt.Orientation.Vertical)
        self.conversation_split.setChildrenCollapsible(False)
        self.conversation_split.setHandleWidth(6)

        self.transcript = Transcript()
        self.conversation_split.addWidget(self.transcript)
        layout.addWidget(self.conversation_split, 1)

        bar = QFrame()
        bar.setStyleSheet(f"border-top: 1px solid {style.BORDER};")
        self.composer_bar = bar  # restyled on a theme change
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 10, 16, 12)
        bar_layout.setSpacing(8)

        self.composer = Composer(self._send)
        self.composer.setToolTip("Drag the divider above to resize.")
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

        self.conversation_split.addWidget(bar)
        self.conversation_split.setStretchFactor(0, 1)   # transcript takes the slack
        self.conversation_split.setStretchFactor(1, 0)
        bar.setMinimumHeight(Composer.MIN_HEIGHT + 22)
        remembered = max(Composer.MIN_HEIGHT + 22, self.settings.composer_height)
        self.conversation_split.setSizes([900, remembered])
        self.conversation_split.splitterMoved.connect(self._on_composer_resized)

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
        # Grouped least to most unlocked, with a heading per group: the list
        # is 23 long now, and how much alignment is left in the weights is
        # the axis a user actually chooses along.
        ordered = [spec for level in config.FREEDOM_ORDER
                   for spec in config.by_freedom(level)]
        seen_levels: set[str] = set()
        for spec in ordered:
            if spec.freedom not in seen_levels:
                seen_levels.add(spec.freedom)
                self.model_combo.addItem(
                    f"— {config.FREEDOM_LABELS[spec.freedom].upper()} —", None)
                heading = self.model_combo.model().item(self.model_combo.count() - 1)
                heading.setEnabled(False)
                paint_item(heading, spec.freedom, available=False)
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
            paint_item(item, spec.freedom, available)
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
            return None       # a group heading, which carries no model
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

        freedom = getattr(spec, "freedom", "vanilla")
        if provider == Provider.LOCAL:
            style_closed_combo(self.model_combo, freedom, spec.is_available())
            self.freedom_label.setText(
                f"<b style='color:{text_colour(freedom)}'>"
                f"{config.FREEDOM_LABELS[freedom]}</b> — {describe(freedom)}")
        else:
            self.model_combo.setStyleSheet("")
            self.freedom_label.setText("")

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
            self._sync_thinking_control()
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

    def _refresh_catalogues(self) -> None:
        for provider in providers.CLOUD:
            if credentials.is_signed_in(provider):
                providers.clear_catalogue(provider)
        current = self._provider()
        if current in providers.CLOUD:
            self._fetch_catalogue(current)

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

        Same guard as _offer_restore: scheduled on a timer, so it can fire
        after the window is gone.

        Asked once per launch and only when it is actually the blocker: there
        are weights to run, and nothing to run them with. Staying silent would
        leave the user with a model list where nothing starts and no
        indication of why.
        """
        if sip.isdeleted(self):
            return
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
                # The build cannot use the card at all -- which hugmunn may
                # have caused, by building without the CUDA toolkit present.
                QMessageBox.warning(
                    self, "Running on the CPU",
                    f"{model_name} loaded, but llama-server is a <b>CPU-only "
                    f"build</b> and cannot use your GPU. That is why it is slow.\n\n"
                    f"This happens when the CUDA toolkit was not installed at "
                    f"build time. Install it and rebuild:\n\n"
                    f"    sudo apt install nvidia-cuda-toolkit\n\n"
                    f"then hugmunn → Set up llama-server… and build again.",
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

    def _persistence(self) -> persistkit.Persistence:
        return persistkit.Persistence(self.persistence_combo.currentData())

    def _on_persistence_changed(self) -> None:
        level = self._persistence()
        self.settings.persistence_level = int(level)
        self.settings.save()
        self.persistence_blurb.setText(persistkit.BLURBS[level])

    def _on_autonomy_changed(self) -> None:
        level = self._autonomy()
        self.settings.autonomy_level = int(level)
        self.settings.save()
        self.autonomy_blurb.setText(
            autonomykit.BLURBS[level] + "  "
            + autonomykit.cloud_note(level, self._provider().value)
        )

    # ------------------------------------------------------------ commands

    def run_command(self, text: str):
        """Handle a slash command. Safe to call from any thread for the
        read-only ones; the mutating ones marshal onto the GUI thread."""
        name, argument = commandkit.parse(text)
        handler = getattr(self, f"_cmd_{name.replace('-', '_')}", None)
        if handler is None:
            return commandkit.Outcome(message=commandkit.unknown(name))
        try:
            return handler(argument)
        except Exception as exc:  # noqa: BLE001 - a bad command is not a crash
            return commandkit.Outcome(message=f"/{name} failed: {exc}")

    def _cmd_help(self, argument: str):
        if argument:
            detail = commandkit.detail_for(argument.lstrip("/"))
            return commandkit.Outcome(message=detail or commandkit.unknown(argument))
        return commandkit.Outcome(message=commandkit.help_text())

    def _cmd_goal(self, argument: str):
        if argument.lower() in ("off", "clear", "none"):
            was, self._goal = self._goal, ""
            return commandkit.Outcome(
                message=f"Goal cleared: {was}" if was else "No goal was set.")
        if not argument:
            return commandkit.Outcome(
                message=f"Goal: {self._goal}" if self._goal
                else "No goal set. /goal <objective> to set one.")
        self._goal = argument
        return commandkit.Outcome(message=(
            f"Goal set: {argument}\n\n"
            f"The agent will work toward this across up to "
            f"{commandkit.GOAL_MAX_ITERATIONS} tool rounds and report whether "
            f"it was met, blocked, or partly met. /goal off to clear."))

    def _cmd_remote(self, argument: str):
        action = (argument or "on").lower()
        if action in ("off", "stop"):
            return commandkit.Outcome(message=self.stop_remote())
        if action in ("url", "status"):
            if self._remote is None or not self._remote.is_running:
                return commandkit.Outcome(message="Remote access is off. /remote on")
            return commandkit.Outcome(message=self._remote_description())
        return commandkit.Outcome(message=self.start_remote(), refresh=True)

    def _cmd_stop(self, argument: str):
        self._cancel()
        if self._bridge is not None:
            self._bridge.cancel()
        return commandkit.Outcome(message="Cancelled.")

    def _cmd_clear(self, argument: str):
        self._new_conversation()
        return commandkit.Outcome(message="New conversation.")

    def _cmd_save(self, argument: str):
        self._persist()
        return commandkit.Outcome(message=f"Saved to {self.session.path}")

    def _cmd_autonomy(self, argument: str):
        if not argument:
            return commandkit.Outcome(message="\n".join(
                ("→ " if int(l) == self.settings.autonomy_level else "  ")
                + f"{int(l)}  {autonomykit.LABELS[l]}" for l in autonomykit.Autonomy))
        index = self.autonomy_combo.findData(int(argument))
        if index < 0:
            return commandkit.Outcome(message="Autonomy is 1 to 4. /autonomy to list.")
        self.autonomy_combo.setCurrentIndex(index)
        return commandkit.Outcome(
            message=autonomykit.LABELS[self._autonomy()], refresh=True)

    def _cmd_persistence(self, argument: str):
        if not argument:
            return commandkit.Outcome(message="\n".join(
                ("→ " if int(l) == self.settings.persistence_level else "  ")
                + f"{int(l)}  {persistkit.LABELS[l]:18} {persistkit.summary(l)}"
                for l in persistkit.Persistence))
        wanted = argument.strip().lower()
        by_name = {"light": 1, "normal": 2, "persistent": 3, "relentless": 4}
        value = by_name.get(wanted, int(wanted) if wanted.isdigit() else 0)
        index = self.persistence_combo.findData(value)
        if index < 0:
            return commandkit.Outcome(
                message="Persistence is 1-4, or light/normal/persistent/relentless.")
        self.persistence_combo.setCurrentIndex(index)
        level = self._persistence()
        return commandkit.Outcome(
            message=f"{persistkit.LABELS[level]} — {persistkit.summary(level)}",
            refresh=True)

    def _cmd_effort(self, argument: str):
        if not argument:
            return commandkit.Outcome(message="\n".join(
                ("→ " if int(l) == self.settings.effort_level else "  ")
                + f"{int(l)}  {effortkit.LABELS[l]}" for l in effortkit.Effort))
        index = self.effort_combo.findData(int(argument))
        if index < 0:
            return commandkit.Outcome(message="Effort is 1 to 4. /effort to list.")
        self.effort_combo.setCurrentIndex(index)
        return commandkit.Outcome(message=effortkit.LABELS[self._effort()], refresh=True)

    def _cmd_think(self, argument: str):
        if not argument:
            return commandkit.Outcome(
                message=f"Reasoning is {'on' if self._thinking_enabled() else 'off'}.")
        wanted = argument.lower() in ("on", "true", "yes", "1")
        index = self.thinking_combo.findData(wanted)
        if index >= 0:
            self.thinking_combo.setCurrentIndex(index)
        return commandkit.Outcome(
            message=f"Reasoning {'on' if wanted else 'off'}.", refresh=True)

    def _cmd_context(self, argument: str):
        spec = self._current_spec()
        if not argument:
            budget = self._context_budget()
            return commandkit.Outcome(message=(
                f"{contextkit.total_tokens(self.history):,} used of "
                f"{budget.available:,} usable "
                f"({budget.limit:,} window, {budget.preamble:,} preamble)"))
        self.context_spin.setValue(int(argument))
        return commandkit.Outcome(
            message=f"Context set to {int(argument):,} tokens. "
                    f"Restart the server to apply.", refresh=True)

    def _cmd_theme(self, argument: str):
        if not argument:
            return commandkit.Outcome(message="\n".join(
                ("→ " if n == self.settings.theme else "  ") + f"{n}  ({theme.LABELS[n]})"
                for n in ("system", *theme.THEMES)))
        if argument not in theme.THEMES and argument != "system":
            return commandkit.Outcome(message=f"No theme called {argument}. /theme to list.")
        self.apply_theme(argument)
        return commandkit.Outcome(message=f"Theme: {theme.LABELS[argument]}", refresh=True)

    def _cmd_model(self, argument: str):
        if not argument:
            return commandkit.Outcome(message="\n".join(
                ("→ " if s.key == self.settings.model_key else "  ")
                + f"{s.key:18} {s.label}" for s in config.REGISTRY))
        index = self.model_combo.findData(argument)
        if index < 0:
            return commandkit.Outcome(message=f"No model {argument}. /model to list.")
        self.model_combo.setCurrentIndex(index)
        return commandkit.Outcome(message=f"Model: {argument}", refresh=True)

    # -------------------------------------------------------------- remote

    def start_remote(self) -> str:
        from ..core import remote as remotekit
        from ..core.webserver import RemoteServer

        from .remote_bridge import RemoteBridge

        if self._remote is not None and self._remote.is_running:
            return self._remote_description()

        if self._bridge is None:
            self._bridge = RemoteBridge(self)
            self._bridge.event.connect(self._on_remote_event)
            self._bridge.approval.connect(self._on_remote_approval)
            self._bridge.state_changed.connect(self._sync_controls)

        self._remote = RemoteServer(self._bridge, port=remotekit.DEFAULT_PORT)
        try:
            self._remote.start()
        except OSError as exc:
            self._remote = None
            return (f"Could not start on port {remotekit.DEFAULT_PORT}: {exc}\n"
                    f"Another copy of hugmunn may already have it.")
        return self._remote_description()

    def stop_remote(self) -> str:
        if self._remote is None or not self._remote.is_running:
            return "Remote access was already off."
        self._remote.stop()
        self._remote = None
        return "Remote access off. The address no longer resolves."

    def _remote_description(self) -> str:
        from ..core import remote as remotekit

        server = self._remote
        lines = [
            "Remote access is on.",
            "",
            f"  {server.url()}",
            "",
            server.exposure.warning(),
            "",
            "The link carries the token. Anyone who has it can run commands "
            "on this machine, so treat it as a password.",
            "",
            "To reach it from another network, run one of these here:",
        ]
        for option in remotekit.tunnel_options(server.port):
            lines += ["", f"  {option['name']}", f"    {option['command']}",
                      f"    {option['note']}"]
        return "\n".join(lines)

    def _on_remote_event(self, payload: dict) -> None:
        """Mirror a browser-driven turn into the desktop transcript."""
        kind = payload.get("kind")
        text = payload.get("text", "")
        if kind == "user":
            self.transcript.add(UserBubble(text))
        elif kind == "command":
            self.transcript.add(UserBubble(text))
            self.transcript.add(Notice(payload.get("result", ""),
                                       theme.active()["fg_dim"]))
        elif kind == "reasoning":
            self._on_reasoning(text)
        elif kind == "content":
            self._on_content(text)
        elif kind == "tool_start":
            self._on_tool_start(payload.get("tool_name", ""),
                                payload.get("tool_summary", ""),
                                payload.get("tool_id", ""))
        elif kind == "tool_result":
            self._on_tool_result(payload.get("tool_name", ""),
                                 payload.get("tool_summary", ""), text)
        elif kind == "error":
            self.transcript.add(Notice(text, theme.active()["error"]))
        elif kind == "done":
            self._on_turn_finished({})
        self.transcript.follow()

    def _on_remote_approval(self, call_id: str, name: str, summary: str,
                            arguments: dict) -> None:
        """Show the desktop dialog for a call the browser started.

        Non-modal on purpose: the phone may answer first, and a modal the
        desktop cannot dismiss would then have to be clicked anyway.
        """
        dialog = ApprovalDialog(name, summary, arguments, self)
        dialog.setModal(False)
        dialog.finished.connect(
            lambda result, cid=call_id: self._bridge.resolve_approval(
                cid, result == QDialog.DialogCode.Accepted))
        dialog.show()

    # ------------------------------------------------------------ sessions

    def _persist(self) -> None:
        """Write the conversation as it stands.

        Called after the user's message and again after the reply, not only at
        the end of a turn: a crash during generation should still leave the
        question behind, and the question is often the expensive part to
        reconstruct.
        """
        if not self.history:
            return
        spec = self._current_spec()
        self.session.messages = list(self.history)
        self.session.provider = self._provider().value
        self.session.model_key = spec.key if spec else ""
        self.session.model_label = spec.label if spec else ""
        self.session.goal = self._goal
        # Everything that changes how the same words behave.
        self.session.settings = {
            "autonomy_level": self.settings.autonomy_level,
            "effort_level": self.settings.effort_level,
            "persistence_level": self.settings.persistence_level,
            "thinking": self._thinking_enabled(),
            "system_prompt": self.settings.system_prompt,
            "enabled_skills": sorted(self._enabled_skills),
            "tools_enabled": self.settings.tools_enabled,
            "context_strategy": self.settings.context_strategy,
            "context_size": (spec.effective_ctx_size
                             if spec and not self._is_cloud() else 0),
            "workdir": self.settings.workdir,
        }
        sessionkit.save(self.session)

    def _offer_restore(self) -> None:
        """On launch, offer back a conversation the process did not survive.

        Guarded against firing into a window that has already gone: this is
        scheduled on a timer, and a timer outlives the widget that set it —
        closing the window inside the delay is an ordinary thing to do. A
        modal opened from a dead window is a hang, not a dialog.

        Only when it ended *uncleanly*. Offering to restore something the user
        deliberately finished teaches them to dismiss the dialog without
        reading it, which is exactly when it will matter.
        """
        if sip.isdeleted(self):
            return
        previous = sessionkit.unfinished()
        if previous is None or previous.id == self.session.id:
            return
        answer = QMessageBox.question(
            self, "Restore the last conversation?",
            f"hugmunn did not shut down cleanly last time, and this "
            f"conversation was still open:\n\n<b>{html_mod.escape(previous.title)}</b>\n"
            f"{previous.turns} turn(s), {previous.age_phrase()}"
            + (f"\n{html_mod.escape(previous.model_label)}" if previous.model_label else "")
            + "\n\nReload it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._restore(previous)
        else:
            # Answered once; do not ask again on the next launch.
            sessionkit.mark_closed(previous)

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        found = [s for s in sessionkit.recent(20) if s.id != self.session.id]
        if not found:
            act = QAction("Nothing saved yet", self)
            act.setEnabled(False)
            self.recent_menu.addAction(act)
            return
        for session in found:
            act = QAction(session.summary(), self)
            act.triggered.connect(lambda _=False, s=session: self._restore(s))
            self.recent_menu.addAction(act)

    def _restore(self, session) -> None:
        """Load a saved conversation and rebuild the transcript from it.

        The history alone is not enough: the transcript is widgets, and a
        restored conversation whose messages are present but whose window is
        empty looks like the restore failed.
        """
        if self._agent_worker is not None:
            return
        self.history = list(session.messages)
        self.session = session
        self.session.closed_cleanly = False
        self.transcript.clear()
        self.stats.clear()
        self._rebuild_transcript(self.history)

        # Put the model back too, when it is still available.
        if session.provider and session.provider != self._provider().value:
            index = self.provider_combo.findData(session.provider)
            if index >= 0:
                self.provider_combo.setCurrentIndex(index)
        if session.model_key:
            index = self.model_combo.findData(session.model_key)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)

        restored = self._restore_settings(session)
        self._goal = session.goal or ""
        note = f"Restored {session.turns} turn(s) from {session.age_phrase()}."
        if restored:
            note += "  " + ", ".join(restored) + " restored."
        if session.goal:
            note += f"  Goal: {session.goal}"
        self.transcript.add(Notice(note, theme.active()["fg_dim"]))
        self.transcript.follow()
        self._update_context_meter()
        sessionkit.save(self.session)

    def _restore_settings(self, session) -> list[str]:
        """Put the controls back the way this conversation ran.

        Named individually rather than assigned wholesale so a session saved
        by an older version -- missing half of these -- restores what it has
        and leaves the rest alone, instead of resetting them to defaults it
        never recorded.
        """
        saved = session.settings or {}
        if not saved:
            return []
        changed: list[str] = []

        for key, combo, label in (
            ("autonomy_level", getattr(self, "autonomy_combo", None), "autonomy"),
            ("effort_level", getattr(self, "effort_combo", None), "effort"),
            ("persistence_level", getattr(self, "persistence_combo", None),
             "persistence"),
            ("context_strategy", getattr(self, "context_combo", None),
             "context handling"),
        ):
            if key in saved and combo is not None:
                index = combo.findData(int(saved[key]))
                if index >= 0 and index != combo.currentIndex():
                    combo.setCurrentIndex(index)
                    changed.append(label)

        if "thinking" in saved and hasattr(self, "thinking_combo"):
            index = self.thinking_combo.findData(bool(saved["thinking"]))
            if index >= 0:
                self.thinking_combo.setCurrentIndex(index)
                changed.append("reasoning")

        if saved.get("system_prompt") and saved["system_prompt"] != self.settings.system_prompt:
            self.system_edit.setPlainText(saved["system_prompt"])
            changed.append("system prompt")

        if "enabled_skills" in saved:
            wanted = set(saved["enabled_skills"])
            if wanted != self._enabled_skills:
                self._set_skills(wanted)
                changed.append("skills")

        if saved.get("context_size") and hasattr(self, "context_spin"):
            if saved["context_size"] != self.context_spin.value():
                self.context_spin.setValue(int(saved["context_size"]))
                changed.append("context size")

        if saved.get("workdir") and saved["workdir"] != self.settings.workdir:
            self.settings.workdir = saved["workdir"]
            self._update_workdir_label()
            changed.append("working directory")

        self.settings.save()
        return changed

    def _rebuild_transcript(self, messages: list[dict[str, Any]]) -> None:
        """Redraw saved messages as the widgets they were.

        Tool results are matched to their call by id, which is why the
        compression rules keep the two together — a restored transcript with
        an orphaned result would render a call that never returned.
        """
        cards: dict[str, ToolCard] = {}
        for message in messages:
            role = message.get("role")
            content = message.get("content")

            if role == "user":
                text = str(content or "")
                if text.startswith("[Earlier in this conversation"):
                    self.transcript.add(Notice(text, theme.active()["fg_dim"]))
                else:
                    self.transcript.add(UserBubble(text))
            elif role == "assistant":
                if content:
                    block = AssistantBlock()
                    self.transcript.add(block)
                    block.append(str(content))
                    block.restyle()
                    block.finish()
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    name = function.get("name", "?")
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    summary = toolkit.summarize_call(name, args if isinstance(args, dict) else {})
                    card = ToolCard(name, summary)
                    self.transcript.add(card)
                    cards[call.get("id", "")] = card
            elif role == "tool":
                card = cards.get(message.get("tool_call_id", ""))
                if card is not None:
                    card.set_result(str(content or ""))
        self.transcript.scroll_to_bottom()

    # ----------------------------------------------------------- reasoning

    def _thinking_enabled(self) -> bool | None:
        """Whether the model should think first. None for cloud models.

        Not ``_thinking``: that name is already the live ThinkingCard widget,
        and an instance attribute shadows a method of the same name -- so the
        method was unreachable and every send raised ``'NoneType' object is
        not callable``.

        On Claude and GPT this is the effort tier's thinking budget, set at
        request time by the client, so a second control here would be two
        dials on one mechanism.
        """
        if self._is_cloud():
            return None
        return bool(self.thinking_combo.currentData())

    def _on_thinking_changed(self) -> None:
        spec = self._current_spec()
        if spec is None or self._is_cloud():
            return
        wanted = bool(self.thinking_combo.currentData())
        self.settings.thinking[spec.key] = wanted
        self.settings.save()
        self._describe_thinking(wanted)

    def _describe_thinking(self, wanted: bool) -> None:
        if self._is_cloud():
            self.thinking_blurb.setText(
                "Set by the effort tier on this provider.")
        elif wanted:
            self.thinking_blurb.setText(
                "Slower, and stronger on multi-step problems. Thinking is shown "
                "separately from the answer.")
        else:
            self.thinking_blurb.setText(
                "Answers begin immediately. This model's default.")

    def _sync_thinking_control(self) -> None:
        spec = self._current_spec()
        cloud = self._is_cloud()
        self.thinking_combo.setEnabled(spec is not None and not cloud)
        if spec is None:
            return
        # The saved preference, else whatever the model ships with.
        wanted = self.settings.thinking.get(
            spec.key, False if cloud else spec.reasoning != "off")
        self.thinking_combo.blockSignals(True)
        self.thinking_combo.setCurrentIndex(
            max(0, self.thinking_combo.findData(bool(wanted))))
        self.thinking_combo.blockSignals(False)
        self._describe_thinking(bool(wanted))

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

    def _on_system_changed(self) -> None:
        self.settings.system_prompt = self.system_edit.toPlainText()
        self.settings.save()
        self._sync_preset_combo()
        if hasattr(self, "context_meter"):
            self._update_context_meter()

    def _on_preset_chosen(self) -> None:
        key = self.prompt_combo.currentData()
        preset = promptkit.BY_KEY.get(key)
        if preset is None:          # "Custom" is a label for what is there
            self.prompt_note.setText("Edited by hand.")
            return
        self.system_edit.blockSignals(True)
        self.system_edit.setPlainText(preset.text)
        self.system_edit.blockSignals(False)
        self.settings.system_prompt = preset.text
        self.settings.save()
        self.prompt_note.setText(preset.note)
        if hasattr(self, "context_meter"):
            self._update_context_meter()

    def _sync_preset_combo(self) -> None:
        key = promptkit.match(self.settings.system_prompt)
        self.prompt_combo.blockSignals(True)
        self.prompt_combo.setCurrentIndex(max(0, self.prompt_combo.findData(key)))
        self.prompt_combo.blockSignals(False)
        preset = promptkit.BY_KEY.get(key)
        self.prompt_note.setText(preset.note if preset else "Edited by hand.")

    def _on_composer_resized(self, *_args) -> None:
        """Remember how tall the message box was left."""
        sizes = self.conversation_split.sizes()
        if len(sizes) == 2 and sizes[1] > 0:
            self.settings.composer_height = int(sizes[1])
            self.settings.save()

    def _new_conversation(self) -> None:
        if self._agent_worker is not None:
            return
        if self.history:
            sessionkit.mark_closed(self.session)
        self.history.clear()
        self.session = sessionkit.Session(
            id=sessionkit.new_id(), started=time.time(), updated=time.time())
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
        if not self._turn_lock.acquire(blocking=False):
            self.transcript.add(Notice(
                "A turn started from the remote session is still running.",
                theme.active()["warning"]))
            self.transcript.follow()
            return
        text = self.composer.toPlainText().strip()
        if not text:
            return

        # Resolved here, never sent. A model asked to interpret "/remote"
        # explains what it thinks the word means.
        if commandkit.is_command(text):
            outcome = self.run_command(text)
            if outcome.handled:
                self.composer.clear()
                self.transcript.add(UserBubble(text))
                self.transcript.add(Notice(outcome.message, theme.active()["fg_dim"]))
                self.transcript.follow()
                self._turn_lock.release()
                return
            text = outcome.send_text or text

        client = self._build_client()
        if client is None:
            self._turn_lock.release()
            QMessageBox.warning(
                self, "Not signed in",
                f"Sign in to {providers.LABELS[self._provider()]} before sending.",
            )
            return
        self.composer.clear()

        self.transcript.add(UserBubble(text))
        self.history.append({"role": "user", "content": text})
        # Saved before generation starts: a crash mid-reply should still leave
        # the question behind.
        self._persist()

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
            self._turn_lock.release()
            return
        if outcome.dropped:
            self.history[:] = outcome.history
            self.transcript.add(Notice(outcome.note, style.WARN))
            self.transcript.follow()

        agent = self.build_agent(client)
        assert agent is not None

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

        Runs on whichever thread is driving the turn. On the desktop that is
        the GUI thread, so it is bounded: a summary of a full context on a
        slow model is tens of seconds, and a frozen window reads as a crash.
        Beyond the budget it gives up and the caller falls back to dropping
        turns, which loses detail but not the conversation.
        """
        import threading as _threading

        deadline = _threading.Event()
        timer = _threading.Timer(self.SUMMARY_TIMEOUT, deadline.set)
        timer.daemon = True
        timer.start()
        client = self._build_client()
        if client is None:
            return ""
        parts = []
        try:
            for event in client.stream(contextkit.summary_request(messages),
                                       max_tokens=800, cancel=deadline):
                if event.kind == "content":
                    parts.append(event.text)
                elif event.kind == "error":
                    return ""
        finally:
            timer.cancel()
        return "" if deadline.is_set() else "".join(parts)

    def build_agent(self, client=None):
        """The one place an Agent is constructed.

        Both the desktop and the remote bridge come through here, so a
        setting cannot apply to one and not the other -- which is the shape
        of bug that made the autonomy tier look broken.
        """
        client = client or self._build_client()
        if client is None:
            return None
        prompt = self.settings.system_prompt
        iterations = persistkit.max_rounds(self._persistence())
        if self._goal:
            # An objective changes the loop, not only the wording: stopping at
            # the first plausible answer is the failure it exists to prevent,
            # and the default round limit is sized for a single question.
            prompt = f"{prompt}\n\n{commandkit.goal_block(self._goal)}"
            iterations = max(iterations, commandkit.GOAL_MAX_ITERATIONS)
        return Agent(
            client=client,
            workdir=self.settings.workdir,
            system_prompt=prompt,
            use_tools=self._tools_active(),
            max_iterations=iterations,
            active_skills=self._active_skills(),
            extra_tools=self._active_plugins(),
            effort=self._effort(),
            persistence=self._persistence(),
            autonomy=self._autonomy(),
            thinking=self._thinking_enabled(),
        )

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
        self._persist()
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
        if self._turn_lock.locked():
            self._turn_lock.release()
        self._sync_controls()
        QTimer.singleShot(0, self.composer.setFocus)

    # ---------------------------------------------------------------- close

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        # Before anything else: a one-second timer that fires during teardown
        # reaches widgets Qt has already destroyed.
        self.resources.stop()
        # The HTTP server holds a listening socket and a thread. Daemon
        # threads die with the process, but the port stays bound until then --
        # which is the difference between reopening the app and being told
        # the address is already in use.
        if self._remote is not None:
            self._remote.stop()
            self._remote = None
        if self._bridge is not None:
            self._bridge.cancel()
        if self._agent_worker is not None:
            self._agent_worker.cancel()
            self._agent_worker.wait(3000)
        self.server.stop()
        self.settings.save()
        # The flag that tells the next launch this was a quit and not a crash.
        if self.history:
            self._persist()
            sessionkit.mark_closed(self.session)
        sessionkit.prune()
        super().closeEvent(event)
