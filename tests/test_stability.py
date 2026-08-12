"""Concurrency, cleanup and the paths that only fail under load.

Written after a sweep rather than after a report, so these are the failures
that had not happened yet: two clients appending to one conversation, a
listening socket outliving its window, and a summary that freezes the GUI
thread for as long as the model feels like taking.
"""

from __future__ import annotations

import importlib
import socket
import threading
import time

import pytest


@pytest.fixture()
def window(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    for name in ("_offer_download", "_offer_restore", "_sign_in", "_offer_runtime_setup"):
        monkeypatch.setattr(mw.MainWindow, name, lambda self, *a: None)
    win = mw.MainWindow()
    yield win
    win.server.stop()
    win.close()


# ------------------------------------------------- one turn, two clients


def test_the_desktop_and_the_browser_share_one_turn_lock(window):
    """Two locks that do not know about each other prevent nothing."""
    from hugmunn.ui.remote_bridge import RemoteBridge

    bridge = RemoteBridge(window)
    assert bridge._turn is window._turn_lock


def test_a_remote_turn_blocks_a_desktop_turn(window, qt_app):
    """Both appending to one history interleaves it into nonsense."""
    from hugmunn.ui.remote_bridge import RemoteBridge

    bridge = RemoteBridge(window)
    window._turn_lock.acquire()          # stand in for a remote turn in flight
    try:
        window.composer.setPlainText("hello from the desk")
        window._send()
        assert window._agent_worker is None, "the desktop turn should not have started"
    finally:
        window._turn_lock.release()


def test_a_desktop_turn_blocks_a_remote_turn(window):
    from hugmunn.ui.remote_bridge import RemoteBridge

    bridge = RemoteBridge(window)
    window._turn_lock.acquire()
    try:
        events = list(bridge.send("hello from the phone"))
        assert events and events[0]["kind"] == "error"
        assert "already running" in events[0]["text"]
    finally:
        window._turn_lock.release()


def test_the_lock_is_released_when_a_command_is_handled(window):
    """A command returns early; the lock must not be left held."""
    window.composer.setPlainText("/help")
    window._send()
    assert not window._turn_lock.locked()


def test_the_lock_is_released_when_no_client_can_be_built(window, monkeypatch):
    """This branch shows a modal, which in a headless run has nothing to
    dismiss it — so the dialog is stubbed rather than the behaviour changed."""
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(type(window), "_ready_to_send", lambda self: True)
    monkeypatch.setattr(type(window), "_build_client", lambda self: None)
    window.composer.setPlainText("hello")
    window._send()
    assert not window._turn_lock.locked()


def test_the_lock_is_released_after_a_turn(window, qt_app, monkeypatch):
    from hugmunn.core.client import Event

    class Fake:
        def is_ready(self): return True
        def stream(self, *a, **k):
            yield Event("content", text="hi")
            yield Event("done")

    monkeypatch.setattr(type(window), "_ready_to_send", lambda self: True)
    monkeypatch.setattr(type(window), "_build_client", lambda self: Fake())
    window.composer.setPlainText("hello")
    window._send()
    if window._agent_worker is not None:
        window._agent_worker.wait(5000)
    qt_app.processEvents()
    assert not window._turn_lock.locked()


def test_the_bridge_releases_the_lock_even_when_a_turn_raises(window, monkeypatch):
    from hugmunn.ui.remote_bridge import RemoteBridge

    bridge = RemoteBridge(window)
    monkeypatch.setattr(type(window), "build_agent",
                        lambda self, client=None: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        list(bridge.send("go"))
    assert not window._turn_lock.locked()


# ------------------------------------------------------------- cleanup


def port_is_bound(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def test_closing_the_window_releases_the_listening_port(qt_app, tmp_path, monkeypatch):
    """A daemon thread dies with the process, but the port stays bound until
    then — which is the difference between reopening the app and being told
    the address is in use."""
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    for name in ("_offer_download", "_offer_restore", "_sign_in", "_offer_runtime_setup"):
        monkeypatch.setattr(mw.MainWindow, name, lambda self, *a: None)

    win = mw.MainWindow()
    message = win.start_remote()
    assert win._remote is not None, message
    port = win._remote.port
    assert port_is_bound(port)

    win.server.stop()
    win.close()
    assert win._remote is None
    assert not port_is_bound(port), "the port should be free once the window closes"


def test_starting_remote_twice_does_not_bind_twice(window):
    first = window.start_remote()
    port = window._remote.port
    second = window.start_remote()
    assert window._remote.port == port
    assert "Remote access is on" in second
    window.stop_remote()


def test_stopping_remote_is_idempotent(window):
    assert "already off" in window.stop_remote()
    window.start_remote()
    assert "off" in window.stop_remote()
    assert "already off" in window.stop_remote()


def test_the_resource_timer_stops_with_the_window(window):
    meters = window.resources
    window.close()
    assert not meters._timer.isActive()


# --------------------------------------------------- the summary deadline


def test_summarising_gives_up_rather_than_freezing_the_window(window, monkeypatch):
    """It runs on the thread driving the turn, which on the desktop is the
    GUI thread. An unbounded call there reads as a crash."""
    from hugmunn.core.client import Event

    class Slow:
        def is_ready(self): return True
        def stream(self, messages, cancel=None, **k):
            # Never finishes on its own; only the deadline stops it.
            while cancel is None or not cancel.is_set():
                time.sleep(0.02)
                yield Event("content", text="x")

    monkeypatch.setattr(type(window), "_build_client", lambda self: Slow())
    monkeypatch.setattr(type(window), "SUMMARY_TIMEOUT", 0.4)

    started = time.monotonic()
    result = window._summarise_span([{"role": "user", "content": "long"}])
    elapsed = time.monotonic() - started
    assert result == "", "an abandoned summary must return nothing, not a fragment"
    assert elapsed < 5, f"the deadline did not fire ({elapsed:.1f}s)"


def test_a_summary_that_finishes_in_time_is_kept(window, monkeypatch):
    from hugmunn.core.client import Event

    class Quick:
        def is_ready(self): return True
        def stream(self, messages, cancel=None, **k):
            yield Event("content", text="notes about earlier")
            yield Event("done")

    monkeypatch.setattr(type(window), "_build_client", lambda self: Quick())
    assert "notes about earlier" in window._summarise_span(
        [{"role": "user", "content": "x"}])


def test_the_summary_deadline_is_a_bounded_number():
    from hugmunn.ui.main_window import MainWindow

    assert 5 <= MainWindow.SUMMARY_TIMEOUT <= 120


# ------------------------------------------------------- thread hygiene


def test_no_threads_are_left_running_after_a_window_closes(qt_app, tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    from hugmunn.ui import main_window as mw

    # NOT reloaded: config resolves its paths on access now, so there is
    # nothing to refresh -- and reloading a module that defines QWidget
    # subclasses makes new Qt types while old instances are still alive.
    for name in ("_offer_download", "_offer_restore", "_sign_in", "_offer_runtime_setup"):
        monkeypatch.setattr(mw.MainWindow, name, lambda self, *a: None)

    before = {t.name for t in threading.enumerate()}
    win = mw.MainWindow()
    win.start_remote()
    win.server.stop()
    win.close()
    time.sleep(0.5)
    leaked = {t.name for t in threading.enumerate()} - before
    assert not any("hugmunn-remote" in name for name in leaked), leaked
