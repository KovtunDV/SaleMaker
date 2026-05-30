"""Загрузка и валидация config.yaml."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TemplateSection:
    path: str = ""
    sheet: int | str = 0
    header_row: int = 1
    start_row: int = 2  # 1-based: строка первой записи (данные) в реестре


@dataclass
class ReferenceSection:
    path: str = ""
    serial_column: str = "Серийный номер"
    template_serial_column: str = "Серийный номер"


@dataclass
class PriceReferenceSection:
    path: str = ""
    sheet: int | str = "Справочник"
    header_row: int = 3  # 1-based строка заголовков


@dataclass
class SemanticField:
    key: str
    label: str
    template_column: str
    excel_column: int | str | None = None  # приоритетная колонка Excel (1-based), напр. 5 или "E"
    values: list[str] = field(default_factory=list)
    required: bool = False


@dataclass
class PhotoSlot:
    slot: int
    template_column: str
    required: bool = False
    hint: str = ""  # подсказка (имя файла, лимит размера и т.д.)
    excel_column: int | str | None = None  # альтернативно: номер/буква колонки Excel (1-based), напр. 15 или "O"


@dataclass
class ChecklistField:
    id: str
    label: str
    type: str = "text"  # text, number, bool, textarea
    values: list[str] = field(default_factory=list)  # значения "по умолчанию"
    default: str | None = None  # None / null в YAML — не подставлять значение
    required: bool = False


@dataclass
class ChecklistSpec:
    """Один вид чек-листа: поля, стартовая колонка Excel, слоты фото."""

    fields: list[ChecklistField]
    checklist_start_column: int | str = 21
    photo_slots: list[PhotoSlot] = field(default_factory=list)


@dataclass
class UiSection:
    font_family: str = ""  # "" = системный по умолчанию
    font_size: int = 10
    header_field: str = ""  # дополнительное ключевое поле в шапке (имя поля/колонки)
    header_label: str = ""  # подпись рядом со значением (если пусто, берём header_field)


@dataclass
class CameraSection:
    max_width: int = 0   # 0 = не задавать
    max_height: int = 0  # 0 = не задавать
    scan_max_index: int = 5
    capture_with_preview: bool = False  # полноэкранное превью перед съёмкой


@dataclass
class PackageSection:
    name_columns: list[str] = field(default_factory=lambda: ["Город", "Тип реализации"])
    datetime_format: str = "%Y-%m-%d_%H_%M"


@dataclass
class ChecklistTemplatesSection:
    path: str = ""  # пусто — checklist_templates.yaml рядом с config.yaml


@dataclass
class AppConfig:
    template: TemplateSection
    reference: ReferenceSection
    price_reference: PriceReferenceSection
    camera: CameraSection
    package: PackageSection
    checklist_templates: ChecklistTemplatesSection
    column_mapping: dict[str, str]
    column_excel_columns: dict[str, int | str]
    column_required: dict[str, bool]
    semantic: list[SemanticField]
    ui: UiSection
    equipment_types: list[str]
    equipment_type_column: str
    checklists: dict[str, ChecklistSpec]
    photo_cell_mode: str = "filename"  # filename | fullpath
    output_path: str = ""
    photos_dir: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AppConfig":
        t = d.get("template") or {}
        r = d.get("reference") or {}
        pr = d.get("price_reference") or {}
        ui_raw = d.get("ui") or {}
        ui = UiSection(
            font_family=str(ui_raw.get("font_family") or ""),
            font_size=int(ui_raw.get("font_size") or 10),
            header_field=str(ui_raw.get("header_field") or ""),
            header_label=str(ui_raw.get("header_label") or ""),
        )
        price_reference = PriceReferenceSection(
            path=str(pr.get("path") or ""),
            sheet=pr.get("sheet") if pr.get("sheet") is not None else "Справочник",
            header_row=int(pr.get("header_row") or 3),
        )
        cam_raw = d.get("camera") or {}
        camera = CameraSection(
            max_width=int(cam_raw.get("max_width") or 0),
            max_height=int(cam_raw.get("max_height") or 0),
            scan_max_index=int(cam_raw.get("scan_max_index") or 5),
            capture_with_preview=bool(cam_raw.get("capture_with_preview", False)),
        )
        pkg_raw = d.get("package") or {}
        package = PackageSection(
            name_columns=[str(x) for x in (pkg_raw.get("name_columns") or ["Город", "Тип реализации"])],
            datetime_format=str(pkg_raw.get("datetime_format") or "%Y-%m-%d_%H_%M"),
        )
        ct_raw = d.get("checklist_templates") or {}
        checklist_templates = ChecklistTemplatesSection(
            path=str(ct_raw.get("path") or ""),
        )

        sem_raw = d.get("semantic") or []
        semantic: list[SemanticField] = []
        for s in sem_raw:
            semantic.append(
                SemanticField(
                    key=str(s["key"]),
                    label=str(s.get("label", s["key"])),
                    template_column=str(s["template_column"]),
                    excel_column=s.get("excel_column"),
                    values=list(s.get("values") or []),
                    required=bool(s.get("required", False)),
                )
            )

        chk: dict[str, ChecklistSpec] = {}
        for eq_type, node in (d.get("checklists") or {}).items():
            key = str(eq_type)
            if isinstance(node, list):
                chk[key] = ChecklistSpec(
                    fields=cls._parse_fields_list(node),
                    checklist_start_column=int(d.get("legacy_checklist_start_column") or 21),
                    photo_slots=[],
                )
            elif isinstance(node, dict):
                aliases = [str(x) for x in (node.get("aliases") or []) if str(x).strip()]
                slots_raw = node.get("photo_slots") or []
                slots: list[PhotoSlot] = []
                for s in slots_raw:
                    slots.append(
                        PhotoSlot(
                            slot=int(s["slot"]),
                            template_column=str(s["template_column"]),
                            required=bool(s.get("required", False)),
                            hint=str(s.get("hint") or ""),
                            excel_column=s.get("excel_column"),
                        )
                    )
                slots.sort(key=lambda x: x.slot)
                fields_raw = node.get("fields") or node.get("items") or []
                spec = ChecklistSpec(
                    fields=cls._parse_fields_list(fields_raw),
                    checklist_start_column=node.get("checklist_start_column") or 21,
                    photo_slots=slots,
                )
                chk[key] = spec
                for a in aliases:
                    # алиасы типа оборудования: применяют тот же чек-лист
                    if a not in chk:
                        chk[a] = spec
            else:
                continue

        eq_types = list(d.get("equipment_types") or [])
        if not eq_types and chk:
            eq_types = list(chk.keys())

        cm_raw = d.get("column_mapping") or {}
        column_mapping: dict[str, str] = {}
        column_excel_columns: dict[str, int | str] = {}
        column_required: dict[str, bool] = {}
        for k, v in cm_raw.items():
            key = str(k)
            if isinstance(v, dict):
                ref = v.get("ref") if v.get("ref") is not None else v.get("reference")
                column_mapping[key] = str(ref or "")
                ec = v.get("excel_column")
                if ec is not None and str(ec).strip() != "":
                    column_excel_columns[key] = ec
                if "required" in v:
                    column_required[key] = bool(v.get("required", False))
            else:
                column_mapping[key] = str(v)

        return cls(
            template=TemplateSection(
                path=str(t.get("path") or ""),
                sheet=t.get("sheet", 0),
                header_row=int(t.get("header_row") or 1),
                start_row=int(t.get("start_row") or (int(t.get("header_row") or 1) + 1)),
            ),
            reference=ReferenceSection(
                path=str(r.get("path") or ""),
                serial_column=str(r.get("serial_column") or "Серийный номер инструмента"),
                template_serial_column=str(
                    r.get("template_serial_column")
                    or r.get("serial_column")
                    or "Серийный номер инструмента"
                ),
            ),
            price_reference=price_reference,
            camera=camera,
            package=package,
            checklist_templates=checklist_templates,
            column_mapping=column_mapping,
            column_excel_columns=column_excel_columns,
            column_required=column_required,
            semantic=semantic,
            ui=ui,
            equipment_types=eq_types,
            equipment_type_column=str(
                d.get("equipment_type_column") or "Тип оборудования"
            ),
            checklists=chk,
            photo_cell_mode=str(d.get("photo_cell_mode") or "filename"),
            output_path=str(d.get("output_path") or ""),
            photos_dir=str(d.get("photos_dir") or ""),
        )

    @staticmethod
    def _parse_field_default(raw: object) -> str | None:
        if raw is None:
            return None
        s = str(raw).strip()
        if not s or s.lower() == "null":
            return None
        return s

    @staticmethod
    def _parse_fields_list(raw: list[Any]) -> list[ChecklistField]:
        out: list[ChecklistField] = []
        for f in raw or []:
            if not isinstance(f, dict):
                continue
            default = (
                AppConfig._parse_field_default(f["default"])
                if "default" in f
                else None
            )
            out.append(
                ChecklistField(
                    id=str(f["id"]),
                    label=str(f.get("label", f["id"])),
                    type=str(f.get("type", "text")),
                    values=list(f.get("values") or []),
                    default=default,
                    required=bool(f.get("required", False)),
                )
            )
        return out


def get_checklist_spec(cfg: AppConfig, equipment_type: str) -> ChecklistSpec | None:
    return cfg.checklists.get(equipment_type.strip())


def checklist_field_has_default(cf: ChecklistField) -> bool:
    """Есть ли явное значение default (не null и не пустое)."""
    if cf.default is None:
        return False
    return bool(str(cf.default).strip())


def find_checklist_yaml_key(data: dict[str, Any], equipment_type: str) -> str | None:
    """Ключ раздела checklists в YAML для типа оборудования (с учётом aliases)."""
    eq = equipment_type.strip()
    checklists = data.get("checklists")
    if not isinstance(checklists, dict):
        return None
    if eq in checklists:
        return eq
    for name, node in checklists.items():
        if not isinstance(node, dict):
            continue
        aliases = [str(a).strip() for a in (node.get("aliases") or []) if str(a).strip()]
        if eq in aliases:
            return str(name)
    return None


def apply_defaults_to_checklist_spec(
    spec: ChecklistSpec,
    defaults_by_id: dict[str, str | None],
) -> None:
    by_id = {f.id: f for f in spec.fields}
    for fid, val in defaults_by_id.items():
        if fid not in by_id:
            continue
        if val is None or not str(val).strip():
            by_id[fid].default = None
        else:
            by_id[fid].default = str(val).strip()


def persist_checklist_field_defaults(
    path: str | Path,
    equipment_type: str,
    defaults_by_id: dict[str, str | None],
) -> None:
    """Записывает default полей чек-листа в config.yaml."""
    data = load_config_dict(path)
    key = find_checklist_yaml_key(data, equipment_type)
    if not key:
        raise ValueError(f"Чек-лист для типа «{equipment_type}» не найден в config.yaml")
    node = data.get("checklists", {}).get(key)
    if not isinstance(node, dict):
        raise ValueError(f"Раздел checklists.{key} должен быть объектом")
    fields_raw = node.get("fields") or []
    if not isinstance(fields_raw, list):
        raise ValueError(f"checklists.{key}.fields должен быть списком")
    by_id: dict[str, dict[str, Any]] = {}
    for item in fields_raw:
        if isinstance(item, dict) and item.get("id") is not None:
            by_id[str(item["id"])] = item
    for fid, val in defaults_by_id.items():
        if fid not in by_id:
            continue
        if val is None or not str(val).strip():
            by_id[fid]["default"] = None
        else:
            by_id[fid]["default"] = str(val).strip()
    save_config_dict(path, data)


def load_config(path: str | Path) -> AppConfig:
    return validate_config_dict(load_config_dict(path))


def load_config_dict(path: str | Path) -> dict[str, Any]:
    """Читает config.yaml в dict (для редактора конфигурации)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Конфиг не найден: {p}")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Корень config.yaml должен быть объектом")
    return data


