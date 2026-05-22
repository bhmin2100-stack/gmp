from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .calendar_utils import is_holiday_or_weekend, month_dates, weekday_ko
from .models import OFF, SHIFT_DAY, SHIFT_GY, SHIFT_SAT_DUTY, SHIFT_SWING, Employee, ScheduleResult
from .stats import STAT_HEADERS, averages, compute_stats

SHIFT_FILLS = {
    SHIFT_DAY: "FFF2CC",
    SHIFT_SWING: "D9EAD3",
    SHIFT_GY: "D9E2F3",
    SHIFT_SAT_DUTY: "FCE4D6",
    OFF: "FFFFFF",
    "": "FFFFFF",
}


def parse_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_unavailable(text: object) -> Set[date]:
    if not text:
        return set()
    result: Set[date] = set()
    for part in str(text).replace(";", ",").replace(" ", ",").split(","):
        d = parse_date(part.strip())
        if d:
            result.add(d)
    return result


def import_employees_from_excel(path: str | Path) -> List[Employee]:
    try:
        from openpyxl import load_workbook  # type: ignore
    except Exception as exc:
        raise RuntimeError("openpyxl이 필요합니다. `pip install -r requirements.txt`를 실행하세요.") from exc

    wb = load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(v).strip() if v is not None else "" for v in rows[0]]

    def find_col(names: Tuple[str, ...], default: Optional[int] = None) -> Optional[int]:
        for name in names:
            for i, h in enumerate(header):
                if name.lower() == h.lower():
                    return i
        return default

    name_col = find_col(("성명", "이름", "name"), 0)
    id_col = find_col(("사번", "employee_id", "id"), 1)
    new_col = find_col(("신규", "new", "is_new"), None)
    unavailable_col = find_col(("불가일", "근무불가", "unavailable", "unavailable_dates"), None)

    employees: List[Employee] = []
    for row in rows[1:]:
        if name_col is None or name_col >= len(row) or not row[name_col]:
            continue
        name = str(row[name_col]).strip()
        employee_id = str(row[id_col]).strip() if id_col is not None and id_col < len(row) and row[id_col] is not None else ""
        is_new = False
        if new_col is not None and new_col < len(row):
            is_new = str(row[new_col]).strip().lower() in ("y", "yes", "true", "1", "신규", "ㅇ", "o")
        unavailable = set()
        if unavailable_col is not None and unavailable_col < len(row):
            unavailable = parse_unavailable(row[unavailable_col])
        employees.append(Employee(name=name, employee_id=employee_id, is_new=is_new, unavailable_dates=unavailable))
    return employees


def parse_employees_from_tsv(text: str) -> List[Employee]:
    employees: List[Employee] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("\t")]
        if parts[0] in ("성명", "이름", "name"):
            continue
        name = parts[0] if len(parts) >= 1 else ""
        employee_id = parts[1] if len(parts) >= 2 else ""
        is_new = len(parts) >= 3 and parts[2].lower() in ("y", "yes", "true", "1", "신규", "ㅇ", "o")
        unavailable = parse_unavailable(parts[3]) if len(parts) >= 4 else set()
        if name:
            employees.append(Employee(name=name, employee_id=employee_id, is_new=is_new, unavailable_dates=unavailable))
    return employees


def export_schedule_to_excel(result: ScheduleResult, path: str | Path) -> None:
    try:
        from openpyxl import Workbook  # type: ignore
        from openpyxl.styles import Alignment, Font, PatternFill, Border, Side  # type: ignore
        from openpyxl.utils import get_column_letter  # type: ignore
    except Exception as exc:
        raise RuntimeError("openpyxl이 필요합니다. `pip install -r requirements.txt`를 실행하세요.") from exc

    wb = Workbook()
    ws = wb.active
    ws.title = "근무표"
    stats_ws = wb.create_sheet("통계")
    warn_ws = wb.create_sheet("검증")

    dates = month_dates(result.year, result.month)
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.cell(row=1, column=1, value="성명")
    ws.cell(row=1, column=2, value="사번")
    for idx, d in enumerate(dates, start=3):
        c = ws.cell(row=1, column=idx, value=weekday_ko(d))
        c.alignment = Alignment(horizontal="center")
        c.font = Font(bold=True)
        c = ws.cell(row=2, column=idx, value=d.day)
        c.alignment = Alignment(horizontal="center")
        if is_holiday_or_weekend(d, result.holidays):
            c.fill = PatternFill("solid", fgColor="F4CCCC")
    ws.cell(row=2, column=1, value="")
    ws.cell(row=2, column=2, value="")

    for row_idx, emp in enumerate(result.employees, start=3):
        ws.cell(row=row_idx, column=1, value=emp.name)
        ws.cell(row=row_idx, column=2, value=emp.employee_id)
        for col_idx, d in enumerate(dates, start=3):
            shift = result.schedule.get(d, {}).get(emp.key, OFF)
            cell = ws.cell(row=row_idx, column=col_idx, value="" if shift == OFF else shift)
            cell.alignment = Alignment(horizontal="center")
            cell.fill = PatternFill("solid", fgColor=SHIFT_FILLS.get(shift, "FFFFFF"))
            cell.border = border

    for col in range(1, len(dates) + 3):
        ws.column_dimensions[get_column_letter(col)].width = 8 if col >= 3 else 14
    ws.freeze_panes = "C3"

    stats = compute_stats(result.employees, dates, result.schedule, result.holidays)
    avg = averages(stats)
    for col, header in enumerate(STAT_HEADERS, start=1):
        stats_ws.cell(row=1, column=col, value=header).font = Font(bold=True)
    stats_ws.cell(row=1, column=len(STAT_HEADERS) + 1, value="총근무 평균편차").font = Font(bold=True)
    for row_idx, stat in enumerate(stats.values(), start=2):
        for col_idx, value in enumerate(stat.as_row(), start=1):
            stats_ws.cell(row=row_idx, column=col_idx, value=value)
        stats_ws.cell(row=row_idx, column=len(STAT_HEADERS) + 1, value=round(stat.total_work - avg.get("total_work", 0), 2))
    for col in range(1, len(STAT_HEADERS) + 2):
        stats_ws.column_dimensions[get_column_letter(col)].width = 14

    warn_ws.cell(row=1, column=1, value="경고")
    warn_ws.cell(row=1, column=1).font = Font(bold=True)
    if result.warnings:
        for row_idx, warning in enumerate(result.warnings, start=2):
            warn_ws.cell(row=row_idx, column=1, value=warning)
    else:
        warn_ws.cell(row=2, column=1, value="검증 경고 없음")
    warn_ws.column_dimensions["A"].width = 80

    wb.save(path)
