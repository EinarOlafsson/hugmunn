"""Model lifecycle tests use real subprocesses without allocating GPU memory."""
import os
import shlex
import subprocess
import sys
import threading
import time

import pytest
from hugmunn import config
from hugmunn.core.gpu import GpuTaskRunner
from hugmunn.core.server import ServerManager, ServerError
from hugmunn.core.tools import ToolError

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process-group assertions')


def command(code):
    return shlex.join([sys.executable, '-c', code])


@pytest.fixture
def model(monkeypatch, tmp_path):
    manager = ServerManager()
    spec = config.REGISTRY[0]
    argv = [sys.executable, '-c', 'import time; time.sleep(120)']
    starts = []
    def spawn(cmd, model, cwd, report, timeout, client):
        starts.append(list(cmd))
        manager._proc = subprocess.Popen(cmd, start_new_session=True)
        manager._spec = model
        manager._launch = (list(cmd), model, cwd)
    monkeypatch.setattr(manager, '_spawn', spawn)
    spawn(argv, spec, tmp_path, None, 1, None)
    yield manager, starts, tmp_path
    manager.stop()


def test_model_exits_before_task_and_exact_command_reloads(model):
    manager, starts, root = model
    original = manager._proc
    runner = GpuTaskRunner(manager)
    code = f'import os; assert not os.path.exists("/proc/{original.pid}"); print("GPU work")'
    result = runner.run(str(root), command(code))
    assert 'exit code: 0' in result and 'GPU work' in result
    assert original.poll() is not None
    assert manager.is_running
    assert len(starts) == 2 and starts[0] == starts[1]


@pytest.mark.parametrize('code,timeout,expected', [
    ('import sys; sys.exit(7)', 10, 'exit code: 7'),
    ('import time; time.sleep(60)', 1, 'timed out'),
])
def test_failure_and_timeout_still_reload(model, code, timeout, expected):
    manager, starts, root = model
    result = GpuTaskRunner(manager).run(str(root), command(code), timeout)
    assert expected in result
    assert manager.is_running and len(starts) == 2


def test_cancel_reaps_task_then_restores_and_releases_lease(model):
    manager, starts, root = model
    runner = GpuTaskRunner(manager)
    output = []
    thread = threading.Thread(target=lambda: output.append(runner.run(
        str(root), command('import time; time.sleep(60)'))))
    thread.start()
    deadline = time.monotonic() + 5
    while runner.status != 'Running GPU task…' and time.monotonic() < deadline:
        time.sleep(.01)
    runner.cancel()
    thread.join(5)
    assert not thread.is_alive()
    assert 'cancelled' in output[0]
    assert not runner.busy and manager.is_running


def test_adopted_server_is_never_killed(tmp_path):
    manager = ServerManager()
    manager._adopted = True
    with pytest.raises(ServerError, match='externally managed'):
        GpuTaskRunner(manager).run(str(tmp_path), command('print("must not run")'))
    assert manager._adopted


def test_reload_failure_is_reported_and_lock_released(model, monkeypatch):
    manager, starts, root = model
    def fail(*args):
        raise OSError('weights unavailable')
    monkeypatch.setattr(manager, '_spawn', fail)
    runner = GpuTaskRunner(manager)
    with pytest.raises(ServerError, match='reload failed'):
        runner.run(str(root), command('print("done")'))
    assert not runner.busy and not manager.gpu_reserved


def test_invalid_task_does_not_unload(model):
    manager, starts, root = model
    proc = manager._proc
    with pytest.raises(ToolError):
        GpuTaskRunner(manager).run(str(root / 'missing'), 'anything')
    assert manager._proc is proc and manager.is_running


def test_gpu_command_uses_shell_approval_policy():
    from hugmunn.core.autonomy import Autonomy, decide
    assert decide('run_gpu_task', {'command': 'python task.py'}, '/tmp', Autonomy.WORKSPACE).needs_approval


def test_background_child_is_reaped_before_model_reload(model, monkeypatch):
    from hugmunn.core.gpu import _group_alive
    manager, starts, root = model
    marker = root / 'child-pid'
    spawn = manager._spawn
    def reload(*args):
        pid = int(marker.read_text())
        stat = __import__('pathlib').Path(f'/proc/{pid}/stat')
        assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] in ('Z', 'X')
        spawn(*args)
    monkeypatch.setattr(manager, '_spawn', reload)
    code = ('import subprocess,sys,pathlib; '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
            f'pathlib.Path({str(marker)!r}).write_text(str(p.pid))')
    assert 'exit code: 0' in GpuTaskRunner(manager).run(str(root), command(code))
    assert manager.is_running


def test_unconfirmed_cleanup_leaves_model_unloaded(model, monkeypatch):
    from hugmunn.core import gpu
    from hugmunn.core.server import GpuCleanupError
    manager, starts, root = model
    terminate = gpu._terminate
    def failure(proc):
        terminate(proc)
        raise OSError('cannot confirm cleanup')
    monkeypatch.setattr(gpu, '_terminate', failure)
    with pytest.raises(GpuCleanupError, match='left unloaded'):
        gpu.GpuTaskRunner(manager).run(str(root), command('print("done")'))
    assert not manager.is_running and len(starts) == 1
