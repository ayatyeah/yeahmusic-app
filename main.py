"""Строчки песни печатаются в отдельных всплывающих окнах синхронно с треком.

    python main.py                 # библиотека треков: новый трек, текст, тайминги, показ
    python main.py --offset -0.3   # при показе сдвинуть все тайминги (сек)
    python main.py test            # прогнать все тесты (test_main.py)

Каждый трек живёт в своей папке tracks/<название>/: песня + lyrics.txt.
lyrics.txt: одна строка = одно окно, тайминг в начале — "[01:23.45] текст".
"""
import argparse
import difflib
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np
import pygame

import ai
import beats
import camfx
import hands

ROOT = Path(__file__).parent
TRACKS = ROOT / "tracks"
AUDIO_EXT = (".mp3", ".ogg", ".wav", ".flac")

# --- внешний вид карточек ---
CARDS_OVERLAY = True   # строки без фона, текст с обводкой (overlay.py, нужен Qt);
                       # False — серые карточки-окошки, как раньше
BOX_W, BOX_H = 380, 230
BG, FG, BORDER = "#ececec", "#141414", "#bdbdbd"
FONT = ("Inter", 22)
CURSOR = "|"
MAX_CARDS = 4          # сколько окон одновременно на экране
RISE_PX = 0.7          # на сколько пикселей поднимаются окна за тик
TICK_MS = 16           # ~60 fps
FADE_MS = 250          # плавное появление / исчезание
CHAR_MS = 85           # обычная скорость печати, мс на букву (при скорости 1.0×)
MIN_CHAR_MS = 45       # быстрее этого не печатаем, даже если строка короткая по времени
LONG_LINE = 60         # «Разбить длинные строки» режет всё, что длиннее

# --- камера (окно по центру экрана во время показа) ---
CAM_DEVICE = None      # None — первая найденная, или например "/dev/video1"
CAM_RES = (640, 480)   # запасной режим (через pygame), если HD не получилось
CAM_HD = (1280, 720)   # основной: HD в MJPEG через OpenCV — чётко, как в «Камере»
# по умолчанию (и в «Проверить камеру») — широкий кадр, как в «Камере» GNOME;
# у песни форма может меняться по строкам (пометка «кадр» в таймингах, camfx.SHAPES)
CAM_ASPECT, CAM_HEIGHT = camfx.SHAPES["wide"][:2]
CAM_MIRROR = True      # зеркалить, как селфи-камера
SHAPE_MS = 650         # как плавно окно меняет форму
CAM_HEADER = 160       # байт в начале общей памяти: номер кадра, x, y, w, h и след
CAM_GHOSTS = 4         # сколько полупрозрачных копий тянется за окном при взмахе
JUMP_PX = 170          # на сколько окно улетает вверх/вниз по взмаху
JUMP_MS = 700

# --- жесты перед камерой ---
SPIN_MS = 900          # оборот / сальто окна
PULSE_MS = 450         # «удар»: окно подпрыгивает
SHAKE_MS = 650         # тряска
SEEK_STEP = 5          # на сколько секунд мотает взмах на паузе
RECOIL_MS = 260        # отдача окна при выстреле из «пистолетика»
BOOM_MS = 340          # «бум» под бит: окно резко увеличивается и пружинит назад
BOOM_SCALE = 0.13      # на сколько увеличивается на самом сильном ударе
BOOM_SWAY = 16         # px: особый момент — окно на ударе качается влево-вправо
SHAKE_HARD = 2.5       # особый момент: на «У-у» — тряска во столько раз сильнее
SHAKE_HARD_MS = 900
SIDE_GAP = 2.5         # сек: слово вдоль края экрана — не чаще
BOOM_LEAD = 0.03       # запускаем чуть раньше бита — пик увеличения точно на ударе
GLITCH_STRENGTH = 0.85 # в припеве удар сильнее этого — строки «ломаются» глитчем
PUSH_STRENGTH = 0.5    # удар сильнее этого — строки разлетаются от удара
EQ_EVERY = 0.07        # как часто слать слою громкость баса (эквалайзер)
CLONES_MS = 1500       # дроп припева: сколько держатся копии кадра вокруг окна
BOOM_MODES = {"always": "вся песня", "chorus": "только припев", "off": "выкл"}
FLASH_MS = 200         # вспышка у пальца
AIM_CONE = 30          # град: строка в этом конусе от ствола — пуля летит в неё
SWIPE_PIXEL_DIFF = 30  # насколько должен измениться пиксель, чтобы считаться «движением»
SWIPE_MIN_AREA = 0.04  # доля кадра в движении: меньше — шум или просто шевелишься
SWIPE_MAX_AREA = 0.45  # больше — это не рука, а смена света / тряска камеры
SWIPE_DISTANCE = 0.45  # на какую долю ширины кадра должна пройти рука
SWIPE_DISTANCE_V = 0.25 # то же для взмаха вверх/вниз: вверх-вниз кадр короче, порог ниже
SWIPE_WINDOW = 0.7     # ...и за сколько секунд
SWIPE_COOLDOWN = 1.5   # пауза после взмаха, чтобы один взмах не сработал дважды
WAVE_WINDOW = 1.4      # помахать: за столько секунд...
WAVE_TURNS = 3         # ...рука должна сменить направление столько раз
WAVE_STEP = 0.12       # и каждый мах — хотя бы на такую долю ширины
COVER_DARK = 0.35      # ладонь на камере: кадр темнее обычного в ~3 раза...
COVER_HOLD = 0.35      # ...дольше стольких секунд
CAM_WARMUP = 1.5       # первые секунды камера подстраивает яркость — жесты не ловим

# --- оформление оболочки ---
UI_BG, UI_FG, UI_DIM = "#101113", "#e6e6e6", "#8a8f98"
UI_PANEL, UI_STRIPE, UI_LINE = "#181a1e", "#1d2025", "#2a2e35"
UI_ACCENT, UI_ACCENT_HI = "#5b6cff", "#7482ff"
UI_FONT = "Inter"
SETTINGS = ROOT / "settings.json"

TIME_RE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$")
TAG_RE = re.compile(r"^\s*[\[(].*[\])]\s*$")   # [Припев], (Куплет 1) и т.п.


# ---------- тайминги и текст ----------

def fmt_time(t):
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def parse_time(s):
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d+(?:[.,]\d+)?)\s*", s)
    if not m:
        return None
    return int(m[1] or 0) * 60 + float(m[2].replace(",", "."))


def read_entries(path):
    """[[секунды | None, текст], ...] из lyrics.txt (пустой список, если текста нет)."""
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = TIME_RE.match(line)
        out.append([int(m[1]) * 60 + float(m[2]), m[3]] if m else [None, line])
    return out


def write_entries(path, entries):
    lines = [f"[{fmt_time(t)}] {s}" if t is not None else s for t, s in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def carry_timings(old, new_lines):
    """Новый текст → entries, сохраняя тайминги строк, которые не поменялись
    (и строк, где просто поправили опечатку, если их число в куске совпало)."""
    new = [[None, s] for s in new_lines]
    sm = difflib.SequenceMatcher(a=[s for _, s in old], b=new_lines, autojunk=False)
    for tag, a0, a1, b0, b1 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and a1 - a0 == b1 - b0):
            for k in range(b1 - b0):
                new[b0 + k][0] = old[a0 + k][0]
    return new


def fill_timing(entries, length):
    """Строки без тайминга равномерно распределяем между размеченными соседями."""
    known = [(-1, 0.0)] + [(i, t) for i, (t, _) in enumerate(entries) if t is not None]
    known.append((len(entries), length))
    out = [t for t, _ in entries]
    for (i0, t0), (i1, t1) in zip(known, known[1:]):
        for i in range(i0 + 1, i1):
            out[i] = t0 + (t1 - t0) * (i - i0) / (i1 - i0)
    return [(t, text) for t, (_, text) in zip(out, entries)]


def clean_lines(text, drop_tags=False):
    out = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line and not (drop_tags and TAG_RE.match(line)):
            out.append(line)
    return out


def split_long(line, limit=LONG_LINE):
    """Режет длинную строку пополам — по знаку препинания у середины, иначе по пробелу."""
    if len(line) <= limit:
        return [line]
    n = len(line)
    cuts = [i + 1 for i, c in enumerate(line) if c in ",;:—" and n / 4 < i < n * 3 / 4]
    cuts = cuts or [i for i, c in enumerate(line) if c == " "]
    if not cuts:
        return [line]
    i = min(cuts, key=lambda i: abs(i - n / 2))
    a, b = line[:i].strip(" ,"), line[i:].strip(" ,")
    if not a or not b:
        return [line]
    return split_long(a, limit) + split_long(b, limit)


# ---------- треки ----------

class Track:
    def __init__(self, folder):
        self.dir = folder
        self.name = folder.name
        self.lyrics = folder / "lyrics.txt"

    @property
    def audio(self):
        files = sorted(p for p in self.dir.iterdir() if p.suffix.lower() in AUDIO_EXT)
        return files[0] if files else None

    def entries(self):
        entries = read_entries(self.lyrics)
        marked = []
        for e in entries:                          # раньше сцены помечали 🎬 в тексте —
            e[1], old = camfx.strip_old_mark(e[1])  # переносим пометку в scenes.json
            if old:
                marked.append(e[1])
        if marked:
            self.save_scenes(self.scenes() | set(marked))
            self.save(entries)
        return entries

    def scenes(self):
        """Строки-сцены (крупно по центру, камера-телевизор) — хранятся в scenes.json."""
        return camfx.load_scenes(self.dir)

    def save_scenes(self, lines):
        camfx.save_scenes(self.dir, lines)

    def frames(self):
        """{строка: форма окна камеры} — хранится в frames.json."""
        return camfx.load_frames(self.dir)

    def keywords(self):
        """{строка: слово крупно} — что выбрал ИИ-режиссёр (keywords.json)."""
        return camfx.load_keywords(self.dir)

    def save_keywords(self, words):
        camfx.save_keywords(self.dir, words)

    def save_frames(self, frames):
        camfx.save_frames(self.dir, frames)

    def save(self, entries):
        write_entries(self.lyrics, entries)


def list_tracks():
    TRACKS.mkdir(exist_ok=True)
    return [Track(d) for d in sorted(TRACKS.iterdir(), key=lambda d: d.name.lower())
            if d.is_dir()]


def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|]+', " ", name).strip(" .")
    return re.sub(r"\s+", " ", name) or "трек"


def free_folder(name):
    folder, n = TRACKS / name, 2
    while folder.exists():
        folder, n = TRACKS / f"{name} {n}", n + 1
    return folder


def add_track(src, name=None, move=False):
    """Песня → новый трек tracks/<название>/. Вернёт Track."""
    folder = free_folder(safe_name(name or src.stem))
    folder.mkdir(parents=True)
    (shutil.move if move else shutil.copy2)(str(src), folder / src.name)
    return Track(folder)


def import_loose_audio():
    """Песни, просто скинутые в папку программы, сами становятся треками
    (а заодно переносится старая раскладка, когда песня и lyrics.txt лежали в корне)."""
    added = []
    for audio in sorted(p for p in ROOT.iterdir() if p.suffix.lower() in AUDIO_EXT):
        tr = add_track(audio, move=True)
        lyrics = ROOT / "lyrics.txt"
        if lyrics.exists() and not added:          # старая раскладка — текст к первой песне
            shutil.move(str(lyrics), tr.lyrics)
        added.append(tr)
        print(f"Добавлен трек: {tr.name}")
    return added


def load_settings():
    data = {}
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"speed": 1.0, "camera": True, "swipe": True, "hands": True, "card_bg": False,
            "boom": "always", "cam_device": None, **data}


def save_settings(data):
    SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- аудио ----------

_base = 0.0   # с какой секунды трек запущен последний раз (get_pos считает от неё)
_loaded = None
_lengths = {}


def start_audio(path, at=0.0):
    global _base, _loaded
    if _loaded != path:
        pygame.mixer.music.load(str(path))
        _loaded = path
    pygame.mixer.music.play(start=at)
    _base = at


def stop_audio():
    pygame.mixer.music.stop()


def song_time():
    """Текущая позиция трека в секундах (часы для всей синхронизации)."""
    return _base + max(pygame.mixer.music.get_pos(), 0) / 1000


def track_length(path):
    if path not in _lengths:
        _lengths[path] = pygame.mixer.Sound(str(path)).get_length()
    return _lengths[path]


# ---------- общее для окон ----------

