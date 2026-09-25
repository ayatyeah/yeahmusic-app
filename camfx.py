"""Эффекты для TikTok: зум на лицо, ключевое слово строки, строки-сцены, «пи-ип».

Зум: в припеве камера плавно приближает лицо, в куплете отъезжает обратно;
на строках-сценах (лип-синк) — крупный план лица.
"""
import collections
import json
import math
import re

import numpy as np
import pygame

ZOOM_CHORUS = 1.3          # во сколько раз приближаем лицо в припеве и в особом моменте
ZOOM_SCENE = ZOOM_CHORUS   # на крупных строках — не ближе среднего
ZOOM_SPEED = 0.06          # плавность: доля пути за кадр
FACE_SMOOTH = 0.25         # плавность слежения за лицом

KEYWORD_MIN = 4            # слова короче — не ключевые
KEYWORD_GAP = 4.0          # сек: крупное слово не чаще


# ---------- зум ----------

class Zoom:
    """Плавное приближение к лицу. Всё в долях кадра (0..1), кадр уже зеркальный."""

    def __init__(self):
        self.level = 1.0
        self.target = 1.0
        self.focus = (0.5, 0.42)          # куда приближать — обычно лицо
        self.crop = (0.0, 0.0, 1.0, 1.0)  # какую часть кадра показали в последний раз

    def set_chorus(self, on):
        self.target = ZOOM_CHORUS if on else 1.0

    def set_level(self, level):
        self.target = level

    def see_face(self, fx, fy):
        """Лицо (fx, fy) — доли показанного (уже приближенного) кадра → доли полного."""
        x0, y0, cw, ch = self.crop
        gx, gy = x0 + fx * cw, y0 + fy * ch
        k = FACE_SMOOTH
        self.focus = (self.focus[0] + (gx - self.focus[0]) * k,
                      self.focus[1] + (gy - self.focus[1]) * k)

    def step(self):
        self.level += (self.target - self.level) * ZOOM_SPEED
        if abs(self.level - self.target) < 0.002:
            self.level = self.target
        cw = ch = 1 / self.level
        x0 = min(max(self.focus[0] - cw / 2, 0.0), 1 - cw)
        y0 = min(max(self.focus[1] - ch / 2, 0.0), 1 - ch)
        self.crop = (x0, y0, cw, ch)
        return self.crop

    def apply(self, surf):
        """Вырезает приближенную часть кадра (размер не меняет — растянет потом scale)."""
        x0, y0, cw, ch = self.step()
        if cw >= 0.999:
            return surf
        w, h = surf.get_size()
        return surf.subsurface((int(x0 * w), int(y0 * h), max(1, int(cw * w)), max(1, int(ch * h))))


# ---------- силуэт: тени-двойники и обводка ----------

GHOST_COLOR = (120, 220, 255)
GHOST_DELAYS = (0.18, 0.36)   # на сколько секунд отстают тени
GHOST_ALPHA = (85, 50)
OUTLINE_COLOR = (200, 245, 255)
OUTLINE_MS = 1200             # сколько светится обводка силуэта


def mask_surface(mask, size, color, alpha, edge=False):
    """Силуэт (или его контур) из маски MediaPipe → полупрозрачная картинка под размер кадра."""
    m = np.asarray(mask, dtype=np.float32)
    if m.ndim == 3:
        m = m[:, :, 0]
    body = m > 0.5
    if edge:                                   # контур: край силуэта
        shifted = (np.roll(body, 2, 0) & np.roll(body, -2, 0)
                   & np.roll(body, 2, 1) & np.roll(body, -2, 1))
        body = body & ~shifted
    h, w = body.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[body] = (*color, alpha)
    surf = pygame.image.frombuffer(rgba.tobytes(), (w, h), "RGBA")
    return pygame.transform.smoothscale(surf, size)


def draw_ghosts(surf, masks):
    """Тени-двойники: силуэты с задержкой повторяют движения."""
    for mask, alpha in zip(masks, GHOST_ALPHA):
        if mask is not None:
            surf.blit(mask_surface(mask, surf.get_size(), GHOST_COLOR, alpha), (0, 0))


def draw_outline(surf, mask, t):
    """Светящийся контур вокруг силуэта; t — доля от 0 до 1 (0 — только вспыхнул)."""
    k = math.sin(math.pi * min(1.0, t)) ** 0.6
    if mask is None or k <= 0.02:
        return
    layer = mask_surface(mask, surf.get_size(), OUTLINE_COLOR, int(230 * k), edge=True)
    surf.blit(layer, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)


# ---------- ключевое слово строки ----------

def words(line):
    return [w for w in re.findall(r"[\wёЁ]+", line) if len(w) >= KEYWORD_MIN]


def keyword_counts(lines):
    """Сколько строк песни содержат каждое слово — повторяющиеся слова и есть «хук»."""
    counts = collections.Counter()
    for line in lines:
        for w in set(x.lower() for x in words(line)):
            counts[w] += 1
    return counts


