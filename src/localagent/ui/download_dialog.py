"""Choose where to put a model's weights, with an honest read on the disk."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout,
)

from ..config import MODELS_ROOT, ModelSpec
from ..core.downloads import evaluate_disk
from . import style


class DownloadDialog(QDialog):
    """Pick a destination, see what that disk means, then commit."""

    def __init__(self, spec: ModelSpec, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._located: Path | None = None
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
        # For weights already on disk — copied from another machine, or fetched
        # outside the app. Points the app at them instead of downloading again.
        self.locate = self.buttons.addButton(
            "I already have it…", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.locate.clicked.connect(self._locate_existing)
        self.start = self.buttons.addButton("Download", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start.setObjectName("primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._reassess()

    def destination(self) -> Path:
        return Path(self.path_edit.text()).expanduser()

    def located_path(self) -> Path | None:
        """Set when the user pointed at weights already on disk."""
        return self._located

    def _locate_existing(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, f"Select the weights for {self.spec.label}",
            self.path_edit.text() or str(Path.home()),
            "GGUF weights (*.gguf);;All files (*)",
        )
        if not chosen:
            return
        path = Path(chosen)
        missing = self.spec.missing_shards(path)
        if missing:
            QMessageBox.warning(
                self, "Incomplete weights",
                f"{path.name} is one of {len(self.spec.files)} files for this "
                f"model, and {len(missing)} sibling(s) are not beside it:\n\n"
                + "\n".join(f"  {m.name}" for m in missing[:4])
                + "\n\nllama.cpp needs every shard in the same directory.",
            )
            return
        self._located = path
        self.accept()

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
