"""What a model is launched with, when there is no script to read it from.

"All 49 layers on GPU" and "extremely slow" are not a contradiction. The
placement was right and the model was still unusable, because the direct-launch
fallback sent only enough flags to load the weights:

* no ``--reasoning off``, and Qwen3.6 thinks by default -- hundreds of tokens
  of chain of thought before the first visible word. At 37 tok/s that is most
  of a minute of silence, which is indistinguishable from a slow model.
* no ``--n-cpu-moe``, so a 122B mixture-of-experts had nowhere to put its
  experts but a 24 GB card.
* no KV quantization, which at 16K context is gigabytes of VRAM the model
  needed for itself.

The tuning lives on the spec now, and these check it survives to the command
line and stays in step with the shipped scripts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hugmunn import config
from hugmunn.core.server import ServerManager


def flags(key):
    """The flags a direct launch would send for this model."""
    return config.by_key(key).launch_arguments()


def value_of(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


# ------------------------------------------------------------ the symptom


@pytest.mark.parametrize("key", [s.key for s in config.REGISTRY])
def test_every_model_states_a_reasoning_mode(key):
    """Left unset, Qwen3.6 thinks before answering and looks broken."""
    assert value_of(flags(key), "--reasoning") in ("off", "auto")


@pytest.mark.parametrize("key", [s.key for s in config.REGISTRY])
def test_thinking_models_separate_the_chain_of_thought(key):
    """Without the format, reasoning is not split out and lands in the answer."""
    args = flags(key)
    if value_of(args, "--reasoning") == "auto":
        assert value_of(args, "--reasoning-format") == "deepseek"


@pytest.mark.parametrize("key", [s.key for s in config.REGISTRY])
def test_every_model_quantizes_its_kv_cache(key):
    """At 16K context an f16 cache is gigabytes the model needed itself."""
    args = flags(key)
    assert value_of(args, "--cache-type-k") == "q8_0"
    assert value_of(args, "--cache-type-v") == "q8_0"


@pytest.mark.parametrize("key", [s.key for s in config.REGISTRY if s.ram_gb])
def test_models_that_need_system_ram_place_their_experts(key):
    """A MoE with nowhere to put its experts thrashes rather than failing."""
    args = flags(key)
    assert value_of(args, "--n-cpu-moe") is not None, (
        f"{key} needs {config.by_key(key).ram_gb} GB of system RAM, which "
        f"means expert layers, which means --n-cpu-moe")


def test_no_model_takes_more_than_sixteen_threads():
    for spec in config.REGISTRY:
        assert value_of(spec.launch_arguments(), "--threads") == "16"


# ------------------------------------------- the fallback reaches the wire


def test_the_tuning_reaches_the_command_line():
    command = ServerManager()._direct_command(config.by_key("uncensored"))
    assert command is not None
    assert "--reasoning" in command and command[command.index("--reasoning") + 1] == "off"
    assert "--cache-type-k" in command


def test_the_flagship_moe_gets_its_expert_split():
    command = ServerManager()._direct_command(config.by_key("uncensored-big"))
    assert command is not None
    assert "--n-cpu-moe" in command
    assert "--n-gpu-layers" in command


def test_the_port_is_the_last_word_so_nothing_overrides_it():
    command = ServerManager()._direct_command(config.by_key("write"))
    assert command[-2] == "--port"


# --------------------------------------------- and stays in step with the
#                                                shipped launch scripts


def script_flag(path: Path, flag: str) -> str | None:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"{re.escape(flag)}\s+\"?([A-Za-z0-9_.$-]+)\"?", text)
    return match.group(1) if match else None


@pytest.mark.parametrize("spec", list(config.REGISTRY), ids=lambda s: s.key)
def test_the_spec_agrees_with_the_script(spec):
    """Two sources for the same tuning, so they are checked against each other.

    The script wins if they ever disagree -- it is what actually runs when it
    is present -- but a silent divergence means a machine without the scripts
    gets quietly different behaviour, which is the failure this whole file is
    about.
    """
    if not spec.script_path.is_file():
        pytest.skip("the models repo is not checked out here")
    from_script = script_flag(spec.script_path, "--reasoning")
    assert from_script == spec.reasoning, (
        f"{spec.key}: script says --reasoning {from_script}, "
        f"spec says {spec.reasoning}")

    has_moe = "--n-cpu-moe" in spec.script_path.read_text(encoding="utf-8")
    assert has_moe == bool(spec.n_cpu_moe), (
        f"{spec.key}: script {'has' if has_moe else 'has no'} --n-cpu-moe, "
        f"spec says n_cpu_moe={spec.n_cpu_moe}")


@pytest.mark.parametrize("spec", list(config.REGISTRY), ids=lambda s: s.key)
def test_the_context_size_agrees_with_the_script(spec):
    if not spec.script_path.is_file():
        pytest.skip("the models repo is not checked out here")
    assert spec.context_tokens == spec.ctx_size
