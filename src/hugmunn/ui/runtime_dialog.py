"""Get llama-server onto this machine, without leaving the app.

The weights copy between machines fine. The binary does not — it is compiled
against this machine's CPU features and CUDA version — so a second machine
reliably ends up with correct weights and nothing to run them with.

Telling the user to run a shell script in the models repo does not close that
gap, because the machine that needs this is often the one where the models
repo was never cloned. So the dialog does the work: it looks at the machine,
says what it will do and what that costs, and builds it.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .. import config
from ..core import setup_llama
from . import theme
from .workers import SetupWorker

#: Searched when looking for an existing binary. A fixed list rather than a
#: filesystem walk: the machine that needs this has weights on network mounts,
#: and a find(1) over those takes minutes.
SEARCH_ROOTS = (
    "~/.local/share/hugmunn/bin", "~/.claude/models/bin", "~/.local/bin",
    "/usr/local/bin", "/usr/bin", "/opt/homebrew/bin",
    "~/llama.cpp/build/bin", "~/src/llama.cpp/build/bin",
    "~/git/llama.cpp/build/bin", "~/repo/llama.cpp/build/bin",
)



BUILD_STEPS = """git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j 4
# Optional: -DGGML_METAL=ON, -DGGML_VULKAN=ON, or
# -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=native
"""


def search() -> list[Path]:
    """Every llama-server on this machine, in the conventional places."""
    import shutil

    found: list[Path] = []
    seen: set[Path] = set()

    def add(candidate: Path) -> None:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(candidate)

    if on_path := shutil.which("llama-server"):
        add(Path(on_path))
    for root in SEARCH_ROOTS:
        add(Path(root).expanduser() / ("llama-server.exe" if os.name == "nt" else "llama-server"))
    return found


def describe(path: Path) -> str:
    """Whether this binary can use the GPU.

    Not the version string, which reports the compiler and says nothing about
    the backend -- "it built fine" is not evidence that the card is in play.
    """
    info = setup_llama.runtime_info(path)
    if not info.version and not info.devices:
        return "could not be run"
    return info.summary()[:200]


class RuntimeDialog(QDialog):
    """Set up, find, or choose a llama-server."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set up llama-server")
        self.setMinimumSize(760, 560)
        self._worker: SetupWorker | None = None
        self._close_requested = False
        self.checks = setup_llama.preflight()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        head = QLabel(
            "Local models are served by <b>llama-server</b>, part of llama.cpp. "
            "It has to be built for this machine's CPU and GPU, which is why "
            "copying model weights across is not enough on its own."
        )
        head.setWordWrap(True)
        layout.addWidget(head)

        self.plan = QLabel(self.checks.summary())
        self.plan.setWordWrap(True)
        self.plan.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.plan)
        self.backend = QComboBox()
        for key, label in (("auto", "Automatic"), ("cpu", "CPU only"), ("cuda", "NVIDIA CUDA"),
                           ("metal", "Apple Metal"), ("vulkan", "Vulkan (AMD / Intel / NVIDIA)")):
            self.backend.addItem(label, key)
        self.backend.setCurrentIndex(max(0, self.backend.findData(config.inference_backend())))
        layout.addWidget(QLabel("Runtime backend:"))
        layout.addWidget(self.backend)
        note = QLabel("Vulkan needs a compatible GPU driver; source builds also need Vulkan headers and glslc. "
                      "CUDA needs a compatible NVIDIA driver/toolkit. Metal uses shared memory on Apple Silicon. "
                      "You can also select an existing ROCm or SYCL llama-server below.")
        note.setWordWrap(True)
        layout.addWidget(note)
        manual = QPushButton("Manual build instructions")
        manual.clicked.connect(lambda: QMessageBox.information(self, "Build llama.cpp", BUILD_STEPS))
        layout.addWidget(manual)

        # The button that does the whole thing. First, and primary, because it
        # is what almost everyone opening this dialog wants.
        actions = QHBoxLayout()
        self.build_button = QPushButton("Build it for me")
        self.build_button.setObjectName("primary")
        self.build_button.clicked.connect(lambda: self._start("build"))
        self.build_button.setEnabled(self.checks.can_build)
        actions.addWidget(self.build_button)

        self.prebuilt_button = QPushButton("Download a prebuilt binary")
        self.prebuilt_button.clicked.connect(self._confirm_prebuilt)
        self.prebuilt_button.setEnabled(setup_llama.prebuilt_asset_name() is not None)
        actions.addWidget(self.prebuilt_button)

        find = QPushButton("Search this machine")
        find.clicked.connect(self._search)
        actions.addWidget(find)
        layout.addLayout(actions)

        if not self.checks.can_build and self.checks.advice():
            advice = QLabel(f"To enable building: <code>{self.checks.advice()}</code>")
            advice.setWordWrap(True)
            advice.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(advice)

        layout.addWidget(QLabel("Or point at one you already have:"))
        row = QHBoxLayout()
        current = config.find_runtime()
        self.path_edit = QLineEdit(str(current) if current else "")
        self.path_edit.setPlaceholderText("/path/to/llama-server")
        self.path_edit.textChanged.connect(self._revalidate)
        row.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.status)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        self.log.setMaximumBlockCount(4000)   # a full build is a lot of lines
        layout.addWidget(self.log, 1)

        self.buttons = QDialogButtonBox()
        self.close_button = self.buttons.addButton(
            "Close", QDialogButtonBox.ButtonRole.RejectRole)
        self.save = self.buttons.addButton(
            "Use this", QDialogButtonBox.ButtonRole.AcceptRole)
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
                f"<span style='color:{palette['error']}'>No llama-server found "
                f"on this machine.</span>")
            self.save.setEnabled(False)
            return
        path = Path(text).expanduser()
        if not path.is_file():
            self.status.setText(f"<span style='color:{palette['error']}'>No such "
                                f"file.</span>")
            self.save.setEnabled(False)
        elif not os.access(path, os.X_OK):
            self.status.setText(
                f"<span style='color:{palette['warning']}'>Not executable.</span> "
                f"Run: <code>chmod +x {path}</code>")
            self.save.setEnabled(False)
        else:
            self.status.setText(
                f"<span style='color:{palette['success']}'>Ready.</span> "
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
                + "\n\nBuild one with the button above, or browse to it if it "
                  "is somewhere else.",
            )
            return
        self.path_edit.setText(str(found[0]))
        if len(found) > 1:
            QMessageBox.information(
                self, f"Found {len(found)}",
                "Using the first; edit the path to pick another.\n\n"
                + "\n".join(f"  {p}" for p in found))

    def _confirm_prebuilt(self) -> None:
        """Download the selected backend; automatic chooses CPU or macOS Metal."""
        if setup_llama.prebuilt_asset_name(self.backend.currentData()) is None:
            QMessageBox.information(self, "No matching download", "Choose another backend or build from source.")
            return
        self._start("prebuilt")

    def _start(self, mode: str) -> None:
        if self._worker is not None:
            return
        self.log.setVisible(True)
        self.log.clear()
        for button in (self.build_button, self.prebuilt_button, self.save):
            button.setEnabled(False)
        self.close_button.setText("Cancel")

        worker = SetupWorker(mode, self, backend=self.backend.currentData())
        worker.line.connect(self._append)
        worker.ok.connect(self._on_ok)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _append(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _on_ok(self, path: str) -> None:
        config.set_runtime(path)
        self.path_edit.setText(path)
        self.status.setText(
            f"<span style='color:{theme.active()['success']}'>Built and "
            f"installed.</span> {describe(Path(path))}")
        self.accept()

    def _on_failed(self, message: str) -> None:
        if self._close_requested:
            return
        from ..core.reporting import report_error
        report_error("runtime-setup")
        self._append("")
        self._append(f"FAILED: {message}")
        QMessageBox.critical(self, "Setup failed", message)

    def _on_finished(self) -> None:
        self._worker = None
        if self._close_requested:
            super().reject()
            return
        self.build_button.setEnabled(self.checks.can_build)
        self.prebuilt_button.setEnabled(setup_llama.prebuilt_asset_name() is not None)
        self.close_button.setText("Close")
        self._revalidate()

    def _accept(self) -> None:
        config.set_runtime(self.path_edit.text().strip())
        config.set_inference_backend(self.backend.currentData())
        self.accept()

    def reject(self) -> None:  # noqa: D102 - Qt naming
        if self._worker is not None:
            self._worker.cancel()
            self._close_requested = True
            self.close_button.setText("Stopping…")
            self.close_button.setEnabled(False)
            return
        super().reject()
