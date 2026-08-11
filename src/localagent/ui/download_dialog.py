"""Choose where to put a model's weights, with an honest read on the disk."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from ..config import MODELS_ROOT, ModelSpec
from ..core.downloads import evaluate_disk
from . import style


class DownloadDialog(QDialog):
    """Pick a destination, see what that disk means, then commit."""

    def __init__(self, spec: ModelSpec, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.setWindowTitle(f"Download {spec.label}")
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        head = QLabel(
            f"<b>{spec.label}</b><br>"
            f"{spec.download_gb:.1f} GB · {len(spec.files)} file(s) · "
            f"<code>{spec.repo}</code>"
        )
        head.setWordWrap(True)
        layout.addWidget(head)

        layout.addWidget(QLabel("Download to:"))
        row = QHBoxLayout()
        self.path_edit = QLineEdit(str(MODELS_ROOT / "gguf"))
        self.path_edit.textChanged.connect(self._reassess)
        row.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.report = QLabel()
        self.report.setWordWrap(True)
        self.report.setTextFormat(Qt.TextFormat.RichText)
        self.report.setStyleSheet(
            f"background:{style.CODE_BG}; padding:10px; border-radius:6px;"
        )
        layout.addWidget(self.report)

        self.buttons = QDialogButtonBox()
        self.buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.start = self.buttons.addButton("Download", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start.setObjectName("primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._reassess()

    def destination(self) -> Path:
        return Path(self.path_edit.text()).expanduser()

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Download location", self.path_edit.text() or str(Path.home())
        )
        if chosen:
            self.path_edit.setText(chosen)

    def _reassess(self) -> None:
        """Re-evaluate on every keystroke — the verdict drives the button."""
        text = self.path_edit.text().strip()
        if not text:
            self.report.setText("Choose a directory.")
            self.start.setEnabled(False)
            return

        disk = evaluate_disk(text)
        ok, message = disk.verdict(self.spec.download_gb)
        colour = style.OK if ok and disk.is_fast else (style.WARN if ok else style.ERR)
        icon = "✓" if ok and disk.is_fast else ("!" if ok else "✕")
        self.report.setText(
            f'<span style="color:{colour}"><b>{icon}</b></span> {message}'
        )
        self.start.setEnabled(ok)
        self.start.setText("Download anyway" if ok and not disk.is_fast else "Download")
