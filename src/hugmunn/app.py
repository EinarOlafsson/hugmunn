"""Application entry point."""

from __future__ import annotations

import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .config import Settings
from .ui import theme
from .ui.main_window import MainWindow


def _warn_on_mixed_qt_bindings() -> None:
    """Say something if PySide6 is already loaded alongside our PyQt6.

    Two Qt bindings in one process is undefined behaviour: they each own a
    copy of the C++ runtime and neither knows about the other's widgets. It
    crashes in places that look unrelated -- an application-wide
    ``setStyleSheet`` walks every widget Qt knows about, and that is where it
    surfaced here, as a segfault with no Python traceback.
    
    hugmunn is installed into an environment shared with spaCR, which is
    PySide6, so a plugin or skill that imports spaCR is enough to do it.
    Warning rather than refusing: it does not always crash, and taking the
    app away from somebody mid-conversation over a maybe is worse.
    """
    if "PySide6" in sys.modules:
        print(
            "warning: PySide6 is loaded in this process alongside PyQt6.\n"
            "         Two Qt bindings share no C++ runtime and crash in "
            "unrelated places.\n"
            "         Something imported it -- a plugin, or spaCR. Run "
            "hugmunn in its own\n"
            "         environment if you see unexplained crashes.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    _warn_on_mixed_qt_bindings()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("hugmunn")
    app.setApplicationDisplayName("hugmunn")

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
