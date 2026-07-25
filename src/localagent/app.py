"""Application entry point."""

from __future__ import annotations

import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.style import STYLESHEET


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("localagent")
    app.setApplicationDisplayName("localagent")
    app.setStyleSheet(STYLESHEET)

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
