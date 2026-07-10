from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .app_paths import app_data_file

MODULE_SETTINGS_PATH = app_data_file("module_settings.json")


def load_modules(path: Path = MODULE_SETTINGS_PATH) -> List[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    modules = data.get("modules", [])
    if not isinstance(modules, list):
        return []
    result: List[str] = []
    seen = set()
    for value in modules:
        name = str(value).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def save_modules(modules: List[str], path: Path = MODULE_SETTINGS_PATH) -> None:
    cleaned: List[str] = []
    seen = set()
    for value in modules:
        name = str(value).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        cleaned.append(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"modules": cleaned}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
