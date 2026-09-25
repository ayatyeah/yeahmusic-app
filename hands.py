"""Руки на камере: MediaPipe находит 21 точку на каждой руке.

Тут — трекинг в фоновом потоке, обводка рук на кадре и жест «пистолетик»:
указательный прямо, большой отставлен и смотрит вверх, безымянный и мизинец поджаты.
Резкий рывок кисти вверх в этой позе (отдача) — выстрел.
Сердечко из двух рук — из него вылетают сердечки. «V» (указательный и средний) — щелчок.
Ещё ищем лицо — камера приближает его в припеве.

Точки руки (MediaPipe):  0 — запястье; большой 1-4; указательный 5-8; средний 9-12;
безымянный 13-16; мизинец 17-20 (у каждого пальца: основание, два сустава, кончик).
"""
import collections
import math
import random
import threading
import time
import urllib.request
from pathlib import Path

import os

# MediaPipe/TFLite сыплют в терминал служебными W0000/INFO — оставляем только ошибки
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import numpy as np  # noqa: E402
import pygame  # noqa: E402

MODEL = Path(__file__).parent / "models" / "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")
SEG_MODEL = Path(__file__).parent / "models" / "selfie_segmenter.tflite"
SEG_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/"
                 "selfie_segmenter/float16/latest/selfie_segmenter.tflite")
SEG_HISTORY = 18            # сколько масок силуэта помним (для теней-двойников)
FACE_MODEL = Path(__file__).parent / "models" / "face_detector.tflite"
FACE_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
                  "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite")
TRACK_SIZE = (360, 480)     # до какого размера уменьшаем кадр для распознавания
HAND_MAX_AGE = 0.35         # сек: руки старше этого уже не рисуем

HAND_COLOR = (0, 255, 200)
GUN_COLOR = (255, 70, 60)
DARK = (0, 0, 0)

EXTENDED = 1.15             # палец прямой: кончик дальше от запястья, чем сустав, в 1.15+ раза
FOLDED = 1.1                # палец поджат: кончик почти не дальше сустава (можно не до конца)
THUMB_OUT = 0.5             # большой отставлен от основания указательного (в длинах ладони)
ARM_TIME = 0.25             # «взвод»: поза держится столько — только тогда можно выстрелить
SHOT_KICK = 0.3             # выстрел: кончик ствола подпрыгнул на столько длин ладони...
SHOT_TILT = 0.3             # ...или ствол «кивнул» вверх (на ~18°)...
SHOT_WINDOW = 0.22          # ...быстрее стольких секунд (медленный подъём руки — не выстрел)
SHOT_COOLDOWN = 0.7         # между выстрелами (и после — снова взвести)
POSE_GRACE = 0.3            # в рывке поза может «смазаться» — столько её ещё помним

HEART_TOUCH = 0.5           # сердечко: кончики обеих рук сведены ближе (в длинах ладони)
HEART_HOLD = 0.25           # и держатся столько
PEACE_SPREAD = 0.3          # «V»: указательный и средний разведены хотя бы на столько
PEACE_HOLD = 0.2
PEACE_COOLDOWN = 1.2

THUMB = (1, 2, 3, 4)
INDEX, MIDDLE, RING, PINKY = (5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)
FINGERS = (THUMB, INDEX, MIDDLE, RING, PINKY)
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16),
               (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]


# ---------- геометрия руки (точки в пикселях окна камеры) ----------

def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def reach(pts, finger):
    """Насколько палец выпрямлен: расстояние запястье→кончик / запястье→средний сустав."""
    return dist(pts[0], pts[finger[3]]) / max(1e-6, dist(pts[0], pts[finger[1]]))


def palm(pts):
    return max(1e-6, dist(pts[0], pts[9]))


def is_gun(pts):
    """Поза «пистолетик»."""
    if reach(pts, INDEX) < EXTENDED:
        return False
    if reach(pts, RING) > FOLDED or reach(pts, PINKY) > FOLDED:
        return False
    if dist(pts[4], pts[5]) < THUMB_OUT * palm(pts):
        return False
    tx, ty = unit(pts[2], pts[4])
    ix, iy = unit(pts[5], pts[8])
    along = tx * ix + ty * iy
    return ty < 0.1 and along < 0.85      # большой вверх или вбок (не вниз), не вдоль указательного


