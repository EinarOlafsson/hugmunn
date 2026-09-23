"""Render desktop icon formats from the supplied SVG mark.

Run ``python packaging/generate_icons.py`` after changing the SVG artwork.
Requires PyQt6 and Pillow, both included in ``hugmunn[build]``.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

ICONS = Path(__file__).resolve().parents[1] / "src/hugmunn/resources/icons"


def render(source: Path, target: Path, *, tile: bool = False) -> None:
    """Render a 1024-pixel mark, optionally on a dark rounded desktop tile."""
    canvas = QImage(1024, 1024, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if tile:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#111111"))
        painter.drawRoundedRect(QRectF(32, 32, 960, 960), 180, 180)
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG: {source}")
    renderer.render(painter, QRectF(0, 0, 1024, 1024))
    painter.end()
    if not canvas.save(str(target)):
        raise OSError(f"Could not save {target}")


def main() -> None:
    """Write transparent marks plus PNG, ICO, and ICNS application icons."""
    app = QGuiApplication.instance() or QGuiApplication([])
    for colour in ("black", "white"):
        render(ICONS / f"hugmunn-mark-{colour}.svg",
               ICONS / f"hugmunn-mark-{colour}.png")
    render(ICONS / "hugmunn-mark-white.svg", ICONS / "hugmunn.png", tile=True)
    with Image.open(ICONS / "hugmunn.png") as icon:
        icon.save(ICONS / "hugmunn.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
        icon.save(ICONS / "hugmunn.icns")
        icon.resize((256, 256), Image.Resampling.LANCZOS).save(ICONS / "hugmunn-256.png")
    app.quit()


if __name__ == "__main__":
    main()
