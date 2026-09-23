# Documentation

Read [the index](index.md) for the user guides. [api.rst](api.rst) generates the
API reference from the public Python docstrings.

To build a local, searchable HTML copy from the repository root:

```bash
python -m pip install -e ".[docs]"
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html` in a browser. No model server or credentials
are required to build the documentation.
