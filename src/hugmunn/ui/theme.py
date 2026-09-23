"""Colour palettes, contrast checks, and Qt stylesheets.

Adapted from spacr's theme system. Resolve colours through :func:`active` when
constructing or repainting a widget so theme changes reach custom rendering.
All palettes share the same keys; :func:`failures` checks foreground contrast.
"""

from __future__ import annotations

from typing import Iterable

# --------------------------------------------------------------- palettes

DARK = {
    "bg": "#16181d",
    "page": "#1a1d23",
    "surface": "#1e2127",
    "surface_alt": "#242830",
    "surface_hi": "#2b3039",
    "border": "#2f343d",
    "border_soft": "#242830",
    "fg": "#dde1e7",
    "fg_muted": "#a8b0bd",
    "fg_dim": "#8b93a1",
    "accent": "#6aa6ff",
    "accent_hi": "#7fb4ff",
    "accent_lo": "#4a86df",
    "accent_soft": "#233348",
    "user": "#2a3446",
    "code_bg": "#12141a",
    "success": "#5ec27a",
    "warning": "#e0b341",
    "error": "#e0685f",
    "on_accent": "#10131a",
    "backdrop": "",
}

# Mirrors DARK key for key. Hover goes *darker* here rather than brighter,
# which is the correction spaCR's light palette needed: mirroring a dark
# palette literally puts the hover state below AA on its own surface.
LIGHT = {
    "bg": "#fafafa",
    "page": "#eef0f3",
    "surface": "#ffffff",
    "surface_alt": "#f2f4f7",
    "surface_hi": "#e6e9ee",
    "border": "#d5d9df",
    "border_soft": "#e5e8ec",
    "fg": "#12141a",
    "fg_muted": "#4b5460",
    "fg_dim": "#5d6673",
    "accent": "#0a63c4",
    "accent_hi": "#0851a3",
    "accent_lo": "#063d7a",
    "accent_soft": "#dbe8fb",
    "user": "#dbe8fb",
    "code_bg": "#f2f4f7",
    "success": "#0f7030",
    "warning": "#8f4e00",
    "error": "#b81d1a",
    "on_accent": "#ffffff",
    "backdrop": "",
}

# Neutral translucent material over a bounded light field. Qt cannot sample
# the pixels behind a widget, so the glass is layered rather than refracted:
# a gradient page, low-alpha charcoal surfaces, tint reserved for actions.
GLASS = {
    "bg": "#0b0d11",
    "page": "#0b0d11",
    "surface": "#25272c",
    "surface_alt": "#2d3036",
    "surface_hi": "#3a3e45",
    "border": "#8e939b",
    "border_soft": "#5b6069",
    "fg": "#fafafa",
    "fg_muted": "#d5d6d9",
    "fg_dim": "#b3b6bc",
    "accent": "#8cc8ff",
    "accent_hi": "#b8dcff",
    "accent_lo": "#579fe0",
    "accent_soft": "#263746",
    "user": "#2f3a47",
    "code_bg": "#1a1c20",
    "success": "#78dfa3",
    "warning": "#f5cf72",
    "error": "#ff9a95",
    "on_accent": "#0b0d11",
    "backdrop": (
        "qlineargradient(x1:0, y1:0, x2:1, y2:1, "
        "stop:0 #171a1f, stop:0.45 #24282f, stop:1 #12151a)"
    ),
}

# Re-hued to the microscopy spaCR is for: the microtubule network is cyan and
# the filopodia run cyan-to-green, so the accent follows the imagery rather
# than fighting it with blue.
CELL = {
    "bg": "#02080b",
    "page": "#061218",
    "surface": "#0a1a21",
    "surface_alt": "#0f2530",
    "surface_hi": "#16323f",
    "border": "#2d5768",
    "border_soft": "#1d3a46",
    "fg": "#ffffff",
    "fg_muted": "#cfe0e6",
    "fg_dim": "#9fb8c2",
    "accent": "#8fe3f7",
    "accent_hi": "#b9f0ff",
    "accent_lo": "#4fb3cf",
    "accent_soft": "#123742",
    "user": "#123742",
    "code_bg": "#04121a",
    "success": "#6fe39a",
    "warning": "#f2ca5c",
    "error": "#ff8f86",
    "on_accent": "#02080b",
    "backdrop": (
        "qlineargradient(x1:0, y1:0, x2:1, y2:1, "
        "stop:0 #041016, stop:0.5 #0a222c, stop:1 #030d12)"
    ),
}

