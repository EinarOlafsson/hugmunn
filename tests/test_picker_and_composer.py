"""The model dropdown's colours, and resizing the message box.

Both were reported broken after the first attempt. The dropdown painted a glow
behind its text with a custom delegate, which looked like a toy. The message
box had a six-pixel drag strip along its own top edge, which was hard to hit
and competed with selecting the first line of text -- so in practice it could
not be resized at all.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")


from hugmunn import config  # noqa: E402
from hugmunn.ui import theme  # noqa: E402
from hugmunn.ui.model_picker import COLOURS, background_colour, text_colour  # noqa: E402


@pytest.fixture()
def window(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn.ui import main_window as mw

    for name in ("_offer_download", "_offer_restore", "_sign_in",
                 "_offer_runtime_setup"):
        monkeypatch.setattr(mw.MainWindow, name, lambda self, *a: None)
    win = mw.MainWindow()
    win.resize(1180, 820)
    win.show()
    qt_app.processEvents()
    yield win
    win.server.stop()
    win.close()


# ------------------------------------------------------------- the dropdown


def test_stock_models_are_white_on_blue():
    assert text_colour("vanilla") == "#ffffff"
    ground = background_colour("vanilla")
    # Blue: the blue channel clearly dominant.
    r, g, b = (int(ground[i:i + 2], 16) for i in (1, 3, 5))
    assert b > r and b > g


def test_unlocked_models_are_red_on_black():
    ink = text_colour("unlocked")
    r, g, b = (int(ink[i:i + 2], 16) for i in (1, 3, 5))
    assert r > g and r > b, "the unlocked colour should read as red"
    assert background_colour("unlocked") == "#000000"


def test_tuned_sits_between_the_two():
    """Only the ends were specified; the middle had to be chosen and should
    look like the middle."""
    def luminance(colour):
        return theme.relative_luminance(colour)

    assert (luminance(background_colour("unlocked"))
            < luminance(background_colour("tuned"))
            < luminance(background_colour("vanilla")))


@pytest.mark.parametrize("level", list(COLOURS))
def test_every_pair_is_readable(level):
    """Fixed colours rather than palette roles, so they cannot be checked by
    the theme tests -- they are checked here instead."""
    ink, ground = COLOURS[level]
    assert theme.contrast_ratio(ink, ground) >= 4.5


def test_the_colours_are_fixed_rather_than_themed():
    """What a row says is a property of the model, not of the theme. A red
    that becomes maroon on one theme and salmon on another is not a signal."""
    previous = theme.active_name()
    try:
        theme.set_active("dark")
        dark = dict(COLOURS)
        theme.set_active("chatgpt-light")
        assert dict(COLOURS) == dark
    finally:
        theme.set_active(previous)


def test_there_is_no_glow_delegate_any_more():
    """It looked like a toy."""
    import hugmunn.ui.model_picker as picker

    assert not hasattr(picker, "FreedomDelegate")
    source = __import__("pathlib").Path(picker.__file__).read_text(encoding="utf-8")
    assert "drawText" not in source, "rows should not be hand-painted"


def test_every_row_carries_its_colour(window):
    model = window.model_combo.model()
    pairs = {(model.item(i).foreground().color().name(),
              model.item(i).background().color().name())
             for i in range(window.model_combo.count())}
    assert ("#ffffff", "#1b4f8f") in pairs      # stock
    assert ("#ff5f55", "#000000") in pairs      # unlocked


def test_the_closed_box_matches_the_selection(window):
    window.model_combo.setCurrentIndex(
        window.model_combo.findData("uncensored-code"))
    sheet = window.model_combo.styleSheet()
    assert text_colour("unlocked") in sheet
    assert background_colour("unlocked") in sheet


# ---------------------------------------------------------- the message box


def test_the_composer_lives_in_a_splitter(window):
    """A six-pixel strip on the text area's own edge could not be hit, and
    fought selecting the first line."""
    assert window.conversation_split.count() == 2
    assert window.conversation_split.handleWidth() >= 4


def test_dragging_the_handle_resizes_the_message_box(window, qt_app):
    before = window.composer.height()
    window.conversation_split.setSizes([400, 380])
    qt_app.processEvents()
    assert window.composer.height() > before + 100


def test_the_new_height_is_remembered(window, qt_app):
    window.conversation_split.setSizes([500, 260])
    qt_app.processEvents()
    window._on_composer_resized()
    assert window.settings.composer_height == 260


def test_a_remembered_height_is_applied_on_the_next_launch(qt_app, tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn.config import Settings
    from hugmunn.ui import main_window as mw

    saved = Settings()
    saved.composer_height = 320
    saved.save()

    for name in ("_offer_download", "_offer_restore", "_sign_in",
                 "_offer_runtime_setup"):
        monkeypatch.setattr(mw.MainWindow, name, lambda self, *a: None)
    win = mw.MainWindow()
    try:
        assert win.conversation_split.sizes()[1] >= 300
    finally:
        win.server.stop()
        win.close()


def test_the_message_box_cannot_be_collapsed_away(window, qt_app):
    """A composer dragged to nothing is a window you cannot type in."""
    window.conversation_split.setSizes([820, 0])
    qt_app.processEvents()
    assert window.composer.height() >= window.composer.MIN_HEIGHT - 1
    assert not window.conversation_split.childrenCollapsible()


def test_the_transcript_takes_the_slack_when_the_window_grows(window, qt_app):
    window.conversation_split.setSizes([500, 200])
    qt_app.processEvents()
    composer_before = window.composer.height()
    window.resize(1180, 1100)
    qt_app.processEvents()
    # Growing the window should give the room to the transcript, not silently
    # inflate the box the user just sized.
    assert abs(window.composer.height() - composer_before) < 80
