from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
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

from .calendar_utils import is_holiday_or_weekend, month_dates, weekday_ko
from .excel_io import export_schedule_to_excel, import_employees_from_excel, normalize_shift_code, parse_employees_from_tsv, parse_schedule_from_tsv, parse_unavailable
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GMP 근무표 자동 생성기")
        self.resize(1400, 850)

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

        self.employee_table = PasteTableWidget(0, 4, allow_expand=True)
        self.employee_table.setHorizontalHeaderLabels(["성명", "사번", "신규", "불가일(YYYY-MM-DD, ...)"])
        self.employee_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self.paste_box = QTextEdit()
        self.paste_box.setPlaceholderText("기존 월 근무표 전체를 붙여넣으세요. 예: 성명\t사번\t1\t2\t3... / 값: S, D, 당직, 지휴, G/지근")
        self.paste_box.setMaximumHeight(100)

        self.schedule_table = PasteTableWidget(0, 0, allow_expand=False)
        self.schedule_table.cellChanged.connect(self.on_schedule_cell_changed)

        self.stats_table = QTableWidget()
        self.warning_box = QTextEdit()
        self.warning_box.setReadOnly(True)

        self._build_ui()
        self.add_employee_rows([
            Employee("홍길동", "1001"),
            Employee("김철수", "1002"),
            Employee("이영희", "1003"),
            Employee("박민수", "1004", is_new=True),
            Employee("최지은", "1005"),
            Employee("정도윤", "1006"),
        ])

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
        import_action = QAction("엑셀 불러오기", self)
        import_action.triggered.connect(self.import_excel)
        export_action = QAction("엑셀 저장", self)
        export_action.triggered.connect(self.export_excel)
        toolbar.addAction(import_action)
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
        add_btn = QPushButton("직원 행 추가")
        add_btn.clicked.connect(lambda: self.employee_table.insertRow(self.employee_table.rowCount()))
        paste_btn = QPushButton("기존 근무표 붙여넣기 반영")
        paste_btn.clicked.connect(self.apply_pasted_employees)
        top.addWidget(generate_btn)
        top.addWidget(validate_btn)
        top.addWidget(add_btn)
        top.addWidget(paste_btn)
        top.addStretch(1)
        root_layout.addLayout(top)

        splitter = QSplitter(Qt.Vertical)
        tabs = QTabWidget()

        employee_tab = QWidget()
        employee_layout = QVBoxLayout(employee_tab)
        employee_layout.addWidget(QLabel("기존 엑셀 근무표를 아래 박스에 통째로 붙여넣고 반영하세요. 형식: 성명/사번/1일~말일, 값: S, D, 당직, 지휴, G/지근"))
        employee_layout.addWidget(self.employee_table)
        employee_layout.addWidget(self.paste_box)
        tabs.addTab(employee_tab, "직원 관리")

        schedule_tab = QWidget()
        schedule_layout = QVBoxLayout(schedule_tab)
        schedule_layout.addWidget(QLabel("근무표: 셀을 직접 수정할 수 있습니다. 사용 코드: D, S, G/지근, 당직, 지휴, 빈칸"))
        schedule_layout.addWidget(self.schedule_table)
        tabs.addTab(schedule_tab, "월간 근무표")

        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        stats_layout.addWidget(self.stats_table)
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

    def import_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "직원 엑셀 불러오기", "", "Excel Files (*.xlsx *.xlsm)")
        if not path:
            return
        try:
            employees = import_employees_from_excel(path)
        except Exception as exc:
            QMessageBox.critical(self, "불러오기 실패", str(exc))
            return
        if not employees:
            QMessageBox.warning(self, "불러오기 실패", "직원 데이터를 찾지 못했습니다. 헤더는 성명/사번/신규/불가일을 권장합니다.")
            return
        self.add_employee_rows(employees)

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
        self.stats_table.clear()
        self.stats_table.setColumnCount(len(headers))
        self.stats_table.setRowCount(len(stats))
        self.stats_table.setHorizontalHeaderLabels(headers)
        for row, stat in enumerate(stats.values()):
            values = stat.as_row() + [round(stat.total_work - avg.get("total_work", 0), 2)]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if col == len(headers) - 1 and isinstance(value, (int, float)) and abs(value) >= 2:
                    item.setBackground(WARNING_COLOR)
                self.stats_table.setItem(row, col, item)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.stats_table.verticalHeader().setVisible(False)

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
