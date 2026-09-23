"""First-run hardware, appearance, accounts and final agreement pages."""

from __future__ import annotations

import re
import shutil
import threading
from importlib.resources import files

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTextBrowser, QVBoxLayout, QWizard, QWizardPage,
)

from .. import config
from ..core import cli, onboarding, providers, reporting
from . import branding, theme
from .login_dialog import LoginDialog


class _Result(QObject):
    ready = Signal(str, object)


class SetupWizard(QWizard):
    """Persist setup only on Finish; account sign-ins remain with their credential stores."""

    def __init__(self, settings: config.Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._original_theme = settings.theme
        self._github_account = settings.github_account
        self._login: QProcess | None = None
        self._login_output = ""
        self._github_check_cancelled = False
        self._result = _Result(self)
        self._result.ready.connect(self._received)
        self.setWindowTitle("Welcome to Hugmunn")
        self.setWindowIcon(branding.window_icon())
        self.setMinimumSize(720, 600)
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setButtonText(QWizard.WizardButton.FinishButton, "Agree and start Hugmunn")

        page, layout = self._page("Thought and memory", "Check this computer")
        self.brand = QLabel()
        self.brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.brand)
        layout.addWidget(self._label("Huginn and Muninn are Odin's ravens: thought and memory. "
            "Hugmunn brings local models and cloud AI into one workspace. This setup checks your "
            "hardware, lets you choose a theme and connect accounts, and ends with the user agreement."))
        self.hardware = QTextBrowser()
        self.hardware.setPlainText("Checking hardware locally…")
        self.hardware.setMinimumHeight(210)
        layout.addWidget(self.hardware)
        self.backend = QComboBox()
        for key, label in (("auto", "Automatic"), ("cpu", "CPU only"), ("cuda", "NVIDIA CUDA"),
                           ("metal", "Apple Metal"), ("vulkan", "Vulkan — AMD / Intel / NVIDIA")):
            self.backend.addItem(label, key)
        self.backend.setCurrentIndex(max(0, self.backend.findData(settings.runtime_backend)))
        layout.addWidget(QLabel("Local model backend (cloud models do not use this):"))
        layout.addWidget(self.backend)
        layout.addWidget(self._label("After setup, use Hugmunn → Set up llama-server to install the "
            "selected runtime. No model or runtime is downloaded during these checks."))

        page, layout = self._page("Make yourself at home", "Choose a theme")
        self.theme_choice = QComboBox()
        for name in ("system", *theme.THEMES):
            self.theme_choice.addItem(theme.LABELS[name], name)
        self.theme_choice.setCurrentIndex(max(0, self.theme_choice.findData(settings.theme)))
        self.theme_choice.currentIndexChanged.connect(self._preview_theme)
        layout.addWidget(self.theme_choice)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)
        layout.addWidget(self._label("The theme updates as you choose. You can change it later from View."))

        page, layout = self._page("Connect AI accounts", "Optional — local models need no cloud account")
        layout.addWidget(self._label("Cloud providers receive your prompts and tool results. "
            "Sign in through Claude Code or Codex with your subscription account. "
            "No API key is needed. Your plan’s model access and usage limits apply."))
        self.ai_status: dict = {}
        for provider in providers.CLOUD:
            row = QHBoxLayout()
            button = QPushButton(f"Connect {providers.LABELS[provider]}…")
            button.clicked.connect(lambda checked=False, p=provider: self._connect_ai(p))
            row.addWidget(button)
            status = QLabel()
            row.addWidget(status)
            self.ai_status[provider] = status
            layout.addLayout(row)
        self._refresh_ai()

        page, layout = self._page("Connect GitHub", "Optional — send error reports to the Hugmunn issue tracker")
        layout.addWidget(self._label("Reports go to PUBLIC issues in EinarOlafsson/hugmunn under "
            "your connected GitHub account. Hugmunn uses GitHub CLI's browser sign-in and does not "
            "store a separate GitHub token. Reporting starts only after the final agreement."))
        self.github_status = self._label("No account connected for reports." if not self._github_account
                                         else f"Connected account: {self._github_account}")
        self.github_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.github_status)
        self.device_code = QLabel()
        self.device_code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.device_code)
        row = QHBoxLayout()
        self.login_button = QPushButton("Sign in with browser")
        self.login_button.clicked.connect(self._github_login)
        row.addWidget(self.login_button)
        self.check_button = QPushButton("Connect existing GitHub CLI account")
        self.check_button.clicked.connect(self._check_github)
        row.addWidget(self.check_button)
        layout.addLayout(row)
        install = QPushButton("Get GitHub CLI")
        install.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://cli.github.com/")))
        layout.addWidget(install)
        disconnect = QPushButton("Disconnect reports from this account")
        disconnect.clicked.connect(self._disconnect)
        layout.addWidget(disconnect)
        self.report_choice = QCheckBox("Automatically send minimal error reports to EinarOlafsson/hugmunn")
        self.report_choice.setChecked(settings.automatic_reports)
        layout.addWidget(self.report_choice)
        layout.addWidget(self._label("No messages, logs, file contents, paths, keys or hardware identifiers "
            "are collected. Your GitHub username is visible as the issue author. You can turn this off in Accounts."))

        self.agreement_page, layout = self._page("User agreement", "Review the terms before starting")
        self.agreement_view = QTextBrowser()
        self.agreement_view.setPlainText(onboarding.AGREEMENT + "\n\n" +
            files("hugmunn").joinpath("resources", "LICENSE.txt").read_text(encoding="utf-8"))
        layout.addWidget(self.agreement_view, 1)
        self.final_status = self._label("")
        layout.addWidget(self.final_status)
        self.final_reports = QCheckBox("Enable automatic public error reports (requires connected GitHub account)")
        self.final_reports.setChecked(self.report_choice.isChecked())
        self.report_choice.toggled.connect(self.final_reports.setChecked)
        self.final_reports.toggled.connect(self.report_choice.setChecked)
        layout.addWidget(self.final_reports)
        self.agree = QCheckBox("I accept the license and user agreement, including my reporting choice above.")
        self.agree.setChecked(False)
        self.agreement_page.registerField("agreement*", self.agree)
        layout.addWidget(self.agree)
        self.currentIdChanged.connect(self._page_changed)
        self._preview_theme()
        QTimer.singleShot(0, lambda: self._run("hardware", onboarding.inspect_hardware))

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _page(self, title: str, subtitle: str):
        page = QWizardPage()
        page.setTitle(title)
        page.setSubTitle(subtitle)
        layout = QVBoxLayout(page)
        layout.setSpacing(14)
        self.addPage(page)
        return page, layout

    def _run(self, kind: str, function) -> None:
        """Run bounded local/auth checks without blocking the GUI or owning Qt threads."""
        bridge = self._result
        def work():
            try:
                result = function()
            except Exception as exc:
                result = exc
            try:
                bridge.ready.emit(kind, result)
            except RuntimeError:
                pass  # The wizard was closed while the bounded check was running.
        threading.Thread(target=work, daemon=True, name=f"hugmunn-setup-{kind}").start()

    def _received(self, kind: str, result) -> None:
        if kind == "hardware":
            self.hardware.setPlainText("Hardware check unavailable. You can continue with cloud models." if
                                  isinstance(result, Exception) else result.summary())
        elif kind == "github":
            self.check_button.setEnabled(True)
            if self._github_check_cancelled:
                return
            if isinstance(result, Exception):
                self.github_status.setText("Could not verify GitHub. Install gh and sign in, then try again.")
            else:
                self._github_account = result
                self.github_status.setText(f"Connected account: {result}")
                self._page_changed()

    def _preview_theme(self, *_args) -> None:
        theme.set_active(self.theme_choice.currentData())
        QApplication.instance().setStyleSheet(theme.stylesheet())
        self.setWindowIcon(branding.window_icon())
        colours = theme.active()
        palette = self.palette()
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
            palette.setColor(role, QColor(colours["bg"]))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
            palette.setColor(role, QColor(colours["fg"]))
        self.setPalette(palette)
        self.setStyleSheet(f"QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {colours['fg_dim']}; "
                          f"border-radius: 3px; background: {colours['surface']}; }} "
                          f"QCheckBox::indicator:checked {{ background: {colours['accent']}; }}")
        pixmap = branding.pixmap("horizontal", 360)
        self.brand.setPixmap(pixmap)
        self.preview.setPixmap(pixmap)

    def _refresh_ai(self) -> None:
        for provider, label in self.ai_status.items():
            label.setText(cli.status(provider).message)

    def _connect_ai(self, provider) -> None:
        LoginDialog(provider, self).exec()
        self._refresh_ai()

    def _check_github(self) -> None:
        self._github_check_cancelled = False
        self.check_button.setEnabled(False)
        self.github_status.setText("Checking the current GitHub CLI account…")
        self._run("github", reporting.github_account)

    def _disconnect(self) -> None:
        self._github_check_cancelled = True
        self._github_account = ""
        self.github_status.setText("Disconnected from reports. GitHub CLI's login is unchanged.")

    def _github_login(self) -> None:
        if self._login is not None:
            return
        gh = shutil.which("gh")
        if not gh:
            self.github_status.setText("Install GitHub CLI with the button below, then return here to sign in.")
            return
        self._login_output = ""
        process = self._login = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._login_read)
        process.finished.connect(self._login_finished)
        process.errorOccurred.connect(self._login_error)
        self.login_button.setEnabled(False)
        self.github_status.setText("Starting GitHub browser sign-in…")
        process.start(gh, ["auth", "login", "--hostname", "github.com", "--web", "--git-protocol", "https", "--skip-ssh-key"])
        process.write(b"\n")

    def _login_read(self) -> None:
        if self._login is None:
            return
        self._login_output = (self._login_output + bytes(self._login.readAllStandardOutput()).decode("utf-8", "replace"))[-8000:]
        match = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", self._login_output)
        if match:
            self.device_code.setText(f"GitHub one-time code: {match[0]} — enter it at github.com/login/device")
            self.github_status.setText("Complete sign-in in your browser. This window will update when it finishes.")

    def _login_finished(self, code: int, *_args) -> None:
        process, self._login = self._login, None
        if process:
            process.deleteLater()
        self.login_button.setEnabled(True)
        self.device_code.clear()
        if code == 0:
            self._check_github()
        else:
            self.github_status.setText("GitHub sign-in did not finish. Try again, or continue without it.")

    def _login_error(self, *_args) -> None:
        if self._login is not None and self._login.state() == QProcess.ProcessState.NotRunning:
            self._login_finished(1)

    def _page_changed(self, *_args) -> None:
        if self.currentPage() is self.agreement_page:
            self.final_status.setText(f"GitHub reports account: {self._github_account or 'not connected; no reports will be sent'}. "
                                      "Your reporting choice is shown below.")

    def _stop_login(self) -> None:
        process, self._login = self._login, None
        if process and process.state() != QProcess.ProcessState.NotRunning:
            process.blockSignals(True)
            process.kill()
            process.waitForFinished(1500)

    def accept(self) -> None:
        if self.currentPage() is not self.agreement_page or not self.agree.isChecked():
            return
        settings = self.settings
        settings.theme = self.theme_choice.currentData()
        settings.automatic_reports = self.report_choice.isChecked()
        settings.github_account = self._github_account
        config.set_inference_backend(self.backend.currentData())
        try:
            onboarding.accept_agreement(settings)
        except OSError:
            settings.agreement_version = settings.agreement_accepted_at = ""
            QMessageBox.warning(self, "Setup could not be saved", "Check that the configuration folder is writable, then try again.")
            return
        self._stop_login()
        super().accept()

    def reject(self) -> None:
        self._stop_login()
        theme.set_active(self._original_theme)
        QApplication.instance().setStyleSheet(theme.stylesheet())
        super().reject()
