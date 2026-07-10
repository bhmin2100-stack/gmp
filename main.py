from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from gmp_scheduler.ui import MainWindow, apply_light_theme


def main() -> int:
    app = QApplication(sys.argv)
    apply_light_theme(app)
    app.setApplicationName("GMP 근무표 자동 생성기")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
