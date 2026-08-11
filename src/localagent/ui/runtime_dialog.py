"""Point localagent at a llama-server, or find out how to get one.

The weights are the hard part to obtain and the easy part to move: 87 GB
copied onto a second machine works fine. The binary is the opposite — small,
but it has to be built for that machine's CPU and GPU, and it is gitignored
inside the models repo precisely because a build is not portable.

So a second machine reliably ends up with correct weights and nothing to run
them with. "build llama.cpp or set LLAMA_SERVER" is accurate and does not help
anybody at 11pm, hence this: it searches the places one plausibly already is,
lets the user point at it, and otherwise gives the exact commands.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .. import config
from . import theme

#: Searched when the user asks localagent to go looking. Deliberately a fixed
#: list rather than a filesystem walk: the machine that needs this has model
#: weights on network mounts, and a find(1) over those takes minutes.
SEARCH_ROOTS = (
    "~/.claude/models/bin", "~/.local/bin", "/usr/local/bin", "/usr/bin",
    "/opt/homebrew/bin", "~/llama.cpp/build/bin", "~/src/llama.cpp/build/bin",
    "~/git/llama.cpp/build/bin", "~/repo/llama.cpp/build/bin",
    "~/miniconda3/bin", "~/anaconda3/bin", "~/.cargo/bin",
)

BUILD_STEPS = """\
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build --config Release -j 16
# the binary lands at build/bin/llama-server
"""

BUILD_STEPS_CPU = """\
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build
cmake --build build --config Release -j 16
# the binary lands at build/bin/llama-server
"""


def search() -> list[Path]:
    """Every llama-server on this machine, in the conventional places."""
    found: list[Path] = []
    on_path = shutil.which("llama-server")
    if on_path:
        found.append(Path(on_path))
    for root in SEARCH_ROOTS:
        candidate = Path(root).expanduser() / "llama-server"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            resolved = candidate.resolve()
            if resolved not in {p.resolve() for p in found}:
                found.append(candidate)
    return found


def describe(path: Path) -> str:
    """The build's own version line, which says whether it has CUDA."""
    try:
        result = subprocess.run([str(path), "--version"], capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "could not be run"
    text = (result.stdout + result.stderr).strip().splitlines()
    return text[0][:120] if text else "no version reported"


class RuntimeDialog(QDialog):
    """Find, choose, or learn how to build a llama-server."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("llama-server")
        self.setMinimumWidth(720)
        palette = theme.active()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        head = QLabel(
            "localagent runs your local models through <b>llama-server</b>, "
            "part of llama.cpp. The weights move between machines fine; the "
            "binary has to be built for this one, so a machine with the "
            "models often has no way to run them."
        )
        head.setWordWrap(True)
        layout.addWidget(head)

        current = config.find_runtime()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        self.path_edit = QLineEdit(str(current) if current else "")
        self.path_edit.setPlaceholderText("/path/to/llama-server")
        self.path_edit.textChanged.connect(self._revalidate)
        row.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        find = QPushButton("Search this machine")
        find.clicked.connect(self._search)
        row.addWidget(find)
        layout.addLayout(row)

        layout.addWidget(QLabel("If you do not have one, build it:"))
        steps = QPlainTextEdit(BUILD_STEPS if _has_nvidia() else BUILD_STEPS_CPU)
        steps.setReadOnly(True)
        steps.setMaximumHeight(150)
        layout.addWidget(steps)

        note = QLabel(
            "The CUDA architecture above is 86, which is right for an RTX 30xx. "
            "Drop <code>-DGGML_CUDA=ON</code> for a CPU-only build."
            if _has_nvidia() else
            "No NVIDIA GPU was detected here, so this is a CPU build. Large "
            "models will be slow."
        )
        note.setObjectName("blurb")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.buttons = QDialogButtonBox()
        self.buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        self.save = self.buttons.addButton("Use this", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save.setObjectName("primary")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._revalidate()

    # ------------------------------------------------------------ behaviour

    def _revalidate(self) -> None:
        palette = theme.active()
        text = self.path_edit.text().strip()
        if not text:
            self.status.setText(
                f"<span style='color:{palette['error']}'>None found on this "
                f"machine.</span> Point at one, or build it below."
            )
            self.save.setEnabled(False)
            return
        path = Path(text).expanduser()
        if not path.is_file():
            self.status.setText(
                f"<span style='color:{palette['error']}'>No such file.</span>")
            self.save.setEnabled(False)
            return
        if not os.access(path, os.X_OK):
            self.status.setText(
                f"<span style='color:{palette['warning']}'>Not executable.</span> "
                f"Run: <code>chmod +x {path}</code>")
            self.save.setEnabled(False)
            return
        self.status.setText(
            f"<span style='color:{palette['success']}'>Executable.</span> "
            f"{describe(path)}")
        self.save.setEnabled(True)

    def _browse(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Select llama-server", self.path_edit.text() or str(Path.home()))
        if chosen:
            self.path_edit.setText(chosen)

    def _search(self) -> None:
        found = search()
        if not found:
            QMessageBox.information(
                self, "Nothing found",
                "No llama-server in any of the usual places:\n\n"
                + "\n".join(f"  {r}" for r in SEARCH_ROOTS)
                + "\n\nBuild it with the commands below, or browse to it if it "
                  "is somewhere else.",
            )
            return
        self.path_edit.setText(str(found[0]))
        if len(found) > 1:
            QMessageBox.information(
                self, f"Found {len(found)}",
                "Using the first; edit the path to pick another.\n\n"
                + "\n".join(f"  {p}" for p in found),
            )

    def _accept(self) -> None:
        config.set_runtime(self.path_edit.text().strip())
        self.accept()


def _has_nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None
