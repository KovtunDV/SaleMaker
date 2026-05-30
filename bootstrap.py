"""
Подготовка окружения Linux перед импортом PySide6.

Shiboken6 — часть PySide6 (генератор Python-биндингов к Qt). Отдельно от PySide6
не используется и не заменяется без смены GUI-библиотеки (PyQt6, Tkinter и т.д.).

Важно: нельзя добавлять /usr/lib в LD_LIBRARY_PATH — подхватится системный Qt6,
несовместимый с Qt из wheel PySide6 (ошибка Qt_6_PRIVATE_API).
Допустимо только LD_PRELOAD на libstdc++.so.6 (обход старого libstdc++ Anaconda).
"""

from __future__ import annotations

import os
import sys

_LIBSTDCXX_CANDIDATES = (
    "/usr/lib/x86_64-linux-gnu/libstdc++.so.6",
    "/lib/x86_64-linux-gnu/libstdc++.so.6",
    "/usr/lib64/libstdc++.so.6",
)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "yes", "true", "on")


def bootstrap() -> None:
    """Опционально перезапуск с LD_PRELOAD=libstdc++.so.6 (только если задан флаг)."""
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("SALEMAKER_BOOTSTRAP_DONE") == "1":
        return
    if not _truthy("SALEMAKER_USE_SYSTEM_LIBSTDCXX"):
        return

    preload = os.environ.get("LD_PRELOAD", "")
    for lib in _LIBSTDCXX_CANDIDATES:
        if not os.path.isfile(lib):
            continue
        if lib in preload.split(":"):
            break
        os.environ["LD_PRELOAD"] = f"{lib}:{preload}" if preload else lib
        os.environ["SALEMAKER_BOOTSTRAP_DONE"] = "1"
        os.execve(sys.executable, [sys.executable, *sys.argv], os.environ)
        return

    os.environ["SALEMAKER_BOOTSTRAP_DONE"] = "1"
