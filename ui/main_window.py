"""SaleMaker main window (PySide6)."""

from __future__ import annotations

import shutil
import sys
import zipfile
from functools import partial
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from camera import Webcam, save_bgr
from photo_crop import (
    crop_central_subject,
    crop_params_from_camera,
    detect_crop_rect,
    draw_crop_overlay,
)
from checklist_templates import (
    TEMPLATE_SKIP_FIELD_IDS,
    find_checklist_template,
    list_template_models_for_equipment,
    load_templates_file,
    remove_checklist_template,
    resolve_templates_path,
    save_templates_file,
    upsert_checklist_template,
)
from config import (
    AppConfig,
    ChecklistField,
    ChecklistSpec,
    checklist_field_has_default,
    default_config_path,
    get_checklist_spec,
    load_config,
    update_camera_settings,
    update_last_paths_settings,
    update_ui_settings,
)
from excel_io import (
    append_template_row,
    build_row_values_from_lookup,
    cell_to_str,
    collect_checklist_values,
    delete_registry_data_row,
    find_row_by_column,
    find_row_by_serial,
    get_checklist_values_from_row,
    load_reference,
    load_registry,
    parse_checklist_start_column,
    update_registry_data_row,
)
from photo_naming import make_photo_path_for_slot
from ui import layout_constants as lc
from ui.config_editor import ConfigEditorWidget
from ui.photo_capture_dialog import run_photo_capture
from ui.scroll_area import ScrollArea
from ui.widgets import (
    CameraPreviewLabel,
    bgr_ndarray_to_qpixmap,
    char_width_px,
    clear_layout,
    compact_form_grid,
    compact_layout,
    grid_add,
    pixmap_from_file,
    set_char_width,
    set_path_entry_style,
)

try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except Exception:
    try:
        cv2.setLogLevel(3)
    except Exception:
        pass


