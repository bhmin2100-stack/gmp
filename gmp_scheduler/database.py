from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .calendar_utils import is_holiday_or_weekend, month_dates
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleMap, ScheduleResult
from .stats import EmployeeStats, compute_stats

DB_PATH = Path("gmp_scheduler.sqlite3")


SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    employee_no TEXT NOT NULL DEFAULT '',
    is_new INTEGER NOT NULL DEFAULT 0,
    UNIQUE(name, employee_no)
);

CREATE TABLE IF NOT EXISTS monthly_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL,
    UNIQUE(year, month, source_name)
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    employee_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    shift_code TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(schedule_id) REFERENCES monthly_schedules(id) ON DELETE CASCADE,
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
    UNIQUE(schedule_id, employee_id, work_date)
);

CREATE TABLE IF NOT EXISTS unavailable_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL,
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
    UNIQUE(employee_id, work_date, source_name)
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_employee(conn: sqlite3.Connection, emp: Employee) -> int:
    conn.execute(
        """
        INSERT INTO employees(name, employee_no, is_new)
        VALUES (?, ?, ?)
        ON CONFLICT(name, employee_no) DO UPDATE SET is_new=excluded.is_new
        """,
        (emp.name, emp.employee_id, 1 if emp.is_new else 0),
    )
    row = conn.execute(
        "SELECT id FROM employees WHERE name=? AND employee_no=?",
        (emp.name, emp.employee_id),
    ).fetchone()
    return int(row["id"])


def save_schedule(result: ScheduleResult, source_name: str = "") -> int:
    with connect() as conn:
        imported_at = datetime.now().isoformat(timespec="seconds")
        result.source_name = source_name
        conn.execute(
            """
            INSERT INTO monthly_schedules(year, month, source_name, imported_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(year, month, source_name) DO UPDATE SET imported_at=excluded.imported_at
            """,
            (result.year, result.month, source_name, imported_at),
        )
        schedule_id = int(conn.execute(
            "SELECT id FROM monthly_schedules WHERE year=? AND month=? AND source_name=?",
            (result.year, result.month, source_name),
        ).fetchone()["id"])
        conn.execute("DELETE FROM assignments WHERE schedule_id=?", (schedule_id,))

        emp_ids = {emp.key: upsert_employee(conn, emp) for emp in result.employees}
        for d in month_dates(result.year, result.month):
            for emp in result.employees:
                conn.execute(
                    """
                    INSERT INTO assignments(schedule_id, employee_id, work_date, shift_code)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(schedule_id, employee_id, work_date)
                    DO UPDATE SET shift_code=excluded.shift_code
                    """,
                    (schedule_id, emp_ids[emp.key], d.isoformat(), result.schedule.get(d, {}).get(emp.key, OFF)),
                )
        return schedule_id


def delete_month_schedule(year: int, month: int, source_name: Optional[str] = None) -> int:
    """Delete saved schedules and unavailable marks for a month/source."""
    start_date = month_dates(year, month)[0].isoformat()
    end_date = month_dates(year, month)[-1].isoformat()
    with connect() as conn:
        if source_name is None:
            schedule_rows = conn.execute(
                "SELECT id FROM monthly_schedules WHERE year=? AND month=?",
                (year, month),
            ).fetchall()
        else:
            schedule_rows = conn.execute(
                "SELECT id FROM monthly_schedules WHERE year=? AND month=? AND source_name=?",
                (year, month, source_name),
            ).fetchall()
        schedule_ids = [int(row["id"]) for row in schedule_rows]
        for schedule_id in schedule_ids:
            conn.execute("DELETE FROM assignments WHERE schedule_id=?", (schedule_id,))
        if source_name is None:
            conn.execute("DELETE FROM monthly_schedules WHERE year=? AND month=?", (year, month))
            conn.execute(
                "DELETE FROM unavailable_days WHERE work_date BETWEEN ? AND ?",
                (start_date, end_date),
            )
        else:
            conn.execute(
                "DELETE FROM monthly_schedules WHERE year=? AND month=? AND source_name=?",
                (year, month, source_name),
            )
            conn.execute(
                "DELETE FROM unavailable_days WHERE work_date BETWEEN ? AND ? AND source_name=?",
                (start_date, end_date, source_name),
            )
        return len(schedule_ids)


