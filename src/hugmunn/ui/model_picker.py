"""Colouring the model dropdown by how much alignment is left in the weights.

Black rows with white text, always. The colour appears only under the cursor,
and only on the text:

* **stock** — blue
* **tuned** — grey
* **unlocked** — red

The background never changes, which is the point. A list where every row is a
different colour is a list you have to decode; a list that is uniform until you
point at something answers one question at a time. It also means the colour
carries no weight until the user asks for it, so nothing is shouting.

Drawn by a delegate because a stylesheet cannot say "this row's hover colour
depends on this row's data". The delegate draws plain text -- an earlier
version painted a glow, which looked like a toy.

The colours are fixed rather than palette roles. That inverts the rule this
app follows everywhere else and is deliberate: what a row says is a property of
the *model*, not of the theme, and a red that goes maroon on one theme and
salmon on another has stopped being a signal. All three clear AA on black.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .. import config

#: Every row, every state.
GROUND = "#000000"
REST = "#ffffff"

#: What the text becomes under the cursor.
HOVER = {
    "vanilla": "#4da6ff",   # stock — blue
    "tuned": "#b4b4b4",     # tuned — grey
    "unlocked": "#ff5f55",  # unlocked — red
}

#: Qt roles the rows carry so the delegate can read them back.
FREEDOM_ROLE = Qt.ItemDataRole.UserRole + 1
AVAILABLE_ROLE = Qt.ItemDataRole.UserRole + 2


def hover_colour(freedom: str) -> str:
    return HOVER.get(freedom, REST)


def describe(freedom: str) -> str:
    return config.FREEDOM_NOTES.get(freedom, "")


class FreedomDelegate(QStyledItemDelegate):
    """Black rows, white text, and the category colour only under the cursor."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        state = option.state
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        selected = bool(state & QStyle.StateFlag.State_Selected)

        # Black under every row, in every state. Qt would otherwise paint the
        # palette's selection colour here, which is the one thing this must
        # not do -- a highlighted row would lose the text colour that is the
        # whole signal.
        painter.fillRect(option.rect, QColor(GROUND))

        freedom = index.data(FREEDOM_ROLE) or "vanilla"
        available = index.data(AVAILABLE_ROLE)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""

        colour = QColor(hover_colour(freedom) if (hovered or selected) else REST)
        if available is False:
            # Not downloaded. Dimmed, but it keeps whichever colour the state
            # calls for -- greying it out would hide what the row is.
            colour.setAlpha(130)

        font = QFont(option.font)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(QPen(colour))
        painter.drawText(option.rect.adjusted(12, 0, -10, 0),
                         Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), option.fontMetrics.height() + 10))
        return size


def paint_item(item, freedom: str, available: bool = True) -> None:
    """Record what a row is, for the delegate to read at paint time."""
    item.setData(freedom, FREEDOM_ROLE)
    item.setData(available, AVAILABLE_ROLE)


def style_closed_combo(combo, freedom: str, available: bool = True) -> None:
    """The closed box: black, white text, the category colour on hover.

    The popup is where models are compared; the closed box is what is on
    screen for the rest of the session.
    """
    colour = hover_colour(freedom)
    combo.setStyleSheet(
        f"QComboBox {{ color: {REST}; background: {GROUND}; font-weight: 600; }}"
        f"QComboBox:hover {{ color: {colour}; background: {GROUND}; }}"
        f"QComboBox QAbstractItemView {{ background: {GROUND}; "
        f"selection-background-color: {GROUND}; }}"
    )
    combo.setToolTip(
        f"{config.FREEDOM_LABELS.get(freedom, freedom)} — {describe(freedom)}"
    )
