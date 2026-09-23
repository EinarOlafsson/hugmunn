# Contributing

Use a dedicated Python 3.10+ environment. Hugmunn uses PyQt6; avoid importing
PySide6 into the same process.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,docs,build]"
```

## Tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q --timeout=45
```

On PowerShell, set `$env:QT_QPA_PLATFORM = "offscreen"` first, then run the
Python command. GUI tests construct real Qt windows without a visible display.
Model clients are faked; tests do not need downloaded weights or cloud billing.
The optional hardware/runtime checks may skip when their prerequisites are
absent. Run tests in a dedicated environment to avoid unrelated pytest plugins.

Keep regression tests focused on behavior. Public API changes should cover the
caller’s observable result, error handling, and resource cleanup. Update the
corresponding docstrings and examples when behavior changes.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/hugmunn/api.py` | Public Python interface, re-exported from `hugmunn` |
| `src/hugmunn/core/` | Model clients, agent loop, tools, sessions, and runtime setup |
| `src/hugmunn/ui/` | PyQt6 desktop widgets and themes |
| `src/hugmunn/skills/` | Bundled Markdown instruction packs |
| `src/hugmunn/resources/icons/` | Raven SVGs and generated native icons |
| `docs/` | User guides and Sphinx API reference |
| `examples/` | Small command-line examples using the public API |
| `packaging/` | Native installer builders |
| `tests/` | Unit and offscreen GUI tests |

## Documentation and packaging

```bash
python -m sphinx -W --keep-going -b html docs docs/_build/html
python -m build
python -m twine check dist/*.whl dist/*.tar.gz
```

Open `docs/_build/html/index.html` to review the documentation. API reference
pages use docstrings directly. Keep README examples short and move longer
explanations into a guide.

After changing the SVG artwork, run `python packaging/generate_icons.py`.
Review both light and dark themes and commit the generated formats alongside
the SVGs. Native desktop instructions are in [docs/installers.md](docs/installers.md).
The version has one source: `src/hugmunn/_version.py`.

The CI package check installs a wheel outside the source tree and verifies
imports, command entry points, bundled skills, artwork, and a real Qt window.
That check is necessary because an editable install can hide missing resources.

## Pull requests

Describe the user-visible problem, the change, and what you tested. Include a
screenshot for visible UI changes. Keep model-specific assumptions out of shared
code where possible. Report the operating system and model/runtime involved
when fixing a platform or provider issue.
