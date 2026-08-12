"""Get a working llama-server onto this machine, from inside the app.

Everything else hugmunn needs is either portable or downloadable. The
binary is neither: it is compiled against this machine's CPU features and CUDA
version, so it cannot ship with the app, cannot live in the models repo, and
cannot be copied from the machine that already works. That is the whole reason
a second machine ends up with 87 GB of correct weights and nothing to run.

So this builds it. Not a shell script the user has to find and run -- the
machine that needs this is the one where the models repo may not be cloned at
all, which makes "run the script in the repo" circular.

Two routes, and the choice is not a preference:

* **Build from source** is the only way to get GPU support on Linux. llama.cpp
  publishes prebuilt CUDA binaries for Windows but not for Linux, so a
  downloaded Linux build is CPU-only. On a 24 GB card that is the difference
  between 37 tok/s and something not worth waiting for.
* **Download a prebuilt** needs no toolchain at all, which matters on a
  managed machine where installing cmake means filing a ticket. It is offered
  as CPU-only, said plainly, rather than presented as equivalent.

Everything is installed under the user's own data directory rather than into
the models repo, so this works whether or not that repo exists.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO = "https://github.com/ggml-org/llama.cpp"

#: Ours, not the models repo's. The machine that needs a build is the one
#: where ~/.claude/models may not exist.
INSTALL_ROOT = Path(
    os.environ.get("HUGMUNN_DATA_DIR", Path.home() / ".local" / "share" / "hugmunn")
)
SOURCE_DIR = INSTALL_ROOT / "llama.cpp"
BIN_DIR = INSTALL_ROOT / "bin"

#: Never the whole machine. This box runs other people's jobs, and a build
#: that takes every core for six minutes is a rude thing to start silently.
DEFAULT_JOBS = 16


class SetupError(RuntimeError):
    pass


@dataclass
class Preflight:
    """What this machine can actually do, checked before anything is promised."""

    git: str = ""
    cmake: str = ""
    compiler: str = ""
    nvcc: str = ""
    nvidia_smi: str = ""
    compute_cap: str = ""
    gpu_name: str = ""
    missing: list[str] = field(default_factory=list)

    @property
    def can_build(self) -> bool:
        return bool(self.git and self.cmake and self.compiler)

    @property
    def can_build_cuda(self) -> bool:
        return self.can_build and bool(self.nvcc) and bool(self.compute_cap)

    @property
    def has_gpu(self) -> bool:
        return bool(self.nvidia_smi and self.gpu_name)

    def summary(self) -> str:
        """What will happen, and what it will cost, before it starts."""
        if self.can_build_cuda:
            return (f"Will build llama.cpp with CUDA for your {self.gpu_name} "
                    f"(compute {self.compute_cap}). Takes a few minutes and "
                    f"uses {DEFAULT_JOBS} cores.")
        if self.can_build and self.has_gpu:
            return (f"A {self.gpu_name} is present but the CUDA toolkit (nvcc) "
                    f"is not installed, so this would be a CPU-only build and "
                    f"the GPU would sit idle. Install the CUDA toolkit and try "
                    f"again for a large speedup.")
        if self.can_build:
            return ("Will build llama.cpp for the CPU. No NVIDIA GPU was "
                    "detected, so large models will be slow.")
        return "Cannot build here: " + ", ".join(self.missing)

    def advice(self) -> str:
        """How to get the missing pieces, named for the package manager here."""
        if not self.missing:
            return ""
        if shutil.which("apt-get"):
            return "sudo apt install " + " ".join(
                {"git": "git", "cmake": "cmake", "a C++ compiler": "build-essential"}
                .get(m, m) for m in self.missing)
        if shutil.which("dnf"):
            return "sudo dnf install git cmake gcc-c++"
        if shutil.which("brew"):
            return "brew install git cmake"
        return "Install: " + ", ".join(self.missing)


def preflight() -> Preflight:
    """Look at the machine. Reads only; changes nothing."""
    result = Preflight(
        git=shutil.which("git") or "",
        cmake=shutil.which("cmake") or "",
        compiler=shutil.which("c++") or shutil.which("g++") or shutil.which("clang++") or "",
        nvcc=shutil.which("nvcc") or "",
        nvidia_smi=shutil.which("nvidia-smi") or "",
    )
    if result.nvidia_smi:
        try:
            out = subprocess.run(
                [result.nvidia_smi, "--query-gpu=name,compute_cap",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout.strip().splitlines()
            if out:
                name, _, cap = out[0].partition(",")
                result.gpu_name = name.strip()
                # cmake wants 86, nvidia-smi says 8.6.
                result.compute_cap = cap.strip().replace(".", "")
        except (subprocess.SubprocessError, OSError, ValueError):
            pass

    for tool, label in ((result.git, "git"), (result.cmake, "cmake"),
                        (result.compiler, "a C++ compiler")):
        if not tool:
            result.missing.append(label)
    return result


def installed_binary() -> Path | None:
    """The llama-server this module has installed, if it is there."""
    candidate = BIN_DIR / "llama-server"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def _run(command: list[str], on_progress: Callable[[str], None],
         cancel=None, cwd: Path | None = None) -> None:
    """Run a command, streaming its output line by line.

    Streamed rather than captured because these take minutes: a build with no
    output is indistinguishable from a hang, and the user will kill it.
    """
    on_progress("$ " + " ".join(command))
    process = subprocess.Popen(
        command, cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, start_new_session=True,
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if cancel is not None and cancel.is_set():
                process.terminate()
                raise SetupError("cancelled")
            line = line.rstrip()
            if line:
                on_progress(line)
    finally:
        process.stdout and process.stdout.close()
    if process.wait() != 0:
        raise SetupError(f"{command[0]} exited with code {process.returncode}")


def build(on_progress: Callable[[str], None], cancel=None,
          jobs: int = DEFAULT_JOBS, cuda: bool | None = None) -> Path:
    """Clone (or update) llama.cpp and build llama-server. Returns its path.

    ``cuda`` defaults to whatever the machine supports. A shallow clone,
    because the history is large and none of it is wanted.
    """
    checks = preflight()
    if not checks.can_build:
        raise SetupError(
            "cannot build here — missing " + ", ".join(checks.missing)
            + ("\n" + checks.advice() if checks.advice() else "")
        )
    if cuda is None:
        cuda = checks.can_build_cuda

    INSTALL_ROOT.mkdir(parents=True, exist_ok=True)

    if (SOURCE_DIR / ".git").is_dir():
        on_progress("==> updating llama.cpp")
        try:
            _run([checks.git, "pull", "--ff-only"], on_progress, cancel, SOURCE_DIR)
        except SetupError:
            # A dirty or diverged checkout should not block a rebuild; the
            # source we already have builds perfectly well.
            on_progress("(could not update; building the existing checkout)")
    else:
        on_progress("==> cloning llama.cpp")
        _run([checks.git, "clone", "--depth", "1", REPO, str(SOURCE_DIR)],
             on_progress, cancel)

    args = [checks.cmake, "-B", str(SOURCE_DIR / "build"),
            "-DCMAKE_BUILD_TYPE=Release", "-DLLAMA_BUILD_SERVER=ON"]
    if cuda:
        # For the card that is here, not for every architecture ever shipped:
        # "all" turns a six-minute build into most of an hour.
        args += ["-DGGML_CUDA=ON",
                 f"-DCMAKE_CUDA_ARCHITECTURES={checks.compute_cap or '86'}"]
        on_progress(f"==> configuring with CUDA for compute {checks.compute_cap}")
    else:
        on_progress("==> configuring for CPU")
    _run(args, on_progress, cancel, SOURCE_DIR)

    on_progress(f"==> building with {jobs} cores")
    _run([checks.cmake, "--build", str(SOURCE_DIR / "build"),
          "--config", "Release", "-j", str(jobs)], on_progress, cancel, SOURCE_DIR)

    built = SOURCE_DIR / "build" / "bin" / "llama-server"
    if not built.is_file():
        raise SetupError(f"the build finished but {built} is not there")

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for tool in ("llama-server", "llama-cli", "llama-bench", "llama-quantize"):
        source = SOURCE_DIR / "build" / "bin" / tool
        if source.is_file():
            link = BIN_DIR / tool
            link.unlink(missing_ok=True)
            try:
                link.symlink_to(source)
            except OSError:
                shutil.copy2(source, link)

    on_progress(f"==> done: {BIN_DIR / 'llama-server'}")
    return BIN_DIR / "llama-server"


# ------------------------------------------------------------ prebuilt route


def prebuilt_asset_name() -> str | None:
    """The release asset for this platform, or None if there is not one.

    CPU-only on Linux by necessity: llama.cpp publishes CUDA binaries for
    Windows and not for Linux, so there is no GPU build to download here.
    """
    machine = platform.machine().lower()
    system = platform.system().lower()
    if system == "linux" and machine in ("x86_64", "amd64"):
        return "ubuntu-x64"
    if system == "darwin":
        return "macos-arm64" if machine == "arm64" else "macos-x64"
    if system == "windows":
        return "win-cuda-x64" if shutil.which("nvidia-smi") else "win-x64"
    return None


def download_prebuilt(on_progress: Callable[[str], None], cancel=None) -> Path:
    """Fetch an official release build. No toolchain needed, no GPU on Linux."""
    import io
    import json
    import zipfile

    import httpx

    fragment = prebuilt_asset_name()
    if fragment is None:
        raise SetupError(
            f"no prebuilt binary is published for {platform.system()} "
            f"{platform.machine()} — build from source instead"
        )

    on_progress("==> asking GitHub for the latest release")
    try:
        response = httpx.get(
            "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest",
            timeout=30.0, follow_redirects=True,
        )
        response.raise_for_status()
        assets = response.json().get("assets") or []
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        raise SetupError(f"could not reach GitHub: {exc}") from exc

    match = next((a for a in assets
                  if fragment in a.get("name", "") and a.get("name", "").endswith(".zip")),
                 None)
    if match is None:
        raise SetupError(
            f"the latest release has no {fragment} asset. Build from source, "
            f"or download one yourself and point hugmunn at it."
        )

    on_progress(f"==> downloading {match['name']}")
    try:
        with httpx.stream("GET", match["browser_download_url"],
                          timeout=httpx.Timeout(300.0, connect=15.0),
                          follow_redirects=True) as stream:
            stream.raise_for_status()
            total = int(stream.headers.get("content-length", 0))
            chunks, seen = [], 0
            for chunk in stream.iter_bytes(1024 * 1024):
                if cancel is not None and cancel.is_set():
                    raise SetupError("cancelled")
                chunks.append(chunk)
                seen += len(chunk)
                if total:
                    on_progress(f"    {seen / 1e6:.0f} / {total / 1e6:.0f} MB")
            payload = b"".join(chunks)
    except httpx.HTTPError as exc:
        raise SetupError(f"download failed: {exc}") from exc

    on_progress("==> unpacking")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for entry in archive.namelist():
            leaf = Path(entry).name
            if not leaf or entry.endswith("/"):
                continue
            if leaf.startswith("llama-") or leaf.endswith((".so", ".dylib", ".dll")):
                target = BIN_DIR / leaf
                target.write_bytes(archive.read(entry))
                if leaf.startswith("llama-"):
                    target.chmod(target.stat().st_mode | 0o755)

    binary = BIN_DIR / "llama-server"
    if not binary.is_file():
        raise SetupError("the archive did not contain llama-server")
    on_progress(f"==> done: {binary}")
    if platform.system().lower() == "linux":
        on_progress("note: this is a CPU-only build — no Linux CUDA release is "
                    "published. Build from source to use the GPU.")
    return binary


# ------------------------------------------------------- what a build can do


@dataclass(frozen=True)
class RuntimeInfo:
    """Whether a given llama-server can use the GPU at all.

    This is the question behind "why is it so slow". A CPU-only build on a
    machine with a good GPU runs a large model several times slower and gives
    no indication that anything is wrong -- it is simply, quietly, not using
    the card. Since hugmunn may have produced that build itself (when the
    CUDA toolkit was absent at build time), it owes the user the answer.
    """

    path: Path
    version: str = ""
    devices: tuple[str, ...] = ()

    @property
    def has_gpu(self) -> bool:
        return any(d.startswith(("CUDA", "ROCm", "Metal", "Vulkan", "SYCL"))
                   for d in self.devices)

    def summary(self) -> str:
        if self.has_gpu:
            return "GPU-capable: " + "; ".join(self.devices)
        return ("CPU-only build — this binary cannot use a GPU. Rebuild with "
                "the CUDA toolkit installed for a large speedup.")


def runtime_info(path: Path | None = None) -> RuntimeInfo:
    """Ask a llama-server binary what compute devices it was built for.

    ``--list-devices`` is the reliable signal: a CUDA build enumerates its
    cards, a CPU-only build has nothing to list. ``--version`` does not say —
    it reports the compiler, not the backend — which is why "it says it built
    fine" is not evidence that the GPU is in play.
    """
    if path is None:
        from ..config import find_runtime

        path = find_runtime()
    if path is None:
        return RuntimeInfo(Path())

    def ask(*args) -> str:
        try:
            result = subprocess.run([str(path), *args], capture_output=True,
                                    text=True, timeout=20)
            return result.stdout + result.stderr
        except (OSError, subprocess.SubprocessError):
            return ""

    version = ""
    for line in ask("--version").splitlines():
        if line.lower().startswith("version"):
            version = line.strip()
            break

    devices = tuple(
        line.strip()
        for line in ask("--list-devices").splitlines()
        if ":" in line and not line.lower().startswith("available")
    )
    return RuntimeInfo(Path(path), version, devices)


#: llama.cpp says this once per load, and it is the definitive answer for a
#: *particular model* rather than for the binary: a CUDA build still runs on
#: the CPU when the layers do not fit, or when --n-gpu-layers is 0.
_OFFLOAD_RE = re.compile(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to\s+GPU")


def offload_from_log(lines) -> str:
    """Read "N/M layers on GPU" out of a server's startup output.

    Empty when the server has not said. Worth surfacing because it answers
    the question the resource meters only hint at, and it distinguishes the
    two different slow cases: a CPU-only binary, and a GPU binary whose model
    did not fit.
    """
    for line in reversed(list(lines)):
        match = _OFFLOAD_RE.search(line)
        if match:
            on_gpu, total = int(match[1]), int(match[2])
            if on_gpu == 0:
                return "0 layers on GPU — running entirely on the CPU"
            if on_gpu >= total:
                return f"all {total} layers on GPU"
            return f"{on_gpu} of {total} layers on GPU, the rest on CPU"
    return ""
