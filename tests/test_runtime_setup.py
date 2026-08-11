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


def test_the_build_instructions_are_runnable_commands():
    from localagent.ui.runtime_dialog import BUILD_STEPS, BUILD_STEPS_CPU

    for steps in (BUILD_STEPS, BUILD_STEPS_CPU):
        assert "git clone" in steps and "cmake" in steps
        assert "llama-server" in steps
    # The CUDA build needs an architecture or it compiles for everything and
    # takes an hour; 86 is the RTX 30xx this was built against.
    assert "CMAKE_CUDA_ARCHITECTURES" in BUILD_STEPS
    assert "GGML_CUDA" not in BUILD_STEPS_CPU


def test_describe_reports_what_a_binary_says(tmp_path):
    from localagent.ui.runtime_dialog import describe

    binary = make_binary(tmp_path / "llama-server")
    assert "version" in describe(binary)


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
