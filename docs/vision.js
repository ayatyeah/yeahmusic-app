// Руки, силуэт и жесты в браузере — JS-версия MediaPipe (та же, что в программе на компьютере).
// Руки: неоновый скелет, поза «пистолетик» и выстрел по рывку.
// Силуэт: тени-двойники и светящаяся обводка.
// Жесты: взмахи и ладонь на камеру — по движению в кадре (как в десктопной версии).

const CDN = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14';
const MODELS = 'https://storage.googleapis.com/mediapipe-models';

const FINGERS = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16], [17, 18, 19, 20]];
const PALM_WEB = [[0, 1], [0, 5], [0, 9], [0, 13], [0, 17], [1, 5], [5, 9], [9, 13], [13, 17],
                  [2, 5], [1, 9], [5, 13], [9, 17], [0, 2]];
const TIPS = [4, 8, 12, 16, 20];
const HAND_COLOR = [0, 255, 200], GUN_COLOR = [255, 70, 60];

const EXTENDED = 1.15, FOLDED = 1.1, THUMB_OUT = 0.5;
const ARM_TIME = 0.25, SHOT_KICK = 0.3, SHOT_TILT = 0.3, SHOT_WINDOW = 0.22, SHOT_COOLDOWN = 0.7;

const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const unit = (a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1]; const n = Math.hypot(dx, dy) || 1;
                         return [dx / n, dy / n]; };
const palm = (p) => Math.max(1e-6, dist(p[0], p[9]));
const reach = (p, f) => dist(p[0], p[f[3]]) / Math.max(1e-6, dist(p[0], p[f[1]]));

function isGun(p) {
  if (reach(p, FINGERS[1]) < EXTENDED) return false;
  if (reach(p, FINGERS[3]) > FOLDED || reach(p, FINGERS[4]) > FOLDED) return false;
  if (dist(p[4], p[5]) < THUMB_OUT * palm(p)) return false;
  const [tx, ty] = unit(p[2], p[4]), [ix, iy] = unit(p[5], p[8]);
  return ty < 0.1 && tx * ix + ty * iy < 0.85;
}

function isThumbGun(p) {
  if ([1, 2, 3, 4].some((i) => reach(p, FINGERS[i]) > FOLDED)) return false;
  if (dist(p[4], p[5]) < THUMB_OUT * 1.4 * palm(p)) return false;
  return Math.abs(unit(p[2], p[4])[1]) < 0.6;
}

const barrel = (p) => (isGun(p) ? [p[8], unit(p[5], p[8])]
                    : isThumbGun(p) ? [p[4], unit(p[2], p[4])] : null);

// ---------- выстрел ----------

class GunDetector {
  constructor() { this.history = []; this.poseSince = null; this.poseUntil = 0; this.cool = 0; }

  armed(now) {
    return this.poseSince !== null && now < this.poseUntil
           && now - this.poseSince >= ARM_TIME && now >= this.cool;
  }

  feed(hands, now) {
    let gun = hands.find((p) => barrel(p));
    let tip, aim;
    if (gun) {
      this.poseSince = this.poseSince ?? now;
      this.poseUntil = now + 0.3;
      [tip, aim] = barrel(gun);
    } else if (now < this.poseUntil && this.history.length && hands.length) {
      const last = this.history[this.history.length - 1];
      gun = hands.reduce((a, b) => (dist(b[8], last.tip) < dist(a[8], last.tip) ? b : a));
      tip = gun[8]; aim = last.aim;
    }
    if (!gun) { this.history = []; this.poseSince = null; return null; }
    this.history.push({ now, tip, palm: palm(gun), aim, armed: this.armed(now) });
    this.history = this.history.filter((h) => now - h.now <= SHOT_WINDOW);
    const ready = this.history.filter((h) => h.armed && h.now < now);
    if (!ready.length) return null;
    const start = ready.reduce((a, b) => (b.tip[1] > a.tip[1] ? b : a));
    const rise = (start.tip[1] - tip[1]) / start.palm;
    const tilt = start.aim[1] - aim[1];
    if (rise >= SHOT_KICK || tilt >= SHOT_TILT) {
      this.history = []; this.poseSince = null; this.cool = now + SHOT_COOLDOWN;
      return { muzzle: start.tip, dir: start.aim };
    }
    return null;
  }
}

