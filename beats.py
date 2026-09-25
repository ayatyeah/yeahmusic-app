"""Биты песни: где удары (в первую очередь бочка) — для «бума» окна камеры в такт.

Без тяжёлых библиотек, на numpy:
  1. «сила ударов» по времени — насколько резко растёт громкость, особенно в басу;
  2. темп — через автокорреляцию: через сколько кадров удары повторяются;
  3. сетка битов с этим шагом, сдвинутая так, чтобы попадать в сильные удары,
     и каждый бит чуть подтянут к ближайшему настоящему удару.
Результат сохраняется рядом с треком (beats.json), чтобы не считать каждый раз.
"""
import json

import numpy as np
import pygame

HOP = 512                  # шаг анализа в сэмплах (~11.6 мс при 44.1 кГц)
WINDOW = 2048
BASS_HZ = 160              # всё ниже — «бочка и бас»
BPM_RANGE = (70, 180)
SNAP = 0.12                # насколько (доля шага) бит можно подтянуть к настоящему удару
LEVEL_FPS = 20             # столько раз в секунду запоминаем громкость баса (для эквалайзера)
LEVEL_BANDS = 5            # на столько полос делим низ спектра
CACHE_NAME = "beats.json"


def load_mono(path):
    """Сэмплы песни (моно, float) и частота — через pygame, он уже умеет mp3."""
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    rate = pygame.mixer.get_init()[0]
    data = pygame.sndarray.array(pygame.mixer.Sound(str(path))).astype(np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data / 32768.0, rate


def onset_strength(x, rate):
    """Сила ударов по кадрам: рост энергии (спектральный поток), бас — с весом ×3."""
    n = 1 + max(0, len(x) - WINDOW) // HOP
    idx = np.arange(WINDOW)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[np.minimum(idx, len(x) - 1)] * np.hanning(WINDOW)
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10)
    flux = np.maximum(0, np.diff(mag, axis=0, prepend=mag[:1]))
    bass_bins = max(2, int(BASS_HZ * WINDOW / rate))
    env = flux.sum(axis=1) + 3 * flux[:, :bass_bins].sum(axis=1)
    env = env - np.convolve(env, np.ones(16) / 16, mode="same")     # убрать медленный фон
    env = np.maximum(env, 0)
    return env / (env.max() or 1.0), rate / HOP


def tempo_period(env, fps):
    """Шаг бита в кадрах по автокорреляции; предпочитаем привычные 90-150 BPM."""
    lo, hi = int(fps * 60 / BPM_RANGE[1]), int(fps * 60 / BPM_RANGE[0])
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lags = np.arange(lo, hi + 1)
    bpm = fps * 60 / lags
    weight = np.exp(-0.5 * (np.log2(bpm / 120) / 0.7) ** 2)          # мягко к ~120
    best = lags[np.argmax(ac[lo:hi + 1] * weight)]
    # уточнить дробный шаг: парабола вокруг максимума
    if lo < best < hi:
        y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
        d = (y0 - y2) / (2 * (y0 - 2 * y1 + y2) or 1)
        return best + max(-0.5, min(0.5, d))
    return float(best)


def beat_grid(env, period):
    """Сетка битов: сдвиг, при котором сетка собирает больше всего силы ударов."""
    n = len(env)
    best_phase, best_score = 0, -1.0
    for phase in range(int(period)):
        pos = np.arange(phase, n, period).astype(int)
        score = env[pos].sum()
        if score > best_score:
            best_phase, best_score = phase, score
    beats = []
    radius = max(1, int(period * SNAP))
    for p in np.arange(best_phase, n, period):
        c = int(round(p))
        a, b = max(0, c - radius), min(n, c + radius + 1)
        beats.append(a + int(np.argmax(env[a:b])))                      # к ближайшему удару
    return beats


