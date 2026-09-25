// Эффекты на canvas: окно камеры (форма, бум, тряска, полёт), строки песни,
// крупные слова, слова по краям, глитч, эквалайзер, тени-двойники, выстрелы.
// Всё рисуется в одном кадре: камера снизу, поверх неё — текст и эффекты.

export const SHAPES = { wide: [16 / 9, 0.38], medium: [3 / 2, 0.42], tall: [4 / 5, 0.5] };

const CARD = { w: 0.44, h: 0.22 };     // размер «плашки» со строкой — доля ширины/высоты экрана
const CHAR_MS = 85, MIN_CHAR_MS = 45;
const RISE = 24;                        // px в секунду: строки медленно всплывают
const FADE = 250, SHAPE_MS = 650, BOOM_MS = 340, SHAKE_MS = 650, JUMP_MS = 700;
const BOOM_SCALE = 0.13, BOOM_SWAY = 16, JUMP_PX = 170;
const GHOST_DELAYS = [0.18, 0.36], GHOST_ALPHA = [0.33, 0.2];
const SHOOT_W = 1080;                   // ширина кадра при записи — как просит TikTok
const FOLLOW = 0.1;                     // плавность слежения за лицом
const FACE_UP = 0.42;                   // лицо держим чуть выше середины кадра
const GRAIN = 96;                       // размер плитки с зерном

