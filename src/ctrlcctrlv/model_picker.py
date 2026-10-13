"""Readable, searchable model selection shared by settings and result windows."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHeaderView,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTabBar, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QCheckBox, QSizePolicy)
from .theme import stylesheet, fit_window_to_screen

DETAIL_ROLE = Qt.ItemDataRole.UserRole + 1


class ModelChooser(QDialog):
    def __init__(self, picker):
        super().__init__(picker.window())
        self.picker = picker
        self.setWindowTitle("Выбрать модель")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(800, 580)
        self.setMinimumSize(520, 360)
        self.setStyleSheet(stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)
        title = QLabel("Выбрать модель")
        title.setProperty("role", "title")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти по названию или разработчику")
        self.search.setAccessibleName("Поиск модели")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.sources = QTabBar()
        for text, source in (("Все", ""), ("На компьютере", "local"), ("OpenRouter", "openrouter")):
            self.sources.addTab(text)
            self.sources.setTabData(self.sources.count() - 1, source)
        source_types = {(picker.itemData(i, DETAIL_ROLE) or {}).get("source") for i in range(picker.count())}
        self.sources.setVisible("local" in source_types and "openrouter" in source_types)
        filters = QHBoxLayout()
        filters.addWidget(self.sources)
        self.downloaded_only = QCheckBox("Скачанные")
        self.downloaded_only.setVisible("local" in source_types)
        self.favorites_only = QCheckBox("Избранное")
        filters.addWidget(self.downloaded_only)
        filters.addWidget(self.favorites_only)
        filters.addStretch()
        layout.addLayout(filters)
        self._mixed = 'local' in source_types and 'openrouter' in source_types
        self._local_only = source_types == {'local'}
        self.table = QTableWidget(0, 3 if self._mixed else 2)
        self.table.setAccessibleName("Доступные модели")
        headers = ["Модель", "Источник", "Размер / Цена"] if self._mixed else ["Модель", "Размер / Состояние" if self._local_only else "Вход / выход · USD / 1 млн токенов"]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeaderItem(1).setToolTip("Стоимость входных / выходных токенов в USD за 1 миллион токенов")
        self.table.horizontalHeader().setStyleSheet("QHeaderView::section { padding: 3px 6px; }")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 100 if self._mixed else 200)
        if self._mixed:
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(2, 180)
        self.table.verticalHeader().setDefaultSectionSize(48)
        layout.addWidget(self.table, 1)
        self.empty = QLabel("Ничего не найдено")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setProperty("role", "muted")
        layout.addWidget(self.empty, 1)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(False)
        self.detail.setProperty("role", "muted")
        self.detail.setMinimumWidth(0)
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.detail)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.favorite = QPushButton("В избранное")
        self.favorite.setAccessibleName("Добавить выбранную модель в избранное")
        self.favorite.clicked.connect(self.toggle_favorite)
        footer.addWidget(self.favorite)
        self.reset_filters = QPushButton("Сбросить фильтры")
        self.reset_filters.clicked.connect(self.clear_filters)
        footer.addWidget(self.reset_filters)
        self.reset_filters.hide()
        footer.addStretch()
        self.install = QPushButton("Установить")
        self.install.clicked.connect(self.install_selected)
        footer.addWidget(self.install)
        self.choose = QPushButton("Выбрать")
        self.choose.setProperty("role", "primary")
        self.choose.setDefault(True)
        self.choose.clicked.connect(self.accept_selected)
        footer.addWidget(self.choose)
        layout.addLayout(footer)
        self.search.textChanged.connect(self.populate)
        self.sources.currentChanged.connect(self.populate)
        self.downloaded_only.toggled.connect(self.populate)
        self.favorites_only.toggled.connect(self.populate)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.itemDoubleClicked.connect(self.accept_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.favorite_menu)
        self.populate()
        self.search.setFocus()

    def populate(self, *_):
        terms = self.search.text().casefold().split()
        source = self.sources.tabData(self.sources.currentIndex())
        self.table.setRowCount(0)
        for index in range(self.picker.count()):
            value = self.picker.itemData(index)
            info = self.picker.itemData(index, DETAIL_ROLE) or {}
            if info.get("unavailable") or (source and info.get("source") != source):
                continue
            if self.downloaded_only.isChecked() and not self.picker.is_installed(value, info):
                continue
            if self.favorites_only.isChecked() and self.picker.favorite_key(value, info) not in self.picker.favorites():
                continue
            name = self.picker.itemText(index)
            haystack = (name + " " + str(value)).casefold()
            if not all(term in haystack for term in terms):
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            installed = self.picker.is_installed(value, info)
            favorite = self.picker.favorite_key(value, info) in self.picker.favorites()
            item = QTableWidgetItem(('★ ' if favorite else '') + name)
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setData(DETAIL_ROLE, info)
            item.setToolTip(name + '\n' + str(value) + ('\nВ избранном' if favorite else ''))
            self.table.setItem(row, 0, item)
            if info.get('source') == 'local':
                metadata = info.get('size', '') + (' · ' if info.get('size') else '') + ('На диске' if installed else 'Не скачана')
            else:
                metadata = info.get('price', 'Цена неизвестна')
            column = 2 if self._mixed else 1
            meta_item = QTableWidgetItem(metadata)
            meta_item.setToolTip(metadata + ('\nUSD / 1 млн токенов · вход / выход' if info.get('source') == 'openrouter' else ''))
            self.table.setItem(row, column, meta_item)
            if self._mixed:
                self.table.setItem(row, 1, QTableWidgetItem('На компьютере' if info.get('source') == 'local' else 'OpenRouter'))
            if value == self.picker.currentData():
                self.table.selectRow(row)
        present = self.table.rowCount() > 0
        self.table.setVisible(present)
        self.empty.setVisible(not present)
        if present and not self.table.selectedItems():
            self.table.selectRow(0)
        self.fit_rows()
        self.selection_changed()

    def fit_rows(self):
        self.table.resizeRowsToContents()
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, max(48, self.table.rowHeight(row)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_rows()

    def selection_changed(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        self.choose.setEnabled(item is not None)
        self.favorite.setEnabled(item is not None)
        self.favorite.setVisible(item is not None)
        self.choose.setVisible(item is not None)
        self.reset_filters.setVisible(item is None)
        self.install.hide()
        if item:
            value = item.data(Qt.ItemDataRole.UserRole)
            identifier = value[-1] if isinstance(value, (tuple, list)) else value
            source = (item.data(DETAIL_ROLE) or {}).get("source", "")
            info = item.data(DETAIL_ROLE) or {}
            missing = source == "local" and not self.picker.is_installed(value, info)
            self.install.setVisible(missing)
            self.choose.setEnabled(not missing)
            favorite = self.picker.favorite_key(value, info) in self.picker.favorites()
            self.favorite.setText("Убрать из избранного" if favorite else "В избранное")
            self.choose.setVisible(not missing)
            self.favorite.setToolTip("Убрать модель из избранного" if favorite else "Добавить модель в избранное")
            self.favorite.setAccessibleName(self.favorite.toolTip())
            self.detail.setText("Не скачана · требуется установка" if missing else "На компьютере · модель скачана" if source == 'local' else "OpenRouter · USD / 1 млн токенов")
            self.detail.setToolTip(str(identifier))
        else:
            self.detail.clear()
            self.detail.setToolTip("")

    def accept_selected(self, *_):
        item = self.table.item(self.table.currentRow(), 0)
        if item is None:
            return
        info = item.data(DETAIL_ROLE) or {}
        if info.get("source") == "local" and not self.picker.is_installed(item.data(Qt.ItemDataRole.UserRole), info):
            self.install_selected()
            return
        # Resolve identity again: a background catalog refresh may have reordered it.
        index = self.picker.findData(item.data(Qt.ItemDataRole.UserRole))
        if index < 0:
            self.populate()
            return
        self.picker.setCurrentIndex(index)
        self.accept()

    def toggle_favorite(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item is None:
            return
        key = self.picker.favorite_key(item.data(Qt.ItemDataRole.UserRole), item.data(DETAIL_ROLE) or {})
        favorites = self.picker.favorites()
        favorites.symmetric_difference_update({key})
        if self.picker.ui_state is not None:
            if not self.picker.ui_state.set(favorites=sorted(favorites)):
                self.detail.setText("Не удалось сохранить избранное")
                return
        self.picker._favorites = favorites
        self.populate()

    def clear_filters(self):
        for widget in (self.search, self.sources, self.downloaded_only, self.favorites_only):
            widget.blockSignals(True)
        self.search.clear()
        self.sources.setCurrentIndex(0)
        self.downloaded_only.setChecked(False)
        self.favorites_only.setChecked(False)
        for widget in (self.search, self.sources, self.downloaded_only, self.favorites_only):
            widget.blockSignals(False)
        self.populate()

    def favorite_menu(self, point):
        from PySide6.QtWidgets import QMenu
        if self.table.currentRow() < 0:
            return
        menu = QMenu(self)
        menu.addAction(self.favorite.text(), self.toggle_favorite)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def showEvent(self, event):
        super().showEvent(event)
        fit_window_to_screen(self)

    def install_selected(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item is None:
            return
        value = item.data(Qt.ItemDataRole.UserRole)
        identifier = value[-1] if isinstance(value, (tuple, list)) else value
        self.picker.install_requested.emit(str(identifier))
        self.reject()


class ModelPicker(QComboBox):
    install_requested = Signal(str)

    def __init__(self, identifier=""):
        super().__init__()
        self.ui_state = None
        self._favorites = set()
        self._installed = None
        self.setMinimumContentsLength(16)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        if identifier:
            self.setText(identifier)

    def set_ui_state(self, state):
        self.ui_state = state
        self._favorites = set(state.get("favorites", []))

    def favorites(self):
        return set(self.ui_state.get("favorites", [])) if self.ui_state is not None else set(self._favorites)

    @staticmethod
    def favorite_key(value, info):
        source, identifier = value if isinstance(value, (tuple, list)) else (info.get("source", "openrouter"), value)
        return str(source) + ":" + str(identifier)

    def set_installed(self, identifiers):
        self._installed = set(identifiers)

    def is_installed(self, value, info):
        identifier = value[-1] if isinstance(value, (tuple, list)) else value
        return info.get("source") == "local" and (self._installed is None or identifier in self._installed)

    def text(self):
        return self.currentData() or ""

    def setText(self, identifier):
        index = self.findData(identifier)
        if index < 0:
            self.addItem(identifier, identifier)
            index = self.count() - 1
            self.setItemData(index, {"unavailable": True}, DETAIL_ROLE)
            self.setItemData(index, "Модель отсутствует в каталоге. Выберите доступную модель.", Qt.ItemDataRole.ToolTipRole)
        self.setCurrentIndex(index)

    def set_models(self, models, review=False):
        current = self.text()
        self.blockSignals(True)
        self.clear()
        for model in models:
            if review and not model.structured:
                continue
            self.addItem(model.name, model.id)
            self.setItemData(self.count() - 1, {"source": "openrouter", "price": f"${model.input_price} / ${model.output_price}"}, DETAIL_ROLE)
            self.setItemData(self.count() - 1, model.label, Qt.ItemDataRole.ToolTipRole)
        if current:
            self.setText(current)
        self.blockSignals(False)

    def create_chooser(self):
        return ModelChooser(self)

    def showPopup(self):
        if self.isEnabled():
            chooser = self.create_chooser()
            chooser.open()
            return chooser

    def keyPressEvent(self, event):
        navigation = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
                      Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                      Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F4)
        search = (event.text().isprintable() and bool(event.text().strip())
                  and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if event.key() in navigation or event.key() == Qt.Key.Key_Space or search:
            chooser = self.showPopup()
            if chooser is not None and search:
                chooser.search.setText(event.text())
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        event.ignore()
