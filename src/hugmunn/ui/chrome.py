"""Rounded desktop chrome with native move/resize and a native-frame fallback."""
from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QPushButton, QWidget
from . import theme


class ChromeWindow(QMainWindow):
    def init_chrome(self):
        self._chrome_actions = self.menuBar().actions()
        self._chrome_menus = [action.menu() for action in self._chrome_actions]
        self.setObjectName('hugmunnWindow')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.menuBar().installEventFilter(self)
        self.window_controls = QWidget()
        row = QHBoxLayout(self.window_controls)
        row.setContentsMargins(4, 0, 8, 0)
        row.setSpacing(4)
        for text, label, fn in [('−', 'Minimize', self.showMinimized),
                                ('□', 'Maximize or restore', self.toggle_maximized),
                                ('×', 'Close', self.close)]:
            button = QPushButton(text)
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setFixedSize(32, 28)
            button.setStyleSheet("padding: 0; font-size: 18px;")
            button.clicked.connect(fn)
            row.addWidget(button)
        self.menuBar().setCornerWidget(self.window_controls)

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def apply_appearance(self, *, panel_opacity=None, window_opacity=None, rounded=None, save=True):
        if not hasattr(self, 'window_controls'):
            self.init_chrome()
        s = self.settings
        if panel_opacity is not None:
            s.panel_opacity = max(50, min(100, int(panel_opacity)))
        if window_opacity is not None:
            s.window_opacity = max(50, min(100, int(window_opacity)))
        if rounded is not None:
            s.rounded_windows = bool(rounded)
        visible, state = self.isVisible(), self.windowState()
        if bool(self.windowFlags() & Qt.WindowType.FramelessWindowHint) != s.rounded_windows:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, s.rounded_windows)
            self.setWindowState(state)
            if visible:
                self.show()
        self.window_controls.setVisible(s.rounded_windows)
        self.setWindowOpacity(max(.5, min(1., s.window_opacity / 100)))
        theme.set_panel_opacity(s.panel_opacity / 100)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(theme.stylesheet())
        self.update()
        if save:
            s.save()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = theme.active()
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(p['page']))
        gradient.setColorAt(.5, QColor(p['surface_hi'] if theme.active_name() in ('glass', 'cell') else p['page']))
        gradient.setColorAt(1, QColor(p['page']))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(p['border']), 1))
        radius = 18 if self.settings.rounded_windows and not self.isMaximized() else 0
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), radius, radius)

    def eventFilter(self, obj, event):
        if obj is self.menuBar() and self.settings.rounded_windows:
            if event.type() == QEvent.Type.MouseButtonDblClick and not obj.actionAt(event.position().toPoint()):
                self.toggle_maximized()
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if not obj.actionAt(event.position().toPoint()) and self.windowHandle():
                    self.windowHandle().startSystemMove()
                    return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if self.settings.rounded_windows and not self.isMaximized() and event.button() == Qt.MouseButton.LeftButton:
            pos, edges = event.position(), Qt.Edge(0)
            if pos.x() < 12: edges |= Qt.Edge.LeftEdge
            if pos.x() > self.width() - 12: edges |= Qt.Edge.RightEdge
            if pos.y() < 8: edges |= Qt.Edge.TopEdge
            if pos.y() > self.height() - 12: edges |= Qt.Edge.BottomEdge
            if edges and self.windowHandle():
                self.windowHandle().startSystemResize(edges)
                return
        super().mousePressEvent(event)
