// Связка всего: песня, разметка, камера, эффекты, жесты и запись видео.
// Разметку делает программа на компьютере (кнопка «📱 Для телефона») — сюда кладём тот файл.

import { Stage } from './effects.js';
import { Vision } from './vision.js';
import { store } from './store.js';

const VERSION = 'v18 · 25.09';        // видно на заставке — сразу понятно, обновилось ли
const $ = (id) => document.getElementById(id);

// Любая ошибка — на экран, а не в молчаливый чёрный фон.
const errors = window.boot || [];
addEventListener('error', (e) => errors.push(e.message));
addEventListener('unhandledrejection', (e) => errors.push(String(e.reason?.message || e.reason)));

// Сервер обработки: своя страница с сервером — она же, иначе (GitHub Pages) — Railway.
const DEFAULT_API = 'https://yeahmusic-app-production.up.railway.app';
const api = () => (localStorage.getItem('api')
  || (location.origin.includes('github.io') ? DEFAULT_API : location.origin)).replace(/\/$/, '');
const stage = new Stage($('canvas'));
const vision = new Vision();

const state = {
  data: null,            // разметка песни: строки, биты, громкость
  audio: new Audio(),
  video: document.createElement('video'),
  stream: null,
  playing: false,
  flags: { camera: true, hands: true, silhouette: true, beat: true, mirror: true, plates: false,
           film: true, face: true, zones: true, mic: false, breakout: false },
  nextLine: 0, nextBoom: 0, lastEq: -9, lastSide: -9, side: 'right', lastWord: -9,
  moments: [],
  track: null,             // какая песня выбрана сейчас
};
// Запись: объявлено здесь, потому что цикл отрисовки рисует отсчёт раньше, чем дойдёт
// очередь до раздела записи ниже.
const rec = { recorder: null, chunks: [], type: '', timer: null, wake: null, count: null };
state.video.playsInline = true;
state.video.muted = true;
state.video.setAttribute('playsinline', '');
state.video.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none';
document.body.appendChild(state.video);
state.audio.preload = 'auto';

// ---------- настройки-«фишки» ----------

document.querySelectorAll('.chip').forEach((chip) => {
  const flag = chip.dataset.flag;
  chip.classList.toggle('on', !!state.flags[flag]);
  chip.onclick = () => {
    state.flags[flag] = !state.flags[flag];
    chip.classList.toggle('on', state.flags[flag]);
    if (flag === 'mirror') stage.mirror = state.flags.mirror;
    if (flag === 'plates') stage.plates = state.flags.plates;
    if (flag === 'film') stage.film = state.flags.film;
    if (flag === 'face') stage.follow = state.flags.face;
    if (flag === 'zones') fit();
    if (flag === 'breakout') {
      stage.breakout = state.flags.breakout;
      if (state.flags.breakout && vision.ready && !vision.segmenter) {
        vision.ready = false;                  // силуэт ещё не грузили — догружаем
        loadVision();
      }
      status(state.flags.breakout
        ? 'Камера в рамке: высунь руку или голову за край — они останутся видны.'
        : 'Камера снова во весь экран.');
    }
    if (flag === 'camera') state.flags.camera ? startCamera() : stopCamera();
  };
});
stage.mirror = state.flags.mirror;
stage.film = state.flags.film;
stage.follow = state.flags.face;

// Подсказки: в меню — строкой, а когда меню закрыто — всплывают сверху над кадром.
let pillTimer = null;

function pill(text, ms = 4000) {
  $('nowPlaying').textContent = text;
  clearTimeout(pillTimer);
  if (ms) pillTimer = setTimeout(() => {
    $('nowPlaying').textContent = state.track ? state.track.name : '';
  }, ms);
}

const status = (text) => {
  $('status').textContent = text;
  if (text && $('sheet').classList.contains('hidden')) pill(text);
};

// ---------- файлы ----------

$('audioFile').onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  e.target.value = '';
  const item = await store.save({ name: file.name.replace(/\.[^.]+$/, ''), audio: file, data: null });
  await loadTrack(item);
  await renderTracks();
  status(`Добавил «${item.name}». Теперь можно сделать разметку или сразу показывать.`);
};

