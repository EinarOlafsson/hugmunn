"""The model dropdown, coloured by how much alignment is left in the weights.

Green, blue, red — stock, tuned, unlocked. It is the one property of a model
that is invisible from its name and decisive in whether it will do the work,
and until now it was buried in a blurb nobody reads twice.

The glow is drawn rather than styled. Qt's stylesheet has no text-shadow, and
``QGraphicsDropShadowEffect`` on a combo box's popup rows is not a thing — so
the rows are painted by a delegate, which also means the glow can respond to
hover and selection instead of being a static colour.

Colours come from the palette, not from the words "green" and "red": a fixed
``#00ff00`` is invisible on the light themes and garish on Cell. Each level
maps to a palette role that every theme already defines and that the contrast
tests already hold to AA.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .. import config
from . import theme

#: Freedom level -> palette role. Roles rather than hexes so every theme gets
#: a version of this that works in it and clears AA.
ROLE = {
    "vanilla": "success",   # green
    "tuned": "accent",      # blue-ish in most themes
    "unlocked": "error",    # red
}

#: How far the glow spreads, in pixels, at full strength.
GLOW_RADIUS = 7
#: Passes drawn to build it up. Four is enough to read as a glow rather than
#: as an outline, and cheap enough to repaint on every hover.
GLOW_PASSES = 4


def colour_for(freedom: str) -> str:
    """The text colour for a freedom level, in the theme on screen."""
    return theme.active()[ROLE.get(freedom, "fg")]


def describe(freedom: str) -> str:
    return config.FREEDOM_NOTES.get(freedom, "")


class FreedomDelegate(QStyledItemDelegate):
    """Paints each row in its freedom colour, glowing on hover and selection.

    A delegate rather than per-item foreground data because the glow has to
    change with the row's state, and item data is static once set.
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = theme.active()

        state = option.state
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        selected = bool(state & QStyle.StateFlag.State_Selected)
        enabled = bool(state & QStyle.StateFlag.State_Enabled)

        # The row's own background. Selection uses the accent-soft surface
        # rather than the accent itself: a filled accent row would fight the
        # coloured text that is the entire point of this delegate.
        if selected or hovered:
            painter.fillRect(option.rect, QColor(
                palette["accent_soft"] if selected else palette["surface_hi"]))

        freedom = index.data(Qt.ItemDataRole.UserRole + 1) or "vanilla"
        available = bool(index.data(Qt.ItemDataRole.UserRole + 2))
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""

        colour = QColor(colour_for(freedom))
        if not available:
            # Not downloaded: keep the hue, drop the presence. A greyed row
            # would lose the one thing the colour is there to say.
            colour.setAlpha(150)

        rect = QRectF(option.rect).adjusted(10, 0, -10, 0)
        font = QFont(option.font)
        font.setBold(selected)
        painter.setFont(font)

        # The glow: the same text drawn repeatedly at low alpha and growing
        # offsets. Only when the row is being pointed at or is current --
        # a list where every row glows is a list where nothing stands out.
        if enabled and (hovered or selected):
            strength = 1.0 if hovered else 0.6
            for pass_index in range(GLOW_PASSES, 0, -1):
                spread = GLOW_RADIUS * pass_index / GLOW_PASSES
                halo = QColor(colour)
                halo.setAlpha(int(26 * strength * (GLOW_PASSES - pass_index + 1)
                                  / GLOW_PASSES))
                painter.setPen(QPen(halo))
                for dx, dy in ((-spread, 0), (spread, 0), (0, -spread), (0, spread),
                               (-spread, -spread), (spread, spread),
                               (-spread, spread), (spread, -spread)):
                    painter.drawText(rect.translated(dx, dy),
                                     Qt.AlignmentFlag.AlignVCenter, text)

        painter.setPen(QPen(colour))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        # Room for the glow, so the top row's halo is not clipped by the
        # popup's edge.
        size.setHeight(max(size.height(), option.fontMetrics.height() + 12))
        return size


def style_closed_combo(combo, freedom: str, available: bool = True) -> None:
    """Colour the *closed* box to match the selection, and glow on hover.

    The popup is where the comparison happens, but the closed box is what is
    on screen for the rest of the session, and a picker that is coloured only
    while open tells you nothing the moment you look away.
    """
    palette = theme.active()
    colour = colour_for(freedom)
    dim = "" if available else "; font-style: italic"
    combo.setStyleSheet(
        f"QComboBox {{ color: {colour}; font-weight: 600{dim}; }}"
        f"QComboBox:hover {{ color: {colour}; border-color: {colour}; }}"
    )
    combo.setToolTip(
        f"{config.FREEDOM_LABELS.get(freedom, freedom)} — {describe(freedom)}"
    )
