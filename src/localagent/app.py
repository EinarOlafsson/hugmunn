"""Application entry point."""

from __future__ import annotations

import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .config import Settings
from .ui import theme
from .ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("localagent")
    app.setApplicationDisplayName("localagent")

    # Resolve the theme before any widget is constructed: several of them read
    # colours in their constructors, and starting on dark then switching would
    # leave those first widgets holding the wrong palette.
    theme.set_active(Settings.load().theme)
    app.setStyleSheet(theme.stylesheet())

    window = MainWindow()
    window.show()

    # Qt swallows SIGINT unless the event loop yields to Python periodically,
    # so Ctrl+C from a terminal would otherwise do nothing.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    idle = QTimer()
    idle.start(250)
    idle.timeout.connect(lambda: None)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
