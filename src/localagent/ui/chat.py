"""Transcript widgets: user bubbles, streaming assistant blocks, tool cards."""

from __future__ import annotations

import html as html_mod

import markdown
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QTextBrowser,
    QToolButton, QVBoxLayout, QWidget,
)

from . import style

_MD_EXTENSIONS = ["fenced_code", "codehilite", "tables", "sane_lists", "nl2br"]
_MD_CONFIG = {
    # noclasses inlines the Pygments colours; QTextBrowser's CSS support is too
    # limited to rely on a stylesheet-driven highlight.
    "codehilite": {"noclasses": True, "pygments_style": "monokai", "guess_lang": False},
}


def render_markdown(text: str) -> str:
    try:
        body = markdown.markdown(text, extensions=_MD_EXTENSIONS, extension_configs=_MD_CONFIG)
    except Exception:  # noqa: BLE001 - never let a rendering bug lose the reply
        body = f"<pre>{html_mod.escape(text)}</pre>"
    return f"<style>{style.DOC_CSS}</style>{body}"


class _AutoBrowser(QTextBrowser):
    """QTextBrowser that grows to fit its content instead of scrolling."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().documentLayout().documentSizeChanged.connect(self._resize_to_fit)

    def _resize_to_fit(self) -> None:
        height = int(self.document().size().height()) + 8
        self.setFixedHeight(max(24, height))

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.document().setTextWidth(self.viewport().width())
        self._resize_to_fit()


class UserBubble(QFrame):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("userMsg")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)


class _Collapsible(QFrame):
    """Header button that folds a body widget away. Collapsed by default."""

    def __init__(self, title: str, object_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setText(title)
        self._toggle.clicked.connect(self._on_toggle)
        outer.addWidget(self._toggle)

        self.body = _AutoBrowser()
        self.body.setVisible(False)
        outer.addWidget(self.body)

    def _on_toggle(self, checked: bool) -> None:
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.body.setVisible(checked)

    def set_title(self, title: str) -> None:
        self._toggle.setText(title)


class ThinkingCard(_Collapsible):
    """Chain of thought. Folded away by default — it is long and rarely wanted."""

    def __init__(self, parent=None) -> None:
        super().__init__("Thinking…", "thinkCard", parent)
        self._raw: list[str] = []

    def append(self, text: str) -> None:
        self._raw.append(text)
        if self.body.isVisible():
            self.body.setPlainText("".join(self._raw))

    def finish(self) -> None:
        joined = "".join(self._raw)
        words = len(joined.split())
        self.set_title(f"Thought for {words} words" if words else "Thinking")
        self.body.setPlainText(joined)
        if not joined.strip():
            self.setVisible(False)


class ToolCard(QFrame):
    """One tool invocation: what was called, and what it returned."""

    STATE_ICON = {"running": "◌", "ok": "●", "denied": "✕", "error": "▲"}
    STATE_COLOR = {
        "running": style.TEXT_DIM, "ok": style.OK,
        "denied": style.WARN, "error": style.ERR,
    }

    def __init__(self, name: str, summary: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("toolCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._icon = QLabel(self.STATE_ICON["running"])
        self._icon.setStyleSheet(f"color: {style.TEXT_DIM};")
        header.addWidget(self._icon)

        header.addWidget(QLabel(f"<b>{html_mod.escape(name)}</b>"))

        self._summary = QLabel(html_mod.escape(summary[:160]))
        self._summary.setObjectName("blurb")
        self._summary.setWordWrap(True)
        header.addWidget(self._summary, 1)
        outer.addLayout(header)

        self._detail = _Collapsible("Output", "toolCard")
        self._detail.setStyleSheet("QFrame#toolCard { border: none; background: transparent; }")
        self._detail.setVisible(False)
        outer.addWidget(self._detail)

    def _set_state(self, state: str) -> None:
        self._icon.setText(self.STATE_ICON[state])
        self._icon.setStyleSheet(f"color: {self.STATE_COLOR[state]};")

    def set_result(self, output: str) -> None:
        self._set_state("error" if output.startswith("Error:") else "ok")
        lines = output.count("\n") + 1
        self._detail.set_title(f"Output · {lines} lines")
        self._detail.body.setPlainText(output)
        self._detail.setVisible(True)

    def set_denied(self) -> None:
        self._set_state("denied")
        self._summary.setText(self._summary.text() + "  — denied")


class AssistantBlock(QWidget):
    """Streaming markdown. Re-renders on a timer so tokens don't thrash layout."""

    RENDER_INTERVAL_MS = 90

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = _AutoBrowser()
        layout.addWidget(self._view)

        self._raw: list[str] = []
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.RENDER_INTERVAL_MS)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    def append(self, text: str) -> None:
        self._raw.append(text)
        self._dirty = True

    def _flush(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        self._view.setHtml(render_markdown("".join(self._raw)))

    def finish(self) -> None:
        self._timer.stop()
        self._flush()
        if not "".join(self._raw).strip():
            self.setVisible(False)

    @property
    def text(self) -> str:
        return "".join(self._raw)


class Transcript(QScrollArea):
    """Vertical stack of message widgets with sticky auto-scroll."""

    STICKY_PX = 90  # how close to the bottom still counts as "following along"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        self._layout.setContentsMargins(20, 16, 20, 16)
        self._layout.setSpacing(12)
        self._layout.addStretch(1)
        self.setWidget(self._inner)

    def add(self, widget: QWidget) -> QWidget:
        at_bottom = self._at_bottom()
        self._layout.insertWidget(self._layout.count() - 1, widget)
        if at_bottom:
            QTimer.singleShot(0, self.scroll_to_bottom)
        return widget

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - self.STICKY_PX

    def scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def follow(self) -> None:
        """Keep the view pinned while streaming, unless the user scrolled up."""
        if self._at_bottom():
            self.scroll_to_bottom()

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


class Notice(QLabel):
    def __init__(self, text: str, colour: str = style.TEXT_DIM, parent=None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setStyleSheet(f"color: {colour}; font-size: 12px; padding: 4px 2px;")
