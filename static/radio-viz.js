// The radio's visualizer: album covers and Dream-folder pictures run through a feedback loop that
// smears, warps and glitches them to the music (WebGL, drawn over the collage; black is see-through
// so the collage still shows).
//
// How it works, in the order the shader does it: 1. The picture is split into 28px blocks. Each
// block has its own "motion vector", re-rolled on every beat, and drags the PREVIOUS frame along
// it. That is the datamosh look: old frames smearing instead of being replaced. 2. The previous
// frame is also warped (sine ripples), slowly zoomed/twisted, split into red/green/blue (colour
// bleed) and blended with itself (frame bleed). Bass moves blocks, mids ripple, treble splits
// colour. 3. The fresh picture is displaced by its own brightness, and on a beat many blocks drop
// the smear and take the fresh picture again (a "keyframe"), while a few horizontal bands tear
// sideways (the glitch). 4. The hue drifts a little every frame (and faster with the mids), so old
// smears slowly change colour. Finally a sharpening pass (stronger on bass) and faint scanlines.
// Safety: effects fire at most ~3 times a second, only some blocks refresh (never a full-screen
// flash), and the
// calm mode (for prefers-reduced-motion) shrinks all motion, drops tears and runs at 24 frames a
// second. Needs window.RadioCore (radio-core.js). Everything it loads is same-origin.
(function () {
  const PRECISION =
    '#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n';

  const VERT =
    'attribute vec2 aPos; varying vec2 vUv; void main() { vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }';

  const FEEDBACK =
    PRECISION +
    `
  varying vec2 vUv;
  uniform sampler2D uPrev, uClipA, uClipB;
  uniform vec2 uRes, uSizeA, uSizeB;
  uniform float uTime, uBass, uMid, uTreble, uLevel, uKey, uSeed, uBlend, uStrength, uCalm;

  float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  float lum(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }
  vec2 cover(vec2 uv, vec2 size) {               // fill the frame without stretching the picture
    float ca = uRes.x / uRes.y, ia = size.x / size.y;
    vec2 c = uv - 0.5;
    if (ca > ia) c.y *= ia / ca; else c.x *= ca / ia;
    return c + 0.5;
  }

  void main() {
    float s = uStrength;
    float calm = 1.0 - 0.8 * uCalm;
    vec2 cell = floor(gl_FragCoord.xy / 28.0);
    vec2 mv = vec2(hash(cell + uSeed) - 0.5, hash(cell * 1.31 + uSeed + 7.0) - 0.5);
    vec2 move = mv * (0.004 + 0.05 * uBass) * s * calm;

    vec2 uv = vUv;
    uv += vec2(sin(vUv.y * 11.0 + uTime * 0.6), cos(vUv.x * 9.0 + uTime * 0.45)) * (0.0015 + 0.010 * uMid) * s * calm;
    vec2 c = uv - 0.5;
    float ang = (0.003 + 0.012 * uLevel) * s * calm * sin(uTime * 0.3);
    c = mat2(cos(ang), -sin(ang), sin(ang), cos(ang)) * c * (1.0 - (0.003 + 0.012 * uBass) * s * calm);
    uv = c + 0.5 + move;

    vec2 off = vec2(0.0015 + 0.012 * uTreble, 0.0) * s * calm;
    vec3 prev;
    prev.r = texture2D(uPrev, uv + off).r;
    prev.g = texture2D(uPrev, uv).g;
    prev.b = texture2D(uPrev, uv - off).b;
    prev = mix(prev, texture2D(uPrev, uv + move * 2.0).rgb, 0.5);

    float d = lum(texture2D(uClipA, cover(vUv, uSizeA)).rgb) - 0.5;
    vec2 disp = vec2(d, -d) * (0.01 + 0.04 * (uBass + uMid)) * s * calm;
    vec3 a = texture2D(uClipA, cover(vUv + disp, uSizeA)).rgb;
    vec3 b = texture2D(uClipB, cover(vUv + disp, uSizeB)).rgb;
    vec3 fresh = mix(a, b, step(hash(cell * 3.7 + 11.0), uBlend));

    float refresh = step(hash(cell * 1.7 + uSeed * 3.0 + 2.0), 0.012 + 0.8 * uKey * s);
    vec3 col = mix(prev * 0.982, fresh, max(refresh, 0.035));

    float band = floor(vUv.y * 22.0);
    float tear = step(0.88, hash(vec2(band, uSeed))) * uKey * s * (1.0 - uCalm);
    col = mix(col, texture2D(uPrev, uv + vec2((hash(vec2(band, uSeed + 1.0)) - 0.5) * 0.25, 0.0)).rgb, tear);

    // a small turn every frame: it builds up through the feedback loop
    float h = (0.004 + 0.02 * uMid) * s * calm;
    vec3 k = vec3(0.57735);
    float cs = cos(h), sn = sin(h);
    col = col * cs + cross(k, col) * sn + k * dot(k, col) * (1.0 - cs);
    gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
  }`;

  const SCREEN =
    PRECISION +
    `
  varying vec2 vUv;
  uniform sampler2D uTex;
  uniform vec2 uRes;
  uniform float uBass, uStrength;
  void main() {
    vec2 px = 1.0 / uRes;
    vec3 c = texture2D(uTex, vUv).rgb;
    vec3 n = texture2D(uTex, vUv + vec2(0.0, px.y)).rgb + texture2D(uTex, vUv - vec2(0.0, px.y)).rgb
           + texture2D(uTex, vUv + vec2(px.x, 0.0)).rgb + texture2D(uTex, vUv - vec2(px.x, 0.0)).rgb;
    float a = (0.35 + 0.6 * uBass) * uStrength;
    vec3 sharp = c * (1.0 + 4.0 * a) - n * a;
    float scan = 0.94 + 0.06 * sin(vUv.y * uRes.y * 3.14159);
    gl_FragColor = vec4(clamp(sharp * scan, 0.0, 1.0), 1.0);
  }`;

  const MAX_CLIP_SIDE = 1024; // bigger pictures are shrunk before they become textures
  const rnd = (a, b) => a + Math.random() * (b - a);

  function unsupported(reason) {
    const nop = () => {};
    return {
      supported: false,
      reason,
      running: false,
      start: nop,
      stop: nop,
      setSources: nop,
      prioritise: nop,
      setIntensity: nop,
      setCalm: nop,
      setAnalyser: nop,
    };
  }

  function create(canvas) {
    const Core = window.RadioCore;
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

    let gfx = null; // everything that lives on the GPU, rebuilt after a lost context
    let analyser = null,
      bytes = null,
      running = false,
      raf = 0,
      lost = false;
    let sources = [],
      priority = '',
      intensity = 0.7,
      calm = false;
    let clipA = null,
      clipB = null,
      blend = 0,
      blendStart = 0,
      nextClipAt = 0,
      loading = false,
      generation = 0;
    let bass = 0,
      mid = 0,
      treble = 0,
      level = 0,
      key = 1,
      seed = 1,
      lastSeed = 0,
      last = 0,
      t0 = performance.now();
    const beats = new Core.BeatDetector();
    let error = '';

    function compile(type, src) {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS))
        throw new Error(gl.getShaderInfoLog(sh) || 'shader failed to compile');
      return sh;
    }
    function program(frag) {
      const p = gl.createProgram();
      gl.attachShader(p, compile(gl.VERTEX_SHADER, VERT));
      gl.attachShader(p, compile(gl.FRAGMENT_SHADER, frag));
      gl.bindAttribLocation(p, 0, 'aPos');
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
    function texture(w, h, source) {
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      if (source) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      return t;
    }
    function target(w, h) {
      const tex = texture(w, h, null),
        fbo = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
      gl.viewport(0, 0, w, h);
      gl.clearColor(0, 0, 0, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      return { tex, fbo, w, h };
    }

    function init() {
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      // one big triangle
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
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
      gfx = { feedback: program(FEEDBACK), screen: program(SCREEN), blank, targets: null, read: 0 };
      sizeTargets(true);
    }
    function sizeTargets(force) {
      const cssW = canvas.clientWidth || 640,
        cssH = canvas.clientHeight || 360;
      const w = Math.max(160, Math.min(960, Math.round(cssW * 0.6))),
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
      key = 1;
    }

    function loadClip(url, then) {
      const mine = generation,
        img = new Image();
      img.decoding = 'async';
      img.onload = () => {
        if (mine !== generation || lost || !gfx) return;
        const k = Math.min(1, MAX_CLIP_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
        const w = Math.max(1, Math.round(img.naturalWidth * k)),
          h = Math.max(1, Math.round(img.naturalHeight * k));
        const c = document.createElement('canvas');
        c.width = w;
        c.height = h;
        c.getContext('2d').drawImage(img, 0, 0, w, h);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        const tex = texture(w, h, c);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        then({ tex, w, h, url });
      };
      img.onerror = () => {
        loading = false;
        nextClipAt = performance.now() + 1500;
      }; // a 404 just means: try another
      img.src = url;
    }
    const drop = clip => {
      if (clip && gfx) gl.deleteTexture(clip.tex);
    };
    function pickUrl() {
      if (priority) {
        const u = priority;
        priority = '';
        return u;
      }
      if (!sources.length) return '';
      return sources[Math.floor(Math.random() * sources.length)];
    }
    function manageClips(now) {
      if (blend > 0 && clipB) {
        // finish a crossfade
        blend = Math.min(1, (now - blendStart) / 2600);
        if (blend >= 1) {
          drop(clipA);
          clipA = clipB;
          clipB = null;
          blend = 0;
          nextClipAt = now + rnd(5000, 9000);
        }
      }
      if (loading || clipB || now < nextClipAt) return;
      const url = pickUrl();
      if (!url) return;
      loading = true;
      loadClip(url, clip => {
        loading = false;
        if (!clipA) {
          clipA = clip;
          nextClipAt = now + rnd(5000, 9000);
          key = 1;
          return;
        }
        clipB = clip;
        blend = 0.001;
        blendStart = performance.now();
      });
    }

    function frame(now) {
      raf = 0;
      if (!running || lost || !gfx) return;
      raf = requestAnimationFrame(frame);
      if (calm && now - last < 1000 / 24) return;
      const dt = Math.min(0.1, (now - last) / 1000 || 0.016);
      last = now;
      sizeTargets(false);

      if (analyser) {
        if (!bytes || bytes.length !== analyser.frequencyBinCount)
          bytes = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(bytes);
        const e = Core.bandEnergies(bytes, analyser.context.sampleRate);
        bass = Core.follow(bass, e.bass);
        mid = Core.follow(mid, e.mid);
        treble = Core.follow(treble, e.treble);
        level = Core.follow(level, e.level);
        if (beats.update(e.bass, now)) {
          key = calm ? 0.3 : 1;
          seed = Math.random() * 100;
          lastSeed = now;
        }
      }
      if (now - lastSeed > 1400) {
        seed = Math.random() * 100;
        lastSeed = now;
      } // quiet passages keep drifting
      key = Math.max(0, key - dt / 0.6);
      manageClips(now);

      const f = gfx.feedback,
        W = gfx.targets[0].w,
        H = gfx.targets[0].h;
      const read = gfx.targets[gfx.read],
        write = gfx.targets[1 - gfx.read];
      gl.bindFramebuffer(gl.FRAMEBUFFER, write.fbo);
      gl.viewport(0, 0, W, H);
      gl.useProgram(f.p);
      const bind = (unit, tex) => {
        gl.activeTexture(gl.TEXTURE0 + unit);
        gl.bindTexture(gl.TEXTURE_2D, tex);
      };
      bind(0, read.tex);
      bind(1, clipA ? clipA.tex : gfx.blank);
      bind(2, clipB ? clipB.tex : clipA ? clipA.tex : gfx.blank);
      gl.uniform1i(f.u.uPrev, 0);
      gl.uniform1i(f.u.uClipA, 1);
      gl.uniform1i(f.u.uClipB, 2);
      gl.uniform2f(f.u.uRes, W, H);
      gl.uniform2f(f.u.uSizeA, clipA ? clipA.w : 1, clipA ? clipA.h : 1);
      gl.uniform2f(
        f.u.uSizeB,
        clipB ? clipB.w : clipA ? clipA.w : 1,
        clipB ? clipB.h : clipA ? clipA.h : 1,
      );
      gl.uniform1f(f.u.uTime, (now - t0) / 1000);
      gl.uniform1f(f.u.uBass, bass);
      gl.uniform1f(f.u.uMid, mid);
      gl.uniform1f(f.u.uTreble, treble);
      gl.uniform1f(f.u.uLevel, level);
      gl.uniform1f(f.u.uKey, key);
      gl.uniform1f(f.u.uSeed, seed);
      gl.uniform1f(f.u.uBlend, clipB ? blend : 0);
      gl.uniform1f(f.u.uStrength, intensity * (calm ? 0.6 : 1));
      gl.uniform1f(f.u.uCalm, calm ? 1 : 0);
      gl.drawArrays(gl.TRIANGLES, 0, 3);

      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, canvas.width, canvas.height);
      const s = gfx.screen;
      gl.useProgram(s.p);
      bind(0, write.tex);
      gl.uniform1i(s.u.uTex, 0);
      gl.uniform2f(s.u.uRes, W, H);
      gl.uniform1f(s.u.uBass, bass);
      gl.uniform1f(s.u.uStrength, intensity);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gfx.read = 1 - gfx.read;
    }

    canvas.addEventListener('webglcontextlost', e => {
      e.preventDefault();
      lost = true;
      gfx = null;
      clipA = clipB = null;
      loading = false;
      generation++;
    });
    canvas.addEventListener('webglcontextrestored', () => {
      try {
        init();
        lost = false;
        blend = 0;
        nextClipAt = 0;
        if (running && !raf) raf = requestAnimationFrame(frame);
      } catch (e) {
        error = String(e.message || e);
      }
    });

    try {
      init();
    } catch (e) {
      console.warn('radio visuals unavailable:', e.message || e);
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
        key = 1;
        if (!raf && !lost) raf = requestAnimationFrame(frame);
      },
      stop() {
        running = false;
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
      },
      setAnalyser(a) {
        analyser = a;
        bytes = null;
      },
      setSources(urls) {
        sources = urls.slice();
      },
      prioritise(url) {
        priority = url || '';
      }, // the next picture to appear, e.g. the cover of the song now playing
      setIntensity(percent) {
        intensity = Math.min(1, Math.max(0, percent / 100));
      },
      setCalm(on) {
        calm = !!on;
      },
    };
  }

  window.RadioViz = { create };
})();