$('dataFile').onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  e.target.value = '';
  try {
    const data = JSON.parse(await file.text());
    applyData(data);
    if (state.track) {
      state.track = await store.save({ ...state.track, data });
      await renderTracks();
    }
    status(`Разметка на месте: строк ${data.lines.length}`);
  } catch (err) {
    status(`Файл разметки не читается: ${err.message}`);
  }
};

function applyData(data) {
  state.data = data;
  state.moments = findMoments(data.lines || []);
}

async function loadTrack(item) {
  state.track = item;
  pill(item.name, 0);
  state.audio.src = URL.createObjectURL(item.audio);
  if (item.data) applyData(item.data); else { state.data = null; state.moments = []; }
  await renderTracks();
}

async function renderTracks() {
  const list = await store.list();
  const box = $('tracks');
  box.innerHTML = '';
  if (!list.length) {
    box.innerHTML = '<div class="empty">Пока пусто. Добавь песню кнопкой ниже.</div>';
    return;
  }
  for (const item of list) {
    const row = document.createElement('div');
    row.className = 'track' + (state.track && item.id === state.track.id ? ' active' : '');
    const size = Math.round((item.audio?.size || 0) / 104857.6) / 10;
    row.innerHTML = `<div class="name">${item.name}
        <div class="meta">${item.data ? `разметка: ${item.data.lines.length} строк` : 'без разметки'}
        · ${size} МБ</div></div>`;
    const play = document.createElement('button');
    play.textContent = '▶';
    play.onclick = async () => { await loadTrack(item); start(); };
    const del = document.createElement('button');
    del.textContent = '✕';
    del.onclick = async () => {
      await store.remove(item.id);
      if (state.track?.id === item.id) { state.track = null; state.data = null; state.audio.src = ''; }
      await renderTracks();
    };
    row.onclick = (e) => { if (e.target === row || e.target.closest('.name')) loadTrack(item); };
    row.append(play, del);
    box.append(row);
  }
}

renderTracks();

// Обработка песни на сервере: тайминги, биты и разметка ИИ — прямо с телефона.
$('prepareBtn').onclick = async () => {
  if (!state.track) { status('Сначала выбери песню в списке.'); return; }
  const form = new FormData();
  form.append('audio', state.track.audio, `${state.track.name}.mp3`);
  form.append('lyrics', $('lyrics').value);
  form.append('name', state.track.name);
  $('prepareBtn').disabled = true;
  status('Сервер слушает песню, это примерно минута…');
  try {
    const res = await fetch(`${api()}/api/prepare`, { method: 'POST', body: form });
    if (!res.ok) throw new Error(`сервер ответил ${res.status}: ${(await res.text()).slice(0, 160)}`);
    applyData(await res.json());
    state.track = await store.save({ ...state.track, data: state.data });
    await renderTracks();
    const big = state.data.lines.filter((l) => l.big).length;
    status(state.data.ai
      ? `Готово: строк ${state.data.lines.length}, крупных ${big}, темп ${state.data.bpm}.`
      : `Биты посчитаны (темп ${state.data.bpm}), но тайминги не распознаны: на сервере нет ключа `
        + 'OpenAI. Добавь его в Railway → Variables → OPENAI_API_KEY.');
  } catch (err) {
    status(`Сервер не справился: ${err.message}`);
  }
  $('prepareBtn').disabled = false;
};

/** Жив ли сервер и видит ли он ключ OpenAI. */
async function checkServer() {
  $('apiInput').value = api();
  try {
    const res = await fetch(`${api()}/health`, { cache: 'no-store' });
    const data = await res.json();
    $('serverState').textContent = data.ai
      ? '✅ сервер на связи, ИИ включён'
      : '⚠️ сервер на связи, но ключа OpenAI нет — будут только биты';
  } catch {
    $('serverState').textContent = '❌ сервер не отвечает — проверь адрес';
  }
}

$('apiInput').onchange = () => {
  localStorage.setItem('api', $('apiInput').value.trim() || DEFAULT_API);
  checkServer();
};
checkServer();



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

const CAMERA_HINTS = {
  NotAllowedError: 'браузер не дал доступ. Нажми на замок слева от адреса → Камера → Разрешить '
                   + '(на iPhone: Настройки → Safari → Камера).',
  NotFoundError: 'камера не найдена.',
  NotReadableError: 'камеру занял кто-то другой — закрой другие вкладки и программы с камерой.',
  SecurityError: 'нужен адрес на https.',
  OverconstrainedError: 'камера не поддержала запрошенный размер.',
};