def keyword(line, counts, in_chorus):
    """(слово, его позиция в строке) или None. В припеве — всегда, в куплете —
    только если слово повторяется в песне (хотя бы в 3 строках)."""
    best = None
    for m in re.finditer(r"[\wёЁ]+", line):
        w = m.group()
        if len(w) < KEYWORD_MIN:
            continue
        score = counts.get(w.lower(), 1) * 10 + len(w)
        if best is None or score > best[0]:
            best = (score, w, m.start())
    if best is None:
        return None
    if not in_chorus and counts.get(best[1].lower(), 1) < 3:
        return None
    return best[1], best[2]


# ---------- строки-сцены (лип-синк): крупно по центру, лицо крупным планом ----------

SCENES_FILE = "scenes.json"
OLD_MARK = "🎬"            # раньше сцену помечали значком прямо в тексте


def load_scenes(folder):
    """Тексты строк-сцен трека (пометки лежат в scenes.json, текст песни чистый)."""
    try:
        return set(json.loads((folder / SCENES_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_scenes(folder, lines):
    path = folder / SCENES_FILE
    if lines:
        path.write_text(json.dumps(sorted(lines), ensure_ascii=False, indent=1), encoding="utf-8")
    elif path.exists():
        path.unlink()


MOMENT_GAP = 3             # плашки между крупными строками (до стольких подряд) — тоже момент


def moment_ranges(times, flags, length):
    """[(начало, конец)] особых моментов: крупные строки и плашки между ними.
    times — время каждой строки (заполненное), flags — строка крупная?"""
    idx = [i for i, f in enumerate(flags) if f]
    ranges, group = [], []
    for i in idx:
        if group and i - group[-1] > MOMENT_GAP + 1:
            ranges.append(group)
            group = []
        group.append(i)
    if group:
        ranges.append(group)
    out = []
    for g in ranges:
        end = g[-1] + 1
        out.append((times[g[0]], times[end] if end < len(times) else length))
    return out


SIDE_MIN = 5               # слово на край экрана — не короче стольких букв
SHOUT_RE = re.compile(r"^\W*(у|о|а|я|э)-(у|о|а|я|э)", re.IGNORECASE)   # «У-у», «О-о»…


def side_word(line):
    """Самое длинное слово строки — для края экрана (или None)."""
    found = [w for w in re.findall(r"[\wёЁ]+", line) if len(w) >= SIDE_MIN]
    return max(found, key=len) if found else None


def shout(line):
    """Строка начинается с «У-у» / «О-о» — в особом моменте на ней сильная тряска."""
    return bool(SHOUT_RE.match(line))


def strip_old_mark(text):
    """(текст без старого значка 🎬, был ли значок)."""
    t = text.lstrip()
    if t.startswith(OLD_MARK):
        return t[len(OLD_MARK):].lstrip(), True
    return text, False


def bleep_sound():
    """Цензурный «пи-и-ип» — 1 кГц."""
    init = pygame.mixer.get_init()
    if not init:
        return None
    rate, _, channels = init
    t = np.arange(int(rate * 0.5)) / rate
    wave = np.sin(2 * np.pi * 1000 * t) * 0.35
    wave[: int(rate * 0.01)] *= np.linspace(0, 1, int(rate * 0.01))    # без щелчков
    wave[-int(rate * 0.01):] *= np.linspace(1, 0, int(rate * 0.01))
    data = (wave * 32767).astype(np.int16)
    if channels > 1:
        data = np.repeat(data[:, None], channels, axis=1)
    return pygame.sndarray.make_sound(np.ascontiguousarray(data))


# ---------- форма окна камеры по строкам ----------

KEYWORDS_FILE = "keywords.json"
FRAMES_FILE = "frames.json"
# форма: (ширина/высота, высота — доля экрана, как называется)
# широкий — как окно «Камеры» GNOME: 16:9, ~810×456 на экране 1920×1200
SHAPES = {"wide": (16 / 9, 0.38, "широкий"),
          "medium": (3 / 2, 0.42, "средний"),
          "tall": (4 / 5, 0.5, "вертикальный")}


def load_frames(folder):
    """{текст строки: форма окна} — с этой строки окно камеры меняет форму."""
    try:
        data = json.loads((folder / FRAMES_FILE).read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if v in SHAPES}
    except (OSError, ValueError, AttributeError):
        return {}


def save_frames(folder, frames):
    path = folder / FRAMES_FILE
    if frames:
        path.write_text(json.dumps(frames, ensure_ascii=False, indent=1), encoding="utf-8")
    elif path.exists():
        path.unlink()


def load_keywords(folder):
    """{строка: слово} — какое слово вынести крупно (выбрал ИИ-режиссёр)."""
    try:
        data = json.loads((folder / KEYWORDS_FILE).read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_keywords(folder, words):
    path = folder / KEYWORDS_FILE
    if words:
        path.write_text(json.dumps(words, ensure_ascii=False, indent=1), encoding="utf-8")
    elif path.exists():
        path.unlink()


def next_shape(shape):
    """без пометки → широкий → средний → вертикальный → без пометки"""
    order = [None, *SHAPES]
    return order[(order.index(shape) + 1) % len(order)]
