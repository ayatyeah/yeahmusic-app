"""Прозрачный слой во весь экран: строки печатаются прямо поверх всего, без фона.

Отдельный процесс на Qt (у Tk на Linux нет прозрачных окон). Главная программа
пишет сюда в stdin команды — по одной JSON-строке:
    {"cmd": "add", "id": 1, "text": "...", "x": 100, "y": 200, "w": 380, "h": 230, "char_ms": 85}
    {"cmd": "remove", "id": 1}          плавно исчезнуть
    {"cmd": "clear"}                    убрать всё сразу
    {"cmd": "pause", "on": true}        всё замирает (печать, подъём, курсор)
    {"cmd": "pow", "x": 900, "y": 500}  выстрел: «ПАУ!» в стиле комикса
    {"cmd": "hole", "x": 300, "y": 200} пулевое отверстие с трещинами, как на стекле;
                                        строку рядом с дыркой разбивает на буквы
    {"cmd": "word", "text": "навсегда"}  ключевое слово крупно по центру экрана
    {"cmd": "glitch", "ms": 160}        строки дёргаются и двоятся красным/голубым
    {"cmd": "flash"}                    белая вспышка на весь экран (дроп припева)
    {"cmd": "center", "text": "...", "ms": 3000}  строка крупно по центру, слово за словом
    {"cmd": "side", "text": "...", "side": "left"}  слово вертикально вдоль края экрана
    {"cmd": "push", "power": 0.8}       удар: строки разлетаются и возвращаются
    {"cmd": "eq", "levels": [..]}       полоски эквалайзера по низу экрана (бас песни)
    {"cmd": "clones", "ms": 1500}       дроп: вокруг окна — маленькие копии кадра с задержкой
    {"cmd": "cam_on", "shm": "..."}     камера: слой сам берёт свежий кадр из общей памяти
    {"cmd": "cam_off"}                  (в начале — номер кадра, x, y, w, h; рисуется под текстом)
    {"cmd": "quit"}
Слой не ловит мышь и клавиатуру — клики проходят сквозь него.
"""
import collections
import json
import math
import os
import random
import struct
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")   # на Wayland окна нельзя ставить в точку

from PySide6.QtCore import QPointF, QRectF, QSocketNotifier, Qt, QTimer  # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage,  # noqa: E402
                           QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

FONT_FAMILY = "Inter"
FONT_SIZE = 30             # px
FONT_WEIGHT = QFont.Weight.Bold
TEXT_COLOR = QColor("#ffffff")
OUTLINE_COLOR = QColor(0, 0, 0, 230)
OUTLINE_PX = 4.5           # толщина обводки
LETTER_SPACING = 1.0       # px между буквами — чтобы обводка не склеивала их
LINE_GAP = 1.15            # межстрочный интервал
CURSOR = "|"
TICK_MS = 16
FADE_MS = 250
RISE_PX = 0.7              # за тик — как у карточек в main.py
BLINK_MS = 530
BOX_COLOR = QColor("#ececec")          # «Фон у строк»: светлая плашка...
BOX_BORDER = QColor("#bdbdbd")
BOX_TEXT = QColor("#141414")           # ...и тёмный текст без обводки
BOX_RADIUS = 6

