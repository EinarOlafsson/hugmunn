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
from hugmunn.ui.model_picker import GROUND, REST, hover_colour  # noqa: E402


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


def test_every_row_is_white_on_black_at_rest():
    """A list where every row is a different colour is a list you decode.
    Uniform until you point at something answers one question at a time."""
    from hugmunn.ui.model_picker import GROUND, REST

    assert REST == "#ffffff"
    assert GROUND == "#000000"


def test_the_hover_colour_is_per_category():
    from hugmunn.ui.model_picker import hover_colour

    def channels(c):
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))

    blue = channels(hover_colour("vanilla"))
    grey = channels(hover_colour("tuned"))
    red = channels(hover_colour("unlocked"))

    assert blue[2] > blue[0] and blue[2] > blue[1], "stock should hover blue"
    assert max(grey) - min(grey) < 12, "tuned should hover grey"
    assert red[0] > red[1] and red[0] > red[2], "unlocked should hover red"


@pytest.mark.parametrize("level", ["vanilla", "tuned", "unlocked"])
def test_every_hover_colour_is_readable_on_black(level):
    from hugmunn.ui.model_picker import GROUND, hover_colour

    assert theme.contrast_ratio(hover_colour(level), GROUND) >= 4.5


def test_the_background_never_changes(window):
    """Including under selection, where Qt would otherwise paint its own
    highlight and take the text colour with it."""
    import pathlib

    from hugmunn.ui import model_picker

    source = pathlib.Path(model_picker.__file__).read_text(encoding="utf-8")
    assert "fillRect(option.rect, QColor(GROUND))" in source
    sheet_fn = model_picker.style_closed_combo
    combo = window.model_combo
    sheet_fn(combo, "unlocked")
    assert "selection-background-color: #000000" in combo.styleSheet()


def test_the_rows_carry_what_the_delegate_needs(window):
    from hugmunn.ui.model_picker import AVAILABLE_ROLE, FREEDOM_ROLE

    model = window.model_combo.model()
    levels = {model.item(i).data(FREEDOM_ROLE)
              for i in range(window.model_combo.count())}
    assert {"vanilla", "tuned", "unlocked"} <= levels
    assert model.item(0).data(AVAILABLE_ROLE) is not None


def test_a_delegate_draws_the_rows(window):
    """A stylesheet cannot say 'this row's hover colour depends on its data'."""
    from hugmunn.ui.model_picker import FreedomDelegate

    assert isinstance(window.model_combo.itemDelegate(), FreedomDelegate)


def test_there_is_no_glow():
    import pathlib

    from hugmunn.ui import model_picker

    source = pathlib.Path(model_picker.__file__).read_text(encoding="utf-8")
    assert source.count("drawText") == 1, "the text should be drawn once"


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
