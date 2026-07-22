from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Mapping, Optional, Set, Tuple

from .calendar_utils import is_duty_day, is_holiday_or_weekend, korean_holidays, month_dates
from .holiday_balance import holiday_run_positions
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleMap, ScheduleResult, ShiftRules
from .monthly_summary import MONTHLY_SUMMARY_COLUMNS, employee_monthly_summary, monthly_summary_key_for_assignment
from .pairing import PAIR_CATEGORY_ORDER, pair_category, pair_coverage
from .rule_utils import day_shift_key_for_date, min_rules_for_date
from .schedule_utils import GY_BLOCK_DAYS, next_workday_after
from .validation import validate_schedule


class ScheduleError(RuntimeError):
    pass


def _generate_month_schedule_once(
    employees: List[Employee],
    year: int,
    month: int,
    rules: Optional[ShiftRules] = None,
    seed: Optional[int] = None,
    previous_day_duty_employee_keys: Optional[Set[str]] = None,
    initial_schedule: Optional[ScheduleMap] = None,
    historical_summary_counts: Optional[Mapping[str, Mapping[str, int]]] = None,
) -> ScheduleResult:
    if not employees:
        raise ScheduleError("직원이 없습니다.")
    rules = rules or ShiftRules()
    rng = random.Random(seed)
    dates = month_dates(year, month)
    date_set = set(dates)
    holidays = korean_holidays(year)
    holiday_positions = holiday_run_positions(dates, holidays)
    schedule: ScheduleMap = {d: {e.key: OFF for e in employees} for d in dates}
    employee_by_key = {e.key: e for e in employees}
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    projected_rest_by_module: Dict[tuple[str, date], int] = defaultdict(int)
    previous_day_duty_employee_keys = previous_day_duty_employee_keys or set()
    historical_summary_counts = historical_summary_counts or {}
    week_dates: Dict[date, List[date]] = defaultdict(list)
    for d in dates:
        week_dates[d - timedelta(days=d.weekday())].append(d)

    def bucket(d: date, shift: str) -> str:
        return monthly_summary_key_for_assignment(d, shift, holidays) or f"other_{shift}"

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
        if b in ("weekday_day", "weekday_swing"):
            if counts[emp.key]["weekday_day"] + counts[emp.key]["weekday_swing"] == 0:
                return -12
        if b in ("holiday_day", "holiday_swing"):
            if counts[emp.key]["holiday_day"] + counts[emp.key]["holiday_swing"] == 0:
                return -12
        if b == "gy" and counts[emp.key][b] == 0:
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

    def fairness_rank(emp: Employee, d: date, shift: str) -> Tuple[int, int, int, int, float, float]:
        summary_key = bucket(d, shift)
        history = historical_summary_counts.get(emp.key, {})
        score, tie_breaker = candidate_score(emp, d, shift)
        return (
            counts[emp.key][summary_key],
            counts[emp.key]["total"],
            int(history.get(summary_key, 0)),
            int(history.get("total", 0)),
            score,
            tie_breaker,
        )

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
            and can_reserve_projected_rest(e, d, shift)
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda e: fairness_rank(e, d, shift))
        chosen = candidates[0]
        mark_assignment(chosen, d, shift)
        reserve_projected_rest(chosen, d, shift)
        mark_projected_rest(chosen, d, shift)
        return True

    def assigned_count(d: date, shift: str) -> int:
        return sum(
            1
            for emp_key, current in schedule.get(d, {}).items()
            if current == shift and not (employee_by_key.get(emp_key) and employee_by_key[emp_key].pair_required)
        )

    def shift_group_size(d: date, shift: str) -> int:
        return sum(1 for current in schedule.get(d, {}).values() if current == shift)

    def can_start_gy_block(emp: Employee, start: date) -> bool:
        if emp.day_only or emp.pair_required:
            return False
        for d in gy_block_dates(start):
            if d in emp.unavailable_dates:
                return False
            if schedule[d].get(emp.key, OFF) != OFF:
                return False
        return can_reserve_projected_rest(emp, start, SHIFT_GY)

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
            rest_date = next_workday_after(start, date_set, holidays)
            return [rest_date] if rest_date in schedule else []
        if shift != SHIFT_GY:
            return []
        block = gy_block_dates(start)
        if not block:
            return []
        rest_date = next_workday_after(block[-1], date_set, holidays)
        return [rest_date] if rest_date in schedule else []

    def can_reserve_projected_rest(emp: Employee, start: date, shift: str) -> bool:
        for rest_date in projected_rest_dates(start, shift):
            current = schedule.get(rest_date, {}).get(emp.key, OFF)
            if current not in (OFF, SHIFT_GY_REST, ""):
                return False
        return True

    def reserve_projected_rest(emp: Employee, start: date, shift: str) -> None:
        for rest_date in projected_rest_dates(start, shift):
            current = schedule.get(rest_date, {}).get(emp.key, OFF)
            if current in (OFF, SHIFT_GY_REST, ""):
                schedule[rest_date][emp.key] = SHIFT_GY_REST

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
            for d in dates:
                shift = schedule.get(d, {}).get(emp.key, OFF)
                if shift == SHIFT_DUTY:
                    reserve_projected_rest(emp, d, SHIFT_DUTY)
                    mark_projected_rest(emp, d, SHIFT_DUTY)
                elif shift == SHIFT_GY and schedule.get(d - timedelta(days=1), {}).get(emp.key, OFF) != SHIFT_GY:
                    reserve_projected_rest(emp, d, SHIFT_GY)
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
        block = gy_block_dates(d)
        candidates.sort(
            key=lambda e: (
                counts[e.key]["gy"],
                counts[e.key]["total"],
                int(historical_summary_counts.get(e.key, {}).get("gy", 0)),
                int(historical_summary_counts.get(e.key, {}).get("total", 0)),
                sum(candidate_score(e, cur, SHIFT_GY)[0] for cur in block),
                rng.random(),
            )
        )
        chosen = candidates[0]
        for cur in block:
            mark_assignment(chosen, cur, SHIFT_GY)
        reserve_projected_rest(chosen, d, SHIFT_GY)
        mark_projected_rest(chosen, d, SHIFT_GY)
        return True

    # Reserve month-long GY/duty blocks first. D/S can then balance around the
    # actual GY and projected-rest constraints instead of losing candidates late.
    for d in dates:
        min_rules = min_rules_for_date(rules, d, holidays)
        gy_shift = SHIFT_DUTY if is_duty_day(d) else SHIFT_GY
        required_gy = min_rules.get(gy_shift, 1)
        if not is_duty_day(d):
            while assigned_count(d, gy_shift) < required_gy:
                if not assign_gy_block_start(d):
                    break
        for _ in range(max(0, required_gy - assigned_count(d, gy_shift))):
            assign_one(d, gy_shift)

    for d in dates:
        min_rules = min_rules_for_date(rules, d, holidays)
        day_swing_order = [shift for shift in (SHIFT_DAY, SHIFT_SWING) if shift in min_rules]
        for shift in day_swing_order:
            for _ in range(max(0, min_rules.get(shift, 0) - assigned_count(d, shift))):
                assign_one(d, shift)

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
            if shift_group_size(cur, shift) >= 2:
                return None
        return block

    pair_mentor_counts: Dict[tuple[str, str], int] = defaultdict(int)
    pair_mentor_loads: Dict[str, int] = defaultdict(int)

    def assign_pair_category(emp: Employee, category: str) -> bool:
        candidates: List[tuple[float, float, date, str, Employee, List[date]]] = []
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
                score = sum(candidate_score(emp, cur, shift)[0] for cur in block)
                score += pair_mentor_counts[(emp.key, mentor.key)] * 180
                score += pair_mentor_loads[mentor.key] * 45
                score += counts[emp.key]["total"] * 20
                score += counts[emp.key][week_bucket(d)] * 10
                score += len(block) * 2
                candidates.append((score, rng.random(), d, shift, mentor, block))
        if not candidates:
            return False
        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        _, _, _, shift, mentor, block = candidates[0]
        for cur in block:
            mark_assignment(emp, cur, shift)
            pair_mentor_counts[(emp.key, mentor.key)] += 1
            pair_mentor_loads[mentor.key] += 1
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