def save_unavailable_days(employees: List[Employee], source_name: str = "") -> int:
    count = 0
    with connect() as conn:
        imported_at = datetime.now().isoformat(timespec="seconds")
        for emp in employees:
            emp_id = upsert_employee(conn, emp)
            for d in emp.unavailable_dates:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO unavailable_days(employee_id, work_date, source_name, imported_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (emp_id, d.isoformat(), source_name, imported_at),
                )
                count += 1
    return count


def cumulative_stats(
    start_year: Optional[int] = None,
    start_month: Optional[int] = None,
    end_year: Optional[int] = None,
    end_month: Optional[int] = None,
) -> List[Dict[str, object]]:
    start_key = start_year * 100 + start_month if start_year and start_month else None
    end_key = end_year * 100 + end_month if end_year and end_month else None
    range_clause = ""
    params: List[object] = []
    if start_key is not None:
        range_clause += " AND (ms.year * 100 + ms.month) >= ?"
        params.append(start_key)
    if end_key is not None:
        range_clause += " AND (ms.year * 100 + ms.month) <= ?"
        params.append(end_key)
    with connect() as conn:
        rows = conn.execute(
            f"""
            WITH first_work AS (
                SELECT employee_id, MIN(work_date) AS first_work_date
                FROM assignments
                WHERE shift_code IN (?, ?, ?, ?)
                GROUP BY employee_id
            )
            SELECT e.name, e.employee_no,
                   fw.first_work_date,
                   SUM(CASE WHEN fw.first_work_date IS NOT NULL AND a.work_date >= fw.first_work_date THEN 1 ELSE 0 END) AS eligible_days,
                   SUM(CASE WHEN a.work_date >= fw.first_work_date AND a.shift_code=? THEN 1 ELSE 0 END) AS d_count,
                   SUM(CASE WHEN a.work_date >= fw.first_work_date AND a.shift_code=? THEN 1 ELSE 0 END) AS s_count,
                   SUM(CASE WHEN a.work_date >= fw.first_work_date AND a.shift_code=? THEN 1 ELSE 0 END) AS weekday_gy_count,
                   SUM(CASE WHEN a.work_date >= fw.first_work_date AND a.shift_code=? THEN 1 ELSE 0 END) AS duty_count,
                   SUM(CASE WHEN a.work_date >= fw.first_work_date AND a.shift_code=? THEN 1 ELSE 0 END) AS gy_rest_count,
                   SUM(CASE WHEN a.work_date >= fw.first_work_date AND a.shift_code IN (?, ?, ?, ?) THEN 1 ELSE 0 END) AS total_work
            FROM employees e
            LEFT JOIN assignments a ON a.employee_id = e.id
            LEFT JOIN monthly_schedules ms ON ms.id = a.schedule_id
            LEFT JOIN first_work fw ON fw.employee_id = e.id
            WHERE 1=1 {range_clause}
            GROUP BY e.id
            HAVING eligible_days > 0
            ORDER BY e.name, e.employee_no
            """,
            (
                SHIFT_DAY,
                SHIFT_SWING,
                SHIFT_GY,
                SHIFT_DUTY,
                SHIFT_DAY,
                SHIFT_SWING,
                SHIFT_GY,
                SHIFT_DUTY,
                SHIFT_GY_REST,
                SHIFT_DAY,
                SHIFT_SWING,
                SHIFT_GY,
                SHIFT_DUTY,
                *params,
            ),
        ).fetchall()
        return [dict(row) for row in rows]


def saved_months() -> List[Dict[str, object]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, year, month, source_name, imported_at
            FROM monthly_schedules
            ORDER BY year DESC, month DESC, source_name
            """
        ).fetchall()
        return [dict(row) for row in rows]


def period_assignment_rows(start_year: int, start_month: int, end_year: int, end_month: int) -> List[Dict[str, object]]:
    start_key = start_year * 100 + start_month
    end_key = end_year * 100 + end_month
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT e.name, e.employee_no, a.work_date, a.shift_code, ms.source_name
            FROM assignments a
            JOIN employees e ON e.id = a.employee_id
            JOIN monthly_schedules ms ON ms.id = a.schedule_id
            WHERE (ms.year * 100 + ms.month) BETWEEN ? AND ?
            ORDER BY a.work_date, a.id
            """,
            (start_key, end_key),
        ).fetchall()
        return [dict(row) for row in rows]


