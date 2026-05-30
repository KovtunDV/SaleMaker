"""Окно съёмки с превью камеры (отдельное top-level окно по размеру экрана)."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QEventLoop, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from camera import Webcam
from ui import layout_constants as lc
from ui.widgets import CameraPreviewLabel, bgr_ndarray_to_qpixmap


class PhotoCaptureWindow(QWidget):
    """Отдельное окно съёмки: пробел/щелчок — снять, Esc — отмена.

    Не использует QDialog.exec() — на части систем Windows/Linux exec() завершается,
    но окно остаётся на экране.
    """

    completed = Signal(object)  # np.ndarray | None

    _WARMUP_FRAMES = 5

    def __init__(self, webcam: Webcam, *, slot: int) -> None:
        super().__init__(None)
        self._webcam = webcam
        self._slot = slot
        self._last_bgr: np.ndarray | None = None
        self._finished = False
        self._frame_busy = False

        self.setWindowTitle(f"Съёмка — слот {slot}")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._preview = CameraPreviewLabel(self)
        self._preview.setMinimumSize(0, 0)
        self._preview.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._preview.installEventFilter(self)
        root.addWidget(self._preview, stretch=1)

        footer = QWidget(self)
        footer.setStyleSheet("background-color: #1a1a1a;")
        footer_l = QVBoxLayout(footer)
        footer_l.setContentsMargins(8, 4, 8, 8)
        footer_l.setSpacing(4)

        self._hint = QLabel("Пробел или щелчок мыши — снять, Esc — отмена")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hint.setStyleSheet("color: #cccccc;")
        footer_l.addWidget(self._hint)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._status.setStyleSheet("color: #ffcc00;")
        self._status.hide()
        footer_l.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_capture = QPushButton("Снять")
        btn_capture.clicked.connect(self._take_photo)
        btn_row.addWidget(btn_capture)
        btn_cancel = QPushButton("Отмена (Esc)")
        btn_cancel.clicked.connect(self._cancel)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch(1)
        footer_l.addLayout(btn_row)
        root.addWidget(footer)

        self.setStyleSheet("background-color: #000000;")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_frame)

        shortcut_ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        QShortcut(Qt.Key.Key_Escape, self, self._cancel, context=shortcut_ctx)
        QShortcut(Qt.Key.Key_Space, self, self._take_photo, context=shortcut_ctx)
        QShortcut(Qt.Key.Key_Return, self, self._take_photo, context=shortcut_ctx)
        QShortcut(Qt.Key.Key_Enter, self, self._take_photo, context=shortcut_ctx)

    def open_on_screen(self, *, screen=None) -> None:
        """Показать окно, привязав его к рабочей области экрана."""
        target = screen or QApplication.primaryScreen()
        if target is not None:
            self.setGeometry(target.availableGeometry())
        self._sync_preview_layout()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._warmup_camera()
        self._timer.start(lc.PREVIEW_INTERVAL_MS)
        self._update_frame()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_preview()
        if not self._finished:
            self._complete(None, emit_only=True)
        super().closeEvent(event)

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:  # noqa: N802
        if obj is self._preview and event.type() == QEvent.Type.MouseButtonPress:
            me = event
            if isinstance(me, QMouseEvent) and me.button() == Qt.MouseButton.LeftButton:
                self._take_photo()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._cancel()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._take_photo()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_preview_layout()
        if self._last_bgr is not None:
            self._preview.refresh_display()

    def _footer_height(self) -> int:
        footer = self._hint.parentWidget()
        if footer is not None:
            return max(lc.CAPTURE_DIALOG_FOOTER_H, footer.sizeHint().height() + 8)
        measured = self._hint.sizeHint().height()
        if self._status.isVisible():
            measured += self._status.sizeHint().height()
        return max(lc.CAPTURE_DIALOG_FOOTER_H, measured + 8)

    def _sync_preview_layout(self) -> None:
        w = max(self.width(), 320)
        h = max(self.height(), 240)
        max_preview_h = max(90, h - self._footer_height())
        self._preview.set_max_preview_size(w, max_preview_h)

    def _stop_preview(self) -> None:
        self._timer.stop()
        self._frame_busy = False

    def _cancel(self) -> None:
        self._complete(None)

    def _complete(self, bgr: np.ndarray | None, *, emit_only: bool = False) -> None:
        if self._finished:
            return
        self._finished = True
        self._stop_preview()
        self.completed.emit(bgr)
        if emit_only:
            return
        self.hide()
        self.close()

    def _warmup_camera(self) -> None:
        if not self._webcam.is_opened():
            return
        for _ in range(self._WARMUP_FRAMES):
            self._webcam.read_bgr()

    def _take_photo(self) -> None:
        if self._finished:
            return
        if self._last_bgr is None:
            frame = self._webcam.read_bgr()
            if frame is None:
                self._show_status("Нет кадра с камеры. Подождите превью или проверьте камеру.")
                return
            self._last_bgr = frame.copy()
        self._complete(self._last_bgr.copy())

    def _show_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.show()
        self._sync_preview_layout()

    def _update_frame(self) -> None:
        if self._finished or self._frame_busy:
            return
        if not self._webcam.is_opened():
            self._show_status("Камера не открыта.")
            return
        self._frame_busy = True
        try:
            bgr = self._webcam.read_bgr()
        finally:
            self._frame_busy = False
        if self._finished:
            return
        if bgr is None:
            self._show_status("Не удалось получить кадр с камеры.")
            return
        self._last_bgr = bgr.copy()
        self._status.hide()
        self._sync_preview_layout()
        pix = bgr_ndarray_to_qpixmap(bgr)
        if pix is None or pix.isNull():
            self._show_status("Ошибка отображения кадра.")
            return
        h, w = bgr.shape[:2]
        self._preview.set_frame_pixmap(pix, w, h)


def run_photo_capture(
    webcam: Webcam,
    slot: int,
    *,
    parent: QWidget | None = None,
) -> np.ndarray | None:
    """Блокирующая съёмка в отдельном окне. Возвращает BGR-кадр или None при отмене."""
    result: list[np.ndarray | None] = [None]
    loop = QEventLoop()
    owner_screen = parent.screen() if parent is not None else None

    if parent is not None:
        parent.setEnabled(False)

    window = PhotoCaptureWindow(webcam, slot=slot)

    def on_completed(bgr: object) -> None:
        result[0] = bgr if bgr is None or isinstance(bgr, np.ndarray) else None
        if loop.isRunning():
            loop.quit()

    window.completed.connect(on_completed)
    window.destroyed.connect(lambda *_: loop.quit() if loop.isRunning() else None)
    window.open_on_screen(screen=owner_screen)

    loop.exec()

    if window.isVisible():
        window.hide()
    window.deleteLater()
    QApplication.processEvents()

    if parent is not None:
        parent.setEnabled(True)
        parent.activateWindow()

    return result[0]


# Обратная совместимость имён
PhotoCaptureDialog = PhotoCaptureWindow
