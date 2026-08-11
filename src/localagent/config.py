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
from dataclasses import asdict, dataclass, field
from pathlib import Path

MODELS_ROOT = Path(os.environ.get("LOCALAGENT_MODELS_ROOT", Path.home() / ".claude" / "models"))
CONFIG_DIR = Path(os.environ.get("LOCALAGENT_CONFIG_DIR", Path.home() / ".config" / "localagent"))
CONFIG_FILE = CONFIG_DIR / "settings.json"

# Where the user actually put each model, keyed by model key. Populated from
# Settings on load. Kept module-level because ModelSpec is frozen and its
# availability check is called from many places that have no Settings handle.
_MODEL_PATHS: dict[str, Path] = {}


def set_model_path(key: str, path: str | Path | None) -> None:
    """Record where a model's primary weight file lives. None clears it."""
    if path is None:
        _MODEL_PATHS.pop(key, None)
    else:
        _MODEL_PATHS[key] = Path(path).expanduser()


def model_path_override(key: str) -> Path | None:
    return _MODEL_PATHS.get(key)


def all_model_paths() -> dict[str, str]:
    return {k: str(v) for k, v in _MODEL_PATHS.items()}


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


def identify(path: str | Path) -> "ModelSpec | None":
    """Which registered model a weight file belongs to, by filename.

    The app opens its download dialog for whichever model is *selected*, and
    on a fresh install that is the smallest one -- not necessarily the one the
    user has weights for. Pointing that dialog at a file for a different model
    used to be reported as a missing shard, which is both wrong and impossible
    to act on. Recognising the file is what makes the answer "that is the
    uncensored 27B, shall I record it there" instead.
    """
    name = Path(path).name
    for spec in REGISTRY:
        if any(Path(f).name == name for f in spec.files):
            return spec
    return None


def scan_for_weights(folder: str | Path, max_depth: int = 3) -> dict[str, Path]:
    """Every registered model whose complete weights are under ``folder``.

    Bounded rather than a full ``rglob``: this runs against directories that
    can hold hundreds of gigabytes across network mounts, and walking one of
    those to the leaves would hang the dialog. Three levels covers the layout
    the download scripts produce (``gguf/<model>/<quant>/shard.gguf``).

    Returned keyed by model, so a folder holding several is one answer rather
    than one question per file.
    """
    root = Path(folder).expanduser()
    found: dict[str, Path] = {}
    if not root.is_dir():
        return found
    patterns = ["*.gguf"] + ["/".join(["*"] * n) + "/*.gguf"
                             for n in range(1, max_depth + 1)]
    for pattern in patterns:
        for entry in sorted(root.glob(pattern)):
            spec = identify(entry)
            if spec is None or spec.key in found:
                continue
            if _weights_complete(entry):
                found[spec.key] = entry
    return found


# A llama-server the user pointed at, remembered across restarts. Module-level
# for the same reason as _MODEL_PATHS: readiness is asked from many places
# that have no Settings handle.
_RUNTIME_PATH: Path | None = None


def set_runtime(path: str | Path | None) -> None:
    """Record a llama-server binary. None clears it."""
    global _RUNTIME_PATH
    _RUNTIME_PATH = Path(path).expanduser() if path else None


def runtime_override() -> Path | None:
    return _RUNTIME_PATH


#: Places a llama-server plausibly lives when it was not built into the models
#: repo. Ollama ships its own llama runner, and conda/pip packages land in the
#: environment's bin -- both are already on this machine when a user thinks
#: they have "no llama.cpp".
_RUNTIME_CANDIDATES = (
    "~/.local/bin/llama-server",
    "/usr/local/bin/llama-server",
    "/opt/homebrew/bin/llama-server",
    "~/llama.cpp/build/bin/llama-server",
    "~/src/llama.cpp/build/bin/llama-server",
)


