"""Palettes, credential storage and the resource buttons."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hugmunn.core import cleanup
from hugmunn.ui import theme


# ------------------------------------------------------------------ themes


@pytest.mark.parametrize("name", theme.THEMES)
def test_every_palette_meets_wcag_aa(name):
    """A theme that fails is text somebody cannot read, not a taste question."""
    assert theme.failures(name) == []


@pytest.mark.parametrize("name", theme.THEMES)
def test_every_palette_has_the_same_keys(name):
    """This is what lets a widget swap palettes without knowing which it has.

    spaCR's light theme shipped broken because a widget read a key that only
    the dark palette had; identical key sets is the property that prevents it.
    """
    assert set(theme.palette_for(name)) == set(theme.DARK)


@pytest.mark.parametrize("name", theme.THEMES)
def test_a_stylesheet_is_produced_for_every_theme(name):
    sheet = theme.stylesheet(name)
    assert "QPushButton" in sheet and "QComboBox" in sheet
    # An unresolved f-string placeholder means a palette key went missing.
    assert "{" not in sheet.replace("{{", "").replace("}}", "").split("QWidget")[0]


def test_unknown_theme_names_fall_back_to_dark_rather_than_raising():
    assert theme.palette_for("chartreuse") == theme.palette_for("dark")
    assert theme.resolve("chartreuse") == "dark"


def test_system_resolves_to_a_real_theme():
    assert theme.resolve("system") in theme.THEMES


def test_active_follows_set_active():
    previous = theme.active_name()
    try:
        theme.set_active("light")
        assert theme.active()["bg"] == theme.LIGHT["bg"]
        theme.set_active("cell")
        assert theme.active()["accent"] == theme.CELL["accent"]
    finally:
        theme.set_active(previous)


def test_style_proxy_tracks_the_live_theme():
    """The whole point of the proxy: ``style.BG`` is a lookup, not a constant.

    A module-level constant captured into an f-string is how spaCR ended up
    painting dark chrome on its light theme.
    """
    from hugmunn.ui import style

    previous = theme.active_name()
    try:
        theme.set_active("dark")
        dark = style.BG
        theme.set_active("light")
        assert style.BG != dark
        assert style.BG == theme.LIGHT["bg"]
    finally:
        theme.set_active(previous)


def test_contrast_ratio_matches_known_values():
    assert theme.contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
    assert theme.contrast_ratio("#777777", "#777777") == pytest.approx(1.0, abs=0.01)


def test_css_emits_plain_hex_when_opaque():
    assert theme.css("#112233", 1.0) == "#112233"
    assert theme.css("#112233", 0.5) == "rgba(17, 34, 51, 0.500)"


# ------------------------------------------------------------- credentials


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import importlib

    from hugmunn.core import credentials

    # NOT reloaded: it resolves its path on access now, so there is nothing
    # to refresh -- and a reloaded module leaves every earlier reference
    # pointing at the old object, which is what a monkeypatch then misses.
    # Force the file backend so the test never writes to a real keyring.
    monkeypatch.setattr(credentials, "_keyring", lambda: None)
    return credentials, tmp_path


def test_a_key_round_trips(isolated_config):
    credentials, _ = isolated_config
    from hugmunn.core.providers import Provider

    credentials.store(Provider.ANTHROPIC, "sk-ant-secret-value-1234")
    assert credentials.load(Provider.ANTHROPIC) == "sk-ant-secret-value-1234"
    assert credentials.is_signed_in(Provider.ANTHROPIC)


def test_the_key_file_is_not_readable_by_anybody_else(isolated_config):
    credentials, tmp_path = isolated_config
    from hugmunn.core.providers import Provider

    credentials.store(Provider.OPENAI, "sk-secret-value-5678")
    mode = (tmp_path / "credentials.json").stat().st_mode & 0o777
    assert mode == 0o600, f"credentials file is mode {mode:o}"


def test_the_key_never_lands_in_settings_json(isolated_config, monkeypatch):
    """settings.json is the file a user copies between machines."""
    credentials, tmp_path = isolated_config
    import importlib

    from hugmunn import config
    from hugmunn.core.providers import Provider

    importlib.reload(config)
    credentials.store(Provider.ANTHROPIC, "sk-ant-do-not-leak-me")
    config.Settings().save()
    assert "do-not-leak-me" not in (tmp_path / "settings.json").read_text()


def test_the_environment_wins_over_stored_state(isolated_config, monkeypatch):
    credentials, _ = isolated_config
    from hugmunn.core.providers import Provider

    credentials.store(Provider.ANTHROPIC, "sk-ant-stored-value-000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-env-111")
    assert credentials.load(Provider.ANTHROPIC) == "sk-ant-from-the-env-111"
    assert credentials.from_env(Provider.ANTHROPIC)


def test_clearing_removes_the_key(isolated_config):
    credentials, _ = isolated_config
    from hugmunn.core.providers import Provider

    credentials.store(Provider.OPENAI, "sk-value-to-remove-99")
    credentials.clear(Provider.OPENAI)
    assert not credentials.is_signed_in(Provider.OPENAI)


def test_storing_an_empty_key_clears_rather_than_saving_blank(isolated_config):
    credentials, _ = isolated_config
    from hugmunn.core.providers import Provider

    credentials.store(Provider.OPENAI, "sk-something-real-123")
    credentials.store(Provider.OPENAI, "   ")
    assert not credentials.is_signed_in(Provider.OPENAI)


def test_masking_shows_enough_to_identify_and_not_enough_to_use(isolated_config):
    credentials, _ = isolated_config
    from hugmunn.core.providers import Provider

    credentials.store(Provider.ANTHROPIC, "sk-ant-api03-abcdefghijklmnop4f2a")
    shown = credentials.masked(Provider.ANTHROPIC)
    assert shown == "sk-ant-…4f2a"
    assert "abcdefghijklmnop" not in shown


# ----------------------------------------------------------------- cleanup


def test_freed_is_never_negative():
    """"freed -4 MB" is not a thing; growth is reported separately."""
    grew = cleanup.Reclaim("ram", before=100, after=180)
    assert grew.freed == 0
    assert grew.grew == 80
    assert "more is in use" in grew.summary()


def test_a_cleanup_that_freed_nothing_says_so():
    assert "freed nothing" in cleanup.Reclaim("ram", 100, 100).summary()


def test_an_unmeasurable_cleanup_does_not_invent_a_number():
    result = cleanup.Reclaim("vram", measured=False, note="no GPU was found")
    assert "nothing to measure" in result.summary()
    assert "freed" not in result.summary()


def test_human_bytes_never_shows_a_fake_precision():
    assert cleanup.human_bytes(512) == "512 B"
    assert cleanup.human_bytes(1536) == "1.5 KB"
    assert cleanup.human_bytes(3 * 1024 ** 3) == "3.0 GB"


def test_clear_ram_reports_honestly_without_a_gui():
    result = cleanup.clear_ram()
    assert result.action == "ram"
    assert result.freed >= 0


def test_clear_vram_declines_when_no_server_is_running():
    class Idle:
        is_running = False
        spec = None

    result = cleanup.clear_vram(Idle())
    if result.measured:
        assert "holding no VRAM" in result.note
        assert result.freed == 0


def test_clear_vram_will_not_stop_an_adopted_server():
    """Somebody else started it; stopping it is not this button's decision."""
    class Adopted:
        is_running = True
        _adopted = True
        spec = None

    result = cleanup.clear_vram(Adopted())
    if result.measured:
        assert "not ours to stop" in result.note


def test_every_action_has_a_confirmation_that_names_what_it_does():
    for action in cleanup.ACTIONS:
        title, text = cleanup.CONFIRMATIONS[action]
        assert title and len(text) > 100
        # A confirmation that only asks "are you sure?" is not a confirmation.
        assert "will" in text


def test_the_module_never_kills_a_process():
    """Asserted by reading the source, as spaCR does. This machine runs other
    people's work, and a cleanup that could reach it is not one anybody can
    leave running."""
    import io
    import tokenize

    # Strings and comments are stripped first: the module docstring explains
    # at length which of these it refuses to use, and matching that would
    # make the guarantee impossible to document.
    with open(cleanup.__file__, "rb") as handle:
        code = "".join(
            token.string
            for token in tokenize.tokenize(handle.readline)
            if token.type not in (tokenize.STRING, tokenize.COMMENT)
        )
    for forbidden in ("os.kill", "SIGKILL", "SIGTERM", "pkill", "killpg",
                      "terminate", "drop_caches", "swapoff"):
        assert forbidden not in code, f"cleanup.py must not contain {forbidden}"


def test_disk_report_deduplicates_by_device():
    entries = cleanup.disk_report([str(Path.home()), str(Path.home())])
    assert len(entries) <= 1
