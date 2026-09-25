"""Отправка своих треков в личное хранилище на сервере (Railway).

    python sync_tracks.py                      # все треки
    python sync_tracks.py "ты в моих мыслях"   # только один

Адрес сервера — в SERVER (или переменная YEAHMUSIC_SERVER), ключ — ACCESS_KEY
(тот же, что задан в переменных Railway). Песни лежат в хранилище под ключом:
без него их никто не увидит.
"""
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

import pygame

import beats
import main as app

SERVER = os.environ.get("YEAHMUSIC_SERVER", "https://yeahmusic-app-production.up.railway.app")


def marks(track):
    """Та же разметка, что делает кнопка «📱 Для телефона»."""
    length = app.track_length(track.audio)
    entries = app.fill_timing(track.entries(), length)
    scenes, frames, words = track.scenes(), track.frames(), track.keywords()
    data = beats.track_beats(track)
    return {
        "name": track.name, "length": round(length, 2), "bpm": data.get("bpm"),
        "beats": data.get("beats", []), "strength": data.get("strength", []),
        "levels": data.get("levels", []), "level_fps": data.get("level_fps", 20),
        "lines": [{"t": round(t, 2), "text": s, "big": s in scenes,
                   "frame": frames.get(s), "word": words.get(s)} for t, s in entries],
    }


def post(url, fields, files):
    """Простая multipart-отправка, чтобы не тащить лишних библиотек."""
    boundary = uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                 f"{value}\r\n").encode()
    for name, (filename, content) in files.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                 f"filename=\"{filename}\"\r\nContent-Type: audio/mpeg\r\n\r\n").encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as res:
        return json.loads(res.read())


def main():
    key = os.environ.get("ACCESS_KEY") or ""
    if not key:
        env = Path(__file__).parent / ".env"
        if env.exists():
            found = dict(l.split("=", 1) for l in env.read_text().splitlines() if "=" in l)
            key = found.get("ACCESS_KEY", "").strip().strip("'\"")
    if not key:
        sys.exit("Нет ключа. Положи ACCESS_KEY в .env рядом с программой (тот же, что в Railway).")

    pygame.mixer.init()
    wanted = sys.argv[1:]
    for track in app.list_tracks():
        if wanted and track.name not in wanted:
            continue
        if not track.audio:
            continue
        print(f"{track.name}: считаю разметку…", flush=True)
        data = marks(track)
        print(f"{track.name}: отправляю {track.audio.stat().st_size // 1024} КБ…", flush=True)
        res = post(f"{SERVER}/api/tracks",
                   {"key": key, "name": track.name, "marks": json.dumps(data, ensure_ascii=False)},
                   {"audio": (track.audio.name, track.audio.read_bytes())})
        print(f"{track.name}: готово ({res})")


if __name__ == "__main__":
    main()
