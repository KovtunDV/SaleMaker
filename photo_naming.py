"""Имена файлов: {СерийныйНомер}-{НомерСлота}.ext — слот 1..6, пересъёмка перезаписывает файл."""

from __future__ import annotations

import re
from pathlib import Path

_INVALID = re.compile(r'[\\/:*?"<>|]+')

MIN_SLOT = 1
MAX_SLOT = 6


def sanitize_serial(serial: str) -> str:
    s = (serial or "").strip()
    if not s:
        return ""
    s = _INVALID.sub("_", s)
    return s.strip(" .")


def build_filename(sanitized_serial: str, slot: int, ext: str) -> str:
    if slot < MIN_SLOT or slot > MAX_SLOT:
        raise ValueError(f"Номер слота должен быть {MIN_SLOT}..{MAX_SLOT}")
    ext = ext.lstrip(".")
    return f"{sanitized_serial}-{slot}.{ext}"


def make_photo_path_for_slot(
    photos_dir: str | Path,
    serial: str,
    slot: int,
    ext: str = "jpg",
) -> tuple[str, str]:
    """
    Фиксированное имя для слота: пересъёмка перезаписывает тот же файл.
    Возвращает (полный путь, имя файла).
    """
    safe = sanitize_serial(serial)
    if not safe:
        raise ValueError("Серийный номер пустой или недопустимый для имени файла.")
    root = Path(photos_dir)
    root.mkdir(parents=True, exist_ok=True)
    name = build_filename(safe, slot, ext)
    return str(root / name), name