def find_runtime() -> Path | None:
    """A ``llama-server`` binary this machine can actually execute.

    Checked in the order a user would expect to win: what they pointed at,
    the environment, the build under ``bin/``, PATH, then a short list of
    conventional locations. ``bin/`` is a symlink into ``src/llama.cpp/build/``
    and both are gitignored, so a fresh clone of the models repo has the
    scripts and the weights and no binary at all -- which is exactly the state
    a second machine starts in.
    """
    def usable(candidate) -> Path | None:
        path = Path(candidate).expanduser()
        return path if path.is_file() and os.access(path, os.X_OK) else None

    if _RUNTIME_PATH is not None and (found := usable(_RUNTIME_PATH)):
        return found
    env = os.environ.get("LLAMA_SERVER", "").strip()
    if env and (found := usable(env)):
        return found
    if found := usable(MODELS_ROOT / "bin" / "llama-server"):
        return found
    # What localagent built for itself. Ahead of PATH because a build made
    # for this machine's GPU beats whatever generic binary happens to be
    # installed system-wide.
    data_dir = os.environ.get(
        "LOCALAGENT_DATA_DIR", str(Path.home() / ".local" / "share" / "localagent"))
    if found := usable(Path(data_dir) / "bin" / "llama-server"):
        return found
    if which := shutil.which("llama-server"):
        return Path(which)
    for candidate in _RUNTIME_CANDIDATES:
        if found := usable(candidate):
            return found
    return None


@dataclass(frozen=True)
class Readiness:
    """Why a model can or cannot start, in the three parts that fail apart."""

    weights: bool
    script: bool
    runtime: bool
    path: Path = Path()

    @property
    def can_launch(self) -> bool:
        """Weights, plus some way to serve them.

        The script is not required: with a binary on hand the server can be
        invoked directly, which is what makes a machine that has weights but
        not the scripts repo usable instead of stuck.
        """
        return self.weights and (self.script or self.runtime)

    def explain(self) -> str:
        """One line naming the missing piece and the fix for it.

        Never "not downloaded" when the weights are sitting there -- that
        sends the user off to re-fetch 87 GB they already have.
        """
        if not self.weights:
            return "not downloaded"
        if not self.script and not self.runtime:
            return ("weights found, but no launch script and no llama-server "
                    "binary - build llama.cpp or set LLAMA_SERVER")
        if not self.script:
            return "weights found; no launch script, so default settings are used"
        if not self.runtime:
            return ("weights and script found, but no llama-server binary - "
                    "build llama.cpp or set LLAMA_SERVER")
        return "ready"


