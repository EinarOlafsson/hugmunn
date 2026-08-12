"""The autonomy level is the only thing that decides whether a call is confirmed.

Reported: "I set the model to have full access after starting with limited
access but now it is still asking me for permission."

The cause was two controls for one decision. ``auto_approve_reads`` predates
the autonomy tiers, and the gate read ``if needs_ok or not auto_approve_reads``
— so with the checkbox off, *every* tier confirmed *every* call, and "4 · Full"
was indistinguishable from "1 · Confirm everything". The checkbox silently won.

Autonomy already covers what the checkbox meant: level 1 *is* "confirm
everything, reads included". So the checkbox is gone and its old value
migrates to level 1.
"""

from __future__ import annotations

import importlib

import pytest

from hugmunn.core import autonomy as autonomykit
from hugmunn.core.agent import Agent
from hugmunn.core.client import Event


class _Call:
    def __init__(self, name, arguments):
        self.id = "c1"
        self.name = name
        self._arguments = arguments

    @property
    def arguments(self):
        import json

        return json.dumps(self._arguments)

    def parsed_arguments(self):
        return self._arguments


class FakeClient:
    """Asks for one tool, then answers."""

    def __init__(self, tool="read_file", arguments=None):
        self.turn = 0
        self.tool = tool
        self.arguments = arguments or {"path": "/tmp/x"}

    def is_ready(self):
        return True

    def stream(self, messages, tools=None, temperature=None, max_tokens=4096,
               cancel=None, thinking=None):
        self.turn += 1
        if self.turn == 1:
            yield Event("tool_calls", tool_calls=[_Call(self.tool, self.arguments)])
        else:
            yield Event("content", text="done")
        yield Event("done")


def asks(level, tool="read_file", arguments=None, workdir="/tmp", **kwargs):
    """Whether a call at this autonomy level reaches the approval dialog."""
    asked = []
    agent = Agent(client=FakeClient(tool, arguments), workdir=workdir,
                  system_prompt="", autonomy=level, **kwargs)
    list(agent.run([{"role": "user", "content": "go"}],
                   approve=lambda n, s, a: asked.append(n) or True))
    return bool(asked)


# ------------------------------------------------------------- the report


@pytest.mark.parametrize("stale_checkbox", [True, False])
def test_full_autonomy_never_asks_whatever_the_old_checkbox_said(stale_checkbox):
    """The exact bug: level 4 still confirming every call."""
    assert not asks(autonomykit.Autonomy.FULL, "write_file",
                    {"path": "/tmp/x", "content": "y"},
                    auto_approve_reads=stale_checkbox)


@pytest.mark.parametrize("stale_checkbox", [True, False])
def test_the_tier_alone_decides(stale_checkbox):
    """Same answers with the deprecated flag either way, at every level."""
    results = {
        level: asks(level, "write_file", {"path": "/tmp/x", "content": "y"},
                    auto_approve_reads=stale_checkbox)
        for level in autonomykit.Autonomy
    }
    assert results == {
        autonomykit.Autonomy.CONFIRM_ALL: True,
        autonomykit.Autonomy.ASK_TO_WRITE: True,
        autonomykit.Autonomy.WORKSPACE: False,
        autonomykit.Autonomy.FULL: False,
    }


def test_raising_the_level_takes_effect_on_the_next_message():
    """The user's sequence: start restricted, then grant full access."""
    write = ("write_file", {"path": "/tmp/x", "content": "y"})
    assert asks(autonomykit.Autonomy.ASK_TO_WRITE, *write)
    assert not asks(autonomykit.Autonomy.FULL, *write)


# --------------------------------------------------- each tier still means it


def test_level_one_confirms_even_a_read():
    assert asks(autonomykit.Autonomy.CONFIRM_ALL)


def test_level_two_lets_reads_through():
    assert not asks(autonomykit.Autonomy.ASK_TO_WRITE)


def test_level_three_asks_outside_the_working_directory(tmp_path):
    inside = asks(autonomykit.Autonomy.WORKSPACE, "write_file",
                  {"path": str(tmp_path / "a.txt"), "content": "y"},
                  workdir=str(tmp_path))
    outside = asks(autonomykit.Autonomy.WORKSPACE, "write_file",
                   {"path": "/etc/hosts", "content": "y"}, workdir=str(tmp_path))
    assert not inside and outside


def test_level_four_still_protects_system_paths():
    """"Full" was never unrestricted, and the label says so."""
    assert asks(autonomykit.Autonomy.FULL, "write_file",
                {"path": "/usr/lib/thing", "content": "y"})


def test_level_four_still_protects_installed_packages():
    assert asks(autonomykit.Autonomy.FULL, "write_file",
                {"path": "/opt/env/lib/python3.10/site-packages/x.py", "content": "y"})


def test_an_editable_install_stays_writable_at_level_four():
    """The carve-out that makes level 4 useful rather than merely dangerous."""
    assert not asks(autonomykit.Autonomy.FULL, "write_file",
                    {"path": "/home/me/repo/hugmunn/src/hugmunn/x.py",
                     "content": "y"})


# --------------------------------------------------------------- migration


def test_the_old_checkbox_migrates_to_confirm_everything(tmp_path, monkeypatch):
    """Somebody who unticked it meant level 1, and should land there."""
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "settings.json").write_text(
        '{"auto_approve_reads": false, "autonomy_level": 4}', encoding="utf-8")

    settings = config.Settings.load()
    assert settings.autonomy_level == 1
    assert settings.auto_approve_reads is True     # migrated, not re-applied


def test_a_normal_settings_file_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    (tmp_path / "settings.json").write_text(
        '{"auto_approve_reads": true, "autonomy_level": 4}', encoding="utf-8")
    assert config.Settings.load().autonomy_level == 4


def test_the_window_has_one_control_for_this_decision(qt_app, tmp_path, monkeypatch):
    """Two controls for one decision is what produced the report."""
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    for name in ("_offer_download", "_offer_restore", "_sign_in", "_offer_runtime_setup"):
        monkeypatch.setattr(mw.MainWindow, name, lambda self, *a: None)

    window = mw.MainWindow()
    try:
        assert not hasattr(window, "auto_reads_check"), (
            "the auto-approve checkbox overrode the autonomy tier; it is gone")
        assert hasattr(window, "autonomy_combo")
    finally:
        window.server.stop()
        window.close()


def test_the_agent_no_longer_consults_the_deprecated_flag():
    """Checked in the source, so it cannot creep back into the gate."""
    import pathlib

    import hugmunn.core.agent as agent_module

    source = pathlib.Path(agent_module.__file__).read_text(encoding="utf-8")
    gate = [line for line in source.splitlines()
            if "approve(" in line and "if " in line]
    assert not any("auto_approve_reads" in line for line in gate)
