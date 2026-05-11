from PyQt6 import sip
import sys
import json
import os
from pathlib import Path

# ── PyQt6 ────────────────────────────────────────────────────────────────────
from PyQt6.QtCore import (
    Qt, QRectF, QSize,
    QAbstractTableModel, QModelIndex, QVariant,
)
from PyQt6.QtWidgets import QMenu
from PyQt6.QtGui import (
    QColor, QPen, QBrush, QPainter, QPainterPath,
    QPixmap, QImage, QIcon,
    QTransform, QShortcut, QKeySequence,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow,
    # Layouts
    QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QWidget, QStackedWidget, QScrollArea,
    # Dock / toolbar
    QDockWidget, QToolBar,
    # Dialogs
    QDialog, QDialogButtonBox, QFileDialog, QMessageBox,
    # Inputs
    QLineEdit, QCheckBox, QComboBox, QSlider,
    QSpinBox, QDoubleSpinBox, QRadioButton, QProgressBar,
    # Buttons / labels
    QPushButton, QLabel,
    # Containers / groups
    QGroupBox,
    # Lists / trees / tables
    QListWidget, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem,
    QTableView, QTableWidget, QTableWidgetItem, QHeaderView,
    # Text
    QTextEdit,
    # Graphics
    QGraphicsScene,
)

# ── Сигнали ───────────────────────────────────────────────────────────────────
from PyQt6.QtCore import pyqtSignal

# ── Зовнішні бібліотеки ───────────────────────────────────────────────────────
import fitz        # PyMuPDF — растровий рендер (підкладка)
import pdfplumber  # Векторна геометрія PDF

# ── Власні модулі ─────────────────────────────────────────────────────────────
from worker import SearchWorker, BatchWorker, ThumbnailWorker, extract_text_by_center

from models import FoundObject
import template_engine as te
from evaluator import ExprEvaluator
from state import SessionState
from graphics import (InteractiveLine, InteractiveRect, InteractiveEllipse,
                      InteractivePath, ValidationBox, ZoomableView, InteractiveMixin,
                      GhostPreviewItem)
# ═══════════════════════════════════════════════════════════════════════════════
# ВІКНО БАЗИ ДАНИХ (ПОВНИЙ ДОКУМЕНТ)
# ═══════════════════════════════════════════════════════════════════════════════
from PyQt6.QtCore import QSortFilterProxyModel, QRegularExpression, Qt, QTimer

class MultiColumnFilterProxyModel(QSortFilterProxyModel):
    def filterAcceptsRow(self, source_row, source_parent):
        regex = self.filterRegularExpression()
        if not regex.pattern(): return True
        model = self.sourceModel()
        for col in range(model.columnCount()):
            idx = model.index(source_row, col, source_parent)
            data = model.data(idx, Qt.ItemDataRole.DisplayRole)
            if data and regex.match(str(data)).hasMatch():
                return True
        return False

class DatabaseTableModel(QAbstractTableModel):
    def __init__(self, data, headers, state, main_window):
        super().__init__()
        self._data = data
        self._headers = headers
        self.state = state
        self.main_window = main_window

    def update_data(self, data, headers):
        self.layoutAboutToBeChanged.emit()
        self._data = data
        self._headers = headers
        self.layoutChanged.emit()

    def rowCount(self, parent=QModelIndex()): return len(self._data)
    def columnCount(self, parent=QModelIndex()): return len(self._headers)

    def flags(self, index):
        col = index.column()
        if col == 3 or col > 5:
            row = self._data[index.row()]
            if col > 5 and col not in row['col_map']:
                return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._data): return QVariant()
        row = self._data[index.row()]
        col = index.column()
        obj = row['obj_ref']
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == 0: return row['page_display']
            elif col == 1: return obj.get('template_name', '')
            elif col == 2: return obj.get('variant_name', '')
            elif col == 3: return obj.get('status', 'pending')
            elif col == 4: return round(obj.get('anchor', {}).get('x', 0), 1)
            elif col == 5: return round(obj.get('anchor', {}).get('y', 0), 1)
            else:
                field_key = row['col_map'].get(col)
                return obj.get('text_fields', {}).get(field_key, "") if field_key else ""
        if role == Qt.ItemDataRole.TextAlignmentRole: return Qt.AlignmentFlag.AlignCenter
        return QVariant()

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role == Qt.ItemDataRole.EditRole:
            row = self._data[index.row()]
            col = index.column()
            obj = row['obj_ref']
            new_val = str(value).strip()
            changed = False
            if col == 3: obj['status'] = new_val; changed = True
            elif col > 5:
                field_key = row['col_map'].get(col)
                if field_key: obj.setdefault('text_fields', {})[field_key] = new_val; changed = True
            if changed:
                self.state.save_setting("session_cache", self.state.session_cache)
                mw = self.main_window
                page_num = int(row['page_key'])
                if mw and mw.state.page_num == page_num:
                    obj_idx = row['obj_idx']
                    if hasattr(mw, 'table_model'):
                        mw.table_model.objects[obj_idx] = obj
                        mw.table_model.layoutChanged.emit()
                    mw.refresh_inspector()
                    for item in mw.scene.items():
                        if hasattr(item, 'row_index') and item.row_index == obj_idx:
                            item.update(); break


                self.dataChanged.emit(index, index)
                return True
        return False

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return QVariant()

