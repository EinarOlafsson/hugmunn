"""Shared fixtures."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the whole session; Qt allows only one."""
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])
