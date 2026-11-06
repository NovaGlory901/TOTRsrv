// FalseHorizon's testable logic: presets and their checks, what the music sounds like to the
// visuals, the per-frame maths that turns a preset into a warp mesh and shapes, the flash guard,
// picking the next preset, and per-person settings. No page access or WebGL, so it runs under Node
// as well as in the browser. In the browser it is window.FalseHorizonCore.
//
// A frame: the previous picture is drawn again through a warp mesh (zoom, spin, drift, ripple) and
// faded, which makes trails and tunnels; the preset's shapes and waveforms go on top; the result is
// colour-processed. Formulas steer it once at the start (init), per frame (frame) and per mesh
// point (pixel); x and y run 0..1, (0,0) at the bottom left.
(function (root) {
  const Expr =
    typeof require !== 'undefined' ? require('./falsehorizon-expr.js') : root.FalseHorizonExpr;
  const RawPresets =
    typeof require !== 'undefined'
      ? require('./falsehorizon-presets.js')
      : root.FalseHorizonPresets;

  const MESH_W = 32,
    MESH_H = 24;
  const MAX_FILE = 65536,
    MAX_NAME = 40,
    MAX_USER_PRESETS = 30,
    MAX_FAVORITES = 30,
    MAX_FORMULA_TOTAL = 30000,
    MAX_WAVES = 2,
    MAX_SHAPES = 4,
    MAX_INSTANCES = 16;
  const CYCLE_CHOICES = [10, 15, 30, 60, 120];
  const MODES = ['horizon', 'classic'];
  // light drawn on top is added every frame and the feedback keeps it, so it is drawn at 40%
  // strength or everything washes to white
  const ADD_GAIN = 0.4;
  const strength = additive => (additive ? ADD_GAIN : 1);

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const finite = (v, fallback) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);
  const lerp = (a, b, t) => a + (b - a) * t;

  // Preset values: [lowest, highest, starting value]. Formulas can change these every frame, never
  // outside the range.
  const RANGE = {
    zoom: [0.8, 1.3, 1],
    rot: [-0.5, 0.5, 0],
    cx: [-0.5, 1.5, 0.5],
    cy: [-0.5, 1.5, 0.5],
    dx: [-0.1, 0.1, 0],
    dy: [-0.1, 0.1, 0],
    sx: [0.5, 2, 1],
    sy: [0.5, 2, 1],
    warp: [0, 4, 0],
    decay: [0.5, 0.99, 0.97],
    wave_r: [0, 1, 1],
    wave_g: [0, 1, 1],
    wave_b: [0, 1, 1],
    wave_a: [0, 1, 0.8],
    wave_scale: [0, 4, 1],
    wave_x: [0, 1, 0.5],
    wave_y: [0, 1, 0.5],
    gamma: [0.5, 2, 1],
    hue: [-1000, 1000, 0],
    bright: [0, 2, 1],
    solarize: [0, 1, 0],
    echo_alpha: [0, 0.5, 0],
    echo_zoom: [0.5, 3, 1],
    echo_orient: [0, 3, 0],
    cover: [0, 1, 0],
  };
  const RANGE_KEYS = Object.keys(RANGE);
  const WARP_NAMES = ['zoom', 'rot', 'cx', 'cy', 'dx', 'dy', 'sx', 'sy', 'warp'];
  const INPUTS = [
    'time',
    'frame',
    'bass',
    'mid',
    'treb',
    'bass_att',
    'mid_att',
    'treb_att',
    'level',
    'beat',
    'aspect',
    'fps',
    'progress',
    'meshx',
    'meshy',
    'pixelsx',
    'pixelsy',
    'aspectx',
    'aspecty',
  ];
  const INPUT_DEFAULTS = {
    fps: 60,
    progress: 0,
    meshx: 32,
    meshy: 24,
    pixelsx: 640,
    pixelsy: 360,
    aspectx: 1,
    aspecty: 1,
  };
  // the ones an imported preset (written for louder numbers) gets scaled up
  const HEARD = ['bass', 'mid', 'treb', 'bass_att', 'mid_att', 'treb_att'];
  const Q = Array.from({ length: 32 }, (_, i) => 'q' + (i + 1));
  const PIXEL_KEEP = new Set([...INPUTS, ...Q, ...WARP_NAMES, 'x', 'y', 'rad', 'ang']);

  const text = (v, max) =>
    typeof v === 'string'
      ? v
          .replace(/[\u0000-\u001f\u007f]/g, ' ')
          .trim()
          .slice(0, max)
      : '';
  const num = (v, lo, hi, fallback) => clamp(finite(v, fallback), lo, hi);
  const flag = (v, fallback) => (typeof v === 'boolean' ? v : fallback);

  function sanitizePreset(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw))
      return { ok: false, error: 'That is not a visual preset.' };
    const name = text(raw.name, MAX_NAME);
    if (!name) return { ok: false, error: 'The preset needs a name.' };
    let formulaChars = 0,
      problem = '';
    const formula = (value, where) => {
      const src = typeof value === 'string' ? value : '';
      formulaChars += src.length;
      const r = Expr.check(src);
      if (!r.ok && !problem) problem = `The ${where} formula has a problem: ${r.error}.`;
      return src;
    };
    const p = {
      name,
      author: text(raw.author, MAX_NAME),
      dialect: raw.dialect === 'milk' ? 'milk' : 'fh',
      hear: num(raw.hear, 1, 3, 1),
    };
    for (const k of ['zoom', 'rot', 'cx', 'cy', 'dx', 'dy', 'sx', 'sy', 'warp', 'decay'])
      p[k] = num(raw[k], RANGE[k][0], RANGE[k][1], RANGE[k][2]);
    const w = raw.wave && typeof raw.wave === 'object' ? raw.wave : {};
    p.wave = {
      mode: Math.round(num(w.mode, 0, 4, 0)),
      r: num(w.r, 0, 1, 1),
      g: num(w.g, 0, 1, 1),
      b: num(w.b, 0, 1, 1),
      a: num(w.a, 0, 1, RANGE.wave_a[2]),
      scale: num(w.scale, 0, 4, 1),
      x: num(w.x, 0, 1, 0.5),
      y: num(w.y, 0, 1, 0.5),
      additive: flag(w.additive, true),
      thick: Math.round(num(w.thick, 1, 3, 1)),
    };
    const c = raw.color && typeof raw.color === 'object' ? raw.color : {};
    p.color = {
      gamma: num(c.gamma, 0.5, 2, 1),
      hue: num(c.hue, -1000, 1000, 0),
      bright: num(c.bright, 0, 2, 1),
      solarize: num(c.solarize, 0, 1, 0),
      echoAlpha: num(c.echoAlpha, 0, 0.5, 0),
      echoZoom: num(c.echoZoom, 0.5, 3, 1),
      echoOrient: Math.round(num(c.echoOrient, 0, 3, 0)),
      cover: num(c.cover, 0, 1, 0),
    };
    p.init = formula(raw.init, 'init');
    p.frame = formula(raw.frame, 'frame');
    p.pixel = formula(raw.pixel, 'pixel');
    p.waves = (Array.isArray(raw.waves) ? raw.waves : []).slice(0, MAX_WAVES).map((s, i) => {
      s = s && typeof s === 'object' ? s : {};
      return {
        samples: Math.round(num(s.samples, 16, 256, 128)),
        r: num(s.r, 0, 1, 1),
        g: num(s.g, 0, 1, 1),
        b: num(s.b, 0, 1, 1),
        a: num(s.a, 0, 1, 0.8),
        additive: flag(s.additive, true),
        dots: flag(s.dots, false),
        thick: Math.round(num(s.thick, 1, 3, 1)),
        spectrum: flag(s.spectrum, false),
        init: formula(s.init, `wave ${i + 1} init`),
        frame: formula(s.frame, `wave ${i + 1} frame`),
        point: formula(s.point, `wave ${i + 1} point`),
      };
    });
    p.shapes = (Array.isArray(raw.shapes) ? raw.shapes : []).slice(0, MAX_SHAPES).map((s, i) => {
      s = s && typeof s === 'object' ? s : {};
      return {
        sides: Math.round(num(s.sides, 3, 100, 4)),
        x: num(s.x, -0.5, 1.5, 0.5),
        y: num(s.y, -0.5, 1.5, 0.5),
        rad: num(s.rad, 0.001, 2, 0.15),
        ang: num(s.ang, -100, 100, 0),
        r: num(s.r, 0, 1, 1),
        g: num(s.g, 0, 1, 1),
        b: num(s.b, 0, 1, 1),
        a: num(s.a, 0, 1, 0.6),
        r2: num(s.r2, 0, 1, 1),
        g2: num(s.g2, 0, 1, 1),
        b2: num(s.b2, 0, 1, 1),
        a2: num(s.a2, 0, 1, 0),
        additive: flag(s.additive, true),
        instances: Math.round(num(s.instances, 1, MAX_INSTANCES, 1)),
        init: formula(s.init, `shape ${i + 1} init`),
        frame: formula(s.frame, `shape ${i + 1}`),
      };
    });
    if (problem) return { ok: false, error: problem };
    if (formulaChars > MAX_FORMULA_TOTAL)
      return {
        ok: false,
        error: `The formulas add up to more than ${MAX_FORMULA_TOTAL} characters.`,
      };
    return { ok: true, preset: p };
  }

  const FORMAT = 'fh-preset';
  function exportPreset(preset) {
    return JSON.stringify({ [FORMAT]: 1, ...preset }, null, 1);
  }
  function parsePresetFile(textIn) {
    if (typeof textIn !== 'string' || textIn.length > MAX_FILE)
      return { ok: false, error: 'That file is too big to be a visual preset.' };
    let raw;
    try {
      raw = JSON.parse(textIn);
    } catch (e) {
      return { ok: false, error: 'That file is not a visual preset (it is not valid JSON).' };
    }
    if (!raw || typeof raw !== 'object' || raw[FORMAT] !== 1)
      return { ok: false, error: 'That file is not a FalseHorizons preset.' };
    return sanitizePreset(raw);
  }

  // The built-in presets, checked once on first use. A broken one is skipped (and tests fail),
  // never allowed to stop the page.
  let builtinCache = null;
  function builtins() {
    if (builtinCache) return builtinCache;
    const list = [];
    for (const raw of (RawPresets && RawPresets.LIST) || []) {
      const r = sanitizePreset(raw);
      if (r.ok) list.push(Object.freeze(r.preset));
      else if (typeof console !== 'undefined')
        console.warn('built-in visual preset skipped:', raw && raw.name, r.error);
    }
    builtinCache = Object.freeze(list);
    return builtinCache;
  }

  const follow = (current, target, up = 0.5, down = 0.08) =>
    current + (target - current) * (target > current ? up : down);
  // the visuals hear everything 20% louder than the analyser says (still capped at 1); the beat
  // detector uses the true level
  const SENSITIVITY = 1.2;
  const hear = v => Math.min(1, v * SENSITIVITY);

  function bandEnergies(bytes, sampleRate) {
    const binHz = sampleRate / 2 / bytes.length;
    const mean = (fromHz, toHz) => {
      const a = Math.max(0, Math.floor(fromHz / binHz)),
        b = Math.min(bytes.length, Math.max(a + 1, Math.ceil(toHz / binHz)));
      let sum = 0;
      for (let k = a; k < b; k++) sum += bytes[k];
      return sum / (b - a) / 255;
    };
    const bass = mean(20, 250),
      mid = mean(250, 2500),
      treb = mean(2500, 12000);
    return { bass, mid, treb, level: (bass * 2 + mid + treb) / 4 };
  }

  // The analyser's readings -> the numbers formulas see (all 0..1): bass, mid, treb (quick) and
  // *_att (slow average, so "bass - bass_att" is a punch), level, beat (1 on the frame of a beat,
  // then fading; never more than 2.5 a second), a 256-point waveform and a 64-bar spectrum.
  class AudioFeatures {
    constructor() {
      this.bass = this.mid = this.treb = this.level = 0;
      this.bass_att = this.mid_att = this.treb_att = 0;
      this.beat = 0;
      this.wave = new Float32Array(256);
      this.spec = new Float32Array(64);
      this.avg = 0;
      this.lastBeat = -1e9;
    }
    update(freq, time, sampleRate, nowMs) {
      if (freq && freq.length) {
        const e = bandEnergies(freq, sampleRate || 44100);
        this.bass = follow(this.bass, hear(e.bass));
        this.mid = follow(this.mid, hear(e.mid));
        this.treb = follow(this.treb, hear(e.treb));
        this.level = follow(this.level, hear(e.level));
        const beat = e.bass > 0.25 && e.bass > this.avg * 1.35 && nowMs - this.lastBeat >= 400;
        this.avg += (e.bass - this.avg) * 0.08;
        if (beat) {
          this.lastBeat = nowMs;
          this.beat = 1;
        } else this.beat *= 0.9;
        const binHz = (sampleRate || 44100) / 2 / freq.length,
          lo = 40,
          hi = 16000;
        for (let k = 0; k < 64; k++) {
          const f0 = lo * Math.pow(hi / lo, k / 64),
            f1 = lo * Math.pow(hi / lo, (k + 1) / 64);
          const a = Math.min(freq.length - 1, Math.floor(f0 / binHz)),
            b = Math.min(freq.length, Math.max(a + 1, Math.ceil(f1 / binHz)));
          let s = 0;
          for (let i = a; i < b; i++) s += freq[i];
          this.spec[k] = follow(this.spec[k], hear(s / (b - a) / 255), 0.6, 0.15);
        }
      } else {
        for (const k of ['bass', 'mid', 'treb', 'level']) this[k] *= 0.95;
        this.beat *= 0.9;
        this.spec.forEach((v, i) => {
          this.spec[i] = v * 0.9;
        });
      }
      this.bass_att += (this.bass - this.bass_att) * 0.04;
      this.mid_att += (this.mid - this.mid_att) * 0.04;
      this.treb_att += (this.treb - this.treb_att) * 0.04;
      if (time && time.length) {
        for (let i = 0; i < 256; i++)
          this.wave[i] = clamp(
            ((time[Math.floor((i * time.length) / 256)] - 128) / 128) * SENSITIVITY,
            -1,
            1,
          );
      } else
        this.wave.forEach((v, i) => {
          this.wave[i] = v * 0.9;
        });
      return this;
    }
  }

  // Fed the picture's average brightness every few frames, says how much to dim it so the
  // brightness never exceeds `cap` and never rises faster than `rise` per second: a preset (or a
  // mistake in one) cannot strobe. Climbing to the cap takes about half a second (under three full
  // flashes a second even for the worst input); getting darker is instant; calm mode is stricter.
  class FlashGuard {
    constructor(calm = false) {
      this.last = null;
      this.shown = 0;
      this.gain = 1;
      this.setCalm(calm);
    }
    setCalm(calm) {
      this.cap = calm ? 0.35 : 0.45;
      this.rise = calm ? 0.5 : 1.0;
    }
    feed(luminance, nowMs) {
      const L = clamp(finite(luminance, 0), 0, 1);
      const dt = this.last === null ? 0.1 : clamp((nowMs - this.last) / 1000, 0, 0.5);
      this.last = nowMs;
      const allowed = Math.min(this.cap, this.shown + this.rise * dt);
      if (L <= allowed) {
        this.shown = L;
        this.gain = 1;
      } else {
        this.shown = allowed;
        this.gain = allowed / L;
      }
      return this.gain;
    }
  }

  const zeroed = () => Object.create(null);
  function copyInputs(into, from, hear) {
    for (let i = 0; i < INPUTS.length; i++) {
      const n = INPUTS[i],
        x = from[n];
      into[n] = x === undefined ? INPUT_DEFAULTS[n] || 0 : x;
    }
    if (hear !== 1) for (let i = 0; i < HEARD.length; i++) into[HEARD[i]] *= hear;
  }

  class PresetRuntime {
    constructor(preset, rng) {
      this.preset = preset;
      this.milk = preset.dialect === 'milk';
      this.hear = preset.hear;
      const c = src => Expr.compile(src, { rng: rng || Math.random });
      this.initProg = c(preset.init);
      this.frameProg = c(preset.frame);
      this.pixelProg = c(preset.pixel);
      this.v = zeroed();
      this.pv = zeroed();
      this.waves = preset.waves.map(s => ({
        spec: s,
        init: c(s.init),
        frame: c(s.frame),
        point: c(s.point),
        v: zeroed(),
      }));
      this.shapes = preset.shapes.map(s => ({
        spec: s,
        init: c(s.init),
        frame: c(s.frame),
        v: zeroed(),
      }));
      this.pixelLocals = [...new Set([...this.pixelProg.reads, ...this.pixelProg.writes])].filter(
        n => !PIXEL_KEEP.has(n),
      );
      this.uv = new Float32Array((MESH_W + 1) * (MESH_H + 1) * 2);
      this.resetOutputs();
      this.initProg.run(this.v);
      for (const w of this.waves) w.init.run(w.v);
      for (const sh of this.shapes) sh.init.run(sh.v);
    }
    base() {
      const p = this.preset,
        w = p.wave,
        c = p.color;
      return {
        zoom: p.zoom,
        rot: p.rot,
        cx: p.cx,
        cy: p.cy,
        dx: p.dx,
        dy: p.dy,
        sx: p.sx,
        sy: p.sy,
        warp: p.warp,
        decay: p.decay,
        wave_r: w.r,
        wave_g: w.g,
        wave_b: w.b,
        wave_a: w.a,
        wave_scale: w.scale,
        wave_x: w.x,
        wave_y: w.y,
        gamma: c.gamma,
        hue: c.hue,
        bright: c.bright,
        solarize: c.solarize,
        echo_alpha: c.echoAlpha,
        echo_zoom: c.echoZoom,
        echo_orient: c.echoOrient,
        cover: c.cover,
      };
    }
    resetOutputs() {
      Object.assign(this.v, this.base());
    }

    // One frame. `inp` holds the INPUTS plus `wave` and `spec` arrays. opts: motion (0..1, how much
    // the warp moves; 1 = as written), weight (0..1, how visible the drawn shapes are: used while
    // one preset fades into another).
    frame(inp, opts) {
      const motion = opts && opts.motion !== undefined ? opts.motion : 1,
        weight = opts && opts.weight !== undefined ? opts.weight : 1;
      const v = this.v;
      this.resetOutputs();
      copyInputs(v, inp, this.hear);
      this.frameProg.run(v);
      if (this.milk) {
        v.cy = 1 - v.cy;
        v.dy = -v.dy;
        v.rot = -v.rot;
        v.wave_y = 1 - v.wave_y;
      } // imported presets count y downwards and turn the other way
      const f = {};
      for (const k of RANGE_KEYS) f[k] = clamp(finite(v[k], RANGE[k][2]), RANGE[k][0], RANGE[k][1]);
      this.mesh(f, inp, motion);
      const draws = [];
      this.wavesAndShapes(f, inp, weight, draws);
      return {
        decay: f.decay,
        mesh: this.uv,
        draws,
        color: {
          gamma: f.gamma,
          hue: f.hue,
          bright: f.bright,
          solarize: f.solarize,
          echoAlpha: f.echo_alpha,
          echoZoom: f.echo_zoom,
          echoOrient: Math.round(f.echo_orient),
          cover: f.cover,
        },
      };
    }

    mesh(f, inp, motion) {
      const aspect = inp.aspect || 1,
        corner = Math.hypot(aspect * 0.5, 0.5),
        time = inp.time || 0,
        pv = this.pv;
      const hasPixel = this.pixelProg.size > 0;
      if (hasPixel) {
        copyInputs(pv, inp, this.hear);
        for (const q of Q) pv[q] = this.v[q] || 0;
      }
      const uv = this.uv;
      let k = 0;
      for (let j = 0; j <= MESH_H; j++) {
        for (let i = 0; i <= MESH_W; i++) {
          const x = i / MESH_W,
            y = j / MESH_H,
            gx = (x - 0.5) * aspect,
            gy = y - 0.5;
          let zoom = f.zoom,
            rot = f.rot,
            cx = f.cx,
            cy = f.cy,
            dx = f.dx,
            dy = f.dy,
            sx = f.sx,
            sy = f.sy,
            warp = f.warp;
          if (hasPixel) {
            for (const n of WARP_NAMES) pv[n] = f[n];
            for (const n of this.pixelLocals) pv[n] = 0;
            pv.x = x;
            pv.y = y;
            pv.rad = Math.hypot(gx, gy) / corner;
            pv.ang = Math.atan2(gy, gx);
            if (this.milk) {
              pv.y = 1 - y;
              pv.ang = -pv.ang;
              pv.cy = 1 - f.cy;
              pv.dy = -f.dy;
              pv.rot = -f.rot;
            }
            this.pixelProg.run(pv);
            zoom = clamp(finite(pv.zoom, 1), RANGE.zoom[0], RANGE.zoom[1]);
            rot = clamp(finite(pv.rot, 0), RANGE.rot[0], RANGE.rot[1]);
            cx = clamp(finite(pv.cx, 0.5), RANGE.cx[0], RANGE.cx[1]);
            cy = clamp(finite(pv.cy, 0.5), RANGE.cy[0], RANGE.cy[1]);
            dx = clamp(finite(pv.dx, 0), RANGE.dx[0], RANGE.dx[1]);
            dy = clamp(finite(pv.dy, 0), RANGE.dy[0], RANGE.dy[1]);
            sx = clamp(finite(pv.sx, 1), RANGE.sx[0], RANGE.sx[1]);
            sy = clamp(finite(pv.sy, 1), RANGE.sy[0], RANGE.sy[1]);
            warp = clamp(finite(pv.warp, 0), RANGE.warp[0], RANGE.warp[1]);
            if (this.milk) {
              cy = 1 - cy;
              dy = -dy;
              rot = -rot;
            }
          }
          const m = motion;
          zoom = 1 + (zoom - 1) * m;
          sx = 1 + (sx - 1) * m;
          sy = 1 + (sy - 1) * m;
          rot *= m;
          dx *= m;
          dy *= m;
          warp *= m;
          let px = ((x - cx) * aspect) / (sx * zoom),
            py = (y - cy) / (sy * zoom);
          const cs = Math.cos(rot),
            sn = Math.sin(rot);
          const rx = cs * px + sn * py,
            ry = -sn * px + cs * py;
          px = rx;
          py = ry;
          const w = 0.012 * warp;
          const wu =
            w *
            (Math.sin(time * 0.33 + y * 9 + x * 2.1) + 0.5 * Math.sin(time * 0.61 - y * 5 + x * 7));
          const wv =
            w *
            (Math.cos(time * 0.29 + x * 8 - y * 3.1) + 0.5 * Math.cos(time * 0.53 + x * 4 + y * 6));
          uv[k++] = px / aspect + cx - dx + wu;
          uv[k++] = py + cy - dy + wv;
        }
      }
    }

    wavesAndShapes(f, inp, weight, draws) {
      const aspect = inp.aspect || 1,
        wave = inp.wave || new Float32Array(256),
        spec = inp.spec || new Float32Array(64);
      if (f.wave_a * weight > 0.003 && f.wave_scale > 0)
        draws.push(...builtinWave(this.preset.wave, f, aspect, wave, spec, weight));
      for (const w of this.waves) {
        const v = w.v,
          s = w.spec;
        copyInputs(v, inp, this.hear);
        for (const q of Q) v[q] = this.v[q] || 0;
        v.r = s.r;
        v.g = s.g;
        v.b = s.b;
        v.a = s.a;
        w.frame.run(v);
        const r = clamp(finite(v.r, 0), 0, 1),
          g = clamp(finite(v.g, 0), 0, 1),
          b = clamp(finite(v.b, 0), 0, 1),
          a = clamp(finite(v.a, 0), 0, 1) * weight * strength(s.additive);
        if (a <= 0.003) continue;
        const n = s.samples,
          verts = new Float32Array(n * 6);
        for (let i = 0; i < n; i++) {
          const t = i / (n - 1);
          v.sample = t;
          v.value1 = s.spectrum
            ? spec[Math.min(63, Math.floor(t * 63))]
            : wave[Math.min(255, Math.floor(t * 255))];
          v.value2 = v.value1;
          v.x = t;
          v.y = 0.5 + v.value1 * 0.25;
          v.r = r;
          v.g = g;
          v.b = b;
          v.a = a;
          w.point.run(v);
          const o = i * 6;
          verts[o] = clamp(finite(v.x, 0.5), -0.5, 1.5) * 2 - 1;
          verts[o + 1] =
            clamp(this.milk ? 1 - finite(v.y, 0.5) : finite(v.y, 0.5), -0.5, 1.5) * 2 - 1;
          verts[o + 2] = clamp(finite(v.r, r), 0, 1);
          verts[o + 3] = clamp(finite(v.g, g), 0, 1);
          verts[o + 4] = clamp(finite(v.b, b), 0, 1);
          verts[o + 5] =
            clamp(finite(v.a, a / weight / strength(s.additive)), 0, 1) *
            weight *
            strength(s.additive);
        }
        draws.push({
          kind: s.dots ? 'dots' : 'strip',
          verts,
          count: n,
          additive: s.additive,
          thick: s.thick,
        });
      }
      for (const sh of this.shapes) {
        const v = sh.v,
          s = sh.spec;
        for (let inst = 0; inst < s.instances; inst++) {
          // a shape can be drawn several times, each run of its formula told which one it is
          copyInputs(v, inp, this.hear);
          for (const q of Q) v[q] = this.v[q] || 0;
          Object.assign(v, {
            sides: s.sides,
            x: s.x,
            y: s.y,
            rad: s.rad,
            ang: s.ang,
            r: s.r,
            g: s.g,
            b: s.b,
            a: s.a,
            r2: s.r2,
            g2: s.g2,
            b2: s.b2,
            a2: s.a2,
            instance: inst,
            num_inst: s.instances,
          });
          sh.frame.run(v);
          const k = weight * strength(s.additive),
            a = clamp(finite(v.a, 0), 0, 1) * k,
            a2 = clamp(finite(v.a2, 0), 0, 1) * k;
          if (a <= 0.003 && a2 <= 0.003) continue;
          const sides = Math.round(clamp(finite(v.sides, 4), 3, 100)),
            x = clamp(finite(v.x, 0.5), -0.5, 1.5),
            y = clamp(this.milk ? 1 - finite(v.y, 0.5) : finite(v.y, 0.5), -0.5, 1.5);
          const rad = clamp(finite(v.rad, 0.1), 0.001, 2),
            ang = clamp(this.milk ? -finite(v.ang, 0) : finite(v.ang, 0), -100, 100);
          const verts = new Float32Array((sides + 2) * 6);
          const put = (n, px, py, r, g, b, al) => {
            const o = n * 6;
            verts[o] = px * 2 - 1;
            verts[o + 1] = py * 2 - 1;
            verts[o + 2] = clamp(finite(r, 0), 0, 1);
            verts[o + 3] = clamp(finite(g, 0), 0, 1);
            verts[o + 4] = clamp(finite(b, 0), 0, 1);
            verts[o + 5] = al;
          };
          put(0, x, y, v.r, v.g, v.b, a);
          for (let n = 0; n <= sides; n++) {
            const th = ang + ((n % sides) * 2 * Math.PI) / sides;
            put(
              n + 1,
              x + (rad * Math.cos(th)) / aspect,
              y + rad * Math.sin(th),
              v.r2,
              v.g2,
              v.b2,
              a2,
            );
          }
          draws.push({ kind: 'fan', verts, count: sides + 2, additive: s.additive, thick: 1 });
        }
      }
    }
  }

  // The built-in waveform: 0 circle, 1 line across, 2 spokes of the spectrum, 3 spectrum line, 4
  // two lines.
  function builtinWave(spec, f, aspect, wave, spec64, weight) {
    const r = f.wave_r,
      g = f.wave_g,
      b = f.wave_b,
      a = f.wave_a * weight * strength(spec.additive),
      sc = f.wave_scale,
      wx = f.wave_x,
      wy = f.wave_y;
    const mk = (n, kind, fill) => {
      const verts = new Float32Array(n * 6);
      for (let i = 0; i < n; i++) {
        const p = fill(i);
        const o = i * 6;
        verts[o] = clamp(p[0], -0.5, 1.5) * 2 - 1;
        verts[o + 1] = clamp(p[1], -0.5, 1.5) * 2 - 1;
        verts[o + 2] = r;
        verts[o + 3] = g;
        verts[o + 4] = b;
        verts[o + 5] = a;
      }
      return { kind, verts, count: n, additive: spec.additive, thick: spec.thick };
    };
    switch (spec.mode) {
      case 1:
        return [mk(128, 'strip', i => [i / 127, wy + 0.3 * sc * wave[i * 2]])];
      case 2:
        return [
          mk(128, 'lines', i => {
            const k = i >> 1,
              th = (k * 2 * Math.PI) / 64,
              rad = 0.1 + (i & 1) * 0.3 * sc * spec64[k];
            return [wx + (rad * Math.cos(th)) / aspect, wy + rad * Math.sin(th)];
          }),
        ];
      case 3:
        return [mk(64, 'strip', i => [i / 63, wy - 0.35 + 0.7 * sc * spec64[i]])];
      case 4:
        return [
          mk(128, 'strip', i => [i / 127, wy - 0.15 + 0.2 * sc * wave[i * 2]]),
          mk(128, 'strip', i => [i / 127, wy + 0.15 + 0.2 * sc * wave[255 - i * 2]]),
        ];
      default:
        return [
          mk(129, 'strip', i => {
            const th = ((i % 128) * 2 * Math.PI) / 128,
              rad = 0.2 * sc * (1 + 0.8 * wave[(i % 128) * 2]);
            return [wx + (rad * Math.cos(th)) / aspect, wy + rad * Math.sin(th)];
          }),
        ];
    }
  }

  // Two frames of the same picture steps (preset fading out, preset fading in). In the middle of a
  // change both warps act on the picture at once, so the old preset's tunnel keeps tearing at the
  // new one's spin; fade, colours and echo are mixed by t, with echo and fade pushed up in the
  // middle so the pictures smear into each other.
  // how much of each warp is added on top of the mix at the halfway point (0 = a plain crossfade)
  const CROSS = 0.5;
  function blendStates(a, b, t) {
    if (!b) return a;
    if (!a || t >= 1) return b;
    const bump = Math.sin(Math.PI * t),
      mesh = new Float32Array(a.mesh.length),
      W = MESH_W + 1;
    for (let i = 0; i < mesh.length; i++) {
      const g = i >> 1,
        // where this point sits with no warp at all
        home = i & 1 ? Math.floor(g / W) / MESH_H : (g % W) / MESH_W;
      const da = a.mesh[i] - home,
        db = b.mesh[i] - home;
      mesh[i] = home + lerp(da, db, t) + bump * CROSS * (da + db);
    }
    const color = {};
    for (const k of Object.keys(a.color)) color[k] = lerp(a.color[k], b.color[k], t);
    color.echoOrient = t < 0.5 ? a.color.echoOrient : b.color.echoOrient;
    color.echoAlpha = Math.min(0.5, color.echoAlpha + 0.25 * bump);
    const decay = lerp(a.decay, b.decay, t);
    return {
      decay: Math.min(0.99, decay + (Math.max(a.decay, b.decay) - decay) * bump),
      mesh,
      color,
      draws: a.draws.concat(b.draws),
    };
  }

  // The fractal: a height field that melts between a simple ring wave and a Mandelbrot surface
  // (smooth escape count, so the terraces have no steps), tipped toward you and seen in
  // perspective. Where that puts each point is added to the warp mesh, so everything in the
  // feedback loop is pulled over the surface. Near the end of each cycle the Mandelbrot constant is
  // shaken by the music's waveform and noise, so the surface tears, then settles and rebuilds.
  // radians a second: one full wave -> fractal -> wave round trip takes about 18 seconds
  const MORPH_SPEED = 0.35;
  // how much of the surface's displacement is added to the mesh per frame (about as strong as a
  // preset's own warp)
  const WARP_PULL = 0.15;
  const MAX_ITER = 24,
    BAILOUT = 256;
  const modulationIndex = (time, speed = MORPH_SPEED) =>
    (Math.sin(finite(time, 0) * speed) + 1) * 0.5;
  // cheap, jumpy, between -1 and 1
  const noise2 = (x, y, t) => (Math.sin(x * 12.9898 + y * 78.233 + t * 3.1) * 43758.5453) % 1;

  // The base wave, about -0.5..0.5: rings that travel out from the middle.
  const baseHeight = (gx, gy, time) => 0.5 * Math.sin(9 * Math.hypot(gx, gy) - time * 1.3);
  // The Mandelbrot height at C = (cr, ci), 0..1: the smooth escape count over MAX_ITER, and 1 for
  // points that never escape.
  function fractalHeight(cr, ci) {
    let zr = 0,
      zi = 0,
      i = 0,
      r2 = 0;
    while (i < MAX_ITER && (r2 = zr * zr + zi * zi) <= BAILOUT * BAILOUT) {
      const t = zr * zr - zi * zi + cr;
      zi = 2 * zr * zi + ci;
      zr = t;
      i++;
    }
    if (i >= MAX_ITER) return 1;
    // i + 1 - log2(log2|z|): a smooth number between whole steps
    return clamp((i + 1 - Math.log2(Math.log2(Math.max(r2, 4)) / 2)) / MAX_ITER, 0, 1);
  }

  // Adds the surface to `mesh` (the warp mesh of one frame, changed in place). amount 0..1 is the
  // user's geometry setting; calm slows the cycle.
  function fractalWarp(mesh, inp, amount, calm) {
    if (!(amount > 0.01)) return mesh;
    const time = finite(inp.time, 0),
      aspect = inp.aspect || 1,
      wave = inp.wave || new Float32Array(256);
    const bass = inp.bass || 0,
      mid = inp.mid || 0;
    const m = modulationIndex(time, calm ? MORPH_SPEED / 4 : MORPH_SPEED),
      glitch = m * m * m * (calm ? 0.3 : 1);
    const yaw = 0.5 * Math.sin(time * 0.23) + mid * 0.5,
      pitch = 0.45 + 0.25 * Math.sin(time * 0.17),
      roll = time * 0.07;
    const cy = Math.cos(yaw),
      sy = Math.sin(yaw),
      cp = Math.cos(pitch),
      sp = Math.sin(pitch),
      cr = Math.cos(roll),
      sr = Math.sin(roll);
    const lift = 0.66 * (1 + 0.7 * bass),
      CAM = 2.35,
      pull = WARP_PULL * amount,
      view = 1.5 + 0.4 * Math.sin(time * 0.11);
    let k = 0;
    for (let j = 0; j <= MESH_H; j++) {
      for (let i = 0; i <= MESH_W; i++, k += 2) {
        const x = i / MESH_W,
          y = j / MESH_H,
          gx = (x - 0.5) * aspect,
          gy = y - 0.5;
        const col = Math.min(255, Math.max(0, Math.floor(x * 255)));
        const shake =
          glitch *
          ((wave[col] || 0) * (0.15 + 0.5 * bass) + 0.12 * noise2(i, j, Math.floor(time * 8)));
        const zb = baseHeight(gx, gy, time);
        const zf =
          fractalHeight(
            -0.7 + (gx * cr - gy * sr) * view + shake,
            (gx * sr + gy * cr) * view + shake * 0.8,
          ) - 0.5;
        let X = gx,
          Y = gy,
          Z = ((1 - m) * zb + m * zf) * lift,
          t;
        t = X * cy + Z * sy;
        Z = -X * sy + Z * cy;
        X = t;
        t = Y * cp - Z * sp;
        Z = Y * sp + Z * cp;
        Y = t;
        const s = CAM / (CAM - Z);
        // the fractal also pulls the picture in toward the middle: a tunnel to fall down
        const dive = 0.2 * m * pull;
        mesh[k] += ((X * s) / aspect + 0.5 - x) * pull + (x - 0.5) * dive;
        mesh[k + 1] += (Y * s + 0.5 - y) * pull + (y - 0.5) * dive;
      }
    }
    return mesh;
  }

  // Random, but never one of the last few shown; Previous steps back through what was shown, Next
  // retraces before picking again.
  class PresetPicker {
    constructor(rng) {
      this.rng = rng || Math.random;
      this.history = [];
      this.i = -1;
    }
    show(name) {
      this.history.length = this.i + 1;
      this.history.push(name);
      if (this.history.length > 100) this.history.shift();
      this.i = this.history.length - 1;
    }
    next(pool) {
      if (this.i < this.history.length - 1) {
        this.i += 1;
        return this.history[this.i];
      }
      if (!pool.length) return null;
      const recent = this.history.slice(-Math.min(4, pool.length - 1));
      const fresh = pool.filter(n => !recent.includes(n));
      const name = (fresh.length ? fresh : pool)[
        Math.floor(this.rng() * (fresh.length ? fresh : pool).length)
      ];
      this.show(name);
      return name;
    }
    prev() {
      if (this.i <= 0) return null;
      this.i -= 1;
      return this.history[this.i];
    }
  }

  const DEFAULTS = {
    on: true,
    intensity: 77,
    mode: 'horizon',
    auto: true,
    cycleSec: 30,
    covers: 40,
    geometry: 60,
    favorites: [],
    last: '',
    userPresets: [],
  };

  function sanitize(raw) {
    raw = raw && typeof raw === 'object' ? raw : {};
    const names = new Set(),
      userPresets = [],
      favorites = [];
    for (const item of Array.isArray(raw.userPresets) ? raw.userPresets : []) {
      const r = sanitizePreset(item);
      if (r.ok && !names.has(r.preset.name) && userPresets.length < MAX_USER_PRESETS) {
        names.add(r.preset.name);
        userPresets.push(r.preset);
      }
    }
    for (const n of Array.isArray(raw.favorites) ? raw.favorites : []) {
      const name = text(n, MAX_NAME);
      if (name && !favorites.includes(name) && favorites.length < MAX_FAVORITES)
        favorites.push(name);
    }
    const cycle = Math.round(num(raw.cycleSec, 10, 300, DEFAULTS.cycleSec));
    return {
      on: flag(raw.on, DEFAULTS.on),
      intensity: Math.round(num(raw.intensity, 0, 100, DEFAULTS.intensity)),
      mode: MODES.includes(raw.mode) ? raw.mode : DEFAULTS.mode,
      auto: flag(raw.auto, DEFAULTS.auto),
      cycleSec: cycle,
      covers: Math.round(num(raw.covers, 0, 100, DEFAULTS.covers)),
      geometry: Math.round(num(raw.geometry, 0, 100, DEFAULTS.geometry)),
      favorites,
      last: text(raw.last, MAX_NAME),
      userPresets,
    };
  }

  const storageKey = username => 'falsehorizon.v1.' + String(username || 'guest').slice(0, 64);
  function loadSettings(storage, username) {
    try {
      const raw = storage.getItem(storageKey(username));
      if (raw !== null && raw !== undefined) return sanitize(JSON.parse(raw));
    } catch (e) {
      /* unreadable: the defaults */
    }
    return sanitize(null);
  }
  function saveSettings(storage, username, settings) {
    try {
      storage.setItem(storageKey(username), JSON.stringify(sanitize(settings)));
      return true;
    } catch (e) {
      return false;
    }
  }

  // Your own presets: a name that a built-in uses is refused (so "Slow Tide" always means the same
  // thing); the same name again replaces yours.
  function savePreset(settings, preset) {
    const r = sanitizePreset(preset);
    if (!r.ok) return { ok: false, error: r.error };
    if (builtins().some(p => p.name.toLowerCase() === r.preset.name.toLowerCase()))
      return { ok: false, error: 'A built-in preset already has that name. Pick another.' };
    const others = settings.userPresets.filter(p => p.name !== r.preset.name);
    if (others.length >= MAX_USER_PRESETS)
      return {
        ok: false,
        error: `You can keep ${MAX_USER_PRESETS} presets of your own. Delete one first.`,
      };
    return {
      ok: true,
      settings: sanitize({ ...settings, userPresets: [...others, r.preset] }),
      preset: r.preset,
    };
  }
  const deletePreset = (settings, name) =>
    sanitize({
      ...settings,
      userPresets: settings.userPresets.filter(p => p.name !== name),
      favorites: settings.favorites.filter(n => n !== name),
    });
  const toggleFavorite = (settings, name) =>
    sanitize({
      ...settings,
      favorites: settings.favorites.includes(name)
        ? settings.favorites.filter(n => n !== name)
        : [...settings.favorites, name],
    });

  const api = {
    MESH_W,
    MESH_H,
    SENSITIVITY,
    CROSS,
    MORPH_SPEED,
    modulationIndex,
    fractalWarp,
    fractalHeight,
    baseHeight,
    MAX_FILE,
    MAX_NAME,
    MAX_USER_PRESETS,
    MAX_FAVORITES,
    MAX_FORMULA_TOTAL,
    MAX_INSTANCES,
    CYCLE_CHOICES,
    MODES,
    RANGE,
    INPUTS,
    DEFAULTS,
    FORMAT,
    clamp,
    sanitizePreset,
    exportPreset,
    parsePresetFile,
    builtins,
    bandEnergies,
    follow,
    AudioFeatures,
    FlashGuard,
    PresetRuntime,
    blendStates,
    PresetPicker,
    sanitize,
    storageKey,
    loadSettings,
    saveSettings,
    savePreset,
    deletePreset,
    toggleFavorite,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FalseHorizonCore = api;
})(typeof window !== 'undefined' ? window : globalThis);
