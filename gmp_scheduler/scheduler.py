from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

from .calendar_utils import is_duty_day, is_holiday_or_weekend, korean_holidays, month_dates
from .holiday_balance import holiday_run_positions
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleMap, ScheduleResult, ShiftRules
from .pairing import PAIR_CATEGORY_ORDER, pair_category, pair_coverage
from .rule_utils import day_shift_key_for_date, min_rules_for_date
from .schedule_utils import GY_BLOCK_DAYS
from .validation import validate_schedule


class ScheduleError(RuntimeError):
    pass


def generate_month_schedule(
    employees: List[Employee],
    year: int,
    month: int,
    rules: Optional[ShiftRules] = None,
    seed: Optional[int] = None,
    previous_day_duty_employee_keys: Optional[Set[str]] = None,
    initial_schedule: Optional[ScheduleMap] = None,
) -> ScheduleResult:
    if not employees:
        raise ScheduleError("직원이 없습니다.")
    rules = rules or ShiftRules()
    rng = random.Random(seed)
    dates = month_dates(year, month)
    holidays = korean_holidays(year)
    holiday_positions = holiday_run_positions(dates, holidays)
    schedule: ScheduleMap = {d: {e.key: OFF for e in employees} for d in dates}
    employee_by_key = {e.key: e for e in employees}
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    projected_rest_by_module: Dict[tuple[str, date], int] = defaultdict(int)
    previous_day_duty_employee_keys = previous_day_duty_employee_keys or set()
    week_dates: Dict[date, List[date]] = defaultdict(list)
    for d in dates:
        week_dates[d - timedelta(days=d.weekday())].append(d)

    def bucket(d: date, shift: str) -> str:
        prefix = "holiday" if is_holiday_or_weekend(d, holidays) else "weekday"
        if shift == SHIFT_DUTY:
            return "holiday_GY"
        return f"{prefix}_{shift}"

    def week_bucket(d: date) -> str:
        start = d - timedelta(days=d.weekday())
        return f"week_{start.isoformat()}"

    def weekly_work_target(emp: Employee, d: date) -> int:
        week_start = d - timedelta(days=d.weekday())
        available_days = sum(1 for cur in week_dates[week_start] if cur not in emp.unavailable_dates)
        return min(rules.min_weekly_work_days, available_days)

    def prev_shift(emp: Employee, d: date) -> str:
        return schedule.get(d - timedelta(days=1), {}).get(emp.key, OFF)

    def consecutive_work_before(emp: Employee, d: date) -> int:
        n = 0
        cur = d - timedelta(days=1)
        while cur in schedule and schedule[cur].get(emp.key, OFF) not in (OFF, SHIFT_GY_REST):
            n += 1
            cur -= timedelta(days=1)
        return n

    def consecutive_gy_before(emp: Employee, d: date) -> int:
        n = 0
        cur = d - timedelta(days=1)
        while cur in schedule and schedule[cur].get(emp.key, OFF) in (SHIFT_GY, SHIFT_DUTY):
            n += 1
            cur -= timedelta(days=1)
        return n

    def training_bonus(emp: Employee, d: date, shift: str) -> int:
        if not emp.is_new:
            return 0
        b = bucket(d, shift)
        if b in ("weekday_D", "weekday_S"):
            if counts[emp.key]["weekday_D"] + counts[emp.key]["weekday_S"] == 0:
                return -12
        if b in ("holiday_D", "holiday_S"):
            if counts[emp.key]["holiday_D"] + counts[emp.key]["holiday_S"] == 0:
                return -12
        if b == "weekday_G/지근" and counts[emp.key][b] == 0:
            return -14
        return 0

    def module_weight_percent(emp: Employee, d: date, shift: str) -> int:
        if not emp.module:
            return 0
        try:
            return max(0, int(rules.module_weights.get(emp.module, {}).get(day_shift_key_for_date(d, holidays, shift), 0)))
        except (TypeError, ValueError):
            return 0

    def weighted_count(raw_count: int, percent: int) -> float:
        if percent <= 0:
            return float(raw_count)
        return raw_count / (1.0 + percent / 100.0)

    def candidate_score(emp: Employee, d: date, shift: str) -> Tuple[float, float]:
        b = bucket(d, shift)
        weight_percent = module_weight_percent(emp, d, shift)
        score = 0.0
        score += weighted_count(counts[emp.key][b], weight_percent) * 18
        score += weighted_count(counts[emp.key][shift], weight_percent) * 5
        score += counts[emp.key]["total"] * 3.5
        score += counts[emp.key]["holiday_work"] * 10
        score -= weight_percent * 0.16
        holiday_position = holiday_positions.get(d)
        if holiday_position == "middle":
            score += counts[emp.key]["long_holiday_middle"] * 220
            score += counts[emp.key][f"long_holiday_middle_{shift}"] * 90
        elif holiday_position == "edge":
            score += counts[emp.key]["long_holiday_edge"] * 35
            score += counts[emp.key][f"long_holiday_edge_{shift}"] * 20
        if shift in (SHIFT_GY, SHIFT_DUTY):
            score += projected_module_rest_conflicts(emp, d, shift) * 120
        weekly_count = counts[emp.key][week_bucket(d)]
        weekly_target = weekly_work_target(emp, d)
        if weekly_count < weekly_target:
            score -= (weekly_target - weekly_count) * 80
        else:
            score += (weekly_count - weekly_target + 1) * 40

        cw = consecutive_work_before(emp, d)
        cgy = consecutive_gy_before(emp, d)
        if cw >= rules.max_consecutive_work_days:
            score += 1000
        else:
            score += cw * 5
        if shift in (SHIFT_GY, SHIFT_DUTY):
            if cgy >= rules.max_consecutive_gy:
                score += 1000
            else:
                score += cgy * 40
        elif prev_shift(emp, d) in (SHIFT_GY, SHIFT_DUTY):
            score += 30

        if d in emp.unavailable_dates:
            score += 100000
        score += training_bonus(emp, d, shift)
        score += rng.random()
        return score, rng.random()

    def mark_assignment(emp: Employee, d: date, shift: str) -> None:
        schedule[d][emp.key] = shift
        counts[emp.key][shift] += 1
        counts[emp.key][bucket(d, shift)] += 1
        counts[emp.key][week_bucket(d)] += 1
        counts[emp.key]["total"] += 1
        if is_holiday_or_weekend(d, holidays):
            counts[emp.key]["holiday_work"] += 1
        holiday_position = holiday_positions.get(d)
        if holiday_position:
            counts[emp.key][f"long_holiday_{holiday_position}"] += 1
            counts[emp.key][f"long_holiday_{holiday_position}_{shift}"] += 1

    if initial_schedule:
        for d, day_map in initial_schedule.items():
            if d not in schedule:
                continue
            for emp_key, shift in day_map.items():
                if shift in (OFF, SHIFT_GY_REST, ""):
                    continue
                emp = employee_by_key.get(emp_key)
                if emp is None:
                    continue
                mark_assignment(emp, d, shift)

    def assign_one(d: date, shift: str) -> bool:
        candidates = [
            e for e in employees
            if schedule[d].get(e.key, OFF) == OFF
            and d not in e.unavailable_dates
            and not e.pair_required
            and (not e.day_only or shift == SHIFT_DAY)
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda e: candidate_score(e, d, shift))
        chosen = candidates[0]
        mark_assignment(chosen, d, shift)
        mark_projected_rest(chosen, d, shift)
        return True

    def assigned_count(d: date, shift: str) -> int:
        return sum(
            1
            for emp_key, current in schedule.get(d, {}).items()
            if current == shift and not (employee_by_key.get(emp_key) and employee_by_key[emp_key].pair_required)
        )

    def can_start_gy_block(emp: Employee, start: date) -> bool:
        if emp.day_only or emp.pair_required:
            return False
        for d in gy_block_dates(start):
            if d in emp.unavailable_dates:
                return False
            if schedule[d].get(emp.key, OFF) != OFF:
                return False
        return True

    def gy_block_dates(start: date) -> List[date]:
        result: List[date] = []
        for offset in range(GY_BLOCK_DAYS):
            d = start + timedelta(days=offset)
            if d not in schedule:
                continue
            if is_duty_day(d):
                break
            result.append(d)
        return result

    def projected_rest_dates(start: date, shift: str) -> List[date]:
        if shift == SHIFT_DUTY:
            rest_date = start + timedelta(days=1)
            return [rest_date] if rest_date in schedule else []
        if shift != SHIFT_GY:
            return []
        block = gy_block_dates(start)
        if not block:
            return []
        rest_date = block[-1] + timedelta(days=1)
        return [rest_date] if rest_date in schedule else []

    def projected_module_rest_conflicts(emp: Employee, start: date, shift: str) -> int:
        if not emp.module:
            return 0
        conflicts = 0
        for rest_date in projected_rest_dates(start, shift):
            conflicts += projected_rest_by_module[(emp.module, rest_date)]
            conflicts += sum(
                1
                for other in employees
                if other.key != emp.key
                and other.module == emp.module
                and schedule.get(rest_date, {}).get(other.key, OFF) == SHIFT_GY_REST
            )
        return conflicts

    def mark_projected_rest(emp: Employee, start: date, shift: str) -> None:
        if not emp.module:
            return
        for rest_date in projected_rest_dates(start, shift):
            projected_rest_by_module[(emp.module, rest_date)] += 1

    def mark_initial_projected_rests() -> None:
        for emp in employees:
            if not emp.module:
                continue
            for d in dates:
                shift = schedule.get(d, {}).get(emp.key, OFF)
                if shift == SHIFT_DUTY:
                    mark_projected_rest(emp, d, SHIFT_DUTY)
                elif shift == SHIFT_GY and schedule.get(d - timedelta(days=1), {}).get(emp.key, OFF) != SHIFT_GY:
                    mark_projected_rest(emp, d, SHIFT_GY)

    mark_initial_projected_rests()

    def assign_gy_block_start(d: date) -> bool:
        blocked_keys = {
            emp.key
            for emp in employees
            if schedule.get(d - timedelta(days=1), {}).get(emp.key, OFF) == SHIFT_DUTY
        }
        if d == dates[0]:
            blocked_keys |= previous_day_duty_employee_keys
        candidates = [e for e in employees if e.key not in blocked_keys and can_start_gy_block(e, d)]
        if not candidates:
            return False
        candidates.sort(key=lambda e: sum(candidate_score(e, cur, SHIFT_GY)[0] for cur in gy_block_dates(d)) + rng.random())
        chosen = candidates[0]
        for cur in gy_block_dates(d):
            mark_assignment(chosen, cur, SHIFT_GY)
        mark_projected_rest(chosen, d, SHIFT_GY)
        return True

    for d in dates:
        min_rules = min_rules_for_date(rules, d, holidays)
        day_swing_order = [shift for shift in (SHIFT_DAY, SHIFT_SWING) if shift in min_rules]
        for shift in day_swing_order:
            for _ in range(max(0, min_rules.get(shift, 0) - assigned_count(d, shift))):
                assign_one(d, shift)
        gy_shift = SHIFT_DUTY if is_duty_day(d) else SHIFT_GY
        required_gy = min_rules.get(gy_shift, 1)
        if not is_duty_day(d):
            while assigned_count(d, gy_shift) < required_gy:
                if not assign_gy_block_start(d):
                    break
        for _ in range(max(0, required_gy - assigned_count(d, gy_shift))):
            assign_one(d, gy_shift)

    def shift_allowed_for_pair(emp: Employee, shift: str) -> bool:
        return not emp.day_only or shift == SHIFT_DAY

    def mentor_gy_block_dates(mentor: Employee, start: date) -> List[date]:
        block: List[date] = []
        cur = start
        while cur in schedule and schedule[cur].get(mentor.key, OFF) == SHIFT_GY:
            block.append(cur)
            cur += timedelta(days=1)
        return block

    def candidate_pair_block(emp: Employee, mentor: Employee, d: date, shift: str) -> Optional[List[date]]:
        if not shift_allowed_for_pair(emp, shift):
            return None
        block = [d]
        if shift == SHIFT_GY:
            if schedule.get(d - timedelta(days=1), {}).get(mentor.key, OFF) == SHIFT_GY:
                return None
            block = mentor_gy_block_dates(mentor, d)
        for cur in block:
            if cur in emp.unavailable_dates:
                return None
            if schedule.get(cur, {}).get(emp.key, OFF) != OFF:
                return None
        return block

    def assign_pair_category(emp: Employee, category: str) -> bool:
        candidates: List[tuple[float, date, str, List[date]]] = []
        for d in dates:
            for mentor in employees:
                if mentor.key == emp.key or mentor.pair_required:
                    continue
                shift = schedule.get(d, {}).get(mentor.key, OFF)
                if pair_category(d, shift, holidays) != category:
                    continue
                block = candidate_pair_block(emp, mentor, d, shift)
                if not block:
                    continue
                penalty = counts[emp.key]["total"] * 20 + counts[emp.key][week_bucket(d)] * 10 + len(block)
                candidates.append((penalty, d, shift, block))
        if not candidates:
            return False
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        _, _, shift, block = candidates[0]
        for cur in block:
            mark_assignment(emp, cur, shift)
        return True

    for emp in employees:
        if not emp.pair_required:
            continue
        covered = pair_coverage(schedule, employees, emp, dates, holidays)
        for category in PAIR_CATEGORY_ORDER:
            if category in covered:
                continue
            if assign_pair_category(emp, category):
                covered = pair_coverage(schedule, employees, emp, dates, holidays)

    result = ScheduleResult(year=year, month=month, employees=employees, schedule=schedule, holidays=holidays)
    result.warnings = validate_schedule(employees, year, month, schedule, holidays, rules)
    return result
