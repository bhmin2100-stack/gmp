from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable


APP_DIR_NAME = "GMP Scheduler"


def app_data_dir() -> Path:
    override = os.environ.get("GMP_SCHEDULER_DATA_DIR", "").strip()
    if override:
        base = Path(override).expanduser()
    else:
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        base = Path(root) / APP_DIR_NAME if root else Path.home() / "AppData" / "Local" / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def app_data_file(file_name: str) -> Path:
    target = app_data_dir() / file_name
    migrate_legacy_file(file_name, target)
    return target


def migrate_legacy_file(file_name: str, target: Path) -> None:
    if target.exists():
        return
    for source in legacy_file_candidates(file_name):
        try:
            if source.resolve() == target.resolve():
                continue
        except OSError:
            pass
        if source.exists() and source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            return


def legacy_file_candidates(file_name: str) -> Iterable[Path]:
    candidates: list[Path] = [Path.cwd() / file_name]
    executable = Path(getattr(sys, "executable", "") or "")
    if executable:
        candidates.append(executable.resolve().parent / file_name)
    candidates.append(Path(__file__).resolve().parents[1] / file_name)

    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).lower()
        except OSError:
            key = str(candidate.absolute()).lower()
        if key in seen:
            continue
        seen.add(key)
        yield candidate
