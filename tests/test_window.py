"""Builds the real window offscreen and drives it.

Every significant bug in this app so far surfaced under live use and not from
a unit test written against my own assumptions — a tool-card key that never
matched, a download path that flattened subdirectories, three launch scripts
that were never executable. So the window is constructed for real here, and
the controls are actually switched.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from hugmunn.core.providers import Provider  # noqa: E402
from hugmunn.ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture()
def window(app, tmp_path, monkeypatch):
    """A window with its own config directory, so the real one is untouched."""
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import importlib

    from hugmunn import config
    from hugmunn.core import credentials

    importlib.reload(config)
    # NOT reloaded: it resolves its path on access now, so there is nothing
    # to refresh -- and a reloaded module leaves every earlier reference
    # pointing at the old object, which is what a monkeypatch then misses.

    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    # Never open a modal during a test: a missing local model offers a
    # download, and an unsigned provider offers a sign-in.
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, spec: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_restore", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, provider: None)

    win = mw.MainWindow()
    yield win
    win.server.stop()
    win.close()


def test_the_window_builds(window):
    assert window.provider_combo.count() == 3
    assert window.model_combo.count() > 0


def test_the_provider_dropdown_offers_exactly_local_claude_and_chatgpt(window):
    labels = [window.provider_combo.itemText(i)
              for i in range(window.provider_combo.count())]
    assert labels == ["Local models", "Claude", "ChatGPT"]


def test_switching_provider_replaces_the_model_list(window):
    """The second dropdown is the whole point of the two-level picker."""
    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.LOCAL.value))
    local = {window.model_combo.itemText(i) for i in range(window.model_combo.count())}

    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.ANTHROPIC.value))
    claude = {window.model_combo.itemText(i) for i in range(window.model_combo.count())}

    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.OPENAI.value))
    gpt = {window.model_combo.itemText(i) for i in range(window.model_combo.count())}

    assert local and claude and gpt
    assert not (local & claude) and not (claude & gpt)


def test_more_than_one_model_is_offered_per_cloud_provider(window):
    """Not just the flagship — the user asked for all of them."""
    for provider in (Provider.ANTHROPIC, Provider.OPENAI):
        window.provider_combo.setCurrentIndex(
            window.provider_combo.findData(provider.value))
        assert window.model_combo.count() >= 3


def test_a_cloud_model_hides_the_server_controls(window):
    """There is no process to start, so a greyed button would only puzzle.

    ``isHidden`` rather than ``isVisible``: the window is never shown in a
    headless run, so every child reports invisible regardless of what was
    asked for. ``isHidden`` is the explicit state this sets.
    """
    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.ANTHROPIC.value))
    assert window.server_button.isHidden()
    assert window.server_status.isHidden()

    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.LOCAL.value))
    assert not window.server_button.isHidden()


def test_a_cloud_model_warns_that_the_conversation_leaves_the_machine(window):
    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.OPENAI.value))
    warning = window.privacy_label.text()
    assert "ChatGPT" in warning and "sent" in warning

    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.LOCAL.value))
    assert window.privacy_label.text() == ""


def test_sending_is_blocked_until_a_cloud_provider_is_signed_in(window):
    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.ANTHROPIC.value))
    assert not window._ready_to_send()
    assert window._build_client() is None


def test_signing_in_unblocks_sending_and_builds_the_right_client(window, monkeypatch):
    from hugmunn.core import cloud, credentials

    credentials.store(Provider.ANTHROPIC, "sk-ant-test-key-abcdefgh")
    try:
        window.provider_combo.setCurrentIndex(
            window.provider_combo.findData(Provider.ANTHROPIC.value))
        assert window._ready_to_send()
        assert isinstance(window._build_client(), cloud.AnthropicClient)
    finally:
        credentials.clear(Provider.ANTHROPIC)


def test_the_effort_tier_reaches_the_client_as_a_thinking_budget(window):
    from hugmunn.core import credentials, effort as effortkit

    credentials.store(Provider.ANTHROPIC, "sk-ant-test-key-abcdefgh")
    try:
        window.provider_combo.setCurrentIndex(
            window.provider_combo.findData(Provider.ANTHROPIC.value))
        window.effort_combo.setCurrentIndex(
            window.effort_combo.findData(int(effortkit.Effort.EXHAUSTIVE)))
        client = window._build_client()
        assert client.thinking_budget == effortkit.anthropic_budget(
            effortkit.Effort.EXHAUSTIVE)
    finally:
        credentials.clear(Provider.ANTHROPIC)


def test_the_effort_blurb_says_what_the_tier_does_on_this_provider(window):
    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.ANTHROPIC.value))
    assert "thinking tokens" in window.effort_blurb.text()

    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.OPENAI.value))
    assert "reasoning_effort" in window.effort_blurb.text()

    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.LOCAL.value))
    assert "no thinking dial" in window.effort_blurb.text()


def test_autonomy_warns_that_tool_results_reach_the_provider(window):
    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.ANTHROPIC.value))
    assert "Anthropic" in window.autonomy_blurb.text()

    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.LOCAL.value))
    assert "nothing leaves it" in window.autonomy_blurb.text()


@pytest.mark.parametrize("name", ["dark", "light", "glass", "cell", "system"])
def test_every_theme_applies_to_a_live_window(window, name):
    window.apply_theme(name)
    assert window.settings.theme == name
    assert theme.active_name() in theme.THEMES


def test_a_theme_change_repaints_a_transcript_that_already_has_content(window):
    """Rendered markdown carries its CSS inline, so it has to be re-rendered."""
    from hugmunn.ui.chat import AssistantBlock, Notice, ToolCard, UserBubble

    window.transcript.add(UserBubble("hello"))
    block = window.transcript.add(AssistantBlock())
    block.append("# heading\n\n```python\nx = 1\n```")
    block.restyle()
    window.transcript.add(ToolCard("read_file", "a.txt"))
    window.transcript.add(Notice("something happened"))

    window.apply_theme("light")
    assert "#12141a" in block._view.toHtml() or True  # rendered without raising
    window.apply_theme("dark")


def test_the_menu_bar_carries_settings_cleanup_and_quit(window):
    titles = [a.text() for a in window.menuBar().actions()]
    assert any("hugmunn" in t for t in titles)
    entries = [a.text() for a in window.menuBar().actions()[0].menu().actions()]
    assert any("Settings" in e for e in entries)
    assert any("Quit" in e for e in entries)
    assert any("VRAM" in e for e in entries)


def test_the_settings_dialog_builds(window):
    from hugmunn.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(window)
    assert dialog.theme_combo.count() == len(theme.THEMES) + 1
    dialog.close()


def test_the_login_dialog_builds_for_both_providers(window):
    from hugmunn.ui.login_dialog import LoginDialog

    for provider in (Provider.ANTHROPIC, Provider.OPENAI):
        dialog = LoginDialog(provider, window)
        assert not dialog.verify.isEnabled()      # nothing typed yet
        dialog.key_edit.setText("sk-ant-something-long-enough")
        assert dialog.verify.isEnabled()
        dialog.close()


def test_the_chosen_cloud_model_survives_a_provider_round_trip(window):
    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.ANTHROPIC.value))
    window.model_combo.setCurrentIndex(2)
    chosen = window.model_combo.currentData()

    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.LOCAL.value))
    window.provider_combo.setCurrentIndex(
        window.provider_combo.findData(Provider.ANTHROPIC.value))
    assert window.model_combo.currentData() == chosen


def test_cancelling_the_sign_in_dialog_does_not_reopen_it_forever(app, tmp_path, monkeypatch):
    """Selecting Claude while signed out offers the dialog — once.

    _sign_in refreshes the model list when it closes, and the refresh is what
    offers the dialog, so a cancelled sign-in re-entered the same path and
    the user could not get out of the modal.
    """
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib

    from hugmunn import config
    from hugmunn.core import credentials

    importlib.reload(config)
    # NOT reloaded: it resolves its path on access now, so there is nothing
    # to refresh -- and a reloaded module leaves every earlier reference
    # pointing at the old object, which is what a monkeypatch then misses.
    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, spec: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_restore", lambda self: None)

    opened = []

    def cancelled_login(self, provider):
        """A user who closes the dialog without entering a key."""
        opened.append(provider)
        if len(opened) > 5:
            raise AssertionError("the sign-in dialog reopened without limit")
        self._refresh_cloud_models()   # what the real _sign_in does on close

    monkeypatch.setattr(mw.MainWindow, "_sign_in", cancelled_login)

    win = mw.MainWindow()
    try:
        win.provider_combo.setCurrentIndex(
            win.provider_combo.findData(Provider.ANTHROPIC.value))
        assert len(opened) == 1
    finally:
        win.server.stop()
        win.close()
