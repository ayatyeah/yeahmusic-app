"""Тесты YeahMusic.
    ./run.sh test        — быстрые тесты логики, без окон (можно гонять когда угодно)
    ./run.sh test gui    — плюс тесты с окнами и камерой (на невидимом экране, если есть xvfb-run)

Всё работает во временной папке — настоящие треки и settings.json не трогаются.
Звук идёт в «пустое» устройство, окна открываются на экране на доли секунды.
"""
import math
import os
import shutil
import struct
import tempfile
import time
import tkinter as tk
import unittest
import wave
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import pygame  # noqa: E402

import beats  # noqa: E402
import ai  # noqa: E402
import camfx  # noqa: E402
import hands  # noqa: E402
import main  # noqa: E402

# Тесты с окнами открывают окна и забирают фокус — мешают работать.
# Запускаются только явно: ./run.sh test gui (на невидимом экране, если есть xvfb-run).
HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
GUI_TESTS = HAS_DISPLAY and os.environ.get("YEAHMUSIC_GUI_TESTS") == "1"


def make_wav(path, seconds):
    """Короткий тихий тон — настоящий аудиофайл для тестов."""
    rate = 22050
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(800 * math.sin(i / 20)))
                               for i in range(int(rate * seconds))))
    return path


def destroy(root):
    """Закрыть Tk, сперва отменив таймеры — иначе Tcl ругается «invalid command name»."""
    for job in root.tk.splitlist(root.tk.call("after", "info")):
        root.tk.call("after", "cancel", job)
    root.destroy()


def press(widget, sequence, tag=None, keysym="??"):
    """Нажатие клавиши без настоящей клавиатуры: вызываем привязку напрямую.
    (event_generate доставляет клавиши только окну с фокусом — если в это время
    печатаешь в другом окне, тест бы падал, а твои нажатия попадали бы в тест.)"""
    script = widget.tk.call("bind", tag or widget._w, sequence)
    assert script, f"клавиша {sequence} не привязана к {tag or widget}"
    subs = {"%W": widget._w, "%K": keysym}
    for code in "# b f h k s t w x y A E N T X Y D".split():
        subs.setdefault("%" + code, "??")
    for k, v in subs.items():
        script = script.replace(k, v)
    widget.tk.call("catch", script)


def pump(root, seconds):
    """Крутим цикл Tk, пока идут after()-таймеры."""
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


class FakeCamera:
    """Вместо вебки: отдаёт цветные кадры 640×480."""

    def __init__(self):
        self.running = True
        self.frames = 0

    def query_image(self):
        return self.running

    def get_image(self):
        self.frames += 1
        surf = pygame.Surface((640, 480))
        surf.fill((200, 30, 30))
        surf.fill((30, 30, 200), (0, 0, 320, 480))     # левая половина синяя
        return surf

    def stop(self):
        self.running = False


class FakeTracker:
    """Вместо MediaPipe: руки задаются вручную через .hands."""

    def __init__(self):
        self.hands, self.version, self.error = [], 0, None
        self.segment = False
        self.masks = []

    def mask_at(self, when, max_age=0.5):
        return None

    def submit(self, surf):
        pass

    def latest(self, size):
        return self.hands, self.version

    def latest_faces(self):
        return []


    def close(self):
        pass


