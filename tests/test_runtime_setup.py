"""Getting a llama-server onto a machine that has the weights and nothing else.

The weights copy across fine -- 87 GB on a USB disk is only slow. The binary
does not: it is built for one machine's CPU and GPU, and it is gitignored
inside the models repo for that reason. So the second machine reliably ends up
with correct weights and no way to run them, and "build llama.cpp or set
LLAMA_SERVER" is accurate without being any help.
"""

from __future__ import annotations

import importlib
import os
import stat

import pytest


@pytest.fixture()
def clean_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAGENT_MODELS_ROOT", str(tmp_path / "models"))
    monkeypatch.setenv("LOCALAGENT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("LLAMA_SERVER", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    from localagent import config

    importlib.reload(config)
    return config, tmp_path


def make_binary(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho 'version: test'\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_nothing_is_found_on_a_bare_machine(clean_config):
    config, _ = clean_config
    assert config.find_runtime() is None


def test_a_chosen_binary_is_used(clean_config, tmp_path):
    config, _ = clean_config
    binary = make_binary(tmp_path / "somewhere" / "llama-server")
    config.set_runtime(binary)
    assert config.find_runtime() == binary


def test_a_chosen_binary_survives_a_restart(clean_config, tmp_path):
    """Otherwise it has to be exported into the environment before launching."""
    config, _ = clean_config
    binary = make_binary(tmp_path / "somewhere" / "llama-server")
    config.set_runtime(binary)
    config.Settings().save()

    config.set_runtime(None)
    assert config.find_runtime() is None
    config.Settings.load()
    assert config.find_runtime() == binary


def test_the_environment_still_works(clean_config, tmp_path, monkeypatch):
    config, _ = clean_config
    binary = make_binary(tmp_path / "env" / "llama-server")
    monkeypatch.setenv("LLAMA_SERVER", str(binary))
    assert config.find_runtime() == binary


def test_a_chosen_binary_outranks_the_environment(clean_config, tmp_path, monkeypatch):
    config, _ = clean_config
    chosen = make_binary(tmp_path / "chosen" / "llama-server")
    from_env = make_binary(tmp_path / "env" / "llama-server")
    monkeypatch.setenv("LLAMA_SERVER", str(from_env))
    config.set_runtime(chosen)
    assert config.find_runtime() == chosen


def test_a_stale_chosen_path_does_not_shadow_a_working_one(clean_config, tmp_path):
    """A binary deleted since it was chosen must not make the app claim none."""
    config, root = clean_config
    config.set_runtime(tmp_path / "deleted" / "llama-server")
    real = make_binary(root / "models" / "bin" / "llama-server")
    assert config.find_runtime() == real


def test_a_non_executable_file_is_not_accepted(clean_config, tmp_path):
    config, _ = clean_config
    path = tmp_path / "notexec" / "llama-server"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    config.set_runtime(path)
    assert config.find_runtime() is None


def test_the_search_list_covers_the_usual_places():
    from localagent.ui.runtime_dialog import SEARCH_ROOTS

    joined = " ".join(SEARCH_ROOTS)
    for expected in ("~/.local/bin", "/usr/local/bin", "llama.cpp/build/bin"):
        assert expected in joined


def test_the_dialog_still_shows_manual_steps_as_a_fallback():
    from localagent.ui.runtime_dialog import BUILD_STEPS

    assert "git clone" in BUILD_STEPS and "cmake" in BUILD_STEPS
    # An architecture is not optional: without it the build compiles for every
    # GPU ever shipped and takes the better part of an hour.
    assert "CMAKE_CUDA_ARCHITECTURES" in BUILD_STEPS


def test_describe_answers_whether_the_gpu_is_usable(tmp_path):
    """Not the version string: that reports the compiler, not the backend."""
    from localagent.ui.runtime_dialog import describe

    binary = make_binary(tmp_path / "llama-server")   # prints only a version
    assert "CPU-only" in describe(binary)


def test_describe_does_not_raise_on_a_binary_that_will_not_run(tmp_path):
    from localagent.ui.runtime_dialog import describe

    missing = tmp_path / "not-there"
    assert describe(missing) == "could not be run"


def test_the_dialog_builds_and_validates(qt_app, clean_config, tmp_path):
    from localagent.ui.runtime_dialog import RuntimeDialog

    config, _ = clean_config
    dialog = RuntimeDialog()
    try:
        assert not dialog.save.isEnabled()          # nothing found, nothing typed
        binary = make_binary(tmp_path / "bin" / "llama-server")
        dialog.path_edit.setText(str(binary))
        assert dialog.save.isEnabled()
        dialog._accept()
        assert config.find_runtime() == binary
    finally:
        dialog.close()


def test_the_dialog_rejects_a_path_that_is_not_executable(qt_app, clean_config, tmp_path):
    from localagent.ui.runtime_dialog import RuntimeDialog

    path = tmp_path / "llama-server"
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    dialog = RuntimeDialog()
    try:
        dialog.path_edit.setText(str(path))
        assert not dialog.save.isEnabled()
        assert "chmod" in dialog.status.text()
    finally:
        dialog.close()


def test_weights_in_the_repo_would_not_be_committed():
    """87 GB landed in the working tree on another machine; git add -A would
    have tried to stage it."""
    from pathlib import Path

    import localagent

    repo = Path(localagent.__file__).resolve().parents[2]
    assert "*.gguf" in (repo / ".gitignore").read_text(encoding="utf-8")


# ------------------------------------------------- building it in the app
#
# The user's point: this should happen automatically on any machine, not by
# finding and running a shell script in a repo that may not be cloned there.


def test_preflight_reads_the_machine_without_changing_it():
    from localagent.core import setup_llama

    checks = setup_llama.preflight()
    assert isinstance(checks.can_build, bool)
    # Whatever it concludes, it must be able to explain it.
    assert checks.summary()


def test_preflight_names_what_is_missing(monkeypatch):
    from localagent.core import setup_llama

    monkeypatch.setattr("shutil.which", lambda name: None)
    checks = setup_llama.preflight()
    assert not checks.can_build
    assert {"git", "cmake", "a C++ compiler"} == set(checks.missing)
    assert "Cannot build here" in checks.summary()


def test_a_gpu_without_the_cuda_toolkit_is_called_out(monkeypatch):
    """Silently building CPU-only on a machine with a 3090 wastes the GPU."""
    from localagent.core import setup_llama

    checks = setup_llama.Preflight(
        git="/g", cmake="/c", compiler="/cc", nvcc="",
        nvidia_smi="/n", gpu_name="NVIDIA GeForce RTX 3090", compute_cap="86")
    assert checks.can_build and not checks.can_build_cuda
    assert "nvcc" in checks.summary()
    assert "CUDA toolkit" in checks.summary()


def test_the_cuda_build_targets_the_card_that_is_present():
    from localagent.core import setup_llama

    checks = setup_llama.Preflight(
        git="/g", cmake="/c", compiler="/cc", nvcc="/nvcc",
        nvidia_smi="/n", gpu_name="RTX 3090", compute_cap="86")
    assert checks.can_build_cuda
    assert "86" in checks.summary()


def test_install_goes_to_our_own_directory_not_the_models_repo():
    """The machine that needs a build is the one where that repo is absent."""
    from localagent.core import setup_llama

    assert "localagent" in str(setup_llama.INSTALL_ROOT)
    assert ".claude/models" not in str(setup_llama.INSTALL_ROOT)


def test_the_build_never_takes_more_than_sixteen_cores():
    """This machine runs other people's jobs."""
    from localagent.core import setup_llama

    assert setup_llama.DEFAULT_JOBS <= 16


def test_a_build_on_a_machine_that_cannot_build_fails_with_advice(monkeypatch):
    from localagent.core import setup_llama

    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(setup_llama.SetupError) as caught:
        setup_llama.build(lambda line: None)
    assert "missing" in str(caught.value)


def test_what_the_installer_produces_is_what_find_runtime_looks_for(clean_config,
                                                                    tmp_path,
                                                                    monkeypatch):
    """Building and then not finding it would be a quiet, baffling failure."""
    config, _ = clean_config
    monkeypatch.setenv("LOCALAGENT_DATA_DIR", str(tmp_path / "data"))
    importlib.reload(config)
    from localagent.core import setup_llama

    importlib.reload(setup_llama)
    binary = make_binary(setup_llama.BIN_DIR / "llama-server")
    assert config.find_runtime() == binary
    assert setup_llama.installed_binary() == binary


def test_a_purpose_built_binary_outranks_a_generic_one_on_path(clean_config,
                                                               tmp_path,
                                                               monkeypatch):
    """Ours is built for this GPU; whatever is on PATH probably is not."""
    config, _ = clean_config
    monkeypatch.setenv("LOCALAGENT_DATA_DIR", str(tmp_path / "data"))
    importlib.reload(config)
    from localagent.core import setup_llama

    importlib.reload(setup_llama)
    ours = make_binary(setup_llama.BIN_DIR / "llama-server")
    generic = make_binary(tmp_path / "usr" / "llama-server")
    monkeypatch.setattr("shutil.which", lambda name: str(generic))
    assert config.find_runtime() == ours


def test_a_prebuilt_asset_is_named_for_this_platform():
    from localagent.core import setup_llama

    name = setup_llama.prebuilt_asset_name()
    assert name is None or any(
        tag in name for tag in ("ubuntu", "macos", "win"))


def test_the_setup_worker_reports_failure_rather_than_raising(qt_app, monkeypatch):
    from localagent.core import setup_llama
    from localagent.ui.workers import SetupWorker

    monkeypatch.setattr(shutil := __import__("shutil"), "which", lambda name: None)
    worker = SetupWorker("build")
    failures = []
    worker.failed.connect(failures.append)
    worker.run()          # synchronously, no event loop needed
    assert failures and "missing" in failures[0]


def test_the_app_offers_setup_on_launch_when_that_is_the_blocker(qt_app, tmp_path,
                                                                 monkeypatch):
    """Weights present, no binary: say so rather than presenting a dead list."""
    monkeypatch.setenv("LOCALAGENT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("LOCALAGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAGENT_MODELS_ROOT", str(tmp_path / "models"))
    from PyQt6.QtWidgets import QMessageBox

    from localagent import config as cfg

    importlib.reload(cfg)
    from localagent.ui import main_window as mw

    importlib.reload(mw)
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, spec: None)
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, p: None)
    monkeypatch.setattr(cfg, "find_runtime", lambda: None)

    weights = tmp_path / "Qwen3.6-27B-UD-Q5_K_XL.gguf"
    weights.write_bytes(b"x")
    cfg.set_model_path("write", weights)

    asked, opened = [], []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a)
                                     or QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(mw.MainWindow, "_setup_runtime",
                        lambda self: opened.append(True))

    window = mw.MainWindow()
    try:
        window._offer_runtime_setup()
        assert asked, "should offer when weights exist and no binary does"
        assert opened, "yes should open the setup dialog"
    finally:
        window.server.stop()
        window.close()


