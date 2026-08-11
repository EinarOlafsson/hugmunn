"""Downloading model weights, and judging whether a disk is a good place for them.

Two things a user cannot easily check before committing to an 87 GB download:
whether the disk has room, and whether it is fast enough for the model to be
usable once it is there. The second matters more than it sounds — a GGUF is
memory-mapped, so pages are read from disk during inference, not only at load.
On a spinning disk a large MoE model is slow every single turn, not merely slow
to start.

Downloads stream from Hugging Face over plain HTTPS rather than shelling out to
``hf``: byte-level progress is needed for a progress bar, and parsing another
tool's output for it is fragile.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx

HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
CHUNK = 8 * 1024 * 1024
TIMEOUT = httpx.Timeout(60.0, connect=15.0)


# ------------------------------------------------------------------ the disk


@dataclass(frozen=True)
class DiskReport:
    path: Path
    free_gb: float
    total_gb: float
    device: str = ""
    kind: str = "unknown"       # "NVMe" | "SSD" | "HDD" | "network" | "unknown"
    rotational: bool | None = None

    @property
    def is_fast(self) -> bool:
        return self.kind in ("NVMe", "SSD")

    def verdict(self, needed_gb: float) -> tuple[bool, str]:
        """(can proceed, human explanation)."""
        if self.free_gb < needed_gb:
            return False, (
                f"Not enough space: {self.free_gb:.0f} GB free, {needed_gb:.0f} GB "
                f"needed. Choose another disk or free {needed_gb - self.free_gb:.0f} GB."
            )

        headroom = self.free_gb - needed_gb
        lines = [f"{self.free_gb:.0f} GB free of {self.total_gb:.0f} GB "
                 f"({self.kind}) — {headroom:.0f} GB left afterwards."]

        if self.kind == "HDD":
            lines.append(
                "This is a spinning disk. Weights are memory-mapped, so a large "
                "model reads from it during inference and not only at load — "
                "expect it to be slow on every turn, not just the first. An "
                "NVMe or SSD is strongly preferable."
            )
        elif self.kind == "network":
            lines.append(
                "This looks like a network mount. Model loading will be limited "
                "by the link, and inference may stall unpredictably. Use local "
                "storage if you can."
            )
        elif self.kind == "NVMe":
            lines.append("NVMe — ideal for this.")
        elif self.kind == "SSD":
            lines.append("SATA SSD — fine, though NVMe loads noticeably faster.")

        if headroom < 20:
            lines.append(
                f"Only {headroom:.0f} GB would remain. Leave room for the OS and "
                "for a partially-downloaded file."
            )
        return True, " ".join(lines)


def _device_for(path: Path) -> tuple[str, bool | None]:
    """Resolve a path to its backing block device and rotational flag."""
    try:
        target = path.resolve()
        source = ""
        best = 0
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            mount = parts[1]
            if str(target) == mount or str(target).startswith(mount.rstrip("/") + "/"):
                if len(mount) >= best:
                    best, source = len(mount), parts[0]
    except OSError:
        return "", None

    if not source.startswith("/dev/"):
        return source, None  # network mount, tmpfs, overlay …

    name = Path(source).name
    # nvme0n1p2 -> nvme0n1 ; sda1 -> sda ; dm-0 stays
    base = name
    if base.startswith("nvme"):
        base = base.split("p")[0]
    else:
        base = base.rstrip("0123456789") or base
    flag = Path(f"/sys/block/{base}/queue/rotational")
    try:
        return base, flag.read_text().strip() == "1"
    except OSError:
        return base, None


def evaluate_disk(path: str | Path) -> DiskReport:
    """Free space plus a best-effort guess at what kind of disk this is."""
    target = Path(path).expanduser()
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return DiskReport(target, 0.0, 0.0)

    device, rotational = _device_for(probe)
    if not device:
        kind = "unknown"
    elif ":" in device or device.startswith("//"):
        kind = "network"
    elif device.startswith("nvme"):
        kind = "NVMe"
    elif rotational is True:
        kind = "HDD"
    elif rotational is False:
        kind = "SSD"
    else:
        kind = "unknown"

    gb = 1024 ** 3
    return DiskReport(target, usage.free / gb, usage.total / gb, device, kind, rotational)


# ------------------------------------------------------------- the download


@dataclass
class Progress:
    file_index: int
    file_count: int
    filename: str
    downloaded: int
    total: int
    overall_downloaded: int
    overall_total: int

    @property
    def percent(self) -> float:
        return 100.0 * self.overall_downloaded / self.overall_total if self.overall_total else 0.0


class DownloadError(RuntimeError):
    pass


def _url(repo: str, filename: str) -> str:
    return f"{HF_ENDPOINT}/{repo}/resolve/main/{filename}"


def file_size(repo: str, filename: str) -> int:
    """Content length without fetching the body, for an accurate total up front."""
    try:
        response = httpx.head(_url(repo, filename), timeout=30.0, follow_redirects=True)
        if response.status_code >= 400:
            return 0
        return int(response.headers.get("content-length", 0))
    except (httpx.HTTPError, ValueError):
        return 0


def download(
    repo: str,
    filenames: list[str],
    destination: Path,
    on_progress=None,
    cancel=None,
) -> list[Path]:
    """Fetch ``filenames`` from ``repo`` into ``destination``.

    Resumes a partial file with a Range request rather than restarting — at
    87 GB over a link that has been measured between 3 and 29 MB/s here, losing
    progress to a dropped connection is expensive.
    """
    destination = Path(destination).expanduser()
    destination.mkdir(parents=True, exist_ok=True)

    sizes = [file_size(repo, name) for name in filenames]
    overall_total = sum(sizes)
    overall_done = 0
    written: list[Path] = []

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        for index, (name, expected) in enumerate(zip(filenames, sizes)):
            target = destination / Path(name).name
            partial = target.with_suffix(target.suffix + ".part")

            if target.is_file() and (not expected or target.stat().st_size == expected):
                overall_done += target.stat().st_size
                written.append(target)
                continue

            start = partial.stat().st_size if partial.is_file() else 0
            headers = {"Range": f"bytes={start}-"} if start else {}
            try:
                with client.stream("GET", _url(repo, name), headers=headers) as response:
                    if response.status_code == 416:      # already complete
                        start = partial.stat().st_size
                    elif response.status_code >= 400:
                        raise DownloadError(
                            f"HTTP {response.status_code} fetching {name} from {repo}"
                        )
                    mode = "ab" if start and response.status_code == 206 else "wb"
                    if mode == "wb":
                        start = 0
                    done = start
                    with open(partial, mode) as handle:
                        for chunk in response.iter_bytes(CHUNK):
                            if cancel is not None and cancel.is_set():
                                raise DownloadError("cancelled")
                            handle.write(chunk)
                            done += len(chunk)
                            if on_progress:
                                on_progress(Progress(
                                    index + 1, len(filenames), Path(name).name,
                                    done, expected or done,
                                    overall_done + done, overall_total or done,
                                ))
            except httpx.HTTPError as exc:
                raise DownloadError(f"{name}: {exc}") from exc

            partial.replace(target)
            overall_done += target.stat().st_size
            written.append(target)

    return written
