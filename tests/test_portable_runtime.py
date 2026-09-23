"""Non-CUDA launch settings and current upstream archive layouts."""

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from hugmunn import config
from hugmunn.core import setup_llama as setup


@pytest.mark.parametrize("system,arch,backend,suffix", [
    ("Linux", "x86_64", "cpu", "ubuntu-x64.tar.gz"),
    ("Linux", "aarch64", "vulkan", "ubuntu-vulkan-arm64.tar.gz"),
    ("Darwin", "arm64", "auto", "macos-arm64.tar.gz"),
    ("Windows", "AMD64", "cpu", "win-cpu-x64.zip"),
    ("Windows", "ARM64", "cpu", "win-cpu-arm64.zip"),
    ("Windows", "AMD64", "vulkan", "win-vulkan-x64.zip"),
    ("Linux", "x86_64", "metal", None),
    ("Linux", "riscv64", "cpu", None),
])
def test_platform_backend_archive_selection(monkeypatch, system, arch, backend, suffix):
    monkeypatch.setattr(setup.platform, "system", lambda: system)
    monkeypatch.setattr(setup.platform, "machine", lambda: arch)
    assert setup.prebuilt_asset_name(backend) == suffix


@pytest.mark.parametrize("backend", ["cpu", "metal", "vulkan"])
def test_portable_launch_avoids_cuda_cache_and_expert_placement(monkeypatch, backend):
    monkeypatch.setattr(config, "_INFERENCE_BACKEND", backend)
    monkeypatch.setattr(config.os, "cpu_count", lambda: 4)
    args = config.by_key("uncensored-big").launch_arguments()
    assert args[args.index("--threads") + 1] == "4"
    assert args[args.index("--cache-type-v") + 1] == "f16"
    assert "--n-cpu-moe" not in args
    if backend == "cpu":
        assert args[args.index("--n-gpu-layers") + 1] == "0"


def test_automatic_mode_is_portable_without_nvidia(monkeypatch):
    monkeypatch.setattr(config, "_INFERENCE_BACKEND", "auto")
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    assert config.backend_arguments() == ["--flash-attn", "auto", "--cache-type-k", "f16", "--cache-type-v", "f16"]


def test_tar_unpack_keeps_versioned_libraries_and_internal_links(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, value in [("release/llama-server", b"binary"), ("release/libggml.so.0.0.1", b"library"),
                            ("../../escape", b"must not escape")]:
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
        link = tarfile.TarInfo("release/libggml.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "libggml.so.0.0.1"
        archive.addfile(link)
    root = tmp_path / "runtime"
    binary = setup._unpack_runtime(buf.getvalue(), "cpu.tar.gz", root)
    assert binary.read_bytes() == b"binary"
    assert (root / "libggml.so").read_bytes() == b"library"
    assert not (tmp_path / "escape").exists()


def test_windows_zip_installs_executable_and_dll(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("runtime/llama-server.exe", b"exe")
        archive.writestr("runtime/ggml.dll", b"dll")
    binary = setup._unpack_runtime(buf.getvalue(), "cpu.zip", tmp_path)
    assert binary.name == "llama-server.exe"
    assert (tmp_path / "ggml.dll").read_bytes() == b"dll"


@pytest.mark.parametrize("backend", ["cpu", "metal", "vulkan"])
def test_build_resets_previous_cuda_configuration(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin" if backend == "metal" else "Linux")
    monkeypatch.setattr(setup, "INSTALL_ROOT", tmp_path)
    monkeypatch.setattr(setup, "SOURCE_DIR", tmp_path / "source")
    monkeypatch.setattr(setup, "preflight", lambda: setup.Preflight(git="git", cmake="cmake", compiler="c++"))
    binary = tmp_path / "source/build/bin/llama-server"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")
    commands = []
    monkeypatch.setattr(setup, "_run", lambda command, *args: commands.append(command))
    assert setup.build(lambda s: None, backend=backend) == binary
    configure = next(c for c in commands if "-B" in c)
    assert "-DGGML_CUDA=OFF" in configure
    for name in ("metal", "vulkan"):
        assert f"-DGGML_{name.upper()}={'ON' if backend == name else 'OFF'}" in configure


def test_silent_build_process_can_be_cancelled():
    import sys
    import threading
    import time
    cancel = threading.Event()
    timer = threading.Timer(0.15, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(setup.SetupError, match="cancelled"):
            setup._run([sys.executable, "-c", "import time; time.sleep(60)"], lambda s: None, cancel)
        assert time.monotonic() - started < 8
    finally:
        timer.cancel()
