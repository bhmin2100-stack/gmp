from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .app_paths import app_data_file


SETTINGS_PATH = app_data_file("monthly_stats_filter.json")
DATE_KEYS = (
    "weekday",
    "family",
    "holiday",
    "saturday",
    "sunday",
    "long_holiday_middle",
)
SHIFT_KEYS = ("day", "swing", "gy")
ALL_FILTERS = frozenset((date_key, shift_key) for date_key in DATE_KEYS for shift_key in SHIFT_KEYS)


def normalize_filters(raw_filters: object) -> set[tuple[str, str]]:
    if not isinstance(raw_filters, (list, tuple, set, frozenset)):
        return set()
    normalized: set[tuple[str, str]] = set()
    for item in raw_filters:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        key = (str(item[0]), str(item[1]))
        if key in ALL_FILTERS:
            normalized.add(key)
    return normalized


def load_monthly_stats_filter(path: Path = SETTINGS_PATH) -> tuple[bool, set[tuple[str, str]]]:
    if not path.exists():
        return False, set(ALL_FILTERS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False, set(ALL_FILTERS)
    if not isinstance(data, dict) or not bool(data.get("pinned")):
        return False, set(ALL_FILTERS)
    return True, normalize_filters(data.get("filters"))


def save_monthly_stats_filter(
    filters: Iterable[tuple[str, str]],
    pinned: bool,
    path: Path = SETTINGS_PATH,
) -> None:
    normalized = normalize_filters(list(filters))
    payload = {
        "pinned": bool(pinned),
        "filters": [list(key) for key in sorted(normalized)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
