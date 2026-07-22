from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gmp_scheduler.app_resources import app_icon_path
from gmp_scheduler.ui import MainWindow, apply_light_theme


def main() -> int:
    app = QApplication(sys.argv)
    apply_light_theme(app)
    app.setApplicationName("GMP 근무표 자동 생성기")
    icon_path = app_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