# На русской раскладке Tk не узнаёт Ctrl+V/C/X/A/Z — подставляем по keysym
RU_CTRL = {"Cyrillic_em": "<<Paste>>", "Cyrillic_es": "<<Copy>>", "Cyrillic_che": "<<Cut>>",
           "Cyrillic_ya": "<<Undo>>", "Cyrillic_ef": "<<SelectAll>>"}


def setup_ui(root):
    root.configure(bg=UI_BG)
    root.option_add("*Font", (UI_FONT, 11))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=UI_BG, foreground=UI_FG, font=(UI_FONT, 11),
                    bordercolor=UI_LINE, lightcolor=UI_LINE, darkcolor=UI_LINE,
                    troughcolor=UI_PANEL, focuscolor=UI_ACCENT)
    style.configure("TButton", background="#23262c", foreground=UI_FG, padding=(12, 7),
                    borderwidth=0, relief="flat", font=(UI_FONT, 11))
    style.map("TButton", background=[("disabled", "#16181b"), ("pressed", "#1c1f24"),
                                     ("active", "#2e323a")],
              foreground=[("disabled", "#4a4f57")])
    style.configure("Accent.TButton", background=UI_ACCENT, foreground="#fff",
                    font=(UI_FONT, 11, "bold"))
    style.map("Accent.TButton", background=[("pressed", "#4a59e0"), ("active", UI_ACCENT_HI)])
    style.configure("Treeview", background=UI_PANEL, fieldbackground=UI_PANEL,
                    foreground=UI_FG, rowheight=34, font=(UI_FONT, 12), borderwidth=0)
    style.configure("Treeview.Heading", background=UI_BG, foreground=UI_DIM, relief="flat",
                    font=(UI_FONT, 10, "bold"), padding=(6, 6))
    style.map("Treeview.Heading", background=[("active", UI_BG)])
    style.map("Treeview", background=[("selected", UI_ACCENT)], foreground=[("selected", "#fff")])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Vertical.TScrollbar", background="#23262c", troughcolor=UI_PANEL,
                    borderwidth=0, arrowsize=0, relief="flat")
    style.map("Vertical.TScrollbar", background=[("active", "#2e323a")])
    style.configure("Horizontal.TScale", background=UI_ACCENT, troughcolor="#23262c",
                    borderwidth=0, sliderthickness=14)

    def ru_ctrl(e):
        ev = RU_CTRL.get(e.keysym)
        if ev == "<<SelectAll>>" and isinstance(e.widget, tk.Text):
            e.widget.tag_add("sel", "1.0", "end")
            return "break"
        if ev:
            e.widget.event_generate(ev)
            return "break"
    for cls in ("Text", "Entry", "TEntry"):
        root.bind_class(cls, "<Control-KeyPress>", ru_ctrl, add="+")


def button(parent, text, cmd, accent=False):
    b = ttk.Button(parent, text=text, command=cmd, takefocus=False,
                   style="Accent.TButton" if accent else "TButton")
    b.pack(side="left", padx=3)
    return b


def slider(parent, var, to, length=None, command=None, from_=0.0, on_press=None):
    """Плоский ползунок в стиле оболочки (ttk-шный в clam выглядит криво)."""
    opts = dict(variable=var, from_=from_, to=to, orient="horizontal", resolution=0.01,
                showvalue=False, bd=0, relief="flat", highlightthickness=0,
                bg=UI_ACCENT, activebackground=UI_ACCENT_HI, troughcolor="#23262c",
                width=10, sliderlength=22, sliderrelief="flat", takefocus=False)
    if length:
        opts["length"] = length
    if command:
        opts["command"] = command
    s = tk.Scale(parent, **opts)

    def jump(e):   # клик сразу ставит ползунок в точку, а не двигает на шаг
        if on_press:
            on_press()
        var.set(float(s.tk.call(s, "get", e.x, e.y)))
        if command:
            command(var.get())
        return "break"
    s.bind("<Button-1>", jump, add="+")
    s.bind("<B1-Motion>", jump, add="+")
    return s


def row(parent, **pack):
    f = tk.Frame(parent, bg=UI_BG)
    f.pack(**{"fill": "x", "padx": 10, "pady": 4, **pack})
    return f


def bind_ctrl(win, letter, ru_keysym, fn):
    """Ctrl+буква, работающий и на английской, и на русской раскладке."""
    for key in (f"<Control-{letter}>", f"<Control-{letter.upper()}>", f"<Control-{ru_keysym}>"):
        win.bind(key, lambda e: (fn(), "break")[1])


# ---------- карточка ----------

def char_ms(text, duration, speed):
    """Мс на букву: обычная скорость, но строка должна успеть допечататься за ~85%
    своего времени; ускоряемся ради этого не дальше MIN_CHAR_MS (ползунок — может)."""
    base = CHAR_MS / speed
    fit = duration * 850 / max(len(text), 1)
    return int(max(min(MIN_CHAR_MS, base), min(base, fit)))


class Overlay:
    """Прозрачный слой (overlay.py, Qt) — строки без фона, только текст с обводкой.
    Отдельный процесс; команды — JSON-строками в его stdin."""

    def __init__(self):
        self.log = open(ROOT / "overlay.log", "w")      # ошибки слоя — сюда
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "overlay.py")], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log,
            env={**os.environ,
                 "QT_QPA_PLATFORM": os.environ.get("YEAHMUSIC_QT_PLATFORM", "xcb")})
        self.next_id = 0

    def alive(self):
        return self.proc.poll() is None

    def send(self, **cmd):
        if not self.alive():
            return False
        try:
            self.proc.stdin.write((json.dumps(cmd, ensure_ascii=False) + "\n").encode())
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            return False

    def close(self):
        self.send(cmd="quit")
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        for pipe in (self.proc.stdin, self.proc.stdout, self.log):
            if pipe:
                pipe.close()


def start_overlay():
    """Запускает слой; None, если Qt нет или слой не поднялся (тогда — обычные карточки)."""
    if not CARDS_OVERLAY or not (ROOT / "overlay.py").exists():
        return None
    try:
        import PySide6  # noqa: F401 — только проверить, что Qt установлен
    except ImportError:
        return None
    ov = Overlay()
    return ov if ov.alive() else None


class OverlayCard:
    """Карточка на прозрачном слое. Тот же интерфейс, что у LyricCard, чтобы Player
    мог работать с любой: печать и анимацию делает сам слой, тут — только позиция."""

    def __init__(self, overlay, text, x, y, duration, speed, box=False):
        self.overlay = overlay
        self.text = text
        self.x, self.y = float(x), float(y)
        self.char_ms = char_ms(text, duration, speed)
        overlay.next_id += 1
        self.id = overlay.next_id
        overlay.send(cmd="add", id=self.id, text=text, x=int(x), y=int(y), w=BOX_W, h=BOX_H,
                     char_ms=self.char_ms, box=box)

    def rise(self, dy):
        self.y -= dy

    def offscreen(self):
        return self.y + BOX_H < 0

    def close(self, instant=False):
        self.overlay.send(cmd="remove", id=self.id)


class LyricCard:
    def __init__(self, root, text, x, y, duration, speed, on_escape, frozen=lambda: False):
        self.text = text
        self.frozen = frozen
        self.i = 0
        self.x, self.y = float(x), float(y)
        self.cursor_on = True
        self.closing = False
        self.char_ms = char_ms(text, duration, speed)

        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
        self._alpha(0.0)
        self._place()
        self.label = tk.Label(self.win, text="", bg=BG, fg=FG, font=FONT,
                              wraplength=BOX_W - 56, justify="center")
        self.label.place(relx=0.5, rely=0.5, anchor="center")
        self.win.bind("<Escape>", lambda e: on_escape())
        self._fade(0.0, 1.0)
        self._type()
        self._blink()

    def _alpha(self, a):
        try:
            self.win.attributes("-alpha", a)
        except tk.TclError:
            pass

    def _fade(self, a, target, then=None):
        if not self.win.winfo_exists():
            return
        step = TICK_MS / FADE_MS * (1 if target > a else -1)
        a = min(1.0, max(0.0, a + step))
        self._alpha(a)
        if a != target:
            self.root.after(TICK_MS, self._fade, a, target, then)
        elif then:
            then()

    def _show(self):
        cursor = CURSOR if self.cursor_on or self.i <= len(self.text) else " "
        self.label.config(text=self.text[:self.i] + cursor)

    def _place(self):
        self.win.geometry(f"{BOX_W}x{BOX_H}+{int(self.x)}+{int(self.y)}")

    def _type(self):
        if self.win.winfo_exists() and self.frozen():
            self.root.after(50, self._type)
        elif self.win.winfo_exists() and self.i <= len(self.text):
            self._show()
            self.i += 1
            # на пробелах и знаках препинания чуть задерживаемся — живее
            pause = 2.5 if self.text[self.i - 2:self.i - 1] in ",.!?…" else 1
            self.root.after(int(self.char_ms * pause), self._type)

    def _blink(self):
        if self.win.winfo_exists():
            self.cursor_on = not self.cursor_on
            if self.i > len(self.text):
                self._show()
            self.root.after(530, self._blink)

    def rise(self, dy):
        self.y -= dy
        self._place()

    def offscreen(self):
        return self.y + BOX_H < 0

    def close(self, instant=False):
        if instant:
            self.win.destroy()
        elif not self.closing:
            self.closing = True
            self._fade(1.0, 0.0, self.win.destroy)


# ---------- камера ----------

def list_camera_devices():
    """[(путь, название)] настоящих камер. У вебки обычно два узла /dev/videoN —
    второй служебный (index 1), его пропускаем. Айфон через DroidCam/Iriun тоже будет тут."""
    found = []
    for d in sorted(Path("/sys/class/video4linux").glob("video*"),
                    key=lambda d: int(d.name[5:] or 0)):
        try:
            if (d / "index").read_text().strip() != "0":
                continue
            name = (d / "name").read_text().strip().split(":")[0]
        except OSError:
            continue
        found.append((f"/dev/{d.name}", name))
    return found


class CvCamera:
    """Камера через OpenCV: 1280×720 в MJPEG — как в приложении «Камера», чётко.
    Кадры читаются в отдельном потоке (чтение ждёт камеру), отдаём последний свежий."""

    def __init__(self, device):
        import cv2
        self.cv2 = cv2
        self.cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            raise RuntimeError("не открылась")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_HD[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HD[1])
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        ok, frame = self.cap.read()
        if not ok:
            self.cap.release()
            raise RuntimeError("нет кадров")
        self.frame, self.fresh = frame, True
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while self.running:
            ok, frame = self.cap.read()
            if ok:
                with self.lock:
                    self.frame, self.fresh = frame, True
            else:
                time.sleep(0.01)

    def query_image(self):
        return self.fresh

    def get_image(self):
        with self.lock:
            frame, self.fresh = self.frame, False
        rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        return pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB")

    def stop(self):
        self.running = False
        self.thread.join(timeout=1)
        self.cap.release()


def camera_busy_by():
    """Кто держит камеру: [имена программ]. Linux отдаёт вебку только одной разом."""
    names = set()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc / "fd").iterdir():
                if fd.resolve().name.startswith("video"):
                    names.add((proc / "comm").read_text().strip())
                    break
        except OSError:
            continue
    return sorted(names)


def open_camera(device=None):
    """Запускает камеру (сначала в HD через OpenCV, иначе через pygame);
    RuntimeError с понятным текстом, если не вышло."""
    devices = [d for d, _ in list_camera_devices()]
    path = device if device in devices else (devices[0] if devices else None)
    if path:
        try:
            return CvCamera(path)
        except Exception:        # нет OpenCV / камера не умеет MJPEG — по-старому
            pass
    import pygame.camera
    pygame.camera.init()
    cams = pygame.camera.list_cameras()
    if not cams:
        raise RuntimeError("Камера не найдена")
    if device and device not in cams:
        device = None                       # выбранную отключили — берём первую
    try:
        cam = pygame.camera.Camera(device or CAM_DEVICE or cams[0], CAM_RES)
        cam.start()
    except (SystemError, OSError, ValueError) as e:
        busy = camera_busy_by()
        who = f"Камеру занял: {', '.join(busy)}. Закрой эту программу (или вкладку браузера)." \
            if busy else f"Камера не запускается.\n{e}"
        raise RuntimeError(who)
    return cam


