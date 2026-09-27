"""All browser operations are dispatched onto Qt's GUI thread.

Desktop and browser share the same AgentWorker, turn lock and approval registry.
The HTTP thread only waits for queued events; it never reads a widget.
"""
from __future__ import annotations

import copy
import queue
import threading
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .. import config
from ..core import autonomy, commands, context, effort, persistence, providers, sessions
from ..core.webserver import PendingApprovals
from . import theme


class RemoteBridge(QObject):
    requested = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._turn = window._turn_lock
        self.approvals = PendingApprovals()
        self._stream = None
        self._live = []
        self._closed = False
        self.requested.connect(self._dispatch)

    @Slot(object)
    def _dispatch(self, request):
        with request['lock']:
            if request['abandoned'] or self._closed:
                request['done'].set()
                return
            try:
                request['value'] = self._on_gui(request['fn'])
            except Exception as exc:
                request['error'] = exc
            finally:
                request['done'].set()

    def invoke(self, fn, timeout=10):
        if self._closed:
            raise RuntimeError('Desktop is closing.')
        if QThread.currentThread() == self.thread():
            return self._on_gui(fn)
        request = dict(fn=fn, done=threading.Event(), lock=threading.Lock(), abandoned=False)
        self.requested.emit(request)
        if not request['done'].wait(timeout):
            request['abandoned'] = True
            raise RuntimeError('Desktop is busy. Try again shortly.')
        if 'error' in request:
            raise request['error']
        if 'value' not in request:
            raise RuntimeError('Desktop is closing.')
        return request['value']

    def _on_gui(self, fn):
        previous = getattr(self.window, '_remote_driving', False)
        self.window._remote_driving = True
        try:
            return fn()
        finally:
            self.window._remote_driving = previous

    def snapshot(self):
        return self.invoke(self._snapshot)

    def _snapshot(self):
        w, s = self.window, self.window.settings
        budget = w._context_budget()
        spec = w._current_spec()
        options = lambda enum, labels: [{'value': int(v), 'label': labels[v]} for v in enum]
        return {
            'model': spec.label if spec else '', 'model_key': w.model_combo.currentData(),
            'provider': s.provider,
            'provider_options': [{'value': p.value, 'label': providers.LABELS[p]} for p in providers.Provider],
            'model_options': [{'value': w.model_combo.itemData(i), 'label': w.model_combo.itemText(i)}
                              for i in range(w.model_combo.count()) if w.model_combo.itemData(i)],
            'autonomy': s.autonomy_level, 'autonomy_options': options(autonomy.Autonomy, autonomy.LABELS),
            'effort': s.effort_level, 'effort_options': options(effort.Effort, effort.LABELS),
            'persistence': s.persistence_level,
            'persistence_options': options(persistence.Persistence, persistence.LABELS),
            'thinking': w._thinking_enabled(), 'tools_enabled': w._tools_active(),
            'workdir': s.workdir, 'system_prompt': s.system_prompt, 'goal': w._goal,
            'theme': s.theme, 'theme_options': [{'value': n, 'label': theme.LABELS[n]} for n in ('system', *theme.THEMES)],
            'palette': theme.active(), 'panel_opacity': s.panel_opacity,
            'window_opacity': s.window_opacity, 'rounded_windows': s.rounded_windows,
            'busy': w._is_busy(), 'server_running': w.server.is_running,
            'server_status': w.server_status.text(), 'session_id': w.session.id,
            'context': {'used': context.total_tokens(w.history), 'available': budget.available, 'limit': budget.limit},
            'context_strategy': s.context_strategy,
            'messages': copy.deepcopy(w.history[-100:]), 'live_events': list(self._live),
            'skills': [{'key': x.key, 'name': x.name, 'category': x.category,
                        'description': x.description, 'enabled': x.key in w._enabled_skills,
                        'tokens': x.approx_tokens} for x in w._skills],
            'plugins': [{'key': x.tool.name, 'enabled': x.tool.name in w._enabled_plugins} for x in w._plugins if x.ok],
            'sessions': [{'id': x.id, 'title': x.title} for x in sessions.recent(20)],
            'pending_approvals': self.approvals.outstanding(),
            'gpu': {'busy': w.gpu.busy or w._gpu_worker is not None,
                    'status': w.gpu.status, 'output': w.gpu.output[-16000:]},
        }

    def send(self, text):
        events = queue.Queue(maxsize=256)
        try:
            result = self.invoke(lambda: self._begin(text, events), timeout=60)
        except (ValueError, RuntimeError) as exc:
            yield {"kind": "error", "text": str(exc)}
            return
        if result is not None:
            yield {'kind': 'notice', 'text': result}
            return
        try:
            while not self._closed:
                try:
                    event = events.get(timeout=1)
                except queue.Empty:
                    yield {'kind': 'ping'}
                    continue
                if event is None:
                    break
                yield event
        finally:
            # Disconnecting a browser does not abort desktop work.
            if self._stream is events:
                self._stream = None

    def _begin(self, text, events):
        w = self.window
        if w._is_busy():
            raise RuntimeError('A task is already running. Wait or press Stop.')
        if commands.is_command(text):
            name, argument = commands.parse(text)
            if name in ('remote', 'skills'):
                return 'Use the browser controls for skills. Manage login and networking on the desktop.'
            if name == 'model' and argument:
                self._setting('model', argument)
                return f'Model: {argument}'
            return w.run_command(text).message
        if not w._ready_to_send():
            raise RuntimeError('Start a model or connect a provider before sending.')
        self._stream = events
        try:
            w._submit_text(text)
        finally:
            if w._agent_worker is None:
                self._stream = None
        if w._agent_worker is None:
            self._stream = None
            return 'Message could not start. Check the model and context limit.'
        return None

    def observe_worker(self, worker):
        self._live = []
        worker.event.connect(self._event)
        worker.finished.connect(self._finished)

    @Slot(object)
    def _event(self, event):
        payload = {'kind': event.kind, 'text': event.text, 'tool_name': event.tool_name,
                   'tool_summary': event.tool_summary, 'tool_id': event.tool_id}
        if self._stream is not None:
            try:
                self._stream.put_nowait(payload)
            except queue.Full:
                self._stream.get_nowait()
                self._stream.put_nowait(None)
                self._stream = None
        if event.kind in ('content', 'reasoning') and self._live and self._live[-1]['kind'] == event.kind:
            self._live[-1]['text'] = (self._live[-1]['text'] + event.text)[-100000:]
        elif event.kind == 'done':
            self._live = []
        else:
            self._live.append(payload)
            self._live = self._live[-200:]

    @Slot()
    def _finished(self):
        if self._stream is not None:
            self._stream.put(None)
            self._stream = None
        self._live = []
        for call_id in list(self.window._approval_dialogs):
            self.window._resolve_shared_approval(call_id, False)
        self.approvals.release_all(False)

    def resolve_approval(self, call_id, allowed):
        return self.invoke(lambda: self.window._resolve_shared_approval(call_id, allowed))

    def apply_setting(self, key, value):
        def apply():
            if self.window._is_busy():
                raise RuntimeError('Wait until the current task has finished before changing settings.')
            self._setting(key, value)
            return self._snapshot()
        return self.invoke(apply)

    def _setting(self, key, value):
        w, s = self.window, self.window.settings
        if key in ('autonomy', 'effort', 'persistence'):
            if type(value) is not int or not 1 <= value <= 4:
                raise ValueError('Choose a level from 1 to 4.')
            combo = getattr(w, key + '_combo')
            combo.setCurrentIndex(combo.findData(value))
        elif key == 'provider':
            if value not in [p.value for p in providers.Provider]:
                raise ValueError('Unknown provider.')
            w._offered_login.add(providers.Provider(value))
            w.provider_combo.setCurrentIndex(w.provider_combo.findData(value))
        elif key == 'model':
            if w.server.is_running:
                raise ValueError('Stop the local model before switching models.')
            index = w.model_combo.findData(value)
            if index < 0:
                raise ValueError('Unknown model.')
            spec = config.by_key(value)
            if spec and not spec.has_weights():
                raise ValueError('Download this model on the desktop first.')
            w.model_combo.setCurrentIndex(index)
        elif key in ('thinking', 'tools_enabled', 'rounded_windows'):
            if type(value) is not bool:
                raise ValueError('Expected true or false.')
            if key == 'thinking':
                w.thinking_combo.setCurrentIndex(w.thinking_combo.findData(value))
            elif key == 'tools_enabled':
                w.tools_check.setChecked(value)
            else:
                w.apply_appearance(rounded=value)
        elif key in ('window_opacity', 'panel_opacity'):
            if type(value) not in (int, float) or not 50 <= value <= 100:
                raise ValueError('Opacity must be 50–100 percent.')
            w.apply_appearance(**{key: int(value)})
        elif key == 'theme':
            if value not in ('system', *theme.THEMES):
                raise ValueError('Unknown theme.')
            w.apply_theme(value)
        elif key == 'skills':
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError('Expected a list of skill keys.')
            if set(value) - {x.key for x in w._skills}:
                raise ValueError('Unknown skill.')
            w._set_skills(set(value))
        elif key == 'plugins':
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError('Expected plugin names.')
            if set(value) - {x.tool.name for x in w._plugins if x.ok}:
                raise ValueError('Unknown plugin.')
            w._enabled_plugins = set(value)
            s.enabled_plugins = value
            w.plugins_button.setMenu(w._build_plugins_menu())
            w._update_plugins_button()
        elif key == 'context':
            if type(value) is not int or not w.context_spin.minimum() <= value <= w.context_spin.maximum():
                raise ValueError('Context size is outside the supported range.')
            w.context_spin.setValue(value)
        elif key in ('workdir', 'system_prompt', 'goal'):
            if not isinstance(value, str) or len(value) > 32000:
                raise ValueError('Expected text of at most 32000 characters.')
            if key == 'goal':
                w._goal = value
            elif key == 'workdir':
                from pathlib import Path
                path = Path(value).expanduser().resolve()
                if not path.is_dir():
                    raise ValueError('Working directory does not exist.')
                s.workdir = str(path)
                w._update_workdir_label()
            else:
                w.system_edit.setPlainText(value)
        else:
            raise ValueError('Unknown setting.')
        s.save()

    def action(self, name, value=None):
        def apply():
            w = self.window
            if w._is_busy():
                raise RuntimeError('A task is already running. Wait or press Stop.')
            if name == 'new':
                w._new_conversation()
            elif name == 'save':
                w._persist()
            elif name == 'restore':
                found = next((x for x in sessions.recent(50) if x.id == value), None)
                if found is None:
                    raise ValueError('Conversation not found.')
                if w.server.is_running:
                    raise ValueError('Stop the model before restoring a conversation with its settings.')
                w._restore(found)
            elif name in ('start_model', 'stop_model'):
                spec = w._current_spec()
                if name == 'start_model':
                    if w._is_cloud() or spec is None or not spec.readiness().can_launch:
                        raise ValueError('Download a local model and set up its runtime on the desktop first.')
                    if not w.server.is_running:
                        w._start_server_worker(spec)
                elif w.server.is_running:
                    w._stop_server_worker()
            elif name == 'gpu':
                if not isinstance(value, str) or not value.strip():
                    raise ValueError('A GPU command is required.')
                w.start_gpu_task(value)
            elif name == 'import_skills':
                w.import_codex_skills()
            else:
                raise ValueError('Unknown action.')
            return self._snapshot()
        return self.invoke(apply)

    def cancel(self):
        self.invoke(self.window._cancel)

    def close(self):
        self._closed = True
        self.approvals.release_all(False)
        if self._stream is not None:
            self._stream.put(None)
