"""Сервер для веб-версии: отдаёт саму PWA и готовит песню к показу.

Телефон присылает песню и текст — сервер считает биты, распознаёт тайминги (OpenAI)
и просит «режиссёра» разметить строки. Обратно уходит тот же JSON, что делает
кнопка «📱 Для телефона» в программе на компьютере.

Песни не храним: файл живёт на диске только во время обработки.

Запуск: uvicorn server:app --port 8080
Ключ OpenAI — в переменной окружения OPENAI_API_KEY (в Railway это Variables).
"""
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import ai
import autotime
import beats

ROOT = Path(__file__).parent
WEB = ROOT / "docs"
MAX_MB = 25            # столько принимает распознавание OpenAI

app = FastAPI(title="YeahMusic")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"ok": True, "ai": ai.available()}


def clean_lines(text):
    out = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if line and not line.startswith("#") and not (line.startswith("[") and line.endswith("]")):
            out.append(line)
    return out


@app.post("/api/prepare")
async def prepare(audio: UploadFile = File(...), lyrics: str = Form(""), name: str = Form("")):
    """Песня + текст → разметка для показа: тайминги, биты, громкость, что показывать крупно."""
    lines = clean_lines(lyrics)
    suffix = Path(audio.filename or "song.mp3").suffix or ".mp3"
    tmp = Path(tempfile.mkdtemp()) / f"song{suffix}"
    try:
        with tmp.open("wb") as f:
            shutil.copyfileobj(audio.file, f)
        if tmp.stat().st_size > MAX_MB * 1024 * 1024:
            raise HTTPException(413, f"Песня больше {MAX_MB} МБ — обрежь или пожми")

        data = beats.analyze(tmp)
        length = round(len(data["levels"]) / data["level_fps"], 2)
        times = [None] * len(lines)
        marks = {"scenes": set(), "frames": {}, "keywords": {}, "note": ""}
        if lines and ai.available():                       # тайминги и разметка — по ключу
            times = autotime.line_times(lines, ai.transcribe_words(tmp))
            marks = ai.direct(fill(times, lines, length), length)
        entries = fill(times, lines, length)
        return JSONResponse({
            "name": name or Path(audio.filename or "песня").stem,
            "length": length, "bpm": data["bpm"], "beats": data["beats"],
            "strength": data["strength"], "levels": data["levels"],
            "level_fps": data["level_fps"],
            "ai": ai.available(),
            "note": marks["note"],
            "lines": [{"t": round(t, 2), "text": s, "big": s in marks["scenes"],
                       "frame": marks["frames"].get(s), "word": marks["keywords"].get(s)}
                      for t, s in entries],
        })
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)      # песню у себя не держим


def fill(times, lines, length):
    """Строки без распознанного времени раскидываем между соседями (как в программе)."""
    known = [(-1, 0.0)] + [(i, t) for i, t in enumerate(times) if t is not None]
    known.append((len(lines), length))
    out = list(times)
    for (i0, t0), (i1, t1) in zip(known, known[1:]):
        for i in range(i0 + 1, i1):
            out[i] = t0 + (t1 - t0) * (i - i0) / max(1, i1 - i0)
    return [(t or 0.0, s) for t, s in zip(out, lines)]


if WEB.exists():           # саму веб-версию отдаём отсюда же
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