# ---------------------------------------------------------------- imitations
#
# Two families that copy a familiar app rather than expressing a taste of
# their own. Both ship light and dark, because that is the pairing each is
# actually recognised by -- Claude reads as the warm cream page, ChatGPT as
# the near-black column -- and offering only one half of each would look like
# a mistake rather than a choice.
#
# The hues are matched to the originals; the *values* are then adjusted where
# they had to be, because these palettes drive a different layout and every
# one still has to clear AA on every surface it can appear on. Where a colour
# moved, it moved as little as the rule allowed.

CLAUDE = {
    "bg": "#262624",
    "page": "#1f1e1d",
    "surface": "#30302e",
    "surface_alt": "#3a3a37",
    "surface_hi": "#454542",
    "border": "#4a4a46",
    "border_soft": "#383835",
    "fg": "#f5f4ef",
    "fg_muted": "#d6d3c8",
    "fg_dim": "#b0aca0",
    # The coral is the signature. It is lightened from the marketing value so
    # it clears 4.5:1 as *text* on these surfaces; the original is used at
    # large sizes and on fills, where 3:1 is the bar.
    "accent": "#e8a38f",
    "accent_hi": "#eb9a80",
    "accent_lo": "#c8704e",
    "accent_soft": "#43322c",
    "user": "#3a3a37",
    "code_bg": "#1c1b1a",
    "success": "#7fb98a",
    "warning": "#d8a659",
    "error": "#e88b7d",
    "on_accent": "#1f1e1d",
    "backdrop": "",
}

CLAUDE_LIGHT = {
    "bg": "#faf9f7",
    "page": "#f0eee6",
    "surface": "#ffffff",
    "surface_alt": "#f5f3ed",
    "surface_hi": "#eae7dd",
    "border": "#ddd9cd",
    "border_soft": "#eae7dd",
    "fg": "#1f1e1d",
    "fg_muted": "#4a4843",
    "fg_dim": "#5d5a53",
    "accent": "#a6431d",
    "accent_hi": "#933a18",
    "accent_lo": "#722d12",
    "accent_soft": "#f7e6df",
    "user": "#f0eee6",
    "code_bg": "#f5f3ed",
    "success": "#2f6b3a",
    "warning": "#7a5310",
    "error": "#a8332a",
    "on_accent": "#ffffff",
    "backdrop": "",
}

CHATGPT = {
    "bg": "#212121",
    "page": "#171717",
    "surface": "#2f2f2f",
    "surface_alt": "#383838",
    "surface_hi": "#424242",
    "border": "#4d4d4d",
    "border_soft": "#383838",
    "fg": "#ececec",
    "fg_muted": "#c5c5c5",
    "fg_dim": "#a0a0a0",
    "accent": "#2bc787",
    "accent_hi": "#4ad19a",
    "accent_lo": "#188f5f",
    "accent_soft": "#1b3a2e",
    "user": "#303030",
    "code_bg": "#171717",
    "success": "#19c37d",
    "warning": "#e0b341",
    "error": "#ef6b62",
    "on_accent": "#0d0d0d",
    "backdrop": "",
}

CHATGPT_LIGHT = {
    "bg": "#ffffff",
    "page": "#f9f9f9",
    "surface": "#ffffff",
    "surface_alt": "#f4f4f4",
    "surface_hi": "#ececec",
    "border": "#e3e3e3",
    "border_soft": "#f0f0f0",
    "fg": "#0d0d0d",
    "fg_muted": "#4a4a4a",
    "fg_dim": "#5d5d5d",
    "accent": "#0f7a52",
    "accent_hi": "#0b5e3f",
    "accent_lo": "#084430",
    "accent_soft": "#e3f4ec",
    "user": "#f4f4f4",
    "code_bg": "#f4f4f4",
    "success": "#0f7a52",
    "warning": "#7a5310",
    "error": "#b3261e",
    "on_accent": "#ffffff",
    "backdrop": "",
}

