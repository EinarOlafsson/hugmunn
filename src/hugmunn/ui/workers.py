"""QThread workers.

Network and subprocess work must stay off the GUI thread or the window freezes
while a 100 GB model loads. The one subtlety is tool approval: the agent loop
runs in the worker and has to *block* until a human answers a dialog that can
only be shown on the GUI thread. That round trip is a queued signal out plus a
``threading.Event`` back.
"""

from __future__ import annotations

import threading
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from ..config import ModelSpec
from ..core.agent import Agent, AgentEvent
from ..core.client import LlamaClient
from ..core.server import ServerError, ServerManager


class ServerWorker(QThread):
    """Starts a llama-server and reports when it is serving."""

    progress = pyqtSignal(str)
    ready = pyqtSignal(str)      # model name reported by the server
    failed = pyqtSignal(str)

    def __init__(self, manager: ServerManager, spec: ModelSpec, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._spec = spec

    def run(self) -> None:
        try:
            self._manager.start(self._spec, on_progress=self.progress.emit)
        except ServerError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.ready.emit(LlamaClient(self._spec.base_url).model_name())


class AgentWorker(QThread):
    """Runs one user turn, including any tool round-trips."""

    reasoning = pyqtSignal(str)
    content = pyqtSignal(str)
    tool_start = pyqtSignal(str, str, str)          # name, summary, call_id
    tool_result = pyqtSignal(str, str, str)         # name, summary, output
    tool_denied = pyqtSignal(str, str)              # name, summary
    approval_requested = pyqtSignal(str, str, dict)  # name, summary, arguments
    turn_finished = pyqtSignal(dict)                # llama.cpp timings
    failed = pyqtSignal(str)

    def __init__(self, agent: Agent, history: list[dict[str, Any]], parent=None) -> None:
        super().__init__(parent)
        self._agent = agent
        self._history = history
        self._cancel = threading.Event()
        self._approval_gate = threading.Event()
        self._approval_result = False

    # ---- called from the GUI thread ----

    def cancel(self) -> None:
        self._cancel.set()
        # Release a pending approval so the worker can observe the cancel.
        self._approval_result = False
        self._approval_gate.set()

    def provide_approval(self, allowed: bool) -> None:
        self._approval_result = allowed
        self._approval_gate.set()

    # ---- worker thread ----

    def _approve(self, name: str, summary: str, arguments: dict[str, Any]) -> bool:
        if self._cancel.is_set():
            return False
        self._approval_gate.clear()
        self.approval_requested.emit(name, summary, arguments)
        self._approval_gate.wait()  # GUI thread releases this via provide_approval
        return self._approval_result

    def run(self) -> None:
        try:
            for event in self._agent.run(self._history, self._approve, cancel=self._cancel):
                self._dispatch(event)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def _dispatch(self, event: AgentEvent) -> None:
        match event.kind:
            case "reasoning":
                self.reasoning.emit(event.text)
            case "content":
                self.content.emit(event.text)
            case "tool_start":
                self.tool_start.emit(event.tool_name, event.tool_summary, event.tool_id)
            case "tool_result":
                self.tool_result.emit(event.tool_name, event.tool_summary, event.text)
            case "denied":
                self.tool_denied.emit(event.tool_name, event.tool_summary)
            case "error":
                self.failed.emit(event.text)
            case "done":
                self.turn_finished.emit(event.timings)


class CatalogueWorker(QThread):
    """Validates an API key by asking the provider what it can reach.

    Doubles as the sign-in check: a key that can list models is a key that
    works, and the list is needed anyway. Off the GUI thread because a
    request to an unreachable host sits there for the full timeout, and a
    frozen sign-in dialog reads as a crash.
    """

    ok = pyqtSignal(object)   # tuple[CloudModel, ...]
    failed = pyqtSignal(str)

    def __init__(self, provider, api_key: str, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._key = api_key

    def run(self) -> None:
        from ..core import providers

        try:
            self.ok.emit(providers.fetch_catalogue(self._provider, self._key))
        except providers.ProviderError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class SetupWorker(QThread):
    """Builds or downloads llama-server, streaming output as it goes.

    A build takes minutes. Off the GUI thread so the window stays alive, and
    line by line so the user can tell a long compile from a hang -- silence
    for four minutes is what makes people kill it.
    """

    line = pyqtSignal(str)
    ok = pyqtSignal(str)      # path to the binary
    failed = pyqtSignal(str)

    def __init__(self, mode: str = "build", parent=None) -> None:
        super().__init__(parent)
        self._mode = mode
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        from ..core import setup_llama

        action = (setup_llama.download_prebuilt if self._mode == "prebuilt"
                  else setup_llama.build)
        try:
            binary = action(self.line.emit, cancel=self._cancel)
        except setup_llama.SetupError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.ok.emit(str(binary))


class DownloadWorker(QThread):
    """Fetches a model's weights, reporting byte-level progress."""

    progress = pyqtSignal(object)   # core.downloads.Progress
    finished_ok = pyqtSignal(str)   # destination
    failed = pyqtSignal(str)

    def __init__(self, spec, destination, parent=None, targets=None) -> None:
        super().__init__(parent)
        self._spec = spec
        self._destination = destination
        self._targets = targets
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        from .. core import downloads

        try:
            downloads.download(
                self._spec.repo, list(self._spec.files), self._destination,
                on_progress=self.progress.emit, cancel=self._cancel,
                targets=self._targets,
            )
        except downloads.DownloadError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished_ok.emit(str(self._destination))
