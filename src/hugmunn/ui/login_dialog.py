"""Sign in to Anthropic or OpenAI, from inside the app.

Two things this does that a settings field holding an API key does not:

* It **verifies the key before saving it**, by listing the models the key can
  reach. A key that is stored and wrong fails later, inside a conversation,
  as an opaque 401 — and the user's reasonable conclusion is that the app is
  broken rather than that they pasted a truncated string.
* It **says where the key is being kept**. A keyring and a mode-600 file are
  not the same promise, and which one you get depends on whether there is a
  D-Bus session, which is not something a user should have to infer.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
)

from ..core import credentials
from ..core.providers import CONSOLE_URLS, KEY_PREFIX, LABELS, Provider
from . import theme
from .workers import CatalogueWorker


class LoginDialog(QDialog):
    """Paste a key, check it, keep it."""

    def __init__(self, provider: Provider, parent=None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.models: tuple = ()
        self._worker: CatalogueWorker | None = None
        name = LABELS[provider]
        self.setWindowTitle(f"Sign in to {name}")
        self.setMinimumWidth(600)

        palette = theme.active()
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        head = QLabel(
            f"<b>{name}</b> runs in the cloud. Your prompts, and anything a "
            f"tool reads on your behalf, are sent to their API."
        )
        head.setWordWrap(True)
        layout.addWidget(head)

        layout.addWidget(QLabel("API key:"))
        row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(KEY_PREFIX.get(provider, "sk-") + "…")
        self.key_edit.textChanged.connect(self._on_key_changed)
        self.key_edit.returnPressed.connect(self._verify)
        row.addWidget(self.key_edit, 1)
        reveal = QPushButton("Show")
        reveal.setCheckable(True)
        reveal.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        row.addWidget(reveal)
        layout.addLayout(row)

        get_key = QPushButton(f"Open the {name} key page…")
        get_key.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(CONSOLE_URLS[provider]))
        )
        layout.addWidget(get_key)

        storage = QLabel(
            f"Stored in <b>{credentials.backend_name()}</b>."
            + ("" if credentials.is_secure() else
               "  <span style='color:%s'>No system keyring was found, so the key "
               "is written to a plain file readable only by you. Anything running "
               "as your user can read it.</span>" % palette["warning"])
        )
        storage.setWordWrap(True)
        storage.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(storage)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.status)

        self.buttons = QDialogButtonBox()
        self.buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        if credentials.is_signed_in(provider) and not credentials.from_env(provider):
            out = self.buttons.addButton("Sign out", QDialogButtonBox.ButtonRole.DestructiveRole)
            out.clicked.connect(self._sign_out)
        self.verify = self.buttons.addButton(
            "Check and save", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.verify.setObjectName("primary")
        self.verify.setEnabled(False)
        self.buttons.accepted.connect(self._verify)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        if credentials.from_env(provider):
            variable = ("ANTHROPIC_API_KEY" if provider == Provider.ANTHROPIC
                        else "OPENAI_API_KEY")
            self.status.setText(
                f"<span style='color:{palette['success']}'>Using <b>{variable}</b> "
                f"from the environment ({credentials.masked(provider)}). It takes "
                f"priority over anything saved here.</span>"
            )
        elif credentials.is_signed_in(provider):
            self.status.setText(
                f"Signed in as {credentials.masked(provider)}. Paste a new key to "
                f"replace it."
            )

    # ------------------------------------------------------------ behaviour

    def _on_key_changed(self, text: str) -> None:
        self.verify.setEnabled(len(text.strip()) > 10)

    def _sign_out(self) -> None:
        credentials.clear(self.provider)
        self.models = ()
        self.reject()

    def _verify(self) -> None:
        key = self.key_edit.text().strip()
        if len(key) <= 10:
            return
        expected = KEY_PREFIX.get(self.provider, "")
        if expected and not key.startswith(expected):
            # A warning rather than a refusal: the prefix is a convention, and
            # a proxy or a future key format could legitimately differ.
            self.status.setText(
                f"<span style='color:{theme.active()['warning']}'>That does not start "
                f"with <code>{expected}</code>. Checking it anyway…</span>"
            )
        else:
            self.status.setText("Checking…")

        self.verify.setEnabled(False)
        self.key_edit.setEnabled(False)
        worker = CatalogueWorker(self.provider, key, self)
        worker.ok.connect(self._on_ok)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda: setattr(self, "_worker", None))
        self._worker = worker
        worker.start()

    def _on_ok(self, models: tuple) -> None:
        # Saved only now: a key that cannot list models is not a key worth
        # keeping, and storing it would mean the next launch starts broken.
        credentials.store(self.provider, self.key_edit.text().strip())
        self.models = models
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.key_edit.setEnabled(True)
        self.verify.setEnabled(True)
        self.status.setText(
            f"<span style='color:{theme.active()['error']}'>{message}</span>"
        )
