"""Lifecycle for a llama-server process launched from one of the model scripts."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from ..config import ModelSpec
from ..errors import HugmunnError
from .client import LlamaClient


class ServerError(HugmunnError):
    """A local model server could not be started or did not become ready."""


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
        self._log_thread: threading.Thread | None = None

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
        extra_args: list[str] | None = None,
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

        from ..config import inference_backend, backend_arguments

        if sys.platform == "win32" or inference_backend() != "auto":
            # Registry scripts use a POSIX shell; native Windows launches the
            # executable with the same model arguments instead.
            command = self._direct_command(spec)
            if command is None:
                raise ServerError("Select llama-server.exe and download the model weights first.")
            self.stop()
            self.log_tail.clear()
            self._spawn(command + list(extra_args or []), spec, Path.cwd(), report, timeout, client)
            return

        script = spec.script_path
        if not script.is_file():
            # A second machine routinely has the weights and not the scripts:
            # they live in a separate repo. Write one rather than launching
            # from an argv that exists only in memory -- a file can be read,
            # edited and tuned, and NCPUMOE is worth several tok/s on the
            # large MoE models with nowhere else to put it.
            from . import scripts

            written = scripts.write(spec, report)
            if written is None:
                command = self._direct_command(spec)
                if command is None:
                    raise ServerError(
                        f"launch script not found: {script}\n\n"
                        f"and no llama-server binary was found either. Build "
                        f"llama.cpp, or set LLAMA_SERVER=/path/to/llama-server."
                    )
                report("running llama-server directly")
                self._spawn(command + list(extra_args or []), spec, Path.cwd(),
                            report, timeout, client)
                return
            script = written
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
        command += backend_arguments()
        context = spec.context_arguments()
        if context:
            command += context
            report(f"context window {context[1]} tokens")
        # Appended last so llama.cpp's last-wins rule applies -- how the
        # tuning sweep overrides --n-cpu-moe without editing the script.
        if extra_args:
            command += list(extra_args)

        self._spawn(command, spec, script.parent, report, timeout, client)

    def _direct_command(self, spec: ModelSpec) -> list[str] | None:
        """Serve ``spec`` without its launch script, or ``None`` if we cannot.

        The flags come from the spec rather than being invented here, so the
        fallback and the script cannot drift. An earlier version sent only
        enough to load the weights, which produced a model that was correct
        and unusable: no ``--reasoning off`` meant Qwen3.6 spent most of a
        minute thinking before its first visible word, and no ``--n-cpu-moe``
        meant a 122B MoE thrashed against a 24 GB card.
        """
        from ..config import find_runtime

        runtime = find_runtime()
        if runtime is None or not spec.has_weights():
            return None
        return [
            str(runtime),
            "--model", str(spec.model_path),
            "--alias", spec.key,
            *spec.launch_arguments(),
            "--host", "127.0.0.1", "--port", str(spec.port),
        ]

    def _spawn(self, command, spec, cwd, report, timeout, client) -> None:
        """Launch a native process and wait for readiness while collecting logs."""
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
        self.log_tail = []
        self._log_thread = threading.Thread(
            target=self._read_log, args=(self._proc.stdout, self.log_tail), daemon=True,
        )
        self._log_thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self._log_thread.join(timeout=1)
                raise ServerError(
                    f"server exited with code {self._proc.returncode}.\n"
                    + "\n".join(self.log_tail[-15:])
                )
            if client.is_ready():
                report(f"{spec.label} ready on port {spec.port}")
                return
            time.sleep(1.0)

        self.stop()
        raise ServerError(f"server did not become ready within {timeout:.0f}s")

    @staticmethod
    def _read_log(stream, tail: list[str]) -> None:
        """Drain a pipe on all platforms, retaining only the last 200 lines.

        Windows select() cannot read subprocess pipes. A dedicated reader also
        prevents a running server from blocking when its output pipe fills.
        """
        if stream is None:
            return
        try:
            for line in stream:
                tail.append(line.rstrip())
                del tail[:-200]
        finally:
            stream.close()

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
            if sys.platform == "win32":
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                if sys.platform == "win32":
                    proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait(timeout=5)
        if self._log_thread is not None:
            self._log_thread.join(timeout=1)
