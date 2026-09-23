"""Sphinx configuration for the user guide and public Python API."""

from hugmunn import __version__

project = "Hugmunn"
author = "Einar Olafsson"
copyright = "2026, Einar Olafsson"
release = __version__
extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.napoleon"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "README.md"]
html_theme = "alabaster"
html_title = f"Hugmunn {release}"
html_logo = "../src/hugmunn/resources/icons/hugmunn-horizontal-black.svg"
html_favicon = "../src/hugmunn/resources/icons/hugmunn.ico"
autodoc_member_order = "bysource"
autodoc_typehints = "description"
