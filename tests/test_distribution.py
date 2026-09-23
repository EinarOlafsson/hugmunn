"""Installed resources and launch behavior that editable installs can hide."""

from importlib.resources import files
import os
import subprocess
import sys

import pytest

from hugmunn import branding
from hugmunn.ui import theme


def test_public_import_does_not_load_qt():
    result = subprocess.run(
        [sys.executable, "-c", "import hugmunn, sys; assert not any(n.startswith(('PySide6', 'PyQt6')) for n in sys.modules)"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("option", ["--version", "--help"])
def test_cli_information_does_not_need_a_display(option, tmp_path):
    environment = dict(os.environ, QT_QPA_PLATFORM="a-plugin-that-does-not-exist",
                       HUGMUNN_CONFIG_DIR=str(tmp_path))
    result = subprocess.run([sys.executable, "-m", "hugmunn", option],
                            env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "hugmunn" in result.stdout.lower()
    assert not list(tmp_path.iterdir())


def test_all_artwork_formats_are_in_the_package():
    resources = files("hugmunn") / "resources" / "icons"
    for name in ("hugmunn.png", "hugmunn.ico", "hugmunn.icns", "hugmunn-256.png"):
        assert (resources / name).read_bytes(), name
    for kind in ("mark", "horizontal"):
        assert '#000000' in branding.svg(kind, light=True)
        assert '#ffffff' in branding.svg(kind, light=False)


def test_window_artwork_changes_with_theme(qt_app, monkeypatch, tmp_path):
    from hugmunn.ui.main_window import MainWindow

    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_offer_restore", lambda self: None)
    monkeypatch.setattr(MainWindow, "_offer_runtime_setup", lambda self: None)
    previous = theme.active_name()
    window = MainWindow()
    try:
        window.apply_theme("light")
        light = window.brand_label.pixmap().toImage()
        window.apply_theme("dark")
        dark = window.brand_label.pixmap().toImage()
        assert not window.windowIcon().isNull()
        assert not light.isNull() and not dark.isNull()
        assert light != dark
        # Both assets must have visible, opaque pixels, not an empty image.
        for rendered in (light, dark):
            assert any(rendered.pixelColor(x, y).alpha() > 0
                       for y in range(rendered.height()) for x in range(rendered.width()))
    finally:
        window.close()
        theme.set_active(previous)
        qt_app.setStyleSheet(theme.stylesheet())


def test_remote_page_uses_inline_theme_aware_mark():
    from hugmunn.core.webui import PAGE

    assert 'fill="currentColor"' in PAGE
    assert "__HUGMUNN_MARK__" not in PAGE
    assert "Two ravens with joined wings sheltering a radiant all-seeing eye" in PAGE
