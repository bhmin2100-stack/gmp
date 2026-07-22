from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from .app_paths import app_data_file
from .monthly_summary import MONTHLY_SUMMARY_COLUMNS


SUMMARY_LAYOUT_PATH = app_data_file("monthly_summary_layout.json")
SUMMARY_KEYS = tuple(column.key for column in MONTHLY_SUMMARY_COLUMNS)
LEGACY_KEY_ALIASES = {
    "weekday_gy": "gy",
    "holiday_gy": "gy",
}


def default_summary_groups() -> list[tuple[str, ...]]:
    return [(key,) for key in SUMMARY_KEYS]


def summary_group_id(group: Sequence[str]) -> str:
    return "|".join(group)


def normalize_summary_groups(raw_groups: object) -> list[tuple[str, ...]]:
    groups: list[tuple[str, ...]] = []
    seen: set[str] = set()
    if isinstance(raw_groups, list):
        for raw_group in raw_groups:
            if not isinstance(raw_group, (list, tuple)):
                continue
            group: list[str] = []
            for raw_key in raw_group:
                key = LEGACY_KEY_ALIASES.get(str(raw_key), str(raw_key))
                if key not in SUMMARY_KEYS or key in seen:
                    continue
                seen.add(key)
                group.append(key)
            if group:
                groups.append(tuple(group))
    for key in SUMMARY_KEYS:
        if key not in seen:
            groups.append((key,))
    return groups or default_summary_groups()


def normalize_hidden_keys(raw_hidden: object) -> set[str]:
    if not isinstance(raw_hidden, (list, tuple, set)):
        return set()
    result = {
        LEGACY_KEY_ALIASES.get(str(raw_key), str(raw_key))
        for raw_key in raw_hidden
    }
    return {key for key in result if key in SUMMARY_KEYS}


def load_summary_layout(path: Path = SUMMARY_LAYOUT_PATH) -> tuple[list[tuple[str, ...]], set[str]]:
    if not path.exists():
        return default_summary_groups(), set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_summary_groups(), set()
    if not isinstance(data, dict):
        return default_summary_groups(), set()
    return normalize_summary_groups(data.get("groups")), normalize_hidden_keys(data.get("hidden"))


def save_summary_layout(
    groups: Sequence[Sequence[str]],
    hidden_keys: Iterable[str],
    path: Path = SUMMARY_LAYOUT_PATH,
) -> None:
    normalized_groups = normalize_summary_groups([list(group) for group in groups])
    normalized_hidden = normalize_hidden_keys(list(hidden_keys))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "groups": [list(group) for group in normalized_groups],
                "hidden": sorted(normalized_hidden),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def merge_summary_groups(
    groups: Sequence[Sequence[str]],
    source_group_id: str,
    target_group_id: str,
) -> list[tuple[str, ...]]:
    normalized = normalize_summary_groups([list(group) for group in groups])
    source_index = next(
        (index for index, group in enumerate(normalized) if summary_group_id(group) == source_group_id),
        -1,
    )
    target_index = next(
        (index for index, group in enumerate(normalized) if summary_group_id(group) == target_group_id),
        -1,
    )
    if source_index < 0 or target_index < 0 or source_index == target_index:
        return normalized
    source = normalized[source_index]
    target = normalized[target_index]
    merged = tuple(dict.fromkeys((*target, *source)))
    result: list[tuple[str, ...]] = []
    for index, group in enumerate(normalized):
        if index == source_index:
            continue
        result.append(merged if index == target_index else group)
    return normalize_summary_groups([list(group) for group in result])


def split_summary_group(
    groups: Sequence[Sequence[str]],
    target_group_id: str,
) -> list[tuple[str, ...]]:
    normalized = normalize_summary_groups([list(group) for group in groups])
    result: list[tuple[str, ...]] = []
    for group in normalized:
        if summary_group_id(group) == target_group_id and len(group) > 1:
            result.extend((key,) for key in group)
        else:
            result.append(group)
    return normalize_summary_groups([list(group) for group in result])
