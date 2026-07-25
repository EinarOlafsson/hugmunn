"""Dark theme. One place to change colours."""

from __future__ import annotations

BG = "#16181d"
BG_RAISED = "#1e2127"
BG_INPUT = "#242830"
BORDER = "#2f343d"
TEXT = "#dde1e7"
TEXT_DIM = "#8b93a1"
ACCENT = "#6aa6ff"
USER = "#2a3446"
OK = "#5ec27a"
WARN = "#e0b341"
ERR = "#e0685f"

CODE_BG = "#12141a"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 14px;
}}
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}

QLabel#heading {{ color: {TEXT_DIM}; font-size: 11px; font-weight: 600;
                  text-transform: uppercase; letter-spacing: 1px; }}
QLabel#status  {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#blurb   {{ color: {TEXT_DIM}; font-size: 11px; }}

QFrame#sidebar {{ background: {BG_RAISED}; border-right: 1px solid {BORDER}; }}

QComboBox, QLineEdit, QSpinBox {{
    background: {BG_INPUT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 8px;
}}
QComboBox:hover, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {BG_INPUT}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT}; selection-color: #10131a;
}}

QPushButton {{
    background: {BG_INPUT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}
QPushButton#primary {{ background: {ACCENT}; color: #10131a; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #7fb4ff; }}
QPushButton#primary:disabled {{ background: {BG_INPUT}; color: {TEXT_DIM}; }}
QPushButton#danger {{ border-color: {ERR}; color: {ERR}; }}

QTextEdit#composer {{
    background: {BG_INPUT}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 8px;
}}
QTextEdit#composer:focus {{ border-color: {ACCENT}; }}

QTextBrowser {{ background: transparent; border: none; }}

QFrame#userMsg {{ background: {USER}; border-radius: 10px; }}
QFrame#toolCard {{ background: {BG_RAISED}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#thinkCard {{ background: {BG_RAISED}; border: 1px solid {BORDER}; border-radius: 8px; }}

QToolButton {{ background: transparent; border: none; color: {TEXT_DIM};
               font-size: 12px; text-align: left; padding: 2px; }}
QToolButton:hover {{ color: {TEXT}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}

QCheckBox {{ spacing: 8px; }}
QSplitter::handle {{ background: {BORDER}; width: 1px; }}
"""

# Injected into every rendered message so code blocks and tables look right.
DOC_CSS = f"""
body {{ color: {TEXT}; font-size: 14px; line-height: 1.55; }}
p {{ margin: 0 0 10px 0; }}
h1,h2,h3,h4 {{ margin: 14px 0 8px 0; color: {TEXT}; }}
h1 {{ font-size: 19px; }} h2 {{ font-size: 17px; }} h3 {{ font-size: 15px; }}
code {{ background: {CODE_BG}; padding: 1px 5px; border-radius: 4px;
        font-family: 'JetBrains Mono','DejaVu Sans Mono',monospace; font-size: 13px; }}
pre {{ background: {CODE_BG}; border: 1px solid {BORDER}; border-radius: 8px;
       padding: 10px 12px; margin: 8px 0; }}
pre code {{ background: transparent; padding: 0; }}
a {{ color: {ACCENT}; }}
ul,ol {{ margin: 0 0 10px 0; padding-left: 22px; }}
li {{ margin: 3px 0; }}
blockquote {{ border-left: 3px solid {BORDER}; margin: 8px 0; padding-left: 12px; color: {TEXT_DIM}; }}
table {{ border-collapse: collapse; margin: 8px 0; }}
th,td {{ border: 1px solid {BORDER}; padding: 5px 9px; }}
th {{ background: {BG_RAISED}; }}
"""
