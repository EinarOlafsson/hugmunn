"""Writing a launch script the first time a model runs.

The direct-launch fallback worked and was the wrong shape: an argv assembled
in memory lasts as long as the process, so there is nothing to read and nowhere
to put a change. NCPUMOE is the clearest case — it is worth several tok/s on
the large MoE models and it is a knob with no handle if the command line is
invisible.
"""

from __future__ import annotations

import importlib
import os
import stat
import subprocess

import pytest


@pytest.fixture()
def root(tmp_path, monkeypatch):
    """A models tree with a binary and one model's weights, and no scripts."""
    monkeypatch.setenv("HUGMUNN_MODELS_ROOT", str(tmp_path))
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("LLAMA_SERVER", raising=False)
    (tmp_path / "gguf").mkdir()
    (tmp_path / "bin").mkdir()
    binary = tmp_path / "bin" / "llama-server"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "gguf" / "w.gguf").write_bytes(b"x")

    from hugmunn import config

    importlib.reload(config)
    from hugmunn.core import scripts

    importlib.reload(scripts)
    return config, scripts, tmp_path


def prepared(config, root, key="uncensored-big"):
    spec = config.by_key(key)
    config.set_model_path(key, root / "gguf" / "w.gguf")
    return spec


def test_a_script_is_written_on_first_launch(root):
    config, scripts, path = root
    spec = prepared(config, path)
    written = scripts.write(spec)
    assert written is not None and written.is_file()
    assert written == spec.script_path


def test_the_generated_script_is_valid_bash(root):
    config, scripts, path = root
    written = scripts.write(prepared(config, path))
    assert subprocess.run(["bash", "-n", str(written)]).returncode == 0


def test_the_generated_script_is_executable(root):
    """Three shipped scripts were once mode 644 and could never be launched."""
    config, scripts, path = root
    written = scripts.write(prepared(config, path))
    assert written.stat().st_mode & stat.S_IXUSR


def test_an_existing_script_is_never_overwritten(root):
    """It exists to hold tuning. Regenerating it would discard that."""
    config, scripts, path = root
    spec = prepared(config, path)
    spec.script_path.parent.mkdir(parents=True, exist_ok=True)
    spec.script_path.write_text("#!/bin/sh\n# my careful tuning\n", encoding="utf-8")

    returned = scripts.write(spec)
    assert returned == spec.script_path
    assert "my careful tuning" in spec.script_path.read_text()


def test_the_tuning_reaches_the_generated_script(root, monkeypatch):
    config, scripts, path = root
    monkeypatch.setattr(config, "_INFERENCE_BACKEND", "cuda")
    text = scripts.write(prepared(config, path)).read_text()
    assert "--reasoning off" in text
    assert "--cache-type-k q8_0" in text
    assert f"--threads {min(16, os.cpu_count() or 1)}" in text


def test_the_expert_split_is_a_variable_not_a_literal(root):
    """The one number worth tuning by hand needs somewhere to be tuned."""
    config, scripts, path = root
    text = scripts.write(prepared(config, path)).read_text()
    assert '--n-cpu-moe "$NCPUMOE"' in text
    assert "NCPUMOE=" in text                # and the comment says how
    assert "nvidia-smi" in text


def test_a_dense_model_gets_no_expert_knob(root):
    config, scripts, path = root
    text = scripts.write(prepared(config, path, "uncensored")).read_text()
    assert "--n-cpu-moe" not in text


def test_the_script_does_not_hardcode_a_home_directory(root):
    """The mistake the shipped scripts made, not repeated by the generator."""
    config, scripts, path = root
    text = scripts.write(prepared(config, path)).read_text()
    assert 'BASE="$(cd' in text
    assert "BASE=/home/" not in text


def test_arguments_are_forwarded_so_overrides_still_work(root):
    """hugmunn appends --model and --ctx-size; llama.cpp takes the last."""
    config, scripts, path = root
    assert '"$@"' in scripts.write(prepared(config, path)).read_text()


def test_the_port_matches_the_spec(root):
    config, scripts, path = root
    spec = prepared(config, path)
    assert f"--port {spec.port}" in scripts.write(spec).read_text()


def test_nothing_is_written_without_weights(root):
    config, scripts, path = root
    assert scripts.write(config.by_key("uncensored-big")) is None


def test_nothing_is_written_without_a_binary(root, monkeypatch):
    config, scripts, path = root
    spec = prepared(config, path)
    (path / "bin" / "llama-server").unlink()
    monkeypatch.setattr("shutil.which", lambda name: None)
    importlib.reload(config)
    importlib.reload(scripts)
    assert scripts.write(spec) is None


def test_a_read_only_location_is_reported_not_raised(root, monkeypatch):
    config, scripts, path = root
    spec = prepared(config, path)
    monkeypatch.setattr(
        scripts.Path, "write_text",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    said = []
    assert scripts.write(spec, said.append) is None
    assert any("could not write" in line for line in said)


def test_every_registered_model_generates_a_valid_script(root):
    """Not just the two the report was about."""
    config, scripts, path = root
    for spec in config.REGISTRY:
        config.set_model_path(spec.key, path / "gguf" / "w.gguf")
        written = scripts.write(spec)
        assert written is not None, spec.key
        assert subprocess.run(["bash", "-n", str(written)]).returncode == 0, spec.key
        text = written.read_text()
        assert f"--ctx-size {spec.ctx_size}" in text, spec.key
        assert f"--reasoning {spec.reasoning}" in text, spec.key


def test_the_server_writes_a_script_rather_than_launching_invisibly(root):
    config, scripts, path = root
    spec = prepared(config, path)
    from hugmunn.core.server import ServerError, ServerManager

    said = []
    try:
        ServerManager().start(spec, on_progress=said.append, timeout=0.1)
    except ServerError:
        pass          # the stub binary exits immediately; the script is the point
    assert spec.script_path.is_file(), "the first launch should leave a script behind"
    assert any("wrote" in line for line in said)