async function startCamera() {
  if (state.stream) return true;
  if (!navigator.mediaDevices?.getUserMedia) {
    status('Браузер не умеет показывать камеру. Нужен Safari или Chrome по https.');
    return false;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      video: {                                   // просим максимум — телефон даст, что сможет
        facingMode: 'user',
        width: { ideal: 1920 }, height: { ideal: 1080 },
        frameRate: { ideal: 30, min: 24 },
      },
      audio: false,
    });
    const track = state.stream.getVideoTracks()[0];
    const set = track?.getSettings?.() || {};
    console.log('камера:', set.width, '×', set.height, set.frameRate, 'к/с');
    state.video.srcObject = state.stream;
    await state.video.play();
    status('Камера включена.');
  } catch (err) {
    const hint = CAMERA_HINTS[err.name] || err.message;
    status(`Камера не включилась: ${hint}`);
    return false;
  }
  loadVision();                       // распознавание грузится отдельно и камере не мешает
  return true;
}

async function loadVision() {
  if (vision.ready || vision.loading) return;
  if (!state.flags.hands && !state.flags.silhouette && !state.flags.breakout) return;
  vision.loading = true;
  try {
    await vision.init({ wantHands: state.flags.hands,
                        wantSegment: state.flags.silhouette || state.flags.breakout,
                        wantFace: state.flags.face });
    status('Руки и силуэт подключены.');
  } catch (err) {
    status(`Камера работает, но распознавание рук не загрузилось: ${err.message}`);
  }
  vision.loading = false;
}

function stopCamera() {
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  state.video.srcObject = null;
}

// ---------- показ ----------

/** Во весь экран: на Android — настоящий полноэкранный режим, на iPhone — установка PWA. */
async function goFullscreen() {
  try {
    if (!document.fullscreenElement && document.documentElement.requestFullscreen) {
      await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
      await screen.orientation?.lock?.('portrait').catch(() => {});
    }
  } catch { /* не дали — не страшно */ }
}

$('fullBtn').onclick = async () => {
  if (document.fullscreenElement) { document.exitFullscreen(); return; }
  await goFullscreen();
  if (!document.fullscreenElement) {
    status('Браузер не даёт полный экран. На iPhone: «Поделиться» → «На экран Домой» — '
           + 'приложение откроется без полос браузера.');
  }
};

async function start() {
  if (!state.audio.src) { status('Сначала добавь или выбери песню.'); return; }
  await goFullscreen();
  if (state.flags.camera) await startCamera();
  state.nextLine = 0; state.nextBoom = 0; state.lastEq = -9; state.lastWord = -9;
  stage.cards = []; stage.effects = [];
  state.audio.currentTime = 0;
  await state.audio.play();
  state.playing = true;
  setPlay(true);
  closeSheet();
  status(state.data ? '' : 'Показ без разметки: только камера и жесты.');
}

function stopShow() {
  state.playing = false;
  state.audio.pause();
  setPlay(false);
}

// ---------- нижняя панель и меню ----------

const sheet = $('sheet');
const closeSheet = () => sheet.classList.add('hidden');

function showTab(name) {
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('on', t.dataset.tab === name));
  document.querySelectorAll('.page').forEach((p) => { p.hidden = p.dataset.page !== name; });
}
document.querySelectorAll('.tab').forEach((t) => { t.onclick = () => showTab(t.dataset.tab); });

/** Кнопка нижней панели: значок, подпись и «горит ли». */
function setBtn(id, icon, label, on = false) {
  const b = $(id);
  b.querySelector('b').textContent = icon;
  b.querySelector('span').textContent = label;
  b.classList.toggle('on', on);
}

const setPlay = (playing) => setBtn('playBtn', playing ? '■' : '▶',
                                    playing ? 'Стоп' : 'Показ', playing);
const setCam = (on) => setBtn('camBtn', '📷', on ? 'Выключить' : 'Камера', on);

$('menuBtn').onclick = () => sheet.classList.toggle('hidden');
$('grip').onclick = closeSheet;
$('playBtn').onclick = () => (state.playing ? stopShow() : start());
$('camBtn').onclick = async () => {
  if (state.stream) { stopCamera(); setCam(false); return; }
  setCam(await startCamera());
};
$('version').textContent = VERSION;
setPlay(false);
setCam(false);

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
    if (s >= 0.7) stage.ring(now, s);                 // волна от сильного удара
    if (moment) stage.spawnSparks(now, s);            // искры в особом моменте
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

