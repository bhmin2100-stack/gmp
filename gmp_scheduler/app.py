from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .calendar_utils import is_holiday_or_weekend, korean_holidays, month_dates, weekday_ko
from .database import cumulative_stats, load_schedule_result, save_schedule, save_unavailable_days, saved_months
from .excel_io import export_schedule_to_excel, normalize_shift_code, parse_employees_from_tsv, parse_schedule_from_clipboard, parse_schedule_from_tsv, parse_unavailable, parse_unavailable_from_clipboard
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleResult, ShiftRules
from .scheduler import ScheduleError, generate_month_schedule
from .stats import STAT_HEADERS, averages, compute_stats
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
HOLIDAY_HEADER_COLOR = QColor("#f4cccc")


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


class MonthRosterTable(PasteTableWidget):
    """A month table in the yearly view that accepts Ctrl+V directly."""

    def __init__(self, owner: "MainWindow", year: int, month: int, *args, **kwargs) -> None:
        super().__init__(*args, allow_expand=False, **kwargs)
        self.owner = owner
        self.year = year
        self.month = month
        self.setToolTip("이 월 표를 클릭한 뒤 Ctrl+V 하면 엑셀 근무표가 바로 붙여넣어집니다.")
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.Paste):
            self.owner.paste_schedule_from_clipboard_for_month(self.year, self.month)
            return
        super().keyPressEvent(event)

    def paste_text(self, text: str) -> None:
        self.owner.paste_schedule_from_clipboard_for_month(self.year, self.month)


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
        self.setToolTip("어느 셀을 클릭해도 Ctrl+V는 엑셀 근무표 전체 붙여넣기로 처리됩니다.")
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.Paste):
            self.owner.paste_schedule_from_clipboard()
            return
        super().keyPressEvent(event)

    def paste_text(self, text: str) -> None:
        self.owner.paste_schedule_from_clipboard()


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
        self.rules = ShiftRules()
        self._updating_table = False

        self.year_spin = QSpinBox()
        self.year_spin.setRange(2020, 2100)
        self.year_spin.setValue(date.today().year)
        self.month_spin = QSpinBox()
        self.month_spin.setRange(1, 12)
        self.month_spin.setValue(date.today().month)

        self._build_rule_widgets()

        self.employee_table = ScheduleInputTable(self, 0, 4)
        self.employee_table.setHorizontalHeaderLabels(["성명", "사번", "신규", "불가일(YYYY-MM-DD, ...)"])
        self.employee_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self.paste_box = QTextEdit()
        self.paste_box.setPlaceholderText("보조 입력칸입니다. 권장 방식: 엑셀에서 표 범위 복사 → 위쪽 [엑셀 근무표 붙여넣기] 또는 [회색 불가일 붙여넣기] 버튼 클릭")
        self.paste_box.setMaximumHeight(100)

        self.schedule_table = CurrentMonthRosterTable(self, 0, 0)
        self.schedule_table.cellChanged.connect(self.on_schedule_cell_changed)

        self.month_stats_table = QTableWidget()
        self.cumulative_stats_table = QTableWidget()
        self.saved_months_table = QTableWidget()
        self.warning_box = QTextEdit()
        self.warning_box.setReadOnly(True)

        self._build_ui()
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
        self.employees = []
        self.add_employee_rows([])
        self.result = ScheduleResult(
            self.year_spin.value(),
            self.month_spin.value(),
            self.employees,
            {d: {} for d in month_dates(self.year_spin.value(), self.month_spin.value())},
            korean_holidays(self.year_spin.value()),
        )
        self.render_schedule_table()
        self.render_year_overview()

    def _build_rule_widgets(self) -> None:
        def spin(value: int, minimum: int = 0, maximum: int = 31) -> QSpinBox:
            widget = QSpinBox()
            widget.setRange(minimum, maximum)
            widget.setValue(value)
            return widget

        self.weekday_day_spin = spin(self.rules.min_weekday.get(SHIFT_DAY, 1))
        self.weekday_sw_spin = spin(self.rules.min_weekday.get(SHIFT_SWING, 1))
        self.weekday_gy_spin = spin(self.rules.min_weekday.get(SHIFT_GY, 1))
        self.holiday_day_spin = spin(self.rules.min_holiday.get(SHIFT_DAY, 1))
        self.holiday_sw_spin = spin(self.rules.min_holiday.get(SHIFT_SWING, 1))
        self.holiday_gy_spin = spin(self.rules.min_holiday.get(SHIFT_DUTY, 1))
        self.max_consecutive_spin = spin(self.rules.max_consecutive_work_days, 1)
        self.max_consecutive_gy_spin = spin(self.rules.max_consecutive_gy, 1)

    def _build_ui(self) -> None:
        toolbar = QToolBar("main")
        self.addToolBar(toolbar)
        save_db_action = QAction("현재 근무표 DB 저장", self)
        save_db_action.triggered.connect(self.save_current_schedule_to_db)
        export_action = QAction("엑셀 저장", self)
        export_action.triggered.connect(self.export_excel)
        toolbar.addAction(save_db_action)
        toolbar.addAction(export_action)

        root = QWidget()
        root_layout = QVBoxLayout(root)

        top = QHBoxLayout()
        top.addWidget(QLabel("연도"))
        top.addWidget(self.year_spin)
        top.addWidget(QLabel("월"))
        top.addWidget(self.month_spin)
        generate_btn = QPushButton("자동 생성")
        generate_btn.clicked.connect(self.generate_schedule)
        validate_btn = QPushButton("검증/통계 갱신")
        validate_btn.clicked.connect(self.refresh_validation_and_stats)
        refresh_year_btn = QPushButton("연간 보기 갱신")
        refresh_year_btn.clicked.connect(self.render_year_overview)
        add_btn = QPushButton("직원 행 추가")
        add_btn.clicked.connect(lambda: self.employee_table.insertRow(self.employee_table.rowCount()))
        paste_btn = QPushButton("엑셀 근무표 붙여넣기")
        paste_btn.clicked.connect(self.paste_schedule_from_clipboard)
        paste_unavailable_btn = QPushButton("회색 불가일 붙여넣기")
        paste_unavailable_btn.clicked.connect(self.paste_unavailable_from_clipboard)
        top.addWidget(generate_btn)
        top.addWidget(validate_btn)
        top.addWidget(refresh_year_btn)
        top.addWidget(add_btn)
        top.addWidget(paste_btn)
        top.addWidget(paste_unavailable_btn)
        top.addStretch(1)
        root_layout.addLayout(top)

        splitter = QSplitter(Qt.Vertical)
        tabs = QTabWidget()

        year_tab = QWidget()
        year_layout = QVBoxLayout(year_tab)
        year_layout.addWidget(QLabel("연도 전체 근무표입니다. 각 월 표를 클릭하고 Ctrl+V 하면 그 월에 엑셀 근무표가 바로 붙습니다. 마우스 휠로 1월~12월을 내려보세요."))
        self.year_scroll = QScrollArea()
        self.year_scroll.setWidgetResizable(True)
        self.year_scroll_content = QWidget()
        self.year_scroll_layout = QVBoxLayout(self.year_scroll_content)
        self.year_scroll.setWidget(self.year_scroll_content)
        year_layout.addWidget(self.year_scroll)
        tabs.addTab(year_tab, "연간 보기")

        schedule_tab = QWidget()
        schedule_layout = QVBoxLayout(schedule_tab)
        schedule_layout.addWidget(QLabel("메인 월별 근무표입니다. 엑셀에서 성명/사번/1일~말일 표를 복사한 뒤 이 표 아무 셀에 커서를 두고 Ctrl+V 하세요. 코드: D, S, G/지근, 당직, 지휴, 빈칸"))
        schedule_layout.addWidget(self.schedule_table)
        tabs.addTab(schedule_tab, "월간 근무표")

        employee_tab = QWidget()
        employee_layout = QVBoxLayout(employee_tab)
        employee_layout.addWidget(QLabel("직원 목록/불가일 확인용입니다. 회색 불가일도 엑셀에서 복사 후 [회색 불가일 붙여넣기]로 반영합니다."))
        employee_layout.addWidget(self.employee_table)
        employee_layout.addWidget(self.paste_box)
        tabs.addTab(employee_tab, "직원/불가일")

        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        stats_layout.addWidget(QLabel("월간 통계"))
        stats_layout.addWidget(self.month_stats_table)
        refresh_cum_btn = QPushButton("누적 통계 새로고침")
        refresh_cum_btn.clicked.connect(self.render_cumulative_stats)
        stats_layout.addWidget(refresh_cum_btn)
        stats_layout.addWidget(QLabel("DB 저장 월 목록"))
        stats_layout.addWidget(self.saved_months_table)
        stats_layout.addWidget(QLabel("누적 통계"))
        stats_layout.addWidget(self.cumulative_stats_table)
        tabs.addTab(stats_tab, "통계")

        settings_tab = QWidget()
        settings_layout = QHBoxLayout(settings_tab)
        weekday_group = QGroupBox("평일 최소 인원")
        weekday_form = QFormLayout(weekday_group)
        weekday_form.addRow("D", self.weekday_day_spin)
        weekday_form.addRow("S", self.weekday_sw_spin)
        weekday_form.addRow("G/지근", self.weekday_gy_spin)

        holiday_group = QGroupBox("휴일/주말 최소 인원")
        holiday_form = QFormLayout(holiday_group)
        holiday_form.addRow("D", self.holiday_day_spin)
        holiday_form.addRow("S", self.holiday_sw_spin)
        holiday_form.addRow("당직", self.holiday_gy_spin)

        rule_group = QGroupBox("제약")
        rule_form = QFormLayout(rule_group)
        rule_form.addRow("최대 연속 근무", self.max_consecutive_spin)
        rule_form.addRow("최대 연속 GY", self.max_consecutive_gy_spin)

        settings_layout.addWidget(weekday_group)
        settings_layout.addWidget(holiday_group)
        settings_layout.addWidget(rule_group)
        settings_layout.addStretch(1)
        tabs.addTab(settings_tab, "근무 설정")

        splitter.addWidget(tabs)
        splitter.addWidget(self.warning_box)
        splitter.setSizes([650, 160])
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)

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

    def sync_rules_from_widgets(self) -> None:
        self.rules.min_weekday = {
            SHIFT_DAY: self.weekday_day_spin.value(),
            SHIFT_SWING: self.weekday_sw_spin.value(),
            SHIFT_GY: self.weekday_gy_spin.value(),
        }
        self.rules.min_holiday = {
            SHIFT_DAY: self.holiday_day_spin.value(),
            SHIFT_SWING: self.holiday_sw_spin.value(),
            SHIFT_DUTY: self.holiday_gy_spin.value(),
        }
        self.rules.max_consecutive_work_days = self.max_consecutive_spin.value()
        self.rules.max_consecutive_gy = self.max_consecutive_gy_spin.value()

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
            emp = Employee(name=name, employee_id=employee_id, is_new=is_new, unavailable_dates=unavailable)
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
        return QApplication.clipboard().text(), mime.html() if mime and mime.hasHtml() else ""

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
        if event.type() == QEvent.KeyPress and event.matches(QKeySequence.Paste):
            target = self._paste_target_month_from_focus()
            if target is not None:
                year, month = target
                self.paste_schedule_from_clipboard_for_month(year, month)
                return True
        return super().eventFilter(watched, event)

    def paste_schedule_from_clipboard_for_month(self, year: int, month: int) -> None:
        text, html = self._clipboard_text_html()
        if not text.strip() and not html.strip():
            QMessageBox.warning(self, "붙여넣기 실패", "클립보드가 비어 있습니다. 엑셀에서 표 범위를 먼저 복사하세요.")
            return
        self.sync_rules_from_widgets()
        try:
            pasted = parse_schedule_from_clipboard(text, html, year, month, self.rules)
        except Exception as exc:
            QMessageBox.warning(self, "붙여넣기 실패", f"{year}년 {month}월 근무표를 읽지 못했습니다.\n{exc}")
            return

        self.year_spin.setValue(year)
        self.month_spin.setValue(month)
        self.result = pasted
        self.employees = list(pasted.employees)
        self.add_employee_rows(self.employees)
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        self.render_year_overview()
        QMessageBox.information(self, "근무표 반영", f"{year}년 {month}월 표에서 {len(self.employees)}명을 인식했습니다.")

    def paste_schedule_from_clipboard(self) -> None:
        self.paste_schedule_from_clipboard_for_month(self.year_spin.value(), self.month_spin.value())

    def paste_unavailable_from_clipboard(self) -> None:
        text, html = self._clipboard_text_html()
        if not html.strip():
            QMessageBox.warning(self, "붙여넣기 실패", "회색 셀은 텍스트 붙여넣기로 인식할 수 없습니다. 엑셀에서 표 범위를 복사한 뒤 이 버튼을 누르세요.")
            return
        self.employees = self.collect_employees()
        if not self.employees and self.result:
            self.employees = self.result.employees
        if not self.employees:
            QMessageBox.warning(self, "불가일 반영 실패", "먼저 근무표를 붙여넣어 직원 목록을 만든 뒤 회색 불가일을 반영하세요.")
            return
        try:
            unavailable_map = parse_unavailable_from_clipboard(text, html, self.year_spin.value(), self.month_spin.value())
        except Exception as exc:
            QMessageBox.warning(self, "불가일 반영 실패", str(exc))
            return
        hit = 0
        updated = []
        for emp in self.employees:
            dates = unavailable_map.get(emp.key) or unavailable_map.get(emp.employee_id) or unavailable_map.get(emp.name) or set()
            if dates:
                hit += len(dates)
                updated.append(Employee(emp.name, emp.employee_id, emp.is_new, set(emp.unavailable_dates) | set(dates)))
            else:
                updated.append(emp)
        self.employees = updated
        self.add_employee_rows(self.employees)
        if self.result:
            self.result.employees = self.employees
            self.refresh_validation_and_stats()
        QMessageBox.information(self, "불가일 반영", f"회색 셀 불가일 {hit}건을 반영했습니다.")

    def apply_pasted_employees(self) -> None:
        text = self.paste_box.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, "붙여넣기 실패", "붙여넣은 표가 비어 있습니다.")
            return
        self.sync_rules_from_widgets()
        try:
            self.result = parse_schedule_from_tsv(text, self.year_spin.value(), self.month_spin.value(), self.rules)
        except Exception as exc:
            # Fallback: old employee-list-only paste format.
            employees = parse_employees_from_tsv(text)
            if not employees:
                QMessageBox.warning(self, "붙여넣기 실패", f"근무표를 읽지 못했습니다.\n{exc}")
                return
            self.add_employee_rows(employees)
            self.paste_box.clear()
            return
        self.employees = self.result.employees
        self.add_employee_rows(self.employees)
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        self.paste_box.clear()

    def save_current_schedule_to_db(self) -> None:
        if not self.result:
            QMessageBox.warning(self, "DB 저장 불가", "먼저 기존 근무표를 붙여넣거나 자동 생성하세요.")
            return
        self.sync_schedule_from_table()
        self.refresh_validation_and_stats()
        source_name = f"{self.result.year}-{self.result.month:02d}"
        try:
            schedule_id = save_schedule(self.result, source_name)
            save_unavailable_days(self.result.employees, source_name)
        except Exception as exc:
            QMessageBox.critical(self, "DB 저장 실패", str(exc))
            return
        self.render_cumulative_stats()
        self.render_year_overview()
        QMessageBox.information(self, "DB 저장 완료", f"근무표를 DB에 저장했습니다. ID: {schedule_id}")

    def export_excel(self) -> None:
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

    def generate_schedule(self) -> None:
        self.sync_rules_from_widgets()
        self.employees = self.collect_employees()
        if len(self.employees) < 3:
            QMessageBox.warning(self, "생성 불가", "D/S/G 최소 인원을 채우려면 직원이 최소 3명 필요합니다.")
            return
        try:
            self.result = generate_month_schedule(
                self.employees,
                self.year_spin.value(),
                self.month_spin.value(),
                self.rules,
            )
        except ScheduleError as exc:
            QMessageBox.warning(self, "생성 실패", str(exc))
            return
        self.render_schedule_table()
        self.refresh_validation_and_stats()
        self.render_year_overview()

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout(child_layout)  # type: ignore[arg-type]

    def _make_schedule_view_table(self, result: ScheduleResult) -> QTableWidget:
        dates = month_dates(result.year, result.month)
        row_count = max(1, len(result.employees))
        table = MonthRosterTable(self, result.year, result.month, row_count, len(dates) + 2)
        table.setHorizontalHeaderLabels(["성명", "사번"] + [str(d.day) for d in dates])
        table.verticalHeader().setVisible(False)
        for col, d in enumerate(dates, start=2):
            item = table.horizontalHeaderItem(col)
            if item and is_holiday_or_weekend(d, result.holidays):
                item.setBackground(HOLIDAY_HEADER_COLOR)
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
                table.setItem(row, 0, QTableWidgetItem(emp.name))
                table.setItem(row, 1, QTableWidgetItem(emp.employee_id))
                for col, d in enumerate(dates, start=2):
                    shift = result.schedule.get(d, {}).get(emp.key, OFF)
                    cell = QTableWidgetItem(shift)
                    cell.setTextAlignment(Qt.AlignCenter)
                    cell.setBackground(SHIFT_COLORS.get(shift, QColor("#ffffff")))
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                    table.setItem(row, col, cell)
        for row in range(table.rowCount()):
            table.setRowHeight(row, 24)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.setMinimumHeight(min(520, 72 + row_count * 28))
        return table

    def render_year_overview(self) -> None:
        if not hasattr(self, "year_scroll_layout"):
            return
        self._clear_layout(self.year_scroll_layout)
        year = self.year_spin.value()
        for month in range(1, 13):
            loaded = load_schedule_result(year, month)
            if self.result and self.result.year == year and self.result.month == month:
                result = self.result
                status = "현재 편집 중"
            elif loaded:
                result = loaded
                status = "DB 저장됨"
            else:
                employees = []
                schedule = {d: {} for d in month_dates(year, month)}
                result = ScheduleResult(year, month, employees, schedule, korean_holidays(year))
                status = "미저장"
            title = QLabel(f"{year}년 {month}월 · {status}")
            title.setStyleSheet("font-size: 16px; font-weight: 700; margin-top: 14px;")
            self.year_scroll_layout.addWidget(title)
            self.year_scroll_layout.addWidget(self._make_schedule_view_table(result))
        self.year_scroll_layout.addStretch(1)

    def render_schedule_table(self) -> None:
        if not self.result:
            return
        self._updating_table = True
        dates = month_dates(self.result.year, self.result.month)
        self.schedule_table.clear()
        self.schedule_table.setRowCount(len(self.result.employees))
        self.schedule_table.setColumnCount(len(dates) + 2)
        headers = ["성명", "사번"] + [f"{weekday_ko(d)}\n{d.day}" for d in dates]
        self.schedule_table.setHorizontalHeaderLabels(headers)
        for col, d in enumerate(dates, start=2):
            if is_holiday_or_weekend(d, self.result.holidays):
                self.schedule_table.horizontalHeaderItem(col).setBackground(HOLIDAY_HEADER_COLOR)
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
                item.setBackground(SHIFT_COLORS.get(shift, QColor("#ffffff")))
                self.schedule_table.setItem(row, col, item)
        self.schedule_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.schedule_table.verticalHeader().setVisible(False)
        self.schedule_table.freezeColumnCount if hasattr(self.schedule_table, "freezeColumnCount") else None
        self._updating_table = False

    def on_schedule_cell_changed(self, row: int, col: int) -> None:
        if self._updating_table or col < 2 or not self.result:
            return
        item = self.schedule_table.item(row, col)
        if not item:
            return
        text = item.text().strip()
        normalized = self.normalize_shift(text)
        if normalized != text:
            item.setText("" if normalized == OFF else normalized)
        item.setBackground(SHIFT_COLORS.get(normalized, QColor("#ffffff")))
        self.sync_schedule_from_table()
        self.refresh_validation_and_stats()

    @staticmethod
    def normalize_shift(text: str) -> str:
        return normalize_shift_code(text)

    def sync_schedule_from_table(self) -> None:
        if not self.result:
            return
        dates = month_dates(self.result.year, self.result.month)
        for row, emp in enumerate(self.result.employees):
            for col, d in enumerate(dates, start=2):
                item = self.schedule_table.item(row, col)
                shift = self.normalize_shift(item.text() if item else "")
                self.result.schedule[d][emp.key] = shift

    def refresh_validation_and_stats(self) -> None:
        self.sync_rules_from_widgets()
        if not self.result:
            return
        self.result.warnings = validate_schedule(
            self.result.employees,
            self.result.year,
            self.result.month,
            self.result.schedule,
            self.result.holidays,
            self.rules,
        )
        self.render_stats()
        self.render_warnings()
        self.paint_validation_errors()

    def render_stats(self) -> None:
        if not self.result:
            return
        dates = month_dates(self.result.year, self.result.month)
        stats = compute_stats(self.result.employees, dates, self.result.schedule, self.result.holidays)
        avg = averages(stats)
        headers = STAT_HEADERS + ["총근무 평균편차"]
        self.month_stats_table.clear()
        self.month_stats_table.setColumnCount(len(headers))
        self.month_stats_table.setRowCount(len(stats))
        self.month_stats_table.setHorizontalHeaderLabels(headers)
        for row, stat in enumerate(stats.values()):
            values = stat.as_row() + [round(stat.total_work - avg.get("total_work", 0), 2)]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if col == len(headers) - 1 and isinstance(value, (int, float)) and abs(value) >= 2:
                    item.setBackground(WARNING_COLOR)
                self.month_stats_table.setItem(row, col, item)
        self.month_stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.month_stats_table.verticalHeader().setVisible(False)

    def render_cumulative_stats(self) -> None:
        month_rows = saved_months()
        self.saved_months_table.clear()
        self.saved_months_table.setColumnCount(5)
        self.saved_months_table.setHorizontalHeaderLabels(["ID", "연도", "월", "출처", "저장시각"])
        self.saved_months_table.setRowCount(len(month_rows))
        for r, row in enumerate(month_rows):
            for c, key in enumerate(["id", "year", "month", "source_name", "imported_at"]):
                self.saved_months_table.setItem(r, c, QTableWidgetItem(str(row.get(key, ""))))
        self.saved_months_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.saved_months_table.verticalHeader().setVisible(False)

        rows = cumulative_stats()
        headers = ["성명", "사번", "D", "S", "G/지근", "당직", "지휴", "총근무"]
        keys = ["name", "employee_no", "d_count", "s_count", "weekday_gy_count", "duty_count", "gy_rest_count", "total_work"]
        self.cumulative_stats_table.clear()
        self.cumulative_stats_table.setColumnCount(len(headers))
        self.cumulative_stats_table.setHorizontalHeaderLabels(headers)
        self.cumulative_stats_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, key in enumerate(keys):
                self.cumulative_stats_table.setItem(r, c, QTableWidgetItem(str(row.get(key) or 0)))
        self.cumulative_stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.cumulative_stats_table.verticalHeader().setVisible(False)

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
        dates = month_dates(self.result.year, self.result.month)
        employee_by_key = {e.key: e for e in self.result.employees}
        # reset colors
        for row, emp in enumerate(self.result.employees):
            for col, d in enumerate(dates, start=2):
                item = self.schedule_table.item(row, col)
                if not item:
                    continue
                shift = self.normalize_shift(item.text())
                item.setBackground(SHIFT_COLORS.get(shift, QColor("#ffffff")))
                if shift not in (OFF, SHIFT_GY_REST) and d in emp.unavailable_dates:
                    item.setBackground(WARNING_COLOR)


def run() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