def test_no_offer_when_nothing_is_downloaded(qt_app, tmp_path, monkeypatch):
    """A fresh install has no weights; the binary is not what is missing."""
    monkeypatch.setenv("LOCALAGENT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("LOCALAGENT_MODELS_ROOT", str(tmp_path / "models"))
    from PyQt6.QtWidgets import QMessageBox

    from localagent import config as cfg

    importlib.reload(cfg)
    from localagent.ui import main_window as mw

    importlib.reload(mw)
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, spec: None)
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, p: None)
    monkeypatch.setattr(cfg, "find_runtime", lambda: None)

    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a)
                                     or QMessageBox.StandardButton.No))
    window = mw.MainWindow()
    try:
        window._offer_runtime_setup()
        assert not asked
    finally:
        window.server.stop()
        window.close()


# --------------------------------------------- CPU or GPU, and saying which
#
# "It works but it is extremely slow" has two different causes that look
# identical from the outside: a CPU-only binary, and a GPU binary whose model
# did not fit in VRAM. llama.cpp reports both and nothing was reading it.


def test_a_cuda_build_is_recognised_as_gpu_capable():
    from localagent.core.setup_llama import RuntimeInfo
    from pathlib import Path

    info = RuntimeInfo(Path("/x"), "version: 1",
                       ("CUDA0: NVIDIA GeForce RTX 3090 (24123 MiB, 57 MiB free)",))
    assert info.has_gpu
    assert "RTX 3090" in info.summary()


