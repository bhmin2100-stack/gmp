from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .calendar_utils import is_holiday_or_weekend, korean_holidays, month_dates, weekday_ko
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleMap, ScheduleResult
from .stats import STAT_HEADERS, averages, compute_stats
from .validation import validate_schedule
from .models import ShiftRules

SHIFT_FILLS = {
    SHIFT_DAY: "FFF2CC",
    SHIFT_SWING: "D9EAD3",
    SHIFT_GY: "D9E2F3",
    SHIFT_DUTY: "FCE4D6",
    SHIFT_GY_REST: "E7E6E6",
    OFF: "FFFFFF",
    "": "FFFFFF",
}


def parse_date(value, default_year: Optional[int] = None, default_month: Optional[int] = None) -> Optional[date]:
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
    if default_year and default_month:
        try:
            day = int(text)
            return date(default_year, default_month, day)
        except ValueError:
            pass
    return None


def parse_unavailable(text: object, default_year: Optional[int] = None, default_month: Optional[int] = None) -> Set[date]:
    if not text:
        return set()
    result: Set[date] = set()
    for part in str(text).replace(";", ",").replace(" ", ",").split(","):
        d = parse_date(part.strip(), default_year, default_month)
        if d:
            result.add(d)
    return result


def normalize_shift_code(value: object, work_date: Optional[date] = None, holidays: Optional[Set[date]] = None) -> str:
    text = "" if value is None else str(value).strip()
    text = text.replace(" ", "")
    if not text:
        return OFF
    upper = text.upper()
    if upper in ("D", "DAY", "데이"):
        return SHIFT_DAY
    if upper in ("S", "SW", "SWING", "스윙"):
        return SHIFT_SWING
    if upper in ("G/지근", "G", "GY") or text in ("G/지근", "G/地勤", "지근", "야간"):
        if work_date and holidays and is_holiday_or_weekend(work_date, holidays):
            return SHIFT_DUTY
        return SHIFT_GY
    if text in ("당직", "주말당직"):
        return SHIFT_DUTY
    if text in ("지휴", "GY휴", "G휴", "야휴"):
        return SHIFT_GY_REST
    if text in ("휴", "휴무", "OFF", "오프", "-", "."):
        return OFF
    return text


def _header_day(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.day
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"\d{1,2}", text)
    if not match:
        return None
    day = int(match.group())
    if 1 <= day <= 31:
        return day
    return None


def parse_schedule_from_tsv(text: str, year: int, month: int, rules: Optional[ShiftRules] = None) -> ScheduleResult:
    rows = [[cell.strip() for cell in line.split("\t")] for line in text.splitlines() if line.strip()]
    if not rows:
        raise ValueError("붙여넣은 표가 비어 있습니다.")

    header_index = 0
    name_col = 0
    id_col = 1
    day_cols: Dict[int, int] = {}

    for idx, row in enumerate(rows[:5]):
        lowered = [c.replace(" ", "").lower() for c in row]
        if any(c in ("성명", "이름", "name") for c in lowered):
            header_index = idx
            for col, cell in enumerate(lowered):
                if cell in ("성명", "이름", "name"):
                    name_col = col
                if cell in ("사번", "직번", "id", "employeeid", "employee_id"):
                    id_col = col
            break

    header = rows[header_index]
    valid_dates = {d.day: d for d in month_dates(year, month)}
    for col, cell in enumerate(header):
        day = _header_day(cell)
        if day in valid_dates:
            day_cols[day] = col

    if not day_cols:
        # Header might be omitted; assume columns after 성명/사번 are 1..말일.
        for offset, d in enumerate(month_dates(year, month), start=2):
            day_cols[d.day] = offset

    holidays = korean_holidays(year)
    employees: List[Employee] = []
    schedule: ScheduleMap = {d: {} for d in month_dates(year, month)}

    for row in rows[header_index + 1:]:
        if name_col >= len(row) or not row[name_col].strip():
            continue
        name = row[name_col].strip()
        employee_id = row[id_col].strip() if id_col < len(row) else ""
        emp = Employee(name=name, employee_id=employee_id)
        employees.append(emp)
        for d in month_dates(year, month):
            col = day_cols.get(d.day)
            raw = row[col] if col is not None and col < len(row) else ""
            schedule[d][emp.key] = normalize_shift_code(raw, d, holidays)

    if not employees:
        raise ValueError("표에서 직원 행을 찾지 못했습니다.")

    result = ScheduleResult(year=year, month=month, employees=employees, schedule=schedule, holidays=holidays)
    result.warnings = validate_schedule(employees, year, month, schedule, holidays, rules or ShiftRules())
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

    for row_idx, emp in enumerate(result.employees, start=3):
        ws.cell(row=row_idx, column=1, value=emp.name)
        ws.cell(row=row_idx, column=2, value=emp.employee_id)
        for col_idx, d in enumerate(dates, start=3):
            shift = result.schedule.get(d, {}).get(emp.key, OFF)
            cell = ws.cell(row=row_idx, column=col_idx, value=shift)
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
