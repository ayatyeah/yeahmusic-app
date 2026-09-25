"""Автотайминги: Whisper слушает песню и находит время каждого слова,
потом слова сопоставляются с текстом песни — у каждой строки появляется время начала.

Сопоставление нестрогое: в песнях Whisper часто слышит слово чуть иначе
(«мыслялях» вместо «мыслях-лях»), поэтому слова сравниваются по похожести,
а порядок строк сохраняется (выравнивание как у diff, только с «почти равно»).
"""
import difflib
import re
from pathlib import Path

MODEL_SIZE = "small"       # tiny / base / small / medium — больше = точнее, но дольше
MODELS_DIR = Path(__file__).parent / "models" / "whisper"
SIMILAR = 0.6              # слова считаем одинаковыми, если похожи хотя бы на столько
GAP = 0.3                  # штраф за пропущенное слово при выравнивании
WORD_LEAD = 0.25           # сек на слово: если первые слова строки не узнаны — сдвиг назад
MIN_WORD = 3               # слова короче — ненадёжные якоря для времени строки
MIN_LINE_GAP = 1.0         # строки не начинаются чаще, чем раз в столько секунд
MIN_SYLLABLE = 0.11        # быстрее этого (сек на слог) строку не спеть — такое время ложное
MIN_REPEAT = 3             # повтор блока из стольких строк (припев) — копируем тайминги


def norm(word):
    return re.sub(r"[^\w]", "", word.lower().replace("ё", "е"))


def split_words(text):
    return [w for w in (norm(x) for x in re.split(r"[\s\-–—]+", text)) if w]


def transcribe(audio, lines, progress=None):
    """[(слово, начало в сек), ...] из песни. progress(доля от 0 до 1) — по ходу."""
    from faster_whisper import WhisperModel
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8",
                         download_root=str(MODELS_DIR))
    # подсказка текстом песни — так Whisper лучше слышит нужные слова
    prompt = " ".join(dict.fromkeys(lines))[:600]
    segments, info = model.transcribe(
        str(audio), language="ru", word_timestamps=True, initial_prompt=prompt,
        condition_on_previous_text=False, vad_filter=False, beam_size=5)
    words = []
    for seg in segments:
        if progress:
            progress(min(1.0, seg.end / max(info.duration, 1)))
        for w in seg.words or []:
            for part in split_words(w.word):
                words.append((part, w.start))
    return words


def similarity(a, b):
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def align(lyric, heard):
    """Выравнивание двух списков слов с «почти равно».
    Вернёт {индекс слова текста: индекс услышанного слова}."""
    n, m = len(lyric), len(heard)
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[0] * (m + 1) for _ in range(n + 1)]     # 1 — пара, 2 — пропуск в тексте, 3 — в услышанном
    for i in range(1, n + 1):
        score[i][0], move[i][0] = -GAP * i, 2
    for j in range(1, m + 1):
        score[0][j], move[0][j] = -GAP * j, 3
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = (similarity(lyric[i - 1], heard[j - 1]) - 0.5) * 2
            best, how = score[i - 1][j - 1] + s, 1
            if score[i - 1][j] - GAP > best:
                best, how = score[i - 1][j] - GAP, 2
            if score[i][j - 1] - GAP > best:
                best, how = score[i][j - 1] - GAP, 3
            score[i][j], move[i][j] = best, how
    pairs, i, j = {}, n, m
    while i > 0 and j > 0:
        how = move[i][j]
        if how == 1:
            if similarity(lyric[i - 1], heard[j - 1]) >= SIMILAR:
                pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif how == 2:
            i -= 1
        else:
            j -= 1
    return pairs


def line_times(lines, words):
    """Время начала каждой строки (None — не узнали) по услышанным словам.
    Время ставим только по «надёжным» словам (от MIN_WORD букв): «у», «в», «ты»
    Whisper слышит где попало, и за них строки цеплялись не туда."""
    lyric, owner = [], []
    for li, line in enumerate(lines):
        for w in split_words(line):
            lyric.append(w)
            owner.append(li)
    pairs = align(lyric, [w for w, _ in words])
    times = [None] * len(lines)
    pos_in_line = {}
    for k, li in enumerate(owner):
        pos = pos_in_line.get(li, 0)
        pos_in_line[li] = pos + 1
        if times[li] is None and k in pairs and len(lyric[k]) >= MIN_WORD:
            times[li] = max(0.0, words[pairs[k]][1] - WORD_LEAD * pos)
    # время должно идти по порядку, не слипаться и оставлять время спеть строки между
    prev, prev_li = -1.0, None
    for li, t in enumerate(times):
        if t is None:
            continue
        need = MIN_LINE_GAP
        if prev_li is not None:
            need = max(need, MIN_SYLLABLE * sum(syllables(x) for x in lines[prev_li:li]))
        if t < prev + need:
            times[li] = None
        else:
            prev, prev_li = t, li
    times = copy_repeats(lines, times, end=(words[-1][1] + 3) if words else 0.0)
    return [round(t, 2) if t is not None else None for t in times]


def syllables(line):
    return max(1, sum(ch in "аеёиоуыэюяaeiouy" for ch in line.lower()))


def interpolate(times, end):
    """Пустые места — равномерно между соседями (как при показе)."""
    known = [(-1, 0.0)] + [(i, t) for i, t in enumerate(times) if t is not None]
    known.append((len(times), max(end, known[-1][1] + 1)))
    out = list(times)
    for (i0, t0), (i1, t1) in zip(known, known[1:]):
        for i in range(i0 + 1, i1):
            out[i] = t0 + (t1 - t0) * (i - i0) / (i1 - i0)
    return out


def repeats(lines):
    """[(начало первого раза, начало повтора, длина), ...] — повторяющиеся блоки строк."""
    found, j = [], 0
    while j < len(lines):
        best = (0, 0)
        for i in range(j):
            n = 0
            while j + n < len(lines) and i + n < j and lines[i + n] == lines[j + n]:
                n += 1
            best = max(best, (n, i))
        if best[0] >= MIN_REPEAT:
            found.append((best[1], j, best[0]))
            j += best[0]
        else:
            j += 1
    return found


def copy_repeats(lines, times, end):
    """Whisper «съедает» повторы припева. Повтору ставим тайминги первого раза
    со сдвигом, а сдвиг берём по строкам повтора, которые всё-таки узнали."""
    times = list(times)
    for a, b, n in repeats(lines):
        first = interpolate(times, end)[a:a + n]
        shifts = sorted(times[b + k] - first[k] for k in range(n) if times[b + k] is not None)
        if not shifts:
            continue
        shift = shifts[len(shifts) // 2]
        for k in range(n):
            times[b + k] = first[k] + shift
    return times


def auto_timings(audio, lines, progress=None):
    return line_times(lines, transcribe(audio, lines, progress))
