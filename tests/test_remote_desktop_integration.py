"""Drive the real Qt bridge from a request thread, including shared approvals."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from PySide6.QtCore import QThread
from hugmunn.core.agent import AgentEvent


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv('HUGMUNN_CONFIG_DIR', str(tmp_path))
    from hugmunn.ui.main_window import MainWindow
    for name in ('_offer_download', '_offer_restore', '_offer_runtime_setup', '_refresh_catalogues', '_sign_in'):
        monkeypatch.setattr(MainWindow, name, lambda *args, **kwargs: None)
    w = MainWindow()
    w.settings.remote_port = 0
    yield w
    w._cancel()
    deadline = time.monotonic() + 5
    while w._is_busy() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(.01)
    w.close()


def threaded(app, fn):
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(fn)
        deadline = time.monotonic() + 10
        while not result.done() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert result.done(), 'GUI dispatch timed out'
        return result.result()


def test_remote_starts_without_a_model_and_does_not_put_password_in_chat(window, qt_app, monkeypatch):
    monkeypatch.setattr(window, '_show_remote_dialog', lambda: None)
    monkeypatch.setattr(window, '_ready_to_send', lambda: False)
    window.composer.setPlainText('/remote')
    window._send()
    assert window._remote.is_running
    assert not window._turn_lock.locked()
    assert window._remote_password not in window._remote_description()
    assert window.history == []
    state = threaded(qt_app, window._bridge.snapshot)
    assert state['skills'] and state['theme_options']


def test_remote_mutations_are_on_gui_thread_and_validate(window, qt_app):
    window.start_remote()
    bridge = window._bridge
    called = []
    original = window.apply_theme
    def apply(name):
        called.append(QThread.currentThread() == window.thread())
        original(name)
    window.apply_theme = apply
    result = threaded(qt_app, lambda: bridge.apply_setting('theme', 'glass'))
    assert result['theme'] == 'glass' and called == [True]
    with pytest.raises(ValueError):
        threaded(qt_app, lambda: bridge.apply_setting('autonomy', 999))
    with pytest.raises(ValueError):
        threaded(qt_app, lambda: bridge.apply_setting('thinking', 'false'))
    window._turn_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match='current task'):
            threaded(qt_app, lambda: bridge.apply_setting('theme', 'dark'))
    finally:
        window._turn_lock.release()


def test_remote_turn_uses_desktop_worker_and_browser_resolves_approval(window, qt_app, monkeypatch):
    window.start_remote()
    bridge = window._bridge
    monkeypatch.setattr(window, '_ready_to_send', lambda: True)
    monkeypatch.setattr(window, '_build_client', lambda: object())
    class FakeAgent:
        def run(self, history, approve, cancel):
            if approve('run_command', 'echo hello', {'command': 'echo hello'}):
                yield AgentEvent('content', text='Approved')
                history.append({'role': 'assistant', 'content': 'Approved'})
            yield AgentEvent('done')
    monkeypatch.setattr(window, 'build_agent', lambda *args: FakeAgent())
    collected = []
    worker = threading.Thread(target=lambda: collected.extend(bridge.send('hello')))
    worker.start()
    deadline = time.monotonic() + 5
    pending = []
    while not pending and time.monotonic() < deadline:
        qt_app.processEvents()
        pending = bridge.approvals.outstanding()
        time.sleep(.005)
    assert pending
    assert window._agent_worker is not None
    assert threaded(qt_app, lambda: bridge.resolve_approval(pending[0]['id'], True))
    while worker.is_alive() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(.005)
    worker.join(1)
    assert not worker.is_alive()
    assert any(x.get('text') == 'Approved' for x in collected)
    assert window.history[-1]['content'] == 'Approved'
    assert not bridge.approvals.outstanding()
    assert not window._approval_dialogs


def test_empty_send_never_leaks_turn_lock(window):
    window.composer.setPlainText('')
    window._send()
    assert not window._turn_lock.locked()
