"""Вкладка «Редактор конфигурации» — по-блочное редактирование YAML."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import (
    block_to_yaml,
    config_editor_blocks,
    load_config_dict,
    merge_block_from_yaml,
    save_config_dict,
    validate_config_dict,
)
from ui import layout_constants as lc
from ui.widgets import compact_layout, set_char_width


class YamlValuesHighlighter(QSyntaxHighlighter):
    """Элементы списков под ключом values: — как в редакторе, жирный, тёмно-синий."""

    _VALUES_KEY = re.compile(r"^(\s*)values:\s*(#.*)?$")
    _LIST_ITEM = re.compile(r"^(\s*)-\s+")
    _ANY_CONTENT = re.compile(r"^(\s*)\S")
    _VALUES_COLOR = QColor("#0D47A1")

    def __init__(self, document, base_font: QFont) -> None:
        super().__init__(document)
        self._values_fmt = QTextCharFormat()
        values_font = QFont(base_font)
        values_font.setBold(True)
        self._values_fmt.setFont(values_font)
        self._values_fmt.setForeground(self._VALUES_COLOR)

    def highlightBlock(self, text: str) -> None:
        prev = self.previousBlockState()
        values_indent = prev - 1 if prev > 0 else -1

        m_values = self._VALUES_KEY.match(text)
        if m_values:
            self.setCurrentBlockState(len(m_values.group(1)) + 1)
            return

        if values_indent < 0:
            self.setCurrentBlockState(0)
            return

        if not text.strip():
            self.setCurrentBlockState(values_indent + 1)
            return

        m_list = self._LIST_ITEM.match(text)
        if m_list and len(m_list.group(1)) >= values_indent:
            self.setFormat(0, len(text), self._values_fmt)
            self.setCurrentBlockState(values_indent + 1)
            return

        m_any = self._ANY_CONTENT.match(text)
        if m_any and len(m_any.group(1)) <= values_indent:
            self.setCurrentBlockState(0)
            return

        self.setCurrentBlockState(values_indent + 1)


class ConfigEditorWidget(QWidget):
    """Редактор блоков semantic и checklists в config.yaml."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_reload_config: Callable[[str], None] | None = None,
        get_active_config_path: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_reload_config = on_reload_config
        self._get_active_config_path = get_active_config_path

        self._file_path: str | None = None
        self._working: dict | None = None
        self._current_block_id: str | None = None
        self._block_dirty = False
        self._loading_block = False
        self._validated = False

        root = QVBoxLayout(self)
        compact_layout(root)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Файл:"))
        self._path_edit = QLineEdit()
        set_char_width(self._path_edit, lc.ENTRY_W_PATH_WIDE)
        toolbar.addWidget(self._path_edit, stretch=1)
        btn_browse = QPushButton("Обзор…")
        btn_browse.clicked.connect(self._browse_file)
        toolbar.addWidget(btn_browse)
        btn_load_current = QPushButton("Загрузить текущий")
        btn_load_current.clicked.connect(self._load_current)
        toolbar.addWidget(btn_load_current)
        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._save)
        toolbar.addWidget(btn_save)
        btn_save_as = QPushButton("Сохранить как…")
        btn_save_as.clicked.connect(self._save_as)
        toolbar.addWidget(btn_save_as)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._block_list = QListWidget()
        self._block_list.setMinimumWidth(220)
        self._block_list.currentRowChanged.connect(self._on_block_row_changed)
        splitter.addWidget(self._block_list)

        self._editor = QTextEdit()
        mono = QFont("Courier New")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(mono)
        YamlValuesHighlighter(self._editor.document(), mono)
        self._editor.textChanged.connect(self._on_editor_changed)
        splitter.addWidget(self._editor)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        bottom = QHBoxLayout()
        btn_apply = QPushButton("Применить")
        btn_apply.clicked.connect(self._apply)
        bottom.addWidget(btn_apply)
        bottom.addStretch(1)
        self._status = QLabel("Загрузите config.yaml.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {lc.COLOR_HINT};")
        bottom.addWidget(self._status, stretch=1)
        root.addLayout(bottom)

    def _set_status(self, text: str, *, ok: bool = False) -> None:
        self._status.setText(text)
        color = "#2e7d32" if ok else lc.COLOR_HINT
        self._status.setStyleSheet(f"color: {color};")

    def _browse_file(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self,
            "config.yaml",
            self._path_edit.text().strip(),
            "YAML (*.yaml *.yml);;Все файлы (*.*)",
        )
        if p:
            self._load_from_path(p)

    def _load_current(self) -> None:
        if self._get_active_config_path is None:
            return
        path = self._get_active_config_path().strip()
        if not path:
            QMessageBox.warning(self, "Конфиг", "Путь к текущей конфигурации не задан.")
            return
        self._load_from_path(path)

    def _load_from_path(self, path: str) -> None:
        if not self._confirm_discard_block_changes():
            return
        try:
            data = load_config_dict(path)
            validate_config_dict(data)
        except Exception as e:
            QMessageBox.critical(self, "Конфиг", str(e))
            return
        self._file_path = str(Path(path).resolve())
        self._path_edit.setText(self._file_path)
        self._working = copy.deepcopy(data)
        self._validated = True
        self._populate_blocks()
        self._set_status(f"Загружен: {self._file_path}", ok=True)

    def _populate_blocks(self) -> None:
        self._block_list.blockSignals(True)
        self._block_list.clear()
        if self._working is None:
            self._block_list.blockSignals(False)
            return
        blocks = config_editor_blocks(self._working)
        for block_id, label in blocks:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, block_id)
            self._block_list.addItem(item)
        self._block_list.blockSignals(False)
        if blocks:
            self._block_list.setCurrentRow(0)
        else:
            self._current_block_id = None
            self._editor.clear()
            self._block_dirty = False

    def _on_block_row_changed(self, row: int) -> None:
        if row < 0:
            return
        item = self._block_list.item(row)
        if item is None:
            return
        block_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(block_id, str):
            return
        if block_id == self._current_block_id:
            return
        if not self._confirm_discard_block_changes():
            self._restore_block_selection()
            return
        self._show_block(block_id)

    def _restore_block_selection(self) -> None:
        if self._current_block_id is None:
            return
        self._block_list.blockSignals(True)
        for i in range(self._block_list.count()):
            item = self._block_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == self._current_block_id:
                self._block_list.setCurrentRow(i)
                break
        self._block_list.blockSignals(False)

    def _confirm_discard_block_changes(self) -> bool:
        if not self._block_dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Несохранённые изменения")
        box.setText("Применить изменения текущего блока перед переключением?")
        btn_apply = box.addButton("Применить", QMessageBox.ButtonRole.AcceptRole)
        btn_discard = box.addButton("Отменить изменения", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_cancel:
            return False
        if clicked is btn_apply:
            return self._apply(silent=False)
        self._block_dirty = False
        return True

    def _show_block(self, block_id: str) -> None:
        if self._working is None:
            return
        try:
            text = block_to_yaml(self._working, block_id)
        except Exception as e:
            QMessageBox.critical(self, "Блок", str(e))
            return
        self._loading_block = True
        self._editor.setPlainText(text)
        self._loading_block = False
        self._current_block_id = block_id
        self._block_dirty = False

    def _on_editor_changed(self) -> None:
        if self._loading_block:
            return
        self._block_dirty = True
        self._validated = False

    def _apply(self, *, silent: bool = False) -> bool:
        if self._working is None or self._current_block_id is None:
            if not silent:
                QMessageBox.warning(self, "Применить", "Сначала загрузите конфигурацию и выберите блок.")
            return False
        text = self._editor.toPlainText()
        try:
            merged = merge_block_from_yaml(self._working, self._current_block_id, text)
            validate_config_dict(merged)
        except Exception as e:
            self._validated = False
            self._set_status(str(e))
            if not silent:
                QMessageBox.critical(self, "Применить", str(e))
            return False
        self._working = merged
        self._block_dirty = False
        self._validated = True
        self._set_status("Блок применён, конфигурация корректна.", ok=True)
        return True

    def _save(self) -> None:
        if self._working is None or not self._file_path:
            QMessageBox.warning(self, "Сохранить", "Сначала загрузите конфигурацию.")
            return
        if self._block_dirty and not self._apply(silent=True):
            QMessageBox.warning(
                self,
                "Сохранить",
                "Исправьте ошибки в текущем блоке и нажмите «Применить».",
            )
            return
        try:
            validate_config_dict(self._working)
        except Exception as e:
            QMessageBox.critical(self, "Сохранить", str(e))
            return
        try:
            save_config_dict(self._file_path, self._working)
        except OSError as e:
            QMessageBox.critical(self, "Сохранить", str(e))
            return
        self._set_status(f"Сохранено: {self._file_path}", ok=True)
        self._prompt_reload_if_active(self._file_path)

    def _save_as(self) -> None:
        if self._working is None:
            QMessageBox.warning(self, "Сохранить как", "Сначала загрузите конфигурацию.")
            return
        if self._block_dirty and not self._apply(silent=True):
            QMessageBox.warning(
                self,
                "Сохранить как",
                "Исправьте ошибки в текущем блоке и нажмите «Применить».",
            )
            return
        try:
            validate_config_dict(self._working)
        except Exception as e:
            QMessageBox.critical(self, "Сохранить как", str(e))
            return
        start = self._file_path or self._path_edit.text().strip()
        p, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить config.yaml",
            start,
            "YAML (*.yaml *.yml);;Все файлы (*.*)",
        )
        if not p:
            return
        try:
            save_config_dict(p, self._working)
        except OSError as e:
            QMessageBox.critical(self, "Сохранить как", str(e))
            return
        self._file_path = str(Path(p).resolve())
        self._path_edit.setText(self._file_path)
        self._set_status(f"Сохранено: {self._file_path}", ok=True)
        self._prompt_reload_if_active(self._file_path)

    def _prompt_reload_if_active(self, saved_path: str) -> None:
        if self._get_active_config_path is None or self._on_reload_config is None:
            return
        active = str(Path(self._get_active_config_path().strip()).resolve())
        saved = str(Path(saved_path).resolve())
        if active != saved:
            return
        answer = QMessageBox.question(
            self,
            "Перезагрузка",
            "Перезагрузить конфигурацию в программе?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._on_reload_config(saved_path)
