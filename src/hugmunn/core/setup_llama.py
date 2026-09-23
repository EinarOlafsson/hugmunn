"""Install llama-server for CPU, CUDA, Apple Metal or Vulkan.

Source builds select a backend explicitly and reset cached CMake GPU options.
Official prebuilt archives are matched to OS, architecture and backend. An
existing ROCm or SYCL build can also be selected in the runtime dialog.
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
DEFAULT_JOBS = min(16, os.cpu_count() or 1)


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
    metal: bool = False
    vulkan: bool = False

    @property
    def backend(self) -> str:
        """Best detected build backend; a driver alone is not a CUDA toolkit."""
        return "metal" if self.metal else "cuda" if self.can_build_cuda else "vulkan" if self.vulkan else "cpu"


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
        if self.can_build and self.backend in ("metal", "vulkan"):
            return f"Will build llama.cpp with {self.backend.title()} acceleration, using up to {DEFAULT_JOBS} cores."
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
        compiler=shutil.which("c++") or shutil.which("g++") or shutil.which("clang++") or shutil.which("cl") or "",
        nvcc=shutil.which("nvcc") or "",
        nvidia_smi=shutil.which("nvidia-smi") or "",
        metal=platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64"),
        vulkan=bool(shutil.which("vulkaninfo")),
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
    candidate = BIN_DIR / ("llama-server.exe" if platform.system() == "Windows" else "llama-server")
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
    import queue
    import signal
    import threading

    output: queue.Queue = queue.Queue()
    def read_output():
        assert process.stdout is not None
        try:
            for line in process.stdout:
                output.put(line)
        finally:
            output.put(None)
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       capture_output=True, timeout=5)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        if os.name != "nt":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        process.wait(timeout=3)
                raise SetupError("cancelled")
            try:
                line = output.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                break
            if line.rstrip():
                on_progress(line.rstrip())
        if process.wait() != 0:
            raise SetupError(f"{command[0]} exited with code {process.returncode}")
    finally:
        reader.join(timeout=1)
        if not reader.is_alive() and process.stdout:
            process.stdout.close()


def build(on_progress: Callable[[str], None], cancel=None,
          jobs: int = DEFAULT_JOBS, cuda: bool | None = None, backend: str = "auto") -> Path:
    """Clone (or update) llama.cpp and build llama-server. Returns its path.

    ``backend`` selects auto, CPU, CUDA, Metal or Vulkan. The older ``cuda``
    argument is retained for callers that explicitly request CUDA or CPU.
    """
    checks = preflight()
    if not checks.can_build:
        raise SetupError(
            "cannot build here — missing " + ", ".join(checks.missing)
            + ("\n" + checks.advice() if checks.advice() else "")
        )
    if cuda is not None:
        backend = "cuda" if cuda else "cpu"  # compatibility with existing API callers
    if backend == "auto":
        backend = checks.backend
    if backend not in ("cpu", "cuda", "metal", "vulkan"):
        raise SetupError(f"unknown backend: {backend}")
    if backend == "cuda" and not checks.can_build_cuda:
        raise SetupError("CUDA requires the NVIDIA CUDA toolkit and a detected GPU. Choose CPU or Vulkan instead.")
    if backend == "metal" and platform.system() != "Darwin":
        raise SetupError("Metal is available only on macOS")
    jobs = max(1, min(jobs, os.cpu_count() or 1))

    INSTALL_ROOT.mkdir(parents=True, exist_ok=True)

    if (SOURCE_DIR / ".git").is_dir():
        on_progress("==> updating llama.cpp")
        try:
            _run([checks.git, "pull", "--ff-only"], on_progress, cancel, SOURCE_DIR)
        except SetupError:
            if cancel is not None and cancel.is_set():
                raise
            # A dirty or diverged checkout should not block a rebuild; the
            # source we already have builds perfectly well.
            on_progress("(could not update; building the existing checkout)")
    else:
        on_progress("==> cloning llama.cpp")
        _run([checks.git, "clone", "--depth", "1", REPO, str(SOURCE_DIR)],
             on_progress, cancel)

    args = [checks.cmake, "-B", str(SOURCE_DIR / "build"),
            "-DCMAKE_BUILD_TYPE=Release", "-DLLAMA_BUILD_SERVER=ON"]
    args += [f"-DGGML_{name.upper()}={'ON' if backend == name else 'OFF'}"
             for name in ("cuda", "metal", "vulkan")]
    if backend == "cuda":
        args.append(f"-DCMAKE_CUDA_ARCHITECTURES={checks.compute_cap}")
    on_progress(f"==> configuring for {backend}")
    _run(args, on_progress, cancel, SOURCE_DIR)

    on_progress(f"==> building with {jobs} cores")
    _run([checks.cmake, "--build", str(SOURCE_DIR / "build"),
          "--config", "Release", "-j", str(jobs)], on_progress, cancel, SOURCE_DIR)

    executable = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
    output = SOURCE_DIR / "build" / "bin"
    if not (output / executable).is_file() and (output / "Release" / executable).is_file():
        output = output / "Release"
    built = output / executable
    if not built.is_file():
        raise SetupError(f"the build finished but {built} is not there")
    # Return the binary beside its shared libraries. Copying just the executable
    # breaks Windows DLL lookup and GPU plugin loading on other platforms.
    on_progress(f"==> done: {built}")
    return built


# ------------------------------------------------------------ prebuilt route


def prebuilt_asset_name(backend: str = "auto") -> str | None:
    """Return an exact release filename suffix for the requested platform/backend."""
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("x86_64", "amd64") else None
    system = platform.system().lower()
    if arch is None:
        return None
    if backend == "auto":
        backend = "metal" if system == "darwin" else "cpu"
    if system == "darwin":
        return f"macos-{arch}.tar.gz" if backend in ("metal", "cpu") else None
    if backend == "metal":
        return None
    if system == "linux":
        middle = "cuda-12.8-" if backend == "cuda" and arch == "x64" else "vulkan-" if backend == "vulkan" else "" if backend == "cpu" else None
        return f"ubuntu-{middle}{arch}.tar.gz" if middle is not None else None
    if system == "windows":
        middle = "cuda-12.4" if backend == "cuda" and arch == "x64" else "vulkan" if backend == "vulkan" and arch == "x64" else "cpu" if backend == "cpu" else None
        return f"win-{middle}-{arch}.zip" if middle else None
    return None


def _unpack_runtime(payload: bytes, name: str, destination: Path) -> Path:
    """Extract runtime files and libraries without trusting archive paths or links."""
    import io
    import tarfile
    import zipfile

    destination.mkdir(parents=True, exist_ok=True)
    def save(filename: str, content: bytes) -> None:
        leaf = Path(filename).name
        if leaf.startswith(("llama-", "lib", "ggml")) or leaf.endswith((".dll", ".dylib")) or ".so" in leaf:
            target = destination / leaf
            target.write_bytes(content)
            target.chmod(target.stat().st_mode | 0o755)

    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for entry in archive.infolist():
                if not entry.is_dir():
                    save(entry.filename, archive.read(entry))
    else:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for entry in archive.getmembers():
                if entry.isfile():
                    source = archive.extractfile(entry)
                    if source:
                        save(entry.name, source.read())
                elif entry.issym() or entry.islnk():
                    # Resolve only within the archive, then write a regular file.
                    try:
                        target_name = str(Path(entry.name).parent / entry.linkname) if entry.issym() else entry.linkname
                        target = archive.getmember(target_name)
                        if target.isfile():
                            source = archive.extractfile(target)
                            if source:
                                save(entry.name, source.read())
                    except KeyError:
                        pass
    executable = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
    binary = destination / executable
    if not binary.is_file():
        raise SetupError(f"the archive did not contain {executable}")
    return binary


def download_prebuilt(on_progress: Callable[[str], None], cancel=None, backend: str = "auto") -> Path:
    """Download an official CPU, Metal, Vulkan or CUDA archive for this machine."""
    import hashlib
    import httpx

    suffix = prebuilt_asset_name(backend)
    if suffix is None:
        raise SetupError("No prebuilt archive for this platform/backend. Choose CPU, build from source, or select an existing runtime.")
    on_progress("==> checking recent official llama.cpp releases")
    try:
        response = httpx.get("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10",
                             timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        releases = response.json()
        match = next((a for release in releases if not release.get("draft")
                      for a in release.get("assets", [])
                      if a.get("name", "").startswith("llama-") and a["name"].endswith("-" + suffix)), None)
        if match is None:
            raise SetupError(f"No current {suffix} archive. Build from source instead.")
        on_progress(f"==> downloading {match['name']}")
        chunks = []
        with httpx.stream("GET", match["browser_download_url"], timeout=300, follow_redirects=True) as stream:
            stream.raise_for_status()
            seen = 0
            for chunk in stream.iter_bytes(1024 * 1024):
                if cancel is not None and cancel.is_set():
                    raise SetupError("cancelled")
                chunks.append(chunk)
                seen += len(chunk)
                on_progress(f"    {seen / 1e6:.0f} MB")
        payload = b"".join(chunks)
        digest = match.get("digest") or ""
        if digest.startswith("sha256:") and hashlib.sha256(payload).hexdigest() != digest[7:]:
            raise SetupError("The runtime download failed its SHA-256 integrity check")
        folder = BIN_DIR / match["name"].removesuffix(".tar.gz").removesuffix(".zip")
        binary = _unpack_runtime(payload, match["name"], folder)
        on_progress(f"==> done: {binary}")
        return binary
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise SetupError(f"Runtime download failed: {exc}") from exc


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
                "CUDA, Metal, Vulkan, ROCm or SYCL for compatible hardware.")


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
