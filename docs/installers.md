# Desktop installers

The native builders bundle Python, PySide6, Hugmunn, its skills, and its artwork.
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
sudo apt install ./dist/desktop/hugmunn_0.0.0.9_amd64.deb
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

`main` is the default and release branch. Make changes on `nightly`, push
completed work there, and merge it into `main` after validation. Before a new
release, bump `src/hugmunn/_version.py` and add the matching version section to
`CHANGELOG.md` on `nightly`.

Every push to `main`, including a merge or a direct version bump, runs
`.github/workflows/publish.yml`. If the version is already published on both
GitHub and PyPI, the workflow skips publication. A new version triggers:

1. Python tests, wheel/source builds, and documentation checks.
2. Linux, Windows, and macOS installer builds on native runners, with startup checks.
3. A lightweight `v<version>` tag pointing to the tested commit.
4. PyPI publication through the repository owner's trusted publisher, and a
   GitHub release with the wheel, source archive, desktop installers, portable
   bundles, and installer checksum manifests. These run independently, so a
   PyPI account setup issue does not withhold desktop downloads.

The workflow does not create commits or add bot contributors. An interrupted
release can be retried from **Actions → Release Hugmunn**, using the same source
commit or its version tag. Once a tag exists, a different commit must use a new
version. Existing PyPI files are skipped on retries.

`.github/workflows/desktop.yml` is also available as a manual build without
publishing. The native packages are unsigned; public distribution with a
verified publisher identity requires your Windows signing certificate and an
Apple Developer ID with notarization.

### One-time PyPI setup

While signed into your own PyPI account, add a pending trusted publisher at
[PyPI account publishing](https://pypi.org/manage/account/publishing/):

| Field | Value |
| --- | --- |
| PyPI project | `hugmunn` |
| GitHub owner | `EinarOlafsson` |
| Repository | `hugmunn` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

The repository has a matching GitHub environment named `pypi`. The PyPI account
that registers the pending publisher owns the new project when it is first
published. No API token needs to be stored in the repository. Building locally
does not publish anything.

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
