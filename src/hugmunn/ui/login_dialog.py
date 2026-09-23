"""Browser sign-in owned by Claude Code or Codex, without API-key storage."""
from __future__ import annotations

import re
import threading

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from ..core import cli
from ..core.providers import LABELS, Provider


class LoginDialog(QDialog):
    """Install guidance, browser login and a bounded background account check."""
    checked = Signal(object)

    def __init__(self, provider: Provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.models = ()
        self._process = None
        self._checking = False
        self.setWindowTitle(f"Connect {LABELS[provider]}")
        self.setMinimumWidth(570)
        layout = QVBoxLayout(self)
        label = QLabel(f"Hugmunn uses {LABELS[provider]} with its own subscription login. "
                       "Prompts and tool results go to the provider. Your plan's model access and "
                       "usage limits apply. Hugmunn does not request or save an API key.")
        label.setWordWrap(True)
        layout.addWidget(label)
        command = cli.NAMES[provider] + " " + " ".join(cli.login_arguments(provider))
        hint = QLabel(f"You can also sign in from a terminal:\n{command}\nThen click Check connection.")
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(hint)
        install = QPushButton(f"Install or update {cli.NAMES[provider]}…")
        install.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(cli.INSTALL_URLS[provider])))
        layout.addWidget(install)
        self.login = QPushButton("Sign in with browser")
        self.login.clicked.connect(self._login)
        layout.addWidget(self.login)
        self.status = QLabel(cli.status(provider).message)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.verify = self.buttons.addButton("Check connection", QDialogButtonBox.ButtonRole.ActionRole)
        self.verify.clicked.connect(self._verify)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.checked.connect(self._checked)
        self._verify()

    def _verify(self):
        if self._checking:
            return
        self._checking = True
        self.verify.setEnabled(False)
        self.status.setText("Checking CLI subscription login…")
        def check():
            result = cli.status(self.provider, refresh=True)
            try:
                self.checked.emit(result)
            except RuntimeError:
                pass
        threading.Thread(target=check, daemon=True).start()

    def _checked(self, result):
        self._checking = False
        self.verify.setEnabled(True)
        self.login.setEnabled(result.installed and self._process is None)
        self.status.setText(result.message)
        if result.signed_in:
            self.models = cli.refresh_models(self.provider)
            self.accept()

    def _login(self):
        path = cli.executable(self.provider)
        if not path or self._process is not None:
            return
        process = QProcess(self)
        env = QProcessEnvironment()
        for key, value in cli.environment().items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._output)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(lambda *_: self.status.setText("Could not start login. Use the terminal command above."))
        self._process = process
        self.login.setEnabled(False)
        self.status.setText("Complete sign-in in your browser. If no browser opens, use the terminal command above.")
        process.start(path, cli.login_arguments(self.provider))

    def _output(self):
        # Display only the vendor's login URL, not arbitrary process output or tokens.
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        urls = re.findall(r'https://(?:claude\.ai|auth\.openai\.com|chatgpt\.com)/[^\s\x1b]+', text)
        if urls:
            self.status.setText("Open this sign-in address if your browser did not open:\n" + urls[-1])

    def _finished(self, *_args):
        process, self._process = self._process, None
        if process:
            process.deleteLater()
        self.login.setEnabled(True)
        self._verify()

    def done(self, result):
        if self._process is not None:
            process, self._process = self._process, None
            process.blockSignals(True)
            process.terminate()
            if not process.waitForFinished(1000):
                process.kill()
                process.waitForFinished(1000)
        super().done(result)
