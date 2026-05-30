"""Layout metrics ported from Tkinter UI (character widths, margins)."""

MIN_WINDOW_W = 800
MIN_WINDOW_H = 520
WIN_W_MAX = 1180
WIN_W_MIN = 920
WIN_SCREEN_RATIO = 0.88
WIN_MARGIN = 48
WIN_Y_OFFSET = 16

SHELL_PAD = (8, 8, 8, 4)
NOTEBOOK_PAD = (6, 4)
SEARCH_PAD = 8
GRID_PAD = (4, 3)
GRID_PAD_WIDE = (6, 3)
TAB4_PAD = (4, 4)

ENTRY_W_SERIAL = 36
ENTRY_W_DEVICE_PP = 28
ENTRY_W_PATH = 56
ENTRY_W_PATH_WIDE = 60
ENTRY_W_FIELD = 58
ENTRY_W_TEXTAREA = 60
COMBO_W_FIELD = 58
COMBO_W_FONT = 30
COMBO_W_CAMERA = 6
ENTRY_W_PHOTO_FN = 26
SPIN_W_CAMERA = 8
SPIN_W_FONT = 6

STATUS_WRAP = 880
# Вкладка «Фотографирование», ориентир 1920×1080 (рабочая область окна ~1180×900)
PHOTO_TAB_REF_W = 1140
PHOTO_COL_CAMERA = 2
PHOTO_COL_SLOTS = 1
CAMERA_CONTROLS_H = 40
PHOTO_PREVIEW_SLOTS_MARGIN = 6
PHOTO_PREVIEW_SIZE_FACTOR = 2.0
# Высота одной строки сетки слотов (2 в ряд): миниатюра + кнопка + подсказка
PHOTO_SLOT_ROW_H = 148
PREVIEW_MIN_W = 240
PREVIEW_MIN_H = 135
PREVIEW_MAX_W = 960
PREVIEW_MAX_H = 540
THUMB_W = 152
THUMB_H = 114
HINT_WRAP = 180
HINT_MAX_LINES_H = 34
PREVIEW_INTERVAL_MS = 30

# Полноэкранная съёмка: резерв под подсказки, кнопки и строку статуса внизу окна
CAPTURE_DIALOG_FOOTER_H = 110

COLOR_REF_GRAY = "#808080"
COLOR_HINT = "#444444"


def photo_slots_content_height(num_slots: int) -> int:
    """Оценка высоты сетки слотов (2 колонки) для подгонки превью камеры."""
    n = max(1, int(num_slots))
    rows = (n + 1) // 2
    return rows * PHOTO_SLOT_ROW_H + max(0, rows - 1) * GRID_PAD[1] * 2 + 4