// ---------- взмахи и ладонь (по движению в кадре) ----------

class MotionGestures {
  constructor() {
    this.prev = null; this.track = []; this.cool = 0;
    this.base = null; this.darkSince = null; this.covered = false;
    this.cv = document.createElement('canvas');
    this.cv.width = 64; this.cv.height = 48;
    this.ctx = this.cv.getContext('2d', { willReadFrequently: true });
  }

  feed(video, now) {
    if (!video.videoWidth) return null;
    this.ctx.drawImage(video, 0, 0, 64, 48);
    const px = this.ctx.getImageData(0, 0, 64, 48).data;
    const gray = new Float32Array(64 * 48);
    let light = 0;
    for (let i = 0; i < gray.length; i++) {
      gray[i] = (px[i * 4] + px[i * 4 + 1] + px[i * 4 + 2]) / 3;
      light += gray[i];
    }
    light /= gray.length;
    const prev = this.prev;
    this.prev = gray;
    const cover = this.checkCover(light, now);
    if (cover || this.covered || this.darkSince !== null || !prev) return cover;
    if (now < this.cool) { this.track = []; return null; }
    let moving = 0, sx = 0, sy = 0;
    for (let i = 0; i < gray.length; i++) {
      if (Math.abs(gray[i] - prev[i]) > 30) { moving++; sx += i % 64; sy += Math.floor(i / 64); }
    }
    const share = moving / gray.length;
    if (share > 0.04 && share < 0.45) {
      this.track.push([now, sx / moving / 63, sy / moving / 47]);
    }
    this.track = this.track.filter((p) => now - p[0] <= 1.4);
    return this.swipe(now) || this.wave(now);
  }

  checkCover(light, now) {
    if (this.base === null) { this.base = light; return null; }
    if (this.covered) {
      if (light > this.base * 0.6) {
        this.covered = false; this.darkSince = null; this.prev = null; this.cool = now + 0.8;
      }
      return null;
    }
    if (light < this.base * 0.35) {
      if (this.darkSince === null) this.darkSince = now;
      else if (now - this.darkSince >= 0.35) {
        this.covered = true; this.track = []; return 'cover';
      }
      return null;
    }
    this.darkSince = null;
    this.base = this.base * 0.95 + light * 0.05;
    return null;
  }

  fire(name, now) { this.track = []; this.cool = now + 1.5; return name; }

  swipe(now) {
    const pts = this.track.filter((p) => now - p[0] <= 0.7);
    if (pts.length < 4) return null;
    const xs = pts.map((p) => p[1]), ys = pts.map((p) => p[2]);
    const dx = xs[xs.length - 1] - xs[0], dy = ys[ys.length - 1] - ys[0];
    const range = (a) => Math.max(...a) - Math.min(...a);
    if (Math.abs(dx) >= Math.abs(dy)) {
      if (Math.abs(dx) >= 0.45 && range(xs) <= Math.abs(dx) * 1.3) {
        return this.fire(dx > 0 ? 'right' : 'left', now);
      }
    } else if (Math.abs(dy) >= 0.25 && range(ys) <= Math.abs(dy) * 1.3) {
      return this.fire(dy > 0 ? 'down' : 'up', now);
    }
    return null;
  }

  wave(now) {
    const xs = this.track.map((p) => p[1]);
    if (!xs.length) return null;
    let turns = 0, dir = 0, extreme = xs[0];
    for (const x of xs.slice(1)) {
      if (dir === 0) { if (Math.abs(x - extreme) >= 0.12) { dir = x > extreme ? 1 : -1; extreme = x; } }
      else if ((x - extreme) * dir > 0) extreme = x;
      else if (Math.abs(x - extreme) >= 0.12) { turns++; dir = -dir; extreme = x; }
    }
    return turns >= 3 ? this.fire('wave', now) : null;
  }
}

