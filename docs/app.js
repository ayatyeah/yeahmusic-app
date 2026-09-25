// Связка всего: песня, разметка, камера, эффекты, жесты и запись видео.
// Разметку делает программа на компьютере (кнопка «📱 Для телефона») — сюда кладём тот файл.

import { Stage } from './effects.js';
import { Vision } from './vision.js';

const $ = (id) => document.getElementById(id);
const stage = new Stage($('canvas'));
const vision = new Vision();

const state = {
  data: null,            // разметка песни: строки, биты, громкость
  audio: new Audio(),
  video: document.createElement('video'),
  stream: null,
  playing: false,
  flags: { camera: true, hands: true, silhouette: true, beat: true, mirror: true, box: false },
  nextLine: 0, nextBoom: 0, lastEq: -9, lastSide: -9, side: 'right', lastWord: -9,
  moments: [],
};
state.video.playsInline = true;
state.video.muted = true;
state.audio.preload = 'auto';

// ---------- настройки-«фишки» ----------

document.querySelectorAll('.chip').forEach((chip) => {
  const flag = chip.dataset.flag;
  chip.classList.toggle('on', !!state.flags[flag]);
  chip.onclick = () => {
    state.flags[flag] = !state.flags[flag];
    chip.classList.toggle('on', state.flags[flag]);
    if (flag === 'mirror') stage.mirror = state.flags.mirror;
    if (flag === 'box') stage.box = state.flags.box;
    if (flag === 'camera') state.flags.camera ? startCamera() : stopCamera();
  };
});
stage.mirror = state.flags.mirror;

const status = (text) => { $('status').textContent = text; };

// ---------- файлы ----------

$('audioFile').onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  state.audio.src = URL.createObjectURL(file);
  status(`Песня: ${file.name}`);
};

$('dataFile').onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    state.data = JSON.parse(await file.text());
    state.moments = findMoments(state.data.lines || []);
    status(`Разметка: ${state.data.name || file.name} — строк ${state.data.lines.length}`);
  } catch (err) {
    status(`Файл разметки не читается: ${err.message}`);
  }
};

// Обработка песни на сервере: тайминги, биты и разметка ИИ — прямо с телефона.
$('prepareBtn').onclick = async () => {
  const file = $('audioFile').files[0];
  if (!file) { status('Сначала выбери песню.'); return; }
  const form = new FormData();
  form.append('audio', file);
  form.append('lyrics', $('lyrics').value);
  form.append('name', file.name.replace(/\.[^.]+$/, ''));
  $('prepareBtn').disabled = true;
  status('Сервер слушает песню, это примерно минута…');
  try {
    const res = await fetch('api/prepare', { method: 'POST', body: form });
    if (!res.ok) throw new Error((await res.text()).slice(0, 200));
    state.data = await res.json();
    state.moments = findMoments(state.data.lines || []);
    const big = state.data.lines.filter((l) => l.big).length;
    status(state.data.ai
      ? `Готово: строк ${state.data.lines.length}, крупных ${big}, темп ${state.data.bpm}.`
      : `Биты посчитаны (темп ${state.data.bpm}), но тайминги не распознать: на сервере нет ключа.`);
  } catch (err) {
    status(`Сервер не справился: ${err.message}`);
  }
  $('prepareBtn').disabled = false;
};

$('demoBtn').onclick = () => {
  state.data = null;
  state.moments = [];
  status('Без разметки: только камера, руки и жесты.');
};

/** Особые моменты: крупные строки и обычные между ними (как в программе). */
function findMoments(lines) {
  const idx = lines.map((l, i) => (l.big ? i : -1)).filter((i) => i >= 0);
  const groups = [];
  for (const i of idx) {
    const last = groups[groups.length - 1];
    if (last && i - last[last.length - 1] <= 4) last.push(i);
    else groups.push([i]);
  }
  return groups.map((g) => {
    const end = g[g.length - 1] + 1;
    return [lines[g[0]].t, end < lines.length ? lines[end].t : 1e9];
  });
}

const inMoment = (t) => state.moments.some(([a, b]) => t >= a && t < b);

// ---------- камера ----------

async function startCamera() {
  if (state.stream) return;
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    state.video.srcObject = state.stream;
    await state.video.play();
    status('Камера включена.');
    if (!vision.ready && (state.flags.hands || state.flags.silhouette)) {
      status('Загружаю распознавание рук…');
      await vision.init({ wantHands: state.flags.hands, wantSegment: state.flags.silhouette });
      status('Готово.');
    }
  } catch (err) {
    status(`Камера не открылась: ${err.message}`);
  }
}

