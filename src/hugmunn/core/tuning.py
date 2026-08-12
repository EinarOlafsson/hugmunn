"""Measuring a model on this machine, and finding its best expert split.

Two things that are guesses until somebody measures them, and that nobody
measures because it is tedious:

**How fast is this model here.** Published figures are for other hardware. The
picker has been showing numbers I measured once, on one machine, which is
useful to me and misleading to anyone else. Measured on first run and stored,
the number becomes true for the machine it is displayed on.

**How many expert layers belong on the CPU.** ``--n-cpu-moe`` decides how a
mixture-of-experts model is split between VRAM and system RAM, and the shipped
default of 999 means *all of them* — safe, because it always fits, and slow,
because every token then reads gigabytes across the memory bus. The right
value is the smallest one that does not run out of VRAM, and it depends on the
card, the quant and the context size. It is worth several tokens per second
and it has never been tuned, because doing it by hand means a dozen restarts
of a model that takes a minute to load.

Both are cheap to do once and never again, and both refuse to run while
somebody else is using the GPU — this machine runs other people's jobs, and a
tuning sweep that evicts a training run is not a feature.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from ..config import ModelSpec, config_dir


def measurements_file() -> Path:
    return config_dir() / "measurements.json"

#: Tokens to generate when timing. Enough to get past the first-token latency
#: and settle, short enough that a benchmark is not itself a wait.
BENCH_TOKENS = 64

#: Values to try, most aggressive first. Fewer experts on the CPU is faster;
#: the sweep stops at the first value that loads and serves, which is the
#: fastest one this card can hold.
NCPUMOE_LADDER = (0, 8, 16, 24, 32, 40, 56, 72, 96, 999)


@dataclass
class Measurement:
    """What was measured, and the conditions it was measured under."""

    key: str
    tokens_per_second: float = 0.0
    prompt_per_second: float = 0.0
    n_cpu_moe: int = 0
    vram_total_mb: int = 0
    gpu: str = ""
    at: float = 0.0
    note: str = ""

    def summary(self) -> str:
        if not self.tokens_per_second:
            return self.note or "not measured"
        parts = [f"{self.tokens_per_second:.1f} tok/s"]
        if self.prompt_per_second:
            parts.append(f"{self.prompt_per_second:.0f} tok/s prompt")
        if self.n_cpu_moe:
            parts.append(f"--n-cpu-moe {self.n_cpu_moe}")
        return " · ".join(parts)


@dataclass
class Measurements:
    """Everything measured on this machine, by model key."""

    models: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Measurements":
        try:
            return cls(json.loads(measurements_file().read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            return cls()

    def save(self) -> None:
        try:
            measurements_file().parent.mkdir(parents=True, exist_ok=True)
            tmp = measurements_file().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.models, indent=1), encoding="utf-8")
            tmp.replace(measurements_file())
        except OSError:
            pass

    def get(self, key: str) -> Measurement | None:
        row = self.models.get(key)
        if not row:
            return None
        known = {f for f in Measurement.__dataclass_fields__}
        return Measurement(**{k: v for k, v in row.items() if k in known})

    def put(self, measurement: Measurement) -> None:
        self.models[measurement.key] = asdict(measurement)
        self.save()


# ------------------------------------------------------------ the GPU guard


def gpu_is_busy() -> tuple[bool, str]:
    """Whether somebody else is on the card.

    Benchmarking against a contended GPU produces a number that is wrong and
    looks right, and the tuning sweep would evict whatever is running. This
    machine is shared, so both refuse rather than compete.
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False, ""
    try:
        out = subprocess.run(
            [exe, "--query-compute-apps=pid,used_memory", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return False, ""
    rows = [r for r in out.splitlines() if r.strip()]
    if not rows:
        return False, ""
    return True, f"{len(rows)} process(es) already using the GPU: " + "; ".join(rows[:3])


def gpu_name_and_vram() -> tuple[str, int]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return "", 0
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip().splitlines()
        name, _, total = out[0].partition(",")
        return name.strip(), int(total)
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return "", 0


# ------------------------------------------------------------- benchmarking


def benchmark(client, tokens: int = BENCH_TOKENS) -> tuple[float, float]:
    """Generate a little and time it. Returns (generate, prompt) tok/s.

    Uses whatever the server reports when it reports anything -- llama.cpp
    attaches real timings to the final chunk, which are better than wall clock
    because they exclude the HTTP round trip.
    """
    started = time.monotonic()
    produced = 0
    timings: dict = {}
    for event in client.stream(
        [{"role": "user", "content": "Count from one to twenty in words."}],
        max_tokens=tokens, thinking=False,
    ):
        if event.kind == "content":
            produced += max(1, len(event.text) // 4)
        elif event.kind == "done" and event.timings:
            timings = event.timings
        elif event.kind == "error":
            return 0.0, 0.0

    if timings.get("predicted_per_second"):
        return (float(timings["predicted_per_second"]),
                float(timings.get("prompt_per_second") or 0.0))
    elapsed = max(time.monotonic() - started, 1e-6)
    return produced / elapsed, 0.0


def measure_model(spec: ModelSpec, server, client_for: Callable,
                  report: Callable[[str], None] = lambda _: None) -> Measurement:
    """Start the model if needed, time it, and record the result."""
    busy, why = gpu_is_busy()
    if busy and not server.is_running:
        return Measurement(spec.key, note=f"skipped — {why}")

    gpu, vram = gpu_name_and_vram()
    started_here = False
    if not server.is_running or server.spec is None or server.spec.key != spec.key:
        report(f"starting {spec.label} to measure it…")
        server.start(spec, on_progress=report)
        started_here = True

    report("timing…")
    generate, prompt = benchmark(client_for(spec))
    if started_here:
        server.stop()

    return Measurement(
        key=spec.key, tokens_per_second=generate, prompt_per_second=prompt,
        n_cpu_moe=spec.n_cpu_moe, vram_total_mb=vram, gpu=gpu, at=time.time(),
    )


# ------------------------------------------------------- the expert split


@dataclass
class Attempt:
    n_cpu_moe: int
    ok: bool
    tokens_per_second: float = 0.0
    note: str = ""


def tune_expert_split(spec: ModelSpec, server, client_for: Callable,
                      report: Callable[[str], None] = lambda _: None,
                      ladder: tuple[int, ...] = NCPUMOE_LADDER,
                      cancel=None) -> Iterator[Attempt]:
    """Try expert splits from aggressive to safe, yielding each result.

    Stops at the first value that both loads and serves. That is the fastest
    this card can hold, because fewer experts on the CPU is always faster when
    it fits at all -- the failure mode is running out of VRAM, not being
    slower.

    Yields as it goes rather than returning at the end: each attempt is a
    model load, which for the 122B is most of a minute, and a progress bar
    with nothing behind it is worse than none.
    """
    if not spec.n_cpu_moe:
        report(f"{spec.label} is not a mixture-of-experts model; nothing to tune.")
        return

    busy, why = gpu_is_busy()
    if busy:
        report(f"not tuning — {why}")
        return

    for value in ladder:
        if cancel is not None and cancel.is_set():
            return
        report(f"trying --n-cpu-moe {value}…")
        server.stop()
        try:
            server.start(spec, on_progress=lambda _m: None, timeout=600,
                         extra_args=["--n-cpu-moe", str(value)])
        except Exception as exc:  # noqa: BLE001 - an OOM here is the expected answer
            yield Attempt(value, False, note=_why_it_failed(exc))
            continue

        generate, _ = benchmark(client_for(spec))
        server.stop()
        if generate <= 0:
            yield Attempt(value, False, note="loaded but did not generate")
            continue
        yield Attempt(value, True, generate)
        return


def _why_it_failed(exc: Exception) -> str:
    text = str(exc).lower()
    if "out of memory" in text or "cuda" in text and "alloc" in text:
        return "out of VRAM"
    if "did not become ready" in text:
        return "did not load in time"
    return str(exc).splitlines()[0][:120]
