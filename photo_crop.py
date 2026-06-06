"""Автоматическое кадрирование центрального объекта на фото."""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class CropDetectResult:
    crop_rect: tuple[int, int, int, int] | None  # x, y, w, h — область сохранения
    found: bool


@dataclass(frozen=True)
class CropOptions:
    padding_ratio: float = 0.05
    center_region: float = 0.75
    min_area_ratio: float = 0.01
    max_area_ratio: float = 0.95
    center_bias: float = 2.0
    process_max_width: int = 1280


@dataclass(frozen=True)
class CropParams:
    enabled: bool = False
    padding_ratio: float = 0.05
    center_region: float = 0.75
    min_area_ratio: float = 0.01
    max_area_ratio: float = 0.95
    center_bias: float = 2.0
    process_max_width: int = 1280


def crop_params_from_camera(camera: object) -> CropParams:
    return CropParams(
        enabled=bool(getattr(camera, "crop_central_subject", False)),
        padding_ratio=float(getattr(camera, "crop_padding_ratio", 0.05) or 0.05),
        center_region=float(getattr(camera, "crop_center_region", 0.75) or 0.75),
        min_area_ratio=float(getattr(camera, "crop_min_area_ratio", 0.01) or 0.01),
        max_area_ratio=float(getattr(camera, "crop_max_area_ratio", 0.95) or 0.95),
    )


def _clamp_bbox(
    x: int,
    y: int,
    w: int,
    h: int,
    img_w: int,
    img_h: int,
    padding_ratio: float,
) -> tuple[int, int, int, int]:
    pad_x = int(w * padding_ratio)
    pad_y = int(h * padding_ratio)
    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(img_w, x + w + pad_x)
    y2 = min(img_h, y + h + pad_y)
    if x2 <= x1 or y2 <= y1:
        return 0, 0, img_w, img_h
    return x1, y1, x2 - x1, y2 - y1


def _center_zone(img_w: int, img_h: int, center_region: float) -> tuple[int, int, int, int]:
    frac = max(0.25, min(1.0, center_region))
    half_w = img_w * frac / 2.0
    half_h = img_h * frac / 2.0
    cx, cy = img_w / 2.0, img_h / 2.0
    return (
        int(cx - half_w),
        int(cy - half_h),
        int(cx + half_w),
        int(cy + half_h),
    )