def is_thumb_gun(pts):
    """Пистолет из большого пальца: большой выставлен вбок, остальные четыре — в кулак.
    Большой вверх («класс») и просто кулак — не пистолет, иначе стреляет само."""
    if any(reach(pts, f) > FOLDED for f in (INDEX, MIDDLE, RING, PINKY)):
        return False
    if dist(pts[4], pts[5]) < THUMB_OUT * 1.4 * palm(pts):
        return False
    return abs(unit(pts[2], pts[4])[1]) < 0.6            # смотрит вбок, а не вверх/вниз


def gun_barrel(pts):
    """(кончик ствола, направление) для любой из поз-пистолетов, иначе None."""
    if is_gun(pts):
        return pts[8], unit(pts[5], pts[8])
    if is_thumb_gun(pts):
        return pts[4], unit(pts[2], pts[4])
    return None


class Shot:
    def __init__(self, muzzle, direction):
        self.muzzle = muzzle          # кончик указательного, пиксели окна камеры
        self.direction = direction    # куда смотрит ствол (единичный вектор)


class GunDetector:
    """Следит за позой «пистолетик» и ловит выстрел.
    Сначала «взвод»: поза держится ARM_TIME — случайное мелькание позы не стреляет.
    Потом выстрел: кончик ствола резко подпрыгнул ИЛИ ствол резко кивнул вверх."""

    def __init__(self):
        self.history = []             # [(время, кончик, длина ладони, ствол, взведён)]
        self.pose_since = None        # с какого момента поза держится
        self.pose_until = 0.0         # до какого времени считаем, что пистолет «в руке»
        self.cooldown_until = 0.0
        self.tip_index = 8

    def aiming(self, now):
        return now < self.pose_until

    def armed(self, now):
        return (self.pose_since is not None and self.aiming(now)
                and now - self.pose_since >= ARM_TIME and now >= self.cooldown_until)

    def feed(self, hands, now):
        """hands — список рук, у каждой 21 точка (x, y) в пикселях. Вернёт Shot или None."""
        gun = next((pts for pts in hands if gun_barrel(pts)), None)
        if gun is not None:
            self.pose_since = self.pose_since or now
            self.pose_until = now + POSE_GRACE
            self.tip_index = 8 if is_gun(gun) else 4      # из какого пальца стреляем
            tip, aim = gun_barrel(gun)
        elif self.aiming(now) and self.history and hands:
            # в рывке поза смазывается — берём руку, ближайшую к прошлому кончику
            last_tip = self.history[-1][1]
            gun = min(hands, key=lambda pts: dist(pts[self.tip_index], last_tip))
            tip, aim = gun[self.tip_index], self.history[-1][3]
        if gun is None:
            self.history.clear()
            self.pose_since = None
            return None
        self.history.append((now, tip, palm(gun), aim, self.armed(now)))
        self.history = [h for h in self.history if now - h[0] <= SHOT_WINDOW]
        ready = [h for h in self.history if h[4] and h[0] < now]    # точки, где уже был взведён
        if not ready:
            return None
        start = max(ready, key=lambda h: h[1][1])                   # самая нижняя до рывка
        rise = (start[1][1] - tip[1]) / start[2]
        tilt = start[3][1] - aim[1]                                  # ствол повернулся вверх
        if rise >= SHOT_KICK or (gun_barrel(gun) and tilt >= SHOT_TILT):
            self.history.clear()
            self.cooldown_until = now + SHOT_COOLDOWN
            self.pose_since = None                                   # после выстрела — взвести заново
            return Shot(start[1], start[3])
        return None


# ---------- трекинг в фоне ----------