// ---------- всё вместе ----------

export class Vision {
  constructor() {
    this.hands = [];
    this.masks = [];
    this.segment = false;
    this.gun = new GunDetector();
    this.motion = new MotionGestures();
    this.ready = false;
    this.lastVideoTime = -1;
  }

  async init({ wantHands = true, wantSegment = true } = {}) {
    const vision = await import(`${CDN}/vision_bundle.mjs`);
    const files = await vision.FilesetResolver.forVisionTasks(`${CDN}/wasm`);
    if (wantHands) {
      this.landmarker = await vision.HandLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath:
          `${MODELS}/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task`,
          delegate: 'GPU' },
        runningMode: 'VIDEO', numHands: 2,
      });
    }
    if (wantSegment) {
      this.segmenter = await vision.ImageSegmenter.createFromOptions(files, {
        baseOptions: { modelAssetPath:
          `${MODELS}/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite`,
          delegate: 'GPU' },
        runningMode: 'VIDEO', outputCategoryMask: true, outputConfidenceMasks: false,
      });
    }
    this.ready = true;
  }

  /** Обработать кадр: руки, силуэт, жесты. Вернёт список событий. */
  process(video, now) {
    const events = [];
    if (!this.ready || !video.videoWidth || video.currentTime === this.lastVideoTime) return events;
    this.lastVideoTime = video.currentTime;
    const ms = now * 1000;
    if (this.landmarker) {
      const res = this.landmarker.detectForVideo(video, ms);
      this.hands = (res.landmarks || []).map((pts) => pts.map((p) => [p.x, p.y]));
      const shot = this.gun.feed(this.hands.map((p) => p.map(([x, y]) => [x * 1000, y * 1000])), now);
      if (shot) events.push({ type: 'shot', muzzle: shot.muzzle.map((v) => v / 1000),
                              dir: shot.dir });
    }
    if (this.segment && this.segmenter) {
      const res = this.segmenter.segmentForVideo(video, ms);
      const mask = res.categoryMask;
      if (mask) {
        this.masks.push({ now, data: mask.getAsUint8Array().slice(),
                          w: mask.width, h: mask.height });
        this.masks = this.masks.filter((m) => now - m.now < 0.8);
        mask.close();
      }
    } else this.masks = [];
    const gesture = this.motion.feed(video, now);
    if (gesture) events.push({ type: 'gesture', name: gesture });
    return events;
  }

  maskAt(when) {
    if (!this.masks.length) return null;
    const best = this.masks.reduce((a, b) => (Math.abs(b.now - when) < Math.abs(a.now - when) ? b : a));
    return Math.abs(best.now - when) <= 0.5 ? best : null;
  }

  /** Силуэты-двойники и неоновые руки поверх кадра камеры. */
  draw(ctx, cam, now, mirror) {
    const toScreen = ([x, y]) => [cam.x + (mirror ? 1 - x : x) * cam.w, cam.y + y * cam.h];
    if (this.segment) {
      [[0.18, 0.33], [0.36, 0.2]].forEach(([delay, alpha]) => {
        const m = this.maskAt(now - delay);
        if (m) this.drawMask(ctx, cam, m, `rgba(120,220,255,${alpha})`, mirror);
      });
      if (now < this.outlineUntil) {
        const m = this.maskAt(now);
        const k = Math.sin(Math.PI * (1 - (this.outlineUntil - now) / 1.2)) ** 0.6;
        if (m) this.drawMask(ctx, cam, m, `rgba(200,245,255,${0.9 * k})`, mirror, true);
      }
    }
    for (const pts of this.hands) {
      const p = pts.map(toScreen);
      const gun = barrel(pts.map(([x, y]) => [x * 1000, y * 1000]));
      const color = gun ? GUN_COLOR : HAND_COLOR;
      ctx.save();
      ctx.lineCap = 'round';
      for (const [a, b] of PALM_WEB) {                  // ладонь — тусклая паутинка
        ctx.strokeStyle = `rgba(${color},.24)`;
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(...p[a]); ctx.lineTo(...p[b]); ctx.stroke();
      }
      for (const [width, alpha] of [[11, 0.18], [7, 0.28], [4, 0.47]]) {   // свечение
        ctx.strokeStyle = `rgba(${color},${alpha})`;
        ctx.lineWidth = width;
        for (const f of FINGERS) for (let i = 0; i < 3; i++) {
          ctx.beginPath(); ctx.moveTo(...p[f[i]]); ctx.lineTo(...p[f[i + 1]]); ctx.stroke();
        }
      }
      ctx.strokeStyle = 'rgba(255,255,255,.85)';        // сердцевина
      ctx.lineWidth = 2;
      for (const f of FINGERS) for (let i = 0; i < 3; i++) {
        ctx.beginPath(); ctx.moveTo(...p[f[i]]); ctx.lineTo(...p[f[i + 1]]); ctx.stroke();
      }
      for (const f of FINGERS) for (const i of f) {
        const tip = TIPS.includes(i);
        ctx.fillStyle = `rgba(${color},.35)`;
        ctx.beginPath(); ctx.arc(...p[i], tip ? 9 : 5, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,.95)';
        ctx.beginPath(); ctx.arc(...p[i], tip ? 3.5 : 2, 0, Math.PI * 2); ctx.fill();
      }
      if (gun && this.gun.armed(now)) {                 // прицел, когда взведён
        const [tip, dir] = gun;
        const [sx, sy] = toScreen([tip[0] / 1000, tip[1] / 1000]);
        const dx = mirror ? -dir[0] : dir[0];
        ctx.strokeStyle = `rgb(${color})`;
        ctx.lineWidth = 2;
        for (let k = 1; k < 8; k += 2) {
          ctx.beginPath();
          ctx.moveTo(sx + dx * k * 14, sy + dir[1] * k * 14);
          ctx.lineTo(sx + dx * (k + 1) * 14, sy + dir[1] * (k + 1) * 14);
          ctx.stroke();
        }
      }
      ctx.restore();
    }
  }

  drawMask(ctx, cam, mask, color, mirror, edge = false) {
    if (!this.maskCanvas) {
      this.maskCanvas = document.createElement('canvas');
      this.maskCtx = this.maskCanvas.getContext('2d');
    }
    const { w, h, data } = mask;
    this.maskCanvas.width = w;
    this.maskCanvas.height = h;
    const img = this.maskCtx.createImageData(w, h);
    const rgba = color.match(/[\d.]+/g).map(Number);
    for (let i = 0; i < w * h; i++) {
      let on = data[i] > 0;
      if (on && edge) {                                 // контур: край силуэта
        const x = i % w, y = (i / w) | 0;
        const inside = (dx, dy) => {
          const nx = x + dx, ny = y + dy;
          return nx >= 0 && ny >= 0 && nx < w && ny < h && data[ny * w + nx] > 0;
        };
        on = !(inside(2, 0) && inside(-2, 0) && inside(0, 2) && inside(0, -2));
      }
      if (on) {
        img.data[i * 4] = rgba[0]; img.data[i * 4 + 1] = rgba[1];
        img.data[i * 4 + 2] = rgba[2]; img.data[i * 4 + 3] = (rgba[3] ?? 1) * 255;
      }
    }
    this.maskCtx.putImageData(img, 0, 0);
    ctx.save();
    ctx.beginPath();
    ctx.rect(cam.x, cam.y, cam.w, cam.h);
    ctx.clip();
    if (mirror) { ctx.translate(cam.x * 2 + cam.w, 0); ctx.scale(-1, 1); }
    ctx.drawImage(this.maskCanvas, cam.x, cam.y, cam.w, cam.h);
    ctx.restore();
  }

  flashOutline(now) { this.outlineUntil = now + 1.2; }
}