@dataclass(frozen=True)
class ModelSpec:
    """One launchable model, identified by the script that starts its server."""

    key: str
    label: str
    script: str
    port: int
    blurb: str
    ram_gb: int = 0  # extra system RAM the model needs beyond VRAM; 0 = GPU-resident
    # Kept as an escape hatch for a model that genuinely cannot emit structured
    # tool calls. Every model currently shipped can, including both abliterated
    # ones — measured 3/3 structured calls with nothing leaking as plain text,
    # so do not set this False without testing that specific build first.
    tools_reliable: bool = True
    # Where the weights come from, for the in-app downloader. Mirrors
    # scripts/download.sh; sharded quants list every shard because a
    # partial set loads with an opaque llama.cpp error.
    repo: str = ""
    files: tuple[str, ...] = ()
    download_gb: float = 0.0

    # Tuning that has to survive when there is no launch script to read it
    # from. Not a duplicate of the script for its own sake: without these the
    # direct-launch fallback produced a model that was correct and unusable.
    #
    # `reasoning` is the one that bites hardest. Qwen3.6 thinks by default, so
    # a server started without "off" spends hundreds of tokens on a chain of
    # thought before the first visible word. At 37 tok/s that is most of a
    # minute of apparent silence, which reads as "extremely slow" even though
    # throughput is exactly right.
    reasoning: str = "off"          # "off" | "auto"
    #: Expert layers to keep in system RAM. 0 means the model is dense or
    #: fits entirely on the GPU. A MoE launched without this on a 24 GB card
    #: does not fail -- it thrashes.
    n_cpu_moe: int = 0
    ctx_size: int = 16384

    @property
    def script_path(self) -> Path:
        return MODELS_ROOT / "scripts" / self.script

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def launch_arguments(self) -> list[str]:
        """The flags a direct launch needs, when there is no script.

        Deliberately the small set that changes whether the model is usable,
        not an attempt to reproduce the script. Sampling is left to
        llama.cpp's defaults; reasoning mode, KV quantization and expert
        placement are not, because each of those turns a working model into
        an apparently broken one.
        """
        args = ["--fit", "on", "--ctx-size", str(self.ctx_size),
                "--flash-attn", "on",
                # Halves the KV cache. At 16K context that is gigabytes, and
                # on a card the model already nearly fills it is the
                # difference between fitting and spilling.
                "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
                "--jinja", "--threads", "16",
                "--reasoning", self.reasoning]
        if self.reasoning == "auto":
            # Without this the chain of thought is not separated out and
            # lands in the visible answer.
            args += ["--reasoning-format", "deepseek"]
        if self.n_cpu_moe:
            args += ["--n-gpu-layers", "999", "--n-cpu-moe", str(self.n_cpu_moe)]
        return args

    @property
    def context_tokens(self) -> int:
        """The ``--ctx-size`` the launch script asks for.

        Read from the script rather than hardcoded, so the two cannot drift.
        The UI needs it to warn when the skill set plus tool schemas would
        leave no room for the conversation — with everything enabled the
        preamble is ~14.7K, which overflows a 16K model immediately and
        surfaces as an opaque HTTP 400 from llama-server.
        """
        try:
            text = self.script_path.read_text(encoding="utf-8")
        except OSError:
            return 0
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("--ctx-size"):
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    return int(parts[1])
        return 0

    @property
    def script_model_path(self) -> Path:
        """The file the launch script hardcodes, absolute.

        Authoritative for where a download must end up: the script is what
        actually loads the weights, so anything else is a guess that can drift.
        """
        try:
            text = self.script_path.read_text(encoding="utf-8")
        except OSError:
            return Path()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("--model"):
                part = line.split(maxsplit=1)[1].strip().rstrip("\\").strip().strip('"').strip("'")
                return Path(part.replace("$BASE", str(MODELS_ROOT)))
        return Path()

    @property
    def model_path(self) -> Path:
        """Where the weights actually are.

        A user-chosen location wins over the script's default. The override is
        applied by passing ``--model`` as an extra argument at launch — the
        scripts forward ``"$@"`` and llama.cpp takes the last occurrence — so
        the script itself is never rewritten and keeps its tuning.
        """
        return model_path_override(self.key) or self.script_model_path

    def expected_files(self) -> dict[str, Path]:
        """Map each repo file to the absolute path the script needs it at.

        Shards live beside the first file under llama.cpp's convention, so the
        directory comes from the script and the names from the repo listing.
        Reconstructing the directory from the repo path instead would drop the
        per-model folder the launch scripts use and silently misplace every
        sharded model.
        """
        first = self.model_path
        # Path() is PosixPath('.'), which is truthy — a bare falsiness check
        # here silently produced relative targets and would have written the
        # weights into the current working directory.
        if not first.is_absolute() or not self.files:
            return {}
        folder = first.parent
        return {name: folder / Path(name).name for name in self.files}

    def missing_shards(self, primary: Path) -> list[Path]:
        """Which sibling shards are absent beside ``primary``.

        Used when a user points at weights they already have: a single shard
        selected out of a set loads with an opaque llama.cpp error, so the
        gap is worth reporting before it becomes a failed launch.
        """
        primary = Path(primary).expanduser()
        return [
            primary.parent / Path(name).name
            for name in self.files
            if not (primary.parent / Path(name).name).is_file()
        ]

    def has_weights(self) -> bool:
        """True when the weights are on disk, and every shard beside them.

        Large quants ship as ``…-00001-of-00003.gguf`` sets. Checking only the
        first shard reports a half-downloaded model as ready and llama.cpp then
        fails at load with an unhelpful error, so every shard is verified.

        This is what "downloaded" means, and it is deliberately separate from
        being launchable — see :meth:`readiness`.
        """
        return _weights_complete(self.model_path)

    def readiness(self) -> "Readiness":
        """What is present, what is missing, and what to do about it.

        Three things have to line up before a model runs, and they fail
        independently: the weights, the launch script, and a ``llama-server``
        binary. Collapsing them into one boolean is what made a machine with
        87 GB of correct weights report the model as *not downloaded* and
        offer to fetch it again — the launch scripts live in a separate repo,
        so a second machine routinely has one without the other.
        """
        return Readiness(
            weights=self.has_weights(),
            script=self.script_path.is_file(),
            runtime=find_runtime() is not None,
            path=self.model_path,
        )

    def is_available(self) -> bool:
        """True when this model can actually be started."""
        return self.readiness().can_launch

    # Retained: external callers and older settings code used this name.
    def _model_arg_exists(self) -> bool:
        return self.has_weights()


