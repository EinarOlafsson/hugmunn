"""Shared fixtures, and undoing the state a reloaded module leaves behind."""

from __future__ import annotations

import importlib
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the whole session; Qt allows only one."""
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def pristine_config():
    """Put ``localagent.config`` back the way it was found.

    Several tests reload it against a temporary models root, because
    MODELS_ROOT is resolved at import. ``monkeypatch`` restores the
    environment variable but not the module that already read it, so without
    this the *next* test sees a models root inside somebody else's tmp_path —
    which showed up as two unrelated failures and sixteen silent skips.
    """
    from localagent import config

    before = (config.MODELS_ROOT, config.CONFIG_DIR)
    yield
    if (config.MODELS_ROOT, config.CONFIG_DIR) != before:
        importlib.reload(config)
    # Module-level overrides outlive a reload only if the module object is the
    # same one, so clear them either way.
    config._MODEL_PATHS.clear()
    config._CONTEXT_SIZES.clear()
    config.set_runtime(None)
