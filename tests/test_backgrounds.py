"""Text must sit on its container's colour, not the window's.

A blanket ``QWidget { background: ... }`` in the stylesheet paints *every*
widget, labels included. A QLabel inside a raised panel then draws a
window-coloured rectangle behind its text, so every piece of text in the app
carries a visible box of the wrong colour. It is nearly invisible on the dark
theme, where the window and the panels are two near-blacks a few levels apart,
and obvious everywhere else.

These render the widgets offscreen and sample the pixels, because that is the
only way to check a thing whose whole symptom is what it looks like.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel  # noqa: E402

from localagent.ui import theme  # noqa: E402


def render(widget):
    """Paint the widget offscreen and return its image."""
    widget.resize(widget.sizeHint().expandedTo(widget.minimumSize()))
    widget.show()
    from PyQt6.QtWidgets import QApplication

    QApplication.processEvents()
    return widget.grab().toImage()


def colour_at(image, x, y):
    c = image.pixelColor(int(x), int(y))
    return (c.red(), c.green(), c.blue())


def close(a, b, tolerance=6):
    """Same colour to the eye. Antialiasing moves a channel by a level or two."""
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def dominant(image, x, y, width, height):
    """The most common colour in a rectangle.

    A single sample point is unreliable: a corner inset lands on background
    for one-line labels and on a glyph for a wrapped one. Text occupies a
    minority of any label's area, so the mode is its background — and if the
    label were painting a box of the wrong colour, the mode would be that
    wrong colour, which is exactly the bug being tested for.
    """
    from collections import Counter

    counts = Counter(
        colour_at(image, px, py)
        for py in range(max(0, y), min(image.height(), y + height))
        for px in range(max(0, x), min(image.width(), x + width))
    )
    return counts.most_common(1)[0][0] if counts else (0, 0, 0)


def label_background(container, label):
    """The colour behind a label, taken across its whole rectangle."""
    image = render(container)
    origin = label.mapTo(container, label.rect().topLeft())
    return dominant(image, origin.x(), origin.y(), label.width(), label.height())


def expected(role):
    """A palette colour as an (r, g, b) triple.

    Compared against the palette rather than against another pixel: a card
    has a rounded, antialiased border, so "somewhere in the container" lands
    on the border as often as on the fill and makes the reference itself
    unreliable.
    """
    value = theme.active()[role].lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


@pytest.fixture(params=list(theme.THEMES))
def themed(request, qt_app):
    from PyQt6.QtWidgets import QApplication

    previous = theme.active_name()
    theme.set_active(request.param)
    QApplication.instance().setStyleSheet(theme.stylesheet())
    yield request.param
    theme.set_active(previous)
    QApplication.instance().setStyleSheet(theme.stylesheet())


# ------------------------------------------------------------ the symptom


def test_the_stylesheet_does_not_blanket_paint_every_widget():
    """The root cause, checked directly so the reason stays legible."""
    import re

    for name in theme.THEMES:
        sheet = re.sub(r"/\*.*?\*/", "", theme.stylesheet(name), flags=re.S)
        rule = re.search(r"QWidget\s*\{([^}]*)\}", sheet)
        assert rule is not None
        assert "background" not in rule.group(1), (
            "QWidget must not set a background: it paints labels, and a label "
            "inside a panel then shows the window colour behind its text"
        )


def test_a_label_in_a_user_bubble_sits_on_the_bubble(themed):
    from localagent.ui.chat import UserBubble

    bubble = UserBubble("hello there")
    label = bubble.findChild(QLabel)
    assert label is not None
    behind = label_background(bubble, label)
    assert close(behind, expected("user")), (
        f"{themed}: text sits on {behind}, the bubble is {expected('user')}")
    bubble.close()


def test_a_label_in_a_tool_card_sits_on_the_card(themed):
    from localagent.ui.chat import ToolCard

    card = ToolCard("read_file", "/tmp/example.txt")
    labels = card.findChildren(QLabel)
    assert labels
    card_colour = expected("surface")
    for label in labels:
        if label.width() < 6 or label.height() < 6:
            continue
        image = render(card)
        origin = label.mapTo(card, label.rect().topLeft())
        behind = dominant(image, origin.x(), origin.y(), label.width(), label.height())
        assert close(behind, card_colour), (
            f"{themed}: '{label.text()[:20]}' sits on {behind}, "
            f"the card is {card_colour}")
    card.close()


def test_the_sidebar_headings_sit_on_the_sidebar(themed, qt_app, tmp_path,
                                                 monkeypatch):
    """The sidebar is the largest run of text in the app."""
    import importlib

    monkeypatch.setenv("LOCALAGENT_CONFIG_DIR", str(tmp_path))
    from localagent import config

    importlib.reload(config)
    from localagent.ui import main_window as mw

    importlib.reload(mw)
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, s: None)
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, p: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_runtime_setup", lambda self: None)

    window = mw.MainWindow()
    try:
        window.show()
        from PyQt6.QtWidgets import QApplication, QFrame

        QApplication.processEvents()
        sidebar = next(f for f in window.findChildren(QFrame)
                       if f.objectName() == "sidebar")
        image = render(sidebar)
        # The panel's own colour, taken from a margin the layout leaves clear.
        panel = expected("surface")
        assert close(colour_at(image, 3, 3), panel), (
            f"{themed}: the sidebar paints {colour_at(image, 3, 3)}, "
            f"expected {panel}")

        for label in sidebar.findChildren(QLabel):
            if not label.isVisible() or label.width() < 8 or label.height() < 8:
                continue
            origin = label.mapTo(sidebar, label.rect().topLeft())
            if origin.x() < 0 or origin.y() < 0:
                continue
            behind = dominant(image, origin.x(), origin.y(),
                              label.width(), label.height())
            assert close(behind, panel), (
                f"{themed}: '{label.text()[:24]}' sits on {behind}, "
                f"the sidebar is {panel}")
    finally:
        window.server.stop()
        window.close()