/** Плитка с зерном: рисуем один раз и потом только сдвигаем. */
function makeGrain() {
  const cv = document.createElement('canvas');
  cv.width = cv.height = GRAIN;
  const ctx = cv.getContext('2d');
  const img = ctx.createImageData(GRAIN, GRAIN);
  for (let i = 0; i < GRAIN * GRAIN; i++) {
    const v = 90 + Math.random() * 76;
    img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = v;
    img.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return cv;
}

const ease = (t) => (t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export class Stage {
  constructor(canvas) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });   // без прозрачности рисуется быстрее
    this.ctx.imageSmoothingQuality = 'high';
    this.quality = 1;                                       // падает, если не вытягиваем
    this.cards = [];
    this.effects = [];          // вспышки, дырки от пуль, «ПАУ!», крупные слова
    this.shape = 'wide';
    this.shapeFrom = null;
    this.anim = null;           // {kind, dir, start, ms} — бум, тряска, полёт
    this.eq = [];
    this.eqAt = -9;
    this.glitchUntil = 0;
    this.clonesUntil = 0;
    this.frames = [];           // недавние кадры камеры — для «эха» на дропе
    this.ghosts = [];           // недавние положения окна — след при полёте
    this.mirror = true;
    this.plates = false;         // «плашки»: светлый фон под строкой, как в программе
                                 // (не box — так называется метод, считающий окно камеры)
    this.zoom = 1;               // приближение кадра (в особых моментах)
    this.zoomTarget = 1;
    this.flashes = [];           // вспышки у пальца при выстреле
    this.hearts = [];
    this.punch = 1;              // удар зумом в такт (когда кадр во весь экран)
    this.rings = [];             // круги-волны от сильных ударов
    this.sparks = [];            // искры под бит
    // сколько по краям занято телефоном (чёлка, полоса «домой»), нашими кнопками
    // и кнопками самого TikTok (справа лайки, снизу подпись)
    this.safe = { top: 0, right: 0, bottom: 0, left: 0 };
    this.shoot = false;          // идёт запись — рисуем крупнее, чтобы клип был 1080p
    this.film = true;            // плёночный вид: тёплый подтон, виньетка, зерно
    this.follow = true;          // кадр держит лицо
    this.focus = { x: 0.5, y: 0.45 };
    this.faceAt = -9;
    this.grain = null;
  }

  get topY() { return this.safe.top || 0; }

  get bottomY() { return this.H - (this.safe.bottom || 0); }

  get leftX() { return this.safe.left || 0; }

  get rightX() { return this.W - (this.safe.right || 0); }

  /** Середина свободного места: правый край экрана занят кнопками TikTok. */
  get midX() { return (this.leftX + this.rightX) / 2; }

  get safeW() { return this.rightX - this.leftX; }

  /** Куда смотреть камере: доли всего кадра (лицо). */
  lookAt(x, y, now) {
    this.faceAt = now;
    this.focus.x += (x - this.focus.x) * FOLLOW;
    this.focus.y += (y - this.focus.y) * FOLLOW;
  }

  resize() {
    // размер берём у самого холста: innerWidth/innerHeight на телефоне врут из-за полос
    // браузера, и тогда картинка растягивается — лицо становится широким
    const r = this.cv.getBoundingClientRect();
    const w = Math.round(r.width) || innerWidth, h = Math.round(r.height) || innerHeight;
    // в записи плотность фиксируем: кадр 1080 по ширине и не меняется на ходу
    const dpr = this.shoot ? Math.max(SHOOT_W / w, 1)
                           : Math.min(devicePixelRatio || 1, 2) * this.quality;
    this.cv.width = Math.round(w * dpr);
    this.cv.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.imageSmoothingQuality = 'high';
    this.W = w;
    this.H = h;
  }

  /** Режим съёмки: холст рисуется в размер клипа (1080 по ширине). */
  shootMode(on) {
    if (this.shoot === on) return;
    this.shoot = on;
    this.resize();
  }

  // ---------- окно камеры ----------

  setShape(name, now) {
    if (!SHAPES[name] || name === this.shape) return;
    this.shapeFrom = { box: this.box(now), start: now };
    this.shape = name;
  }

  get phone() { return this.H / this.W > 1.4; }

  shapeBox(name) {
    if (this.phone) return { x: 0, y: 0, w: this.W, h: this.H };   // на телефоне — весь экран
    const [aspect, height] = SHAPES[name];
    const h = this.H * height, w = h * aspect;
    return { x: (this.W - w) / 2, y: (this.H - h) / 2, w, h };
  }

  /** На телефоне форма кадра превращается в приближение: широкий — как есть, узкий — ближе. */
  shapeZoom() {
    if (!this.phone) return 1;
    return { wide: 1, medium: 1.15, tall: 1.3 }[this.shape] || 1;
  }

  box(now) {
    let b = this.shapeBox(this.shape);
    if (this.shapeFrom) {                       // плавная смена формы
      const t = clamp((now - this.shapeFrom.start) / (SHAPE_MS / 1000), 0, 1);
      const k = ease(t), a = this.shapeFrom.box;
      b = { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k,
            w: a.w + (b.w - a.w) * k, h: a.h + (b.h - a.h) * k };
      if (t >= 1) this.shapeFrom = null;
    }
    const a = this.anim;
    this.punch = 1;
    if (!a) return b;
    const t = clamp((now - a.start) / (a.ms / 1000), 0, 1);
    if (t >= 1) { this.anim = null; return b; }
    if (a.kind === 'boom') {
      const hit = t < 0.12 ? t / 0.12 : Math.exp(-(t - 0.12) * 6) * Math.cos((t - 0.12) * 9);
      const k = 1 + BOOM_SCALE * a.dir * hit;
      const dx = BOOM_SWAY * (a.sway || 0) * hit;
      if (this.phone) {                       // кадр и так во весь экран — бьём зумом
        this.punch = k;
        return { ...b, x: b.x + dx };
      }
      return { x: b.x - b.w * (k - 1) / 2 + dx, y: b.y - b.h * (k - 1) / 2, w: b.w * k, h: b.h * k };
    }
    if (a.kind === 'shake') {
      const dx = 34 * a.dir * Math.sin(t * Math.PI * 9) * (1 - t);
      const dy = a.dir > 1 ? 22 * a.dir * Math.sin(t * Math.PI * 13 + 1) * (1 - t) : 0;
      return { ...b, x: b.x + dx, y: b.y + dy };
    }
    if (a.kind === 'jump') {
      const off = JUMP_PX * Math.sin(Math.PI * t ** 0.7) * (1 - t * 0.25);
      return { ...b, x: b.x + a.dir[0] * off, y: b.y - a.dir[1] * off };
    }
    return b;
  }

  boom(strength = 1, sway = 0, now = 0) {
    if (this.anim && this.anim.kind !== 'boom') return;
    this.anim = { kind: 'boom', dir: strength, sway, start: now, ms: BOOM_MS };
  }

  shake(power = 1, now = 0) {
    if (this.anim && this.anim.kind === 'boom') this.anim = null;
    this.anim = { kind: 'shake', dir: power, start: now, ms: SHAKE_MS };
  }

  jump(dir, now = 0) {
    this.ghosts = [];
    this.anim = { kind: 'jump', dir, start: now, ms: JUMP_MS };
  }

  // ---------- строки ----------

  addCard(text, duration, speed, now, big = false) {
    const ms = Math.max(Math.min(MIN_CHAR_MS, CHAR_MS / speed),
                        Math.min(CHAR_MS / speed, duration * 850 / Math.max(text.length, 1)));
    if (big) {                                    // крупно по центру, слово за словом
      this.effects = this.effects.filter((e) => e.kind !== 'center');
      const words = text.split(/\s+/);
      this.effects.push({ kind: 'center', born: now, life: duration + 0.6,
        words: words.map((w, i) => [w, duration * 0.7 * i / Math.max(1, words.length)]) });
      return null;
    }
    const phone = this.phone;
    const w = phone ? this.safeW * (0.5 + Math.random() * 0.44) : this.W * CARD.w;
    // высота — по тому, сколько строк реально получится: иначе коробка занимает пол-экрана
    const size = this.H * 0.028;
    const perLine = Math.max(8, Math.floor((w - 30) / (size * 0.52)));
    const rows = Math.max(1, Math.ceil(text.length / perLine));
    const h = phone ? rows * size * 1.2 + 24 : this.H * CARD.h;
    const cam = this.box(now);
    const cover = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x))
                          * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
    // лицо держим свободным: при слежении оно ровно в середине видимого кадра
    const face = { x: cam.x + cam.w * 0.28, y: cam.y + cam.h * 0.22,
                   w: cam.w * 0.44, h: cam.h * 0.34 };
    const busy = [face, ...this.cards.map((c) => ({ x: c.x - 8, y: c.y - 8,
                                                    w: c.w + 16, h: c.h + 16 }))];
    if (this.effects.some((e) => e.kind === 'center')) {      // там сейчас крупная строка
      busy.push({ x: this.leftX, y: this.H * 0.5, w: this.safeW, h: this.H * 0.33 });
    }
    // место — случайное по всей свободной части экрана, лишь бы ни на что не налезло
    const top = this.topY + 6, floor = this.bottomY - 6;
    const spanX = Math.max(0, this.safeW - w), spanY = Math.max(0, floor - top - h);
    let pos = null, best = null, least = Infinity;
    for (let i = 0; i < 40; i++) {
      const cand = { x: this.leftX + Math.random() * spanX, y: top + Math.random() * spanY, w, h };
      const score = busy.reduce((sum, b) => sum + cover(cand, b), 0);
      if (!score) { pos = cand; break; }
      if (score < least) { least = score; best = cand; }
    }
    if (!pos) this.cards.shift();                     // всё занято — убираем старую строку
    const card = { text, ...(pos || best), born: now, charMs: ms, push: [0, 0], pushAt: -9 };
    this.cards.push(card);
    while (this.cards.length > (phone ? 2 : 3)) this.cards.shift();
    return card;
  }

  word(text, depth, now) { this.effects.push({ kind: 'word', text, depth, born: now, life: 1.3 }); }

  sideWord(text, left, now) {
    this.effects = this.effects.filter((e) => !(e.kind === 'side' && e.left === left));
    this.effects.push({ kind: 'side', text, left, born: now, life: 1.7 });
  }

  flash(now) { this.effects.push({ kind: 'flash', born: now, life: 0.32 }); }

  glitch(ms, now) { this.glitchUntil = now + ms / 1000; }

  clones(now, seconds = 1.5) { this.clonesUntil = now + seconds; }

  push(power, now) {
    for (const c of this.cards) {
      const dx = c.x + c.w / 2 - this.W / 2, dy = c.y + c.h / 2 - this.H / 2;
      const n = Math.hypot(dx, dy) || 1;
      c.push = [dx / n * 26 * power, dy / n * 26 * power];
      c.pushAt = now;
    }
  }

  shot(x, y, dir, now) {                          // выстрел: «ПАУ!», дырка, разбитая строка
    this.effects.push({ kind: 'pow', x: x + dir[0] * 70, y: y + dir[1] * 70, born: now, life: 0.75,
                        word: ['ПАУ!', 'БАХ!', 'ПИУ!'][Math.floor(Math.random() * 3)],
                        tilt: (Math.random() - 0.5) * 24 });
    const d = 320 + Math.random() * 380;
    const hx = clamp(x + dir[0] * d, 60, this.W - 60), hy = clamp(y + dir[1] * d, 60, this.H - 60);
    this.effects.push({ kind: 'hole', x: hx, y: hy, born: now, life: 5,
                        rays: Array.from({ length: 12 }, (_, i) => {
                          const a = (i / 12) * Math.PI * 2 + Math.random() * 0.3;
                          const len = 55 + Math.random() * 65;
                          return [Math.cos(a) * len, Math.sin(a) * len];
                        }) });
    const hit = this.cards.find((c) => hx > c.x - 90 && hx < c.x + c.w + 90 &&
                                        hy > c.y - 60 && hy < c.y + c.h + 60);
    if (hit) {
      this.cards = this.cards.filter((c) => c !== hit);
      const chars = [...hit.text.slice(0, hit.shown || hit.text.length)];
      this.effects.push({ kind: 'shatter', born: now, life: 1.4, x: hx, y: hy,
        letters: chars.map((ch, i) => {
          const cx = hit.x + hit.w / 2 + (i - chars.length / 2) * 14;
          const cy = hit.y + hit.h / 2;
          const a = Math.atan2(cy - hy, cx - hx), sp = 250 + Math.random() * 450;
          return { ch, x: cx, y: cy, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp - 250,
                   spin: (Math.random() - 0.5) * 900 };
        }) });
    }
  }

  setEq(levels, now) { this.eq = levels; this.eqAt = now; }

  // ---------- рисование ----------

  draw(now, video, vision) {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.W, this.H);
    const cam = this.box(now);
    if (video && video.readyState >= 2) this.drawCamera(ctx, cam, video, now, vision);
    this.drawBeatFx(ctx, now);
    this.drawEq(ctx, now);
    for (const e of this.effects) if (e.kind === 'hole') this.drawHole(ctx, e, now);
    this.drawCards(ctx, now);
    for (const e of this.effects) {
      if (e.kind === 'shatter') this.drawShatter(ctx, e, now);
      else if (e.kind === 'center') this.drawCenter(ctx, e, now);
      else if (e.kind === 'word') this.drawWord(ctx, e, now);
      else if (e.kind === 'side') this.drawSide(ctx, e, now, cam);
      else if (e.kind === 'pow') this.drawPow(ctx, e, now);
      else if (e.kind === 'flash') {
        const k = Math.max(0, 1 - (now - e.born) / e.life);
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        const grad = ctx.createRadialGradient(this.W / 2, this.H / 2, 0,
                                              this.W / 2, this.H / 2, Math.max(this.W, this.H) * 0.7);
        grad.addColorStop(0, `rgba(255,255,255,${0.75 * k})`);
        grad.addColorStop(0.5, `rgba(120,140,255,${0.45 * k})`);
        grad.addColorStop(1, `rgba(255,60,120,${0.2 * k})`);
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, this.W, this.H);
        ctx.restore();
      }
    }
    this.effects = this.effects.filter((e) => now - e.born < e.life);
  }

  drawCamera(ctx, cam, video, now, vision) {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw) return;
    const aspect = cam.w / cam.h;                  // обрезаем кадр под форму окна
    let sw = vw, sh = vh;
    if (vw / vh > aspect) sw = vh * aspect; else sh = vw / aspect;
    this.zoom += (this.zoomTarget - this.zoom) * 0.06;      // плавное приближение к лицу
    const zoom = this.zoom * this.shapeZoom() * this.punch;
    sw /= zoom; sh /= zoom;
    if (this.follow && now - this.faceAt > 2) this.lookAt(0.5, 0.45, this.faceAt);  // лица нет — к центру
    const fx = this.follow ? this.focus.x : 0.5;
    const fy = this.follow ? this.focus.y : 0.5;
    const sx = clamp(fx * vw - sw / 2, 0, vw - sw);
    const sy = clamp(fy * vh - sh * FACE_UP, 0, vh - sh);
    // какую часть кадра видно — руки и силуэт рисуем по этим же долям, иначе будет сдвиг
    this.view = { sx: sx / vw, sy: sy / vh, sw: sw / vw, sh: sh / vh };
    const paint = (box, alpha = 1) => {
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.rect(box.x, box.y, box.w, box.h);
      ctx.clip();
      if (this.mirror) { ctx.translate(box.x * 2 + box.w, 0); ctx.scale(-1, 1); }
      ctx.drawImage(video, sx, sy, sw, sh, box.x, box.y, box.w, box.h);
      ctx.restore();
    };
    if (now < this.clonesUntil) {                  // «эхо» кадра на дропе
      this.frames.push({ now, box: { ...cam } });
      this.frames = this.frames.filter((f) => now - f.now < 0.7);
      [0.12, 0.24, 0.36, 0.48].forEach((delay, k) => {
        const s = 0.34, w = cam.w * s, h = cam.h * s;
        const side = k % 2 ? 1 : -1, row = Math.floor(k / 2) - 0.5;
        paint({ x: cam.x + cam.w / 2 + side * (cam.w / 2 + w * 0.62) - w / 2,
                y: cam.y + cam.h / 2 + row * h * 1.15 - h / 2, w, h }, 0.85 - 0.12 * k);
      });
    }
    for (const g of this.ghosts) paint(g.box, g.alpha);
    if (now < this.glitchUntil) {                  // двоение красный/голубой
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.filter = 'url(#none)';
      paint({ ...cam, x: cam.x - 9 }, 0.5);
      paint({ ...cam, x: cam.x + 9 }, 0.5);
      ctx.restore();
    }
    paint(cam);
    if (this.film) this.drawFilm(ctx, cam, now);
    if (vision) vision.draw(ctx, cam, now, this.mirror, this.view);
    this.drawSparks(ctx, now);
    ctx.strokeStyle = 'rgba(189,189,189,.85)';
    ctx.lineWidth = 1;
    ctx.strokeRect(cam.x + 0.5, cam.y + 0.5, cam.w - 1, cam.h - 1);
    if (this.anim && this.anim.kind === 'jump') {  // след за окном при полёте
      this.ghosts = [{ box: { ...cam }, alpha: 0.35 }, ...this.ghosts].slice(0, 3)
        .map((g, i) => ({ ...g, alpha: 0.32 - i * 0.1 }));
    } else this.ghosts = [];
  }

  /** Вспышка у кончика пальца в момент выстрела и летящие сердечки. */
  drawSparks(ctx, now) {
    this.flashes = this.flashes.filter((f) => now - f.born < 0.2);
    for (const f of this.flashes) {
      const k = 1 - (now - f.born) / 0.2;
      ctx.save();
      ctx.translate(f.x, f.y);
      ctx.fillStyle = `rgba(255,200,60,${k})`;
      ctx.beginPath();
      for (let i = 0; i < 24; i++) {
        const a = Math.PI * i / 12;
        const r = (i % 2 ? 22 * k + 4 : 60 * k + 10);
        ctx[i ? 'lineTo' : 'moveTo'](Math.cos(a) * r, Math.sin(a) * r);
      }
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = `rgba(255,255,230,${k})`;
      ctx.beginPath();
      ctx.arc(0, 0, 18 * k + 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    const dt = 1 / 60;
    this.hearts = this.hearts.filter((h) => (h.age += dt) < h.life);
    for (const h of this.hearts) {
      h.x += h.vx * dt; h.y += h.vy * dt; h.vx += (Math.random() - 0.5) * 60 * dt;
      const k = Math.min(1, h.age * 8) * (1 - Math.max(0, h.age - h.life * 0.6) / (h.life * 0.4));
      const r = h.size * (0.6 + 0.4 * Math.min(1, h.age * 5));
      ctx.save();
      ctx.globalAlpha = Math.max(0, k);
      ctx.fillStyle = h.color;
      ctx.beginPath();
      for (let i = 0; i <= 32; i++) {                       // контур сердечка
        const t = Math.PI * 2 * i / 32;
        const x = 16 * Math.sin(t) ** 3;
        const y = -(13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t));
        ctx[i ? 'lineTo' : 'moveTo'](h.x + x * r / 16, h.y + y * r / 16);
      }
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
  }

  spark(x, y, now) { this.flashes.push({ x, y, born: now }); }

  heart(x, y) {
    const colors = ['#ff3c6e', '#ff6ea0', '#f01e46'];
    this.hearts.push({ x: x + (Math.random() - 0.5) * 20, y: y + (Math.random() - 0.5) * 20,
      vx: (Math.random() - 0.5) * 240, vy: -120 - Math.random() * 140,
      size: 12 + Math.random() * 14, age: 0, life: 1.2 + Math.random() * 0.8,
      color: colors[Math.floor(Math.random() * 3)] });
    if (this.hearts.length > 60) this.hearts.shift();
  }

  /** Плёночный вид: тёплый подтон, виньетка и зерно — кадр перестаёт быть «видеозвонком». */
  drawFilm(ctx, cam, now) {
    ctx.save();
    ctx.globalCompositeOperation = 'soft-light';          // тёплый свет, тени холоднее
    const warm = ctx.createLinearGradient(cam.x, cam.y, cam.x, cam.y + cam.h);
    warm.addColorStop(0, 'rgba(255,190,120,.30)');
    warm.addColorStop(1, 'rgba(60,110,200,.22)');
    ctx.fillStyle = warm;
    ctx.fillRect(cam.x, cam.y, cam.w, cam.h);
    ctx.globalCompositeOperation = 'source-over';
    const r = Math.hypot(cam.w, cam.h) / 2;               // виньетка
    const vig = ctx.createRadialGradient(cam.x + cam.w / 2, cam.y + cam.h / 2, r * 0.45,
                                         cam.x + cam.w / 2, cam.y + cam.h / 2, r);
    vig.addColorStop(0, 'rgba(0,0,0,0)');
    vig.addColorStop(1, 'rgba(0,0,0,.42)');
    ctx.fillStyle = vig;
    ctx.fillRect(cam.x, cam.y, cam.w, cam.h);
    if (!this.grain) this.grain = ctx.createPattern(makeGrain(), 'repeat');   // зерно
    if (this.grain) {                                    // плитку сдвигаем каждый кадр
      ctx.globalAlpha = 0.06;
      ctx.globalCompositeOperation = 'overlay';
      ctx.translate(-Math.random() * GRAIN, -Math.random() * GRAIN);
      ctx.fillStyle = this.grain;
      ctx.fillRect(cam.x, cam.y, cam.w + GRAIN, cam.h + GRAIN);
    }
    ctx.restore();
    void now;
  }

  ring(now, power = 1) { this.rings.push({ born: now, power }); }

  spawnSparks(now, power = 1) {
    for (let i = 0; i < 6 * power; i++) {
      this.sparks.push({ x: Math.random() * this.W, y: this.H + 10,
        vx: (Math.random() - 0.5) * 120, vy: -180 - Math.random() * 320,
        r: 2 + Math.random() * 3, born: now, life: 0.8 + Math.random() * 0.7 });
    }
    if (this.sparks.length > 120) this.sparks.splice(0, this.sparks.length - 120);
  }

  /** Волны от ударов и искры — поверх картинки, но под текстом. */
  drawBeatFx(ctx, now) {
    this.rings = this.rings.filter((r) => now - r.born < 0.8);
    for (const r of this.rings) {
      const t = (now - r.born) / 0.8;
      ctx.save();
      ctx.globalAlpha = (1 - t) * 0.5 * r.power;
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 6 * (1 - t) + 1;
      ctx.beginPath();
      ctx.arc(this.W / 2, this.H / 2, t * Math.max(this.W, this.H) * 0.7, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
    const dt = 1 / 60;
    this.sparks = this.sparks.filter((s) => (now - s.born) < s.life);
    ctx.save();
    for (const s of this.sparks) {
      s.x += s.vx * dt; s.y += s.vy * dt; s.vy += 140 * dt;
      ctx.globalAlpha = 1 - (now - s.born) / s.life;
      ctx.fillStyle = '#ffe9a8';
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  drawEq(ctx, now) {
    const fade = Math.max(0, 1 - (now - this.eqAt) / 0.6);
    if (!this.eq.length || fade <= 0) return;
    const levels = [...this.eq.slice(1).reverse(), ...this.eq];
    const bar = this.safeW / levels.length;
    ctx.save();
    ctx.fillStyle = '#fff';
    levels.forEach((v, i) => {
      const h = 8 + 120 * v;
      ctx.globalAlpha = fade * (0.25 + 0.5 * v);
      ctx.fillRect(this.leftX + i * bar + bar * 0.14, this.bottomY - h - 8, bar * 0.72, h);
    });
    ctx.restore();
  }

  outlineText(ctx, text, x, y, font, fill = '#fff', outline = 'rgba(0,0,0,.92)', width = 8) {
    ctx.font = font;
    ctx.lineJoin = 'round';
    ctx.lineWidth = width;
    ctx.strokeStyle = outline;
    ctx.strokeText(text, x, y);
    ctx.fillStyle = fill;
    ctx.fillText(text, x, y);
  }

  wrap(ctx, text, maxWidth) {
    const words = text.split(' ');
    const lines = [];
    let line = '';
    for (const w of words) {
      const test = line ? `${line} ${w}` : w;
      if (line && ctx.measureText(test).width > maxWidth) { lines.push(line); line = w; }
      else line = test;
    }
    if (line) lines.push(line);
    return lines;
  }

  drawCards(ctx, now) {
    const size = Math.round(this.H * 0.028);
    const big = this.effects.some((e) => e.kind === 'center');
    ctx.save();
    if (big) ctx.globalAlpha = 0.35;                     // крупная строка важнее
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    for (const card of this.cards) {
      const age = now - card.born;
      card.y -= RISE * (1 / 60);
      const shown = Math.min(card.text.length, Math.floor(age * 1000 / card.charMs));
      card.shown = shown;
      const alpha = Math.min(1, age / (FADE / 1000));
      const kick = now - card.pushAt < 0.7
        ? (() => { const t = (now - card.pushAt) / 0.7, k = Math.exp(-5 * t) * Math.cos(9 * t);
                   return [card.push[0] * k, card.push[1] * k]; })()
        : [0, 0];
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.translate(kick[0], kick[1]);
      ctx.font = `700 ${size}px Inter, system-ui, sans-serif`;
      const lines = this.wrap(ctx, card.text, card.w - 30);
      if (this.plates) {                    // светлая плашка под текстом
        const h = lines.length * size * 1.2 + 26, w = card.w;
        const x = card.x, y = card.y + card.h / 2 - h / 2;
        ctx.fillStyle = '#ececec';
        ctx.strokeStyle = '#bdbdbd';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(x, y, w, h, 8);
        ctx.fill();
        ctx.stroke();
      }
      const lineH = size * 1.2;
      let left = shown;
      let y = card.y + card.h / 2 - (lines.length - 1) * lineH / 2;
      const glitching = age < 0.14 || now < this.glitchUntil;
      for (const line of lines) {
        const part = line.slice(0, Math.max(0, left));
        const cursor = left < line.length ? '|' : (left - line.length <= 1 ? '|' : '');
        left -= line.length + 1;
        const x = card.x + card.w / 2;
        if (glitching) {                      // строку «ломает» глитчем
          ctx.save();
          ctx.globalAlpha = alpha * 0.8;
          ctx.fillStyle = 'rgba(255,30,70,.85)';
          ctx.fillText(part, x - 5, y);
          ctx.fillStyle = 'rgba(30,230,255,.85)';
          ctx.fillText(part, x + 5, y);
          ctx.restore();
        }
        if (this.plates) { ctx.fillStyle = '#141414'; ctx.fillText(part + cursor, x, y); }
        else this.outlineText(ctx, part + cursor, x, y, ctx.font, '#fff', 'rgba(0,0,0,.92)', 6);
        y += lineH;
        if (left < 0) break;
      }
      ctx.restore();
    }
    ctx.restore();
    this.cards = this.cards.filter((c) => c.y + c.h > -20);
  }

  drawCenter(ctx, e, now) {
    const age = (now - e.born) * 1000;
    const size = Math.round(this.H * 0.062);
    ctx.save();
    ctx.textAlign = 'center';
    ctx.font = `900 ${size}px Inter, system-ui, sans-serif`;
    const fade = clamp((e.life * 1000 - age) / 250, 0, 1);
    const maxW = this.safeW * 0.96;
    const rows = [];
    let row = [], width = 0;
    for (const [w, at] of e.words) {
      const ww = ctx.measureText(w + ' ').width;
      if (row.length && width + ww > maxW) { rows.push([row, width]); row = []; width = 0; }
      row.push([w, at, ww]); width += ww;
    }
    if (row.length) rows.push([row, width]);
    let y = Math.min(this.H * 0.68, this.bottomY - size * (rows.length - 0.2))
            - (rows.length - 1) * size * 0.6;
    for (const [cells, width] of rows) {
      let x = this.midX - width / 2;
      for (const [w, at, ww] of cells) {
        const t = age - at * 1000;
        if (t >= 0) {
          const pop = clamp(t / 110, 0, 1);
          const scale = 0.6 + 0.55 * pop - 0.15 * clamp((t - 110) / 90, 0, 1);
          ctx.save();
          ctx.globalAlpha = fade * clamp(t / 80, 0, 1);
          ctx.translate(x + ww / 2, y);
          ctx.scale(scale, scale);
          this.outlineText(ctx, w, 0, 0, ctx.font, '#fff', '#000', 9);
          ctx.restore();
        }
        x += ww;
      }
      y += size * 1.15;
    }
    ctx.restore();
  }

  drawWord(ctx, e, now) {
    const t = (now - e.born) / e.life;
    const size = Math.round(this.H * 0.11);
    ctx.save();
    ctx.textAlign = 'center';
    ctx.font = `900 ${size}px Inter, system-ui, sans-serif`;
    const scale = e.depth
      ? 0.05 + 1.35 * clamp(t / 0.17, 0, 1) ** 0.45 - 0.2 * clamp((t - 0.17) / 0.1, 0, 1)
      : 0.3 + 0.95 * clamp(t / 0.12, 0, 1) - 0.15 * clamp((t - 0.12) / 0.1, 0, 1);
    ctx.globalAlpha = Math.max(0, 1 - Math.max(0, t - 0.65) / 0.35);
    ctx.translate(this.midX, this.topY + (this.bottomY - this.topY) * 0.42);
    ctx.scale(scale, scale);
    if (t < 0.15) {
      ctx.fillStyle = 'rgba(255,40,70,.8)';
      ctx.fillText(e.text, -8, 0);
      ctx.fillStyle = 'rgba(40,220,255,.8)';
      ctx.fillText(e.text, 8, 0);
    }
    this.outlineText(ctx, e.text, 0, 0, ctx.font, '#fff', '#000', 12);
    ctx.restore();
  }

  drawSide(ctx, e, now, cam) {
    const age = (now - e.born) * 1000;
    const slide = Math.max(0, 1 - age / 220) ** 3;
    const fade = clamp((e.life * 1000 - age) / 350, 0, 1);
    const size = Math.round(this.H * 0.095);
    ctx.save();
    ctx.font = `900 ${size}px Inter, system-ui, sans-serif`;
    ctx.textAlign = 'center';
    const width = ctx.measureText(e.text).width;
    const fit = Math.min(1, (Math.min(cam.h, this.bottomY - this.topY) * 0.9) / width);
    const half = size * fit * 0.62 + 14;
    const x = clamp(e.left ? cam.x - half : cam.x + cam.w + half,
                    this.leftX + half, this.rightX - half)
              + (e.left ? -160 : 160) * slide;
    ctx.globalAlpha = fade;
    ctx.translate(x, clamp(cam.y + cam.h / 2, this.topY + 20, this.bottomY - 20));
    ctx.rotate(e.left ? -Math.PI / 2 : Math.PI / 2);
    ctx.scale(fit, fit);
    this.outlineText(ctx, e.text, 0, size * 0.34, ctx.font, '#fff', '#000', 10);
    ctx.restore();
  }

  drawPow(ctx, e, now) {
    const t = (now - e.born) / e.life;
    const size = Math.round(this.H * 0.06);
    ctx.save();
    ctx.translate(e.x, e.y);
    ctx.rotate(e.tilt * Math.PI / 180);
    const scale = 0.5 + 0.8 * clamp(t * 6, 0, 1) - 0.15 * Math.max(0, t - 0.2);
    ctx.scale(scale, scale);
    ctx.globalAlpha = Math.max(0, 1 - Math.max(0, t - 0.6) / 0.4);
    ctx.beginPath();                                   // звезда-вспышка
    for (let k = 0; k < 22; k++) {
      const a = Math.PI * k / 11;
      const r = (k % 2 ? 55 : 95) * (0.85 + 0.3 * Math.sin(k));
      ctx[k ? 'lineTo' : 'moveTo'](Math.cos(a) * r, Math.sin(a) * r * 0.75);
    }
    ctx.closePath();
    ctx.fillStyle = '#ffcc1f';
    ctx.fill();
    ctx.lineWidth = 5;
    ctx.strokeStyle = '#111';
    ctx.stroke();
    ctx.textAlign = 'center';
    ctx.font = `900 ${size}px Inter, system-ui, sans-serif`;
    this.outlineText(ctx, e.word, 0, size * 0.34, ctx.font, '#ff3b2f', '#111', 7);
    ctx.restore();
  }

  drawHole(ctx, e, now) {
    const t = (now - e.born) / e.life;
    ctx.save();
    ctx.globalAlpha = clamp((e.life - (now - e.born)) / 1, 0, 1);
    ctx.translate(e.x, e.y);
    if ((now - e.born) < 0.42) {                        // ударная волна
      const k = (now - e.born) / 0.42;
      ctx.save();
      ctx.globalAlpha = 0.9 * (1 - k);
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 3 * (1 - k) + 1;
      ctx.beginPath();
      ctx.arc(0, 0, 12 + 150 * (1 - (1 - k) ** 3), 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
    ctx.strokeStyle = 'rgba(255,255,255,.9)';
    ctx.lineWidth = 1.4;
    for (const [rx, ry] of e.rays) {
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(rx, ry);
      ctx.stroke();
    }
    ctx.fillStyle = '#000';
    ctx.beginPath();
    ctx.arc(0, 0, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    void t;
  }

  drawShatter(ctx, e, now) {
    const t = now - e.born;
    const size = Math.round(this.H * 0.028);
    ctx.save();
    ctx.globalAlpha = clamp((e.life - t) / 0.5, 0, 1);
    ctx.font = `700 ${size}px Inter, system-ui, sans-serif`;
    ctx.textAlign = 'center';
    for (const l of e.letters) {
      ctx.save();
      ctx.translate(l.x + l.vx * t, l.y + l.vy * t + 900 * t * t / 2);
      ctx.rotate(l.spin * t * Math.PI / 180);
      this.outlineText(ctx, l.ch, 0, 0, ctx.font, '#fff', '#000', 5);
      ctx.restore();
    }
    ctx.restore();
  }
}

export { GHOST_DELAYS, GHOST_ALPHA };
