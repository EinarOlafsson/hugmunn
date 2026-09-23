"""Sidebar widget showing live CPU, RAM, GPU and VRAM.

Bars are drawn rather than using ``QProgressBar`` so all four line up on a
shared geometry and can be colour-coded by pressure.

The thresholds are not decoration. On this class of machine the two numbers
that decide whether a model is usable are free VRAM (does it load at all) and
free RAM against the model size (13 tok/s versus 2 when it pages). Amber warns,
red means a launch will disappoint.
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..core.resources import Sampler, Snapshot
from . import style

WARN = 75.0
CRITICAL = 90.0


class _Bar(QWidget):
    """One labelled meter: name on the left, value right, bar underneath."""

    HEIGHT = 30

    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self._name = name
        self._percent = 0.0
        self._detail = ""

    def set_value(self, percent: float, detail: str) -> None:
        changed = abs(percent - self._percent) > 0.4 or detail != self._detail
        self._percent, self._detail = percent, detail
        if changed:
            self.update()   # repaint only when something actually moved

    def _colour(self) -> QColor:
        if self._percent >= CRITICAL:
            return QColor(style.ERR)
        if self._percent >= WARN:
            return QColor(style.WARN)
        return QColor(style.ACCENT)

    def paintEvent(self, event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        painter.setFont(font)

        painter.setPen(QColor(style.TEXT_DIM))
        painter.drawText(0, 12, self._name)
        painter.drawText(
            self.rect().adjusted(0, 0, 0, -18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            self._detail,
        )

        track_y, height = 18, 6
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(style.BORDER))
        painter.drawRoundedRect(0, track_y, self.width(), height, 3, 3)

        filled = int(self.width() * min(100.0, max(0.0, self._percent)) / 100.0)
        if filled > 0:
            painter.setBrush(self._colour())
            painter.drawRoundedRect(0, track_y, max(filled, 4), height, 3, 3)
        painter.end()


class ResourceBar(QFrame):
    """Polls once a second and updates four meters."""

    INTERVAL_MS = 1000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("toolCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        # Each bar is a fixed 30px and cannot compress, so a layout short of
        # room does not shrink them -- it draws them outside this frame, over
        # whatever is next to it. Claiming the full height up front is what
        # stops the layout from trying.
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(),
                           QSizePolicy.Policy.Fixed)

        self._sampler = Sampler()
        self.cpu = _Bar("CPU")
        self.ram = _Bar("RAM")
        self.gpu = _Bar("GPU")
        self.vram = _Bar("VRAM")
        for bar in (self.cpu, self.ram, self.gpu, self.vram):
            layout.addWidget(bar)

        self.note = QLabel()
        self.note.setObjectName("blurb")
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        layout.addWidget(self.note)

        self.setMinimumHeight(
            4 * _Bar.HEIGHT + layout.spacing() * 3
            + layout.contentsMargins().top() + layout.contentsMargins().bottom()
        )

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(self.INTERVAL_MS)
        self.refresh()

    def restyle(self) -> None:
        """Repaint after a theme change.

        The meters read their colours inside ``paintEvent``, which resolves
        against the live palette, so nothing needs recomputing — they just
        have to be asked to draw again.
        """
        for bar in (self.cpu, self.ram, self.gpu, self.vram):
            bar.update()

    def stop(self) -> None:
        """Stop polling. Idempotent, and safe after the widgets are gone."""
        try:
            self._timer.stop()
        except RuntimeError:
            pass          # the timer's C++ side is already destroyed

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        self.stop()
        super().closeEvent(event)

    def refresh(self) -> None:
        # The timer can outlive the bars it updates: Qt destroys child C++
        # objects before Python drops its references, so a tick that lands in
        # that window reaches a deleted _Bar and raises. Visible as a
        # traceback on quit, and as an intermittent failure in any test that
        # builds and closes a window.
        if sip.isdeleted(self) or sip.isdeleted(self.cpu):
            self.stop()
            return
        snap = self._sampler.sample()
        self.cpu.set_value(snap.cpu_percent, f"{snap.cpu_percent:.0f}%")
        self.ram.set_value(
            snap.ram_percent,
            f"{snap.ram_used_gb:.0f} / {snap.ram_total_gb:.0f} GB",
        )

        if snap.has_gpu:
            self.gpu.setVisible(True)
            self.vram.setVisible(True)
            self.gpu.set_value(snap.gpu_percent or 0.0, f"{snap.gpu_percent:.0f}%")
            self.vram.set_value(
                snap.vram_percent,
                f"{snap.vram_used_gb:.1f} / {snap.vram_total_gb:.1f} GB",
            )
        else:
            self.gpu.setVisible(False)
            self.vram.setVisible(False)

        self.note.setText(self._warning(snap))
        self.note.setVisible(bool(self.note.text()))

    @staticmethod
    def _warning(snap: Snapshot) -> str:
        """Describe the most pressing memory constraint in the snapshot."""
        if snap.swap_used_gb > 1.0:
            return (
                f"Swap in use ({snap.swap_used_gb:.1f} GB). "
                "Memory pressure may slow model loading and generation."
            )
        if snap.has_gpu and (snap.vram_total_gb - (snap.vram_used_gb or 0)) < 2.0:
            others = f" ({snap.gpu_procs} process(es) hold it)" if snap.gpu_procs else ""
            return f"Almost no free VRAM{others} — a GPU-resident model will not load."
        if snap.ram_percent >= CRITICAL:
            free = snap.ram_total_gb - snap.ram_used_gb
            return f"Only {free:.0f} GB RAM free — the 87-102 GB models will swap."
        return ""
