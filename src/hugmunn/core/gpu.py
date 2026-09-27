"""Exclusive, cancellable GPU jobs with guaranteed model restoration attempts."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .tools import Tool, ToolError
from .server import GpuCleanupError


def _group_alive(pgid):
    if sys.platform.startswith("linux"):
        # Zombies have released their GPU allocations and cannot run again.
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = path.read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def _terminate(proc):
    """Reap the task's process tree before the model may reclaim VRAM."""
    if sys.platform == "win32":
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15, check=False)
    else:
        # Includes background children after their parent shell has exited.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait(timeout=15)
    deadline = time.monotonic() + 10
    while sys.platform != "win32" and _group_alive(proc.pid):
        if time.monotonic() >= deadline:
            raise GpuCleanupError("GPU task processes are still exiting. Model left unloaded.")
        time.sleep(.05)


class GpuTaskRunner:
    """One task at a time. Commands are trusted local code, not sandboxed."""

    def __init__(self, server):
        self.server = server
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.status = "Idle"
        self.output = ""

    @property
    def busy(self):
        return self._lock.locked()

    def cancel(self):
        self._cancel.set()

    def run(self, workdir: str, command: str, timeout: int = 3600,
            on_progress=None) -> str:
        if sys.platform == "win32":
            raise ToolError("GPU task handoff currently requires Linux or macOS process groups.")
        root = Path(workdir).expanduser().resolve()
        if not root.is_dir() or not isinstance(command, str) or not command.strip():
            raise ToolError("Provide a command and an existing working directory.")
        if isinstance(timeout, bool) or not 1 <= int(timeout) <= 86400:
            raise ToolError("GPU task timeout must be between 1 and 86400 seconds.")
        if not self._lock.acquire(blocking=False):
            raise ToolError("A GPU task is already running.")
        self._cancel.clear()
        self.output = ""

        def report(message):
            self.status = message
            if on_progress:
                on_progress(message)

        try:
            with self.server.gpu_session(report):
                if self._cancel.is_set():
                    return "GPU task cancelled before launch."
                report("Running GPU task…")
                # Bounded output, drained while the command runs to avoid pipe deadlocks.
                chunks = deque(maxlen=32)
                proc = subprocess.Popen(command, shell=True, cwd=str(root),
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        start_new_session=True)

                def drain():
                    try:
                        while chunk := proc.stdout.read1(4096):
                            chunks.append(chunk)
                            self.output = b"".join(chunks).decode("utf-8", "replace")
                    finally:
                        proc.stdout.close()

                reader = threading.Thread(target=drain, daemon=True)
                reader.start()
                deadline = time.monotonic() + int(timeout)
                reason = ""
                try:
                    while proc.poll() is None:
                        if self._cancel.wait(0.1):
                            reason = "cancelled"
                            break
                        if time.monotonic() >= deadline:
                            reason = "timed out"
                            break
                finally:
                    try:
                        _terminate(proc)
                    except Exception as exc:
                        raise GpuCleanupError(f"GPU task cleanup failed; model left unloaded: {exc}") from exc
                    reader.join(timeout=5)
                result = f"GPU task {reason or ('exit code: ' + str(proc.returncode))}\n{self.output}"
            self.output = result
            report("Idle")
            return result
        except Exception as exc:
            report(f"Failed: {exc}")
            raise
        finally:
            self._lock.release()

    def tool(self):
        return Tool(
            name="run_gpu_task",
            description="Run a foreground GPU command after unloading Hugmunn's local model. "
                        "The model reloads before the next response. Do not use background jobs "
                        "or detach child processes. Subject to the shell-command approval policy.",
            parameters={"type": "object", "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "1–86400 seconds; default 3600."},
            }, "required": ["command"]},
            run=self.run, requires_approval=True,
        )