THEMES = ("dark", "light", "glass", "cell",
          "claude", "claude-light", "chatgpt", "chatgpt-light")

LABELS = {
    "dark": "Dark",
    "light": "Light",
    "glass": "Glass",
    "cell": "Cell",
    "claude": "Claude",
    "claude-light": "Claude light",
    "chatgpt": "ChatGPT",
    "chatgpt-light": "ChatGPT light",
    "system": "Match the system",
}

BLURBS = {
    "dark": "The default. Near-black surfaces, blue accent.",
    "light": "For a bright room. Same structure, hover goes darker.",
    "glass": "Layered neutral material over a soft gradient. Tint only on actions.",
    "cell": "Deep teal over a micrograph gradient, cyan accent. From spaCR.",
    "claude": "Warm greys and the coral accent, after Claude's dark theme.",
    "claude-light": "The cream page Claude is recognised by.",
    "chatgpt": "Near-black column and green accent, after ChatGPT's dark theme.",
    "chatgpt-light": "ChatGPT's white page and light grey chrome.",
    "system": "Follows the desktop's light or dark preference at startup.",
}

_PALETTES = {
    "dark": DARK, "light": LIGHT, "glass": GLASS, "cell": CELL,
    "claude": CLAUDE, "claude-light": CLAUDE_LIGHT,
    "chatgpt": CHATGPT, "chatgpt-light": CHATGPT_LIGHT,
}

_active = "dark"


def palette_for(name: str = "dark") -> dict:
    """The palette dict for ``name``; anything unknown resolves to dark.

    ``"system"`` is a *preference* value, not a palette — resolve it with
    :func:`resolve` before calling this.
    """
    return dict(_PALETTES.get(name, DARK))


def resolve(name: str) -> str:
    """Turn a stored preference into a real theme name.

    ``"system"`` asks Qt what the desktop is set to. Qt only started
    reporting that in 6.5, and a headless test has no application at all,
    so anything unanswerable falls back to dark — which is what the app
    looked like before themes existed.
    """
    if name != "system":
        return name if name in _PALETTES else "dark"
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return "dark"
        scheme = app.styleHints().colorScheme()
        return "light" if scheme == Qt.ColorScheme.Light else "dark"
    except Exception:
        return "dark"


def set_active(name: str) -> str:
    """Record the theme that is on screen. Returns the resolved name."""
    global _active
    _active = resolve(name)
    return _active


def active_name() -> str:
    return _active


def active() -> dict:
    """The palette on screen right now.

    What a widget wants. Resolve colours through this at construction or
    paint time — never capture one into a module-level constant, which is
    the bug that produced dark chrome on spaCR's light theme.
    """
    return palette_for(_active)


# ------------------------------------------------------------ colour maths