class GestureDetector:
    """Жесты по движению в кадре (без нейросетей):
    left/right/up/down — быстрый взмах через кадр, wave — помахать туда-сюда,
    cover — закрыть камеру ладонью."""

    def __init__(self):
        self.prev = None
        self.track = []            # [(время, центр движения x, y — от 0 до 1), ...]
        self.cooldown_until = 0.0
        self.base = None           # обычная яркость кадра
        self.dark_since = None
        self.covered = False

    def fire(self, event, now, cooldown=SWIPE_COOLDOWN):
        self.track.clear()
        self.cooldown_until = now + cooldown
        return event

    def feed(self, gray, now):
        """gray — маленький ч/б кадр, массив [x, y]. Возвращает название жеста или None."""
        prev, self.prev = self.prev, gray
        event = self.check_cover(float(gray.mean()), now)
        if event or self.covered or self.dark_since is not None or prev is None:
            return event
        if now < self.cooldown_until:
            self.track.clear()
            return None
        moving = np.abs(gray - prev) > SWIPE_PIXEL_DIFF
        if SWIPE_MIN_AREA < moving.mean() < SWIPE_MAX_AREA:
            cols, rows = moving.sum(axis=1), moving.sum(axis=0)
            cx = float((cols * np.arange(len(cols))).sum() / cols.sum() / (len(cols) - 1))
            cy = float((rows * np.arange(len(rows))).sum() / rows.sum() / (len(rows) - 1))
            self.track.append((now, cx, cy))
        self.track = [p for p in self.track if now - p[0] <= WAVE_WINDOW]
        return self.check_swipe(now) or self.check_wave(now)

    def check_cover(self, light, now):
        if self.base is None:
            self.base = light
            return None
        if self.covered:
            if light > self.base * 0.6:            # ладонь убрали
                self.covered = False
                self.dark_since = None
                self.prev = None
                self.cooldown_until = now + 0.8     # уход руки — не взмах
            return None
        if light < self.base * COVER_DARK:
            if self.dark_since is None:
                self.dark_since = now
            elif now - self.dark_since >= COVER_HOLD:
                self.covered = True
                return self.fire("cover", now, 0)
            return None
        self.dark_since = None
        self.base = self.base * 0.95 + light * 0.05
        return None

    def check_swipe(self, now):
        pts = [p for p in self.track if now - p[0] <= SWIPE_WINDOW]
        if len(pts) < 4:
            return None
        xs, ys = [p[1] for p in pts], [p[2] for p in pts]
        dx, dy = xs[-1] - xs[0], ys[-1] - ys[0]
        # далеко и без сильных метаний туда-обратно
        if abs(dx) >= abs(dy):
            if abs(dx) >= SWIPE_DISTANCE and max(xs) - min(xs) <= abs(dx) * 1.3:
                return self.fire("right" if dx > 0 else "left", now)
        elif abs(dy) >= SWIPE_DISTANCE_V and max(ys) - min(ys) <= abs(dy) * 1.3:
            return self.fire("down" if dy > 0 else "up", now)
        return None

    def check_wave(self, now):
        """Считаем развороты руки: мах в одну сторону, потом в другую, и так WAVE_TURNS раз."""
        xs = [p[1] for p in self.track]
        if not xs:
            return None
        turns, direction, extreme = 0, 0, xs[0]
        for x in xs[1:]:
            if direction == 0:
                if abs(x - extreme) >= WAVE_STEP:
                    direction, extreme = (1 if x > extreme else -1), x
            elif (x - extreme) * direction > 0:          # продолжает в ту же сторону
                extreme = x
            elif abs(x - extreme) >= WAVE_STEP:          # развернулась
                turns += 1
                direction, extreme = -direction, x
        return self.fire("wave", now) if turns >= WAVE_TURNS else None


def small_gray(surf):
    """Кадр 64×48 в оттенках серого для поиска движения."""
    small = pygame.transform.scale(surf, (64, 48))
    return pygame.surfarray.array3d(small).mean(axis=2)


def ease(t):
    """Плавный разгон и торможение, t от 0 до 1."""
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