# Mirrors ~/.claude/models/scripts/. Ports match the --port in each script.
REGISTRY: tuple[ModelSpec, ...] = (
    ModelSpec(
        key="write",
        reasoning="off",
        ctx_size=16384,
        label="Qwen3.6-27B · writing",
        script="write.sh",
        port=8080,
        blurb="Dense 27B at Q5. Thinking off. ~37 tok/s, fully on GPU.",
        repo="unsloth/Qwen3.6-27B-GGUF",
        files=("Qwen3.6-27B-UD-Q5_K_XL.gguf",),
        download_gb=20.0,
    ),
    ModelSpec(
        key="code",
        reasoning="auto",
        ctx_size=32768,
        label="Qwen3.6-27B · coding",
        script="code.sh",
        port=8081,
        blurb="Dense 27B at Q4, long context. Thinking auto. ~40 tok/s.",
        repo="unsloth/Qwen3.6-27B-GGUF",
        files=("Qwen3.6-27B-UD-Q4_K_XL.gguf",),
        download_gb=17.6,
    ),
    ModelSpec(
        key="code-glm",
        reasoning="auto",
        ctx_size=32768,
        label="GLM-4.7-Flash · coding",
        script="code-glm.sh",
        port=8084,
        blurb="MoE, 4 of 64 experts active. Fastest here at ~63 tok/s. MIT.",
        repo="unsloth/GLM-4.7-Flash-GGUF",
        files=("GLM-4.7-Flash-UD-Q6_K_XL.gguf",),
        download_gb=26.2,
    ),
    ModelSpec(
        key="code-heavy",
        reasoning="auto",
        ctx_size=32768,
        n_cpu_moe=999,
        label="Qwen3-Coder-Next 80B · coding",
        script="code-heavy.sh",
        port=8082,
        blurb="80B-A3B at Q4. Experts stream from RAM.",
        ram_gb=55,
        repo="unsloth/Qwen3-Coder-Next-GGUF",
        files=("Qwen3-Coder-Next-UD-Q4_K_XL.gguf",),
        download_gb=49.6,
    ),
    ModelSpec(
        key="code-q6",
        reasoning="auto",
        ctx_size=32768,
        n_cpu_moe=999,
        label="Qwen3-Coder-Next 80B · coding (Q6)",
        script="code-q6.sh",
        port=8085,
        blurb="Same model at Q6. Best local coding quality.",
        ram_gb=70,
        repo="unsloth/Qwen3-Coder-Next-GGUF",
        files=("UD-Q6_K/Qwen3-Coder-Next-UD-Q6_K-00001-of-00003.gguf", "UD-Q6_K/Qwen3-Coder-Next-UD-Q6_K-00002-of-00003.gguf", "UD-Q6_K/Qwen3-Coder-Next-UD-Q6_K-00003-of-00003.gguf"),
        download_gb=65.8,
    ),
    ModelSpec(
        key="write-big",
        reasoning="off",
        ctx_size=16384,
        n_cpu_moe=999,
        label="Qwen3.5-122B · writing (flagship)",
        script="write-big.sh",
        port=8083,
        blurb="122B-A10B at Q5. Best prose. ~6-8 tok/s.",
        ram_gb=95,
        repo="unsloth/Qwen3.5-122B-A10B-GGUF",
        files=("UD-Q5_K_XL/Qwen3.5-122B-A10B-UD-Q5_K_XL-00001-of-00003.gguf", "UD-Q5_K_XL/Qwen3.5-122B-A10B-UD-Q5_K_XL-00002-of-00003.gguf", "UD-Q5_K_XL/Qwen3.5-122B-A10B-UD-Q5_K_XL-00003-of-00003.gguf"),
        download_gb=91.9,
    ),
    ModelSpec(
        key="agentic",
        reasoning="auto",
        ctx_size=32768,
        n_cpu_moe=999,
        label="MiniMax-M2.7 230B · agentic",
        script="agentic.sh",
        port=8086,
        blurb="Largest model that fits. 230B-A10B at Q3.",
        ram_gb=105,
        repo="unsloth/MiniMax-M2.7-GGUF",
        files=("UD-Q3_K_XL/MiniMax-M2.7-UD-Q3_K_XL-00001-of-00004.gguf", "UD-Q3_K_XL/MiniMax-M2.7-UD-Q3_K_XL-00002-of-00004.gguf", "UD-Q3_K_XL/MiniMax-M2.7-UD-Q3_K_XL-00003-of-00004.gguf", "UD-Q3_K_XL/MiniMax-M2.7-UD-Q3_K_XL-00004-of-00004.gguf"),
        download_gb=101.9,
    ),
    ModelSpec(
        key="uncensored",
        reasoning="off",
        ctx_size=16384,
        label="Qwen3.6-27B · uncensored",
        script="uncensored.sh",
        port=8087,
        blurb="Same 27B base, refusals ablated. 36.7 tok/s, all GPU. "
              "Tool calling verified intact.",
        repo="llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-GGUF",
        files=("Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf",),
        download_gb=19.7,
    ),
    ModelSpec(
        key="uncensored-big",
        reasoning="off",
        ctx_size=16384,
        n_cpu_moe=999,
        label="Qwen3.5-122B · uncensored (flagship)",
        script="uncensored-big.sh",
        port=8088,
        blurb="Most capable uncensored model that fits. 13.2 tok/s, and 5x "
              "faster prompts than the stock 122B. Tool calling verified.",
        ram_gb=90,
        repo="mradermacher/Qwen3.5-122B-A10B-abliterated-i1-GGUF",
        files=("Qwen3.5-122B-A10B-abliterated.i1-Q5_K_M.gguf",),
        download_gb=87.0,
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
    # User-authored tools are never on by default — a human reads the file and
    # switches it on. See core/plugins.py for why.
    enabled_plugins: list[str] = field(default_factory=list)
    system_prompt: str = (
        "You are a capable coding and writing assistant running locally on the "
        "user's machine. Be direct and concise. When you use a tool, use its "
        "result rather than guessing. Prefer showing code over describing it."
    )
    max_tool_iterations: int = 12
    # Absolute path to each model's primary weight file, when the user has
    # put it somewhere other than the launch script's default.
    model_paths: dict[str, str] = field(default_factory=dict)
    # A llama-server binary the user pointed at, when it is not on PATH and
    # not built into the models repo.
    llama_server: str = ""
    effort_level: int = 2      # core.effort.Effort
    autonomy_level: int = 2    # core.autonomy.Autonomy
    custom_base_url: str = ""
    # "local" | "anthropic" | "openai" — the first level of the model picker.
    provider: str = "local"
    # Last model chosen within each cloud provider, so switching provider and
    # back does not reset to whatever happens to be first in the list.
    cloud_models: dict[str, str] = field(default_factory=dict)
    # One of ui.theme.THEMES, or "system".
    theme: str = "dark"
    # API keys are deliberately NOT here. See core/credentials.py: this file
    # is the one a user might copy between machines or paste into a bug report.

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        settings = cls(**{k: v for k, v in data.items() if k in known})
        for key, path in (settings.model_paths or {}).items():
            set_model_path(key, path)
        set_runtime(settings.llama_server or None)
        return settings

    def save(self) -> None:
        self.model_paths = all_model_paths()
        self.llama_server = str(runtime_override() or "")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)  # atomic; a crash mid-write can't truncate settings