// ---------- песни с сервера (личное хранилище по ключу) ----------

const cloudKey = () => localStorage.getItem('key') || '';
$('keyInput').value = cloudKey();
$('keyInput').onchange = () => localStorage.setItem('key', $('keyInput').value.trim());

$('cloudBtn').onclick = async () => {
  const key = $('keyInput').value.trim() || cloudKey();
  if (!key) { $('cloud').innerHTML = '<div class="empty">Введи ключ хранилища.</div>'; return; }
  localStorage.setItem('key', key);
  $('cloud').innerHTML = '<div class="empty">Смотрю, что на сервере…</div>';
  try {
    const res = await fetch(`${api()}/api/tracks?key=${encodeURIComponent(key)}`);
    if (!res.ok) throw new Error(res.status === 403 ? 'ключ не подошёл' : await res.text());
    const list = await res.json();
    if (!list.length) { $('cloud').innerHTML = '<div class="empty">На сервере пусто.</div>'; return; }
    $('cloud').innerHTML = '';
    for (const item of list) {
      const row = document.createElement('div');
      row.className = 'track';
      row.innerHTML = `<div class="name">${item.name}
        <div class="meta">${item.lines} строк · ${Math.round(item.size / 104857.6) / 10} МБ</div></div>`;
      const get = document.createElement('button');
      get.textContent = '⤓';
      get.onclick = () => download(item, key, get);
      row.append(get);
      $('cloud').append(row);
    }
  } catch (err) {
    $('cloud').innerHTML = `<div class="empty">Не вышло: ${err.message}</div>`;
  }
};

/** Скачать песню с сервера и положить в свой список на телефоне. */
async function download(item, key, button) {
  button.textContent = '…';
  try {
    const url = (what) => `${api()}/api/tracks/${encodeURIComponent(item.id)}/${what}`
                          + `?key=${encodeURIComponent(key)}`;
    const [marks, audio] = await Promise.all([
      fetch(url('marks')).then((r) => r.json()),
      fetch(url('audio')).then((r) => r.blob()),
    ]);
    const saved = await store.save({ name: item.name, audio, data: marks });
    await loadTrack(saved);
    await renderTracks();
    status(`Скачал «${item.name}» — уже в списке песен.`);
    button.textContent = '✓';
  } catch (err) {
    status(`Не скачалось: ${err.message}`);
    button.textContent = '⤓';
  }
}

// ---------- проверка эффектов без песни ----------

const DEMO_LINES = ['Проверка строки раз', 'Вторая строка подлиннее, с переносом',
                    'Третья строка', 'И ещё одна для счёта'];

$('demoBtn').onclick = () => {
  const now = performance.now() / 1000;
  stage.cards = [];
  stage.effects = [];
  status('Проверка: строки, плашки, бум, глитч и выстрел. Плашки включаются кнопкой «Плашки».');
  closeSheet();
  DEMO_LINES.forEach((text, i) => {
    setTimeout(() => {
      const t = performance.now() / 1000;
      stage.addCard(text, 2.4, 1, t, i === 2);       // третью показываем крупно по центру
      if (i === 1) stage.word('ПРОВЕРКА', true, t);
      if (i === 3) stage.sideWord('СБОКУ', true, t);
    }, i * 1600);
  });
  for (let i = 0; i < 12; i++) {                      // удары, как под бит
    setTimeout(() => {
      const t = performance.now() / 1000;
      stage.boom(0.9, i % 2 ? 1 : -1, t);
      stage.push(0.8, t);
      if (i % 4 === 3) stage.glitch(160, t);
      if (i % 3 === 0) { stage.ring(t, 1); stage.spawnSparks(t, 1.5); }
      stage.setEq([0.9, 0.7, 0.5, 0.3, 0.15].map((v) => v * (0.5 + Math.random() / 2)), t);
    }, 400 + i * 500);
  }
  setTimeout(() => {                                  // выстрел со всеми эффектами
    const t = performance.now() / 1000;
    const cam = stage.box(t);
    const x = cam.x + cam.w * 0.6, y = cam.y + cam.h * 0.5;
    stage.shot(x, y, [1, -0.15], t);
    stage.spark(x, y, t);
    sound('shot');
  }, 3200);
  setTimeout(() => {
    const t = performance.now() / 1000;
    stage.flash(t); stage.clones(t); stage.ring(t, 1);
  }, 5200);
  void now;
};

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

