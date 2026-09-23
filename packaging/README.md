# Native packaging

See [the installer guide](../docs/installers.md) for prerequisites, supported
outputs, installation, and release preparation.

```bash
python -m pip install ".[build]"
python packaging/build_desktop.py
```

`hugmunn.spec` defines the frozen application. `build_desktop.py` runs its
smoke check and wraps it as a native installer. `windows.nsi` defines the
per-user Windows installation and uninstallation behavior.

`generate_icons.py` renders the provided raven SVGs into PNG, ICO, and ICNS.
The black and white source SVGs live in `src/hugmunn/resources/icons/` and are
also used directly by the desktop and remote interfaces.