class Sandbox(unittest.TestCase):
    """Подменяет папку проекта на временную."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(main, "ROOT", self.root_dir),
            mock.patch.object(main, "TRACKS", self.root_dir / "tracks"),
            mock.patch.object(main, "SETTINGS", self.root_dir / "settings.json"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def make_track(self, name="Трек", lines=None, seconds=3.0, audio=True):
        folder = self.root_dir / "tracks" / name
        folder.mkdir(parents=True)
        if audio:
            make_wav(folder / "song.wav", seconds)
        tr = main.Track(folder)
        if lines is not None:
            tr.save(lines)
        return tr


# ---------- чистая логика ----------

class TestTime(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(main.fmt_time(0), "00:00.00")
        self.assertEqual(main.fmt_time(83.456), "01:23.46")
        self.assertEqual(main.fmt_time(600), "10:00.00")

    def test_parse(self):
        self.assertEqual(main.parse_time("1:23.5"), 83.5)
        self.assertEqual(main.parse_time("83,5"), 83.5)
        self.assertEqual(main.parse_time(" 7 "), 7)
        self.assertEqual(main.parse_time("01:02.25"), 62.25)
        for bad in ("", "abc", "1:2:3", "-5"):
            self.assertIsNone(main.parse_time(bad), bad)

    def test_roundtrip(self):
        for t in (0, 1.4, 59.99, 61.07, 143.5):
            self.assertAlmostEqual(main.parse_time(main.fmt_time(t)), t, places=2)


class TestLyricsFile(Sandbox):
    def test_read_write_roundtrip(self):
        path = self.root_dir / "lyrics.txt"
        entries = [[1.4, "Первая"], [None, "Вторая, без времени"], [65.25, "Третья [с] скобками"]]
        main.write_entries(path, entries)
        self.assertEqual(path.read_text(encoding="utf-8").splitlines(),
                         ["[00:01.40] Первая", "Вторая, без времени", "[01:05.25] Третья [с] скобками"])
        self.assertEqual(main.read_entries(path), entries)

    def test_skips_comments_and_blank(self):
        path = self.root_dir / "lyrics.txt"
        path.write_text("# комментарий\n\n   \n[00:02.00]  строка  \nещё\n", encoding="utf-8")
        self.assertEqual(main.read_entries(path), [[2.0, "строка"], [None, "ещё"]])

    def test_missing_file(self):
        self.assertEqual(main.read_entries(self.root_dir / "nope.txt"), [])


class TestCarryTimings(unittest.TestCase):
    OLD = [[1.0, "a"], [2.0, "b"], [3.0, "c"], [4.0, "d"]]

    def times(self, new_lines):
        return [t for t, _ in main.carry_timings(self.OLD, new_lines)]

    def test_unchanged(self):
        self.assertEqual(self.times(["a", "b", "c", "d"]), [1.0, 2.0, 3.0, 4.0])

    def test_typo_keeps_time(self):
        self.assertEqual(self.times(["a", "B!", "c", "d"]), [1.0, 2.0, 3.0, 4.0])

    def test_inserted_line_is_empty(self):
        self.assertEqual(self.times(["a", "new", "b", "c", "d"]), [1.0, None, 2.0, 3.0, 4.0])

    def test_deleted_line(self):
        self.assertEqual(self.times(["a", "c", "d"]), [1.0, 3.0, 4.0])

    def test_from_nothing(self):
        self.assertEqual([t for t, _ in main.carry_timings([], ["x", "y"])], [None, None])


class TestFillTiming(unittest.TestCase):
    def test_nothing_timed_is_even(self):
        out = main.fill_timing([[None, "a"], [None, "b"], [None, "c"]], 40)
        self.assertEqual([t for t, _ in out], [10, 20, 30])

    def test_interpolates_between_marks(self):
        out = main.fill_timing([[2.0, "a"], [None, "b"], [None, "c"], [8.0, "d"]], 100)
        self.assertEqual([round(t, 2) for t, _ in out], [2.0, 4.0, 6.0, 8.0])

    def test_tail_after_last_mark(self):
        out = main.fill_timing([[10.0, "a"], [None, "b"]], 20)
        self.assertEqual([t for t, _ in out], [10.0, 15.0])

    def test_all_timed_untouched(self):
        entries = [[1.0, "a"], [5.5, "b"]]
        self.assertEqual(main.fill_timing(entries, 60), [(1.0, "a"), (5.5, "b")])


class TestTextCleanup(unittest.TestCase):
    def test_clean_lines(self):
        text = "  Первая   строка \n\n\t\n[Припев]\n(Куплет 1)\nВторая (тихо) строка\n"
        self.assertEqual(main.clean_lines(text),
                         ["Первая строка", "[Припев]", "(Куплет 1)", "Вторая (тихо) строка"])
        self.assertEqual(main.clean_lines(text, drop_tags=True),
                         ["Первая строка", "Вторая (тихо) строка"])

    def test_split_short_unchanged(self):
        self.assertEqual(main.split_long("коротко"), ["коротко"])

    def test_split_at_comma(self):
        line = "Ну там ещё пацанов 5-6 со мной будет, зутусаемся там у тебя на хате, посидим"
        parts = main.split_long(line)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= main.LONG_LINE for p in parts))
        self.assertEqual(" ".join(parts).replace(",", ""), line.replace(",", ""))

    def test_split_without_punctuation(self):
        line = " ".join(["слово"] * 30)
        parts = main.split_long(line, 40)
        self.assertTrue(all(len(p) <= 40 for p in parts))
        self.assertEqual(" ".join(parts), line)

    def test_unsplittable_word(self):
        word = "x" * 100
        self.assertEqual(main.split_long(word), [word])


# ---------- прозрачный слой со строками (без окон на экране) ----------

class FakeOverlay:
    def __init__(self):
        self.sent = []
        self.next_id = 0

    def alive(self):
        return True

    def send(self, **cmd):
        self.sent.append(cmd)
        return True


class TestOverlayCards(unittest.TestCase):
    def test_char_ms(self):
        self.assertEqual(main.char_ms("x" * 20, 60, 1.0), main.CHAR_MS)
        self.assertEqual(main.char_ms("x" * 20, 60, 2.0), main.CHAR_MS // 2)
        self.assertEqual(main.char_ms("x" * 100, 1, 1.0), main.MIN_CHAR_MS)

    def test_card_commands(self):
        ov = FakeOverlay()
        a = main.OverlayCard(ov, "первая", 10, 500, 3, 1.0)
        b = main.OverlayCard(ov, "вторая", 20, 600, 3, 1.0)
        self.assertEqual(ov.sent[0], {"cmd": "add", "id": 1, "text": "первая", "x": 10, "y": 500,
                                      "w": main.BOX_W, "h": main.BOX_H, "char_ms": a.char_ms,
                                      "box": False})
        self.assertEqual(b.id, 2)
        main.OverlayCard(ov, "с фоном", 0, 0, 3, 1.0, box=True)
        self.assertTrue(ov.sent[-1]["box"], "«Фон у строк» уходит в слой")
        a.rise(600)
        self.assertFalse(a.offscreen(), "ещё виден краешек")
        a.rise(200)
        self.assertTrue(a.offscreen())
        a.close()
        self.assertEqual(ov.sent[-1], {"cmd": "remove", "id": 1})

    def test_overlay_disabled(self):
        with mock.patch.object(main, "CARDS_OVERLAY", False):
            self.assertIsNone(main.start_overlay())


class TestOverlayProcess(unittest.TestCase):
    """Настоящий процесс слоя, но в невидимом режиме Qt (offscreen) — на экране ничего."""

    def test_commands_and_quit(self):
        with mock.patch.dict(os.environ, {"YEAHMUSIC_QT_PLATFORM": "offscreen"}):
            ov = main.start_overlay()
        self.assertIsNotNone(ov, "Qt установлен и слой стартует")
        try:
            self.assertEqual(ov.proc.stdout.readline().strip(), b"ready")
            self.assertTrue(ov.send(cmd="add", id=1, text="строка", x=10, y=10, w=380, h=230,
                                    char_ms=50))
            self.assertTrue(ov.send(cmd="pause", on=True))
            self.assertTrue(ov.send(cmd="clear"))
            time.sleep(0.2)
            self.assertTrue(ov.alive(), "команды не роняют слой")
        finally:
            ov.close()
        self.assertEqual(ov.proc.returncode, 0)
        self.assertFalse(ov.send(cmd="clear"), "после выхода send просто возвращает False")


class TestOverlayLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import overlay
        cls.ov = overlay

    def card(self, text="Раз, два", char_ms=100):
        return self.ov.Card({"id": 1, "text": text, "x": 0, "y": 0, "w": 380, "h": 230,
                             "char_ms": char_ms}, 0.0)

    def test_typing_with_punctuation_pause(self):
        c = self.card()
        c.step(0)
        self.assertEqual(c.i, 1)
        c.step(350)                        # «Раз,» — после запятой пауза 2.5×
        self.assertEqual(c.text[:c.i], "Раз,")
        c.step(500)
        self.assertEqual(c.text[:c.i], "Раз,")
        c.step(600)                        # запятая в 300 + 250 → пробел в 550
        self.assertEqual(c.text[:c.i], "Раз, ")
        c.step(10_000)
        self.assertEqual(c.i, len(c.text))

    def test_fade(self):
        c = self.card()
        self.assertEqual(c.alpha(0), 0)
        self.assertEqual(c.alpha(self.ov.FADE_MS), 1)
        c.leaving_at = 1000
        self.assertAlmostEqual(c.alpha(1000 + self.ov.FADE_MS / 2), 0.5)
        self.assertEqual(c.alpha(2000), 0)


# ---------- руки и «пистолетик» (без окон) ----------

GUN = [(0, 0), (20, -20), (35, -45), (45, -70), (55, -95),          # большой — вверх
       (60, -10), (100, -10), (130, -10), (160, -10),               # указательный — прямо
       (60, 5), (85, 10), (80, 25), (65, 25),                       # средний поджат
       (55, 18), (78, 22), (72, 35), (58, 33),                      # безымянный поджат
       (48, 30), (66, 34), (62, 44), (50, 42)]                      # мизинец поджат


def hand(pose=GUN, at=(300, 400), lift=0.0, **fingers):
    """Рука в пикселях: pose со сдвигом в точку at; lift — подъём всей кисти вверх.
    fingers=dict(ring=[...4 точки...]) — заменить палец."""
    pts = list(pose)
    for name, idx in (("thumb", 1), ("index", 5), ("middle", 9), ("ring", 13), ("pinky", 17)):
        if name in fingers:
            pts[idx:idx + 4] = fingers[name]
    return [(x + at[0], y + at[1] - lift) for x, y in pts]


OPEN_RING = [(55, 18), (95, 25), (125, 30), (150, 35)]
OPEN_PINKY = [(48, 30), (80, 40), (105, 48), (125, 55)]
FIST_INDEX = [(60, -10), (85, -5), (80, 10), (65, 8)]


class TestGunPose(unittest.TestCase):
    def test_gun(self):
        self.assertTrue(hands.is_gun(hand()))

    def test_open_hand_is_not_gun(self):
        self.assertFalse(hands.is_gun(hand(ring=OPEN_RING, pinky=OPEN_PINKY)))

    def test_fist_is_not_gun(self):
        self.assertFalse(hands.is_gun(hand(index=FIST_INDEX)))

    def test_thumb_down_is_not_gun(self):
        down = [(20, 20), (35, 45), (45, 70), (55, 95)]
        self.assertFalse(hands.is_gun(hand(thumb=down)))

    def test_rotation_and_scale(self):
        a = math.radians(-30)                  # пистолет чуть вверх и в 1.5 раза больше
        rot = [(1.5 * (x * math.cos(a) - y * math.sin(a)),
                1.5 * (x * math.sin(a) + y * math.cos(a))) for x, y in GUN]
        self.assertTrue(hands.is_gun(hand(rot)))


class TestGunShot(unittest.TestCase):
    def feed(self, lifts, dt=0.05, det=None, t0=0.0, frames=None):
        det = det or hands.GunDetector()
        shots = []
        for i, lift in enumerate(lifts):
            pts = frames[i] if frames else [hand(lift=lift)]
            shot = det.feed(pts, t0 + i * dt)
            if shot:
                shots.append(shot)
        return shots, det

    ARM = [0] * 7                                 # 0.35 с держим позу — взвели

    def test_kick_fires(self):
        shots, _ = self.feed(self.ARM + [15, 45])
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0].muzzle, (460, 390), "из точки, где был кончик до рывка")
        self.assertAlmostEqual(shots[0].direction[0], 1.0, places=3)

    def test_slow_raise_does_not_fire(self):
        self.assertEqual(self.feed([i * 3 for i in range(30)])[0], [])

    def test_kick_down_does_not_fire(self):
        self.assertEqual(self.feed([45, 30, 15, 0])[0], [])

    def test_no_pose_no_shot(self):
        open_hand = lambda lift: [hand(lift=lift, ring=OPEN_RING, pinky=OPEN_PINKY)]  # noqa: E731
        frames = [open_hand(v) for v in (0, 0, 0, 15, 45)]
        self.assertEqual(self.feed([0] * 5, frames=frames)[0], [])

    def test_blurred_pose_in_kick_still_fires(self):
        frames = [[hand()]] * 7 + [[hand(lift=20, ring=OPEN_RING)],
                                   [hand(lift=45, ring=OPEN_RING, pinky=OPEN_PINKY)]]
        self.assertEqual(len(self.feed([0] * 9, frames=frames)[0]), 1)

    def test_not_armed_no_shot(self):
        """Поза мелькнула на миг и рука дёрнулась — это не выстрел."""
        self.assertEqual(self.feed([0, 0, 15, 45])[0], [])

    def test_tilt_fires(self):
        """Кивок стволом вверх (кисть наклонилась), даже если кончик почти не сдвинулся."""
        def tilted(deg):
            a = math.radians(-deg)
            tx, ty = GUN[8]
            pose = [(tx + (x - tx) * math.cos(a) - (y - ty) * math.sin(a),
                     ty + (x - tx) * math.sin(a) + (y - ty) * math.cos(a)) for x, y in GUN]
            return [hand(pose)]
        frames = [tilted(0)] * 7 + [tilted(12), tilted(26)]
        self.assertEqual(len(self.feed([0] * 9, frames=frames)[0]), 1)

    def test_cooldown(self):
        shots, det = self.feed(self.ARM + [45])
        more, _ = self.feed([0, 0, 45], det=det, t0=0.5)
        self.assertEqual((len(shots), len(more)), (1, 0), "после выстрела — снова взвести")
        later, _ = self.feed(self.ARM + [45], det=det, t0=2.0)
        self.assertEqual(len(later), 1)

    def test_aiming_flag(self):
        det = hands.GunDetector()
        det.feed([hand()], 1.0)
        self.assertTrue(det.aiming(1.1))
        self.assertFalse(det.armed(1.1), "ещё не взведён")
        det.feed([hand()], 1.2)
        det.feed([hand()], 1.3)
        self.assertTrue(det.armed(1.3))
        self.assertFalse(det.aiming(2.0))


THUMB_OUT_RIGHT = [(20, -10), (50, -15), (85, -18), (120, -20)]   # большой вправо


class TestThumbGun(unittest.TestCase):
    def thumb(self, lift=0.0):
        return hand(at=(300, 400), lift=lift, index=FIST_INDEX, thumb=THUMB_OUT_RIGHT)

    def test_pose(self):
        pts = self.thumb()
        self.assertTrue(hands.is_thumb_gun(pts))
        self.assertFalse(hands.is_gun(pts))
        tip, aim = hands.gun_barrel(pts)
        self.assertEqual(tip, pts[4], "ствол — кончик большого")
        self.assertGreater(aim[0], 0.9, "смотрит вправо")
        tucked = [(20, -10), (40, -14), (52, -14), (60, -12)]          # большой прижат
        self.assertIsNone(hands.gun_barrel(hand(index=FIST_INDEX, thumb=tucked)), "просто кулак")
        self.assertIsNone(hands.gun_barrel(hand(index=FIST_INDEX)), "«класс» — не пистолет")

    def test_thumb_shot(self):
        det = hands.GunDetector()
        shots = [det.feed([self.thumb(lift)], i * 0.05)
                 for i, lift in enumerate([0] * 7 + [20, 45])]
        shots = [x for x in shots if x]
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0].muzzle, self.thumb()[4], "выстрел из большого пальца")


class TestHandDrawing(unittest.TestCase):
    def test_hull(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
        self.assertEqual(sorted(hands.hull(pts)), [(0, 0), (0, 10), (10, 0), (10, 10)])

    def test_draw_hand_and_flash(self):
        surf = pygame.Surface((600, 600))
        hands.draw_hand(surf, hand(), gun=True)
        core = tuple(surf.get_at((415, 390)))[:3]
        self.assertGreater(min(core), 200, "сердцевина пальца — белая")
        glow = tuple(surf.get_at((415, 395)))[:3]
        self.assertGreater(glow[0], glow[2], "вокруг — свечение цветом пистолета")
        self.assertLess(max(glow), 200, "свечение мягкое, не заливка")
        self.assertEqual(tuple(surf.get_at((415, 420)))[:3], (0, 0, 0), "дальше — чисто")
        web = tuple(surf.get_at((330, 395)))[:3]
        self.assertNotEqual(web, (0, 0, 0), "ладонь-паутинка есть")
        self.assertLess(max(web), max(glow) + 40, "но тусклее пальцев")
        self.assertTrue(any(tuple(surf.get_at((x, 390)))[:3] == hands.GUN_COLOR
                            for x in range(470, 560)), "пунктир прицела красный")
        hands.draw_muzzle_flash(surf, (100, 100), 0.0, 1)
        self.assertNotEqual(tuple(surf.get_at((100, 100)))[:3], (0, 0, 0))

    def test_gunshot_sound(self):
        pygame.mixer.init()
        snd = hands.gunshot_sound()
        self.assertAlmostEqual(snd.get_length(), 0.45, delta=0.02)


def make_clicks(path, bpm=120, seconds=20, rate=22050, offset=0.25):
    """Трек-«метроном»: низкий удар (как бочка) каждые 60/bpm секунд."""
    import numpy as np
    n = int(rate * seconds)
    x = np.random.default_rng(1).normal(0, 0.01, n)                # лёгкий шум
    t = np.arange(int(rate * 0.12)) / rate
    kick = np.sin(2 * math.pi * 60 * t) * np.exp(-t * 30)
    hits = np.arange(offset, seconds - 0.2, 60 / bpm)
    for h in hits:
        i = int(h * rate)
        x[i:i + len(kick)] += kick[:n - i]
    data = (np.clip(x, -1, 1) * 30000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return hits


class TestBeats(Sandbox):
    @classmethod
    def setUpClass(cls):
        pygame.mixer.init()

    def test_tempo_and_grid(self):
        hits = make_clicks(self.root_dir / "c.wav", bpm=120)
        b = beats.analyze(self.root_dir / "c.wav")
        self.assertAlmostEqual(b["bpm"], 120, delta=2)
        found = b["beats"]
        close = [min(abs(f - h) for f in found) for h in hits[1:-1]]
        self.assertLess(max(close), 0.015, "биты попадают в удары (±15 мс)")

    def test_fast_track(self):
        make_clicks(self.root_dir / "f.wav", bpm=150)
        self.assertAlmostEqual(beats.analyze(self.root_dir / "f.wav")["bpm"], 150, delta=3)

    def test_cache(self):
        tr = self.make_track("B", [[None, "a"]], audio=False)
        make_clicks(tr.dir / "song.wav", bpm=100, seconds=8)
        with mock.patch.object(beats, "analyze", wraps=beats.analyze) as spy:
            first = beats.track_beats(tr)
            again = beats.track_beats(tr)
        self.assertEqual(spy.call_count, 1, "второй раз — из beats.json")
        self.assertEqual(first, again)

    def test_schedule_modes(self):
        data = {"bpm": 100, "beats": [0, 1, 2, 3, 4, 5], "strength": [1, 0.2, 1, 1, 1, 1]}
        self.assertEqual(beats.boom_schedule(data, "off", []), [])
        self.assertEqual([b[0] for b in beats.boom_schedule(data, "always", [])], [0, 2, 3, 4, 5],
                         "слабый удар пропускаем")
        whole = {b[0]: b[1] for b in beats.boom_schedule(data, "always", [(2, 4)])}
        self.assertEqual((whole[2], whole[0]), (1, beats.VERSE_POWER), "в припеве сильнее")
        self.assertEqual([b[0] for b in beats.boom_schedule(data, "chorus", [(2, 4)])], [2, 3])

    def test_half_time_for_fast_songs(self):
        data = {"bpm": 150, "beats": [0, 1, 2, 3, 4, 5], "strength": [0.5, 1, 0.5, 1, 0.5, 1]}
        self.assertEqual([b[0] for b in beats.boom_schedule(data, "always", [])], [1, 3, 5],
                         "через бит — по сильным")

    def test_special_moment(self):
        data = {"bpm": 150, "beats": [0, 1, 2, 3, 4, 5, 6, 7],
                "strength": [0.5, 1, 0.5, 1, 0.5, 1, 0.5, 1]}
        booms = beats.boom_schedule(data, "always", [], moments=[(2, 6)])
        times = [b[0] for b in booms]
        self.assertEqual(times, [1, 2, 3, 4, 5, 7], "в моменте — каждый бит, вне — через один")
        sways = [b[2] for b in booms if 2 <= b[0] < 6]
        self.assertEqual(sways, [1, -1, 1, -1], "окно качается влево-вправо")
        self.assertTrue(all(b[2] == 0 for b in booms if not 2 <= b[0] < 6))
        in_moment = {b[0]: b[1] for b in booms}
        self.assertEqual(in_moment[3], 1.0, "сила — как в припеве, не ослаблена")

    def test_moment_ranges(self):
        times = [0, 5, 10, 15, 20, 25, 30]
        flags = [False, True, False, True, False, False, False]
        self.assertEqual(camfx.moment_ranges(times, flags, 40), [(5, 20)],
                         "плашка между крупными — тоже момент")
        flags = [True, False, False, False, False, True, False]
        self.assertEqual(camfx.moment_ranges(times, flags, 40), [(0, 5), (25, 30)],
                         "далеко друг от друга — два момента")
        self.assertEqual(camfx.moment_ranges(times, [True] + [False] * 6, 40), [(0, 5)])

    def test_chorus_ranges(self):
        chorus = ["Зима пройдёт", "Ты в мыслях", "Навсегда"]
        lines = chorus + ["Куплет"] + chorus
        entries = list(zip([0, 3, 6, 10, 20, 23, 26], lines))
        self.assertEqual(beats.chorus_ranges(entries, 40), [(0, 10), (20, 40)])
        self.assertEqual(beats.chorus_ranges(list(zip([0, 5], ["а", "б"])), 10), [], "нет повторов")


PEACE_INDEX = [(60, -10), (80, -40), (95, -65), (110, -90)]     # указательный вверх-вправо
PEACE_MIDDLE = [(60, 5), (95, -5), (125, -12), (150, -20)]      # средний вправо


class TestSigns(unittest.TestCase):
    def heart_hands(self, gap=0):
        """Две руки: кончики указательных сведены вверху, больших — внизу."""
        a = [(0, 0)] * 21
        b = [(0, 0)] * 21
        for pts, side in ((a, -1), (b, 1)):
            pts[0] = (300 + side * 120, 400)             # запястья по бокам
            pts[9] = (300 + side * 60, 330)              # ладонь ~92 px
            pts[8] = (300 + side * gap, 250)             # указательные вверху
            pts[4] = (300 + side * gap, 400)             # большие внизу
        return [a, b]

    def test_heart(self):
        self.assertIsNotNone(hands.heart_center(self.heart_hands()))
        self.assertIsNone(hands.heart_center(self.heart_hands(gap=80)), "пальцы разведены")
        self.assertIsNone(hands.heart_center(self.heart_hands()[:1]), "одна рука")

    def test_hearts_fly(self):
        g = hands.Gestures(seed=1)
        for i in range(10):
            g.feed(self.heart_hands(), i * 0.05)
        self.assertGreater(len(g.hearts), 5)
        y0 = g.hearts[0][1]
        g.feed([], 0.6)
        self.assertLess(g.hearts[0][1], y0, "сердечки летят вверх")

    def test_peace(self):
        v = hand(index=PEACE_INDEX, middle=PEACE_MIDDLE)
        self.assertTrue(hands.is_peace(v))
        self.assertFalse(hands.is_peace(hand()), "пистолет — не V")
        g = hands.Gestures()
        events = [g.feed([v], i * 0.05) for i in range(20)]
        self.assertEqual(events.count("peace"), 1, "держишь — один щелчок")
        g.feed([], 1.1)
        events = [g.feed([v], 1.5 + i * 0.05) for i in range(10)]
        self.assertEqual(events.count("peace"), 1, "опустил и показал снова — ещё щелчок")


class TestCamFx(unittest.TestCase):
    def test_keyword(self):
        lines = ["Ты в моих мыслях навсегда", "В мыслях навсегда", "Ты в моих мыслях навсегда",
                 "Хочу увидеть тебя"]
        counts = camfx.keyword_counts(lines)
        self.assertEqual(camfx.keyword(lines[0], counts, True), ("навсегда", 17))
        self.assertIsNotNone(camfx.keyword(lines[0], counts, False), "хук и в куплете")
        self.assertIsNone(camfx.keyword(lines[3], counts, False), "в куплете — только хук")
        self.assertEqual(camfx.keyword(lines[3], counts, True)[0], "увидеть")

    def test_zoom(self):
        z = camfx.Zoom()
        self.assertEqual(z.step(), (0.0, 0.0, 1.0, 1.0))
        z.set_chorus(True)
        for _ in range(200):
            crop = z.step()
        self.assertAlmostEqual(crop[2], 1 / camfx.ZOOM_CHORUS, places=2)
        z.focus = (0.99, 0.01)                       # лицо у края — кадр не вылезает
        x0, y0, cw, ch = z.step()
        self.assertTrue(0 <= x0 and x0 + cw <= 1.0001 and 0 <= y0 and y0 + ch <= 1.0001)
        surf = pygame.Surface((400, 600))
        self.assertLess(z.apply(surf).get_width(), 400)
        z.set_chorus(False)
        for _ in range(200):
            z.step()
        self.assertEqual(z.apply(surf).get_size(), (400, 600), "в куплете — без зума")

    def test_face_mapping(self):
        z = camfx.Zoom()
        z.crop = (0.25, 0.25, 0.5, 0.5)
        for _ in range(60):
            z.see_face(0.5, 0.5)                     # центр приближенного — центр полного
        self.assertAlmostEqual(z.focus[0], 0.5, places=2)

    def test_silhouette(self):
        import numpy as np
        mask = np.zeros((40, 30), np.float32)
        mask[10:30, 8:22] = 1.0                       # «человек» в центре
        surf = pygame.Surface((300, 400))
        camfx.draw_ghosts(surf, [mask, np.roll(mask, 5, 1)])
        inside = tuple(surf.get_at((150, 200)))[:3]
        self.assertNotEqual(inside, (0, 0, 0), "тень-двойник видна")
        self.assertEqual(tuple(surf.get_at((10, 10)))[:3], (0, 0, 0), "фон не трогаем")
        edge = pygame.Surface((300, 400))
        camfx.draw_outline(edge, mask, 0.5)
        border = tuple(edge.get_at((150, 103)))[:3]
        self.assertGreater(sum(border), 200, "контур светится")
        self.assertLess(sum(tuple(edge.get_at((150, 200)))[:3]), 60, "внутри силуэта — пусто")
        camfx.draw_outline(edge, None, 0.5)           # маски нет — просто ничего

    def test_side_word_and_shout(self):
        self.assertEqual(camfx.side_word("У-у, она как будто сериал"), "сериал")
        self.assertIsNone(camfx.side_word("Ты в моих"))
        self.assertTrue(camfx.shout("У-у, из-за неё ****"))
        self.assertTrue(camfx.shout("(О-о) что-то"))
        self.assertFalse(camfx.shout("Ты в моих мыслях"))
        self.assertFalse(camfx.shout("Утро"))

    def test_scenes_file(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self.assertEqual(camfx.load_scenes(folder), set())
            camfx.save_scenes(folder, {"строка"})
            self.assertEqual(camfx.load_scenes(folder), {"строка"})
            camfx.save_scenes(folder, set())
            self.assertFalse((folder / camfx.SCENES_FILE).exists(), "пусто — файла нет")
        self.assertEqual(camfx.strip_old_mark("🎬 Хочу"), ("Хочу", True))
        self.assertEqual(camfx.strip_old_mark("Хочу"), ("Хочу", False))



    def test_frames_file(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self.assertEqual(camfx.load_frames(folder), {})
            camfx.save_frames(folder, {"а": "wide", "б": "мусор"})
            self.assertEqual(camfx.load_frames(folder), {"а": "wide"}, "непонятные формы — мимо")
            camfx.save_frames(folder, {})
            self.assertFalse((folder / camfx.FRAMES_FILE).exists())
        seq = [camfx.next_shape(None)]
        while seq[-1] is not None:
            seq.append(camfx.next_shape(seq[-1]))
        self.assertEqual(seq, ["wide", "medium", "tall", None])


@unittest.skipUnless(HAS_DISPLAY, "нет экрана")
class TestPlayerHeadless(Sandbox):
    """Показ целиком, но окно Tk скрыто, а слой — заглушка: на экране ничего не появляется.
    Ловит ошибки вроде «показ падает на старте», которые иначе видны только глазами."""

    def run_player(self, lines, seconds=4.0, run=1.5, frames=None):
        tr = self.make_track("P", lines, seconds=seconds)
        if frames:
            tr.save_frames(frames)
        pygame.mixer.init()
        root = tk.Tk()
        root.withdraw()
        errors = []
        root.report_callback_exception = lambda *a: errors.append(a[1])
        ov = FakeOverlay()
        events = []
        try:
            player = main.Player(root, tr, 0, 1.0, lambda: None, overlay=ov)
            player.on_scene = lambda *a: events.append(a[0])
            player.on_frame = lambda shape: events.append(shape)
            pump(root, run)
            player.stop()
        finally:
            main.stop_audio()
            destroy(root)
        self.assertEqual(errors, [])
        return ov.sent, events

    def test_lines_reach_overlay(self):
        sent, _ = self.run_player([[0.0, "первая строка"], [0.5, "вторая строка"]])
        texts = [c["text"] for c in sent if c["cmd"] == "add"]
        self.assertEqual(texts, ["первая строка", "вторая строка"])

    def test_moment_shake_and_edge_words(self):
        lines = [[0.0, "🎬 Хочу надежды"], [0.3, "У-у, она как будто сериал"],
                 [0.6, "🎬 Серия последняя"], [0.9, "обычная строка потом"]]
        tr = self.make_track("P", lines, seconds=4)
        pygame.mixer.init()
        root = tk.Tk()
        root.withdraw()
        ov = FakeOverlay()
        shakes = []
        try:
            player = main.Player(root, tr, 0, 1.0, lambda: None, overlay=ov)
            player.on_shake = shakes.append
            pump(root, 1.4)
            player.stop()
        finally:
            main.stop_audio()
            destroy(root)
        self.assertEqual(shakes, [main.SHAKE_HARD], "тряска на «У-у» в особом моменте")
        sides = [c for c in ov.sent if c["cmd"] == "side"]
        self.assertEqual([c["text"] for c in sides], ["СЕРИАЛ"], "слово на край — в моменте")

    def test_beat_effects_sent(self):
        """На ударах слой получает толчок строк и громкость для эквалайзера."""
        tr = self.make_track("P", [[0.0, "первая строка"], [0.6, "вторая строка"]], seconds=4)
        pygame.mixer.init()
        root = tk.Tk()
        root.withdraw()
        ov = FakeOverlay()
        try:
            player = main.Player(root, tr, 0, 1.0, lambda: None, overlay=ov)
            player.set_music([(0.2, 1.0, 0), (0.5, 0.3, 0)], lambda *a: None,
                             levels=[[90, 70, 50, 30, 10]] * 100, level_fps=20)
            pump(root, 1.0)
            player.stop()
        finally:
            main.stop_audio()
            destroy(root)
        pushes = [c for c in ov.sent if c["cmd"] == "push"]
        self.assertEqual([c["power"] for c in pushes], [1.0], "слабый удар строки не толкает")
        eq = [c for c in ov.sent if c["cmd"] == "eq"]
        self.assertGreater(len(eq), 5, "громкость идёт постоянно")
        self.assertEqual(eq[0]["levels"], [90, 70, 50, 30, 10])

    def test_frame_marks(self):
        _, events = self.run_player([[0.0, "раз"], [0.3, "два"], [0.6, "три"]],
                                    frames={"раз": "wide", "три": "medium"})
        self.assertEqual(events, ["wide", "medium"])

    def test_scene_and_censor(self):
        sent, scenes = self.run_player([[0.0, "🎬 Хочу"], [0.3, "🎬 из-за неё ****"],
                                        [0.6, "обычная строка"]])
        center = [c["text"] for c in sent if c["cmd"] == "center"]
        cards = [c["text"] for c in sent if c["cmd"] == "add"]
        self.assertEqual(center, ["Хочу", "из-за неё ****"], "сцены — крупно по центру")
        self.assertEqual(cards, ["обычная строка"])
        tr = main.Track(self.root_dir / "tracks" / "P")
        self.assertEqual([s for _, s in tr.entries()][0], "Хочу", "значок убран из текста")
        self.assertEqual(tr.scenes(), {"Хочу", "из-за неё ****"}, "пометка — в scenes.json")
        self.assertEqual(scenes[:2], ["start", "line"])
        self.assertEqual(sorted(scenes[2:]), ["bleep", "end"], "пи-ип на ****, потом конец сцены")


@unittest.skipUnless(HAS_DISPLAY, "нет экрана")
class TestPlayButton(Sandbox):
    """Кнопка «▶ Показать» целиком: главное окно скрыто, слой — в невидимом режиме Qt,
    камера и руки — заглушки. Ловит поломки между камерой, слоем и показом."""

    def test_play_with_camera_in_overlay(self):
        self.make_track("P", [[0.0, "первая"], [0.4, "вторая"]], seconds=5)
        overlay_py = Path(main.__file__).parent / "overlay.py"
        shutil.copy(overlay_py, self.root_dir / "overlay.py")
        pygame.mixer.init()
        orig_tk = tk.Tk

        def hidden_tk(*a, **k):
            r = orig_tk(*a, **k)
            r.withdraw()
            r.deiconify = lambda: None
            return r
        cams = []

        def fake_open(device=None):
            cams.append(FakeCamera())
            return cams[-1]
        with mock.patch.object(tk, "Tk", hidden_tk), \
             mock.patch.object(main, "open_camera", fake_open), \
             mock.patch.object(main, "HandTracker", FakeTracker), \
             mock.patch.dict(os.environ, {"YEAHMUSIC_QT_PLATFORM": "offscreen"}):
            lib = main.Library(0)
            errors = []
            lib.root.report_callback_exception = lambda *a: errors.append(a[1])
            try:
                self.assertIsNotNone(lib.overlay, "слой стартовал")
                lib.use_camera.set(True)
                lib.play()
                self.assertIsNotNone(lib.camera)
                self.assertIsNotNone(lib.player, "показ запустился после камеры")
                self.assertIsNotNone(lib.camera.shm, "камеру рисует слой")
                pump(lib.root, 1.0)
                self.assertEqual(len(lib.player.cards), 2, "строки ушли на экран")
                self.assertEqual(errors, [])
                self.assertGreater(lib.camera.seq, 3, "кадры камеры идут")
                lib.play()                                   # стоп
                self.assertIsNone(lib.camera)
            finally:
                if lib.player:
                    lib.player.stop()
                lib.stop_camera()
                if lib.overlay:
                    lib.overlay.close()
                main.stop_audio()
                destroy(lib.root)


class TestAI(unittest.TestCase):
    """ИИ-часть: разбор ответа модели и поиск ключа. Сеть не трогаем."""

    ENTRIES = [(0.0, "первая строка"), (3.0, "вторая строка"), (6.0, "третья строка"),
               (9.0, "четвёртая строка"), (12.0, "пятая строка")]

    def test_apply_marks(self):
        answer = {"note": "так задумано", "lines": [
            {"i": 0, "big": True, "frame": "wide", "keyword": "первая"},
            {"i": 1, "big": True, "frame": None, "keyword": None},      # подряд — отбросим
            {"i": 2, "big": False, "frame": "чепуха", "keyword": None},  # формы такой нет
            {"i": 3, "big": True, "frame": "tall", "keyword": "выдумка"},  # слова нет в строке
            {"i": 99, "big": True, "frame": "wide", "keyword": None},   # строки нет
        ]}
        marks = ai.apply_marks(self.ENTRIES, answer)
        self.assertEqual(marks["scenes"], {"первая строка", "четвёртая строка"})
        self.assertEqual(marks["frames"], {"первая строка": "wide", "четвёртая строка": "tall"})
        self.assertEqual(marks["keywords"], {"первая строка": "первая"})
        self.assertEqual(marks["note"], "так задумано")

    def test_apply_marks_empty(self):
        self.assertEqual(ai.apply_marks(self.ENTRIES, {})["scenes"], set())

    def test_key_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / ".env").write_text("OPENAI_API_KEY=sk-test-123\n", encoding="utf-8")
            with mock.patch.object(ai, "ROOT", folder), \
                 mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(ai.load_key(), "sk-test-123")
                self.assertTrue(ai.available())
            with mock.patch.object(ai, "ROOT", folder / "нет"), \
                 mock.patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(ai.load_key())
                self.assertFalse(ai.available())

    def test_key_from_env(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}):
            self.assertEqual(ai.load_key(), "sk-env")


class TestAutoTime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import autotime
        cls.at = autotime

    def test_words(self):
        self.assertEqual(self.at.split_words("Ты в моих мыслях-лях, навсегда!"),
                         ["ты", "в", "моих", "мыслях", "лях", "навсегда"])
        self.assertEqual(self.at.split_words("Зима пройдёт"), ["зима", "пройдет"])

    def test_fuzzy_align(self):
        pairs = self.at.align(["хочу", "трогать", "твое", "тело"], ["хочу", "тухать", "твой", "тело"])
        self.assertEqual(pairs.get(0), 0)
        self.assertEqual(pairs.get(3), 3)

    def test_line_times(self):
        lines = ["Первая строчка песни", "Вторая строчка тоже", "Третья пропала совсем",
                 "Четвёртая строчка есть"]
        words = [("первая", 1.0), ("строчка", 1.4), ("песни", 1.8),
                 ("вторая", 4.0), ("строчка", 4.4), ("тоже", 4.8),
                 ("четвертая", 10.0), ("строчка", 10.5), ("есть", 11.0)]
        self.assertEqual(self.at.line_times(lines, words), [1.0, 4.0, None, 10.0])

    def test_too_fast_is_dropped(self):
        lines = ["Раз два три четыре пять шесть", "Семь восемь девять десять"]
        words = [("раз", 1.0), ("семь", 1.3)]
        self.assertEqual(self.at.line_times(lines, words)[1], None)

    def test_chorus_copied(self):
        chorus = ["Зима пройдёт года", "Ты в мыслях навсегда", "Мыслях навсегда опять"]
        lines = chorus + ["Куплет посередине тут"] + chorus
        words = [("зима", 1.0), ("мыслях", 4.0), ("мыслях", 7.0), ("куплет", 10.0),
                 ("зима", 20.0)]                     # второй припев Whisper «съел»
        times = self.at.line_times(lines, words)
        self.assertEqual(self.at.repeats(lines), [(0, 4, 3)])
        self.assertEqual(times[4], 20.0)
        self.assertAlmostEqual(times[5] - times[4], times[1] - times[0], places=1)
        self.assertAlmostEqual(times[6] - times[4], times[2] - times[0], places=1)


class TestHandTracker(unittest.TestCase):
    """Настоящий MediaPipe на пустых кадрах: рук нет, ошибок нет. Окон не открывает."""

    @unittest.skipUnless(hands.MODEL.exists(), "нет модели models/hand_landmarker.task")
    def test_blank_frames(self):
        tr = hands.HandTracker()
        try:
            end = time.time() + 15
            while tr.version < 3 and time.time() < end and tr.error is None:
                tr.submit(pygame.Surface((558, 744)))
                time.sleep(0.05)
            self.assertIsNone(tr.error)
            self.assertGreaterEqual(tr.version, 3, "кадры распознаются")
            self.assertEqual(tr.latest((558, 744))[0], [])
        finally:
            tr.close()
        self.assertFalse(tr.thread.is_alive())


# ---------- треки, папки, настройки ----------

class TestTracks(Sandbox):
    def test_safe_name(self):
        self.assertEqual(main.safe_name('a/b:c*"d'), "a b c d")
        self.assertEqual(main.safe_name("  ..  "), "трек")
        self.assertEqual(main.safe_name("Мой   трек"), "Мой трек")

    def test_free_folder(self):
        (self.root_dir / "tracks" / "x").mkdir(parents=True)
        self.assertEqual(main.free_folder("x").name, "x 2")
        self.assertEqual(main.free_folder("y").name, "y")

    def test_list_and_audio(self):
        a = self.make_track("b-трек")
        self.make_track("А без песни", audio=False)
        names = [t.name for t in main.list_tracks()]
        self.assertEqual(sorted(names), sorted(["b-трек", "А без песни"]))
        self.assertEqual(a.audio.name, "song.wav")
        self.assertIsNone(main.Track(self.root_dir / "tracks" / "А без песни").audio)

    def test_import_loose_audio(self):
        """Песни, скинутые в папку программы, сами становятся треками."""
        make_wav(self.root_dir / "old song.wav", 1)
        make_wav(self.root_dir / "вторая.wav", 1)
        (self.root_dir / "lyrics.txt").write_text("[00:01.00] раз\n", encoding="utf-8")
        with mock.patch("builtins.print"):
            added = main.import_loose_audio()
        self.assertEqual(sorted(t.name for t in added), ["old song", "вторая"])
        folder = self.root_dir / "tracks" / "old song"
        self.assertTrue((folder / "old song.wav").exists())
        self.assertEqual(main.Track(folder).entries(), [[1.0, "раз"]], "старый lyrics.txt — сюда")
        self.assertFalse((self.root_dir / "lyrics.txt").exists())
        with mock.patch("builtins.print"):
            self.assertEqual(main.import_loose_audio(), [], "второй раз — нечего добавлять")
        self.assertEqual(len(main.list_tracks()), 2)

    def test_settings(self):
        self.assertEqual(main.load_settings()["speed"], 1.0)
        main.save_settings({"speed": 0.6})
        self.assertEqual(main.load_settings()["speed"], 0.6)
        main.SETTINGS.write_text("{битый json", encoding="utf-8")
        self.assertEqual(main.load_settings()["speed"], 1.0)


# ---------- аудио ----------

class TestAudio(Sandbox):
    @classmethod
    def setUpClass(cls):
        pygame.mixer.init()

    def test_length_and_clock(self):
        path = make_wav(self.root_dir / "a.wav", 4)
        self.assertAlmostEqual(main.track_length(path), 4, delta=0.05)
        main.start_audio(path)
        time.sleep(0.3)
        self.assertTrue(0.1 < main.song_time() < 1.0, main.song_time())
        main.start_audio(path, 2.0)             # перемотка
        time.sleep(0.2)
        self.assertTrue(2.0 <= main.song_time() < 2.8, main.song_time())
        main.stop_audio()
        self.assertFalse(pygame.mixer.music.get_busy())


# ---------- окна ----------

@unittest.skipUnless(GUI_TESTS, "тесты с окнами — только ./run.sh test gui")
class GuiTest(Sandbox):
    """Библиотека + окна; экземпляры Editor/TextWindow/Player ловим в self.made."""

    def setUp(self):
        super().setUp()
        pygame.mixer.init()
        self.made = {}
        for name in ("Editor", "TextWindow"):
            orig = getattr(main, name)

            def factory(*a, _orig=orig, _name=name, **k):
                obj = _orig(*a, **k)
                self.made[_name] = obj
                return obj
            p = mock.patch.object(main, name, factory)
            p.start()
            self.patches.append(p)
        self.cams = []

        def fake_open():
            self.cams.append(FakeCamera())
            return self.cams[-1]
        p = mock.patch.object(main, "open_camera", fake_open)
        p.start()
        self.patches.append(p)
        p = mock.patch.object(main, "HandTracker", FakeTracker)
        p.start()
        self.patches.append(p)
        p = mock.patch.object(tk.Misc, "focus_force", lambda self: None)
        p.start()
        self.patches.append(p)
        self.lib = None

    def tearDown(self):
        main.stop_audio()
        if self.lib:
            if self.lib.player:
                self.lib.player.stop()
            destroy(self.lib.root)
        super().tearDown()

    def open_lib(self):
        self.lib = main.Library(0)
        self.lib.root.update()
        return self.lib

    def rows(self):
        return [self.lib.tree.item(i)["values"] for i in self.lib.tree.get_children()]


class TestLibrary(GuiTest):
    def test_empty_library(self):
        self.open_lib()
        self.assertEqual(self.rows(), [])
        with mock.patch.object(main.messagebox, "showinfo") as info:
            self.lib.play()
            self.lib.edit_text()
        self.assertEqual(info.call_count, 2)

    def test_rows(self):
        self.make_track("A", [[1.0, "x"], [None, "y"]])
        self.make_track("B", audio=False)
        self.open_lib()
        self.assertEqual(self.rows(), [["A", 2, "1/2", "song.wav"], ["B", "—", "—", "нет песни!"]])
        self.assertEqual(self.lib.tree.item("B")["tags"], ["odd"])

    def test_new_track_full_flow(self):
        """Выбор песни → вставка текста → чистка → тайминги → показ."""
        src = make_wav(self.root_dir / "Моя песня.wav", 3)
        self.open_lib()
        with mock.patch.object(main.filedialog, "askopenfilenames", return_value=(str(src),)), \
             mock.patch.object(main.simpledialog, "askstring", return_value="Моя песня"):
            self.lib.new_track()
        folder = self.root_dir / "tracks" / "Моя песня"
        self.assertTrue((folder / "Моя песня.wav").exists(), "песня скопирована в проект")
        self.assertTrue(src.exists(), "оригинал остался на месте")
        self.assertEqual(self.lib.root.state(), "withdrawn")

        tw = self.made["TextWindow"]
        self.lib.root.clipboard_clear()
        self.lib.root.clipboard_append("[Припев]\n\n  Раз   два  \nТри, четыре, пять, шесть, "
                                       "семь, восемь, девять, десять, одиннадцать\n\n")
        tw.paste()
        tw.tidy()
        tw.split()
        lines = tw.lines()
        self.assertEqual(lines[0], "Раз два")
        self.assertTrue(all(len(s) <= main.LONG_LINE for s in lines))
        self.assertIn("не сохранено", tw.status.cget("text"))

        with mock.patch.object(main.messagebox, "askyesno", return_value=True):
            tw.save_and_exit()                      # → сразу в тайминги
        self.assertEqual([s for _, s in main.Track(folder).entries()], lines)

        ed = self.made["Editor"]
        pump(self.lib.root, 0.3)
        ed.mark()
        pump(self.lib.root, 0.3)
        ed.mark()
        t0, t1 = ed.entries[0][0], ed.entries[1][0]
        self.assertTrue(0 < t0 < t1, (t0, t1))
        ed.save_and_exit()
        self.assertEqual(self.lib.root.state(), "normal")
        self.assertEqual(main.Track(folder).entries()[0][0], t0)
        self.assertEqual(self.rows()[0][2], f"2/{len(lines)}")

        self.lib.play()
        self.assertIsNotNone(self.lib.player)
        self.assertEqual(self.lib.buttons[0].instate(["disabled"]), True)
        pump(self.lib.root, 0.8)
        self.assertGreaterEqual(len(self.lib.player.cards), 1)
        self.lib.play()                              # второй раз = стоп
        self.assertIsNone(self.lib.player)
        self.assertEqual(self.lib.buttons[0].instate(["disabled"]), False)

    def test_new_track_cancel(self):
        self.open_lib()
        with mock.patch.object(main.filedialog, "askopenfilenames", return_value=()):
            self.lib.new_track()
        src = make_wav(self.root_dir / "x.wav", 1)
        with mock.patch.object(main.filedialog, "askopenfilenames", return_value=(str(src),)), \
             mock.patch.object(main.simpledialog, "askstring", return_value=None):
            self.lib.new_track()
        self.assertEqual(main.list_tracks(), [])

    def test_new_track_rejects_non_audio(self):
        bad = self.root_dir / "text.txt"
        bad.write_text("x")
        self.open_lib()
        with mock.patch.object(main.filedialog, "askopenfilenames", return_value=(str(bad),)), \
             mock.patch.object(main.messagebox, "showwarning") as warn:
            self.lib.new_track()
        warn.assert_called_once()
        self.assertEqual(main.list_tracks(), [])

    def test_new_track_many_at_once(self):
        srcs = [make_wav(self.root_dir / f"песня {i}.wav", 1) for i in range(3)]
        self.open_lib()
        with mock.patch.object(main.filedialog, "askopenfilenames",
                               return_value=tuple(str(p) for p in srcs)), \
             mock.patch.object(main.simpledialog, "askstring", return_value="Первая"):
            self.lib.new_track()
        self.assertEqual(sorted(t.name for t in main.list_tracks()),
                         ["Первая", "песня 1", "песня 2"], "трек на каждую песню")
        self.assertEqual(self.made["TextWindow"].track.name, "Первая", "текст — для первой")

    def test_same_name_gets_number(self):
        self.make_track("Трек")
        src = make_wav(self.root_dir / "y.wav", 1)
        self.open_lib()
        with mock.patch.object(main.filedialog, "askopenfilenames", return_value=(str(src),)), \
             mock.patch.object(main.simpledialog, "askstring", return_value="Трек"):
            self.lib.new_track()
        self.assertIn("Трек 2", [t.name for t in main.list_tracks()])

    def test_rename_delete_folder(self):
        self.make_track("Старое", [[None, "a"]])
        self.open_lib()
        with mock.patch.object(main.simpledialog, "askstring", return_value="Новое/имя"):
            self.lib.rename()
        self.assertEqual(self.lib.current_name(), "Новое имя")
        with mock.patch.object(main.subprocess, "Popen") as popen:
            self.lib.open_folder()
        self.assertEqual(popen.call_args[0][0][1], str(self.root_dir / "tracks" / "Новое имя"))
        with mock.patch.object(main.messagebox, "askyesno", return_value=False):
            self.lib.delete()
        self.assertEqual(len(main.list_tracks()), 1)
        with mock.patch.object(main.messagebox, "askyesno", return_value=True):
            self.lib.delete()
        self.assertEqual(main.list_tracks(), [])

    def test_timings_need_text_and_audio(self):
        self.make_track("Без песни", [[None, "a"]], audio=False)
        self.open_lib()
        with mock.patch.object(main.messagebox, "showwarning") as warn:
            self.lib.edit_timings()
        warn.assert_called_once()
        self.assertNotIn("Editor", self.made)

    def test_no_text_offers_text_window(self):
        self.make_track("Пустой")
        self.open_lib()
        with mock.patch.object(main.messagebox, "askyesno", return_value=True):
            self.lib.edit_timings()
        self.assertIn("TextWindow", self.made)
        self.assertNotIn("Editor", self.made)

    def test_speed_setting_saved(self):
        self.open_lib()
        self.lib.speed.set(0.5)
        self.lib.show_speed()
        self.lib.save_speed()
        self.assertEqual(self.lib.speed_label.cget("text"), "0.5×")
        self.assertEqual(main.load_settings()["speed"], 0.5)


class TestTextWindow(GuiTest):
    def open_text(self, lines):
        tr = self.make_track("T", lines)
        self.open_lib()
        self.lib.edit_text()
        return tr, self.made["TextWindow"]

    def test_typo_keeps_timing(self):
        tr, tw = self.open_text([[1.0, "раз"], [2.0, "двa"], [3.0, "три"]])
        tw.set_lines(["раз", "два", "три", "четыре"])
        tw.update_status()
        self.assertIn("строк: 4", tw.status.cget("text"))
        self.assertIn("сохранятся у 3/3", tw.status.cget("text"))
        tw.save()
        self.assertEqual(tr.entries(), [[1.0, "раз"], [2.0, "два"], [3.0, "три"], [None, "четыре"]])

    def test_discard(self):
        tr, tw = self.open_text([[None, "a"]])
        tw.set_lines(["b"])
        with mock.patch.object(main.messagebox, "askyesno", return_value=True):
            tw.close_discard()
        self.assertEqual(tr.entries(), [[None, "a"]])
        self.assertEqual(self.lib.root.state(), "normal")

    def test_window_close_asks(self):
        tr, tw = self.open_text([[None, "a"]])
        tw.set_lines(["b"])
        with mock.patch.object(main.messagebox, "askyesnocancel", return_value=None):
            tw.ask_close()                          # «Отмена» — окно остаётся
        self.assertTrue(tw.win.winfo_exists())
        with mock.patch.object(main.messagebox, "askyesnocancel", return_value=True):
            tw.ask_close()
        self.assertEqual(tr.entries(), [[None, "b"]])

    def test_empty_text_not_saved(self):
        tr, tw = self.open_text([[None, "a"]])
        tw.set_lines([])
        with mock.patch.object(main.messagebox, "showwarning") as warn:
            self.assertFalse(tw.save())
        warn.assert_called_once()
        self.assertEqual(tr.entries(), [[None, "a"]])

    def test_clear(self):
        _, tw = self.open_text([[None, "a"]])
        with mock.patch.object(main.messagebox, "askyesno", return_value=True):
            tw.clear()
        self.assertEqual(tw.lines(), [])

    def test_russian_ctrl_v(self):
        _, tw = self.open_text([[None, "a"]])
        tw.set_lines([])
        self.lib.root.clipboard_clear()
        self.lib.root.clipboard_append("вставка")
        press(tw.text, "<Control-KeyPress>", tag="Text", keysym="Cyrillic_em")
        self.assertEqual(tw.lines(), ["вставка"])


class TestEditor(GuiTest):
    def open_editor(self, lines, seconds=6.0):
        tr = self.make_track("E", lines, seconds=seconds)
        self.open_lib()
        self.lib.edit_timings()
        ed = self.made["Editor"]
        ed.win.update()
        return tr, ed

    def test_starts_on_first_empty(self):
        _, ed = self.open_editor([[1.0, "a"], [None, "b"], [None, "c"]])
        self.assertEqual(ed.selected(), 1)

    def test_keys(self):
        tr, ed = self.open_editor([[None, "a"], [None, "b"], [None, "c"]])
        w = ed.win
        press(w, "<space>")
        self.assertIsNotNone(ed.entries[0][0])
        self.assertEqual(ed.selected(), 1)
        press(w, "<BackSpace>")
        self.assertIsNone(ed.entries[0][0])
        self.assertEqual(ed.selected(), 0)
        ed.set_time(0, 1.0)
        press(w, "<bracketright>")
        self.assertEqual(ed.entries[0][0], 1.1)
        press(w, "<bracketleft>")
        press(w, "<bracketleft>")
        self.assertEqual(ed.entries[0][0], 0.9)
        press(w, "<Cyrillic_hardsign>")     # ] на русской раскладке
        self.assertEqual(ed.entries[0][0], 1.0)
        press(w, "<Down>")
        self.assertEqual(ed.selected(), 1)
        press(w, "<Up>")
        press(w, "<Delete>")
        self.assertIsNone(ed.entries[0][0])
        press(w, "<Control-s>")
        self.assertFalse(ed.dirty)
        self.assertEqual(tr.entries()[0][0], None)

    def test_edit_shift_clear(self):
        tr, ed = self.open_editor([[1.0, "a"], [2.0, "b"], [None, "c"]])
        ed.select(2)
        with mock.patch.object(main.simpledialog, "askstring", return_value="0:04,5"):
            ed.edit_time()
        self.assertEqual(ed.entries[2][0], 4.5)
        with mock.patch.object(main.simpledialog, "askstring", return_value="-0.5"):
            ed.shift_all()
        self.assertEqual([t for t, _ in ed.entries], [0.5, 1.5, 4.0])
        with mock.patch.object(main.simpledialog, "askstring", return_value="+1"):
            ed.shift_all()
        with mock.patch.object(main.simpledialog, "askstring", return_value="ерунда"):
            ed.shift_all()
        self.assertEqual([t for t, _ in ed.entries], [1.5, 2.5, 5.0])
        ed.set_time(0, 0.2)
        with mock.patch.object(main.simpledialog, "askstring", return_value="-1"):
            ed.shift_all()
        self.assertEqual(ed.entries[0][0], 0.0, "время не уходит в минус")
        with mock.patch.object(main.messagebox, "askyesno", return_value=True):
            ed.clear_all()
        self.assertTrue(all(t is None for t, _ in ed.entries))

    def test_seek_pause_listen(self):
        _, ed = self.open_editor([[None, "a"], [3.0, "b"]])
        ed.seek(2.0)
        pump(self.lib.root, 0.2)
        self.assertTrue(2.0 <= main.song_time() < 2.8)
        ed.toggle_pause()
        self.assertTrue(ed.paused)
        self.assertIn("Играть", ed.pause_btn.cget("text")) if pump(self.lib.root, 0.1) is None else None
        ed.toggle_pause()
        self.assertFalse(ed.paused)
        ed.select(1)
        ed.listen_from_selected()                   # за 2 с до строки
        pump(self.lib.root, 0.1)
        self.assertTrue(1.0 <= main.song_time() < 1.6, main.song_time())
        ed.seek(-5)
        self.assertEqual(main._base, 0.0)
        ed.seek(999)
        self.assertLess(main._base, ed.length)

    def test_playing_row_highlight(self):
        _, ed = self.open_editor([[0.0, "первая"], [30.0, "вторая"]], seconds=40)
        pump(self.lib.root, 0.3)
        self.assertEqual(ed.playing_row, 0)
        self.assertEqual(ed.now_line.cget("text"), "первая")
        self.assertIn("playing", ed.tree.item("0")["tags"])

    def test_slider_click_seeks(self):
        _, ed = self.open_editor([[None, "a"]], seconds=10)
        sc = ed.scale
        pump(self.lib.root, 0.2)
        x = sc.winfo_width() // 2
        sc.event_generate("<Button-1>", x=x, y=5, when="now")
        self.assertTrue(ed.dragging)
        sc.event_generate("<ButtonRelease-1>", x=x, y=5, when="now")
        self.assertFalse(ed.dragging)
        self.assertAlmostEqual(main._base, 5.0, delta=1.0)

    def test_close_without_saving(self):
        tr, ed = self.open_editor([[None, "a"]])
        ed.mark()
        with mock.patch.object(main.messagebox, "askyesno", return_value=True):
            ed.close_discard()
        self.assertEqual(tr.entries(), [[None, "a"]])
        self.assertFalse(pygame.mixer.music.get_busy(), "музыка остановлена")
        self.assertEqual(self.lib.root.state(), "normal")

    def test_escape_saves(self):
        tr, ed = self.open_editor([[None, "a"]])
        ed.mark()
        press(ed.win, "<Escape>")
        self.assertIsNotNone(tr.entries()[0][0])

    def test_window_close_cancel(self):
        _, ed = self.open_editor([[None, "a"]])
        ed.mark()
        with mock.patch.object(main.messagebox, "askyesnocancel", return_value=None):
            ed.ask_close()
        self.assertTrue(ed.win.winfo_exists())


class TestCamera(GuiTest):
    def test_preview_toggle(self):
        self.open_lib()
        self.lib.toggle_preview()
        cam = self.lib.camera
        self.assertIsNotNone(cam)
        self.assertEqual(self.lib.preview_btn.cget("text"), "Закрыть камеру")
        pump(self.lib.root, 0.2)
        self.assertGreater(self.cams[0].frames, 0, "кадры идут в окно")
        x, y, w, h = cam.rect
        sw, sh = self.lib.root.winfo_screenwidth(), self.lib.root.winfo_screenheight()
        self.assertAlmostEqual(x + w / 2, sw / 2, delta=2, msg="по центру")
        self.assertAlmostEqual(y + h / 2, sh / 2, delta=2)
        self.assertAlmostEqual(w / h, main.CAM_ASPECT, delta=0.01)
        press(self.lib.root, "<Escape>")
        self.assertIsNone(self.lib.camera)
        self.assertFalse(self.cams[0].running, "камера освобождена")
        self.assertFalse(cam.win.winfo_exists())

    def test_frame_crop_and_mirror(self):
        self.open_lib()
        self.lib.toggle_preview()
        cam = self.lib.camera
        data = cam.frame_ppm(self.cams[0].get_image())
        header = f"P6 {cam.w} {cam.h} 255 ".encode()
        self.assertTrue(data.startswith(header))
        self.assertEqual(len(data), len(header) + cam.w * cam.h * 3)
        pixels = data[len(header):]
        left = tuple(pixels[(cam.h // 2 * cam.w + 5) * 3:][:3])
        # в кадре синее слева, после зеркала слева должно быть красное
        self.assertEqual(left, (200, 30, 30) if main.CAM_MIRROR else (30, 30, 200))

    def test_spin(self):
        self.open_lib()
        self.lib.toggle_preview()
        cam = self.lib.camera
        pump(self.lib.root, 0.1)
        press(self.lib.root, "<Right>")
        self.assertEqual(cam.spin_dir, 1)
        cam.spin(-1)                                 # пока крутится — второй оборот не начать
        self.assertEqual(cam.spin_dir, 1)
        widths = []
        end = time.time() + main.SPIN_MS / 1000 + 0.3
        while time.time() < end:
            self.lib.root.update()
            widths.append(cam.win.winfo_width())
            time.sleep(0.01)
        self.assertLess(min(widths), cam.w * 0.2, "ребром окно — узкая полоска")
        cam.win.update_idletasks()
        self.assertEqual(cam.spin_dir, 0)
        self.assertEqual((cam.win.winfo_width(), cam.win.winfo_height()), (cam.w, cam.h))
        self.assertEqual((cam.photo.width(), cam.photo.height()), (cam.w, cam.h))

    def test_spin_shading_direction(self):
        import math
        self.open_lib()
        self.lib.toggle_preview()
        cam = self.lib.camera
        pump(self.lib.root, 0.1)
        cam.last = pygame.Surface((cam.w, cam.h))
        cam.last.fill((200, 200, 200))
        for d, darker in ((1, "right"), (-1, "left")):
            surf = cam.spun_frame(math.pi / 4, d)
            left, right = surf.get_at((1, 10))[0], surf.get_at((surf.get_width() - 2, 10))[0]
            self.assertEqual("right" if right < left else "left", darker, d)
            self.assertAlmostEqual(surf.get_width(), cam.w * math.cos(math.pi / 4), delta=2)
        flip = cam.spun_frame(math.pi / 4, -1, vertical=True)
        self.assertEqual(flip.get_width(), cam.w)
        self.assertAlmostEqual(flip.get_height(), cam.h * math.cos(math.pi / 4), delta=2)
        top, bottom = flip.get_at((10, 1))[0], flip.get_at((10, flip.get_height() - 2))[0]
        self.assertLess(top, bottom, "при сальто верх уходит назад — темнее")

    def anim_sizes(self, cam, start):
        start()
        sizes = []
        end = time.time() + 1.3
        while time.time() < end:
            self.lib.root.update()
            sizes.append((cam.win.winfo_width(), cam.win.winfo_height(), cam.win.winfo_x()))
            time.sleep(0.01)
        self.assertIsNone(cam.anim, "анимация закончилась")
        self.assertEqual(sizes[-1][:2], (cam.w, cam.h), "окно вернулось к обычному размеру")
        return sizes

    def test_flip_pulse_shake(self):
        self.open_lib()
        self.lib.toggle_preview()
        cam = self.lib.camera
        pump(self.lib.root, 0.1)
        sizes = self.anim_sizes(cam, lambda: press(self.lib.root, "<Up>"))
        self.assertLess(min(h for _, h, _ in sizes), cam.h * 0.2, "сальто: окно ребром")
        sizes = self.anim_sizes(cam, lambda: press(self.lib.root, "<Down>"))
        self.assertGreater(max(w for w, _, _ in sizes), cam.w * 1.1, "удар: окно подпрыгнуло")
        sizes = self.anim_sizes(cam, lambda: press(self.lib.root, "<w>"))
        xs = [x for _, _, x in sizes]
        self.assertGreater(max(xs) - min(xs), 20, "тряска: окно ходит туда-сюда")

    def test_swipe_in_camera_spins(self):
        self.open_lib()
        self.lib.use_swipe.set(True)
        self.lib.toggle_preview()
        cam = self.lib.camera
        fake = self.cams[0]
        pos = iter(range(0, 640, 60))

        def frame():
            surf = pygame.Surface((640, 480))
            surf.fill((40, 40, 40))
            surf.fill((240, 240, 240), (next(pos, 600), 150, 80, 180))
            return surf
        fake.get_image = frame
        cam.started -= main.CAM_WARMUP
        # в окне кадр зеркальный: рука, идущая по кадру вправо, на экране идёт влево
        end = time.time() + 1.5
        while time.time() < end and not cam.spin_dir:
            self.lib.root.update()
            time.sleep(0.03)
        self.assertEqual(cam.spin_dir, -1 if main.CAM_MIRROR else 1)

    def test_camera_gestures_without_song(self):
        self.open_lib()
        self.lib.toggle_preview()
        cam = self.lib.camera
        cam.gesture("cover")                         # песни нет — ничего не ломается
        self.assertIsNone(cam.anim)
        cam.gesture("right")
        self.assertEqual(cam.spin_dir, 1)

    def test_swipe_off(self):
        self.open_lib()
        self.lib.use_swipe.set(False)
        self.lib.save_camera()
        self.assertFalse(main.load_settings()["swipe"])
        self.lib.toggle_preview()
        self.assertIsNone(self.lib.camera.detector)
        self.lib.camera.spin(1)                      # стрелки работают и без взмахов
        self.assertEqual(self.lib.camera.spin_dir, 1)

    def test_play_with_camera(self):
        self.make_track("P", [[0.0, "a"], [0.1, "b"], [0.2, "c"], [0.3, "d"]], seconds=5)
        self.open_lib()
        self.lib.use_camera.set(True)
        self.lib.play()
        self.assertIsNotNone(self.lib.camera)
        self.assertTrue(self.lib.preview_btn.instate(["disabled"]))
        face = self.lib.player.avoid
        pump(self.lib.root, 0.6)
        for c in self.lib.player.cards:
            self.assertFalse(main.overlaps((c.x, c.y + 20, main.BOX_W, main.BOX_H), face),
                             "карточка не закрывает лицо")
        self.lib.play()
        self.assertIsNone(self.lib.camera)
        self.assertFalse(self.cams[0].running)

    def test_play_without_camera(self):
        self.make_track("P", [[0.0, "a"]], seconds=5)
        self.open_lib()
        self.lib.use_camera.set(False)
        self.lib.save_camera()
        self.assertFalse(main.load_settings()["camera"])
        self.lib.play()
        self.assertIsNone(self.lib.camera)
        self.assertIsNone(self.lib.player.avoid)
        self.assertEqual(self.cams, [])

    def test_camera_failure_still_plays(self):
        self.make_track("P", [[0.0, "a"]], seconds=5)
        self.open_lib()
        self.lib.use_camera.set(True)
        with mock.patch.object(main, "open_camera", side_effect=RuntimeError("Камера не найдена")), \
             mock.patch.object(main.messagebox, "showwarning") as warn:
            self.lib.play()
        warn.assert_called_once()
        self.assertIsNotNone(self.lib.player, "показ идёт и без камеры")


def moving_block(x, y=15, w=64, h=48, size=10, bg=40.0):
    """Ч/б кадр 64×48 (массив [x, y]) с ярким прямоугольником в (x, y) — «рука»."""
    import numpy as np
    g = np.full((w, h), bg)
    g[max(0, x):x + size, max(0, y):y + size * 2] = 230
    return g


class TestSwipe(unittest.TestCase):
    def run_frames(self, xs, dt=0.05, det=None, t0=0.0):
        det = det or main.GestureDetector()
        hits = [det.feed(moving_block(x), t0 + i * dt) for i, x in enumerate(xs)]
        return [h for h in hits if h], det

    def test_right(self):
        self.assertEqual(self.run_frames([2, 10, 20, 30, 40, 50])[0], ["right"])

    def test_left(self):
        self.assertEqual(self.run_frames([50, 40, 30, 20, 10, 2])[0], ["left"])

    def test_still_hand(self):
        self.assertEqual(self.run_frames([20] * 20)[0], [])

    def test_small_wiggle(self):
        self.assertEqual(self.run_frames([20, 24, 20, 24, 20, 24, 20])[0], [])

    def test_too_slow(self):
        self.assertEqual(self.run_frames([2, 10, 20, 30, 40, 50], dt=0.5)[0], [])

    def test_light_change_ignored(self):
        import numpy as np
        det = main.GestureDetector()
        self.assertIsNone(det.feed(np.full((64, 48), 40.0), 0))
        self.assertIsNone(det.feed(np.full((64, 48), 200.0), 0.05))
        self.assertEqual(det.track, [])

    def test_up_down(self):
        det = main.GestureDetector()
        hits = [det.feed(moving_block(27, y, size=10), i * 0.05)
                for i, y in enumerate([28, 21, 14, 7, 0])]
        self.assertEqual([h for h in hits if h], ["up"])
        det = main.GestureDetector()
        hits = [det.feed(moving_block(27, y, size=10), i * 0.05)
                for i, y in enumerate([0, 7, 14, 21, 28])]
        self.assertEqual([h for h in hits if h], ["down"])

    def test_wave(self):
        xs = [20, 30, 40, 30, 20, 30, 40, 30, 20]       # туда-сюда, меньше взмаха
        self.assertEqual(self.run_frames(xs, dt=0.08)[0], ["wave"])

    def test_cover_and_uncover(self):
        import numpy as np
        det = main.GestureDetector()
        bright, dark = np.full((64, 48), 120.0), np.full((64, 48), 10.0)
        t = 0.0
        for _ in range(10):
            self.assertIsNone(det.feed(bright, t))
            t += 0.05
        hits = []
        for _ in range(12):                              # ладонь 0.6 с
            hits.append(det.feed(dark, t))
            t += 0.05
        self.assertEqual([h for h in hits if h], ["cover"], "одна пауза, не много")
        hits = [det.feed(bright, t + i * 0.05) for i in range(5)]
        self.assertEqual([h for h in hits if h], [], "убрать руку — не жест")
        t += 2
        hits = [det.feed(dark, t + i * 0.05) for i in range(12)]
        self.assertEqual([h for h in hits if h], ["cover"], "второй раз — снова работает")

    def test_blink_is_not_cover(self):
        import numpy as np
        det = main.GestureDetector()
        for i in range(10):
            det.feed(np.full((64, 48), 120.0), i * 0.05)
        hits = [det.feed(np.full((64, 48), 10.0), 0.5 + i * 0.05) for i in range(3)]
        self.assertEqual([h for h in hits if h], [], "короткое затемнение — не ладонь")

    def test_cooldown(self):
        hits, det = self.run_frames([2, 10, 20, 30, 40, 50])
        more, _ = self.run_frames([50, 40, 30, 20, 10, 2], det=det, t0=0.3)
        self.assertEqual(more, [], "сразу после взмаха — пауза")
        later, _ = self.run_frames([2, 10, 20, 30, 40, 50], det=det, t0=5)
        self.assertEqual(later, ["right"], "после паузы снова работает")


class TestOpenCamera(unittest.TestCase):
    def test_no_camera_message(self):
        import pygame.camera
        with mock.patch.object(main, "list_camera_devices", return_value=[]), \
             mock.patch.object(pygame.camera, "init"), \
             mock.patch.object(pygame.camera, "list_cameras", return_value=[], create=True):
            with self.assertRaisesRegex(RuntimeError, "не найдена"):
                main.open_camera()


class TestSongGestures(GuiTest):
    def start(self):
        self.make_track("P", [[0.0, "первая строка"], [0.4, "вторая"], [3.0, "третья"],
                              [6.0, "четвёртая"]], seconds=12)
        self.open_lib()
        self.lib.use_camera.set(True)
        self.lib.play()
        pump(self.lib.root, 0.3)
        return self.lib.player, self.lib.camera

    def test_cover_pauses_and_resumes(self):
        player, cam = self.start()
        cam.gesture("cover")
        self.assertTrue(player.paused)
        self.assertEqual(cam.badge.cget("text"), "⏸ пауза")
        self.assertFalse(pygame.mixer.music.get_busy(), "музыка на паузе")
        card = player.cards[0]
        typed = card.i
        pump(self.lib.root, 0.5)
        self.assertEqual(card.i, typed, "карточка не печатается на паузе")
        self.assertIsNotNone(self.lib.player, "показ не закончился из-за паузы")
        cam.gesture("cover")
        self.assertFalse(player.paused)
        self.assertEqual(cam.badge.cget("text"), "▶ дальше")
        pump(self.lib.root, 0.3)
        self.assertGreater(card.i, typed, "печатает дальше")

    def test_seek_when_paused(self):
        player, cam = self.start()
        cam.gesture("cover")
        cam.gesture("right")
        self.assertIsNone(cam.anim, "на паузе взмах — перемотка, а не оборот")
        self.assertAlmostEqual(main._base, 5.3, delta=0.4)
        self.assertEqual(player.cards, [])
        self.assertTrue(player.paused, "после перемотки всё ещё пауза")
        self.assertTrue(cam.badge.cget("text").startswith("⏩"))
        cam.gesture("left")
        cam.gesture("left")
        self.assertEqual(main._base, 0.0, "назад не дальше начала")
        cam.gesture("cover")
        pump(self.lib.root, 0.3)
        self.assertEqual(player.cards[0].text, "первая строка")

    def test_seek_shows_current_line(self):
        player, cam = self.start()
        player.seek(3.4)                               # попали внутрь «третьей»
        pump(self.lib.root, 0.2)
        self.assertEqual([c.text for c in player.cards], ["третья"])

    def test_swipe_while_playing_spins(self):
        player, cam = self.start()
        cam.gesture("left")
        self.assertEqual(cam.spin_dir, -1)
        self.assertFalse(player.paused)

    def test_space_pauses_without_camera(self):
        self.make_track("P", [[0.0, "a"]], seconds=5)
        self.open_lib()
        self.lib.use_camera.set(False)
        self.lib.play()
        press(self.lib.root, "<space>")
        self.assertTrue(self.lib.player.paused)
        press(self.lib.root, "<space>")
        self.assertFalse(self.lib.player.paused)


class TestPlayer(GuiTest):
    def test_cards_type_and_stop_at_end(self):
        self.make_track("P", [[0.0, "Раз два три"], [0.6, "Вторая строка"], [None, ""]],
                        seconds=1.5)
        self.open_lib()
        self.lib.speed.set(2.0)
        self.lib.play()
        pump(self.lib.root, 0.5)
        card = self.lib.player.cards[0]
        self.assertEqual(card.char_ms, int(main.CHAR_MS / 2.0))
        self.assertTrue(card.label.cget("text").startswith("Раз"))
        pump(self.lib.root, 0.5)
        self.assertEqual(len(self.lib.player.cards), 2)
        pump(self.lib.root, 1.5)                     # трек кончился → показ сам остановился
        self.assertIsNone(self.lib.player)
        self.assertEqual(self.lib.play_btn.cget("text"), "▶ Показать")

    def test_typing_speed(self):
        root = tk.Tk()
        try:
            slow = main.LyricCard(root, "x" * 20, 0, 0, 60, 1.0, lambda: None)
            fast = main.LyricCard(root, "x" * 20, 0, 0, 60, 2.0, lambda: None)
            rushed = main.LyricCard(root, "x" * 100, 0, 0, 1, 1.0, lambda: None)
            self.assertEqual(slow.char_ms, main.CHAR_MS)
            self.assertEqual(fast.char_ms, main.CHAR_MS // 2)
            self.assertEqual(rushed.char_ms, main.MIN_CHAR_MS, "не быстрее минимума")
            pump(root, 0.5)
            self.assertTrue(slow.label.cget("text").startswith("xxx"))
            self.assertTrue(slow.label.cget("text").endswith(main.CURSOR))
            pump(root, 1.6)                          # допечаталась, курсор мигает
            self.assertEqual(slow.label.cget("text").rstrip(main.CURSOR + " "), "x" * 20)
            slow.close()
            pump(root, 0.5)
            self.assertFalse(slow.win.winfo_exists(), "плавно закрылась")
        finally:
            destroy(root)

    def test_escape_stops(self):
        self.make_track("P", [[0.0, "строка"]], seconds=5)
        self.open_lib()
        self.lib.play()
        pump(self.lib.root, 0.3)
        press(self.lib.root, "<Escape>")
        self.assertIsNone(self.lib.player)
        self.assertFalse(pygame.mixer.music.get_busy())

    def test_offset(self):
        tr = self.make_track("P", [[1.0, "a"], [2.0, "b"]], seconds=3)
        root = tk.Tk()
        try:
            p = main.Player(root, tr, -0.5, 1.0, lambda: None)
            self.assertEqual([t for t, _ in p.timed], [0.5, 1.5])
            p.stop()
        finally:
            destroy(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
