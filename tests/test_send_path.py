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

pytest.importorskip("PySide6.QtWidgets")

from hugmunn.core.client import Event  # noqa: E402


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
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

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
    import hugmunn.ui.main_window as mw

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
    from hugmunn.core import prompts as promptkit

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


# ------------------------------------------------------------ crash recovery
#
# The scenario, end to end: a conversation happens, the process disappears
# without closing anything, and the next launch offers the work back.


def test_a_turn_is_saved_as_it_happens(window, qt_app):
    """Not on exit — a save-on-quit never runs when the process dies."""
    from hugmunn.core import sessions

    run_turn(window, qt_app, "how does the vacuole form?")
    saved = sessions.load(window.session.path)
    assert saved is not None
    assert saved.turns == 1
    assert any("vacuole" in str(m.get("content") or "") for m in saved.messages)


def test_the_question_survives_a_crash_during_generation(window, qt_app):
    """The expensive part to reconstruct is usually the question."""
    from hugmunn.core import sessions

    window.composer.setPlainText("a long carefully worded question")
    # Persist happens before the worker starts; simulate dying right there.
    window.transcript.add(__import__(
        "hugmunn.ui.chat", fromlist=["UserBubble"]).UserBubble("x"))
    window.history.append({"role": "user", "content": "a long carefully worded question"})
    window._persist()

    saved = sessions.load(window.session.path)
    assert saved is not None
    assert not saved.closed_cleanly
    assert "carefully worded" in str(saved.messages[-1]["content"])


def test_a_crashed_session_is_offered_back_on_the_next_launch(qt_app, tmp_path,
                                                              monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    from hugmunn.core import sessions

    importlib.reload(sessions)
    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, s: None)
    # NOT stubbed here: _offer_restore is what this test exercises.
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, p: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_runtime_setup", lambda self: None)

    # A previous run that never closed cleanly.
    sessions.save(sessions.Session(
        id="20260101-120000-999", started=0, updated=__import__("time").time(),
        messages=[{"role": "user", "content": "the lost question"},
                  {"role": "assistant", "content": "the lost answer"}],
        closed_cleanly=False))

    from PySide6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a)
                                     or QMessageBox.StandardButton.Yes))
    window = mw.MainWindow()
    try:
        window._offer_restore()
        assert asked, "an unclean session should be offered back"
        assert "the lost question" in str(asked[0])
        # And accepting really loads it.
        assert any("lost question" in str(m.get("content") or "")
                   for m in window.history)
    finally:
        window.server.stop()
        window.close()


def test_declining_the_restore_does_not_ask_again(qt_app, tmp_path, monkeypatch):
    """Asked once. Repeating it every launch is how a prompt gets ignored."""
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    from hugmunn.core import sessions

    importlib.reload(sessions)
    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    for name in ("_offer_download", "_sign_in", "_offer_runtime_setup"):
        monkeypatch.setattr(mw.MainWindow, name, lambda self, *a: None)

    sessions.save(sessions.Session(
        id="20260101-120000-998", started=0, updated=__import__("time").time(),
        messages=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
        closed_cleanly=False))

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    window = mw.MainWindow()
    try:
        window._offer_restore()
        assert sessions.unfinished() is None, "declining must settle it"
    finally:
        window.server.stop()
        window.close()


def test_a_clean_quit_is_not_offered_back(window, qt_app):
    from hugmunn.core import sessions

    run_turn(window, qt_app)
    window.close()
    assert sessions.load(window.session.path).closed_cleanly
    assert sessions.unfinished() is None


def test_restoring_rebuilds_the_transcript_not_just_the_history(window, qt_app):
    """A restore whose messages are present and whose window is empty looks
    like it failed."""
    from hugmunn.core import sessions
    from hugmunn.ui.chat import AssistantBlock, ToolCard, UserBubble

    saved = sessions.Session(
        id="20260101-130000-1", started=0, updated=__import__("time").time(),
        messages=[
            {"role": "user", "content": "read it"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "the file contents"},
            {"role": "assistant", "content": "It says hello."},
        ])
    sessions.save(saved)

    window._restore(saved)
    qt_app.processEvents()

    kinds = [type(window.transcript._layout.itemAt(i).widget()).__name__
             for i in range(window.transcript._layout.count() - 1)]
    assert "UserBubble" in kinds
    assert "ToolCard" in kinds
    assert "AssistantBlock" in kinds


def test_a_new_conversation_starts_a_new_session(window, qt_app):
    run_turn(window, qt_app)
    first = window.session.id
    window._new_conversation()
    assert window.session.id != first
    assert window.history == []
