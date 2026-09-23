"""Render the bundled artwork for the active Qt palette."""

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from ..branding import svg
from . import theme


def pixmap(kind: str = "mark", width: int = 64, *, scale: float = 1.0) -> QPixmap:
    """Render artwork at a logical width, preserving its aspect ratio.

    ``scale`` is the display's device pixel ratio. Colours are chosen from the
    active background so the mark remains visible when the theme changes.
    """
    light = QColor(theme.active()["bg"]).lightnessF() > 0.5
    renderer = QSvgRenderer(QByteArray(svg(kind, light=light).encode("utf-8")))
    bounds = renderer.viewBoxF()
    height = round(width * bounds.height() / bounds.width())
    result = QPixmap(round(width * scale), round(height * scale))
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    renderer.render(painter)
    painter.end()
    result.setDevicePixelRatio(scale)
    return result


def window_icon() -> QIcon:
    """Return an icon with several sizes for title bars and task switchers."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(pixmap(width=size))
    return icon
