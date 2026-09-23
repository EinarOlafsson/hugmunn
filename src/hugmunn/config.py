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

def _env(name: str, default: Path) -> Path:
    """Read HUGMUNN_<name>, falling back to the old LOCALAGENT_<name>.

    The old names keep working: somebody with them in a shell profile or a
    systemd unit should not have their setup broken by a rename, and a
    variable that silently stops being read is worse than one that is
    deprecated loudly.
    """
    value = (os.environ.get(f"HUGMUNN_{name}")
             or os.environ.get(f"LOCALAGENT_{name}") or "")
    return Path(value) if value.strip() else default


def models_root() -> Path:
    return _env("MODELS_ROOT", Path.home() / ".claude" / "models")


def config_dir() -> Path:
    """Settings live here.

    Still ``~/.config/localagent`` when that exists and the new one does not,
    so a rename does not look like losing every saved conversation.
    """
    explicit = _env("CONFIG_DIR", Path())
    if str(explicit) != ".":
        return explicit
    new = Path.home() / ".config" / "hugmunn"
    old = Path.home() / ".config" / "localagent"
    if not new.exists() and old.exists():
        return old
    return new


def config_file() -> Path:
    return config_dir() / "settings.json"


def __getattr__(name: str):
    """Serve the path constants as live lookups (PEP 562).

    They used to be bound at import, which meant changing the environment
    afterwards required reloading this module -- and everything holding a
    ``from config import CONFIG_DIR`` still had the old one. The tests did
    reload, including the Qt modules, and reloading a module that defines
    QWidget subclasses makes *new* classes while old instances are still
    alive. That segfaulted in combination while every file passed alone,
    which is the most confusing shape a bug can take.

    Resolving on access removes the reason to reload anything.
    """
    if name == "MODELS_ROOT":
        return models_root()
    if name == "CONFIG_DIR":
        return config_dir()
    if name == "CONFIG_FILE":
        return config_file()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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


# Context window per model, when overridden. Module-level for the same reason
# as _MODEL_PATHS: read from places that have no Settings handle.
_CONTEXT_SIZES: dict[str, int] = {}


def set_context_size(key: str, size: int | None) -> None:
    """Override a model's context window. None or 0 restores its default."""
    if size:
        _CONTEXT_SIZES[key] = int(size)
    else:
        _CONTEXT_SIZES.pop(key, None)


def context_size_override(key: str) -> int:
    return _CONTEXT_SIZES.get(key, 0)


