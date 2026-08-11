"""Actually pressing Send on a real window.

Every UI test so far built the window and poked at controls. None of them ran
a turn, which is how ``self._thinking()`` shipped: ``_thinking`` was already
the live ThinkingCard widget, an instance attribute shadows a method of the
same name, and so the method was unreachable. Every send raised
``'NoneType' object is not callable`` and the app aborted.

A window whose controls all read correctly and whose Send button crashes is
not a tested window.
"""

from __future__ import annotations

import ast
import importlib
import os
import pathlib

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from localagent.core.client import Event  # noqa: E402


class FakeClient:
    """A client that answers immediately, recording what it was asked."""

    def __init__(self):
        self.calls = []

    def is_ready(self):
        return True

    def model_name(self):
        return "fake"

    def stream(self, messages, tools=None, temperature=None, max_tokens=4096,
               cancel=None, thinking=None):
        self.calls.append({"messages": list(messages), "thinking": thinking,
                           "tools": tools})
        yield Event("content", text="an answer")
        yield Event("done", timings={"predicted_per_second": 42.0})


@pytest.fixture()
def window(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAGENT_CONFIG_DIR", str(tmp_path))
    from localagent import config

    importlib.reload(config)
    from localagent.ui import main_window as mw

    importlib.reload(mw)
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, s: None)
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, p: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_runtime_setup", lambda self: None)

    win = mw.MainWindow()
    client = FakeClient()
    monkeypatch.setattr(mw.MainWindow, "_build_client", lambda self: client)
    monkeypatch.setattr(mw.MainWindow, "_ready_to_send", lambda self: True)
    win.client = client
    yield win
    if win._agent_worker is not None:
        win._agent_worker.wait(3000)
    win.server.stop()
    win.close()


def run_turn(window, qt_app, text="hello"):
    window.composer.setPlainText(text)
    window._send()
    worker = window._agent_worker
    if worker is not None:
        worker.wait(5000)
    qt_app.processEvents()
    return window.client.calls


# --------------------------------------------------------------- the crash


def test_pressing_send_does_not_raise(window, qt_app):
    """The regression: every send aborted the process."""
    assert run_turn(window, qt_app), "the client was never called"


def test_enter_in_the_composer_sends(window, qt_app):
    """The path the traceback came through — keyPressEvent, not the button."""
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent

    window.composer.setPlainText("hello")
    window.composer.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                  Qt.KeyboardModifier.NoModifier))
    if window._agent_worker is not None:
        window._agent_worker.wait(5000)
    qt_app.processEvents()
    assert window.client.calls


def test_no_method_is_shadowed_by_an_instance_attribute():
    """The class of bug, checked directly.

    ``self._thinking = None`` in the constructor silently made
    ``self._thinking()`` uncallable. Python gives no warning for this and the
    failure appears only when the method is called.
    """
    import localagent.ui.main_window as mw

    source = pathlib.Path(mw.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
        assigned = set()
        for sub in ast.walk(node):
            targets = (sub.targets if isinstance(sub, ast.Assign)
                       else [sub.target] if isinstance(sub, ast.AnnAssign) else [])
            for target in targets:
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"):
                    assigned.add(target.attr)
        clash = methods & assigned
        assert not clash, f"{node.name}: {sorted(clash)} is both a method and an attribute"


# ------------------------------------------------------- the turn's content


def test_the_reasoning_setting_reaches_the_client(window, qt_app):
    window.thinking_combo.setCurrentIndex(
        window.thinking_combo.findData(False))
    assert run_turn(window, qt_app)[0]["thinking"] is False

    window.history.clear()
    window.thinking_combo.setCurrentIndex(window.thinking_combo.findData(True))
    assert run_turn(window, qt_app, "again")[-1]["thinking"] is True


def test_the_system_prompt_preset_reaches_the_client(window, qt_app):
    from localagent.core import prompts as promptkit

    window.prompt_combo.setCurrentIndex(window.prompt_combo.findData("none"))
    sent = run_turn(window, qt_app)[0]["messages"]
    system = next((m for m in sent if m["role"] == "system"), None)
    # "None" means no persona; whatever is sent must not reintroduce one.
    assert system is None or "You are a capable" not in system["content"]

    window.history.clear()
    window.prompt_combo.setCurrentIndex(window.prompt_combo.findData("research"))
    sent = run_turn(window, qt_app, "again")[-1]["messages"]
    system = next((m for m in sent if m["role"] == "system"), None)
    assert system is not None and "research" in system["content"].lower()


def test_the_answer_reaches_the_transcript(window, qt_app):
    run_turn(window, qt_app)
    assert any("assistant" == m.get("role") for m in window.history)


def test_the_context_meter_updates_after_a_turn(window, qt_app):
    before = window.context_meter.value()
    run_turn(window, qt_app, "hello " * 200)
    assert window.context_meter.value() >= before


def test_an_empty_message_sends_nothing(window, qt_app):
    window.composer.setPlainText("   ")
    window._send()
    assert not window.client.calls


def test_every_sidebar_control_survives_a_turn(window, qt_app):
    """Change everything, then send. Nothing may raise."""
    window.effort_combo.setCurrentIndex(3)
    window.autonomy_combo.setCurrentIndex(2)
    window.thinking_combo.setCurrentIndex(1)
    window.context_combo.setCurrentIndex(1)
    window.prompt_combo.setCurrentIndex(1)
    window.tools_check.setChecked(True)
    assert run_turn(window, qt_app)
