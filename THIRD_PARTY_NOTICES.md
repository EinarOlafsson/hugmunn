# Third-party software

Hugmunn's PolyForm Noncommercial license applies to Hugmunn's own code,
documentation, and artwork. It does not replace or restrict the licenses of
third-party components, model weights, or external runtimes.

## Qt, PySide6, and Shiboken

The desktop interface uses the unmodified PySide6 Essentials and Shiboken
packages from The Qt Company. The Qt modules used by Hugmunn are available
under the GNU Lesser General Public License version 3. Their copyright remains
with The Qt Company and the respective contributors. Hugmunn uses the LGPL
option for these components, not a commercial Qt license.

Copies of the LGPL version 3 and the GPL version 3 it incorporates are provided
in `licenses/LGPL-3.0-only.txt` and `licenses/GPL-3.0-only.txt`.

You may replace or modify these libraries and reverse engineer the combined
application as needed to debug modifications to the LGPL components. Hugmunn's
noncommercial terms do not limit these LGPL rights. The libraries remain
dynamically linked; the desktop bundles do not encrypt or lock them.

With a Python installation, install replacement libraries in that environment.
For a frozen bundle, replace compatible libraries under `_internal/PySide6`
and `_internal/shiboken6` (Windows/Linux), or the corresponding framework and
resource directories inside `Hugmunn.app/Contents` (macOS). Alternatively,
rebuild Hugmunn using `packaging/build_desktop.py` and your replacement libraries.
Keep the Python ABI and Qt versions compatible. Unsigned local builds are
supported; no signing key is required to rebuild or run Hugmunn.

Corresponding upstream source and build instructions:

- Qt source releases: https://download.qt.io/official_releases/qt/
- PySide6 and Shiboken source: https://code.qt.io/pyside/pyside-setup.git/
- Qt build instructions: https://doc.qt.io/qt-6/build-sources.html
- PySide6 build instructions: https://doc.qt.io/qtforpython-6/building_from_source/index.html
- Qt license and third-party notices: https://doc.qt.io/qt-6/licenses.html
- PySide6 third-party notices: https://doc.qt.io/qtforpython-6/licenses.html

Each native bundle includes `THIRD_PARTY_VERSIONS.json` with the exact Qt,
PySide6, Shiboken, and Python versions and source locations for that build.
Dependency metadata and available upstream license files are also retained
inside the bundle. No changes are made to these libraries by the Hugmunn build.

## Other dependencies

The distribution retains installed metadata and license files for its Python
dependencies. Direct runtime dependencies include HTTPX (BSD-3-Clause),
Python-Markdown (BSD-3-Clause), and Pygments (BSD-2-Clause), along with their
dependencies. Native bundles also contain Python under the Python Software
Foundation license and its included third-party notices. PyInstaller's
bootloader distribution exception applies to its bundled bootloader.

Model weights and llama.cpp are downloaded separately and are not included
in Hugmunn's Python distributions or native installers. Their own license
terms apply. Commercial permission for Hugmunn does not grant commercial
permission for a model or another dependency.
