"""Backward-compatible UI entrypoint.

The actual PySide6 implementation lives in gmp_scheduler.app.
"""
from __future__ import annotations

try:
    from .app import MainWindow, run
except ModuleNotFoundError as exc:  # pragma: no cover
    if exc.name != "PySide6":
        raise
    MainWindow = None  # type: ignore

    def run() -> None:
        raise RuntimeError("PySide6가 필요합니다. `pip install -r requirements.txt`를 실행하세요.")