def bass_levels(x, rate):
    """Громкость по нескольким низким полосам, LEVEL_FPS раз в секунду — для эквалайзера."""
    step = int(rate / LEVEL_FPS)
    n = max(1, len(x) // step)
    frames = x[:n * step].reshape(n, step)
    spec = np.abs(np.fft.rfft(frames * np.hanning(step), axis=1))
    edges = np.linspace(0, int(400 * step / rate), LEVEL_BANDS + 1).astype(int)
    bands = np.stack([spec[:, a:max(b, a + 1)].mean(axis=1)
                      for a, b in zip(edges, edges[1:])], axis=1)
    bands = np.log1p(bands * 8)
    top = np.percentile(bands, 97) or 1.0
    return np.clip(bands / top, 0, 1)


def analyze(path):
    """{"bpm", "beats": [сек], "strength": [0-1], "levels": [[полосы], ...]}."""
    x, rate = load_mono(path)
    env, fps = onset_strength(x, rate)
    period = tempo_period(env, fps)
    frames = beat_grid(env, period)
    strength = env[frames]
    top = np.percentile(strength, 90) if len(strength) else 1.0
    strength = np.clip(strength / (top or 1.0), 0, 1)
    # кадр f — это начало окна, а удар «виден» примерно на 3/4 окна: сдвигаем время
    shift = WINDOW * 0.75 / rate
    return {"bpm": round(float(fps * 60 / period), 1),
            "beats": [round(f / fps + shift, 3) for f in frames],
            "strength": [round(float(s), 2) for s in strength],
            "level_fps": LEVEL_FPS,
            "levels": [[int(v * 100) for v in row] for row in bass_levels(x, rate)]}


def track_beats(track):
    """Биты трека: из beats.json, если песня не менялась, иначе считаем и сохраняем."""
    audio = track.audio
    stamp = {"audio": audio.name, "size": audio.stat().st_size}
    cache = track.dir / CACHE_NAME
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if all(data.get(k) == v for k, v in stamp.items()):
            return data
    except (OSError, ValueError):
        pass
    data = {**stamp, **analyze(audio)}
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def chorus_ranges(entries, length, min_repeat=3):
    """[(начало, конец)] припевов в секундах: блоки строк, которые повторяются в песне
    (как в автотаймингах). entries — [(время, строка)] с уже заполненным временем."""
    from autotime import repeats
    lines = [s for _, s in entries]
    times = [t for t, _ in entries]
    ranges = []
    for a, b, n in repeats(lines):
        for start in (a, b):
            end = start + n
            t_end = times[end] if end < len(times) else length
            ranges.append((times[start], t_end))
    return sorted(ranges)


HALF_TIME_BPM = 130        # быстрее — бум через бит (как бочка в трэпе), иначе мельтешит
VERSE_POWER = 0.6          # «всегда»: в куплетах бум слабее, чем в припеве
MOMENT_POWER = 1.15        # особый момент: как припев, но удар чуть сильнее...


def boom_schedule(beats, mode, ranges, min_strength=0.35, moments=()):
    """Когда делать «бум»: [(время, сила, качнуть)]. mode — "off" / "chorus" / "always".
    «always» — по всей песне, но в припеве сильнее (в куплетах ×VERSE_POWER).
    Особый момент (moments) — как припев, плюс движ: удар на каждый бит (не через один),
    чуть сильнее, и окно качается влево-вправо (качнуть = ±1, иначе 0).
    Слабые удары пропускаем, чтобы окно не дёргалось на каждый шорох."""
    if mode == "off":
        return []
    times, strength = beats["beats"], beats["strength"]
    half = beats["bpm"] > HALF_TIME_BPM           # через бит — чётные или нечётные, где сильнее
    parity = int(sum(strength[1::2]) > sum(strength[0::2]))
    out, side = [], 1
    for i, (t, s) in enumerate(zip(times, strength)):
        moment = any(a <= t < b for a, b in moments)
        if half and not moment and i % 2 != parity:
            continue
        if s < min_strength:
            continue
        in_chorus = moment or any(a <= t < b for a, b in ranges)
        if mode == "chorus" and not in_chorus:
            continue
        if moment:
            out.append((t, round(min(1.0, s * MOMENT_POWER), 2), side))
            side = -side
        else:
            out.append((t, s if in_chorus else round(s * VERSE_POWER, 2), 0))
    return out