POW_MS = 750               # сколько живёт «ПАУ!»
POW_WORDS = ["ПАУ!", "ПАУ!", "БАХ!", "ПИУ!"]
HOLE_MS = 5000             # сколько висит дырка от пули
HOLE_FADE_MS = 1000
WORD_MS = 1300             # ключевое слово крупно
WORD_SIZE = 120
FLASH_MS = 320
SHATTER_MS = 1400          # буквы разлетаются от выстрела
SHATTER_RADIUS = 110       # строку ближе стольких px к дырке — разбиваем
RING_MS = 420              # ударная волна от попадания
GLITCH_SHIFT = 5           # px: насколько двоится красный/голубой
GLITCH_NEW_MS = 140        # каждая новая строка появляется с коротким глитчем
SIDE_SIZE = 110            # слово вдоль края экрана: размер...
SIDE_MS = 1700             # ...сколько живёт
SIDE_MARGIN = 70           # ...и отступ от края экрана (если камеры нет)
SIDE_GAP_PX = 14           # зазор между словом и боком окна камеры
CENTER_SIZE = 64           # «центральные» строки: размер шрифта
CENTER_Y = 0.66            # и высота на экране (доля) — ниже лица, чтобы видно губы
CENTER_WIDTH = 0.62        # ширина строки — доля экрана
RAISE_MS = 400             # как часто поднимать слой поверх других окон
PUSH_PX = 26               # на сколько строки разлетаются от удара
PUSH_MS = 700
EQ_HEIGHT = 120            # высота полосок эквалайзера
EQ_FADE = 0.6              # нет данных столько секунд — полоски гаснут
EQ_COLOR = QColor(255, 255, 255, 150)
CLONE_SCALE = 0.34         # размер копий кадра на дропе
CLONE_DELAYS = (0.12, 0.24, 0.36, 0.48)
CAM_GLITCH_PX = 9          # на сколько разъезжаются красный и голубой у камеры
CAM_HEADER = 160           # как в main.py: начало общей памяти — кадр, окно и его след


class Effect:
    """«ПАУ!» или дырка от пули. Живут по настоящему времени, пауза их не держит."""

    def __init__(self, kind, x, y):
        self.kind = kind
        self.x, self.y = float(x), float(y)
        self.born = time.monotonic() * 1000
        rnd = random.Random()
        self.word = rnd.choice(POW_WORDS)
        self.tilt = rnd.uniform(-14, 14)
        self.spikes = [(math.pi * k / 11 + rnd.uniform(-0.1, 0.1), rnd.uniform(0.8, 1.15))
                       for k in range(22)]
        # трещины как на стекле: лучи от центра + поперечные «паутинки» между ними
        n = rnd.randint(11, 15)
        self.rays = []
        for k in range(n):
            a = 2 * math.pi * k / n + rnd.uniform(-0.15, 0.15)
            length = rnd.uniform(55, 120)
            pts, r = [], 7.0
            while r < length:
                pts.append((math.cos(a) * r, math.sin(a) * r))
                r += rnd.uniform(8, 16)
                a += rnd.uniform(-0.08, 0.08)
            self.rays.append(pts)
        self.web = []
        for ring in (15, 28, 44):
            for k in range(n):
                if rnd.random() < 0.75:
                    a, b = self.rays[k], self.rays[(k + 1) % n]
                    pa = min(a, key=lambda q: abs(math.hypot(*q) - ring))
                    pb = min(b, key=lambda q: abs(math.hypot(*q) - ring))
                    mid = ((pa[0] + pb[0]) / 2 * 0.93, (pa[1] + pb[1]) / 2 * 0.93)
                    self.web.append((pa, mid, pb))
        self.chips = [(2 * math.pi * k / 16, rnd.uniform(9, 14)) for k in range(16)]

    def age(self):
        return time.monotonic() * 1000 - self.born

    def done(self):
        life = {"pow": POW_MS, "hole": HOLE_MS, "word": WORD_MS, "flash": FLASH_MS,
                "shatter": SHATTER_MS, "center": getattr(self, "life", 0), "side": SIDE_MS}
        return self.age() > life[self.kind]


class Card:
    def __init__(self, cmd, now):
        self.id = cmd["id"]
        self.text = cmd["text"]
        self.x, self.y = float(cmd["x"]), float(cmd["y"])
        self.w, self.h = cmd["w"], cmd["h"]
        self.char_ms = cmd["char_ms"]
        self.box = cmd.get("box", False)   # плашка-фон, как у старых карточек
        self.push = (0.0, 0.0)             # удар: куда толкнуло строку
        self.push_at = -99.0
        self.born = now
        self.i = 0
        self.next_at = now
        self.leaving_at = None       # когда начали исчезать

    def step(self, now):
        """Допечатать буквы, которым пора; на знаках препинания — пауза подольше."""
        while self.i < len(self.text) and now >= self.next_at:
            self.i += 1
            slow = 2.5 if self.text[self.i - 1] in ",.!?…" else 1
            self.next_at += self.char_ms * slow

    def kick(self, now, power, width, height):
        """Толчок от удара: летит от центра экрана и пружинит обратно."""
        dx, dy = self.x + self.w / 2 - width / 2, self.y + self.h / 2 - height / 2
        n = math.hypot(dx, dy) or 1
        self.push = (dx / n * PUSH_PX * power, dy / n * PUSH_PX * power)
        self.push_at = now

    def offset(self, now):
        t = (now - self.push_at) / PUSH_MS
        if t >= 1 or t < 0:
            return 0.0, 0.0
        k = math.exp(-5 * t) * math.cos(9 * t)
        return self.push[0] * k, self.push[1] * k

    def alpha(self, now):
        a = min(1.0, (now - self.born) / FADE_MS)
        if self.leaving_at is not None:
            a = min(a, 1.0 - (now - self.leaving_at) / FADE_MS)
        return max(0.0, a)


class Overlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.NoDropShadowWindowHint
                         | Qt.WindowType.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(QApplication.primaryScreen().geometry())
        self.font = QFont(FONT_FAMILY)
        self.font.setPixelSize(FONT_SIZE)
        self.font.setWeight(FONT_WEIGHT)
        self.font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, LETTER_SPACING)
        self.metrics = QFontMetricsF(self.font)
        self.cards = {}
        self.effects = []
        self.glitch_until = 0.0
        self.word_font = QFont(FONT_FAMILY)
        self.word_font.setPixelSize(WORD_SIZE)
        self.word_font.setWeight(QFont.Weight.Black)
        self.side_font = QFont(FONT_FAMILY)
        self.side_font.setPixelSize(SIDE_SIZE)
        self.side_font.setWeight(QFont.Weight.Black)
        self.side_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        self.center_font = QFont(FONT_FAMILY)
        self.center_font.setPixelSize(CENTER_SIZE)
        self.center_font.setWeight(QFont.Weight.Black)
        self.raised_at = 0.0
        self.cam_shm = None            # кадр камеры приходит через общую память
        self.cam_img = None
        self.cam_rect = None
        self.cam_ghosts = []           # след за окном при взмахе
        self.cam_history = collections.deque(maxlen=48)   # копии кадра для «эха» на дропе
        self.clones_until = 0.0
        self.eq, self.eq_at = [], -99.0
        self.pow_font = QFont(FONT_FAMILY)
        self.pow_font.setPixelSize(64)
        self.pow_font.setWeight(QFont.Weight.Black)
        self.paused = False
        # «часы» слоя: стоят, пока пауза — так печать и подъём продолжаются с того же места
        self.clock = 0.0
        self.last_real = time.monotonic()
        self.buffer = b""

        self.notifier = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Type.Read, self)
        self.notifier.activated.connect(self.read_commands)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK_MS)
        self.show()

    # --- команды из главной программы ---

    def read_commands(self):
        data = os.read(sys.stdin.fileno(), 65536)
        if not data:                     # главная программа закрылась
            QApplication.quit()
            return
        self.buffer += data
        *lines, self.buffer = self.buffer.split(b"\n")
        for line in lines:
            if line.strip():
                self.handle(json.loads(line))

    def handle(self, cmd):
        kind = cmd["cmd"]
        if kind == "add":
            self.cards[cmd["id"]] = Card(cmd, self.clock)
        elif kind == "remove" and cmd["id"] in self.cards:
            card = self.cards[cmd["id"]]
            if card.leaving_at is None:
                card.leaving_at = self.clock
        elif kind == "clear":
            self.cards.clear()
        elif kind in ("pow", "hole"):
            self.effects.append(Effect(kind, cmd["x"], cmd["y"]))
            if kind == "hole":
                self.shatter_near(cmd["x"], cmd["y"])
        elif kind == "word":
            e = Effect("word", self.width() / 2, self.height() * 0.42)
            e.word = cmd["text"]
            e.depth = cmd.get("style") == "depth"          # вылетает из глубины
            self.effects.append(e)
        elif kind == "flash":
            self.effects.append(Effect("flash", 0, 0))
        elif kind == "side":
            left = cmd.get("side", "left") == "left"
            self.effects = [e for e in self.effects                 # на краю — одно слово
                            if not (e.kind == "side" and e.left == left)]
            e = Effect("side", SIDE_MARGIN if left else self.width() - SIDE_MARGIN,
                       self.height() / 2)
            e.word, e.left = cmd["text"], left
            self.effects.append(e)
        elif kind == "center":
            for e in self.effects:                       # прошлая строка уступает место
                if e.kind == "center":
                    e.life = min(e.life, e.age() + 250)
            e = Effect("center", self.width() / 2, self.height() * CENTER_Y)
            words = cmd["text"].split()
            spread = cmd.get("ms", 3000) * 0.7                # слова — за 70% времени строки
            e.words = [(w, spread * k / max(1, len(words))) for k, w in enumerate(words)]
            e.life = cmd.get("ms", 3000) + 600
            self.effects.append(e)
        elif kind == "glitch":
            self.glitch_until = time.monotonic() * 1000 + cmd.get("ms", 160)
        elif kind == "push":
            power = cmd.get("power", 1.0)
            for card in self.cards.values():
                card.kick(self.clock, power, self.width(), self.height())
        elif kind == "eq":
            self.eq = [v / 100 for v in cmd["levels"]]
            self.eq_at = time.monotonic()
        elif kind == "clones":
            self.clones_until = time.monotonic() + cmd.get("ms", 1500) / 1000
        elif kind == "cam_on":
            self.open_cam(cmd["shm"])
        elif kind == "cam_off":
            self.cam_img = self.cam_rect = None
            if self.cam_shm:
                self.cam_shm.close()
                self.cam_shm = None
        elif kind == "pause":
            self.paused = bool(cmd["on"])
        elif kind == "quit":
            QApplication.quit()
        self.update()

    # --- анимация ---

    def tick(self):
        real = time.monotonic()
        self.poll_cam()
        if (self.cards or self.effects or self.cam_img) and real - self.raised_at > RAISE_MS / 1000:
            self.raise_()                 # окно камеры открылось позже — текст всё равно сверху
            self.raised_at = real
        if not self.paused:
            self.clock += (real - self.last_real) * 1000
        self.last_real = real
        if self.effects or time.monotonic() * 1000 < self.glitch_until + 50:
            self.effects = [e for e in self.effects if not e.done()]
            self.update()
        if self.paused or not self.cards:
            return
        for card in list(self.cards.values()):
            card.step(self.clock)
            card.y -= RISE_PX * (self.clock - getattr(card, "moved", card.born)) / TICK_MS
            card.moved = self.clock
            gone = card.leaving_at is not None and self.clock - card.leaving_at >= FADE_MS
            if gone or card.y + card.h < 0:
                del self.cards[card.id]
        self.update()

    # --- рисование ---

    def wrap(self, text, width):
        lines, line = [], ""
        for word in text.split(" "):
            test = f"{line} {word}" if line else word
            if line and self.metrics.horizontalAdvance(test) > width:
                lines.append(line)
                line = word
            else:
                line = test
        lines.append(line)
        return lines

    def paint_pow(self, p, e):
        t = e.age() / POW_MS
        scale = 0.5 + 0.8 * min(1.0, t * 6) - 0.15 * max(0.0, t - 0.2)    # «выпрыгивает»
        p.save()
        p.translate(e.x, e.y)
        p.rotate(e.tilt)
        p.scale(scale, scale)
        p.setOpacity(max(0.0, 1 - max(0.0, t - 0.6) / 0.4))
        burst = QPolygonF([QPointF(math.cos(a) * r * (95 if k % 2 == 0 else 55),
                                   math.sin(a) * r * (70 if k % 2 == 0 else 40))
                           for k, (a, r) in enumerate(e.spikes)])
        p.setPen(QPen(QColor("#111"), 5))
        p.setBrush(QColor("#ffcc1f"))
        p.drawPolygon(burst)
        path = QPainterPath()
        fm = QFontMetricsF(self.pow_font)
        path.addText(QPointF(-fm.horizontalAdvance(e.word) / 2, fm.ascent() / 2 - 6),
                     self.pow_font, e.word)
        pen = QPen(QColor("#111"), 7)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(path, pen)
        p.fillPath(path, QColor("#ff3b2f"))
        p.restore()

    def paint_hole(self, p, e):
        age = e.age()
        p.save()
        p.translate(e.x, e.y)
        p.setOpacity(max(0.0, min(1.0, (HOLE_MS - age) / HOLE_FADE_MS)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, color in ((2.6, QColor(0, 0, 0, 130)), (1.1, QColor(255, 255, 255, 235))):
            p.setPen(QPen(color, width))
            for ray in e.rays:
                p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in ray]))
            for a, m, b in e.web:
                p.drawPolyline(QPolygonF([QPointF(*a), QPointF(*m), QPointF(*b)]))
        # сколотый светлый край вокруг дырки
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(QColor(235, 240, 245, 220))
        p.drawPolygon(QPolygonF([QPointF(math.cos(a) * r, math.sin(a) * r) for a, r in e.chips]))
        glow = QRadialGradient(0, 0, 8)
        glow.setColorAt(0, QColor(0, 0, 0, 255))
        glow.setColorAt(0.7, QColor(25, 25, 25, 255))
        glow.setColorAt(1, QColor(80, 80, 80, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(0, 0), 7.5, 7.5)
        p.restore()

    def open_cam(self, name):
        from multiprocessing import shared_memory
        try:
            self.cam_shm = shared_memory.SharedMemory(name=name, track=False)
            self.cam_seq = -1
        except (FileNotFoundError, OSError):
            self.cam_shm = None

    def poll_cam(self):
        """Свежий кадр камеры из общей памяти (если главная программа положила новый)."""
        if not self.cam_shm:
            return
        seq, x, y, w, h, ghosts = struct.unpack_from("<qiiiii", self.cam_shm.buf, 0)
        if seq == self.cam_seq or w <= 0 or h <= 0:
            return
        self.cam_seq = seq
        self.cam_ghosts = [struct.unpack_from("<iii", self.cam_shm.buf, 28 + 12 * k)
                           for k in range(min(ghosts, 8))]
        data = bytes(self.cam_shm.buf[CAM_HEADER:CAM_HEADER + w * h * 3])
        self.cam_img = QImage(data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        self.cam_rect = QRectF(x, y, w, h)
        now = time.monotonic()
        if now < self.clones_until + max(CLONE_DELAYS):        # копии для «эха» — мельче
            small = self.cam_img.scaled(int(w * CLONE_SCALE), int(h * CLONE_SCALE),
                                        Qt.AspectRatioMode.IgnoreAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
            self.cam_history.append((now, small))
        self.update()

    def tinted(self, img, keep):
        """Копия кадра только в одном канале — для красно-голубого двоения."""
        out = QImage(img.size(), QImage.Format.Format_ARGB32)
        out.fill(0)
        p = QPainter(out)
        p.drawImage(0, 0, img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        p.fillRect(out.rect(), keep)
        p.end()
        return out

    def paint_cam(self, p, now_ms):
        """Камера: копии-«эхо» на дропе, красно-голубое двоение на сильном ударе."""
        rect, img = self.cam_rect, self.cam_img
        now = time.monotonic()
        if now < self.clones_until and self.cam_history:
            for k, delay in enumerate(CLONE_DELAYS):
                old = min(self.cam_history, key=lambda f: abs(now - delay - f[0]))[1]
                side = -1 if k % 2 == 0 else 1
                row = k // 2
                x = rect.center().x() + side * (rect.width() / 2 + old.width() * 0.62)
                y = rect.center().y() + (row - 0.5) * old.height() * 1.15
                p.setOpacity(0.85 - 0.12 * k)
                p.drawImage(QRectF(x - old.width() / 2, y - old.height() / 2,
                                   old.width(), old.height()), old)
            p.setOpacity(1.0)
        if now_ms < self.glitch_until:                    # двоение красный/голубой
            for dx, keep in ((-CAM_GLITCH_PX, QColor(255, 60, 60)),
                             (CAM_GLITCH_PX, QColor(60, 255, 255))):
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                p.drawImage(rect.translated(dx, 0), self.tinted(img, keep))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setOpacity(0.75)
        for gx, gy, alpha in self.cam_ghosts:             # след: где окно было чуть раньше
            p.setOpacity(max(0.0, min(1.0, alpha / 255)))
            p.drawImage(QRectF(gx, gy, rect.width(), rect.height()), img)
        p.setOpacity(1.0 if now_ms >= self.glitch_until else 0.8)
        p.drawImage(rect, img)
        p.setOpacity(1.0)
        p.setPen(QPen(BOX_BORDER, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)

    def paint_eq(self, p):
        """Полоски по низу экрана прыгают по басу песни."""
        fade = max(0.0, 1 - (time.monotonic() - self.eq_at) / EQ_FADE)
        if not self.eq or fade <= 0:
            return
        w, h = self.width(), self.height()
        n = len(self.eq) * 2 - 1                          # зеркалим от центра
        levels = list(reversed(self.eq[1:])) + self.eq
        bar = w / n
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(levels):
            p.setOpacity(fade * (0.35 + 0.65 * v))
            p.setBrush(EQ_COLOR)
            height = 8 + EQ_HEIGHT * v
            p.drawRoundedRect(QRectF(i * bar + bar * 0.14, h - height - 16,
                                     bar * 0.72, height), 5, 5)
        p.setOpacity(1.0)

    def layout(self, card):
        """[(строка, x, y базовой линии)] — как текст карточки лежит на экране."""
        full = self.wrap(card.text, card.w - 40)
        line_h = self.metrics.height() * LINE_GAP
        top = card.y + (card.h - line_h * len(full)) / 2 + self.metrics.ascent()
        return [(line, card.x + (card.w - self.metrics.horizontalAdvance(line)) / 2,
                 top + n * line_h) for n, line in enumerate(full)]

    def shatter_near(self, x, y):
        """Выстрел рядом со строкой — она разлетается на буквы."""
        best, best_d = None, SHATTER_RADIUS
        for card in self.cards.values():
            if card.leaving_at is not None:
                continue
            cx = min(max(x, card.x), card.x + card.w)
            cy = min(max(y, card.y + card.h * 0.3), card.y + card.h * 0.7)
            d = math.hypot(x - cx, y - cy)
            if d < best_d:
                best, best_d = card, d
        if best is None:
            return None
        rnd = random.Random()
        e = Effect("shatter", x, y)
        e.box = best.box
        e.letters = []
        shown = best.i
        for line, lx, ly in self.layout(best):
            for k, ch in enumerate(line):
                if shown <= 0:
                    break
                shown -= 1
                if ch == " ":
                    continue
                gx = lx + self.metrics.horizontalAdvance(line[:k])
                gy = ly - self.metrics.ascent() / 2
                dx, dy = gx - x, gy - y
                n = math.hypot(dx, dy) or 1
                speed = rnd.uniform(250, 750) * (1.3 - min(1, n / 300))
                e.letters.append([ch, gx, ly, dx / n * speed + rnd.uniform(-80, 80),
                                  dy / n * speed - rnd.uniform(150, 400), rnd.uniform(-540, 540)])
            shown -= 1                                   # пробел-перенос между строками
        self.effects.append(e)
        del self.cards[best.id]
        return best.id

    def paint_shatter(self, p, e, pen):
        t = e.age() / 1000
        if t < RING_MS / 1000:                           # ударная волна от попадания
            k = t / (RING_MS / 1000)
            r = 12 + 150 * (1 - (1 - k) ** 3)
            p.save()
            p.setOpacity(0.9 * (1 - k))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#ffffff"), 3 * (1 - k) + 1))
            p.drawEllipse(QPointF(e.x, e.y), r, r)
            p.restore()
        p.save()
        p.setOpacity(max(0.0, min(1.0, (SHATTER_MS / 1000 - t) / 0.5)))
        for ch, x, y, vx, vy, spin in e.letters:
            p.save()
            p.translate(x + vx * t, y + vy * t + 900 * t * t / 2)       # гравитация
            p.rotate(spin * t)
            path = QPainterPath()
            path.addText(QPointF(0, 0), self.font, ch)
            if e.box:
                p.fillPath(path, BOX_TEXT)
            else:
                p.strokePath(path, pen)
                p.fillPath(path, TEXT_COLOR)
            p.restore()
        p.restore()

    def paint_center(self, p, e):
        """Строка крупно по центру: слова появляются по очереди и «выпрыгивают»."""
        age = e.age()
        fm = QFontMetricsF(self.center_font)
        space = fm.horizontalAdvance(" ")
        rows, row, width = [], [], 0.0
        limit = self.width() * CENTER_WIDTH
        for w, at in e.words:                                    # перенос по ширине
            ww = fm.horizontalAdvance(w)
            if row and width + space + ww > limit:
                rows.append((row, width))
                row, width = [], 0.0
            width += (space if row else 0) + ww
            row.append((w, at, ww))
        if row:
            rows.append((row, width))
        line_h = fm.height() * 1.05
        top = e.y - line_h * (len(rows) - 1) / 2
        fade = max(0.0, min(1.0, (e.life - age) / 250))
        pen = QPen(QColor("#000"), 9)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        for n, (row, width) in enumerate(rows):
            x = e.x - width / 2
            for w, at, ww in row:
                t = age - at
                if t >= 0:
                    pop = min(1.0, t / 110)
                    scale = 0.6 + 0.55 * pop - 0.15 * max(0.0, min(1.0, (t - 110) / 90))
                    path = QPainterPath()
                    path.addText(QPointF(-ww / 2, fm.ascent() / 2 - 6), self.center_font, w)
                    p.save()
                    p.translate(x + ww / 2, top + n * line_h)
                    p.scale(scale, scale)
                    p.setOpacity(fade * min(1.0, t / 80))
                    p.strokePath(path, pen)
                    p.fillPath(path, QColor("#ffffff"))
                    p.restore()
                x += ww + space

    def paint_side(self, p, e):
        """Слово вертикально вдоль края: выезжает от края, держится и гаснет."""
        t = e.age()
        slide = max(0.0, 1 - t / 220) ** 3                      # въезд ~0.2 с
        fade = max(0.0, min(1.0, (SIDE_MS - t) / 350))
        fm = QFontMetricsF(self.side_font)
        path = QPainterPath()
        path.addText(QPointF(0, 0), self.side_font, e.word)
        box = path.boundingRect()
        path.translate(-box.center())                 # центр — по самим буквам (зазор ровный)
        # вдоль бока окна камеры (и вместе с ним трясётся/качается); без камеры — у края экрана
        cam = self.cam_rect
        if cam is not None:
            height = cam.height()
            size = min(1.0, height * 0.95 / max(1.0, box.width()))
            half = box.height() * size / 2 + SIDE_GAP_PX
            x = cam.left() - half if e.left else cam.right() + half
            y = cam.center().y()
        else:
            height, x, y = self.height() * 0.85, e.x, e.y
        p.save()
        p.translate(x + (-1 if e.left else 1) * 160 * slide, y)
        p.rotate(-90 if e.left else 90)                         # слева — снизу вверх
        fit = height / max(1.0, box.width())
        if fit < 1:                                              # длинное слово — мельче
            p.scale(fit, fit)
        p.setOpacity(fade)
        pen = QPen(QColor("#000"), 10)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(path, pen)
        p.fillPath(path, QColor("#ffffff"))
        p.restore()

    def paint_word(self, p, e):
        t = e.age() / WORD_MS
        if getattr(e, "depth", False):                     # из глубины: точка → на весь экран
            grow = min(1.0, t / 0.17)
            scale = 0.05 + 1.35 * grow ** 0.45 - 0.2 * max(0.0, min(1.0, (t - 0.17) / 0.1))
        else:
            pop = min(1.0, t / 0.12)
            scale = 0.3 + 0.95 * pop - 0.15 * max(0.0, min(1.0, (t - 0.12) / 0.1))
        fm = QFontMetricsF(self.word_font)
        path = QPainterPath()
        path.addText(QPointF(-fm.horizontalAdvance(e.word) / 2, fm.ascent() / 2 - 10),
                     self.word_font, e.word)
        p.save()
        p.translate(e.x, e.y)
        p.scale(scale, scale)
        p.setOpacity(max(0.0, 1 - max(0.0, t - 0.65) / 0.35))
        if t < 0.15:                                  # на входе — глитч красным/голубым
            for dx, color in ((-8, QColor(255, 40, 70, 200)), (8, QColor(40, 220, 255, 200))):
                p.save()
                p.translate(dx, 0)
                p.fillPath(path, color)
                p.restore()
        pen = QPen(QColor("#000"), 12)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(path, pen)
        p.fillPath(path, QColor("#ffffff"))
        p.restore()

    def glitching(self, card):
        now = time.monotonic() * 1000
        new = card.alpha(self.clock) < 1 or (self.clock - card.born) < GLITCH_NEW_MS
        return now < self.glitch_until or new

    def paint_glitch(self, p, card, path, pen):
        """Сломанный экран: копии красным и голубым в стороны + сдвинутые полосы."""
        rnd = random.Random()
        shift = GLITCH_SHIFT * rnd.uniform(0.6, 1.6)
        for dx, color in ((-shift, QColor(255, 30, 70, 210)), (shift, QColor(30, 230, 255, 210))):
            p.save()
            p.translate(dx, rnd.uniform(-1.5, 1.5))
            p.fillPath(path, color)
            p.restore()
        bands = [(card.y + card.h * rnd.uniform(0.2, 0.8), rnd.uniform(4, 14))
                 for _ in range(rnd.randint(2, 3))]
        top = card.y
        for by, bh in sorted(bands) + [(card.y + card.h, 0)]:
            p.save()
            p.setClipRect(QRectF(card.x - 40, top, card.w + 80, max(0.0, by - top)))
            self.paint_text(p, card, path, pen)
            p.restore()
            p.save()
            p.setClipRect(QRectF(card.x - 40, by, card.w + 80, bh))
            p.translate(rnd.uniform(-14, 14), 0)
            self.paint_text(p, card, path, pen)
            p.restore()
            top = by + bh

    def paint_text(self, p, card, path, pen):
        if card.box:
            p.fillPath(path, BOX_TEXT)
        else:
            p.strokePath(path, pen)
            p.fillPath(path, TEXT_COLOR)

    def paintEvent(self, _event):
        if not self.cards and not self.effects and self.cam_img is None and not self.eq:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.cam_img is not None:                     # камера — самым нижним слоем
            self.paint_cam(p, time.monotonic() * 1000)
        self.paint_eq(p)
        for e in self.effects:
            if e.kind == "hole":
                self.paint_hole(p, e)
        pen = QPen(OUTLINE_COLOR, OUTLINE_PX)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        blink_on = int(self.clock // BLINK_MS) % 2 == 0
        for card in self.cards.values():
            kick = card.offset(self.clock)
            if kick != (0.0, 0.0):
                p.save()
                p.translate(*kick)
            shown = card.text[:card.i]
            typing = card.i < len(card.text)
            cursor = CURSOR if typing or blink_on else ""
            # раскладка по строкам — по полному тексту, чтобы строки не прыгали при печати
            path = QPainterPath()
            left = len(shown)
            lines = self.layout(card)
            for n, (line, x, y) in enumerate(lines):
                part = line[:max(0, left)]
                left -= len(line) + 1
                is_last = left < 0 or n == len(lines) - 1
                if is_last:
                    part += cursor
                path.addText(QPointF(x, y), self.font, part)
                if is_last:
                    break
            p.setOpacity(card.alpha(self.clock))
            if card.box:
                p.setPen(QPen(BOX_BORDER, 1))
                p.setBrush(BOX_COLOR)
                p.drawRoundedRect(QRectF(card.x, card.y, card.w, card.h), BOX_RADIUS, BOX_RADIUS)
            if self.glitching(card):
                self.paint_glitch(p, card, path, pen)
            else:
                self.paint_text(p, card, path, pen)
            if kick != (0.0, 0.0):
                p.restore()
        p.setOpacity(1.0)
        for e in self.effects:                          # поверх текста
            if e.kind == "shatter":
                self.paint_shatter(p, e, pen)
            elif e.kind == "word":
                self.paint_word(p, e)
            elif e.kind == "center":
                self.paint_center(p, e)
            elif e.kind == "side":
                self.paint_side(p, e)
            elif e.kind == "pow":
                self.paint_pow(p, e)
        for e in self.effects:
            if e.kind == "flash":
                p.setOpacity(0.6 * max(0.0, 1 - e.age() / FLASH_MS))
                p.fillRect(self.rect(), QColor("#ffffff"))
        p.end()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    overlay = Overlay()                   # noqa: F841 — держим ссылку, пока работает цикл
    print("ready", flush=True)
    app.exec()


if __name__ == "__main__":
    main()
