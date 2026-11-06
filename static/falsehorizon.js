// FalseHorizon: a preset-driven music visualizer (WebGL). This file only draws; presets, formulas,
// the flash guard and the audio analysis are in falsehorizon-core.js, which must load first.
//
// Per frame: the previous picture is redrawn through the preset's warp mesh and faded (trails and
// tunnels), with a little album art and, if the page supplies a "scene" canvas, that stamped in so
// the visuals warp it; then the preset's waveforms and shapes go on top; the picture's average
// brightness goes to the flash guard, which says how much to dim; then colour processing. Changing
// preset blends the two warps and fades the shapes over a few seconds.
// Safety: flash guard, a calm mode (less motion, 24 fps), a smaller drawing size on slow devices,
// and it stops when the tab is hidden. Everything it loads is same-origin.
(function () {
  const PRECISION =
    '#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n';

  const COVER_GLSL = `
  uniform sampler2D uCoverA, uCoverB; uniform vec2 uCoverSizeA, uCoverSizeB, uRes; uniform float uCoverMix;
  // fill the frame without stretching the picture
  vec2 fit(vec2 uv, vec2 size) {
    float ca = uRes.x / uRes.y, ia = size.x / size.y;
    vec2 c = uv - 0.5;
    if (ca > ia) c.y *= ia / ca; else c.x *= ca / ia;
    return c + 0.5;
  }
  vec3 coverColor(vec2 uv) { return mix(texture2D(uCoverA, fit(uv, uCoverSizeA)).rgb, texture2D(uCoverB, fit(uv, uCoverSizeB)).rgb, uCoverMix); }`;

  const WARP_VERT =
    'attribute vec2 aPos; attribute vec2 aUv; varying vec2 vUv; varying vec2 vScreen; void main() { vUv = aUv; vScreen = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }';
  const WARP_FRAG =
    PRECISION +
    `
  varying vec2 vUv; varying vec2 vScreen;
  uniform sampler2D uPrev, uScene; uniform float uDecay, uFade, uInject, uSceneAmt;
  ${COVER_GLSL}
  // past the edge the picture folds back instead of smearing
  vec2 mirror(vec2 p) { return abs(mod(p + 1.0, 2.0) - 1.0); }
  void main() {
    vec3 c = texture2D(uPrev, mirror(vUv)).rgb * uDecay;
    // a hair off every frame so slow fades really reach black (8-bit rounding would stall them)
    c = max(c - vec3(uFade), 0.0);
    if (uInject > 0.0) c += coverColor(vScreen) * uInject;
    // the page's own pictures and words go into the loop
    if (uSceneAmt > 0.0) { vec4 s = texture2D(uScene, vScreen); c = mix(c, s.rgb, s.a * uSceneAmt); }
    gl_FragColor = vec4(min(c, 1.0), 1.0);
  }`;

  const FILL_VERT =
    'attribute vec2 aPos; attribute vec4 aCol; uniform vec2 uOff; uniform float uPoint; varying vec4 vCol; void main() { vCol = aCol; gl_PointSize = uPoint; gl_Position = vec4(aPos + uOff, 0.0, 1.0); }';
  const FILL_FRAG = PRECISION + 'varying vec4 vCol; void main() { gl_FragColor = vCol; }';

  const FULL_VERT =
    'attribute vec2 aPos; varying vec2 vUv; void main() { vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }';
  const COMPOSITE_FRAG =
    PRECISION +
    `
  varying vec2 vUv;
  uniform sampler2D uTex;
  uniform sampler2D uScene; uniform float uGamma, uHue, uBright, uSolar, uEchoA, uEchoZ, uEchoO, uCoverAmt, uSceneDirect, uWobble, uClock;
  ${COVER_GLSL}
  vec2 mirror(vec2 p) { return abs(mod(p + 1.0, 2.0) - 1.0); }
  void main() {
    vec3 c = texture2D(uTex, vUv).rgb;
    // a mirrored, zoomed copy laid over the picture
    if (uEchoA > 0.001) {
      vec2 e = (vUv - 0.5) / uEchoZ + 0.5;
      if (uEchoO > 0.5 && uEchoO < 1.5 || uEchoO > 2.5) e.x = 1.0 - e.x;
      if (uEchoO > 1.5) e.y = 1.0 - e.y;
      c = mix(c, texture2D(uTex, mirror(e)).rgb, uEchoA);
    }
    c = pow(max(c, vec3(0.0)), vec3(1.0 / uGamma));
    c = mix(c, abs(c * 2.0 - 1.0), uSolar);
    float h = uHue * 6.28318;
    vec3 k = vec3(0.57735);
    float cs = cos(h), sn = sin(h);
    // turn the colours around the grey axis
    c = c * cs + cross(k, c) * sn + k * dot(k, c) * (1.0 - cs);
    if (uCoverAmt > 0.0) c += coverColor(vUv) * uCoverAmt * (0.4 + dot(c, vec3(0.299, 0.587, 0.114)));
    // a little of the scene straight, but rippling with the music, so words stay readable
    if (uSceneDirect > 0.0) {
      vec2 d = vec2(sin(vUv.y * 14.0 + uClock * 1.3), cos(vUv.x * 11.0 - uClock * 1.1)) * uWobble;
      vec4 s = texture2D(uScene, mirror(vUv + d));
      c += s.rgb * s.a * uSceneDirect;
    }
    gl_FragColor = vec4(clamp(c * uBright, 0.0, 1.0), 1.0);
  }`;

  // The picture's average brightness, worked out on the graphics card into a 16 x 9 image that is
  // then read back (144 pixels).
  const MEASURE_FRAG =
    PRECISION +
    `
  varying vec2 vUv; uniform sampler2D uTex;
  void main() {
    vec2 cell = vec2(1.0 / 16.0, 1.0 / 9.0), o = floor(vUv * vec2(16.0, 9.0)) * cell;
    float s = 0.0;
    for (int i = 0; i < 8; i++) for (int j = 0; j < 8; j++) s += dot(texture2D(uTex, o + (vec2(float(i), float(j)) + 0.5) / 8.0 * cell).rgb, vec3(0.299, 0.587, 0.114));
    s /= 64.0;
    gl_FragColor = vec4(s, s, s, 1.0);
  }`;

  const MAX_COVER_SIDE = 1024;
  const FADE_MS = 2500,
    FADE_CALM_MS = 3500,
    COVER_FADE_MS = 2600;
  // the scene is drawn and uploaded at this fraction of the picture's size: the feedback blurs it
  // anyway
  const SCENE_SCALE = 0.67;
  const SCENE_AMT = 0.22,
    // how hard the scene is pushed into the feedback each frame (small: the warp has to win), and
    // how much is shown unwarped
    SCENE_DIRECT = 0.14;
  const rnd = (a, b) => a + Math.random() * (b - a);

  function unsupported(reason) {
    const nop = () => {};
    return {
      supported: false,
      reason,
      running: false,
      start: nop,
      stop: nop,
      destroy: nop,
      setSources: nop,
      prioritise: nop,
      setIntensity: nop,
      setMaxFps: nop,
      setCovers: nop,
      setGeometry: nop,
      setScene: nop,
      setCalm: nop,
      setAnalyser: nop,
      setAuto: nop,
      setUserPresets: nop,
      setFavorites: nop,
      lock: nop,
      next: () => null,
      prev: () => null,
      setPreset: () => false,
      onPreset: nop,
      list: () => [],
      current: () => '',
      inspect: () => ({ supported: false }),
    };
  }

  function create(canvas) {
    const Core = window.FalseHorizonCore;
    let gl = null;
    try {
      gl =
        canvas.getContext('webgl', {
          alpha: false,
          antialias: false,
          powerPreference: 'low-power',
        }) || canvas.getContext('experimental-webgl');
    } catch (e) {
      gl = null;
    }
    if (!gl || !Core)
      return unsupported('This browser has no WebGL, so the visuals are not available.');

    let gfx = null;
    let analyser = null,
      freqBytes = null,
      timeBytes = null,
      running = false,
      raf = 0,
      lost = false;
    let maxFps = 0,
      geometry = 0.6,
      intensity = 0.7,
      covers = 0.4,
      calm = false,
      auto = true,
      cycleMs = 30000,
      locked = false,
      favorites = [],
      userPresets = [],
      listener = null;
    let cur = null,
      curName = '',
      fade = null,
      presetSince = 0;
    let coverA = null,
      coverB = null,
      coverMix = 0,
      coverStart = 0,
      nextCoverAt = 0,
      loading = false,
      generation = 0,
      sources = [],
      priority = '';
    let scene = null;
    let frames = 0,
      last = 0,
      t0 = performance.now(),
      scale = 1,
      avgDt = 16,
      slowFrames = 0,
      gain = 1;
    const features = new Core.AudioFeatures(),
      guard = new Core.FlashGuard(false),
      picker = new Core.PresetPicker();
    const inputs = {
      time: 0,
      frame: 0,
      bass: 0,
      mid: 0,
      treb: 0,
      bass_att: 0,
      mid_att: 0,
      treb_att: 0,
      level: 0,
      beat: 0,
      aspect: 1.78,
      wave: features.wave,
      spec: features.spec,
    };
    let error = '';

    const all = () => Core.builtins().concat(userPresets);
    const find = name => all().find(p => p.name === name) || null;
    const runtime = p => {
      try {
        return new Core.PresetRuntime(p);
      } catch (e) {
        error = String(e.message || e);
        return null;
      }
    };
    function announce() {
      if (listener)
        try {
          listener(currentName());
        } catch (e) {
          /* a broken listener must not stop the picture */
        }
    }

    function setPreset(which, opts) {
      const p = typeof which === 'string' ? find(which) : which;
      if (!p || lost) return false;
      const rt = runtime(p);
      if (!rt) return false;
      if (fade) {
        cur = fade.to;
        curName = fade.name;
        fade = null;
      } // a change in the middle of a change: finish the first at once
      const now = performance.now();
      if (!cur || (opts && opts.instant)) {
        cur = rt;
        curName = p.name;
        fade = null;
      } else fade = { to: rt, name: p.name, start: now, dur: calm ? FADE_CALM_MS : FADE_MS };
      presetSince = now;
      // chosen by hand: Previous can come back to it
      if (!(opts && opts.quiet) && picker.history[picker.i] !== p.name) picker.show(p.name);
      announce();
      return true;
    }
    const currentName = () => (fade ? fade.name : curName);
    function pool() {
      const names = all().map(p => p.name),
        liked = favorites.filter(n => names.includes(n));
      return liked.length && Math.random() < 0.5 ? liked : names;
    }
    function step(dir) {
      const name = dir > 0 ? picker.next(pool()) : picker.prev();
      if (!name) return null;
      return setPreset(name, { quiet: true }) ? name : null;
    }

    function compile(type, src) {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS))
        throw new Error(gl.getShaderInfoLog(sh) || 'shader failed to compile');
      return sh;
    }
    function program(vert, frag, attribs) {
      const p = gl.createProgram();
      gl.attachShader(p, compile(gl.VERTEX_SHADER, vert));
      gl.attachShader(p, compile(gl.FRAGMENT_SHADER, frag));
      attribs.forEach((name, i) => gl.bindAttribLocation(p, i, name));
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS))
        throw new Error(gl.getProgramInfoLog(p) || 'program failed to link');
      const u = {};
      const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
      for (let i = 0; i < n; i++) {
        const info = gl.getActiveUniform(p, i);
        u[info.name] = gl.getUniformLocation(p, info.name);
      }
      return { p, u };
    }
    function texture(source, w, h, filter) {
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter || gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter || gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      if (source) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      return t;
    }
    function target(w, h) {
      const tex = texture(null, w, h),
        fbo = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
      gl.viewport(0, 0, w, h);
      gl.clearColor(0, 0, 0, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      return { tex, fbo, w, h };
    }

    function init() {
      const W = Core.MESH_W,
        H = Core.MESH_H,
        pos = new Float32Array((W + 1) * (H + 1) * 2),
        idx = new Uint16Array(W * H * 6);
      let k = 0;
      for (let j = 0; j <= H; j++)
        for (let i = 0; i <= W; i++) {
          pos[k++] = (i / W) * 2 - 1;
          pos[k++] = (j / H) * 2 - 1;
        }
      k = 0;
      for (let j = 0; j < H; j++)
        for (let i = 0; i < W; i++) {
          const a = j * (W + 1) + i,
            b = a + 1,
            c = a + W + 1,
            d = c + 1;
          idx[k++] = a;
          idx[k++] = b;
          idx[k++] = c;
          idx[k++] = b;
          idx[k++] = d;
          idx[k++] = c;
        }
      const buf = (data, usage, type) => {
        const b = gl.createBuffer();
        gl.bindBuffer(type || gl.ARRAY_BUFFER, b);
        gl.bufferData(type || gl.ARRAY_BUFFER, data, usage);
        return b;
      };
      const blank = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, blank);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        1,
        1,
        0,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        new Uint8Array([0, 0, 0, 255]),
      );
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gfx = {
        warp: program(WARP_VERT, WARP_FRAG, ['aPos', 'aUv']),
        fill: program(FILL_VERT, FILL_FRAG, ['aPos', 'aCol']),
        composite: program(FULL_VERT, COMPOSITE_FRAG, ['aPos']),
        measure: program(FULL_VERT, MEASURE_FRAG, ['aPos']),
        triBuf: buf(new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW),
        meshPos: buf(pos, gl.STATIC_DRAW),
        meshUv: buf(new Float32Array(pos.length), gl.DYNAMIC_DRAW),
        dynBuf: buf(new Float32Array(1536), gl.STREAM_DRAW),
        idxBuf: buf(idx, gl.STATIC_DRAW, gl.ELEMENT_ARRAY_BUFFER),
        indexCount: idx.length,
        blank,
        sceneTex: null,
        targets: null,
        read: 0,
        small: null,
        pixels: new Uint8Array(16 * 9 * 4),
      };
      sizeTargets(true);
      gfx.small = target(16, 9);
    }
    function sizeTargets(force) {
      const cssW = canvas.clientWidth || 640,
        cssH = canvas.clientHeight || 360;
      const w = Math.max(160, Math.min(960, Math.round(cssW * 0.6 * scale))),
        h = Math.max(90, Math.round((w * cssH) / cssW));
      if (!force && gfx.targets && gfx.targets[0].w === w && gfx.targets[0].h === h) return;
      if (gfx.targets)
        gfx.targets.forEach(t => {
          gl.deleteTexture(t.tex);
          gl.deleteFramebuffer(t.fbo);
        });
      canvas.width = w;
      canvas.height = h;
      gfx.targets = [target(w, h), target(w, h)];
      gfx.read = 0;
      inputs.aspect = w / h;
      inputs.pixelsx = w;
      inputs.pixelsy = h;
      inputs.aspectx = w > h ? h / w : 1;
      inputs.aspecty = w > h ? 1 : w / h;
    }

    function loadCover(url, then) {
      const mine = generation,
        img = new Image();
      img.decoding = 'async';
      img.onload = () => {
        if (mine !== generation || lost || !gfx) return;
        const k = Math.min(1, MAX_COVER_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
        const w = Math.max(1, Math.round(img.naturalWidth * k)),
          h = Math.max(1, Math.round(img.naturalHeight * k));
        const c = document.createElement('canvas');
        c.width = w;
        c.height = h;
        c.getContext('2d').drawImage(img, 0, 0, w, h);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        const tex = texture(c);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        then({ tex, w, h });
      };
      img.onerror = () => {
        loading = false;
        nextCoverAt = performance.now() + 1500;
      }; // a 404 just means: try another
      img.src = url;
    }
    const dropCover = c => {
      if (c && gfx) gl.deleteTexture(c.tex);
    };
    function manageCovers(now) {
      if (coverMix > 0 && coverB) {
        coverMix = Math.min(1, (now - coverStart) / COVER_FADE_MS);
        if (coverMix >= 1) {
          dropCover(coverA);
          coverA = coverB;
          coverB = null;
          coverMix = 0;
          nextCoverAt = now + rnd(15000, 25000);
        }
      }
      if (loading || coverB || now < nextCoverAt || covers <= 0) return;
      const url =
        priority || (sources.length ? sources[Math.floor(Math.random() * sources.length)] : '');
      priority = '';
      if (!url) return;
      loading = true;
      loadCover(url, c => {
        loading = false;
        if (!coverA) {
          coverA = c;
          nextCoverAt = performance.now() + rnd(15000, 25000);
          return;
        }
        coverB = c;
        coverMix = 0.001;
        coverStart = performance.now();
      });
    }

    const bind = (unit, tex) => {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, tex);
    };
    function coverUniforms(u) {
      const a = coverA,
        b = coverB || coverA;
      bind(1, a ? a.tex : gfx.blank);
      bind(2, b ? b.tex : gfx.blank);
      gl.uniform1i(u.uCoverA, 1);
      gl.uniform1i(u.uCoverB, 2);
      gl.uniform2f(u.uCoverSizeA, a ? a.w : 1, a ? a.h : 1);
      gl.uniform2f(u.uCoverSizeB, b ? b.w : 1, b ? b.h : 1);
      gl.uniform1f(u.uCoverMix, coverB ? coverMix : 0);
    }
    function drawFill(d, w) {
      const f = gfx.fill;
      gl.bindBuffer(gl.ARRAY_BUFFER, gfx.dynBuf);
      gl.bufferData(gl.ARRAY_BUFFER, d.verts, gl.STREAM_DRAW);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 24, 0);
      gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 24, 8);
      gl.blendFunc(gl.SRC_ALPHA, d.additive ? gl.ONE : gl.ONE_MINUS_SRC_ALPHA);
      const mode =
        d.kind === 'strip'
          ? gl.LINE_STRIP
          : d.kind === 'lines'
            ? gl.LINES
            : d.kind === 'dots'
              ? gl.POINTS
              : gl.TRIANGLE_FAN;
      gl.uniform1f(f.u.uPoint, d.kind === 'dots' ? 2 + d.thick * 2 : 1);
      const px = 2 / w,
        offsets =
          d.thick > 1 && d.kind !== 'fan' && d.kind !== 'dots'
            ? [
                [0, 0],
                [px, 0],
                [0, px],
                [-px, 0],
                [0, -px],
              ].slice(0, d.thick === 2 ? 3 : 5)
            : [[0, 0]];
      for (const o of offsets) {
        gl.uniform2f(f.u.uOff, o[0], o[1]);
        gl.drawArrays(mode, 0, d.count);
      }
    }

    function frame(now) {
      raf = 0;
      if (!running || lost || !gfx) return;
      raf = requestAnimationFrame(frame);
      // calm mode, or a cap a slow device asked for
      const gap = calm ? 1000 / 24 : maxFps ? 1000 / maxFps - 2 : 0;
      if (gap && now - last < gap) return;
      const rawDt = now - last || 16;
      last = now;
      avgDt += (Math.min(rawDt, 200) - avgDt) * 0.05;
      if (!calm && ++slowFrames >= 90) {
        // too slow for 90 frames: draw smaller (and bigger again if there is room)
        slowFrames = 0;
        if (avgDt > 30 && scale > 0.5) scale = Math.max(0.5, scale * 0.8);
        else if (avgDt < 18 && scale < 1) scale = Math.min(1, scale * 1.15);
      }
      sizeTargets(false);

      if (analyser) {
        if (!freqBytes || freqBytes.length !== analyser.frequencyBinCount) {
          freqBytes = new Uint8Array(analyser.frequencyBinCount);
          timeBytes = new Uint8Array(analyser.fftSize || analyser.frequencyBinCount * 2);
        }
        analyser.getByteFrequencyData(freqBytes);
        if (analyser.getByteTimeDomainData) analyser.getByteTimeDomainData(timeBytes);
        features.update(
          freqBytes,
          analyser.getByteTimeDomainData ? timeBytes : null,
          analyser.context ? analyser.context.sampleRate : 44100,
          now,
        );
      } else features.update(null, null, 44100, now);
      Object.assign(inputs, {
        time: (now - t0) / 1000,
        frame: frames++,
        bass: features.bass,
        mid: features.mid,
        treb: features.treb,
        bass_att: features.bass_att,
        mid_att: features.mid_att,
        treb_att: features.treb_att,
        level: features.level,
        beat: calm ? features.beat * 0.4 : features.beat,
      });

      if (cur && !fade && auto && !locked && now - presetSince > cycleMs) step(1);
      let t = 0;
      if (fade) {
        t = Math.min(1, (now - fade.start) / fade.dur);
        t = t * t * (3 - 2 * t);
      }
      const motion = (0.1 + 0.9 * intensity) * (calm ? 0.4 : 1);
      let state = null;
      if (cur) {
        // in the middle of a change both presets draw at nearly full strength, so they cut across
        // each other
        const bump = Math.sin(Math.PI * t);
        const a = cur.frame(inputs, { motion, weight: fade ? Math.min(1, 1 - t + 0.6 * bump) : 1 });
        if (fade) {
          const b = fade.to.frame(inputs, { motion, weight: Math.min(1, t + 0.6 * bump) });
          state = Core.blendStates(a, b, t);
          if (now - fade.start >= fade.dur) {
            cur = fade.to;
            curName = fade.name;
            fade = null;
          }
        } else state = a;
      }
      manageCovers(now);
      if (!state) return;
      if (geometry > 0) Core.fractalWarp(state.mesh, inputs, geometry, calm);
      let sceneOn = false;
      if (scene) {
        try {
          if (
            scene.update(
              Math.round(canvas.width * SCENE_SCALE),
              Math.round(canvas.height * SCENE_SCALE),
            )
          ) {
            if (!gfx.sceneTex) gfx.sceneTex = texture(null, 1, 1);
            bind(3, gfx.sceneTex);
            gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, scene.canvas);
            gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
            sceneOn = true;
          }
        } catch (e) {
          scene = null;
          error = 'The page scene stopped: ' + String(e.message || e);
        }
      }
      if (!sceneOn) bind(3, gfx.blank);
      const eff = covers * (0.25 + 0.75 * state.color.cover);

      const read = gfx.targets[gfx.read],
        write = gfx.targets[1 - gfx.read],
        W = write.w,
        H = write.h;
      gl.bindFramebuffer(gl.FRAMEBUFFER, write.fbo);
      gl.viewport(0, 0, W, H);
      gl.disable(gl.BLEND);
      const wp = gfx.warp;
      gl.useProgram(wp.p);
      gl.bindBuffer(gl.ARRAY_BUFFER, gfx.meshPos);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, gfx.meshUv);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, state.mesh);
      gl.enableVertexAttribArray(1);
      gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 0, 0);
      bind(0, read.tex);
      gl.uniform1i(wp.u.uPrev, 0);
      coverUniforms(wp.u);
      gl.uniform2f(wp.u.uRes, W, H);
      gl.uniform1i(wp.u.uScene, 3);
      gl.uniform1f(wp.u.uSceneAmt, sceneOn ? SCENE_AMT : 0);
      gl.uniform1f(wp.u.uDecay, state.decay);
      gl.uniform1f(wp.u.uFade, 0.6 / 255);
      // scaled by the fade, so the cover settles at a fixed brightness however long the trails are
      gl.uniform1f(wp.u.uInject, (1 - state.decay) * eff);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gfx.idxBuf);
      gl.drawElements(gl.TRIANGLES, gfx.indexCount, gl.UNSIGNED_SHORT, 0);

      if (state.draws.length) {
        gl.useProgram(gfx.fill.p);
        gl.enableVertexAttribArray(0);
        gl.enableVertexAttribArray(1);
        gl.enable(gl.BLEND);
        for (const d of state.draws) drawFill(d, W);
        gl.disable(gl.BLEND);
      }

      if (frames % 6 === 1) {
        gl.disableVertexAttribArray(1);
        gl.bindFramebuffer(gl.FRAMEBUFFER, gfx.small.fbo);
        gl.viewport(0, 0, 16, 9);
        gl.useProgram(gfx.measure.p);
        gl.bindBuffer(gl.ARRAY_BUFFER, gfx.triBuf);
        gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
        bind(0, write.tex);
        gl.uniform1i(gfx.measure.u.uTex, 0);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        gl.readPixels(0, 0, 16, 9, gl.RGBA, gl.UNSIGNED_BYTE, gfx.pixels);
        let sum = 0;
        for (let i = 0; i < gfx.pixels.length; i += 4) sum += gfx.pixels[i];
        gain = guard.feed(sum / (16 * 9) / 255, now);
      }

      gl.disableVertexAttribArray(1);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, canvas.width, canvas.height);
      const cp = gfx.composite,
        c = state.color;
      gl.useProgram(cp.p);
      gl.bindBuffer(gl.ARRAY_BUFFER, gfx.triBuf);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      bind(0, write.tex);
      gl.uniform1i(cp.u.uTex, 0);
      coverUniforms(cp.u);
      gl.uniform2f(cp.u.uRes, canvas.width, canvas.height);
      gl.uniform1i(cp.u.uScene, 3);
      gl.uniform1f(cp.u.uSceneDirect, sceneOn ? SCENE_DIRECT * (calm ? 0.6 : 1) : 0);
      gl.uniform1f(cp.u.uWobble, (0.004 + 0.016 * features.bass) * (calm ? 0.4 : 1));
      gl.uniform1f(cp.u.uClock, inputs.time);
      gl.uniform1f(cp.u.uGamma, c.gamma);
      gl.uniform1f(cp.u.uHue, c.hue);
      gl.uniform1f(cp.u.uBright, c.bright * gain);
      gl.uniform1f(cp.u.uSolar, c.solarize);
      gl.uniform1f(cp.u.uEchoA, c.echoAlpha);
      gl.uniform1f(cp.u.uEchoZ, c.echoZoom);
      gl.uniform1f(cp.u.uEchoO, c.echoOrient);
      gl.uniform1f(cp.u.uCoverAmt, 0.35 * eff);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gfx.read = 1 - gfx.read;
    }

    function onVisibility() {
      // a hidden tab draws nothing and wakes up cleanly
      if (document.hidden) {
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
      } else if (running && !raf && !lost) {
        last = performance.now();
        raf = requestAnimationFrame(frame);
      }
    }
    document.addEventListener('visibilitychange', onVisibility);
    canvas.addEventListener('webglcontextlost', e => {
      e.preventDefault();
      lost = true;
      gfx = null;
      coverA = coverB = null;
      loading = false;
      generation++;
    });
    canvas.addEventListener('webglcontextrestored', () => {
      try {
        init();
        lost = false;
        coverMix = 0;
        nextCoverAt = 0;
        const keep = currentName();
        cur = null;
        fade = null;
        if (keep) setPreset(keep, { instant: true, quiet: true });
        if (running && !raf) raf = requestAnimationFrame(frame);
      } catch (e) {
        error = String(e.message || e);
      }
    });

    try {
      init();
    } catch (e) {
      console.warn('FalseHorizon unavailable:', e.message || e);
      return unsupported(
        'The visuals could not start on this device (' + String(e.message || e).slice(0, 80) + ').',
      );
    }

    return {
      supported: true,
      reason: '',
      get running() {
        return running;
      },
      get error() {
        return error;
      },
      start() {
        if (running) return;
        running = true;
        last = performance.now();
        guard.last = null;
        if (!cur && !lost) step(1);
        if (!raf && !lost && !document.hidden) raf = requestAnimationFrame(frame);
      },
      stop() {
        running = false;
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
      },
      destroy() {
        this.stop();
        document.removeEventListener('visibilitychange', onVisibility);
        const ext = gl.getExtension && gl.getExtension('WEBGL_lose_context');
        if (ext) ext.loseContext();
      },
      setAnalyser(a) {
        analyser = a;
        freqBytes = null;
        timeBytes = null;
      },
      setSources(urls) {
        sources = urls.slice();
      },
      prioritise(url) {
        priority = url || '';
        nextCoverAt = 0;
      }, // the next picture to appear, e.g. the cover of the song now playing
      setMaxFps(n) {
        maxFps = n > 0 ? Math.min(60, Math.max(10, n)) : 0;
      },
      setIntensity(percent) {
        intensity = Math.min(1, Math.max(0, percent / 100));
      },
      setScene(source) {
        scene = source && source.canvas && source.update ? source : null;
      }, // { canvas, update(w, h) -> true when the canvas is ready to use }
      get hasScene() {
        return !!scene;
      },
      setGeometry(percent) {
        geometry = Math.min(1, Math.max(0, percent / 100));
      },
      setCovers(percent) {
        covers = Math.min(1, Math.max(0, percent / 100));
      },
      setCalm(on) {
        calm = !!on;
        guard.setCalm(calm);
      },
      setAuto(on, seconds) {
        auto = !!on;
        if (seconds) cycleMs = Math.min(300, Math.max(10, seconds)) * 1000;
      },
      setUserPresets(list) {
        userPresets = list.slice();
      },
      setFavorites(list) {
        favorites = list.slice();
      },
      lock(on) {
        locked = !!on;
      },
      get locked() {
        return locked;
      },
      setPreset: (which, opts) => setPreset(which, opts),
      next: () => step(1),
      prev: () => step(-1),
      onPreset(fn) {
        listener = fn;
      },
      list: () => all().map(p => ({ name: p.name, builtin: Core.builtins().includes(p) })),
      current: currentName,
      inspect: () => ({
        supported: true,
        running,
        preset: currentName(),
        fading: !!fade,
        width: canvas.width,
        height: canvas.height,
        scale,
        gain,
        frames,
        locked,
        auto,
        hasCover: !!coverA,
        hasScene: !!scene,
        error,
      }),
    };
  }

  window.FalseHorizon = { create };
})();
