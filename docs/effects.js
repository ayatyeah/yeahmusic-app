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

const ease = (t) => (t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export class Stage {
  constructor(canvas) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });   // без прозрачности рисуется быстрее
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
  }

  resize() {
    const dpr = Math.min(devicePixelRatio || 1, 1.5) * this.quality;
    this.cv.width = Math.round(innerWidth * dpr);
    this.cv.height = Math.round(innerHeight * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.W = innerWidth;
    this.H = innerHeight;
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
    if (!a) return b;
    const t = clamp((now - a.start) / (a.ms / 1000), 0, 1);
    if (t >= 1) { this.anim = null; return b; }
    if (a.kind === 'boom') {
      const hit = t < 0.12 ? t / 0.12 : Math.exp(-(t - 0.12) * 6) * Math.cos((t - 0.12) * 9);
      const k = 1 + BOOM_SCALE * a.dir * hit;
      const dx = BOOM_SWAY * (a.sway || 0) * hit;
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
    const phone = this.H / this.W > 1.4;
    const w = this.W * (phone ? 0.86 : CARD.w), h = this.H * (phone ? 0.16 : CARD.h);
    const cam = this.box(now);
    const hits = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    const face = { x: cam.x + cam.w / 5, y: cam.y + cam.h / 6, w: cam.w * 0.6, h: cam.h * 0.66 };
    const center = { x: this.W * 0.1, y: this.H * 0.55, w: this.W * 0.8, h: this.H * 0.28 };
    const busy = [face, center, ...this.cards.map((c) => ({ x: c.x, y: c.y, w: c.w, h: c.h }))];
    // места для строк: сверху и снизу от кадра, ряд за рядом — так они не налезают
    const slots = [];
    const full = cam.h > this.H * 0.9;
    const bands = full ? [this.H * 0.08, this.H * 0.62] : [this.H * 0.07, cam.y + cam.h + 12];
    for (const top of bands) {
      for (let k = 0; k < 3; k++) {
        const y = top + k * (h + 10);
        if (y + h > this.H - 30) break;
        slots.push({ x: (this.W - w) / 2 + (phone ? 0 : (k % 2 ? 1 : -1) * this.W * 0.12),
                     y, w, h });
      }
    }
    const free = slots.filter((slot) => !busy.some((b) => hits(slot, b)));
    const pos = free.length ? free[Math.floor(Math.random() * free.length)] : null;
    const fallback = slots[0] || { x: 20, y: this.H * 0.1, w, h };
    const card = { text, ...(pos || fallback || { x: 20, y: this.H * 0.3 }), w, h,
                   born: now, charMs: ms, push: [0, 0], pushAt: -9 };
    this.cards.push(card);
    while (this.cards.length > 3) this.cards.shift();
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
        ctx.save();
        ctx.globalAlpha = 0.6 * Math.max(0, 1 - (now - e.born) / e.life);
        ctx.fillStyle = '#fff';
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
    const zoom = this.zoom * this.shapeZoom();
    sw /= zoom; sh /= zoom;
    const sx = (vw - sw) / 2, sy = (vh - sh) / 2;
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

  drawEq(ctx, now) {
    const fade = Math.max(0, 1 - (now - this.eqAt) / 0.6);
    if (!this.eq.length || fade <= 0) return;
    const levels = [...this.eq.slice(1).reverse(), ...this.eq];
    const bar = this.W / levels.length;
    ctx.save();
    ctx.fillStyle = '#fff';
    levels.forEach((v, i) => {
      const h = 8 + 120 * v;
      ctx.globalAlpha = fade * (0.25 + 0.5 * v);
      ctx.fillRect(i * bar + bar * 0.14, this.H - h - 16, bar * 0.72, h);
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
    const maxW = this.W * 0.86;
    const rows = [];
    let row = [], width = 0;
    for (const [w, at] of e.words) {
      const ww = ctx.measureText(w + ' ').width;
      if (row.length && width + ww > maxW) { rows.push([row, width]); row = []; width = 0; }
      row.push([w, at, ww]); width += ww;
    }
    if (row.length) rows.push([row, width]);
    let y = this.H * 0.68 - (rows.length - 1) * size * 0.6;
    for (const [cells, width] of rows) {
      let x = this.W / 2 - width / 2;
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
    ctx.translate(this.W / 2, this.H * 0.42);
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
    const fit = Math.min(1, cam.h * 0.95 / width);
    const half = size * fit * 0.62 + 14;
    const x = (e.left ? cam.x - half : cam.x + cam.w + half) + (e.left ? -160 : 160) * slide;
    ctx.globalAlpha = fade;
    ctx.translate(x, cam.y + cam.h / 2);
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
