"""
Shefos Tycoon — pygame: шефосеры (ручной + авто), конвейер, сохранение, магазин, темы.
>20 кликов/с по ручному шефосеру — удаление сохранения и выход (античит).
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import math
import os
import random
import sys
import warnings
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Deque, Dict, List, Optional, Tuple

# Чёткое масштабирование (nearest), не линейная интерполяция SDL
os.environ.setdefault("SDL_RENDER_SCALE_QUALITY", "0")

import pygame
from cryptography.fernet import Fernet

try:
    # Fallback рендер текста, если pygame.font не доступен (SDL_ttf / freetype не собраны).
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
except Exception:  # pragma: no cover - зависит от наличия Pillow
    Image = None
    ImageDraw = None
    ImageFont = None

def _ui_font_family() -> str:
    """Единый фолбек для шрифта интерфейса на разных ОС."""
    if sys.platform == "win32":
        return "segoeui"
    # Часто доступен в Linux-дистрибутивах; pygame fallback тоже сработает.
    return "DejaVu Sans"

def _resource_root() -> str:
    """Ресурсы (картинки, звуки): в onefile-exe — распакованный каталог PyInstaller."""
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        return meipass
    return os.path.dirname(os.path.abspath(__file__))


def _data_root() -> str:
    """Сохранения: рядом с exe (портабельно) или рядом со скриптом при запуске из исходников."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


RESOURCE_ROOT = _resource_root()
DATA_ROOT = _data_root()
# Логический кадр игры (внутренний буфер); окно по умолчанию меньше — масштаб с сохранением пропорций
DISPLAY_W, DISPLAY_H = 1920, 1080
DEFAULT_WINDOW_W, DEFAULT_WINDOW_H = 1280, 720
SAVE_PATH = os.path.join(DATA_ROOT, "shefos_save.enc")
LEGACY_SAVE_JSON = os.path.join(DATA_ROOT, "shefos_save.json")

def _fernet_cipher() -> Fernet:
    key = base64.urlsafe_b64encode(
        hashlib.sha256(b"SHEFOS_SAVE_FERNET_KEY_v2\x7f\x91").digest()
    )
    return Fernet(key)


def _render_text_surface_pil(
    text: str, size: int, color: Tuple[int, int, int], *, bold: bool = False
) -> pygame.Surface:
    """Render text to a pygame.Surface via Pillow (RGBA)."""
    # Если Pillow недоступен — возвращаем прозрачную заглушку, но без краша.
    if Image is None or ImageDraw is None or ImageFont is None:
        return pygame.Surface((1, 1), pygame.SRCALPHA)

    return _render_text_surface_pil_cached(text, size, color, bold)