def all_context_sizes() -> dict[str, int]:
    return dict(_CONTEXT_SIZES)


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
    if found := usable(models_root() / "bin" / "llama-server"):
        return found
    # What hugmunn built for itself. Ahead of PATH because a build made
    # for this machine's GPU beats whatever generic binary happens to be
    # installed system-wide.
    data_dir = os.environ.get(
        "HUGMUNN_DATA_DIR", str(Path.home() / ".local" / "share" / "hugmunn"))
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
    #: How much of the base model's alignment is still in the weights.
    #: "vanilla"  — as the lab shipped it. Refuses what it was trained to.
    #: "tuned"    — a community tune or a standard abliteration. Steerable
    #:              by system prompt, still declines some things outright.
    #: "unlocked" — refusal directions removed, usually by Heretic. Will
    #:              discuss what a vanilla build will not. Not lawless: the
    #:              residue is in the weights and no build reaches zero.
    freedom: str = "vanilla"
    reasoning: str = "off"          # "off" | "auto"
    #: Expert layers to keep in system RAM. 0 means the model is dense or
    #: fits entirely on the GPU. A MoE launched without this on a 24 GB card
    #: does not fail -- it thrashes.
    n_cpu_moe: int = 0
    ctx_size: int = 16384

    @property
    def script_path(self) -> Path:
        return models_root() / "scripts" / self.script

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def effective_ctx_size(self) -> int:
        """The context window to actually ask for.

        A user override wins over the model's default. Applied at launch, so
        changing it needs a server restart -- llama.cpp allocates the KV cache
        once, at load.
        """
        return _CONTEXT_SIZES.get(self.key) or self.ctx_size

    def context_arguments(self) -> list[str]:
        """Extra args appended to a *script* launch to apply an override.

        The scripts hardcode a --ctx-size. Appending another one works because
        they forward "$@" and llama.cpp takes the last occurrence -- the same
        mechanism the weight-path override uses, and for the same reason: the
        script keeps its tuning and is never rewritten.
        """
        override = context_size_override(self.key)
        return ["--ctx-size", str(override)] if override else []

    def launch_arguments(self) -> list[str]:
        """The flags a direct launch needs, when there is no script.

        Deliberately the small set that changes whether the model is usable,
        not an attempt to reproduce the script. Sampling is left to
        llama.cpp's defaults; reasoning mode, KV quantization and expert
        placement are not, because each of those turns a working model into
        an apparently broken one.
        """
        args = ["--fit", "on", "--ctx-size", str(self.effective_ctx_size),
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
                return Path(part.replace("$BASE", str(models_root())))
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
        freedom="vanilla",
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
        freedom="vanilla",
        reasoning="auto",
        ctx_size=32768,
        label="Qwen3.6-27B · coding",
        script="code.sh",
        port=8081,
        blurb="Dense 27B at Q4, long context. ~40 tok/s.",
        repo="unsloth/Qwen3.6-27B-GGUF",
        files=("Qwen3.6-27B-UD-Q4_K_XL.gguf",),
        download_gb=17.6,
    ),
    ModelSpec(
        key="code-glm",
        freedom="vanilla",
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
        freedom="vanilla",
        reasoning="auto",
        ctx_size=32768,
        n_cpu_moe=999,
        label="Qwen3-Coder-Next 80B · coding",
        script="code-heavy.sh",
        port=8082,
        blurb="80B-A3B at Q4. Experts stream from RAM. ~23 tok/s.",
        ram_gb=55,
        repo="unsloth/Qwen3-Coder-Next-GGUF",
        files=("Qwen3-Coder-Next-UD-Q4_K_XL.gguf",),
        download_gb=49.6,
    ),
    ModelSpec(
        key="code-q6",
        freedom="vanilla",
        reasoning="auto",
        ctx_size=32768,
        n_cpu_moe=999,
        label="Qwen3-Coder-Next 80B · coding (Q6)",
        script="code-q6.sh",
        port=8085,
        blurb="Same 80B-A3B at Q6. Best stock coding quality here. ~11 tok/s.",
        ram_gb=70,
        repo="unsloth/Qwen3-Coder-Next-GGUF",
        files=("UD-Q6_K/Qwen3-Coder-Next-UD-Q6_K-00001-of-00003.gguf", "UD-Q6_K/Qwen3-Coder-Next-UD-Q6_K-00002-of-00003.gguf", "UD-Q6_K/Qwen3-Coder-Next-UD-Q6_K-00003-of-00003.gguf"),
        download_gb=65.8,
    ),
    ModelSpec(
        key="write-big",
        freedom="vanilla",
        reasoning="off",
        ctx_size=16384,
        n_cpu_moe=999,
        label="Qwen3.5-122B · writing (flagship)",
        script="write-big.sh",
        port=8083,
        blurb="122B-A10B at Q5. Best prose here. ~6-8 tok/s.",
        ram_gb=95,
        repo="unsloth/Qwen3.5-122B-A10B-GGUF",
        files=("UD-Q5_K_XL/Qwen3.5-122B-A10B-UD-Q5_K_XL-00001-of-00003.gguf", "UD-Q5_K_XL/Qwen3.5-122B-A10B-UD-Q5_K_XL-00002-of-00003.gguf", "UD-Q5_K_XL/Qwen3.5-122B-A10B-UD-Q5_K_XL-00003-of-00003.gguf"),
        download_gb=91.9,
    ),
    ModelSpec(
        key="agentic",
        freedom="vanilla",
        reasoning="auto",
        ctx_size=32768,
        n_cpu_moe=999,
        label="MiniMax-M2.7 230B · agentic",
        script="agentic.sh",
        port=8086,
        blurb="Largest that fits. 230B-A10B at Q3.",
        ram_gb=105,
        repo="unsloth/MiniMax-M2.7-GGUF",
        files=("UD-Q3_K_XL/MiniMax-M2.7-UD-Q3_K_XL-00001-of-00004.gguf", "UD-Q3_K_XL/MiniMax-M2.7-UD-Q3_K_XL-00002-of-00004.gguf", "UD-Q3_K_XL/MiniMax-M2.7-UD-Q3_K_XL-00003-of-00004.gguf", "UD-Q3_K_XL/MiniMax-M2.7-UD-Q3_K_XL-00004-of-00004.gguf"),
        download_gb=101.9,
    ),
    ModelSpec(
        key="gemma-fast",
        freedom="vanilla",
        reasoning="off",
        ctx_size=16384,
        label="Gemma-4-26B-A4B · fast",
        script="gemma-fast.sh",
        port=8092,
        blurb="MoE with 4B active. Very fast, fits on a 24 GB card. Vision-capable base.",
        repo="unsloth/gemma-4-26B-A4B-it-GGUF",
        files=("gemma-4-26B-A4B-it-UD-Q5_K_M.gguf",),
        download_gb=21.2,
    ),
    ModelSpec(
        key="gemma",
        freedom="vanilla",
        reasoning="off",
        ctx_size=16384,
        label="Gemma-4-31B · general",
        script="gemma.sh",
        port=8093,
        blurb="Dense 31B. Strong general reasoning for its size.",
        repo="unsloth/gemma-4-31B-it-GGUF",
        files=("gemma-4-31B-it-Q5_K_M.gguf",),
        download_gb=21.7,
    ),
    ModelSpec(
        key="mistral",
        freedom="vanilla",
        reasoning="off",
        ctx_size=32768,
        label="Mistral Small 3.2 24B",
        script="mistral.sh",
        port=8094,
        blurb="Lightly aligned base. Good instruction following, Apache 2.0.",
        repo="unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF",
        files=("Mistral-Small-3.2-24B-Instruct-2506-UD-Q5_K_XL.gguf",),
        download_gb=16.8,
    ),
    ModelSpec(
        key="qwen-moe",
        freedom="vanilla",
        reasoning="auto",
        ctx_size=32768,
        label="Qwen3.5-35B-A3B · fast MoE",
        script="qwen-moe.sh",
        port=8095,
        blurb="35B with 3B active. Near-27B quality at much higher speed.",
        repo="unsloth/Qwen3.5-35B-A3B-GGUF",
        files=("Qwen3.5-35B-A3B-UD-Q5_K_XL.gguf",),
        download_gb=26.4,
    ),
    ModelSpec(
        key="devstral",
        freedom="vanilla",
        reasoning="off",
        ctx_size=32768,
        label="Devstral Small 24B · agentic coding",
        script="devstral.sh",
        port=8096,
        blurb="Mistral's agentic coding model. Built for tool use over a repo.",
        repo="bartowski/mistralai_Devstral-Small-2507-GGUF",
        files=("mistralai_Devstral-Small-2507-Q5_K_M.gguf",),
        download_gb=16.8,
    ),
    ModelSpec(
        key="dolphin",
        freedom="tuned",
        reasoning="off",
        ctx_size=32768,
        label="Dolphin-Mistral 24B · Venice",
        script="dolphin.sh",
        port=8097,
        blurb="Dolphin tune on a lightly-aligned Mistral base. Steerable by system prompt.",
        repo="bartowski/cognitivecomputations_Dolphin-Mistral-24B-Venice-Edition-GGUF",
        files=("cognitivecomputations_Dolphin-Mistral-24B-Venice-Edition-Q5_K_M.gguf",),
        download_gb=16.8,
    ),
    ModelSpec(
        key="dolphin-r1",
        freedom="tuned",
        reasoning="auto",
        ctx_size=32768,
        label="Dolphin 3.0 R1 Mistral 24B",
        script="dolphin-r1.sh",
        port=8098,
        blurb="Reasoning tune, abliterated. Middle ground on compliance.",
        repo="mradermacher/Dolphin3.0-R1-Mistral-24B-abliterated-GGUF",
        files=("Dolphin3.0-R1-Mistral-24B-abliterated.Q5_K_M.gguf",),
        download_gb=16.8,
    ),
    ModelSpec(
        key="huihui-27b",
        freedom="tuned",
        reasoning="off",
        ctx_size=16384,
        label="Qwen3.5-27B · abliterated",
        script="huihui-27b.sh",
        port=8099,
        blurb="The most downloaded 27B abliteration. Standard method, not Heretic.",
        repo="mradermacher/Huihui-Qwen3.5-27B-abliterated-i1-GGUF",
        files=("Huihui-Qwen3.5-27B-abliterated.i1-Q5_K_M.gguf",),
        download_gb=19.4,
    ),
    ModelSpec(
        key="uncensored",
        freedom="unlocked",
        reasoning="off",
        ctx_size=16384,
        label="Qwen3.6-27B · uncensored",
        script="uncensored.sh",
        port=8087,
        blurb="Heretic v2 on the 27B base. 36.7 tok/s, all GPU. Reasoning off "
              "matters here: with thinking on, refusals reappear.",
        repo="llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-GGUF",
        files=("Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf",),
        download_gb=19.7,
    ),
    ModelSpec(
        key="uncensored-big",
        freedom="unlocked",
        reasoning="off",
        ctx_size=16384,
        n_cpu_moe=999,
        label="Qwen3.5-122B · uncensored (flagship)",
        script="uncensored-big.sh",
        port=8088,
        blurb="Most capable uncensored model that fits. 13.2 tok/s. Reasoning "
              "off matters here: with thinking on, refusals reappear.",
        ram_gb=90,
        repo="mradermacher/Qwen3.5-122B-A10B-abliterated-i1-GGUF",
        files=("Qwen3.5-122B-A10B-abliterated.i1-Q5_K_M.gguf",),
        download_gb=87.0,
    ),
    ModelSpec(
        key="uncensored-code",
        freedom="unlocked",
        reasoning="auto",
        ctx_size=32768,
        n_cpu_moe=999,
        label="Qwen3-Coder-Next 80B · uncensored",
        script="uncensored-code.sh",
        port=8091,
        blurb="The 80B-A3B abliterated. Reported as the most permissive of the set in practice.",
        ram_gb=55,
        repo="bartowski/huihui-ai_Qwen3-Coder-Next-abliterated-GGUF",
        files=("huihui-ai_Qwen3-Coder-Next-abliterated-Q4_K_M.gguf",),
        download_gb=48.6,
    ),
    ModelSpec(
        key="uncensored-code-q6",
        freedom="unlocked",
        reasoning="auto",
        ctx_size=32768,
        n_cpu_moe=999,
        label="Qwen3-Coder-Next 80B · uncensored (Q6)",
        script="uncensored-code-q6.sh",
        port=8100,
        blurb="Same, at Q6. Best uncensored coding quality that fits.",
        ram_gb=72,
        repo="bartowski/huihui-ai_Qwen3-Coder-Next-abliterated-GGUF",
        files=("huihui-ai_Qwen3-Coder-Next-abliterated-Q6_K/huihui-ai_Qwen3-Coder-Next-abliterated-Q6_K-00001-of-00002.gguf", "huihui-ai_Qwen3-Coder-Next-abliterated-Q6_K/huihui-ai_Qwen3-Coder-Next-abliterated-Q6_K-00002-of-00002.gguf"),
        download_gb=65.6,
    ),
    ModelSpec(
        key="uncensored-gemma",
        freedom="unlocked",
        reasoning="off",
        ctx_size=16384,
        label="Gemma-4-31B · uncensored (accuracy)",
        script="uncensored-gemma.sh",
        port=8089,
        blurb="Heretic on Gemma-4. Gemma abliterations lose the least capability measured.",
        repo="llmfan46/gemma-4-31B-it-uncensored-heretic-GGUF",
        files=("gemma-4-31B-it-uncensored-heretic-Q5_K_M.gguf",),
        download_gb=21.8,
    ),
    ModelSpec(
        key="uncensored-fast",
        freedom="unlocked",
        reasoning="off",
        ctx_size=16384,
        label="Gemma-4-26B-A4B · uncensored (fast)",
        script="uncensored-fast.sh",
        port=8090,
        blurb="MoE, 4B active. Abliterated harder than most. Fits on a 24 GB card.",
        repo="mradermacher/gemma-4-26B-A4B-it-ultra-uncensored-heretic-i1-GGUF",
        files=("gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-Q6_K.gguf",),
        download_gb=22.6,
    ),
    ModelSpec(
        key="uncensored-deckard",
        freedom="unlocked",
        reasoning="auto",
        ctx_size=16384,
        label="Gemma-4-31B · Deckard (thinking)",
        script="uncensored-deckard.sh",
        port=8101,
        blurb="Heretic plus a reasoning tune. Thinking on is the point of this one.",
        repo="mradermacher/gemma-4-31B-it-The-DECKARD-HERETIC-UNCENSORED-Thinking-i1-GGUF",
        files=("gemma-4-31B-it-The-DECKARD-HERETIC-UNCENSORED-Thinking.i1-Q5_K_M.gguf",),
        download_gb=21.8,
    ),
    ModelSpec(
        key="uncensored-mistral",
        freedom="unlocked",
        reasoning="off",
        ctx_size=32768,
        label="Mistral Small 3.2 24B · uncensored",
        script="uncensored-mistral.sh",
        port=8102,
        blurb="Abliterated on an already lightly-aligned base — less to remove, less lost.",
        repo="mradermacher/Huihui-Mistral-Small-3.2-24B-Instruct-2506-abliterated-llamacppfixed-i1-GGUF",
        files=("Huihui-Mistral-Small-3.2-24B-Instruct-2506-abliterated-llamacppfixed.i1-Q5_K_M.gguf",),
        download_gb=16.8,
    ),
    ModelSpec(
        key="uncensored-deepseek",
        freedom="unlocked",
        reasoning="auto",
        ctx_size=32768,
        n_cpu_moe=999,
        label="DeepSeek-V4-Flash 284B · uncensored",
        script="uncensored-deepseek.sh",
        port=8103,
        blurb="284B MoE at Q2. The largest thing that fits, and the card admits Q2 still refuses sometimes.",
        ram_gb=118,
        repo="huihui-ai/Huihui-DeepSeek-V4-Flash-abliterated-ds4-GGUF",
        files=("Huihui-DeepSeek-V4-Flash-BF16-abliterated-ds4-Q2_K.gguf",),
        download_gb=99.7,
    ),
    ModelSpec(
        key="qwen38", label="Qwen3.8-27B", script="qwen38.sh", port=8104,
        blurb="Newer Qwen dense model for writing and coding. Q5 download; use recent llama.cpp.",
        repo="unsloth/Qwen3.8-27B-GGUF",
        files=("Qwen3.8-27B-UD-Q5_K_M.gguf",), download_gb=19.8,
        ctx_size=16384, reasoning="auto",
    ),
    ModelSpec(
        key="qwen38-unlocked", label="Qwen3.8-27B · uncensored",
        script="qwen38-unlocked.sh", port=8105, freedom="unlocked",
        blurb="Community Heretic variant at Q5. MTP weights included; speculative decoding is not enabled.",
        repo="llmfan46/Qwen3.8-27B-Ultra-Uncensored-Heretic-Native-MTP-Preserved-GGUF",
        files=("Qwen3.8-27B-Ultra-Uncensored-Heretic-Native-MTP-Preserved-Q5_K_M.gguf",),
        download_gb=20.1, ctx_size=16384, reasoning="off",
    ),
    ModelSpec(
        key="gemma12-unlocked", label="Gemma-4-12B · uncensored",
        script="gemma12-unlocked.sh", port=8106, freedom="unlocked",
        blurb="Smaller Heretic variant at Q5. About 9.4 GB of weights before context and runtime memory.",
        repo="llmfan46/gemma-4-12B-it-uncensored-heretic-GGUF",
        files=("gemma-4-12B-it-uncensored-heretic-Q5_K_M.gguf",),
        download_gb=9.4, ctx_size=8192, reasoning="off",
    ),

)

