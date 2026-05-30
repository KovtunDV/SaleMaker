"""Захват кадра с веб-камеры через OpenCV."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


class Webcam:
    def __init__(self, device_index: int = 0, max_width: int = 0, max_height: int = 0) -> None:
        self._device_index = device_index
        self._max_width = int(max_width or 0)
        self._max_height = int(max_height or 0)
        self._cap: cv2.VideoCapture | None = None

    @property
    def device_index(self) -> int:
        return self._device_index

    def open(self) -> bool:
        self.release()
        backends: list[int | None]
        if sys.platform.startswith("win"):
            # DirectShow → Media Foundation → авто: разные ПК/драйверы камер на Windows.
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, None]
        elif sys.platform.startswith("linux"):
            backends = [cv2.CAP_V4L2, None]
        else:
            backends = [None]

        for backend in backends:
            cap = (
                cv2.VideoCapture(self._device_index)
                if backend is None
                else cv2.VideoCapture(self._device_index, backend)
            )
            if cap is not None and cap.isOpened():
                self._cap = cap
                break
            if cap is not None:
                cap.release()

        if self._cap is not None and self._cap.isOpened():
            if self._max_width > 0:
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._max_width))
            if self._max_height > 0:
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._max_height))
            return True
        self.release()
        return False

    def is_opened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def read_bgr(self) -> Optional[np.ndarray]:
        if not self._cap or not self._cap.isOpened():
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def read_rgb(self) -> Optional[np.ndarray]:
        bgr = self.read_bgr()
        if bgr is None:
            return None
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> "Webcam":
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


def save_bgr(path: str, frame_bgr: np.ndarray) -> None:
    """Сохраняет BGR-кадр в файл (пути и имена с кириллицей на Windows)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = (p.suffix or ".jpg").lower()
    if ext in (".jpg", ".jpeg"):
        encode_ext = ".jpg"
        params = [cv2.IMWRITE_JPEG_QUALITY, 95]
    elif ext == ".png":
        encode_ext = ".png"
        params = []
    else:
        encode_ext = ext
        params = []
    ok, buf = cv2.imencode(encode_ext, frame_bgr, params)
    if not ok:
        raise OSError(f"Не удалось закодировать изображение: {path}")
    p.write_bytes(buf.tobytes())
