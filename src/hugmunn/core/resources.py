"""Live CPU, RAM, GPU and VRAM sampling.

Read straight from ``/proc`` and ``nvidia-smi`` rather than taking a psutil
dependency — the four numbers needed here are cheap to get and the file formats
have been stable for two decades.

Sampling matters for a local-model UI specifically. Whether a model will load
at all depends on free VRAM, and whether it runs at 13 tok/s or 2 depends on
whether the weights fit in RAM — both measured in this session. Showing them
next to the Start button turns "why is it slow" into something visible before
you press it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_STAT = Path("/proc/stat")
_MEMINFO = Path("/proc/meminfo")


@dataclass(frozen=True)
class Snapshot:
    cpu_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    swap_used_gb: float = 0.0
    gpu_percent: float | None = None      # None when there is no NVIDIA GPU
    vram_used_gb: float | None = None
    vram_total_gb: float | None = None
    gpu_procs: int = 0

    @property
    def ram_percent(self) -> float:
        return 100.0 * self.ram_used_gb / self.ram_total_gb if self.ram_total_gb else 0.0

    @property
    def vram_percent(self) -> float:
        if not self.vram_total_gb:
            return 0.0
        return 100.0 * (self.vram_used_gb or 0.0) / self.vram_total_gb

    @property
    def has_gpu(self) -> bool:
        return self.vram_total_gb is not None


def _cpu_times() -> tuple[int, int] | None:
    """(busy, total) jiffies since boot. CPU load needs two samples to differ."""
    try:
        first = _STAT.read_text().split("\n", 1)[0]
    except OSError:
        return None
    parts = first.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    values = [int(v) for v in parts[1:8] if v.isdigit()]
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total - idle, total


def _memory() -> tuple[float, float, float]:
    """(used_gb, total_gb, swap_used_gb), using MemAvailable for 'used'.

    MemAvailable rather than MemFree: page cache is reclaimable, and counting it
    as used makes a healthy machine look exhausted.
    """
    fields: dict[str, int] = {}
    try:
        for line in _MEMINFO.read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                fields[key] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0, 0.0, 0.0
    mib = 1024 * 1024
    total = fields.get("MemTotal", 0) / mib
    used = total - fields.get("MemAvailable", 0) / mib
    swap = (fields.get("SwapTotal", 0) - fields.get("SwapFree", 0)) / mib
    return used, total, swap


def _gpu() -> tuple[float, float, float, int] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, check=True,
        ).stdout.strip().splitlines()
        util, used, total = (float(v) for v in out[0].split(","))
        procs = subprocess.run(
            [exe, "--query-compute-apps=pid", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=4,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return None
    return util, used / 1024, total / 1024, len([p for p in procs.splitlines() if p.strip()])


class Sampler:
    """Stateful because CPU load is a delta between two reads of /proc/stat."""

    def __init__(self) -> None:
        self._previous = _cpu_times()

    def sample(self) -> Snapshot:
        cpu = 0.0
        current = _cpu_times()
        if current and self._previous:
            busy = current[0] - self._previous[0]
            total = current[1] - self._previous[1]
            if total > 0:
                cpu = max(0.0, min(100.0, 100.0 * busy / total))
        if current:
            self._previous = current

        used, total_ram, swap = _memory()
        gpu = _gpu()
        if gpu is None:
            return Snapshot(cpu, used, total_ram, swap)
        util, vram_used, vram_total, procs = gpu
        return Snapshot(cpu, used, total_ram, swap, util, vram_used, vram_total, procs)
