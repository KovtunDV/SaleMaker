"""Файл шаблонов значений чек-листа (тип оборудования + модель)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_FILENAME = "checklist_templates.yaml"
# Модель — ключ записи шаблона, в fields не дублируем.
TEMPLATE_SKIP_FIELD_IDS = frozenset({"model"})


def default_templates_path(config_yaml: str | Path) -> Path:
    return Path(config_yaml).resolve().parent / DEFAULT_FILENAME


def resolve_templates_path(cfg: object, config_yaml: str | Path) -> Path:
    """Путь к файлу шаблонов из config.checklist_templates.path или рядом с config.yaml."""
    custom = ""
    section = getattr(cfg, "checklist_templates", None)
    if section is not None:
        custom = str(getattr(section, "path", "") or "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return default_templates_path(config_yaml)


def load_templates_file(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"templates": []}
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Корень файла шаблонов должен быть объектом")
    if "templates" not in data or data["templates"] is None:
        data["templates"] = []
    if not isinstance(data["templates"], list):
        raise ValueError("Ключ «templates» должен быть списком")
    return data


def save_templates_file(path: str | Path, data: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def list_template_models_for_equipment(
    data: dict[str, Any],
    equipment_type: str,
) -> list[str]:
    """Уникальные модели из шаблонов для указанного типа оборудования."""
    eq = equipment_type.strip()
    if not eq:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in data.get("templates") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("equipment_type", "")).strip() != eq:
            continue
        mdl = str(item.get("model", "")).strip()
        if not mdl or mdl in seen:
            continue
        seen.add(mdl)
        out.append(mdl)
    out.sort(key=str.casefold)
    return out


def find_checklist_template(
    data: dict[str, Any],
    equipment_type: str,
    model: str,
) -> dict[str, str] | None:
    eq = equipment_type.strip()
    mdl = model.strip()
    if not eq or not mdl:
        return None
    for item in data.get("templates") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("equipment_type", "")).strip() != eq:
            continue
        if str(item.get("model", "")).strip() != mdl:
            continue
        raw = item.get("fields")
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if v is not None and str(v).strip() != ""}
    return None


def upsert_checklist_template(
    data: dict[str, Any],
    equipment_type: str,
    model: str,
    fields: dict[str, str],
) -> None:
    eq = equipment_type.strip()
    mdl = model.strip()
    if not eq or not mdl:
        raise ValueError("Тип оборудования и модель обязательны для шаблона")
    clean_fields = {
        str(k): str(v).strip()
        for k, v in fields.items()
        if k not in TEMPLATE_SKIP_FIELD_IDS and v is not None and str(v).strip()
    }
    templates = data.setdefault("templates", [])
    if not isinstance(templates, list):
        raise ValueError("Ключ «templates» должен быть списком")
    for item in templates:
        if not isinstance(item, dict):
            continue
        if str(item.get("equipment_type", "")).strip() == eq and str(item.get("model", "")).strip() == mdl:
            item["fields"] = clean_fields
            return
    templates.append(
        {
            "equipment_type": eq,
            "model": mdl,
            "fields": clean_fields,
        }
    )


def remove_checklist_template(
    data: dict[str, Any],
    equipment_type: str,
    model: str,
) -> bool:
    eq = equipment_type.strip()
    mdl = model.strip()
    templates = data.get("templates")
    if not isinstance(templates, list):
        return False
    before = len(templates)
    data["templates"] = [
        t
        for t in templates
        if not (
            isinstance(t, dict)
            and str(t.get("equipment_type", "")).strip() == eq
            and str(t.get("model", "")).strip() == mdl
        )
    ]
    return len(data["templates"]) < before
