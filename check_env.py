#!/usr/bin/env python3
"""Проверка окружения перед запуском SaleMaker."""

from __future__ import annotations

import platform
import sys


def main() -> int:
    print(f"Python: {sys.version}")
    print(f"Platform: {platform.platform()}")
    print(f"Executable: {sys.executable}")

    ok = True
    for name in ("pandas", "openpyxl", "cv2", "PIL", "yaml"):
        try:
            __import__(name if name != "cv2" else "cv2")
            print(f"  OK  {name}")
        except ImportError as e:
            print(f"  FAIL {name}: {e}")
            ok = False

    try:
        import PySide6

        print(f"  OK  PySide6 {PySide6.__version__}")
        import shiboken6

        print(f"  OK  shiboken6 {shiboken6.__version__}")
        from PySide6.QtWidgets import QApplication

        app = QApplication([])
        print("  OK  QApplication")
        del app
    except Exception as e:
        print(f"  FAIL PySide6/shiboken6/Qt: {e}")
        ok = False
        if sys.platform.startswith("linux"):
            print()
            print("Linux:")
            print("  • Не задавайте LD_LIBRARY_PATH=/usr/lib/... — ломает Qt (Qt_6_PRIVATE_API).")
            print("  • venv лучше создавать: /usr/bin/python3 -m venv .venv")
            print("  • GLIBCXX_3.4.29: pip install -r requirements-linux-py39-legacy.txt")
            print("    и запуск: SALEMAKER_USE_SYSTEM_LIBSTDCXX=1 ./run-linux.sh")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