class HandTracker:
    """MediaPipe в отдельном потоке: главный поток кидает свежие кадры,
    поток отдаёт последние найденные руки. Загрузка модели — тоже в потоке."""

    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.frame = None
        self.hands = []
        self.faces = []            # [(центр x, центр y, ширина)] — доли кадра
        self.segment = False       # искать силуэт (включаем только когда нужен)
        self.masks = collections.deque(maxlen=SEG_HISTORY)
        self.hands_at = 0.0
        self.version = 0
        self.error = None
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def submit(self, surf):
        """Кадр окна камеры (уже зеркальный) → на распознавание, в тех же пропорциях."""
        w, h = surf.get_size()
        size = (max(64, int(TRACK_SIZE[1] * w / h)), TRACK_SIZE[1])
        small = pygame.transform.smoothscale(surf, size)
        rgb = np.ascontiguousarray(pygame.surfarray.array3d(small).transpose(1, 0, 2))
        with self.lock:
            self.frame = rgb
        self.wake.set()

    def latest(self, size):
        """(руки в пикселях окна size=(w, h), номер результата)."""
        w, h = size
        with self.lock:
            fresh = time.monotonic() - self.hands_at <= HAND_MAX_AGE
            hands = [[(x * w, y * h) for x, y in pts] for pts in self.hands] if fresh else []
            return hands, self.version

    def mask_at(self, when, max_age=0.5):
        """Силуэт, каким он был в момент when (или None, если такого нет)."""
        with self.lock:
            masks = list(self.masks)
        if not masks:
            return None
        t, mask = min(masks, key=lambda m: abs(m[0] - when))
        return mask if abs(t - when) <= max_age else None

    def latest_faces(self):
        """[(центр x, центр y, ширина)] — доли кадра, который отдали в submit."""
        with self.lock:
            return list(self.faces) if time.monotonic() - self.hands_at <= 1.0 else []

    def setup(self):
        for path, url in ((MODEL, MODEL_URL), (FACE_MODEL, FACE_MODEL_URL),
                          (SEG_MODEL, SEG_MODEL_URL)):
            if not path.exists():
                path.parent.mkdir(exist_ok=True)
                urllib.request.urlretrieve(url, path)
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        opts = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL)),
            running_mode=vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=0.5, min_tracking_confidence=0.5)
        faces = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(FACE_MODEL)),
            running_mode=vision.RunningMode.VIDEO, min_detection_confidence=0.5))
        seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=str(SEG_MODEL)),
            running_mode=vision.RunningMode.VIDEO, output_confidence_masks=True))
        return mp, vision.HandLandmarker.create_from_options(opts), faces, seg

    def loop(self):
        try:
            mp, landmarker, face_detector, segmenter = self.setup()
        except Exception as e:           # нет модели / нет mediapipe — просто без рук
            self.error = str(e)
            return
        t0, last_ts = time.monotonic(), -1
        with landmarker, face_detector, segmenter:
            while self.running:
                if not self.wake.wait(0.2):
                    continue
                self.wake.clear()
                with self.lock:
                    frame, self.frame = self.frame, None
                if frame is None:
                    continue
                ts = max(last_ts + 1, int((time.monotonic() - t0) * 1000))
                last_ts = ts
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)
                res = landmarker.detect_for_video(image, ts)
                hands = [[(p.x, p.y) for p in hand] for hand in res.hand_landmarks]
                faces = []
                for det in face_detector.detect_for_video(image, ts).detections:
                    box = det.bounding_box
                    fh, fw = frame.shape[:2]
                    faces.append(((box.origin_x + box.width / 2) / fw,
                                  (box.origin_y + box.height / 2) / fh, box.width / fw))
                if self.segment:
                    mask = segmenter.segment_for_video(image, ts).confidence_masks[0]
                    with self.lock:
                        self.masks.append((time.monotonic(), np.array(mask.numpy_view(),
                                                                     dtype=np.float16)))
                with self.lock:
                    self.hands = hands
                    self.faces = faces
                    self.hands_at = time.monotonic()
                    self.version += 1

    def close(self):
        self.running = False
        self.wake.set()
        self.thread.join(timeout=1)


# ---------- рисование ----------

