from __future__ import annotations

import sys
import re
from collections import Counter
from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QDate, QEvent, QMimeData, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QCursor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .calendar_settings import add_custom_family_day, add_custom_holiday, remove_family_day, remove_holiday
from .calendar_utils import family_days, is_duty_day, is_family_day, is_holiday_or_weekend, korean_holidays, month_dates, weekday_ko
from .database import delete_month_schedule, load_schedule_result, period_assignment_rows, replace_employee_unavailable_days, save_schedule, save_unavailable_days, saved_months
from .excel_io import export_schedule_to_excel, gray_cell_offsets_from_html_rows, import_schedule_from_excel, normalize_shift_code, parse_employees_from_tsv, parse_html_table, parse_schedule_from_clipboard, parse_schedule_from_html_rows, parse_schedule_from_tsv, parse_unavailable, parse_unavailable_from_clipboard, parse_unavailable_from_html_rows, parse_unavailable_from_html_rows_by_position
from .models import DAY_TYPE_LABELS, DAY_TYPE_ORDER, OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleMap, ScheduleResult, ShiftRules
from .module_settings import load_modules, save_modules
from .rule_settings import load_team_rules, save_team_rules
from .rule_utils import min_rules_for_date, rule_value_for_display, set_rule_value_from_display
from .schedule_utils import expand_gy_blocks
from .scheduler import ScheduleError, generate_month_schedule
from .stats import STAT_HEADERS, averages, compute_stats
from .stats_exclusions import exclude_person, excluded_people, include_person
from .validation import validate_schedule

SHIFT_OPTIONS = ["", SHIFT_DAY, SHIFT_SWING, SHIFT_GY, SHIFT_DUTY, SHIFT_GY_REST]
SHIFT_COLORS = {
    SHIFT_DAY: QColor("#fff2cc"),
    SHIFT_SWING: QColor("#d9ead3"),
    SHIFT_GY: QColor("#d9e2f3"),
    SHIFT_DUTY: QColor("#fce4d6"),
    SHIFT_GY_REST: QColor("#e7e6e6"),
    OFF: QColor("#ffffff"),
    "": QColor("#ffffff"),
}
WARNING_COLOR = QColor("#f4cccc")
UNAVAILABLE_COLOR = QColor("#e7e6e6")
HOLIDAY_HEADER_COLOR = QColor("#fce4d6")
FAMILY_HEADER_COLOR = QColor("#d9ead3")
STAFFING_OK_COLOR = QColor("#008000")
OVERVIEW_START_YEAR = 2025
NAME_COL_WIDTH = 70
ID_COL_WIDTH = 54
DAY_COL_WIDTH = 32
COMPACT_ROW_HEIGHT = 18
COMPACT_FONT_SIZE = 8
HEADER_FONT_SIZE = 9
VIEW_LEGACY = "legacy"
VIEW_V11 = "V11"
VIEW_V12 = "V12"
TEAM_VIEWS = (VIEW_V11, VIEW_V12)
RULE_COMBINED = "combined"
RULE_SETTING_OPTIONS = (
    (RULE_COMBINED, "V11/12 통합"),
    (VIEW_V11, "V11만"),
    (VIEW_V12, "V12만"),
)
LEGACY_LABEL = "기존"
LOCKED_SPLIT_COLOR = QColor("#f3f3f3")
TEAM_SPLIT_START_DATE = date(2026, 8, 1)
ADMIN_PASSWORD = "GMP1124"


class PasteTableWidget(QTableWidget):
    """QTableWidget with Excel-style tab/newline paste support."""

    def __init__(self, *args, allow_expand: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.allow_expand = allow_expand
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.Paste):
            text = QApplication.clipboard().text()
            self.paste_text(text)
            return
        super().keyPressEvent(event)

    def paste_text(self, text: str) -> None:
        if not text:
            return
        start_row = max(0, self.currentRow())
        start_col = max(0, self.currentColumn())
        rows = [line.split("\t") for line in text.rstrip("\n").splitlines()]
        if not rows:
            return
        if self.allow_expand:
            self.setRowCount(max(self.rowCount(), start_row + len(rows)))
            self.setColumnCount(max(self.columnCount(), start_col + max(len(r) for r in rows)))
        for r_offset, values in enumerate(rows):
            for c_offset, value in enumerate(values):
                row = start_row + r_offset
                col = start_col + c_offset
                if row >= self.rowCount() or col >= self.columnCount():
                    continue
                self.setItem(row, col, QTableWidgetItem(value.strip()))


class ShiftComboDelegate(QStyledItemDelegate):
    """Dropdown editor for schedule cells."""

    def createEditor(self, parent, option, index):  # type: ignore[override]
        if index.column() < 2:
            return None
        editor = QComboBox(parent)
        editor.addItems(SHIFT_OPTIONS)
        return editor

    def setEditorData(self, editor, index) -> None:  # type: ignore[override]
        if not isinstance(editor, QComboBox):
            return
        value = normalize_shift_code(index.data() or "")
        pos = editor.findText("" if value == OFF else value)
        editor.setCurrentIndex(max(0, pos))

    def setModelData(self, editor, model, index) -> None:  # type: ignore[override]
        if not isinstance(editor, QComboBox):
            return
        value = normalize_shift_code(editor.currentText())
        model.setData(index, "" if value == OFF else value)


class MonthRosterTable(PasteTableWidget):
    """A month table in the yearly view that accepts Ctrl+V directly."""

    def __init__(self, owner: "MainWindow", year: int, month: int, *args, **kwargs) -> None:
        super().__init__(*args, allow_expand=False, **kwargs)
        self.owner = owner
        self.year = year
        self.month = month
        self.setToolTip("이 월 표를 클릭한 뒤 Ctrl+V 하면 엑셀 근무표가 바로 붙여넣어집니다.")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.Copy):
            self.owner.copy_table_selection_to_clipboard(self, skip_columns=2)
            return
        if event.matches(QKeySequence.Paste):
            self.owner.paste_month_table_clipboard(self)
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.owner.clear_selected_schedule_cells(self, getattr(self, "result", None))
            return
        super().keyPressEvent(event)

    def paste_text(self, text: str) -> None:
        self.owner.paste_month_table_clipboard(self)


class CurrentMonthRosterTable(PasteTableWidget):
    """Main monthly roster table.

    The monthly roster must not use ordinary spreadsheet paste at the clicked
    cell because users paste the whole existing Excel roster. If we let
    QTableWidget paste TSV from the current cell, selecting e.g. the 20th day
    column writes the source "사번" column under the 20th day. Always route
    Ctrl+V through the schedule parser for the currently selected year/month.
    """

    def __init__(self, owner: "MainWindow", *args, **kwargs) -> None:
        super().__init__(*args, allow_expand=False, **kwargs)
        self.owner = owner
        self.setToolTip("큰 표는 전체 붙여넣기, 근무 코드만 복사한 작은 범위는 선택 셀부터 붙여넣기 됩니다.")
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.Copy):
            self.owner.copy_schedule_selection_to_clipboard()
            return
        if event.matches(QKeySequence.Paste):
            self.owner.paste_current_month_clipboard()
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.owner.clear_selected_schedule_cells(self, self.owner.result)
            return
        super().keyPressEvent(event)

    def paste_text(self, text: str) -> None:
        self.owner.paste_current_month_clipboard()


class ScheduleInputTable(PasteTableWidget):
    """Input helper table whose paste means 'read the whole roster clipboard'."""

    def __init__(self, owner: "MainWindow", *args, **kwargs) -> None:
        super().__init__(*args, allow_expand=False, **kwargs)
        self.owner = owner
        self.setToolTip("엑셀 근무표 전체를 복사한 뒤 Ctrl+V 하면 현재 월 근무표로 반영됩니다.")
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.Paste):
            self.owner.paste_schedule_from_clipboard()
            return
        super().keyPressEvent(event)

    def paste_text(self, text: str) -> None:
        self.owner.paste_schedule_from_clipboard()



