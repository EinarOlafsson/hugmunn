"""Choose where to put a model's weights, with an honest read on the disk."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout,
)

from .. import config
from ..config import ModelSpec, models_root
from ..core.downloads import evaluate_disk
from . import style


class DownloadDialog(QDialog):
    """Pick a destination, see what that disk means, then commit."""

    def __init__(self, spec: ModelSpec, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._located: dict[str, Path] = {}
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
        self.path_edit = QLineEdit(str(models_root() / "gguf"))
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
        # The usual case is several models copied across together.
        self.scan = self.buttons.addButton(
            "Scan a folder…", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.scan.clicked.connect(self._scan_folder)
        self.start = self.buttons.addButton("Download", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start.setObjectName("primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._reassess()

    def destination(self) -> Path:
        return Path(self.path_edit.text()).expanduser()

    def located(self) -> dict[str, Path]:
        """Model key -> weight path, for anything the user pointed at.

        A dict rather than one path because all three outcomes are the same
        shape: this model's weights, a *different* model's weights, or a
        folder holding several.
        """
        return dict(self._located)

    def _locate_existing(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, f"Select the weights for {self.spec.label}",
            self.path_edit.text() or str(Path.home()),
            "GGUF weights (*.gguf);;All files (*)",
        )
        if not chosen:
            return
        path = Path(chosen)
        owner = config.identify(path)

        if owner is None:
            QMessageBox.warning(
                self, "Not a model hugmunn knows",
                f"{path.name} does not match any model in the list.\n\n"
                f"{self.spec.label} expects:\n"
                + "\n".join(f"  {Path(f).name}" for f in self.spec.files)
                + "\n\nIf this is a model hugmunn does not ship, point a "
                  "launch script at it instead.",
            )
            return

        if owner.key != self.spec.key:
            # The dialog opens for whichever model is selected, and on a fresh
            # install that is the smallest -- not the one the user has. Saying
            # "a sibling shard is missing" here was both wrong and unactionable.
            answer = QMessageBox.question(
                self, "That is a different model",
                f"{path.name} is <b>{owner.label}</b>, not {self.spec.label}.\n\n"
                f"Record it as {owner.label}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        missing = owner.missing_shards(path)
        if missing:
            QMessageBox.warning(
                self, "Incomplete weights",
                f"{owner.label} ships as {len(owner.files)} files and "
                f"{len(missing)} of them are not beside this one:\n\n"
                + "\n".join(f"  {m.name}" for m in missing[:4])
                + "\n\nllama.cpp needs every shard in the same directory.",
            )
            return

        self._located = {owner.key: path}
        self.accept()

    def _scan_folder(self) -> None:
        """Point at a directory and record every model found in it.

        The common case is a user who copied several models across at once.
        Asking them to locate each one through a dialog that opens for the
        wrong model is how this went wrong in the first place.
        """
        chosen = QFileDialog.getExistingDirectory(
            self, "Folder holding your model weights",
            self.path_edit.text() or str(Path.home()),
        )
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
        names = "\n".join(
            f"  {config.by_key(k).label}\n      {v.name}" for k, v in found.items()
        )
        answer = QMessageBox.question(
            self, f"Found {len(found)} model(s)",
            f"Under {chosen}:\n\n{names}\n\nRecord all of them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._located = found
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