let lastError = '';
const fps = { frames: 0, since: 0, value: 60 };

/** Если не вытягиваем — сами снижаем нагрузку, чтобы шло плавно. */
function keepSmooth(now) {
  fps.frames++;
  if (now - fps.since < 2) return;
  fps.value = fps.frames / (now - fps.since);
  fps.frames = 0;
  fps.since = now;
  if (fps.value > 52 && stage.quality < 1) {     // тянет — возвращаем качество
    stage.quality = 1;
    stage.resize();
    return;
  }
  if (fps.value > 45 || !state.playing && !state.stream) return;
  if (state.flags.silhouette) {                   // силуэт — самое тяжёлое, гасим первым
    state.flags.silhouette = false;
    document.querySelector('[data-flag=silhouette]')?.classList.remove('on');
    status(`Идёт рывками (${Math.round(fps.value)} кадров/с) — выключил силуэт.`);
  } else if (stage.quality > 0.7) {
    stage.quality = 0.7;                          // дальше — рисуем мельче и растягиваем
    stage.resize();
    status(`Идёт рывками (${Math.round(fps.value)} кадров/с) — снизил качество картинки.`);
  } else if (state.flags.hands && fps.value < 24) {
    state.flags.hands = false;
    document.querySelector('[data-flag=hands]')?.classList.remove('on');
    status('Совсем не вытягивает — выключил распознавание рук.');
  }
}

function loop() {
  try {
    const now = performance.now() / 1000;
    if (!fps.since) fps.since = now;
    keepSmooth(now);
    const songTime = state.audio.currentTime;
    if (state.playing) timeline(songTime, now);
    // силуэт нужен и для выхода за рамку — там он работает всю песню, а не только в моменте
    vision.segment = vision.ready && (state.flags.breakout
      || (state.flags.silhouette && inMoment(songTime)));
    vision.ghosts = state.flags.silhouette && inMoment(songTime);
    stage.zoomTarget = inMoment(songTime) ? 1.3 : 1;      // в особом моменте — ближе к лицу
    if (state.flags.camera && vision.ready) {
      const cam = stage.box(now);
      for (const e of vision.process(state.video, now)) handleEvent(e, now, cam);
      if (state.flags.face && vision.face) stage.lookAt(vision.face.x, vision.face.y, now);
    }
    const live = state.stream && state.video.readyState >= 2;
    stage.draw(now, state.flags.camera && live ? state.video : null,
               state.flags.hands || state.flags.silhouette || state.flags.breakout ? vision : null);
    if (!live) hello();                       // камеры нет — объясняем, что нажать
    drawCount();                              // 3-2-1 перед записью
  } catch (err) {
    if (err.message !== lastError) {          // сбой в отрисовке не должен всё гасить
      lastError = err.message;
      errors.push(`отрисовка: ${err.message}`);
      status(`Сбой отрисовки: ${err.message}`);
      console.error(err);
    }
    try { hello(); } catch { /* тогда уже ничем не помочь */ }
  }
  requestAnimationFrame(loop);
}

/** Заставка, пока камера не включена: чтобы экран не был просто чёрным. */
function hello() {
  const ctx = stage.ctx, W = stage.W, H = stage.H;
  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, '#171a21');
  grad.addColorStop(1, '#0d0e11');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, W, H);
  ctx.textAlign = 'center';
  ctx.fillStyle = '#e6e6e6';
  ctx.font = `700 ${Math.round(H * 0.034)}px Inter, system-ui, sans-serif`;
  ctx.fillText('YeahMusic', W / 2, H * 0.38);
  ctx.fillStyle = '#8a8f98';
  ctx.font = `${Math.round(H * 0.019)}px Inter, system-ui, sans-serif`;
  const lines = state.stream
    ? ['Камера включается…']
    : ['Нажми «📷 Камера» снизу, чтобы увидеть себя',
       '«☰ Меню» — свои песни и эффекты',
       '«⏺ Запись» — отсчёт 3-2-1 и съёмка клипа'];
  lines.forEach((t, i) => ctx.fillText(t, W / 2, H * 0.45 + i * H * 0.035));
  ctx.font = `${Math.round(H * 0.014)}px Inter, system-ui, sans-serif`;
  ctx.fillStyle = '#5a5f68';
  ctx.fillText(VERSION, W / 2, H * 0.62);
  if (errors.length) {                       // ошибки видно прямо на экране
    ctx.fillStyle = '#ff6b6b';
    errors.slice(-3).forEach((t, i) => ctx.fillText(t.slice(0, 90), W / 2, H * 0.66 + i * H * 0.022));
  }
}

