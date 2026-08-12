"""Nothing may overlap anything, at any window size.

The resource meters were the visible case: four bars at a fixed 30px inside a
frame the layout had squeezed to 65px. A fixed-height widget cannot compress,
so Qt does not shrink it — it draws it outside its parent, over whatever is
next to it. The sidebar is fifteen sections tall and wants about 1400px, so on
any normal screen there was always a shortfall to resolve that way.

Checked as geometry rather than by eye, at several window heights, because the
symptom only appears below a threshold that depends on the screen.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QFrame, QLabel, QWidget  # noqa: E402

#: Heights worth checking: the default, a 1080p desktop after chrome, a
#: laptop, and something deliberately cramped.
HEIGHTS = (1256, 900, 700, 520)


@pytest.fixture()
def window(qt_app, tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, s: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_restore", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, p: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_runtime_setup", lambda self: None)

    win = mw.MainWindow()
    win.show()
    qt_app.processEvents()
    yield win
    win.server.stop()
    win.close()


def siblings_of(parent):
    """Direct children that occupy space, with their rectangles in ``parent``."""
    rows = []
    for child in parent.findChildren(QWidget):
        if child.parent() is not parent or child.isHidden():
            continue
        if child.width() <= 0 or child.height() <= 0:
            continue
        rect = child.rect().translated(child.mapTo(parent, child.rect().topLeft()))
        rows.append((child, rect))
    return sorted(rows, key=lambda row: (row[1].top(), row[1].left()))


def name(widget):
    if isinstance(widget, QLabel):
        return f"label {widget.text()[:28]!r}"
    return widget.objectName() or type(widget).__name__


def overlaps(parent):
    """Every pair of siblings whose rectangles genuinely intersect.

    Rectangles, not vertical extents: two widgets side by side in a row share
    a vertical band and are not overlapping, which an earlier version of this
    reported as a fault the moment a spin box gained a button beside it.
    """
    found = []
    rows = siblings_of(parent)
    for index, (first, a) in enumerate(rows):
        for second, b in rows[index + 1:]:
            if b.top() >= a.bottom():
                break          # sorted by top; nothing later can reach back
            if a.intersects(b):
                found.append(
                    f"{name(first)} {a.getRect()} intersects "
                    f"{name(second)} {b.getRect()}")
    return found


def sidebar_of(window):
    return next(f for f in window.findChildren(QFrame)
                if f.objectName() == "sidebar")


@pytest.mark.parametrize("height", HEIGHTS)
def test_nothing_in_the_sidebar_overlaps(window, qt_app, height):
    window.resize(1180, height)
    qt_app.processEvents()
    clashes = overlaps(sidebar_of(window))
    assert not clashes, f"at {height}px:\n" + "\n".join(clashes)


@pytest.mark.parametrize("height", HEIGHTS)
def test_the_meters_keep_their_full_height(window, qt_app, height):
    """The reported symptom: bars drawn on top of each other."""
    window.resize(1180, height)
    qt_app.processEvents()
    meters = window.resources
    assert meters.height() >= meters.minimumHeight(), (
        f"at {height}px the meters are {meters.height()}px, "
        f"below their {meters.minimumHeight()}px minimum")


@pytest.mark.parametrize("height", HEIGHTS)
def test_each_meter_stays_inside_its_frame(window, qt_app, height):
    """A fixed-height child of a squeezed parent paints outside it."""
    window.resize(1180, height)
    qt_app.processEvents()
    meters = window.resources
    for bar in (meters.cpu, meters.ram, meters.gpu, meters.vram):
        if bar.isHidden():
            continue
        top = bar.mapTo(meters, bar.rect().topLeft()).y()  # noqa: E501
        assert top >= 0, f"{bar._name} starts {top}px above its frame"
        assert top + bar.height() <= meters.height(), (
            f"at {height}px the {bar._name} meter ends at "
            f"{top + bar.height()}px, outside its {meters.height()}px frame")


def test_the_meters_do_not_collide_with_each_other(window, qt_app):
    window.resize(1180, 520)
    qt_app.processEvents()
    assert not overlaps(window.resources)


def test_the_sidebar_scrolls_rather_than_compressing(window, qt_app):
    """Scrolling is the only arrangement where nothing overlaps at any size.

    The column wants ~1400px. Every alternative to scrolling resolves a
    shortfall by compressing children, and the ones that cannot compress get
    drawn over their neighbours.
    """
    from PyQt6.QtWidgets import QScrollArea

    sidebar = sidebar_of(window)
    scroller = sidebar.parent()
    while scroller is not None and not isinstance(scroller, QScrollArea):
        scroller = scroller.parent()
    assert scroller is not None, "the sidebar must live inside a scroll area"

    window.resize(1180, 520)
    qt_app.processEvents()
    assert sidebar.height() >= sidebar.sizeHint().height() - 1, (
        "the sidebar is being compressed instead of scrolled")


def test_the_sidebar_never_scrolls_sideways(window, qt_app):
    """A horizontal scrollbar in a controls column is a layout bug, not a feature."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QScrollArea

    sidebar = sidebar_of(window)
    scroller = sidebar.parent()
    while scroller is not None and not isinstance(scroller, QScrollArea):
        scroller = scroller.parent()
    assert scroller.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_the_meters_stop_polling_when_the_window_closes(qt_app, tmp_path, monkeypatch):
    """A one-second timer that outlives its widgets raises during teardown.

    Qt destroys child C++ objects before Python drops its references, so a
    tick landing in that window reaches a deleted _Bar. It showed up as a
    traceback on quit, and as an intermittent error in any test that builds
    and closes a window — one run in three or four.
    """
    import importlib

    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, s: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_restore", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, p: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_runtime_setup", lambda self: None)

    win = mw.MainWindow()
    meters = win.resources
    assert meters._timer.isActive()
    win.server.stop()
    win.close()
    assert not meters._timer.isActive(), "the poll timer must stop with the window"


def test_a_tick_after_teardown_does_not_raise(qt_app):
    """The backstop, for anything that destroys a widget without closeEvent.

    ``deleteLater`` only posts a DeferredDelete event; ``processEvents`` does
    not deliver it, so the C++ object is still alive afterwards and the test
    proves nothing. ``sip.delete`` destroys it now, which is what the guard
    is actually defending against.
    """
    from PyQt6 import sip

    from hugmunn.ui.resource_bar import ResourceBar

    meters = ResourceBar()
    assert meters._timer.isActive()
    sip.delete(meters.cpu)
    assert sip.isdeleted(meters.cpu)

    meters.refresh()          # must not raise
    assert not meters._timer.isActive(), "a tick on dead widgets must stop the timer"