class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GMP 근무표 자동 생성기")
        self.resize(1600, 900)

        self.employees: List[Employee] = []
        self.result: Optional[ScheduleResult] = None
        self.team_rules = load_team_rules()
        self.rule_settings_source = RULE_COMBINED
        self.rules = self.team_rules[self.rule_settings_source]
        self.module_names = load_modules()
        self.is_admin = False
        self.admin_only_widgets: List[QWidget] = []
        self.admin_only_actions: List[QAction] = []
        self._updating_table = False
        self._year_overview_refresh_pending = False
        self._year_overview_dirty = True
        self._cumulative_stats_dirty = True
        self._suppress_month_reload = False
        self._schedule_header_menu_connected = False
        self.month_split_page_index = 0
        self._preserve_roster_page_on_load = False

        self.year_spin = QSpinBox()
        self.year_spin.setRange(2020, 2100)
        self.year_spin.setKeyboardTracking(True)
        self.year_spin.setValue(date.today().year)
        self.month_spin = QSpinBox()
        self.month_spin.setRange(1, 12)
        self.month_spin.setKeyboardTracking(True)
        self.month_spin.setValue(date.today().month)

        self.schedule_source_label = QLabel("")

        self._build_rule_widgets()

        self.employee_table = ScheduleInputTable(self, 0, 5)
        self.employee_table.setHorizontalHeaderLabels(["성명", "사번", "신규", "불가일(YYYY-MM-DD, ...)", "모듈"])
        self.employee_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.employee_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.employee_table.customContextMenuRequested.connect(self.show_employee_module_menu)

        self.paste_box = QTextEdit()
        self.paste_box.setPlaceholderText("보조 입력칸입니다. 엑셀에서 표 범위 복사 → [엑셀 근무표 붙여넣기] 또는 표에서 Ctrl+V. 근무 코드와 회색 불가일을 함께 인식합니다.")
        self.paste_box.setMaximumHeight(100)

        self.schedule_table = CurrentMonthRosterTable(self, 0, 0)
        self.schedule_table.setItemDelegate(ShiftComboDelegate(self.schedule_table))
        self.schedule_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.schedule_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.schedule_table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self.schedule_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.schedule_table.customContextMenuRequested.connect(
            lambda pos: self.show_schedule_cell_menu(self.schedule_table, self.result, pos)
        )
        self.schedule_table.cellChanged.connect(self.on_schedule_cell_changed)
        self.schedule_table.cellDoubleClicked.connect(self.on_schedule_cell_double_clicked)
        self.month_split_scroll = QScrollArea()
        self.month_split_scroll.setWidgetResizable(True)
        self.month_split_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.month_split_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.month_split_content = QWidget()
        self.month_split_layout = QVBoxLayout(self.month_split_content)
        self.month_split_scroll.setWidget(self.month_split_content)
        self.month_split_scroll.hide()
        self.create_schedule_btn = QPushButton("근무표 생성")
        self.create_schedule_btn.setStyleSheet("font-weight: 700; padding: 8px;")
        self.create_schedule_btn.clicked.connect(self.create_missing_schedule_for_current_page)
        self.create_schedule_btn.hide()
        self.regenerate_schedule_btn = QPushButton("근무표 재생성")
        self.regenerate_schedule_btn.setStyleSheet("font-weight: 700; padding: 8px;")
        self.regenerate_schedule_btn.clicked.connect(self.regenerate_schedule_for_current_page)
        self.regenerate_schedule_btn.hide()
        self.seed_employees_btn = QPushButton("인원 반영")
        self.seed_employees_btn.setStyleSheet("font-weight: 700; padding: 8px;")
        self.seed_employees_btn.clicked.connect(self.seed_employees_for_current_page)
        self.seed_employees_btn.hide()

        self.month_stats_table = QTableWidget()
        self.cumulative_stats_table = QTableWidget()
        self.saved_months_table = QTableWidget()
        self.current_stats_row_people: List[tuple[str, str]] = []
        self.warning_box = QTextEdit()
        self.warning_box.setReadOnly(True)
        self.warning_box.setMinimumHeight(60)
        self.stats_start_year_spin = QSpinBox()
        self.stats_start_year_spin.setRange(2020, 2100)
        self.stats_start_year_spin.setValue(OVERVIEW_START_YEAR)
        self.stats_start_month_spin = QSpinBox()
        self.stats_start_month_spin.setRange(1, 12)
        self.stats_start_month_spin.setValue(1)
        self.stats_end_year_spin = QSpinBox()
        self.stats_end_year_spin.setRange(2020, 2100)
        self.stats_end_year_spin.setValue(date.today().year)
        self.stats_end_month_spin = QSpinBox()
        self.stats_end_month_spin.setRange(1, 12)
        self.stats_end_month_spin.setValue(date.today().month)
        self.stats_mode_combo = QComboBox()
        self.stats_mode_combo.addItems(["월별 필터 통계"])
        self.stats_mode_combo.currentTextChanged.connect(lambda _text: self.on_stats_mode_changed())
        self.stats_source_combo = QComboBox()
        self.stats_source_combo.addItem("전체", "")
        for team in TEAM_VIEWS:
            self.stats_source_combo.addItem(team, team)
        self.stats_source_combo.currentIndexChanged.connect(lambda _index: self.render_cumulative_stats())
        self.stats_value_mode_combo = QComboBox()
        self.stats_value_mode_combo.addItems(["갯수", "퍼센트", "갯수+퍼센트"])
        self.stats_value_mode_combo.currentTextChanged.connect(lambda _text: self.render_cumulative_stats())
        self.monthly_stats_date_types = [
            ("weekday", "평일"),
            ("family", "페데"),
            ("holiday", "공휴일"),
            ("saturday", "토요일"),
            ("sunday", "일요일"),
        ]
        self.monthly_stats_shift_types = [
            ("day", "Day"),
            ("swing", "SW"),
            ("gy", "GY"),
        ]
        self.monthly_stats_filters: set[tuple[str, str]] = {
            (date_key, shift_key)
            for date_key, _date_label in self.monthly_stats_date_types
            for shift_key, _shift_label in self.monthly_stats_shift_types
        }
        self.cumulative_stats_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cumulative_stats_table.customContextMenuRequested.connect(self.show_stats_table_menu)
        self.calendar_date_edit = QDateEdit()
        self.calendar_date_edit.setCalendarPopup(True)
        today = date.today()
        self.calendar_date_edit.setDate(QDate(today.year, today.month, today.day))

        self._build_ui()
        self.year_spin.valueChanged.connect(lambda _value: self.on_selected_month_changed())
        self.month_spin.valueChanged.connect(lambda _value: self.on_selected_month_changed())
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
        self.load_selected_month_from_db(refresh_overview=False)
        self.mark_year_overview_dirty()

    @staticmethod
    def legacy_source_name(year: int, month: int) -> str:
        return f"{year}-{month:02d}"

    @staticmethod
    def is_team_source(source_name: str) -> bool:
        return source_name in TEAM_VIEWS

    def selected_schedule_view(self) -> str:
        return VIEW_LEGACY

    def split_date(self) -> date:
        return TEAM_SPLIT_START_DATE

    def enabled_split_date(self) -> Optional[date]:
        return self.split_date()

    def month_has_team_dates(self, year: int, month: int) -> bool:
        split = self.enabled_split_date()
        return bool(split and month_dates(year, month)[-1] >= split)

    def source_name_for_view(self, year: int, month: int, view: Optional[str] = None) -> str:
        selected = view or self.selected_schedule_view()
        if selected in TEAM_VIEWS and self.month_has_team_dates(year, month):
            return selected
        return self.legacy_source_name(year, month)

    def source_label_for_view(self, year: int, month: int, view: Optional[str] = None) -> str:
        source_name = self.source_name_for_view(year, month, view)
        if source_name in TEAM_VIEWS:
            split = self.enabled_split_date()
            dates = month_dates(year, month)
            if split and dates[0] < split <= dates[-1]:
                return f"{source_name} (2026-08 전 기존 표시)"
            return source_name
        return LEGACY_LABEL

    def update_schedule_source_status(self) -> None:
        if not hasattr(self, "schedule_source_label"):
            return
        year = self.year_spin.value()
        month = self.month_spin.value()
        label = self.source_label_for_view(year, month)
        if self.month_has_team_dates(year, month):
            self.schedule_source_label.setText(f"보기: V11 위 / V12 아래 · 기준 {self.split_date():%Y-%m}")
        else:
            self.schedule_source_label.setText(f"보기/저장: {label} · V11/V12는 {self.split_date():%Y-%m}부터")

    def admin_edit_triggers(self) -> QAbstractItemView.EditTriggers:
        return (
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )

    def current_edit_triggers(self) -> QAbstractItemView.EditTriggers:
        return self.admin_edit_triggers() if self.is_admin else QAbstractItemView.NoEditTriggers

    def require_admin(self, action: str = "이 작업") -> bool:
        if self.is_admin:
            return True
        QMessageBox.warning(self, "관리자 권한 필요", f"{action}은 관리자만 할 수 있습니다. 관리자 로그인 후 다시 시도하세요.")
        return False

    def login_admin(self) -> None:
        password, ok = QInputDialog.getText(
            self,
            "관리자 로그인",
            "관리자 비밀번호",
            QLineEdit.Password,
        )
        if not ok:
            return
        if password != ADMIN_PASSWORD:
            QMessageBox.warning(self, "관리자 로그인 실패", "관리자 비밀번호가 맞지 않습니다.")
            return
        self.set_admin_mode(True)
        QMessageBox.information(self, "관리자 로그인", "관리자 모드로 전환했습니다.")

    def logout_admin(self) -> None:
        self.set_admin_mode(False)

    def set_admin_mode(self, enabled: bool) -> None:
        self.is_admin = enabled
        self.apply_access_mode()
        if self.result:
            self.render_schedule_table()

    def apply_access_mode(self) -> None:
        if hasattr(self, "role_label"):
            self.role_label.setText(f"권한: {'관리자' if self.is_admin else '일반'}")
        if hasattr(self, "admin_login_btn"):
            self.admin_login_btn.setVisible(not self.is_admin)
        if hasattr(self, "admin_logout_btn"):
            self.admin_logout_btn.setVisible(self.is_admin)
        for action in self.admin_only_actions:
            action.setEnabled(self.is_admin)
        for widget in self.admin_only_widgets:
            widget.setEnabled(self.is_admin)
        if hasattr(self, "schedule_table"):
            self.schedule_table.setEditTriggers(self.current_edit_triggers())
        if hasattr(self, "employee_table"):
            self.employee_table.setEditTriggers(self.current_edit_triggers())
        if hasattr(self, "paste_box"):
            self.paste_box.setEnabled(self.is_admin)
        if hasattr(self, "tabs") and hasattr(self, "settings_tab_index"):
            self.tabs.setTabEnabled(self.settings_tab_index, self.is_admin)

    @staticmethod
    def employee_display_name(emp: Employee) -> str:
        return f"{emp.name} ({emp.employee_id})" if emp.employee_id else emp.name

    def refresh_unavailable_employee_combo(self) -> None:
        if not hasattr(self, "unavailable_employee_combo"):
            return
        current_key = self.unavailable_employee_combo.currentData()
        self.unavailable_employee_combo.blockSignals(True)
        self.unavailable_employee_combo.clear()
        self.unavailable_employee_combo.addItem("내 이름 선택", "")
        for emp in self.employees:
            self.unavailable_employee_combo.addItem(self.employee_display_name(emp), emp.key)
        if current_key:
            index = self.unavailable_employee_combo.findData(current_key)
            if index >= 0:
                self.unavailable_employee_combo.setCurrentIndex(index)
        self.unavailable_employee_combo.blockSignals(False)

    def selected_unavailable_employee_key(self) -> str:
        if not hasattr(self, "unavailable_employee_combo"):
            return ""
        value = self.unavailable_employee_combo.currentData()
        return str(value or "")

    def refresh_module_list(self) -> None:
        if not hasattr(self, "module_list_widget"):
            return
        self.module_list_widget.clear()
        for name in self.module_names:
            self.module_list_widget.addItem(name)

    def add_module_from_input(self) -> None:
        if not hasattr(self, "module_name_edit"):
            return
        name = self.module_name_edit.text().strip()
        if not name:
            return
        if name not in self.module_names:
            self.module_names.append(name)
            save_modules(self.module_names)
            self.refresh_module_list()
        self.module_name_edit.clear()

    def remove_selected_module(self) -> None:
        if not hasattr(self, "module_list_widget"):
            return
        row = self.module_list_widget.currentRow()
        if row < 0 or row >= len(self.module_names):
            return
        name = self.module_names.pop(row)
        save_modules(self.module_names)
        self.refresh_module_list()
        self.clear_module_from_employees(name)

    def selected_employee_rows(self, fallback_row: int = -1) -> List[int]:
        rows = sorted({index.row() for index in self.employee_table.selectedIndexes() if index.row() >= 0})
        if not rows and fallback_row >= 0:
            rows = [fallback_row]
        return rows

    def show_employee_module_menu(self, pos) -> None:
        row = self.employee_table.rowAt(pos.y())
        rows = self.selected_employee_rows(row)
        if not rows:
            return
        menu = QMenu(self)
        for module_name in self.module_names:
            menu.addAction(module_name).triggered.connect(
                lambda _checked=False, r=rows, m=module_name: self.apply_module_to_employee_rows(r, m)
            )
        if self.module_names:
            menu.addSeparator()
        menu.addAction("모듈 없음").triggered.connect(
            lambda _checked=False, r=rows: self.apply_module_to_employee_rows(r, "")
        )
        menu.exec(self.employee_table.viewport().mapToGlobal(pos))

    def apply_module_to_employee_rows(self, rows: List[int], module_name: str) -> None:
        for row in rows:
            if row < 0 or row >= self.employee_table.rowCount():
                continue
            self.employee_table.setItem(row, 4, QTableWidgetItem(module_name))
        self.employees = self.collect_employees()
        if self.result:
            module_by_key = {emp.key: emp.module for emp in self.employees}
            self.result.employees = [
                Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates), module_by_key.get(emp.key, emp.module))
                for emp in self.result.employees
            ]
        self.statusBar().showMessage(f"{len(rows)}명 모듈 설정 완료", 4000)

    def apply_module_to_schedule_rows(self, result: ScheduleResult, rows: List[int], module_name: str) -> None:
        if hasattr(self, "require_admin") and not self.require_admin("모듈 설정"):
            return
        if not rows:
            return
        keys = {
            result.employees[row].key
            for row in rows
            if 0 <= row < len(result.employees)
        }
        if not keys:
            return
        for row in range(self.employee_table.rowCount()):
            name = self._cell_text(self.employee_table, row, 0)
            employee_no = self._cell_text(self.employee_table, row, 1)
            if f"{name}|{employee_no}" in keys:
                self.employee_table.setItem(row, 4, QTableWidgetItem(module_name))
        self.employees = [
            Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates), module_name if emp.key in keys else emp.module)
            for emp in self.collect_employees()
        ]
        result.employees = [
            Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates), module_name if emp.key in keys else emp.module)
            for emp in result.employees
        ]
        if self.result and result.year == self.result.year and result.month == self.result.month:
            self.result.employees = [
                Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates), module_name if emp.key in keys else emp.module)
                for emp in self.result.employees
            ]
        self.save_result_silently(result)
        self.mark_cumulative_stats_dirty()
        self.mark_year_overview_dirty()
        self.statusBar().showMessage(f"{len(keys)}명 모듈 설정 완료", 4000)

    def clear_module_from_employees(self, module_name: str) -> None:
        changed = False
        for row in range(self.employee_table.rowCount()):
            if self._cell_text(self.employee_table, row, 4) == module_name:
                self.employee_table.setItem(row, 4, QTableWidgetItem(""))
                changed = True
        if changed:
            self.employees = self.collect_employees()

    def toggle_unavailable_for_cell(self, table: QTableWidget, result: Optional[ScheduleResult], row: int, col: int) -> None:
        if self.is_admin or not isinstance(result, ScheduleResult) or col < 2:
            return
        selected_key = self.selected_unavailable_employee_key()
        if not selected_key:
            QMessageBox.warning(self, "이름 선택 필요", "상단의 내 이름을 먼저 선택하세요.")
            return
        if row < 0 or row >= len(result.employees):
            return
        emp = result.employees[row]
        if emp.key != selected_key:
            QMessageBox.warning(self, "본인 행만 수정", "일반 사용자는 선택한 본인 행의 근무 불가일만 표시할 수 있습니다.")
            return
        dates = month_dates(result.year, result.month)
        day_index = col - 2
        if day_index < 0 or day_index >= len(dates):
            return
        d = dates[day_index]
        if d in emp.unavailable_dates:
            emp.unavailable_dates.remove(d)
            action = "해제"
        else:
            emp.unavailable_dates.add(d)
            action = "표시"
        try:
            replace_employee_unavailable_days(emp, result.year, result.month, self.storage_source_name(result))
        except Exception as exc:
            QMessageBox.critical(self, "불가일 저장 실패", str(exc))
            return
        item = table.item(row, col)
        if item:
            shift = result.schedule.get(d, {}).get(emp.key, OFF)
            self.apply_schedule_cell_background(item, result, emp, d, shift, include_validation=True)
        if (
            self.result
            and self.result.year == result.year
            and self.result.month == result.month
            and self.storage_source_name(self.result) == self.storage_source_name(result)
        ):
            self.refresh_validation_and_stats()
        self.mark_year_overview_dirty()
        self.statusBar().showMessage(f"{d.isoformat()} {emp.name} 근무 불가일 {action} 완료", 4000)

    def set_current_month(self, year: int, month: int) -> None:
        self._suppress_month_reload = True
        try:
            self.year_spin.setValue(year)
            self.month_spin.setValue(month)
        finally:
            self._suppress_month_reload = False

    def roster_page_count(self, year: int, month: int) -> int:
        return len(TEAM_VIEWS) if self.month_has_team_dates(year, month) else 1

    def clamp_month_split_page(self, year: int, month: int) -> None:
        self.month_split_page_index = min(
            max(0, self.month_split_page_index),
            self.roster_page_count(year, month) - 1,
        )

    def shift_month_values(self, year: int, month: int, delta: int) -> tuple[int, int]:
        total = year * 12 + (month - 1) + delta
        return total // 12, total % 12 + 1

    def show_roster_page(self, year: int, month: int, page_index: int) -> bool:
        if year < self.year_spin.minimum() or year > self.year_spin.maximum():
            return False
        if month < self.month_spin.minimum() or month > self.month_spin.maximum():
            return False
        self.month_split_page_index = min(max(0, page_index), self.roster_page_count(year, month) - 1)
        self.set_current_month(year, month)
        self._preserve_roster_page_on_load = True
        try:
            self.on_selected_month_changed()
        finally:
            self._preserve_roster_page_on_load = False
        return True

    def move_roster_page(self, delta: int) -> bool:
        if delta == 0:
            return False
        year = self.year_spin.value()
        month = self.month_spin.value()
        if hasattr(self, "tabs") and self.tabs.currentIndex() == getattr(self, "year_tab_index", -1):
            next_year, next_month = self.shift_month_values(year, month, delta)
            return self.show_roster_page(next_year, next_month, 0)
        page_count = self.roster_page_count(year, month)
        page_index = self.month_split_page_index if page_count > 1 else 0
        target_index = page_index + delta
        if 0 <= target_index < page_count:
            self.month_split_page_index = target_index
            self.render_schedule_table()
            self.mark_year_overview_dirty()
            return True

        month_delta = 1 if delta > 0 else -1
        next_year, next_month = self.shift_month_values(year, month, month_delta)
        next_count = self.roster_page_count(next_year, next_month)
        next_index = 0 if delta > 0 else next_count - 1
        return self.show_roster_page(next_year, next_month, next_index)

    def empty_schedule_result(self, year: int, month: int, source_name: Optional[str] = None) -> ScheduleResult:
        employees: List[Employee] = []
        return ScheduleResult(
            year,
            month,
            employees,
            {d: {} for d in month_dates(year, month)},
            korean_holidays(year),
            source_name=source_name or self.source_name_for_view(year, month),
        )

    @staticmethod
    def clone_schedule_result(result: ScheduleResult, source_name: Optional[str] = None) -> ScheduleResult:
        employees = [
            Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates), emp.module)
            for emp in result.employees
        ]
        schedule = {
            d: dict(day_map)
            for d, day_map in result.schedule.items()
        }
        return ScheduleResult(
            result.year,
            result.month,
            employees,
            schedule,
            set(result.holidays),
            list(result.warnings),
            source_name if source_name is not None else result.source_name,
        )

    def load_legacy_schedule_result(self, year: int, month: int) -> Optional[ScheduleResult]:
        legacy_name = self.legacy_source_name(year, month)
        result = load_schedule_result(year, month, source_name=legacy_name, fallback_source_names=[""])
        if result and not result.source_name:
            result.source_name = legacy_name
        return result

    def load_existing_schedule_for_source(self, year: int, month: int, source_name: str) -> Optional[ScheduleResult]:
        if self.is_team_source(source_name) and self.month_has_team_dates(year, month):
            return load_schedule_result(year, month, source_name=source_name)
        return self.load_legacy_schedule_result(year, month)

    def load_schedule_for_view(self, year: int, month: int, view: Optional[str] = None) -> ScheduleResult:
        source_name = self.source_name_for_view(year, month, view)
        if self.is_team_source(source_name):
            loaded = load_schedule_result(year, month, source_name=source_name)
            if loaded:
                result = loaded
            else:
                legacy = self.load_legacy_schedule_result(year, month)
                result = self.clone_schedule_result(legacy, source_name) if legacy else self.empty_schedule_result(year, month, source_name)
            result.source_name = source_name
            self.apply_split_legacy_prefix(result)
            return result

        loaded = self.load_legacy_schedule_result(year, month)
        if loaded:
            return loaded
        return self.empty_schedule_result(year, month, source_name)

    def apply_split_legacy_prefix(self, result: ScheduleResult) -> None:
        split = self.enabled_split_date()
        if not split or not self.is_team_source(result.source_name):
            return
        dates = month_dates(result.year, result.month)
        if dates[0] >= split:
            return
        legacy = self.load_legacy_schedule_result(result.year, result.month)
        if not legacy:
            return

        employee_by_key = {emp.key: emp for emp in result.employees}
        for emp in legacy.employees:
            if emp.key not in employee_by_key:
                copied = Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates), emp.module)
                result.employees.append(copied)
                employee_by_key[copied.key] = copied

        for d in dates:
            day_map = result.schedule.setdefault(d, {})
            for emp in result.employees:
                day_map.setdefault(emp.key, OFF)
            if d >= split:
                continue
            legacy_day = legacy.schedule.get(d, {})
            for emp in result.employees:
                day_map[emp.key] = legacy_day.get(emp.key, OFF)

    def is_locked_split_cell(self, result: ScheduleResult, d: date) -> bool:
        split = self.enabled_split_date()
        return bool(split and self.is_team_source(result.source_name) and d < split)

    def storage_source_name(self, result: ScheduleResult) -> str:
        if result.source_name:
            if self.is_team_source(result.source_name) and not self.month_has_team_dates(result.year, result.month):
                return self.legacy_source_name(result.year, result.month)
            return result.source_name
        return self.source_name_for_view(result.year, result.month)

    def result_for_storage(self, result: ScheduleResult, source_name: str) -> ScheduleResult:
        stored = self.clone_schedule_result(result, source_name)
        split = self.enabled_split_date()
        if split and self.is_team_source(source_name):
            for d in month_dates(stored.year, stored.month):
                if d >= split:
                    continue
                day_map = stored.schedule.setdefault(d, {})
                for emp in stored.employees:
                    day_map[emp.key] = OFF
        return stored

    def save_result_to_db(self, result: ScheduleResult) -> int:
        if not self.is_admin:
            raise PermissionError("관리자만 근무표를 저장할 수 있습니다.")
        source_name = self.storage_source_name(result)
        stored = self.result_for_storage(result, source_name)
        schedule_id = save_schedule(stored, source_name)
        save_unavailable_days(stored.employees, source_name, stored.year, stored.month)
        result.source_name = source_name
        self.update_next_month_gy_carryover(stored)
        return schedule_id

    def load_selected_month_from_db(self, refresh_overview: bool = True) -> None:
        if self._suppress_month_reload:
            return
        year = self.year_spin.value()
        month = self.month_spin.value()
        self.result = self.load_schedule_for_view(year, month)
        self.apply_previous_month_gy_carryover(self.result)
        self.apply_split_legacy_prefix(self.result)
        self.employees = list(self.result.employees)
        self.add_employee_rows(self.employees)
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        self.update_schedule_source_status()
        if refresh_overview:
            self.mark_year_overview_dirty()

    def refresh_current_month_from_db(self) -> None:
        self.load_selected_month_from_db(refresh_overview=True)

    def on_selected_month_changed(self) -> None:
        if self._suppress_month_reload:
            return
        if not self._preserve_roster_page_on_load:
            self.month_split_page_index = 0
        self.load_selected_month_from_db()

    def on_tab_changed(self, index: int) -> None:
        if index == getattr(self, "year_tab_index", -1) and self._year_overview_dirty:
            self.schedule_year_overview_refresh()
        elif index == getattr(self, "stats_tab_index", -1) and self._cumulative_stats_dirty:
            self.render_cumulative_stats()

    def mark_year_overview_dirty(self) -> None:
        self._year_overview_dirty = True
        if hasattr(self, "tabs") and self.tabs.currentIndex() == getattr(self, "year_tab_index", -1):
            self.schedule_year_overview_refresh()

    def mark_cumulative_stats_dirty(self) -> None:
        self._cumulative_stats_dirty = True
        if hasattr(self, "tabs") and self.tabs.currentIndex() == getattr(self, "stats_tab_index", -1):
            self.render_cumulative_stats()

    def _build_rule_widgets(self) -> None:
        def spin(value: int, minimum: int = 0, maximum: int = 31) -> QSpinBox:
            widget = QSpinBox()
            widget.setRange(minimum, maximum)
            widget.setValue(value)
            return widget

        self.day_type_rule_spins: Dict[tuple[str, str], QSpinBox] = {}
        for day_type in DAY_TYPE_ORDER:
            for shift in (SHIFT_DAY, SHIFT_SWING, SHIFT_GY):
                self.day_type_rule_spins[(day_type, shift)] = spin(
                    rule_value_for_display(self.rules, day_type, shift)
                )
        self.max_consecutive_spin = spin(self.rules.max_consecutive_work_days, 1)
        self.max_consecutive_gy_spin = spin(self.rules.max_consecutive_gy, 1)

    def active_rule_settings_source(self) -> str:
        if hasattr(self, "rule_source_combo"):
            value = self.rule_source_combo.currentData()
            if value in self.team_rules:
                return str(value)
        return self.rule_settings_source

    def rules_for_source(self, source_name: str) -> ShiftRules:
        if source_name in TEAM_VIEWS:
            return self.team_rules[source_name]
        return self.team_rules[RULE_COMBINED]

    def current_result_rules(self, result: Optional[ScheduleResult] = None) -> ShiftRules:
        target = result or self.result
        if isinstance(target, ScheduleResult):
            source_name = self.storage_source_name(target)
        else:
            source_name = self.current_roster_source_name() if hasattr(self, "year_spin") else RULE_COMBINED
        return self.rules_for_source(source_name)

    def populate_rule_widgets(self, source_name: str) -> None:
        rules = self.rules_for_source(source_name)
        self.rules = rules
        for (day_type, shift), widget in self.day_type_rule_spins.items():
            widget.blockSignals(True)
            widget.setValue(rule_value_for_display(rules, day_type, shift))
            widget.blockSignals(False)
        for widget, value in (
            (self.max_consecutive_spin, rules.max_consecutive_work_days),
            (self.max_consecutive_gy_spin, rules.max_consecutive_gy),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def on_rule_source_changed(self) -> None:
        self.sync_rules_from_widgets(source_name=self.rule_settings_source, widget_source=self.rule_settings_source, save=False)
        self.rule_settings_source = self.active_rule_settings_source()
        self.populate_rule_widgets(self.rule_settings_source)
        self.refresh_validation_and_stats()

    def selected_calendar_date(self) -> date:
        qdate = self.calendar_date_edit.date()
        return date(qdate.year(), qdate.month(), qdate.day())

    def refresh_calendar_after_override(self) -> None:
        if self.result:
            self.result.holidays = korean_holidays(self.result.year)
            self.render_schedule_table()
            self.refresh_validation_and_stats()
        self.mark_year_overview_dirty()

    def add_selected_holiday(self) -> None:
        add_custom_holiday(self.selected_calendar_date())
        self.refresh_calendar_after_override()

    def remove_selected_holiday(self) -> None:
        remove_holiday(self.selected_calendar_date())
        self.refresh_calendar_after_override()

    def add_selected_family_day(self) -> None:
        add_custom_family_day(self.selected_calendar_date())
        self.refresh_calendar_after_override()

    def remove_selected_family_day(self) -> None:
        remove_family_day(self.selected_calendar_date())
        self.refresh_calendar_after_override()

    def apply_calendar_override(self, d: date, action: str) -> None:
        if not self.require_admin("휴일/페밀리데이 편집"):
            return
        if action == "add_holiday":
            add_custom_holiday(d)
        elif action == "remove_holiday":
            remove_holiday(d)
        elif action == "add_family":
            add_custom_family_day(d)
        elif action == "remove_family":
            remove_family_day(d)
        self.refresh_calendar_after_override()

    def show_date_header_menu(self, table: QTableWidget, pos) -> None:
        col = table.horizontalHeader().logicalIndexAt(pos)
        if col < 2:
            return
        result = getattr(table, "result", None)
        if not isinstance(result, ScheduleResult):
            result = self.result
        if not isinstance(result, ScheduleResult):
            return
        dates = month_dates(result.year, result.month)
        day_index = col - 2
        if day_index < 0 or day_index >= len(dates):
            return
        d = dates[day_index]
        menu = QMenu(self)
        menu.addAction(f"{d.isoformat()} 페밀리데이로 설정").triggered.connect(lambda: self.apply_calendar_override(d, "add_family"))
        menu.addAction("페밀리데이 제외").triggered.connect(lambda: self.apply_calendar_override(d, "remove_family"))
        menu.addSeparator()
        menu.addAction("휴일로 설정").triggered.connect(lambda: self.apply_calendar_override(d, "add_holiday"))
        menu.addAction("휴일 제외").triggered.connect(lambda: self.apply_calendar_override(d, "remove_holiday"))
        menu.exec(table.horizontalHeader().mapToGlobal(pos))

    def _build_ui(self) -> None:
        toolbar = QToolBar("main")
        self.addToolBar(toolbar)
        save_db_action = QAction("현재 근무표 DB 저장", self)
        save_db_action.triggered.connect(self.save_current_schedule_to_db)
        export_action = QAction("엑셀 저장", self)
        export_action.triggered.connect(self.export_excel)
        toolbar.addAction(save_db_action)
        toolbar.addAction(export_action)
        self.admin_only_actions.extend([save_db_action, export_action])

        root = QWidget()
        root_layout = QVBoxLayout(root)

        top = QHBoxLayout()
        self.role_label = QLabel("")
        self.admin_login_btn = QPushButton("관리자 로그인")
        self.admin_login_btn.clicked.connect(self.login_admin)
        self.admin_logout_btn = QPushButton("일반 모드")
        self.admin_logout_btn.clicked.connect(self.logout_admin)
        refresh_db_btn = QPushButton("새로고침")
        refresh_db_btn.clicked.connect(self.refresh_current_month_from_db)
        self.unavailable_employee_combo = QComboBox()
        self.unavailable_employee_combo.setMinimumWidth(160)
        top.addWidget(self.role_label)
        top.addWidget(self.admin_login_btn)
        top.addWidget(self.admin_logout_btn)
        top.addWidget(refresh_db_btn)
        top.addWidget(QLabel("내 이름"))
        top.addWidget(self.unavailable_employee_combo)
        top.addWidget(QLabel("연도"))
        top.addWidget(self.year_spin)
        top.addWidget(QLabel("월"))
        top.addWidget(self.month_spin)
        top.addWidget(self.schedule_source_label)
        generate_btn = QPushButton("자동 생성")
        generate_btn.clicked.connect(self.generate_schedule)
        validate_btn = QPushButton("검증/통계 갱신")
        validate_btn.clicked.connect(self.refresh_validation_and_stats)
        refresh_year_btn = QPushButton("연간 보기 갱신")
        refresh_year_btn.clicked.connect(self.render_year_overview)
        paste_btn = QPushButton("엑셀 근무표 붙여넣기")
        paste_btn.clicked.connect(self.paste_schedule_from_clipboard)
        import_excel_btn = QPushButton("엑셀 파일 근무표 불러오기")
        import_excel_btn.clicked.connect(self.import_schedule_excel)
        top.addWidget(generate_btn)
        top.addWidget(validate_btn)
        top.addWidget(refresh_year_btn)
        top.addWidget(paste_btn)
        top.addWidget(import_excel_btn)
        top.addStretch(1)
        root_layout.addLayout(top)
        self.admin_only_widgets.extend([generate_btn, paste_btn, import_excel_btn])

        splitter = QSplitter(Qt.Vertical)
        self.tabs = QTabWidget()
        tabs = self.tabs

        year_tab = QWidget()
        year_layout = QVBoxLayout(year_tab)
        year_layout.addWidget(QLabel("선택한 연도/월의 근무표입니다. PageUp/PageDown, 좌우 화살표, 또는 상단 연도/월 변경으로 다른 월을 봅니다. 표를 클릭하고 Ctrl+V 하면 해당 월에 엑셀 근무표가 바로 붙습니다."))
        self.year_scroll = QScrollArea()
        self.year_scroll.setWidgetResizable(True)
        self.year_scroll_content = QWidget()
        self.year_scroll_layout = QVBoxLayout(self.year_scroll_content)
        self.year_scroll.setWidget(self.year_scroll_content)
        year_layout.addWidget(self.year_scroll)
        self.year_tab_index = tabs.addTab(year_tab, "근무표 보기")

        self.schedule_tab_index = -1
        self.schedule_tab_container = QWidget()
        self.schedule_tab_container.hide()
        schedule_layout = QVBoxLayout(self.schedule_tab_container)
        schedule_layout.addWidget(QLabel("메인 월별 근무표입니다. 엑셀에서 성명/사번/1일~말일 표를 복사한 뒤 이 표 아무 셀에 커서를 두고 Ctrl+V 하세요. 코드: D, S, G/지근, 당직, 지휴, 빈칸"))
        schedule_layout.addWidget(self.seed_employees_btn)
        schedule_layout.addWidget(self.create_schedule_btn)
        schedule_layout.addWidget(self.regenerate_schedule_btn)
        schedule_layout.addWidget(self.schedule_table)
        schedule_layout.addWidget(self.month_split_scroll)
        if hasattr(self, "admin_only_widgets"):
            self.admin_only_widgets.append(self.seed_employees_btn)
            self.admin_only_widgets.append(self.create_schedule_btn)
            self.admin_only_widgets.append(self.regenerate_schedule_btn)

        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        period_layout = QHBoxLayout()
        period_layout.addWidget(QLabel("대상"))
        period_layout.addWidget(self.stats_source_combo)
        period_layout.addWidget(QLabel("기간"))
        period_layout.addWidget(self.stats_start_year_spin)
        period_layout.addWidget(QLabel("년"))
        period_layout.addWidget(self.stats_start_month_spin)
        period_layout.addWidget(QLabel("월 ~"))
        period_layout.addWidget(self.stats_end_year_spin)
        period_layout.addWidget(QLabel("년"))
        period_layout.addWidget(self.stats_end_month_spin)
        period_layout.addWidget(QLabel("월"))
        refresh_cum_btn = QPushButton("누적 통계 새로고침")
        refresh_cum_btn.clicked.connect(self.render_cumulative_stats)
        manage_excluded_btn = QPushButton("제외 인원 관리")
        manage_excluded_btn.clicked.connect(self.show_stats_exclusion_manager)
        self.admin_only_widgets.append(manage_excluded_btn)
        filter_btn = QPushButton("필터 설정")
        filter_btn.clicked.connect(self.show_monthly_stats_filter_dialog)
        period_layout.addWidget(refresh_cum_btn)
        period_layout.addWidget(filter_btn)
        period_layout.addWidget(manage_excluded_btn)
        period_layout.addStretch(1)
        stats_layout.addLayout(period_layout)
        stats_layout.addWidget(self.cumulative_stats_table)
        self.stats_tab_index = tabs.addTab(stats_tab, "통계")

        settings_tab = QWidget()
        settings_layout = QHBoxLayout(settings_tab)
        staffing_group = QGroupBox("날짜 유형별 최소 인원")
        staffing_layout = QVBoxLayout(staffing_group)
        rule_source_row = QHBoxLayout()
        rule_source_row.addWidget(QLabel("설정 대상"))
        self.rule_source_combo = QComboBox()
        for key, label in RULE_SETTING_OPTIONS:
            self.rule_source_combo.addItem(label, key)
        self.rule_source_combo.setCurrentIndex(0)
        self.rule_source_combo.currentIndexChanged.connect(lambda _index: self.on_rule_source_changed())
        rule_source_row.addWidget(self.rule_source_combo)
        rule_source_row.addStretch(1)
        staffing_layout.addLayout(rule_source_row)
        self.day_type_rule_table = QTableWidget(len(DAY_TYPE_ORDER), 4)
        self.day_type_rule_table.setHorizontalHeaderLabels(["구분", "Day", "SW", "GY"])
        self.day_type_rule_table.verticalHeader().setVisible(False)
        self.day_type_rule_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for row, day_type in enumerate(DAY_TYPE_ORDER):
            label_item = QTableWidgetItem(DAY_TYPE_LABELS[day_type])
            label_item.setFlags(label_item.flags() & ~Qt.ItemIsEditable)
            self.day_type_rule_table.setItem(row, 0, label_item)
            for col, shift in enumerate((SHIFT_DAY, SHIFT_SWING, SHIFT_GY), start=1):
                self.day_type_rule_table.setCellWidget(row, col, self.day_type_rule_spins[(day_type, shift)])
        self.day_type_rule_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.day_type_rule_table.verticalHeader().setDefaultSectionSize(34)
        self.day_type_rule_table.setFixedHeight(220)
        staffing_layout.addWidget(self.day_type_rule_table)
        staffing_layout.addWidget(QLabel("토요일 GY 값은 자동으로 토요일 당직 인원으로 적용됩니다."))

        rule_group = QGroupBox("제약")
        rule_form = QFormLayout(rule_group)
        rule_form.addRow("최대 연속 근무", self.max_consecutive_spin)
        rule_form.addRow("최대 연속 GY", self.max_consecutive_gy_spin)

        module_group = QGroupBox("모듈")
        module_layout = QVBoxLayout(module_group)
        self.module_list_widget = QListWidget()
        self.module_name_edit = QLineEdit()
        self.module_name_edit.setPlaceholderText("모듈 이름")
        module_button_row = QHBoxLayout()
        add_module_btn = QPushButton("추가")
        remove_module_btn = QPushButton("삭제")
        add_module_btn.clicked.connect(self.add_module_from_input)
        remove_module_btn.clicked.connect(self.remove_selected_module)
        module_button_row.addWidget(add_module_btn)
        module_button_row.addWidget(remove_module_btn)
        module_layout.addWidget(self.module_list_widget)
        module_layout.addWidget(self.module_name_edit)
        module_layout.addLayout(module_button_row)
        self.refresh_module_list()

        settings_layout.addWidget(staffing_group, 2)
        settings_layout.addWidget(rule_group)
        settings_layout.addWidget(module_group)
        settings_layout.addStretch(1)
        self.settings_tab_index = tabs.addTab(settings_tab, "근무 설정")

        splitter.addWidget(tabs)
        splitter.addWidget(self.warning_box)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([650, 160])
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.tabs.setCurrentIndex(self.year_tab_index)
        self.refresh_unavailable_employee_combo()
        self.apply_access_mode()

    def add_employee_rows(self, employees: List[Employee]) -> None:
        self.employee_table.setRowCount(0)
        for emp in employees:
            row = self.employee_table.rowCount()
            self.employee_table.insertRow(row)
            self.employee_table.setItem(row, 0, QTableWidgetItem(emp.name))
            self.employee_table.setItem(row, 1, QTableWidgetItem(emp.employee_id))
            self.employee_table.setItem(row, 2, QTableWidgetItem("Y" if emp.is_new else ""))
            unavailable = ", ".join(sorted(d.isoformat() for d in emp.unavailable_dates))
            self.employee_table.setItem(row, 3, QTableWidgetItem(unavailable))
            self.employee_table.setItem(row, 4, QTableWidgetItem(emp.module))
        self.refresh_unavailable_employee_combo()
        self.apply_access_mode()

    def sync_rules_from_widgets(
        self,
        source_name: Optional[str] = None,
        *,
        widget_source: Optional[str] = None,
        save: bool = True,
    ) -> ShiftRules:
        active_source = self.active_rule_settings_source()
        update_source = widget_source or active_source
        target_source = source_name or update_source
        rules = self.rules_for_source(update_source)
        for (day_type, shift), widget in self.day_type_rule_spins.items():
            set_rule_value_from_display(rules, day_type, shift, widget.value())
        rules.min_weekday = {
            SHIFT_DAY: self.day_type_rule_spins[(DAY_TYPE_ORDER[0], SHIFT_DAY)].value(),
            SHIFT_SWING: self.day_type_rule_spins[(DAY_TYPE_ORDER[0], SHIFT_SWING)].value(),
            SHIFT_GY: self.day_type_rule_spins[(DAY_TYPE_ORDER[0], SHIFT_GY)].value(),
        }
        rules.min_holiday = {
            SHIFT_DAY: self.day_type_rule_spins[(DAY_TYPE_ORDER[3], SHIFT_DAY)].value(),
            SHIFT_SWING: self.day_type_rule_spins[(DAY_TYPE_ORDER[3], SHIFT_SWING)].value(),
            SHIFT_DUTY: self.day_type_rule_spins[(DAY_TYPE_ORDER[3], SHIFT_GY)].value(),
        }
        rules.max_consecutive_work_days = self.max_consecutive_spin.value()
        rules.max_consecutive_gy = self.max_consecutive_gy_spin.value()
        if update_source == self.rule_settings_source:
            self.rules = rules
        if save:
            save_team_rules(self.team_rules)
        return self.rules_for_source(target_source)

    def collect_employees(self) -> List[Employee]:
        employees: List[Employee] = []
        seen = set()
        year = self.year_spin.value()
        month = self.month_spin.value()
        for row in range(self.employee_table.rowCount()):
            name_item = self.employee_table.item(row, 0)
            if not name_item or not name_item.text().strip():
                continue
            name = name_item.text().strip()
            employee_id = self._cell_text(self.employee_table, row, 1)
            is_new_text = self._cell_text(self.employee_table, row, 2).lower()
            is_new = is_new_text in ("y", "yes", "true", "1", "신규", "ㅇ", "o")
            unavailable = parse_unavailable(self._cell_text(self.employee_table, row, 3), year, month)
            module = self._cell_text(self.employee_table, row, 4)
            emp = Employee(name=name, employee_id=employee_id, is_new=is_new, unavailable_dates=unavailable, module=module)
            if emp.key in seen:
                continue
            seen.add(emp.key)
            employees.append(emp)
        return employees

    @staticmethod
    def _cell_text(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item else ""

    def _clipboard_text_html(self) -> tuple[str, str]:
        mime = QApplication.clipboard().mimeData()
        text = QApplication.clipboard().text()
        html = mime.html() if mime and mime.hasHtml() else ""
        if not html and mime:
            for fmt in mime.formats():
                if "html" not in fmt.lower():
                    continue
                raw = bytes(mime.data(fmt))
                header = raw[:300].decode("ascii", errors="ignore")
                match = re.search(r"StartHTML:(\d+).*?EndHTML:(\d+)", header, flags=re.S)
                if match:
                    start, end = int(match.group(1)), int(match.group(2))
                    if 0 <= start < end <= len(raw):
                        raw = raw[start:end]
                for encoding in ("utf-8", "utf-16", "cp949", "latin-1"):
                    try:
                        candidate = raw.decode(encoding, errors="ignore")
                    except Exception:
                        continue
                    if "<table" in candidate.lower() or "<html" in candidate.lower():
                        html = candidate
                        break
                if html:
                    break
        return text, html

    def _paste_target_month_from_focus(self) -> Optional[tuple[int, int]]:
        """Return target year/month when Ctrl+V happens inside a roster table.

        QTableWidget.keyPressEvent is not enough because Qt can route Ctrl+V to
        the cell editor/viewport depending on the current focus state. Walking
        up from the focused widget lets us intercept paste consistently.
        """
        widget = QApplication.focusWidget()
        while widget is not None:
            if isinstance(widget, MonthRosterTable):
                return widget.year, widget.month
            if isinstance(widget, CurrentMonthRosterTable):
                return self.year_spin.value(), self.month_spin.value()
            if isinstance(widget, ScheduleInputTable):
                return self.year_spin.value(), self.month_spin.value()
            widget = widget.parentWidget()
        return None

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_PageUp, Qt.Key_PageDown):
            delta = -1 if event.key() == Qt.Key_PageUp else 1
            if self.move_roster_page(delta):
                return True
        if event.type() == QEvent.KeyPress and event.matches(QKeySequence.Copy):
            table = self._focus_ancestor(CurrentMonthRosterTable)
            if table is not None:
                self.copy_table_selection_to_clipboard(table, skip_columns=2)
                return True
            table = self._focus_ancestor(MonthRosterTable)
            if table is not None:
                self.copy_table_selection_to_clipboard(table, skip_columns=2)
                return True
        if event.type() == QEvent.KeyPress and event.matches(QKeySequence.Paste):
            table = self._focus_ancestor(CurrentMonthRosterTable)
            if table is not None:
                self.paste_current_month_clipboard()
                return True
            table = self._focus_ancestor(MonthRosterTable)
            if table is not None:
                self.paste_month_table_clipboard(table)
                return True
            target = self._paste_target_month_from_focus()
            if target is not None:
                year, month = target
                self.paste_schedule_from_clipboard_for_month(year, month)
                return True
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            table = self._focus_ancestor(CurrentMonthRosterTable)
            if table is not None:
                self.clear_selected_schedule_cells(table, self.result)
                return True
            table = self._focus_ancestor(MonthRosterTable)
            if table is not None:
                self.clear_selected_schedule_cells(table, getattr(table, "result", None))
                return True
        return super().eventFilter(watched, event)

    def _focus_ancestor(self, cls):
        widget = QApplication.focusWidget()
        while widget is not None:
            if isinstance(widget, cls):
                return widget
            widget = widget.parentWidget()
        return None

    def _clipboard_matrix(self) -> List[List[str]]:
        text = QApplication.clipboard().text()
        if not text:
            return []
        return [[cell.strip() for cell in line.split("\t")] for line in text.rstrip("\n").splitlines()]

    def clipboard_looks_like_full_roster(self) -> bool:
        rows = [row for row in self._clipboard_matrix() if any(cell.strip() for cell in row)]
        if not rows:
            return False
        lowered_first = [cell.replace(" ", "").lower() for cell in rows[0]]
        if any(cell in ("성명", "이름", "name") for cell in lowered_first):
            return True
        max_cols = max(len(row) for row in rows)
        first = rows[0][0].strip() if rows[0] else ""
        if max_cols < 2 or normalize_shift_code(first) in SHIFT_OPTIONS:
            return False
        second = rows[0][1].strip() if len(rows[0]) > 1 else ""
        # 이름+사번+표시들, 또는 이름+표시들 형태면 전체 근무표로 본다.
        return max_cols >= 3 or normalize_shift_code(second) in SHIFT_OPTIONS

    def paste_schedule_from_clipboard_for_month(self, year: int, month: int, source_name: Optional[str] = None) -> None:
        if not self.require_admin("근무표 붙여넣기"):
            return
        text, html = self._clipboard_text_html()
        if not text.strip() and not html.strip():
            QMessageBox.warning(self, "붙여넣기 실패", "클립보드가 비어 있습니다. 엑셀에서 표 범위를 먼저 복사하세요.")
            return
        target_source = source_name or self.current_roster_source_name()
        rules = self.sync_rules_from_widgets(source_name=target_source)
        unavailable_map: Dict[str, set[date]] = {}
        html_rows = parse_html_table(html) if html.strip() else []
        if html_rows:
            try:
                unavailable_map = parse_unavailable_from_html_rows(html_rows, year, month)
            except Exception:
                unavailable_map = {}
        try:
            if html_rows:
                pasted = parse_schedule_from_html_rows(html_rows, year, month, rules)
            else:
                pasted = parse_schedule_from_clipboard(text, html, year, month, rules)
        except Exception as exc:
            if unavailable_map and self.apply_unavailable_map_to_current_month(year, month, unavailable_map, source_name):
                return
            QMessageBox.warning(self, "붙여넣기 실패", f"{year}년 {month}월 근무표를 읽지 못했습니다.\n{exc}")
            return

        if html_rows:
            position_unavailable_map = parse_unavailable_from_html_rows_by_position(
                html_rows,
                pasted.employees,
                year,
                month,
            )
            unavailable_map = self.merge_unavailable_maps(unavailable_map, position_unavailable_map)

        if unavailable_map:
            pasted.employees = self.merge_unavailable_into_employees(pasted.employees, unavailable_map)
            if not self.result_has_work_marks(pasted) and self.apply_unavailable_map_to_current_month(year, month, unavailable_map, source_name):
                return

        self.set_current_month(year, month)
        pasted.source_name = source_name or self.source_name_for_view(year, month)
        self.apply_split_legacy_prefix(pasted)
        self.result = pasted
        self.apply_previous_month_gy_carryover(self.result)
        self.apply_split_legacy_prefix(self.result)
        self.employees = list(pasted.employees)
        self.add_employee_rows(self.employees)
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        try:
            self.save_result_silently(self.result)
            self.mark_cumulative_stats_dirty()
        except Exception as exc:
            QMessageBox.warning(self, "자동 저장 실패", f"근무표는 반영됐지만 DB 자동 저장에 실패했습니다.\n{exc}")
        self.mark_year_overview_dirty()
        QMessageBox.information(self, "근무표 반영", f"{year}년 {month}월 표에서 {len(self.employees)}명을 인식했고 DB에 자동 저장했습니다.")

    @staticmethod
    def result_has_work_marks(result: ScheduleResult) -> bool:
        work_shifts = {SHIFT_DAY, SHIFT_SWING, SHIFT_GY, SHIFT_DUTY}
        return any(
            shift in work_shifts
            for day_map in result.schedule.values()
            for shift in day_map.values()
        )

    def current_roster_source_name(self) -> str:
        year = self.year_spin.value()
        month = self.month_spin.value()
        if self.month_has_team_dates(year, month):
            self.clamp_month_split_page(year, month)
            return TEAM_VIEWS[self.month_split_page_index]
        return self.source_name_for_view(year, month)

    def schedule_source_has_saved_data(self, year: int, month: int, source_name: str) -> bool:
        return self.load_existing_schedule_for_source(year, month, source_name) is not None

    def should_offer_schedule_generation(self, result: ScheduleResult, source_name: str) -> bool:
        if self.is_team_source(source_name):
            return (
                not self.schedule_source_has_saved_data(result.year, result.month, source_name)
                or not self.result_has_work_marks(result)
            )
        return not self.result_has_work_marks(result)

    def update_create_schedule_button(
        self,
        result: Optional[ScheduleResult],
        source_name: Optional[str] = None,
    ) -> None:
        if not hasattr(self, "create_schedule_btn"):
            return
        if result is None:
            self.create_schedule_btn.hide()
            if hasattr(self, "seed_employees_btn"):
                self.seed_employees_btn.hide()
            if hasattr(self, "regenerate_schedule_btn"):
                self.regenerate_schedule_btn.hide()
            return
        target_source = source_name or self.storage_source_name(result)
        should_show = self.should_offer_schedule_generation(result, target_source)
        should_regenerate = self.result_has_work_marks(result)
        if self.is_team_source(target_source):
            self.create_schedule_btn.setText(f"{target_source} 근무표 생성")
            if hasattr(self, "seed_employees_btn"):
                self.seed_employees_btn.setText(f"{target_source} 인원 반영")
            if hasattr(self, "regenerate_schedule_btn"):
                self.regenerate_schedule_btn.setText(f"{target_source} 근무표 재생성")
        else:
            self.create_schedule_btn.setText("근무표 생성")
            if hasattr(self, "seed_employees_btn"):
                self.seed_employees_btn.setText("인원 반영")
            if hasattr(self, "regenerate_schedule_btn"):
                self.regenerate_schedule_btn.setText("근무표 재생성")
        self.create_schedule_btn.setVisible(should_show)
        if hasattr(self, "seed_employees_btn"):
            self.seed_employees_btn.setVisible(should_show)
        if hasattr(self, "regenerate_schedule_btn"):
            self.regenerate_schedule_btn.setVisible(should_regenerate)

    def seed_employees_from_previous_schedule(self, year: int, month: int, source_name: str) -> List[Employee]:
        seed_year = year
        seed_month = month
        for _ in range(24):
            seed_year, seed_month = self.shift_month_values(seed_year, seed_month, -1)
            seed_source = (
                source_name
                if self.is_team_source(source_name) and self.month_has_team_dates(seed_year, seed_month)
                else self.legacy_source_name(seed_year, seed_month)
            )
            previous = self.load_existing_schedule_for_source(seed_year, seed_month, seed_source)
            if previous and previous.employees:
                return [
                    Employee(emp.name, emp.employee_id, emp.is_new, set(), emp.module)
                    for emp in previous.employees
                ]
        return []

    def generation_rules_message(self, source_name: Optional[str] = None) -> str:
        target_source = source_name or self.current_roster_source_name()
        rules = self.sync_rules_from_widgets(source_name=target_source)
        rule_lines = []
        for day_type in DAY_TYPE_ORDER:
            day_rules = rules.min_by_day_type.get(day_type, {})
            gy_shift = SHIFT_DUTY if day_type == DAY_TYPE_ORDER[3] else SHIFT_GY
            gy_label = "당직" if day_type == DAY_TYPE_ORDER[3] else "GY"
            rule_lines.append(
                f"- {DAY_TYPE_LABELS[day_type]}: Day {day_rules.get(SHIFT_DAY, 0)}명, "
                f"SW {day_rules.get(SHIFT_SWING, 0)}명, {gy_label} {day_rules.get(gy_shift, 0)}명"
            )
        return (
            f"아래 규칙으로 {target_source} 근무표를 자동 생성합니다.\n\n"
            f"{chr(10).join(rule_lines)}\n"
            f"- 최대 연속 근무: {rules.max_consecutive_work_days}일\n"
            f"- 최대 연속 G/당직: {rules.max_consecutive_gy}일\n"
            "- 직원별 불가일은 근무 배정에서 제외합니다.\n"
            f"- 직원별 근무 가능일을 기준으로 주 {rules.min_weekly_work_days}회 이상 배정을 우선합니다.\n"
            "- 전월 말 당직 이월 규칙을 반영합니다.\n\n"
            "확인을 누르면 바로 생성하고 DB에 자동 저장합니다."
        )

    def confirm_generation_rules(self, source_name: Optional[str] = None) -> bool:
        answer = QMessageBox.question(
            self,
            "근무표 생성 규칙",
            self.generation_rules_message(source_name),
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Ok,
        )
        return answer == QMessageBox.Ok

    def create_missing_schedule_for_current_page(self) -> None:
        source_name = self.current_roster_source_name()
        self.generate_schedule(source_name=source_name)

    def regenerate_schedule_for_current_page(self) -> None:
        source_name = self.current_roster_source_name()
        self.regenerate_schedule_for_month_source(self.year_spin.value(), self.month_spin.value(), source_name)

    def seed_employees_for_current_page(self) -> None:
        source_name = self.current_roster_source_name()
        self.create_empty_roster_from_previous(self.year_spin.value(), self.month_spin.value(), source_name)

    def create_schedule_for_month_source(self, year: int, month: int, source_name: str) -> None:
        view = source_name if self.is_team_source(source_name) else None
        self.set_current_month(year, month)
        self.result = self.load_schedule_for_view(year, month, view)
        self.apply_previous_month_gy_carryover(self.result)
        self.apply_split_legacy_prefix(self.result)
        self.employees = list(self.result.employees)
        self.add_employee_rows(self.employees)
        self.generate_schedule(source_name=source_name)

    def regenerate_schedule_for_month_source(self, year: int, month: int, source_name: str) -> None:
        if not self.require_admin("근무표 재생성"):
            return
        label = self.source_label_for_view(year, month, source_name if self.is_team_source(source_name) else None)
        answer = QMessageBox.question(
            self,
            "근무표 재생성",
            f"{year}년 {month}월 {label} 기존 근무표를 다시 생성할까요?\n현재 근무 배정은 새 자동 생성 결과로 덮어씁니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        view = source_name if self.is_team_source(source_name) else None
        self.set_current_month(year, month)
        self.result = self.load_schedule_for_view(year, month, view)
        self.apply_previous_month_gy_carryover(self.result)
        self.apply_split_legacy_prefix(self.result)
        self.employees = list(self.result.employees)
        self.add_employee_rows(self.employees)
        self.generate_schedule(source_name=source_name)

    def make_schedule_generate_button(self, year: int, month: int, source_name: str) -> QPushButton:
        button = QPushButton("근무표 생성")
        button.setFixedWidth(105)
        button.setEnabled(getattr(self, "is_admin", True))
        button.clicked.connect(
            lambda _checked=False, y=year, m=month, s=source_name: self.create_schedule_for_month_source(y, m, s)
        )
        return button

    def make_schedule_regenerate_button(self, year: int, month: int, source_name: str) -> QPushButton:
        button = QPushButton("근무표 재생성")
        button.setFixedWidth(115)
        button.setEnabled(getattr(self, "is_admin", True))
        button.clicked.connect(
            lambda _checked=False, y=year, m=month, s=source_name: self.regenerate_schedule_for_month_source(y, m, s)
        )
        return button

    def make_seed_employees_button(self, year: int, month: int, source_name: str) -> QPushButton:
        button = QPushButton("인원 반영")
        button.setFixedWidth(90)
        button.setEnabled(getattr(self, "is_admin", True))
        button.clicked.connect(
            lambda _checked=False, y=year, m=month, s=source_name: self.create_empty_roster_from_previous(y, m, s)
        )
        return button

    def create_empty_roster_from_previous(self, year: int, month: int, source_name: str) -> None:
        if not self.require_admin("인원 반영"):
            return
        current = self.load_schedule_for_view(year, month, source_name if self.is_team_source(source_name) else None)
        seeded = self.seed_employees_from_previous_schedule(year, month, source_name)
        if not seeded:
            QMessageBox.warning(self, "인원 반영 실패", "이전 달에서 가져올 인원이 없습니다.")
            return
        unavailable_by_key = {emp.key: set(emp.unavailable_dates) for emp in current.employees}
        employees = [
            Employee(emp.name, emp.employee_id, emp.is_new, unavailable_by_key.get(emp.key, set()), emp.module)
            for emp in seeded
        ]
        dates = month_dates(year, month)
        empty = ScheduleResult(
            year,
            month,
            employees,
            {d: {emp.key: OFF for emp in employees} for d in dates},
            korean_holidays(year),
            source_name=source_name,
        )
        self.apply_split_legacy_prefix(empty)
        save_schedule(empty, source_name)
        save_unavailable_days(empty.employees, source_name, year, month)
        self.result = empty
        self.set_current_month(year, month)
        self.employees = list(empty.employees)
        self.add_employee_rows(self.employees)
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        self.mark_cumulative_stats_dirty()
        self.mark_year_overview_dirty()
        QMessageBox.information(self, "인원 반영 완료", f"{year}년 {month}월 {source_name} 표에 이전달 인원 {len(employees)}명을 반영했습니다.")

    @staticmethod
    def merge_unavailable_into_employees(
        employees: List[Employee],
        unavailable_map: Dict[str, set[date]],
    ) -> List[Employee]:
        updated: List[Employee] = []
        for emp in employees:
            dates = unavailable_map.get(emp.key) or unavailable_map.get(emp.employee_id) or unavailable_map.get(emp.name) or set()
            if dates:
                updated.append(Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates) | set(dates), emp.module))
            else:
                updated.append(emp)
        return updated

    @staticmethod
    def merge_unavailable_maps(
        primary: Dict[str, set[date]],
        fallback: Dict[str, set[date]],
    ) -> Dict[str, set[date]]:
        merged: Dict[str, set[date]] = {key: set(dates) for key, dates in primary.items()}
        for key, dates in fallback.items():
            merged.setdefault(key, set()).update(dates)
        return merged

    def apply_unavailable_map_to_current_month(
        self,
        year: int,
        month: int,
        unavailable_map: Dict[str, set[date]],
        source_name: Optional[str] = None,
    ) -> bool:
        target_source = source_name or self.source_name_for_view(year, month)
        has_saved_schedule = self.schedule_source_has_saved_data(year, month, target_source)
        if self.result and self.result.year == year and self.result.month == month and self.storage_source_name(self.result) == target_source:
            target = self.result
        else:
            target = self.load_schedule_for_view(year, month, target_source if self.is_team_source(target_source) else None)
        if not target:
            return False
        updated = self.merge_unavailable_into_employees(target.employees, unavailable_map)
        hit = sum(
            len(unavailable_map.get(emp.key) or unavailable_map.get(emp.employee_id) or unavailable_map.get(emp.name) or set())
            for emp in target.employees
        )
        if hit <= 0:
            return False
        target.employees = updated
        if not has_saved_schedule or not self.result_has_work_marks(target):
            target.schedule = {
                d: {emp.key: OFF for emp in updated}
                for d in month_dates(year, month)
            }
            save_schedule(target, target_source)
        self.result = target
        self.set_current_month(year, month)
        self.employees = list(updated)
        self.add_employee_rows(self.employees)
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        save_unavailable_days(self.employees, self.storage_source_name(target), target.year, target.month)
        QMessageBox.information(self, "불가일 반영", f"{year}년 {month}월 회색 셀 불가일 {hit}건을 반영했습니다.")
        return True

    def apply_gray_cells_by_position(
        self,
        html_rows: List[List[dict]],
        result: ScheduleResult,
        table: Optional[QTableWidget] = None,
    ) -> bool:
        gray_offsets = gray_cell_offsets_from_html_rows(html_rows)
        if not gray_offsets:
            return False

        target_table = table
        if target_table is None and not self.schedule_table.isHidden():
            target_table = self.schedule_table
        selected_key = self.selected_unavailable_employee_key()
        employee_index_by_key = {emp.key: idx for idx, emp in enumerate(result.employees)}
        if selected_key:
            start_row = employee_index_by_key.get(selected_key, -1)
        elif target_table is not None:
            start_row = target_table.currentRow()
        else:
            start_row = -1
        start_col = target_table.currentColumn() if target_table is not None else 2
        start_col = max(2, start_col)
        if start_row < 0:
            return False

        dates = month_dates(result.year, result.month)
        updated = list(result.employees)
        hit = 0
        for row_offset, col_offset in gray_offsets:
            row = start_row if selected_key else start_row + row_offset
            day_index = start_col - 2 + col_offset
            if row < 0 or row >= len(updated) or day_index < 0 or day_index >= len(dates):
                continue
            emp = updated[row]
            if selected_key and emp.key != selected_key:
                continue
            d = dates[day_index]
            if self.is_locked_split_cell(result, d):
                continue
            if d not in emp.unavailable_dates:
                updated[row] = Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates) | {d}, emp.module)
                hit += 1
        if hit <= 0:
            return False

        result.employees = updated
        self.employees = list(updated)
        self.add_employee_rows(self.employees)
        save_unavailable_days(self.employees, self.storage_source_name(result), result.year, result.month)
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        self.mark_year_overview_dirty()
        QMessageBox.information(self, "遺덇???諛섏쁺", f"?뚯깋 ? 遺덇???{hit}嫄댁쓣 諛섏쁺?덉뒿?덈떎.")
        return True

    def paste_schedule_from_clipboard(self) -> None:
        if not self.require_admin("근무표 붙여넣기"):
            return
        if self.month_has_team_dates(self.year_spin.value(), self.month_spin.value()):
            QMessageBox.information(self, "붙여넣기 대상 선택", "V11 또는 V12 표 안을 클릭한 뒤 Ctrl+V로 붙여넣으세요.")
            return
        self.paste_schedule_from_clipboard_for_month(self.year_spin.value(), self.month_spin.value())

    def apply_imported_schedule(self, result: ScheduleResult, source_label: str) -> None:
        self.set_current_month(result.year, result.month)
        result.source_name = self.source_name_for_view(result.year, result.month)
        self.apply_split_legacy_prefix(result)
        self.result = result
        self.apply_previous_month_gy_carryover(self.result)
        self.apply_split_legacy_prefix(self.result)
        self.employees = list(result.employees)
        self.add_employee_rows(self.employees)
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        try:
            self.save_result_silently(self.result)
            self.mark_cumulative_stats_dirty()
        except Exception as exc:
            QMessageBox.warning(self, "자동 저장 실패", f"근무표는 반영됐지만 DB 자동 저장에 실패했습니다.\n{exc}")
        self.mark_year_overview_dirty()
        QMessageBox.information(self, "근무표 반영", f"{source_label}에서 {len(self.employees)}명을 인식했고 DB에 자동 저장했습니다.")

    def import_schedule_excel(self) -> None:
        if not self.require_admin("엑셀 근무표 불러오기"):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "근무표 엑셀 파일 선택",
            "",
            "Excel Files (*.xlsx *.xlsm)",
        )
        if not path:
            return
        target_source = self.current_roster_source_name()
        rules = self.sync_rules_from_widgets(source_name=target_source)
        try:
            imported = import_schedule_from_excel(path, self.year_spin.value(), self.month_spin.value(), rules)
        except Exception as exc:
            QMessageBox.warning(self, "엑셀 불러오기 실패", str(exc))
            return
        self.apply_imported_schedule(imported, Path(path).name)

    def paste_current_month_clipboard(self) -> None:
        if not self.is_admin:
            self.paste_unavailable_from_clipboard()
            return
        if self.clipboard_looks_like_full_roster():
            self.paste_schedule_from_clipboard()
            return
        text, html = self._clipboard_text_html()
        html_rows = parse_html_table(html) if html.strip() else []
        if self.result and html_rows and self.apply_gray_cells_by_position(html_rows, self.result, self.schedule_table):
            return
        self.paste_schedule_cells_from_clipboard()

    def paste_month_table_clipboard(self, table: MonthRosterTable) -> None:
        if not self.is_admin:
            self.paste_unavailable_from_clipboard()
            return
        if self.clipboard_looks_like_full_roster():
            result = getattr(table, "result", None)
            source_name = result.source_name if isinstance(result, ScheduleResult) else None
            self.paste_schedule_from_clipboard_for_month(table.year, table.month, source_name)
            return
        text, html = self._clipboard_text_html()
        html_rows = parse_html_table(html) if html.strip() else []
        result = getattr(table, "result", None)
        if isinstance(result, ScheduleResult) and html_rows and self.apply_gray_cells_by_position(html_rows, result, table):
            return
        if not isinstance(result, ScheduleResult):
            return
        self.paste_cells_into_table(table, result)

    def paste_cells_into_table(self, table: QTableWidget, result: ScheduleResult) -> None:
        if not self.require_admin("근무 코드 붙여넣기"):
            return
        matrix = self._clipboard_matrix()
        if not matrix:
            return
        start_row = max(0, table.currentRow())
        start_col = max(2, table.currentColumn())
        dates = month_dates(result.year, result.month)
        table.blockSignals(True)
        for r_offset, values in enumerate(matrix):
            row = start_row + r_offset
            if row >= table.rowCount() or row >= len(result.employees):
                continue
            emp = result.employees[row]
            for c_offset, raw in enumerate(values):
                col = start_col + c_offset
                if col < 2 or col >= table.columnCount():
                    continue
                day_index = col - 2
                d = dates[day_index] if 0 <= day_index < len(dates) else None
                if d is None or self.is_locked_split_cell(result, d):
                    continue
                shift = normalize_shift_code(raw)
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, col, item)
                item.setText("" if shift == OFF else shift)
                item.setTextAlignment(Qt.AlignCenter)
                self.apply_schedule_cell_background(item, result, emp, d, shift)
        table.blockSignals(False)
        self.sync_result_from_table(table, result)
        self.save_result_silently(result)
        if (
            self.result
            and self.result.year == result.year
            and self.result.month == result.month
            and self.storage_source_name(self.result) == self.storage_source_name(result)
        ):
            self.result = result
            self.employees = list(result.employees)
            self.render_schedule_table()
            self.refresh_validation_and_stats()
        self.mark_cumulative_stats_dirty()
        self.mark_year_overview_dirty()

    def clear_selected_schedule_cells(self, table: QTableWidget, result: Optional[ScheduleResult]) -> None:
        if not self.require_admin("근무 코드 삭제"):
            return
        if not isinstance(result, ScheduleResult):
            return
        selected = sorted(
            {(index.row(), index.column()) for index in table.selectedIndexes() if index.column() >= 2}
        )
        if not selected:
            row = table.currentRow()
            col = table.currentColumn()
            if row >= 0 and col >= 2:
                selected = [(row, col)]
        if not selected:
            return

        dates = month_dates(result.year, result.month)
        table.blockSignals(True)
        for row, col in selected:
            if row < 0 or row >= len(result.employees):
                continue
            day_index = col - 2
            if day_index < 0 or day_index >= len(dates):
                continue
            emp = result.employees[row]
            d = dates[day_index]
            if self.is_locked_split_cell(result, d):
                continue
            result.schedule.setdefault(d, {})[emp.key] = OFF
            item = table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                table.setItem(row, col, item)
            item.setText("")
            item.setTextAlignment(Qt.AlignCenter)
            self.apply_schedule_cell_background(item, result, emp, d, OFF)
        table.blockSignals(False)

        self.save_result_silently(result)
        if self.result and self.result.year == result.year and self.result.month == result.month:
            self.result = result
            self.employees = list(result.employees)
            self.render_schedule_table()
            self.refresh_validation_and_stats()
        self.mark_cumulative_stats_dirty()
        self.mark_year_overview_dirty()

    def schedule_context_cells(self, table: QTableWidget, pos) -> List[tuple[int, int]]:
        index = table.indexAt(pos)
        if not index.isValid() or index.column() < 2:
            return []
        clicked = (index.row(), index.column())
        selected = sorted(
            {(idx.row(), idx.column()) for idx in table.selectedIndexes() if idx.column() >= 2}
        )
        if clicked not in selected:
            table.clearSelection()
            table.setCurrentCell(index.row(), index.column())
            item = table.item(index.row(), index.column())
            if item is not None:
                item.setSelected(True)
            return [clicked]
        return selected or [clicked]

    def schedule_context_rows(self, table: QTableWidget, pos) -> List[int]:
        index = table.indexAt(pos)
        if not index.isValid():
            return []
        clicked_row = index.row()
        selected_rows = sorted({idx.row() for idx in table.selectedIndexes() if idx.row() >= 0})
        if clicked_row not in selected_rows:
            return [clicked_row]
        return selected_rows or [clicked_row]

    def add_schedule_module_menu(self, menu: QMenu, result: ScheduleResult, rows: List[int]) -> None:
        module_menu = menu.addMenu("모듈 설정")
        for module_name in self.module_names:
            module_menu.addAction(module_name).triggered.connect(
                lambda _checked=False, r=rows, m=module_name: self.apply_module_to_schedule_rows(result, r, m)
            )
        if self.module_names:
            module_menu.addSeparator()
        module_menu.addAction("모듈 없음").triggered.connect(
            lambda _checked=False, r=rows: self.apply_module_to_schedule_rows(result, r, "")
        )

    def delete_schedule_rows(self, result: ScheduleResult, rows: List[int]) -> None:
        if not self.require_admin("인원 삭제"):
            return
        valid_rows = sorted({row for row in rows if 0 <= row < len(result.employees)})
        if not valid_rows:
            return
        labels = [
            result.employees[row].name or result.employees[row].employee_id
            for row in valid_rows
        ]
        answer = QMessageBox.question(
            self,
            "인원 삭제",
            f"선택한 {len(valid_rows)}명을 이 근무표에서 삭제할까요?\n{', '.join(labels[:5])}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        removed_keys = {result.employees[row].key for row in valid_rows}
        result.employees = [
            emp for index, emp in enumerate(result.employees)
            if index not in valid_rows
        ]
        for day_map in result.schedule.values():
            for key in removed_keys:
                day_map.pop(key, None)
        if self.result and result.year == self.result.year and result.month == self.result.month and self.storage_source_name(self.result) == self.storage_source_name(result):
            self.result = result
            self.employees = list(result.employees)
            self.add_employee_rows(self.employees)
            self.render_schedule_table()
            self.refresh_validation_and_stats()
        self.save_result_silently(result)
        self.mark_cumulative_stats_dirty()
        self.mark_year_overview_dirty()
        self.statusBar().showMessage(f"{len(valid_rows)}명 삭제 완료", 4000)

    def show_schedule_cell_menu(self, table: QTableWidget, result: Optional[ScheduleResult], pos) -> None:
        if not isinstance(result, ScheduleResult):
            return
        cells = self.schedule_context_cells(table, pos)
        rows = self.schedule_context_rows(table, pos)
        if not cells and not rows:
            return
        menu = QMenu(self)
        if cells:
            menu.addAction("데이 (D)").triggered.connect(
                lambda _checked=False, c=cells: self.apply_schedule_cells_choice(table, result, c, SHIFT_DAY)
            )
            menu.addAction("스윙 (S)").triggered.connect(
                lambda _checked=False, c=cells: self.apply_schedule_cells_choice(table, result, c, SHIFT_SWING)
            )
            menu.addAction("GY/지근").triggered.connect(
                lambda _checked=False, c=cells: self.apply_schedule_cells_choice(table, result, c, SHIFT_GY)
            )
            menu.addAction("당직").triggered.connect(
                lambda _checked=False, c=cells: self.apply_schedule_cells_choice(table, result, c, SHIFT_DUTY)
            )
            menu.addSeparator()
            menu.addAction("근무불가").triggered.connect(
                lambda _checked=False, c=cells: self.apply_schedule_cells_choice(table, result, c, None, mark_unavailable=True)
            )
            menu.addAction("비우기").triggered.connect(
                lambda _checked=False, c=cells: self.apply_schedule_cells_choice(table, result, c, OFF)
            )
            menu.addSeparator()
        if rows:
            self.add_schedule_module_menu(menu, result, rows)
            menu.addSeparator()
            menu.addAction("인원 삭제").triggered.connect(
                lambda _checked=False, r=rows: self.delete_schedule_rows(result, r)
            )
        menu.exec(table.viewport().mapToGlobal(pos))

    def apply_schedule_cells_choice(
        self,
        table: QTableWidget,
        result: ScheduleResult,
        cells: List[tuple[int, int]],
        shift: Optional[str],
        *,
        mark_unavailable: bool = False,
    ) -> None:
        if hasattr(self, "require_admin") and not self.require_admin("근무표 셀 변경"):
            return
        dates = month_dates(result.year, result.month)
        changed = False
        table.blockSignals(True)
        try:
            for row, col in cells:
                if row < 0 or row >= len(result.employees):
                    continue
                day_index = col - 2
                if day_index < 0 or day_index >= len(dates):
                    continue
                emp = result.employees[row]
                d = dates[day_index]
                if self.is_locked_split_cell(result, d):
                    continue
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, col, item)
                if mark_unavailable:
                    emp.unavailable_dates.add(d)
                    normalized = OFF
                else:
                    emp.unavailable_dates.discard(d)
                    normalized = normalize_shift_code(shift or OFF)
                result.schedule.setdefault(d, {})[emp.key] = normalized
                item.setText("" if normalized == OFF else normalized)
                item.setTextAlignment(Qt.AlignCenter)
                self.apply_schedule_cell_background(item, result, emp, d, normalized, include_validation=True)
                changed = True
        finally:
            table.blockSignals(False)
        if not changed:
            return
        self.save_result_silently(result)
        if self.result and self.result.year == result.year and self.result.month == result.month:
            self.result = result
            self.employees = list(result.employees)
            self.render_schedule_table()
            self.refresh_validation_and_stats()
        self.mark_cumulative_stats_dirty()
        self.mark_year_overview_dirty()

    def save_result_silently(self, result: ScheduleResult) -> None:
        self.save_result_to_db(result)

    def update_next_month_gy_carryover(self, result: ScheduleResult) -> None:
        next_year = result.year if result.month < 12 else result.year + 1
        next_month = result.month + 1 if result.month < 12 else 1
        source_name = self.storage_source_name(result)
        next_source = source_name if self.is_team_source(source_name) and self.month_has_team_dates(next_year, next_month) else self.legacy_source_name(next_year, next_month)
        next_result = self.load_existing_schedule_for_source(next_year, next_month, next_source)
        if not next_result:
            return
        before = {
            d: dict(next_result.schedule.get(d, {}))
            for d in month_dates(next_year, next_month)[:6]
        }
        self.apply_previous_month_gy_carryover(next_result)
        after = {
            d: dict(next_result.schedule.get(d, {}))
            for d in month_dates(next_year, next_month)[:6]
        }
        if before != after:
            stored = self.result_for_storage(next_result, self.storage_source_name(next_result))
            save_schedule(stored, stored.source_name)

    def copy_schedule_selection_to_clipboard(self) -> None:
        self.copy_table_selection_to_clipboard(self.schedule_table, skip_columns=2)

    @staticmethod
    def table_item_background_hex(item: Optional[QTableWidgetItem]) -> str:
        if item is None:
            return "#ffffff"
        brush = item.background()
        if brush.style() == Qt.NoBrush:
            return "#ffffff"
        color = brush.color()
        return color.name() if color.isValid() else "#ffffff"

    def copy_table_selection_to_clipboard(self, table: QTableWidget, skip_columns: int = 0) -> None:
        indexes = [
            index for index in table.selectedIndexes()
            if index.row() >= 0 and index.column() >= skip_columns
        ]
        if not indexes:
            item = table.currentItem()
            text = item.text() if item else ""
            color = self.table_item_background_hex(item)
            mime = QMimeData()
            mime.setText(text)
            mime.setHtml(
                f'<html><body><table><tr><td style="background-color:{color}">'
                f"{escape(text)}</td></tr></table></body></html>"
            )
            QApplication.clipboard().setMimeData(mime)
            return
        top = min(index.row() for index in indexes)
        bottom = max(index.row() for index in indexes)
        left = min(index.column() for index in indexes)
        right = max(index.column() for index in indexes)
        selected_cells = {(index.row(), index.column()) for index in indexes}
        lines = []
        html_rows = []
        for row in range(top, bottom + 1):
            values = []
            html_cells = []
            for col in range(left, right + 1):
                if (row, col) in selected_cells:
                    item = table.item(row, col)
                    text = item.text() if item else ""
                    color = self.table_item_background_hex(item)
                else:
                    text = ""
                    color = "#ffffff"
                values.append(text)
                html_cells.append(
                    f'<td style="background-color:{color};text-align:center">{escape(text)}</td>'
                )
            lines.append("\t".join(values))
            html_rows.append("<tr>" + "".join(html_cells) + "</tr>")
        mime = QMimeData()
        mime.setText("\n".join(lines))
        mime.setHtml("<html><body><table>" + "".join(html_rows) + "</table></body></html>")
        QApplication.clipboard().setMimeData(mime)

    def paste_schedule_cells_from_clipboard(self) -> None:
        if not self.require_admin("근무 코드 붙여넣기"):
            return
        if not self.result:
            return
        matrix = self._clipboard_matrix()
        if not matrix:
            return
        start_row = max(0, self.schedule_table.currentRow())
        start_col = max(2, self.schedule_table.currentColumn())
        dates = month_dates(self.result.year, self.result.month)
        self._updating_table = True
        for r_offset, values in enumerate(matrix):
            row = start_row + r_offset
            if row >= self.schedule_table.rowCount() or row >= len(self.result.employees):
                continue
            emp = self.result.employees[row]
            for c_offset, raw in enumerate(values):
                # Preserve blank cells in copied ranges, but ignore whitespace-only
                # fragments produced by some clipboard formats.
                if raw == "" and len(values) == 1 and len(matrix) == 1:
                    shift = OFF
                else:
                    shift = normalize_shift_code(raw)
                col = start_col + c_offset
                if col < 2 or col >= self.schedule_table.columnCount():
                    continue
                day_index = col - 2
                d = dates[day_index] if 0 <= day_index < len(dates) else None
                if d is None or self.is_locked_split_cell(self.result, d):
                    continue
                item = self.schedule_table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    self.schedule_table.setItem(row, col, item)
                item.setText("" if shift == OFF else shift)
                item.setTextAlignment(Qt.AlignCenter)
                self.apply_schedule_cell_background(item, self.result, emp, d, shift)
        self._updating_table = False
        self.sync_schedule_from_table()
        self.render_schedule_table()
        self.refresh_validation_and_stats()

    def paste_unavailable_from_clipboard(self) -> None:
        selected_key = self.selected_unavailable_employee_key()
        if not self.is_admin and not selected_key:
            QMessageBox.warning(self, "이름 선택 필요", "상단의 내 이름을 먼저 선택하세요.")
            return
        text, html = self._clipboard_text_html()
        if not html.strip():
            QMessageBox.warning(self, "붙여넣기 실패", "회색 셀은 텍스트만으로 인식할 수 없습니다. 엑셀에서 표 범위를 복사한 뒤 근무표에 붙여넣으세요.")
            return
        html_rows = parse_html_table(html)
        if self.result:
            self.employees = self.result.employees
        else:
            self.employees = self.collect_employees()
        if not self.employees:
            QMessageBox.warning(self, "불가일 반영 실패", "먼저 근무표를 붙여넣어 직원 목록을 만든 뒤 회색 불가일을 반영하세요.")
            return
        try:
            unavailable_map = parse_unavailable_from_html_rows(html_rows, self.year_spin.value(), self.month_spin.value())
        except Exception as exc:
            QMessageBox.warning(self, "불가일 반영 실패", str(exc))
            return
        hit = 0
        updated = []
        selected_employee: Optional[Employee] = None
        for emp in self.employees:
            if not self.is_admin and emp.key != selected_key:
                updated.append(emp)
                continue
            dates = unavailable_map.get(emp.key) or unavailable_map.get(emp.employee_id) or unavailable_map.get(emp.name) or set()
            if dates:
                hit += len(dates)
                new_emp = Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates) | set(dates), emp.module)
                updated.append(new_emp)
                if emp.key == selected_key:
                    selected_employee = new_emp
            else:
                updated.append(emp)
                if emp.key == selected_key:
                    selected_employee = emp
        if hit <= 0:
            if self.result and self.apply_gray_cells_by_position(html_rows, self.result):
                return
            QMessageBox.warning(self, "불가일 없음", "선택한 사람에게 반영할 회색 불가일을 찾지 못했습니다.")
            return
        self.employees = updated
        if self.result:
            self.result.employees = self.employees
            source_name = self.storage_source_name(self.result)
            try:
                if self.is_admin:
                    save_unavailable_days(self.employees, source_name, self.result.year, self.result.month)
                elif selected_employee:
                    replace_employee_unavailable_days(selected_employee, self.result.year, self.result.month, source_name)
            except Exception as exc:
                QMessageBox.critical(self, "불가일 저장 실패", str(exc))
                return
            self.render_schedule_table()
            self.refresh_validation_and_stats()
            self.mark_year_overview_dirty()
        self.add_employee_rows(self.employees)
        QMessageBox.information(self, "불가일 반영", f"회색 셀 불가일 {hit}건을 반영했습니다.")

    def apply_pasted_employees(self) -> None:
        if not self.require_admin("직원/근무표 붙여넣기"):
            return
        text = self.paste_box.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, "붙여넣기 실패", "붙여넣은 표가 비어 있습니다.")
            return
        target_source = self.current_roster_source_name()
        rules = self.sync_rules_from_widgets(source_name=target_source)
        try:
            self.result = parse_schedule_from_tsv(text, self.year_spin.value(), self.month_spin.value(), rules)
        except Exception as exc:
            # Fallback: old employee-list-only paste format.
            employees = parse_employees_from_tsv(text)
            if not employees:
                QMessageBox.warning(self, "붙여넣기 실패", f"근무표를 읽지 못했습니다.\n{exc}")
                return
            self.add_employee_rows(employees)
            self.paste_box.clear()
            return
        self.result.source_name = self.source_name_for_view(self.result.year, self.result.month)
        self.apply_split_legacy_prefix(self.result)
        self.employees = self.result.employees
        self.add_employee_rows(self.employees)
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        self.paste_box.clear()

    def save_current_schedule_to_db(self) -> None:
        if not self.require_admin("근무표 DB 저장"):
            return
        if not self.result:
            QMessageBox.warning(self, "DB 저장 불가", "먼저 기존 근무표를 붙여넣거나 자동 생성하세요.")
            return
        self.sync_schedule_from_table()
        self.refresh_validation_and_stats()
        try:
            schedule_id = self.save_result_to_db(self.result)
        except Exception as exc:
            QMessageBox.critical(self, "DB 저장 실패", str(exc))
            return
        self.mark_cumulative_stats_dirty()
        self.mark_year_overview_dirty()
        QMessageBox.information(self, "DB 저장 완료", f"근무표를 DB에 저장했습니다. ID: {schedule_id}")

    def export_excel(self) -> None:
        if not self.require_admin("엑셀 저장"):
            return
        if not self.result:
            QMessageBox.warning(self, "저장 불가", "먼저 근무표를 자동 생성하세요.")
            return
        self.sync_schedule_from_table()
        self.refresh_validation_and_stats()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "근무표 엑셀 저장",
            f"근무표_{self.result.year}_{self.result.month:02d}.xlsx",
            "Excel Files (*.xlsx)",
        )
        if not path:
            return
        try:
            export_schedule_to_excel(self.result, path)
        except Exception as exc:
            QMessageBox.critical(self, "저장 실패", str(exc))
            return
        QMessageBox.information(self, "저장 완료", path)

    def generate_schedule(
        self,
        _checked: bool = False,
        *,
        source_name: Optional[str] = None,
        confirm_rules: bool = True,
    ) -> None:
        if hasattr(self, "require_admin") and not self.require_admin("근무표 자동 생성"):
            return
        year = self.year_spin.value()
        month = self.month_spin.value()
        target_source = source_name or self.current_roster_source_name()
        if confirm_rules and not self.confirm_generation_rules(target_source):
            return
        rules = self.sync_rules_from_widgets(source_name=target_source)
        self.employees = self.collect_employees()
        if not self.employees and self.result:
            self.employees = list(self.result.employees)
        if not self.employees:
            self.employees = self.seed_employees_from_previous_schedule(year, month, target_source)
            if self.employees:
                self.add_employee_rows(self.employees)
        if len(self.employees) < 3:
            QMessageBox.warning(self, "생성 불가", "D/S/G 최소 인원을 채우려면 직원이 최소 3명 필요합니다.")
            return
        initial_result = ScheduleResult(
            year,
            month,
            list(self.employees),
            {d: {emp.key: OFF for emp in self.employees} for d in month_dates(year, month)},
            korean_holidays(year),
            source_name=target_source,
        )
        initial_schedule = self.previous_month_gy_initial_schedule(initial_result)
        try:
            self.result = generate_month_schedule(
                self.employees,
                year,
                month,
                rules,
                previous_day_duty_employee_keys=self.previous_month_last_duty_keys(
                    year,
                    month,
                    target_source,
                ),
                initial_schedule=initial_schedule,
            )
        except ScheduleError as exc:
            QMessageBox.warning(self, "생성 실패", str(exc))
            return
        self.result.source_name = target_source
        self.apply_split_legacy_prefix(self.result)
        try:
            self.save_result_silently(self.result)
            self.mark_cumulative_stats_dirty()
        except Exception as exc:
            QMessageBox.warning(self, "자동 저장 실패", f"근무표는 생성됐지만 DB 자동 저장에 실패했습니다.\n{exc}")
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        self.mark_year_overview_dirty()

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout(child_layout)  # type: ignore[arg-type]

    def configure_roster_table_layout(self, table: QTableWidget, date_count: int, row_count: int, *, overview: bool = False) -> None:
        """Make roster tables compact and avoid horizontal scrolling."""
        table.setWordWrap(False)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff if overview else Qt.ScrollBarAsNeeded)
        table.setAlternatingRowColors(False)
        table.setShowGrid(True)

        font = table.font()
        font.setPointSize(COMPACT_FONT_SIZE)
        table.setFont(font)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setDefaultSectionSize(DAY_COL_WIDTH)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(20)
        header_font = header.font()
        header_font.setPointSize(HEADER_FONT_SIZE)
        header.setFont(header_font)
        header.setFixedHeight(34)

        table.setColumnWidth(0, NAME_COL_WIDTH)
        table.setColumnWidth(1, ID_COL_WIDTH)
        for col in range(2, date_count + 2):
            table.setColumnWidth(col, DAY_COL_WIDTH)
        for row in range(table.rowCount()):
            table.setRowHeight(row, COMPACT_ROW_HEIGHT)

        if overview:
            table.setMinimumWidth(NAME_COL_WIDTH + ID_COL_WIDTH + date_count * DAY_COL_WIDTH + 8)
            table.setFixedHeight(36 + max(1, row_count) * COMPACT_ROW_HEIGHT)

    def _make_schedule_view_table(self, result: ScheduleResult) -> QTableWidget:
        dates = month_dates(result.year, result.month)
        row_count = max(1, len(result.employees))
        table = MonthRosterTable(self, result.year, result.month, row_count, len(dates) + 2)
        table.result = result
        table.setItemDelegate(ShiftComboDelegate(table))
        table.setEditTriggers(self.current_edit_triggers())
        table.setHorizontalHeaderLabels(["성명", "사번"] + [str(d.day) for d in dates])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        table.horizontalHeader().customContextMenuRequested.connect(
            lambda pos, t=table: self.show_date_header_menu(t, pos)
        )
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, t=table: self.show_schedule_cell_menu(t, getattr(t, "result", None), pos)
        )
        for col, d in enumerate(dates, start=2):
            item = table.horizontalHeaderItem(col)
            if item:
                if is_family_day(d):
                    item.setBackground(FAMILY_HEADER_COLOR)
                elif is_holiday_or_weekend(d, result.holidays):
                    item.setBackground(HOLIDAY_HEADER_COLOR)
                if self.is_locked_split_cell(result, d):
                    item.setToolTip("2026-08 전 기존 근무표입니다.")
            self.apply_staffing_header_style(table, result, col, d)
        if not result.employees:
            hint = QTableWidgetItem("여기에 클릭 후 Ctrl+V")
            hint.setTextAlignment(Qt.AlignCenter)
            hint.setBackground(QColor("#fff2cc"))
            table.setItem(0, 0, hint)
            table.setItem(0, 1, QTableWidgetItem("엑셀 표 복사"))
            for col in range(2, len(dates) + 2):
                table.setItem(0, col, QTableWidgetItem(""))
        else:
            for row, emp in enumerate(result.employees):
                name_item = QTableWidgetItem(emp.name)
                id_item = QTableWidgetItem(emp.employee_id)
                name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
                id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 0, name_item)
                table.setItem(row, 1, id_item)
                for col, d in enumerate(dates, start=2):
                    shift = result.schedule.get(d, {}).get(emp.key, OFF)
                    cell = QTableWidgetItem("" if shift == OFF else shift)
                    cell.setTextAlignment(Qt.AlignCenter)
                    self.apply_schedule_cell_background(cell, result, emp, d, shift)
                    if self.is_locked_split_cell(result, d) or not self.is_admin:
                        cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        if self.is_locked_split_cell(result, d):
                            cell.setToolTip("2026-08 전 기존 근무표입니다.")
                    else:
                        cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    table.setItem(row, col, cell)
        table.cellChanged.connect(lambda row, col, t=table: self.on_month_table_cell_changed(t, row, col))
        table.cellDoubleClicked.connect(lambda row, col, t=table: self.on_month_table_cell_double_clicked(t, row, col))
        self.configure_roster_table_layout(table, len(dates), row_count, overview=True)
        return table

    def render_year_overview(self) -> None:
        if not hasattr(self, "year_scroll_layout"):
            return
        self._year_overview_dirty = False
        self._clear_layout(self.year_scroll_layout)
        year = self.year_spin.value()
        month = self.month_spin.value()
        self.clamp_month_split_page(year, month)

        nav_row = QHBoxLayout()
        prev_btn = QPushButton("◀ 이전 달")
        next_btn = QPushButton("다음 달 ▶")
        prev_btn.setFixedWidth(105)
        next_btn.setFixedWidth(105)
        prev_btn.clicked.connect(lambda _checked=False: self.move_roster_page(-1))
        next_btn.clicked.connect(lambda _checked=False: self.move_roster_page(1))
        page_label = QLabel(f"{year}년 {month}월")
        page_label.setStyleSheet("font-size: 20px; font-weight: 800; margin: 10px 0;")
        nav_row.addWidget(prev_btn)
        nav_row.addWidget(page_label)
        nav_row.addStretch(1)
        nav_row.addWidget(next_btn)
        self.year_scroll_layout.addLayout(nav_row)

        def resolve_result(source_name: str, view: Optional[str] = None) -> tuple[ScheduleResult, str]:
            loaded = self.load_existing_schedule_for_source(year, month, source_name)
            if (
                self.result
                and self.result.year == year
                and self.result.month == month
                and self.storage_source_name(self.result) == source_name
            ):
                result = self.result
                status = "현재 편집 중"
            elif loaded:
                result = loaded
                status = "DB 저장됨"
            else:
                result = self.load_schedule_for_view(year, month, view)
                status = "기존 복사본" if result.employees and self.is_team_source(source_name) else "미작성"
            if self.is_team_source(source_name):
                self.apply_split_legacy_prefix(result)
            return result, status

        def add_roster_section(title_text: str, result: ScheduleResult, status: str, source_name: str, clearable: bool) -> None:
            title_row = QHBoxLayout()
            title = QLabel(f"{title_text} · {status}")
            title.setStyleSheet("font-size: 16px; font-weight: 700; margin-top: 14px;")
            title_row.addWidget(title)
            title_row.addStretch(1)
            if self.should_offer_schedule_generation(result, source_name):
                title_row.addWidget(self.make_seed_employees_button(year, month, source_name))
                title_row.addWidget(self.make_schedule_generate_button(year, month, source_name))
            if self.result_has_work_marks(result):
                title_row.addWidget(self.make_schedule_regenerate_button(year, month, source_name))
            if clearable:
                clear_btn = QPushButton("이 달 초기화")
                clear_btn.setFixedWidth(95)
                clear_btn.setEnabled(self.is_admin)
                clear_btn.clicked.connect(lambda _checked=False, y=year, m=month: self.clear_month_schedule(y, m))
                title_row.addWidget(clear_btn)
            self.year_scroll_layout.addLayout(title_row)
            self.year_scroll_layout.addWidget(self._make_schedule_view_table(result))

        if self.month_has_team_dates(year, month):
            for team in TEAM_VIEWS:
                source_name = self.source_name_for_view(year, month, team)
                result, status = resolve_result(source_name, team)
                add_roster_section(self.source_label_for_view(year, month, team), result, status, source_name, False)
        else:
            source_name = self.source_name_for_view(year, month)
            result, status = resolve_result(source_name)
            add_roster_section(self.source_label_for_view(year, month), result, status, source_name, True)
        self.year_scroll_layout.addStretch(1)

    def schedule_year_overview_refresh(self) -> None:
        """Refresh the annual view after the current UI event returns."""
        if self._year_overview_refresh_pending:
            return
        self._year_overview_refresh_pending = True

        def refresh() -> None:
            self._year_overview_refresh_pending = False
            self.render_year_overview()

        QTimer.singleShot(0, refresh)

    def clear_month_schedule(self, year: int, month: int) -> None:
        if not self.require_admin("월 근무표 초기화"):
            return
        source_name = self.source_name_for_view(year, month)
        source_label = self.source_label_for_view(year, month)
        answer = QMessageBox.question(
            self,
            "월 근무표 초기화",
            f"{year}년 {month}월 {source_label} 저장 근무표와 불가일을 삭제할까요?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        deleted = delete_month_schedule(year, month, source_name)
        if self.result and self.result.year == year and self.result.month == month:
            self.result = self.load_schedule_for_view(year, month)
            self.employees = list(self.result.employees)
            self.add_employee_rows(self.result.employees)
            self.render_schedule_table()
            self.refresh_validation_and_stats()
        self.mark_cumulative_stats_dirty()
        self.mark_year_overview_dirty()
        QMessageBox.information(self, "초기화 완료", f"{year}년 {month}월 {source_label} 근무표를 초기화했습니다. 삭제된 저장본: {deleted}개")

    def render_split_month_tables(self, year: int, month: int) -> None:
        self._clear_layout(self.month_split_layout)
        self.clamp_month_split_page(year, month)
        team = TEAM_VIEWS[self.month_split_page_index]
        result = self.result if (
            self.result
            and self.result.year == year
            and self.result.month == month
            and self.storage_source_name(self.result) == team
        ) else self.load_schedule_for_view(year, month, team)
        self.apply_previous_month_gy_carryover(result)
        self.apply_split_legacy_prefix(result)
        self.update_create_schedule_button(result, team)

        nav = QHBoxLayout()
        prev_btn = QPushButton("Prev Page")
        next_btn = QPushButton("Next Page")
        page_label = QLabel(f"{year}-{month:02d} / {team} / {self.month_split_page_index + 1}/{len(TEAM_VIEWS)}")
        page_label.setStyleSheet("font-size: 16px; font-weight: 800; margin-top: 10px;")
        prev_btn.clicked.connect(lambda _checked=False: self.move_roster_page(-1))
        next_btn.clicked.connect(lambda _checked=False: self.move_roster_page(1))
        nav.addWidget(prev_btn)
        nav.addWidget(page_label)
        nav.addStretch(1)
        nav.addWidget(next_btn)
        self.month_split_layout.addLayout(nav)
        self.month_split_layout.addWidget(self._make_schedule_view_table(result))
        self.month_split_layout.addStretch(1)

    def render_schedule_table(self) -> None:
        if not self.result:
            self.update_create_schedule_button(None)
            return
        self._updating_table = True
        dates = month_dates(self.result.year, self.result.month)
        if self.month_has_team_dates(self.result.year, self.result.month):
            self.schedule_table.hide()
            self.month_split_scroll.show()
            self.render_split_month_tables(self.result.year, self.result.month)
            self._updating_table = False
            return
        self.month_split_scroll.hide()
        self.schedule_table.show()
        self.schedule_table.clear()
        self.schedule_table.setEditTriggers(self.current_edit_triggers())
        self.schedule_table.setRowCount(len(self.result.employees))
        self.schedule_table.setColumnCount(len(dates) + 2)
        headers = ["성명", "사번"] + [f"{weekday_ko(d)}\n{d.day}" for d in dates]
        self.schedule_table.setHorizontalHeaderLabels(headers)
        self.schedule_table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        if not self._schedule_header_menu_connected:
            self.schedule_table.horizontalHeader().customContextMenuRequested.connect(
                lambda pos: self.show_date_header_menu(self.schedule_table, pos)
            )
            self._schedule_header_menu_connected = True
        for col, d in enumerate(dates, start=2):
            item = self.schedule_table.horizontalHeaderItem(col)
            if item:
                if is_family_day(d):
                    item.setBackground(FAMILY_HEADER_COLOR)
                elif is_holiday_or_weekend(d, self.result.holidays):
                    item.setBackground(HOLIDAY_HEADER_COLOR)
                if self.is_locked_split_cell(self.result, d):
                    item.setToolTip("2026-08 전 기존 근무표입니다.")
            self.apply_staffing_header_style(self.schedule_table, self.result, col, d)
        for row, emp in enumerate(self.result.employees):
            name_item = QTableWidgetItem(emp.name)
            id_item = QTableWidgetItem(emp.employee_id)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            self.schedule_table.setItem(row, 0, name_item)
            self.schedule_table.setItem(row, 1, id_item)
            for col, d in enumerate(dates, start=2):
                shift = self.result.schedule.get(d, {}).get(emp.key, OFF)
                value = "" if shift == OFF else shift
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                self.apply_schedule_cell_background(item, self.result, emp, d, shift)
                if self.is_locked_split_cell(self.result, d) or not self.is_admin:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    if self.is_locked_split_cell(self.result, d):
                        item.setToolTip("2026-08 전 기존 근무표입니다.")
                else:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                self.schedule_table.setItem(row, col, item)
        self.schedule_table.verticalHeader().setVisible(False)
        self.configure_roster_table_layout(self.schedule_table, len(dates), len(self.result.employees), overview=False)
        self.schedule_table.freezeColumnCount if hasattr(self.schedule_table, "freezeColumnCount") else None
        self.update_create_schedule_button(self.result)
        self._updating_table = False

    def on_schedule_cell_changed(self, row: int, col: int) -> None:
        if self._updating_table or col < 2 or not self.result:
            return
        if not self.is_admin:
            self.render_schedule_table()
            return
        dates = month_dates(self.result.year, self.result.month)
        day_index = col - 2
        if day_index < 0 or day_index >= len(dates) or self.is_locked_split_cell(self.result, dates[day_index]):
            self.render_schedule_table()
            return
        if row < 0 or row >= len(self.result.employees):
            return
        emp = self.result.employees[row]
        d = dates[day_index]
        item = self.schedule_table.item(row, col)
        if not item:
            return
        text = item.text().strip()
        normalized = self.normalize_shift(text)
        if normalized != text:
            item.setText("" if normalized == OFF else normalized)
        self.apply_schedule_cell_background(item, self.result, emp, d, normalized)
        self.sync_schedule_from_table()
        self.render_schedule_table()
        self.refresh_validation_and_stats()

    def on_schedule_cell_double_clicked(self, row: int, col: int) -> None:
        self.toggle_unavailable_for_cell(self.schedule_table, self.result, row, col)

    def on_month_table_cell_changed(self, table: MonthRosterTable, row: int, col: int) -> None:
        if col < 2:
            return
        result = getattr(table, "result", None)
        if not isinstance(result, ScheduleResult):
            return
        if not self.is_admin:
            dates = month_dates(result.year, result.month)
            day_index = col - 2
            if 0 <= row < len(result.employees) and 0 <= day_index < len(dates):
                emp = result.employees[row]
                d = dates[day_index]
                shift = result.schedule.get(d, {}).get(emp.key, OFF)
                item = table.item(row, col)
                if item:
                    table.blockSignals(True)
                    item.setText("" if shift == OFF else shift)
                    self.apply_schedule_cell_background(item, result, emp, d, shift, include_validation=True)
                    table.blockSignals(False)
            return
        dates = month_dates(result.year, result.month)
        day_index = col - 2
        if day_index < 0 or day_index >= len(dates) or self.is_locked_split_cell(result, dates[day_index]):
            item = table.item(row, col)
            if item and 0 <= row < len(result.employees):
                emp = result.employees[row]
                d = dates[day_index]
                shift = result.schedule.get(d, {}).get(emp.key, OFF)
                table.blockSignals(True)
                item.setText("" if shift == OFF else shift)
                self.apply_schedule_cell_background(item, result, emp, d, shift)
                table.blockSignals(False)
            return
        if row < 0 or row >= len(result.employees):
            return
        emp = result.employees[row]
        d = dates[day_index]
        item = table.item(row, col)
        if not item:
            return
        normalized = self.normalize_shift(item.text())
        table.blockSignals(True)
        item.setText("" if normalized == OFF else normalized)
        self.apply_schedule_cell_background(item, result, emp, d, normalized)
        table.blockSignals(False)
        self.sync_result_from_table(table, result)
        self.save_result_silently(result)
        if self.result and self.result.year == result.year and self.result.month == result.month:
            self.result = result
            self.render_schedule_table()
            self.refresh_validation_and_stats()
        self.mark_cumulative_stats_dirty()
        self.mark_year_overview_dirty()

    def on_month_table_cell_double_clicked(self, table: MonthRosterTable, row: int, col: int) -> None:
        self.toggle_unavailable_for_cell(table, getattr(table, "result", None), row, col)

    @staticmethod
    def normalize_shift(text: str) -> str:
        return normalize_shift_code(text)

    def schedule_cell_background(
        self,
        result: ScheduleResult,
        emp: Employee,
        d: date,
        shift: str,
        *,
        include_validation: bool = False,
    ) -> QColor:
        if shift not in (OFF, ""):
            return SHIFT_COLORS.get(shift, QColor("#ffffff"))
        if d in emp.unavailable_dates:
            return UNAVAILABLE_COLOR
        if self.is_locked_split_cell(result, d) and shift == OFF:
            return LOCKED_SPLIT_COLOR
        if is_family_day(d):
            return FAMILY_HEADER_COLOR
        if is_holiday_or_weekend(d, result.holidays):
            return HOLIDAY_HEADER_COLOR
        return SHIFT_COLORS.get(shift, QColor("#ffffff"))

    def apply_schedule_cell_background(
        self,
        item: QTableWidgetItem,
        result: ScheduleResult,
        emp: Employee,
        d: date,
        shift: str,
        *,
        include_validation: bool = False,
    ) -> None:
        item.setBackground(
            self.schedule_cell_background(result, emp, d, shift, include_validation=include_validation)
        )

    def sync_result_from_table(self, table: QTableWidget, result: ScheduleResult) -> None:
        dates = month_dates(result.year, result.month)
        for row, emp in enumerate(result.employees):
            for col, d in enumerate(dates, start=2):
                if self.is_locked_split_cell(result, d):
                    continue
                item = table.item(row, col)
                result.schedule[d][emp.key] = self.normalize_shift(item.text() if item else "")
        carryover_counts = self.previous_month_gy_carryover_counts(result)
        for emp in result.employees:
            count = min(carryover_counts.get(emp.key, 0), len(dates))
            for day_index in range(count):
                if self.is_locked_split_cell(result, dates[day_index]):
                    continue
                result.schedule[dates[day_index]][emp.key] = OFF
        expand_gy_blocks(result.employees, result.year, result.month, result.schedule)
        self.apply_previous_month_gy_carryover(result, carryover_counts)
        self.enforce_duty_day_without_gy(result)
        self.apply_split_legacy_prefix(result)

    def previous_month_gy_carryover_counts(self, result: ScheduleResult) -> Dict[str, int]:
        prev_year = result.year if result.month > 1 else result.year - 1
        prev_month = result.month - 1 if result.month > 1 else 12
        source_name = self.storage_source_name(result)
        prev_source = source_name if self.is_team_source(source_name) and self.month_has_team_dates(prev_year, prev_month) else self.legacy_source_name(prev_year, prev_month)
        previous = self.load_existing_schedule_for_source(prev_year, prev_month, prev_source)
        if not previous:
            return {}
        current_dates = month_dates(result.year, result.month)
        previous_dates = month_dates(prev_year, prev_month)
        previous_last = previous_dates[-1]
        employee_keys = {emp.key for emp in result.employees}
        carryover_counts: Dict[str, int] = {}
        current_date_set = set(current_dates)
        for emp in previous.employees:
            if emp.key not in employee_keys:
                continue
            for d in previous_dates:
                if previous.schedule.get(d, {}).get(emp.key, OFF) != SHIFT_GY:
                    continue
                prev_day = d - timedelta(days=1)
                if prev_day in previous.schedule and previous.schedule.get(prev_day, {}).get(emp.key, OFF) == SHIFT_GY:
                    continue
                overflow = 0
                for offset in range(1, 6):
                    cur = d + timedelta(days=offset)
                    if is_duty_day(cur):
                        break
                    if cur in previous.schedule and any(
                        shift == SHIFT_DUTY for shift in previous.schedule.get(cur, {}).values()
                    ):
                        break
                    if cur in current_date_set:
                        overflow += 1
                if overflow <= 0:
                    continue
                carryover_counts[emp.key] = max(
                    carryover_counts.get(emp.key, 0),
                    min(overflow, len(current_dates)),
                )
        return carryover_counts

    def apply_previous_month_gy_carryover(
        self,
        result: ScheduleResult,
        carryover_counts: Optional[Dict[str, int]] = None,
    ) -> None:
        counts = carryover_counts if carryover_counts is not None else self.previous_month_gy_carryover_counts(result)
        current_dates = month_dates(result.year, result.month)
        for emp in result.employees:
            count = min(counts.get(emp.key, 0), len(current_dates))
            for day_index in range(count):
                d = current_dates[day_index]
                if self.is_locked_split_cell(result, d):
                    continue
                if not is_duty_day(d):
                    result.schedule[d][emp.key] = SHIFT_GY
        self.enforce_month_start_after_previous_duty(result)
        self.enforce_duty_day_without_gy(result)

    def previous_month_gy_initial_schedule(self, result: ScheduleResult) -> ScheduleMap:
        carryover_counts = self.previous_month_gy_carryover_counts(result)
        current_dates = month_dates(result.year, result.month)
        initial: ScheduleMap = {}
        for emp in result.employees:
            count = min(carryover_counts.get(emp.key, 0), len(current_dates))
            for day_index in range(count):
                d = current_dates[day_index]
                if self.is_locked_split_cell(result, d) or is_duty_day(d):
                    continue
                initial.setdefault(d, {})[emp.key] = SHIFT_GY
        return initial

    def previous_month_last_duty_keys(self, year: int, month: int, source_name: Optional[str] = None) -> set[str]:
        prev_year = year if month > 1 else year - 1
        prev_month = month - 1 if month > 1 else 12
        current_source = source_name or self.source_name_for_view(year, month)
        prev_source = current_source if self.is_team_source(current_source) and self.month_has_team_dates(prev_year, prev_month) else self.legacy_source_name(prev_year, prev_month)
        previous = self.load_existing_schedule_for_source(prev_year, prev_month, prev_source)
        if not previous:
            return set()
        previous_last = month_dates(prev_year, prev_month)[-1]
        if previous_last + timedelta(days=1) != month_dates(year, month)[0]:
            return set()
        return {
            emp.key
            for emp in previous.employees
            if previous.schedule.get(previous_last, {}).get(emp.key, OFF) == SHIFT_DUTY
        }

    def enforce_month_start_after_previous_duty(self, result: ScheduleResult) -> None:
        current_dates = month_dates(result.year, result.month)
        if not current_dates:
            return
        first_day = current_dates[0]
        blocked_keys = self.previous_month_last_duty_keys(result.year, result.month, self.storage_source_name(result))
        if not blocked_keys:
            return
        for emp in result.employees:
            if emp.key not in blocked_keys:
                continue
            for d in current_dates:
                if self.is_locked_split_cell(result, d):
                    continue
                if is_duty_day(d):
                    break
                if result.schedule.get(d, {}).get(emp.key, OFF) != SHIFT_GY:
                    break
                result.schedule[d][emp.key] = OFF

    @staticmethod
    def enforce_duty_day_without_gy(result: ScheduleResult) -> None:
        for d in month_dates(result.year, result.month):
            day_map = result.schedule.get(d, {})
            if not any(shift == SHIFT_DUTY for shift in day_map.values()):
                continue
            for emp in result.employees:
                if day_map.get(emp.key, OFF) == SHIFT_GY:
                    day_map[emp.key] = OFF

    def sync_schedule_from_table(self) -> None:
        if not self.result:
            return
        self.sync_result_from_table(self.schedule_table, self.result)

    def apply_staffing_header_style(self, table: QTableWidget, result: ScheduleResult, col: int, d: date) -> None:
        item = table.horizontalHeaderItem(col)
        if not item:
            return
        counts = Counter(
            shift for shift in result.schedule.get(d, {}).values()
            if shift and shift not in (OFF, SHIFT_GY_REST)
        )
        min_rules = min_rules_for_date(self.current_result_rules(result), d, result.holidays)
        is_duty = is_duty_day(d)
        required = [SHIFT_DAY, SHIFT_SWING, SHIFT_DUTY if is_duty else SHIFT_GY]

        def actual_count(shift: str) -> int:
            return counts[shift]

        if all(actual_count(shift) >= max(1, min_rules.get(shift, 1)) for shift in required):
            item.setForeground(STAFFING_OK_COLOR)
            font = item.font()
            font.setBold(True)
            font.setItalic(any(actual_count(shift) > max(1, min_rules.get(shift, 1)) for shift in required))
            item.setFont(font)
        else:
            item.setForeground(QColor("#000000"))
            font = item.font()
            font.setBold(False)
            font.setItalic(False)
            item.setFont(font)

    def refresh_schedule_header_styles(self) -> None:
        if not self.result:
            return
        if self.schedule_table.isHidden():
            return
        dates = month_dates(self.result.year, self.result.month)
        for col, d in enumerate(dates, start=2):
            self.apply_staffing_header_style(self.schedule_table, self.result, col, d)

    def refresh_validation_and_stats(self) -> None:
        self.sync_rules_from_widgets()
        if not self.result:
            return
        rules = self.current_result_rules(self.result)
        self.result.warnings = validate_schedule(
            self.result.employees,
            self.result.year,
            self.result.month,
            self.result.schedule,
            self.result.holidays,
            rules,
        )
        self.render_stats()
        self.render_warnings()
        self.refresh_schedule_header_styles()
        self.paint_validation_errors()

    def render_stats(self) -> None:
        if not self.result:
            return
        if self.stats_mode_combo.currentText() == "월간 통계":
            self.render_month_stats_as_main()

    def on_stats_mode_changed(self) -> None:
        self.render_cumulative_stats()

    def render_month_stats_as_main(self) -> None:
        if not self.result:
            return
        dates = month_dates(self.result.year, self.result.month)
        stats = compute_stats(self.result.employees, dates, self.result.schedule, self.result.holidays)
        excluded = excluded_people("월간 통계")
        stats = {key: stat for key, stat in stats.items() if key not in excluded}
        avg = averages(stats)
        headers = STAT_HEADERS + ["총근무 평균편차"]
        table = self.cumulative_stats_table
        self.current_stats_row_people = []
        table.clear()
        table.setColumnCount(len(headers))
        table.setRowCount(len(stats))
        table.setHorizontalHeaderLabels(headers)
        for row, stat in enumerate(stats.values()):
            self.current_stats_row_people.append((stat.employee_key, f"{stat.name} / {stat.employee_id}".rstrip(" / ")))
            values = stat.as_row() + [round(stat.total_work - avg.get("total_work", 0), 2)]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if col == len(headers) - 1 and isinstance(value, (int, float)) and abs(value) >= 2:
                    item.setBackground(WARNING_COLOR)
                table.setItem(row, col, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)

    def render_cumulative_stats(self) -> None:
        self._cumulative_stats_dirty = False
        start_year = self.stats_start_year_spin.value()
        start_month = self.stats_start_month_spin.value()
        end_year = self.stats_end_year_spin.value()
        end_month = self.stats_end_month_spin.value()
        if start_year * 100 + start_month > end_year * 100 + end_month:
            QMessageBox.warning(self, "기간 오류", "통계 시작 월이 종료 월보다 늦습니다.")
            return
        self.render_monthly_filter_stats(start_year, start_month, end_year, end_month)
        self.cumulative_stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.cumulative_stats_table.verticalHeader().setVisible(False)
        self.cumulative_stats_table.verticalHeader().setDefaultSectionSize(34)

    @staticmethod
    def iter_month_values(start_year: int, start_month: int, end_year: int, end_month: int) -> List[tuple[int, int]]:
        months: List[tuple[int, int]] = []
        year = start_year
        month = start_month
        while year * 100 + month <= end_year * 100 + end_month:
            months.append((year, month))
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1
        return months

    def selected_monthly_stats_filters(self) -> set[tuple[str, str]]:
        return set(getattr(self, "monthly_stats_filters", set()))

    def selected_stats_source_name(self) -> str:
        if not hasattr(self, "stats_source_combo"):
            return ""
        return str(self.stats_source_combo.currentData() or "")

    def show_monthly_stats_filter_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("월별 필터 설정")
        layout = QVBoxLayout(dialog)
        grid = QGridLayout()
        checks: Dict[tuple[str, str], QCheckBox] = {}
        grid.addWidget(QLabel(""), 0, 0)
        for col, (_shift_key, shift_label) in enumerate(self.monthly_stats_shift_types, start=1):
            label = QLabel(shift_label)
            label.setAlignment(Qt.AlignCenter)
            grid.addWidget(label, 0, col)
        selected = self.selected_monthly_stats_filters()
        for row, (date_key, date_label) in enumerate(self.monthly_stats_date_types, start=1):
            grid.addWidget(QLabel(date_label), row, 0)
            for col, (shift_key, _shift_label) in enumerate(self.monthly_stats_shift_types, start=1):
                checkbox = QCheckBox()
                checkbox.setChecked((date_key, shift_key) in selected)
                checkbox.setStyleSheet("margin-left: 12px; margin-right: 12px;")
                key = (date_key, shift_key)
                checks[key] = checkbox
                grid.addWidget(checkbox, row, col, alignment=Qt.AlignCenter)
        layout.addLayout(grid)

        button_row = QHBoxLayout()
        select_all_btn = QPushButton("전체 선택")
        clear_btn = QPushButton("전체 해제")
        cancel_btn = QPushButton("취소")
        apply_btn = QPushButton("적용")
        select_all_btn.clicked.connect(lambda: [checkbox.setChecked(True) for checkbox in checks.values()])
        clear_btn.clicked.connect(lambda: [checkbox.setChecked(False) for checkbox in checks.values()])
        cancel_btn.clicked.connect(dialog.reject)

        def apply_filters() -> None:
            self.monthly_stats_filters = {key for key, checkbox in checks.items() if checkbox.isChecked()}
            dialog.accept()
            self.render_cumulative_stats()

        apply_btn.clicked.connect(apply_filters)
        button_row.addWidget(select_all_btn)
        button_row.addWidget(clear_btn)
        button_row.addStretch(1)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(apply_btn)
        layout.addLayout(button_row)
        dialog.exec()

    def monthly_stats_date_keys(self, d: date) -> set[str]:
        official_holidays = korean_holidays(d.year) - family_days(d.year)
        keys: set[str] = set()
        if is_family_day(d):
            keys.add("family")
        if d in official_holidays:
            keys.add("holiday")
        if d.weekday() == 5:
            keys.add("saturday")
        elif d.weekday() == 6:
            keys.add("sunday")
        elif not keys:
            keys.add("weekday")
        return keys

    @staticmethod
    def monthly_stats_shift_key(shift: str) -> Optional[str]:
        if shift == SHIFT_DAY:
            return "day"
        if shift == SHIFT_SWING:
            return "swing"
        if shift in (SHIFT_GY, SHIFT_DUTY):
            return "gy"
        return None

    def render_monthly_filter_stats(self, start_year: int, start_month: int, end_year: int, end_month: int) -> None:
        selected_filters = self.selected_monthly_stats_filters()
        months = self.iter_month_values(start_year, start_month, end_year, end_month)
        month_keys = [f"{year}-{month:02d}" for year, month in months]
        rows = self.filter_period_rows_for_stats(period_assignment_rows(start_year, start_month, end_year, end_month))
        excluded = excluded_people("월별 필터 통계")

        people: Dict[str, Dict[str, str]] = {}
        counts: Dict[str, Dict[str, int]] = {}
        for row in rows:
            name = str(row.get("name") or "")
            employee_no = str(row.get("employee_no") or "")
            employee_key = f"{name}|{employee_no}"
            if employee_key in excluded:
                continue
            d = date.fromisoformat(str(row.get("work_date")))
            shift_key = self.monthly_stats_shift_key(str(row.get("shift_code") or OFF))
            if shift_key is None:
                continue
            if not any((date_key, shift_key) in selected_filters for date_key in self.monthly_stats_date_keys(d)):
                continue
            month_key = f"{d.year}-{d.month:02d}"
            people.setdefault(employee_key, {"name": name, "employee_no": employee_no})
            counts.setdefault(employee_key, {key: 0 for key in month_keys})
            if month_key in counts[employee_key]:
                counts[employee_key][month_key] += 1

        sorted_keys = sorted(people, key=lambda key: (people[key]["name"], people[key]["employee_no"]))
        month_color_values = {
            month_key: [
                float(counts.get(employee_key, {}).get(month_key, 0))
                for employee_key in sorted_keys
            ]
            for month_key in month_keys
        }

        table = self.cumulative_stats_table
        self.current_stats_row_people = []
        table.clear()
        table.setColumnCount(1 + len(month_keys))
        table.setRowCount(len(sorted_keys))
        table.setHorizontalHeaderLabels(["이름"] + month_keys)
        for row_index, employee_key in enumerate(sorted_keys):
            person = people[employee_key]
            name = person["name"]
            employee_no = person["employee_no"]
            label = name if not employee_no else f"{name} / {employee_no}"
            self.current_stats_row_people.append((employee_key, label))
            name_item = QTableWidgetItem(label)
            table.setItem(row_index, 0, name_item)
            for col_offset, month_key in enumerate(month_keys, start=1):
                value = counts.get(employee_key, {}).get(month_key, 0)
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if value > 0:
                    item.setBackground(self._relative_gradient_color(float(value), month_color_values[month_key]))
                table.setItem(row_index, col_offset, item)

    def render_saved_months_as_main_stat(self, month_rows: List[Dict[str, object]]) -> None:
        headers = ["ID", "연도", "월", "출처", "저장시각"]
        keys = ["id", "year", "month", "source_name", "imported_at"]
        self.current_stats_row_people = []
        self.cumulative_stats_table.clear()
        self.cumulative_stats_table.setColumnCount(len(headers))
        self.cumulative_stats_table.setHorizontalHeaderLabels(headers)
        self.cumulative_stats_table.setRowCount(len(month_rows))
        for r, row in enumerate(month_rows):
            for c, key in enumerate(keys):
                self.cumulative_stats_table.setItem(r, c, QTableWidgetItem(str(row.get(key, ""))))

    def render_period_shift_stats(self, start_year: int, start_month: int, end_year: int, end_month: int) -> None:
        rows = self.filter_period_rows_for_split(period_assignment_rows(start_year, start_month, end_year, end_month))
        period_end = month_dates(end_year, end_month)[-1]
        work_shifts = {SHIFT_DAY, SHIFT_SWING, SHIFT_GY, SHIFT_DUTY}
        by_emp: Dict[tuple[str, str], Dict[str, object]] = {}
        for row in rows:
            name = str(row.get("name") or "")
            employee_no = str(row.get("employee_no") or "")
            key = (name, employee_no)
            d = date.fromisoformat(str(row.get("work_date")))
            shift = str(row.get("shift_code") or OFF)
            stat = by_emp.setdefault(key, {
                "name": name,
                "employee_no": employee_no,
                "first_work_date": None,
                "eligible_dates": set(),
                "weekday_day": 0,
                "weekday_swing": 0,
                "holiday_day": 0,
                "holiday_swing": 0,
                "family_day": 0,
                "family_swing": 0,
                "gy": 0,
                "duty": 0,
                "last_gy_date": None,
                "last_duty_date": None,
                "total_work": 0,
            })
            if shift in work_shifts:
                first = stat["first_work_date"]
                if first is None or d < first:
                    stat["first_work_date"] = d
        for row in rows:
            key = (str(row.get("name") or ""), str(row.get("employee_no") or ""))
            stat = by_emp.get(key)
            if not stat or stat["first_work_date"] is None:
                continue
            d = date.fromisoformat(str(row.get("work_date")))
            if d < stat["first_work_date"]:
                continue
            stat["eligible_dates"].add(d)  # type: ignore[index]
            shift = str(row.get("shift_code") or OFF)
            if shift in work_shifts:
                stat["total_work"] = int(stat["total_work"]) + 1
            if shift == SHIFT_DAY:
                if is_family_day(d):
                    stat["family_day"] = int(stat["family_day"]) + 1
                elif is_holiday_or_weekend(d, korean_holidays(d.year)):
                    stat["holiday_day"] = int(stat["holiday_day"]) + 1
                else:
                    stat["weekday_day"] = int(stat["weekday_day"]) + 1
            elif shift == SHIFT_SWING:
                if is_family_day(d):
                    stat["family_swing"] = int(stat["family_swing"]) + 1
                elif is_holiday_or_weekend(d, korean_holidays(d.year)):
                    stat["holiday_swing"] = int(stat["holiday_swing"]) + 1
                else:
                    stat["weekday_swing"] = int(stat["weekday_swing"]) + 1
            elif shift == SHIFT_GY:
                stat["gy"] = int(stat["gy"]) + 1
                last_gy = stat["last_gy_date"]
                if last_gy is None or d > last_gy:
                    stat["last_gy_date"] = d
            elif shift == SHIFT_DUTY:
                stat["duty"] = int(stat["duty"]) + 1
                last_duty = stat["last_duty_date"]
                if last_duty is None or d > last_duty:
                    stat["last_duty_date"] = d

        mode = self.stats_mode_combo.currentText()
        value_mode = self.stats_value_mode_combo.currentText()
        excluded = excluded_people(mode)
        if mode == "GY/당직":
            headers = ["성명", "사번", "첫근무일", "대상일수", "G/지근", "당직", "GY+당직", "GY율", "이전 GY 후", "이전 당직 후"]
            metric_keys = ["gy", "duty", "gy_total", "gy_total", "days_since_gy", "days_since_duty"]
        else:
            headers = ["성명", "사번", "첫근무일", "대상일수", "평일 D", "평일 S", "휴일 D", "휴일 S", "페데 D", "페데 S", "총근무", "근무율"]
            metric_keys = [
                "weekday_day", "weekday_swing",
                "holiday_day", "holiday_swing",
                "family_day", "family_swing",
                "total_work", "total_work",
            ]
        values_rows = []
        self.current_stats_row_people = []
        color_value_rows: List[List[Optional[float]]] = []
        for stat in by_emp.values():
            if stat["first_work_date"] is None:
                continue
            employee_key = f"{stat['name']}|{stat['employee_no']}"
            if employee_key in excluded:
                continue
            eligible_days = len(stat["eligible_dates"])  # type: ignore[arg-type]
            if mode == "GY/당직":
                gy_total = int(stat["gy"]) + int(stat["duty"])
                metric_counts = [int(stat["gy"]), int(stat["duty"]), gy_total, gy_total]
                days_since_gy = self._days_since(stat["last_gy_date"], period_end)
                days_since_duty = self._days_since(stat["last_duty_date"], period_end)
                values = [
                    stat["name"], stat["employee_no"], stat["first_work_date"], eligible_days,
                    *[
                        self._format_stat_value(count, eligible_days, value_mode if idx < 3 else "퍼센트")
                        for idx, count in enumerate(metric_counts)
                    ],
                    self._format_days_since(days_since_gy),
                    self._format_days_since(days_since_duty),
                ]
                color_values: List[Optional[float]] = [
                    *(count / eligible_days if eligible_days > 0 else 0.0 for count in metric_counts),
                    float(days_since_gy) if days_since_gy is not None else None,
                    float(days_since_duty) if days_since_duty is not None else None,
                ]
            else:
                metric_counts = [
                    int(stat["weekday_day"]), int(stat["weekday_swing"]),
                    int(stat["holiday_day"]), int(stat["holiday_swing"]),
                    int(stat["family_day"]), int(stat["family_swing"]),
                    int(stat["total_work"]), int(stat["total_work"]),
                ]
                values = [
                    stat["name"], stat["employee_no"], stat["first_work_date"], eligible_days,
                    *[
                        self._format_stat_value(count, eligible_days, value_mode if idx < 7 else "퍼센트")
                        for idx, count in enumerate(metric_counts)
                    ],
                ]
                color_values = [
                    count / eligible_days if eligible_days > 0 else 0.0
                    for count in metric_counts
                ]
            values_rows.append(values)
            self.current_stats_row_people.append((
                employee_key,
                f"{stat['name']} / {stat['employee_no']}".rstrip(" / "),
            ))
            color_value_rows.append(color_values)
        column_values = list(zip(*color_value_rows)) if color_value_rows else []
        self.cumulative_stats_table.clear()
        self.cumulative_stats_table.setColumnCount(len(headers))
        self.cumulative_stats_table.setHorizontalHeaderLabels(headers)
        self.cumulative_stats_table.setRowCount(len(values_rows))
        for r, values in enumerate(values_rows):
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                metric_index = c - 4
                if 0 <= metric_index < len(metric_keys) and column_values:
                    current = color_value_rows[r][metric_index]
                    values_for_color = [v for v in column_values[metric_index] if v is not None]
                    if current is not None:
                        item.setBackground(self._relative_gradient_color(current, values_for_color))
                self.cumulative_stats_table.setItem(r, c, item)

    def filter_period_rows_for_split(self, rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
        split = self.enabled_split_date()
        filtered: List[Dict[str, object]] = []
        for row in rows:
            source_name = str(row.get("source_name") or "")
            is_team = self.is_team_source(source_name)
            if not split:
                if not is_team:
                    filtered.append(row)
                continue
            work_date = date.fromisoformat(str(row.get("work_date")))
            if work_date < split:
                if not is_team:
                    filtered.append(row)
            elif is_team:
                filtered.append(row)
        return filtered

    def filter_period_rows_for_stats(self, rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
        selected_source = self.selected_stats_source_name()
        rows = self.filter_period_rows_for_split(rows)
        if not selected_source:
            return rows
        return [
            row for row in rows
            if str(row.get("source_name") or "") == selected_source
        ]

    def show_stats_table_menu(self, pos) -> None:
        mode = self.stats_mode_combo.currentText()
        if mode == "저장 월":
            return
        menu = QMenu(self)
        row = self.cumulative_stats_table.rowAt(pos.y())
        if 0 <= row < len(self.current_stats_row_people):
            employee_key, label = self.current_stats_row_people[row]
            menu.addAction(f"{label} 제외").triggered.connect(
                lambda _checked=False, m=mode, k=employee_key, l=label: self.exclude_stats_person(m, k, l)
            )
            menu.addSeparator()
        menu.addAction("제외 인원 관리").triggered.connect(self.show_stats_exclusion_manager)
        menu.exec(self.cumulative_stats_table.viewport().mapToGlobal(pos))

    def exclude_stats_person(self, mode: str, employee_key: str, label: str) -> None:
        exclude_person(mode, employee_key, label)
        self.render_cumulative_stats()

    def restore_stats_person(self, mode: str, employee_key: str) -> None:
        include_person(mode, employee_key)
        self.render_cumulative_stats()

    def show_stats_exclusion_manager(self) -> None:
        mode = self.stats_mode_combo.currentText()
        if mode == "저장 월":
            QMessageBox.information(self, "제외 인원 관리", "저장 월 목록에는 제외 인원을 적용하지 않습니다.")
            return
        excluded = excluded_people(mode)
        if not excluded:
            QMessageBox.information(self, "제외 인원 관리", f"{mode} 제외 인원이 없습니다.")
            return
        menu = QMenu(self)
        menu.addAction(f"{mode} 제외 인원")
        menu.addSeparator()
        for employee_key, label in sorted(excluded.items(), key=lambda item: item[1]):
            menu.addAction(f"{label} 복구").triggered.connect(
                lambda _checked=False, m=mode, k=employee_key: self.restore_stats_person(m, k)
            )
        menu.exec(QCursor.pos())

    @staticmethod
    def _percent(numerator: int, denominator: int) -> str:
        if denominator <= 0:
            return "0.0%"
        return f"{numerator / denominator * 100:.1f}%"

    def _format_stat_value(self, count: int, denominator: int, mode: str) -> str:
        percent = self._percent(count, denominator)
        if mode == "퍼센트":
            return percent
        if mode == "갯수+퍼센트":
            return f"{count} ({percent})"
        return str(count)

    @staticmethod
    def _days_since(last_date: object, period_end: date) -> Optional[int]:
        if not isinstance(last_date, date):
            return None
        return max(0, (period_end - last_date).days)

    @staticmethod
    def _format_days_since(days: Optional[int]) -> str:
        if days is None:
            return "-"
        return f"{days}일"

    @staticmethod
    def _relative_gradient_color(value: float, values: List[float]) -> QColor:
        if not values:
            return QColor("#ffffff")
        low = min(values)
        high = max(values)
        if high <= low:
            if high <= 0:
                return QColor("#ffffff")
            ratio = 1.0
        else:
            ratio = min(1.0, max(0.0, (value - low) / (high - low)))
        stops = [
            (0.0, (74, 144, 226)),
            (0.33, (80, 200, 120)),
            (0.66, (255, 224, 102)),
            (1.0, (244, 76, 76)),
        ]
        left = stops[0]
        right = stops[-1]
        for index in range(len(stops) - 1):
            if stops[index][0] <= ratio <= stops[index + 1][0]:
                left = stops[index]
                right = stops[index + 1]
                break
        span = max(right[0] - left[0], 0.000001)
        local = (ratio - left[0]) / span
        r = round(left[1][0] * (1 - local) + right[1][0] * local)
        g = round(left[1][1] * (1 - local) + right[1][1] * local)
        b = round(left[1][2] * (1 - local) + right[1][2] * local)
        return QColor(r, g, b)

    def render_warnings(self) -> None:
        if not self.result:
            return
        if self.result.warnings:
            self.warning_box.setPlainText("\n".join(self.result.warnings))
        else:
            self.warning_box.setPlainText("검증 경고 없음")

    def paint_validation_errors(self) -> None:
        if not self.result:
            return
        if self.schedule_table.isHidden():
            return
        dates = month_dates(self.result.year, self.result.month)
        employee_by_key = {e.key: e for e in self.result.employees}
        # reset colors
        for row, emp in enumerate(self.result.employees):
            for col, d in enumerate(dates, start=2):
                item = self.schedule_table.item(row, col)
                if not item:
                    continue
                shift = self.normalize_shift(item.text())
                self.apply_schedule_cell_background(
                    item,
                    self.result,
                    emp,
                    d,
                    shift,
                    include_validation=True,
                )


def run() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