// первое касание экрана — можно просить камеру (браузер разрешает только после жеста)
addEventListener('pointerdown', function first() {
  removeEventListener('pointerdown', first);
  if (state.flags.camera && !state.stream) startCamera();
}, { once: false });

/** Подгон под экран телефона: размер холста и поля под чёлку и нижнюю панель. */
function fit() {
  stage.resize();
  const probe = getComputedStyle($('safe'));
  const top = parseFloat(probe.paddingTop) || 0;         // чёлка / строка состояния
  const bottom = parseFloat(probe.paddingBottom) || 0;   // полоса «домой»
  const bars = document.body.classList.contains('recording');
  const safe = stage.phone
    ? { top: top + 46, right: 0, bottom: bottom + (bars ? 96 : 84), left: 0 }
    : { top: top + 16, right: 16, bottom: bottom + 16, left: 16 };
  if (state.flags.zones && stage.phone) {        // место под кнопки и подпись самого TikTok
    safe.right = Math.max(safe.right, stage.W * 0.17);
    safe.left = Math.max(safe.left, stage.W * 0.04);
    safe.bottom = Math.max(safe.bottom, stage.H * 0.19);
    safe.top = Math.max(safe.top, stage.H * 0.11);
  }
  stage.safe = safe;
  const guide = $('zones');
  guide.classList.toggle('on', !!state.flags.zones && stage.phone);
  guide.style.inset = `${safe.top}px ${safe.right}px ${safe.bottom}px ${safe.left}px`;
}

addEventListener('resize', fit);
addEventListener('orientationchange', () => setTimeout(fit, 250));
visualViewport?.addEventListener('resize', fit);
fit();
loop();
window.ready = true;              // сторож в index.html: приложение поднялось

// ---------- отдельный режим записи ----------
// Нажал «Запись» — интерфейс уходит, идёт отсчёт 3-2-1, песня запускается сама,
// на экране только время и круглая кнопка «стоп».

let mix = null;                       // звук песни + микрофон, собираем один раз

function mixer() {
  if (!mix) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const dest = ctx.createMediaStreamDestination();
    const song = ctx.createMediaElementSource(state.audio);
    song.connect(dest);
    song.connect(ctx.destination);    // песню при этом всё так же слышно
    mix = { ctx, dest };
  }
  if (mix.ctx.state === 'suspended') mix.ctx.resume();
  return mix;
}

/** Отсчёт перед съёмкой: рисуется на холсте, его же видно в записи. */
function countdown(from) {
  return new Promise((resolve) => {
    rec.count = { left: from, at: performance.now() / 1000 };
    const tick = () => {
      if (!rec.count) { resolve(); return; }            // отменили
      rec.count.left -= 1;
      rec.count.at = performance.now() / 1000;
      if (rec.count.left <= 0) { rec.count = null; resolve(); }
      else setTimeout(tick, 1000);
    };
    setTimeout(tick, 1000);
  });
}

function drawCount() {
  if (!rec.count) return;
  const ctx = stage.ctx, t = performance.now() / 1000 - rec.count.at;
  const k = Math.max(0, 1 - t);
  const cx = stage.midX, cy = stage.topY + (stage.bottomY - stage.topY) / 2;
  ctx.save();
  ctx.globalAlpha = 0.25 + 0.55 * k;
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.arc(cx, cy, stage.W * 0.16 * (1.4 - 0.4 * k), 0, Math.PI * 2);
  ctx.stroke();
  ctx.globalAlpha = 0.5 + 0.5 * k;
  ctx.textAlign = 'center';
  ctx.font = `900 ${Math.round(stage.H * 0.16)}px Inter, system-ui, sans-serif`;
  stage.outlineText(ctx, String(rec.count.left), cx, cy + stage.H * 0.055, ctx.font, '#fff', '#000', 12);
  ctx.restore();
}

