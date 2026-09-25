"""ИИ-помощь через OpenAI: тайминги получше и «режиссёр», который размечает песню.

Ключ берётся из переменной OPENAI_API_KEY или из файла .env / .emv рядом с программой.
Всё, что ИИ придумал, сохраняется в файлы трека — второй раз платить не нужно.

Расход на песню: разметка ~2.5 тыс. токенов (доли цента на mini-модели),
распознавание — по минутам звука.
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).parent
KEY_FILES = (".env", ".emv")
KEY_NAMES = ("OPENAI_API_KEY", "OPEN_AI_KEY", "OPENAI_KEY")

TRANSCRIBE_MODEL = "whisper-1"        # даёт время каждого слова
DIRECTOR_MODEL = "gpt-4.1-mini"       # дёшево и достаточно для разметки
SHAPES = ("wide", "medium", "tall")


def load_key():
    """Ключ OpenAI или None, если его нет."""
    for name in KEY_NAMES:
        if os.environ.get(name):
            return os.environ[name].strip()
    for fname in KEY_FILES:
        path = ROOT / fname
        if not path.exists():
            continue
        found = dict(re.findall(r"(\w+)\s*=\s*['\"]?([^'\"\s]+)", path.read_text(encoding="utf-8")))
        for name in KEY_NAMES:
            if found.get(name):
                return found[name]
    return None


def available():
    return bool(load_key())


def client():
    from openai import OpenAI
    key = load_key()
    if not key:
        raise RuntimeError("Нет ключа OpenAI: положи его в .env как OPENAI_API_KEY")
    return OpenAI(api_key=key)


# ---------- тайминги ----------

def transcribe_words(audio, model=TRANSCRIBE_MODEL):
    """[(слово, начало в сек), ...] — как autotime.transcribe, но распознаёт облако.
    Слышит заметно лучше локальной модели (особенно вступления и эффекты на голосе)."""
    from autotime import split_words
    with open(audio, "rb") as f:
        res = client().audio.transcriptions.create(
            model=model, file=f, response_format="verbose_json",
            timestamp_granularities=["word"], language="ru")
    out = []
    for w in getattr(res, "words", None) or []:
        for part in split_words(w.word):
            out.append((part, float(w.start)))
    return out


def auto_timings(audio, lines, progress=None):
    """Тайминги строк через облачное распознавание (сопоставление — наше же, из autotime)."""
    import autotime
    if progress:
        progress(0.3)
    words = transcribe_words(audio)
    if progress:
        progress(0.9)
    return autotime.line_times(lines, words)


# ---------- «режиссёр»: разметка песни ----------

DIRECTOR_RULES = """Ты режиссёр коротких вертикальных клипов (TikTok) для песни.
Тебе дают строки песни с временем начала. Размечаешь, как их показывать.

Правила:
- big=true — строка идёт крупно по центру экрана (под лип-синк, крупный план лица).
  Таких строк мало: 1-2 на куплет или яркий момент, между ними хотя бы 2 обычные строки.
  Подряд крупные строки не ставим.
- Строки подряд с big=true и обычные между ними образуют «особый момент»:
  там музыка бьёт сильнее, поэтому выделяй самый цепляющий кусок песни.
- frame — с этой строки меняется форма окна камеры: "wide" (широкий кадр),
  "medium" (поуже), "tall" (вертикальный), null — не менять.
  Меняй форму на смене частей песни (куплет/припев/бридж) и иногда внутри — 5-9 раз на песню.
- keyword — одно слово из строки, которое вынести крупно. Только для самых сильных строк
  (3-6 на песню), для остальных null. Слово должно быть в строке дословно.
Отвечай JSON по схеме."""

DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "big": {"type": "boolean"},
                    "frame": {"type": ["string", "null"], "enum": [*SHAPES, None]},
                    "keyword": {"type": ["string", "null"]},
                },
                "required": ["i", "big", "frame", "keyword"],
                "additionalProperties": False,
            },
        },
        "note": {"type": "string"},
    },
    "required": ["lines", "note"],
    "additionalProperties": False,
}


def direct(entries, length, model=DIRECTOR_MODEL):
    """entries — [(время, строка)]. Вернёт {"scenes": {строки}, "frames": {строка: форма},
    "keywords": {строка: слово}, "note": "что придумал"}."""
    song = "\n".join(f"{i}\t{int(t // 60):02d}:{t % 60:05.2f}\t{s}"
                     for i, (t, s) in enumerate(entries))
    res = client().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": DIRECTOR_RULES},
                  {"role": "user", "content": f"Длина песни {length:.0f} с.\n"
                                              f"Строки (номер, время, текст):\n{song}"}],
        response_format={"type": "json_schema", "json_schema": {
            "name": "marks", "strict": True, "schema": DIRECTOR_SCHEMA}},
    )
    return apply_marks(entries, json.loads(res.choices[0].message.content))


def apply_marks(entries, data):
    """Ответ модели → пометки трека (лишнее и выдуманное отбрасываем)."""
    scenes, frames, keywords = set(), {}, {}
    prev_big = -9
    for item in data.get("lines", []):
        i = item.get("i")
        if not isinstance(i, int) or not 0 <= i < len(entries):
            continue
        text = entries[i][1]
        if item.get("big") and i - prev_big > 1:      # подряд крупные — не даём
            scenes.add(text)
            prev_big = i
        if item.get("frame") in SHAPES:
            frames[text] = item["frame"]
        word = (item.get("keyword") or "").strip()
        if word and word.lower() in text.lower():     # слово должно быть в строке
            keywords[text] = word
    return {"scenes": scenes, "frames": frames, "keywords": keywords,
            "note": data.get("note", "")}