def load_schedule_result(
    year: int,
    month: int,
    source_name: Optional[str] = None,
    fallback_source_names: Optional[Iterable[str]] = None,
) -> Optional[ScheduleResult]:
    """Load the latest saved schedule for a year/month from the local DB."""
    from .calendar_utils import korean_holidays

    with connect() as conn:
        sched = None
        source_candidates: List[Optional[str]]
        if source_name is not None:
            source_candidates = [source_name]
            if fallback_source_names:
                source_candidates.extend(fallback_source_names)
        else:
            source_candidates = []

        for candidate in source_candidates:
            sched = conn.execute(
                """
                SELECT id, source_name
                FROM monthly_schedules
                WHERE year=? AND month=? AND source_name=?
                ORDER BY imported_at DESC, id DESC
                LIMIT 1
                """,
                (year, month, candidate or ""),
            ).fetchone()
            if sched:
                break

        if sched is None and source_name is None:
            sched = conn.execute(
                """
                SELECT id, source_name
                FROM monthly_schedules
                WHERE year=? AND month=?
                ORDER BY imported_at DESC, id DESC
                LIMIT 1
                """,
                (year, month),
            ).fetchone()
        if not sched:
            return None
        rows = conn.execute(
            """
            SELECT e.id AS employee_db_id, e.name, e.employee_no, e.is_new, a.work_date, a.shift_code
            FROM assignments a
            JOIN employees e ON e.id = a.employee_id
            WHERE a.schedule_id=?
            ORDER BY a.work_date, a.id
            """,
            (int(sched["id"]),),
        ).fetchall()
        if not rows:
            return None

        employee_ids_by_key: Dict[str, int] = {}
        employee_meta_by_key: Dict[str, tuple[str, str, bool]] = {}
        for row in rows:
            name = str(row["name"])
            employee_no = str(row["employee_no"] or "")
            key = f"{name}|{employee_no}"
            employee_ids_by_key[key] = int(row["employee_db_id"])
            employee_meta_by_key[key] = (name, employee_no, bool(row["is_new"]))
        unavailable_by_key: Dict[str, Set[date]] = {key: set() for key in employee_meta_by_key}
        if employee_ids_by_key:
            placeholders = ",".join("?" for _ in employee_ids_by_key)
            start_date = month_dates(year, month)[0].isoformat()
            end_date = month_dates(year, month)[-1].isoformat()
            unavailable_rows = conn.execute(
                f"""
                SELECT employee_id, work_date
                FROM unavailable_days
                WHERE employee_id IN ({placeholders})
                  AND work_date BETWEEN ? AND ?
                """,
                (*employee_ids_by_key.values(), start_date, end_date),
            ).fetchall()
            key_by_employee_id = {employee_id: key for key, employee_id in employee_ids_by_key.items()}
            for unavailable_row in unavailable_rows:
                key = key_by_employee_id.get(int(unavailable_row["employee_id"]))
                if key:
                    unavailable_by_key.setdefault(key, set()).add(date.fromisoformat(str(unavailable_row["work_date"])))
        employees = [
            Employee(name, employee_no, is_new, unavailable_by_key.get(key, set()))
            for key, (name, employee_no, is_new) in employee_meta_by_key.items()
        ]
        schedule: ScheduleMap = {d: {emp.key: OFF for emp in employees} for d in month_dates(year, month)}
        for row in rows:
            d = date.fromisoformat(str(row["work_date"]))
            emp_key = f"{row['name']}|{row['employee_no'] or ''}"
            if d in schedule:
                schedule[d][emp_key] = str(row["shift_code"] or OFF)
        return ScheduleResult(year, month, employees, schedule, korean_holidays(year), source_name=str(sched["source_name"] or ""))
