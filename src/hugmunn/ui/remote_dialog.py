"""Local-only login and network configuration. Secrets never enter chat history."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout)
from ..core.remote import Credentials, tunnel_options


class RemoteDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Remote access')
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        form = QFormLayout()
        credentials = Credentials.load()
        self.username = QLineEdit(credentials.username if credentials else 'hugmunn')
        self.password = QLineEdit(window._remote_password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Leave blank to keep the existing password')
        form.addRow('Username', self.username)
        form.addRow('Password', self.password)
        show = QCheckBox('Show password')
        show.toggled.connect(lambda checked: self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        form.addRow('', show)
        self.host = QComboBox()
        self.host.addItem('This machine / HTTPS tunnel', '127.0.0.1')
        self.host.addItem('Local network', '0.0.0.0')
        self.host.setCurrentIndex(max(0, self.host.findData(window.settings.remote_host)))
        form.addRow('Reachable from', self.host)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(window.settings.remote_port)
        form.addRow('Port', self.port)
        self.cert = QLineEdit(window.settings.remote_certfile)
        self.key = QLineEdit(window.settings.remote_keyfile)
        self.cert.setPlaceholderText('Optional certificate PEM path')
        self.key.setPlaceholderText('Optional private key PEM path')
        form.addRow('HTTPS certificate', self.cert)
        form.addRow('HTTPS key', self.key)
        layout.addLayout(form)
        note = QLabel('The initial login is generated for you. Save it in your password manager. '
                      'For access outside a trusted network, use HTTPS or a private tunnel.')
        note.setWordWrap(True)
        layout.addWidget(note)
        apply = QPushButton('Save login and start / restart')
        apply.setObjectName('primary')
        apply.clicked.connect(self.apply)
        layout.addWidget(apply)
        stop = QPushButton('Stop remote access')
        stop.clicked.connect(lambda: (window.stop_remote(), self.refresh()))
        layout.addWidget(stop)
        self.tunnels = QLabel()
        self.tunnels.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.tunnels.setWordWrap(True)
        layout.addWidget(self.tunnels)
        self.refresh()

    def refresh(self):
        w = self.window
        server = w._remote
        url = server.url() if server else 'Stopped'
        if server and server.host == '0.0.0.0':
            url += f'\nFrom another device: use this computer’s LAN IP and port {server.port}.'
        self.status.setText(url)
        self.tunnels.setText('Tunnel commands (run on this computer):\n' + '\n'.join(
            option['command'] for option in tunnel_options(w.settings.remote_port)))

    def apply(self):
        w = self.window
        try:
            current = Credentials.load()
            if self.password.text():
                current = Credentials.create(self.username.text(), self.password.text())
            elif current is None or current.username != self.username.text().strip():
                raise ValueError('Enter a password when changing the username.')
            if bool(self.cert.text().strip()) != bool(self.key.text().strip()):
                raise ValueError('Provide both the HTTPS certificate and key.')
            current.save()
            w._remote_password = self.password.text() or w._remote_password
            w.settings.remote_host = self.host.currentData()
            w.settings.remote_port = self.port.value()
            w.settings.remote_certfile = self.cert.text().strip()
            w.settings.remote_keyfile = self.key.text().strip()
            w.settings.save()
            w.stop_remote()
            result = w.start_remote()
            if w._remote is None:
                raise ValueError(result)
            self.refresh()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Remote access', str(exc))
