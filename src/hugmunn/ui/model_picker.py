"""Colouring the model dropdown by how much alignment is left in the weights.

Plain solid rows. An earlier version painted a glow behind the text with a
custom delegate, which looked like a toy; this uses the item's own foreground
and background, which is what a dropdown normally does and what Qt renders
consistently across styles.

Three bands, and only the ends were specified:

* **stock** — white on blue.
* **unlocked** — red on black.
* **tuned** sits between them and had to be chosen. It gets white on a dark
  slate: the same ink as stock so it reads as the safe half of the list, on a
  ground most of the way to the black that marks unlocked.

The colours are fixed rather than taken from the palette. That is the opposite
of the rule everywhere else in this app, and deliberate — what a row says is a
property of the *model*, not of the theme, and a red that becomes maroon on one
theme and salmon on another stops being a signal. Every pair here clears AA on
its own, so no theme can make them unreadable either.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor

from .. import config

#: (text, background) per freedom level. Checked against WCAG AA as pairs;
#: see ``test_the_dropdown_colours_are_readable``.
COLOURS = {
    "vanilla": ("#ffffff", "#1b4f8f"),   # white on blue
    "tuned": ("#ffffff", "#1f242c"),     # white on dark slate
    "unlocked": ("#ff5f55", "#000000"),  # red on black
}

#: Slightly lifted grounds for the row under the cursor, so hover still reads
#: without changing what the colour means.
HOVER = {
    "vanilla": "#2461ab",
    "tuned": "#2b323c",
    "unlocked": "#141414",
}


def text_colour(freedom: str) -> str:
    return COLOURS.get(freedom, COLOURS["vanilla"])[0]


def background_colour(freedom: str) -> str:
    return COLOURS.get(freedom, COLOURS["vanilla"])[1]


def describe(freedom: str) -> str:
    return config.FREEDOM_NOTES.get(freedom, "")


def paint_item(item, freedom: str, available: bool = True) -> None:
    """Colour one row of the dropdown.

    Set on the item rather than drawn by a delegate: a combo box popup is a
    normal list view, and letting it do its own painting is both less code and
    what every other dropdown on the machine looks like.
    """
    text, background = COLOURS.get(freedom, COLOURS["vanilla"])
    ink = QColor(text)
    if not available:
        # Not downloaded. Keep the hue -- it is the one thing the colour is
        # there to say -- and drop the presence.
        ink.setAlpha(140)
    item.setForeground(ink)
    item.setBackground(QColor(background))


def style_closed_combo(combo, freedom: str, available: bool = True) -> None:
    """Colour the closed box to match the selection.

    The popup is where models are compared; the closed box is what is on
    screen for the rest of the session, and a picker coloured only while open
    tells you nothing the moment you look away.
    """
    text, background = COLOURS.get(freedom, COLOURS["vanilla"])
    combo.setStyleSheet(
        f"QComboBox {{ color: {text}; background: {background}; "
        f"font-weight: 600; }}"
        f"QComboBox:hover {{ background: {HOVER.get(freedom, background)}; }}"
        f"QComboBox QAbstractItemView {{ background: {background}; "
        f"selection-background-color: {HOVER.get(freedom, background)}; "
        f"selection-color: {text}; }}"
    )
    combo.setToolTip(
        f"{config.FREEDOM_LABELS.get(freedom, freedom)} — {describe(freedom)}"
    )