def _center_overlap_ratio(
    x: int,
    y: int,
    bw: int,
    bh: int,
    zone: tuple[int, int, int, int],
) -> float:
    zx0, zy0, zx1, zy1 = zone
    ix0 = max(x, zx0)
    iy0 = max(y, zy0)
    ix1 = min(x + bw, zx1)
    iy1 = min(y + bh, zy1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = float((ix1 - ix0) * (iy1 - iy0))
    zone_area = float(max(1, (zx1 - zx0) * (zy1 - zy0)))
    return inter / zone_area


def _score_bbox(
    x: int,
    y: int,
    bw: int,
    bh: int,
    img_w: int,
    img_h: int,
    *,
    center_zone: tuple[int, int, int, int],
    min_area_ratio: float,
    max_area_ratio: float,
    center_bias: float,
) -> float:
    area = float(bw * bh)
    img_area = float(img_w * img_h)
    if area < min_area_ratio * img_area or area > max_area_ratio * img_area:
        return -1.0
    overlap = _center_overlap_ratio(x, y, bw, bh, center_zone)
    if overlap < 0.08:
        return -1.0
    mx = x + bw / 2.0
    my = y + bh / 2.0
    cx, cy = img_w / 2.0, img_h / 2.0
    dist = math.hypot(mx - cx, my - cy) / max(math.hypot(cx, cy), 1.0)
    center_score = max(0.0, 1.0 - dist)
    return area * (0.5 + overlap) * (1.0 + center_bias * center_score)


def _best_bbox_from_contours(
    contours: list,
    img_w: int,
    img_h: int,
    *,
    center_zone: tuple[int, int, int, int],
    min_area_ratio: float,
    max_area_ratio: float,
    center_bias: float,
) -> tuple[int, int, int, int] | None:
    best_score = -1.0
    best: tuple[int, int, int, int] | None = None
    for cnt in contours:
        if cnt is None or len(cnt) < 3:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        score = _score_bbox(
            x,
            y,
            bw,
            bh,
            img_w,
            img_h,
            center_zone=center_zone,
            min_area_ratio=min_area_ratio,
            max_area_ratio=max_area_ratio,
            center_bias=center_bias,
        )
        if score > best_score:
            best_score = score
            best = (x, y, bw, bh)
    return best


def _best_bbox_from_mask(
    mask: np.ndarray,
    img_w: int,
    img_h: int,
    opts: CropOptions,
    zone: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bbox = _best_bbox_from_contours(
        list(contours),
        img_w,
        img_h,
        center_zone=zone,
        min_area_ratio=opts.min_area_ratio,
        max_area_ratio=opts.max_area_ratio,
        center_bias=opts.center_bias,
    )
    if bbox is not None:
        return bbox

    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best_score = -1.0
    best: tuple[int, int, int, int] | None = None
    for i in range(1, n):
        x, y, bw, bh, _area = stats[i]
        score = _score_bbox(
            int(x),
            int(y),
            int(bw),
            int(bh),
            img_w,
            img_h,
            center_zone=zone,
            min_area_ratio=opts.min_area_ratio,
            max_area_ratio=opts.max_area_ratio,
            center_bias=opts.center_bias,
        )
        if score > best_score:
            best_score = score
            best = (int(x), int(y), int(bw), int(bh))
    return best


def _foreground_mask_from_background(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    margin = max(3, int(min(h, w) * 0.06))
    strips = [
        lab[:margin, :, :].reshape(-1, 3),
        lab[-margin:, :, :].reshape(-1, 3),
        lab[:, :margin, :].reshape(-1, 3),
        lab[:, -margin:, :].reshape(-1, 3),
    ]
    border = np.concatenate(strips, axis=0)
    bg = np.median(border, axis=0)
    diff = np.sqrt(np.sum((lab - bg) ** 2, axis=2))
    diff_u8 = np.clip(diff * 1.8, 0, 255).astype(np.uint8)
    blur = cv2.GaussianBlur(diff_u8, (5, 5), 0)
    _t, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    return mask


def _find_bbox_on_bgr(bgr: np.ndarray, opts: CropOptions) -> tuple[int, int, int, int] | None:
    h, w = bgr.shape[:2]
    zone = _center_zone(w, h, opts.center_region)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    fg = _foreground_mask_from_background(bgr)
    bbox = _best_bbox_from_mask(fg, w, h, opts, zone)
    if bbox is not None:
        return bbox

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 30, 100)
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    dilated = cv2.dilate(closed, kernel, iterations=1)
    bbox = _best_bbox_from_mask(dilated, w, h, opts, zone)
    if bbox is not None:
        return bbox

    for mode in (cv2.THRESH_BINARY, cv2.THRESH_BINARY_INV):
        _t, mask = cv2.threshold(blurred, 0, 255, mode | cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=3)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        bbox = _best_bbox_from_mask(mask, w, h, opts, zone)
        if bbox is not None:
            return bbox
    return None


def _opts_from_params(params: CropParams) -> CropOptions:
    return CropOptions(
        padding_ratio=max(0.0, params.padding_ratio),
        center_region=params.center_region,
        min_area_ratio=params.min_area_ratio,
        max_area_ratio=params.max_area_ratio,
        center_bias=params.center_bias,
    )


def _scale_work_frame(
    frame_bgr: np.ndarray,
    opts: CropOptions,
) -> tuple[np.ndarray, float, int, int]:
    orig_h, orig_w = frame_bgr.shape[:2]
    scale = 1.0
    work = frame_bgr
    if orig_w > opts.process_max_width > 0:
        scale = opts.process_max_width / float(orig_w)
        work = cv2.resize(
            frame_bgr,
            (int(orig_w * scale), int(orig_h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    return work, scale, orig_w, orig_h


def _finalize_crop_rect(
    bbox: tuple[int, int, int, int] | None,
    *,
    scale: float,
    orig_w: int,
    orig_h: int,
    padding_ratio: float,
) -> tuple[int, int, int, int] | None:
    if bbox is None:
        return None
    x, y, bw, bh = bbox
    if scale != 1.0:
        inv = 1.0 / scale
        x = int(x * inv)
        y = int(y * inv)
        bw = int(bw * inv)
        bh = int(bh * inv)
    x, y, bw, bh = _clamp_bbox(x, y, bw, bh, orig_w, orig_h, padding_ratio)
    if bw < orig_w * 0.08 or bh < orig_h * 0.08:
        return None
    if bw >= orig_w * 0.97 and bh >= orig_h * 0.97:
        return None
    return x, y, bw, bh


def detect_crop_rect(
    frame_bgr: np.ndarray,
    params: CropParams | None = None,
    *,
    padding_ratio: float | None = None,
    center_region: float | None = None,
    min_area_ratio: float | None = None,
    max_area_ratio: float | None = None,
) -> CropDetectResult:
    """Определяет прямоугольник сохранения (x, y, w, h) или None."""
    if frame_bgr is None or frame_bgr.size == 0:
        return CropDetectResult(None, False)

    if params is None:
        params = CropParams(
            padding_ratio=padding_ratio if padding_ratio is not None else 0.05,
            center_region=center_region if center_region is not None else 0.75,
            min_area_ratio=min_area_ratio if min_area_ratio is not None else 0.01,
            max_area_ratio=max_area_ratio if max_area_ratio is not None else 0.95,
        )
    opts = _opts_from_params(params)
    work, scale, orig_w, orig_h = _scale_work_frame(frame_bgr, opts)
    bbox = _find_bbox_on_bgr(work, opts)
    rect = _finalize_crop_rect(
        bbox,
        scale=scale,
        orig_w=orig_w,
        orig_h=orig_h,
        padding_ratio=opts.padding_ratio,
    )
    return CropDetectResult(rect, rect is not None)


def draw_crop_overlay(
    frame_bgr: np.ndarray,
    crop_rect: tuple[int, int, int, int] | None,
    *,
    color: tuple[int, int, int] = (0, 255, 0),
    thickness: int = 3,
) -> np.ndarray:
    """Рисует зелёную рамку области сохранения."""
    if frame_bgr is None or frame_bgr.size == 0 or crop_rect is None:
        return frame_bgr
    out = frame_bgr.copy()
    x, y, w, h = crop_rect
    cv2.rectangle(out, (x, y), (x + w - 1, y + h - 1), color, max(2, thickness))
    return out


def crop_central_subject(
    frame_bgr: np.ndarray,
    params: CropParams | None = None,
    *,
    padding_ratio: float = 0.05,
    center_region: float = 0.75,
    min_area_ratio: float = 0.01,
    max_area_ratio: float = 0.95,
) -> tuple[np.ndarray, bool, CropDetectResult]:
    """
    Обрезает кадр по доминирующему объекту в центральной зоне.
    Возвращает (кадр, был_ли_применён_кроп, результат_детекции).
    """
    if params is None:
        params = CropParams(
            padding_ratio=padding_ratio,
            center_region=center_region,
            min_area_ratio=min_area_ratio,
            max_area_ratio=max_area_ratio,
        )
    detected = detect_crop_rect(frame_bgr, params)
    if not detected.found or detected.crop_rect is None:
        return frame_bgr, False, detected
    x, y, w, h = detected.crop_rect
    cropped = frame_bgr[y : y + h, x : x + w].copy()
    return cropped, True, detected