def _channels(colour: str) -> tuple[int, int, int]:
    text = colour.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        raise ValueError(f"not a #rrggbb colour: {colour!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _linear(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: str) -> float:
    r, g, b = _channels(colour)
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast between two colours — 1.0 (identical) to 21.0."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def composite(top: str, alpha: float, under: str) -> str:
    alpha = max(0.0, min(1.0, float(alpha)))
    return "#%02x%02x%02x" % tuple(
        int(round(alpha * t + (1.0 - alpha) * u))
        for t, u in zip(_channels(top), _channels(under))
    )


def css(colour: str, alpha: float = 1.0) -> str:
    """Render for QSS — plain hex when opaque, ``rgba()`` when not."""
    if alpha >= 1.0:
        return colour
    r, g, b = _channels(colour)
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


#: ``(foreground, surface, minimum ratio)``. 4.5:1 is AA for body text;
#: 3.0:1 is AA for large text and non-text UI components (WCAG 1.4.11),
#: which is the right tier for dim hint text and the status hues.
_SURFACES = ("bg", "page", "surface", "surface_alt", "surface_hi")

RULES: tuple[tuple[str, str, float], ...] = tuple(
    [(fg, s, 4.5) for fg in ("fg", "fg_muted", "accent") for s in _SURFACES]
    + [(fg, s, 3.0) for fg in ("fg_dim", "success", "warning", "error") for s in _SURFACES]
    + [
        ("accent", "accent_soft", 4.5),
        ("on_accent", "accent", 4.5),
        ("on_accent", "accent_lo", 4.5),
        ("fg", "user", 4.5),
        ("fg", "code_bg", 4.5),
    ]
)


def failures(name: str) -> list[str]:
    """Every contrast rule ``name`` does not meet. Empty is the goal.

    Run over all four themes by the test suite. A palette that fails here
    is not a taste question — it is text somebody cannot read.
    """
    palette = palette_for(name)
    out = []
    for fg, surface, required in RULES:
        ratio = contrast_ratio(palette[fg], palette[surface])
        if ratio < required:
            out.append(
                f"{name}: {fg} on {surface} is {ratio:.2f}:1, needs {required:.1f}:1"
            )
    return out


def all_failures(names: Iterable[str] = THEMES) -> list[str]:
    return [line for name in names for line in failures(name)]


# ------------------------------------------------------------- stylesheet


def stylesheet(name: str | None = None) -> str:
    """The full QSS for a theme.

    ``None`` means the active one. Emitted from the palette rather than
    written out per theme, so adding a palette is the whole of adding a
    theme.
    """
    p = palette_for(resolve(name) if name else _active)
    page = p["backdrop"] or p["page"]
    return f"""
/* No background here. A blanket QWidget rule paints every widget, labels
   included, so a label inside a raised panel draws a window-coloured box
   behind its text -- every piece of text in the app carrying a rectangle of
   the wrong colour. Containers are painted explicitly below; everything else
   inherits what it sits on. */
QWidget {{ color: {p['fg']}; font-size: 14px; }}
QMainWindow, QDialog {{ background: {page}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* Passive widgets: never their own colour, always their container's. */
QLabel, QCheckBox, QRadioButton, QGroupBox, QSplitter, QScrollBar,
QDialogButtonBox, QTabWidget, QTabWidget::pane, QStackedWidget {{
    background: transparent;
}}

/* Text views inside a card are part of the card, not fields on top of it. */
QFrame#toolCard QPlainTextEdit, QFrame#thinkCard QPlainTextEdit,
QFrame#toolCard QTextBrowser, QFrame#thinkCard QTextBrowser,
QFrame#userMsg QLabel {{
    background: transparent; border: none;
}}

QLabel#heading {{ color: {p['fg_dim']}; font-size: 11px; font-weight: 600;
                  text-transform: uppercase; letter-spacing: 1px; }}
QLabel#status  {{ color: {p['fg_dim']}; font-size: 12px; }}
QLabel#blurb   {{ color: {p['fg_dim']}; font-size: 11px; }}
QLabel#cloud   {{ color: {p['warning']}; font-size: 11px; font-weight: 600; }}

QFrame#sidebar {{ background: {p['surface']}; border-right: 1px solid {p['border']}; }}

QComboBox, QLineEdit, QSpinBox {{
    background: {p['surface_alt']}; border: 1px solid {p['border']};
    border-radius: 6px; padding: 6px 8px; color: {p['fg']};
}}
QComboBox:hover, QLineEdit:focus {{ border-color: {p['accent']}; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {p['fg_dim']}; }}
QComboBox QAbstractItemView {{
    background: {p['surface_alt']}; border: 1px solid {p['border']};
    color: {p['fg']};
    selection-background-color: {p['accent']}; selection-color: {p['on_accent']};
}}

QPushButton {{
    background: {p['surface_alt']}; border: 1px solid {p['border']};
    border-radius: 6px; padding: 7px 14px; color: {p['fg']};
}}
QPushButton:hover {{ border-color: {p['accent']}; background: {p['surface_hi']}; }}
QPushButton:disabled {{ color: {p['fg_dim']}; border-color: {p['border_soft']}; }}
QPushButton#primary {{ background: {p['accent']}; color: {p['on_accent']};
                       border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {p['accent_hi']}; }}
QPushButton#primary:disabled {{ background: {p['surface_alt']}; color: {p['fg_dim']}; }}
QPushButton#danger {{ border-color: {p['error']}; color: {p['error']}; }}

QTextEdit#composer {{
    background: {p['surface_alt']}; border: 1px solid {p['border']};
    border-radius: 8px; padding: 8px; color: {p['fg']};
}}
QTextEdit#composer:focus {{ border-color: {p['accent']}; }}
QPlainTextEdit {{ background: {p['surface_alt']}; border: 1px solid {p['border']};
                  border-radius: 6px; color: {p['fg']}; }}

QTextBrowser {{ background: transparent; border: none; }}

QFrame#userMsg {{ background: {p['user']}; border-radius: 10px; }}
QFrame#toolCard, QFrame#thinkCard {{
    background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 8px;
}}

QToolButton {{ background: transparent; border: none; color: {p['fg_dim']};
               font-size: 12px; text-align: left; padding: 2px; }}
QToolButton:hover {{ color: {p['fg']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['fg_dim']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {p['border']}; border-radius: 5px; min-width: 30px; }}

QCheckBox {{ spacing: 8px; }}
QSplitter::handle {{ background: {p['border']}; width: 1px; }}
QProgressBar {{ background: {p['surface_alt']}; border: 1px solid {p['border']};
                border-radius: 6px; text-align: center; color: {p['fg']}; }}
QProgressBar::chunk {{ background: {p['accent']}; border-radius: 5px; }}

QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {p['fg_dim']};
                padding: 7px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p['fg']}; border-bottom-color: {p['accent']}; }}

QMenu {{ background: {p['surface_alt']}; border: 1px solid {p['border']}; color: {p['fg']}; }}
QMenuBar {{ background: {p['surface']}; color: {p['fg']}; }}
QMenuBar::item {{ background: transparent; padding: 4px 8px; }}
QMenuBar::item:selected {{ background: {p['accent']}; color: {p['on_accent']}; }}
QMenu::item:selected {{ background: {p['accent']}; color: {p['on_accent']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}
QToolTip {{ background: {p['surface_alt']}; color: {p['fg']};
            border: 1px solid {p['border']}; padding: 4px; }}
"""


def doc_css(name: str | None = None) -> str:
    """CSS injected into every rendered message, so code and tables match."""
    p = palette_for(resolve(name) if name else _active)
    return f"""
body {{ color: {p['fg']}; font-size: 14px; line-height: 1.55; }}
p {{ margin: 0 0 10px 0; }}
h1,h2,h3,h4 {{ margin: 14px 0 8px 0; color: {p['fg']}; }}
h1 {{ font-size: 19px; }} h2 {{ font-size: 17px; }} h3 {{ font-size: 15px; }}
code {{ background: {p['code_bg']}; padding: 1px 5px; border-radius: 4px;
        font-family: 'JetBrains Mono','DejaVu Sans Mono',monospace; font-size: 13px; }}
pre {{ background: {p['code_bg']}; border: 1px solid {p['border']}; border-radius: 8px;
       padding: 10px 12px; margin: 8px 0; }}
pre code {{ background: transparent; padding: 0; }}
a {{ color: {p['accent']}; }}
ul,ol {{ margin: 0 0 10px 0; padding-left: 22px; }}
li {{ margin: 3px 0; }}
blockquote {{ border-left: 3px solid {p['border']}; margin: 8px 0;
              padding-left: 12px; color: {p['fg_dim']}; }}
table {{ border-collapse: collapse; margin: 8px 0; }}
th,td {{ border: 1px solid {p['border']}; padding: 5px 9px; }}
th {{ background: {p['surface']}; }}
"""