def _employee_is_eligible_for_summary(employee: Employee, summary_key: str) -> bool:
    if employee.pair_required:
        return False
    if summary_key in {"weekday_swing", "holiday_swing", "gy", "saturday_duty"} and employee.day_only:
        return False
    return True


def _distribution_range(values: List[int]) -> int:
    return max(values) - min(values) if values else 0


def schedule_fairness_score(
    result: ScheduleResult,
    historical_summary_counts: Optional[Mapping[str, Mapping[str, int]]] = None,
) -> Tuple[int, int, int, int, int, int, int]:
    history = historical_summary_counts or {}
    summaries = {
        employee.key: employee_monthly_summary(result, employee)
        for employee in result.employees
    }
    three_level_columns = 0
    singleton_excess_spread = 0
    monthly_range_total = 0
    cumulative_range_total = 0
    monthly_totals: List[int] = []
    cumulative_totals: List[int] = []

    for employee in result.employees:
        if employee.pair_required:
            continue
        current_total = sum(summaries[employee.key].values())
        monthly_totals.append(current_total)
        cumulative_totals.append(current_total + int(history.get(employee.key, {}).get("total", 0)))

    for column in MONTHLY_SUMMARY_COLUMNS:
        eligible = [
            employee
            for employee in result.employees
            if _employee_is_eligible_for_summary(employee, column.key)
        ]
        current_values = [summaries[employee.key][column.key] for employee in eligible]
        cumulative_values = [
            summaries[employee.key][column.key]
            + int(history.get(employee.key, {}).get(column.key, 0))
            for employee in eligible
        ]
        three_level_columns += max(0, len(set(current_values)) - 2)
        spread = _distribution_range(current_values)
        if column.key != "gy":
            singleton_excess_spread += max(0, spread - 1)
        monthly_range_total += spread
        cumulative_range_total += _distribution_range(cumulative_values)

    return (
        three_level_columns,
        singleton_excess_spread,
        monthly_range_total,
        _distribution_range(monthly_totals),
        cumulative_range_total,
        _distribution_range(cumulative_totals),
        len(result.warnings),
    )


