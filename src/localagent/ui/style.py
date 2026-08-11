"""Live colour names, resolved against whatever theme is on screen.

This module used to hold the colours. It now forwards to :mod:`theme`, which
holds four palettes, and it does so through PEP 562 ``__getattr__`` so that
``style.ACCENT`` is a *lookup* rather than a constant. That distinction is the
whole point: a constant captured into an f-string at import time keeps the dark
theme's value forever, which is how spaCR's light theme ended up drawing
near-black panels on a near-white page.

So the rule for call sites is: read these inside a constructor or a paint
handler, never at module scope and never as a default argument value. Widgets
are rebuilt when the theme changes, so a per-construction read is enough.
"""

from __future__ import annotations

from . import theme

# style name -> palette key
_ALIASES = {
    "BG": "bg",
    "BG_RAISED": "surface",
    "BG_INPUT": "surface_alt",
    "BORDER": "border",
    "TEXT": "fg",
    "TEXT_DIM": "fg_dim",
    "ACCENT": "accent",
    "USER": "user",
    "OK": "success",
    "WARN": "warning",
    "ERR": "error",
    "CODE_BG": "code_bg",
}


def __getattr__(name: str) -> str:
    """Resolve a colour name against the live palette."""
    key = _ALIASES.get(name)
    if key is not None:
        return theme.active()[key]
    if name == "STYLESHEET":
        return theme.stylesheet()
    if name == "DOC_CSS":
        return theme.doc_css()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*_ALIASES, "STYLESHEET", "DOC_CSS"])
