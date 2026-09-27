"""Search a large skill catalogue without loading it all into the model context."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QTextBrowser, QVBoxLayout)
from ..core import skills


class SkillsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Skills library')
        self.resize(800, 650)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search name, category or description…')
        layout.addWidget(self.search)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.list = QListWidget()
        layout.addWidget(self.list, 2)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        layout.addWidget(self.preview, 1)
        note = QLabel('Codex skills load their instructions when relevant. Some require tools, packages or accounts that must be set up separately.')
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        for text, fn in [('Import installed Codex skills', self.import_local),
                         ('Import folder…', self.import_folder), ('Refresh', self.reload)]:
            button = QPushButton(text)
            button.clicked.connect(fn)
            row.addWidget(button)
        layout.addLayout(row)
        self.search.textChanged.connect(self.fill)
        self.list.itemChanged.connect(self.toggle)
        self.list.currentItemChanged.connect(self.show_skill)
        self.fill()

    def fill(self):
        query = self.search.text().casefold()
        self.list.blockSignals(True)
        self.list.clear()
        for skill in self.window._skills:
            if query not in f'{skill.name} {skill.category} {skill.description}'.casefold():
                continue
            item = QListWidgetItem(f'{skill.name} · {skill.category} · ~{skill.approx_tokens} tokens')
            item.setData(Qt.ItemDataRole.UserRole, skill.key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if skill.key in self.window._enabled_skills else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.summary.setText(f'{len(self.window._enabled_skills)} enabled · {len(self.window._skills)} available')

    def toggle(self, item):
        self.window._on_skill_toggled(item.data(Qt.ItemDataRole.UserRole), item.checkState() == Qt.CheckState.Checked)
        self.summary.setText(f'{len(self.window._enabled_skills)} enabled · {len(self.window._skills)} available')

    def show_skill(self, item, previous=None):
        if item is None:
            self.preview.clear()
            return
        skill = next(x for x in self.window._skills if x.key == item.data(Qt.ItemDataRole.UserRole))
        self.preview.setPlainText(f'{skill.description}\n\n{skill.source_path}\n\n{skill.body}')

    def reload(self):
        self.window._reload_skills()
        self.fill()

    def import_local(self):
        count = self.window.import_codex_skills()
        self.fill()
        self.summary.setText(f'Imported {count} new skills · {len(self.window._skills)} available')

    def import_folder(self):
        from pathlib import Path
        folder = QFileDialog.getExistingDirectory(self, 'Folder containing Codex skills')
        if folder:
            skills.import_directory(Path(folder))
            self.reload()