def _app_dir() -> Path:
    return Path(__file__).resolve().parent.parent


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SaleMaker")
        self.setMinimumSize(lc.MIN_WINDOW_W, lc.MIN_WINDOW_H)

        self._cfg: AppConfig | None = None
        self._ref_df: pd.DataFrame | None = None
        self._matched: pd.Series | None = None
        self._registry_df: pd.DataFrame | None = None
        self._current_row_index: int | None = None
        self._last_bgr: object | None = None
        self._webcam = Webcam(0)
        self._photo_paths: dict[int, str] = {}
        self._checklist_getters: dict[str, object] = {}
        self._checklist_widgets_by_id: dict[str, QWidget] = {}

        self.semantic_widgets: dict[str, QComboBox] = {}
        self.template_field_edits: dict[str, QLineEdit] = {}
        self._photo_filenames: dict[int, str] = {}
        self._photo_filename_labels: dict[int, QLabel] = {}

        self._cam_label: CameraPreviewLabel | None = None
        self._cam_fr: QGroupBox | None = None
        self._ph_fr: QGroupBox | None = None
        self._slot_thumb_labels: dict[int, QLabel] = {}
        self._thumb_pixmaps: dict[int, QPixmap] = {}
        self._cam_pixmap: QPixmap | None = None
        self._checklist_frame: QGroupBox | None = None
        self._ph_slots_container: QWidget | None = None

        self._dirty: bool = False
        self._suppress_dirty: bool = False
        self._suppress_price_equipment_sync: bool = False
        self._suppress_equipment_change: bool = False
        self._suppress_model_template: bool = False
        self._header_traces_installed: bool = False

        self._config_file_path: str = ""
        self._price_df: pd.DataFrame | None = None
        self._equipment_cb: QComboBox | None = None
        self._ref_path_entry: QLineEdit | None = None
        self._price_ref_path_entry: QLineEdit | None = None

        self._camera_index: int = 0
        self._camera_choices: list[int] = []
        self._camera_cb: QComboBox | None = None

        self._serial_edit: QLineEdit | None = None
        self._device_pp_edit: QLineEdit | None = None
        self._config_path_edit: QLineEdit | None = None
        self._ref_path_edit: QLineEdit | None = None
        self._template_path_edit: QLineEdit | None = None
        self._output_path_edit: QLineEdit | None = None
        self._photos_dir_edit: QLineEdit | None = None
        self._camera_max_w_spin: QSpinBox | None = None
        self._camera_max_h_spin: QSpinBox | None = None
        self._capture_preview_cb: QCheckBox | None = None
        self._crop_central_subject_cb: QCheckBox | None = None
        self._cam_crop_status: QLabel | None = None
        self._last_crop_detected: bool = False
        self._ui_font_family_cb: QComboBox | None = None
        self._ui_font_size_spin: QSpinBox | None = None

        self._header_extra_label: QLabel | None = None
        self._record_label: QLabel | None = None
        self._status_label: QLabel | None = None
        self._toolbar_serial_label: QLabel | None = None

        self._btn_prev: QPushButton | None = None
        self._btn_next: QPushButton | None = None
        self._btn_save_changes: QPushButton | None = None
        self._btn_delete: QPushButton | None = None

        self._tab1_scroll: ScrollArea | None = None
        self._tab2_scroll: ScrollArea | None = None
        self._tab3_scroll: ScrollArea | None = None
        self._tab4_scroll: ScrollArea | None = None
        self._tab5_scroll: ScrollArea | None = None
        self._config_editor: ConfigEditorWidget | None = None

        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._preview_loop)

        self._build_shell()
        self._try_load_config(str(default_config_path()))
        self._fit_window_to_screen()

    # --- helpers for widget text ---

    def _config_yaml_path(self) -> str:
        if self._config_path_edit:
            t = self._config_path_edit.text().strip()
            if t:
                return t
        if self._config_file_path:
            return self._config_file_path
        return str(default_config_path())

    def _persist_paths_to_config(
        self,
        *,
        reference_path: str | None = None,
        template_path: str | None = None,
        output_path: str | None = None,
        photos_dir: str | None = None,
        price_reference_path: str | None = None,
    ) -> None:
        try:
            update_last_paths_settings(
                self._config_yaml_path(),
                reference_path=reference_path,
                template_path=template_path,
                output_path=output_path,
                photos_dir=photos_dir,
                price_reference_path=price_reference_path,
            )
        except Exception:
            return
        cfg = self._cfg
        if cfg is None:
            return
        if reference_path is not None:
            cfg.reference.path = reference_path
        if template_path is not None:
            cfg.template.path = template_path
        if output_path is not None:
            cfg.output_path = output_path
        if photos_dir is not None:
            cfg.photos_dir = photos_dir
        if price_reference_path is not None:
            cfg.price_reference.path = price_reference_path

    def _apply_paths_from_cfg(self) -> None:
        cfg = self._cfg
        if cfg is None:
            return
        if self._config_path_edit and self._config_file_path:
            self._config_path_edit.setText(self._config_file_path)
        if self._ref_path_entry is not None:
            self._ref_path_entry.setText(cfg.reference.path or "")
        if self._price_ref_path_entry is not None:
            self._price_ref_path_entry.setText(cfg.price_reference.path or "")
        if self._template_path_edit is not None:
            self._template_path_edit.setText(cfg.template.path or "")
        if self._output_path_edit is not None:
            self._output_path_edit.setText(cfg.output_path or "")
        if self._photos_dir_edit is not None:
            photos = (getattr(cfg, "photos_dir", "") or "").strip()
            self._photos_dir_edit.setText(photos or str(_app_dir() / "photos"))
        if self._camera_max_w_spin is not None:
            self._camera_max_w_spin.setValue(int(cfg.camera.max_width))
        if self._camera_max_h_spin is not None:
            self._camera_max_h_spin.setValue(int(cfg.camera.max_height))
        if self._capture_preview_cb is not None:
            self._capture_preview_cb.setChecked(bool(cfg.camera.capture_with_preview))
        if self._crop_central_subject_cb is not None:
            self._crop_central_subject_cb.setChecked(bool(cfg.camera.crop_central_subject))
        if self._ui_font_family_cb is not None:
            self._ui_font_family_cb.setCurrentText(cfg.ui.font_family)
        if self._ui_font_size_spin is not None:
            self._ui_font_size_spin.setValue(int(cfg.ui.font_size))
        self._apply_ui_font(cfg.ui.font_family, int(cfg.ui.font_size))

    def _equipment_text(self) -> str:
        if self._equipment_cb is None:
            return ""
        return self._equipment_cb.currentText().strip()

    def _set_equipment_text(self, text: str) -> None:
        cb = self._equipment_cb
        if cb is None:
            return
        t = (text or "").strip()
        if not t:
            return
        self._suppress_equipment_change = True
        try:
            idx = cb.findText(t, Qt.MatchFlag.MatchExactly)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            else:
                cb.addItem(t)
                cb.setCurrentIndex(cb.count() - 1)
        finally:
            self._suppress_equipment_change = False

    def _set_status(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def _set_record(self, text: str) -> None:
        if self._record_label is not None:
            self._record_label.setText(text)

    # --- header / price reference ---

    def _update_header_extra(self) -> None:
        cfg = self._cfg
        if self._header_extra_label is None:
            return
        if cfg is None:
            self._header_extra_label.setText("")
            return
        key = (cfg.ui.header_field or "").strip()
        if not key:
            self._header_extra_label.setText("")
            return
        label = (cfg.ui.header_label or "").strip() or key

        val = ""
        if key == cfg.reference.template_serial_column or key.lower() == "серийный номер":
            val = self._serial_edit.text().strip() if self._serial_edit else ""
        elif "п/п" in key.lower():
            val = self._device_pp_edit.text().strip() if self._device_pp_edit else ""
        elif key in self.template_field_edits:
            val = self.template_field_edits[key].text().strip()
        else:
            for sf in cfg.semantic:
                if key == sf.key or key == sf.template_column or key == sf.label:
                    w = self.semantic_widgets.get(sf.key)
                    val = w.currentText().strip() if w else ""
                    break

        if val:
            self._header_extra_label.setText(f"{label}: {val}")
        else:
            self._header_extra_label.setText(f"{label}: —")

    def _install_header_traces(self) -> None:
        if self._header_traces_installed:
            return
        self._header_traces_installed = True

        def connect_line(edit: QLineEdit) -> None:
            if getattr(edit, "_salemaker_header", False):
                return
            edit.textChanged.connect(self._update_header_extra)
            edit._salemaker_header = True  # type: ignore[attr-defined]

        def connect_combo(cb: QComboBox) -> None:
            if getattr(cb, "_salemaker_header", False):
                return
            cb.currentTextChanged.connect(self._update_header_extra)
            cb._salemaker_header = True  # type: ignore[attr-defined]

        if self._serial_edit:
            connect_line(self._serial_edit)
        if self._device_pp_edit:
            connect_line(self._device_pp_edit)
        for edit in self.template_field_edits.values():
            connect_line(edit)
        for cb in self.semantic_widgets.values():
            connect_combo(cb)

    def _browse_price_ref(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "", "", "Excel (*.xlsx *.xls)")
        if p and self._price_ref_path_entry is not None:
            self._price_ref_path_entry.setText(p)
            self._persist_paths_to_config(price_reference_path=p)

    def _load_price_reference_data(self, path: str) -> pd.DataFrame:
        cfg = self._cfg
        sheet = cfg.price_reference.sheet if cfg else "Справочник"
        hr = int(cfg.price_reference.header_row if cfg else 3)
        df = pd.read_excel(path, sheet_name=sheet, header=hr - 1)
        need = ["Наименование", "Вендор", "Модель", "Состояние ТМЦ"]
        missing = [c for c in need if c not in df.columns]
        if missing:
            raise ValueError(f"Не найдены колонки: {', '.join(missing)}")
        df = df[need].copy()
        for c in need:
            df[c] = df[c].map(cell_to_str)
        return df[(df["Вендор"] != "") & (df["Модель"] != "") & (df["Наименование"] != "")]

    def _try_load_reference_file(self, path: str) -> tuple[bool, str]:
        p = (path or "").strip()
        if not p:
            return False, "путь не указан"
        if not Path(p).is_file():
            return False, f"файл не найден: {p}"
        try:
            self._ref_df = load_reference(p)
            return True, ""
        except Exception as e:
            self._ref_df = None
            return False, str(e)

    def _try_load_price_reference_file(self, path: str) -> tuple[bool, str]:
        p = (path or "").strip()
        if not p:
            return False, "путь не указан"
        if not Path(p).is_file():
            return False, f"файл не найден: {p}"
        try:
            self._price_df = self._load_price_reference_data(p)
            return True, ""
        except Exception as e:
            self._price_df = None
            return False, str(e)

    def _auto_open_references(self) -> None:
        """Открывает справочники по путям из конфигурации (старт и перезагрузка yaml)."""
        cfg = self._cfg
        if cfg is None:
            return

        self._ref_df = None
        self._price_df = None
        self._matched = None

        ref_path = (cfg.reference.path or "").strip()
        if not ref_path and self._ref_path_entry is not None:
            ref_path = self._ref_path_entry.text().strip()
        price_path = (cfg.price_reference.path or "").strip()
        if not price_path and self._price_ref_path_entry is not None:
            price_path = self._price_ref_path_entry.text().strip()

        problems: list[str] = []
        ref_ok = False
        price_ok = False
        ref_configured = bool(ref_path)
        price_configured = bool(price_path)

        if ref_configured:
            ref_ok, err = self._try_load_reference_file(ref_path)
            if not ref_ok:
                problems.append(f"Справочник Excel: {err}")
        if price_configured:
            price_ok, err = self._try_load_price_reference_file(price_path)
            if not price_ok:
                problems.append(f"Справочник цен: {err}")

        status_parts: list[str] = []
        if ref_ok and self._ref_df is not None:
            status_parts.append(f"Справочник: {len(self._ref_df)} строк.")
        if price_ok and self._price_df is not None:
            status_parts.append(f"Справочник цен: {len(self._price_df)} строк.")
        if status_parts:
            self._set_status(" ".join(status_parts))

        if price_ok:
            self._rebuild_checklist()

        self._update_reference_path_colors()

        if not problems:
            return

        details = "\n".join(f"• {p}" for p in problems)
        if ref_configured and price_configured and not ref_ok and not price_ok:
            QMessageBox.warning(
                self,
                "Справочники",
                f"Не удалось открыть справочники:\n\n{details}\n\n"
                "Программа работает в режиме без справочников.",
            )
        elif ref_configured and not price_configured and not ref_ok:
            QMessageBox.warning(
                self,
                "Справочники",
                f"Не удалось открыть справочник Excel:\n\n{details}\n\n"
                "Программа работает в режиме без справочников.",
            )
        elif price_configured and not ref_configured and not price_ok:
            QMessageBox.warning(
                self,
                "Справочник цен",
                f"Не удалось открыть справочник цен:\n\n{details}\n\n"
                "Программа работает в режиме без справочника цен.",
            )
        else:
            QMessageBox.warning(
                self,
                "Справочники",
                f"Часть справочников недоступна:\n\n{details}",
            )

    def _open_price_reference(self) -> None:
        if self._price_ref_path_entry is None:
            return
        path = self._price_ref_path_entry.text().strip()
        if not path:
            QMessageBox.warning(self, "Справочник цен", "Укажите файл справочника цен.")
            return
        ok, err = self._try_load_price_reference_file(path)
        if not ok:
            QMessageBox.critical(self, "Справочник цен", err)
            return
        assert self._price_df is not None
        self._set_status(f"Справочник цен: {len(self._price_df)} строк.")
        self._update_reference_path_colors()
        self._persist_paths_to_config(price_reference_path=path)
        self._rebuild_checklist()

    def _price_vendors(self) -> list[str]:
        df = self._price_df
        if df is None or df.empty:
            return []
        return sorted(set(df["Вендор"].map(cell_to_str).tolist()))

    def _price_models_for_vendor(self, vendor: str) -> list[str]:
        df = self._price_df
        if df is None or df.empty:
            return []
        v = (vendor or "").strip()
        if not v:
            return []
        sub = df[df["Вендор"].map(cell_to_str) == v]
        return sorted(set(sub["Модель"].map(cell_to_str).tolist()))

    def _price_name_for_vendor_model(self, vendor: str, model: str) -> str:
        df = self._price_df
        if df is None or df.empty:
            return ""
        v = (vendor or "").strip()
        m = (model or "").strip()
        if not v or not m:
            return ""
        sub = df[(df["Вендор"].map(cell_to_str) == v) & (df["Модель"].map(cell_to_str) == m)]
        if sub.empty:
            return ""
        return cell_to_str(sub.iloc[0]["Наименование"])

    def _context_vars_for_templates(self) -> dict[str, str]:
        cfg = self._cfg
        ctx: dict[str, str] = {
            "serial": self._serial_edit.text().strip() if self._serial_edit else "",
            "device_pp": self._device_pp_edit.text().strip() if self._device_pp_edit else "",
            "equipment_type": self._equipment_text(),
        }
        if cfg:
            for k, w in self.semantic_widgets.items():
                ctx[str(k)] = w.currentText().strip()
            for col, edit in self.template_field_edits.items():
                ctx[str(col)] = edit.text().strip()
        return ctx

    def _resolve_dynamic_values(self, raw_values: list[str]) -> list[str]:
        ctx = self._context_vars_for_templates()
        out: list[str] = []
        for rv in raw_values or []:
            s = str(rv)
            try:
                s2 = s.format(**ctx)
            except Exception:
                s2 = s
            s2 = s2.strip()
            if not s2:
                continue
            out.append(s2)
        seen: set[str] = set()
        uniq: list[str] = []
        for x in out:
            if x in seen:
                continue
            seen.add(x)
            uniq.append(x)
        return uniq

    def _resolve_dynamic_default(self, raw_default: str | None) -> str:
        if raw_default is None:
            return ""
        s = str(raw_default).strip()
        if not s or s.lower() == "null":
            return ""
        ctx = self._context_vars_for_templates()
        try:
            s2 = s.format(**ctx)
        except Exception:
            s2 = s
        return s2.strip()

    def _refresh_dynamic_combobox_values(self) -> None:
        cfg = self._cfg
        if cfg is None:
            return
        eq = self._equipment_text()
        spec = get_checklist_spec(cfg, eq)
        if spec is None:
            return
        by_id = {cf.id: cf for cf in (spec.fields or [])}
        for fid, w in self._checklist_widgets_by_id.items():
            if not isinstance(w, QComboBox):
                continue
            cf = by_id.get(fid)
            if cf is None:
                continue
            if fid in ("manufacturer", "model") and self._price_df is not None:
                continue
            if not getattr(cf, "values", None):
                continue
            vals = self._resolve_dynamic_values(list(cf.values))
            cur = w.currentText()
            w.blockSignals(True)
            w.clear()
            w.addItems(vals)
            if cur and cur in vals:
                w.setCurrentText(cur)
            else:
                w.setCurrentIndex(-1)
                w.setCurrentText("")
            w.blockSignals(False)
            if not w.currentText().strip() and checklist_field_has_default(cf):
                d = self._resolve_dynamic_default(cf.default)
                if d:
                    w.setCurrentText(d)

    def _get_checklist_manufacturer_text(self) -> str:
        w = self._checklist_widgets_by_id.get("manufacturer")
        if isinstance(w, QComboBox):
            return w.currentText().strip()
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return ""

    def _template_models_for_equipment(self) -> list[str]:
        cfg = self._cfg
        if cfg is None:
            return []
        eq = self._equipment_text().strip()
        if not eq:
            return []
        try:
            tpl_path = resolve_templates_path(cfg, self._config_yaml_path())
            data = load_templates_file(tpl_path)
        except Exception:
            return []
        return list_template_models_for_equipment(data, eq)

    def _models_for_model_combo(self) -> list[str]:
        """Модели справочника цен по вендору или модели из шаблонов, если вендор пуст."""
        vendor = self._get_checklist_manufacturer_text()
        if vendor:
            return self._price_models_for_vendor(vendor)
        return self._template_models_for_equipment()

    def _on_vendor_changed(self, _text: str = "") -> None:
        w_vendor = self._checklist_widgets_by_id.get("manufacturer")
        if not isinstance(w_vendor, QComboBox):
            return
        vendor = w_vendor.currentText().strip()
        self._refresh_model_combo_for_vendor()
        if vendor and not self._suppress_price_equipment_sync:
            self._on_vendor_model_changed()

    def _refresh_model_combo_for_vendor(self, *, keep_model: str = "") -> None:
        """Список моделей: справочник цен по вендору или шаблоны по типу оборудования."""
        w_model = self._checklist_widgets_by_id.get("model")
        if not isinstance(w_model, QComboBox):
            return
        models = self._models_for_model_combo()
        cur = (keep_model or w_model.currentText()).strip()
        w_model.blockSignals(True)
        w_model.clear()
        if models:
            w_model.addItems(models)
        if cur and cur in models:
            w_model.setCurrentText(cur)
        elif cur and cur not in models:
            w_model.setCurrentIndex(-1)
            w_model.setCurrentText("")
        elif not cur:
            w_model.setCurrentIndex(-1)
            w_model.setCurrentText("")
        w_model.blockSignals(False)

    def _on_vendor_model_changed(self, _text: str = "") -> None:
        if self._suppress_price_equipment_sync or self._suppress_model_template:
            return
        w_vendor = self._checklist_widgets_by_id.get("manufacturer")
        w_model = self._checklist_widgets_by_id.get("model")
        model = ""
        vendor = ""
        if isinstance(w_model, QComboBox):
            model = w_model.currentText().strip()
        elif isinstance(w_model, QLineEdit):
            model = w_model.text().strip()
        if isinstance(w_vendor, QComboBox):
            vendor = w_vendor.currentText().strip()
        elif isinstance(w_vendor, QLineEdit):
            vendor = w_vendor.text().strip()
        if not model:
            return

        if self._price_df is not None and vendor:
            name = self._price_name_for_vendor_model(vendor, model)
            if name and name != self._equipment_text():
                keep_vendor = vendor
                keep_model = model
                self._suppress_model_template = True
                try:
                    self._set_equipment_text(name)
                    self._on_equipment_changed()
                    w_vendor2 = self._checklist_widgets_by_id.get("manufacturer")
                    w_model2 = self._checklist_widgets_by_id.get("model")
                    if isinstance(w_vendor2, QComboBox):
                        w_vendor2.blockSignals(True)
                        w_vendor2.setCurrentText(keep_vendor)
                        w_vendor2.blockSignals(False)
                    if isinstance(w_model2, QComboBox):
                        self._refresh_model_combo_for_vendor(keep_model=keep_model)
                    self._refresh_dynamic_combobox_values()
                finally:
                    self._suppress_model_template = False

        self._apply_model_checklist_template()

    def _get_checklist_model_text(self) -> str:
        w = self._checklist_widgets_by_id.get("model")
        if isinstance(w, QComboBox):
            return w.currentText().strip()
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return ""

    def _apply_checklist_fields_dict(
        self,
        fields_map: dict[str, str],
        spec: ChecklistSpec,
    ) -> None:
        by_id = {cf.id: cf for cf in spec.fields}
        for fid, val in fields_map.items():
            if fid in TEMPLATE_SKIP_FIELD_IDS:
                continue
            cf = by_id.get(fid)
            w = self._checklist_widgets_by_id.get(fid)
            if cf is None or w is None:
                continue
            if cf.type == "textarea" and isinstance(w, QTextEdit):
                w.setPlainText(val)
            elif cf.type == "bool" and isinstance(w, QCheckBox):
                w.setChecked(val in ("Да", "да", "1", "true", "True", "yes", "Yes"))
            elif isinstance(w, QLineEdit):
                w.setText(val)
            elif isinstance(w, QComboBox):
                w.blockSignals(True)
                w.setCurrentText(val)
                w.blockSignals(False)

    def _apply_model_checklist_template(self) -> None:
        cfg = self._cfg
        if cfg is None:
            return
        model = self._get_checklist_model_text()
        if not model:
            return
        eq = self._equipment_text().strip()
        if not eq:
            return
        spec = get_checklist_spec(cfg, eq)
        if spec is None:
            return
        tpl_path = resolve_templates_path(cfg, self._config_yaml_path())
        try:
            data = load_templates_file(tpl_path)
        except Exception as e:
            self._set_status(f"Файл шаблонов: {e}")
            return
        fields_map = find_checklist_template(data, eq, model)
        if not fields_map:
            self._set_status(
                f"Шаблон для модели «{model}» (тип «{eq}») не найден в {tpl_path.name}."
            )
            return
        self._suppress_model_template = True
        self._suppress_price_equipment_sync = True
        try:
            self._apply_checklist_fields_dict(fields_map, spec)
            if "manufacturer" in fields_map and self._price_df is not None:
                self._refresh_model_combo_for_vendor(keep_model=model)
        finally:
            self._suppress_model_template = False
            self._suppress_price_equipment_sync = False
        self._set_status(f"Найден шаблон для модели «{model}» (тип оборудования «{eq}»).")

    def _current_checklist_template_fields(self) -> dict[str, str]:
        active = self._active_checklist_spec()
        if active is None:
            return {}
        _eq, spec = active
        vals = collect_checklist_values(spec.fields, self._checklist_getters)
        out: dict[str, str] = {}
        for cf, v in zip(spec.fields, vals):
            if cf.id in TEMPLATE_SKIP_FIELD_IDS:
                continue
            s = str(v).strip() if v is not None else ""
            if s:
                out[cf.id] = s
        return out

    def _use_checklist_as_template(self) -> None:
        active = self._active_checklist_spec()
        if active is None:
            QMessageBox.warning(
                self,
                "Шаблон",
                "Выберите тип оборудования и убедитесь, что для него задан чек-лист.",
            )
            return
        eq, _spec = active
        model = self._get_checklist_model_text()
        if not model:
            QMessageBox.warning(self, "Шаблон", "Укажите модель оборудования в чек-листе.")
            return
        fields = self._current_checklist_template_fields()
        if not fields:
            QMessageBox.warning(
                self,
                "Шаблон",
                "Нет заполненных полей чек-листа для сохранения (кроме модели).",
            )
            return
        cfg = self._cfg
        assert cfg is not None
        tpl_path = resolve_templates_path(cfg, self._config_yaml_path())
        try:
            data = load_templates_file(tpl_path)
            upsert_checklist_template(data, eq, model, fields)
            save_templates_file(tpl_path, data)
        except Exception as e:
            QMessageBox.critical(self, "Шаблон", str(e))
            return
        self._set_status(
            f"Шаблон сохранён: {tpl_path.name} — «{eq}» / «{model}» ({len(fields)} полей)."
        )

    def _clear_checklist_template(self) -> None:
        active = self._active_checklist_spec()
        if active is None:
            QMessageBox.warning(
                self,
                "Шаблон",
                "Выберите тип оборудования и убедитесь, что для него задан чек-лист.",
            )
            return
        eq, _spec = active
        model = self._get_checklist_model_text()
        if not model:
            QMessageBox.warning(self, "Шаблон", "Укажите модель оборудования в чек-листе.")
            return
        cfg = self._cfg
        assert cfg is not None
        tpl_path = resolve_templates_path(cfg, self._config_yaml_path())
        try:
            data = load_templates_file(tpl_path)
            removed = remove_checklist_template(data, eq, model)
            if removed:
                save_templates_file(tpl_path, data)
        except Exception as e:
            QMessageBox.critical(self, "Шаблон", str(e))
            return
        if not removed:
            QMessageBox.information(
                self,
                "Шаблон",
                f"Запись «{eq}» / «{model}» в файле шаблонов не найдена.",
            )
        self._rebuild_checklist()
        if removed:
            self._set_status(
                f"Шаблон удалён из {tpl_path.name}. Поля чек-листа — по умолчанию из config.yaml."
            )
        else:
            self._set_status("Чек-лист пересобран по умолчанию из config.yaml.")

    # --- dirty tracking ---

    def _set_dirty(self, v: bool) -> None:
        if self._suppress_dirty:
            return
        self._dirty = v

    def _mark_clean(self) -> None:
        self._dirty = False

    def _mark_dirty(self, *_args: object) -> None:
        """Слот для сигналов Qt (без передачи int/str в C++)."""
        self._set_dirty(True)

    def _connect_dirty_line(self, edit: QLineEdit) -> None:
        if getattr(edit, "_salemaker_dirty", False):
            return
        edit.textChanged.connect(self._mark_dirty)
        edit._salemaker_dirty = True  # type: ignore[attr-defined]

    def _connect_dirty_combo(self, cb: QComboBox) -> None:
        if getattr(cb, "_salemaker_dirty", False):
            return
        cb.currentTextChanged.connect(self._mark_dirty)
        cb._salemaker_dirty = True  # type: ignore[attr-defined]

    def _connect_dirty_check(self, ch: QCheckBox) -> None:
        if getattr(ch, "_salemaker_dirty", False):
            return
        ch.stateChanged.connect(self._mark_dirty)
        ch._salemaker_dirty = True  # type: ignore[attr-defined]

    def _connect_dirty_text(self, te: QTextEdit) -> None:
        if getattr(te, "_salemaker_dirty", False):
            return
        te.textChanged.connect(self._mark_dirty)
        te._salemaker_dirty = True  # type: ignore[attr-defined]

    def _install_dirty_traces(self) -> None:
        if self._serial_edit:
            self._connect_dirty_line(self._serial_edit)
        if self._device_pp_edit:
            self._connect_dirty_line(self._device_pp_edit)
        if self._equipment_cb:
            self._connect_dirty_combo(self._equipment_cb)
        for edit in self.template_field_edits.values():
            self._connect_dirty_line(edit)
        for cb in self.semantic_widgets.values():
            self._connect_dirty_combo(cb)
        for w in self._checklist_widgets_by_id.values():
            if isinstance(w, QLineEdit):
                self._connect_dirty_line(w)
            elif isinstance(w, QComboBox):
                self._connect_dirty_combo(w)
            elif isinstance(w, QCheckBox):
                self._connect_dirty_check(w)
            elif isinstance(w, QTextEdit):
                self._connect_dirty_text(w)

    def _maybe_save_dirty_before_leave(self) -> bool:
        if not self._dirty:
            return True
        if self._current_row_index is None:
            r = QMessageBox.question(
                self,
                "Есть изменения",
                "Есть несохранённые изменения. Сохранить в реестр?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Yes:
                self._save_excel()
            self._mark_clean()
            return True

        r = QMessageBox.question(
            self,
            "Есть изменения",
            "Есть несохранённые изменения. Сохранить изменения в текущую строку реестра?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Yes:
            ok = self._save_current_row_updates()
            if not ok:
                return False
        else:
            idx = self._current_row_index
            if idx is not None:
                self._apply_registry_row(idx)
        self._mark_clean()
        return True

    def _on_save_changes_clicked(self) -> None:
        if self._current_row_index is None:
            QMessageBox.information(self, "Сохранение", "Нет выбранной записи реестра (режим новой записи).")
            return
        ok = self._save_current_row_updates()
        if ok:
            self._mark_clean()

    @staticmethod
    def _sanitize_filename_part(s: str) -> str:
        bad = '\\/:*?"<>|'
        out = "".join("_" if ch in bad else ch for ch in (s or "").strip())
        out = "_".join([p for p in out.split() if p])
        return out.strip(" ._")

    def _get_registry_value_for_column(self, df: pd.DataFrame, row_idx: int, col_name: str) -> str:
        cfg = self._cfg
        if cfg is None:
            return ""
        key = (col_name or "").strip()
        if not key:
            return ""
        if key in df.columns:
            return cell_to_str(df.iloc[row_idx][key])
        ec = cfg.column_excel_columns.get(key) if hasattr(cfg, "column_excel_columns") else None
        if ec is not None:
            try:
                c1 = parse_checklist_start_column(ec)
                if 1 <= c1 <= len(df.columns):
                    return cell_to_str(df.iloc[row_idx, c1 - 1])
            except Exception:
                return ""
        return ""

    def _collect_photo_paths_from_registry(self, df: pd.DataFrame) -> list[Path]:
        cfg = self._cfg
        if cfg is None:
            return []
        cols: list[tuple[str | None, int | str | None]] = []
        for spec in cfg.checklists.values():
            for ps in spec.photo_slots:
                cols.append((ps.template_column, getattr(ps, "excel_column", None)))

        photos_dir = Path(self._photos_dir_edit.text().strip() if self._photos_dir_edit else ".")
        seen: set[str] = set()
        out: list[Path] = []

        for r in range(len(df)):
            for name, ec in cols:
                val = ""
                if ec is not None:
                    try:
                        c1 = parse_checklist_start_column(ec)
                        if 1 <= c1 <= len(df.columns):
                            val = cell_to_str(df.iloc[r, c1 - 1])
                    except Exception:
                        val = ""
                elif name and name in df.columns:
                    val = cell_to_str(df.iloc[r][name])
                if not val:
                    continue
                p = Path(val)
                if not p.is_absolute():
                    p = photos_dir / p.name
                if p.is_file():
                    k = str(p.resolve()).lower()
                    if k in seen:
                        continue
                    seen.add(k)
                    out.append(p)
        return out

    def _make_send_package(self) -> None:
        cfg = self._cfg
        if cfg is None:
            QMessageBox.warning(self, "Конфиг", "Загрузите config.yaml.")
            return
        out = (self._output_path_edit.text().strip() if self._output_path_edit else "") or cfg.output_path
        if not out or not Path(out).is_file():
            QMessageBox.warning(self, "Реестр", "Откройте/укажите файл реестра.")
            return

        self._reload_registry_silent()
        df = self._registry_df
        if df is None or len(df) == 0:
            QMessageBox.warning(self, "Реестр", "Реестр пуст или не загружен.")
            return

        base_row = self._current_row_index if self._current_row_index is not None else 0
        base_row = max(0, min(int(base_row), len(df) - 1))

        parts: list[str] = []
        for c in (cfg.package.name_columns or []):
            key = str(c).strip()
            if not key:
                continue
            is_column_ref = (key in df.columns) or (
                hasattr(cfg, "column_excel_columns") and key in (cfg.column_excel_columns or {})
            )
            if is_column_ref:
                v = self._get_registry_value_for_column(df, base_row, key)
                s = self._sanitize_filename_part(v)
                if s:
                    parts.append(s)
            else:
                s = self._sanitize_filename_part(key)
                if s:
                    parts.append(s)
        dt = datetime.now().strftime(cfg.package.datetime_format or "%Y-%m-%d_%H_%M")
        parts.append(self._sanitize_filename_part(dt) or dt)
        base_name = "_".join([p for p in parts if p]) or f"package_{dt}"

        reg_src = Path(out)
        reg_ext = reg_src.suffix or ".xlsx"
        reg_arc_name = f"{base_name}{reg_ext}"
        zip_path = reg_src.with_name(f"{base_name}.zip")

        photo_paths = self._collect_photo_paths_from_registry(df)

        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(reg_src, arcname=reg_arc_name)
                for p in photo_paths:
                    zf.write(p, arcname=str(Path("photos") / p.name))
        except Exception as e:
            QMessageBox.critical(self, "Пакет", str(e))
            return

        self._set_status(f"Пакет сформирован: {zip_path}")

    def _on_new_record_clicked(self) -> None:
        self._save_excel()

    def _registry_duplicate_error(self, ignore_index: int | None = None) -> str | None:
        cfg = self._cfg
        df = self._registry_df
        if cfg is None or df is None or len(df) == 0:
            return None
        serial = self._serial_edit.text().strip() if self._serial_edit else ""
        if not serial:
            return None
        ser_col = cfg.reference.template_serial_column
        if ser_col in df.columns:
            col = df[ser_col].astype(str).str.strip()
            mask = col.str.lower() == serial.lower()
            if ignore_index is not None and 0 <= ignore_index < len(mask):
                mask.iloc[ignore_index] = False
            if mask.any():
                return "Такой серийный номер уже есть в таблице реестра. Запись невозможна."
        pp = self._device_pp_edit.text().strip() if self._device_pp_edit else ""
        pp_col = self._device_pp_template_column(cfg)
        if pp and pp_col:
            ec = cfg.column_excel_columns.get(pp_col) if hasattr(cfg, "column_excel_columns") else None
            if ec is not None:
                try:
                    c1 = parse_checklist_start_column(ec)
                    if 1 <= c1 <= len(df.columns):
                        col = df.iloc[:, c1 - 1].astype(str).str.strip()
                        mask = col == pp
                    else:
                        mask = pd.Series([False] * len(df))
                except Exception:
                    mask = pd.Series([False] * len(df))
            elif pp_col in df.columns:
                col = df[pp_col].astype(str).str.strip()
                mask = col == pp
            else:
                mask = pd.Series([False] * len(df))
            if ignore_index is not None and 0 <= ignore_index < len(mask):
                mask.iloc[ignore_index] = False
            if mask.any():
                return "Такой «Номер по п/п на устройстве» уже есть в реестре. Запись невозможна."
        return None

    def _save_current_row_updates(self) -> bool:
        cfg = self._cfg
        if cfg is None:
            QMessageBox.warning(self, "Конфиг", "Загрузите config.yaml.")
            return False
        idx = self._current_row_index
        if idx is None:
            return False
        out = (self._output_path_edit.text().strip() if self._output_path_edit else "") or cfg.output_path
        if not out:
            QMessageBox.warning(self, "Файл", "Укажите файл реестра.")
            return False
        if not Path(out).is_file():
            QMessageBox.warning(self, "Реестр", "Файл реестра не найден.")
            return False

        self._reload_registry_silent()
        dup_msg = self._registry_duplicate_error(ignore_index=idx)
        if dup_msg:
            QMessageBox.warning(self, "Дубликат в реестре", dup_msg)
            return False

        serial = self._serial_edit.text().strip() if self._serial_edit else ""
        if not serial:
            QMessageBox.warning(self, "Серийный номер", "Введите серийный номер.")
            return False
        if not self._validate_required_photos():
            return False

        eq = self._equipment_text()
        spec = get_checklist_spec(cfg, eq)
        if spec is None:
            QMessageBox.warning(self, "Конфиг", f"Нет чек-листа для типа «{eq}» в config.yaml.")
            return False

        if not self._validate_required_inputs(spec):
            return False

        chk_vals = collect_checklist_values(spec.fields, self._checklist_getters)
        sem = {k: w.currentText() for k, w in self.semantic_widgets.items()}
        fe = {k: v.text().strip() for k, v in self.template_field_edits.items()}

        photo_raw = self._build_photo_dict_for_save(spec.photo_slots)
        photo_for_row: dict[int, str] = {}
        for ps in spec.photo_slots:
            raw = (photo_raw.get(ps.slot) or "").strip()
            photo_for_row[ps.slot] = Path(raw).name if raw else ""

        row_dict = build_row_values_from_lookup(
            cfg,
            None,
            serial,
            cfg.reference.template_serial_column,
            fe,
            sem,
            eq,
            photo_for_row,
            spec.photo_slots,
        )

        direct_cols: dict[int, str] = {}
        for tmpl_col, ec in (cfg.column_excel_columns.items() if hasattr(cfg, "column_excel_columns") else []):
            try:
                c1 = parse_checklist_start_column(ec)
            except Exception:
                continue
            if tmpl_col in row_dict:
                direct_cols[c1] = str(row_dict.get(tmpl_col, "") or "")
        for sf in cfg.semantic:
            if getattr(sf, "excel_column", None):
                try:
                    c1 = parse_checklist_start_column(sf.excel_column)
                    direct_cols[c1] = str(sem.get(sf.key, "") or "")
                except Exception:
                    continue
        for ps in spec.photo_slots:
            if getattr(ps, "excel_column", None):
                try:
                    c1 = parse_checklist_start_column(ps.excel_column)
                    direct_cols[c1] = photo_for_row.get(ps.slot, "")
                except Exception:
                    continue

        try:
            update_registry_data_row(
                out,
                cfg,
                idx,
                row_dict,
                chk_vals,
                spec.checklist_start_column,
                direct_columns=direct_cols or None,
            )
        except Exception as e:
            QMessageBox.critical(self, "Excel", str(e))
            return False

        self._reload_registry_silent()
        if self._registry_df is not None and 0 <= idx < len(self._registry_df):
            self._apply_registry_row(idx)
        self._update_record_indicator()
        self._set_status("Изменения сохранены в текущую строку реестра.")
        return True

    def _apply_ui_font(self, family: str, size: int) -> None:
        fam = (family or "").strip()
        sz = int(size) if int(size) > 0 else 10
        app = QApplication.instance()
        if app is None:
            return
        font = app.font()
        if fam:
            font.setFamily(fam)
        font.setPointSize(sz)
        app.setFont(font)

    def _on_apply_font_clicked(self) -> None:
        fam = self._ui_font_family_cb.currentText() if self._ui_font_family_cb else ""
        sz = int(self._ui_font_size_spin.value()) if self._ui_font_size_spin else 10
        self._apply_ui_font(fam, sz)
        try:
            if self._config_path_edit:
                update_ui_settings(self._config_path_edit.text(), fam, sz)
            self._set_status("Шрифт интерфейса применён и сохранён в config.yaml.")
        except Exception as e:
            self._set_status(f"Шрифт применён, но не сохранён в config.yaml: {e}")

    def _get_font_families(self) -> list[str]:
        try:
            fams = sorted(set(QFontDatabase.families()))
        except Exception:
            fams = []
        return [""] + fams

    @staticmethod
    def _device_pp_template_column(cfg: AppConfig) -> str | None:
        def norm(s: object) -> str:
            return "".join(str(s or "").lower().split())

        want = norm("Номер по п/п на устройстве")
        for tmpl, ref in cfg.column_mapping.items():
            if want in norm(tmpl) or want in norm(ref):
                return tmpl
        for tmpl, ref in cfg.column_mapping.items():
            if "п/п" in str(tmpl).lower() or "п/п" in str(ref).lower():
                return tmpl
        return None

    def _device_pp_reference_column(self, cfg: AppConfig) -> str:
        key = self._device_pp_template_column(cfg)
        if key and key in cfg.column_mapping:
            return cfg.column_mapping[key]
        return "Номер по п/п на устройстве"

    def _fit_window_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        sw, sh = geo.width(), geo.height()
        w = min(lc.WIN_W_MAX, max(lc.WIN_W_MIN, int(sw * lc.WIN_SCREEN_RATIO)))
        h = max(lc.MIN_WINDOW_H, int(sh * lc.WIN_SCREEN_RATIO))
        if h + lc.WIN_MARGIN > sh:
            h = sh - lc.WIN_MARGIN
        x = max(geo.x(), geo.x() + (sw - w) // 2)
        y = max(geo.y(), geo.y() + (sh - h) // 2 - lc.WIN_Y_OFFSET)
        self.setGeometry(x, y, w, h)

    def _h_separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _build_shell(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(*lc.SHELL_PAD)

        top = QHBoxLayout()
        top.addWidget(QLabel("SaleMaker"))
        self._header_extra_label = QLabel("")
        top.addWidget(self._header_extra_label)
        top.addStretch()
        root.addLayout(top)

        search = QGroupBox("Серийный номер и поиск в справочнике")
        search_l = QGridLayout(search)
        search_l.setContentsMargins(lc.SEARCH_PAD, lc.SEARCH_PAD, lc.SEARCH_PAD, lc.SEARCH_PAD)
        pad = lc.GRID_PAD
        self._toolbar_serial_label = QLabel("Серийный номер")
        grid_add(search_l, self._toolbar_serial_label, 0, 0)
        self._serial_edit = QLineEdit()
        set_char_width(self._serial_edit, lc.ENTRY_W_SERIAL)
        grid_add(search_l, self._serial_edit, 0, 1)
        btn_find = QPushButton("Найти в справочнике")
        btn_find.clicked.connect(self._find_row)
        grid_add(search_l, btn_find, 0, 2)
        grid_add(search_l, QLabel("Номер по п/п на устройстве"), 0, 3)
        self._device_pp_edit = QLineEdit()
        set_char_width(self._device_pp_edit, lc.ENTRY_W_DEVICE_PP)
        grid_add(search_l, self._device_pp_edit, 0, 4)
        btn_pp = QPushButton("Найти по п/п")
        btn_pp.clicked.connect(self._find_row_by_device_pp)
        grid_add(search_l, btn_pp, 0, 5)
        search_l.setColumnStretch(6, 1)
        self._record_label = QLabel("—")
        self._record_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_add(search_l, self._record_label, 0, 7)
        root.addWidget(search)

        nav = QHBoxLayout()
        nav.setContentsMargins(8, 2, 8, 6)
        self._btn_prev = QPushButton("← Предыдущая запись")
        self._btn_prev.clicked.connect(self._nav_prev)
        nav.addWidget(self._btn_prev)
        self._btn_next = QPushButton("Следующая запись →")
        self._btn_next.clicked.connect(self._nav_next)
        nav.addWidget(self._btn_next)
        btn_new = QPushButton("Новая запись")
        btn_new.clicked.connect(self._on_new_record_clicked)
        nav.addWidget(btn_new)
        self._btn_save_changes = QPushButton("Сохранить изменения")
        self._btn_save_changes.clicked.connect(self._on_save_changes_clicked)
        nav.addWidget(self._btn_save_changes)
        self._btn_delete = QPushButton("Удалить текущую запись")
        self._btn_delete.clicked.connect(self._delete_current_record)
        nav.addWidget(self._btn_delete)
        nav.addStretch()
        btn_package = QPushButton("Сформировать пакет на отправку")
        btn_package.clicked.connect(self._make_send_package)
        nav.addWidget(btn_package)
        btn_save_reg = QPushButton("Записать в реестр")
        btn_save_reg.clicked.connect(self._save_excel)
        nav.addWidget(btn_save_reg)
        root.addLayout(nav)

        self._notebook = QTabWidget()
        self._tab1_scroll = ScrollArea()
        self._tab2_scroll = ScrollArea()
        self._tab3_scroll = ScrollArea()
        self._tab4_scroll = ScrollArea()
        self._tab5_scroll = ScrollArea()
        self._notebook.addTab(self._tab1_scroll, "Общие настройки и справочник")
        self._notebook.addTab(self._tab2_scroll, "Единица оборудования")
        self._notebook.addTab(self._tab3_scroll, "Чек-лист")
        self._notebook.addTab(self._tab4_scroll, "Фотографирование")
        self._config_editor = ConfigEditorWidget(
            on_reload_config=self._try_load_config,
            get_active_config_path=self._config_yaml_path,
        )
        tab5_lay = QVBoxLayout(self._tab5_scroll.inner)
        tab5_lay.setContentsMargins(0, 0, 0, 0)
        tab5_lay.addWidget(self._config_editor)
        self._notebook.addTab(self._tab5_scroll, "Редактор конфигурации")
        self._notebook.currentChanged.connect(self._on_notebook_tab_changed)
        root.addWidget(self._notebook, stretch=1)

        self._set_status("Загрузите config.yaml и справочник.")
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setMaximumWidth(lc.STATUS_WRAP)
        root.addWidget(self._status_label)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_camera()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._notebook is not None and self._notebook.currentIndex() == 3:
            QTimer.singleShot(0, self._sync_camera_preview_layout)

    def _on_notebook_tab_changed(self, _index: int) -> None:
        if self._notebook is not None and self._notebook.currentIndex() == 3:
            QTimer.singleShot(0, self._sync_camera_preview_layout)

    def _sync_camera_preview_layout(self) -> None:
        """Высота превью камеры ≈ высоте сетки слотов 1–4, чтобы блок влезал в 1080p."""
        if self._cam_label is None or self._ph_slots_container is None:
            return
        cfg = self._cfg
        n_slots = 4
        if cfg is not None:
            spec = get_checklist_spec(cfg, self._equipment_text())
            if spec and spec.photo_slots:
                n_slots = len(spec.photo_slots)

        self._ph_slots_container.adjustSize()
        slots_h = self._ph_slots_container.sizeHint().height()
        if slots_h < 80:
            slots_h = lc.photo_slots_content_height(n_slots)

        preview_h = max(
            lc.PREVIEW_MIN_H,
            int((slots_h - lc.PHOTO_PREVIEW_SLOTS_MARGIN) * lc.PHOTO_PREVIEW_SIZE_FACTOR),
        )
        preview_h = min(preview_h, lc.PREVIEW_MAX_H)

        tab_w = self._tab4_scroll.viewport().width() if self._tab4_scroll else lc.PHOTO_TAB_REF_W
        if tab_w < 400:
            tab_w = lc.PHOTO_TAB_REF_W
        cam_col_w = int(tab_w * lc.PHOTO_COL_CAMERA / (lc.PHOTO_COL_CAMERA + lc.PHOTO_COL_SLOTS))
        cam_col_w = max(lc.PREVIEW_MIN_W, cam_col_w - 40)

        preview_w = int(preview_h * 16 / 9)
        if preview_w > cam_col_w:
            preview_w = cam_col_w
            preview_h = max(lc.PREVIEW_MIN_H, int(preview_w * 9 / 16))
        preview_w = max(lc.PREVIEW_MIN_W, min(preview_w, lc.PREVIEW_MAX_W))
        preview_h = max(lc.PREVIEW_MIN_H, min(preview_h, lc.PREVIEW_MAX_H))

        self._cam_label.set_max_preview_size(preview_w, preview_h)
        if self._cam_pixmap is not None and not self._cam_pixmap.isNull():
            self._cam_label.refresh_display()

        if self._cam_fr is not None:
            self._cam_fr.setMaximumHeight(16777215)
        if self._ph_fr is not None:
            self._ph_fr.setMaximumHeight(16777215)

    def _browse_config(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self,
            "config.yaml",
            "",
            "YAML (*.yaml *.yml);;Все файлы (*.*)",
        )
        if p and self._config_path_edit:
            self._config_path_edit.setText(p)
            self._try_load_config(p)

    def _reload_config(self) -> None:
        if self._config_path_edit:
            self._try_load_config(self._config_path_edit.text())

    def _try_load_config(self, path: str) -> None:
        try:
            cfg = load_config(path)
        except Exception as e:
            QMessageBox.critical(self, "Конфиг", str(e))
            return
        self._cfg = cfg
        self._config_file_path = str(Path(path).resolve())
        self._rebuild_ui()
        self._update_header_extra()
        self._auto_open_references()

    def _clear_tab(self, scroll: ScrollArea) -> None:
        """Полностью сбрасывает содержимое вкладки (новый inner, старый удаляет QScrollArea)."""
        policy = scroll.inner.sizePolicy()
        new_inner = QWidget()
        new_inner.setSizePolicy(policy)
        scroll.setWidget(new_inner)
        scroll.inner = new_inner

    def _rebuild_ui(self) -> None:
        cfg = self._cfg
        if cfg is None:
            return
        for scroll in (self._tab1_scroll, self._tab2_scroll, self._tab3_scroll, self._tab4_scroll):
            if scroll is not None:
                self._clear_tab(scroll)

        self.template_field_edits.clear()
        self.semantic_widgets.clear()
        self._checklist_getters.clear()
        self._checklist_widgets_by_id.clear()
        self._photo_paths.clear()
        self._slot_thumb_labels.clear()
        self._thumb_pixmaps.clear()
        self._cam_label = None
        self._cam_fr = None
        self._ph_fr = None
        self._checklist_frame = None
        self._ph_slots_container = None
        self._photo_filenames.clear()
        self._header_traces_installed = False

        if self._toolbar_serial_label:
            text = cfg.reference.template_serial_column
            if not text.strip().endswith("*"):
                text = f"{text.strip()} *"
            self._toolbar_serial_label.setText(text)

        self._build_tab1()
        self._build_tab2()
        self._build_tab3()
        self._build_tab4()
        self._reload_registry_silent()
        self._update_record_indicator()
        self._refresh_all_thumbs()
        self._install_dirty_traces()
        self._install_header_traces()
        self._update_header_extra()
        self._update_reference_path_colors()
        self._mark_clean()

    def _update_reference_path_colors(self) -> None:
        if self._ref_path_entry is not None:
            set_path_entry_style(self._ref_path_entry, self._ref_df is not None)
        if self._price_ref_path_entry is not None:
            set_path_entry_style(self._price_ref_path_entry, self._price_df is not None)

    def _build_tab1(self) -> None:
        cfg = self._cfg
        assert cfg is not None
        assert self._tab1_scroll is not None
        f = self._tab1_scroll.inner
        grid = f.layout()
        if grid is None:
            grid = QGridLayout(f)
            f.setLayout(grid)
        compact_form_grid(grid)
        row = 0

        grid_add(grid, QLabel("Конфигурация (config.yaml)"), row, 0)
        self._config_path_edit = QLineEdit(self._config_file_path or str(default_config_path()))
        set_char_width(self._config_path_edit, lc.ENTRY_W_PATH)
        grid_add(grid, self._config_path_edit, row, 1)
        btn_browse_cfg = QPushButton("Открыть…")
        btn_browse_cfg.clicked.connect(self._browse_config)
        grid_add(grid, btn_browse_cfg, row, 2)
        btn_reload = QPushButton("Загрузить")
        btn_reload.clicked.connect(self._reload_config)
        grid_add(grid, btn_reload, row, 3)
        row += 1

        grid_add(grid, self._h_separator(), row, 0, 1, 4)
        row += 1

        grid_add(grid, QLabel("Справочник Excel"), row, 0)
        self._ref_path_entry = QLineEdit()
        self._ref_path_edit = self._ref_path_entry
        set_char_width(self._ref_path_entry, lc.ENTRY_W_PATH_WIDE)
        grid_add(grid, self._ref_path_entry, row, 1)
        btn_ref = QPushButton("Обзор…")
        btn_ref.clicked.connect(self._browse_ref)
        grid_add(grid, btn_ref, row, 2)
        btn_open_ref = QPushButton("Открыть")
        btn_open_ref.clicked.connect(self._open_reference)
        grid_add(grid, btn_open_ref, row, 3)
        row += 1

        grid_add(grid, QLabel("Справочник цен (лист «Справочник», строка 3)"), row, 0)
        self._price_ref_path_entry = QLineEdit()
        set_char_width(self._price_ref_path_entry, lc.ENTRY_W_PATH_WIDE)
        grid_add(grid, self._price_ref_path_entry, row, 1)
        btn_price = QPushButton("Обзор…")
        btn_price.clicked.connect(self._browse_price_ref)
        grid_add(grid, btn_price, row, 2)
        btn_open_price = QPushButton("Открыть")
        btn_open_price.clicked.connect(self._open_price_reference)
        grid_add(grid, btn_open_price, row, 3)
        row += 1

        grid_add(grid, QLabel("Шаблон Excel"), row, 0)
        self._template_path_edit = QLineEdit()
        set_char_width(self._template_path_edit, lc.ENTRY_W_PATH)
        grid_add(grid, self._template_path_edit, row, 1)
        btn_tpl = QPushButton("Обзор…")
        btn_tpl.clicked.connect(self._browse_template)
        grid_add(grid, btn_tpl, row, 2)
        row += 1

        grid_add(grid, QLabel("Файл реестра (запись строк)"), row, 0)
        self._output_path_edit = QLineEdit()
        set_char_width(self._output_path_edit, lc.ENTRY_W_PATH)
        grid_add(grid, self._output_path_edit, row, 1)
        btn_out = QPushButton("Обзор…")
        btn_out.clicked.connect(self._browse_output)
        grid_add(grid, btn_out, row, 2)
        row += 1

        grid_add(grid, QLabel("Папка фото"), row, 0)
        self._photos_dir_edit = QLineEdit()
        set_char_width(self._photos_dir_edit, lc.ENTRY_W_PATH)
        grid_add(grid, self._photos_dir_edit, row, 1)
        btn_photos = QPushButton("Обзор…")
        btn_photos.clicked.connect(self._browse_photos)
        grid_add(grid, btn_photos, row, 2)
        row += 1

        grid_add(grid, QLabel("Макс. разрешение камеры (W×H)"), row, 0)
        res_fr = QHBoxLayout()
        self._camera_max_w_spin = QSpinBox()
        self._camera_max_w_spin.setRange(0, 10000)
        self._camera_max_w_spin.setMinimumWidth(char_width_px(self._camera_max_w_spin, lc.SPIN_W_CAMERA))
        res_fr.addWidget(self._camera_max_w_spin)
        res_fr.addWidget(QLabel("×"))
        self._camera_max_h_spin = QSpinBox()
        self._camera_max_h_spin.setRange(0, 10000)
        self._camera_max_h_spin.setMinimumWidth(char_width_px(self._camera_max_h_spin, lc.SPIN_W_CAMERA))
        res_fr.addWidget(self._camera_max_h_spin)
        res_w = QWidget()
        res_w.setLayout(res_fr)
        grid_add(grid, res_w, row, 1)
        btn_cam_save = QPushButton("Сохранить")
        btn_cam_save.clicked.connect(self._save_camera_settings)
        grid_add(grid, btn_cam_save, row, 2)
        row += 1

        self._capture_preview_cb = QCheckBox(
            "Фото с превью (полный экран перед съёмкой; пробел или щелчок — снять, Esc — отмена)"
        )
        self._capture_preview_cb.toggled.connect(self._on_capture_preview_toggled)
        grid_add(grid, self._capture_preview_cb, row, 0, 1, 4)
        row += 1

        self._crop_central_subject_cb = QCheckBox(
            "Авто-кадрирование: сохранять только центральный объект (обрезка по контуру)"
        )
        self._crop_central_subject_cb.toggled.connect(self._on_crop_central_subject_toggled)
        grid_add(grid, self._crop_central_subject_cb, row, 0, 1, 4)
        row += 1

        grid_add(grid, self._h_separator(), row, 0, 1, 4)
        row += 1

        grid_add(grid, QLabel("Постоянные значения (для всех записей)"), row, 0, 1, 4)
        row += 1

        for sf in cfg.semantic:
            lbl = f"{sf.label} *" if getattr(sf, "required", False) else sf.label
            grid_add(grid, QLabel(lbl), row, 0)
            cb = QComboBox()
            cb.addItems(list(sf.values or []))
            cb.setEditable(not bool(sf.values))
            set_char_width(cb, lc.ENTRY_W_PATH)
            if sf.values:
                cb.setCurrentIndex(0)
            self.semantic_widgets[sf.key] = cb
            grid_add(grid, cb, row, 1, 1, 3)
            row += 1

        grid_add(grid, self._h_separator(), row, 0, 1, 4)
        row += 1

        grid_add(grid, QLabel("Интерфейс"), row, 0, 1, 4)
        row += 1
        grid_add(grid, QLabel("Шрифт (семейство)"), row, 0)
        self._ui_font_family_cb = QComboBox()
        self._ui_font_family_cb.addItems(self._get_font_families())
        self._ui_font_family_cb.setEditable(False)
        set_char_width(self._ui_font_family_cb, lc.COMBO_W_FONT)
        grid_add(grid, self._ui_font_family_cb, row, 1)
        size_lbl = QLabel("Размер")
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_add(grid, size_lbl, row, 2)
        self._ui_font_size_spin = QSpinBox()
        self._ui_font_size_spin.setRange(7, 16)
        self._ui_font_size_spin.setValue(10)
        self._ui_font_size_spin.setMinimumWidth(char_width_px(self._ui_font_size_spin, lc.SPIN_W_FONT))
        grid_add(grid, self._ui_font_size_spin, row, 3)
        row += 1
        btn_font = QPushButton("Применить шрифт")
        btn_font.clicked.connect(self._on_apply_font_clicked)
        grid_add(grid, btn_font, row, 0, 1, 4)
        grid.setColumnStretch(1, 1)

        self._apply_paths_from_cfg()
        self._ref_path_entry.editingFinished.connect(
            lambda: self._persist_paths_to_config(
                reference_path=self._ref_path_entry.text().strip()
            )
        )
        self._price_ref_path_entry.editingFinished.connect(
            lambda: self._persist_paths_to_config(
                price_reference_path=self._price_ref_path_entry.text().strip()
            )
        )
        self._template_path_edit.editingFinished.connect(
            lambda: self._persist_paths_to_config(
                template_path=self._template_path_edit.text().strip()
            )
        )
        self._output_path_edit.editingFinished.connect(
            lambda: self._persist_paths_to_config(
                output_path=self._output_path_edit.text().strip()
            )
        )
        self._photos_dir_edit.editingFinished.connect(
            lambda: self._persist_paths_to_config(
                photos_dir=self._photos_dir_edit.text().strip()
            )
        )

    def _build_tab2(self) -> None:
        cfg = self._cfg
        assert cfg is not None
        assert self._tab2_scroll is not None
        f = self._tab2_scroll.inner
        f.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(f)
        compact_layout(root)

        # Фиксированный блок: одна строка (подпись + тип оборудования)
        eq_group = QGroupBox("Тип оборудования")
        eq_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        eq_row = QHBoxLayout(eq_group)
        compact_layout(eq_row)
        eq_lbl = QLabel(cfg.equipment_type_column)
        eq_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        eq_row.addWidget(eq_lbl)
        self._equipment_cb = QComboBox()
        self._equipment_cb.addItems(list(cfg.equipment_types or []))
        self._equipment_cb.setEditable(False)
        if cfg.equipment_types:
            self._equipment_cb.setCurrentIndex(0)
        self._equipment_cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        eq_row.addWidget(self._equipment_cb, stretch=1)
        self._equipment_cb.currentIndexChanged.connect(self._on_equipment_index_changed)
        root.addWidget(eq_group, stretch=0)

        # Динамический блок: занимает оставшуюся высоту вкладки
        data_group = QGroupBox("Данные из справочника")
        data_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        data_outer = QVBoxLayout(data_group)
        compact_layout(data_outer)

        data_scroll = QScrollArea()
        data_scroll.setWidgetResizable(True)
        data_scroll.setFrameShape(QFrame.Shape.NoFrame)
        data_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        data_inner = QWidget()
        data_grid = QGridLayout()
        data_inner.setLayout(data_grid)
        compact_form_grid(data_grid)
        data_scroll.setWidget(data_inner)
        data_outer.addWidget(data_scroll, stretch=1)

        drow = 0
        pp_key = self._device_pp_template_column(cfg)
        tsc = cfg.reference.template_serial_column
        col_req = getattr(cfg, "column_required", {}) or {}
        for tmpl_col in cfg.column_mapping.keys():
            if tmpl_col == cfg.equipment_type_column:
                continue
            if tmpl_col == tsc:
                continue
            if pp_key and tmpl_col == pp_key and self._device_pp_edit is not None:
                self.template_field_edits[tmpl_col] = self._device_pp_edit
                continue
            nm = tmpl_col[:64]
            if col_req.get(tmpl_col, False):
                nm = f"{nm} *"
            lbl = QLabel(nm)
            lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            grid_add(data_grid, lbl, drow, 0)
            edit = QLineEdit()
            edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.template_field_edits[tmpl_col] = edit
            grid_add(data_grid, edit, drow, 1)
            drow += 1

        data_grid.setColumnStretch(1, 1)
        root.addWidget(data_group, stretch=1)

    def _build_tab3(self) -> None:
        assert self._tab3_scroll is not None
        f = self._tab3_scroll.inner
        grid = f.layout()
        if grid is None:
            grid = QGridLayout(f)
            f.setLayout(grid)

        compact_form_grid(grid)
        self._checklist_frame = QGroupBox("Чек-лист")
        cl = QFormLayout(self._checklist_frame)
        compact_layout(cl)
        cl.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        grid_add(grid, self._checklist_frame, 0, 0)
        grid.setColumnStretch(0, 1)

        btn_row_w = QWidget()
        btn_row = QHBoxLayout(btn_row_w)
        compact_layout(btn_row)
        btn_use_tpl = QPushButton("Использовать как шаблон")
        btn_use_tpl.clicked.connect(self._use_checklist_as_template)
        btn_row.addWidget(btn_use_tpl)
        btn_clear_tpl = QPushButton("Очистить шаблон")
        btn_clear_tpl.clicked.connect(self._clear_checklist_template)
        btn_row.addWidget(btn_clear_tpl)
        btn_row.addStretch(1)
        grid_add(grid, btn_row_w, 1, 0)

        self._rebuild_checklist()

    def _build_tab4(self) -> None:
        assert self._tab4_scroll is not None
        f = self._tab4_scroll.inner
        outer = f.layout()
        if outer is None:
            outer = QGridLayout(f)
            f.setLayout(outer)

        layout = QGridLayout()
        compact_layout(layout)
        outer.addLayout(layout, 0, 0, 1, 1)

        layout.setColumnStretch(0, lc.PHOTO_COL_CAMERA)
        layout.setColumnStretch(1, lc.PHOTO_COL_SLOTS)
        layout.setRowStretch(0, 0)

        cam_fr = QGroupBox("Камера")
        self._cam_fr = cam_fr
        cam_fr.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        cam_l = QVBoxLayout(cam_fr)
        compact_layout(cam_l)
        self._cam_label = CameraPreviewLabel()
        self._cam_label.set_minimum_preview_size(lc.PREVIEW_MIN_W, lc.PREVIEW_MIN_H)
        self._cam_label.set_max_preview_size(lc.PREVIEW_MAX_W, lc.PREVIEW_MAX_H)
        cam_l.addWidget(self._cam_label, stretch=0)
        self._cam_crop_status = QLabel("")
        self._cam_crop_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_crop_status.setWordWrap(True)
        self._cam_crop_status.hide()
        cam_l.addWidget(self._cam_crop_status, stretch=0)
        bf = QHBoxLayout()
        bf.addWidget(QLabel("Камера:"))
        self._camera_cb = QComboBox()
        set_char_width(self._camera_cb, lc.COMBO_W_CAMERA)
        self._camera_cb.setEditable(False)
        self._camera_cb.currentTextChanged.connect(self._on_camera_selected)
        bf.addWidget(self._camera_cb)
        btn_refresh = QPushButton("Обновить")
        btn_refresh.clicked.connect(self._refresh_camera_list)
        bf.addWidget(btn_refresh)
        btn_start = QPushButton("Включить")
        btn_start.clicked.connect(self._start_camera)
        bf.addWidget(btn_start)
        btn_stop = QPushButton("Выключить")
        btn_stop.clicked.connect(self._stop_camera)
        bf.addWidget(btn_stop)
        cam_l.addLayout(bf)
        grid_add(layout, cam_fr, 0, 0)

        ph_fr = QGroupBox("Слоты фото")
        self._ph_fr = ph_fr
        ph_fr.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        ph_l = QVBoxLayout(ph_fr)
        compact_layout(ph_l)
        self._ph_slots_container = QWidget()
        self._ph_slots_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        ph_l.addWidget(self._ph_slots_container)
        grid_add(layout, ph_fr, 0, 1)

        self._rebuild_photo_slots()
        QTimer.singleShot(0, self._sync_camera_preview_layout)
        QTimer.singleShot(0, self._refresh_camera_list)

    def _save_camera_settings(self) -> None:
        try:
            if self._config_path_edit and self._camera_max_w_spin and self._camera_max_h_spin:
                preview = (
                    self._capture_preview_cb.isChecked()
                    if self._capture_preview_cb is not None
                    else None
                )
                crop = (
                    self._crop_central_subject_cb.isChecked()
                    if self._crop_central_subject_cb is not None
                    else None
                )
                update_camera_settings(
                    self._config_path_edit.text(),
                    int(self._camera_max_w_spin.value()),
                    int(self._camera_max_h_spin.value()),
                    capture_with_preview=preview,
                    crop_central_subject=crop,
                )
                if self._cfg is not None:
                    if preview is not None:
                        self._cfg.camera.capture_with_preview = preview
                    if crop is not None:
                        self._cfg.camera.crop_central_subject = crop
            self._set_status("Настройки камеры сохранены в config.yaml.")
        except Exception as e:
            self._set_status(f"Не удалось сохранить настройки камеры: {e}")

    def _on_capture_preview_toggled(self, checked: bool) -> None:
        if self._cfg is not None:
            self._cfg.camera.capture_with_preview = checked
        try:
            if self._config_path_edit and self._camera_max_w_spin and self._camera_max_h_spin:
                update_camera_settings(
                    self._config_yaml_path(),
                    int(self._camera_max_w_spin.value()),
                    int(self._camera_max_h_spin.value()),
                    capture_with_preview=checked,
                )
        except Exception:
            pass

    def _on_crop_central_subject_toggled(self, checked: bool) -> None:
        if self._cfg is not None:
            self._cfg.camera.crop_central_subject = checked
        self._last_crop_detected = False
        if not checked and self._cam_crop_status is not None:
            self._cam_crop_status.hide()
        try:
            if self._config_path_edit and self._camera_max_w_spin and self._camera_max_h_spin:
                update_camera_settings(
                    self._config_yaml_path(),
                    int(self._camera_max_w_spin.value()),
                    int(self._camera_max_h_spin.value()),
                    crop_central_subject=checked,
                )
        except Exception:
            pass

    def _crop_central_subject_enabled(self) -> bool:
        if self._crop_central_subject_cb is not None:
            return self._crop_central_subject_cb.isChecked()
        cfg = self._cfg
        return bool(cfg and cfg.camera.crop_central_subject)

    def _camera_crop_params(self):
        cfg = self._cfg
        if cfg is None:
            from photo_crop import CropParams

            return CropParams()
        return crop_params_from_camera(cfg.camera)

    def _prepare_camera_preview_bgr(self, bgr: np.ndarray) -> tuple[np.ndarray, bool]:
        if not self._crop_central_subject_enabled():
            self._last_crop_detected = False
            if self._cam_crop_status is not None:
                self._cam_crop_status.hide()
            return bgr, False
        params = self._camera_crop_params()
        detected = detect_crop_rect(bgr, params)
        found = detected.found
        self._last_crop_detected = found
        if self._cam_crop_status is not None:
            if found:
                self._cam_crop_status.setText(
                    "Авто-кадрирование: объект найден — зелёная рамка = область сохранения"
                )
                self._cam_crop_status.setStyleSheet("color: #2e7d32;")
            else:
                self._cam_crop_status.setText(
                    "Авто-кадрирование: объект не найден — будет сохранён полный кадр"
                )
                self._cam_crop_status.setStyleSheet("color: #f57c00;")
            self._cam_crop_status.show()
        if found and detected.crop_rect is not None:
            return draw_crop_overlay(bgr, detected.crop_rect), True
        return bgr, False

    def _detect_cameras(self) -> list[int]:
        cfg = self._cfg
        mx = int(cfg.camera.scan_max_index) if cfg else 5
        found: list[int] = []
        for i in range(0, max(0, mx) + 1):
            try:
                if sys.platform.startswith("win"):
                    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                elif sys.platform.startswith("linux"):
                    cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
                else:
                    cap = cv2.VideoCapture(i)
                ok = cap is not None and cap.isOpened()
                if cap is not None:
                    cap.release()
                if ok:
                    found.append(i)
            except Exception:
                continue
        return found

    def _refresh_camera_list(self) -> None:
        if self._camera_cb is None:
            return
        cams = self._detect_cameras()
        self._camera_choices = cams
        vals = [str(i) for i in cams] if cams else ["0"]
        self._camera_cb.blockSignals(True)
        self._camera_cb.clear()
        self._camera_cb.addItems(vals)
        cur = str(self._camera_index)
        if cur in vals:
            self._camera_cb.setCurrentText(cur)
        else:
            self._camera_cb.setCurrentIndex(0)
            try:
                self._camera_index = int(vals[0])
            except Exception:
                self._camera_index = 0
        self._camera_cb.blockSignals(False)

    def _on_camera_selected(self, _text: str = "") -> None:
        if self._camera_cb is None:
            return
        try:
            idx = int(self._camera_cb.currentText())
        except Exception:
            idx = 0
        self._camera_index = idx
        was_on = self._webcam.is_opened()
        self._stop_camera()
        max_w = int(self._camera_max_w_spin.value()) if self._camera_max_w_spin else 0
        max_h = int(self._camera_max_h_spin.value()) if self._camera_max_h_spin else 0
        self._webcam = Webcam(idx, max_w, max_h)
        if was_on:
            self._start_camera()

    def _on_equipment_index_changed(self, _index: int) -> None:
        if self._suppress_equipment_change:
            return
        self._on_equipment_changed()

    def _on_equipment_changed(self) -> None:
        self._photo_paths.clear()
        self._photo_filenames.clear()
        self._rebuild_checklist()
        self._rebuild_photo_slots()
        self._refresh_all_thumbs()
        self._install_dirty_traces()
        self._refresh_dynamic_combobox_values()

    def _rebuild_photo_slots(self) -> None:
        cfg = self._cfg
        if cfg is None or self._ph_slots_container is None:
            return
        old_lay = self._ph_slots_container.layout()
        if old_lay is not None:
            clear_layout(old_lay)
            grid = old_lay
        else:
            grid = QGridLayout(self._ph_slots_container)
            self._ph_slots_container.setLayout(grid)
        if not isinstance(grid, QGridLayout):
            grid = QGridLayout(self._ph_slots_container)
            self._ph_slots_container.setLayout(grid)
        compact_form_grid(grid)

        self._slot_thumb_labels.clear()
        self._thumb_pixmaps.clear()
        self._photo_filename_labels.clear()

        eq = self._equipment_text()
        spec = get_checklist_spec(cfg, eq)
        slots_list = spec.photo_slots if spec else []
        if not slots_list:
            lbl = QLabel("Для выбранного типа не заданы слоты фото в config.yaml.")
            grid_add(grid, lbl, 0, 0)
            return

        for i, ps in enumerate(slots_list):
            col = i % 2
            r = i // 2
            sf = QGroupBox(f"Слот {ps.slot}")
            sf_l = QVBoxLayout(sf)
            compact_layout(sf_l)
            sf_l.setSpacing(3)
            lb = QLabel()
            lb.setFixedSize(lc.THUMB_W, lc.THUMB_H)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setScaledContents(True)
            sf_l.addWidget(lb)
            self._slot_thumb_labels[ps.slot] = lb
            btn_cap = QPushButton("Снять")
            btn_cap.clicked.connect(partial(self._capture_slot, ps.slot))
            sf_l.addWidget(btn_cap)
            fn_lbl = QLabel(self._photo_filenames.get(ps.slot, "") or "")
            fn_lbl.setWordWrap(True)
            fn_lbl.setMaximumWidth(lc.HINT_WRAP)
            fn_lbl.setStyleSheet(f"color: {lc.COLOR_HINT};")
            sf_l.addWidget(fn_lbl)
            self._photo_filename_labels[ps.slot] = fn_lbl
            if ps.hint:
                hint = QLabel(ps.hint)
                hint.setStyleSheet(f"color: {lc.COLOR_HINT};")
                hint.setWordWrap(True)
                hint.setMaximumWidth(lc.HINT_WRAP)
                hint.setMaximumHeight(lc.HINT_MAX_LINES_H)
                sf_l.addWidget(hint)
            grid_add(grid, sf, r, col)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._install_dirty_traces()
        QTimer.singleShot(0, self._sync_camera_preview_layout)
        QTimer.singleShot(0, self._refresh_all_thumbs)

    def _reload_registry_silent(self) -> None:
        out = self._output_path_edit.text().strip() if self._output_path_edit else ""
        if not out:
            self._registry_df = None
            return
        p = Path(out)
        if not p.is_file():
            self._registry_df = None
            return
        try:
            cfg = self._cfg
            hr = cfg.template.header_row if cfg else 1
            sh = cfg.template.sheet if cfg else 0
            sr = cfg.template.start_row if cfg else None
            self._registry_df = load_registry(p, sheet_name=sh, header_row=hr, start_row=sr)
        except Exception:
            self._registry_df = None

    def _update_record_indicator(self) -> None:
        df = self._registry_df
        idx = self._current_row_index
        if df is None or len(df) == 0:
            if idx is None:
                self._set_record("Запись: — (реестр пуст или не загружен)")
            else:
                self._set_record("Запись: —")
            self._nav_update_buttons()
            return
        n = len(df)
        if idx is None:
            self._set_record(f"Запись: новая (в реестре уже {n})")
        else:
            self._set_record(f"Запись № {idx + 1} из {n}")
        self._nav_update_buttons()

    def _nav_update_buttons(self) -> None:
        df = self._registry_df
        n = 0 if df is None else len(df)
        idx = self._current_row_index
        if self._btn_prev is None or self._btn_next is None:
            return
        if n == 0:
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(False)
            if self._btn_save_changes:
                self._btn_save_changes.setEnabled(False)
            if self._btn_delete:
                self._btn_delete.setEnabled(False)
            return
        self._btn_prev.setEnabled(True)
        self._btn_next.setEnabled(True)
        if idx is None:
            if self._btn_save_changes:
                self._btn_save_changes.setEnabled(False)
            if self._btn_delete:
                self._btn_delete.setEnabled(False)
            return
        if self._btn_save_changes:
            self._btn_save_changes.setEnabled(True)
        if self._btn_delete:
            self._btn_delete.setEnabled(True)
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < n - 1)

    def _nav_prev(self) -> None:
        if not self._maybe_save_dirty_before_leave():
            return
        df = self._registry_df
        if df is None or len(df) == 0:
            return
        n = len(df)
        if self._current_row_index is None:
            self._current_row_index = n - 1
        elif self._current_row_index > 0:
            self._current_row_index -= 1
        else:
            return
        self._apply_registry_row(self._current_row_index)
        self._update_record_indicator()

    def _nav_next(self) -> None:
        if not self._maybe_save_dirty_before_leave():
            return
        df = self._registry_df
        if df is None or len(df) == 0:
            return
        n = len(df)
        if self._current_row_index is None:
            self._current_row_index = 0
        elif self._current_row_index < n - 1:
            self._current_row_index += 1
        else:
            return
        self._apply_registry_row(self._current_row_index)
        self._update_record_indicator()

    def _new_record(self) -> None:
        if not self._maybe_save_dirty_before_leave():
            return
        self._reset_to_new_entry()

    def _apply_registry_row(self, idx: int) -> None:
        self._suppress_dirty = True
        cfg = self._cfg
        df = self._registry_df
        if cfg is None or df is None or idx < 0 or idx >= len(df):
            self._suppress_dirty = False
            return
        row = df.iloc[idx]

        if self._serial_edit:
            self._serial_edit.setText(cell_to_str(row.get(cfg.reference.template_serial_column, "")))

        for tmpl_col, edit in self.template_field_edits.items():
            ec = cfg.column_excel_columns.get(tmpl_col) if hasattr(cfg, "column_excel_columns") else None
            if ec is not None:
                try:
                    c1 = parse_checklist_start_column(ec)
                    if 1 <= c1 <= len(df.columns):
                        edit.setText(cell_to_str(df.iloc[idx, c1 - 1]))
                        continue
                except Exception:
                    pass
            if tmpl_col in df.columns:
                edit.setText(cell_to_str(row[tmpl_col]))

        for sf in cfg.semantic:
            w = self.semantic_widgets.get(sf.key)
            if w and sf.template_column in df.columns:
                w.setCurrentText(cell_to_str(row[sf.template_column]))

        eq_col = cfg.equipment_type_column
        if eq_col in df.columns:
            self._set_equipment_text(cell_to_str(row[eq_col]))
        self._rebuild_checklist()
        self._rebuild_photo_slots()

        eq = self._equipment_text()
        spec = get_checklist_spec(cfg, eq)
        fields = spec.fields if spec else []
        start = parse_checklist_start_column(spec.checklist_start_column if spec else 21)
        vals = get_checklist_values_from_row(df, idx, start, len(fields))
        self._set_checklist_values(vals, fields)

        photos_dir = Path(self._photos_dir_edit.text().strip() if self._photos_dir_edit else ".")
        self._photo_paths.clear()
        for ps in (spec.photo_slots if spec else []):
            cell_name = ""
            if getattr(ps, "excel_column", None):
                try:
                    c1 = parse_checklist_start_column(ps.excel_column)
                    if 1 <= c1 <= len(df.columns):
                        cell_name = cell_to_str(df.iloc[idx, c1 - 1])
                except Exception:
                    cell_name = ""
            elif ps.template_column in df.columns:
                cell_name = cell_to_str(row.get(ps.template_column, ""))
            if cell_name:
                self._photo_filenames[ps.slot] = cell_name
            if not cell_name:
                continue
            pth = Path(cell_name)
            if not pth.is_absolute():
                pth = photos_dir / cell_name
            if pth.is_file():
                self._photo_paths[ps.slot] = str(pth)
        self._refresh_photo_filename_labels()
        self._refresh_all_thumbs()

        if self._ref_df is not None and self._serial_edit:
            self._matched = find_row_by_serial(
                self._ref_df, self._serial_edit.text().strip(), cfg.reference.serial_column
            )
        self._set_status(f"Загружена запись № {idx + 1} из реестра.")
        self._suppress_dirty = False
        self._mark_clean()

    def _set_checklist_values(self, vals: list[str], fields: list[ChecklistField]) -> None:
        self._suppress_price_equipment_sync = True
        self._suppress_model_template = True
        try:
            for i, cf in enumerate(fields):
                v = vals[i] if i < len(vals) else ""
                w = self._checklist_widgets_by_id.get(cf.id)
                if w is None:
                    continue
                if cf.type == "textarea" and isinstance(w, QTextEdit):
                    w.setPlainText(v)
                elif cf.type == "bool" and isinstance(w, QCheckBox):
                    w.setChecked(v in ("Да", "да", "1", "true", "True", "yes", "Yes"))
                elif isinstance(w, QLineEdit):
                    w.setText(v)
                elif isinstance(w, QComboBox):
                    w.blockSignals(True)
                    w.setCurrentText(v)
                    w.blockSignals(False)
            self._refresh_model_combo_for_vendor()
        finally:
            self._suppress_price_equipment_sync = False
            self._suppress_model_template = False

    def _install_model_combo_popup_refresh(self, cb: QComboBox) -> None:
        if getattr(cb, "_salemaker_popup_refresh", False):
            return
        cb._salemaker_popup_refresh = True
        orig_show = cb.showPopup

        def _show_popup() -> None:
            if not self._get_checklist_manufacturer_text():
                self._refresh_model_combo_for_vendor(keep_model=self._get_checklist_model_text())
            orig_show()

        cb.showPopup = _show_popup  # type: ignore[method-assign]

    def _connect_model_template_signal(self) -> None:
        w_model = self._checklist_widgets_by_id.get("model")
        if isinstance(w_model, QComboBox):
            try:
                w_model.currentTextChanged.disconnect(self._on_vendor_model_changed)
            except (TypeError, RuntimeError):
                pass
            w_model.currentTextChanged.connect(self._on_vendor_model_changed)
            self._install_model_combo_popup_refresh(w_model)
        elif isinstance(w_model, QLineEdit):
            try:
                w_model.textChanged.disconnect(self._on_vendor_model_changed)
            except (TypeError, RuntimeError):
                pass
            w_model.textChanged.connect(self._on_vendor_model_changed)

    def _rebuild_checklist(self) -> None:
        cfg = self._cfg
        if cfg is None or self._checklist_frame is None:
            return
        lay = self._checklist_frame.layout()
        if lay is not None:
            clear_layout(lay)
        else:
            lay = QFormLayout(self._checklist_frame)
            compact_layout(lay)
            lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        if not isinstance(lay, QFormLayout):
            lay = QFormLayout(self._checklist_frame)
            compact_layout(lay)
            lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._checklist_getters.clear()
        self._checklist_widgets_by_id.clear()

        eq = self._equipment_text()
        spec = get_checklist_spec(cfg, eq)
        fields = spec.fields if spec else []
        self._suppress_price_equipment_sync = True
        try:
            for cf in fields:
                lbl = f"{cf.label} *" if getattr(cf, "required", False) else cf.label
                lbl_w = QLabel(lbl)
                lbl_w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                w = self._make_checklist_widget(self._checklist_frame, cf)
                lay.addRow(lbl_w, w)
                self._checklist_widgets_by_id[cf.id] = w
                if checklist_field_has_default(cf):
                    d = self._resolve_dynamic_default(cf.default)
                    if d:
                        if isinstance(w, QComboBox):
                            w.blockSignals(True)
                            w.setCurrentText(d)
                            w.blockSignals(False)
                        elif isinstance(w, QLineEdit):
                            w.setText(d)
            self._install_dirty_traces()
            self._refresh_model_combo_for_vendor()
            self._refresh_dynamic_combobox_values()
            self._connect_model_template_signal()
        finally:
            self._suppress_price_equipment_sync = False

    def _active_checklist_spec(self) -> tuple[str, ChecklistSpec] | None:
        cfg = self._cfg
        if cfg is None:
            return None
        eq = self._equipment_text()
        if not eq:
            return None
        spec = get_checklist_spec(cfg, eq)
        if spec is None:
            return None
        return eq, spec

    def _make_checklist_widget(self, parent: QWidget, cf: ChecklistField) -> QWidget:
        if cf.id in ("manufacturer", "model") and self._price_df is not None:
            cb = QComboBox(parent)
            cb.setEditable(True)
            set_char_width(cb, lc.COMBO_W_FIELD)
            cb.blockSignals(True)
            if cf.id == "manufacturer":
                base = self._price_vendors()
                extra = [v for v in (cf.values or []) if v and v not in base]
                cb.addItems(base + extra)
                cb.setCurrentIndex(-1)
                cb.setCurrentText("")
                cb.blockSignals(False)
                cb.currentTextChanged.connect(self._on_vendor_changed)
            else:
                cb.addItems(list(cf.values or []))
                cb.setCurrentIndex(-1)
                cb.setCurrentText("")
                cb.blockSignals(False)
            self._checklist_getters[cf.id] = lambda c=cb: c.currentText().strip()
            return cb

        if cf.type in ("text", "number") and cf.values:
            cb = QComboBox(parent)
            cb.setEditable(True)
            cb.blockSignals(True)
            cb.addItems(list(cf.values))
            if checklist_field_has_default(cf):
                d = self._resolve_dynamic_default(cf.default)
                if d:
                    cb.setCurrentText(d)
                else:
                    cb.setCurrentIndex(-1)
                    cb.setCurrentText("")
            else:
                cb.setCurrentIndex(-1)
                cb.setCurrentText("")
            cb.blockSignals(False)
            set_char_width(cb, lc.COMBO_W_FIELD)
            self._checklist_getters[cf.id] = lambda c=cb: c.currentText().strip()
            return cb

        if cf.type == "textarea":
            t = QTextEdit(parent)
            t.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            t.setFixedHeight(t.fontMetrics().lineSpacing() * 3 + 12)
            t.setMinimumWidth(char_width_px(t, lc.ENTRY_W_TEXTAREA))
            self._checklist_getters[cf.id] = lambda te=t: te.toPlainText().strip()
            return t

        if cf.type == "bool":
            ch = QCheckBox("Да", parent)
            self._checklist_getters[cf.id] = lambda c=ch: "Да" if c.isChecked() else "Нет"
            return ch

        if cf.type == "number":
            e = QLineEdit(parent)
            set_char_width(e, lc.ENTRY_W_FIELD)
            self._checklist_getters[cf.id] = lambda le=e: le.text().strip()
            return e

        e = QLineEdit(parent)
        set_char_width(e, lc.ENTRY_W_FIELD)
        self._checklist_getters[cf.id] = lambda le=e: le.text().strip()
        return e

    def _browse_ref(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "", "", "Excel (*.xlsx *.xls)")
        if p and self._ref_path_edit:
            self._ref_path_edit.setText(p)
            self._persist_paths_to_config(reference_path=p)

    def _browse_template(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "", "", "Excel (*.xlsx)")
        if p and self._template_path_edit:
            self._template_path_edit.setText(p)
            self._persist_paths_to_config(template_path=p)

    def _browse_output(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "", "", "Excel (*.xlsx)")
        if p and self._output_path_edit:
            self._output_path_edit.setText(p)
            self._persist_paths_to_config(output_path=p)
            self._reload_registry_silent()
            self._current_row_index = None
            self._update_record_indicator()

    def _browse_photos(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "")
        if d and self._photos_dir_edit:
            self._photos_dir_edit.setText(d)
            self._persist_paths_to_config(photos_dir=d)

    def _open_reference(self) -> None:
        if self._ref_path_edit is None:
            return
        path = self._ref_path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Справочник", "Укажите файл справочника.")
            return
        ok, err = self._try_load_reference_file(path)
        if not ok:
            QMessageBox.critical(self, "Ошибка", err)
            return
        assert self._ref_df is not None
        self._set_status(f"Справочник: {len(self._ref_df)} строк.")
        self._update_reference_path_colors()
        self._persist_paths_to_config(reference_path=path)

    def _ensure_reference_loaded(self) -> bool:
        if self._ref_df is not None:
            return True
        if self._ref_path_edit is None:
            return False
        path = self._ref_path_edit.text().strip()
        if not path:
            return False
        try:
            self._ref_df = load_reference(path)
            return True
        except Exception:
            return False

    def _fill_from_reference_row(self, row: pd.Series, *, sync_serial: bool) -> None:
        cfg = self._cfg
        assert cfg is not None
        if sync_serial and self._serial_edit:
            sc = cfg.reference.serial_column
            if sc in row.index:
                self._serial_edit.setText(cell_to_str(row[sc]))
        for tmpl_col, ref_col in cfg.column_mapping.items():
            edit = self.template_field_edits.get(tmpl_col)
            if edit is None:
                continue
            if ref_col not in row.index:
                continue
            edit.setText(cell_to_str(row[ref_col]))
        et = cfg.equipment_type_column
        if et in cfg.column_mapping:
            ref_col = cfg.column_mapping[et]
            if ref_col in row.index:
                v = row[ref_col]
                if not pd.isna(v) and str(v).strip():
                    self._set_equipment_text(str(v).strip())
        ref_pp = self._device_pp_reference_column(cfg)
        if ref_pp in row.index and self._device_pp_edit:
            self._device_pp_edit.setText(cell_to_str(row[ref_pp]))

    def _find_row(self) -> None:
        cfg = self._cfg
        if cfg is None or not self._ensure_reference_loaded():
            QMessageBox.warning(self, "Данные", "Загрузите конфиг и откройте справочник.")
            return
        serial = self._serial_edit.text().strip() if self._serial_edit else ""
        if not serial:
            QMessageBox.warning(self, "Серийный номер", "Введите серийный номер.")
            return
        sc = cfg.reference.serial_column
        row = find_row_by_serial(self._ref_df, serial, sc)
        self._matched = row
        if row is None:
            self._set_status("Строка не найдена.")
            return
        self._fill_from_reference_row(row, sync_serial=False)
        self._rebuild_checklist()
        self._rebuild_photo_slots()
        self._current_row_index = None
        self._update_record_indicator()
        self._set_status("Данные из справочника подставлены.")

    def _find_row_by_device_pp(self) -> None:
        cfg = self._cfg
        if cfg is None or not self._ensure_reference_loaded():
            QMessageBox.warning(self, "Данные", "Загрузите конфиг и откройте справочник.")
            return
        ref_col = self._device_pp_reference_column(cfg)
        if ref_col not in self._ref_df.columns:
            QMessageBox.warning(self, "Справочник", f"В справочнике нет столбца «{ref_col}».")
            return
        val = self._device_pp_edit.text().strip() if self._device_pp_edit else ""
        if not val:
            QMessageBox.warning(self, "Номер по п/п", "Введите номер по п/п на устройстве.")
            return
        row = find_row_by_column(self._ref_df, val, ref_col)
        self._matched = row
        if row is None:
            self._set_status("Строка по номеру п/п не найдена.")
            return
        self._fill_from_reference_row(row, sync_serial=True)
        self._rebuild_checklist()
        self._rebuild_photo_slots()
        self._current_row_index = None
        self._update_record_indicator()
        self._set_status("Строка по номеру п/п найдена, данные подставлены.")

    def _start_camera(self) -> None:
        self._preview_timer.stop()
        if self._webcam.is_opened():
            self._stop_camera()
        max_w = int(self._camera_max_w_spin.value()) if self._camera_max_w_spin else 0
        max_h = int(self._camera_max_h_spin.value()) if self._camera_max_h_spin else 0
        self._webcam = Webcam(int(self._camera_index), max_w, max_h)
        if not self._webcam.open():
            QMessageBox.critical(self, "Камера", "Не удалось открыть веб-камеру.")
            return
        self._preview_timer.start(lc.PREVIEW_INTERVAL_MS)

    def _preview_loop(self) -> None:
        if not self._webcam.is_opened() or self._cam_label is None:
            return
        bgr = self._webcam.read_bgr()
        if bgr is not None:
            self._last_bgr = bgr.copy()
            preview_bgr, _found = self._prepare_camera_preview_bgr(bgr)
            pix = bgr_ndarray_to_qpixmap(preview_bgr)
            if pix is not None and not pix.isNull():
                self._cam_pixmap = pix
                if self._cam_label is not None:
                    h, w = bgr.shape[:2]
                    self._cam_label.set_frame_pixmap(self._cam_pixmap, w, h)

    def _stop_camera(self) -> None:
        self._preview_timer.stop()
        self._webcam.release()
        if self._cam_label:
            self._cam_label.clear_frame()
            self._cam_pixmap = None
        if self._cam_crop_status is not None:
            self._cam_crop_status.hide()

    @staticmethod
    def _bgr_to_qpixmap(bgr: np.ndarray) -> QPixmap:
        pix = bgr_ndarray_to_qpixmap(bgr)
        return pix if pix is not None else QPixmap()

    def _set_slot_filename_label(self, slot: int, text: str | None = None) -> None:
        lbl = self._photo_filename_labels.get(slot)
        if lbl is None:
            return
        name = text if text is not None else self._photo_filenames.get(slot, "")
        lbl.setText(name or "")

    def _refresh_photo_filename_labels(self) -> None:
        for slot in self._photo_filename_labels:
            self._set_slot_filename_label(slot)

    def _display_slot_thumb(self, slot: int, pix: QPixmap) -> None:
        lb = self._slot_thumb_labels.get(slot)
        if lb is None or pix.isNull():
            return
        scaled = pix.scaled(
            lc.THUMB_W,
            lc.THUMB_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumb_pixmaps[slot] = scaled
        lb.setPixmap(scaled)
        lb.update()

    def _capture_slot(self, slot: int) -> None:
        serial = self._serial_edit.text().strip() if self._serial_edit else ""
        if not serial:
            QMessageBox.warning(self, "Серийный номер", "Введите серийный номер в верхней панели.")
            return
        if not self._webcam.is_opened():
            QMessageBox.warning(self, "Камера", "Включите камеру и дождитесь кадра.")
            return

        cfg = self._cfg
        use_preview = bool(cfg and cfg.camera.capture_with_preview)
        if self._capture_preview_cb is not None:
            use_preview = self._capture_preview_cb.isChecked()

        if use_preview:
            preview_was_active = self._preview_timer.isActive()
            if preview_was_active:
                self._preview_timer.stop()
            try:
                bgr = run_photo_capture(
                    self._webcam,
                    slot,
                    parent=self,
                    crop_enabled=self._crop_central_subject_enabled(),
                    crop_params=self._camera_crop_params() if self._crop_central_subject_enabled() else None,
                )
                if bgr is None:
                    return
                self._last_bgr = bgr.copy()
            finally:
                if preview_was_active and self._webcam.is_opened():
                    self._preview_timer.start(lc.PREVIEW_INTERVAL_MS)
        else:
            bgr = self._last_bgr
            if bgr is None:
                bgr = self._webcam.read_bgr()
            if bgr is None:
                QMessageBox.warning(self, "Камера", "Включите камеру и дождитесь кадра.")
                return
            self._last_bgr = bgr.copy()

        self._save_slot_photo(slot, bgr)

    def _save_slot_photo(self, slot: int, bgr: np.ndarray) -> None:
        serial = self._serial_edit.text().strip() if self._serial_edit else ""
        photos_dir = self._photos_dir_edit.text().strip() if self._photos_dir_edit else ""
        cfg = self._cfg
        crop_applied = False
        if self._crop_central_subject_enabled() and cfg is not None:
            params = self._camera_crop_params()
            bgr, crop_applied, _detected = crop_central_subject(bgr, params)
        try:
            full_path, fname = make_photo_path_for_slot(photos_dir, serial, slot, "jpg")
        except ValueError as e:
            QMessageBox.critical(self, "Имя файла", str(e))
            return
        try:
            save_bgr(full_path, bgr)
        except OSError as e:
            QMessageBox.critical(self, "Сохранение", str(e))
            return
        self._photo_paths[slot] = full_path
        self._photo_filenames[slot] = fname
        self._set_slot_filename_label(slot, fname)
        self._display_slot_thumb(slot, self._bgr_to_qpixmap(bgr))
        try:
            sz_kb = Path(full_path).stat().st_size / 1024
            if sz_kb > 200:
                QMessageBox.warning(
                    self,
                    "Размер файла",
                    f"Файл «{fname}» больше 200 КБ ({sz_kb:.0f} КБ). При необходимости сожмите изображение.",
                )
        except OSError:
            pass
        status = f"Слот {slot}: {fname}"
        if self._crop_central_subject_enabled():
            if crop_applied:
                status += " (авто-кадрирование)"
            else:
                status += " (объект не найден — полный кадр)"
        self._set_status(status)
        QTimer.singleShot(0, partial(self._refresh_thumb, slot))

    def _refresh_thumb(self, slot: int) -> None:
        lb = self._slot_thumb_labels.get(slot)
        path = self._photo_paths.get(slot)
        if lb is None:
            return
        if not path or not Path(path).is_file():
            lb.clear()
            self._thumb_pixmaps.pop(slot, None)
            return
        pix = pixmap_from_file(path)
        if pix is None or pix.isNull():
            lb.clear()
            self._thumb_pixmaps.pop(slot, None)
            return
        self._display_slot_thumb(slot, pix)

    def _refresh_all_thumbs(self) -> None:
        for s in list(self._slot_thumb_labels.keys()):
            self._refresh_thumb(s)

    def _build_photo_dict_for_save(self, photo_slots: list) -> dict[int, str]:
        out: dict[int, str] = {}
        for ps in photo_slots:
            text = (self._photo_filenames.get(ps.slot) or "").strip()
            if text:
                out[ps.slot] = text
            elif ps.slot in self._photo_paths:
                out[ps.slot] = Path(self._photo_paths[ps.slot]).name
            else:
                out[ps.slot] = ""
        return out

    def _delete_current_record(self) -> None:
        cfg = self._cfg
        if cfg is None:
            QMessageBox.warning(self, "Конфиг", "Загрузите config.yaml.")
            return
        idx = self._current_row_index
        if idx is None:
            QMessageBox.information(self, "Удаление", "Нет выбранной строки реестра (режим новой записи).")
            return
        out = (self._output_path_edit.text().strip() if self._output_path_edit else "") or cfg.output_path
        if not out or not Path(out).is_file():
            QMessageBox.warning(self, "Реестр", "Файл реестра не найден.")
            return
        r = QMessageBox.question(
            self,
            "Удаление",
            "Удалить текущую строку из файла реестра? Действие необратимо.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_registry_data_row(out, cfg, idx)
        except Exception as e:
            QMessageBox.critical(self, "Excel", str(e))
            return
        self._reload_registry_silent()
        df = self._registry_df
        if df is None or len(df) == 0:
            self._current_row_index = None
            self._new_record()
        else:
            new_idx = min(idx, len(df) - 1)
            self._current_row_index = new_idx
            self._apply_registry_row(new_idx)
        self._update_record_indicator()
        self._set_status("Строка удалена из реестра.")

    def _validate_required_photos(self) -> bool:
        cfg = self._cfg
        if cfg is None:
            return False
        spec = get_checklist_spec(cfg, self._equipment_text())
        if spec is None:
            QMessageBox.warning(self, "Конфиг", "Для выбранного типа оборудования нет описания чек-листа.")
            return False
        for ps in spec.photo_slots:
            if not ps.required:
                continue
            p = self._photo_paths.get(ps.slot)
            if not p or not Path(p).is_file():
                QMessageBox.warning(
                    self,
                    "Фото",
                    f"Обязательное фото не снято: слот {ps.slot} ({ps.template_column}).",
                )
                return False
        return True

    def _validate_required_inputs(self, spec: object | None) -> bool:
        cfg = self._cfg
        if cfg is None:
            return False

        missing: list[str] = []

        for sf in cfg.semantic:
            if not getattr(sf, "required", False):
                continue
            w = self.semantic_widgets.get(sf.key)
            if w is None:
                continue
            if len(w.currentText().strip()) < 1:
                missing.append(sf.label)

        col_req = getattr(cfg, "column_required", {}) or {}
        for tmpl_col, edit in self.template_field_edits.items():
            if not col_req.get(tmpl_col, False):
                continue
            if len(edit.text().strip()) < 1:
                missing.append(tmpl_col)

        fields = getattr(spec, "fields", None) if spec is not None else None
        if fields:
            for cf in fields:
                if not getattr(cf, "required", False):
                    continue
                g = self._checklist_getters.get(cf.id)
                if g is None:
                    continue
                try:
                    val = g() if callable(g) else str(g)
                except Exception:
                    val = ""
                if len(str(val).strip()) < 1:
                    missing.append(getattr(cf, "label", cf.id))

        if self._serial_edit and len(self._serial_edit.text().strip()) < 1:
            missing.insert(0, "Серийный номер")

        if missing:
            uniq = []
            for m in missing:
                if m not in uniq:
                    uniq.append(m)
            QMessageBox.warning(
                self,
                "Обязательные поля",
                "Заполните обязательные поля:\n- " + "\n- ".join(uniq),
            )
            return False
        return True

    def _reset_to_new_entry(self) -> None:
        self._suppress_dirty = True
        self._current_row_index = None
        if self._serial_edit:
            self._serial_edit.setText("")
        if self._device_pp_edit:
            self._device_pp_edit.setText("")
        self._matched = None
        cfg = self._cfg
        if cfg:
            for edit in self.template_field_edits.values():
                edit.setText("")
            if cfg.equipment_types and self._equipment_cb:
                self._equipment_cb.setCurrentIndex(0)
            self._rebuild_checklist()
            self._rebuild_photo_slots()
        self._photo_paths.clear()
        self._refresh_all_thumbs()
        self._update_record_indicator()
        self._set_status("Новая запись. Заполните данные на вкладках и сохраните в реестр.")
        self._suppress_dirty = False
        self._mark_clean()

    def _save_excel(self) -> None:
        cfg = self._cfg
        if cfg is None:
            QMessageBox.warning(self, "Конфиг", "Загрузите config.yaml.")
            return

        if self._current_row_index is not None:
            ok = self._save_current_row_updates()
            if ok:
                self._reset_to_new_entry()
            return

        tpl = (self._template_path_edit.text().strip() if self._template_path_edit else "") or cfg.template.path
        out = (self._output_path_edit.text().strip() if self._output_path_edit else "") or cfg.output_path
        if not tpl:
            QMessageBox.warning(self, "Шаблон", "Укажите файл шаблона Excel.")
            return
        if not out:
            QMessageBox.warning(self, "Файл", "Укажите файл реестра.")
            return
        tpl_path = Path(tpl)
        out_path = Path(out)
        if not tpl_path.is_file():
            QMessageBox.critical(self, "Шаблон", "Файл шаблона не найден.")
            return
        if not out_path.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tpl_path, out_path)
        self._reload_registry_silent()
        dup_msg = self._registry_duplicate_error(ignore_index=None)
        if dup_msg:
            QMessageBox.warning(self, "Дубликат в реестре", dup_msg)
            return
        serial = self._serial_edit.text().strip() if self._serial_edit else ""
        if not serial:
            QMessageBox.warning(self, "Серийный номер", "Введите серийный номер.")
            return
        if not self._validate_required_photos():
            return

        eq = self._equipment_text()
        spec = get_checklist_spec(cfg, eq)
        if spec is None:
            QMessageBox.warning(self, "Конфиг", f"Нет чек-листа для типа «{eq}» в config.yaml.")
            return
        if not self._validate_required_inputs(spec):
            return
        fields = spec.fields
        chk_vals = collect_checklist_values(fields, self._checklist_getters)

        sem = {k: w.currentText() for k, w in self.semantic_widgets.items()}
        fe = {k: v.text().strip() for k, v in self.template_field_edits.items()}

        matched = None
        if self._ref_df is not None:
            matched = find_row_by_serial(self._ref_df, serial, cfg.reference.serial_column)

        photo_raw = self._build_photo_dict_for_save(spec.photo_slots)
        photo_for_row: dict[int, str] = {}
        for ps in spec.photo_slots:
            raw = (photo_raw.get(ps.slot) or "").strip()
            if not raw:
                photo_for_row[ps.slot] = ""
                continue
            if cfg.photo_cell_mode == "fullpath":
                p = Path(raw)
                if not p.is_absolute() and ps.slot in self._photo_paths:
                    photo_for_row[ps.slot] = str(Path(self._photo_paths[ps.slot]).resolve())
                else:
                    photo_for_row[ps.slot] = str(p.resolve()) if p.is_absolute() else raw
            else:
                photo_for_row[ps.slot] = Path(raw).name

        row_dict = build_row_values_from_lookup(
            cfg,
            matched,
            serial,
            cfg.reference.template_serial_column,
            fe,
            sem,
            eq,
            photo_for_row,
            spec.photo_slots,
        )

        direct_cols: dict[int, str] = {}
        for tmpl_col, ec in (cfg.column_excel_columns.items() if hasattr(cfg, "column_excel_columns") else []):
            try:
                c1 = parse_checklist_start_column(ec)
            except Exception:
                continue
            if tmpl_col in row_dict:
                direct_cols[c1] = str(row_dict.get(tmpl_col, "") or "")
        for sf in cfg.semantic:
            if getattr(sf, "excel_column", None):
                try:
                    c1 = parse_checklist_start_column(sf.excel_column)
                    direct_cols[c1] = str(sem.get(sf.key, "") or "")
                except Exception:
                    continue
        for ps in spec.photo_slots:
            if getattr(ps, "excel_column", None):
                try:
                    c1 = parse_checklist_start_column(ps.excel_column)
                    direct_cols[c1] = photo_for_row.get(ps.slot, "")
                except Exception:
                    continue

        try:
            append_template_row(
                out,
                cfg,
                row_dict,
                chk_vals,
                spec.checklist_start_column,
                direct_columns=direct_cols or None,
            )
        except Exception as e:
            QMessageBox.critical(self, "Excel", str(e))
            return
        self._reload_registry_silent()
        if self._registry_df is not None and len(self._registry_df) > 0:
            self._reset_to_new_entry()
        self._update_record_indicator()
        self._set_status(f"Строка записана в реестр: {out}")
