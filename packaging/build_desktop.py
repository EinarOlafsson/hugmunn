"""Build a native installer and portable bundle on the current platform.

Usage: ``python packaging/build_desktop.py``. Install ``.[build]`` first.
Linux needs dpkg-deb, Windows needs NSIS, and macOS uses the system hdiutil.
Build output stays under build/desktop and dist/desktop.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/desktop"
DIST = ROOT / "dist/desktop"
ICONS = ROOT / "src/hugmunn/resources/icons"
VERSION = runpy.run_path(str(ROOT / "src/hugmunn/_version.py"))["__version__"]


def run(*command: str, **kwargs) -> None:
    """Run a build tool, stopping the build if it fails."""
    subprocess.run(command, check=True, cwd=ROOT, **kwargs)


def require_tool(name: str) -> str:
    """Locate a native packaging tool or report the missing prerequisite."""
    path = shutil.which(name)
    if not path and name == "makensis":
        candidate = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "NSIS/makensis.exe"
        if candidate.is_file():
            path = str(candidate)
    if not path:
        raise SystemExit(f"Install {name} before building a desktop installer.")
    return path


def linux(bundle: Path) -> list[Path]:
    """Package the frozen app as a Debian installer and portable tar archive."""
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine)
    if arch is None:
        raise SystemExit(f"Unsupported Debian architecture: {machine}")
    archive = DIST / f"Hugmunn-{VERSION}-linux-{machine}.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(bundle, arcname="Hugmunn")
    package = DIST / f"hugmunn_{VERSION}_{arch}.deb"
    with tempfile.TemporaryDirectory(prefix="deb-", dir=BUILD) as temporary:
        stage = Path(temporary)
        shutil.copytree(bundle, stage / "opt/hugmunn")
        (stage / "usr/bin").mkdir(parents=True)
        (stage / "usr/bin/hugmunn").symlink_to("/opt/hugmunn/Hugmunn")
        applications = stage / "usr/share/applications"
        applications.mkdir(parents=True)
        (applications / "hugmunn.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Hugmunn\n"
            "Comment=Chat with local and cloud language models\n"
            "Exec=/opt/hugmunn/Hugmunn\nIcon=hugmunn\nTerminal=false\n"
            "Categories=Development;Science;\nStartupWMClass=hugmunn\n",
            encoding="utf-8",
        )
        icon_dir = stage / "usr/share/icons/hicolor/256x256/apps"
        icon_dir.mkdir(parents=True)
        shutil.copy2(ICONS / "hugmunn-256.png", icon_dir / "hugmunn.png")
        license_dir = stage / "usr/share/doc/hugmunn"
        license_dir.mkdir(parents=True)
        shutil.copy2(ROOT / "LICENSE", license_dir / "copyright")
        control = stage / "DEBIAN"
        control.mkdir()
        size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) // 1024
        (control / "control").write_text(
            f"Package: hugmunn\nVersion: {VERSION}\nArchitecture: {arch}\n"
            "Section: science\nPriority: optional\nMaintainer: Einar Olafsson\n"
            f"Installed-Size: {size}\n"
            f"Depends: libc6 (>= {platform.libc_ver()[1] or '2.35'}), libstdc++6, libgl1, libegl1, libdbus-1-3, "
            "libfontconfig1, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, "
            "libxcb-icccm4, libxcb-keysyms1, libxcb-image0, libxcb-render-util0, "
            "libxcb-xinerama0\n"
            "Homepage: https://github.com/EinarOlafsson/hugmunn\n"
            "Description: Desktop client for local and cloud language models\n"
            " Includes the Python runtime and Qt interface. Model weights and\n"
            " llama-server are installed separately.\n",
            encoding="utf-8",
        )
        run(require_tool("dpkg-deb"), "--root-owner-group", "--build", str(stage), str(package))
    return [package, archive]


def windows(bundle: Path) -> list[Path]:
    """Build a per-user NSIS installer and portable zip with matching icons."""
    machine = platform.machine().lower()
    stem = DIST / f"Hugmunn-{VERSION}-windows-{machine}"
    archive = Path(shutil.make_archive(str(stem), "zip", bundle.parent, bundle.name))
    installer = stem.with_name(stem.name + "-setup.exe")
    run(require_tool("makensis"), f"/DVERSION={VERSION}", f"/DSOURCE={bundle}",
        f"/DOUTPUT={installer}", f"/DICON={ICONS / 'hugmunn.ico'}",
        str(ROOT / "packaging/windows.nsi"))
    return [installer, archive]


def macos(bundle: Path) -> list[Path]:
    """Create a drag-to-Applications DMG for the builder's architecture."""
    image = DIST / f"Hugmunn-{VERSION}-macos-{platform.machine()}.dmg"
    with tempfile.TemporaryDirectory(prefix="dmg-", dir=BUILD) as temporary:
        stage = Path(temporary)
        shutil.copytree(bundle, stage / bundle.name, symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        run(require_tool("hdiutil"), "create", "-volname", "Hugmunn", "-srcfolder",
            str(stage), "-ov", "-format", "UDZO", str(image))
    return [image]


def main() -> None:
    """Freeze, smoke-test, and package the application for this host."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-freeze", action="store_true", help="Reuse build/desktop/frozen output")
    args = parser.parse_args()
    builders = {"linux": linux, "win32": windows, "darwin": macos}
    builder = builders.get(sys.platform)
    if builder is None:
        parser.error(f"Unsupported platform: {sys.platform}")
    require_tool({"linux": "dpkg-deb", "win32": "makensis", "darwin": "hdiutil"}[sys.platform])
    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    if not args.skip_freeze:
        run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(BUILD / "frozen"), "--workpath", str(BUILD / "work"),
            str(ROOT / "packaging/hugmunn.spec"))
    bundle = BUILD / "frozen" / ("Hugmunn.app" if sys.platform == "darwin" else "Hugmunn")
    executable = bundle / ({"darwin": "Contents/MacOS/Hugmunn", "win32": "Hugmunn.exe"}.get(sys.platform, "Hugmunn"))
    if not executable.is_file():
        parser.error(f"Missing frozen executable: {executable}")
    with tempfile.TemporaryDirectory(prefix="hugmunn-smoke-") as temporary:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HUGMUNN_CONFIG_DIR=temporary,
                   HUGMUNN_MODELS_ROOT=str(Path(temporary) / "models"))
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)
        run(str(executable), "--smoke-test", env=env, timeout=60)
    outputs = builder(bundle)
    checksum = DIST / f"SHA256SUMS-{sys.platform}-{platform.machine()}.txt"
    with checksum.open("w", encoding="utf-8") as stream:
        for path in outputs:
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            stream.write(f"{digest.hexdigest()}  {path.name}\n")
            print(path)


if __name__ == "__main__":
    main()
