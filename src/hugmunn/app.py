"""Application entry point."""

from __future__ import annotations

import argparse
import signal
import sys


def _warn_on_mixed_qt_bindings() -> None:
    """Warn when incompatible Qt Python bindings share the application process.

    Plugins can import PyQt6 indirectly. Both bindings own Qt objects, and
    mixing them can cause crashes during rendering or application shutdown.
    """
    if "PyQt6" in sys.modules:
        print(
            "warning: PyQt6 is loaded in this process alongside PySide6.\n"
            "         Two Qt bindings share no C++ runtime and crash in "
            "unrelated places.\n"
            "         Something imported it -- a plugin, or spaCR. Run "
            "hugmunn in its own\n"
            "         environment if you see unexplained crashes.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    """Launch the desktop application and return Qt's exit status.

    Args:
        argv: Arguments including the executable name, or ``None`` to use
            ``sys.argv``. ``--help`` and ``--version`` work without a display.
            Other arguments are passed to Qt (for example ``-platform``).
    """
    from ._version import __version__

    args = list(argv if argv is not None else sys.argv)
    parser = argparse.ArgumentParser(description="Hugmunn desktop model client")
    parser.add_argument("--version", action="version", version=f"hugmunn {__version__}")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    options, qt_args = parser.parse_known_args(args[1:])

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .config import Settings
    from .ui import theme
    from .ui.branding import window_icon
    from .ui.main_window import MainWindow

    _warn_on_mixed_qt_bindings()
    app = QApplication([args[0] if args else "hugmunn", *qt_args])
    app.setApplicationName("hugmunn")
    app.setApplicationDisplayName("hugmunn")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Hugmunn")
    app.setDesktopFileName("hugmunn")

    # Resolve the theme before any widget is constructed: several of them read
    # colours in their constructors, and starting on dark then switching would
    # leave those first widgets holding the wrong palette.
    theme.set_active(Settings.load().theme)
    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(window_icon())

    window = MainWindow()
    window.show()

    if options.smoke_test:
        # Exercise the installed resources and real widget tree without starting
        # model servers or entering the event loop that offers setup dialogs.
        from . import skills

        if window.windowIcon().isNull() or window.brand_label.pixmap().isNull():
            raise RuntimeError("Bundled application artwork is missing")
        if not skills():
            raise RuntimeError("Bundled skills are missing")
        window.close()
        return 0

    # Qt swallows SIGINT unless the event loop yields to Python periodically,
    # so Ctrl+C from a terminal would otherwise do nothing.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    idle = QTimer()
    idle.start(250)
    idle.timeout.connect(lambda: None)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