@lru_cache(maxsize=4096)
def _render_text_surface_pil_cached(
    text: str, size: int, color: Tuple[int, int, int], bold: bool
) -> pygame.Surface:
    """Cached PIL text rendering to keep FPS acceptable."""
    # Шрифты, упакованные в onefile PyInstaller (fonts/*), приоритет №1.
    regular_candidates = [
        os.path.join(RESOURCE_ROOT, "DejaVuSans.ttf"),
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    bold_candidates = [
        os.path.join(RESOURCE_ROOT, "DejaVuSans-Bold.ttf"),
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]

    if sys.platform == "win32":
        regular_candidates = [
            os.path.join(RESOURCE_ROOT, "DejaVuSans.ttf"),
            r"C:\Windows\Fonts\arial.ttf",
        ] + regular_candidates
        bold_candidates = [
            os.path.join(RESOURCE_ROOT, "DejaVuSans-Bold.ttf"),
            r"C:\Windows\Fonts\arialbd.ttf",
        ] + bold_candidates

    candidates = bold_candidates if bold else regular_candidates
    font_path = next((p for p in candidates if os.path.isfile(p)), None)
    if font_path:
        if os.environ.get("SHEFOS_DEBUG_FONT") == "1":
            print(f"[SHEFOS_DEBUG_FONT] {font_path}", file=sys.stderr)
        font = ImageFont.truetype(font_path, size=size)
    else:
        font = ImageFont.load_default()

    tmp = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    draw_tmp = ImageDraw.Draw(tmp)
    try:
        bbox = draw_tmp.textbbox((0, 0), text, font=font)
        left, top, right, bottom = bbox
        w = max(1, int(right - left))
        h = max(1, int(bottom - top))
    except Exception:
        w, h = max(1, size * max(1, len(text)) // 2), max(1, size)
        left, top = 0, 0

    pad = 2
    img = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((pad - left, pad - top), text, font=font, fill=color)

    return pygame.image.frombuffer(img.tobytes(), img.size, "RGBA")


def _render_text_surface(
    text: str, size: int, color: Tuple[int, int, int], *, bold: bool = False
) -> pygame.Surface:
    """Render text to pygame.Surface using pygame.font if possible, otherwise PIL."""
    try:
        # Если pygame.font недоступен — будет исключение (и мы уйдём в PIL).
        f = pygame.font.SysFont(_ui_font_family(), size, bold=bold)
        return f.render(text, True, color)
    except Exception:
        return _render_text_surface_pil(text, size, color, bold=bold)


class _PILFontWrapper:
    """Мини-обёртка, совместимая по API с pygame.font: только .render()."""

    def __init__(self, size: int, *, bold: bool = False) -> None:
        self._size = size
        self._bold = bold

    def render(
        self, text: str, antialias: bool, color: Tuple[int, int, int]
    ) -> pygame.Surface:
        # antialias игнорируем: Pillow и так делает сглаживание на TTF.
        return _render_text_surface(text, self._size, color, bold=self._bold)


def _draw_icon_close(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    s = 11
    w = 3
    pygame.draw.line(surf, c, (cx - s, cy - s), (cx + s, cy + s), w)
    pygame.draw.line(surf, c, (cx - s, cy + s), (cx + s, cy - s), w)


def _draw_icon_moon(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    r = pygame.Rect(cx - 12, cy - 12, 24, 24)
    pygame.draw.arc(surf, c, r, 0.4, 2.9, 5)


def _draw_icon_sun(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pygame.draw.circle(surf, c, (cx, cy), 7)
    for i in range(8):
        a = i * math.pi / 4
        x1 = int(cx + math.cos(a) * 10)
        y1 = int(cy + math.sin(a) * 10)
        x2 = int(cx + math.cos(a) * 18)
        y2 = int(cy + math.sin(a) * 18)
        pygame.draw.line(surf, c, (x1, y1), (x2, y2), 3)


def _draw_icon_eye(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pygame.draw.ellipse(surf, c, (cx - 18, cy - 10, 36, 20), 2)
    pygame.draw.circle(surf, c, (cx, cy), 5)


def _draw_icon_eye_slash(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pygame.draw.ellipse(surf, c, (cx - 18, cy - 10, 36, 20), 2)
    pygame.draw.line(surf, c, (cx - 15, cy + 7), (cx + 15, cy - 7), 3)


def _draw_icon_check(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pts = [(cx - 13, cy + 1), (cx - 4, cy + 11), (cx + 15, cy - 11)]
    pygame.draw.lines(surf, c, False, pts, 4)


def _draw_icon_minus(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pygame.draw.line(surf, c, (cx - 12, cy), (cx + 12, cy), 4)


def _draw_icon_plus(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pygame.draw.line(surf, c, (cx - 12, cy), (cx + 12, cy), 4)
    pygame.draw.line(surf, c, (cx, cy - 12), (cx, cy + 12), 4)


def _draw_icon_coin(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pygame.draw.circle(surf, c, (cx, cy), 10, 2)
    pygame.draw.circle(surf, c, (cx - 3, cy - 3), 3)


def _draw_icon_zap(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pts = [
        (cx + 7, cy - 13),
        (cx - 9, cy - 1),
        (cx + 2, cy + 2),
        (cx - 6, cy + 15),
        (cx + 13, cy - 4),
    ]
    pygame.draw.polygon(surf, c, pts)


def _draw_icon_chevron_left(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pts = [(cx + 6, cy - 11), (cx - 8, cy), (cx + 6, cy + 11)]
    pygame.draw.polygon(surf, c, pts)


def _draw_icon_chevron_right(
    surf: pygame.Surface, cx: int, cy: int, c: Tuple[int, int, int]
) -> None:
    pts = [(cx - 6, cy - 11), (cx + 8, cy), (cx - 6, cy + 11)]
    pygame.draw.polygon(surf, c, pts)


CLICKS_PER_SEC_LIMIT = 20
THROWER_HALF = 56
MAX_AUTOS = 8  # легаси: лимит «всего» для старых сохранений
AUTOS_PER_FLOOR = 8
MAX_FLOORS = 24
MAX_MANUAL_LEVEL = 60
MAX_VISUAL_TIER = 8  # картинка и звук апгрейда — только 1…8; уровень может расти дальше
GAME_W = 1280

SHOP_TAB_EARN = "earn"
SHOP_TAB_BASE = "base"
SHOP_TAB_WEAPONS = "weapons"

RAID_INTERVAL_SEC = 300.0
RAID_CHANCE = 0.5
# Внутри одного рейда делаем несколько волн и паузы между ними.
RAID_WAVES = 3
RAID_WAVE_TARGETS: List[int] = [28, 36, 40]  # всего: 104 красношляпника (включая босса)
RAID_WAVE_PAUSE_SEC = 5.0
RAID_SPAWN_INTERVAL = 0.35

# У шефосера 3 HP (было 10)
RAID_SHEF_MAX_HP = 3

RAID_ENEMY_SPEED = 100.0
RAID_HIT_RADIUS = 46
RAID_BULLET_RADIUS = 36
REDHAT_FILE = "redhat.png"

# (название, цена, перезарядка с, скорость снаряда, сила/урон за попадание)
# Индексы = "тиры" оружий для рейда:
# 0: 1 тир (кинжал)
# 1: 2 тир (бомба)
# 2: 3 тир (разлёт)
# 3: 4 тир (цепь)
# 4: 5 тир (лазер)
WEAPON_DEFS: List[Tuple[str, int, float, float, int]] = [
    ("Нейро-кинжал Mk.I", 420, 0.38, 1100, 2),  # чуть сильнее
    ("Плазменный fork()", 1800, 0.30, 1300, 2),  # бомба 2 тиром (чуть слабее)
    ("Systemd-стиратель", 6500, 0.24, 1500, 3),  # разлёт
    ("Модуль .ko-убийца", 22000, 0.19, 1750, 4),  # цепь 4 тиром
    ("Override.conf XL", 62000, 0.06, 2100, 99),  # лазер 5 тиром (ваншот)
]

RAID_MUSIC_FILE = "raid.mp3"

# Название атаки для UI (должно совпадать по индексу с WEAPON_DEFS)
WEAPON_ATTACK_LABELS: List[str] = ["Кинжал", "Бомба", "Разлёт", "Цепь", "Лазер"]

# moon.png — только иконка светлой/тёмной темы (не шефосеры)
THEME_ICON_FILE = "moon.png"
SHEFCOIN_FILE = "shefcoin.png"

# Шефосеры: ур.1…8 → картинка (ручной и авто по уровню; дальше цикл по 8)
LEVEL_SHEFOSER_IMAGES: List[str] = [
    "tux.png",  # 1
    "xut.png",  # 2
    "arch.png",  # 3
    "320kilo.png",  # 4
    "shefos.png",  # 5
    "zirtux.png",  # 6
    "glitchtux.png",  # 7
    "shefwinner.png",  # 8
]

# Звук при достижении тира 1…8 (апгрейд ручного/авто). Размещение авто ур.1 — отдельно button.mp3
TIER_SOUND_FILES: Dict[int, str] = {
    1: "button.mp3",
    2: "invert.mp3",
    3: "arch.mp3",
    4: "320kg.mp3",
    5: "shef.mp3",
    6: "trueshef.mp3",
    7: "shefglitch.mp3",
    8: "king.mp3",
}

# Функциональные кнопки магазина (настройки, покупка «в режим размещения» и т.п.)
TAP_SOUND_FILE = "tap.mp3"

_SKIN_PALETTES: List[Tuple[int, int, int]] = [
    (240, 180, 80),
    (120, 200, 240),
    (200, 120, 220),
    (120, 220, 160),
    (240, 140, 120),
]


def _make_placeholder_surface(index: int) -> pygame.Surface:
    surf = pygame.Surface((48, 48), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))
    c = _SKIN_PALETTES[index % len(_SKIN_PALETTES)]
    pygame.draw.circle(surf, c, (24, 24), 20)
    pygame.draw.circle(
        surf,
        (max(0, c[0] // 3), max(0, c[1] // 3), max(0, c[2] // 3)),
        (24, 24),
        20,
        3,
    )
    t = _render_text_surface(str(index + 1), 16, (20, 20, 24), bold=True)
    surf.blit(t, (24 - t.get_width() // 2, 24 - t.get_height() // 2))
    return surf


def load_scaled_skin_surfs() -> List[pygame.Surface]:
    """Текстуры шефосеров 1…8 — крупнее для 1080p, без сглаживания при уменьшении."""
    surfs: List[pygame.Surface] = []
    for i, fn in enumerate(LEVEL_SHEFOSER_IMAGES):
        path = os.path.join(RESOURCE_ROOT, fn)
        if os.path.isfile(path):
            img = pygame.image.load(path).convert_alpha()
        else:
            img = _make_placeholder_surface(i)
        iw = max(56, min(128, img.get_width()))
        ih = int(img.get_height() * (iw / max(1, img.get_width())))
        surfs.append(pygame.transform.scale(img, (iw, ih)))
    return surfs


def load_theme_moon_icon() -> Optional[pygame.Surface]:
    """Иконка темы (светлая/тёмная) — moon.png."""
    path = os.path.join(RESOURCE_ROOT, THEME_ICON_FILE)
    if not os.path.isfile(path):
        return None
    img = pygame.image.load(path).convert_alpha()
    iw = max(44, min(80, img.get_width()))
    ih = int(img.get_height() * (iw / max(1, img.get_width())))
    return pygame.transform.scale(img, (iw, ih))


def load_shefcoin_icon() -> Optional[pygame.Surface]:
    """Иконка монет в HUD — shefcoin.png."""
    path = os.path.join(RESOURCE_ROOT, SHEFCOIN_FILE)
    if not os.path.isfile(path):
        return None
    img = pygame.image.load(path).convert_alpha()
    h = 40
    w = max(1, int(img.get_width() * (h / max(1, img.get_height()))))
    return pygame.transform.smoothscale(img, (w, h))


def load_redhat_surface() -> pygame.Surface:
    path = os.path.join(RESOURCE_ROOT, REDHAT_FILE)
    if os.path.isfile(path):
        img = pygame.image.load(path).convert_alpha()
        side = 52
        return pygame.transform.smoothscale(
            img,
            (
                side,
                max(1, int(img.get_height() * (side / max(1, img.get_width())))),
            ),
        )
    surf = pygame.Surface((52, 52), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))
    pygame.draw.ellipse(surf, (180, 40, 40), (4, 8, 44, 40))
    pygame.draw.rect(surf, (120, 30, 30), (18, 22, 16, 8))
    return surf


def delete_save_file() -> None:
    for p in (SAVE_PATH, LEGACY_SAVE_JSON):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def show_autoclicker_alert_and_exit() -> None:
    title = "Автокликер"
    text = (
        "Обнаружено более 20 кликов в секунду по ручному шефосеру.\n"
        "Сохранение удалено. Игра будет закрыта."
    )
    delete_save_file()
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x30)
    else:
        print(title, text, file=sys.stderr)
    pygame.quit()
    os._exit(1)


@dataclass
class ItemOnBelt:
    x: float
    y: float
    phase: str
    vx: float = 0.0
    vy: float = 0.0
    coin_value: int = 1
    skin_slot: int = 0  # индекс в _skin_surfs


@dataclass
class AutoShefoser:
    x: float
    y: float
    level: int = 1
    cooldown: float = 0.0


@dataclass
class RaidRedhat:
    x: float
    y: float
    id: int = 0
    level: int = 1  # 1..4
    hp: int = 1
    damage: int = 1  # сколько HP теряет шефосер при контакте
    speed_mul: float = 1.0  # множитель скорости движения
    is_boss: bool = False
    hit_radius: int = RAID_HIT_RADIUS
    scale_mul: float = 1.0  # визуальный масштаб
    alive: bool = True


@dataclass
class RaidBullet:
    x: float
    y: float
    vx: float
    vy: float
    life: float = 2.2
    damage: int = 1  # урон по красношляпнику
    kind: str = "basic"  # basic|spread|bomb|chain
    aoe_radius: float = 0.0
    chain_left: int = 0  # сколько дополнительных прыжков после текущего попадания
    last_hit_id: int = -1


THEMES: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "dark": {
        "bg": (24, 26, 30),
        "panel": (32, 34, 40),
        "panel_line": (60, 64, 72),
        "shop": (28, 30, 36),
        "shop_line": (55, 58, 66),
        "belt": (45, 48, 55),
        "belt_edge": (70, 75, 85),
        "belt_mark": (90, 95, 105),
        "btn": (48, 52, 62),
        "btn_hi": (90, 100, 118),
        "btn_dis": (38, 40, 44),
        "text": (230, 232, 238),
        "text_dim": (168, 172, 182),
        "text_muted": (135, 140, 150),
        "money": (200, 210, 120),
        "accent": (240, 200, 120),
        "conv": (45, 48, 55),
    },
    "light": {
        "bg": (248, 249, 252),
        "panel": (230, 232, 240),
        "panel_line": (180, 186, 198),
        "shop": (236, 238, 245),
        "shop_line": (200, 205, 218),
        "belt": (210, 214, 224),
        "belt_edge": (160, 168, 182),
        "belt_mark": (130, 138, 155),
        "btn": (200, 206, 220),
        "btn_hi": (120, 140, 200),
        "btn_dis": (220, 222, 228),
        "text": (28, 32, 42),
        "text_dim": (70, 78, 95),
        "text_muted": (110, 118, 132),
        "money": (90, 120, 40),
        "accent": (180, 100, 40),
        "conv": (210, 214, 224),
    },
}


class TycoonGame:
    # Внутренний кадр 1920×1080; окно может быть меньше — см. _present_canvas / _logical_pos
    W, H = DISPLAY_W, DISPLAY_H
    PANEL_H = 188
    BELT_Y = 756
    BELT_H = 108
    BELT_SPEED_BASE = 236.0

    def __init__(self) -> None:
        pygame.init()
        # На некоторых Linux-системах/в средах без аудио или если pygame собран
        # без SDL_mixer, `pygame.mixer` может отсутствовать/не быть доступным.
        # В таком случае игра работает без звука.
        self._mixer_ok = False
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            self._mixer_ok = True
        except Exception:
            self._mixer_ok = False
        if os.environ.get("SHEFOS_DEBUG_MIXER") == "1":
            print(f"[SHEFOS_DEBUG_MIXER] mixer_ok={self._mixer_ok}", file=sys.stderr)
        pygame.display.set_caption("Shefos Tycoon — F11 полный экран")
        self._fullscreen = False
        _win_flags = pygame.DOUBLEBUF | pygame.RESIZABLE
        try:
            self.screen = pygame.display.set_mode(
                (DEFAULT_WINDOW_W, DEFAULT_WINDOW_H),
                _win_flags,
            )
        except pygame.error:
            self.screen = pygame.display.set_mode((DEFAULT_WINDOW_W, DEFAULT_WINDOW_H))
        self._canvas = pygame.Surface((self.W, self.H))
        self._dst_rect = pygame.Rect(0, 0, self.W, self.H)
        self.clock = pygame.time.Clock()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                self.font = pygame.font.SysFont(_ui_font_family(), 34)
                self.font_title = pygame.font.SysFont(_ui_font_family(), 38, bold=True)
                self.font_small = pygame.font.SysFont(_ui_font_family(), 26)
                self.font_tiny = pygame.font.SysFont(_ui_font_family(), 22)
                self.font_btn = pygame.font.SysFont(_ui_font_family(), 28)
        except Exception:
            # В текущей системе `pygame.font` может быть нерабочим:
            # тогда рендерим текст через Pillow.
            self.font = _PILFontWrapper(34)
            self.font_title = _PILFontWrapper(38, bold=True)
            self.font_small = _PILFontWrapper(26)
            self.font_tiny = _PILFontWrapper(22)
            self.font_btn = _PILFontWrapper(28)

        self._skin_surfs = load_scaled_skin_surfs()
        self._n_skins = max(1, len(self._skin_surfs))
        self._moon_icon = load_theme_moon_icon()
        self._shefcoin_icon = load_shefcoin_icon()

        self._sfx_tap: Optional[pygame.mixer.Sound] = None
        if self._mixer_ok:
            _tap_path = os.path.join(RESOURCE_ROOT, TAP_SOUND_FILE)
            if os.path.isfile(_tap_path):
                try:
                    self._sfx_tap = pygame.mixer.Sound(_tap_path)
                except Exception:
                    self._sfx_tap = None

        self._tier_sounds: Dict[int, pygame.mixer.Sound] = {}
        if self._mixer_ok:
            for tier, fn in TIER_SOUND_FILES.items():
                p = os.path.join(RESOURCE_ROOT, fn)
                if os.path.isfile(p):
                    try:
                        self._tier_sounds[tier] = pygame.mixer.Sound(p)
                    except Exception:
                        # Если конкретный звук поврежден или mixer недоступен
                        # уже после init — просто пропускаем.
                        continue

        self._raid_music: Optional[pygame.mixer.Sound] = None
        if self._mixer_ok:
            p = os.path.join(RESOURCE_ROOT, RAID_MUSIC_FILE)
            if os.path.isfile(p):
                try:
                    self._raid_music = pygame.mixer.Sound(p)
                except Exception:
                    self._raid_music = None

        self.belt_scroll = 0.0
        self.items: List[ItemOnBelt] = []
        self.autos_by_floor: List[List[AutoShefoser]] = [[]]
        self.floors_owned: int = 1
        self.current_floor: int = 0
        self._placement_paid: int = 0

        belt_cy = self.BELT_Y + self.BELT_H // 2
        self.thrower_x = 224.0
        self.thrower_y = 404.0
        self.landing_x = 800.0
        self.landing_y = belt_cy - 20

        self.manual_level = 1
        self.money = 0
        self.placement_pending = False
        self._save_timer = 0.0
        self.theme = "dark"
        self.selected_auto: Optional[Tuple[int, int]] = None

        self._btn_floor_prev: Optional[pygame.Rect] = None
        self._btn_floor_next: Optional[pygame.Rect] = None
        self._btn_floor_buy: Optional[pygame.Rect] = None

        self._thrower_rect = pygame.Rect(
            int(self.thrower_x - THROWER_HALF),
            int(self.thrower_y - THROWER_HALF),
            THROWER_HALF * 2,
            THROWER_HALF * 2,
        )
        self._click_times: Deque[float] = deque()

        self._btn_manual: Optional[pygame.Rect] = None
        self._btn_auto_buy: Optional[pygame.Rect] = None
        self._btn_auto_upgrade: Optional[pygame.Rect] = None
        self._btn_settings: Optional[pygame.Rect] = None
        self._btn_tab_earn: Optional[pygame.Rect] = None
        self._btn_tab_base: Optional[pygame.Rect] = None
        self._btn_tab_weapons: Optional[pygame.Rect] = None
        self._btn_weapon_rows: List[Optional[pygame.Rect]] = [None] * len(WEAPON_DEFS)

        self.shop_tab: str = SHOP_TAB_EARN
        self.weapons_owned: List[bool] = [False] * len(WEAPON_DEFS)
        self.equipped_weapon: int = -1
        self.money_debt: int = 0
        self.raid_active: bool = False
        self.raid_shef_hp: int = 0
        self.raid_redhats: List[RaidRedhat] = []
        self.raid_bullets: List[RaidBullet] = []
        self._raid_redhat_id_seq: int = 1
        self.raid_spawned: int = 0  # всего спавнов за рейд
        self.raid_spawn_timer: float = 0.0
        self._raid_wave_timer: float = RAID_INTERVAL_SEC
        self._raid_banner_sec: float = 0.0
        self._raid_game_msg_sec: float = 0.0
        self._redhat_surf: pygame.Surface = load_redhat_surface()
        self._raid_weapon_cd = 0.0

        # Визуал лазера (тир 5): показываем большую полоску, пока держишь/стреляешь.
        self._raid_laser_t: float = 0.0
        self._raid_laser_start: Tuple[float, float] = (224.0, 404.0)
        self._raid_laser_end: Tuple[float, float] = (224.0 + 200.0, 404.0)
        self._raid_laser_width: int = 28

        # Новая логика рейда: волны + паузы
        self.raid_wave_idx: int = 0
        self.raid_wave_spawned: int = 0
        self.raid_wave_pause_timer: float = 0.0
        self.raid_wave_target: int = RAID_WAVE_TARGETS[0] if RAID_WAVE_TARGETS else 0

        # Предподготовим спрайты красношляпников под 4 уровня (чтобы не рескейлить каждый кадр)
        base = self._redhat_surf
        bw, bh = base.get_width(), base.get_height()
        level_scales = {1: 1.0, 2: 1.15, 3: 1.35, 4: 2.4}
        self._raid_redhat_surf_by_level: Dict[int, pygame.Surface] = {}
        for lvl, sm in level_scales.items():
            w = max(1, int(bw * sm))
            h = max(1, int(bh * sm))
            self._raid_redhat_surf_by_level[lvl] = pygame.transform.smoothscale(base, (w, h))

        self.belt_speed_mul = 1.0
        self.cheat_unlocked = False
        self.cheat_active = False
        self.settings_open = False
        self._password_buf = ""
        self._pw_show = False
        self._settings_focus_field: Optional[str] = None
        self._cheat_msg = ""
        self.cheat_time_scale = 1.0
        self.cheat_free_shop = False
        self.cheat_belt_frozen = False
        self.cheat_max_autos_override = 32
        self._cheat_field_money = ""
        self._cheat_field_manual = ""
        self._cheat_field_belt = ""
        self._cheat_field_time = ""
        self._cheat_field_autos_cap = ""
        self._cheat_field_free_shop = "0"
        self._cheat_field_freeze = "0"
        self._cheat_field_auto_lvl = ""

        self._load_game()
        self._apply_theme_name(self.theme)
        self._native_blit = False
        self._update_display_transform()

    def _update_display_transform(self) -> None:
        """Окно = весь экран: 1:1 при совпадении размера, иначе растягивание на весь кадр без полос."""
        sw, sh = self.screen.get_size()
        lw, lh = self.W, self.H
        if not lw or not lh:
            return
        if sw == lw and sh == lh:
            self._dst_rect = pygame.Rect(0, 0, sw, sh)
            self._native_blit = True
            return
        self._native_blit = False
        self._dst_rect = pygame.Rect(0, 0, sw, sh)

    def _logical_pos(self, screen_pos: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """Координаты в логическом кадре из позиции на окне (весь кадр растянут на окно)."""
        mx, my = screen_pos
        sw, sh = self.screen.get_size()
        if sw <= 0 or sh <= 0:
            return None
        lx = mx * self.W / sw
        ly = my * self.H / sh
        return int(lx), int(ly)

    def _toggle_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            try:
                self.screen = pygame.display.set_mode(
                    (DISPLAY_W, DISPLAY_H),
                    pygame.FULLSCREEN | pygame.DOUBLEBUF,
                )
            except pygame.error:
                self.screen = pygame.display.set_mode(
                    (0, 0),
                    pygame.FULLSCREEN | pygame.DOUBLEBUF,
                )
        else:
            try:
                self.screen = pygame.display.set_mode(
                    (DEFAULT_WINDOW_W, DEFAULT_WINDOW_H),
                    pygame.DOUBLEBUF | pygame.RESIZABLE,
                )
            except pygame.error:
                self.screen = pygame.display.set_mode(
                    (DEFAULT_WINDOW_W, DEFAULT_WINDOW_H),
                )
        self._update_display_transform()

    def _c(self, key: str) -> Tuple[int, int, int]:
        return THEMES.get(self.theme, THEMES["dark"])[key]

    def _apply_theme_name(self, name: str) -> None:
        self.theme = name if name in THEMES else "dark"

    @staticmethod
    def _skin_slot_for_level(level: int) -> int:
        """Слот текстуры 0…7: после 8-го уровня визуал как у 8."""
        t = min(max(1, level), MAX_VISUAL_TIER)
        return t - 1

    def _play_upgrade_sound(self, new_level: int) -> None:
        """Звук только при росте тира 1…8; дальше — tap (без смены тира)."""
        if new_level <= MAX_VISUAL_TIER:
            self._play_sound_for_tier(new_level)
        else:
            self._play_tap()

    def _play_sound(self, sound: Optional[pygame.mixer.Sound]) -> None:
        if sound:
            try:
                sound.play()
            except Exception:
                pass

    def _play_tap(self) -> None:
        """Кнопки магазина/настроек, покупка авто (режим размещения), апгрейды через UI."""
        self._play_sound(self._sfx_tap)

    def _shop_free(self) -> bool:
        return self.cheat_unlocked and self.cheat_free_shop

    def _play_sound_for_tier(self, tier: int) -> None:
        """Звук тира 1…8: апгрейд уровня или размещение авто ур.1 (тир 1 = button)."""
        t = max(1, min(8, tier))
        self._play_sound(self._tier_sounds.get(t))

    def belt_speed(self) -> float:
        """Скорость конвейера слегка растёт с прогрессом (баланс дохода)."""
        tier = max(0, (self.manual_level - 1) // 10) + sum(
            max(0, a.level - 1) // 8 for a in self._all_autos_flat()
        )
        return (
            self.BELT_SPEED_BASE
            * (1.0 + 0.04 * min(tier, 12))
            * self.belt_speed_mul
        )

    def _all_autos_flat(self) -> List[AutoShefoser]:
        return [a for fl in self.autos_by_floor for a in fl]

    def _total_auto_count(self) -> int:
        return sum(len(fl) for fl in self.autos_by_floor)

    def _autos_on_current_floor(self) -> List[AutoShefoser]:
        return self.autos_by_floor[self.current_floor]

    def _max_autos_this_floor(self) -> int:
        if not self.cheat_unlocked:
            return AUTOS_PER_FLOOR
        return max(1, min(99999, self.cheat_max_autos_override))

    def _max_autos_effective(self) -> int:
        """Для совместимости с читом — лимит авто на одном этаже."""
        return self._max_autos_this_floor()

    def _ensure_floor_lists(self) -> None:
        while len(self.autos_by_floor) < self.floors_owned:
            self.autos_by_floor.append([])

    def _cancel_placement(self) -> None:
        if not self.placement_pending:
            return
        if self._placement_paid > 0 and not self._shop_free():
            self.money += self._placement_paid
        self._placement_paid = 0
        self.placement_pending = False

    def _change_floor(self, new_floor: int) -> None:
        if self.raid_active:
            return
        self.current_floor = max(0, min(new_floor, self.floors_owned - 1))
        self.selected_auto = None

    def buy_floor_cost(self) -> int:
        f = self.floors_owned
        return int(1400 * (1.72 ** (f - 1)))

    def _raid_power_score(self) -> int:
        return self.manual_level + sum(a.level for a in self._all_autos_flat())

    def _raid_win_reward(self) -> int:
        p = self._raid_power_score()
        return int(60 + p * 14 + (p * p) * 0.12)

    def _raid_lose_penalty(self) -> int:
        p = self._raid_power_score()
        return int(40 + p * 10 + (p * p) * 0.08)

    def _earn_coins(self, amount: int) -> None:
        if amount <= 0:
            return
        pay = min(amount, self.money_debt)
        self.money_debt -= pay
        self.money += amount - pay

    def _raid_take_penalty(self, amount: int) -> None:
        if amount <= 0:
            return
        if self.money >= amount:
            self.money -= amount
        else:
            self.money_debt += amount - self.money
            self.money = 0

    def _win32_raid_alert(self) -> None:
        msg = (
            "Обнаружена сетевая атака.\n\n"
            "Источник: Red Hat — направлен рейд на ваш локальный шефосер.\n"
            "Рекомендуется немедленно применить защитное ПО (вкладка «Оружие»)."
        )
        if sys.platform == "win32":
            try:
                ctypes.windll.user32.MessageBoxW(0, msg, "Безопасность Windows", 0x30)
            except (AttributeError, OSError):
                pass

    def _start_raid(self, *, force: bool = False) -> None:
        if self.raid_active:
            return
        if not force and self.current_floor != 0:
            return
        if force:
            self.current_floor = 0
            self.selected_auto = None
        self.raid_active = True
        self.raid_shef_hp = RAID_SHEF_MAX_HP
        self.raid_redhats.clear()
        self.raid_bullets.clear()
        self.raid_spawned = 0  # всего спавнов за рейд (для отладки/статистики)
        self.raid_wave_idx = 0
        self.raid_wave_spawned = 0
        self.raid_wave_pause_timer = 0.0
        self.raid_spawn_timer = 0.2  # таймер до первого спавна волны
        self.raid_wave_target = RAID_WAVE_TARGETS[0] if RAID_WAVE_TARGETS else 0
        self._raid_banner_sec = 3.0
        self._raid_game_msg_sec = 0.0
        self._raid_weapon_cd = 0.0
        self._win32_raid_alert()
        self._play_tap()

        # Музыка рейда (loop), если mixer доступен и файл есть.
        if self._mixer_ok and self._raid_music is not None:
            try:
                self._raid_music.play(loops=-1)
            except Exception:
                pass

    def _end_raid_win(self) -> None:
        r = self._raid_win_reward()
        self._earn_coins(r)
        self.raid_active = False
        self.raid_redhats.clear()
        self.raid_bullets.clear()
        self._raid_wave_timer = RAID_INTERVAL_SEC
        self._cheat_msg = f"Рейд отбит! +{r} монет"
        self._save_game()

        # Останавливаем музыку рейда.
        if self._mixer_ok and self._raid_music is not None:
            try:
                self._raid_music.stop()
            except Exception:
                pass

    def _end_raid_lose(self) -> None:
        p = self._raid_lose_penalty()
        self._raid_take_penalty(p)
        self.raid_active = False
        self.raid_redhats.clear()
        self.raid_bullets.clear()
        self._raid_wave_timer = RAID_INTERVAL_SEC
        self._cheat_msg = f"Рейд прошёл! Штраф {p} (долг: {self.money_debt})"
        self._save_game()

        # Останавливаем музыку рейда.
        if self._mixer_ok and self._raid_music is not None:
            try:
                self._raid_music.stop()
            except Exception:
                pass

    def _random_raid_redhat_level(self, wave_idx: int) -> int:
        # Распределение уровней по волнам (3 уровня + босс отдельным спавном).
        if wave_idx <= 0:
            p1, p2, p3 = 0.60, 0.28, 0.12
        elif wave_idx == 1:
            p1, p2, p3 = 0.45, 0.35, 0.20
        else:
            p1, p2, p3 = 0.30, 0.35, 0.35
        r = random.random()
        if r < p1:
            return 1
        if r < p1 + p2:
            return 2
        return 3

    def _spawn_raid_redhat(self, *, level: int, is_boss: bool) -> None:
        base_w, base_h = self._redhat_surf.get_width(), self._redhat_surf.get_height()
        level_scales = {1: 1.0, 2: 1.15, 3: 1.35, 4: 2.4}
        sm = level_scales.get(level, 1.0)
        rw = max(1, int(base_w * sm))
        rh = max(1, int(base_h * sm))
        y = random.uniform(float(self.PANEL_H + rh), float(self.BELT_Y - 60))

        if level <= 3:
            hp = level
            damage = level  # 1..3
            speed_mul = max(0.6, 1.0 - (level - 1) * 0.08)
        else:
            hp = 30
            damage = 4
            speed_mul = 0.28

        hit_radius = RAID_HIT_RADIUS + (12 if is_boss else level * 3)
        rid = self._raid_redhat_id_seq
        self._raid_redhat_id_seq += 1
        self.raid_redhats.append(
            RaidRedhat(
                x=float(GAME_W + rw + 10),
                y=y,
                id=rid,
                level=level,
                hp=hp,
                damage=damage,
                speed_mul=speed_mul,
                is_boss=is_boss,
                hit_radius=int(hit_radius),
                scale_mul=sm,
            )
        )

    def _update_raid(self, dt: float) -> None:
        if not self.raid_active:
            return

        if self._raid_laser_t > 0.0:
            self._raid_laser_t = max(0.0, self._raid_laser_t - dt)

        if self._raid_weapon_cd > 0:
            self._raid_weapon_cd = max(0.0, self._raid_weapon_cd - dt)

        if self._raid_banner_sec > 0:
            self._raid_banner_sec -= dt
        if self._raid_game_msg_sec > 0:
            self._raid_game_msg_sec -= dt

        # Спавн волн
        if self.raid_wave_idx < RAID_WAVES:
            if self.raid_wave_pause_timer > 0:
                self.raid_wave_pause_timer = max(0.0, self.raid_wave_pause_timer - dt)
            else:
                self.raid_spawn_timer -= dt
                while (
                    self.raid_wave_spawned < self.raid_wave_target and self.raid_spawn_timer <= 0
                ):
                    if (
                        self.raid_wave_idx == RAID_WAVES - 1
                        and self.raid_wave_spawned == 0
                    ):
                        self._spawn_raid_redhat(level=4, is_boss=True)
                    else:
                        lvl = self._random_raid_redhat_level(self.raid_wave_idx)
                        self._spawn_raid_redhat(level=lvl, is_boss=False)

                    self.raid_spawned += 1
                    self.raid_wave_spawned += 1
                    self.raid_spawn_timer += RAID_SPAWN_INTERVAL

                if self.raid_wave_spawned >= self.raid_wave_target > 0:
                    next_wave = self.raid_wave_idx + 1
                    if next_wave < RAID_WAVES:
                        self.raid_wave_idx = next_wave
                        self.raid_wave_target = RAID_WAVE_TARGETS[self.raid_wave_idx]
                        self.raid_wave_spawned = 0
                        self.raid_wave_pause_timer = RAID_WAVE_PAUSE_SEC
                        self.raid_spawn_timer = 0.2
                        self._raid_banner_sec = 2.5
                    else:
                        # Все волны созданы, дальше только бой.
                        self.raid_wave_idx = RAID_WAVES
                        self.raid_wave_target = 0
                        self.raid_wave_spawned = 0

        tx, ty = self.thrower_x, self.thrower_y
        # Движение красношляпников
        for r in self.raid_redhats:
            if not r.alive:
                continue
            dx, dy = tx - r.x, ty - r.y
            dist = max(1.0, math.hypot(dx, dy))
            sp = RAID_ENEMY_SPEED * dt * r.speed_mul
            r.x += dx / dist * sp
            r.y += dy / dist * sp
            if math.hypot(r.x - tx, r.y - ty) < r.hit_radius:
                r.alive = False
                self.raid_shef_hp -= r.damage
                self._play_tap()

        if self.raid_shef_hp <= 0:
            self._end_raid_lose()
            return

        def _explode(cx: float, cy: float, dmg: int, radius: float) -> None:
            # АОЕ по всем живым красношляпникам.
            for rr in self.raid_redhats:
                if not rr.alive:
                    continue
                if math.hypot(rr.x - cx, rr.y - cy) <= radius:
                    rr.hp -= dmg
                    if rr.hp <= 0:
                        rr.alive = False

        # Пули
        next_bullets: List[RaidBullet] = []
        for b in self.raid_bullets:
            b.life -= dt
            if b.life <= 0:
                if b.kind == "bomb" and b.aoe_radius > 0:
                    _explode(b.x, b.y, b.damage, b.aoe_radius)
                continue

            b.x += b.vx * dt
            b.y += b.vy * dt

            # Ищем ближайшее попадание (в пределах "радиуса столкновения")
            hit_r: Optional[RaidRedhat] = None
            best_d = 1e18
            for r in self.raid_redhats:
                if not r.alive:
                    continue
                thr = max(RAID_BULLET_RADIUS, int(r.hit_radius * 0.45))
                d = math.hypot(b.x - r.x, b.y - r.y)
                if d < thr and d < best_d:
                    best_d = d
                    hit_r = r

            if hit_r is None:
                next_bullets.append(b)
                continue

            if b.kind in ("basic", "spread"):
                hit_r.hp -= b.damage
                if hit_r.hp <= 0:
                    hit_r.alive = False
                continue

            if b.kind == "bomb":
                if b.aoe_radius > 0:
                    _explode(b.x, b.y, b.damage, b.aoe_radius)
                continue

            if b.kind == "chain":
                hit_r.hp -= b.damage
                if hit_r.hp <= 0:
                    hit_r.alive = False

                if b.chain_left > 0:
                    b.chain_left -= 1
                    # Находим следующий ближайший таргет (не тот, который только что был)
                    spd_mag = math.hypot(b.vx, b.vy)
                    next_t: Optional[RaidRedhat] = None
                    best_next = 1e18
                    for rr in self.raid_redhats:
                        if not rr.alive or rr.id == hit_r.id:
                            continue
                        d2 = math.hypot(rr.x - b.x, rr.y - b.y)
                        if d2 < best_next:
                            best_next = d2
                            next_t = rr
                    b.last_hit_id = hit_r.id
                    if next_t is None or spd_mag <= 1e-6:
                        continue
                    ndx = next_t.x - b.x
                    ndy = next_t.y - b.y
                    dist = max(1.0, math.hypot(ndx, ndy))
                    b.vx = ndx / dist * spd_mag
                    b.vy = ndy / dist * spd_mag
                    b.life = max(b.life, 0.6)
                    next_bullets.append(b)
                continue

            # неизвестный kind
            continue

        self.raid_bullets = next_bullets

        # Победа, когда все волны закончились и никого не осталось.
        if self.raid_wave_idx >= RAID_WAVES and not any(r.alive for r in self.raid_redhats):
            self._end_raid_win()

    def _set_raid_laser_visual(self, target: Tuple[int, int]) -> None:
        # Лазер 5: показываем луч от шефосера к указателю.
        self._raid_laser_start = (float(self.thrower_x), float(self.thrower_y))
        self._raid_laser_end = (float(target[0]), float(target[1]))
        self._raid_laser_t = 0.12

    def _raid_try_shoot(self, target: Tuple[int, int]) -> None:
        if not self.raid_active or self.current_floor != 0:
            return
        wi = self.equipped_weapon
        if wi < 0 or wi >= len(WEAPON_DEFS) or not self.weapons_owned[wi]:
            return
        _, _, cd, spd, power = WEAPON_DEFS[wi]
        if self._raid_weapon_cd > 0:
            return
        self._raid_weapon_cd = cd
        tx, ty = float(target[0]), float(target[1])
        sx, sy = self.thrower_x, self.thrower_y
        dx, dy = tx - sx, ty - sy
        dist = max(1.0, math.hypot(dx, dy))
        ndx, ndy = dx / dist, dy / dist

        # Каждый ранг оружия => отдельная "атака"
        # 0: базовый снаряд
        # 1: бомба АОЕ
        # 2: spread (три снаряда)
        # 3: chain (цепь: несколько попаданий)
        # 4: лазер по линии (без пуль, невидимый, ваншот)
        if wi == 4:
            # Лазер: невидимый луч. Ваншот всех, кто рядом с сегментом thrower->target.
            self._set_raid_laser_visual(target)
            dx2, dy2 = tx - sx, ty - sy
            seg_len_sq = max(1.0, dx2 * dx2 + dy2 * dy2)
            for r in self.raid_redhats:
                if not r.alive:
                    continue
                px, py = r.x - sx, r.y - sy
                proj = (px * dx2 + py * dy2) / seg_len_sq
                if proj < 0.0 or proj > 1.0:
                    continue
                cx, cy = sx + proj * dx2, sy + proj * dy2
                d = math.hypot(r.x - cx, r.y - cy)
                thr = 18.0 + r.level * 6.0
                if d <= thr:
                    r.hp = 0
                    r.alive = False
            return

        if wi == 0:
            self.raid_bullets.append(
                RaidBullet(
                    x=sx,
                    y=sy,
                    vx=ndx * spd,
                    vy=ndy * spd,
                    damage=power,
                    kind="basic",
                    life=2.2,
                )
            )
        elif wi == 2:
            # Тройной разлёт
            for ang in (-0.22, 0.0, 0.22):
                ca, sa = math.cos(ang), math.sin(ang)
                rx = ndx * ca - ndy * sa
                ry = ndx * sa + ndy * ca
                self.raid_bullets.append(
                    RaidBullet(
                        x=sx,
                        y=sy,
                        vx=rx * spd,
                        vy=ry * spd,
                        damage=power,
                        kind="spread",
                        life=2.0,
                    )
                )
        elif wi == 1:
            # Бомба 2 тир: взрыв АОЕ по попаданию
            self.raid_bullets.append(
                RaidBullet(
                    x=sx,
                    y=sy,
                    vx=ndx * spd,
                    vy=ndy * spd,
                    damage=power,
                    kind="bomb",
                    aoe_radius=85.0,  # чуть слабее по радиусу
                    life=1.2,
                )
            )
        elif wi == 3:
            # Цепь 4 тир: после попадания прыгает к следующему таргету
            self.raid_bullets.append(
                RaidBullet(
                    x=sx,
                    y=sy,
                    vx=ndx * spd,
                    vy=ndy * spd,
                    damage=power,
                    kind="chain",
                    chain_left=4,
                    life=2.8,
                    last_hit_id=-1,
                )
            )
        else:
            # fallback
            self.raid_bullets.append(
                RaidBullet(
                    x=sx,
                    y=sy,
                    vx=ndx * spd,
                    vy=ndy * spd,
                    damage=power,
                    kind="basic",
                    life=2.2,
                )
            )

        self._play_tap()

    def _apply_save_payload(self, raw: Dict[str, Any], ver: int) -> None:
        self.money = max(0, int(raw.get("money", 0)))
        self.cheat_unlocked = bool(raw.get("cheat_unlocked", False))
        self.cheat_active = bool(raw.get("cheat_active", False))
        ml = int(raw.get("manual_level", 1))
        if self.cheat_unlocked:
            self.manual_level = max(1, min(999_999_999, ml))
        else:
            self.manual_level = max(1, min(MAX_MANUAL_LEVEL, ml))
        self._apply_theme_name(str(raw.get("theme", "dark")))
        b = float(raw.get("belt_speed_mul", 1.0))
        if self.cheat_unlocked:
            self.belt_speed_mul = max(0.0001, min(1_000_000.0, b))
        else:
            self.belt_speed_mul = max(0.5, min(2.0, b))
        self.cheat_time_scale = 1.0
        self.cheat_free_shop = False
        self.cheat_belt_frozen = False
        self.cheat_max_autos_override = 32
        if self.cheat_unlocked:
            self.cheat_time_scale = max(0.0, min(10_000.0, float(raw.get("cheat_time_scale", 1.0))))
            self.cheat_free_shop = bool(raw.get("cheat_free_shop", False))
            self.cheat_belt_frozen = bool(raw.get("cheat_belt_frozen", False))
            self.cheat_max_autos_override = max(
                1, min(99999, int(raw.get("cheat_max_autos_override", 32)))
            )
        self.autos_by_floor = []
        if raw.get("autos_by_floor"):
            for floor_autos in raw.get("autos_by_floor", []):
                fl: List[AutoShefoser] = []
                for j, ad in enumerate(floor_autos):
                    ax = float(ad["x"])
                    ay = float(ad["y"])
                    if ver < 3:
                        ax *= 2
                        ay *= 2
                    fl.append(
                        AutoShefoser(
                            x=ax,
                            y=ay,
                            level=max(1, int(ad.get("level", 1))),
                            cooldown=0.5 + 0.1 * j,
                        )
                    )
                self.autos_by_floor.append(fl)
        else:
            self.autos_by_floor = [[]]
            for i, ad in enumerate(raw.get("autos", [])):
                ax = float(ad["x"])
                ay = float(ad["y"])
                if ver < 3:
                    ax *= 2
                    ay *= 2
                self.autos_by_floor[0].append(
                    AutoShefoser(
                        x=ax,
                        y=ay,
                        level=max(1, int(ad.get("level", 1))),
                        cooldown=0.5 + 0.1 * i,
                    )
                )
        fo = int(raw.get("floors_owned", len(self.autos_by_floor) or 1))
        self.floors_owned = max(1, min(MAX_FLOORS, max(fo, len(self.autos_by_floor))))
        self._ensure_floor_lists()
        self.current_floor = max(
            0,
            min(self.floors_owned - 1, int(raw.get("current_floor", 0))),
        )
        tab = str(raw.get("shop_tab", SHOP_TAB_EARN))
        self.shop_tab = tab if tab in (SHOP_TAB_EARN, SHOP_TAB_BASE, SHOP_TAB_WEAPONS) else SHOP_TAB_EARN
        wo = raw.get("weapons_owned")
        if isinstance(wo, list) and len(wo) == len(WEAPON_DEFS):
            self.weapons_owned = [bool(x) for x in wo]
        else:
            self.weapons_owned = [False] * len(WEAPON_DEFS)
        eq = int(raw.get("equipped_weapon", -1))
        self.equipped_weapon = eq if -1 <= eq < len(WEAPON_DEFS) else -1
        if self.equipped_weapon >= 0 and not self.weapons_owned[self.equipped_weapon]:
            self.equipped_weapon = -1
        self.money_debt = max(0, int(raw.get("money_debt", 0)))

    def _load_game(self) -> None:
        if os.path.isfile(SAVE_PATH):
            try:
                with open(SAVE_PATH, "rb") as f:
                    plain = _fernet_cipher().decrypt(f.read())
                raw = json.loads(plain.decode("utf-8"))
                ver = int(raw.get("version", 5))
                self._apply_save_payload(raw, ver)
                return
            except (OSError, ValueError, json.JSONDecodeError, Exception):
                pass
        if os.path.isfile(LEGACY_SAVE_JSON):
            try:
                with open(LEGACY_SAVE_JSON, encoding="utf-8") as f:
                    raw = json.load(f)
                ver = int(raw.get("version", 1))
                self._apply_save_payload(raw, ver)
                self._save_game()
                try:
                    os.remove(LEGACY_SAVE_JSON)
                except OSError:
                    pass
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass

    def _save_game(self) -> None:
        autos_payload: List[List[Dict[str, Any]]] = []
        for fl in self.autos_by_floor:
            row: List[Dict[str, Any]] = []
            for a in fl:
                row.append({"x": a.x, "y": a.y, "level": a.level})
            autos_payload.append(row)
        payload = {
            "version": 9,
            "money": self.money,
            "manual_level": self.manual_level,
            "theme": self.theme,
            "belt_speed_mul": self.belt_speed_mul,
            "cheat_unlocked": self.cheat_unlocked,
            "cheat_active": self.cheat_active,
            "cheat_time_scale": self.cheat_time_scale,
            "cheat_free_shop": self.cheat_free_shop,
            "cheat_belt_frozen": self.cheat_belt_frozen,
            "cheat_max_autos_override": self.cheat_max_autos_override,
            "floors_owned": self.floors_owned,
            "current_floor": self.current_floor,
            "autos_by_floor": autos_payload,
            "shop_tab": self.shop_tab,
            "weapons_owned": self.weapons_owned,
            "equipped_weapon": self.equipped_weapon,
            "money_debt": self.money_debt,
        }
        try:
            blob = _fernet_cipher().encrypt(json.dumps(payload).encode("utf-8"))
            tmp = SAVE_PATH + ".tmp"
            with open(tmp, "wb") as f:
                f.write(blob)
            os.replace(tmp, SAVE_PATH)
        except OSError:
            pass

    def manual_skin_slot(self) -> int:
        return self._skin_slot_for_level(self.manual_level)

    def coins_per_manual_item(self) -> int:
        lv = self.manual_level
        base = 1 + int(lv * 0.42) + (lv // 6) * 2 + (lv // 15)
        return max(1, min(base, 120))

    def coins_per_auto_item(self, level: int) -> int:
        lv = level
        base = 1 + int(lv * 0.38) + (lv // 7) * 2
        return max(1, min(base, 100))

    def upgrade_manual_cost(self) -> int:
        if self.manual_level >= MAX_MANUAL_LEVEL:
            return 0
        lv = self.manual_level
        return int(72 * (1.46 ** (lv - 1)) + (lv // 7) * 32)

    def buy_auto_cost(self) -> int:
        n = self._total_auto_count()
        all_a = self._all_autos_flat()
        avg = sum(a.level for a in all_a) / max(1, len(all_a))
        return int((165 + n * 28) * (1.44 ** n) * (1.0 + 0.055 * avg))

    def upgrade_auto_cost(self, level: int) -> int:
        return int(62 * (1.42 ** (level - 1)) + (level // 4) * 18)

    def _trim_clicks(self, now: float) -> None:
        while self._click_times and self._click_times[0] < now - 1.0:
            self._click_times.popleft()

    def _register_manual_click(self, now: float) -> bool:
        self._trim_clicks(now)
        self._click_times.append(now)
        self._trim_clicks(now)
        if self.cheat_unlocked and self.cheat_active:
            return True
        if len(self._click_times) > CLICKS_PER_SEC_LIMIT:
            show_autoclicker_alert_and_exit()
            return False
        return True

    def _spawn_projectile(
        self, sx: float, sy: float, coin_value: int, skin_slot: int
    ) -> None:
        dx = self.landing_x - sx
        dy = self.landing_y - sy
        dist = max(1.0, math.hypot(dx, dy))
        speed = 840.0
        slot = skin_slot % self._n_skins
        self.items.append(
            ItemOnBelt(
                x=sx,
                y=sy,
                phase="air",
                vx=dx / dist * speed,
                vy=dy / dist * speed,
                coin_value=coin_value,
                skin_slot=slot,
            )
        )

    def try_throw_manual(self, pos: Tuple[int, int]) -> None:
        if self.raid_active:
            return
        if self.current_floor != 0:
            return
        if not self._thrower_rect.collidepoint(pos):
            return
        now = pygame.time.get_ticks() / 1000.0
        if not self._register_manual_click(now):
            return
        self._spawn_projectile(
            self.thrower_x,
            self.thrower_y,
            self.coins_per_manual_item(),
            self.manual_skin_slot(),
        )

    def _auto_hit_rect(self, auto: AutoShefoser) -> pygame.Rect:
        return pygame.Rect(
            int(auto.x - THROWER_HALF - 2),
            int(auto.y - THROWER_HALF - 2),
            (THROWER_HALF + 2) * 2,
            (THROWER_HALF + 2) * 2,
        )

    def try_click_autos(self, pos: Tuple[int, int]) -> bool:
        """Выбор авто для апгрейда. True если клик по полю обработан."""
        fl = self._autos_on_current_floor()
        for i in range(len(fl) - 1, -1, -1):
            if self._auto_hit_rect(fl[i]).collidepoint(pos):
                self.selected_auto = (self.current_floor, i)
                return True
        self.selected_auto = None
        return False

    def _shop_click(self, pos: Tuple[int, int]) -> bool:
        if pos[0] < GAME_W:
            return False
        if self._btn_settings and self._btn_settings.collidepoint(pos):
            self.settings_open = True
            self._password_buf = ""
            self._cheat_msg = ""
            self._settings_focus_field = None
            self._pw_show = False
            self._sync_cheat_fields_from_game()
            self._play_tap()
            return True
        if self._btn_tab_earn and self._btn_tab_earn.collidepoint(pos):
            self.shop_tab = SHOP_TAB_EARN
            self._play_tap()
            self._save_game()
            return True
        if self._btn_tab_base and self._btn_tab_base.collidepoint(pos):
            self.shop_tab = SHOP_TAB_BASE
            self._play_tap()
            self._save_game()
            return True
        if self._btn_tab_weapons and self._btn_tab_weapons.collidepoint(pos):
            self.shop_tab = SHOP_TAB_WEAPONS
            self._play_tap()
            self._save_game()
            return True

        if self.shop_tab == SHOP_TAB_EARN:
            if self._btn_manual and self._btn_manual.collidepoint(pos):
                if self.manual_level >= MAX_MANUAL_LEVEL:
                    return True
                cost = self.upgrade_manual_cost()
                free = self._shop_free()
                if free or self.money >= cost:
                    if not free:
                        self.money -= cost
                    self.manual_level += 1
                    self._play_upgrade_sound(self.manual_level)
                    self._save_game()
                return True
            if self._btn_auto_buy and self._btn_auto_buy.collidepoint(pos):
                if self.placement_pending:
                    return True
                if len(self._autos_on_current_floor()) >= self._max_autos_this_floor():
                    return True
                cost = self.buy_auto_cost()
                free = self._shop_free()
                if free or self.money >= cost:
                    if not free:
                        self.money -= cost
                        self._placement_paid = cost
                    self.placement_pending = True
                    self._play_tap()
                    self._save_game()
                return True
            if self._btn_auto_upgrade and self._btn_auto_upgrade.collidepoint(pos):
                if self.selected_auto is None:
                    return True
                fi, ai = self.selected_auto
                if fi != self.current_floor:
                    return True
                if not (0 <= fi < len(self.autos_by_floor) and 0 <= ai < len(self.autos_by_floor[fi])):
                    return True
                auto = self.autos_by_floor[fi][ai]
                cost = self.upgrade_auto_cost(auto.level)
                free = self._shop_free()
                if free or self.money >= cost:
                    if not free:
                        self.money -= cost
                    auto.level += 1
                    self._play_upgrade_sound(auto.level)
                    self._save_game()
                return True
        elif self.shop_tab == SHOP_TAB_BASE:
            if self._btn_floor_buy and self._btn_floor_buy.collidepoint(pos):
                if self.floors_owned >= MAX_FLOORS:
                    return True
                c = self.buy_floor_cost()
                free = self._shop_free()
                if free or self.money >= c:
                    if not free:
                        self.money -= c
                    self.floors_owned += 1
                    self.autos_by_floor.append([])
                    self._ensure_floor_lists()
                    self._play_tap()
                    self._save_game()
                return True
        elif self.shop_tab == SHOP_TAB_WEAPONS:
            for i, r in enumerate(self._btn_weapon_rows):
                if r is not None and r.collidepoint(pos):
                    _, cost, _, _, _power = WEAPON_DEFS[i]
                    free = self._shop_free()
                    if not self.weapons_owned[i]:
                        if free or self.money >= cost:
                            if not free:
                                self.money -= cost
                            self.weapons_owned[i] = True
                            self.equipped_weapon = i
                            self._play_tap()
                            self._save_game()
                    else:
                        self.equipped_weapon = i
                        self._play_tap()
                        self._save_game()
                    return True
        return True

    def try_place_auto(self, pos: Tuple[int, int]) -> None:
        if self.raid_active:
            return
        if not self.placement_pending:
            return
        fl = self.autos_by_floor[self.current_floor]
        if len(fl) >= self._max_autos_this_floor():
            self._cancel_placement()
            return
        x, y = float(pos[0]), float(pos[1])
        x = max(THROWER_HALF + 16, min(GAME_W - THROWER_HALF - 16, x))
        y = max(160.0, min(self.BELT_Y - 80.0, y))
        fl.append(AutoShefoser(x=x, y=y, level=1, cooldown=0.55))
        self.placement_pending = False
        self._placement_paid = 0
        self.selected_auto = (self.current_floor, len(fl) - 1)
        self._play_sound_for_tier(1)
        self._save_game()

    def update(self, dt: float) -> None:
        spd = self.belt_speed()
        if self.cheat_unlocked and self.cheat_belt_frozen:
            spd = 0.0
        self.belt_scroll = (self.belt_scroll + spd * dt) % 80

        for fl in self.autos_by_floor:
            for auto in fl:
                auto.cooldown -= dt
                if auto.cooldown <= 0:
                    cd = max(0.32, 2.25 / float(auto.level) ** 0.92)
                    auto.cooldown = cd
                    self._spawn_projectile(
                        auto.x,
                        auto.y,
                        self.coins_per_auto_item(auto.level),
                        self._skin_slot_for_level(auto.level),
                    )

        g = 1800.0
        belt_top = self.BELT_Y
        sh = self._skin_surfs[0].get_height()
        next_items: List[ItemOnBelt] = []
        for it in self.items:
            if it.phase == "air":
                it.vy += g * dt
                it.x += it.vx * dt
                it.y += it.vy * dt
                if it.y + sh // 2 >= belt_top:
                    it.phase = "belt"
                    it.y = belt_top - sh // 2
                    it.vx = spd
                    it.vy = 0.0
                next_items.append(it)
            else:
                it.x += spd * dt
                if it.x < self.W + 160:
                    next_items.append(it)
                else:
                    self._earn_coins(it.coin_value)
        self.items = next_items

        self._update_raid(dt)
        if not self.settings_open and not self.raid_active:
            self._raid_wave_timer -= dt
            if self._raid_wave_timer <= 0:
                self._raid_wave_timer = RAID_INTERVAL_SEC
                # Рейд включается только если денег хватает на оружие 2-го ранга.
                # (индекс 1 в WEAPON_DEFS)
                if (
                    self.current_floor == 0
                    and random.random() < RAID_CHANCE
                    and len(WEAPON_DEFS) > 1
                    and self.money >= WEAPON_DEFS[1][1]
                ):
                    self._start_raid()

        self._save_timer += dt
        if self._save_timer >= 2.0:
            self._save_timer = 0.0
            self._save_game()

    def draw_conveyor(self) -> None:
        r = pygame.Rect(0, self.BELT_Y, self.W, self.BELT_H)
        pygame.draw.rect(self._canvas, self._c("belt"), r)
        pygame.draw.rect(self._canvas, self._c("belt_edge"), r, 2)
        y = self.BELT_Y + self.BELT_H // 2
        scroll = int(self.belt_scroll) % 80
        for i in range(-1, self.W // 40 + 2):
            bx = i * 80 + scroll
            pygame.draw.line(self._canvas, self._c("belt_mark"), (bx, y - 24), (bx + 40, y + 24), 6)

    def draw_shop(self) -> None:
        panel = pygame.Rect(GAME_W, 0, self.W - GAME_W, self.H)
        pygame.draw.rect(self._canvas, self._c("shop"), panel)
        pygame.draw.line(self._canvas, self._c("shop_line"), (GAME_W, 0), (GAME_W, self.H), 2)

        x0 = GAME_W + 20
        y = 16
        title_x = x0
        if self._moon_icon:
            self._canvas.blit(self._moon_icon, (x0, y))
            title_x = x0 + self._moon_icon.get_width() + 16
        self._canvas.blit(self.font_title.render("Магазин", True, self._c("text")), (title_x, y + 4))
        self._btn_settings = pygame.Rect(x0 + 360, y, 200, 56)
        pygame.draw.rect(self._canvas, self._c("btn"), self._btn_settings, border_radius=10)
        pygame.draw.rect(self._canvas, self._c("btn_hi"), self._btn_settings, 2, border_radius=10)
        self._canvas.blit(
            self.font_small.render("Настройки", True, self._c("text")),
            (x0 + 388, y + 12),
        )
        y += 64

        self._btn_manual = None
        self._btn_auto_buy = None
        self._btn_auto_upgrade = None
        self._btn_floor_buy = None
        for i in range(len(self._btn_weapon_rows)):
            self._btn_weapon_rows[i] = None

        tab_w = 154
        gap_t = 8
        tab_specs = [
            (SHOP_TAB_EARN, "Заработок"),
            (SHOP_TAB_BASE, "База"),
            (SHOP_TAB_WEAPONS, "Оружие"),
        ]
        self._btn_tab_earn = pygame.Rect(x0, y, tab_w, 42)
        self._btn_tab_base = pygame.Rect(x0 + tab_w + gap_t, y, tab_w, 42)
        self._btn_tab_weapons = pygame.Rect(x0 + 2 * (tab_w + gap_t), y, tab_w, 42)
        tab_rects = [self._btn_tab_earn, self._btn_tab_base, self._btn_tab_weapons]
        for rect, (tid, label) in zip(tab_rects, tab_specs):
            sel = self.shop_tab == tid
            pygame.draw.rect(
                self._canvas,
                self._c("accent") if sel else self._c("btn"),
                rect,
                border_radius=10,
            )
            pygame.draw.rect(self._canvas, self._c("btn_hi"), rect, 2, border_radius=10)
            tc = self._c("text") if sel else self._c("text_dim")
            t = self.font_small.render(label, True, tc)
            self._canvas.blit(
                t,
                (rect.centerx - t.get_width() // 2, rect.centery - t.get_height() // 2),
            )
        y += 50

        if self.shop_tab == SHOP_TAB_EARN:
            cost_m = self.upgrade_manual_cost()
            maxed = self.manual_level >= MAX_MANUAL_LEVEL and not self.cheat_unlocked
            self._btn_manual = pygame.Rect(x0, y, 500, 72)
            col = self._c("btn_dis") if maxed else self._c("btn")
            pygame.draw.rect(self._canvas, col, self._btn_manual, border_radius=12)
            pygame.draw.rect(self._canvas, self._c("btn_hi"), self._btn_manual, 2, border_radius=12)
            t1 = self.font_btn.render("Улучшить шефосер", True, self._c("text"))
            sub = f"МАКС" if maxed else f"{cost_m} монет · доход/ур."
            t2 = self.font_small.render(sub, True, self._c("text_dim"))
            self._canvas.blit(t1, (x0 + 16, y + 6))
            self._canvas.blit(t2, (x0 + 16, y + 38))
            y += 84

            cost_a = self.buy_auto_cost()
            n_here = len(self._autos_on_current_floor())
            cap_here = self._max_autos_this_floor()
            self._btn_auto_buy = pygame.Rect(x0, y, 500, 72)
            col = self._c("btn") if n_here < cap_here else self._c("btn_dis")
            pygame.draw.rect(self._canvas, col, self._btn_auto_buy, border_radius=12)
            pygame.draw.rect(self._canvas, self._c("btn_hi"), self._btn_auto_buy, 2, border_radius=12)
            t1 = self.font_btn.render("Купить авто-шефосер", True, self._c("text"))
            sub = (
                f"{cost_a} · этаж {self.current_floor + 1} · {n_here}/{cap_here}"
                if n_here < cap_here
                else f"Этаж полон ({cap_here}/{cap_here})"
            )
            t2 = self.font_small.render(sub, True, self._c("text_dim"))
            self._canvas.blit(t1, (x0 + 16, y + 6))
            self._canvas.blit(t2, (x0 + 16, y + 38))
            y += 84

            ucost = 0
            dis = True
            if self.selected_auto is not None:
                fi, ai = self.selected_auto
                if (
                    fi == self.current_floor
                    and 0 <= fi < len(self.autos_by_floor)
                    and 0 <= ai < len(self.autos_by_floor[fi])
                ):
                    dis = False
                    ucost = self.upgrade_auto_cost(self.autos_by_floor[fi][ai].level)
            self._btn_auto_upgrade = pygame.Rect(x0, y, 500, 72)
            col = self._c("btn_dis") if dis else self._c("btn")
            pygame.draw.rect(self._canvas, col, self._btn_auto_upgrade, border_radius=12)
            pygame.draw.rect(self._canvas, self._c("btn_hi"), self._btn_auto_upgrade, 2, border_radius=12)
            if dis:
                t1 = self.font_btn.render("Улучшить выбранный авто", True, self._c("text_dim"))
                t2 = self.font_small.render("Клик по авто на этом этаже", True, self._c("text_muted"))
            else:
                _, ai = self.selected_auto or (0, 0)
                n = ai + 1
                t1 = self.font_btn.render(f"Улучшить авто №{n} (этаж)", True, self._c("text"))
                t2 = self.font_small.render(f"{ucost} монет · скорость/доход", True, self._c("text_dim"))
            self._canvas.blit(t1, (x0 + 16, y + 6))
            self._canvas.blit(t2, (x0 + 16, y + 38))
            y += 88

            if self.placement_pending:
                hint = self.font_small.render(
                    "Клик на поле — место. Esc — отмена.",
                    True,
                    self._c("accent"),
                )
                self._canvas.blit(hint, (x0, y))

        elif self.shop_tab == SHOP_TAB_BASE:
            fc = self.buy_floor_cost()
            self._btn_floor_buy = pygame.Rect(x0, y, 500, 72)
            can_buy_floor = self.floors_owned < MAX_FLOORS
            col = self._c("btn") if can_buy_floor else self._c("btn_dis")
            pygame.draw.rect(self._canvas, col, self._btn_floor_buy, border_radius=12)
            pygame.draw.rect(self._canvas, self._c("btn_hi"), self._btn_floor_buy, 2, border_radius=12)
            t1 = self.font_btn.render("Купить этаж", True, self._c("text"))
            sub2 = (
                f"{fc} монет · этажей {self.floors_owned}/{MAX_FLOORS}"
                if can_buy_floor
                else "Все этажи куплены"
            )
            t2f = self.font_small.render(sub2, True, self._c("text_dim"))
            self._canvas.blit(t1, (x0 + 16, y + 6))
            self._canvas.blit(t2f, (x0 + 16, y + 38))
            y += 88
            self._canvas.blit(
                self.font_tiny.render("На этаж до 8 авто-шефосеров. Ручной — только 1-й этаж.", True, self._c("text_muted")),
                (x0, y),
            )

        elif self.shop_tab == SHOP_TAB_WEAPONS:
            self._canvas.blit(
                self.font_small.render("Кибер-оружие против рейдов Red Hat", True, self._c("text_dim")),
                (x0, y),
            )
            y += 30
            for i, (wname, cost, cd, _spd, power) in enumerate(WEAPON_DEFS):
                owned = self.weapons_owned[i]
                eq = self.equipped_weapon == i
                r = pygame.Rect(x0, y, 500, 64)
                self._btn_weapon_rows[i] = r
                col = self._c("accent") if eq else self._c("btn")
                pygame.draw.rect(self._canvas, col, r, border_radius=10)
                pygame.draw.rect(self._canvas, self._c("btn_hi"), r, 2, border_radius=10)
                line1 = f"{i + 1}. {wname}"
                if not owned:
                    atk = WEAPON_ATTACK_LABELS[i] if i < len(WEAPON_ATTACK_LABELS) else "атака"
                    line2 = f"Купить — {cost} мон · перезарядка {cd:.2f} с · {atk} · сила {power}"
                else:
                    atk = WEAPON_ATTACK_LABELS[i] if i < len(WEAPON_ATTACK_LABELS) else "атака"
                    if not eq:
                        line2 = f"Взять в руки (клик)  [{atk}]"
                    else:
                        line2 = f"Взять в руки (клик)  [{atk}]  [экипировано]"
                self._canvas.blit(self.font_tiny.render(line1, True, self._c("text")), (x0 + 12, y + 10))
                self._canvas.blit(self.font_tiny.render(line2, True, self._c("text_dim")), (x0 + 12, y + 34))
                y += 72
            self._canvas.blit(
                self.font_tiny.render(
                    "Во время рейда: клик по полю игры — выстрел с позиции ручного шефосера.",
                    True,
                    self._c("accent"),
                ),
                (x0, y + 4),
            )

    def draw_gui(self) -> None:
        ph = self.PANEL_H
        panel = pygame.Rect(0, 0, GAME_W, ph)
        pygame.draw.rect(self._canvas, self._c("panel"), panel)
        pygame.draw.line(self._canvas, self._c("panel_line"), (0, ph), (GAME_W, ph), 2)
        pad_x = 28
        self._canvas.blit(
            self.font_title.render("Shefos Tycoon", True, self._c("text")),
            (pad_x, 12),
        )

        fh, fw = 34, 40
        gap = 8
        t_floor = self.font_small.render(
            f"Этаж {self.current_floor + 1}/{self.floors_owned}",
            True,
            self._c("text"),
        )
        tw = t_floor.get_width()
        total_w = fw + gap + tw + gap + fw
        x_left = GAME_W - 16 - total_w
        floor_y = 10
        self._btn_floor_prev = pygame.Rect(x_left, floor_y, fw, fh)
        self._btn_floor_next = pygame.Rect(x_left + fw + gap + tw + gap, floor_y, fw, fh)
        can_prev = self.current_floor > 0 and not self.raid_active
        can_next = self.current_floor < self.floors_owned - 1 and not self.raid_active
        pygame.draw.rect(
            self._canvas,
            self._c("btn") if can_prev else self._c("btn_dis"),
            self._btn_floor_prev,
            border_radius=10,
        )
        pygame.draw.rect(self._canvas, self._c("btn_hi"), self._btn_floor_prev, 2, border_radius=10)
        pygame.draw.rect(
            self._canvas,
            self._c("btn") if can_next else self._c("btn_dis"),
            self._btn_floor_next,
            border_radius=10,
        )
        pygame.draw.rect(self._canvas, self._c("btn_hi"), self._btn_floor_next, 2, border_radius=10)
        self._canvas.blit(
            t_floor,
            (
                x_left + fw + gap,
                floor_y + (fh - t_floor.get_height()) // 2,
            ),
        )
        cl = self._c("text") if can_prev else self._c("text_muted")
        cr = self._c("text") if can_next else self._c("text_muted")
        _draw_icon_chevron_left(
            self._canvas, self._btn_floor_prev.centerx, self._btn_floor_prev.centery, cl
        )
        _draw_icon_chevron_right(
            self._canvas, self._btn_floor_next.centerx, self._btn_floor_next.centery, cr
        )

        money_y = 52
        money_txt = self.font.render(str(self.money), True, self._c("money"))
        if self._shefcoin_icon:
            ic = self._shefcoin_icon
            iy = money_y + max(0, (money_txt.get_height() - ic.get_height()) // 2)
            self._canvas.blit(ic, (pad_x, iy))
            self._canvas.blit(money_txt, (pad_x + ic.get_width() + 12, money_y))
        else:
            self._canvas.blit(
                self.font.render(f"Монеты: {self.money}", True, self._c("money")),
                (pad_x, money_y),
            )
        if self.money_debt > 0:
            debt_txt = self.font_small.render(
                f"Долг: {self.money_debt}",
                True,
                (210, 100, 85),
            )
            self._canvas.blit(debt_txt, (pad_x, money_y + 28))

        sel = ""
        if self.selected_auto is not None:
            fi, ai = self.selected_auto
            if (
                fi == self.current_floor
                and 0 <= fi < len(self.autos_by_floor)
                and 0 <= ai < len(self.autos_by_floor[fi])
            ):
                sel = f"Выбран авто №{ai + 1} на этаже"
        n_here = len(self._autos_on_current_floor())
        cap_here = self._max_autos_this_floor()
        stat_line = (
            f"Ручной: ур.{self.manual_level}/{MAX_MANUAL_LEVEL} · "
            f"этаж {self.current_floor + 1}/{self.floors_owned} · "
            f"авто {n_here}/{cap_here}"
            if not self.cheat_unlocked
            else f"Ручной: ур.{self.manual_level} (чит) · "
            f"этаж {self.current_floor + 1}/{self.floors_owned} · "
            f"авто {n_here}/{cap_here}"
        )
        y_line = 102 if self.money_debt > 0 else 86
        self._canvas.blit(
            self.font_small.render(stat_line, True, self._c("text_dim")),
            (pad_x, y_line),
        )
        y_line += 28
        if sel:
            self._canvas.blit(
                self.font_tiny.render(sel, True, self._c("text_dim")),
                (pad_x, y_line),
            )
            y_line += 22
        y_line += 4
        hint_lines = (
            "Клик — бросок/выбор · ↑↓ смена этажа",
            "F11 — полный экран · Esc — отмена размещения / выход",
        )
        for hl in hint_lines:
            self._canvas.blit(
                self.font_tiny.render(hl, True, self._c("text_dim")),
                (pad_x, y_line),
            )
            y_line += 24

    def draw_manual_thrower(self) -> None:
        if self.current_floor != 0:
            return
        slot = self.manual_skin_slot()
        surf = self._skin_surfs[slot]
        rect = surf.get_rect(center=(int(self.thrower_x), int(self.thrower_y)))
        self._canvas.blit(surf, rect)
        if self.raid_active:
            bw, bh = 120, 12
            bx = int(self.thrower_x - bw // 2)
            by = int(self.thrower_y - THROWER_HALF - 36)
            pygame.draw.rect(self._canvas, (40, 40, 48), (bx, by, bw, bh), border_radius=4)
            frac = max(0.0, min(1.0, self.raid_shef_hp / float(RAID_SHEF_MAX_HP)))
            if frac > 0:
                pygame.draw.rect(
                    self._canvas,
                    (70, 190, 100),
                    (bx + 2, by + 2, int((bw - 4) * frac), bh - 4),
                    border_radius=2,
                )
        lbl = self.font_small.render(f"шефосер ур.{self.manual_level}", True, self._c("text"))
        self._canvas.blit(
            lbl,
            (int(self.thrower_x) - lbl.get_width() // 2, rect.bottom + 8),
        )

    def draw_autos(self) -> None:
        for i, auto in enumerate(self._autos_on_current_floor()):
            slot = self._skin_slot_for_level(auto.level)
            surf = self._skin_surfs[slot]
            rect = surf.get_rect(center=(int(auto.x), int(auto.y)))
            self._canvas.blit(surf, rect)
            if self.selected_auto == (self.current_floor, i):
                pygame.draw.circle(
                    self._canvas,
                    self._c("accent"),
                    (int(auto.x), int(auto.y)),
                    THROWER_HALF + 12,
                    4,
                )
            cap = self.font_small.render(f"№{i + 1} ур.{auto.level}", True, self._c("text_dim"))
            self._canvas.blit(cap, (int(auto.x) - cap.get_width() // 2, rect.bottom + 4))

    def draw_items(self) -> None:
        for it in self.items:
            surf = self._skin_surfs[it.skin_slot % self._n_skins]
            rect = surf.get_rect(center=(int(it.x), int(it.y)))
            self._canvas.blit(surf, rect)

    def draw_raid_layer(self) -> None:
        if not self.raid_active:
            return

        # Движение/мобы
        for r in self.raid_redhats:
            if not r.alive:
                continue
            s = self._raid_redhat_surf_by_level.get(r.level, self._redhat_surf)
            hw, hh = s.get_width() // 2, s.get_height() // 2
            self._canvas.blit(s, (int(r.x - hw), int(r.y - hh)))

            # Только у босса явно показываем LV4 + BOSS.
            if r.is_boss:
                lv = self.font_small.render("LV4", True, (255, 160, 160))
                boss = self.font_small.render("БОСС", True, (255, 80, 80))
                bx = int(r.x - lv.get_width() // 2)
                by = int(r.y - hh - lv.get_height() - 6)
                self._canvas.blit(lv, (bx, by))
                self._canvas.blit(boss, (bx, by + lv.get_height() + 2))

        # Лазер 5: большая полоса. Отрисовываем поверх мобов.
        if (
            self.current_floor == 0
            and self.equipped_weapon == 4
            and self._raid_laser_t > 0.0
        ):
            x1, y1 = self._raid_laser_start
            x2, y2 = self._raid_laser_end
            w = self._raid_laser_width
            pygame.draw.line(
                self._canvas,
                (50, 150, 255),
                (int(x1), int(y1)),
                (int(x2), int(y2)),
                w + 10,
            )
            pygame.draw.line(
                self._canvas,
                (120, 240, 255),
                (int(x1), int(y1)),
                (int(x2), int(y2)),
                w,
            )

        for b in self.raid_bullets:
            pygame.draw.circle(self._canvas, (100, 220, 255), (int(b.x), int(b.y)), 8)
            pygame.draw.circle(self._canvas, (200, 250, 255), (int(b.x), int(b.y)), 4)

        # Волны (без надписи "передышка")
        if self._raid_banner_sec > 0:
            if self.raid_wave_idx < RAID_WAVES:
                title = f"ВОЛНА {self.raid_wave_idx + 1}/{RAID_WAVES}"
            else:
                title = "ФИНАЛ"
            c = (220, 60, 60)
            b1 = self.font_title.render(title, True, c)
            self._canvas.blit(b1, (GAME_W // 2 - b1.get_width() // 2, self.PANEL_H + 14))

        if self._raid_game_msg_sec > 0:
            b2 = self.font_small.render(
                "Клик по полю — выстрел с ручного шефосера (оружие в магазине).",
                True,
                self._c("accent"),
            )
            self._canvas.blit(b2, (GAME_W // 2 - b2.get_width() // 2, self.PANEL_H + 54))

    def draw_background(self) -> None:
        self._canvas.fill(self._c("bg"))

    def _settings_layout(self) -> Dict[str, pygame.Rect]:
        px, py = 120, 40
        pw, ph = 1680, 780
        d: Dict[str, pygame.Rect] = {
            "panel": pygame.Rect(px, py, pw, ph),
            "close": pygame.Rect(px + pw - 200, py + 16, 180, 48),
            "theme_dark": pygame.Rect(px + 40, py + 100, 200, 48),
            "theme_light": pygame.Rect(px + 260, py + 100, 200, 48),
            "pw_field": pygame.Rect(px + 40, py + 200, 520, 48),
            "pw_show": pygame.Rect(px + 572, py + 200, 150, 48),
            "activate": pygame.Rect(px + 738, py + 200, 160, 48),
        }
        lx = px + 280
        fw = 560
        fh = 34
        cr = py + 272
        d["cheat_f_money"] = pygame.Rect(lx, cr, fw, fh)
        cr += 42
        d["cheat_f_manual"] = pygame.Rect(lx, cr, fw, fh)
        cr += 42
        d["cheat_f_belt"] = pygame.Rect(lx, cr, fw, fh)
        cr += 42
        d["cheat_f_time"] = pygame.Rect(lx, cr, fw, fh)
        cr += 42
        d["cheat_f_autos_cap"] = pygame.Rect(lx, cr, fw, fh)
        cr += 42
        d["cheat_f_free_shop"] = pygame.Rect(lx, cr, 200, fh)
        d["cheat_f_freeze"] = pygame.Rect(lx + 360, cr, 200, fh)
        cr += 42
        d["cheat_f_auto_lvl"] = pygame.Rect(lx, cr, fw, fh)
        cr += 48
        d["cheat_apply"] = pygame.Rect(px + 40, cr, 140, 44)
        d["cheat_toggle"] = pygame.Rect(px + 188, cr, 250, 44)
        d["cheat_rain"] = pygame.Rect(px + 448, cr, 150, 44)
        d["cheat_raid"] = pygame.Rect(px + 606, cr, 200, 44)
        d["cheat_del_save"] = pygame.Rect(px + 814, cr, 200, 44)
        d["cheat_destroy"] = pygame.Rect(px + 1022, cr, 280, 44)
        return d

    def _sync_cheat_fields_from_game(self) -> None:
        self._cheat_field_money = str(self.money)
        self._cheat_field_manual = str(self.manual_level)
        self._cheat_field_belt = str(self.belt_speed_mul)
        self._cheat_field_time = str(self.cheat_time_scale)
        self._cheat_field_autos_cap = str(self.cheat_max_autos_override)
        self._cheat_field_free_shop = "1" if self.cheat_free_shop else "0"
        self._cheat_field_freeze = "1" if self.cheat_belt_frozen else "0"
        self._cheat_field_auto_lvl = ""

    def _apply_cheat_fields(self) -> None:
        if not self.cheat_unlocked:
            return
        try:
            self.money = max(0, min(10**18, int(self._cheat_field_money.strip() or "0")))
            self.manual_level = max(
                1, min(999_999_999, int(self._cheat_field_manual.strip() or "1"))
            )
            self.belt_speed_mul = max(
                0.0001,
                min(1_000_000.0, float(self._cheat_field_belt.strip() or "1")),
            )
            self.cheat_time_scale = max(
                0.0, min(10_000.0, float(self._cheat_field_time.strip() or "1"))
            )
            self.cheat_max_autos_override = max(
                1, min(99999, int(self._cheat_field_autos_cap.strip() or "32"))
            )
            fs = self._cheat_field_free_shop.strip().lower()
            self.cheat_free_shop = fs in ("1", "да", "yes", "true", "on", "y")
            fr = self._cheat_field_freeze.strip().lower()
            self.cheat_belt_frozen = fr in ("1", "да", "yes", "true", "on", "y")
            al = self._cheat_field_auto_lvl.strip()
            if al and self.selected_auto is not None:
                fi, ai = self.selected_auto
                if 0 <= fi < len(self.autos_by_floor) and 0 <= ai < len(
                    self.autos_by_floor[fi]
                ):
                    lv = max(1, min(999_999_999, int(al)))
                    self.autos_by_floor[fi][ai].level = lv
                    self._play_upgrade_sound(lv)
            self._cheat_msg = "Применено"
        except ValueError:
            self._cheat_msg = "Неверные числа"
        self._save_game()

    def _reset_game_after_delete_save(self) -> None:
        self.money = 0
        self.manual_level = 1
        self.autos_by_floor = [[]]
        self.floors_owned = 1
        self.current_floor = 0
        self._placement_paid = 0
        self.items.clear()
        self.belt_scroll = 0.0
        self.belt_speed_mul = 1.0
        self.theme = "dark"
        self.cheat_unlocked = False
        self.cheat_active = False
        self.cheat_time_scale = 1.0
        self.cheat_free_shop = False
        self.cheat_belt_frozen = False
        self.cheat_max_autos_override = 32
        self.placement_pending = False
        self.selected_auto = None
        self.shop_tab = SHOP_TAB_EARN
        self.weapons_owned = [False] * len(WEAPON_DEFS)
        self.equipped_weapon = -1
        self.money_debt = 0
        self.raid_active = False
        self.raid_shef_hp = 0
        self.raid_redhats.clear()
        self.raid_bullets.clear()
        self.raid_spawned = 0
        self.raid_spawn_timer = 0.4
        self._raid_wave_timer = RAID_INTERVAL_SEC
        self._raid_banner_sec = 0.0
        self._raid_game_msg_sec = 0.0
        self._raid_weapon_cd = 0.0
        self._cheat_msg = "Сохранение удалено"

    def _wipe_cheats_forever(self) -> None:
        self.cheat_unlocked = False
        self.cheat_active = False
        self.cheat_free_shop = False
        self.cheat_belt_frozen = False
        self.cheat_time_scale = 1.0
        self.belt_speed_mul = max(0.5, min(2.0, self.belt_speed_mul))
        self.manual_level = min(self.manual_level, MAX_MANUAL_LEVEL)
        self.cheat_max_autos_override = 32
        for fl in self.autos_by_floor:
            for a in fl:
                a.level = min(a.level, MAX_MANUAL_LEVEL)
        self._password_buf = ""
        self._cheat_msg = "Читы сброены. Пароль можно ввести снова."
        self._save_game()

    def _draw_text_field(
        self,
        rect: pygame.Rect,
        text: str,
        focused: bool,
    ) -> None:
        psh = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        pygame.draw.rect(psh, (0, 0, 0, 45), psh.get_rect(), border_radius=8)
        self._canvas.blit(psh, (rect.x + 2, rect.y + 2))
        pygame.draw.rect(
            self._canvas,
            self._c("panel") if focused else self._c("btn"),
            rect,
            border_radius=8,
        )
        pygame.draw.rect(self._canvas, self._c("btn_hi"), rect, 2, border_radius=8)
        vis = text if len(text) <= 48 else text[:45] + "…"
        self._canvas.blit(
            self.font_small.render(vis or " ", True, self._c("text")),
            (rect.x + 10, rect.y + 5),
        )

    def _cheat_spawn_rain(self) -> None:
        sh = self._skin_surfs[0].get_height()
        belt_top = self.BELT_Y
        spd = self.belt_speed()
        if self.cheat_unlocked and self.cheat_belt_frozen:
            spd = 0.0
        if spd <= 0:
            spd = max(120.0, self.BELT_SPEED_BASE * max(0.05, self.belt_speed_mul))
        for _ in range(48):
            x = random.uniform(100.0, float(GAME_W - 100))
            skin = random.randint(0, self._n_skins - 1)
            self.items.append(
                ItemOnBelt(
                    x=x,
                    y=belt_top - sh // 2,
                    phase="belt",
                    vx=spd,
                    vy=0.0,
                    coin_value=random.randint(1, 80),
                    skin_slot=skin,
                )
            )

    def _try_submit_cheat_password(self) -> None:
        t = self._password_buf.strip()
        h = hashlib.sha512(b"shefos_cheat_pw_salt_v1" + t.encode("utf-8")).hexdigest()
        good = "3e702361ed80bad9fec975cd3527009cacc90a52b334df7d2cf8220537b11efb8ad8f963f73c5327c8e0fb0d912c95cc75f8913b7b34448b2e9a694b3a1bf396"
        if hmac.compare_digest(h, good):
            self.cheat_unlocked = True
            self._cheat_msg = "Доступ разблокирован"
            self._password_buf = ""
            self._sync_cheat_fields_from_game()
        else:
            self._cheat_msg = "Неверный пароль"
        self._save_game()

    def _settings_click(self, pos: Tuple[int, int]) -> None:
        L = self._settings_layout()
        if not L["panel"].collidepoint(pos):
            self.settings_open = False
            self._settings_focus_field = None
            self._pw_show = False
            return
        if L["close"].collidepoint(pos):
            self.settings_open = False
            self._settings_focus_field = None
            self._pw_show = False
            self._play_tap()
            return
        if L["theme_dark"].collidepoint(pos):
            self.theme = "dark"
            self._play_tap()
            self._save_game()
            return
        if L["theme_light"].collidepoint(pos):
            self.theme = "light"
            self._play_tap()
            self._save_game()
            return
        if not self.cheat_unlocked:
            if L["pw_show"].collidepoint(pos):
                self._pw_show = not self._pw_show
                self._play_tap()
                return
            if L["activate"].collidepoint(pos):
                self._try_submit_cheat_password()
                self._play_tap()
                return
            if L["pw_field"].collidepoint(pos):
                self._settings_focus_field = "pw"
                return
        if self.cheat_unlocked:
            if L["cheat_apply"].collidepoint(pos):
                self._apply_cheat_fields()
                self._play_tap()
                return
            if L["cheat_toggle"].collidepoint(pos):
                self.cheat_active = not self.cheat_active
                self._play_tap()
                self._save_game()
                return
            if L["cheat_rain"].collidepoint(pos):
                self._cheat_spawn_rain()
                self._play_tap()
                self._save_game()
                return
            if L["cheat_raid"].collidepoint(pos):
                self._start_raid(force=True)
                self._play_tap()
                self._save_game()
                return
            if L["cheat_del_save"].collidepoint(pos):
                delete_save_file()
                self._reset_game_after_delete_save()
                self._play_tap()
                return
            if L["cheat_destroy"].collidepoint(pos):
                self._wipe_cheats_forever()
                self._play_tap()
                return
            fm = (
                ("cheat_f_money", "money"),
                ("cheat_f_manual", "manual"),
                ("cheat_f_belt", "belt"),
                ("cheat_f_time", "time"),
                ("cheat_f_autos_cap", "autos_cap"),
                ("cheat_f_free_shop", "free_shop"),
                ("cheat_f_freeze", "freeze"),
                ("cheat_f_auto_lvl", "auto_lvl"),
            )
            for rk, fk in fm:
                if L[rk].collidepoint(pos):
                    self._settings_focus_field = fk
                    return
        self._settings_focus_field = None

    def _settings_append_char(self, ch: str) -> None:
        k = self._settings_focus_field
        if not k or not ch.isprintable():
            return
        lim = 96 if k == "pw" else 28
        if k == "pw":
            if len(self._password_buf) < lim:
                self._password_buf += ch
        elif k == "money":
            if len(self._cheat_field_money) < lim:
                self._cheat_field_money += ch
        elif k == "manual":
            if len(self._cheat_field_manual) < lim:
                self._cheat_field_manual += ch
        elif k == "belt":
            if len(self._cheat_field_belt) < lim:
                self._cheat_field_belt += ch
        elif k == "time":
            if len(self._cheat_field_time) < lim:
                self._cheat_field_time += ch
        elif k == "autos_cap":
            if len(self._cheat_field_autos_cap) < lim:
                self._cheat_field_autos_cap += ch
        elif k == "free_shop":
            if len(self._cheat_field_free_shop) < lim:
                self._cheat_field_free_shop += ch
        elif k == "freeze":
            if len(self._cheat_field_freeze) < lim:
                self._cheat_field_freeze += ch
        elif k == "auto_lvl":
            if len(self._cheat_field_auto_lvl) < lim:
                self._cheat_field_auto_lvl += ch

    def _settings_backspace(self) -> None:
        k = self._settings_focus_field
        if k == "pw":
            self._password_buf = self._password_buf[:-1]
        elif k == "money":
            self._cheat_field_money = self._cheat_field_money[:-1]
        elif k == "manual":
            self._cheat_field_manual = self._cheat_field_manual[:-1]
        elif k == "belt":
            self._cheat_field_belt = self._cheat_field_belt[:-1]
        elif k == "time":
            self._cheat_field_time = self._cheat_field_time[:-1]
        elif k == "autos_cap":
            self._cheat_field_autos_cap = self._cheat_field_autos_cap[:-1]
        elif k == "free_shop":
            self._cheat_field_free_shop = self._cheat_field_free_shop[:-1]
        elif k == "freeze":
            self._cheat_field_freeze = self._cheat_field_freeze[:-1]
        elif k == "auto_lvl":
            self._cheat_field_auto_lvl = self._cheat_field_auto_lvl[:-1]

    def _handle_settings_keydown(self, event: pygame.event.Event) -> None:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self._settings_focus_field == "pw":
                self._try_submit_cheat_password()
            elif (
                self.cheat_unlocked
                and self._settings_focus_field
                and self._settings_focus_field != "pw"
            ):
                self._apply_cheat_fields()
            return
        if not self._settings_focus_field:
            return
        if event.key == pygame.K_BACKSPACE:
            self._settings_backspace()

    def _handle_settings_textinput(self, event: pygame.event.Event) -> None:
        if not self._settings_focus_field:
            return
        for ch in event.text:
            self._settings_append_char(ch)

    def _draw_settings_btn(
        self,
        rect: pygame.Rect,
        label: str,
        icon_fn: Optional[Any],
        *,
        selected: bool = False,
        text_left: int = 50,
        compact: bool = False,
        icon_center: bool = False,
        font: Optional[Any] = None,
    ) -> None:
        sh = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        pygame.draw.rect(sh, (0, 0, 0, 72), sh.get_rect(), border_radius=14)
        self._canvas.blit(sh, (rect.x + 5, rect.y + 5))
        base = self._c("accent") if selected else self._c("btn")
        pygame.draw.rect(self._canvas, base, rect, border_radius=14)
        rim = tuple(min(255, c + 40) for c in base)
        pygame.draw.line(
            self._canvas,
            rim,
            (rect.left + 12, rect.top + 3),
            (rect.right - 12, rect.top + 3),
            2,
        )
        pygame.draw.rect(self._canvas, self._c("btn_hi"), rect, 2, border_radius=14)
        tc = self._c("text")
        if icon_fn:
            ix = rect.centerx if icon_center and not label.strip() else rect.left + (
                22 if compact else 24
            )
            icon_fn(self._canvas, ix, rect.centery, tc)
        fnt = font if font is not None else self.font_small
        t = fnt.render(label, True, tc)
        self._canvas.blit(
            t,
            (rect.left + text_left, rect.centery - t.get_height() // 2),
        )

    def draw_settings_overlay(self) -> None:
        ov = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 170))
        self._canvas.blit(ov, (0, 0))
        L = self._settings_layout()
        pygame.draw.rect(self._canvas, self._c("shop"), L["panel"], border_radius=16)
        pygame.draw.rect(self._canvas, self._c("shop_line"), L["panel"], 3, border_radius=16)
        px = L["panel"].x
        py = L["panel"].y
        self._canvas.blit(
            self.font_title.render("Настройки", True, self._c("text")),
            (px + 40, py + 24),
        )
        self._draw_settings_btn(L["close"], "Закрыть", _draw_icon_close, text_left=44)
        self._canvas.blit(
            self.font_small.render("Тема", True, self._c("text_dim")),
            (px + 40, py + 72),
        )
        self._draw_settings_btn(
            L["theme_dark"],
            "Тёмная",
            _draw_icon_moon,
            selected=self.theme == "dark",
        )
        self._draw_settings_btn(
            L["theme_light"],
            "Светлая",
            _draw_icon_sun,
            selected=self.theme == "light",
        )
        msg_y = py + 168
        if not self.cheat_unlocked:
            self._canvas.blit(
                self.font_small.render(
                    "Пароль чит-режима (разблокировка)",
                    True,
                    self._c("text_dim"),
                ),
                (px + 40, msg_y),
            )
            psh = pygame.Surface((L["pw_field"].w, L["pw_field"].h), pygame.SRCALPHA)
            pygame.draw.rect(psh, (0, 0, 0, 50), psh.get_rect(), border_radius=10)
            self._canvas.blit(psh, (L["pw_field"].x + 3, L["pw_field"].y + 3))
            pygame.draw.rect(
                self._canvas,
                self._c("panel") if self._settings_focus_field == "pw" else self._c("btn"),
                L["pw_field"],
                border_radius=10,
            )
            hi = tuple(min(255, c + 28) for c in self._c("btn"))
            pygame.draw.line(
                self._canvas,
                hi,
                (L["pw_field"].left + 8, L["pw_field"].top + 2),
                (L["pw_field"].right - 8, L["pw_field"].top + 2),
                2,
            )
            pygame.draw.rect(self._canvas, self._c("btn_hi"), L["pw_field"], 2, border_radius=10)
            if self._pw_show:
                shown = self._password_buf
                if len(shown) > 42:
                    shown = shown[:39] + "…"
                pw_vis = shown
            else:
                pw_vis = "•" * len(self._password_buf)
            self._canvas.blit(
                self.font_small.render(pw_vis or " ", True, self._c("text")),
                (L["pw_field"].x + 12, L["pw_field"].y + 10),
            )
            show_lbl = "Скрыть" if self._pw_show else "Показать"
            show_ico = _draw_icon_eye_slash if self._pw_show else _draw_icon_eye
            self._draw_settings_btn(
                L["pw_show"], show_lbl, show_ico, text_left=44, compact=True
            )
            self._draw_settings_btn(
                L["activate"], "Проверить", _draw_icon_check, text_left=44
            )
        if self._cheat_msg:
            self._canvas.blit(
                self.font_small.render(self._cheat_msg, True, self._c("accent")),
                (px + 920, msg_y + 4),
            )
        if self.cheat_unlocked:
            self._canvas.blit(
                self.font_small.render(
                    "Чит-режим (числа — большие лимиты; Enter в поле — применить)",
                    True,
                    self._c("text_dim"),
                ),
                (px + 40, L["cheat_f_money"].y - 28),
            )
            rows = (
                ("Монеты", "cheat_f_money", self._cheat_field_money, "money"),
                ("Ручной ур.", "cheat_f_manual", self._cheat_field_manual, "manual"),
                ("× конвейера", "cheat_f_belt", self._cheat_field_belt, "belt"),
                ("× времени игры", "cheat_f_time", self._cheat_field_time, "time"),
                ("Лимит авто (чит)", "cheat_f_autos_cap", self._cheat_field_autos_cap, "autos_cap"),
            )
            for lab, rk, val, fk in rows:
                self._canvas.blit(
                    self.font_tiny.render(lab, True, self._c("text_dim")),
                    (px + 40, L[rk].centery - 12),
                )
                self._draw_text_field(
                    L[rk], val, self._settings_focus_field == fk
                )
            self._canvas.blit(
                self.font_tiny.render(
                    "Магаз бесплатно / стоп ленты (1/0 да/нет):",
                    True,
                    self._c("text_dim"),
                ),
                (px + 40, L["cheat_f_free_shop"].y - 22),
            )
            self._draw_text_field(
                L["cheat_f_free_shop"],
                self._cheat_field_free_shop,
                self._settings_focus_field == "free_shop",
            )
            self._draw_text_field(
                L["cheat_f_freeze"],
                self._cheat_field_freeze,
                self._settings_focus_field == "freeze",
            )
            self._canvas.blit(
                self.font_tiny.render("Ур. выбранного авто", True, self._c("text_dim")),
                (px + 40, L["cheat_f_auto_lvl"].centery - 12),
            )
            self._draw_text_field(
                L["cheat_f_auto_lvl"],
                self._cheat_field_auto_lvl,
                self._settings_focus_field == "auto_lvl",
            )
            self._draw_settings_btn(
                L["cheat_apply"],
                "Применить",
                _draw_icon_check,
                text_left=44,
                font=self.font_tiny,
            )
            st = "Чит: ВКЛ" if self.cheat_active else "Чит: ВЫКЛ"
            self._draw_settings_btn(
                L["cheat_toggle"],
                st,
                _draw_icon_zap,
                selected=self.cheat_active,
                text_left=46,
                font=self.font_tiny,
            )
            self._draw_settings_btn(
                L["cheat_rain"],
                "Дождь",
                _draw_icon_zap,
                text_left=44,
                font=self.font_tiny,
            )
            self._draw_settings_btn(
                L["cheat_raid"],
                "Вызвать рейд",
                _draw_icon_zap,
                text_left=44,
                font=self.font_tiny,
            )
            self._draw_settings_btn(
                L["cheat_del_save"],
                "Удалить сохранение",
                _draw_icon_close,
                text_left=44,
                font=self.font_tiny,
            )
            self._draw_settings_btn(
                L["cheat_destroy"],
                "Сбросить читы",
                _draw_icon_close,
                text_left=44,
                font=self.font_tiny,
            )

    def run(self) -> None:
        while True:
            dt = self.clock.tick(60) / 1000.0
            dt_eff = dt * self.cheat_time_scale if self.cheat_unlocked else dt
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._save_game()
                    pygame.quit()
                    sys.exit(0)
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    if self.settings_open:
                        self.settings_open = False
                        self._settings_focus_field = None
                        self._pw_show = False
                        continue
                    if self.placement_pending:
                        self._cancel_placement()
                        continue
                    self._save_game()
                    pygame.quit()
                    sys.exit(0)
                if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                    self._toggle_fullscreen()
                    continue
                if (
                    event.type == pygame.KEYDOWN
                    and not self.settings_open
                    and not self.raid_active
                ):
                    if event.key == pygame.K_UP:
                        self._change_floor(self.current_floor + 1)
                        continue
                    if event.key == pygame.K_DOWN:
                        self._change_floor(self.current_floor - 1)
                        continue
                if self.settings_open:
                    if event.type == pygame.TEXTINPUT:
                        self._handle_settings_textinput(event)
                    elif event.type == pygame.KEYDOWN:
                        self._handle_settings_keydown(event)
                    elif (
                        event.type == pygame.MOUSEBUTTONDOWN
                        and event.button == 1
                    ):
                        lp = self._logical_pos(event.pos)
                        if lp is not None:
                            self._settings_click(lp)
                    continue
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    lp = self._logical_pos(event.pos)
                    if lp is None:
                        continue
                    pos = lp
                    if pos[0] < GAME_W:
                        if (
                            self._btn_floor_prev
                            and self._btn_floor_prev.collidepoint(pos)
                            and not self.raid_active
                        ):
                            self._change_floor(self.current_floor - 1)
                            continue
                        if (
                            self._btn_floor_next
                            and self._btn_floor_next.collidepoint(pos)
                            and not self.raid_active
                        ):
                            self._change_floor(self.current_floor + 1)
                            continue
                    if pos[0] >= GAME_W:
                        self._shop_click(pos)
                    elif self.raid_active and self.current_floor == 0:
                        self._raid_try_shoot(pos)
                    elif self.placement_pending:
                        self.try_place_auto(pos)
                    elif self.current_floor == 0 and self._thrower_rect.collidepoint(
                        pos
                    ):
                        self.try_throw_manual(pos)
                    else:
                        self.try_click_autos(pos)

            # Зажатие лазера (5 тир): пока удерживаешь ЛКМ, продолжаем ваншотить.
            if (
                self.raid_active
                and self.current_floor == 0
                and not self.settings_open
                and self.equipped_weapon == 4
            ):
                pressed = pygame.mouse.get_pressed(num_buttons=3)[0]
                if pressed:
                    lp = self._logical_pos(pygame.mouse.get_pos())
                    if lp is not None and lp[0] < GAME_W:
                        self._set_raid_laser_visual(lp)
                        self._raid_try_shoot(lp)

            if not self.settings_open:
                self.update(dt_eff)
            self.draw_background()
            self.draw_gui()
            self.draw_shop()
            self.draw_manual_thrower()
            self.draw_autos()
            self.draw_conveyor()
            self.draw_items()
            self.draw_raid_layer()
            if self.placement_pending and not self.settings_open:
                lp = self._logical_pos(pygame.mouse.get_pos())
                if lp is not None:
                    mx, my = lp
                    if mx < GAME_W and self.PANEL_H < my < self.BELT_Y - 20:
                        s = pygame.Surface((80, 80), pygame.SRCALPHA)
                        s.fill((120, 200, 255, 90))
                        self._canvas.blit(s, (mx - 40, my - 40))
            if self.settings_open:
                self.draw_settings_overlay()
            self._update_display_transform()
            self._present_canvas()
            pygame.display.flip()

    def _present_canvas(self) -> None:
        """Внутренний кадр 1920×1080: 1:1 или растягивание на всё окно (без чёрных полос по краям)."""
        if self._native_blit:
            self.screen.blit(self._canvas, (0, 0))
            return
        sw, sh = self.screen.get_size()
        scaled = pygame.transform.scale(self._canvas, (max(1, sw), max(1, sh)))
        self.screen.blit(scaled, (0, 0))


def main() -> None:
    TycoonGame().run()


if __name__ == "__main__":
    main()