function stopCamera() {
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  state.video.srcObject = null;
}

// ---------- показ ----------

$('startBtn').onclick = async () => {
  if (state.flags.camera) await startCamera();
  state.nextLine = 0; state.nextBoom = 0; state.lastEq = -9; state.lastWord = -9;
  stage.cards = []; stage.effects = [];
  if (state.audio.src) { state.audio.currentTime = 0; await state.audio.play(); }
  state.playing = true;
  $('stopBtn').disabled = false;
  $('panel').classList.add('hidden');
};

$('stopBtn').onclick = () => {
  state.playing = false;
  state.audio.pause();
  $('stopBtn').disabled = true;
  $('panel').classList.remove('hidden');
};

$('showPanel').onclick = () => $('panel').classList.toggle('hidden');

/** Что должно случиться к этому моменту песни. */
function timeline(songTime, now) {
  const data = state.data;
  if (!data) return;
  const lines = data.lines || [];
  while (state.nextLine < lines.length && songTime >= lines[state.nextLine].t) {
    const line = lines[state.nextLine];
    const next = lines[state.nextLine + 1];
    const duration = (next ? next.t : data.length || songTime + 4) - line.t;
    state.nextLine++;
    if (line.frame) stage.setShape(line.frame, now);
    stage.addCard(line.text, duration, 1, now, line.big);
    const stars = line.text.indexOf('***');        // «****» — цензурный писк
    if (stars >= 0) setTimeout(() => sound('bleep'), stars * 85);
    if (line.big) vision.flashOutline(now);
    const word = line.word || pickWord(line.text);
    if (word && !line.big && now - state.lastWord > 4) {
      state.lastWord = now;
      stage.word(word.toUpperCase(), inMoment(songTime), now);
    }
    const side = pickWord(line.text, 5);
    if (side && !line.big && now - state.lastSide > 2.5 && inMoment(songTime)) {
      state.lastSide = now;
      state.side = state.side === 'right' ? 'left' : 'right';
      stage.sideWord(side.toUpperCase(), state.side === 'left', now);
    }
    if (inMoment(songTime) && /^\W*(у|о|а|я|э)-(у|о|а|я|э)/i.test(line.text)) {
      stage.shake(2.5, now);
      stage.glitch(300, now);
    }
  }
  if (!state.flags.beat) return;
  const beats = data.beats || [], strength = data.strength || [];
  while (state.nextBoom < beats.length && beats[state.nextBoom] - 0.03 <= songTime) {
    const i = state.nextBoom++;
    const s = strength[i] ?? 0.6;
    if (songTime - beats[i] > 0.15 || s < 0.35) continue;
    const moment = inMoment(songTime);
    stage.boom(moment ? Math.min(1, s * 1.15) : s * 0.75, moment ? (i % 2 ? 1 : -1) : 0, now);
    if (s >= 0.5) stage.push(s, now);
    if (moment && s >= 0.85) stage.glitch(140, now);
  }
  const levels = data.levels || [];
  if (levels.length && now - state.lastEq > 0.07) {
    state.lastEq = now;
    const i = Math.min(levels.length - 1, Math.floor(songTime * (data.level_fps || 20)));
    stage.setEq(levels[i].map((v) => v / 100), now);
  }
}

/** Слово для крупного показа, если разметка его не назвала. */
function pickWord(text, min = 4) {
  const words = (text.match(/[\wёЁ]+/g) || []).filter((w) => w.length >= min);
  return words.sort((a, b) => b.length - a.length)[0] || null;
}

// ---------- жесты ----------

const onCam = (cam, p) => [cam.x + (state.flags.mirror ? 1 - p[0] : p[0]) * cam.w,
                           cam.y + p[1] * cam.h];

function handleEvent(e, now, cam) {
  if (e.type === 'shot') {
    const [x, y] = onCam(cam, e.muzzle);
    const dir = [state.flags.mirror ? -e.dir[0] : e.dir[0], e.dir[1]];
    stage.shot(x, y, dir, now);
    stage.spark(x, y, now);
    sound('shot');
    return;
  }
  if (e.type === 'heart') {
    const [x, y] = onCam(cam, e.point);
    for (let i = 0; i < 2; i++) stage.heart(x, y);
    return;
  }
  const name = e.name;
  if (name === 'cover') {
    if (state.audio.paused) state.audio.play(); else state.audio.pause();
  } else if (name === 'wave') stage.shake(1, now);
  else if (name === 'up') stage.jump([0, 1], now);
  else if (name === 'down') stage.jump([0, -1], now);
  else if (name === 'left') stage.jump([-1, 0], now);
  else if (name === 'right') stage.jump([1, 0], now);
}

