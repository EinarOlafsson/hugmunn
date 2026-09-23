"""Read the bundled raven artwork without importing Qt.

Resources are addressed inside the installed package so the desktop app,
remote page, and frozen installers use the same artwork as a source checkout.
"""

from importlib.resources import files
from typing import Literal


def svg(kind: Literal["mark", "horizontal"] = "mark", *, light: bool = False) -> str:
    """Return black artwork for a light surface, or white for a dark surface.

    Args:
        kind: Raven mark or horizontal wordmark.
        light: Whether the background is light.

    Raises:
        ValueError: If ``kind`` does not name bundled artwork.
    """
    if kind not in {"mark", "horizontal"}:
        raise ValueError(f"Unknown artwork: {kind!r}")
    colour = "black" if light else "white"
    return (files("hugmunn") / "resources" / "icons" /
            f"hugmunn-{kind}-{colour}.svg").read_text(encoding="utf-8")
