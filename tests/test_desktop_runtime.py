"""Platform behavior needed by the frozen desktop application."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hugmunn.core import devtools, server
from hugmunn.core.tools import ToolError


def test_frozen_python_tool_uses_an_external_interpreter(monkeypatch, tmp_path):
    executable = sys.executable
    monkeypatch.setattr(devtools, "sys", SimpleNamespace(frozen=True, executable="/frozen/Hugmunn"))
    monkeypatch.setenv("HUGMUNN_PYTHON", executable)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    result = devtools.python_exec(str(tmp_path), "import os; print(6 * 7); assert 'LD_LIBRARY_PATH' not in os.environ")
    assert "42" in result
    assert "exit code: 0" in result
    assert not list(tmp_path.iterdir())


def test_frozen_python_tool_reports_a_missing_interpreter(monkeypatch, tmp_path):
    monkeypatch.setattr(devtools, "sys", SimpleNamespace(frozen=True, executable="/frozen/Hugmunn"))
    monkeypatch.delenv("HUGMUNN_PYTHON", raising=False)
    monkeypatch.setattr(devtools.shutil, "which", lambda name: None)
    with pytest.raises(ToolError, match="HUGMUNN_PYTHON"):
        devtools.python_exec(str(tmp_path), "print(42)")
    assert not list(tmp_path.iterdir())


def test_windows_launches_runtime_without_a_shell_script(monkeypatch):
    monkeypatch.setattr(server, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(server.LlamaClient, "is_ready", lambda self: False)
    manager = server.ServerManager()
    command = ["llama-server.exe", "--model", "weights.gguf"]
    monkeypatch.setattr(manager, "_direct_command", lambda spec: command)
    spawn = Mock()
    monkeypatch.setattr(manager, "_spawn", spawn)
    manager.start(SimpleNamespace(base_url="http://127.0.0.1:9999"), extra_args=["--ctx-size", "4096"])
    assert spawn.call_args.args[0] == command + ["--ctx-size", "4096"]


def test_windows_stops_a_native_server_process(monkeypatch):
    monkeypatch.setattr(server, "sys", SimpleNamespace(platform="win32"))
    process = Mock()
    process.poll.return_value = None
    manager = server.ServerManager()
    manager._proc = process
    manager.stop()
    process.terminate.assert_called_once()
    process.wait.assert_called_once()
    assert manager._proc is None


def test_server_reader_drains_a_real_process_and_bounds_history(tmp_path):
    import subprocess
    import threading

    process = subprocess.Popen(
        [sys.executable, "-c", "for i in range(5000): print('log', i)"],
        stdout=subprocess.PIPE, text=True,
    )
    tail = []
    reader = threading.Thread(target=server.ServerManager._read_log, args=(process.stdout, tail))
    reader.start()
    try:
        assert process.wait(timeout=10) == 0
        reader.join(timeout=5)
        assert not reader.is_alive()
        assert len(tail) == 200
        assert tail[-1] == "log 4999"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
