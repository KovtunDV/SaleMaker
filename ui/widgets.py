"""Qt widget helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QImage, QPixmap
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QLayout, QLineEdit, QSizePolicy, QWidget


def char_width_px(widget: QWidget, chars: int, extra: int = 24) -> int:
    fm = QFontMetrics(widget.font())
    return fm.horizontalAdvance("0") * chars + extra


def set_char_width(widget: QLineEdit | QComboBox, chars: int, extra: int = 24) -> None:
    w = char_width_px(widget, chars, extra)
    widget.setMinimumWidth(w)


def set_path_entry_style(entry: QLineEdit, loaded: bool) -> None:
    if loaded:
        entry.setStyleSheet("")
    else:
        entry.setStyleSheet(f"color: #808080;")


def bgr_ndarray_to_qpixmap(bgr: np.ndarray) -> QPixmap | None:
    """BGR-кадр OpenCV → QPixmap (копия буфера, без привязки к lifetime numpy)."""
    if bgr is None or bgr.size == 0:
        return None
    try:
        import cv2

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    except Exception:
        return None
    h, w = rgb.shape[:2]
    if h < 1 or w < 1:
        return None
    rgb = np.ascontiguousarray(rgb)
    # Явная копия байтов — надёжнее на разных сборках Qt/Windows, чем QImage(data=...).
    qimg = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    if qimg.isNull():
        return None
    return QPixmap.fromImage(qimg)


def pixmap_from_file(path: str | Path) -> QPixmap | None:
    """Загрузка изображения в QPixmap (корректно для путей с кириллицей в Windows)."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    img = QImage.fromData(data)
    if not img.isNull():
        return QPixmap.fromImage(img)
    try:
        import cv2

        arr = np.frombuffer(data, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        return bgr_ndarray_to_qpixmap(bgr)
    except Exception:
        return None


def grid_add(
    grid: QGridLayout,
    widget: QWidget,
    row: int,
    column: int,
    row_span: int = 1,
    column_span: int = 1,
) -> None:
    """Явный 5-арный addWidget — обходит предупреждения Shiboken для (row, column=0)."""
    grid.addWidget(widget, row, column, row_span, column_span)


def compact_form_grid(layout: QGridLayout) -> None:
    """Плотная сетка полей (как в Tkinter padx/pady 3–4)."""
    layout.setVerticalSpacing(4)
    layout.setHorizontalSpacing(8)
    layout.setContentsMargins(6, 6, 6, 6)


def compact_layout(layout: QLayout) -> None:
    layout.setSpacing(4)
    if hasattr(layout, "setContentsMargins"):
        layout.setContentsMargins(6, 6, 6, 6)


def section_title(text: str) -> QWidget:
    """Заголовок секции без лишней высоты строки."""
    from PySide6.QtWidgets import QLabel

    lbl = QLabel(text)
    lbl.setStyleSheet("font-weight: bold;")
    lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return lbl


class CameraPreviewLabel(QLabel):
    """Превью камеры: сохраняет соотношение сторон кадра, без растягивания по высоте."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aspect_w = 16
        self._aspect_h = 9
        self._max_w = 0
        self._max_h = 0
        self._source: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setScaledContents(False)

    def set_minimum_preview_size(self, width: int, height: int) -> None:
        self.setMinimumSize(max(160, width), max(90, height))

    def set_max_preview_size(self, width: int, height: int) -> None:
        self._max_w = max(0, int(width))
        self._max_h = max(0, int(height))
        if self._max_w and self._max_h:
            self.setMaximumSize(self._max_w, self._max_h)
        self.updateGeometry()

    def set_frame_size(self, width: int, height: int) -> None:
        if width > 0 and height > 0:
            self._aspect_w = width
            self._aspect_h = height
            self.updateGeometry()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        if width <= 0 or self._aspect_w <= 0:
            return self.minimumHeight()
        w = min(width, self._max_w) if self._max_w > 0 else width
        h = int(w * self._aspect_h / self._aspect_w)
        h = max(self.minimumHeight(), h)
        if self._max_h > 0:
            h = min(h, self._max_h)
        return h

    def set_frame_pixmap(self, pixmap: QPixmap, frame_w: int, frame_h: int) -> None:
        self._source = pixmap
        self.set_frame_size(frame_w, frame_h)
        self._refresh_pixmap()

    def clear_frame(self) -> None:
        self._source = None
        self.clear()

    def refresh_display(self) -> None:
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._source is None or self._source.isNull():
            self.clear()
            return
        size = self.size()
        if size.width() < 2 or size.height() < 2:
            return
        target_w, target_h = size.width(), size.height()
        if self._max_w > 0:
            target_w = min(target_w, self._max_w)
        if self._max_h > 0:
            target_h = min(target_h, self._max_h)
        self.setPixmap(
            self._source.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_pixmap()


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        else:
            sub = item.layout()
            if sub is not None:
                clear_layout(sub)
