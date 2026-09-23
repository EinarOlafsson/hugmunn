# Desktop installers

The native builders bundle Python, PyQt6, Hugmunn, its skills, and its artwork.
Users do not need a separate Python installation to launch these applications.
Model weights, llama-server, development tools, and GPU drivers remain separate.
The packaging structure follows the native-build approach used in
[spacr](https://github.com/EinarOlafsson/spacr/tree/main/packaging).

## Build locally

Use a clean virtual environment on the target operating system:

```bash
python -m pip install ".[build]"
python packaging/build_desktop.py
```

| Platform | Additional build tool | Output in `dist/desktop/` |
| --- | --- | --- |
| Linux, Debian/Ubuntu | `dpkg-deb` from `dpkg` | `.deb` installer and portable `.tar.gz` |
| Windows | [NSIS](https://nsis.sourceforge.io/Download) on PATH or in its default installation directory | Per-user `-setup.exe` and portable `.zip` |
| macOS | Xcode command-line tools and the system `hdiutil` | `.dmg` containing `Hugmunn.app` and an Applications shortcut |

The builder reads the version from `src/hugmunn/_version.py`, freezes the app
with PyInstaller, launches its offscreen smoke check, and creates the native
package. Each build writes a SHA-256 manifest. `--skip-freeze` reuses the existing
frozen output; use this only when debugging packaging without code changes.

Build on each target OS. PyInstaller is not a cross-compiler: a Linux build
cannot produce a Windows executable or macOS application. Architecture follows
the build runner. The GitHub workflow uses Ubuntu 22.04, Windows x64, and macOS
Apple silicon; it does not produce a universal macOS binary.

On Linux, build on the oldest distribution you intend to support. A frozen app
still depends on the host’s C library and graphics stack. Builds on a newer
machine may not run on older distributions. The Debian installer declares its
build host’s glibc floor and Qt/X11 runtime dependencies.

## Install and remove

On Debian or Ubuntu, install the matching package with:

```bash
sudo apt install ./dist/desktop/hugmunn_0.0.0.3_amd64.deb
```

The application appears as **Hugmunn** in the desktop menu. Remove it with
`sudo apt remove hugmunn`. The portable archive can instead be extracted and
launched with `./Hugmunn/Hugmunn`, provided the system graphics libraries exist.

On Windows, run the setup `.exe`. It installs under
`%LOCALAPPDATA%\Programs\Hugmunn`, creates Start menu and desktop shortcuts, and
registers an uninstaller. Administrator access is not required. Remove it from
Windows’ installed-apps settings.

On macOS, open the `.dmg` and drag **Hugmunn.app** to **Applications**. Remove the
application bundle to uninstall it. If macOS blocks an unsigned build, follow
its security settings for software you trust; distributing signed and notarized
builds requires an Apple Developer account.

Uninstalling the application leaves model downloads, settings, credentials, and
saved conversations in their existing user directories.

## Release workflow

`.github/workflows/desktop.yml` builds and smoke-tests native bundles on their
own operating systems when manually dispatched or when a version tag is pushed.
Artifacts are uploaded to the workflow run. The workflow does not automatically
publish a GitHub release or sign the binaries.

Before sharing a release:

1. Update `src/hugmunn/_version.py` and `CHANGELOG.md`.
2. Run tests, build documentation, and validate the wheel and source archive.
3. Build the native packages and test an installation on each target platform.
4. Sign Windows executables and sign/notarize macOS builds if distributing them
   with a verified publisher identity. Certificates are not included here.
5. Attach the tested artifacts and checksum manifests to the matching release.

`.github/workflows/publish.yml` supports PyPI trusted publishing from a matching
version tag. It requires a GitHub environment named `pypi` and a PyPI trusted
publisher for owner `EinarOlafsson`, repository `hugmunn`, workflow `publish.yml`,
and environment `pypi`. Configure those services before running the publish
workflow. Building locally does not publish anything.

See the [Python packaging guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
and [PyInstaller documentation](https://pyinstaller.org/en/stable/usage.html) for
the underlying build tools.

## Runtime limitations

Cloud chat requires network access and an API key. Local chat requires a
platform-compatible llama-server binary; on Windows, select `llama-server.exe`
in runtime setup or through `LLAMA_SERVER`.

The `python_exec` tool in a frozen application uses an external Python
interpreter: set `HUGMUNN_PYTHON` to its executable, or provide `python3`/`python`
on PATH. This interpreter supplies the packages available to executed snippets.
The bundled application runtime is not a general Python command-line tool.
