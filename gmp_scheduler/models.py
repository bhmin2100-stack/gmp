from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Set


SHIFT_DAY = "D"
SHIFT_SWING = "S"
SHIFT_GY = "G/지근"
SHIFT_DUTY = "당직"
SHIFT_GY_REST = "지휴"
OFF = ""

WORK_SHIFTS = (SHIFT_DAY, SHIFT_SWING, SHIFT_GY, SHIFT_DUTY)
REST_SHIFTS = (SHIFT_GY_REST,)
ALL_SHIFTS = (SHIFT_DAY, SHIFT_SWING, SHIFT_GY, SHIFT_DUTY, SHIFT_GY_REST)
DAY_TYPE_WEEKDAY = "weekday"
DAY_TYPE_FAMILY = "family"
DAY_TYPE_PUBLIC_HOLIDAY = "public_holiday"
DAY_TYPE_SATURDAY = "saturday"
DAY_TYPE_SUNDAY = "sunday"
DAY_TYPE_ORDER = (
    DAY_TYPE_WEEKDAY,
    DAY_TYPE_FAMILY,
    DAY_TYPE_PUBLIC_HOLIDAY,
    DAY_TYPE_SATURDAY,
    DAY_TYPE_SUNDAY,
)
DAY_TYPE_LABELS = {
    DAY_TYPE_WEEKDAY: "평일",
    DAY_TYPE_FAMILY: "페데",
    DAY_TYPE_PUBLIC_HOLIDAY: "공휴일",
    DAY_TYPE_SATURDAY: "토요일",
    DAY_TYPE_SUNDAY: "일요일",
}


@dataclass(frozen=True)
class Employee:
    name: str
    employee_id: str = ""
    is_new: bool = False
    unavailable_dates: Set[date] = field(default_factory=set)
    module: str = ""
    day_only: bool = False

    @property
    def key(self) -> str:
        return f"{self.name}|{self.employee_id}"


@dataclass
class ShiftRules:
    min_weekday: Dict[str, int] = field(default_factory=lambda: {
        SHIFT_DAY: 1,
        SHIFT_SWING: 1,
        SHIFT_GY: 1,
    })
    min_holiday: Dict[str, int] = field(default_factory=lambda: {
        SHIFT_DAY: 1,
        SHIFT_SWING: 1,
        SHIFT_DUTY: 1,
    })
    min_by_day_type: Dict[str, Dict[str, int]] = field(default_factory=lambda: {
        DAY_TYPE_WEEKDAY: {
            SHIFT_DAY: 1,
            SHIFT_SWING: 1,
            SHIFT_GY: 1,
        },
        DAY_TYPE_FAMILY: {
            SHIFT_DAY: 1,
            SHIFT_SWING: 1,
            SHIFT_GY: 1,
        },
        DAY_TYPE_PUBLIC_HOLIDAY: {
            SHIFT_DAY: 1,
            SHIFT_SWING: 1,
            SHIFT_GY: 1,
        },
        DAY_TYPE_SATURDAY: {
            SHIFT_DAY: 1,
            SHIFT_SWING: 1,
            SHIFT_DUTY: 1,
        },
        DAY_TYPE_SUNDAY: {
            SHIFT_DAY: 1,
            SHIFT_SWING: 1,
            SHIFT_GY: 1,
        },
    })
    max_consecutive_work_days: int = 5
    max_consecutive_gy: int = 6
    min_weekly_work_days: int = 2
    allow_same_day_multiple_shift: bool = False
    module_weights: Dict[str, Dict[str, int]] = field(default_factory=dict)


@dataclass
class Assignment:
    work_date: date
    employee_key: str
    shift: str


ScheduleMap = Dict[date, Dict[str, str]]


@dataclass
class ScheduleResult:
    year: int
    month: int
    employees: List[Employee]
    schedule: ScheduleMap
    holidays: Set[date]
    warnings: List[str] = field(default_factory=list)
    source_name: str = ""
