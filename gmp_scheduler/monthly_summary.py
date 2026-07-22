from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional, Set

from .calendar_utils import is_holiday_or_weekend, month_dates
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_SWING, Employee, ScheduleResult


@dataclass(frozen=True)
class MonthlySummaryColumn:
    key: str
    header: str
    short_label: str
    tooltip: str
    shift: str
    day_scope: str


MONTHLY_SUMMARY_COLUMNS = (
    MonthlySummaryColumn("weekday_day", "평일\nD", "평D", "평일 Day 근무 일수", SHIFT_DAY, "weekday"),
    MonthlySummaryColumn("weekday_swing", "평일\nSW", "평SW", "평일 SW 근무 일수", SHIFT_SWING, "weekday"),
    MonthlySummaryColumn("gy", "GY", "GY", "일요일부터 금요일까지 GY 근무 일수", SHIFT_GY, "sunday_to_friday"),
    MonthlySummaryColumn("holiday_day", "주말·휴일\nD", "휴D", "주말 및 휴일 Day 근무 일수", SHIFT_DAY, "holiday_or_weekend"),
    MonthlySummaryColumn("holiday_swing", "주말·휴일\nSW", "휴SW", "주말 및 휴일 SW 근무 일수", SHIFT_SWING, "holiday_or_weekend"),
    MonthlySummaryColumn("saturday_duty", "토당", "토당", "토요일 당직 근무 일수", SHIFT_DUTY, "saturday"),
)

MONTHLY_SUMMARY_BY_KEY = {column.key: column for column in MONTHLY_SUMMARY_COLUMNS}


def monthly_summary_group_header(keys: tuple[str, ...]) -> str:
    if len(keys) == 1:
        return MONTHLY_SUMMARY_BY_KEY[keys[0]].header
    labels = [MONTHLY_SUMMARY_BY_KEY[key].short_label for key in keys]
    return "합계\n" + "+".join(labels)


def monthly_summary_group_tooltip(keys: tuple[str, ...]) -> str:
    return " + ".join(MONTHLY_SUMMARY_BY_KEY[key].tooltip for key in keys)


def monthly_summary_key_for_assignment(work_date: date, shift: str, holidays: Set[date]) -> Optional[str]:
    holiday_or_weekend = is_holiday_or_weekend(work_date, holidays)
    if shift == SHIFT_DAY:
        return "holiday_day" if holiday_or_weekend else "weekday_day"
    if shift == SHIFT_SWING:
        return "holiday_swing" if holiday_or_weekend else "weekday_swing"
    if shift == SHIFT_GY and work_date.weekday() != 5:
        return "gy"
    if shift == SHIFT_DUTY and work_date.weekday() == 5:
        return "saturday_duty"
    return None


def employee_monthly_summary(result: ScheduleResult, employee: Employee) -> Dict[str, int]:
    counts = {column.key: 0 for column in MONTHLY_SUMMARY_COLUMNS}
    for work_date in month_dates(result.year, result.month):
        shift = result.schedule.get(work_date, {}).get(employee.key, OFF)
        key = monthly_summary_key_for_assignment(work_date, shift, result.holidays)
        if key:
            counts[key] += 1
    return counts