// ---------- звуки (синтез, без файлов) ----------

let actx = null;
function sound(kind) {
  actx = actx || new (window.AudioContext || window.webkitAudioContext)();
  const t = actx.currentTime;
  if (kind === 'shot') {                       // выстрел: щелчок шума + низкий удар
    const n = actx.sampleRate * 0.4;
    const buf = actx.createBuffer(1, n, actx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < n; i++) {
      const k = i / actx.sampleRate;
      data[i] = (Math.random() * 2 - 1) * Math.exp(-k * 28)
              + Math.sin(2 * Math.PI * (95 - 50 * k) * k) * Math.exp(-k * 14) * 0.8;
    }
    const src = actx.createBufferSource();
    const gain = actx.createGain();
    gain.gain.value = 0.8;
    src.buffer = buf;
    src.connect(gain).connect(actx.destination);
    src.start();
  } else {                                     // цензурный «пи-ип»
    const osc = actx.createOscillator();
    const gain = actx.createGain();
    osc.frequency.value = 1000;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.25, t + 0.01);
    gain.gain.setValueAtTime(0.25, t + 0.45);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.5);
    osc.connect(gain).connect(actx.destination);
    osc.start(t);
    osc.stop(t + 0.5);
  }
}

// ---------- главный цикл ----------

function loop() {
  const now = performance.now() / 1000;
  const songTime = state.audio.currentTime;
  if (state.playing) timeline(songTime, now);
  vision.segment = state.flags.silhouette && vision.ready && inMoment(songTime);
  stage.zoomTarget = inMoment(songTime) ? 1.3 : 1;      // в особом моменте — ближе к лицу
  if (state.flags.camera && vision.ready) {
    const cam = stage.box(now);
    for (const e of vision.process(state.video, now)) handleEvent(e, now, cam);
  }
  stage.draw(now, state.flags.camera ? state.video : null,
             state.flags.hands || state.flags.silhouette ? vision : null);
  requestAnimationFrame(loop);
}

addEventListener('resize', () => stage.resize());
stage.resize();
loop();

// ---------- запись видео ----------

let recorder = null, chunks = [];

$('recBtn').onclick = async () => {
  if (recorder) {
    recorder.stop();
    return;
  }
  try {
    const canvasStream = $('canvas').captureStream(30);
    const ctxAudio = new (window.AudioContext || window.webkitAudioContext)();
    const dest = ctxAudio.createMediaStreamDestination();
    if (state.audio.src) {                       // песня — в запись
      const src = ctxAudio.createMediaElementSource(state.audio);
      src.connect(dest);
      src.connect(ctxAudio.destination);
    }
    try {                                        // и голос с микрофона
      const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      ctxAudio.createMediaStreamSource(mic).connect(dest);
    } catch { /* без микрофона — тоже нормально */ }
    const stream = new MediaStream([...canvasStream.getVideoTracks(),
                                    ...dest.stream.getAudioTracks()]);
    const type = ['video/mp4;codecs=avc1', 'video/webm;codecs=vp9', 'video/webm']
      .find((t) => MediaRecorder.isTypeSupported(t));
    recorder = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 8_000_000 });
    chunks = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `yeahmusic-${Date.now()}.${type.includes('mp4') ? 'mp4' : 'webm'}`;
      a.click();
      recorder = null;
      $('recBtn').textContent = '● Запись';
      $('rec').textContent = '';
      status('Видео сохранено.');
    };
    recorder.start();
    $('recBtn').textContent = '■ Стоп запись';
    const started = Date.now();
    const tick = () => {
      if (!recorder) return;
      const s = Math.floor((Date.now() - started) / 1000);
      $('rec').textContent = `● ${String((s / 60) | 0).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
      setTimeout(tick, 500);
    };
    tick();
  } catch (err) {
    status(`Запись не пошла: ${err.message}`);
  }
};

// ---------- установка как приложения ----------

let installPrompt = null;
addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  installPrompt = e;
  $('installBtn').hidden = false;
});
$('installBtn').onclick = async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  installPrompt = null;
  $('installBtn').hidden = true;
};

if ('serviceWorker' in navigator) {
  addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}