#: Ordered least to most unlocked, which is the order the picker groups by.
FREEDOM_ORDER = ("vanilla", "tuned", "unlocked")

FREEDOM_LABELS = {
    "vanilla": "Stock",
    "tuned": "Tuned",
    "unlocked": "Unlocked",
}

FREEDOM_NOTES = {
    "vanilla": "As the lab shipped it. Declines what it was trained to decline.",
    "tuned": "A community tune or a standard abliteration. Steerable by system "
             "prompt; still refuses some things outright.",
    "unlocked": "Refusal directions removed. Will discuss what a stock build "
                "will not. Not lawless — the residue is in the weights, and no "
                "build reaches zero.",
}


def by_freedom(level: str) -> list[ModelSpec]:
    return [m for m in REGISTRY if m.freedom == level]


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
    # Superseded by autonomy_level. Kept only so an old settings file loads
    # and can be migrated; see load().
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
    # Superseded by persistence_level, which owns the round budget. Kept so
    # an old settings file still loads.
    max_tool_iterations: int = 40
    persistence_level: int = 2   # core.persistence.Persistence
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
    #: Height of the message box, in pixels. Dragged, and remembered.
    composer_height: int = 110
    # Context window per model, when the user has overridden the default.
    context_sizes: dict[str, int] = field(default_factory=dict)
    # core.context.Strategy — what to do when a conversation stops fitting.
    context_strategy: int = 2
    # Tokens held back for the reply. A window that is full to the last token
    # has nowhere to put the answer.
    reserve_output: int = 2048
    # Whether each local model thinks before answering. Absent means the
    # model's own default from the registry.
    thinking: dict[str, bool] = field(default_factory=dict)
    # API keys are deliberately NOT here. See core/credentials.py: this file
    # is the one a user might copy between machines or paste into a bug report.

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(config_file().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        settings = cls(**{k: v for k, v in data.items() if k in known})
        for key, path in (settings.model_paths or {}).items():
            set_model_path(key, path)
        # A user who turned off auto-approval meant "confirm everything",
        # which is autonomy level 1. Two controls for one decision is what
        # made level 4 still ask for permission.
        if not settings.auto_approve_reads:
            settings.autonomy_level = 1
            settings.auto_approve_reads = True
        set_runtime(settings.llama_server or None)
        for key, size in (settings.context_sizes or {}).items():
            set_context_size(key, size)
        return settings

    def save(self) -> None:
        self.model_paths = all_model_paths()
        self.llama_server = str(runtime_override() or "")
        self.context_sizes = all_context_sizes()
        target = config_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(target)  # atomic; a crash mid-write can't truncate settings