def hull(points):
    """Выпуклая оболочка (монотонная цепь)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ладонь-«паутинка»: запястье к основанию каждого пальца, основания между собой и диагонали
TIPS = (4, 8, 12, 16, 20)
FINGER_POINTS = [i for f in FINGERS for i in f]
PALM_WEB = [(0, 1), (0, 5), (0, 9), (0, 13), (0, 17),
            (1, 5), (5, 9), (9, 13), (13, 17),
            (2, 5), (1, 9), (5, 13), (9, 17), (0, 2)]


def draw_hand(surf, pts, gun=False):
    """Стильный неоновый контур: мягкое свечение, тонкая белая сердцевина,
    светящиеся кончики; ладонь — приглушённая паутинка. Пистолет — красным."""
    color = GUN_COLOR if gun else HAND_COLOR
    glow = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    fingers = [(a, b) for f in FINGERS for a, b in zip(f, f[1:])]
    for a, b in PALM_WEB:                                    # ладонь — тоньше и тусклее
        pygame.draw.line(glow, (*color, 60), pts[a], pts[b], 2)
    for width, alpha in ((11, 45), (7, 70), (4, 120)):       # свечение вокруг пальцев
        for a, b in fingers:
            pygame.draw.line(glow, (*color, alpha), pts[a], pts[b], width)
        for i in FINGER_POINTS:
            pygame.draw.circle(glow, (*color, alpha), pts[i], width / 2)
    for a, b in fingers:                                     # сердцевина
        pygame.draw.line(glow, (255, 255, 255, 220), pts[a], pts[b], 2)
    for i in FINGER_POINTS:
        tip = i in TIPS
        pygame.draw.circle(glow, (*color, 90), pts[i], 9 if tip else 5)
        pygame.draw.circle(glow, (255, 255, 255, 240), pts[i], 3.5 if tip else 2)
    surf.blit(glow, (0, 0))
    barrel = gun_barrel(pts) if gun else None
    if barrel:                                        # прицел: пунктир от кончика ствола
        (x, y), (dx, dy) = barrel
        for k in range(1, 8, 2):
            pygame.draw.line(surf, color, (x + dx * k * 14, y + dy * k * 14),
                             (x + dx * (k + 1) * 14, y + dy * (k + 1) * 14), 2)


def starburst(center, r_out, r_in, spikes=12, seed=None):
    rnd = random.Random(seed)
    pts = []
    for k in range(spikes * 2):
        a = math.pi * k / spikes + rnd.uniform(-0.12, 0.12)
        r = (r_out if k % 2 == 0 else r_in) * rnd.uniform(0.8, 1.15)
        pts.append((center[0] + math.cos(a) * r, center[1] + math.sin(a) * r))
    return pts


def draw_muzzle_flash(surf, pos, t, seed):
    """Вспышка у кончика пальца, t от 0 до 1 — сколько прошло от выстрела."""
    k = 1 - t
    pygame.draw.polygon(surf, (255, 200, 60), starburst(pos, 60 * k + 10, 22 * k + 4, seed=seed))
    pygame.draw.polygon(surf, (255, 255, 230), starburst(pos, 30 * k + 5, 12 * k + 2, seed=seed + 1))


# ---------- звук ----------

def gunshot_sound():
    """Синтезированный «пау»: щелчок шума + низкий удар, быстро затухают."""
    init = pygame.mixer.get_init()
    if not init:
        return None
    rate, _, channels = init
    n = int(rate * 0.45)
    t = np.arange(n) / rate
    rng = np.random.default_rng(7)
    noise = rng.uniform(-1, 1, n) * np.exp(-t * 28)
    noise = np.convolve(noise, np.ones(6) / 6, mode="same")        # чуть глуше
    thump = np.sin(2 * math.pi * (95 - 50 * t) * t) * np.exp(-t * 14)
    wave = np.clip(noise * 0.9 + thump * 0.8, -1, 1) * 0.9
    data = (wave * 32767).astype(np.int16)
    if channels > 1:
        data = np.repeat(data[:, None], channels, axis=1)
    return pygame.sndarray.make_sound(np.ascontiguousarray(data))


# ---------- сердечко и «V» ----------

def heart_center(hands_px):
    """Сердечко из двух рук: указательные сведены вверху, большие — внизу.
    Вернёт центр сердечка или None."""
    if len(hands_px) < 2:
        return None
    a, b = hands_px[:2]
    size = (palm(a) + palm(b)) / 2
    if dist(a[8], b[8]) > HEART_TOUCH * size or dist(a[4], b[4]) > HEART_TOUCH * size:
        return None
    top = ((a[8][0] + b[8][0]) / 2, (a[8][1] + b[8][1]) / 2)
    bottom = ((a[4][0] + b[4][0]) / 2, (a[4][1] + b[4][1]) / 2)
    if top[1] > bottom[1] - 0.3 * size:              # указательные должны быть выше больших
        return None
    return (top[0] + bottom[0]) / 2, (top[1] + bottom[1]) / 2


def is_peace(pts):
    """«V»: указательный и средний прямые и разведены, безымянный и мизинец поджаты."""
    if reach(pts, INDEX) < EXTENDED or reach(pts, MIDDLE) < EXTENDED:
        return False
    if reach(pts, RING) > FOLDED or reach(pts, PINKY) > FOLDED:
        return False
    return dist(pts[8], pts[12]) > PEACE_SPREAD * palm(pts)


def heart_shape(cx, cy, r):
    """Контур сердечка (многоугольник) с центром (cx, cy), «радиус» r."""
    pts = []
    for k in range(32):
        t = 2 * math.pi * k / 32
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * r / 16, cy + y * r / 16))
    return pts


class Gestures:
    """Сердечко (пока держишь — летят сердечки) и «V» (щелчок — событие "peace")."""

    def __init__(self, seed=None):
        self.rnd = random.Random(seed)
        self.hearts = []             # [x, y, vx, vy, размер, возраст, жизнь, оттенок]
        self.heart_since = None
        self.heart_at = None
        self.peace_since = None
        self.peace_ready = True
        self.peace_cooldown = 0.0
        self.last = None

    def busy(self):
        return self.heart_since is not None or self.peace_since is not None

    def feed(self, hands_px, now):
        """Вернёт "peace", когда показали «V», иначе None."""
        dt = min(0.1, now - self.last) if self.last is not None else 0.0
        self.last = now
        event = None
        center = heart_center(hands_px)
        if center:
            self.heart_since = self.heart_since or now
            self.heart_at = center
            if now - self.heart_since >= HEART_HOLD:
                burst = 10 if not self.hearts else 0            # первый раз — пачкой
                for _ in range(burst + int(dt * 7 + self.rnd.random())):
                    self.spawn(center)
        else:
            self.heart_since = None
        peace = any(is_peace(pts) for pts in hands_px)
        if peace:
            self.peace_since = self.peace_since or now
            if (self.peace_ready and now - self.peace_since >= PEACE_HOLD
                    and now >= self.peace_cooldown):
                event = "peace"
                self.peace_ready = False
                self.peace_cooldown = now + PEACE_COOLDOWN
        else:
            self.peace_since = None
            self.peace_ready = True                     # опустил руку — можно щёлкнуть снова
        for h in self.hearts:
            h[0] += h[2] * dt
            h[1] += h[3] * dt
            h[2] += self.rnd.uniform(-90, 90) * dt
            h[5] += dt
        self.hearts = [h for h in self.hearts if h[5] < h[6]][-60:]
        return event

    def spawn(self, center):
        r = self.rnd
        self.hearts.append([center[0] + r.uniform(-10, 10), center[1] + r.uniform(-10, 10),
                            r.uniform(-120, 120), r.uniform(-260, -120), r.uniform(12, 26),
                            0.0, r.uniform(1.2, 2.0), r.choice([(255, 60, 110), (255, 110, 160),
                                                                (240, 30, 70)])])

    def draw(self, surf):
        for x, y, _, _, size, age, life, color in self.hearts:
            k = min(1.0, age * 8) * (1 - max(0.0, age - life * 0.6) / (life * 0.4))
            r = size * (0.6 + 0.4 * min(1.0, age * 5))
            if k <= 0.02:
                continue
            shape = heart_shape(x, y, r)
            layer = pygame.Surface((int(r * 2.6), int(r * 2.6)), pygame.SRCALPHA)
            off = (x - r * 1.3, y - r * 1.3)
            local = [(px - off[0], py - off[1]) for px, py in shape]
            pygame.draw.polygon(layer, (*color, int(255 * k)), local)
            pygame.draw.polygon(layer, (255, 255, 255, int(200 * k)), local, 2)
            surf.blit(layer, off)
