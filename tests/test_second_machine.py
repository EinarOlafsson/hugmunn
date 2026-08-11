"""The state a second machine starts in: weights, and not much else.

The weights are 481 GB and get copied by hand or downloaded directly. The
launch scripts are a separate private repo. The llama.cpp build is gitignored
inside that repo. So the three arrive independently, and collapsing them into
one "is it available" boolean reported 87 GB of correct weights as *not
downloaded* and offered to fetch them again.
"""

from __future__ import annotations

import importlib
import os
import stat

import pytest


@pytest.fixture()
def fake_root(tmp_path, monkeypatch):
    """A models root we control, with nothing in it yet."""
    monkeypatch.setenv("LOCALAGENT_MODELS_ROOT", str(tmp_path))
    monkeypatch.setenv("LOCALAGENT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("LLAMA_SERVER", raising=False)
    from localagent import config

    importlib.reload(config)
    (tmp_path / "gguf").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "bin").mkdir()
    return config, tmp_path


def write_script(root, name="uncensored.sh", weights="model.gguf"):
    path = root / "scripts" / name
    path.write_text(
        "#!/usr/bin/env bash\n"
        'BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
        'exec "$LLAMA_SERVER" \\\n'
        f'  --model "$BASE/gguf/{weights}" \\\n'
        "  --ctx-size 16384 \\\n"
        '  "$@"\n',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def write_runtime(root):
    binary = root / "bin" / "llama-server"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


def spec_for(config, root, weights="model.gguf"):
    return config.ModelSpec(
        key="uncensored", label="Test model", script="uncensored.sh",
        port=8087, blurb="", files=(weights,), download_gb=19.7,
    )


# ------------------------------------------------------- the reported bug


def test_weights_present_but_no_script_is_not_reported_as_not_downloaded(fake_root):
    """The exact failure: 19.7 GB on disk, and the app says fetch it again."""
    config, root = fake_root
    (root / "gguf" / "model.gguf").write_bytes(b"weights")
    write_runtime(root)
    spec = spec_for(config, root)
    config.set_model_path("uncensored", root / "gguf" / "model.gguf")

    ready = spec.readiness()
    assert ready.weights is True
    assert ready.script is False
    assert "not downloaded" not in ready.explain()
    # And it can still run, because a binary is enough.
    assert ready.can_launch


def test_weights_without_a_script_or_a_binary_says_what_to_install(fake_root):
    config, root = fake_root
    (root / "gguf" / "model.gguf").write_bytes(b"weights")
    config.set_model_path("uncensored", root / "gguf" / "model.gguf")
    ready = spec_for(config, root).readiness()

    assert not ready.can_launch
    assert "llama.cpp" in ready.explain() or "LLAMA_SERVER" in ready.explain()
    assert "not downloaded" not in ready.explain()


def test_no_weights_still_reads_as_not_downloaded(fake_root):
    """The one case where that wording is right."""
    config, root = fake_root
    write_script(root)
    write_runtime(root)
    assert spec_for(config, root).readiness().explain() == "not downloaded"


def test_everything_present_is_ready(fake_root):
    config, root = fake_root
    (root / "gguf" / "model.gguf").write_bytes(b"weights")
    write_script(root)
    write_runtime(root)
    spec = spec_for(config, root)
    assert spec.readiness().explain() == "ready"
    assert spec.is_available()


# ------------------------------------------------------ finding the binary


def test_the_runtime_is_found_under_bin(fake_root):
    config, root = fake_root
    assert config.find_runtime() is None
    binary = write_runtime(root)
    importlib.reload(config)
    assert config.find_runtime() == binary


def test_an_explicit_llama_server_wins(fake_root, monkeypatch, tmp_path):
    config, root = fake_root
    write_runtime(root)
    elsewhere = tmp_path / "custom-llama-server"
    elsewhere.write_text("#!/bin/sh\n", encoding="utf-8")
    elsewhere.chmod(elsewhere.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("LLAMA_SERVER", str(elsewhere))
    importlib.reload(config)
    assert config.find_runtime() == elsewhere


def test_a_non_executable_binary_is_not_offered(fake_root):
    config, root = fake_root
    (root / "bin" / "llama-server").write_text("#!/bin/sh\n", encoding="utf-8")
    importlib.reload(config)
    found = config.find_runtime()
    # Either nothing, or something genuinely on PATH — never the unexecutable one.
    assert found is None or found != root / "bin" / "llama-server"


# ------------------------------------------- launching without the script


def test_a_direct_command_loads_the_right_weights(fake_root):
    config, root = fake_root
    (root / "gguf" / "model.gguf").write_bytes(b"weights")
    binary = write_runtime(root)
    config.set_model_path("uncensored", root / "gguf" / "model.gguf")

    from localagent.core.server import ServerManager

    command = ServerManager()._direct_command(spec_for(config, root))
    assert command is not None
    assert command[0] == str(binary)
    assert "--model" in command
    assert command[command.index("--model") + 1].endswith("model.gguf")
    # --fit sizes the GPU offload automatically; guessing --n-gpu-layers is
    # what OOMs a 24 GB card on a 26 GB model.
    assert "--fit" in command


def test_no_direct_command_without_weights(fake_root):
    config, root = fake_root
    write_runtime(root)
    from localagent.core.server import ServerManager

    assert ServerManager()._direct_command(spec_for(config, root)) is None


def test_a_missing_script_and_no_binary_raises_something_actionable(fake_root):
    config, root = fake_root
    (root / "gguf" / "model.gguf").write_bytes(b"weights")
    config.set_model_path("uncensored", root / "gguf" / "model.gguf")

    from localagent.core.server import ServerError, ServerManager

    with pytest.raises(ServerError) as caught:
        ServerManager().start(spec_for(config, root), timeout=0.1)
    assert "LLAMA_SERVER" in str(caught.value)


# ------------------------------------------------ the scripts relocate


def test_the_shipped_scripts_do_not_hardcode_a_home_directory():
    """Every script must derive BASE from its own location.

    They are a git repo pulled onto other machines and other user accounts.
    A literal /home/<someone>/.claude/models resolves to nothing there, and
    every model reports itself missing — which is what happened.
    """
    # Reload rather than importing the cached module: the fixtures above
    # reload config against a temporary root, and that leaks.
    from localagent import config

    importlib.reload(config)
    scripts = sorted((config.MODELS_ROOT / "scripts").glob("*.sh"))
    if not scripts:
        pytest.skip("the models repo is not checked out here")
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        assert "BASE=/home/" not in text, f"{script.name} hardcodes a home directory"
        assert 'BASE="$(cd' in text, f"{script.name} does not derive BASE"