class DatabaseWindow(QMainWindow):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state, self.main_window = state, parent
        self.setWindowTitle("База даних об'єктів")
        self.resize(1200, 700)
        container = QWidget(); self.setCentralWidget(container); layout = QVBoxLayout(container)
        
        top_panel = QHBoxLayout(); layout.addLayout(top_panel)
        top_panel.addWidget(QLabel("Шаблон:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("Всі об'єкти")
        self.combo_mode.currentTextChanged.connect(self.refresh_data)
        top_panel.addWidget(self.combo_mode)
        
        self.search_input = QLineEdit(); self.search_input.setPlaceholderText("Пошук по всіх колонках...")
        self.search_input.textChanged.connect(self.update_filter); top_panel.addWidget(self.search_input)
        
        self.btn_manual_refresh = QPushButton("🔄 Оновити таблицю")
        self.btn_manual_refresh.clicked.connect(self.refresh_data)
        self.btn_manual_refresh.setStyleSheet("background-color: #3498db; color: white; padding: 4px 10px; font-weight: bold;")
        top_panel.addWidget(self.btn_manual_refresh)
        
        top_panel.addStretch()
        
        self.table_view = QTableView(); self.table_view.setSortingEnabled(True)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setAlternatingRowColors(True)
        
        self.source_model = DatabaseTableModel([], [], self.state, self.main_window)
        self.proxy_model = MultiColumnFilterProxyModel()
        self.proxy_model.setSourceModel(self.source_model)
        self.table_view.setModel(self.proxy_model)
        
        self.table_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        layout.addWidget(self.table_view)
        
        self.status_label = QLabel("Всього об'єктів: 0")
        layout.addWidget(self.status_label)
        
        # Початкове завантаження
        self.refresh_data()

    def update_filter(self, text):
        regex = QRegularExpression(text, QRegularExpression.PatternOption.CaseInsensitiveOption)
        self.proxy_model.setFilterRegularExpression(regex)

    def _update_template_list(self, norm_cache):
        """Оновлює випадаючий список шаблонів, не скидаючи вибір користувача."""
        current_selection = self.combo_mode.currentText()
        templates = set()
        for page_data in norm_cache.values():
            for obj in page_data:
                templates.add(obj.get('template_name', 'Unknown'))
        
        sorted_tmpls = sorted(list(templates))
        new_list = ["Всі об'єкти"] + sorted_tmpls
        
        # Перевіряємо, чи змінився склад списку
        existing = [self.combo_mode.itemText(i) for i in range(self.combo_mode.count())]
        if existing != new_list:
            self.combo_mode.blockSignals(True)
            self.combo_mode.clear()
            self.combo_mode.addItems(new_list)
            # Відновлюємо вибір, якщо він все ще існує
            idx = self.combo_mode.findText(current_selection)
            if idx >= 0: self.combo_mode.setCurrentIndex(idx)
            self.combo_mode.blockSignals(False)

    def refresh_data(self):
        """Головний метод оновлення даних."""
        mode = self.combo_mode.currentText()
        norm_cache = {str(k): v for k, v in self.state.session_cache.items()}
        
        # Оновлюємо список шаблонів у комбобоксі
        self._update_template_list(norm_cache)
        
        template_fields = {}
        for page_data in norm_cache.values():
            for obj in page_data:
                t = obj.get('template_name', 'Unknown')
                if t not in template_fields: template_fields[t] = set()
                for f in obj.get('text_fields', {}).keys():
                    template_fields[t].add(f)
        for t in template_fields:
            template_fields[t] = sorted(list(template_fields[t]))
        
        headers = ["Сторінка", "Шаблон", "Варіант", "Статус", "Pos_X", "Pos_Y"]
        rows = []
        max_f = max([len(f) for f in template_fields.values()] if template_fields else [0])
        
        if mode == "Всі об'єкти":
            for i in range(max_f): headers.append(f"txt_{i+1}")
        else:
            headers.extend(template_fields.get(mode, []))
        
        for page_key in sorted(norm_cache.keys(), key=int):
            for idx, obj in enumerate(norm_cache[page_key]):
                t_name = obj.get('template_name', 'Unknown')
                if mode != "Всі об'єкти" and t_name != mode: continue
                col_map = {6 + i: f for i, f in enumerate(template_fields.get(t_name, []))}
                rows.append({
                    'page_key': page_key, 'page_display': int(page_key)+1, 
                    'obj_idx': idx, 'obj_ref': obj, 'col_map': col_map
                })
        
        self.source_model.update_data(rows, headers)
        self.status_label.setText(f"Об'єктів: {len(rows)}")

    def on_selection_changed(self, selected, deselected):
        indexes = selected.indexes()
        if not indexes: return
        source_idx = self.proxy_model.mapToSource(indexes[0])
        if source_idx.row() >= len(self.source_model._data): return
        row = self.source_model._data[source_idx.row()]
        
        mw = self.main_window
        if mw:
            page_num, obj_idx = int(row['page_key']), row['obj_idx']
            if mw.state.current_mode != "VALIDATE": mw.switch_mode("VALIDATE")
            if mw.state.page_num != page_num: mw.go_to_page(page_num)
            QTimer.singleShot(150, lambda: self._do_focus(obj_idx))

    def _do_focus(self, idx):
        if hasattr(self.main_window, 'table_view'):
            self.main_window.table_view.selectRow(idx)
        self.main_window.action_isolate_object(idx, zoom=True)
        self.main_window.raise_(); self.main_window.activateWindow()

class DatabaseWindow(QMainWindow):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state, self.main_window = state, parent
        self.setWindowTitle(f"База даних об'єктів — {os.path.basename(state.pdf_path or 'Документ')}")
        self.resize(1200, 700)
        
        container = QWidget()
        self.setCentralWidget(container)
        layout = QVBoxLayout(container)
        
        top_panel = QHBoxLayout()
        layout.addLayout(top_panel)
        
        top_panel.addWidget(QLabel("Шаблон:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("Всі об'єкти")
        # Ми забрали звідси статичний цикл. Тепер список оновлюється динамічно в refresh_data!
        self.combo_mode.currentTextChanged.connect(self.refresh_data)
        top_panel.addWidget(self.combo_mode)
        
        top_panel.addSpacing(15)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Глобальний пошук...")
        self.search_input.textChanged.connect(self.update_filter)
        top_panel.addWidget(self.search_input)
        
        top_panel.addSpacing(15)
        
        # === ДОДАНО КНОПКУ ОНОВИТИ ===
        self.btn_manual_refresh = QPushButton("🔄 Оновити")
        self.btn_manual_refresh.setStyleSheet("background-color: #3498db; color: white; padding: 4px 15px; font-weight: bold; border-radius: 3px;")
        self.btn_manual_refresh.clicked.connect(self.refresh_data)
        top_panel.addWidget(self.btn_manual_refresh)
        # ==============================
        
        top_panel.addStretch()
        
        self.table_view = QTableView()
        self.table_view.setSortingEnabled(True)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self._show_context_menu)
        
        self.source_model = DatabaseTableModel([], [], self.state, self.main_window)
        self.proxy_model = MultiColumnFilterProxyModel() # Наш новий проксі
        self.proxy_model.setSourceModel(self.source_model)
        
        self.table_view.setModel(self.proxy_model)
        layout.addWidget(self.table_view)
        
        self.table_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        
        self.status_label = QLabel("Всього об'єктів: 0")
        layout.addWidget(self.status_label)
        
        self.refresh_data() # Це завантажить і таблицю, і динамічний список шаблонів

    def update_filter(self, text): 
        from PyQt6.QtCore import QRegularExpression
        regex = QRegularExpression(text, QRegularExpression.PatternOption.CaseInsensitiveOption)
        self.proxy_model.setFilterRegularExpression(regex)

    def _update_template_list(self, norm_cache):
        """Динамічно оновлює випадаючий список шаблонів."""
        current_selection = self.combo_mode.currentText()
        templates = set()
        for page_data in norm_cache.values():
            for obj in page_data:
                templates.add(obj.get('template_name', 'Unknown'))
        
        sorted_tmpls = sorted(list(templates))
        new_list = ["Всі об'єкти"] + sorted_tmpls
        
        existing = [self.combo_mode.itemText(i) for i in range(self.combo_mode.count())]
        if existing != new_list:
            self.combo_mode.blockSignals(True)
            self.combo_mode.clear()
            self.combo_mode.addItems(new_list)
            idx = self.combo_mode.findText(current_selection)
            if idx >= 0: self.combo_mode.setCurrentIndex(idx)
            self.combo_mode.blockSignals(False)
    def _show_context_menu(self, pos):
        """Контекстне меню для зміни статусу виділених об'єктів."""
        selection = self.table_view.selectionModel().selectedRows()
        if not selection:
            return
        
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act_approved = menu.addAction("✅ Approved")
        act_pending = menu.addAction("⏳ Pending")
        menu.addSeparator()
        act_delete = menu.addAction("🗑 Видалити")
        
        action = menu.exec(self.table_view.viewport().mapToGlobal(pos))
        if not action:
            return
        
        if action == act_approved:
            self._set_status_for_selected(selection, "approved")
        elif action == act_pending:
            self._set_status_for_selected(selection, "pending")
        elif action == act_delete:
            self._delete_selected(selection)

    def _set_status_for_selected(self, selection, new_status):
        """Змінює статус для всіх виділених об'єктів."""
        for proxy_idx in selection:
            source_idx = self.proxy_model.mapToSource(proxy_idx)
            row_data = self.source_model._data[source_idx.row()]
            obj = row_data['obj_ref']
            obj['status'] = new_status
        
        # Синхронізуємо з БД
        for page_key in set(str(self.source_model._data[self.proxy_model.mapToSource(idx).row()]['page_key']) for idx in selection):
            page_objects = self.state.session_cache.get(page_key, [])
            self.state.sync_page_to_db(int(page_key), page_objects, status="saved")
        
        self.refresh_data()
        
        # Оновити головне вікно
        if self.main_window:
            self.main_window.load_cached_validation()
            self.main_window.sync_layers_visibility()

    def _delete_selected(self, selection):
        """Видаляє виділені об'єкти."""
        reply = QMessageBox.question(
            self, "Видалення",
            f"Видалити {len(selection)} об'єктів?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Збираємо об'єкти для видалення (по сторінках)
        to_delete = {}
        for proxy_idx in selection:
            source_idx = self.proxy_model.mapToSource(proxy_idx)
            row_data = self.source_model._data[source_idx.row()]
            page_key = str(row_data['page_key'])
            obj_ref = row_data['obj_ref']
            to_delete.setdefault(page_key, []).append(id(obj_ref))
        
        # Видаляємо з кешу
        for page_key, obj_ids in to_delete.items():
            page_objects = self.state.session_cache.get(page_key, [])
            self.state.session_cache[page_key] = [
                o for o in page_objects if id(o) not in obj_ids
            ]
            self.state.sync_page_to_db(int(page_key), self.state.session_cache[page_key], status="saved")
        
        self.refresh_data()
        
        if self.main_window:
            self.main_window.load_cached_validation()
            self.main_window.sync_layers_visibility()
            for page_key in to_delete:
                self.main_window.update_thumbnail_status(int(page_key))
    def refresh_data(self):
        mode = self.combo_mode.currentText()
        norm_cache = {str(k): v for k, v in self.state.session_cache.items()}
        
        # Динамічно оновлюємо список шаблонів
        self._update_template_list(norm_cache)
        
        template_fields = {}
        for page_data in norm_cache.values():
            for obj in page_data:
                t = obj.get('template_name', 'Unknown')
                if t not in template_fields: template_fields[t] = []
                # Порядок із ghost_zones (як у шаблоні)
                for gz in obj.get('custom_zones', {}).get('ghost_zones', []):
                    fn = gz.get('field', '').split('[')[0]
                    if fn and fn not in template_fields[t]:
                        template_fields[t].append(fn)
                # Додаємо решту полів, яких немає в ghost_zones
                for f in obj.get('text_fields', {}).keys():
                    if f not in template_fields[t]:
                        template_fields[t].append(f)
        
        headers = ["Сторінка", "Шаблон", "Варіант", "Статус", "Pos_X", "Pos_Y"]
        rows = []; max_f = max([len(f) for f in template_fields.values()] if template_fields else [0])
        if mode == "Всі об'єкти":
            for i in range(max_f): headers.append(f"txt_{i+1}")
        else: headers.extend(template_fields.get(mode, []))
        
        for page_key in sorted(norm_cache.keys(), key=int):
            for idx, obj in enumerate(norm_cache[page_key]):
                if mode != "Всі об'єкти" and obj.get('template_name') != mode: continue
                col_map = {6 + i: f for i, f in enumerate(template_fields.get(obj.get('template_name'), []))}
                rows.append({'page_key': page_key, 'page_display': int(page_key)+1, 'obj_idx': idx, 'obj_ref': obj, 'col_map': col_map})
        self.source_model.update_data(rows, headers)
        self.status_label.setText(f"Об'єктів відображено: {len(rows)}")

    def on_selection_changed(self, selected, deselected):
        indexes = selected.indexes()
        if not indexes: return
        source_idx = self.proxy_model.mapToSource(indexes[0])
        row = self.source_model._data[source_idx.row()]
        
        mw = self.main_window
        if mw:
            page_num, obj_idx = int(row['page_key']), row['obj_idx']
            if mw.state.current_mode != "VALIDATE": mw.switch_mode("VALIDATE")
            if mw.state.page_num != page_num: mw.go_to_page(page_num)
            
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(150, lambda: self._do_focus(obj_idx))

    def _do_focus(self, idx):
        if hasattr(self.main_window, 'table_view'):
            self.main_window.table_view.selectRow(idx)
        self.main_window.action_isolate_object(idx, zoom=True)
        self.main_window.raise_(); self.main_window.activateWindow()


class DataGridModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.objects = []  # Список словників obj_dict
        self.headers = ["☑", "Шаблон", "Знайдені дані"]

    def rowCount(self, parent=QModelIndex()):
        return len(self.objects)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return QVariant()
        obj = self.objects[index.row()]
        
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 1:
                return obj.get("template_name", "Unknown")
            elif index.column() == 2:
                fields = obj.get("text_fields", {})
                return " | ".join(f"{k}: {v}" for k, v in fields.items())
                
        elif role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return Qt.CheckState.Checked if obj.get("status") == "approved" else Qt.CheckState.Unchecked

        return QVariant()

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            obj = self.objects[index.row()]
            obj["status"] = "approved" if value == Qt.CheckState.Checked.value else "pending"
            self.dataChanged.emit(index, index)
            return True
        return False

    def flags(self, index):
        base_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            return base_flags | Qt.ItemFlag.ItemIsUserCheckable
        return base_flags

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return QVariant()
        
    def add_object(self, obj_dict):
        self.beginInsertRows(QModelIndex(), len(self.objects), len(self.objects))
        self.objects.append(obj_dict)
        self.endInsertRows()
        
    def clear(self):
        self.beginResetModel()
        self.objects.clear()
        self.endResetModel()
# ═══════════════════════════════════════════════════════════════════════════════
# СТАРТОВЕ ВІКНО ТА ДАШБОРД
# ═══════════════════════════════════════════════════════════════════════════════

class StartDashboard(QDialog):
    def __init__(self, state: SessionState):
        super().__init__()
        self.state = state
        self.state.load_all_from_db()
        self.setWindowTitle("EPLAN Studio v2.0")
        self.setFixedSize(520, 480)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        # --- Заголовок ---
        title = QLabel("EPLAN Studio v2.0")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #2c3e50;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Інтелектуальна обробка інженерних PDF-креслень")
        subtitle.setStyleSheet("font-size: 11px; color: #7f8c8d;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(8)

        # --- Відновлення незавершеної сесії ---
        recovery = self.state.check_recovery()
        if recovery:
            pdf_path = recovery.get("pdf_path", "")
            last_page = int(recovery.get("page", 0))
            file_name = os.path.basename(pdf_path) if pdf_path else "—"
            pages_cached = len(self.state.session_cache)

            group_recovery = QGroupBox("🔄 Незавершена сесія")
            group_recovery.setStyleSheet(
                "QGroupBox { border: 2px solid #f39c12; border-radius: 6px; "
                "margin-top: 6px; padding-top: 14px; font-weight: bold; }"
                "QGroupBox::title { color: #f39c12; }")
            rec_layout = QVBoxLayout()
            rec_layout.setSpacing(4)

            info = QLabel(
                f"Файл: <b>{file_name}</b><br>"
                f"Остання сторінка: <b>{last_page + 1}</b> · "
                f"Сторінок з даними: <b>{pages_cached}</b>")
            info.setStyleSheet("font-size: 11px; color: #34495e;")
            info.setWordWrap(True)
            info.setMinimumHeight(36)
            rec_layout.addWidget(info)

            btn_recover = QPushButton("▶  Продовжити роботу")
            btn_recover.setFixedHeight(34)
            btn_recover.setStyleSheet(
                "background-color: #f39c12; color: white; font-weight: bold; "
                "font-size: 12px; border-radius: 5px;")
            btn_recover.clicked.connect(lambda: self._do_recovery(pdf_path, last_page))
            rec_layout.addWidget(btn_recover)

            group_recovery.setLayout(rec_layout)
            layout.addWidget(group_recovery)

        # --- PDF ---
        group_pdf = QGroupBox("Вихідний документ")
        pdf_layout = QHBoxLayout()
        self.edit_pdf_path = QLineEdit()
        self.edit_pdf_path.setPlaceholderText("Оберіть PDF-креслення EPLAN...")
        btn_pdf = QPushButton("📂 Огляд...")
        btn_pdf.setFixedWidth(90)
        btn_pdf.clicked.connect(self.select_pdf)
        pdf_layout.addWidget(self.edit_pdf_path)
        pdf_layout.addWidget(btn_pdf)
        group_pdf.setLayout(pdf_layout)
        layout.addWidget(group_pdf)

        # --- Шаблони ---
        group_tmpl = QGroupBox("Бібліотека шаблонів")
        tmpl_layout = QHBoxLayout()
        default_tmpl_dir = str(Path.cwd() / "templates")
        os.makedirs(default_tmpl_dir, exist_ok=True)
        self.edit_tmpl_dir = QLineEdit(default_tmpl_dir)
        btn_tmpl = QPushButton("📁 Папка...")
        btn_tmpl.setFixedWidth(90)
        btn_tmpl.clicked.connect(self.select_tmpl_dir)
        tmpl_layout.addWidget(self.edit_tmpl_dir)
        tmpl_layout.addWidget(btn_tmpl)
        group_tmpl.setLayout(tmpl_layout)
        layout.addWidget(group_tmpl)

        layout.addSpacing(4)

        # --- Кнопки дій ---
        self.btn_start = QPushButton("🚀  Запустити редактор")
        self.btn_start.setFixedHeight(44)
        self.btn_start.setStyleSheet(
            "background-color: #2ecc71; color: white; font-weight: bold; "
            "font-size: 14px; border-radius: 6px;")
        self.btn_start.clicked.connect(self.validate_and_accept)
        layout.addWidget(self.btn_start)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_open_session = QPushButton("📂  Відкрити сесію (.epss)")
        self.btn_open_session.setFixedHeight(36)
        self.btn_open_session.setStyleSheet(
            "background-color: #3498db; color: white; font-weight: bold; "
            "font-size: 12px; border-radius: 5px;")
        self.btn_open_session.clicked.connect(self.open_session)
        btn_row.addWidget(self.btn_open_session)

        self.btn_quit = QPushButton("Вихід")
        self.btn_quit.setFixedHeight(36)
        self.btn_quit.setFixedWidth(80)
        self.btn_quit.setStyleSheet(
            "background-color: #95a5a6; color: white; font-weight: bold; "
            "border-radius: 5px;")
        self.btn_quit.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_quit)

        layout.addLayout(btn_row)

    def _do_recovery(self, pdf_path, last_page):
        """Відновлення незавершеної сесії."""
        if not pdf_path or not os.path.exists(pdf_path):
            QMessageBox.warning(self, "Помилка",
                f"PDF-файл не знайдено:\n{pdf_path}")
            return
        self.state.pdf_path = pdf_path
        self.state.templates_dir = self.edit_tmpl_dir.text().strip()
        self.state.page_num = last_page
        self.accept()

    def select_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Вибір PDF", "", "PDF Files (*.pdf)")
        if path:
            self.edit_pdf_path.setText(path)

    def select_tmpl_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Папка шаблонів")
        if path:
            self.edit_tmpl_dir.setText(path)

    def validate_and_accept(self):
        pdf_path = self.edit_pdf_path.text().strip()
        tmpl_dir = self.edit_tmpl_dir.text().strip()
        if not pdf_path or not os.path.exists(pdf_path):
            return QMessageBox.critical(self, "Помилка", "Вкажіть існуючий PDF файл.")
        # Очищаємо старий кеш при запуску з новим PDF
        self.state.clear_cache()
        self.state.pdf_path = pdf_path
        self.state.templates_dir = tmpl_dir
        self.state.page_num = 0
        self.accept()

    def open_session(self):
        """Відкриває збережену сесію .epss."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Відкрити сесію", "", "EPLAN Session (*.epss)")
        if not file_path:
            return

        data = self.state.import_session(file_path)
        if not data:
            return QMessageBox.critical(self, "Помилка", "Не вдалося прочитати файл сесії.")

        pdf_path = data.get("pdf_path", "")
        if not pdf_path or not os.path.exists(pdf_path):
            return QMessageBox.critical(
                self, "Помилка",
                f"PDF-файл із сесії не знайдено:\n{pdf_path}")

        self.state.pdf_path = pdf_path
        self.state.templates_dir = data.get("templates_dir", self.edit_tmpl_dir.text())
        self.state.page_num = data.get("page_num", 0)
        self.state.session_cache = data.get("session_cache", {})

        # Синхронізуємо в SQLite
        # Спочатку зберігаємо кеш, бо clear_cache() його затре
        loaded_cache = dict(self.state.session_cache)
        self.state.clear_cache()
        self.state.session_cache = loaded_cache
        for page_key, objects in loaded_cache.items():
            self.state.sync_page_to_db(int(page_key), objects, status="saved")
        
        print(f"[DEBUG open_session] cache_keys={list(self.state.session_cache.keys())[:5]}, total={len(self.state.session_cache)}")
        self.accept()
# ═══════════════════════════════════════════════════════════════════════════════
# ДІАЛОГ ПАКЕТНОЇ ОБРОБКИ
# ═══════════════════════════════════════════════════════════════════════════════

class SettingsDialog(QDialog):
    """Компактний діалог налаштувань відображення з вкладками."""
    PEN_STYLES = {
        'Суцільна': 'solid', 'Штрихова': 'dash', 'Точкова': 'dot',
        'Штрих-пунктирна': 'dashdot', 'Штрих-двопунктирна': 'dashdotdot',
    }
    STYLE_DISPLAY = {v: k for k, v in PEN_STYLES.items()}

    LAYERS = [
        ("frame_pending",  "Рамка Pending",   True,  "#fff39c12", "#40f39c12", 2.0, "solid"),
        ("frame_approved", "Рамка Approved",  True,  "#ff27ae60", "#4027ae60", 2.0, "solid"),
        ("skeleton",       "Скелет",          False, "#ffe74c3c", None,        1.5, "solid"),
        ("zones",          "Текст. зони",     True,  "#ff9b59b6", "#4d9b59b6", 1.5, "dash"),
        ("service_zones",  "Сервісні зони",   True,  "#ff16a085", "#4d1abc9c", 1.5, "dash"),
        ("anchor",         "Anchor ✕",        False, "#ffdc1414", None,        2.0, "solid"),
        ("vectors",        "Вектори PDF",     False, "#ff0096ff", None,        1.0, "solid"),
    ]

    def __init__(self, state_ref, parent=None):
        super().__init__(parent)
        self.state = state_ref
        self.setWindowTitle("Налаштування відображення")
        self.setModal(True)
        self.setFixedWidth(440)
        self._ctrls: dict = {}

        from PyQt6.QtWidgets import QTabWidget, QScrollArea
        root = QVBoxLayout(self)
        root.setSpacing(4)
        root.setContentsMargins(6, 6, 6, 6)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # ── Вкладка "Стилі" ───────────────────────────────────────────────
        styles_widget = QWidget()
        styles_layout = QVBoxLayout(styles_widget)
        styles_layout.setSpacing(3)
        styles_layout.setContentsMargins(4, 4, 4, 4)

        for key, label, has_fill, def_c, def_f, def_w, def_s in self.LAYERS:
            row = QHBoxLayout()
            row.setSpacing(4)

            lbl = QLabel(f"<b>{label}</b>")
            lbl.setFixedWidth(100)
            row.addWidget(lbl)

            # Колір контуру
            cur_c = state_ref.load_setting(f"color_contour_{key}", def_c)
            btn_c = QPushButton()
            btn_c.setFixedSize(36, 22)
            btn_c.setStyleSheet(f"background:{cur_c}; border:1px solid #888;")
            btn_c.setToolTip("Контур")
            btn_c.clicked.connect(lambda _, k=key, b=btn_c, dk=def_c:
                                  self._pick_color(k, "contour", b, dk))
            row.addWidget(btn_c)

            # Колір заливки
            if has_fill:
                cur_f = state_ref.load_setting(f"color_fill_{key}", def_f or "#00000000")
                btn_f = QPushButton()
                btn_f.setFixedSize(36, 22)
                btn_f.setStyleSheet(f"background:{cur_f}; border:1px solid #888;")
                btn_f.setToolTip("Заливка")
                btn_f.clicked.connect(lambda _, k=key, b=btn_f, dk=def_f:
                                      self._pick_color(k, "fill", b, dk))
                row.addWidget(btn_f)
            else:
                row.addSpacing(40)

            # Товщина
            spin = QDoubleSpinBox()
            spin.setRange(0.5, 10.0); spin.setSingleStep(0.5)
            spin.setDecimals(1); spin.setFixedWidth(60)
            spin.setSuffix("px")
            spin.setValue(float(state_ref.load_setting(f"line_width_{key}", def_w)))
            row.addWidget(spin)

            # Тип лінії
            combo = QComboBox()
            combo.setFixedWidth(120)
            for s in self.PEN_STYLES:
                combo.addItem(s)
            combo.setCurrentText(
                self.STYLE_DISPLAY.get(state_ref.load_setting(f"line_style_{key}", def_s), "Суцільна"))
            row.addWidget(combo)

            styles_layout.addLayout(row)
            self._ctrls[key] = {
                "btn_c": btn_c, "spin": spin, "combo": combo,
                **({"btn_f": btn_f} if has_fill else {})
            }

        styles_layout.addStretch()
        tabs.addTab(styles_widget, "🎨 Стилі")

        # ── Вкладка "Видимість" ───────────────────────────────────────────
        vis_widget = QWidget()
        vis_layout = QVBoxLayout(vis_widget)
        vis_layout.setSpacing(4)
        vis_layout.setContentsMargins(6, 6, 6, 6)
        self._vis_checks: dict = {}
        for key, label, *_ in self.LAYERS:
            cb = QCheckBox(label)
            cb.setChecked(state_ref.load_setting(f"visible_{key}", True))
            vis_layout.addWidget(cb)
            self._vis_checks[key] = cb
        vis_layout.addStretch()
        tabs.addTab(vis_widget, "👁 Видимість")

        root.addWidget(tabs)

# ── Кнопки ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_reset = QPushButton("🔄 Скинути")
        btn_reset.setStyleSheet("background:#e74c3c; color:white; padding:4px 8px;")
        btn_reset.clicked.connect(self._reset_defaults)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        
        btn_box = QDialogButtonBox()
        
        # Створюємо кнопки і відразу зберігаємо посилання на них у змінні
        btn_apply = btn_box.addButton("Застосувати", QDialogButtonBox.ButtonRole.ApplyRole)
        btn_ok = btn_box.addButton("OK", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel = btn_box.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)

        # Підключаємо сигнали безпосередньо до змінних (БЕЗ використання StandardButton)
        btn_ok.clicked.connect(self._save_and_accept)
        btn_cancel.clicked.connect(self.reject)
        btn_apply.clicked.connect(self._apply_only)
        
        btn_row.addWidget(btn_box)
        root.addLayout(btn_row)

    def _pick_color(self, key, ctype, btn, default_hex):
        from PyQt6.QtWidgets import QColorDialog
        from PyQt6.QtGui import QColor
        cur = self.state.load_setting(f"color_{ctype}_{key}", default_hex or "#ff000000")
        dlg = QColorDialog(QColor(cur), self)
        dlg.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        if dlg.exec():
            new_hex = dlg.currentColor().name(QColor.NameFormat.HexArgb)
            self.state.save_setting(f"color_{ctype}_{key}", new_hex)
            btn.setStyleSheet(f"background:{new_hex}; border:1px solid #888;")
            self._apply_only() # Миттєво показуємо колір на кресленні!

    def _apply_only(self):
        """Зберігає налаштування та оновлює сцену без закриття діалогу."""
        for key, ctrls in self._ctrls.items():
            self.state.save_setting(f"line_width_{key}", ctrls["spin"].value())
            style_val = self.PEN_STYLES.get(ctrls["combo"].currentText(), "solid")
            self.state.save_setting(f"line_style_{key}", style_val)
        for key, cb in self._vis_checks.items():
            self.state.save_setting(f"visible_{key}", cb.isChecked())
            
        # Смикаємо головне вікно, щоб воно перемалювало графіку!
        if self.parent():
            if hasattr(self.parent(), 'refresh_scene_styles'):
                self.parent().refresh_scene_styles()
            if hasattr(self.parent(), '_refresh_ghost_preview'):
                self.parent()._refresh_ghost_preview()

    def _save_and_accept(self):
        """Для кнопки ОК - застосовуємо і закриваємо."""
        self._apply_only()
        self.accept()

    def _reset_defaults(self):
        from PyQt6.QtWidgets import QMessageBox
        if QMessageBox.question(self, "Скидання", "Повернути дефолтні?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) != QMessageBox.StandardButton.Yes:
            return
        for key, _lbl, has_fill, def_c, def_f, def_w, def_s in self.LAYERS:
            self.state.save_setting(f"color_contour_{key}", def_c)
            self.state.save_setting(f"line_width_{key}", def_w)
            self.state.save_setting(f"line_style_{key}", def_s)
            self.state.save_setting(f"visible_{key}", True)
            if has_fill and def_f:
                self.state.save_setting(f"color_fill_{key}", def_f)
            c = self._ctrls.get(key, {})
            if "btn_c" in c: c["btn_c"].setStyleSheet(f"background:{def_c}; border:1px solid #888;")
            if "btn_f" in c and def_f: c["btn_f"].setStyleSheet(f"background:{def_f}; border:1px solid #888;")
            if "spin" in c: c["spin"].setValue(def_w)
            if "combo" in c: c["combo"].setCurrentText(self.STYLE_DISPLAY.get(def_s, "Суцільна"))
        for cb in self._vis_checks.values():
            cb.setChecked(True)


class BatchDialog(QDialog):
    """
    Діалог налаштування та запуску пакетної обробки PDF.

    Режими:
      • Ручний      — сторінка за сторінкою вручну, CSV за запитом.
      • Напівавто   — авто-скан, пауза після кожної, CSV за запитом.
      • Автоматичний — обробити діапазон у фоні, CSV автоматично після завершення.
    """
    # Сигнал: (mode, from_0based, to_0based_exclusive, skip_cached)
    batch_requested = pyqtSignal(str, int, int, bool)

    def __init__(self, total_pages: int, current_page: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Пакетна обробка")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.total_pages = total_pages

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # ── Вибір режиму ──────────────────────────────────────────────────────
        grp_mode = QGroupBox("Режим обробки")
        ml = QVBoxLayout(grp_mode)

        self.rb_manual   = QRadioButton("🖱  Ручний")
        self.rb_semiauto = QRadioButton("⏭  Напівавтоматичний")
        self.rb_headless = QRadioButton("🤖  Автоматичний (Headless)")
        self.rb_manual.setChecked(True)

        desc_manual = QLabel(
            "Користувач натискає «Знайти» на кожній сторінці, підтверджує об'єкти.\n"
            "Збереження в SQLite та CSV — за запитом користувача."
        )
        desc_semi = QLabel(
            "Програма автоматично сканує сторінку, виводить результат і чекає\n"
            "натискання «Далі ▶». Підтверджені об'єкти зберігаються в SQLite.\n"
            "CSV — за запитом у кінці."
        )
        desc_headless = QLabel(
            "Програма обробляє вказаний діапазон у фоні без зупинок.\n"
            "Всі знайдені об'єкти зберігаються в SQLite, CSV — автоматично після завершення."
        )
        for lbl in (desc_manual, desc_semi, desc_headless):
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #555; margin-left: 20px; margin-bottom: 4px;")

        ml.addWidget(self.rb_manual);   ml.addWidget(desc_manual)
        ml.addWidget(self.rb_semiauto); ml.addWidget(desc_semi)
        ml.addWidget(self.rb_headless); ml.addWidget(desc_headless)
        root.addWidget(grp_mode)

        # ── Діапазон сторінок ─────────────────────────────────────────────────
        self.grp_range = QGroupBox("Діапазон сторінок")
        rl = QFormLayout(self.grp_range)

        self.spin_from = QSpinBox()
        self.spin_from.setRange(1, total_pages)
        self.spin_from.setValue(current_page + 1)
        self.spin_from.setSuffix(f"  (з {total_pages})")

        self.spin_to = QSpinBox()
        self.spin_to.setRange(1, total_pages)
        self.spin_to.setValue(total_pages)
        self.spin_to.setSuffix(f"  (з {total_pages})")

        self.chk_skip_cached = QCheckBox("Пропускати сторінки, що вже є в кеші SQLite")
        self.chk_skip_cached.setChecked(True)

        rl.addRow("Від сторінки:", self.spin_from)
        rl.addRow("До сторінки (включно):", self.spin_to)
        rl.addRow(self.chk_skip_cached)
        root.addWidget(self.grp_range)

        # Логіка вмикання/вимикання групи діапазону
        self.grp_range.setEnabled(False)

        def _on_mode_changed():
            is_headless = self.rb_headless.isChecked()
            is_semi     = self.rb_semiauto.isChecked()
            self.grp_range.setEnabled(is_headless or is_semi)

        self.rb_manual.toggled.connect(_on_mode_changed)
        self.rb_semiauto.toggled.connect(_on_mode_changed)
        self.rb_headless.toggled.connect(_on_mode_changed)

        # ── Кнопки ───────────────────────────────────────────────────────────
        btn_box = QDialogButtonBox()
        btn_box.addButton("▶ Запустити", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.addButton("Скасувати",   QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def _on_accept(self):
        p_from = self.spin_from.value() - 1          # 0-based
        p_to   = self.spin_to.value()                # 0-based exclusive (range end)
        skip   = self.chk_skip_cached.isChecked()

        if self.rb_manual.isChecked():
            self.batch_requested.emit("manual", 0, 0, False)
            self.accept()
            return

        if self.rb_semiauto.isChecked():
            if p_from >= p_to:
                QMessageBox.warning(self, "Помилка", "«Від» має бути менше «До»!")
                return
            self.batch_requested.emit("semiauto", p_from, p_to, skip)
            self.accept()
            return

        # Headless
        if p_from >= p_to:
            QMessageBox.warning(self, "Помилка", "«Від» має бути менше «До»!")
            return
        self.batch_requested.emit("headless", p_from, p_to, skip)
        self.accept()


# ── Діалог вибору діапазону для CSV-експорту ─────────────────────────────────
class ExportRangeDialog(QDialog):
    """Питає користувача: які сторінки включити до CSV-файлу."""

    def __init__(self, total_pages: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Експорт у CSV")
        self.setModal(True)
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)

        grp = QGroupBox("Які сторінки включити?")
        gl = QVBoxLayout(grp)

        self.rb_all      = QRadioButton("Всі сторінки з кешу SQLite")
        self.rb_approved = QRadioButton("Тільки підтверджені (status = approved)")
        self.rb_range    = QRadioButton("Вказати діапазон вручну")
        self.rb_all.setChecked(True)

        rng_widget = QWidget()
        rng_layout = QHBoxLayout(rng_widget)
        rng_layout.setContentsMargins(20, 0, 0, 0)
        self.spin_from = QSpinBox(); self.spin_from.setRange(1, total_pages); self.spin_from.setValue(1)
        self.spin_to   = QSpinBox(); self.spin_to.setRange(1, total_pages);   self.spin_to.setValue(total_pages)
        rng_layout.addWidget(QLabel("Від:")); rng_layout.addWidget(self.spin_from)
        rng_layout.addWidget(QLabel("До:"));  rng_layout.addWidget(self.spin_to)
        rng_widget.setEnabled(False)
        self.rb_range.toggled.connect(rng_widget.setEnabled)

        for w in (self.rb_all, self.rb_approved, self.rb_range, rng_widget):
            gl.addWidget(w)
        root.addWidget(grp)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def get_filter(self) -> dict:
        """Повертає словник з параметрами фільтру для export_csv_from_cache."""
        if self.rb_all.isChecked():
            return {"mode": "all"}
        if self.rb_approved.isChecked():
            return {"mode": "approved"}
        return {
            "mode": "range",
            "from": self.spin_from.value() - 1,
            "to":   self.spin_to.value(),        # exclusive
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ГОЛОВНЕ ВІКНО СТУДІЇ
# ═══════════════════════════════════════════════════════════════════════════════
class TemplateStudioMainWindow(QMainWindow):
    
    def __init__(self, state: SessionState):
        super().__init__()
        self.state = state
        self.setWindowTitle(f"EPLAN Template Studio - {os.path.basename(self.state.pdf_path)}")
        self.resize(1500, 900)
        
        self.db_window = None

        self.current_base_raw = None     
        self.current_selected_node = None 
        self.config_raw_elements = {}    
        
        # --- Стан пакетної обробки ---
        self.batch_worker: BatchWorker | None = None
        self._batch_mode   = "manual"   # "manual" | "semiauto" | "headless"
        self._batch_range  = range(0, 0)
        self._batch_templates: list = []
        
        self.state.template_changed.connect(self.update_json_preview)
        
        self.init_ui()
        self.scan_template_library()

        # === ЗАПУСК НАВІГАТОРА ===
        self.list_thumbnails.clear()
        self.original_thumbnails.clear()
        self.thumb_worker = ThumbnailWorker(self.state.pdf_path, max_width=160)
        self.thumb_worker.thumbnail_ready.connect(self.on_thumbnail_ready)
        self.thumb_worker.start()
        # ============================================================

        # Сесія вже завантажена через StartDashboard (recovery / new / .epss)
        self.load_pdf_page(self.state.page_num)
        self.switch_mode("VALIDATE")

    def init_ui(self):
        self.skip_zoom = False
        self.toolbar = QToolBar("Studio Toolbar")
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)


        # Десь поруч із кнопками режимів у init_ui:
        # --- Пункт 7a: Кнопка відкриття нового PDF ---
        self.btn_open_pdf = QPushButton("📂 Відкрити PDF")
        self.btn_open_pdf.setStyleSheet("background-color: #34495e; color: white; font-weight: bold;")
        self.btn_open_pdf.clicked.connect(self.action_open_pdf)
        self.toolbar.addWidget(self.btn_open_pdf)
        self.btn_save_progress = QPushButton("💾 Зберегти")
        self.btn_save_progress.setStyleSheet("background-color: #2980b9; color: white; font-weight: bold;")
        self.btn_save_progress.clicked.connect(self.manual_save_to_db)
        self.toolbar.addWidget(self.btn_save_progress)

        self.btn_save_session_as = QPushButton("💾 Зберегти сесію як...")
        self.btn_save_session_as.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold;")
        self.btn_save_session_as.clicked.connect(self.action_save_session_as)
        self.toolbar.addWidget(self.btn_save_session_as)

        self.btn_open_session = QPushButton("📂 Відкрити сесію")
        self.btn_open_session.setStyleSheet("background-color: #2c3e50; color: white; font-weight: bold;")
        self.btn_open_session.clicked.connect(self.action_open_session)
        self.toolbar.addWidget(self.btn_open_session)

        
        self.btn_mode_config = QPushButton("🛠 КОНФІГУРАЦІЯ")
        self.btn_mode_config.setCheckable(True)
        self.btn_mode_validate = QPushButton("👁 ВАЛІДАЦІЯ ТА ПОШУК")
        self.btn_mode_validate.setCheckable(True)
        self.btn_mode_validate.setChecked(True)
        self.btn_mode_export = QPushButton("📊 ЕКСПОРТ CSV")
        
        self.toolbar.addWidget(self.btn_mode_config)
        self.toolbar.addWidget(self.btn_mode_validate)
        self.toolbar.addWidget(self.btn_mode_export)

        # === ДОДАТИ ЦЕЙ БЛОК: КНОПКА "БАЗА ДАНИХ" ===
        self.btn_database = QPushButton("📂 База даних")
        self.btn_database.setStyleSheet(
            "background-color: #27ae60; color: white; font-weight: bold; padding: 0px 10px;")
        self.btn_database.setFixedHeight(self.btn_mode_export.sizeHint().height())
        self.btn_database.clicked.connect(self.open_database_window)
        self.toolbar.addWidget(self.btn_database)
        # ============================================

        self.btn_settings = QPushButton("⚙ Налаштування")
        self.btn_settings.setStyleSheet(
            "background-color: #607d8b; color: white; font-weight: bold; padding: 0px 10px;")
        self.btn_settings.setFixedHeight(self.btn_mode_export.sizeHint().height())
        self.toolbar.addWidget(self.btn_settings)

        self.scene = QGraphicsScene()
        self.view = ZoomableView(self.scene)
        # Живий примарний контур (CONFIG-режим)
        self.ghost_preview = GhostPreviewItem(self.scene)
        self.setCentralWidget(self.view)
        self.view.rect_drawn.connect(self.on_tz_rect_drawn)
        self.view.point_snapped.connect(self.on_anchor_snapped)

        self.left_dock = QDockWidget("Структура", self)
        self.left_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        left_widget = QWidget()
        self.left_layout = QVBoxLayout(left_widget)
        
        group_lib = QGroupBox("Бібліотека шаблонів")
        group_lib.setToolTip("Бібліотека JSON-шаблонів для розпізнавання графічних об'єктів.\n"
                             "✓ — шаблон активний для сканування.\n"
                             "Пріоритет — порядок обробки (0 = перший, 999 = останній).")
        lib_layout = QVBoxLayout()
        self.list_templates = QTreeWidget()
        self.list_templates.setHeaderLabels(["Шаблон", "Пріоритет"])
        self.list_templates.setColumnWidth(0, 180)
        self.list_templates.setColumnWidth(1, 60)
        self.list_templates.setRootIsDecorated(False)
        self.list_templates.setToolTip("Клік — вибрати шаблон для редагування.\n"
                                       "Галочка — увімкнути/вимкнути для сканування.\n"
                                       "Колонка «Пріоритет» — подвійний клік для зміни.")
        lib_layout.addWidget(self.list_templates)
        
        btn_layout = QHBoxLayout()
        self.btn_new_tmpl = QPushButton("➕ Новий")
        self.btn_clone_tmpl = QPushButton("📑 Дублювати")
        self.btn_save_tmpl = QPushButton("💾 Зберегти")
        self.btn_edit_tmpl = QPushButton("📐 Редагувати на стор.")
        self.btn_edit_tmpl.setStyleSheet("background-color: #16a085; color: white;")
        self.btn_edit_tmpl.setToolTip("Розмістити вибраний шаблон на сторінці для редагування")

        # === НОВА КНОПКА ВИДАЛЕННЯ ===
        self.btn_del_tmpl = QPushButton("🗑 Видалити")
        self.btn_del_tmpl.setStyleSheet("background-color: #c0392b; color: white;") # Зробимо її червоною
        # =============================

        btn_layout.addWidget(self.btn_new_tmpl)
        btn_layout.addWidget(self.btn_clone_tmpl)
        btn_layout.addWidget(self.btn_save_tmpl)
        btn_layout.addWidget(self.btn_del_tmpl) # <-- ПЕРЕВІРТЕ ЦЕЙ РЯДОК (додавання у віджет)
        btn_layout.addWidget(self.btn_edit_tmpl)


        lib_layout.addLayout(btn_layout)
        group_lib.setLayout(lib_layout)
        self.left_layout.addWidget(group_lib)
        
        self.container_config_tree = QWidget()
        config_tree_layout = QVBoxLayout(self.container_config_tree)
        config_tree_layout.setContentsMargins(0,0,0,0)
        
        btn_add_layout = QHBoxLayout()
        self.btn_add_var = QPushButton("+ Variable")
        self.btn_add_variant = QPushButton("+ Variant")
        self.btn_add_tz = QPushButton("+ Text Zone")
        self.btn_add_sz = QPushButton("+ Service Zone")
        self.btn_add_sz.setStyleSheet("background-color: #16a085; color: white;")

        btn_add_layout.addWidget(self.btn_add_var)
        btn_add_layout.addWidget(self.btn_add_variant)
        
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["Ім'я / Роль", "Значення"])
        config_tree_layout.addLayout(btn_add_layout)
        config_tree_layout.addWidget(self.btn_add_tz)
        config_tree_layout.addWidget(self.btn_add_sz)
        config_tree_layout.addWidget(self.tree_widget)
        self.left_layout.addWidget(self.container_config_tree)

        self.group_found_list = QGroupBox("Знайдені об'єкти (☑ - Експортувати)")
        found_layout = QVBoxLayout()

        # --- НОВА ТАБЛИЦЯ ЗАМІСТЬ СПИСКУ ---
        self.table_view = QTableView()
        self.table_model = DataGridModel()
        # ... ваш код налаштування таблиці (QTableView) ...
        self.table_view.setModel(self.table_model)
        
        # --- НАВІГАТОР СТОРІНОК ---
        # ═══════════════════════════════════════════════════════════════════
        # НИЖНЯ ПАНЕЛЬ: Навігатор сторінок + Управління (об'єднано)
        # ═══════════════════════════════════════════════════════════════════
        self.nav_dock = QDockWidget("Навігатор", self)
        self.nav_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.nav_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        nav_root = QWidget()
        nav_vbox = QVBoxLayout(nav_root)
        nav_vbox.setContentsMargins(4, 2, 4, 2)
        nav_vbox.setSpacing(2)

        # ── Рядок керування ─────────────────────────────────────────────────
        BTN = "color:white; font-weight:bold; padding:5px 10px;"
        controls_row = QHBoxLayout()
        controls_row.setSpacing(4)

        self.btn_first_page = QPushButton("⏮")
        self.btn_first_page.setFixedSize(30, 28)
        self.btn_prev_page = QPushButton("◀")
        self.btn_prev_page.setFixedSize(30, 28)
        
        self.lbl_page_info = QLabel("Стор. 1")
        self.lbl_page_info.setStyleSheet("font-weight:bold; font-size:13px;")
        
        self.btn_next_page = QPushButton("▶")
        self.btn_next_page.setFixedSize(30, 28)
        self.btn_last_page = QPushButton("⏭")
        self.btn_last_page.setFixedSize(30, 28)

        self.nav_jump_edit = QLineEdit()
        self.nav_jump_edit.setPlaceholderText("№ стор.")
        self.nav_jump_edit.setFixedWidth(65)
        self.nav_jump_edit.setToolTip("Введіть номер і натисніть Enter")
        self.nav_jump_edit.returnPressed.connect(self._nav_jump_by_input)

        self.nav_search = QLineEdit()
        self.nav_search.setPlaceholderText("🔍 Фільтр…")
        self.nav_search.setClearButtonEnabled(True)
        self.nav_search.setFixedWidth(90)
        self.nav_search.textChanged.connect(self._nav_filter)

        self.nav_stats_label = QLabel("Сторінок: —")
        self.nav_stats_label.setStyleSheet("color:#555; font-size:11px;")

        self.btn_approve_all = QPushButton("✅ Підтвердити всі")
        self.btn_approve_all.setStyleSheet("background:#27ae60;" + BTN)

        self.btn_run_engine = QPushButton("🔍 Знайти та Витягти текст")
        self.btn_run_engine.setStyleSheet("background:#f39c12;" + BTN)

        self.chk_skip_approved = QCheckBox("Пропускати approved")
        self.chk_skip_approved.setChecked(True)
        self.chk_skip_approved.setToolTip("При скануванні зберігати об'єкти зі статусом Approved")

        self.btn_batch = QPushButton("⚡ Пакетна обробка…")
        self.btn_batch.setStyleSheet("background:#8e44ad;" + BTN)

        self.btn_semiauto_next = QPushButton("▶▶ Далі (Напівавто)")
        self.btn_semiauto_next.setStyleSheet("background:#2980b9;" + BTN)
        self.btn_semiauto_next.setVisible(False)

        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 100)
        self.batch_progress.setVisible(False)
        self.batch_progress.setFixedWidth(160)
        self.batch_progress.setFormat("%v/%m")

        self.btn_batch_stop = QPushButton("⏹ Стоп")
        self.btn_batch_stop.setStyleSheet("background:#c0392b;" + BTN)
        self.btn_batch_stop.setVisible(False)

        controls_row.addWidget(self.lbl_page_info)
        controls_row.addWidget(self.btn_first_page)
        controls_row.addWidget(self.btn_prev_page)
        controls_row.addWidget(self.nav_jump_edit)
        controls_row.addWidget(self.btn_next_page)
        controls_row.addWidget(self.btn_last_page)
        controls_row.addWidget(self.nav_search)
        controls_row.addWidget(self.nav_stats_label)
        controls_row.addStretch()
        controls_row.addWidget(self.batch_progress)
        controls_row.addWidget(self.btn_semiauto_next)
        controls_row.addWidget(self.btn_batch_stop)
        controls_row.addWidget(self.btn_approve_all)
        controls_row.addWidget(self.btn_batch)
        controls_row.addWidget(self.btn_run_engine)
        controls_row.addWidget(self.chk_skip_approved)       

        nav_vbox.addLayout(controls_row)

        # ── Горизонтальна смуга мініатюр ────────────────────────────────────
        # ── Горизонтальна смуга мініатюр (Адаптовано під Ландшафт A3) ───────
        THUMB_W, THUMB_H = 160, 115  # Ландшафтні пропорції
        self._thumb_item_size = QSize(THUMB_W + 6, THUMB_H + 24)

        self.list_thumbnails = QListWidget()
        self.list_thumbnails.setFlow(QListWidget.Flow.LeftToRight)
        self.list_thumbnails.setWrapping(False)
        self.list_thumbnails.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_thumbnails.setIconSize(QSize(THUMB_W, THUMB_H))
        self.list_thumbnails.setResizeMode(QListWidget.ResizeMode.Fixed)
        self.list_thumbnails.setGridSize(self._thumb_item_size)
        self.list_thumbnails.setSpacing(4)
        self.list_thumbnails.setUniformItemSizes(True)
        self.list_thumbnails.setFixedHeight(THUMB_H + 45) # Запас під текст і скролбар
        self.list_thumbnails.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_thumbnails.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_thumbnails.setStyleSheet("""
            QListWidget {
                background: #f0f0f0;
                border: 1px solid #ccc;
                border-radius: 3px;
            }
            QListWidget::item { color:#222; padding:1px; border-radius:2px; }
            QListWidget::item:selected { background:#1a6ebd; color:white; }
            QListWidget::item:hover:!selected { background:#ddeeff; }
        """)
        self.list_thumbnails.itemClicked.connect(self.on_thumbnail_clicked)
        nav_vbox.addWidget(self.list_thumbnails)

        self.nav_dock.setWidget(nav_root)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.nav_dock)

        self.original_thumbnails = {}
        self.thumb_worker = None

        # --- ПІДКЛЮЧЕННЯ СИГНАЛІВ ТА ПОДІЙ ---
        self.btn_mode_config.clicked.connect(lambda: self.switch_mode("CONFIG"))
        self.btn_mode_validate.clicked.connect(lambda: self.switch_mode("VALIDATE"))
        self.btn_mode_export.clicked.connect(lambda: self.export_csv_from_cache(auto=False))
        self.btn_settings.clicked.connect(self.open_settings_dialog)

        self.btn_edit_tmpl.clicked.connect(self.action_start_template_placement)

        self.btn_first_page.clicked.connect(self._nav_jump_first)
        self.btn_prev_page.clicked.connect(lambda: self.change_page(-1))
        self.btn_next_page.clicked.connect(lambda: self.change_page(1))
        self.btn_last_page.clicked.connect(self._nav_jump_last)
        self.btn_approve_all.clicked.connect(self.approve_all_validations)
        self.btn_run_engine.clicked.connect(self.run_validation)
        self.btn_batch.clicked.connect(self.open_batch_dialog)
        self.btn_semiauto_next.clicked.connect(self.semiauto_next)
        self.btn_batch_stop.clicked.connect(self.stop_batch)
        # --- Пункт 3: Клавіатурна навігація по сторінках ---
        QShortcut(QKeySequence(Qt.Key.Key_Left), self).activated.connect(
            lambda: self._keyboard_nav(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self).activated.connect(
            lambda: self._keyboard_nav(1))
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self).activated.connect(
            lambda: self._keyboard_nav(-1))
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self).activated.connect(
            lambda: self._keyboard_nav(1))
        QShortcut(QKeySequence(Qt.Key.Key_Home), self).activated.connect(
            lambda: self._keyboard_nav_jump(0))
        QShortcut(QKeySequence(Qt.Key.Key_End), self).activated.connect(
            self._nav_jump_last)
        QShortcut(QKeySequence("Ctrl+G"), self).activated.connect(
            lambda: self.nav_jump_edit.setFocus())

        # --- ПІДКЛЮЧЕННЯ РЕЖИМУ ІЗОЛЯЦІЇ ---
        self.table_view.selectionModel().selectionChanged.connect(self.on_table_row_selected)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.verticalHeader().hide()
        self.table_view.selectionModel().selectionChanged.connect(self.on_table_selection_changed)
        self.table_model.dataChanged.connect(self.on_table_data_changed)
        found_layout.addWidget(self.table_view)

        self.btn_export_from_list = QPushButton("📊 Експортувати вибрані")
        self.btn_export_from_list.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 8px;")
        self.btn_export_from_list.clicked.connect(lambda: self.export_csv_from_cache(auto=False))
        found_layout.addWidget(self.btn_export_from_list)

        self.group_found_list.setLayout(found_layout)
        self.left_layout.addWidget(self.group_found_list)
        self.group_found_list.hide()

        self.left_dock.setWidget(left_widget)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.left_dock)

        # ── Правий Dock: CONFIG властивості + VALIDATE інспектор ──────────────
        self.right_dock = QDockWidget("Властивості / Результат", self)
        self.right_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        self.right_main_stack = QStackedWidget()

        config_container = QWidget()
        config_layout = QVBoxLayout(config_container)
        config_layout.setContentsMargins(0, 0, 0, 0)
        group_props = QGroupBox("Властивості елемента")
        props_layout = QVBoxLayout(group_props)
        self.prop_stack = QStackedWidget()

        self.w_blank = QWidget(); self.prop_stack.addWidget(self.w_blank)

        self.w_geom = QWidget(); self.form_geom = QFormLayout(self.w_geom)
        self.edit_role = QLineEdit()
        self.edit_type = QComboBox(); self.edit_type.addItems(["H", "V", "D", "rect", "arc", "ellipse", "path", "image"])
        self.edit_mode = QComboBox(); self.edit_mode.addItems(["single", "collect"])
        self.chk_is_base = QCheckBox("Базовий об'єкт (is_base)")
        self.edit_x0_off = QLineEdit(); self.edit_y0_off = QLineEdit(); self.edit_len_rat = QLineEdit()
        self.edit_wid_rat = QLineEdit(); self.edit_rad_rat = QLineEdit()
        self.edit_cnt_min = QLineEdit(); self.edit_cnt_max = QLineEdit()
        self.edit_xy_tol = QLineEdit(); self.edit_lw_tol = QLineEdit()
        self.edit_pl_rat = QLineEdit(); self.edit_pl_rat_w = QLineEdit(); self.edit_pl_rat_h = QLineEdit()
        self.form_geom.addRow("Role:", self.edit_role)
        self.form_geom.addRow("Type:", self.edit_type)
        self.form_geom.addRow("Mode:", self.edit_mode)
        self.form_geom.addRow("", self.chk_is_base)
        self.form_geom.addRow("x0_offset_ratio:", self.edit_x0_off)
        self.form_geom.addRow("y0_offset_ratio:", self.edit_y0_off)
        self.form_geom.addRow("length_ratio:", self.edit_len_rat)
        self.form_geom.addRow("width_ratio (rect):", self.edit_wid_rat)
        self.form_geom.addRow("radius_ratio (arc):", self.edit_rad_rat)
        self.form_geom.addRow("page_ratio (H/V):", self.edit_pl_rat)
        self.form_geom.addRow("page_ratio_W (Складні):", self.edit_pl_rat_w)
        self.form_geom.addRow("page_ratio_H (Складні):", self.edit_pl_rat_h)
        self.form_geom.addRow("count (min) - collect:", self.edit_cnt_min)
        self.form_geom.addRow("count (max) - collect:", self.edit_cnt_max)
        self.form_geom.addRow("xy_tol:", self.edit_xy_tol)
        self.form_geom.addRow("lw_tol:", self.edit_lw_tol)
        self.prop_stack.addWidget(self.w_geom)

        self.w_constr = QWidget(); self.form_constr = QFormLayout(self.w_constr)
        self.edit_ar_min = QLineEdit(); self.edit_ar_max = QLineEdit()
        self.edit_ihl_min = QLineEdit(); self.edit_ihl_max = QLineEdit()
        self.form_constr.addRow("Aspect Ratio (min):", self.edit_ar_min)
        self.form_constr.addRow("Aspect Ratio (max):", self.edit_ar_max)
        self.form_constr.addRow("inner_h_lines_count (min):", self.edit_ihl_min)
        self.form_constr.addRow("inner_h_lines_count (max):", self.edit_ihl_max)
        self.prop_stack.addWidget(self.w_constr)

        # w_anchor — точка захоплення
        self.edit_anch_x = QLineEdit(); self.edit_anch_y = QLineEdit()
        self.edit_anch_w = QLineEdit(); self.edit_anch_h = QLineEdit()
        self.chk_anch_exp = QCheckBox("Export X/Y")
        self.w_anchor = QWidget()
        self.form_anchor = QFormLayout(self.w_anchor)
        self.btn_set_anchor = QPushButton("📍 Вказати точку захоплення на кресленні")
        self.btn_set_anchor.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 5px;")
        self.btn_set_anchor.clicked.connect(self.start_anchor_snapping)
        self.form_anchor.addRow(self.btn_set_anchor)
        self.form_anchor.addRow("X (expr):", self.edit_anch_x)
        self.form_anchor.addRow("Y (expr):", self.edit_anch_y)
        self.form_anchor.addRow("Width (expr):", self.edit_anch_w)
        self.form_anchor.addRow("Height (expr):", self.edit_anch_h)
        self.form_anchor.addRow("", self.chk_anch_exp)
        self.prop_stack.addWidget(self.w_anchor)

        self.w_var = QWidget(); self.form_var = QFormLayout(self.w_var)
        self.edit_var_name = QLineEdit(); self.edit_var_expr = QLineEdit()
        self.form_var.addRow("Name:", self.edit_var_name); self.form_var.addRow("Expression:", self.edit_var_expr)
        self.prop_stack.addWidget(self.w_var)

        self.w_variant = QWidget(); self.form_variant = QFormLayout(self.w_variant)
        self.edit_variant_name = QLineEdit(); self.edit_variant_cond = QLineEdit()
        self.form_variant.addRow("Variant Name:", self.edit_variant_name); self.form_variant.addRow("Condition:", self.edit_variant_cond)
        self.prop_stack.addWidget(self.w_variant)

        self.w_tz = QWidget(); self.form_tz = QFormLayout(self.w_tz)
        draw_layout = QHBoxLayout()
        self.combo_tz_ref = QComboBox() 
        self.btn_draw_tz = QPushButton("🖍 Виділити мишкою")
        self.btn_draw_tz.setStyleSheet("background-color: #9b59b6; color: white; font-weight: bold; border-radius: 4px; padding: 5px;")
        self.btn_draw_tz.clicked.connect(self.view.start_drawing) 
        
        draw_layout.addWidget(QLabel("Прив'язка:")); draw_layout.addWidget(self.combo_tz_ref)
        draw_layout.addWidget(self.btn_draw_tz)
        self.form_tz.addRow(draw_layout)
        
        self.edit_tz_field = QLineEdit()
        self.edit_tz_x0 = QLineEdit(); self.edit_tz_y0 = QLineEdit(); self.edit_tz_x1 = QLineEdit(); self.edit_tz_y1 = QLineEdit()
        self.chk_tz_multi = QCheckBox("Multiline"); self.edit_tz_join = QLineEdit()
        self.edit_tz_repeat = QLineEdit(); self.edit_tz_collect = QComboBox(); self.edit_tz_collect.addItems(["", "join"]); self.edit_tz_sep = QLineEdit()
        
        self.form_tz.addRow("Field Name:", self.edit_tz_field)
        self.form_tz.addRow("x0 (expr):", self.edit_tz_x0); self.form_tz.addRow("y0 (expr):", self.edit_tz_y0)
        self.form_tz.addRow("x1 (expr):", self.edit_tz_x1); self.form_tz.addRow("y1 (expr):", self.edit_tz_y1)
        self.form_tz.addRow("Repeat over:", self.edit_tz_repeat)
        self.form_tz.addRow("Collect (mode):", self.edit_tz_collect); self.form_tz.addRow("Separator:", self.edit_tz_sep)
        self.form_tz.addRow("", self.chk_tz_multi); self.form_tz.addRow("Join char (\\n):", self.edit_tz_join)
        self.prop_stack.addWidget(self.w_tz)
        
        # === Service Zone Properties ===
        self.w_sz = QWidget(); self.form_sz = QFormLayout(self.w_sz)
        sz_draw_layout = QHBoxLayout()
        self.combo_sz_ref = QComboBox()
        self.btn_draw_sz = QPushButton("🖍 Виділити мишкою")
        self.btn_draw_sz.setStyleSheet("background-color: #16a085; color: white; font-weight: bold; border-radius: 4px; padding: 5px;")
        self.btn_draw_sz.clicked.connect(self.view.start_drawing)
        sz_draw_layout.addWidget(QLabel("Прив'язка:")); sz_draw_layout.addWidget(self.combo_sz_ref)
        sz_draw_layout.addWidget(self.btn_draw_sz)
        self.form_sz.addRow(sz_draw_layout)
        
        self.edit_sz_field = QLineEdit()
        self.edit_sz_x0 = QLineEdit(); self.edit_sz_y0 = QLineEdit()
        self.edit_sz_x1 = QLineEdit(); self.edit_sz_y1 = QLineEdit()
        self.chk_sz_required = QCheckBox("Required (відкидати об'єкт якщо порожнє)")
        self.chk_sz_export = QCheckBox("Export (включити в CSV)")
        
        self.form_sz.addRow("Field Name:", self.edit_sz_field)
        self.form_sz.addRow("x0 (expr):", self.edit_sz_x0); self.form_sz.addRow("y0 (expr):", self.edit_sz_y0)
        self.form_sz.addRow("x1 (expr):", self.edit_sz_x1); self.form_sz.addRow("y1 (expr):", self.edit_sz_y1)
        self.form_sz.addRow("", self.chk_sz_required)
        self.form_sz.addRow("", self.chk_sz_export)
        self.prop_stack.addWidget(self.w_sz)

        self.w_pins = QWidget(); self.form_pins = QFormLayout(self.w_pins)
        self.edit_pin_search = QLineEdit(); self.edit_pin_len = QLineEdit(); self.edit_pin_sides = QLineEdit()
        self.edit_pin_x0_min = QLineEdit(); self.edit_pin_x0_max = QLineEdit()
        self.form_pins.addRow("Search margin ratio:", self.edit_pin_search); self.form_pins.addRow("Max length ratio:", self.edit_pin_len)
        self.form_pins.addRow("x0_in_range [min]:", self.edit_pin_x0_min); self.form_pins.addRow("x0_in_range [max]:", self.edit_pin_x0_max)
        self.form_pins.addRow("Sides (comma separated):", self.edit_pin_sides)
        self.prop_stack.addWidget(self.w_pins)

        self.w_out = QWidget(); self.form_out = QFormLayout(self.w_out)
        self.edit_out_fields = QTextEdit(); self.edit_out_fields.setFixedHeight(80)
        self.form_out.addRow("CSV Fields (comma separated):", self.edit_out_fields)
        self.prop_stack.addWidget(self.w_out)
        # === ДОДАНО: Вкладка Налаштувань Шаблону ===
        self.w_settings = QWidget(); self.form_settings = QFormLayout(self.w_settings)
        self.chk_page_data = QCheckBox("Page_Data (Глобальний штамп сторінки)")
        self.form_settings.addRow("", self.chk_page_data)
        self.spin_priority = QSpinBox()
        self.spin_priority.setRange(0, 999)
        self.spin_priority.setValue(50)
        self.spin_priority.setToolTip("Пріоритет сканування (0 = найвищий, 999 = найнижчий)")
        self.form_settings.addRow("Пріоритет (priority):", self.spin_priority)
        self.prop_stack.addWidget(self.w_settings)
        # ===========================================

        self.prop_scroll = QScrollArea(); self.prop_scroll.setWidgetResizable(True)
        self.prop_scroll.setWidget(self.prop_stack); props_layout.addWidget(self.prop_scroll)
        
        self.json_preview = QTextEdit(); self.json_preview.setReadOnly(True)
        self.json_preview.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt; background-color: #f8f9fa;")
        config_layout.addWidget(group_props, stretch=1); config_layout.addWidget(QLabel("Live JSON (Preview):")); config_layout.addWidget(self.json_preview, stretch=1)
        self.right_main_stack.addWidget(config_container)

        validate_container = QWidget()
        validate_layout = QVBoxLayout(validate_container)
        validate_layout.setContentsMargins(0, 0, 0, 0)
        
# --- НОВИЙ ІНСПЕКТОР ВЛАСТИВОСТЕЙ ---
        self.inspector_table = QTableWidget(0, 2)
        self.inspector_table.setHorizontalHeaderLabels(["Поле", "Значення"])
        self.inspector_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.inspector_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.inspector_table.verticalHeader().hide()
        
        # Відстежуємо ручне редагування тексту користувачем
        # Відстежуємо клік (для зуму) та зміну тексту (для редагування)
        self.inspector_table.cellClicked.connect(self.on_inspector_cell_clicked)
        self.inspector_table.cellChanged.connect(self.on_inspector_cell_changed)

        # У методі init_ui, після створення self.inspector_table:
        header = self.inspector_table.horizontalHeader()
        header.setSectionsClickable(True)
        # Підключаємо подвійний клік по заголовку до нашого методу
        header.sectionDoubleClicked.connect(self.on_inspector_header_double_clicked)
        
        # Змінна для відстеження поточного порядку сортування
        self.inspector_sort_order = Qt.SortOrder.AscendingOrder

        validate_layout.addWidget(QLabel("Деталі об'єкта (Можна редагувати):"))
        validate_layout.addWidget(self.inspector_table, stretch=1)


        self.right_main_stack.addWidget(validate_container)
        
        right_layout.addWidget(self.right_main_stack)
        self.right_dock.setWidget(right_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.right_dock)

        # --- ПІДКЛЮЧЕННЯ СИГНАЛІВ ТА ПОДІЙ ---
        
        self.btn_new_tmpl.clicked.connect(self.action_new_template)
        self.btn_clone_tmpl.clicked.connect(self.action_clone_template)
        self.btn_save_tmpl.clicked.connect(self.action_save_template)
        self.btn_del_tmpl.clicked.connect(self.action_delete_template)
        
        self.list_templates.itemClicked.connect(self.on_template_selected)
        self.list_templates.itemChanged.connect(self.on_template_item_changed)


        self.btn_add_var.clicked.connect(self.action_add_variable)
        self.btn_add_variant.clicked.connect(self.action_add_variant)
        self.btn_add_tz.clicked.connect(self.action_add_text_zone)
        self.btn_add_sz.clicked.connect(self.action_add_service_zone)
        self.tree_widget.itemClicked.connect(self.on_tree_item_clicked)
        
        widgets_to_connect = [
            self.edit_role, self.edit_x0_off, self.edit_y0_off, self.edit_len_rat, 
            self.edit_pl_rat, self.edit_pl_rat_w, self.edit_pl_rat_h, 
            self.edit_wid_rat, self.edit_rad_rat, self.edit_cnt_min, self.edit_cnt_max,
            self.edit_xy_tol, self.edit_lw_tol, self.edit_anch_x, self.edit_anch_y, self.edit_anch_w, self.edit_anch_h,
            self.edit_var_name, self.edit_var_expr, self.edit_variant_name, self.edit_variant_cond,
            self.edit_tz_field, self.edit_tz_x0, self.edit_tz_y0, self.edit_tz_x1, self.edit_tz_y1, 
            self.edit_tz_join, self.edit_tz_repeat, self.edit_tz_sep,
            self.edit_sz_field, self.edit_sz_x0, self.edit_sz_y0, self.edit_sz_x1, self.edit_sz_y1,
            self.edit_pin_search, self.edit_pin_len, self.edit_pin_sides, self.edit_pin_x0_min, self.edit_pin_x0_max,
            self.edit_ar_min, self.edit_ar_max, self.edit_ihl_min, self.edit_ihl_max
        ]
        for w in widgets_to_connect: 
            w.textEdited.connect(self.update_properties_to_state)
            w.textEdited.connect(self._refresh_ghost_preview)

        self.chk_sz_required.toggled.connect(self.update_properties_to_state)
        self.chk_sz_export.toggled.connect(self.update_properties_to_state)

        self.edit_out_fields.textChanged.connect(self.update_properties_to_state)
        self.edit_type.currentTextChanged.connect(self.update_properties_to_state)
        self.edit_mode.currentTextChanged.connect(self.update_properties_to_state)
        self.edit_tz_collect.currentTextChanged.connect(self.update_properties_to_state)
        self.chk_is_base.toggled.connect(self.update_properties_to_state)
        self.chk_anch_exp.toggled.connect(self.update_properties_to_state)
        self.chk_tz_multi.toggled.connect(self.update_properties_to_state)
        self.chk_page_data.toggled.connect(self.update_properties_to_state) # ДОДАНО

        self.edit_type.currentTextChanged.connect(self._refresh_ghost_preview)
        self.chk_is_base.toggled.connect(self._refresh_ghost_preview)
        
        self.shortcut_del = QShortcut(QKeySequence(Qt.Key.Key_Delete), self); self.shortcut_del.activated.connect(self.delete_selected)
        self.shortcut_space = QShortcut(QKeySequence(Qt.Key.Key_Space), self); self.shortcut_space.activated.connect(self.approve_selected_validation)
        self.shortcut_w = QShortcut(QKeySequence(Qt.Key.Key_W), self); self.shortcut_w.activated.connect(lambda: self.navigate_validations(-1))
        self.shortcut_s = QShortcut(QKeySequence(Qt.Key.Key_S), self); self.shortcut_s.activated.connect(lambda: self.navigate_validations(1))

        self.init_layers_manager(right_layout)
        
        # --- ПЕРЕХОПЛЕННЯ КЛІКІВ СЦЕНИ ---
        self._original_scene_mouse_press = self.scene.mousePressEvent
        
        def custom_scene_mouse_press(event):
            # 1. Якщо це правий клік — режим Штампа
            if event.button() == Qt.MouseButton.RightButton and self.state.current_mode == "VALIDATE":
                self.action_stamp_template(event.scenePos())
                
            # 2. Якщо це лівий клік, перевіряємо, чи клікнули на порожнє місце
            elif event.button() == Qt.MouseButton.LeftButton:
                item_at_click = self.scene.itemAt(event.scenePos(), self.view.transform())
                # Якщо клікнули не на рамку (ValidationBox), знімаємо ізоляцію
                if not item_at_click or not hasattr(item_at_click, 'is_isolated'):
                    self.action_clear_isolation()
                    self.table_view.clearSelection() # Зкидаємо виділення в таблиці
                    
            # Передаємо клік далі
            self._original_scene_mouse_press(event)
            
        self.scene.mousePressEvent = custom_scene_mouse_press
    
    def approve_selected_validation(self):
        """Змінює статус вибраної рамки (pending/approved) при натисканні Пробілу."""
        if self.state.current_mode != "VALIDATE": return
        
        from graphics import ValidationBox
        for item in self.scene.items():
            if isinstance(item, ValidationBox) and item.is_selected: 
                if hasattr(item, 'row_index'):
                    # Дізнаємося поточний статус
                    current_status = self.table_model.objects[item.row_index].get("status", "pending")
                    new_status = "approved" if current_status == "pending" else "pending"
                    
                    # Оновлюємо дані об'єкта
                    self.table_model.objects[item.row_index]["status"] = new_status
                    item.set_status(new_status)
                    if hasattr(item, 'found_obj'):
                        item.found_obj.status = new_status
                        
                    # Оновлюємо таблицю ліворуч
                    idx = self.table_model.index(item.row_index, 0)
                    self.table_model.dataChanged.emit(idx, self.table_model.index(item.row_index, self.table_model.columnCount()-1))
                    
                    # Зберігаємо та синхронізуємо БД
                    self.manual_save_to_db()
                    self.refresh_inspector()
                break
    def open_database_window(self):
        """Відкриває незалежне вікно бази даних об'єктів."""
        if not self.state.pdf_path:
            QMessageBox.warning(self, "Помилка", "Спочатку відкрийте PDF документ")
            return
            
        if self.db_window is None or sip.isdeleted(self.db_window):
            # ВАЖЛИВО: Передаємо self як другий аргумент (parent=self)
            self.db_window = DatabaseWindow(self.state, self)
            self.db_window.show()
        else:
            self.db_window.show()
            self.db_window.raise_()
            self.db_window.activateWindow()
            self.db_window.refresh_data() # Оновлюємо на всяк випадок

    def action_delete_template(self):
        """Видаляє вибраний шаблон з диска та оновлює список"""
        selected_items = self.list_templates.selectedItems()
        if not selected_items:
            return QMessageBox.warning(self, "Увага", "Виберіть шаблон у списку зліва для видалення!")
        
        item = selected_items[0]
        tmpl_name = item.text(0)
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        
        # Вікно підтвердження (щоб випадково не видалити)
        reply = QMessageBox.question(self, "Підтвердження видалення", 
                                     f"Ви дійсно хочете назавжди видалити шаблон '{tmpl_name}'?\n\nФайл буде видалено з диска.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                import os
                # 1. Видаляємо файл
                if os.path.exists(file_path):
                    os.remove(file_path)
                
                # 2. Видаляємо зі списку UI
                idx = self.list_templates.indexOfTopLevelItem(item)
                if idx >= 0:
                    self.list_templates.takeTopLevelItem(idx)
                
                # 3. Очищаємо робочу область (створюємо порожній шаблон)
                self.action_new_template()
                
                QMessageBox.information(self, "Успіх", f"Шаблон '{tmpl_name}' успішно видалено.")
            except Exception as e:
                QMessageBox.critical(self, "Помилка", f"Не вдалося видалити файл:\n{e}")
    
    def action_start_template_placement(self):
        """Запускає режим розміщення вибраного шаблону на сторінці для редагування."""
        selected_items = self.list_templates.selectedItems()
        if not selected_items:
            return QMessageBox.warning(self, "Увага", "Виберіть шаблон у бібліотеці!")
        
        item = selected_items[0]
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not file_path or not os.path.exists(file_path):
            return QMessageBox.critical(self, "Помилка", "Файл шаблону не знайдено!")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                tmpl_data = json.load(f)
        except Exception as e:
            return QMessageBox.critical(self, "Помилка", f"Не вдалося прочитати:\n{e}")
        
        self._placement_template = tmpl_data
        self._placement_file = file_path
        
        QMessageBox.information(self, "Розміщення шаблону",
            f"Шаблон: {tmpl_data.get('name', 'unknown')}\n\n"
            "Клікніть на точці креслення, куди має прив'язатись Anchor.\n"
            "Активний режим OSNAP — курсор прилипне до кінців/середин ліній.")
        
        self.switch_mode("CONFIG")
        self.view.point_snapped.disconnect()
        self.view.point_snapped.connect(self._on_template_placed)
        self.view.start_snapping()

    def _on_template_placed(self, ax, ay):
        """Обробляє розміщення шаблону: розраховує координати скелета і будує config_raw_elements."""
        # Відновлюємо стандартний обробник
        self.view.point_snapped.disconnect()
        self.view.point_snapped.connect(self.on_anchor_snapped)
        
        tmpl_data = getattr(self, '_placement_template', None)
        if not tmpl_data:
            return
        
        # Завантажуємо шаблон у state
        self.state.update_template(tmpl_data)
        
        # Розраховуємо базові координати: anchor = (ax, ay), base_element починається з anchor
        anch_def = tmpl_data.get('anchor', {})
        ev_test = ExprEvaluator({'base_element': {'x0': 0, 'y0': 0, 'length': 100.0}})
        off_x = ev_test.eval(anch_def.get('x', 'base_element.x0'))
        off_y = ev_test.eval(anch_def.get('y', 'base_element.y0'))
        
        bx = ax - off_x
        by = ay - off_y
        
        # base_element довжина — з page_length_ratio
        base_def = next((l for l in tmpl_data.get('geometry', {}).get('lines', []) 
                        if l.get('is_base')), None)
        if not base_def:
            return QMessageBox.critical(self, "Помилка", "Шаблон не має base_element!")
        
        # Розраховуємо реальну довжину base_element
        page_w = float(tmpl_data.get('_page_width', 595))
        page_h = float(tmpl_data.get('_page_height', 842))
        base_type = base_def.get('type', 'V')
        plr = float(base_def.get('page_length_ratio', 0.1))
        
        if base_type == 'H':
            blen = plr * page_w
        elif base_type == 'V':
            blen = plr * page_h
        else:
            plr_w = float(base_def.get('page_ratio_W', 0.1))
            plr_h = float(base_def.get('page_ratio_H', 0.1))
            blen = max(plr_w * page_w, plr_h * page_h)
        
        # Будуємо config_raw_elements для всіх ліній шаблону
        self.config_raw_elements.clear()
        
        for line_def in tmpl_data.get('geometry', {}).get('lines', []):
            role = line_def.get('role', 'unknown')
            l_type = line_def.get('type', 'H')
            lx0 = bx + float(line_def.get('x0_offset_ratio', 0)) * blen
            ly0 = by + float(line_def.get('y0_offset_ratio', 0)) * blen
            
            if l_type in ('arc', 'rect', 'ellipse', 'image'):
                lw = float(line_def.get('width_ratio', 1.0)) * blen
                lh = float(line_def.get('height_ratio', 1.0)) * blen
                raw = {
                    'type': l_type, 'dir': l_type,
                    'x0': lx0, 'y0': ly0, 'x1': lx0 + lw, 'y1': ly0 + lh,
                    'length': max(lw, lh)
                }
            elif l_type == 'H':
                ll = float(line_def.get('length_ratio', 1.0)) * blen
                raw = {'type': 'line', 'dir': 'H',
                       'x0': lx0, 'y0': ly0, 'x1': lx0 + ll, 'y1': ly0, 'length': ll}
            elif l_type == 'V':
                ll = float(line_def.get('length_ratio', 1.0)) * blen
                raw = {'type': 'line', 'dir': 'V',
                       'x0': lx0, 'y0': ly0, 'x1': lx0, 'y1': ly0 + ll, 'length': ll}
            elif l_type == 'D':
                import math
                lx1 = bx + float(line_def.get('x1_offset_ratio', 1.0)) * blen
                ly1 = by + float(line_def.get('y1_offset_ratio', 1.0)) * blen
                ll = math.hypot(lx1 - lx0, ly1 - ly0)
                raw = {'type': 'line', 'dir': 'D',
                       'x0': lx0, 'y0': ly0, 'x1': lx1, 'y1': ly1, 'length': ll}
            else:
                continue
            
            self.config_raw_elements[role] = raw
            if line_def.get('is_base'):
                self.current_base_raw = raw
        
        self.rebuild_tree()
        self._refresh_ghost_preview()
        
        QMessageBox.information(self, "Готово",
            f"Шаблон розміщено в точці ({ax:.1f}, {ay:.1f}).\n"
            "Тепер можна редагувати: додавати/видаляти лінії, варіанти, текстові зони.\n"
            "Натисніть '💾 Зберегти' для збереження змін.")

    def refresh_inspector(self):
        """Примусово оновлює Інспектор (Live Preview) після завантаження сторінки"""
        self.on_table_selection_changed(self.table_view.selectionModel().selection(), None)

    def action_toggle_layer(self, layer_key: str):
        """Зберігає стан видимості шару та оновлює сцену."""
        is_visible = self.layer_controls[layer_key]["cb"].isChecked()
        self.state.save_setting(f"visible_{layer_key}", is_visible)

        for item in self.scene.items():
            if isinstance(item, ValidationBox):
                item.apply_new_settings(self.state)
                if layer_key == "zones":
                    item.set_handles_visible(is_visible)
            elif hasattr(item, 'apply_new_settings'):
                item.apply_new_settings(self.state)

            # Вектори PDF
            if layer_key == "vectors" and isinstance(item, InteractiveMixin):
                if not isinstance(item, ValidationBox):
                    item.setVisible(is_visible)

        # Синхронізуємо видимість ValidationBox (show/hide при всіх шарах вимкнених)
        self.sync_layers_visibility()
        self.scene.update()

        if self.state.current_mode == "CONFIG":
            self._refresh_ghost_preview()

    def sync_layers_visibility(self):
        """Примусово синхронізує видимість шарів для всіх об'єктів на сцені."""
        zones_visible = self.state.load_setting("visible_zones", True)
        frame_p = self.state.load_setting("visible_frame_pending", True)
        frame_a = self.state.load_setting("visible_frame_approved", True)
        skeleton = self.state.load_setting("visible_skeleton", True)
        anchor = self.state.load_setting("visible_anchor", True)
        any_layer_on = zones_visible or frame_p or frame_a or skeleton or anchor
        
        for item in self.scene.items():
            if isinstance(item, ValidationBox):
                item.set_handles_visible(zones_visible)
                item.apply_new_settings(self.state)
                item.setVisible(any_layer_on)

    def on_inspector_header_double_clicked(self, logical_index):
        """Сортування Live Preview при подвійному кліку по заголовку"""
        from PyQt6.QtCore import Qt
        
        # 1. Змінюємо порядок на протилежний
        if self.inspector_sort_order == Qt.SortOrder.AscendingOrder:
            self.inspector_sort_order = Qt.SortOrder.DescendingOrder
        else:
            self.inspector_sort_order = Qt.SortOrder.AscendingOrder
            
        # 2. Вмикаємо сортування, виконуємо його та вимикаємо
        # Це дозволяє відсортувати поточні дані, але не заважає додаванню нових
        self.inspector_table.setSortingEnabled(True)
        self.inspector_table.sortByColumn(logical_index, self.inspector_sort_order)
        self.inspector_table.setSortingEnabled(False)
        
        print(f"[UI] Таблицю відсортовано за стовпцем {logical_index}")



    def action_change_layer_color(self, layer_key):
        """Відкриває діалог вибору кольору для конкретного шару"""
        from PyQt6.QtWidgets import QColorDialog
        
        # Беремо поточний колір з бази або дефолтний
        current_hex = self.state.load_setting(f"color_{layer_key}", "#000000")
        color = QColorDialog.getColor(QColor(current_hex))
        
        if color.isValid():
            new_hex = color.name()
            # 1. Зберігаємо в базу
            self.state.save_setting(f"color_{layer_key}", new_hex)
            
            # 2. Оновлюємо колір самої кнопки в UI
            self.layer_controls[layer_key]["btn"].setStyleSheet(
                f"background-color: {new_hex}; border: 1px solid black;"
            )
            
            # 3. Оновлюємо всі об'єкти на сцені
            for item in self.scene.items():
                if hasattr(item, 'apply_new_settings'):
                    item.apply_new_settings(self.state)
            self.scene.update()

    def init_layers_manager(self, parent_layout):
        """Менеджер шарів — тільки перемикачі видимості. Стилі — у SettingsDialog."""
        layers_group = QGroupBox("Менеджер шарів")
        layout = QVBoxLayout()

        # key → (назва, default)
        vis_layers = [
            ("frame_pending",  "Рамка (Pending)",    True),
            ("frame_approved", "Рамка (Approved)",   True),
            ("skeleton",       "Скелет (Лінії)",     True),
            ("zones",          "Зони тексту",        True),
            ("service_zones",  "Сервісні зони",      True),
            ("anchor",         "Точка захоплення ✕", True),
            ("vectors",        "Вектори PDF",        True),
        ]

        self.layer_controls = {}
        for key, name, default in vis_layers:
            cb = QCheckBox(name)
            cb.setChecked(self.state.load_setting(f"visible_{key}", default))
            cb.stateChanged.connect(lambda _, k=key: self.action_toggle_layer(k))
            layout.addWidget(cb)
            self.layer_controls[key] = {"cb": cb}

        btn_open_settings = QPushButton("🎨 Налаштування стилів…")
        btn_open_settings.setStyleSheet(
            "background:#3498db; color:white; font-weight:bold; padding:5px; margin-top:6px;")
        btn_open_settings.clicked.connect(self.open_settings_dialog)
        layout.addWidget(btn_open_settings)

        layers_group.setLayout(layout)
        parent_layout.addWidget(layers_group)

    def action_reset_colors_to_default(self):
        """Скидає всі кольори та шари до заводських налаштувань"""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Скидання налаштувань", 
            "Ви впевнені, що хочете повернути дефолтні кольори та прозорість?", 
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes: return

        # Наші оригінальні дефолтні кольори (ARGB)
        layers = [
            ("frame_pending", True, "#fff39c12", "#40f39c12"),
            ("frame_approved", True, "#ff27ae60", "#4027ae60"),
            ("skeleton", True, "#ffe74c3c", None),
            ("zones", True, "#ff9b59b6", "#4d9b59b6"),
            ("vectors", True, "#ff0096ff", None)
        ]

        for key, visible, def_contour, def_fill in layers:
            # 1. Записуємо в базу даних SQLite
            self.state.save_setting(f"visible_{key}", visible)
            if def_contour: self.state.save_setting(f"color_contour_{key}", def_contour)
            if def_fill: self.state.save_setting(f"color_fill_{key}", def_fill)

            # 2. Оновлюємо візуал самих кнопок у Менеджері шарів
            controls = self.layer_controls.get(key)
            if controls:
                controls["cb"].blockSignals(True)
                controls["cb"].setChecked(visible)
                controls["cb"].blockSignals(False)
                
                if "btn_contour" in controls and def_contour:
                    controls["btn_contour"].setStyleSheet(f"background-color: {def_contour}; color: {'#000' if def_fill else '#fff'};")
                if "btn_fill" in controls and def_fill:
                    controls["btn_fill"].setStyleSheet(f"background-color: {def_fill};")

        # 3. Миттєво перемальовуємо всю сцену
        self.refresh_scene_styles()

    def action_change_color(self, layer_key, color_type):
        """Виклик діалогу з підтримкою альфа-каналу (прозорості)"""
        from PyQt6.QtWidgets import QColorDialog
        from PyQt6.QtGui import QColor

        setting_key = f"color_{color_type}_{layer_key}"
        # Беремо поточний колір. Якщо його немає - чорний непрозорий
        current_hex = self.state.load_setting(setting_key, "#ff000000")
        
        # Налаштовуємо діалог
        dialog = QColorDialog(QColor(current_hex), self)
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        dialog.setWindowTitle(f"Оберіть колір та прозорість ({color_type})")
        
        if dialog.exec():
            color = dialog.currentColor()
            # Зберігаємо у форматі #AARRGGBB (з прозорістю)
            new_hex = color.name(QColor.NameFormat.HexArgb) 
            
            self.state.save_setting(setting_key, new_hex)
            
            # Оновлюємо вигляд кнопки
            btn_key = f"btn_{color_type}"
            self.layer_controls[layer_key][btn_key].setStyleSheet(f"background-color: {new_hex};")
            
            # Перемальовуємо сцену
            for item in self.scene.items():
                if hasattr(item, 'apply_new_settings'):
                    item.apply_new_settings(self.state)
            self.scene.update()
    
    # 1. Оновлюємо метод ізоляції, щоб він приймав назву поля
    def action_isolate_object(self, target_row_index, zoom=True, active_field=None):
        target_item = None
        zones_visible = self.state.load_setting("visible_zones", True)
        for item in self.scene.items():
            if isinstance(item, ValidationBox):
                if hasattr(item, 'row_index') and item.row_index == target_row_index:
                    item.is_isolated = True
                    item.active_field = active_field
                    item.setOpacity(1.0)
                    item.setZValue(10)
                    item.set_handles_visible(zones_visible)
                    target_item = item
                else:
                    item.is_isolated = False
                    item.active_field = None
                    item.setOpacity(0.15)
                    item.setZValue(2)
                    item.set_handles_visible(False)
        
        if target_item and zoom:
            self.view.centerOn(target_item)
        self.scene.update()
    def on_table_row_selected(self, selected, deselected):
        """Обробник вибору рядка з захистом від стрибків."""
        if getattr(self, 'skip_zoom', False):
            return

        indexes = selected.indexes()
        if not indexes: return
        
        row = indexes[0].row()
        
        # Якщо мишка натиснута — відкладаємо зум до відпускання
        from PyQt6.QtWidgets import QApplication
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, lambda r=row: self.action_isolate_object(r, zoom=True))
        else:
            self.action_isolate_object(row, zoom=True)

    def on_validation_box_clicked(self, clicked_item):
        """Виділяє рядок у таблиці, але БЛОКУЄ стрибок камери (для комфортного драгу)"""
        if hasattr(clicked_item, 'row_index'):
            from PyQt6.QtCore import QItemSelectionModel
            index = self.table_model.index(clicked_item.row_index, 0)
            
            # Піднімаємо вибраний item поверх інших
            for item in self.scene.items():
                if isinstance(item, ValidationBox):
                    item.setZValue(2)
            clicked_item.setZValue(10)
            
            self.skip_zoom = True
            self.table_view.selectionModel().select(
                index, 
                QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
            )
            self.skip_zoom = False
            self.sync_layers_visibility()

    # 2. Оновлюємо метод скидання ізоляції
    def action_clear_isolation(self):
        zones_visible = self.state.load_setting("visible_zones", True)
        for item in self.scene.items():
            if isinstance(item, ValidationBox):
                item.is_isolated = False
                item.active_field = None
                item.setOpacity(1.0)
                item.setZValue(2)
                item.set_handles_visible(zones_visible)
        self.scene.update()

        



    def action_change_approved_color(self):
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor()
        if color.isValid():
            self.state.save_setting("color_approved", color.name())
            self.refresh_ui_palette()

    def refresh_ui_palette(self):
        """Перемальовує всі об'єкти на сцені з новими кольорами з бази"""
        for item in self.scene.items():
            if isinstance(item, ValidationBox):
                item.apply_new_settings(self.state)

    def action_recreate_object(self, old_val_box, new_scene_rect, action_mode='move'):
        """Знищує старий об'єкт і перемальовує його (Ідеальний Метод 2)."""
        row_idx = getattr(old_val_box, 'row_index', -1)
        if row_idx == -1: return

        # ── zone_edit / service_zone_edit: Зона розтягнута мишкою -> перераховуємо текст! ────────
        if action_mode in ('zone_edit', 'service_zone_edit'):
            self.recalculate_object_ocr(old_val_box, include_service=(action_mode == 'service_zone_edit'))
            return

        self.skip_zoom = True

        old_obj = old_val_box.found_obj
        old_ui_rect = old_obj.custom_zones.get('ui_rect', {
            'x': old_obj.anchor.get('x', 0), 'y': old_obj.anchor.get('y', 0),
            'w': old_obj.anchor.get('width', 100), 'h': old_obj.anchor.get('height', 100)
        })

        dx = new_scene_rect.x() - float(old_ui_rect['x'])
        dy = new_scene_rect.y() - float(old_ui_rect['y'])

        new_anchor = dict(old_obj.anchor)
        new_vars = dict(old_obj.variables)

        if action_mode == 'move':
            new_anchor['x'] = float(old_obj.anchor.get('x', 0)) + dx
            new_anchor['y'] = float(old_obj.anchor.get('y', 0)) + dy
            for k, v in new_vars.items():
                if isinstance(v, dict) and 'x0' in v:
                    v['x0'] = float(v['x0']) + dx
                    v['y0'] = float(v['y0']) + dy
                    if 'x1' in v: v['x1'] = float(v['x1']) + dx
                    if 'y1' in v: v['y1'] = float(v['y1']) + dy
        elif action_mode == 'resize':
            scale_w = new_scene_rect.width() / old_ui_rect['w'] if old_ui_rect['w'] > 0 else 1.0
            new_anchor['width'] = float(old_obj.anchor.get('width', 100)) * scale_w
            for k, v in new_vars.items():
                if isinstance(v, dict) and 'x0' in v:
                    if 'length' in v: v['length'] = float(v['length']) * scale_w
                    if 'x1' in v: v['x1'] = float(v['x0']) + (float(v['x1']) - float(v['x0'])) * scale_w
                    if 'y1' in v: v['y1'] = float(v['y0']) + (float(v['y1']) - float(v['y0'])) * scale_w

        from models import FoundObject
        new_obj = FoundObject(
            template_name=old_obj.template_name,
            variant_name=old_obj.variant_name,
            anchor=new_anchor, variables=new_vars, line_ids=[], pins=getattr(old_obj, 'pins', {})
        )
        new_obj.text_fields = dict(old_obj.text_fields)
        new_obj.status = old_obj.status

        # Зберігаємо ручні зміщення текстових зон перед перестворенням
        old_zones = {
            gz.get('field', ''): dict(gz)
            for gz in old_obj.custom_zones.get('ghost_zones', [])
        }

        new_obj = self.apply_working_algorithm(new_obj, new_anchor)

        # Відновлюємо ручні зміщення ratios
        for gz in new_obj.custom_zones.get('ghost_zones', []):
            field = gz.get('field', '')
            if field in old_zones:
                gz['rx0'] = old_zones[field]['rx0']
                gz['ry0'] = old_zones[field]['ry0']
                gz['rx1'] = old_zones[field]['rx1']
                gz['ry1'] = old_zones[field]['ry1']

        self.scene.removeItem(old_val_box)
        self.table_model.objects[row_idx] = new_obj.to_dict()

        new_val_box = ValidationBox(new_obj, self.on_validation_box_clicked, state_ref=self.state, on_geometry_changed_cb=self.action_recreate_object)
        new_val_box.row_index = row_idx
        self.scene.addItem(new_val_box)

        self.table_view.selectRow(row_idx)
        self.refresh_inspector()
        self.manual_save_to_db()
        self.skip_zoom = False

    def action_stamp_template(self, scene_pos):
        """Ручне витягування штампа із збереженням архітектури базової лінії."""
        if self.state.current_mode != "VALIDATE":
            print(f"[STAMP] Відхилено: mode={self.state.current_mode}")
            return
        
        selected_item = self.list_templates.currentItem()
        if not selected_item:
            print("[STAMP] Відхилено: не вибрано шаблон")
            return
        print(f"[STAMP] Шаблон: {selected_item.text(0)}, pos=({scene_pos.x():.1f}, {scene_pos.y():.1f})")

            
        import json
        with open(selected_item.data(0, Qt.ItemDataRole.UserRole), 'r', encoding='utf-8') as f:
            tmpl = json.load(f)
            
        from models import FoundObject
        variant_name = tmpl.get('variants', [{}])[0].get('name', 'Manual') if tmpl.get('variants') else "Manual"
        
        # === МАТЕМАТИЧНИЙ РЕВЕРС БАЗОВОЇ ЛІНІЇ ===
        # Щоб об'єкт будувався від бази, а якір був під мишкою - вираховуємо зсув
        anch_def = tmpl.get('anchor', {})
        ev_test = ExprEvaluator({'base_element': {'x0': 0, 'y0': 0, 'length': 100.0}})
        off_x = ev_test.eval(anch_def.get('x', 'base_element.x0'))
        off_y = ev_test.eval(anch_def.get('y', 'base_element.y0'))
        
        bx = scene_pos.x() - off_x
        by = scene_pos.y() - off_y

        new_anchor = {'x': scene_pos.x(), 'y': scene_pos.y(), 'width': 100.0, 'height': 100.0}
        new_vars = {
            'base_element': {'x0': bx, 'y0': by, 'length': 100.0}
        }
        
        new_obj = FoundObject(
            template_name=tmpl.get("name", "Manual Stamp"),
            variant_name=variant_name,
            anchor=new_anchor, variables=new_vars, line_ids=[], pins={}
        )
        new_obj.status = "pending"
        new_obj = self.apply_working_algorithm(new_obj, new_anchor)

        # Додаємо об'єкт на сцену та в таблицю
        obj_dict = new_obj.to_dict()
        self.table_model.add_object(obj_dict)
        val_box = ValidationBox(
            new_obj, self.on_validation_box_clicked,
            state_ref=self.state,
            on_geometry_changed_cb=self.action_recreate_object
        )
        val_box.row_index = self.table_model.rowCount() - 1
        self.scene.addItem(val_box)
        self.manual_save_to_db()

    def s(self):
        """Перемальовує всі об'єкти на сцені з новими кольорами з бази"""
        for item in self.scene.items():
            if isinstance(item, ValidationBox):
                # Викликаємо оновлення кольорів (потрібно буде додати метод update_styles в ValidationBox)
                item.apply_new_settings(self.state)

    def open_file_dialog(self):
        """Діалог вибору файлу з очищенням попереднього стану"""
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Оберіть PDF", "", "PDF Files (*.pdf)")
        if not path: return 
        
        self.current_pdf_path = path
        self.state.pdf_path = path

        
        # ОЧИЩЕННЯ: Видаляємо стару БД і очищаємо UI
        self.state.clear_cache()
        self.table.setRowCount(0)
        
        self.state.page_num = 0
        self.load_pdf_page(0)
        print(f"Відкрито новий файл: {path}. Старий кеш SQLite видалено.")
    
    def manual_save_to_db(self):
        """Збереження поточної сторінки та ПРИМУСОВЕ оновлення вікна БД."""
        try:
            objects_dicts = []
            if hasattr(self, 'table_model'):
                objects_dicts = [dict(obj) for obj in self.table_model.objects]
            
            # Оновлюємо кеш сесії
            self.state.session_cache[str(self.state.page_num)] = objects_dicts
            # Зберігаємо в SQLite
            self.state.sync_page_to_db(self.state.page_num, objects_dicts, status="saved")
            
            if hasattr(self, 'update_thumbnail_status'):
                self.update_thumbnail_status(self.state.page_num)
                
            # === СИНХРОНІЗАЦІЯ З ВІКНОМ БАЗИ ДАНИХ ===
            if hasattr(self, 'db_window') and self.db_window and self.db_window.isVisible():
                self.db_window.refresh_data()
            # ========================================
                
        except Exception as e:
            print(f"[DB Sync Error] {e}")

    def on_table_data_changed(self, top_left, bottom_right):
        """Синхронізує колір рамки на Canvas і кеш, коли змінюють галочку в таблиці."""
        row = top_left.row()
        if row >= len(self.table_model.objects):
            return
        obj_dict = self.table_model.objects[row]
        for item in self.scene.items():
            if hasattr(item, 'row_index') and item.row_index == row:
                item.set_status(obj_dict["status"])
                # КРИТИЧНО: оновлюємо статус всередині графічного об'єкта
                if hasattr(item, 'found_obj'):
                    item.found_obj.status = obj_dict["status"]
                break
                
        # Оновлюємо кеш та навігатор
        self.manual_save_to_db()


    def prompt_recovery(self, recovery_data):
        """Запит на відновлення із зазначенням імені файлу та скиданням воркерів."""
        last_page = int(recovery_data["page"])
        pdf_path = recovery_data["pdf_path"]
        file_name = os.path.basename(pdf_path) if pdf_path else "невідомий файл"

        msg = (f"Знайдено збережену сесію!\n\n"
               f"Файл: {file_name}\n"
               f"Остання сторінка: {last_page + 1}\n\n"
               f"Бажаєте продовжити роботу саме з цим файлом?")
        
        reply = QMessageBox.question(self, "Відновлення сесії", msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            # ПЕРЕМИКАЄМО ФАЙЛ НА ТОЙ, ЩО В СЕСІЇ
            if pdf_path and os.path.exists(pdf_path):
                self.state.pdf_path = pdf_path
                self.setWindowTitle(f"EPLAN Studio v2.0 - {file_name}")
                self.state.page_num = last_page
                self.load_pdf_page(last_page)
                self.switch_mode("VALIDATE")
                
                # === ВИПРАВЛЕННЯ ДУБЛЮВАННЯ ===
                # 1. Зупиняємо старий воркер, який запустився в __init__
                if hasattr(self, 'thumb_worker') and self.thumb_worker:
                    self.thumb_worker.stop()
                    self.thumb_worker.wait()
                
                # 2. Очищаємо список від сміття старого файлу
                self.list_thumbnails.clear()
                self.original_thumbnails.clear()
                
                # 3. Запускаємо правильний потік
                self.thumb_worker = ThumbnailWorker(self.state.pdf_path, max_width=160)
                self.thumb_worker.thumbnail_ready.connect(self.on_thumbnail_ready)
                self.thumb_worker.start()
                # ==============================
                
            else:
                QMessageBox.warning(self, "Помилка", f"Файл {file_name} не знайдено за шляхом {pdf_path}")
        else:
            self.state.clear_cache()
            self.load_pdf_page(0)
            # Перемалювати бейджі мініатюр після очищення кешу
            for pn in list(self.original_thumbnails.keys()):
                self.update_thumbnail_status(pn)
            
    def on_thumbnail_ready(self, page_num, qimage):
        """Отримує мініатюру з воркера і додає її в список навігатора."""
        pixmap = QPixmap.fromImage(qimage)
        self.original_thumbnails[page_num] = pixmap

        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, page_num)
        item.setText(f"{page_num + 1}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        # Розмір елемента щільно відповідає іконці + підпис
        item.setSizeHint(getattr(self, '_thumb_item_size', QSize(166, 139)))

        self.list_thumbnails.addItem(item)
        self.update_thumbnail_status(page_num)

        total   = self.list_thumbnails.count()
        scanned = sum(1 for k in self.state.session_cache if self.state.session_cache[k])
        self.nav_stats_label.setText(f"Сторінок: {total}  |  Оброблено: {scanned}")

    def go_to_page(self, new_page: int):
        """Єдиний централізований метод переходу між сторінками."""
        if not self.state.pdf_path: return
        
        with fitz.open(self.state.pdf_path) as doc:
            total_pages = len(doc)
            if not (0 <= new_page < total_pages): return
            
            # 1. Синхронізуємо table_model → session_cache
            if self.state.page_num != new_page and self.table_model.objects:
                self.state.session_cache[str(self.state.page_num)] = [
                    dict(obj) for obj in self.table_model.objects
                ]
                
            prev_page = self.state.page_num
            self.state.page_num = new_page
            
            # 2. Оновлюємо текст
            self.lbl_page_info.setText(f"Сторінка {new_page + 1} з {total_pages}")
            
            # 3. Завантажуємо растрову підкладку
            self.load_pdf_page(new_page)
            
            # 4. Завжди завантажуємо кешовані об'єкти
            self.load_cached_validation()
            
            # Скидаємо зум на повну сторінку (тільки при зміні сторінки)
            if prev_page != new_page and self.scene.sceneRect().width() > 0:
                self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
                
            # 5. Виділяємо мініатюру
            for i in range(self.list_thumbnails.count()):
                item = self.list_thumbnails.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == new_page:
                    self.list_thumbnails.setCurrentItem(item)
                    self.list_thumbnails.scrollToItem(item)
                    break
                    
            # 6. Оновлюємо бейджі
            self.update_thumbnail_status(prev_page)
            self.update_thumbnail_status(new_page)


    def change_page(self, direction):
        self.go_to_page(self.state.page_num + direction)

    def on_thumbnail_clicked(self, item):
        self.go_to_page(item.data(Qt.ItemDataRole.UserRole))

    def _nav_jump(self, page_num: int):
        self.go_to_page(page_num)

    def _nav_jump_last(self):
        if not self.state.pdf_path: return
        with fitz.open(self.state.pdf_path) as doc:
            self.go_to_page(len(doc) - 1)
            
    def _nav_jump_first(self):
        self.go_to_page(0)


    def _keyboard_nav(self, direction):
        """Пункт 3: Навігація клавіатурою із захистом від конфлікту з текстовими полями."""
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QTextEdit, QComboBox)):
            return
        self.change_page(direction)

    def _keyboard_nav_jump(self, page):
        """Пункт 3: Перехід на конкретну сторінку з клавіатури."""
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QTextEdit, QComboBox)):
            return
        self.go_to_page(page)
        # --- ДОДАЙТЕ ЦІ ДВА МЕТОДИ СЮДИ ---
    
    def _nav_jump_by_input(self):
        """Перехід на сторінку за введеним номером у текстовому полі."""
        text = self.nav_jump_edit.text().strip()
        if not text.isdigit():
            return
            
        # Користувач вводить звичайний номер (починається з 1)
        # А програма рахує з 0, тому віднімаємо 1
        target_page = int(text) - 1
        self.go_to_page(target_page)
        self.nav_jump_edit.clear() # Очищаємо поле після переходу

    def _nav_filter(self, text):
        """Фільтрує мініатюри в навігаторі за номером сторінки."""
        search_str = text.strip()
        for i in range(self.list_thumbnails.count()):
            item = self.list_thumbnails.item(i)
            if not search_str:
                item.setHidden(False) # Показуємо всі, якщо пошук порожній
            else:
                # Перевіряємо, чи введений текст збігається з номером сторінки (1-based)
                page_num_str = str(item.data(Qt.ItemDataRole.UserRole) + 1)
                item.setHidden(search_str not in page_num_str)

    def update_thumbnail_status(self, page_num):
        """
        Малює статусний бейдж поверх мініатюри:
          🟢 Зелений  — всі об'єкти approved
          🟡 Жовтий   — є об'єкти зі статусом pending
          Синя смужка — поточна відкрита сторінка
        """
        if page_num not in self.original_thumbnails:
            return

        base_pix = self.original_thumbnails[page_num].copy()

        # Визначаємо набір об'єктів завжди з кешу (єдине джерело правди)
        objects_on_page = self.state.session_cache.get(
            str(page_num), self.state.session_cache.get(page_num, [])
        )

        if objects_on_page:
            statuses = [o.get('status', 'pending') for o in objects_on_page]
            all_approved = all(s == 'approved' for s in statuses)
            badge_color = QColor(39, 174, 96) if all_approved else QColor(243, 156, 18)
            count_text = str(len(objects_on_page))

            painter = QPainter(base_pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            radius = 13
            cx = base_pix.width() - radius - 4
            cy = radius + 4
            painter.setBrush(QBrush(badge_color))
            painter.setPen(QPen(Qt.GlobalColor.white, 2))
            painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)
            painter.setPen(QPen(Qt.GlobalColor.white))
            font = painter.font()
            font.setPixelSize(12)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(cx - radius, cy - radius, radius * 2, radius * 2,
                             Qt.AlignmentFlag.AlignCenter, count_text)
            painter.end()

        # Синя смужка знизу для поточної сторінки
        if page_num == self.state.page_num:
            painter = QPainter(base_pix)
            painter.setBrush(QBrush(QColor(41, 128, 185)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(0, base_pix.height() - 5, base_pix.width(), 5)
            painter.end()

        for i in range(self.list_thumbnails.count()):
            item = self.list_thumbnails.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == page_num:
                item.setIcon(QIcon(base_pix))
                break

    # ══════════════════════════════════════════════════════════════════════════
    # ЛОГІКА РЕНДЕРИНГУ PDF (ІДЕАЛЬНА СИНХРОНІЗАЦІЯ ДЛЯ A3 ТА A4)
    # ══════════════════════════════════════════════════════════════════════════
    def load_pdf_page(self, page_num):
        self.scene.clear()
        if not os.path.exists(self.state.pdf_path): return
        
        try:
            with pdfplumber.open(self.state.pdf_path) as p_doc:
                p_page = p_doc.pages[page_num]# ДОДАЄМО ШИРИНУ СТОРІНКИ:
                self.state.template_data["_page_height"] = p_page.height 
                self.state.template_data["_page_width"] = p_page.width
                
                for raw_line in p_page.lines: self.add_plumber_line_to_scene(raw_line)
                for r in p_page.rects: self.add_plumber_rect_to_scene(r)
                for c in p_page.curves: self.add_plumber_curve_to_scene(c)
                for img in p_page.images: self.add_plumber_image_to_scene(img)

            with fitz.open(self.state.pdf_path) as doc:
                if page_num < 0 or page_num >= len(doc): return
                page = doc[page_num]
                zoom = 4.0 
                mat = fitz.Matrix(zoom, zoom)
                
                # КРИТИЧНЕ ВИПРАВЛЕННЯ ДЛЯ A3: 
                page.set_cropbox(page.mediabox)
                
                pix = page.get_pixmap(matrix=mat)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
                bg_item = self.scene.addPixmap(QPixmap.fromImage(img))
                
                scale_x = float(p_page.width) / float(pix.width)
                scale_y = float(p_page.height) / float(pix.height)
                
                bg_item.setTransform(QTransform().scale(scale_x, scale_y))
                bg_item.setZValue(-1) 
                bg_item.setPos(0, 0) 
                
        except Exception as e:
            QMessageBox.critical(self, "Помилка PDF", f"Не вдалося синхронізувати шари: {str(e)}")
        
        self.refresh_scene_styles() 
    
    def refresh_scene_styles(self):
        """Примусово застосовує кольори та шари з БД до всіх об'єктів на сцені + синхронізує Менеджер шарів"""
        if not hasattr(self, 'state'): return
        
        # Синхронізуємо галочки в правій панелі
        if hasattr(self, 'layer_controls'):
            for key, ctrl in self.layer_controls.items():
                is_vis = self.state.load_setting(f"visible_{key}", True)
                ctrl["cb"].blockSignals(True)
                ctrl["cb"].setChecked(is_vis)
                ctrl["cb"].blockSignals(False)

        for item in self.scene.items():
            if hasattr(item, 'apply_new_settings'):
                item.apply_new_settings(self.state)
                
        self.scene.update()

    def add_plumber_line_to_scene(self, raw):
        import math
        
        if 'pts' in raw and len(raw['pts']) >= 2:
            (px0, py0), (px1, py1) = raw['pts'][0], raw['pts'][-1]
        else:
            px0, py0 = raw['x0'], raw.get('top', raw['y0'])
            px1, py1 = raw['x1'], raw.get('bottom', raw['y1'])

        dx, dy = abs(px1 - px0), abs(py1 - py0)
        
        if dy <= 1.0 and dx > 1.0:
            dir_type, length = 'H', dx
            x0, y0, x1, y1 = min(px0, px1), min(py0, py1), max(px0, px1), min(py0, py1)
        elif dx <= 1.0 and dy > 1.0:
            dir_type, length = 'V', dy
            x0, y0, x1, y1 = min(px0, px1), min(py0, py1), min(px0, px1), max(py0, py1)
        elif dx > 1.0 and dy > 1.0:
            dir_type = 'D'
            length = math.sqrt(dx**2 + dy**2)
            # --- НОРМАЛІЗАЦІЯ ДЛЯ СЦЕНИ ---
            if py0 <= py1:
                x0, y0, x1, y1 = px0, py0, px1, py1
            else:
                x0, y0, x1, y1 = px1, py1, px0, py0
        else: 
            return 
            
        line_data = {'type': 'line', 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'length': length, 'dir': dir_type}
        
        item = InteractiveLine(x0, y0, x1, y1, line_data, self.on_canvas_object_clicked)
        item.setZValue(0)
        
        # Налаштування кольору, яке не зникає при наведенні мишки
        if dir_type == 'V': color = QColor(0, 0, 255, 100)
        elif dir_type == 'D': color = QColor(255, 165, 0, 180) # Помаранчевий
        else: color = QColor(255, 0, 0, 100)
        
        item.pen_default = QPen(color, 2)
        item.setPen(item.pen_default)
        self.scene.addItem(item)

    def add_plumber_curve_to_scene(self, c):
        """Делегує рендеринг кривих до graphics.render_pdf_curve (Без'є / еліпс / bbox)."""
        from graphics import render_pdf_curve
        x = c.get('x0', 0)
        y = c.get('top', 0)
        w = c.get('width', 0)
        h = c.get('height', 0)
        if w < 1 or h < 1:
            return
        raw = {
            'x0': x, 'y0': y, 'w': w, 'h': h,
            'path': c.get('path', []),   # ← PDF-команди: ('m',…), ('c',…), ('h',)
            'pts':  c.get('pts', []),    # ← fallback: лише кінцеві точки
        }
        item = render_pdf_curve(raw, self.on_canvas_object_clicked)
        self.scene.addItem(item)

    def add_plumber_rect_to_scene(self, r):
        x, y, w, h = r.get('x0', 0), r.get('top', 0), r.get('width', 0), r.get('height', 0)
        if w < 1 or h < 1: return
        raw = {'type': 'rect', 'x0': x, 'y0': y, 'x1': x+w, 'y1': y+h, 'length': max(w,h), 'dir': 'rect'}
        item = InteractiveRect(x, y, w, h, raw, self.on_canvas_object_clicked)
        item.setZValue(0)
        self.scene.addItem(item)
    def add_plumber_image_to_scene(self, img):
        """Додає зображення з PDF на сцену як клікабельний елемент."""
        x = float(img.get('x0', 0))
        y = float(img.get('top', img.get('y0', 0)))
        w = float(img.get('width', 0))
        h = float(img.get('height', 0))
        if w < 1 or h < 1:
            return
        
        srcsize = img.get('srcsize', (0, 0))
        raw = {
            'type': 'image', 'dir': 'image',
            'x0': x, 'y0': y, 'x1': x + w, 'y1': y + h,
            'length': max(w, h),
            'name': img.get('name', ''),
            'srcsize_w': float(srcsize[0]) if srcsize else 0,
            'srcsize_h': float(srcsize[1]) if srcsize else 0,
            'width': w, 'height': h
        }
        item = InteractiveRect(x, y, w, h, raw, self.on_canvas_object_clicked)
        item.setZValue(0)
        img_color = QColor(100, 149, 237, 150)  # Cornflower blue
        item.pen_default = QPen(img_color, 1.5, Qt.PenStyle.DotLine)
        item.setPen(item.pen_default)
        item.setBrush(QBrush(QColor(100, 149, 237, 25)))
        self.scene.addItem(item)
    def on_canvas_object_clicked(self, clicked_item):
        import math
        if self.state.current_mode != "CONFIG": return
        for item in self.scene.items():
            if isinstance(item, InteractiveMixin): item.set_selected(False)
        clicked_item.set_selected(True)
        
        raw = clicked_item.raw_data
        lines_array = self.state.template_data.setdefault("geometry", {}).setdefault("lines", [])

        page_h = float(self.state.template_data.get("_page_height", 842.0))
        page_w = float(self.state.template_data.get("_page_width", 595.0))

        if not lines_array:
            self.config_raw_elements.clear() 
            role_name = "base_element"
            elem_def = {
                "role": role_name, "type": raw['dir'], "is_base": True, 
                "x0_offset_ratio": 0.0, "y0_offset_ratio": 0.0, "length_ratio": 1.0
            }
            
            raw_len = max(raw['length'], 0.001)

            if raw['dir'] == 'H':
                elem_def["page_length_ratio"] = round(raw['length'] / page_w, 6)
            elif raw['dir'] == 'V':
                elem_def["page_length_ratio"] = round(raw['length'] / page_h, 6)
            else:
                w, h = abs(raw['x1'] - raw['x0']), abs(raw['y1'] - raw['y0'])
                elem_def["page_ratio_W"] = round(w / page_w, 6)
                elem_def["page_ratio_H"] = round(h / page_h, 6)
                
                if raw['dir'] == 'image':
                    elem_def['type'] = 'image'
                    elem_def['match'] = {
                        'name': raw.get('name', ''),
                        'width': round(w, 2),
                        'height': round(h, 2),
                        'srcsize_w': raw.get('srcsize_w', 0),
                        'srcsize_h': raw.get('srcsize_h', 0),
                        'tolerance': 0.2
                    }
                    elem_def['width_ratio'] = round(w / raw_len, 4)
                    elem_def['height_ratio'] = round(h / raw_len, 4)
                elif raw['dir'] in ['rect', 'arc', 'ellipse']:
                    if raw['dir'] == 'arc': 
                        elem_def['radius_ratio'] = round((w / 2) / raw_len, 4)
                    
                    if 'path' in raw and raw['path']:
                        path_ratios = []
                        for cmd, *args in raw['path']:
                            n_args = [(round((p[0]-raw['x0'])/raw_len, 5), round((p[1]-raw['y0'])/raw_len, 5)) for p in args]
                            path_ratios.append([cmd] + n_args)
                        elem_def['path_ratios'] = path_ratios
                    elif 'pts' in raw and raw['pts']:
                        pts_ratios = [(round((p[0]-raw['x0'])/raw_len, 5), round((p[1]-raw['y0'])/raw_len, 5)) for p in raw['pts']]
                        elem_def['pts_ratios'] = pts_ratios
                        
                elif raw['dir'] == 'D':
                    elem_def['x1_offset_ratio'] = round((raw['x1'] - raw['x0']) / raw_len, 4)
                    elem_def['y1_offset_ratio'] = round((raw['y1'] - raw['y0']) / raw_len, 4)
                    
                    # --- ДОДАНО: Розрахунок та збереження кута (від 0 до 180) ---
                    real_angle = math.degrees(math.atan2(raw['y1'] - raw['y0'], raw['x1'] - raw['x0']))
                    if real_angle < 0: real_angle += 180.0
                    if real_angle >= 180.0: real_angle -= 180.0
                    elem_def['angle'] = round(real_angle, 2)

            lines_array.append(elem_def)
            self.current_base_raw = raw
            self.config_raw_elements[role_name] = raw
            self.state.template_data["anchor"] = {"x": f"{role_name}.x0", "y": f"{role_name}.y0", "width": f"{role_name}.length", "height": f"{role_name}.length", "export": True}
        else:
            if not self.current_base_raw: return
            base = self.current_base_raw
            blen = max(base['length'], 0.001)
            
            x0_off = (raw['x0'] - base['x0']) / blen
            y0_off = (raw['y0'] - base['y0']) / blen
            len_ratio = raw['length'] / blen
            
            role_name = "right_wall" if raw['dir'] == 'V' else ("top_line" if y0_off < 0.1 else "bottom_line")
            if raw['dir'] in ['arc', 'ellipse', 'rect']: role_name = "shape"
            if raw['dir'] == 'image': role_name = "image"
            
            existing_roles = [l.get("role", "") for l in lines_array]
            final_role = role_name
            counter = 1
            while final_role in existing_roles:
                final_role = f"{role_name}_{counter}"
                counter += 1

            elem_def = {
                "role": final_role, 
                "type": raw['dir'], 
                "x0_offset_ratio": round(x0_off, 4), 
                "y0_offset_ratio": round(y0_off, 4), 
                "length_ratio": round(len_ratio, 4)
            }
            w, h = abs(raw['x1'] - raw['x0']), abs(raw['y1'] - raw['y0'])
            
            if raw['dir'] == 'image':
                elem_def['type'] = 'image'
                elem_def['width_ratio'] = round(w / blen, 4)
                elem_def['height_ratio'] = round(h / blen, 4)
                elem_def['match'] = {
                    'name': raw.get('name', ''),
                    'width': round(w, 2),
                    'height': round(h, 2),
                    'srcsize_w': raw.get('srcsize_w', 0),
                    'srcsize_h': raw.get('srcsize_h', 0),
                    'tolerance': 0.2
                }
            elif raw['dir'] in ['rect', 'arc', 'ellipse']:
                elem_def['width_ratio'] = round(w / blen, 4)
                elem_def['height_ratio'] = round(h / blen, 4)
                if raw['dir'] == 'arc': 
                    elem_def['radius_ratio'] = round((w / 2) / blen, 4)
                
                if 'path' in raw and raw['path']:
                    path_ratios = []
                    for cmd, *args in raw['path']:
                        n_args = [(round((p[0]-base['x0'])/blen, 5), round((p[1]-base['y0'])/blen, 5)) for p in args]
                        path_ratios.append([cmd] + n_args)
                    elem_def['path_ratios'] = path_ratios
                elif 'pts' in raw and raw['pts']:
                    pts_ratios = [(round((p[0]-base['x0'])/blen, 5), round((p[1]-base['y0'])/blen, 5)) for p in raw['pts']]
                    elem_def['pts_ratios'] = pts_ratios
                    
            elif raw['dir'] == 'D':
                elem_def['x1_offset_ratio'] = round((raw['x1'] - base['x0']) / blen, 4)
                elem_def['y1_offset_ratio'] = round((raw['y1'] - base['y0']) / blen, 4)
                
                # --- ДОДАНО: Розрахунок та збереження кута (від 0 до 180) ---
                real_angle = math.degrees(math.atan2(raw['y1'] - raw['y0'], raw['x1'] - raw['x0']))
                if real_angle < 0: real_angle += 180.0
                if real_angle >= 180.0: real_angle -= 180.0
                elem_def['angle'] = round(real_angle, 2)
                
            lines_array.append(elem_def)
            self.config_raw_elements[final_role] = raw

        self.state.update_template(self.state.template_data)
        self.rebuild_tree()
        
        root_geom = self.tree_widget.topLevelItem(0)
        if root_geom:
            child = root_geom.child(len(lines_array) - 1)
            if child: 
                self.tree_widget.setCurrentItem(child)
                self.on_tree_item_clicked(child, 0)
                
        self._refresh_ghost_preview()


    def on_tz_rect_drawn(self, rx0, ry0, rx1, ry1):
        """Обробляє виділення для text_zones та service_zones."""
        if not self.current_selected_node: return
        category = self.current_selected_node[0]
        if category not in ("text_zones", "service_zones"): return
        
        # Вибір комбобоксу залежно від типу
        if category == "text_zones":
            base_role = self.combo_tz_ref.currentData() or "base_element"
        else:
            base_role = self.combo_sz_ref.currentData() or "base_element"
        
        ref_raw = self.config_raw_elements.get(base_role)
        if not ref_raw:
            ref_raw = self.current_base_raw
            if not ref_raw: return
            base_role = next((l.get("role") for l in self.state.template_data.get("geometry", {}).get("lines", []) if l.get("is_base")), "base_element")
        
        bx = ref_raw.get('x0', 0)
        by = ref_raw.get('top', ref_raw.get('y0', 0))
        blen = self.current_base_raw.get('length', 1) if self.current_base_raw else 1
        
        def make_formula(val, base_val, base_var):
            rel = (val - base_val) / blen
            if abs(rel) < 0.0001: return f"{base_role}.{base_var}"
            sign = "+" if rel > 0 else "-"
            return f"{base_role}.{base_var} {sign} base_element.length * {abs(rel):.4f}"
        
        if category == "text_zones":
            self.edit_tz_x0.setText(make_formula(rx0, bx, "x0"))
            self.edit_tz_y0.setText(make_formula(ry0, by, "y0"))
            self.edit_tz_x1.setText(make_formula(rx1, bx, "x0"))
            self.edit_tz_y1.setText(make_formula(ry1, by, "y0"))
        else:
            self.edit_sz_x0.setText(make_formula(rx0, bx, "x0"))
            self.edit_sz_y0.setText(make_formula(ry0, by, "y0"))
            self.edit_sz_x1.setText(make_formula(rx1, bx, "x0"))
            self.edit_sz_y1.setText(make_formula(ry1, by, "y0"))
        
        self.update_properties_to_state()

    def run_validation(self):
        """Запуск OCR з можливістю збереження approved об'єктів."""
        page_key = str(self.state.page_num)
        skip_approved = self.chk_skip_approved.isChecked()
        
        # Зберігаємо approved об'єкти якщо потрібно
        approved_objects = []
        if skip_approved:
            cached = self.state.session_cache.get(page_key, 
                     self.state.session_cache.get(self.state.page_num, []))
            approved_objects = [obj for obj in cached if obj.get('status') == 'approved']
        
        # Очищаємо кеш сторінки
        self.state.session_cache.pop(page_key, None)
        self.state.session_cache.pop(self.state.page_num, None)
        
        # Повертаємо approved об'єкти в кеш
        if approved_objects:
            self.state.session_cache[page_key] = [dict(o) for o in approved_objects]
        
        self.load_pdf_page(self.state.page_num)
        self.table_model.clear()
        self.inspector_table.setRowCount(0)
        
        for item in list(self.scene.items()):
            if isinstance(item, ValidationBox):
                self.scene.removeItem(item)
        
        # Готуємо список шаблонів
        templates_to_run = []
        is_tree = hasattr(self.list_templates, 'topLevelItemCount')
        items_count = self.list_templates.topLevelItemCount() if is_tree else self.list_templates.count()
        
        for index in range(items_count):
            item = self.list_templates.topLevelItem(index) if is_tree else self.list_templates.item(index)
            if not item: continue
            is_checked = item.checkState(0) == Qt.CheckState.Checked if is_tree else item.checkState() == Qt.CheckState.Checked
            if is_checked:
                import json
                user_data = item.data(0, Qt.ItemDataRole.UserRole) if is_tree else item.data(Qt.ItemDataRole.UserRole)
                try:
                    with open(user_data, 'r', encoding='utf-8') as f:
                        templates_to_run.append(json.load(f))
                except Exception as e:
                    print(f"Помилка завантаження шаблону {user_data}: {e}")
        
        templates_to_run.sort(key=lambda t: t.get('priority', 0), reverse=True)
        if not templates_to_run: 
            return QMessageBox.warning(self, "Увага", "Не вибрано жодного шаблону!")
        
        # Блокуємо UI та запускаємо Worker
        self.lock_ui(True)
        print(f"[Worker] Запуск фонового потоку для сторінки {self.state.page_num + 1}...")
        
        # Передаємо exclusion_zones для approved об'єктів
        exclusion_zones = []
        if skip_approved:
            for obj in approved_objects:
                a = obj.get('anchor', {})
                ui = obj.get('custom_zones', {}).get('ui_rect', {})
                if ui:
                    exclusion_zones.append({
                        'x': ui.get('x', 0), 'y': ui.get('y', 0),
                        'w': ui.get('w', 0), 'h': ui.get('h', 0)
                    })
        
        from worker import SearchWorker
        self.search_worker = SearchWorker(
            self.state.pdf_path, self.state.page_num, 
            templates_to_run, exclusion_zones=exclusion_zones
        )
        self.search_worker.object_found.connect(self.on_worker_object_found)
        self.search_worker.error.connect(self.on_worker_error)
        self.search_worker.scan_finished.connect(self.on_worker_finished)
        self.search_worker.start()

        
    def load_cached_validation(self):
        """Завантажує об'єкти з кешу та малює рамки на Canvas."""
        # 1. Чистимо старі ValidationBox зі сцени
        for item in list(self.scene.items()):
            if isinstance(item, ValidationBox):
                self.scene.removeItem(item)

        # 2. Беремо об'єкти з кешу (через уніфікований хелпер)
        cached_objects = self.state.get_page_objects(self.state.page_num)

        if hasattr(self, 'table_model'):
            self.table_model.beginResetModel()
            self.table_model.objects = [obj.copy() for obj in cached_objects]
            self.table_model.endResetModel()

        # 3. Малюємо ValidationBox на Canvas
        if cached_objects:
            for i, obj_dict in enumerate(self.table_model.objects):
                obj = FoundObject.from_dict(obj_dict)
                box = ValidationBox(
                    obj, self.on_validation_box_clicked,
                    state_ref=self.state,
                    on_geometry_changed_cb=self.action_recreate_object
                )
                box.row_index = i
                self.scene.addItem(box)

            self.btn_run_engine.setText(f"🔍 Пересканувати ({len(cached_objects)} об'єктів)")
        else:
            self.btn_run_engine.setText("🔍 Знайти та Витягти текст")

        # 4. Синхронізуємо видимість шарів для всіх нових об'єктів
        self.sync_layers_visibility()
        self.refresh_inspector()
    # --- СЛОТИ ФОНОВОГО ПОТОКУ ---

    def on_worker_object_found(self, obj_dict):
        """Додає об'єкт у нову таблицю та малює рамку на Canvas"""
        self.table_model.add_object(obj_dict)
        
        obj = FoundObject.from_dict(obj_dict)
        # ДОДАНО on_geometry_changed_cb=self.action_recreate_object
        val_box = ValidationBox(obj, self.on_validation_box_clicked, state_ref=self.state, on_geometry_changed_cb=self.action_recreate_object)
        val_box.row_index = self.table_model.rowCount() - 1 
        self.scene.addItem(val_box)


    def on_worker_error(self, err_msg):
        QMessageBox.critical(self, "Помилка Воркера", f"Сталася помилка:\n{err_msg}")

    def on_worker_finished(self, all_objects_dicts):
        """Потік завершено. Merge approved + нові, зберігаємо в БД."""
        self.lock_ui(False)
        
        # Merge: approved (збережені) + нові (pending)
        page_key = str(self.state.page_num)
        if self.chk_skip_approved.isChecked():
            approved = [o for o in self.state.session_cache.get(page_key, [])
                        if o.get('status') == 'approved']
            merged = approved + all_objects_dicts
        else:
            merged = all_objects_dicts

        self.state.set_page_objects(self.state.page_num, merged,
                                    save_to_db=True, status="pending")

        self.load_cached_validation()
        self.update_thumbnail_status(self.state.page_num)
        QMessageBox.information(
            self, "Готово",
            f"Знайдено: {len(all_objects_dicts)} нових + {len(merged) - len(all_objects_dicts)} approved")
        self.sync_layers_visibility()
        self.refresh_inspector()
        
    # ═══════════════════════════════════════════════════════════════════════
    # ПАКЕТНА ОБРОБКА — три режими
    # ═══════════════════════════════════════════════════════════════════════

    def _get_checked_templates(self) -> list:
        """Повертає список JSON-шаблонів, що позначені у бібліотеці."""
        templates = []
        for i in range(self.list_templates.topLevelItemCount()):
            item = self.list_templates.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                with open(item.data(0, Qt.ItemDataRole.UserRole), 'r', encoding='utf-8') as f:
                    templates.append(json.load(f))
        templates.sort(key=lambda t: (not t.get('Page_Data', False), t.get('priority', 50)))
        return templates

    def open_batch_dialog(self):
        """Відкриває діалог вибору режиму пакетної обробки."""
        with fitz.open(self.state.pdf_path) as doc:
            total = len(doc)
        dlg = BatchDialog(total, self.state.page_num, parent=self)
        dlg.batch_requested.connect(self._start_batch)
        dlg.exec()

    def _start_batch(self, mode: str, p_from: int, p_to: int, skip_cached: bool):
        """Запускає пакетну обробку у вибраному режимі."""
        if mode == "manual":
            return  # Ручний — нічого не запускаємо

        templates = self._get_checked_templates()
        if not templates:
            QMessageBox.warning(self, "Увага", "Не вибрано жодного шаблону!")
            return

        page_range = range(p_from, p_to)

        # Якщо потрібно — виключаємо вже кешовані сторінки
        if skip_cached:
            cached_keys = {int(k) for k in self.state.session_cache.keys()}
            page_range = [p for p in page_range if p not in cached_keys]
            if not page_range:
                QMessageBox.information(self, "Пакетна обробка",
                    "Всі сторінки в діапазоні вже є в кеші.\nЗніміть прапорець «Пропускати кешовані», щоб повторити.")
                return
            page_range = range(page_range[0], page_range[-1] + 1) if len(page_range) > 0 else range(0, 0)

        self._batch_mode      = mode
        self._batch_range     = page_range
        self._batch_templates = templates

        # Загальне блокування UI
        self.btn_batch.setEnabled(False)
        self.btn_run_engine.setEnabled(False)
        self.btn_batch_stop.setVisible(True)

        if mode == "semiauto":
            self.btn_semiauto_next.setVisible(False)
            self._semiauto_pages  = list(page_range)
            self._semiauto_idx    = 0
            self._semiauto_done   = 0
            self._semiauto_advance()

        elif mode == "headless":
            total = len(list(page_range))
            self.batch_progress.setRange(0, total)
            self.batch_progress.setValue(0)
            self.batch_progress.setVisible(True)

            skip_approved = self.chk_skip_approved.isChecked()
            approved_cache = dict(self.state.session_cache) if skip_approved else {}
            self.batch_worker = BatchWorker(
                self.state.pdf_path, page_range, templates,
                skip_approved=skip_approved, approved_cache=approved_cache
            )
            self.batch_worker.page_started.connect(self._on_batch_page_started)
            self.batch_worker.page_finished.connect(self.on_batch_page_finished)
            self.batch_worker.page_error.connect(self._on_batch_page_error)
            self.batch_worker.batch_finished.connect(self.on_batch_finished)
            self.batch_worker.progress_changed.connect(
                lambda cur, _tot: self.batch_progress.setValue(cur))
            self.batch_worker.start()

    # ── Headless: слоти BatchWorker ──────────────────────────────────────────

    def _on_batch_page_started(self, page_num: int):
        with fitz.open(self.state.pdf_path) as doc:
            total = len(doc)
        self.lbl_page_info.setText(f"🤖  Обробка: стор. {page_num + 1} / {total}")
    
    def on_batch_page_finished(self, page_num, found_objects_dicts):
        """Зберігає результати. Approved об'єкти зберігають свій статус."""
        page_key = str(page_num)
        self.state.session_cache.pop(page_key, None)
        self.state.session_cache.pop(page_num, None)
        # Зберігаємо без примусової зміни статусу — кожен об'єкт має свій
        self.state.session_cache[page_key] = found_objects_dicts
        self.state.sync_page_to_db(page_num, found_objects_dicts, status="saved")
        
        if page_num == self.state.page_num:
            self.load_cached_validation()
        self.update_thumbnail_status(page_num)
    

    def _on_batch_page_error(self, page_num: int, err: str):
        if page_num >= 0:
            print(f"[Batch] Помилка на стор. {page_num + 1}: {err}")
        else:
            QMessageBox.critical(self, "Помилка Batch", err)

    def on_batch_finished(self, processed: int):
        self._reset_batch_ui()
        with fitz.open(self.state.pdf_path) as doc:
            total = len(doc)
        self.lbl_page_info.setText(f"Сторінка {self.state.page_num + 1} з {total}")
        
        # Перемалювати поточну сторінку та оновити всі бейджі
        self.load_cached_validation()
        for pn in list(self.original_thumbnails.keys()):
            self.update_thumbnail_status(pn)

        if self._batch_mode == "headless":
            # Автоматичний режим → одразу зберігаємо CSV
            QMessageBox.information(
                self, "Готово",
                f"Оброблено сторінок: {processed}.\nРезультати збережено в SQLite.\n\nСтартує автоматичний CSV-експорт…"
            )
            self.export_csv_from_cache(auto=True)
        else:
            QMessageBox.information(
                self, "Пакетна обробка завершена",
                f"Оброблено сторінок: {processed}.\nРезультати збережено в SQLite."
            )

    def stop_batch(self):
        """Зупиняє поточний BatchWorker або Напівавто."""
        if hasattr(self, 'batch_worker') and self.batch_worker and self.batch_worker.isRunning():
            self.batch_worker.stop()
        if hasattr(self, '_semiauto_worker') and self._semiauto_worker and self._semiauto_worker.isRunning():
            self._semiauto_worker.terminate()
        self._reset_batch_ui()

    def _reset_batch_ui(self):
        """Повертає нижню панель до стандартного стану."""
        self.batch_progress.setVisible(False)
        self.btn_semiauto_next.setVisible(False)
        self.btn_batch_stop.setVisible(False)
        self.btn_batch.setEnabled(True)
        self.btn_run_engine.setEnabled(True)
        self._batch_mode = "manual"


    def update_navigator_badges(self):
        """
        Оновлює візуальні статуси (бейджі) на мініатюрах сторінок.
        🟢 Зелений - всі об'єкти схвалені (approved)
        🟡 Жовтий - є об'єкти в очікуванні (pending)
        ⚪ Прозорий/немає - об'єктів не знайдено
        """
        if not hasattr(self, 'list_thumbnails'):
            return

        for i in range(self.list_thumbnails.count()):
            item = self.list_thumbnails.item(i)
            page_idx = i
            page_key = str(page_idx)
            
            # Отримуємо дані з кешу
            objects = self.state.session_cache.get(page_key, [])
            
            if not objects:
                item.setData(Qt.ItemDataRole.StatusTipRole, "")
                # Можна додати індикацію порожньої сторінки, якщо потрібно
                continue
                
            # Перевіряємо статуси
            all_approved = all(obj.get('status') == 'approved' for obj in objects)
            has_pending = any(obj.get('status') == 'pending' for obj in objects)
            
            # Формуємо бейдж (можна через зміну іконки або кольору тексту)
            if all_approved:
                item.setText(f"Page {page_idx + 1} 🟢")
                item.setForeground(QColor("#27ae60")) # Зелений текст
            elif has_pending:
                count_pending = sum(1 for obj in objects if obj.get('status') == 'pending')
                item.setText(f"Page {page_idx + 1} 🟡({count_pending})")
                item.setForeground(QColor("#d35400")) # Помаранчевий текст
            else:
                item.setText(f"Page {page_idx + 1}")
                item.setForeground(QColor("#2c3e50"))
    # ── Напівавто: крок за кроком ─────────────────────────────────────────────

    def _semiauto_advance(self):
        """Переходить до наступної сторінки зі списку Напівавто."""
        pages = getattr(self, '_semiauto_pages', [])
        idx   = getattr(self, '_semiauto_idx', 0)

        if idx >= len(pages):
            # Всі сторінки оброблено
            self._semiauto_done = idx
            self._on_batch_finished(idx)
            return

        page_num = pages[idx]
        self._semiauto_idx = idx + 1

        with fitz.open(self.state.pdf_path) as doc:
            total = len(doc)
        self.lbl_page_info.setText(f"⏭  Напівавто: {page_num + 1} / {total}  (крок {idx + 1} з {len(pages)})")

        self.state.page_num = page_num
        self.load_pdf_page(page_num)

        self.table_model.clear()
        self.inspector_table.setRowCount(0)
        self.lock_ui(True)
        self.btn_batch_stop.setEnabled(True)

        # Exclusion zones для approved об'єктів
        exclusion_zones = []
        if self.chk_skip_approved.isChecked():
            cached = self.state.session_cache.get(str(page_num), [])
            for obj in cached:
                if obj.get('status') == 'approved':
                    ui = obj.get('custom_zones', {}).get('ui_rect', {})
                    if ui:
                        exclusion_zones.append({
                            'x': ui.get('x', 0), 'y': ui.get('y', 0),
                            'w': ui.get('w', 0), 'h': ui.get('h', 0)
                        })
        
        self._semiauto_worker = SearchWorker(
            self.state.pdf_path, page_num, self._batch_templates,
            exclusion_zones=exclusion_zones
        )
        self._semiauto_worker.object_found.connect(self.on_worker_object_found)
        self._semiauto_worker.error.connect(self.on_worker_error)
        self._semiauto_worker.scan_finished.connect(self._on_semiauto_scan_done)
        self._semiauto_worker.start()

    def _on_semiauto_scan_done(self, all_dicts: list):
        """Після сканування — зберігаємо в SQLite і чекаємо кліку «Далі»."""
        self.lock_ui(False)
        self.btn_batch_stop.setEnabled(True)

        if all_dicts:
            self.state.set_page_objects(self.state.page_num, all_dicts,
                                        save_to_db=True, status="pending")
        self.update_thumbnail_status(self.state.page_num)

        pages  = getattr(self, '_semiauto_pages', [])
        idx    = getattr(self, '_semiauto_idx', 1)
        remain = len(pages) - idx

        if remain > 0:
            next_pg = pages[idx] + 1
            self.btn_semiauto_next.setText(f"▶▶ Далі → стор. {next_pg}  ({remain} залишилось)")
        else:
            self.btn_semiauto_next.setText("▶▶ Завершити Напівавто")

        self.btn_semiauto_next.setVisible(True)
        self.refresh_scene_styles()


    def semiauto_next(self):
        """Кнопка «Далі» у Напівавто-режимі — зберігає поточне і йде до наступної сторінки."""
        self.btn_semiauto_next.setVisible(False)
        self.manual_save_to_db()
        self._semiauto_advance()

    def apply_working_algorithm(self, obj, new_anchor):
        """Єдиний робочий алгоритм для ручного розміщення та перетягування (аналог worker.py)."""
        import pdfplumber
        import json
        import math
        from evaluator import ExprEvaluator

        tmpl_data = None
        for i in range(self.list_templates.topLevelItemCount()):
            item = self.list_templates.topLevelItem(i)
            with open(item.data(0, Qt.ItemDataRole.UserRole), 'r', encoding='utf-8') as f:
                t = json.load(f)
                if t.get("name") == obj.template_name:
                    tmpl_data = t; break
        if not tmpl_data: return obj

        ax, ay = float(new_anchor.get('x', 0)), float(new_anchor.get('y', 0))
        aw = max(float(new_anchor.get('width', 1)), 1.0)

        eval_ctx = dict(obj.variables)
        eval_ctx['anchor'] = new_anchor
        eval_ctx['pins'] = getattr(obj, 'pins', {})
        
        # Базові змінні для формул
        if 'base_element' not in eval_ctx: eval_ctx['base_element'] = {'x0': ax, 'y0': ay, 'length': aw}
        if 'shape' not in eval_ctx: eval_ctx['shape'] = {'x0': ax, 'y0': ay, 'length': aw}
        
        

        # === ВИПРАВЛЕННЯ ЗМІЩЕННЯ ===
        base_data = eval_ctx['base_element']
        bx = float(base_data.get('x0', ax))
        by = float(base_data.get('y0', ay))
        blen = max(float(base_data.get('length', aw)), 1.0)

        # --- А. РОЗРАХУНОК ФІЗИЧНИХ ЛІНІЙ (Скелет) ---
        abs_lines = []
        for line_def in tmpl_data.get('geometry', {}).get('lines', []):
            role = line_def.get('role', 'unknown')
            l_type = line_def.get('type', 'H')
            try:
                lx0 = bx + float(line_def.get('x0_offset_ratio', 0)) * blen
                ly0 = by + float(line_def.get('y0_offset_ratio', 0)) * blen
                
                if l_type == 'path':
                    abs_cmds = []
                    all_pts_x, all_pts_y = [], []
                    for cmd_data in line_def.get('commands_ratio', []):
                        cmd = cmd_data[0]
                        coords = cmd_data[1:]
                        abs_cmd = [cmd]
                        for ci in range(0, len(coords), 2):
                            if ci + 1 < len(coords):
                                px = bx + float(coords[ci]) * blen
                                py = by + float(coords[ci+1]) * blen
                                abs_cmd.extend([px, py])
                                all_pts_x.append(px)
                                all_pts_y.append(py)
                        abs_cmds.append(abs_cmd)
                    if all_pts_x:
                        p_min_x, p_min_y = min(all_pts_x), min(all_pts_y)
                        p_max_x, p_max_y = max(all_pts_x), max(all_pts_y)
                        eval_ctx[role] = {'x0': p_min_x, 'y0': p_min_y, 'x1': p_max_x, 'y1': p_max_y,
                                          'length': max(p_max_x - p_min_x, p_max_y - p_min_y, 1.0)}
                        abs_lines.append({'x0': p_min_x, 'y0': p_min_y, 'x1': p_max_x, 'y1': p_max_y,
                                          'type': 'path', 'path_cmds': abs_cmds})

                elif l_type in ('arc', 'rect', 'ellipse'):
                    lw = float(line_def.get('width_ratio', line_def.get('length_ratio', 1.0))) * blen
                    lh = float(line_def.get('height_ratio', line_def.get('length_ratio', 1.0))) * blen
                    w_safe, h_safe = max(abs(lw), 1.0), max(abs(lh), 1.0)
                    eval_ctx[role] = {'x0': lx0, 'y0': ly0, 'x1': lx0 + lw, 'y1': ly0 + lh, 'length': max(w_safe, h_safe), 'bbox_w': w_safe, 'bbox_h': h_safe, 'center_x': lx0 + w_safe/2, 'center_y': ly0 + h_safe/2}
                    seg_type = 'ellipse' if line_def.get('require_closed', l_type != 'arc') else 'arc'
                    
                    rec_path, rec_pts = [], []
                    if 'path_ratios' in line_def:
                        for cmd, *args in line_def['path_ratios']:
                            rec_path.append([cmd] + [(bx + p[0]*blen, by + p[1]*blen) for p in args])
                    elif 'pts_ratios' in line_def:
                        rec_pts = [(bx + p[0]*blen, by + p[1]*blen) for p in line_def['pts_ratios']]
                        
                    abs_lines.append({'x0': lx0, 'y0': ly0, 'x1': lx0+w_safe, 'y1': ly0+h_safe, 'type': seg_type, 'path': rec_path, 'pts': rec_pts})
                else:
                    ll = float(line_def.get('length_ratio', 1.0)) * blen
                    if l_type == 'H': 
                        lx1, ly1 = lx0 + ll, ly0
                    elif l_type == 'V': 
                        lx1, ly1 = lx0, ly0 + ll
                    elif l_type == 'D': 
                        lx1 = bx + float(line_def.get('x1_offset_ratio', line_def.get('x0_offset_ratio', 0) + line_def.get('length_ratio', 1.0))) * blen
                        ly1 = by + float(line_def.get('y1_offset_ratio', line_def.get('y0_offset_ratio', 0) + line_def.get('length_ratio', 1.0))) * blen
                        ll = math.hypot(lx1 - lx0, ly1 - ly0) # Точна довжина діагоналі

                    eval_ctx[role] = {'x0': lx0, 'y0': ly0, 'x1': lx1, 'y1': ly1, 'length': ll}
                    abs_lines.append({'x0': lx0, 'y0': ly0, 'x1': lx1, 'y1': ly1, 'type': 'line'})
            except Exception: pass
        # --- Розрахунок позиції зображень (type: "image") ---
        for line_def in tmpl_data.get('geometry', {}).get('lines', []):
            if line_def.get('type') != 'image':
                continue
            role = line_def.get('role', 'unknown')
            
            # Розраховуємо позицію відносно base_element (без пошуку на сторінці)
            img_x0 = bx + float(line_def.get('x0_offset_ratio', 0)) * blen
            img_y0 = by + float(line_def.get('y0_offset_ratio', 0)) * blen
            img_w = float(line_def.get('width_ratio', 0.1)) * blen
            img_h = float(line_def.get('height_ratio', 0.1)) * blen
            
            eval_ctx[role] = {
                'x0': img_x0, 'y0': img_y0,
                'x1': img_x0 + img_w, 'y1': img_y0 + img_h,
                'width': img_w, 'height': img_h,
                'name': line_def.get('match', {}).get('name', ''),
                'srcsize_w': line_def.get('match', {}).get('srcsize_w', 0),
                'srcsize_h': line_def.get('match', {}).get('srcsize_h', 0),
                'length': max(img_w, img_h, 1.0)
            }
            abs_lines.append({
                'x0': img_x0, 'y0': img_y0,
                'x1': img_x0 + img_w, 'y1': img_y0 + img_h,
                'type': 'image'
            })    
        #ev = ExprEvaluator(eval_ctx)
        
        # --- Б. РОЗРАХУНОК UNION RECT (Велика рамка виділення) ---
        if not abs_lines:
            abs_lines.append({'x0': ax, 'y0': ay, 'x1': ax+aw, 'y1': ay+aw, 'type': 'line'})

        min_x = min([l['x0'] for l in abs_lines] + [l['x1'] for l in abs_lines])
        min_y = min([l['y0'] for l in abs_lines] + [l['y1'] for l in abs_lines])
        max_x = max([l['x0'] for l in abs_lines] + [l['x1'] for l in abs_lines])
        max_y = max([l['y0'] for l in abs_lines] + [l['y1'] for l in abs_lines])
        
        union_w = max(max_x - min_x, 1.0)
        union_h = max(max_y - min_y, 1.0)

        obj.anchor = new_anchor
        obj.custom_zones['ui_rect'] = {'x': min_x, 'y': min_y, 'w': union_w, 'h': union_h}
        obj.custom_zones['anchor_pos'] = {'x': float(new_anchor.get('x', 0)), 'y': float(new_anchor.get('y', 0))}
        obj.custom_zones['manual_rect'] = obj.custom_zones['ui_rect']

        # --- В. КОНВЕРТАЦІЯ СКЕЛЕТА В RATIOS ---
        obj.custom_zones['ghost_skeleton'] = []
        for l in abs_lines:
            skel_data = {
                'rx0': (l['x0'] - min_x) / union_w, 'ry0': (l['y0'] - min_y) / union_h,
                'rx1': (l['x1'] - min_x) / union_w, 'ry1': (l['y1'] - min_y) / union_h,
                'type': l.get('type', 'line')
            }
            if l.get('type') == 'path' and l.get('path_cmds'):
                ratios = []
                for cmd_data in l['path_cmds']:
                    cmd = cmd_data[0]
                    ratio_cmd = [cmd]
                    for ci in range(1, len(cmd_data), 2):
                        if ci + 1 <= len(cmd_data):
                            ratio_cmd.append((cmd_data[ci] - min_x) / union_w)
                            ratio_cmd.append((cmd_data[ci+1] - min_y) / union_h)
                    ratios.append(ratio_cmd)
                skel_data['path_ratios'] = ratios
            elif l.get('path'):
                skel_data['path'] = [[cmd] + [((p[0]-min_x)/union_w, (p[1]-min_y)/union_h) for p in args] for cmd, *args in l['path']]
            elif l.get('pts'):
                skel_data['pts'] = [((p[0]-min_x)/union_w, (p[1]-min_y)/union_h) for p in l['pts']]
            obj.custom_zones['ghost_skeleton'].append(skel_data)
        # === ДОДАЙТЕ СЮДИ ===
        ev = ExprEvaluator(eval_ctx)
        # ====================
        # --- Г. OCR ТЕКСТУ З ПІДТРИМКОЮ МАСИВІВ (repeat_over) ---
        var_def = next((v for v in tmpl_data.get('variants', []) if v.get('name') == obj.variant_name), {})
        new_text_fields = {}
        obj.custom_zones['ghost_zones'] = []

        try:
            with pdfplumber.open(self.state.pdf_path) as pdf_doc:
                page = pdf_doc.pages[self.state.page_num]
                
                for tz in var_def.get('text_zones', []):
                    field_name = tz.get('field', 'unknown')
                    repeat_expr = tz.get('repeat_over', "").strip()
                    
                    try:
                        count = 1
                        if repeat_expr:
                            try: count = int(ev.eval(repeat_expr))
                            except: count = 1
                        
                        extracted_texts = []
                        
                        for i in range(count):
                            ev.safe_env['i'] = i  # Впорскуємо i у формули
                            
                            x0, y0 = ev.eval(tz['x0']), ev.eval(tz['y0'])
                            x1, y1 = ev.eval(tz['x1']), ev.eval(tz['y1'])

                            if i == 0 or i == count - 1:
                                obj.custom_zones['ghost_zones'].append({
                                    'field': f"{field_name}[{i}]" if count > 1 else field_name, 
                                    'rx0': (x0 - min_x) / union_w, 'ry0': (y0 - min_y) / union_h, 
                                    'rx1': (x1 - min_x) / union_w, 'ry1': (y1 - min_y) / union_h
                                })

                            text = extract_text_by_center(page, x0, y0, x1, y1, tz.get('multiline'), tz.get('join', '\n'))
                            
                            if text: extracted_texts.append(text)
                            else:
                                if count > 1: extracted_texts.append("")

                        if count > 1:
                            separator = tz.get('separator', ', ')
                            new_text_fields[field_name] = separator.join(extracted_texts)
                        else:
                            new_text_fields[field_name] = extracted_texts[0] if extracted_texts else ""

                    except Exception as e:
                        print(f"Помилка масиву OCR {field_name}: {e}")

        except Exception as e:
            print(f"Помилка PDF OCR: {e}")

        obj.text_fields = new_text_fields
        return obj

    def _refresh_ghost_preview(self, *args):
        """Оновлює живий примарний контур. Захищено від RuntimeError та помилок парсингу."""
        if self.state.current_mode != "CONFIG":
            return

        try:
            if not hasattr(self, 'ghost_preview') or self.ghost_preview is None or sip.isdeleted(self.ghost_preview):
                from graphics import GhostPreviewItem
                self.ghost_preview = GhostPreviewItem(self.scene, self.state)
        except Exception:
            from graphics import GhostPreviewItem
            self.ghost_preview = GhostPreviewItem(self.scene, self.state)

        self.ghost_preview.hide_preview()
        
        self.update_properties_to_state()
        
        tmpl = self.state.template_data
        if not tmpl: return

        try:
            sel = [it for it in self.scene.selectedItems() if hasattr(it, 'raw_data')]
            if sel:
                base_raw = sel[0].raw_data
            elif getattr(self, 'current_base_raw', None):
                base_raw = self.current_base_raw
            else:
                return

            bx = float(base_raw.get('x0', 0))
            by = float(base_raw.get('top', base_raw.get('y0', 0)))
            blen = max(float(base_raw.get('length', base_raw.get('width', 1))), 1.0)

            from evaluator import ExprEvaluator
            eval_ctx = {
                'base_element': {'x0': bx, 'y0': by, 'length': blen},
                'shape': {'x0': bx, 'y0': by, 'length': blen}
            }

            # 3. Розрахунок ліній скелета
            abs_lines = []
            for line_def in tmpl.get('geometry', {}).get('lines', []):
                l_type = line_def.get('type', 'H')
                role = line_def.get('role', 'unknown')
                try:
                    lx0 = bx + float(line_def.get('x0_offset_ratio', 0)) * blen
                    ly0 = by + float(line_def.get('y0_offset_ratio', 0)) * blen
                    
                    if l_type in ('arc', 'ellipse', 'rect'):
                        lw = float(line_def.get('width_ratio', line_def.get('length_ratio', 1.0))) * blen
                        lh = float(line_def.get('height_ratio', line_def.get('length_ratio', 1.0))) * blen
                        
                        w_safe = max(abs(lw), 1.0)
                        h_safe = max(abs(lh), 1.0)
                        
                        eval_ctx[role] = {'x0': lx0, 'y0': ly0, 'x1': lx0 + lw, 'y1': ly0 + lh, 'length': max(w_safe, h_safe), 'bbox_w': w_safe, 'bbox_h': h_safe, 'center_x': lx0 + w_safe/2, 'center_y': ly0 + h_safe/2}
                        seg_type = 'ellipse' if line_def.get('require_closed', l_type != 'arc') else 'arc'
                        
                        # --- ВІДНОВЛЕННЯ БЕЗ'Є ---
                        rec_path, rec_pts = [], []
                        if 'path_ratios' in line_def:
                            for cmd, *args in line_def['path_ratios']:
                                rec_path.append([cmd] + [(bx + p[0]*blen, by + p[1]*blen) for p in args])
                        elif 'pts_ratios' in line_def:
                            rec_pts = [(bx + p[0]*blen, by + p[1]*blen) for p in line_def['pts_ratios']]
                            
                        abs_lines.append({'x0': lx0, 'y0': ly0, 'x1': lx0+w_safe, 'y1': ly0+h_safe, 'type': seg_type, 'path': rec_path, 'pts': rec_pts})
                    else:
                        ll  = float(line_def.get('length_ratio', 1.0)) * blen
                        eval_ctx[role] = {'x0': lx0, 'y0': ly0, 'x1': lx0+ll, 'y1': ly0, 'length': ll}
                        if l_type == 'H': abs_lines.append({'x0': lx0, 'y0': ly0, 'x1': lx0+ll, 'y1': ly0, 'type': 'line'})
                        elif l_type == 'V': abs_lines.append({'x0': lx0, 'y0': ly0, 'x1': lx0, 'y1': ly0+ll, 'type': 'line'})
                        elif l_type == 'D': 
                            lx1 = bx + float(line_def.get('x1_offset_ratio', line_def.get('x0_offset_ratio', 0) + line_def.get('length_ratio', 1.0))) * blen
                            ly1 = by + float(line_def.get('y1_offset_ratio', line_def.get('y0_offset_ratio', 0) + line_def.get('length_ratio', 1.0))) * blen
                            abs_lines.append({'x0': lx0, 'y0': ly0, 'x1': lx1, 'y1': ly1, 'type': 'line'})
                except Exception: pass

            if not abs_lines:
                abs_lines.append({'x0': bx, 'y0': by, 'x1': bx + blen, 'y1': by, 'type': 'line'})

            # 4. Рамка (Bounding Box)
            xs = [l['x0'] for l in abs_lines] + [l['x1'] for l in abs_lines]
            ys = [l['y0'] for l in abs_lines] + [l['y1'] for l in abs_lines]
            ui_rect = {
                'x': min(xs), 'y': min(ys), 
                'w': max(max(xs) - min(xs), 1.0), 
                'h': max(max(ys) - min(ys), 1.0)
            }

            ev = ExprEvaluator(eval_ctx)

            # 5. Точка захоплення (Anchor)
            anchor_pos = None
            anch_def = tmpl.get('anchor', {})
            if anch_def and anch_def.get('x') and anch_def.get('y'):
                try:
                    anchor_pos = {
                        'x': ev.eval(anch_def['x']),
                        'y': ev.eval(anch_def['y'])
                    }
                    eval_ctx['anchor'] = anchor_pos
                    ev = ExprEvaluator(eval_ctx) 
                except: pass

            # 6. Текстові зони
            ghost_zones = []
            variants = tmpl.get('variants', [])
            if variants:
                for tz in variants[0].get('text_zones', []):
                    field_name = tz.get('field', 'unknown')
                    repeat_expr = tz.get('repeat_over', "").strip()
                    try:
                        count = 1
                        if repeat_expr:
                            try: count = int(ev.eval(repeat_expr))
                            except: count = 1
                            
                        for i in range(count):
                            ev.safe_env['i'] = i
                            x0, y0 = ev.eval(tz['x0']), ev.eval(tz['y0'])
                            x1, y1 = ev.eval(tz['x1']), ev.eval(tz['y1'])
                            
                            if i == 0 or i == count - 1:
                                ghost_zones.append({
                                    'field': f"{field_name}[{i}]" if count > 1 else field_name,
                                    'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1
                                })
                    except Exception:
                        pass
            # 6.5. Service Zones
            service_zones = []
            for sz in tmpl.get('service_zones', []):
                field_name = sz.get('field', 'unknown')
                try:
                    x0, y0 = ev.eval(sz['x0']), ev.eval(sz['y0'])
                    x1, y1 = ev.eval(sz['x1']), ev.eval(sz['y1'])
                    service_zones.append({
                        'field': field_name,
                        'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1
                    })
                except Exception:
                    pass

            self.ghost_preview.update_preview(abs_lines, ghost_zones, anchor_pos, ui_rect, service_zones)
        except Exception as e:
            print(f"[Live Preview] Тимчасова помилка побудови: {e}")


    def open_settings_dialog(self):
        """Відкриває єдиний діалог налаштувань відображення."""
        dlg = SettingsDialog(self.state, parent=self)
        dlg.exec()
        # Завжди оновлюємо сцену після закриття (навіть Cancel не відкочує вже збережені кольори)
        self.refresh_scene_styles()

    def switch_mode(self, new_mode):
        self.state.set_mode(new_mode)
        self.btn_mode_config.setChecked(new_mode == "CONFIG")
        self.btn_mode_validate.setChecked(new_mode == "VALIDATE")
        if new_mode == "CONFIG":
            self.left_dock.show(); self.right_dock.show(); self.nav_dock.show()
            self.right_main_stack.setCurrentIndex(0)
            self.container_config_tree.show()
            self.group_found_list.hide()
            self._set_validation_buttons_enabled(False)
            self.load_pdf_page(self.state.page_num)
        elif new_mode == "VALIDATE":
            self.left_dock.show(); self.right_dock.show(); self.nav_dock.show()
            self.right_main_stack.setCurrentIndex(1)
            self.container_config_tree.hide()
            self.group_found_list.show()
            self._set_validation_buttons_enabled(True)
            
            # --- Пункт 7c: Безпечне очищення Ghost Preview ---
            try:
                if hasattr(self, 'ghost_preview') and self.ghost_preview and not sip.isdeleted(self.ghost_preview):
                    self.ghost_preview.hide_preview()
            except Exception:
                pass
            
            # --- Пункт 1: Завжди лише load_cached — без авто-пошуку ---
            self.load_pdf_page(self.state.page_num)
            self.load_cached_validation()

    def approve_all_validations(self):
        page_key = str(self.state.page_num)
        if page_key in self.state.session_cache:
            for obj in self.state.session_cache[page_key]:
                obj['status'] = 'approved'
            
            # ЗАМІСТЬ save_setting ПРИМУСОВО СИНХРОНІЗУЄМО
            self.state.sync_page_to_db(self.state.page_num, self.state.session_cache[page_key], status="approved")
            
        if hasattr(self, 'table_model'):
            for obj in self.table_model.objects: obj['status'] = 'approved'
            self.table_model.layoutChanged.emit()
            
        for item in self.scene.items():
            if hasattr(item, 'set_status'): item.set_status('approved')
        self.manual_save_to_db()

    def _set_validation_buttons_enabled(self, enabled: bool):
        """Показує/ховає кнопки валідації залежно від режиму."""
        for btn in (self.btn_approve_all, self.btn_run_engine, self.btn_batch):
            btn.setVisible(enabled)

    def recalculate_object_ocr(self, val_box, include_service=False):
        """Миттєво перечитує текст для зон, розмір яких змінили мишкою."""
        import pdfplumber
        try:
            with pdfplumber.open(self.state.pdf_path) as pdf:
                page = pdf.pages[self.state.page_num]
                obj = val_box.found_obj
                ur = obj.custom_zones.get('ui_rect', {})
                if not ur: return
                
                # OCR для звичайних text_zones
                for tz in obj.custom_zones.get('ghost_zones', []):
                    zx0 = ur['x'] + tz['rx0'] * ur['w']
                    zy0 = ur['y'] + tz['ry0'] * ur['h']
                    zx1 = ur['x'] + tz['rx1'] * ur['w']
                    zy1 = ur['y'] + tz['ry1'] * ur['h']
                    text = extract_text_by_center(page, zx0, zy0, zx1, zy1)
                    obj.text_fields[tz['field']] = text
                
                # OCR для service_zones (читаємо в text_fields теж — для conditions та відображення)
                for sz in obj.custom_zones.get('service_ghost_zones', []):
                    zx0 = ur['x'] + sz['rx0'] * ur['w']
                    zy0 = ur['y'] + sz['ry0'] * ur['h']
                    zx1 = ur['x'] + sz['rx1'] * ur['w']
                    zy1 = ur['y'] + sz['ry1'] * ur['h']
                    text = extract_text_by_center(page, zx0, zy0, zx1, zy1)
                    obj.text_fields[sz['field']] = text
                            
            if hasattr(self, 'table_model'):
                self.table_model.layoutChanged.emit()
            self.refresh_inspector()
            self.manual_save_to_db()
        except Exception as e:
            print(f"Помилка OCR при ресайзі: {e}")

    def action_change_pending_color(self):
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor()
        if color.isValid():
            # 1. Зберігаємо в базу назавжди
            self.state.save_setting("color_pending", color.name())
            
            # 2. Оновлюємо всі рамки на поточній сторінці миттєво
            if hasattr(self, 'refresh_scene_styles'):
                self.refresh_scene_styles()
            elif hasattr(self, 'refresh_ui_palette'):
                self.refresh_ui_palette()


    def navigate_validations(self, direction):
        if self.state.current_mode != "VALIDATE": return
        boxes = [item for item in self.scene.items() if isinstance(item, ValidationBox)]
        if not boxes: return
        boxes.sort(key=lambda b: (b.y(), b.x()))
        current_idx = next((i for i, b in enumerate(boxes) if b.is_selected), -1)
        next_idx = 0 if direction > 0 else len(boxes) - 1 if current_idx == -1 else (current_idx + direction) % len(boxes)
        self.on_validation_box_clicked(boxes[next_idx])
        self.view.ensureVisible(boxes[next_idx].sceneBoundingRect(), 50, 50)
    
    def start_anchor_snapping(self):
        """Запускає режим вибору точки"""
        if not self.current_base_raw:
            return QMessageBox.warning(self, "Увага", "Спочатку створіть або виберіть базовий елемент (base_element)!")
        self.view.start_snapping()

    def on_anchor_snapped(self, ax, ay):
        """Приймає точні координати з полотна та перетворює на формулу для EPLAN"""
        base_role = "base_element"
        ref_raw = self.current_base_raw
        if not ref_raw: return
        
        bx = ref_raw.get('x0', 0)
        by = ref_raw.get('top', ref_raw.get('y0', 0))
        blen = ref_raw.get('length', 1)

        def make_formula(val, base_val, base_var):
            rel = (val - base_val) / blen
            if abs(rel) < 0.0001: return f"{base_role}.{base_var}"
            sign = "+" if rel > 0 else "-"
            return f"{base_role}.{base_var} {sign} {base_role}.length * {abs(rel):.4f}"

        # Записуємо формулу в поля інспектора
        self.edit_anch_x.setText(make_formula(ax, bx, "x0"))
        self.edit_anch_y.setText(make_formula(ay, by, "y0"))
        
        # Перемикаємось на вкладку anchor, щоб користувач побачив результат
        self.prop_stack.setCurrentWidget(self.w_anchor)
        self.update_properties_to_state()

    def action_export_csv(self):
        """Професійний експорт: групування за шаблонами та гарантія глобальних колонок."""
        approved_objects = [obj for obj in self.table_model.objects if obj.get("status") == "approved"]
        
        if not approved_objects: 
            return QMessageBox.information(self, "Експорт", "Немає підтверджених об'єктів для експорту (поставте галочки).")
            
        from PyQt6.QtWidgets import QFileDialog
        import os, csv, json
        from datetime import datetime
        
        export_dir = QFileDialog.getExistingDirectory(self, "Оберіть папку для збереження")
        if not export_dir: return
            
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_dir = os.path.join(export_dir, f"EPLAN_Export_{timestamp}")
            os.makedirs(session_dir, exist_ok=True)
            
            template_groups = {}
            for obj in approved_objects:
                name = obj.get("template_name", "General")
                template_groups.setdefault(name, []).append(obj)
                
            for tmpl_name, objects in template_groups.items():
                file_path = os.path.join(session_dir, f"{tmpl_name}.csv")
                dynamic_keys = set()
                for o in objects:
                    dynamic_keys.update(o.get("text_fields", {}).keys())
                
                # === ГАРАНТІЯ ГЛОБАЛЬНИХ КОЛОНОК (Page_Data) ===
                is_tree = hasattr(self.list_templates, 'topLevelItemCount')
                t_count = self.list_templates.topLevelItemCount() if is_tree else self.list_templates.count()
                
                for i in range(t_count):
                    item = self.list_templates.topLevelItem(i) if is_tree else self.list_templates.item(i)
                    if not item: continue
                    try:
                        user_data = item.data(0, Qt.ItemDataRole.UserRole) if is_tree else item.data(Qt.ItemDataRole.UserRole)
                        with open(user_data, 'r', encoding='utf-8') as f:
                            t = json.load(f)
                            if t.get('Page_Data', False):
                                t_name = t['name']
                                for var in t.get('variants', []):
                                    for tz in var.get('text_zones', []):
                                        dynamic_keys.add(f"{t_name}_{tz['field']}")
                    except Exception: pass
                # ===============================================
                
                dynamic_keys.discard("channel_id")
                sorted_dyn = sorted(list(dynamic_keys))
                headers = ['Page', 'Template_Name', 'Channel_ID', 'Pos_X', 'Pos_Y', 'Page_W', 'Page_H'] + sorted_dyn
                
                with open(file_path, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=headers, delimiter=';', quoting=csv.QUOTE_ALL, extrasaction='ignore')
                    writer.writeheader()
                    for obj in objects:
                        anchor = obj.get("anchor", {})
                        text_data = obj.get("text_fields", {})
                        row = {
                            "Page": obj.get("page_num", 0) + 1,
                            "Template_Name": obj.get("template_name"),
                            "Channel_ID": text_data.get("channel_id", ""),
                            "Pos_X": round(anchor.get('x', 0), 2),
                            "Pos_Y": round(anchor.get('y', 0), 2),
                            "Page_W": round(obj.get("page_w", 0.0), 2),
                            "Page_H": round(obj.get("page_h", 0.0), 2)
                        }
                        for key in sorted_dyn: row[key] = text_data.get(key, "")
                        writer.writerow(row)
                        
            QMessageBox.information(self, "Успіх", f"Експорт завершено!\nПапка: {session_dir}")
        except Exception as e: 
            QMessageBox.critical(self, "Помилка", f"Не вдалося експортувати: {e}")

    def export_csv_from_cache(self, auto: bool = False):
        """Збирає об'єкти з кешу та зберігає у CSV. Нова структура V3."""
        import csv, json, os, fitz
        from datetime import datetime

        with fitz.open(self.state.pdf_path) as doc:
            total_pages = len(doc)

        if auto:
            fltr = {"mode": "all"}
        else:
            dlg = ExportRangeDialog(total_pages, parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            fltr = dlg.get_filter()

        self.state.load_all_from_db()
        all_objects = []
        seen_ids = set()
        cache = self.state.session_cache

        for key in sorted(cache.keys(), key=lambda k: int(k)):
            page_idx = int(key)
            if fltr["mode"] == "range" and not (fltr["from"] <= page_idx < fltr["to"]):
                continue
            for o in cache[key]:
                if fltr["mode"] == "approved" and o.get("status", "pending") != "approved":
                    continue
                anchor = o.get("anchor", {})
                uid = (page_idx, o.get("template_name", ""),
                       round(anchor.get("x", 0), 1), round(anchor.get("y", 0), 1))
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)
                o['_page_idx'] = page_idx
                all_objects.append(o)

        if not all_objects:
            return QMessageBox.information(self, "Експорт", "Немає об'єктів для фільтру.")

        export_dir = os.path.dirname(self.state.pdf_path) if auto else QFileDialog.getExistingDirectory(self, "Папка для CSV")
        if not export_dir:
            return

        try:
            session_dir = os.path.join(export_dir,
                f"EPLAN_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            os.makedirs(session_dir, exist_ok=True)

            # --- Збір порядку полів із шаблонів ---
            template_field_order = {}   # {tmpl_name: [field1, field2, ...]}
            page_data_field_order = {}  # {tmpl_name: [field1, field2, ...]}

            is_tree = hasattr(self.list_templates, 'topLevelItemCount')
            t_count = self.list_templates.topLevelItemCount() if is_tree else self.list_templates.count()
            for i in range(t_count):
                item = self.list_templates.topLevelItem(i) if is_tree else self.list_templates.item(i)
                if not item:
                    continue
                try:
                    user_data = item.data(0, Qt.ItemDataRole.UserRole) if is_tree else item.data(Qt.ItemDataRole.UserRole)
                    with open(user_data, 'r', encoding='utf-8') as f:
                        t = json.load(f)
                    t_name = t.get('name', '')
                    fields = []
                    for var in t.get('variants', []):
                        for tz in var.get('text_zones', []):
                            fn = tz.get('field', '')
                            if fn and fn not in fields:
                                fields.append(fn)
                    if t.get('Page_Data', False):
                        page_data_field_order[t_name] = fields
                    else:
                        template_field_order[t_name] = fields
                except Exception:
                    pass

            # --- Групуємо по шаблонах ---
            groups = {}
            for obj in all_objects:
                groups.setdefault(obj.get("template_name", "General"), []).append(obj)

            total_written = 0
            for tmpl_name, objects in groups.items():
                file_path = os.path.join(session_dir, f"{tmpl_name}.csv")

                # Порядок полів об'єкта — з шаблону або з ghost_zones
                obj_fields = template_field_order.get(tmpl_name, [])
                # Додаємо service_zones з export=true
                service_fields_export = []
                for i_t in range(t_count):
                    item = self.list_templates.topLevelItem(i_t) if is_tree else self.list_templates.item(i_t)
                    if not item: continue
                    try:
                        user_data = item.data(0, Qt.ItemDataRole.UserRole) if is_tree else item.data(Qt.ItemDataRole.UserRole)
                        with open(user_data, 'r', encoding='utf-8') as f:
                            t = json.load(f)
                        if t.get('name', '') == tmpl_name:
                            for sz in t.get('service_zones', []):
                                if sz.get('export', False):
                                    fn = sz.get('field', '')
                                    if fn and fn not in obj_fields:
                                        service_fields_export.append(fn)
                            break
                    except Exception: pass
                obj_fields = obj_fields + service_fields_export
                if not obj_fields:
                    seen = set()
                    for o in objects:
                        for gz in o.get('custom_zones', {}).get('ghost_zones', []):
                            fn = gz.get('field', '')
                            base_fn = fn.split('[')[0]
                            if base_fn and base_fn not in seen:
                                seen.add(base_fn)
                                obj_fields.append(base_fn)

                # Page_Data колонки — з усіх Page_Data шаблонів, у порядку шаблону
                pd_columns = []
                for pd_name, pd_fields in page_data_field_order.items():
                    for fn in pd_fields:
                        col = f"{pd_name}_{fn}"
                        if col not in pd_columns:
                            pd_columns.append(col)

                # Фінальні заголовки
                headers = ['ID', 'PagePDF', 'Template_Name', 'Variant', 'Status',
                           'Pos_X', 'Pos_Y', 'Page_W', 'Page_H'] + obj_fields + pd_columns

                # Сортуємо: PagePDF → Template_Name → оригінальний порядок
                objects.sort(key=lambda o: (o.get('_page_idx', 0), o.get('template_name', '')))

                # Збираємо Page_Data по сторінках для впорскування при експорті
                page_data_map = {}  # {page_idx: {col_name: value}}
                for pg_key in sorted(cache.keys(), key=lambda k: int(k)):
                    for o in cache[pg_key]:
                        t_name = o.get('template_name', '')
                        if t_name in page_data_field_order:
                            pd = page_data_map.setdefault(int(pg_key), {})
                            for fn in page_data_field_order[t_name]:
                                col = f"{t_name}_{fn}"
                                val = o.get('text_fields', {}).get(fn, '')
                                if val:
                                    pd[col] = val

                with open(file_path, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=headers,
                                            delimiter=';', quoting=csv.QUOTE_ALL,
                                            extrasaction='ignore')
                    writer.writeheader()
                    for idx, obj in enumerate(objects, start=1):
                        page_idx = obj.get('_page_idx', obj.get('page_num', 0))
                        row = {
                            "ID": idx,
                            "PagePDF": page_idx + 1,
                            "Template_Name": obj.get("template_name", ""),
                            "Variant": obj.get("variant_name", ""),
                            "Status": obj.get("status", "pending"),
                            "Pos_X": round(obj.get("anchor", {}).get('x', 0), 2),
                            "Pos_Y": round(obj.get("anchor", {}).get('y', 0), 2),
                            "Page_W": round(obj.get("page_w", 0), 2),
                            "Page_H": round(obj.get("page_h", 0), 2),
                        }
                        tf = obj.get("text_fields", {})
                        for fn in obj_fields:
                            row[fn] = tf.get(fn, "")
                        # Page_Data — впорскуємо з page_data_map
                        pd = page_data_map.get(page_idx, {})
                        for col in pd_columns:
                            row[col] = pd.get(col, "")
                        writer.writerow(row)
                        total_written += 1

            QMessageBox.information(self, "Успіх",
                f"Експортовано {total_written} об'єктів у {len(groups)} файлів.\n{session_dir}")
        except Exception as e:
            QMessageBox.critical(self, "Помилка експорту", str(e))


    # ══════════════════════════════════════════════════════════════════════════
    # УПРАВЛІННЯ ДЕРЕВОМ ТА ВЛАСТИВОСТЯМИ
    # ══════════════════════════════════════════════════════════════════════════
    def update_json_preview(self):
        self.json_preview.setText(json.dumps(self.state.template_data, indent=2, ensure_ascii=False))

    def scan_template_library(self):
        """Завантажує бібліотеку шаблонів у QTreeWidget, сортуючи за пріоритетом (0 = найвищий)."""
        self.list_templates.blockSignals(True)
        self.list_templates.clear()
        tmpl_dir = Path(self.state.templates_dir)
        entries = []
        if tmpl_dir.exists():
            for f in tmpl_dir.glob("*.json"):
                try:
                    with open(f, 'r', encoding='utf-8') as file:
                        data = json.load(file)
                        is_enabled = data.get("enabled", True)
                        priority = data.get("priority", 50)
                except:
                    is_enabled, priority = False, 50
                entries.append((priority, is_enabled, f))
        entries.sort(key=lambda e: e[0])
        for priority, is_enabled, f in entries:
            item = QTreeWidgetItem([f.name, str(priority)])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(0, Qt.CheckState.Checked if is_enabled else Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, str(f))
            item.setToolTip(0, f"Файл: {f.name}\nШлях: {f}")
            item.setToolTip(1, "Пріоритет сканування.\n0 = обробляється першим.\n999 = обробляється останнім.\nПодвійний клік для зміни.")
            self.list_templates.addTopLevelItem(item)
        self.list_templates.blockSignals(False)

    def on_template_selected(self, item, column=0):
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not file_path or not os.path.exists(file_path): return
        try:
            with open(file_path, 'r', encoding='utf-8') as f: self.state.update_template(json.load(f))
            self.current_base_raw = None; self.current_selected_node = None; self.prop_stack.setCurrentWidget(self.w_blank)
            self.config_raw_elements.clear()
            self.rebuild_tree()
        except Exception as e: QMessageBox.critical(self, "Помилка", f"Не вдалося прочитати:\n{e}")
    

    def on_table_selection_changed(self, selected, deselected):
        """Заповнює інспектор: окремим об'єктом або всіма текстами сторінки"""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTableWidgetItem
        
        indexes = selected.indexes()
        self.inspector_table.blockSignals(True)
        self.inspector_table.setRowCount(0)
        
        if not indexes:
            # --- РЕЖИМ ГЛОБАЛЬНОГО ОГЛЯДУ ---
            all_rows = []
            for obj_idx, obj in enumerate(self.table_model.objects):
                tmpl = obj.get("template_name", "Unknown")
                fields = obj.get("text_fields", {})
                for k, v in fields.items():
                    all_rows.append({"display_key": f"[{tmpl}] {k}", "real_key": k, "value": v, "obj_idx": obj_idx})
            
            self.inspector_table.setRowCount(len(all_rows))
            for i, r_data in enumerate(all_rows):
                key_item = QTableWidgetItem(r_data["display_key"])
                key_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                # Надійно зберігаємо дані по окремих комірках
                key_item.setData(Qt.ItemDataRole.UserRole, r_data["obj_idx"])
                key_item.setData(Qt.ItemDataRole.UserRole + 1, r_data["real_key"])
                
                val_item = QTableWidgetItem(str(r_data["value"]))
                val_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
                
                self.inspector_table.setItem(i, 0, key_item)
                self.inspector_table.setItem(i, 1, val_item)
        else:
            # --- РЕЖИМ ОДНОГО ОБ'ЄКТА ---
            row_idx = indexes[0].row()
            obj_dict = self.table_model.objects[row_idx]
            fields = obj_dict.get("text_fields", {})
            self.inspector_table.setRowCount(len(fields))
            for i, (k, v) in enumerate(fields.items()):
                key_item = QTableWidgetItem(k)
                key_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                key_item.setData(Qt.ItemDataRole.UserRole, row_idx)
                key_item.setData(Qt.ItemDataRole.UserRole + 1, k)
                
                val_item = QTableWidgetItem(str(v))
                val_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
                
                self.inspector_table.setItem(i, 0, key_item)
                self.inspector_table.setItem(i, 1, val_item)
        
        self.inspector_table.blockSignals(False)

    # 3. Оновлюємо метод кліку по осередку Live Preview (Корекція ЗУМУ)
    def on_inspector_cell_clicked(self, row, column):
        from PyQt6.QtCore import Qt, QRectF, QPointF
        key_item = self.inspector_table.item(row, 0)
        if not key_item: return
        
        obj_idx = key_item.data(Qt.ItemDataRole.UserRole)
        field_name = key_item.data(Qt.ItemDataRole.UserRole + 1)
        if obj_idx is None or field_name is None: return

        target_box = None
        for item in self.scene.items():
            if isinstance(item, ValidationBox) and hasattr(item, 'row_index') and item.row_index == obj_idx:
                target_box = item
                break
        
        if not target_box: return

        # Активуємо ізоляцію ТА підсвітку конкретного поля
        self.action_isolate_object(obj_idx, zoom=False, active_field=field_name)

        # Розраховуємо зону тексту та ЗУМИМО ТУДИ
        if 'ghost_zones' in target_box.found_obj.custom_zones:
            zone_data = next((z for z in target_box.found_obj.custom_zones['ghost_zones'] 
                            if z['field'] == field_name), None)
            if zone_data:
                rect = target_box.rect()
                zx = rect.x() + zone_data['rx0'] * rect.width()
                zy = rect.y() + zone_data['ry0'] * rect.height()
                zw = (zone_data['rx1'] - zone_data['rx0']) * rect.width()
                zh = (zone_data['ry1'] - zone_data['ry0']) * rect.height()
                
                # ВАЖЛИВО: Перетворення локальних координат зони в координати сцени
                local_zone_rect = QRectF(zx, zy, zw, zh)
                # mapToScene перетворює прямокутник об'єкта в глобальну систему координат
                scene_zone_rect = target_box.mapToScene(local_zone_rect).boundingRect()
                
                # Тепер центруємо камеру на глобальному прямокутнику зони
                self.view.centerOn(scene_zone_rect.center())

    def on_inspector_cell_changed(self, row, column):
        """Надійне збереження тексту при редагуванні будь-де"""
        if column != 1: return
        from PyQt6.QtCore import Qt
        
        key_item = self.inspector_table.item(row, 0)
        val_item = self.inspector_table.item(row, 1)
        if not key_item or not val_item: return
        
        obj_idx = key_item.data(Qt.ItemDataRole.UserRole)
        field_key = key_item.data(Qt.ItemDataRole.UserRole + 1)
        if obj_idx is None or field_key is None: return
        
        new_val = val_item.text()
        self.table_model.objects[obj_idx]["text_fields"][field_key] = new_val
        self.table_model.dataChanged.emit(
            self.table_model.index(obj_idx, 2), 
            self.table_model.index(obj_idx, 2)
        )

    def on_inspector_item_changed(self, item):
        """Зберігає правки тексту. Працює і в одиночному, і в глобальному режимі"""
        if item.column() != 1: return
        row = item.row()
        key_item = self.inspector_table.item(row, 0)
        if not key_item: return
        
        # Дізнаємося, якому об'єкту належить цей рядок
        data = key_item.data(Qt.ItemDataRole.UserRole)
        if not data: return
        obj_idx, field_key = data
        
        new_val = item.text()
        # Оновлюємо дані в моделі
        self.table_model.objects[obj_idx]["text_fields"][field_key] = new_val
        
        # Повідомляємо основну таблицю, що дані змінилися (для оновлення колонки 2)
        self.table_model.dataChanged.emit(
            self.table_model.index(obj_idx, 2), 
            self.table_model.index(obj_idx, 2)
        )

    def on_template_item_changed(self, item, column):
        """Обробляє зміну галочки (колонка 0) та пріоритету (колонка 1) у бібліотеці шаблонів."""
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not file_path or not os.path.exists(file_path):
            return
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            changed = False
            # --- Галочка enabled ---
            is_checked = (item.checkState(0) == Qt.CheckState.Checked)
            if data.get("enabled", True) != is_checked:
                data["enabled"] = is_checked
                changed = True
            # --- Пріоритет (колонка 1) ---
            if column == 1:
                new_text = item.text(1).strip()
                try:
                    new_priority = int(new_text)
                    new_priority = max(0, min(999, new_priority))
                except ValueError:
                    new_priority = data.get("priority", 50)
                if data.get("priority", 50) != new_priority:
                    data["priority"] = new_priority
                    changed = True
                # Коригуємо відображення (якщо юзер ввів текст)
                item.setText(1, str(new_priority))
            if changed:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                # Пересортовуємо список за новим пріоритетом
                if column == 1:
                    self.scan_template_library()
        except Exception as e:
            print(f"Помилка оновлення шаблону: {e}")
    
    def rebuild_tree(self):
        self.tree_widget.clear()
        # === ДОДАНО: Налаштування Шаблону ===
        set_root = QTreeWidgetItem(["SETTINGS", ""])
        set_root.setData(0, Qt.ItemDataRole.UserRole, ("settings", 0, -1))
        self.tree_widget.addTopLevelItem(set_root)
        # ====================================
        
        geom_root = QTreeWidgetItem(["GEOMETRY", ""])
        for i, elem in enumerate(self.state.template_data.get("geometry", {}).get("lines", [])):
            role = elem.get("role", "unknown") + (" (BASE)" if elem.get("is_base") else "")
            item = QTreeWidgetItem([role, elem.get("type", "?")])
            item.setData(0, Qt.ItemDataRole.UserRole, ("geometry", i, -1)); geom_root.addChild(item)
            
        constr_root = QTreeWidgetItem(["CONSTRAINTS", ""]); constr_root.setData(0, Qt.ItemDataRole.UserRole, ("constraints", 0, -1))
        anch_root = QTreeWidgetItem(["ANCHOR", ""]); anch_root.setData(0, Qt.ItemDataRole.UserRole, ("anchor", 0, -1))

        vars_root = QTreeWidgetItem(["VARIABLES", ""])
        for key, val in self.state.template_data.get("variables", {}).items():
            item = QTreeWidgetItem([key, str(val)])
            item.setData(0, Qt.ItemDataRole.UserRole, ("variables", key, -1)); vars_root.addChild(item)

        pins_root = QTreeWidgetItem(["PINS", ""]); pins_root.setData(0, Qt.ItemDataRole.UserRole, ("pins", 0, -1))

        sz_root = QTreeWidgetItem(["SERVICE_ZONES", ""])
        for i, sz in enumerate(self.state.template_data.get("service_zones", [])):
            sz_item = QTreeWidgetItem([sz.get("field", f"service_{i}"), sz.get("x0", "")])
            sz_item.setData(0, Qt.ItemDataRole.UserRole, ("service_zones", i, -1))
            sz_root.addChild(sz_item)

        var_root = QTreeWidgetItem(["VARIANTS", ""])
        for i, var in enumerate(self.state.template_data.get("variants", [])):
            v_item = QTreeWidgetItem([var.get("name", f"variant_{i}"), var.get("condition", "")])
            v_item.setData(0, Qt.ItemDataRole.UserRole, ("variants", i, -1))
            for j, tz in enumerate(var.get("text_zones", [])):
                tz_item = QTreeWidgetItem([tz.get("field", f"zone_{j}"), tz.get("x0", "")])
                tz_item.setData(0, Qt.ItemDataRole.UserRole, ("text_zones", j, i))
                v_item.addChild(tz_item)
            var_root.addChild(v_item)

        out_root = QTreeWidgetItem(["OUTPUT_FIELDS", ""]); out_root.setData(0, Qt.ItemDataRole.UserRole, ("output", 0, -1))


        self.tree_widget.addTopLevelItem(geom_root); self.tree_widget.addTopLevelItem(constr_root)
        self.tree_widget.addTopLevelItem(anch_root); self.tree_widget.addTopLevelItem(vars_root)
        self.tree_widget.addTopLevelItem(pins_root); self.tree_widget.addTopLevelItem(sz_root)
        self.tree_widget.addTopLevelItem(var_root); self.tree_widget.addTopLevelItem(out_root)
        self.tree_widget.expandAll()

    def _set_prop_signals_blocked(self, blocked):
        """Блокує або розблоковує сигнали UI-елементів інспектора."""
        widgets = [
            self.edit_role, self.edit_x0_off, self.edit_y0_off, self.edit_len_rat, 
            self.edit_pl_rat, self.edit_pl_rat_w, self.edit_pl_rat_h, 
            self.edit_wid_rat, self.edit_rad_rat, self.edit_cnt_min, self.edit_cnt_max,
            self.edit_xy_tol, self.edit_lw_tol, self.edit_anch_x, self.edit_anch_y, 
            self.edit_anch_w, self.edit_anch_h, self.edit_var_name, self.edit_var_expr, 
            self.edit_variant_name, self.edit_variant_cond,
            self.edit_tz_field, self.edit_tz_x0, self.edit_tz_y0, self.edit_tz_x1, self.edit_tz_y1, 
            self.edit_tz_join, self.edit_tz_repeat, self.edit_tz_sep,
            self.edit_sz_field, self.edit_sz_x0, self.edit_sz_y0, self.edit_sz_x1, self.edit_sz_y1,
            self.edit_pin_search, self.edit_pin_len, self.edit_pin_sides, 
            self.edit_pin_x0_min, self.edit_pin_x0_max,
            self.edit_ar_min, self.edit_ar_max, self.edit_ihl_min, self.edit_ihl_max,
            self.edit_out_fields,
            self.edit_type, self.edit_mode, self.edit_tz_collect,
            self.chk_is_base, self.chk_anch_exp, self.chk_tz_multi, self.chk_page_data,
            self.chk_sz_required, self.chk_sz_export
        ]
        for w in widgets:
            w.blockSignals(blocked)

    def on_tree_item_clicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data: self.current_selected_node = None; self.prop_stack.setCurrentWidget(self.w_blank); return
            
        category, identifier, var_idx = data
        self.current_selected_node = data
        self._set_prop_signals_blocked(True)
        
        if category == "geometry":
            self.prop_stack.setCurrentWidget(self.w_geom)
            elem = self.state.template_data["geometry"]["lines"][identifier]
            
            # --- Завантажуємо базові параметри ---
            self.edit_role.setText(str(elem.get("role", "")))
            self.edit_type.setCurrentText(str(elem.get("type", "H")))
            self.edit_mode.setCurrentText(str(elem.get("mode", "single")))
            self.chk_is_base.setChecked(elem.get("is_base", False))
            
            self.edit_x0_off.setText(str(elem.get("x0_offset_ratio", "")))
            self.edit_y0_off.setText(str(elem.get("y0_offset_ratio", "")))
            self.edit_len_rat.setText(str(elem.get("length_ratio", "")))
            self.edit_wid_rat.setText(str(elem.get("width_ratio", "")))
            self.edit_rad_rat.setText(str(elem.get("radius_ratio", "")))
            
            # === СПЕЦИФІКАЦІЯ V5.2: ЗАВАНТАЖУЄМО RATIOS В ІНСПЕКТОР ===
            self.edit_pl_rat.setText(str(elem.get("page_length_ratio", "")))
            self.edit_pl_rat_w.setText(str(elem.get("page_ratio_W", "")))
            self.edit_pl_rat_h.setText(str(elem.get("page_ratio_H", "")))
            # ==========================================================
            
            self.edit_cnt_min.setText(str(elem.get("count", {}).get("min", "")))
            self.edit_cnt_max.setText(str(elem.get("count", {}).get("max", "")))
            self.edit_xy_tol.setText(str(elem.get("xy_tol", "")))
            self.edit_lw_tol.setText(str(elem.get("lw_tol", "")))

        elif category == "constraints":
            self.prop_stack.setCurrentWidget(self.w_constr)
            constr = self.state.template_data.get("geometry", {}).get("constraints", {})
            self.edit_ar_min.setText(str(constr.get("aspect_ratio", {}).get("min", ""))); self.edit_ar_max.setText(str(constr.get("aspect_ratio", {}).get("max", "")))
            self.edit_ihl_min.setText(str(constr.get("inner_h_lines_count", {}).get("min", ""))); self.edit_ihl_max.setText(str(constr.get("inner_h_lines_count", {}).get("max", "")))

        elif category == "anchor":
            self.prop_stack.setCurrentWidget(self.w_anchor)
            anch = self.state.template_data.get("anchor", {})
            self.edit_anch_x.setText(str(anch.get("x", ""))); self.edit_anch_y.setText(str(anch.get("y", "")))
            self.edit_anch_w.setText(str(anch.get("width", ""))); self.edit_anch_h.setText(str(anch.get("height", "")))
            self.chk_anch_exp.setChecked(anch.get("export", False))

        elif category == "variables":
            self.prop_stack.setCurrentWidget(self.w_var)
            self.edit_var_name.setText(identifier); self.edit_var_expr.setText(str(self.state.template_data["variables"].get(identifier, "")))

        elif category == "variants":
            self.prop_stack.setCurrentWidget(self.w_variant)
            elem = self.state.template_data["variants"][identifier]
            self.edit_variant_name.setText(elem.get("name", "")); self.edit_variant_cond.setText(elem.get("condition", ""))

        elif category == "text_zones":
            self.prop_stack.setCurrentWidget(self.w_tz)
            elem = self.state.template_data["variants"][var_idx]["text_zones"][identifier]
            
            self.combo_tz_ref.clear()
            for l in self.state.template_data.get("geometry", {}).get("lines", []):
                role = l.get("role", "unknown")
                is_b = " (БАЗА)" if l.get("is_base") else ""
                self.combo_tz_ref.addItem(f"{role}{is_b}", userData=role)
            
            self.edit_tz_field.setText(elem.get("field", "")); self.edit_tz_x0.setText(elem.get("x0", "")); self.edit_tz_y0.setText(elem.get("y0", ""))
            self.edit_tz_x1.setText(elem.get("x1", "")); self.edit_tz_y1.setText(elem.get("y1", ""))
            self.edit_tz_repeat.setText(elem.get("repeat_over", "")); self.edit_tz_collect.setCurrentText(elem.get("collect", "")); self.edit_tz_sep.setText(elem.get("separator", ""))
            self.chk_tz_multi.setChecked(elem.get("multiline", False)); self.edit_tz_join.setText(elem.get("join", "\\n").replace('\n','\\n'))

        # ... (попередній код методу)
        elif category == "pins":
            self.prop_stack.setCurrentWidget(self.w_pins)
            elem = self.state.template_data.get("pins", {})
            if isinstance(elem, list):
                elem = elem[0] if len(elem) > 0 else {}
                self.state.template_data["pins"] = elem
                
            self.edit_pin_search.setText(str(elem.get("search_margin_ratio", ""))); self.edit_pin_len.setText(str(elem.get("max_length_ratio", "")))
            self.edit_pin_sides.setText(",".join(elem.get("sides", [])))
            ranges = elem.get("x0_in_range", [])
            self.edit_pin_x0_min.setText(str(ranges[0]) if len(ranges) > 0 else ""); self.edit_pin_x0_max.setText(str(ranges[1]) if len(ranges) > 1 else "")

        elif category == "output":
            self.prop_stack.setCurrentWidget(self.w_out)
            self.edit_out_fields.setText(", ".join(self.state.template_data.get("output_fields", [])))
        # === ДОДАНО ===
        elif category == "service_zones":
            self.prop_stack.setCurrentWidget(self.w_sz)
            elem = self.state.template_data["service_zones"][identifier]
            self.combo_sz_ref.clear()
            for l in self.state.template_data.get("geometry", {}).get("lines", []):
                role = l.get("role", "unknown")
                is_b = " (БАЗА)" if l.get("is_base") else ""
                self.combo_sz_ref.addItem(f"{role}{is_b}", userData=role)
            self.edit_sz_field.setText(elem.get("field", ""))
            self.edit_sz_x0.setText(elem.get("x0", "")); self.edit_sz_y0.setText(elem.get("y0", ""))
            self.edit_sz_x1.setText(elem.get("x1", "")); self.edit_sz_y1.setText(elem.get("y1", ""))
            self.chk_sz_required.setChecked(elem.get("required", False))
            self.chk_sz_export.setChecked(elem.get("export", False))
        elif category == "settings":
            self.prop_stack.setCurrentWidget(self.w_settings)
            self.chk_page_data.setChecked(self.state.template_data.get("Page_Data", False))
            self.spin_priority.setValue(self.state.template_data.get("priority", 50))
        # ==============    
        # Знімаємо блокування сигналів (один раз!)
        self._set_prop_signals_blocked(False)
        
        # --- ОНОВЛЕННЯ LIVE PREVIEW ---
        self._refresh_ghost_preview()


    def update_properties_to_state(self):
        if not self.current_selected_node: return
        category, identifier, var_idx = self.current_selected_node
        
        def set_float(elem, k, w):
            v = w.text().replace(',', '.').strip()
            if v:
                try: elem[k] = float(v)
                except: pass
            else: elem.pop(k, None)

        if category == "geometry":
            elem = self.state.template_data["geometry"]["lines"][identifier]
            elem["role"] = self.edit_role.text().strip()
            elem["type"] = self.edit_type.currentText()
            
            if self.edit_mode.currentText() == "collect": elem["mode"] = "collect"
            else: elem.pop("mode", None)
            
            if self.chk_is_base.isChecked(): elem["is_base"] = True
            else: elem.pop("is_base", None)
            
            set_float(elem, "x0_offset_ratio", self.edit_x0_off)
            set_float(elem, "y0_offset_ratio", self.edit_y0_off)
            set_float(elem, "length_ratio", self.edit_len_rat)
            set_float(elem, "width_ratio", self.edit_wid_rat)
            set_float(elem, "radius_ratio", self.edit_rad_rat)
            set_float(elem, "xy_tol", self.edit_xy_tol)
            set_float(elem, "lw_tol", self.edit_lw_tol)
            
            # --- ВЕРСІЯ V5.2: ЗБЕРЕЖЕННЯ RATIOS ЯК ОДНОГО ЧИСЛА ---
            set_float(elem, "page_length_ratio", self.edit_pl_rat)
            set_float(elem, "page_ratio_W", self.edit_pl_rat_w)
            set_float(elem, "page_ratio_H", self.edit_pl_rat_h)
            # -------------------------------------------------------

            # Словник "count" залишаємо, бо він потрібний для mode: "collect"
            c_min, c_max = self.edit_cnt_min.text().strip(), self.edit_cnt_max.text().strip()
            if c_min or c_max:
                cnt = elem.get("count", {})
                if c_min: cnt["min"] = int(c_min)
                if c_max: cnt["max"] = int(c_max)
                elem["count"] = cnt
            else: elem.pop("count", None)

        elif category == "constraints":
            constr = self.state.template_data["geometry"].setdefault("constraints", {})
            ar_min, ar_max = self.edit_ar_min.text().replace(',', '.').strip(), self.edit_ar_max.text().replace(',', '.').strip()
            if ar_min or ar_max:
                ar = constr.setdefault("aspect_ratio", {})
                if ar_min: ar["min"] = float(ar_min)
                if ar_max: ar["max"] = float(ar_max)
            else: constr.pop("aspect_ratio", None)
            
            ihl_min, ihl_max = self.edit_ihl_min.text().strip(), self.edit_ihl_max.text().strip()
            if ihl_min or ihl_max:
                ihl = constr.setdefault("inner_h_lines_count", {})
                if ihl_min: ihl["min"] = int(ihl_min)
                if ihl_max: ihl["max"] = int(ihl_max)
            else: constr.pop("inner_h_lines_count", None)

        elif category == "anchor":
            anch = self.state.template_data["anchor"]
            anch["x"] = self.edit_anch_x.text().strip(); anch["y"] = self.edit_anch_y.text().strip()
            anch["width"] = self.edit_anch_w.text().strip(); anch["height"] = self.edit_anch_h.text().strip()
            anch["export"] = self.chk_anch_exp.isChecked()

        elif category == "variables":
            new_name = self.edit_var_name.text().strip()
            if new_name and new_name != identifier:
                self.state.template_data["variables"][new_name] = self.edit_var_expr.text().strip()
                del self.state.template_data["variables"][identifier]
                self.current_selected_node = ("variables", new_name, -1)
            else: self.state.template_data["variables"][identifier] = self.edit_var_expr.text().strip()

        elif category == "variants":
            elem = self.state.template_data["variants"][identifier]
            elem["name"] = self.edit_variant_name.text().strip(); elem["condition"] = self.edit_variant_cond.text().strip()

        elif category == "text_zones":
            elem = self.state.template_data["variants"][var_idx]["text_zones"][identifier]
            elem["field"] = self.edit_tz_field.text().strip()
            elem["x0"] = self.edit_tz_x0.text().strip(); elem["y0"] = self.edit_tz_y0.text().strip()
            elem["x1"] = self.edit_tz_x1.text().strip(); elem["y1"] = self.edit_tz_y1.text().strip()
            rep = self.edit_tz_repeat.text().strip(); col = self.edit_tz_collect.currentText(); sep = self.edit_tz_sep.text()
            if rep: elem["repeat_over"] = rep
            else: elem.pop("repeat_over", None)
            if col: elem["collect"] = col
            else: elem.pop("collect", None)
            if sep: elem["separator"] = sep
            else: elem.pop("separator", None)
            if self.chk_tz_multi.isChecked():
                elem["multiline"] = True; elem["join"] = self.edit_tz_join.text().replace('\\n','\n')
            else:
                elem.pop("multiline", None); elem.pop("join", None)

        elif category == "pins":
            elem = self.state.template_data.setdefault("pins", {})
            set_float(elem, "search_margin_ratio", self.edit_pin_search); set_float(elem, "max_length_ratio", self.edit_pin_len)
            sides = self.edit_pin_sides.text().strip()
            if sides: elem["sides"] = [s.strip() for s in sides.split(",")]
            r_min, r_max = self.edit_pin_x0_min.text().strip(), self.edit_pin_x0_max.text().strip()
            if r_min or r_max: elem["x0_in_range"] = [r_min, r_max]
            else: elem.pop("x0_in_range", None)

        elif category == "output":
            fields = [f.strip() for f in self.edit_out_fields.toPlainText().split(',') if f.strip()]
            self.state.template_data["output_fields"] = fields
        # === ДОДАНО ===
        elif category == "service_zones":
            elem = self.state.template_data["service_zones"][identifier]
            elem["field"] = self.edit_sz_field.text().strip()
            elem["x0"] = self.edit_sz_x0.text().strip(); elem["y0"] = self.edit_sz_y0.text().strip()
            elem["x1"] = self.edit_sz_x1.text().strip(); elem["y1"] = self.edit_sz_y1.text().strip()
            elem["required"] = self.chk_sz_required.isChecked()
            elem["export"] = self.chk_sz_export.isChecked()
        elif category == "settings":
            self.state.template_data["Page_Data"] = self.chk_page_data.isChecked()
            self.state.template_data["priority"] = self.spin_priority.value()
        # ==============
        self.state.update_template(self.state.template_data)
        
        item = self.tree_widget.currentItem()
        if item:
            if category == "geometry": item.setText(0, self.edit_role.text() + (" (BASE)" if self.chk_is_base.isChecked() else ""))
            elif category == "variables": item.setText(0, self.current_selected_node[1]); item.setText(1, self.edit_var_expr.text())
            elif category == "text_zones": item.setText(0, self.edit_tz_field.text())
            elif category == "variants": item.setText(0, self.edit_variant_name.text()); item.setText(1, self.edit_variant_cond.text())
    
    def _sync_global_page_data(self):
        """Збирає дані з ON-шаблонів на сцені і миттєво роздає OFF-шаблонам."""
        if not hasattr(self, 'table_model') or not self.table_model.objects: return
        
        # 1. Знаходимо, які шаблони в бібліотеці є глобальними (ON)
        templates_info = {}
        global_data = {}
        
        is_tree = hasattr(self.list_templates, 'topLevelItemCount')
        t_count = self.list_templates.topLevelItemCount() if is_tree else self.list_templates.count()
        
        for i in range(t_count):
            item = self.list_templates.topLevelItem(i) if is_tree else self.list_templates.item(i)
            if not item: continue
            try:
                user_data = item.data(0, Qt.ItemDataRole.UserRole) if is_tree else item.data(Qt.ItemDataRole.UserRole)
                with open(user_data, 'r', encoding='utf-8') as f:
                    t = json.load(f)
                    is_global = t.get('Page_Data', False)
                    # ... (далі ваш код) ...
                    templates_info[t['name']] = is_global
                    
                    # Формуємо порожній котел для гарантії наявності стовпців
                    if is_global:
                        t_name = t['name']
                        for var in t.get("variants", []):
                            for tz in var.get("text_zones", []):
                                global_data[f"{t_name}_{tz['field']}"] = ""
            except: pass

        # 2. Збираємо реальні тексти з ON-об'єктів, що зараз є на кресленні
        for obj in self.table_model.objects:
            if templates_info.get(obj.get('template_name')) == True:
                t_name = obj['template_name']
                for k, v in obj.get('text_fields', {}).items():
                    global_data[f"{t_name}_{k}"] = v

        # 3. Впорскуємо ці дані усім OFF-об'єктам
        changed = False
        for obj in self.table_model.objects:
            if templates_info.get(obj.get('template_name')) != True:
                if 'text_fields' not in obj: obj['text_fields'] = {}
                for k, v in global_data.items():
                    if obj['text_fields'].get(k) != v:
                        obj['text_fields'][k] = v
                        changed = True

        if changed:
            self.table_model.layoutChanged.emit()
            self.refresh_inspector()
            self.manual_save_to_db()

    def delete_selected(self):
        # --- ЛОГІКА ДЛЯ РЕЖИМУ ВАЛІДАЦІЇ (Видалення рамок) ---
        if self.state.current_mode == "VALIDATE":
            indexes = self.table_view.selectionModel().selectedRows()
            if not indexes: return
            row_idx = indexes[0].row()
            
            item_to_remove = None
            for item in self.scene.items():
                if isinstance(item, ValidationBox) and getattr(item, 'row_index', -1) == row_idx:
                    item_to_remove = item; break
                    
            if item_to_remove: self.scene.removeItem(item_to_remove)
                
            self.table_model.beginRemoveRows(QModelIndex(), row_idx, row_idx)
            del self.table_model.objects[row_idx]
            self.table_model.endRemoveRows()
            
            # Зміщуємо індекси рамок, які залишилися нижче
            for item in self.scene.items():
                if isinstance(item, ValidationBox) and hasattr(item, 'row_index') and item.row_index > row_idx:
                    item.row_index -= 1
                        
            self.table_view.clearSelection()
            self.action_clear_isolation()
            self.refresh_inspector()
            self.manual_save_to_db()
            self._sync_global_page_data()
            return

        # --- СТАРА ЛОГІКА ДЛЯ РЕЖИМУ КОНФІГУРАЦІЇ ---
        if not self.current_selected_node: return
        category, identifier, var_idx = self.current_selected_node
        if category == "geometry":
            if self.state.template_data["geometry"]["lines"][identifier].get("is_base"):
                from PyQt6.QtWidgets import QMessageBox
                if QMessageBox.question(self, "Видалення БАЗИ", "Очистити весь шаблон?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                    self.state.template_data["geometry"]["lines"] = []; self.current_base_raw = None; self.config_raw_elements.clear()
                else: return
            else: del self.state.template_data["geometry"]["lines"][identifier]
        elif category == "variables": del self.state.template_data["variables"][identifier]
        elif category == "variants": del self.state.template_data["variants"][identifier]
        elif category == "text_zones": del self.state.template_data["variants"][var_idx]["text_zones"][identifier]
        elif category == "service_zones": del self.state.template_data["service_zones"][identifier]
        self.current_selected_node = None; self.prop_stack.setCurrentWidget(self.w_blank); self.rebuild_tree(); self.state.update_template(self.state.template_data)

    def action_add_variable(self):
        vars_dict = self.state.template_data.setdefault("variables", {})
        vars_dict[f"new_var_{len(vars_dict)}"] = "0"
        self.state.update_template(self.state.template_data); self.rebuild_tree()

    def action_add_variant(self):
        self.state.template_data.setdefault("variants", []).append({"name": f"variant_{len(self.state.template_data['variants'])}", "condition": "True", "text_zones": []})
        self.state.update_template(self.state.template_data); self.rebuild_tree()

    def action_add_text_zone(self):
        if not self.current_selected_node or self.current_selected_node[0] != "variants":
            return QMessageBox.warning(self, "Увага", "Виділіть Варіант (Variant) у дереві, куди додати Text Zone!")
        
        var_idx = self.current_selected_node[1]
        tz_list = self.state.template_data["variants"][var_idx].setdefault("text_zones", [])
        
        # === ЗАХИСТ ВІД ДУБЛІКАТІВ ТЕКСТОВИХ ПОЛІВ ===
        existing_fields = [tz.get("field", "") for tz in tz_list]
        base_field = "new_field"
        final_field = base_field
        counter = 1
        while final_field in existing_fields:
            final_field = f"{base_field}_{counter}"
            counter += 1
        # =============================================
        
        tz_list.append({"field": final_field, "x0": "", "y0": "", "x1": "", "y1": ""})
        self.state.update_template(self.state.template_data)
        self.rebuild_tree()
    def action_add_service_zone(self):
        """Додає service_zone до шаблону."""
        sz_list = self.state.template_data.setdefault("service_zones", [])
        existing = [sz.get("field", "") for sz in sz_list]
        base = "service_field"
        final = base
        counter = 1
        while final in existing:
            final = f"{base}_{counter}"
            counter += 1
        sz_list.append({
            "field": final, "x0": "", "y0": "", "x1": "", "y1": "",
            "required": False, "export": False
        })
        self.state.update_template(self.state.template_data)
        self.rebuild_tree()

    def action_open_pdf(self):
        """Відкриває діалогове вікно вибору PDF та ініціалізує всі робочі процеси."""
        # --- 7b: Підтвердження збереження при наявності прогресу ---
        if self.state.session_cache:
            reply = QMessageBox.question(
                self, "Зберегти сесію?",
                "Є незбережений прогрес поточного документа.\nЗберегти сесію перед відкриттям нового файлу?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes:
                self.manual_save_to_db()

        file_path, _ = QFileDialog.getOpenFileName(self, "Відкрити PDF", "", "PDF Files (*.pdf)")
        
        if file_path:
            # 0. Зупиняємо активні воркери
            self._stop_all_workers()
            
            # 1. Очищаємо кеш старого документа (Баг #4)
            self.state.clear_cache()
            
            # 2. Очищаємо UI: таблиця, інспектор, сцена
            self.table_model.clear()
            self.inspector_table.setRowCount(0)
            self.current_base_raw = None
            self.config_raw_elements.clear()
            
            # 3. Оновлюємо стан проекту
            self.state.pdf_path = file_path
            self.state.page_num = 0
            
            # 4. Очищаємо навігатор та перезапускаємо мініатюри
            self.list_thumbnails.clear()
            self.original_thumbnails.clear()
            
            self.thumb_worker = ThumbnailWorker(self.state.pdf_path, max_width=160)
            self.thumb_worker.thumbnail_ready.connect(self.on_thumbnail_ready)
            self.thumb_worker.start()
            
            # 5. Завантажуємо першу сторінку
            self.load_pdf_page(0)
            
            # 6. Перемикаємо на CONFIG (чистий старт)
            self.switch_mode("CONFIG")
            
            # 7. Оновлюємо заголовок вікна
            self.setWindowTitle(f"EPLAN Studio v2.0 - {os.path.basename(file_path)}")
            
            # 8. Синхронізуємо вікно БД якщо відкрите
            if hasattr(self, 'db_window') and self.db_window and self.db_window.isVisible():
                self.db_window.setWindowTitle(f"База даних об'єктів — {os.path.basename(file_path)}")
                self.db_window.refresh_data()
            
            print(f"[UI] PDF відкрито: {file_path}. Кеш очищено. Мініатюри перезапущено.")

    def _stop_all_workers(self):
        """Безпечна зупинка всіх фонових потоків."""
        for attr in ('search_worker', 'batch_worker', 'thumb_worker'):
            worker = getattr(self, attr, None)
            if worker:
                if hasattr(worker, 'stop'):
                    worker.stop()
                if hasattr(worker, 'wait'):
                    worker.wait(2000)
    
    def action_save_session_as(self):
        """Зберігає поточну сесію у файл .epss."""
        # Спочатку синхронізуємо поточну сторінку
        if self.table_model.objects:
            self.state.session_cache[str(self.state.page_num)] = [
                dict(obj) for obj in self.table_model.objects
            ]
        
        default_name = os.path.splitext(os.path.basename(self.state.pdf_path or "session"))[0]
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Зберегти сесію", default_name + ".epss",
            "EPLAN Session (*.epss)")
        if not file_path:
            return
        
        if self.state.export_session(file_path):
            QMessageBox.information(self, "Збережено",
                f"Сесію збережено:\n{os.path.basename(file_path)}")
        else:
            QMessageBox.critical(self, "Помилка", "Не вдалося зберегти сесію.")

    def action_open_session(self):
        """Відкриває збережену сесію .epss з головного вікна."""
        # Підтвердження збереження поточного прогресу
        if self.state.session_cache:
            reply = QMessageBox.question(
                self, "Зберегти поточну сесію?",
                "Є незбережений прогрес.\nЗберегти перед відкриттям іншої сесії?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes:
                self.action_save_session_as()

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Відкрити сесію", "", "EPLAN Session (*.epss)")
        if not file_path:
            return

        data = self.state.import_session(file_path)
        if not data:
            return QMessageBox.critical(self, "Помилка", "Не вдалося прочитати файл сесії.")

        pdf_path = data.get("pdf_path", "")
        if not pdf_path or not os.path.exists(pdf_path):
            return QMessageBox.critical(
                self, "Помилка",
                f"PDF-файл із сесії не знайдено:\n{pdf_path}")

        # Зупиняємо воркери
        self._stop_all_workers()

        # Завантажуємо стан
        loaded_cache = data.get("session_cache", {})
        self.state.clear_cache()
        self.state.pdf_path = pdf_path
        self.state.templates_dir = data.get("templates_dir", self.state.templates_dir)
        self.state.page_num = data.get("page_num", 0)
        self.state.session_cache = loaded_cache
        for page_key, objects in loaded_cache.items():
            self.state.sync_page_to_db(int(page_key), objects, status="saved")

        # Оновлюємо UI
        self.table_model.clear()
        self.inspector_table.setRowCount(0)
        self.current_base_raw = None
        self.config_raw_elements.clear()

        self.list_thumbnails.clear()
        self.original_thumbnails.clear()
        self.thumb_worker = ThumbnailWorker(self.state.pdf_path, max_width=160)
        self.thumb_worker.thumbnail_ready.connect(self.on_thumbnail_ready)
        self.thumb_worker.start()

        self.load_pdf_page(self.state.page_num)
        self.switch_mode("VALIDATE")
        self.setWindowTitle(f"EPLAN Studio v2.0 - {os.path.basename(pdf_path)}")

        if hasattr(self, 'db_window') and self.db_window and self.db_window.isVisible():
            self.db_window.setWindowTitle(f"База даних об'єктів — {os.path.basename(pdf_path)}")
            self.db_window.refresh_data()

    def action_new_template(self):
        # Зберігаємо розміри сторінки перед очищенням
        ph = self.state.template_data.get("_page_height", 842)
        pw = self.state.template_data.get("_page_width", 595)
        # Додано global_xy_tol та global_lw_tol за замовчуванням
        self.state.template_data = {
            "name": "new_template", 
            "version": "1.0", 
            "enabled": True, 
            "Page_Data": False,     # <--- ДОДАЙТЕ ЦЕЙ РЯДОК
            "global_xy_tol": 0.02,  # Допуск 2% для координат
            "global_lw_tol": 0.05,  # Допуск 5% для товщини ліній
            "priority": 50,         # Пріоритет сканування (0=найвищий)
            "_page_height": ph,  # <--- ВАЖЛИВО!
            "_page_width": pw,   # <--- ВАЖЛИВО!

            "geometry": {"lines": []}, 
            "variants": [], 
            "variables": {}, 
            "output_fields": [], 
            "anchor": {}, 
            "pins": {}
        }
        self.current_base_raw = None
        self.current_selected_node = None
        self.list_templates.clearSelection()
        self.prop_stack.setCurrentWidget(self.w_blank)
        self.config_raw_elements.clear()
        self.state.update_template(self.state.template_data)
        self.rebuild_tree()
        
        for item in self.scene.items():
            if isinstance(item, InteractiveMixin): 
                item.set_selected(False)

    def action_clone_template(self):
        if not self.state.template_data.get("geometry", {}).get("lines"): return
        cloned_data = json.loads(json.dumps(self.state.template_data))
        cloned_data["name"] += "_copy"; self.state.update_template(cloned_data); self.list_templates.clearSelection()

    def action_save_template(self):
        # === ВАЛІДАЦІЯ ТОЧКИ ЗАХОПЛЕННЯ ===
        anchor_data = self.state.template_data.get("anchor", {})
        if not anchor_data.get("x") or not anchor_data.get("y"):
            QMessageBox.critical(self, "Помилка збереження", 
                "Для експорту в EPLAN обов'язково потрібна Точка Захоплення!\n"
                "Перейдіть у властивості (Anchor) і вкажіть її на кресленні (кнопка 📍).")
            return
        # ==================================
        default_path = os.path.join(self.state.templates_dir, self.state.template_data.get("name", "template") + ".json")
        file_path, _ = QFileDialog.getSaveFileName(self, "Зберегти шаблон", default_path, "JSON Files (*.json)")
        if file_path:
            self.state.template_data["name"] = os.path.basename(file_path).replace('.json', '')
            with open(file_path, 'w', encoding='utf-8') as f: json.dump(self.state.template_data, f, indent=2, ensure_ascii=False)
            self.scan_template_library()
    
    def lock_ui(self, locked: bool):
        """Блокує кнопки навігації під час роботи фонового потоку"""
        self.btn_run_engine.setEnabled(not locked)
        self.btn_prev_page.setEnabled(not locked)
        self.btn_next_page.setEnabled(not locked)
        self.btn_save_progress.setEnabled(not locked)
        if locked:
            self.btn_run_engine.setText("⏳ Сканування...")
            self.btn_run_engine.setStyleSheet("background-color: #7f8c8d; color: white; font-weight: bold; padding: 10px;")
        else:
            self.btn_run_engine.setText("🔍 Знайти та Витягти текст (Run Engine)")
            self.btn_run_engine.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold; padding: 10px;")

    def on_inspector_selection_changed(self):
        """Фокусує камеру на Text Zone, навіть якщо об'єкт не вибраний в основній таблиці"""
        from PyQt6.QtCore import QRectF
        selected_items = self.inspector_table.selectedItems()
        if not selected_items: return

        row = selected_items[0].row()
        key_item = self.inspector_table.item(row, 0)
        data = key_item.data(Qt.ItemDataRole.UserRole)
        if not data: return
        obj_idx, field_name = data

        # 1. Шукаємо відповідну рамку на сцені
        target_box = None
        for item in self.scene.items():
            if isinstance(item, ValidationBox) and hasattr(item, 'row_index') and item.row_index == obj_idx:
                target_box = item
                break
        
        if not target_box: return

        # 2. Якщо в основній таблиці нічого не вибрано — вмикаємо ізоляцію для цього об'єкта вручну
        if not self.table_view.selectionModel().hasSelection():
             self.action_isolate_object(obj_idx, zoom=False) # zoom=False, бо ми самі наведемо зум на поле

        # 3. Розраховуємо зону тексту та зумимо
        if 'ghost_zones' in target_box.found_obj.custom_zones:
            zone_data = next((z for z in target_box.found_obj.custom_zones['ghost_zones'] 
                              if z['field'] == field_name), None)
            if zone_data:
                rect = target_box.rect()
                zx = target_box.x() + zone_data['rx0'] * rect.width()
                zy = target_box.y() + zone_data['ry0'] * rect.height()
                zw = (zone_data['rx1'] - zone_data['rx0']) * rect.width()
                zh = (zone_data['ry1'] - zone_data['ry0']) * rect.height()
                
                target_rect = QRectF(zx, zy, zw, zh).normalized()
                self.view.centerOn(target_rect.center())
                # Опціонально: можна підсвітити зону яскравіше
# ═══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК ПРОГРАМИ
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    app = QApplication(sys.argv)
    app_state = SessionState()
    
    if StartDashboard(app_state).exec() == QDialog.DialogCode.Accepted:
        main_window = TemplateStudioMainWindow(app_state)
        main_window.show()
        sys.exit(app.exec())