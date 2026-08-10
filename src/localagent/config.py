"""Model registry and persisted settings.

The registry deliberately points at the launch scripts under ``~/.claude/models``
rather than re-deriving llama-server arguments. Those scripts carry per-model
tuning (reasoning mode, sampling, ``--n-cpu-moe`` splits, ``--fit``) that would
rot immediately if duplicated here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

MODELS_ROOT = Path(os.environ.get("LOCALAGENT_MODELS_ROOT", Path.home() / ".claude" / "models"))
CONFIG_DIR = Path(os.environ.get("LOCALAGENT_CONFIG_DIR", Path.home() / ".config" / "localagent"))
CONFIG_FILE = CONFIG_DIR / "settings.json"


_SHARD_RE = re.compile(r"^(?P<stem>.+)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$")


def _weights_complete(first: Path) -> bool:
    """True when ``first`` exists and, if it is a shard, all its siblings do too."""
    if not first.is_file():
        return False
    match = _SHARD_RE.match(first.name)
    if match is None:
        return True
    stem, total = match["stem"], int(match["total"])
    return all(
        (first.parent / f"{stem}-{n:05d}-of-{total:05d}.gguf").is_file()
        for n in range(1, total + 1)
    )


@dataclass(frozen=True)
class ModelSpec:
    """One launchable model, identified by the script that starts its server."""

    key: str
    label: str
    script: str
    port: int
    blurb: str
    ram_gb: int = 0  # extra system RAM the model needs beyond VRAM; 0 = GPU-resident

    @property
    def script_path(self) -> Path:
        return MODELS_ROOT / "scripts" / self.script

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def is_available(self) -> bool:
        """True when the launch script exists and its weights have been downloaded."""
        if not self.script_path.is_file():
            return False
        return bool(self._model_arg_exists())

    def _model_arg_exists(self) -> bool:
        """Parse the --model line out of the script and check the weights are complete.

        Large quants ship as ``…-00001-of-00003.gguf`` shard sets. Checking only
        the first shard reports a half-downloaded model as ready, and llama.cpp
        then fails at load with an unhelpful error — so every shard is verified.
        """
        try:
            text = self.script_path.read_text(encoding="utf-8")
        except OSError:
            return False
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("--model"):
                continue
            # --model "$BASE/gguf/foo.gguf" \
            part = line.split(maxsplit=1)[1].strip().rstrip("\\").strip().strip('"').strip("'")
            return _weights_complete(Path(part.replace("$BASE", str(MODELS_ROOT))))
        return False


# Mirrors ~/.claude/models/scripts/. Ports match the --port in each script.
REGISTRY: tuple[ModelSpec, ...] = (
    ModelSpec(
        key="write",
        label="Qwen3.6-27B · writing",
        script="write.sh",
        port=8080,
        blurb="Dense 27B at Q5. Thinking off. ~37 tok/s, fully on GPU.",
    ),
    ModelSpec(
        key="code",
        label="Qwen3.6-27B · coding",
        script="code.sh",
        port=8081,
        blurb="Dense 27B at Q4, long context. Thinking auto. ~40 tok/s.",
    ),
    ModelSpec(
        key="code-glm",
        label="GLM-4.7-Flash · coding",
        script="code-glm.sh",
        port=8084,
        blurb="MoE, 4 of 64 experts active. Fastest here at ~63 tok/s. MIT.",
    ),
    ModelSpec(
        key="code-heavy",
        label="Qwen3-Coder-Next 80B · coding",
        script="code-heavy.sh",
        port=8082,
        blurb="80B-A3B at Q4. Experts stream from RAM.",
        ram_gb=55,
    ),
    ModelSpec(
        key="code-q6",
        label="Qwen3-Coder-Next 80B · coding (Q6)",
        script="code-q6.sh",
        port=8085,
        blurb="Same model at Q6. Best local coding quality.",
        ram_gb=70,
    ),
    ModelSpec(
        key="write-big",
        label="Qwen3.5-122B · writing (flagship)",
        script="write-big.sh",
        port=8083,
        blurb="122B-A10B at Q5. Best prose. ~6-8 tok/s.",
        ram_gb=95,
    ),
    ModelSpec(
        key="agentic",
        label="MiniMax-M2.7 230B · agentic",
        script="agentic.sh",
        port=8086,
        blurb="Largest model that fits. 230B-A10B at Q3.",
        ram_gb=105,
    ),
    ModelSpec(
        key="uncensored",
        label="Qwen3.6-27B · uncensored",
        script="uncensored.sh",
        port=8087,
        blurb="Same 27B base, refusals ablated. Fast (~37 tok/s), all GPU. "
              "Tool calling is degraded — turn tools off for this one.",
    ),
    ModelSpec(
        key="uncensored-big",
        label="Qwen3.5-122B · uncensored (flagship)",
        script="uncensored-big.sh",
        port=8088,
        blurb="Most capable uncensored model that fits. ~6-8 tok/s. "
              "Tool calling is degraded — turn tools off for this one.",
        ram_gb=90,
    ),
)


def by_key(key: str) -> ModelSpec | None:
    return next((m for m in REGISTRY if m.key == key), None)


def available_models() -> list[ModelSpec]:
    return [m for m in REGISTRY if m.is_available()]


def free_ram_gb() -> int:
    """Available (not merely free) system RAM, which is what actually matters."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024 // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def free_vram_mb() -> int:
    """Free VRAM via nvidia-smi. 0 when unavailable, which callers treat as unknown."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0
    import subprocess

    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()
        return int(out[0]) if out else 0
    except (subprocess.SubprocessError, ValueError, IndexError):
        return 0


@dataclass
class Settings:
    model_key: str = "code-glm"
    workdir: str = str(Path.home())
    tools_enabled: bool = True
    auto_approve_reads: bool = True
    # None means "not chosen yet" — the UI substitutes the skills marked
    # default-on. An empty list is a real choice (everything off) and is
    # preserved, which is why this can't just default to [].
    enabled_skills: list[str] | None = None
    system_prompt: str = (
        "You are a capable coding and writing assistant running locally on the "
        "user's machine. Be direct and concise. When you use a tool, use its "
        "result rather than guessing. Prefer showing code over describing it."
    )
    max_tool_iterations: int = 12
    custom_base_url: str = ""

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)  # atomic; a crash mid-write can't truncate settings
