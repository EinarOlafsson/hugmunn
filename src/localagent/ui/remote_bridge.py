"""Lets the browser drive the same conversation the desktop window is showing.

There is one conversation, not two that are kept in sync. A message sent from
a phone lands in the desktop transcript because the bridge appends to the same
history and mirrors the same events; nothing is copied or reconciled.

Two threading facts shape everything here:

* **Qt widgets may only be touched from the GUI thread.** The web server hands
  each request to its own thread, so every visible effect goes out as a queued
  signal rather than a direct call. This is the same round trip the agent
  worker already uses for tool approval, for the same reason.
* **Only one turn may run at a time.** Not because of the model -- llama.cpp
  would serialise them -- but because the conversation is a single list and
  two turns appending to it interleave into nonsense. A second request is
  refused with a reason rather than queued, because a phone waiting silently
  behind a desktop turn looks broken.
"""

from __future__ import annotations

import threading
from typing import Any, Iterator

from PyQt6.QtCore import QObject, pyqtSignal

from ..core import autonomy as autonomykit
from ..core import commands as commandkit
from ..core import context as contextkit
from ..core import effort as effortkit
from ..core.agent import Agent
from ..core.webserver import PendingApprovals


class RemoteBridge(QObject):
    """Implements ``webserver.Bridge`` against a live MainWindow."""

    #: Mirrors a remote turn into the desktop transcript. Queued, because it
    #: is emitted from a request thread and the slot touches widgets.
    event = pyqtSignal(dict)
    #: A tool call from a remote turn that needs a human. The desktop shows
    #: its own dialog; whichever client answers first releases the agent.
    approval = pyqtSignal(str, str, str, dict)   # id, name, summary, arguments
    state_changed = pyqtSignal()

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.approvals = PendingApprovals()
        # The window's lock, not one of our own: a desktop turn and a remote
        # turn appending to one history interleave into nonsense, and two
        # locks that do not know about each other prevent nothing.
        self._turn = window._turn_lock
        self._cancel = threading.Event()

    # ----------------------------------------------------------- read-only

    def snapshot(self) -> dict[str, Any]:
        """Everything a freshly-loaded page needs. Safe from any thread.

        Reads plain Python state rather than querying widgets: a widget read
        from a request thread is undefined behaviour even when it appears to
        work, and every value here has a non-widget source of truth.
        """
        window = self.window
        settings = window.settings
        budget = self._budget()
        return {
            "model": window.session.model_label or settings.model_key,
            "provider": settings.provider,
            "autonomy": settings.autonomy_level,
            "autonomy_options": [
                {"value": int(level), "label": autonomykit.LABELS[level]}
                for level in autonomykit.Autonomy
            ],
            "effort": settings.effort_level,
            "effort_options": [
                {"value": int(level), "label": effortkit.LABELS[level]}
                for level in effortkit.Effort
            ],
            "thinking": settings.thinking.get(settings.model_key, False),
            "goal": getattr(window, "_goal", ""),
            "busy": self._turn.locked(),
            "context": {
                "used": contextkit.total_tokens(window.history),
                "available": budget.available,
                "limit": budget.limit,
            },
            "messages": [
                {"role": m.get("role"), "content": m.get("content"),
                 "name": m.get("name", "")}
                for m in window.history[-60:]
            ],
            "pending_approvals": self.approvals.outstanding(),
        }

    def _budget(self) -> contextkit.Budget:
        try:
            return self.window._context_budget()
        except Exception:  # noqa: BLE001 - a snapshot must never fail
            return contextkit.Budget(limit=8192)

    # -------------------------------------------------------------- writing

    def send(self, text: str) -> Iterator[dict[str, Any]]:
        """Run one turn from a request thread, yielding events as they occur."""
        text = (text or "").strip()
        if not text:
            return

        if not self._turn.acquire(blocking=False):
            yield {"kind": "error",
                   "text": "A turn is already running, here or at the desk. "
                           "Wait for it, or press Stop."}
            return
        try:
            self._cancel.clear()

            if commandkit.is_command(text):
                outcome = self.window.run_command(text)
                if outcome.handled:
                    self.event.emit({"kind": "command", "text": text,
                                     "result": outcome.message})
                    yield {"kind": "notice", "text": outcome.message}
                    self.state_changed.emit()
                    return
                text = outcome.send_text or text

            self.window.history.append({"role": "user", "content": text})
            self.event.emit({"kind": "user", "text": text})
            self.window._persist()

            agent = self.window.build_agent()
            if agent is None:
                yield {"kind": "error",
                       "text": "No model is ready. Start one on the desktop, "
                               "or sign in to a provider."}
                return

            for event in agent.run(self.window.history, self._approve,
                                   cancel=self._cancel):
                payload = {
                    "kind": event.kind, "text": event.text,
                    "tool_name": event.tool_name, "tool_summary": event.tool_summary,
                    "tool_id": event.tool_id,
                }
                self.event.emit(payload)
                yield payload

            self.window._persist()
        finally:
            self.approvals.release_all(allowed=False)
            self._turn.release()
            self.state_changed.emit()

    def _approve(self, name: str, summary: str, arguments: dict) -> bool:
        """Block until a human answers, from either client."""
        call_id = f"{name}:{len(self.approvals.outstanding())}:{id(arguments)}"
        gate = self.approvals.create(call_id, name, summary, arguments)
        # Ask both: the desktop opens a dialog, the browser gets it inline.
        self.approval.emit(call_id, name, summary, arguments)
        while not gate.wait(timeout=0.5):
            if self._cancel.is_set():
                self.approvals.resolve(call_id, False)
                break
        return self.approvals.result(call_id)

    def resolve_approval(self, call_id: str, allowed: bool) -> bool:
        return self.approvals.resolve(call_id, allowed)

    def apply_setting(self, key: str, value: Any) -> dict[str, Any]:
        """Change a setting from the browser, applied on the GUI thread."""
        self.window.apply_remote_setting(key, value)
        return self.snapshot()

    def cancel(self) -> None:
        self._cancel.set()
        self.approvals.release_all(allowed=False)
        window = self.window
        if getattr(window, "_agent_worker", None) is not None:
            window._agent_worker.cancel()
