"""Main window: model/server controls on the left, conversation on the right."""

from __future__ import annotations

import html as html_mod
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut, QTextOption
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSizePolicy, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from .. import config
from ..config import ModelSpec, Settings
from ..core.agent import Agent
from ..core.client import LlamaClient
from ..core.server import ServerManager
from . import style
from .chat import AssistantBlock, Notice, ThinkingCard, ToolCard, Transcript, UserBubble
from .workers import AgentWorker, ServerWorker


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

        self._server_worker: ServerWorker | None = None
        self._agent_worker: AgentWorker | None = None
        self._assistant: AssistantBlock | None = None
        self._thinking: ThinkingCard | None = None
        self._tool_cards: dict[str, ToolCard] = {}

        self._build_ui()
        self._refresh_models()
        self._sync_controls()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_conversation())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 880])
        self.setCentralWidget(splitter)

        QShortcut(QKeySequence("Ctrl+L"), self, self._new_conversation)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._send)

    def _build_sidebar(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sidebar")
        panel.setMinimumWidth(270)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        layout.addWidget(self._heading("Model"))
        self.model_combo = QComboBox()
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        layout.addWidget(self.model_combo)

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
        layout.addWidget(self._heading("System prompt"))
        self.system_edit = QPlainTextEdit(self.settings.system_prompt)
        self.system_edit.setMaximumHeight(120)
        self.system_edit.textChanged.connect(self._on_system_changed)
        layout.addWidget(self.system_edit)

        layout.addStretch(1)
        new_chat = QPushButton("New conversation  (Ctrl+L)")
        new_chat.clicked.connect(self._new_conversation)
        layout.addWidget(new_chat)

        self._update_workdir_label()
        return panel

    def _build_conversation(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.transcript = Transcript()
        layout.addWidget(self.transcript, 1)

        bar = QFrame()
        bar.setStyleSheet(f"border-top: 1px solid {style.BORDER};")
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

    # ------------------------------------------------------------- model list

    def _refresh_models(self) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for spec in config.REGISTRY:
            available = spec.is_available()
            label = spec.label if available else f"{spec.label}  — not downloaded"
            self.model_combo.addItem(label, spec.key)
            index = self.model_combo.count() - 1
            self.model_combo.model().item(index).setEnabled(available)
        wanted = self.model_combo.findData(self.settings.model_key)
        if wanted >= 0 and self.model_combo.model().item(wanted).isEnabled():
            self.model_combo.setCurrentIndex(wanted)
        else:
            first = next((i for i in range(self.model_combo.count())
                          if self.model_combo.model().item(i).isEnabled()), 0)
            self.model_combo.setCurrentIndex(first)
        self.model_combo.blockSignals(False)
        self._on_model_changed()

    def _current_spec(self) -> ModelSpec | None:
        return config.by_key(self.model_combo.currentData())

    def _on_model_changed(self) -> None:
        spec = self._current_spec()
        if spec is None:
            return
        self.settings.model_key = spec.key
        self.settings.save()
        note = spec.blurb
        if spec.ram_gb:
            note += f"  Needs ~{spec.ram_gb} GB free RAM."
        self.model_blurb.setText(note)
        if not self.server.is_running:
            self.server_status.setText("Stopped")

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
        if not spec.is_available():
            QMessageBox.warning(self, "Not downloaded",
                                f"{spec.label} has not finished downloading yet.")
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
        self.server_status.setText(f"Ready · {model_name}")
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

    # ------------------------------------------------------------ the turn

    def _sync_controls(self) -> None:
        running = self.server.is_running
        busy = self._agent_worker is not None
        self.server_button.setEnabled(not busy)
        self.server_button.setText("Stop server" if running else "Start server")
        self.send_button.setEnabled(running and not busy)
        self.send_button.setVisible(not busy)
        self.stop_button.setVisible(busy)
        self.model_combo.setEnabled(not busy and not running)
        self.composer.setEnabled(not busy)

    def _send(self) -> None:
        if self._agent_worker is not None or not self.server.is_running:
            return
        text = self.composer.toPlainText().strip()
        if not text:
            return
        self.composer.clear()

        self.transcript.add(UserBubble(text))
        self.history.append({"role": "user", "content": text})

        spec = self._current_spec()
        assert spec is not None
        agent = Agent(
            client=LlamaClient(spec.base_url),
            workdir=self.settings.workdir,
            system_prompt=self.settings.system_prompt,
            use_tools=self.settings.tools_enabled,
            auto_approve_reads=self.settings.auto_approve_reads,
            max_iterations=self.settings.max_tool_iterations,
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
