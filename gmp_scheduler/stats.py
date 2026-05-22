from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Set

from .calendar_utils import is_holiday_or_weekend
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleMap


@dataclass
class EmployeeStats:
    employee_key: str
    name: str
    employee_id: str
    day: int = 0
    swing: int = 0
    gy: int = 0
    weekday_day: int = 0
    weekday_swing: int = 0
    weekday_gy: int = 0
    holiday_day: int = 0
    holiday_swing: int = 0
    holiday_gy: int = 0
    duty: int = 0
    gy_rest: int = 0
    weekend_work: int = 0
    off: int = 0
    total_work: int = 0
    max_consecutive_work: int = 0
    max_consecutive_gy: int = 0

    def as_row(self) -> List[object]:
        return [
            self.name,
            self.employee_id,
            self.day,
            self.swing,
            self.gy,
            self.weekday_day,
            self.weekday_swing,
            self.weekday_gy,
            self.holiday_day,
            self.holiday_swing,
            self.holiday_gy,
            self.duty,
            self.gy_rest,
            self.off,
            self.total_work,
            self.max_consecutive_work,
            self.max_consecutive_gy,
        ]


STAT_HEADERS = [
    "성명",
    "사번",
    "D",
    "S",
    "GY",
    "평일 D",
    "평일 S",
    "평일 G/지근",
    "휴일 D",
    "휴일 S",
    "휴일 GY",
    "당직",
    "지휴",
    "빈칸/휴무",
    "총근무",
    "최대연속근무",
    "최대연속GY",
]


def is_work_shift(shift: str) -> bool:
    return shift in (SHIFT_DAY, SHIFT_SWING, SHIFT_GY, SHIFT_DUTY)


def is_gy_shift(shift: str) -> bool:
    return shift in (SHIFT_GY, SHIFT_DUTY)


def compute_stats(employees: List[Employee], dates: Iterable[date], schedule: ScheduleMap, holidays: Set[date]) -> Dict[str, EmployeeStats]:
    stats = {
        e.key: EmployeeStats(employee_key=e.key, name=e.name, employee_id=e.employee_id)
        for e in employees
    }
    date_list = list(dates)
    for e in employees:
        consecutive_work = 0
        consecutive_gy = 0
        for d in date_list:
            shift = schedule.get(d, {}).get(e.key, OFF)
            if not shift or shift == OFF or shift == SHIFT_GY_REST:
                if shift == SHIFT_GY_REST:
                    stats[e.key].gy_rest += 1
                else:
                    stats[e.key].off += 1
                consecutive_work = 0
                consecutive_gy = 0
                continue

            is_holiday = is_holiday_or_weekend(d, holidays)
            stats[e.key].total_work += 1
            consecutive_work += 1
            stats[e.key].max_consecutive_work = max(stats[e.key].max_consecutive_work, consecutive_work)

            if shift == SHIFT_DAY:
                stats[e.key].day += 1
                if is_holiday:
                    stats[e.key].holiday_day += 1
                else:
                    stats[e.key].weekday_day += 1
            elif shift == SHIFT_SWING:
                stats[e.key].swing += 1
                if is_holiday:
                    stats[e.key].holiday_swing += 1
                else:
                    stats[e.key].weekday_swing += 1
            elif shift in (SHIFT_GY, SHIFT_DUTY):
                stats[e.key].gy += 1
                consecutive_gy += 1
                stats[e.key].max_consecutive_gy = max(stats[e.key].max_consecutive_gy, consecutive_gy)
                if shift == SHIFT_DUTY:
                    stats[e.key].duty += 1
                    stats[e.key].holiday_gy += 1
                elif is_holiday:
                    stats[e.key].holiday_gy += 1
                else:
                    stats[e.key].weekday_gy += 1

            if shift not in (SHIFT_GY, SHIFT_DUTY):
                consecutive_gy = 0
            if is_holiday:
                stats[e.key].weekend_work += 1
    return stats


def averages(stats: Dict[str, EmployeeStats]) -> Dict[str, float]:
    if not stats:
        return {}
    keys = [
        "day",
        "swing",
        "gy",
        "weekday_day",
        "weekday_swing",
        "weekday_gy",
        "holiday_day",
        "holiday_swing",
        "holiday_gy",
        "weekend_work",
        "duty",
        "gy_rest",
        "off",
        "total_work",
    ]
    result = {}
    for key in keys:
        result[key] = sum(getattr(s, key) for s in stats.values()) / len(stats)
    return result
