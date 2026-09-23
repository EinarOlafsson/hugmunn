# Build on the target operating system using packaging/build_desktop.py.
import runpy
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
icons = root / "src/hugmunn/resources/icons"
version = runpy.run_path(str(root / "src/hugmunn/_version.py"))["__version__"]
icon = icons / ("hugmunn.icns" if sys.platform == "darwin" else "hugmunn.ico")

a = Analysis(
    [str(root / "packaging/launcher.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=collect_data_files("hugmunn") + copy_metadata("hugmunn", recursive=True),
    hiddenimports=collect_submodules("hugmunn.core"),
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6", "PySide2", "PyQt5", "tkinter", "pytest", "sphinx"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="Hugmunn", debug=False, strip=False, upx=False,
    console=False, icon=str(icon),
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Hugmunn")
if sys.platform == "darwin":
    app = BUNDLE(
        collection, name="Hugmunn.app", icon=str(icon),
        bundle_identifier="org.hugmunn.desktop", version=version,
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleDisplayName": "Hugmunn",
            "CFBundleShortVersionString": version,
        },
    )