def save_config_dict(path: str | Path, data: dict[str, Any]) -> None:
    """Записывает dict в config.yaml."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def validate_config_dict(data: dict[str, Any]) -> AppConfig:
    """Проверяет структуру конфигурации через AppConfig.from_dict."""
    if not isinstance(data, dict):
        raise ValueError("Корень config.yaml должен быть объектом")
    try:
        return AppConfig.from_dict(data)
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Ошибка структуры конфигурации: {e}") from e


def format_yaml_error(err: yaml.YAMLError) -> str:
    mark = getattr(err, "problem_mark", None)
    if mark is not None:
        return f"YAML, строка {mark.line + 1}, столбец {mark.column + 1}: {err}"
    return str(err)


def _dump_yaml_fragment(fragment: Any) -> str:
    return yaml.safe_dump(
        fragment,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()


def config_editor_blocks(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Список редактируемых блоков: (block_id, label)."""
    blocks: list[tuple[str, str]] = []
    if data.get("semantic") is not None:
        blocks.append(("semantic", "semantic — постоянные значения"))
    blocks.append(
        ("column_mapping", "column_mapping — сопоставление справочника и реестра")
    )
    blocks.append(
        ("package", "package — имя пакета реестра (ZIP)")
    )
    checklists = data.get("checklists")
    if isinstance(checklists, dict):
        for name in checklists:
            blocks.append((f"checklist:{name}", f"checklists — {name}"))
    return blocks