class CameraWindow:
    """Окно без рамки по центру экрана с живой картинкой с камеры.
    Жесты (или клавиши) крутят и трясут окно; жесты для песни уходят в on_gesture."""

    def __init__(self, root, on_escape=None, swipe=True, on_gesture=None, track_hands=False,
                 overlay=None, targets=None, device=None):
        self.root = root
        self.cam = open_camera(device)
        self.bleep_sound = None
        self.overlay = overlay
        self.chorus = None             # None — песни нет, False — куплет, True — припев
        self.targets = targets         # где сейчас строки — пуля летит в ближайшую
        self.zoom = camfx.Zoom()
        self.signs = hands.Gestures()
        self.outline_at = None         # когда вспыхнула обводка силуэта
        self.tracker = HandTracker() if track_hands else None
        self.gun = hands.GunDetector()
        self.hands = []                # руки в пикселях окна (последний результат)
        self.hands_version = -1
        self.flashes = []              # [(время выстрела, где, seed)] — вспышки у пальца
        self.shot_sound = None
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.h = int(sh * CAM_HEIGHT)
        self.w = int(self.h * CAM_ASPECT)
        self.x, self.y = (sw - self.w) // 2, (sh - self.h) // 2
        self.rect = (self.x, self.y, self.w, self.h)
        self.shape = None              # (откуда w,h, куда w,h, начало) — смена формы окна
        self.last = None               # последний кадр, уже подогнанный под окно
        self.detector = GestureDetector() if swipe else None
        self.started = time.monotonic()
        self.on_gesture = on_gesture
        self.anim = None               # (вид, направление, начало, длительность)
        self.anim_job = None
        self.badge = None

        self.badge = None              # (текст, до какого времени) — значок жеста на кадре
        self.ghosts = []               # где окно было чуть раньше (след при взмахе)
        # Камеру рисует прозрачный слой — тогда строки гарантированно поверх неё
        # (отдельное окно Wayland/GNOME может положить поверх текста). Без слоя — окно Tk.
        self.shm = None
        self.win = None
        if overlay and overlay.alive():
            from multiprocessing import shared_memory
            size = CAM_HEADER + root.winfo_screenwidth() * root.winfo_screenheight() * 3
            self.shm = shared_memory.SharedMemory(create=True, size=size)
            self.seq = 0
        else:
            self.win = tk.Toplevel(root)
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            self.win.configure(bg="#000", highlightthickness=1, highlightbackground=BORDER)
            self.photo = tk.PhotoImage(width=self.w, height=self.h)
            tk.Label(self.win, image=self.photo, bd=0, bg="#000").pack(fill="both", expand=True)
            if on_escape:
                self.win.bind("<Escape>", lambda e: on_escape())
        self.box = (self.x, self.y, self.w, self.h)     # где окно сейчас на экране
        self.place(self.w, self.h, self.x, self.y)
        self.job = root.after(10, self.update)

    def place(self, w, h, x, y):
        """Поставить «окно» камеры: w×h в точке (x, y)."""
        self.box = (int(x), int(y), int(w), int(h))
        if self.win:
            self.win.geometry(f"{int(w)}x{int(h)}+{int(x)}+{int(y)}")

    @property
    def spin_dir(self):
        return self.anim[1] if self.anim and self.anim[0] == "spin" else 0

    # --- кадр ---

    def prepare(self, surf):
        """Кадр с камеры → обрезка под пропорции окна, зеркало, размер окна."""
        fw, fh = surf.get_size()
        aspect = self.w / self.h
        if fw / fh > aspect:
            cw = int(fh * aspect)
            surf = surf.subsurface(((fw - cw) // 2, 0, cw, fh))
        else:
            ch = int(fw / aspect)
            surf = surf.subsurface((0, (fh - ch) // 2, fw, ch))
        if CAM_MIRROR:
            surf = pygame.transform.flip(surf, True, False)
        if hasattr(self, "zoom"):
            surf = self.zoom.apply(surf)             # в припеве — ближе к лицу
        return pygame.transform.smoothscale(surf, (self.w, self.h))

    def reshape(self, aspect, height):
        """Плавно поменять форму окна: aspect — ширина/высота, height — доля экрана."""
        sh = self.root.winfo_screenheight()
        h = int(sh * height)
        target = (int(h * aspect), h)
        if (self.shape and self.shape[1] == target) or (not self.shape and target == (self.w, self.h)):
            return
        self.shape = ((self.w, self.h), target, time.monotonic())

    def step_shape(self, now):
        if not self.shape:
            return
        (w0, h0), (w1, h1), start = self.shape
        t = min(1.0, (now - start) * 1000 / SHAPE_MS)
        k = ease(t)
        self.w, self.h = int(w0 + (w1 - w0) * k), int(h0 + (h1 - h0) * k)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.x, self.y = (sw - self.w) // 2, (sh - self.h) // 2
        self.rect = (self.x, self.y, self.w, self.h)
        if not self.anim:
            self.place(self.w, self.h, self.x, self.y)
        if t >= 1.0:
            self.shape = None

    def bleep(self):
        """Цензура «****»: пи-и-ип."""
        if self.bleep_sound is None:
            self.bleep_sound = camfx.bleep_sound()
        if self.bleep_sound:
            self.bleep_sound.play()

    def set_chorus(self, on):
        self.chorus = on
        self.zoom.set_chorus(bool(on))
        if self.tracker:               # силуэт ищем только когда он нужен
            self.tracker.segment = bool(on)
        if on:
            self.outline_at = time.monotonic()

    @staticmethod
    def to_ppm(surf):
        w, h = surf.get_size()
        return f"P6 {w} {h} 255 ".encode() + pygame.image.tobytes(surf, "RGB")

    def frame_ppm(self, surf):
        return self.to_ppm(self.prepare(surf))

    def show(self, surf):
        if self.badge and time.monotonic() < self.badge[1]:
            surf = surf.copy()
            self.draw_badge(surf, self.badge[0])
        w, h = surf.get_size()
        if self.shm:
            # кадр + заголовок (номер, где, размер) — в общую память; слой сам заберёт
            # свежий кадр в своём темпе, в канал команд кадры не идут — очередь не копится
            data = pygame.image.tobytes(surf, "RGB")
            x, y = self.box[0] + (self.box[2] - w) // 2, self.box[1] + (self.box[3] - h) // 2
            self.seq += 1
            ghosts = self.ghost_trail(x, y)
            head = struct.pack("<qiiiii", self.seq, x, y, w, h, len(ghosts))
            for gx, gy, alpha in ghosts:
                head += struct.pack("<iii", gx, gy, alpha)
            self.shm.buf[CAM_HEADER:CAM_HEADER + len(data)] = data
            self.shm.buf[:CAM_HEADER] = head.ljust(CAM_HEADER, b"\0")
            if self.seq == 1:
                self.overlay.send(cmd="cam_on", shm=self.shm.name)
        else:
            self.photo.configure(data=self.to_ppm(surf), format="ppm", width=w, height=h)

    def ghost_trail(self, x, y):
        """След за окном во время взмаха: где оно было чуть раньше, всё прозрачнее."""
        if not self.anim or self.anim[0] != "jump":
            self.ghosts = []
            return []
        self.ghosts = ([(x, y)] + self.ghosts)[:CAM_GHOSTS * 3]
        out = []
        for k, (gx, gy) in enumerate(self.ghosts[3::3], start=1):     # каждый третий кадр
            out.append((gx, gy, int(150 * (1 - k / (CAM_GHOSTS + 1)))))
        return out

    def draw_badge(self, surf, text):
        if not pygame.font.get_init():
            pygame.font.init()
        font = pygame.font.SysFont("Inter,DejaVu Sans", 30, bold=True)
        label = font.render(text, True, (255, 255, 255))
        w, h = surf.get_size()
        pad = pygame.Rect(0, 0, label.get_width() + 36, label.get_height() + 16)
        pad.center = (w // 2, h // 2)
        surf.fill((0, 0, 0), pad)
        surf.blit(label, label.get_rect(center=pad.center))

    def update(self):
        if self.cam.query_image():
            now = time.monotonic()
            self.step_shape(now)
            raw = self.prepare(self.cam.get_image())
            if self.detector and now - self.started > CAM_WARMUP:
                event = self.detector.feed(small_gray(raw), now)
                busy = self.gun.aiming(now) or self.signs.busy()
                if event and not busy:                     # поза рукой — не взмах
                    self.gesture(event)
            if self.tracker:
                self.tracker.submit(raw)                   # распознаём без фильтра
                self.track_hands(now)
            self.last = raw
            self.decorate(self.last, now)
            if not self.anim:          # во время анимации рисует animate()
                self.show(self.last)
        self.job = self.root.after(10, self.update)

    # --- руки и «пистолетик» ---

    def track_hands(self, now):
        found, version = self.tracker.latest((self.w, self.h))
        self.hands = found
        if version != self.hands_version:
            self.hands_version = version
            shot = self.gun.feed(found, now)
            if shot:
                self.fire(shot)
            self.signs.feed(found, now)
            faces = self.tracker.latest_faces()
            if faces:
                fx, fy, _ = max(faces, key=lambda f: f[2])
                self.zoom.see_face(fx, fy)

    def decorate(self, surf, now):
        """Обводка рук и вспышки выстрелов — прямо на кадре."""
        if self.tracker and self.tracker.segment:
            # тени-двойники: силуэт с задержкой повторяет движения
            camfx.draw_ghosts(surf, [self.tracker.mask_at(now - d) for d in camfx.GHOST_DELAYS])
            if self.outline_at is not None:
                t = (now - self.outline_at) * 1000 / camfx.OUTLINE_MS
                if t <= 1:
                    camfx.draw_outline(surf, self.tracker.mask_at(now, 0.5), t)
                else:
                    self.outline_at = None
        for pts in self.hands:
            # прицел — только когда пистолет взведён: видно, что можно стрелять
            hands.draw_hand(surf, pts, gun=hands.gun_barrel(pts) is not None
                            and self.gun.armed(now))
        self.signs.draw(surf)
        self.flashes = [f for f in self.flashes if (now - f[0]) * 1000 < FLASH_MS]
        for born, pos, seed in self.flashes:
            hands.draw_muzzle_flash(surf, pos, (now - born) * 1000 / FLASH_MS, seed)

    def fire(self, shot):
        """Пау: вспышка у пальца, отдача окна, звук, «ПАУ!» и дырка от пули на экране."""
        now = time.monotonic()
        self.flashes.append((now, shot.muzzle, random.randrange(1000)))
        if self.shot_sound is None:
            self.shot_sound = hands.gunshot_sound()
        if self.shot_sound:
            self.shot_sound.play()
        self.start("recoil", 1, RECOIL_MS)
        if self.overlay:
            mx, my = self.x + shot.muzzle[0], self.y + shot.muzzle[1]
            dx, dy = shot.direction
            self.overlay.send(cmd="pow", x=int(mx + dx * 70), y=int(my + dy * 70))
            hx, hy = self.bullet_hit(mx, my, dx, dy)
            self.overlay.send(cmd="hole", x=int(hx), y=int(hy))

    def bullet_hit(self, mx, my, dx, dy):
        """Куда «прилетела» пуля: в строку, куда примерно целился (тогда она разлетится),
        иначе — просто по направлению ствола, за пределами окна камеры."""
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        best, best_cos = None, math.cos(math.radians(AIM_CONE))
        for tx, ty in (self.targets() if self.targets else []):
            vx, vy = tx - mx, ty - my
            n = math.hypot(vx, vy) or 1
            cos = (vx * dx + vy * dy) / n
            if cos > best_cos:
                best, best_cos = (tx, ty), cos
        if best:
            return best[0] + random.uniform(-40, 40), best[1] + random.uniform(-15, 15)
        d = random.uniform(320, 700)
        hx, hy = mx + dx * d, my + dy * d
        while overlaps((hx - 20, hy - 20, 40, 40), self.rect) and d < 3000:
            d += 60
            hx, hy = mx + dx * d, my + dy * d
        return min(max(hx, 60), sw - 60), min(max(hy, 60), sh - 60)

    # --- жесты ---

    def gesture(self, event):
        """Сначала спрашиваем хозяина (песня: пауза, перемотка), иначе — эффект окна."""
        if self.on_gesture:
            label = self.on_gesture(event)
            if label:
                self.flash(label)
                return
        effects = {"left": lambda: self.jump((-1, 0)), "right": lambda: self.jump((1, 0)),
                   "up": lambda: self.jump((0, 1)), "down": lambda: self.jump((0, -1)),
                   "wave": self.shake,
                   "shoot": lambda: self.fire(hands.Shot((self.w * 0.62, self.h * 0.5),
                                                         hands.unit((0, 0), (1, -0.15)))),
                   "heart": lambda: [self.signs.spawn((self.w / 2, self.h * 0.6))
                                     for _ in range(12)]}
        if event in effects:
            effects[event]()

    def flash(self, text):
        """Крупный значок посреди окна на секунду — видно, что жест сработал."""
        if text:
            self.badge = (text, time.monotonic() + 0.9)

    # --- анимации окна ---

    def start(self, kind, direction, ms):
        if self.anim:
            return
        self.anim = (kind, direction, time.monotonic(), ms)
        self.animate()

    def spin(self, direction):
        """Оборот на 360° вокруг вертикальной оси: 1 — вправо, -1 — влево."""
        self.start("spin", direction, SPIN_MS)

    def flip(self):
        """Сальто: оборот вокруг горизонтальной оси, верх уходит назад."""
        self.start("flip", -1, SPIN_MS)

    def pulse(self):
        self.start("pulse", 1, PULSE_MS)

    def jump(self, direction):
        """Взмах: окно улетает в ту сторону, куда махнул, и пружинит назад, за ним след.
        direction — (dx, dy): (0, 1) вверх, (0, -1) вниз, (-1, 0) влево, (1, 0) вправо."""
        self.ghosts = []
        self.start("jump", direction, JUMP_MS)

    def boom(self, strength=1.0, sway=0):
        """Удар под бит. Новый бум перебивает прошлый, а вот оборот/сальто не трогаем.
        sway = ±1 — ещё и качнуть окно в сторону (особый момент)."""
        if self.anim and self.anim[0] != "boom":
            return
        self.boom_sway = sway
        if self.anim_job:
            self.root.after_cancel(self.anim_job)
        self.anim = None
        self.start("boom", strength, BOOM_MS)

    def shake(self, power=1.0):
        """Тряска окна; power > 1 — сильная (перебивает бум, оборот не трогает)."""
        if self.anim and self.anim[0] == "boom" and power > 1:
            if self.anim_job:
                self.root.after_cancel(self.anim_job)
            self.anim = None
        self.start("shake", power, SHAKE_MS if power <= 1 else SHAKE_HARD_MS)

    def spun_frame(self, a, direction=None, vertical=False):
        """Кадр, повёрнутый на угол a вокруг вертикальной (или горизонтальной) оси:
        сжат, дальний край темнее, с обратной стороны — зеркально."""
        direction = direction if direction is not None else self.spin_dir
        src = pygame.transform.rotate(self.last, 90) if vertical else self.last
        full_w, full_h = src.get_size()
        c = math.cos(a)
        cw = max(2, int(full_w * abs(c)))
        surf = pygame.transform.smoothscale(src, (cw, full_h))
        if c < 0:
            surf = pygame.transform.flip(surf, True, False)
        px = pygame.surfarray.pixels3d(surf)
        far = 0.65 * abs(math.sin(a))                 # насколько темнеет дальний край
        # вращение вправо: правый край уходит вглубь (темнеет), левый идёт на нас;
        # после 90° окно перевёрнуто, и уходящий край оказывается на экране с другой стороны
        ramp = np.linspace(1.0, 1.0 - far, cw) if direction * math.sin(a) * c > 0 \
            else np.linspace(1.0 - far, 1.0, cw)
        if c < 0:
            ramp = ramp * 0.8                          # обратная сторона чуть темнее
        px[:] = (px * ramp[:, None, None]).astype(np.uint8)
        del px
        return pygame.transform.rotate(surf, -90) if vertical else surf

    def animate(self):
        kind, direction, start, ms = self.anim
        t = min(1.0, (time.monotonic() - start) * 1000 / ms)
        if t >= 1.0:
            self.anim = self.anim_job = None
            self.place(self.w, self.h, self.x, self.y)
            if self.last is not None:
                self.show(self.last)
            return
        frame, w, h, dx = self.last, self.w, self.h, 0
        if kind in ("spin", "flip"):
            a = 2 * math.pi * ease(t)
            if self.last is not None:
                frame = self.spun_frame(a, direction, vertical=kind == "flip")
                w, h = frame.get_size()
            elif kind == "spin":
                w = max(2, int(self.w * abs(math.cos(a))))
            else:
                h = max(2, int(self.h * abs(math.cos(a))))
        elif kind == "pulse":
            k = 1 + 0.14 * math.sin(math.pi * t)
            w, h = int(self.w * k), int(self.h * k)
            if self.last is not None:
                frame = pygame.transform.smoothscale(self.last, (w, h))
        elif kind == "jump":
            off = JUMP_PX * math.sin(math.pi * t ** 0.7) * (1 - t * 0.25)
            dx, dy = direction
            self.place(w, h, self.x + int(dx * off), self.y - int(dy * off))
            if frame is not None:
                self.show(frame)
            self.anim_job = self.root.after(TICK_MS, self.animate)
            return
        elif kind == "shake":
            dx = int(34 * direction * math.sin(t * math.pi * 9) * (1 - t))
            if direction > 1:                                   # сильная — ещё и вверх-вниз
                dy = int(22 * direction * math.sin(t * math.pi * 13 + 1) * (1 - t))
                self.place(w, h, self.x + (self.w - w) // 2 + dx, self.y + dy)
                if frame is not None:
                    self.show(frame)
                self.anim_job = self.root.after(TICK_MS, self.animate)
                return
        elif kind == "boom":
            # резкий удар (~40 мс) и пружинистый возврат
            hit = t / 0.12 if t < 0.12 else math.exp(-(t - 0.12) * 6) * math.cos((t - 0.12) * 9)
            k = 1 + BOOM_SCALE * direction * hit
            w, h = int(self.w * k), int(self.h * k)
            dx = int(BOOM_SWAY * getattr(self, "boom_sway", 0) * hit)
            if self.last is not None:
                frame = pygame.transform.smoothscale(self.last, (w, h))
                glow = int(70 * direction * max(0.0, hit))
                if glow > 4:
                    frame.fill((glow, glow, glow), special_flags=pygame.BLEND_RGB_ADD)
        elif kind == "recoil":
            dy_kick = -int(26 * math.sin(math.pi * t) * (1 - t * 0.5))
            if self.last is not None:
                frame = self.last.copy()
                if t < 0.3:                                  # белая вспышка
                    k = int(140 * (1 - t / 0.3))
                    frame.fill((k, k, k), special_flags=pygame.BLEND_RGB_ADD)
            self.place(w, h, self.x, self.y + dy_kick)
            if frame is not None:
                self.show(frame)
            self.anim_job = self.root.after(TICK_MS, self.animate)
            return
        self.place(w, h, self.x + (self.w - w) // 2 + dx, self.y + (self.h - h) // 2)
        if frame is not None:
            self.show(frame)
        self.anim_job = self.root.after(TICK_MS, self.animate)

    def close(self):
        self.root.after_cancel(self.job)
        if self.anim_job:
            self.root.after_cancel(self.anim_job)
        if self.tracker:
            self.tracker.close()
        self.cam.stop()
        if self.win:
            self.win.destroy()
        if self.shm:
            self.overlay.send(cmd="cam_off")
            self.shm.close()
            self.shm.unlink()


HandTracker = hands.HandTracker     # тесты подменяют на заглушку


def overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


# ---------- показ ----------

class Player:
    def __init__(self, root, track, offset, speed, on_done, avoid=None, overlay=None,
                 box=False):
        self.root = root
        self.box = box
        self.overlay = overlay if overlay and overlay.alive() else None
        self.booms = []                 # [(время, сила)] — появится, когда посчитаются биты
        self.next_boom = 0
        self.on_boom = None
        self.ranges, self.on_section = [], None
        self.levels, self.level_fps = [], 20
        self.last_eq = -99.0
        self.section = None             # True — припев, False — куплет
        self.last_word = -99.0
        self.last_side, self.side = -99.0, "right"
        self.on_shake = None            # особый момент: «У-у» → сильная тряска
        self.speed = speed
        self.avoid_rect = avoid         # окно камеры: прямоугольник или функция (форма меняется)
        self.on_done = on_done
        self.audio = track.audio
        self.length = track_length(track.audio)
        timed = fill_timing(track.entries(), self.length)
        scenes, frames = track.scenes(), track.frames()
        self.timed = [(t + offset, s) for t, s in timed]
        self.scene = [s in scenes for _, s in timed]
        self.frame_marks = [frames.get(s) for _, s in timed]
        self.moments = camfx.moment_ranges([t for t, _ in self.timed], self.scene, self.length)
        self.on_frame = None            # пометка «кадр» на строке → форма окна камеры
        self.counts = camfx.keyword_counts([s for _, s in self.timed])
        self.ai_words = track.keywords()
        self.on_scene = None            # сцена «сериал»: start / line / end / bleep
        self.in_scene = False
        self.episode = 0
        self.next = 0
        self.cards = []
        self.paused = False
        self.sw = root.winfo_screenwidth()
        self.sh = root.winfo_screenheight()
        start_audio(track.audio)
        self.job = root.after(TICK_MS, self.tick)

    @property
    def avoid(self):
        """Середина окна камеры (лицо) — туда карточки стараемся не ставить."""
        rect = self.avoid_rect() if callable(self.avoid_rect) else self.avoid_rect
        if not rect:
            return None
        x, y, w, h = rect
        return (x + w // 5, y + h // 6, w * 3 // 5, h * 2 // 3)

    def random_pos(self):
        """Случайное место: не на лицо в камере и по возможности не поверх прошлой карточки."""
        fallback = None
        for _ in range(60):
            x = random.randint(40, self.sw - BOX_W - 40)
            y = random.randint(self.sh // 4, self.sh - BOX_H - 60)
            if self.avoid and overlaps((x, y, BOX_W, BOX_H), self.avoid):
                continue
            fallback = fallback or (x, y)
            if not self.cards:
                return x, y
            last = self.cards[-1]
            if abs(x - last.x) > BOX_W * 0.8 or abs(y - last.y) > BOX_H * 0.8:
                return x, y
        return fallback or (x, y)

    def set_music(self, booms, on_boom, ranges=(), on_section=None, levels=(), level_fps=20):
        """Биты посчитались: когда бумы, где припевы, громкость баса (для эквалайзера)."""
        self.booms, self.on_boom = booms, on_boom
        self.ranges, self.on_section = list(ranges), on_section
        self.levels, self.level_fps = list(levels), level_fps or 20
        self.skip_booms(song_time())

    def set_booms(self, booms, on_boom):
        self.set_music(booms, on_boom)

    def in_chorus(self, t):
        """Припев — или особый момент (крупные строки и плашки между ними): он как припев."""
        return any(a <= t < b for a, b in self.ranges) or any(a <= t < b for a, b in self.moments)

    def skip_booms(self, t):
        self.next_boom = next((i for i, b in enumerate(self.booms) if b[0] >= t),
                              len(self.booms))

    def tick(self):
        if self.paused:                 # всё замерло: и песня, и карточки
            self.job = self.root.after(TICK_MS, self.tick)
            return
        now = song_time()
        chorus = self.in_chorus(now)
        self.send_eq(now)
        if self.on_section and chorus != self.section:
            self.section = chorus
            self.on_section(chorus)
        while self.next_boom < len(self.booms) and self.booms[self.next_boom][0] - BOOM_LEAD <= now:
            bt, strength, sway = self.booms[self.next_boom]
            self.next_boom += 1
            if self.on_boom and now - bt < 0.15:      # старые (после лагов) не догоняем
                self.on_boom(strength, sway)
                if self.overlay and strength >= PUSH_STRENGTH:
                    self.overlay.send(cmd="push", power=round(strength, 2))
                if self.overlay and chorus and strength >= GLITCH_STRENGTH:
                    # сильный удар: строки ломает глитчем, камера двоится красным/голубым
                    self.overlay.send(cmd="glitch", ms=140)
        while self.next < len(self.timed) and now >= self.timed[self.next][0]:
            t, text = self.timed[self.next]
            end = self.timed[self.next + 1][0] if self.next + 1 < len(self.timed) else t + 4
            self.scene_step(self.next, text)
            if self.on_shake and self.in_moment(t) and camfx.shout(text):
                self.on_shake(SHAKE_HARD)
                if self.overlay:
                    self.overlay.send(cmd="glitch", ms=300)
            if self.frame_marks[self.next] and self.on_frame:
                self.on_frame(self.frame_marks[self.next])
            self.next += 1
            if text and self.overlay and self.in_scene:
                # строка-сцена: крупно по центру, слово за словом
                self.overlay.send(cmd="center", text=text, ms=int((end - t) * 1000))
                self.censor(text, (end - t) * 700 / max(1, len(text)))
                continue
            if text:
                if self.overlay:
                    card = OverlayCard(self.overlay, text, *self.random_pos(), end - t,
                                       self.speed, self.box)
                    self.cards.append(card)
                    if not self.in_scene:
                        self.big_word(text, card.char_ms, now)
                        self.edge_word(text, card.char_ms, now)
                    self.censor(text, card.char_ms)
                    continue
                self.cards.append(LyricCard(self.root, text, *self.random_pos(), end - t,
                                            self.speed, self.stop, lambda: self.paused))

        for card in self.cards:
            card.rise(RISE_PX)
        while self.cards and (len(self.cards) > MAX_CARDS or self.cards[0].offscreen()):
            self.cards.pop(0).close()

        if not pygame.mixer.music.get_busy():
            self.stop()
            return
        self.job = self.root.after(TICK_MS, self.tick)

    def scene_step(self, i, text):
        """Строки с 🎬 — сцена «сериал»: начало, очередная серия, конец."""
        event = None
        if self.scene[i]:
            self.episode = self.episode + 1 if self.in_scene else 1
            event = "start" if not self.in_scene else "line"
            self.in_scene = True
        elif self.in_scene:
            self.in_scene = False
            event = "end"
        if event and self.on_scene:
            self.on_scene(event, text, self.episode)

    def censor(self, text, ms_per_char):
        """«****» в строке — пи-ип ровно когда до звёздочек допечатает."""
        pos = text.find("***")
        if pos >= 0 and self.on_scene:
            self.root.after(int(pos * ms_per_char),
                            lambda: self.job and self.on_scene("bleep", text, 0))

    def send_eq(self, now):
        """Громкость баса — слою, он рисует полоски эквалайзера."""
        if not self.levels or not self.overlay or now - self.last_eq < EQ_EVERY:
            return
        self.last_eq = now
        i = min(len(self.levels) - 1, max(0, int(now * self.level_fps)))
        self.overlay.send(cmd="eq", levels=self.levels[i])

    def in_moment(self, t):
        return any(a <= t < b for a, b in self.moments)

    def edge_word(self, text, ms_per_char, now):
        """В припеве и особом моменте — самое длинное слово строки вертикально по краю
        экрана, по очереди слева и справа."""
        word = camfx.side_word(text)
        if not word or not self.in_chorus(now) or now - self.last_side < SIDE_GAP:
            return
        self.last_side = now
        self.side = "left" if self.side == "right" else "right"
        side, overlay = self.side, self.overlay
        self.root.after(int(text.find(word) * ms_per_char),
                        lambda: self.job and overlay.send(cmd="side", text=word.upper(), side=side))

    def big_word(self, text, ms_per_char, now):
        """Ключевое слово строки — крупно по центру, когда до него допечатает."""
        word = self.ai_words.get(text)                 # слово выбрал ИИ-режиссёр
        found = (word, text.lower().find(word.lower())) if word else \
            camfx.keyword(text, self.counts, self.in_chorus(now))
        if not found or now - self.last_word < camfx.KEYWORD_GAP:
            return
        self.last_word = now
        word, pos = found
        # в припеве и в особом моменте слово вылетает из глубины, иначе просто выпрыгивает
        style = "depth" if self.in_chorus(now) else "pop"
        overlay = self.overlay
        self.root.after(int(pos * ms_per_char),
                        lambda: self.job and overlay.send(cmd="word", text=word.upper(),
                                                          style=style))

    def clear_cards(self):
        if self.overlay:
            self.overlay.send(cmd="clear")
        else:
            for card in self.cards:
                card.close(instant=True)
        self.cards.clear()

    def toggle_pause(self):
        if self.paused:
            pygame.mixer.music.unpause()
        else:
            pygame.mixer.music.pause()
        self.paused = not self.paused
        if self.overlay:
            self.overlay.send(cmd="pause", on=self.paused)
        return self.paused

    def seek(self, dt):
        """Перемотка на dt секунд; карточки начинаются заново с текущей строки."""
        t = max(0.0, min(song_time() + dt, self.length - 0.5))
        start_audio(self.audio, t)
        if self.paused:
            pygame.mixer.music.pause()
        self.clear_cards()
        self.next = max(0, sum(1 for lt, _ in self.timed if lt <= t) - 1)
        self.skip_booms(t)
        return t

    def stop(self):
        if self.job is None:
            return
        self.root.after_cancel(self.job)
        self.job = None
        self.clear_cards()
        if self.overlay:
            self.overlay.send(cmd="pause", on=False)
        stop_audio()
        self.on_done()


# ---------- окно текста ----------

class TextWindow:
    """Вставка и правка текста песни. Тайминги неизменённых строк сохраняются."""

    def __init__(self, root, track, on_close, is_new=False):
        self.track = track
        self.on_close = on_close
        self.is_new = is_new
        self.old = track.entries()

        self.win = tk.Toplevel(root)
        self.win.title(f"Текст — {track.name}")
        self.win.geometry("900x720")
        self.win.configure(bg=UI_BG)

        tk.Label(self.win, text=f"Текст песни «{track.name}»", bg=UI_BG, fg="#fff",
                 font=(UI_FONT, 16, "bold")).pack(pady=(12, 0))
        tk.Label(self.win, bg=UI_BG, fg=UI_DIM, font=(UI_FONT, 11),
                 text="Вставь текст целиком (Ctrl+V или кнопкой). "
                      "Одна строка = одно всплывающее окно. Пустые строки не считаются."
                 ).pack(pady=(2, 6))

        tools = row(self.win)
        button(tools, "Вставить из буфера", self.paste)
        button(tools, "Очистить", self.clear)
        button(tools, "Убрать пустые и [пометки]", self.tidy)
        button(tools, f"Разбить длинные (>{LONG_LINE})", self.split)

        frame = tk.Frame(self.win, bg=UI_LINE, padx=1, pady=1)
        frame.pack(fill="both", expand=True, padx=10, pady=4)
        self.text = tk.Text(frame, bg=UI_PANEL, fg=UI_FG, insertbackground="#fff",
                            font=(UI_FONT, 13), wrap="word", undo=True, relief="flat",
                            padx=10, pady=8, selectbackground=UI_ACCENT)
        bar = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=bar.set)
        self.text.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.text.insert("1.0", "\n".join(s for _, s in self.old))
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.text.bind("<<Modified>>", lambda e: self.update_status())

        bottom = row(self.win, pady=(4, 12))
        self.status = tk.Label(bottom, bg=UI_BG, fg=UI_DIM, font=(UI_FONT, 11))
        self.status.pack(side="left")
        for b in reversed([
            ("Сохранить", self.save, False),
            ("Сохранить и выйти", self.save_and_exit, True),
            ("Выйти без сохранения", self.close_discard, False),
        ]):
            button(bottom, b[0], b[1], b[2]).pack(side="right", padx=3)

        bind_ctrl(self.win, "s", "Cyrillic_yeru", self.save)
        self.win.protocol("WM_DELETE_WINDOW", self.ask_close)
        self.update_status()
        self.text.focus_set()

    def lines(self):
        return clean_lines(self.text.get("1.0", "end"))

    def set_lines(self, lines):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))
        self.text.edit_separator()

    def update_status(self):
        self.text.edit_modified(False)
        lines = self.lines()
        kept = sum(t is not None for t, _ in carry_timings(self.old, lines))
        timed = sum(t is not None for t, _ in self.old)
        extra = f"   тайминги сохранятся у {kept}/{timed}" if timed else ""
        self.status.config(text=f"строк: {len(lines)}{extra}"
                                f"{'   • не сохранено' if self.dirty() else ''}")

    def dirty(self):
        return self.lines() != [s for _, s in self.old]

    # --- кнопки ---

    def paste(self):
        try:
            clip = self.win.clipboard_get()
        except tk.TclError:
            return
        self.text.insert("insert", clip)
        self.update_status()

    def clear(self):
        if self.lines() and messagebox.askyesno("Очистить", "Стереть весь текст?",
                                                parent=self.win):
            self.set_lines([])
            self.update_status()

    def tidy(self):
        self.set_lines(clean_lines(self.text.get("1.0", "end"), drop_tags=True))
        self.update_status()

    def split(self):
        self.set_lines([part for line in self.lines() for part in split_long(line)])
        self.update_status()

    def save(self):
        lines = self.lines()
        if not lines:
            messagebox.showwarning("Пусто", "Вставь текст песни.", parent=self.win)
            return False
        self.old = carry_timings(self.old, lines)
        self.track.save(self.old)
        self.update_status()
        return True

    def save_and_exit(self):
        if self.save():
            self.close(open_timings=self.is_new and messagebox.askyesno(
                "Готово", "Текст сохранён. Перейти к разметке таймингов?", parent=self.win))

    def close_discard(self):
        if not self.dirty() or messagebox.askyesno(
                "Выйти", "Выйти без сохранения? Изменения пропадут.", parent=self.win):
            self.close()

    def ask_close(self):
        if not self.dirty():
            return self.close()
        answer = messagebox.askyesnocancel("Выйти", "Сохранить изменения?", parent=self.win)
        if answer is None:
            return
        if answer and not self.save():
            return
        self.close()

    def close(self, open_timings=False):
        self.win.destroy()
        self.on_close(open_timings)


# ---------- редактор таймингов ----------

HELP = ("Пробел — строка началась, дальше   Backspace — назад и стереть   ↑↓ — выбрать строку   "
        "←→ — перемотка 5 с   P — пауза\nEnter / двойной клик — слушать с выбранной строки   "
        "[ ] — сдвиг ±0.1 с   E — ввести время   Del — стереть   Ctrl+S — сохранить   "
        "Esc — сохранить и выйти\nS — строка-сцена (•): крупно по центру, лицо крупным планом   "
        "K — кадр: с этой строки окно камеры широкое / среднее / вертикальное")


class Editor:
    """Список всех строк с таймингами: пробелом ставишь время и идёшь дальше."""

    def __init__(self, root, track, on_close):
        self.track = track
        self.on_close = on_close
        self.entries = track.entries()
        self.scenes = track.scenes()
        self.frames = track.frames()
        self.audio = track.audio
        self.length = track_length(self.audio)
        self.paused = False
        self.dirty = False
        self.dragging = False
        self.playing_row = None

        self.win = tk.Toplevel(root)
        self.win.title(f"Тайминги — {track.name}")
        self.win.geometry("1100x800")
        self.win.configure(bg=UI_BG)

        self.clock = tk.Label(self.win, bg=UI_BG, fg="#fff", font=(UI_FONT, 18, "bold"))
        self.clock.pack(pady=(10, 0))
        self.now_line = tk.Label(self.win, bg=UI_BG, fg="#ffd24a", font=(UI_FONT, 16),
                                 wraplength=1060, height=2)
        self.now_line.pack()

        self.pos = tk.DoubleVar()
        self.scale = slider(self.win, self.pos, to=self.length,
                            on_press=lambda: setattr(self, "dragging", True))
        self.scale.pack(fill="x", padx=14, pady=(4, 6))
        self.scale.bind("<ButtonRelease-1>", self.scale_release)

        play = row(self.win)
        button(play, "⏮ С начала", lambda: self.seek(0))
        self.pause_btn = button(play, "⏸ Пауза  P", self.toggle_pause)
        button(play, "« 5 с  ←", lambda: self.seek(song_time() - 5))
        button(play, "5 с »  →", lambda: self.seek(song_time() + 5))
        button(play, "🎧 Слушать со строки  Enter", self.listen_from_selected)

        mark = row(self.win)
        button(mark, "✔ Отметить  Пробел", self.mark, accent=True)
        button(mark, "↩ Назад  Backspace", self.back)
        button(mark, "−0.1 с  [", lambda: self.nudge(-0.1))
        button(mark, "+0.1 с  ]", lambda: self.nudge(0.1))
        button(mark, "Ввести время  E", self.edit_time)
        button(mark, "Стереть  Del", self.clear_time)
        button(mark, "Сцена  S", self.toggle_scene)
        button(mark, "Кадр  K", self.cycle_frame)

        frame = tk.Frame(self.win, bg=UI_LINE, padx=1, pady=1)
        frame.pack(fill="both", expand=True, padx=10, pady=6)
        self.tree = ttk.Treeview(frame, columns=("n", "time", "frame", "text"), show="headings",
                                 selectmode="browse", takefocus=False)
        for col, title, w, stretch in (("n", "#", 56, False), ("time", "время", 100, False),
                                       ("frame", "кадр", 110, False),
                                       ("text", "строка", 760, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=w, stretch=stretch, anchor="w")
        self.tree.tag_configure("odd", background=UI_STRIPE)
        self.tree.tag_configure("playing", background="#4a3b00")
        self.tree.tag_configure("empty", foreground="#6b7079")
        self.tree.tag_configure("scene", foreground="#c7a6ff")
        bar = ttk.Scrollbar(frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        for i in range(len(self.entries)):
            self.tree.insert("", "end", iid=str(i))
            self.redraw_row(i)

        tk.Label(self.win, text=HELP, bg=UI_BG, fg=UI_DIM, font=(UI_FONT, 10),
                 justify="center").pack(pady=(0, 4))

        bottom = row(self.win, pady=(0, 12))
        button(bottom, "Сдвинуть все…", self.shift_all)
        button(bottom, "Стереть все…", self.clear_all)
        for text, fn, accent in (("Сохранить  Ctrl+S", self.save, False),
                                 ("Сохранить и выйти  Esc", self.save_and_exit, True),
                                 ("Выйти без сохранения", self.close_discard, False)):
            button(bottom, text, fn, accent).pack(side="right", padx=3)

        # клик по строке выбирает её, но фокус остаётся у окна — чтобы работали клавиши
        self.tree.bind("<ButtonRelease-1>", lambda e: self.win.focus_set())
        self.tree.bind("<Double-1>", lambda e: self.listen_from_selected())
        keys = {
            "<space>": self.mark, "<BackSpace>": self.back,
            "<Up>": lambda: self.move(-1), "<Down>": lambda: self.move(1),
            "<Left>": lambda: self.seek(song_time() - 5),
            "<Right>": lambda: self.seek(song_time() + 5),
            "<p>": self.toggle_pause, "<Cyrillic_ze>": self.toggle_pause,
            "<Return>": self.listen_from_selected,
            "<bracketleft>": lambda: self.nudge(-0.1), "<bracketright>": lambda: self.nudge(0.1),
            "<Cyrillic_ha>": lambda: self.nudge(-0.1),
            "<Cyrillic_hardsign>": lambda: self.nudge(0.1),
            "<e>": self.edit_time, "<Cyrillic_u>": self.edit_time,
            "<s>": self.toggle_scene, "<Cyrillic_yeru>": self.toggle_scene,
            "<k>": self.cycle_frame, "<Cyrillic_el>": self.cycle_frame,
            "<Delete>": self.clear_time, "<Escape>": self.save_and_exit,
        }
        for key, fn in keys.items():
            self.win.bind(key, lambda e, fn=fn: (fn(), "break")[1])
        bind_ctrl(self.win, "s", "Cyrillic_yeru", self.save)
        self.win.protocol("WM_DELETE_WINDOW", self.ask_close)

        first_empty = next((i for i, (t, _) in enumerate(self.entries) if t is None), 0)
        self.select(first_empty)
        self.win.focus_force()
        start_audio(self.audio)
        self.tick()

    # --- строки ---

    def redraw_row(self, i):
        t, text = self.entries[i]
        tags = ("odd",) if i % 2 else ()
        if t is None:
            tags += ("empty",)
        if i == self.playing_row:
            tags += ("playing",)
        scene = text in self.scenes
        if scene:
            tags += ("scene",)
        shape = self.frames.get(text)
        self.tree.item(str(i), values=(f"{i + 1} •" if scene else i + 1,
                                       fmt_time(t) if t is not None else "—",
                                       camfx.SHAPES[shape][2] if shape else "", text), tags=tags)

    def selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else 0

    def select(self, i):
        i = max(0, min(len(self.entries) - 1, i))
        self.tree.selection_set(str(i))
        self.tree.see(str(i))

    def move(self, d):
        self.select(self.selected() + d)

    def set_time(self, i, t):
        self.entries[i][0] = None if t is None else round(max(0.0, t), 2)
        self.dirty = True
        self.redraw_row(i)

    # --- действия ---

    def mark(self):
        i = self.selected()
        self.set_time(i, song_time())
        self.select(i + 1)

    def back(self):
        i = max(0, self.selected() - 1)
        self.set_time(i, None)
        self.select(i)

    def toggle_scene(self):
        """Строка-сцена: крупно по центру, камера — телевизор и крупный план лица."""
        i = self.selected()
        self.scenes ^= {self.entries[i][1]}
        self.dirty = True
        self.redraw_row(i)

    def cycle_frame(self):
        """С этой строки окно камеры: широкое → среднее → вертикальное → без смены."""
        i = self.selected()
        text = self.entries[i][1]
        shape = camfx.next_shape(self.frames.get(text))
        if shape:
            self.frames[text] = shape
        else:
            self.frames.pop(text, None)
        self.dirty = True
        self.redraw_row(i)

    def clear_time(self):
        self.set_time(self.selected(), None)

    def nudge(self, dt):
        i = self.selected()
        t = self.entries[i][0]
        if t is not None:
            self.set_time(i, t + dt)

    def edit_time(self):
        i = self.selected()
        t = self.entries[i][0]
        s = simpledialog.askstring("Время", f"Строка {i + 1}: мм:сс.сс или секунды",
                                   initialvalue=fmt_time(t) if t is not None else "",
                                   parent=self.win)
        if s is not None:
            self.set_time(i, parse_time(s) if s.strip() else None)
        self.win.focus_force()

    def shift_all(self):
        s = simpledialog.askstring("Сдвинуть все", "На сколько секунд сдвинуть все тайминги?\n"
                                   "Минус — раньше, например -0.3", parent=self.win)
        s = (s or "").strip()
        dt = parse_time(s.lstrip("+-"))
        if dt is not None:
            dt = -dt if s.startswith("-") else dt
            for i, (t, _) in enumerate(self.entries):
                if t is not None:
                    self.set_time(i, t + dt)
        self.win.focus_force()

    def clear_all(self):
        if messagebox.askyesno("Стереть все", "Стереть все тайминги?", parent=self.win):
            for i in range(len(self.entries)):
                self.set_time(i, None)
            self.select(0)
        self.win.focus_force()

    def seek(self, t):
        start_audio(self.audio, max(0.0, min(t, self.length - 0.5)))
        self.paused = False

    def scale_release(self, _e):
        self.dragging = False
        self.seek(self.pos.get())
        self.win.focus_set()

    def listen_from_selected(self):
        """Прыгнуть на 2 с раньше выбранной строки — проверить, как попадает."""
        i = self.selected()
        prev = [t for t, _ in self.entries[:i + 1] if t is not None]
        self.seek((prev[-1] - 2) if prev else 0)

    def toggle_pause(self):
        if self.paused:
            pygame.mixer.music.unpause()
        elif pygame.mixer.music.get_busy():
            pygame.mixer.music.pause()
        else:                      # трек доиграл — запускаем заново
            return self.seek(0)
        self.paused = not self.paused

    def save(self):
        self.track.save(self.entries)
        self.track.save_scenes(self.scenes)
        self.track.save_frames(self.frames)
        self.dirty = False

    def save_and_exit(self):
        self.save()
        self.close()

    def close_discard(self):
        if not self.dirty or messagebox.askyesno(
                "Выйти", "Выйти без сохранения? Новые тайминги пропадут.", parent=self.win):
            self.close()

    def ask_close(self):
        if self.dirty:
            answer = messagebox.askyesnocancel("Выйти", "Сохранить тайминги?", parent=self.win)
            if answer is None:
                return
            if answer:
                self.save()
        self.close()

    def close(self):
        self.win.after_cancel(self.job)
        stop_audio()
        self.win.destroy()
        self.on_close()

    # --- обновление экрана ---

    def tick(self):
        busy = pygame.mixer.music.get_busy()
        now = song_time() if busy or self.paused else self.length
        self.pause_btn.config(text="▶ Играть  P" if self.paused or not busy else "⏸ Пауза  P")
        done = sum(t is not None for t, _ in self.entries)
        self.clock.config(text=f"{fmt_time(now)} / {fmt_time(self.length)}"
                               f"      размечено {done}/{len(self.entries)}"
                               f"{'   • не сохранено' if self.dirty else ''}")
        if not self.dragging:
            self.pos.set(now)
        # какая строка сейчас звучит по уже расставленным таймингам
        row_ = None
        for i, (t, _) in enumerate(self.entries):
            if t is not None and t <= now:
                row_ = i
        if row_ != self.playing_row:
            old, self.playing_row = self.playing_row, row_
            for i in (old, row_):
                if i is not None:
                    self.redraw_row(i)
            self.now_line.config(text=self.entries[row_][1] if row_ is not None else "")
        self.job = self.win.after(50, self.tick)


# ---------- библиотека треков ----------

GESTURES_HELP = (
    "Жесты (клавиши):  взмах ← → ↑ ↓  окно улетает туда со следом  ·  помахать (W)  тряска  ·  "
    "ладонь на камеру (Пробел)  пауза  ·  на паузе ← →  перемотка  ·  💥 бум — окно бьёт в такт\n"
    "Руки:  пистолетик + рывок вверх — выстрел (F), пуля разбивает строку  ·  "
    "сердечко из двух рук — летят сердечки (H)")

class Library:
    def __init__(self, offset):
        self.offset = offset
        self.player = None
        self.overlay = start_overlay()   # Qt стартует ~секунду — запускаем заранее
        self.root = tk.Tk()
        self.root.title("YeahMusic")
        self.root.geometry("1120x680")
        self.root.minsize(1120, 560)
        setup_ui(self.root)
        self.settings = load_settings()

        head = row(self.root, padx=18, pady=(18, 6))
        titles = tk.Frame(head, bg=UI_BG)
        titles.pack(side="left")
        tk.Label(titles, text="YeahMusic", bg=UI_BG, fg="#fff",
                 font=(UI_FONT, 24, "bold")).pack(anchor="w")
        tk.Label(titles, text="Выбери трек или создай новый", bg=UI_BG, fg=UI_DIM,
                 font=(UI_FONT, 11)).pack(anchor="w")
        button(head, "＋  Новый трек", self.new_track, accent=True).pack(side="right")

        speed = row(self.root, padx=18, pady=(4, 2))
        tk.Label(speed, text="Скорость печати", bg=UI_BG, fg=UI_DIM,
                 font=(UI_FONT, 10)).pack(side="left")
        self.speed = tk.DoubleVar(value=self.settings["speed"])
        self.speed_label = tk.Label(speed, bg=UI_BG, fg=UI_FG, width=5, anchor="w",
                                    font=(UI_FONT, 10, "bold"))
        scale = slider(speed, self.speed, from_=0.4, to=2.0, length=220,
                       command=lambda v: self.show_speed())
        scale.pack(side="left", padx=(10, 6))
        self.speed_label.pack(side="left")
        self.card_bg = tk.BooleanVar(value=self.settings["card_bg"])
        tk.Checkbutton(speed, text="▢  Фон у строк", variable=self.card_bg,
                       command=self.save_camera, bg=UI_BG, fg=UI_FG, selectcolor="#23262c",
                       activebackground=UI_BG, activeforeground="#fff", highlightthickness=0,
                       bd=0, font=(UI_FONT, 10), takefocus=False).pack(side="left", padx=(24, 0))
        scale.bind("<ButtonRelease-1>", lambda e: self.save_speed())
        self.show_speed()

        cam = row(self.root, padx=18, pady=(2, 2))
        self.camera = None
        self.use_camera = tk.BooleanVar(value=self.settings["camera"])
        tk.Checkbutton(cam, text="📷  Камера", variable=self.use_camera,
                       command=self.save_camera, bg=UI_BG, fg=UI_FG, selectcolor="#23262c",
                       activebackground=UI_BG, activeforeground="#fff", highlightthickness=0,
                       bd=0, font=(UI_FONT, 10), takefocus=False).pack(side="left")
        self.use_swipe = tk.BooleanVar(value=self.settings["swipe"])
        tk.Checkbutton(cam, text="🖐  Жесты руками", variable=self.use_swipe,
                       command=self.save_camera, bg=UI_BG, fg=UI_FG, selectcolor="#23262c",
                       activebackground=UI_BG, activeforeground="#fff", highlightthickness=0,
                       bd=0, font=(UI_FONT, 10), takefocus=False).pack(side="left", padx=(16, 0))
        self.use_hands = tk.BooleanVar(value=self.settings["hands"])
        tk.Checkbutton(cam, text="✋  Руки", variable=self.use_hands,
                       command=self.save_camera, bg=UI_BG, fg=UI_FG, selectcolor="#23262c",
                       activebackground=UI_BG, activeforeground="#fff", highlightthickness=0,
                       bd=0, font=(UI_FONT, 10), takefocus=False).pack(side="left", padx=(16, 0))
        self.boom_btn = button(speed, "", self.cycle_boom)
        self.boom_btn.pack(side="left", padx=(24, 0))
        self.show_boom()
        self.cam_btn = button(cam, "", self.cycle_cam_device)
        self.cam_btn.pack(side="left", padx=(16, 0))
        self.show_cam_device()
        self.preview_btn = button(cam, "Проверить камеру", self.toggle_preview)
        self.preview_btn.pack(side="left", padx=10)
        tk.Label(self.root, text=GESTURES_HELP, bg=UI_BG, fg="#5a5f68", justify="left",
                 wraplength=1060,
                 font=(UI_FONT, 9)).pack(anchor="w", padx=18)
        # клавиши = те же жесты (удобно проверить или если рука не ловится)
        keys = {"<Left>": "left", "<Right>": "right", "<Up>": "up", "<Down>": "down",
                "<w>": "wave", "<Cyrillic_tse>": "wave", "<space>": "cover",
                "<f>": "shoot", "<Cyrillic_a>": "shoot",
                "<h>": "heart", "<Cyrillic_er>": "heart"}
        for key, event in keys.items():
            self.root.bind(key, lambda e, ev=event: self.key_gesture(ev))
        self.keys = keys

        frame = tk.Frame(self.root, bg=UI_LINE, padx=1, pady=1)
        frame.pack(fill="both", expand=True, padx=18, pady=8)
        self.tree = ttk.Treeview(frame, columns=("name", "lines", "timed", "audio"),
                                 show="headings", selectmode="browse")
        for col, title, w, stretch in (("name", "трек", 280, True), ("lines", "строк", 70, False),
                                       ("timed", "тайминги", 110, False),
                                       ("audio", "файл", 280, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=w, stretch=stretch, anchor="w")
        self.tree.tag_configure("odd", background=UI_STRIPE)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.play())
        self.tree.bind("<Return>", lambda e: self.play())
        for key, event in self.keys.items():    # чтобы список не листался, пока камера
            self.tree.bind(key, lambda e, ev=event: self.key_gesture(ev))

        # нижняя панель пакуется раньше списка: когда места мало, сжимается список, а не кнопки
        actions = row(self.root, padx=18, pady=(4, 18))
        actions.pack_configure(side="bottom", before=frame)
        self.buttons = [
            button(actions, "📝 Текст", self.edit_text),
            button(actions, "⏱ Тайминги", self.edit_timings),
            button(actions, "🎙 Автотайминги", self.auto_time),
            button(actions, "🤖 ИИ-разметка", self.ai_marks),
            button(actions, "📱 Для телефона", self.export_phone),
        ]
        tk.Frame(actions, width=22, bg=UI_BG).pack(side="left")      # отступ между группами
        self.buttons += [
            button(actions, "✏ Переименовать", self.rename),
            button(actions, "📂 Папка", self.open_folder),
            button(actions, "🗑 Удалить", self.delete),
        ]
        self.buttons.append(self.preview_btn)
        self.play_btn = button(actions, "▶ Показать", self.play, accent=True)
        self.play_btn.pack(side="right")
        self.status = tk.Label(actions, bg=UI_BG, fg=UI_DIM, font=(UI_FONT, 10))
        self.status.pack(side="right", padx=10)
        self.root.bind("<Escape>", lambda e: self.player.stop() if self.player
                       else self.stop_camera())

        self.refresh()

    # --- список ---

    def refresh(self, select=None):
        for tr in import_loose_audio():            # песни, скинутые в папку программы
            select = select or tr.name
        keep = select or self.current_name()
        self.tree.delete(*self.tree.get_children())
        for n, tr in enumerate(list_tracks()):
            entries = tr.entries()
            timed = sum(t is not None for t, _ in entries)
            audio = tr.audio
            self.tree.insert("", "end", iid=tr.name, values=(
                tr.name, len(entries) or "—",
                f"{timed}/{len(entries)}" if entries else "—",
                audio.name if audio else "нет песни!"), tags=("odd",) if n % 2 else ())
        items = self.tree.get_children()
        if items:
            pick = keep if keep in items else items[0]
            self.tree.selection_set(pick)
            self.tree.see(pick)
            self.tree.focus(pick)
        self.tree.focus_set()

    def show_speed(self):
        self.speed_label.config(text=f"{self.speed.get():.1f}×")

    def save_speed(self):
        self.settings["speed"] = round(self.speed.get(), 2)
        save_settings(self.settings)

    def show_boom(self):
        self.boom_btn.config(text=f"💥 Бум под бит: {BOOM_MODES[self.settings['boom']]}")

    def card_targets(self):
        if not self.player:
            return []
        return [(c.x + BOX_W / 2, c.y + BOX_H / 2) for c in self.player.cards]

    def on_scene(self, kind, text, number):
        cam = self.camera
        if kind == "bleep" and cam:
            cam.bleep()

    def show_cam_device(self):
        devices = dict(list_camera_devices())
        dev = self.settings.get("cam_device")
        name = devices.get(dev) or (next(iter(devices.values())) if devices else "нет")
        self.cam_btn.config(text=f"📷 {name[:22]}")

    def cycle_cam_device(self):
        """Переключить камеру: вебка → айфон (DroidCam/Iriun) → … Список — по названиям."""
        devices = [d for d, _ in list_camera_devices()]
        if not devices:
            return
        cur = self.settings.get("cam_device")
        cur = cur if cur in devices else devices[0]
        self.settings["cam_device"] = devices[(devices.index(cur) + 1) % len(devices)]
        save_settings(self.settings)
        self.show_cam_device()
        if self.camera and not self.player:              # открыта проверка — перезапустить
            self.stop_camera()
            self.toggle_preview()

    def on_frame(self, shape):
        if self.camera:
            aspect, height, _ = camfx.SHAPES[shape]
            self.camera.reshape(aspect, height)

    def on_section(self, chorus):
        """Смена куплет/припев: фильтр и зум камеры; вход в припев — дроп: вспышка и удар."""
        if self.camera:
            self.camera.set_chorus(chorus)
        if chorus and self.player and song_time() > 1.0:
            if self.overlay:
                self.overlay.send(cmd="flash")
                # дроп: вокруг окна — копии кадра с задержкой, «эхо» движения
                self.overlay.send(cmd="clones", ms=CLONES_MS)
            if self.camera:
                self.camera.boom(1.6)

    def cycle_boom(self):
        """вся песня → только припев → выкл → вся песня"""
        modes = list(BOOM_MODES)
        self.settings["boom"] = modes[(modes.index(self.settings["boom"]) + 1) % len(modes)]
        save_settings(self.settings)
        self.show_boom()

    def start_booms(self, tr, player):
        """Биты считаются в фоне (~1 с, потом из кэша) — и сразу отдаются плееру."""
        mode = self.settings["boom"]
        job = {}

        def work():
            try:
                data = beats.track_beats(tr)
                length = track_length(tr.audio)
                entries = fill_timing(tr.entries(), length)
                ranges = beats.chorus_ranges(entries, length)
                scenes = tr.scenes()
                moments = camfx.moment_ranges([t for t, _ in entries],
                                              [s in scenes for _, s in entries], length)
                job["ranges"] = ranges
                job["data"] = data
                job["booms"] = (beats.boom_schedule(data, mode, ranges, moments=moments)
                                if self.camera else [])
            except Exception as e:      # битый файл и т.п. — просто без бумов
                job["booms"], job["ranges"], job["data"] = [], [], {}
                print("Биты не посчитались:", e)

        def wait():
            if "booms" not in job:
                self.root.after(100, wait)
            elif self.player is player:
                player.set_music(job["booms"], self.camera.boom if self.camera else None,
                                 job["ranges"], self.on_section,
                                 job["data"].get("levels", ()),
                                 job["data"].get("level_fps", 20))
        player.on_scene = self.on_scene
        player.on_frame = self.on_frame
        player.on_shake = self.camera.shake if self.camera else None
        threading.Thread(target=work, daemon=True).start()
        wait()

    def save_camera(self):
        self.settings["camera"] = self.use_camera.get()
        self.settings["swipe"] = self.use_swipe.get()
        self.settings["hands"] = self.use_hands.get()
        self.settings["card_bg"] = self.card_bg.get()
        save_settings(self.settings)

    def start_camera(self, on_escape):
        try:
            self.camera = CameraWindow(self.root, on_escape, swipe=self.use_swipe.get(),
                                       on_gesture=self.song_gesture,
                                       track_hands=self.use_hands.get(), overlay=self.overlay,
                                       targets=self.card_targets,
                                       device=self.settings.get("cam_device"))
            # фокус может оказаться у окна камеры — клавиши должны работать и там
            if self.camera.win:                     # у камеры в слое своего окна нет
                for key, event in self.keys.items():
                    self.camera.win.bind(key, lambda e, ev=event: self.key_gesture(ev))
        except RuntimeError as e:
            messagebox.showwarning("Камера", str(e), parent=self.root)
            self.camera = None
        return self.camera

    def stop_camera(self):
        if self.camera:
            self.camera.close()
            self.camera = None
        self.preview_btn.config(text="Проверить камеру")

    def toggle_preview(self):
        """Камера без музыки — встать в кадр перед записью."""
        if self.camera:
            return self.stop_camera()
        if self.start_camera(self.stop_camera):
            self.preview_btn.config(text="Закрыть камеру")

    def song_gesture(self, event):
        """Жесты, которые управляют песней. Вернёт значок для окна камеры или None,
        если жест не про песню (тогда окно камеры само крутится/трясётся)."""
        if not self.player:
            return None
        if event == "cover":
            return "⏸ пауза" if self.player.toggle_pause() else "▶ дальше"
        if self.player.paused and event in ("left", "right"):
            step = SEEK_STEP if event == "right" else -SEEK_STEP
            t = self.player.seek(step)
            return f"{'⏩' if step > 0 else '⏪'} {fmt_time(t)[:5]}"
        return None

    def key_gesture(self, event):
        if self.camera:
            self.camera.gesture(event)
        elif self.player:
            self.song_gesture(event)
        else:
            return None                 # без камеры и показа — стрелки листают список
        return "break"

    def current_name(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def current(self, need_audio=False, need_text=False):
        name = self.current_name()
        if not name:
            messagebox.showinfo("Нет трека", "Сначала создай трек: «＋ Новый трек».",
                                parent=self.root)
            return None
        tr = Track(TRACKS / name)
        if need_audio and not tr.audio:
            messagebox.showwarning("Нет песни", f"В папке трека нет файла песни "
                                   f"({', '.join(AUDIO_EXT)}).", parent=self.root)
            return None
        if need_text and not tr.entries():
            if messagebox.askyesno("Нет текста", "У трека ещё нет текста. Добавить сейчас?",
                                   parent=self.root):
                self.open_text(tr)
            return None
        return tr

    # --- дочерние окна: библиотека прячется, пока открыто окно трека ---

    def hide(self):
        self.root.withdraw()

    def back(self, name):
        self.root.deiconify()
        self.refresh(select=name)

    def open_text(self, tr, is_new=False):
        def done(open_timings):
            if open_timings:
                self.open_timings(tr)
            else:
                self.back(tr.name)
        self.hide()
        TextWindow(self.root, tr, done, is_new)

    def open_timings(self, tr):
        self.hide()
        Editor(self.root, tr, lambda: self.back(tr.name))

    # --- кнопки ---

    def new_track(self):
        """Можно выбрать сразу несколько песен — трек создастся для каждой."""
        paths = filedialog.askopenfilenames(
            parent=self.root, title="Выбери песни (можно несколько)",
            filetypes=[("Аудио", " ".join(f"*{e} *{e.upper()}" for e in AUDIO_EXT)),
                       ("Все файлы", "*")])
        songs = [Path(p) for p in paths if Path(p).suffix.lower() in AUDIO_EXT]
        if not songs:
            if paths:
                messagebox.showwarning("Не тот файл", f"Нужен файл {', '.join(AUDIO_EXT)}",
                                       parent=self.root)
            return
        name = simpledialog.askstring(
            "Новый трек", "Название трека:" if len(songs) == 1 else
            f"Песен выбрано: {len(songs)}. Названия возьму из имён файлов.\nНазвание первого:",
            initialvalue=songs[0].stem, parent=self.root)
        if not name or not name.strip():
            return
        tracks = [add_track(songs[0], name)] + [add_track(s) for s in songs[1:]]
        self.refresh(select=tracks[0].name)
        self.open_text(tracks[0], is_new=True)

    def edit_text(self):
        tr = self.current()
        if tr:
            self.open_text(tr)

    def export_phone(self):
        """Разметка трека одним файлом — его открывает веб-версия на телефоне.
        Саму песню не кладём: она большая и остаётся у тебя."""
        tr = self.current(need_audio=True, need_text=True)
        if not tr:
            return
        length = track_length(tr.audio)
        entries = fill_timing(tr.entries(), length)
        scenes, frames, words = tr.scenes(), tr.frames(), tr.keywords()
        try:
            data = beats.track_beats(tr)
        except Exception:
            data = {}
        out = {
            "name": tr.name, "audio": tr.audio.name, "length": round(length, 2),
            "bpm": data.get("bpm"), "beats": data.get("beats", []),
            "strength": data.get("strength", []),
            "levels": data.get("levels", []), "level_fps": data.get("level_fps", 20),
            "lines": [{"t": round(t, 2), "text": s, "big": s in scenes,
                       "frame": frames.get(s), "word": words.get(s)} for t, s in entries],
        }
        path = tr.dir / f"{tr.name}.yeahmusic.json"
        path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        messagebox.showinfo("Для телефона", f"Файл разметки готов:\n{path}\n\n"
                            "Перекинь его и саму песню на телефон, открой веб-версию "
                            "YeahMusic и выбери их там.", parent=self.root)

    def ai_marks(self):
        """ИИ-режиссёр размечает песню: крупные строки, смены кадра, слова крупно."""
        tr = self.current(need_audio=True, need_text=True)
        if not tr:
            return
        if not ai.available():
            messagebox.showwarning("ИИ-разметка", "Нет ключа OpenAI. Положи его рядом с "
                                   "программой в файл .env строкой OPENAI_API_KEY=...",
                                   parent=self.root)
            return
        if (tr.scenes() or tr.frames()) and not messagebox.askyesno(
                "ИИ-разметка", "У трека уже есть разметка. Заменить на новую?",
                parent=self.root):
            return
        for b in self.buttons + [self.play_btn]:
            b.state(["disabled"])
        self.status.config(text="🤖 режиссёр думает…")
        job = {}

        def work():
            try:
                length = track_length(tr.audio)
                job["marks"] = ai.direct(fill_timing(tr.entries(), length), length)
            except Exception as e:
                job["error"] = str(e)

        threading.Thread(target=work, daemon=True).start()
        self.root.after(200, self.ai_marks_wait, tr, job)

    def ai_marks_wait(self, tr, job):
        if not job:
            self.root.after(200, self.ai_marks_wait, tr, job)
            return
        for b in self.buttons + [self.play_btn]:
            b.state(["!disabled"])
        self.status.config(text="")
        if "error" in job:
            messagebox.showwarning("ИИ-разметка", f"Не получилось:\n{job['error']}",
                                   parent=self.root)
            return
        marks = job["marks"]
        tr.save_scenes(marks["scenes"])
        tr.save_frames(marks["frames"])
        tr.save_keywords(marks["keywords"])
        self.refresh(select=tr.name)
        messagebox.showinfo("ИИ-разметка", f"Готово: крупных строк {len(marks['scenes'])}, "
                            f"смен кадра {len(marks['frames'])}, слов крупно "
                            f"{len(marks['keywords'])}.\n\n{marks['note'][:400]}",
                            parent=self.root)

    def auto_time(self):
        """Whisper слушает песню и сам расставляет время строк (в фоне, ~1 мин)."""
        tr = self.current(need_audio=True, need_text=True)
        if not tr:
            return
        entries = tr.entries()
        timed = sum(t is not None for t, _ in entries)
        if timed and not messagebox.askyesno(
                "Автотайминги", f"У трека уже есть тайминги ({timed}/{len(entries)}). "
                "Заменить их автоматическими?", parent=self.root):
            return
        for b in self.buttons + [self.play_btn]:
            b.state(["disabled"])
        job = {"progress": 0.0, "result": None, "error": None}

        def work():
            import autotime
            try:
                # с ключом OpenAI слушает облако (точнее и быстрее), иначе — локальная модель
                engine = ai if ai.available() else autotime
                job["result"] = engine.auto_timings(
                    tr.audio, [s for _, s in entries],
                    progress=lambda f: job.__setitem__("progress", f))
            except Exception as e:
                print("Облако не сработало, слушаю локально:", e)
                job["result"] = autotime.auto_timings(
                    tr.audio, [s for _, s in entries],
                    progress=lambda f: job.__setitem__("progress", f))
            except Exception as e:      # нет модели / нет сети при первом запуске и т.п.
                job["error"] = str(e)

        threading.Thread(target=work, daemon=True).start()
        self.root.after(200, self.auto_time_wait, tr, entries, job)

    def auto_time_wait(self, tr, entries, job):
        if job["result"] is None and job["error"] is None:
            where = "ИИ" if ai.available() else "локально"
            self.status.config(text=f"🎙 слушаю песню ({where})… {int(job['progress'] * 100)}%")
            self.root.after(200, self.auto_time_wait, tr, entries, job)
            return
        for b in self.buttons + [self.play_btn]:
            b.state(["!disabled"])
        self.status.config(text="")
        if job["error"]:
            messagebox.showwarning("Автотайминги", f"Не получилось:\n{job['error']}",
                                   parent=self.root)
            return
        times = job["result"]
        tr.save([[t, s] for t, (_, s) in zip(times, entries)])
        self.refresh(select=tr.name)
        found = sum(t is not None for t in times)
        if messagebox.askyesno(
                "Автотайминги", f"Готово: узнал {found} из {len(times)} строк, остальные "
                "встанут между соседними.\nОткрыть тайминги, чтобы проверить и подправить?",
                parent=self.root):
            self.open_timings(tr)

    def edit_timings(self):
        tr = self.current(need_audio=True, need_text=True)
        if tr:
            self.open_timings(tr)

    def rename(self):
        tr = self.current()
        if not tr:
            return
        name = simpledialog.askstring("Переименовать", "Новое название:",
                                      initialvalue=tr.name, parent=self.root)
        if not name or safe_name(name) == tr.name:
            return
        folder = free_folder(safe_name(name))
        tr.dir.rename(folder)
        self.refresh(select=folder.name)

    def open_folder(self):
        tr = self.current()
        if tr:
            subprocess.Popen(["xdg-open", str(tr.dir)])

    def delete(self):
        tr = self.current()
        if tr and messagebox.askyesno(
                "Удалить", f"Удалить трек «{tr.name}» вместе с песней и текстом?",
                icon="warning", parent=self.root):
            shutil.rmtree(tr.dir)
            self.refresh()

    def play(self):
        if self.player:
            return self.player.stop()
        tr = self.current(need_audio=True, need_text=True)
        if not tr:
            return
        self.stop_camera()
        if self.use_camera.get():
            self.start_camera(lambda: self.player and self.player.stop())
        for b in self.buttons:
            b.state(["disabled"])
        self.play_btn.config(text="■ Стоп  Esc")
        if CARDS_OVERLAY and not (self.overlay and self.overlay.alive()):
            self.overlay = start_overlay()          # слой упал или не стартовал — ещё раз
        self.player = Player(self.root, tr, self.offset, self.speed.get(), self.play_done,
                             avoid=(lambda: self.camera and self.camera.rect)
                             if self.camera else None,
                             overlay=self.overlay, box=self.card_bg.get())
        self.start_booms(tr, self.player)

    def play_done(self):
        self.player = None
        self.stop_camera()
        for b in self.buttons:
            b.state(["!disabled"])
        self.play_btn.config(text="▶ Показать")
        self.root.deiconify()

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        stop_audio()
        if self.camera:
            self.camera.cam.stop()
        if self.overlay:
            self.overlay.close()


def run_tests(gui=False):
    """Тесты логики — сразу. С окнами — на невидимом экране (xvfb-run), чтобы не мешать."""
    import os
    import sys
    import unittest
    if gui and os.environ.get("YEAHMUSIC_GUI_TESTS") != "1":
        env = {**os.environ, "YEAHMUSIC_GUI_TESTS": "1"}
        if shutil.which("xvfb-run"):
            env.pop("WAYLAND_DISPLAY", None)
            os.execvpe("xvfb-run", ["xvfb-run", "-a", sys.executable, __file__, "test", "gui"], env)
        print("xvfb-run не найден — окна тестов будут появляться на экране.\n"
              "Поставить невидимый экран: sudo dnf install xorg-x11-server-Xvfb")
        os.environ.update(env)
    unittest.main(module="test_main", argv=[__file__, "-v"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", help="test — прогнать тесты; иначе не нужен")
    ap.add_argument("what", nargs="?", help="test gui — ещё и тесты с окнами")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="при показе сдвинуть все тайминги в секундах (минус — раньше)")
    args = ap.parse_args()

    if args.mode == "test":
        run_tests(gui=args.what == "gui")

    pygame.mixer.init()
    Library(args.offset).run()


if __name__ == "__main__":
    main()