async function startRecording() {
  if (rec.recorder || rec.count) return;
  if (!window.MediaRecorder || !$('canvas').captureStream) {
    status('Этот браузер не умеет записывать видео. На iPhone нужен Safari.');
    return;
  }
  document.body.classList.add('recording');
  $('recBar').hidden = false;
  closeSheet();
  fit();
  await goFullscreen();
  if (state.flags.camera) await startCamera();
  try { rec.wake = await navigator.wakeLock?.request('screen'); } catch { /* не дали — ладно */ }
  stage.shootMode(true);            // рисуем в размер клипа: 1080 по ширине
  fit();
  await countdown(3);
  if (!document.body.classList.contains('recording')) return;   // успели нажать «стоп»
  try {
    const video = $('canvas').captureStream(60).getVideoTracks();
    const audio = [];
    if (state.audio.src) audio.push(...mixer().dest.stream.getAudioTracks());
    if (state.flags.mic) {                       // по умолчанию выключен: он ловит песню
      try {                                      // из динамика и портит чистую дорожку
        const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const { ctx, dest } = mixer();
        ctx.createMediaStreamSource(micStream).connect(dest);
        if (!audio.length) audio.push(...dest.stream.getAudioTracks());
      } catch { /* не дали микрофон — пишем без него */ }
    }
    rec.type = ['video/mp4;codecs=avc1', 'video/webm;codecs=vp9', 'video/webm']
      .find((t) => MediaRecorder.isTypeSupported(t)) || '';
    rec.recorder = new MediaRecorder(new MediaStream([...video, ...audio]),
      { mimeType: rec.type, videoBitsPerSecond: 14_000_000, audioBitsPerSecond: 192_000 });
    rec.chunks = [];
    rec.recorder.ondataavailable = (e) => e.data.size && rec.chunks.push(e.data);
    rec.recorder.onstop = saveVideo;
    rec.recorder.start();
    if (state.audio.src) await start();          // песня с начала, вместе с записью
    const began = Date.now();
    rec.timer = setInterval(() => {
      const sec = Math.floor((Date.now() - began) / 1000);
      $('recTime').textContent = `${String((sec / 60) | 0).padStart(2, '0')}:`
                                 + String(sec % 60).padStart(2, '0');
    }, 500);
    $('recTime').textContent = '00:00';
  } catch (err) {
    status(`Запись не пошла: ${err.message}`);
    exitRecording();
  }
}

function saveVideo() {
  const blob = new Blob(rec.chunks, { type: rec.type });
  const name = `yeahmusic-${Date.now()}.${rec.type.includes('mp4') ? 'mp4' : 'webm'}`;
  const file = new File([blob], name, { type: rec.type });
  if (navigator.canShare?.({ files: [file] })) {      // на телефоне — сразу «Поделиться»
    navigator.share({ files: [file] }).catch(() => saveFile(blob, name));
  } else saveFile(blob, name);
  status(`Клип готов: ${Math.round(blob.size / 104857.6) / 10} МБ.`);
}

function saveFile(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
}

function exitRecording() {
  clearInterval(rec.timer);
  rec.timer = null;
  rec.count = null;
  rec.recorder = null;
  rec.wake?.release?.().catch(() => {});
  rec.wake = null;
  document.body.classList.remove('recording');
  $('recBar').hidden = true;
  stage.shootMode(false);
  fit();
}

function stopRecording() {
  if (rec.recorder && rec.recorder.state !== 'inactive') rec.recorder.stop();
  stopShow();
  exitRecording();
}

$('recBtn').onclick = startRecording;
$('recStop').onclick = stopRecording;

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
  addEventListener('load', async () => {
    try {
      const reg = await navigator.serviceWorker.register('sw.js');
      reg.update();                            // проверяем обновление при каждом запуске
      reg.addEventListener('updatefound', () => {
        reg.installing?.addEventListener('statechange', function () {
          if (this.state === 'installed' && navigator.serviceWorker.controller) {
            status('Есть новая версия — обнови страницу.');
          }
        });
      });
    } catch { /* без офлайна тоже работает */ }
  });
}