def block_to_yaml(data: dict[str, Any], block_id: str) -> str:
    """Фрагмент YAML для выбранного блока."""
    if block_id == "semantic":
        return _dump_yaml_fragment({"semantic": data.get("semantic") or []})
    if block_id == "column_mapping":
        return _dump_yaml_fragment(
            {"column_mapping": data.get("column_mapping") or {}}
        )
    if block_id == "package":
        return _dump_yaml_fragment({"package": data.get("package") or {}})
    if block_id.startswith("checklist:"):
        name = block_id.split(":", 1)[1]
        checklists = data.get("checklists")
        if not isinstance(checklists, dict):
            raise KeyError(f"Раздел checklists не найден для «{name}»")
        if name not in checklists:
            raise KeyError(f"Чек-лист «{name}» не найден")
        return _dump_yaml_fragment({"checklists": {name: checklists[name]}})
    raise KeyError(f"Неизвестный блок: {block_id}")


def merge_block_from_yaml(data: dict[str, Any], block_id: str, text: str) -> dict[str, Any]:
    """Парсит фрагмент YAML и подставляет в копию конфигурации."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(format_yaml_error(e)) from e
    if parsed is None:
        raise ValueError("Фрагмент YAML пуст")
    if not isinstance(parsed, dict):
        raise ValueError("Фрагмент YAML должен быть объектом (mapping)")

    merged = copy.deepcopy(data)
    if block_id == "semantic":
        if "semantic" not in parsed:
            raise ValueError("Ожидается ключ верхнего уровня «semantic»")
        if not isinstance(parsed["semantic"], list):
            raise ValueError("semantic должен быть списком")
        merged["semantic"] = parsed["semantic"]
        return merged

    if block_id == "column_mapping":
        if "column_mapping" not in parsed:
            raise ValueError("Ожидается ключ верхнего уровня «column_mapping»")
        if not isinstance(parsed["column_mapping"], dict):
            raise ValueError("column_mapping должен быть объектом (mapping)")
        merged["column_mapping"] = parsed["column_mapping"]
        return merged

    if block_id == "package":
        if "package" not in parsed:
            raise ValueError("Ожидается ключ верхнего уровня «package»")
        if not isinstance(parsed["package"], dict):
            raise ValueError("package должен быть объектом (mapping)")
        merged["package"] = parsed["package"]
        return merged

    if block_id.startswith("checklist:"):
        name = block_id.split(":", 1)[1]
        checklists = parsed.get("checklists")
        if not isinstance(checklists, dict):
            raise ValueError("Ожидается ключ верхнего уровня «checklists»")
        if name not in checklists:
            raise ValueError(f"В фрагменте нет раздела «{name}»")
        if "checklists" not in merged or not isinstance(merged["checklists"], dict):
            merged["checklists"] = {}
        merged["checklists"][name] = checklists[name]
        return merged

    raise KeyError(f"Неизвестный блок: {block_id}")


def update_ui_settings(path: str | Path, font_family: str, font_size: int) -> None:
    """Сохраняет ui.font_* в config.yaml (перезаписывает файл)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Конфиг не найден: {p}")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Корень config.yaml должен быть объектом")
    ui = data.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        data["ui"] = ui
    ui["font_family"] = str(font_family or "")
    ui["font_size"] = int(font_size or 10)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def update_price_reference_settings(path: str | Path, price_path: str) -> None:
    """Сохраняет price_reference.path в config.yaml (перезаписывает файл)."""
    update_last_paths_settings(path, price_reference_path=price_path)


