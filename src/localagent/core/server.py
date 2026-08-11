"""Lifecycle for a llama-server process launched from one of the model scripts."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable

from ..config import ModelSpec
from .client import LlamaClient


class ServerError(RuntimeError):
    pass


class ServerManager:
    """Starts, monitors, and stops one llama-server at a time.

    An already-running server on the target port is adopted rather than
    duplicated — two llama-servers on one GPU would OOM each other.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._spec: ModelSpec | None = None
        self._adopted = False
        self.log_tail: list[str] = []

    @property
    def spec(self) -> ModelSpec | None:
        return self._spec

    @property
    def is_running(self) -> bool:
        if self._adopted and self._spec is not None:
            return LlamaClient(self._spec.base_url).is_ready()
        return self._proc is not None and self._proc.poll() is None

    def start(
        self,
        spec: ModelSpec,
        on_progress: Callable[[str], None] | None = None,
        timeout: float = 900.0,
    ) -> None:
        """Launch ``spec`` and block until its /health endpoint reports ready.

        Large MoE models read 50-100 GB off disk before serving, so the default
        timeout is generous. ``on_progress`` receives human-readable status.
        """
        def report(msg: str) -> None:
            if on_progress:
                on_progress(msg)

        client = LlamaClient(spec.base_url)
        if client.is_ready():
            report(f"Adopted running server on port {spec.port}")
            self._spec, self._adopted, self._proc = spec, True, None
            return

        script = spec.script_path
        if not script.is_file():
            # A second machine routinely has the weights and not the scripts:
            # they live in a separate repo. Serve them directly rather than
            # refusing, and say that the per-model tuning is not in play.
            command = self._direct_command(spec)
            if command is None:
                raise ServerError(
                    f"launch script not found: {script}\n\n"
                    f"and no llama-server binary was found either. Build "
                    f"llama.cpp, or set LLAMA_SERVER=/path/to/llama-server."
                )
            report(f"no launch script; running llama-server directly")
            self._spawn(command, spec, script.parent if script.parent.is_dir()
                        else Path.cwd(), report, timeout, client)
            return
        if not os.access(script, os.X_OK):
            # A missing +x bit is trivially fixable and not worth failing on —
            # three shipped scripts were mode 644 for weeks and simply could
            # not be launched. Repair it, and only give up if that fails too.
            try:
                script.chmod(script.stat().st_mode | 0o111)
                report(f"made {script.name} executable")
            except OSError as exc:
                raise ServerError(
                    f"launch script is not executable and could not be fixed: "
                    f"{script} ({exc}). Run: chmod +x {script}"
                ) from exc

        self.stop()
        self.log_tail.clear()
        report(f"Starting {spec.label}…")

        # start_new_session so we can signal the whole process group; the scripts
        # exec llama-server, but a shell may sit in between.
        # A user-chosen weight location is applied as an extra --model argument
        # rather than by editing the script: the scripts forward "$@" and
        # llama.cpp takes the last occurrence, so per-model tuning is preserved.
        command = [str(script)]
        from ..config import model_path_override

        override = model_path_override(spec.key)
        if override is not None:
            command += ["--model", str(override)]
            report(f"using weights at {override}")

        self._spawn(command, spec, script.parent, report, timeout, client)

    def _direct_command(self, spec: ModelSpec) -> list[str] | None:
        """Serve ``spec`` without its launch script, or ``None`` if we cannot.

        Deliberately conservative. The scripts carry tuning that matters --
        ``--n-cpu-moe`` splits, sampling, reasoning mode -- and guessing at it
        would be worse than the script. What is here is only what is needed to
        load the weights and answer: ``--fit on`` sizes the GPU offload
        automatically, which is the one decision that otherwise OOMs.
        """
        from ..config import find_runtime

        runtime = find_runtime()
        if runtime is None or not spec.has_weights():
            return None
        return [
            str(runtime),
            "--model", str(spec.model_path),
            "--alias", spec.key,
            "--fit", "on",
            "--ctx-size", "16384",
            "--flash-attn", "on",
            "--jinja",
            "--threads", "16",
            "--host", "127.0.0.1", "--port", str(spec.port),
        ]

    def _spawn(self, command, spec, cwd, report, timeout, client) -> None:
        self._proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
            cwd=str(cwd),
        )
        self._spec, self._adopted = spec, False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise ServerError(
                    f"server exited with code {self._proc.returncode}.\n"
                    + "\n".join(self.log_tail[-15:])
                )
            self._drain_log()
            if client.is_ready():
                report(f"{spec.label} ready on port {spec.port}")
                return
            time.sleep(1.0)

        self.stop()
        raise ServerError(f"server did not become ready within {timeout:.0f}s")

    def _drain_log(self) -> None:
        """Non-blocking read of whatever the server has written so far."""
        if self._proc is None or self._proc.stdout is None:
            return
        import select

        while select.select([self._proc.stdout], [], [], 0)[0]:
            line = self._proc.stdout.readline()
            if not line:
                break
            self.log_tail.append(line.rstrip())
            del self.log_tail[:-200]

    def stop(self) -> None:
        """Terminate a server we started. Adopted servers are left alone."""
        if self._adopted:
            self._spec, self._adopted = None, False
            return
        proc = self._proc
        self._proc, self._spec = None, None
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
