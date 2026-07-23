from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Dict, List, Mapping, Sequence, Set

from .calendar_utils import is_duty_day, month_dates
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleMap, ScheduleResult, ShiftRules
from .pairing import PAIR_CATEGORY_LABELS, PAIR_CATEGORY_ORDER, pair_category, pair_coverage
from .rule_utils import min_rules_for_date
from .stats import compute_stats


def max_consecutive_day_swing(
    employee_key: str,
    dates: List[date],
    schedule: ScheduleMap,
) -> int:
    longest = 0
    current = 0
    for d in dates:
        if schedule.get(d, {}).get(employee_key, OFF) in (SHIFT_DAY, SHIFT_SWING):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def validate_schedule(
    employees: List[Employee],
    year: int,
    month: int,
    schedule: ScheduleMap,
    holidays: Set[date],
    rules: ShiftRules,
) -> List[str]:
    warnings: List[str] = []
    employee_by_key = {e.key: e for e in employees}

    for d in month_dates(year, month):
        actual_counts = Counter(
            shift for shift in schedule.get(d, {}).values()
            if shift and shift not in (OFF, SHIFT_GY_REST)
        )
        counts = Counter(
            shift
            for emp_key, shift in schedule.get(d, {}).items()
            if shift
            and shift not in (OFF, SHIFT_GY_REST)
            and not (employee_by_key.get(emp_key) and employee_by_key[emp_key].pair_required)
        )
        min_rules = min_rules_for_date(rules, d, holidays)
        for shift, minimum in min_rules.items():
            actual = counts[shift]
            if actual < minimum:
                warnings.append(f"{d.isoformat()} {shift} 최소 인원 부족: {actual}/{minimum}")
        if actual_counts[SHIFT_DUTY] and actual_counts[SHIFT_GY]:
            warnings.append(f"{d.isoformat()} 당직일에는 G/지근이 같이 있을 수 없음")
        for shift, actual in actual_counts.items():
            if actual <= 2 or not pair_category(d, shift, holidays):
                continue
            pair_names = [
                employee_by_key[emp_key].name
                for emp_key, current in schedule.get(d, {}).items()
                if current == shift
                and employee_by_key.get(emp_key)
                and employee_by_key[emp_key].pair_required
            ]
            if pair_names:
                warnings.append(
                    f"{d.isoformat()} {shift} 페어 동시근무 2명 초과: 전체 {actual}명 / 페어 {', '.join(pair_names)}"
                )

        for emp_key, shift in schedule.get(d, {}).items():
            emp = employee_by_key.get(emp_key)
            if not emp or not shift or shift in (OFF, SHIFT_GY_REST):
                continue
            if d in emp.unavailable_dates:
                warnings.append(f"{d.isoformat()} {emp.name} 불가일 배정 위반: {shift}")
            if shift == SHIFT_DUTY and not is_duty_day(d):
                warnings.append(f"{d.isoformat()} {emp.name} 당직은 토요일만 배정 가능함")
            if shift == SHIFT_GY and is_duty_day(d):
                warnings.append(f"{d.isoformat()} {emp.name} 토요일 GY는 당직으로 입력해야 함")
            prev_day = d - timedelta(days=1)
            if shift == SHIFT_GY and schedule.get(prev_day, {}).get(emp_key, OFF) == SHIFT_DUTY:
                warnings.append(f"{d.isoformat()} {emp.name} 당직 다음날 같은 사람이 G/지근 시작됨")

    dates = month_dates(year, month)
    stats = compute_stats(employees, dates, schedule, holidays)
    for s in stats.values():
        day_swing_run = max_consecutive_day_swing(s.employee_key, dates, schedule)
        if day_swing_run > rules.max_consecutive_work_days:
            warnings.append(f"{s.name} 최대 연속 Day/SW 초과: {day_swing_run}/{rules.max_consecutive_work_days}")
        if s.max_consecutive_gy > rules.max_consecutive_gy:
            warnings.append(f"{s.name} 연속 GY 초과: {s.max_consecutive_gy}/{rules.max_consecutive_gy}")

    dates_by_week: Dict[date, List[date]] = defaultdict(list)
    for d in month_dates(year, month):
        dates_by_week[d - timedelta(days=d.weekday())].append(d)
    for week_start, week_dates in dates_by_week.items():
        weekly_targets: Dict[str, int] = {}
        weekly_counts: Dict[str, int] = {}
        for emp in employees:
            available_days = sum(1 for d in week_dates if d not in emp.unavailable_dates)
            target = min(rules.min_weekly_work_days, available_days)
            assigned = sum(
                1
                for d in week_dates
                if schedule.get(d, {}).get(emp.key, OFF) not in (OFF, SHIFT_GY_REST)
            )
            weekly_targets[emp.key] = target
            weekly_counts[emp.key] = assigned
        if sum(weekly_counts.values()) < sum(weekly_targets.values()):
            continue
        for emp in employees:
            target = weekly_targets[emp.key]
            assigned = weekly_counts[emp.key]
            if target > 0 and assigned < target:
                warnings.append(
                    f"{week_start.isoformat()} 주 {emp.name} 주간 최소 근무 부족: {assigned}/{target}"
                )

    for emp in employees:
        if not emp.is_new:
            continue
        s = stats[emp.key]
        if (s.weekday_day + s.weekday_swing) == 0:
            warnings.append(f"신규 {emp.name} 평일 D/S 경험 없음")
        if (s.holiday_day + s.holiday_swing) == 0:
            warnings.append(f"신규 {emp.name} 휴일 D/S 경험 없음")
        if s.weekday_gy == 0:
            warnings.append(f"신규 {emp.name} 평일 G/지근 경험 없음")
    for emp in employees:
        if not emp.pair_required:
            continue
        covered = pair_coverage(schedule, employees, emp, dates, holidays)
        missing = [PAIR_CATEGORY_LABELS[key] for key in PAIR_CATEGORY_ORDER if key not in covered]
        if missing:
            warnings.append(f"페어 {emp.name} 미충족: {', '.join(missing)}")
    return warnings


def validate_schedules_by_source(
    results_by_source: Mapping[str, ScheduleResult],
    rules_by_source: Mapping[str, ShiftRules],
    source_order: Sequence[str],
) -> Dict[str, List[str]]:
    warnings_by_source: Dict[str, List[str]] = {}
    for source_name in source_order:
        result = results_by_source.get(source_name)
        rules = rules_by_source.get(source_name)
        if result is None or rules is None:
            continue
        warnings = validate_schedule(
            result.employees,
            result.year,
            result.month,
            result.schedule,
            result.holidays,
            rules,
        )
        result.warnings = warnings
        warnings_by_source[source_name] = warnings
    return warnings_by_source


def format_warning_sections(
    warnings_by_source: Mapping[str, List[str]],
    source_order: Sequence[str],
) -> str:
    sections: List[str] = []
    for source_name in source_order:
        warnings = warnings_by_source.get(source_name, [])
        lines = [f"[{source_name}]"]
        lines.extend(warnings or ["검증 경고 없음"])
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