def update_camera_settings(
    path: str | Path,
    max_width: int,
    max_height: int,
    *,
    capture_with_preview: bool | None = None,
) -> None:
    """Сохраняет настройки camera.* в config.yaml (перезаписывает файл)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Конфиг не найден: {p}")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Корень config.yaml должен быть объектом")
    cam = data.get("camera")
    if not isinstance(cam, dict):
        cam = {}
        data["camera"] = cam
    cam["max_width"] = int(max_width or 0)
    cam["max_height"] = int(max_height or 0)
    if capture_with_preview is not None:
        cam["capture_with_preview"] = bool(capture_with_preview)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def update_last_paths_settings(
    path: str | Path,
    *,
    reference_path: str | None = None,
    template_path: str | None = None,
    output_path: str | None = None,
    photos_dir: str | None = None,
    price_reference_path: str | None = None,
) -> None:
    """Сохраняет последние выбранные пути в config.yaml (перезаписывает файл)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Конфиг не найден: {p}")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Корень config.yaml должен быть объектом")

    if reference_path is not None:
        ref = data.get("reference")
        if not isinstance(ref, dict):
            ref = {}
            data["reference"] = ref
        ref["path"] = str(reference_path or "")

    if template_path is not None:
        tpl = data.get("template")
        if not isinstance(tpl, dict):
            tpl = {}
            data["template"] = tpl
        tpl["path"] = str(template_path or "")

    if output_path is not None:
        data["output_path"] = str(output_path or "")

    if photos_dir is not None:
        data["photos_dir"] = str(photos_dir or "")

    if price_reference_path is not None:
        pr = data.get("price_reference")
        if not isinstance(pr, dict):
            pr = {}
            data["price_reference"] = pr
        pr["path"] = str(price_reference_path or "")

    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / "config.yaml"