def test_a_cpu_only_build_says_so_and_says_what_to_do():
    from localagent.core.setup_llama import RuntimeInfo
    from pathlib import Path

    info = RuntimeInfo(Path("/x"), "version: 1", ())
    assert not info.has_gpu
    assert "CPU-only" in info.summary()
    assert "CUDA toolkit" in info.summary()


def test_a_version_string_alone_does_not_imply_a_gpu():
    """The build log saying 'built with GNU 13.3.0' tells you nothing."""
    from localagent.core.setup_llama import RuntimeInfo
    from pathlib import Path

    assert not RuntimeInfo(Path("/x"), "built with GNU 13.3.0", ()).has_gpu


def test_offload_is_read_out_of_the_startup_log():
    from localagent.core.setup_llama import offload_from_log

    assert offload_from_log(
        ["load_tensors: offloaded 49/49 layers to GPU"]) == "all 49 layers on GPU"
    assert "entirely on the CPU" in offload_from_log(
        ["load_tensors: offloaded 0/49 layers to GPU"])
    assert offload_from_log(
        ["load_tensors: offloaded 20/49 layers to GPU"]
    ) == "20 of 49 layers on GPU, the rest on CPU"


def test_the_last_offload_line_wins():
    """A server that reloads logs twice; the current load is the answer."""
    from localagent.core.setup_llama import offload_from_log

    assert offload_from_log([
        "load_tensors: offloaded 0/49 layers to GPU",
        "load_tensors: offloaded 49/49 layers to GPU",
    ]) == "all 49 layers on GPU"


def test_a_log_that_says_nothing_gives_nothing():
    from localagent.core.setup_llama import offload_from_log

    assert offload_from_log(["starting", "listening on 127.0.0.1:8080"]) == ""


def test_the_status_line_reports_where_the_model_ran(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAGENT_CONFIG_DIR", str(tmp_path))
    from localagent import config as cfg

    importlib.reload(cfg)
    from localagent.ui import main_window as mw

    importlib.reload(mw)
    monkeypatch.setattr(mw.MainWindow, "_offer_download", lambda self, s: None)
    monkeypatch.setattr(mw.MainWindow, "_sign_in", lambda self, p: None)
    monkeypatch.setattr(mw.MainWindow, "_offer_runtime_setup", lambda self: None)

    window = mw.MainWindow()
    try:
        window.server.log_tail = ["load_tensors: offloaded 49/49 layers to GPU"]
        window._on_server_ready("qwen3.6-27b")
        assert "all 49 layers on GPU" in window.server_status.text()
    finally:
        window.server.stop()
        window.close()
