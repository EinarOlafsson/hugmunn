"""Appearance, accounts and resources, in one place.

Three tabs rather than three menus because they are the settings a user
changes rarely and deliberately — unlike the model, skills and effort
controls, which are part of composing a turn and stay in the sidebar.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..core import cleanup, credentials, providers
from ..core.providers import BLURBS, CONSOLE_URLS, LABELS, Provider
from . import theme
from .login_dialog import LoginDialog


class SettingsDialog(QDialog):
    def __init__(self, window, parent=None) -> None:
        super().__init__(parent or window)
        self.window = window
        self.setWindowTitle("Settings")
        self.setMinimumSize(720, 560)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._appearance_tab(), "Appearance")
        tabs.addTab(self._accounts_tab(), "Accounts")
        tabs.addTab(self._resources_tab(), "Resources")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox()
        close = buttons.addButton("Close", QDialogButtonBox.ButtonRole.AcceptRole)
        close.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ------------------------------------------------------------ appearance

    def _appearance_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        layout.addWidget(QLabel("<b>Theme</b>"))
        self.theme_combo = QComboBox()
        for name in ("system", *theme.THEMES):
            self.theme_combo.addItem(theme.LABELS[name], name)
        index = self.theme_combo.findData(self.window.settings.theme)
        self.theme_combo.setCurrentIndex(max(0, index))
        self.theme_combo.currentIndexChanged.connect(self._on_theme)
        layout.addWidget(self.theme_combo)

        self.theme_blurb = QLabel()
        self.theme_blurb.setObjectName("blurb")
        self.theme_blurb.setWordWrap(True)
        layout.addWidget(self.theme_blurb)

        self.swatches = QLabel()
        self.swatches.setTextFormat(Qt.TextFormat.RichText)
        self.swatches.setWordWrap(True)
        layout.addWidget(self.swatches)

        note = QLabel(
            "Every palette is checked against WCAG AA — body text at 4.5:1, hint "
            "text and status colours at 3:1 — on every surface it can appear on. "
            "A theme that fails is a test failure, not a matter of taste."
        )
        note.setObjectName("blurb")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addStretch(1)
        self._on_theme()
        return page

    def _on_theme(self) -> None:
        name = self.theme_combo.currentData()
        self.theme_blurb.setText(theme.BLURBS.get(name, ""))
        palette = theme.palette_for(theme.resolve(name))
        chips = "".join(
            f"<span style='background:{palette[k]}; color:{palette['fg'] if k != 'fg' else palette['bg']};"
            f" padding:3px 7px; border-radius:4px;'>&nbsp;{k}&nbsp;</span> "
            for k in ("bg", "surface", "surface_alt", "accent", "success", "warning", "error")
        )
        self.swatches.setText(chips)
        self.window.apply_theme(name)

    # -------------------------------------------------------------- accounts

    def _accounts_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        intro = QLabel(
            "Local models need no account and send nothing anywhere. Claude and "
            "ChatGPT need an API key, and every prompt — plus anything a tool "
            "reads for them — goes to that provider."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._account_rows: dict[Provider, QLabel] = {}
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for row, provider in enumerate((Provider.ANTHROPIC, Provider.OPENAI)):
            name = QLabel(f"<b>{LABELS[provider]}</b>")
            grid.addWidget(name, row * 2, 0)

            status = QLabel()
            status.setWordWrap(True)
            grid.addWidget(status, row * 2, 1)
            self._account_rows[provider] = status

            button = QPushButton()
            button.setMinimumWidth(140)
            button.clicked.connect(lambda _=False, p=provider: self._sign_in(p))
            grid.addWidget(button, row * 2, 2)
            setattr(self, f"_btn_{provider.value}", button)

            blurb = QLabel(BLURBS[provider] + f"  Keys: {CONSOLE_URLS[provider]}")
            blurb.setObjectName("blurb")
            blurb.setWordWrap(True)
            grid.addWidget(blurb, row * 2 + 1, 1, 1, 2)
        layout.addLayout(grid)

        storage = QLabel(
            f"Keys are kept in <b>{credentials.backend_name()}</b> and never "
            f"written to settings.json."
        )
        storage.setObjectName("blurb")
        storage.setWordWrap(True)
        layout.addWidget(storage)

        layout.addStretch(1)
        self._refresh_accounts()
        return page

    def _refresh_accounts(self) -> None:
        palette = theme.active()
        for provider, label in self._account_rows.items():
            button = getattr(self, f"_btn_{provider.value}")
            count = len(providers.models_for(provider))
            if credentials.is_signed_in(provider):
                source = (" (from the environment)" if credentials.from_env(provider)
                          else "")
                label.setText(
                    f"<span style='color:{palette['success']}'>Signed in</span> as "
                    f"{credentials.masked(provider)}{source} · {count} models available"
                )
                button.setText("Change key…")
            else:
                label.setText(
                    f"<span style='color:{palette['fg_dim']}'>Not signed in</span> — "
                    f"{count} models listed until you do"
                )
                button.setText("Sign in…")

    def _sign_in(self, provider: Provider) -> None:
        dialog = LoginDialog(provider, self)
        dialog.exec()
        self._refresh_accounts()
        self.window.refresh_models()

    # ------------------------------------------------------------- resources

    def _resources_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        intro = QLabel(
            "These free what hugmunn is holding, and nothing else. No process "
            "is killed, nothing needs root, and no request in flight is "
            "cancelled — this machine runs other people's work too."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        for action in cleanup.ACTIONS:
            title, _ = cleanup.CONFIRMATIONS[action]
            row = QHBoxLayout()
            button = QPushButton(title)
            button.setMinimumWidth(160)
            button.clicked.connect(lambda _=False, a=action: self._run_cleanup(a))
            row.addWidget(button)
            summary = QLabel()
            summary.setObjectName("blurb")
            summary.setWordWrap(True)
            row.addWidget(summary, 1)
            setattr(self, f"_result_{action}", summary)
            layout.addLayout(row)

        layout.addStretch(1)
        return page

    def _run_cleanup(self, action: str) -> None:
        title, explanation = cleanup.CONFIRMATIONS[action]
        label = getattr(self, f"_result_{action}")

        if action == "disk":
            entries = cleanup.disk_report()
            label.setText("<br>".join(e.summary() for e in entries)
                          or "No readable drives.")
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
            result = cleanup.clear_vram(self.window.server)
            self.window._sync_controls()
        else:
            result = cleanup.clear_cpu()
        label.setText(result.summary())
