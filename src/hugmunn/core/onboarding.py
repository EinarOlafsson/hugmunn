"""Versioned first-run agreement and a read-only, local hardware inventory."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import Settings, find_runtime, models_root

AGREEMENT_VERSION = "2026-09-23.1"
ISSUES_URL = "https://github.com/EinarOlafsson/hugmunn/issues"
AGREEMENT = """Hugmunn user agreement

Hugmunn 0.0.0.9 and later are licensed under PolyForm Noncommercial 1.0.0.
Personal and noncommercial research use and modifications are allowed under
that license. Commercial use requires separate permission from Einar Olafsson
(einar.olafsson@gmail.com). Redistribution must retain the required notices.
Earlier releases retain their original licenses. The full license below governs.

Third-party libraries and model weights retain their own terms. Qt/PySide6
LGPL rights, including replacing those libraries, remain available. Hugmunn
is supplied without warranty, as described in the license.

AI providers receive the prompts and tool results you send to their services.
Local model inference stays on your machine unless you use network tools.
AI responses can be incorrect; review changes and commands before relying on them.

Automatic error reports are enabled by default. After you connect GitHub and
finish this agreement, Hugmunn may create PUBLIC issues at
github.com/EinarOlafsson/hugmunn under the GitHub account shown in setup.
Reports contain the app/Python/OS versions, an error category and, for unexpected
exceptions, built-in exception names and Hugmunn module/line locations. They
exclude exception messages, logs, prompts, conversations, file contents, paths,
API keys and hardware identifiers. Your GitHub username is publicly visible
as the issue author. Repeated reports are limited and deduplicated.

You can turn reporting off below or later in Accounts. GitHub login and AI
login are optional. No report is sent before this agreement is accepted.
Hardware checks are local and are not included in reports.
"""


def needs_setup(settings: Settings) -> bool:
    """Require acceptance again when the agreement changes."""
    return settings.agreement_version != AGREEMENT_VERSION or not settings.agreement_accepted_at


def accept_agreement(settings: Settings) -> None:
    """Record acceptance only after the final unchecked checkbox is selected."""
    settings.agreement_version = AGREEMENT_VERSION
    settings.agreement_accepted_at = datetime.now(timezone.utc).isoformat()
    settings.save()


@dataclass(frozen=True)
class Hardware:
    """Machine-local inventory; unknown values are not treated as zero capacity."""

    system: str
    cpu_threads: int
    ram_gb: float | None
    disk_free_gb: float | None
    gpu: str
    runtime: bool

    def summary(self) -> str:
        """Display memory and disk checks without promising model performance."""
        ram = f"{self.ram_gb:.1f} GB" if self.ram_gb is not None else "not detected"
        disk = f"{self.disk_free_gb:.1f} GB" if self.disk_free_gb is not None else "not detected"
        advice = "Cloud models need no local GPU."
        if self.ram_gb is not None and self.ram_gb < 16:
            advice += " Use cloud models or a small local model; large downloads may not fit in memory."
        else:
            advice += " Model weights, context and the OS must all fit in available RAM/VRAM."
        return (f"{self.system}\nCPU: {self.cpu_threads} logical cores\nRAM: {ram}\n"
                f"Free space on model drive: {disk}\nGraphics: {self.gpu}\n"
                f"llama-server: {'found' if self.runtime else 'not installed (optional for cloud use)'}\n\n{advice}")


def _ram_bytes() -> int | None:
    try:
        if platform.system() == "Windows":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in
                    ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.total
        elif platform.system() == "Darwin":
            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=3))
        else:
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        pass
    return None


def inspect_hardware() -> Hardware:
    """Read RAM, disk and GPU information without downloading or installing anything."""
    ram = _ram_bytes()
    root = models_root().expanduser()
    while not root.exists() and root != root.parent:
        root = root.parent
    try:
        free = shutil.disk_usage(root).free / 1e9
    except OSError:
        free = None
    gpu = "not detected; CPU inference is available"
    if platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64"):
        gpu = "Apple Silicon (Metal; shared system memory)"
    elif exe := shutil.which("nvidia-smi"):
        try:
            result = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                    capture_output=True, text=True, timeout=3, check=True)
            gpu = result.stdout.strip() or gpu
        except (OSError, subprocess.SubprocessError):
            pass
    elif shutil.which("vulkaninfo"):
        try:
            result = subprocess.run(["vulkaninfo", "--summary"], capture_output=True,
                                    text=True, timeout=3, check=True)
            devices = [line.split("=", 1)[1].strip() for line in result.stdout.splitlines()
                       if "deviceName" in line and "=" in line]
            if devices:
                gpu = "Vulkan: " + ", ".join(devices)
        except (OSError, subprocess.SubprocessError):
            pass
    return Hardware(f"{platform.system()} {platform.machine()}", os.cpu_count() or 1,
                    ram / 1e9 if ram else None, free, gpu, find_runtime() is not None)