def _rebalance_single_day_summary_columns(result: ScheduleResult, rules: ShiftRules) -> None:
    summary_shifts = {
        "weekday_day": SHIFT_DAY,
        "weekday_swing": SHIFT_SWING,
        "holiday_day": SHIFT_DAY,
        "holiday_swing": SHIFT_SWING,
    }
    warnings = set(result.warnings)
    max_moves = len(result.employees) * len(summary_shifts)
    for _ in range(max_moves):
        summaries = {
            employee.key: employee_monthly_summary(result, employee)
            for employee in result.employees
        }
        moved = False
        for summary_key, shift in summary_shifts.items():
            eligible = [
                employee
                for employee in result.employees
                if _employee_is_eligible_for_summary(employee, summary_key)
            ]
            if not eligible:
                continue
            low_candidates = sorted(eligible, key=lambda employee: summaries[employee.key][summary_key])
            high_candidates = sorted(eligible, key=lambda employee: summaries[employee.key][summary_key], reverse=True)
            for high in high_candidates:
                for low in low_candidates:
                    if summaries[high.key][summary_key] - summaries[low.key][summary_key] <= 1:
                        break
                    for work_date in month_dates(result.year, result.month):
                        if monthly_summary_key_for_assignment(work_date, shift, result.holidays) != summary_key:
                            continue
                        if result.schedule.get(work_date, {}).get(high.key, OFF) != shift:
                            continue
                        if result.schedule.get(work_date, {}).get(low.key, OFF) != OFF:
                            continue
                        if work_date in low.unavailable_dates:
                            continue
                        result.schedule[work_date][high.key] = OFF
                        result.schedule[work_date][low.key] = shift
                        new_warnings = set(validate_schedule(
                            result.employees,
                            result.year,
                            result.month,
                            result.schedule,
                            result.holidays,
                            rules,
                        ))
                        if new_warnings - warnings:
                            result.schedule[work_date][low.key] = OFF
                            result.schedule[work_date][high.key] = shift
                            continue
                        warnings = new_warnings
                        moved = True
                        break
                    if moved:
                        break
                if moved:
                    break
            if moved:
                break
        if not moved:
            break
    result.warnings = sorted(warnings)


def generate_month_schedule(
    employees: List[Employee],
    year: int,
    month: int,
    rules: Optional[ShiftRules] = None,
    seed: Optional[int] = None,
    previous_day_duty_employee_keys: Optional[Set[str]] = None,
    initial_schedule: Optional[ScheduleMap] = None,
    historical_summary_counts: Optional[Mapping[str, Mapping[str, int]]] = None,
    balance_attempts: int = 12,
) -> ScheduleResult:
    attempt_count = max(1, balance_attempts)
    base_seed = seed if seed is not None else random.SystemRandom().randrange(0, 2**31)
    effective_rules = rules or ShiftRules()
    candidates: List[ScheduleResult] = []
    for attempt in range(attempt_count):
        candidate = _generate_month_schedule_once(
            employees,
            year,
            month,
            effective_rules,
            base_seed + attempt,
            previous_day_duty_employee_keys,
            initial_schedule,
            historical_summary_counts,
        )
        _rebalance_single_day_summary_columns(candidate, effective_rules)
        candidates.append(candidate)
    return min(
        candidates,
        key=lambda result: schedule_fairness_score(result, historical_summary_counts),
    )
