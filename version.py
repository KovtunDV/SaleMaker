"""Версия приложения SaleMaker.

Формат: MAJOR.MINOR.PATCH (например 0.01.03).
При каждом изменении кода увеличивайте PATCH — последний сегмент (…02 → …03).
"""

from __future__ import annotations

APP_NAME = "SaleMaker"
__version__ = "0.01.03"


def app_window_title() -> str:
    return f"{APP_NAME} {__version__}"
