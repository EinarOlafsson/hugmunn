"""Free what hugmunn owns, and nothing else.

Adapted from ``spacr.qt.resource_cleanup``, including its refusal: *"free as
many resources as possible" must never reach anything this program does not
own*. This machine runs other people's work — a model that loads four seconds
sooner is not worth somebody's training run — so no process is killed here, by
name or by memory use, and nothing needs root. No ``drop_caches``: the page
cache belongs to the kernel, and dropping it is a transfer from everybody to
nobody.

One thing genuinely differs from spaCR and it changes what the VRAM button
*is*. spaCR holds VRAM through torch, in its own process, so clearing it means
``empty_cache()``. hugmunn holds no VRAM at all — llama-server does, in a
child process, and 20 GB of weights is the whole of it. There is no API to
hand part of that back. So "release VRAM" here stops the server, which is
honest about being all-or-nothing, and it says how long the reload will take
rather than presenting itself as free.

Every number is measured before and after by the same function. A cleanup that
freed nothing reports that it freed nothing.
"""

from __future__ import annotations

import gc
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

ACTIONS = ("ram", "vram", "cpu", "disk")


def human_bytes(count: int) -> str:
    """``1536`` -> ``"1.5 KB"``. Two significant places, never a fake one."""
    value = float(max(0, int(count)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


@dataclass(frozen=True)
class Reclaim:
    """The measured result of one cleanup. Not the intent — the outcome."""

    action: str
    before: int = 0
    after: int = 0
    details: tuple[str, ...] = ()
    note: str = ""
    measured: bool = True

    @property
    def freed(self) -> int:
        """Never negative. Memory that *grew* is reported as zero freed and
        named in :meth:`summary`, because "freed -4 MB" is not a thing."""
        return max(0, int(self.before) - int(self.after))

    @property
    def grew(self) -> int:
        return max(0, int(self.after) - int(self.before))

    def summary(self) -> str:
        label = {"ram": "RAM", "vram": "VRAM", "cpu": "CPU"}.get(self.action, self.action)
        if not self.measured:
            return f"{label}: nothing to measure — {self.note}"
        if self.action == "cpu":
            body = (f"{self.before} → {self.after} threads" if self.before != self.after
                    else f"{self.after} threads, unchanged")
        elif self.freed:
            body = f"freed {human_bytes(self.freed)}"
        elif self.grew:
            body = f"freed nothing — {human_bytes(self.grew)} more is in use than before"
        else:
            body = "freed nothing measurable"
        return f"{label}: {body}. {self.note}".strip()


# ----------------------------------------------------------- measurement


def process_rss() -> int:
    """This process's resident set size, or 0 when it cannot be read.

    Zero means "could not measure", and a cleanup that could not measure
    says so rather than reporting a figure it made up.
    """
    try:
        with open("/proc/self/statm", encoding="utf-8") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def gpu_used_bytes() -> int | None:
    """VRAM in use across the device, or ``None`` when there is no GPU to ask.

    Device-wide rather than per-process on purpose: the number the user is
    looking at in the meter is the device's, and reporting a different one
    after pressing the button would read as the button lying.
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    import subprocess

    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()
        return int(out[0]) * 1024 * 1024 if out else None
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None


def _thread_count() -> int:
    import threading

    return int(threading.active_count())


# ------------------------------------------------------------------- RAM


def clear_ram() -> Reclaim:
    """Drop hugmunn's own caches and collect. RSS measured either side."""
    before = process_rss()
    details: list[str] = []

    # Every lru_cache in our own modules. Not imported means not populated,
    # and importing a module to clear it would allocate rather than free.
    import sys

    for name, module in list(sys.modules.items()):
        if not name.startswith("hugmunn."):
            continue
        for attr in dir(module):
            value = getattr(module, attr, None)
            clear = getattr(value, "cache_clear", None)
            info = getattr(value, "cache_info", None)
            if not callable(clear) or not callable(info):
                continue
            try:
                held = int(info().currsize)
                if held:
                    clear()
                    details.append(f"{name}.{attr} ({held} entries)")
            except Exception:
                continue

    try:
        from PyQt6.QtGui import QPixmapCache

        held = int(QPixmapCache.totalUsed())
        if held:
            QPixmapCache.clear()
            details.append(f"Qt pixmap cache ({held} KB)")
    except Exception:
        pass

    collected = gc.collect()
    if collected:
        details.append(f"{collected} unreachable objects collected")

    after = process_rss()
    if not before or not after:
        return Reclaim("ram", before, after, tuple(details), measured=False,
                       note="this process's memory use could not be read")
    note = ""
    if not details:
        note = "Nothing was cached, so there was nothing to drop."
    elif before <= after:
        note = ("The caches are gone; the allocator has not handed those pages "
                "back to the OS, so the process size did not move.")
    return Reclaim("ram", before, after, tuple(details), note=note)


# ------------------------------------------------------------------ VRAM


def clear_vram(server=None) -> Reclaim:
    """Stop the model server, which is the only VRAM hugmunn holds.

    ``server`` is a :class:`~hugmunn.core.server.ServerManager`. A server
    this app *adopted* rather than started is left running: it was somebody
    else's decision to start it, and stopping it is not this button's to make.
    """
    before = gpu_used_bytes()
    if before is None:
        return Reclaim("vram", 0, 0, (), measured=False,
                       note="no NVIDIA GPU was found, so there is no VRAM to report")

    if server is None or not server.is_running:
        return Reclaim("vram", before, before, (),
                       note=("No model server is running, so hugmunn is holding "
                             "no VRAM. What is in use belongs to another process, "
                             "and no program can reclaim that."))
    if getattr(server, "_adopted", False):
        return Reclaim("vram", before, before, (),
                       note=("The running server was already up when hugmunn "
                             "started, so it is not ours to stop. Stop it where "
                             "it was launched."))

    label = server.spec.label if server.spec else "the model"
    server.stop()

    # nvidia-smi reports the device, and the driver takes a moment to reap a
    # freed context. Polling briefly beats reporting "freed nothing" for a
    # release that did happen.
    import time

    after = before
    for _ in range(20):
        time.sleep(0.25)
        current = gpu_used_bytes()
        if current is not None and current < after:
            after = current
        if current is not None and before - current > 512 * 1024 * 1024:
            break

    return Reclaim("vram", before, after, (f"stopped {label}",),
                   note=("Unloading is all-or-nothing — llama.cpp has no way to "
                         "release part of a model. Starting it again re-reads "
                         "the weights from disk."))


# ------------------------------------------------------------------- CPU


def clear_cpu() -> Reclaim:
    """Retire idle worker capacity. Nothing running or queued is touched."""
    before = _thread_count()
    details: list[str] = []
    try:
        from PyQt6.QtCore import QThreadPool

        pool = QThreadPool.globalInstance()
        if pool is not None:
            # NOT pool.clear(): that discards *queued* work. Cycling the
            # expiry timeout retires threads that have already finished and
            # leaves everything running or waiting exactly where it is.
            previous = int(pool.expiryTimeout())
            pool.setExpiryTimeout(0)
            pool.setExpiryTimeout(previous if previous > 0 else 30000)
            details.append(f"idle pool threads retired "
                           f"({pool.activeThreadCount()} still working)")
    except Exception:
        pass

    after = _thread_count()
    note = ""
    if before == after and not details:
        note = "There was no idle capacity to retire."
    else:
        note = ("No process was killed and no request was cancelled. A model "
                "server keeps its own threads and is not touched.")
    return Reclaim("cpu", before, after, tuple(details), note=note)


# ------------------------------------------------------------------ disk


@dataclass(frozen=True)
class DiskEntry:
    path: str
    total: int
    used: int
    free: int

    @property
    def percent_used(self) -> float:
        return (100.0 * self.used / self.total) if self.total else 0.0

    def summary(self) -> str:
        return (f"{self.path}: {human_bytes(self.free)} free of "
                f"{human_bytes(self.total)} ({self.percent_used:.0f}% used)")


def disk_report(paths=None) -> list[DiskEntry]:
    """Free space on every drive hugmunn touches. Reads; frees nothing.

    Deduplicated by device, so weights and a working directory on the same
    disk are one line rather than two identical ones — that duplication is
    what makes a disk readout stop being read.
    """
    from .. import config

    wanted = list(paths or [])
    if not wanted:
        wanted = [str(config.MODELS_ROOT), str(config.CONFIG_DIR), str(Path.home())]
        for spec in config.REGISTRY:
            path = spec.model_path
            if path.is_absolute() and path.parent.is_dir():
                wanted.append(str(path.parent))

    entries: list[DiskEntry] = []
    seen: set[int] = set()
    for path in wanted:
        try:
            device = os.stat(path).st_dev
            if device in seen:
                continue
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        seen.add(device)
        entries.append(DiskEntry(path, usage.total, usage.used, usage.free))
    return entries


# --------------------------------------------------- what the button says
#
# A confirmation that asks "are you sure?" is not a confirmation: a user
# cannot consent to an unnamed action. Each of these names what will happen,
# in order, and says what it cannot do.

CONFIRMATIONS: dict[str, tuple[str, str]] = {
    "ram": ("Release RAM", (
        "hugmunn will:\n"
        "  • drop its own caches — rendered messages, icons, parsed assets;\n"
        "  • run a full garbage collection.\n\n"
        "It will not touch any other program and it will not drop the operating "
        "system's page cache. A running model server keeps its memory: that is "
        "the model itself, and dropping it would mean unloading it.\n\n"
        "You will be told how much was actually freed, measured before and after."
    )),
    "vram": ("Release VRAM", (
        "hugmunn will stop the running model server, which unloads the "
        "weights from the GPU.\n\n"
        "This is all-or-nothing — llama.cpp cannot release part of a model — and "
        "starting the model again re-reads its weights from disk, which for a "
        "large model is tens of seconds.\n\n"
        "It cannot reclaim VRAM held by another process; no program can. A "
        "server that was already running before hugmunn started is left "
        "alone, because stopping it is not this button's decision."
    )),
    "cpu": ("Release CPU", (
        "hugmunn will let Qt retire the worker threads it has finished with.\n\n"
        "No process is killed and no request is cancelled — not hugmunn's, "
        "and certainly not anybody else's work on this machine. Threads still "
        "doing work are left alone, and a model server's own threads are not "
        "touched.\n\n"
        "It cannot make anything already running go faster; it gives back "
        "capacity hugmunn is holding and not using."
    )),
    "disk": ("Check disk space", (
        "hugmunn will read the free space on every drive it touches — where "
        "the weights live, the config directory and your home directory — and "
        "show one line per drive.\n\n"
        "Nothing is deleted, moved or written. This action only reads."
    )),
}
