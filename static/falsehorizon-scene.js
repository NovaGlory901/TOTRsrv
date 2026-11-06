// FalseHorizon's scene: the radio page's vaporwave backdrop (turning colour sky, twinkling stars,
// scrolling grid floor) and its collage (covers, pictures, video, drifting words, little windows,
// glowing orbs) drawn onto a 2D canvas every frame, so the visualizer can use it as an ingredient:
// it is stamped into the feedback loop and gets warped, smeared and torn with everything else,
// instead of the visuals being laid over a finished page. The DOM elements stay where they are
// (hidden while the visuals are on) and keep running their own animations; this only reads their
// size, position, opacity and transform. Everything drawn comes from the page itself (same-origin
// pictures, text set with textContent), nothing is fetched here.
(function (root) {
  const BLEND = {
    screen: 'screen',
    lighten: 'lighten',
    'soft-light': 'soft-light',
    overlay: 'overlay',
    difference: 'difference',
  };
  const MAX_LAYERS = 40; // the page keeps about a dozen; this is only a ceiling for a broken page

  function create(host, opts) {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const theme = {
      border: '#335',
      pink: '255,120,200',
      cyan: '120,240,255',
      lilac: '200,160,255',
      bg2: '20,10,40',
      fg: '#fff',
      onAccent: '#102',
    };
    let themeAt = -1e9,
      failures = 0;
    const layers = []; // reused every frame

    function readTheme(now) {
      // colours come from the page's theme variables; looked up about once a second
      if (now - themeAt < 1000) return;
      themeAt = now;
      const cs = getComputedStyle(document.documentElement),
        get = (n, d) => (cs.getPropertyValue(n) || '').trim() || d;
      theme.border = get('--border', theme.border);
      theme.pink = get('--pink-rgb', theme.pink);
      theme.cyan = get('--cyan-rgb', theme.cyan);
      theme.lilac = get('--lilac-rgb', theme.lilac);
      theme.bg2 = get('--bg2-rgb', theme.bg2);
      theme.fg = get('--fg', theme.fg);
      theme.onAccent = get('--on-accent', theme.onAccent);
    }

    // The page's background, redrawn with the same ingredients as the CSS (the CSS one is hidden
    // under the visuals): a slowly turning colour wheel, a field of twinkling stars and a
    // perspective grid that scrolls toward you.
    const stars = Array.from({ length: 70 }, () => ({
      x: Math.random(),
      y: Math.random() * 0.75,
      p: Math.random() * 6.28,
      r: 0.6 + Math.random() * 1.2,
    }));
    function drawBackdrop(w, h, t) {
      ctx.save();
      ctx.globalAlpha = 0.55;
      if (ctx.createConicGradient) {
        const g = ctx.createConicGradient(t * 0.05, w / 2, h * 0.6);
        g.addColorStop(0, theme.border);
        g.addColorStop(0.25, `rgb(${theme.pink})`);
        g.addColorStop(0.5, `rgb(${theme.cyan})`);
        g.addColorStop(0.75, `rgb(${theme.lilac})`);
        g.addColorStop(1, theme.border);
        ctx.fillStyle = g;
      } else {
        const g = ctx.createRadialGradient(w / 2, h * 0.6, 0, w / 2, h * 0.6, w * 0.7);
        g.addColorStop(0, `rgb(${theme.pink})`);
        g.addColorStop(0.5, `rgb(${theme.lilac})`);
        g.addColorStop(1, `rgb(${theme.cyan})`);
        ctx.fillStyle = g;
      }
      ctx.fillRect(0, 0, w, h);
      ctx.globalAlpha = 1;
      ctx.fillStyle = '#fff';
      for (const s of stars) {
        ctx.globalAlpha = 0.35 + 0.55 * (0.5 + 0.5 * Math.sin(t * 1.2 + s.p));
        ctx.fillRect(s.x * w, s.y * h, s.r, s.r);
      }
      const horizon = h * 0.55,
        scroll = (t / 3) % 1; // the floor: lines that spread out and rush toward the bottom edge
      ctx.lineWidth = 1;
      ctx.strokeStyle = `rgba(${theme.cyan},.45)`;
      ctx.globalAlpha = 1;
      ctx.beginPath();
      for (let i = 0; i < 12; i++) {
        const z = (i + scroll) / 12,
          y = horizon + (h - horizon) * z * z;
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
      }
      ctx.stroke();
      ctx.strokeStyle = `rgba(${theme.pink},.45)`;
      ctx.beginPath();
      for (let i = -12; i <= 12; i++) {
        ctx.moveTo(w / 2 + i * w * 0.012, horizon);
        ctx.lineTo(w / 2 + i * w * 0.14, h);
      }
      ctx.stroke();
      ctx.restore();
    }

    // The picture fills its box without stretching (the CSS says object-fit: cover).
    function drawCover(el, w, h) {
      const nw = el.naturalWidth || el.videoWidth,
        nh = el.naturalHeight || el.videoHeight;
      if (!nw || !nh || (el.tagName === 'IMG' && !el.complete)) return;
      if (el.tagName === 'VIDEO' && el.readyState < 2) return;
      const k = Math.max(w / nw, h / nh),
        sw = w / k,
        sh = h / k;
      ctx.drawImage(el, (nw - sw) / 2, (nh - sh) / 2, sw, sh, 0, 0, w, h);
    }

    function drawOrb(el, w, h) {
      const hue = Number(el.dataset.hue) || 280,
        r = w / 2;
      const g = ctx.createRadialGradient(r, h / 2, 0, r, h / 2, r);
      g.addColorStop(0, `hsla(${hue},95%,78%,.9)`);
      g.addColorStop(0.68, `hsla(${hue + 40},90%,70%,0)`);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
    }

    function drawPhrase(el, w, h, cs) {
      const text = el.textContent,
        size = parseFloat(cs.fontSize) || 32;
      ctx.font = `${size}px ${cs.fontFamily}`;
      ctx.textBaseline = 'top';
      const draw = (dx, color) => {
        ctx.fillStyle = color;
        if (el.classList.contains('v')) {
          let y = 0;
          for (const ch of text) {
            ctx.fillText(ch, dx + (w - ctx.measureText(ch).width) / 2, y);
            y += size * 1.05;
          }
        } else ctx.fillText(text, dx, 0);
      };
      draw(-3, `rgba(${theme.pink},.8)`);
      draw(3, `rgba(${theme.cyan},.8)`);
      draw(0, '#fff');
    }

    function drawWindow(el, w, h, cs) {
      ctx.fillStyle = `rgba(${theme.bg2},.8)`;
      ctx.fillRect(0, 0, w, h);
      const bar = el.querySelector('.dbar');
      if (bar) {
        const g = ctx.createLinearGradient(0, 0, w, 0);
        g.addColorStop(0, `rgb(${theme.pink})`);
        g.addColorStop(1, `rgb(${theme.cyan})`);
        ctx.fillStyle = g;
        ctx.fillRect(0, bar.offsetTop, w, bar.offsetHeight);
      }
      ctx.strokeStyle = `rgb(${theme.lilac})`;
      ctx.lineWidth = 2;
      ctx.strokeRect(1, 1, w - 2, h - 2);
      ctx.font = `${parseFloat(cs.fontSize) || 20}px ${cs.fontFamily}`;
      ctx.textBaseline = 'top';
      el.querySelectorAll('.dbar span, .dbody div').forEach(t => {
        ctx.fillStyle = t.closest('.dbar') ? theme.onAccent : theme.fg;
        ctx.textAlign = t.closest('.dbar') && t.nextSibling === null ? 'right' : 'left';
        ctx.fillText(
          t.textContent,
          ctx.textAlign === 'right' ? w - 8 : t.offsetLeft,
          t.offsetTop,
          w - 16,
        );
      });
      ctx.textAlign = 'left';
    }

    function drawLayer(el, k) {
      const cs = getComputedStyle(el),
        alpha = parseFloat(cs.opacity);
      const w = el.offsetWidth,
        h = el.offsetHeight;
      if (!(alpha > 0.01) || !w || !h) return;
      ctx.save();
      ctx.globalAlpha = Math.min(1, alpha);
      ctx.globalCompositeOperation = BLEND[cs.mixBlendMode] || 'source-over';
      const m =
        cs.transform && cs.transform !== 'none' ? new DOMMatrix(cs.transform) : new DOMMatrix();
      ctx.setTransform(k, 0, 0, k, 0, 0);
      // the CSS turns things around their middle
      ctx.translate(el.offsetLeft + w / 2, el.offsetTop + h / 2);
      ctx.transform(m.a, m.b, m.c, m.d, m.e, m.f);
      ctx.translate(-w / 2, -h / 2);
      // hue and saturation shifts are kept; blur is too slow to repeat every frame
      const tint = (cs.filter || '').replace(/blur\([^)]*\)/g, '').trim();
      if (tint && tint !== 'none' && 'filter' in ctx) ctx.filter = tint;
      if (el.tagName === 'IMG' || el.tagName === 'VIDEO') drawCover(el, w, h);
      else if (el.classList.contains('orb')) drawOrb(el, w, h);
      else if (el.classList.contains('phrase')) drawPhrase(el, w, h, cs);
      else if (el.classList.contains('dwin')) drawWindow(el, w, h, cs);
      ctx.restore();
    }

    // Draw everything for this frame into a canvas w x h. Returns true when the canvas is fit to
    // use.
    function update(w, h) {
      try {
        if (canvas.width !== w || canvas.height !== h) {
          canvas.width = w;
          canvas.height = h;
        }
        readTheme(performance.now());
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = 'source-over';
        ctx.filter = 'none';
        ctx.clearRect(0, 0, w, h);
        drawBackdrop(w, h, performance.now() / 1000);
        const box = host.clientWidth;
        if (!box) return true; // nothing laid out yet: just the backdrop
        const k = w / box;
        layers.length = 0;
        for (let i = 0; i < host.children.length && i < MAX_LAYERS; i++)
          layers.push(host.children[i]);
        layers.sort((a, b) => (Number(a.style.zIndex) || 0) - (Number(b.style.zIndex) || 0));
        for (const el of layers) drawLayer(el, k);
        failures = 0;
        return true;
      } catch (e) {
        if (++failures >= 3 && opts && opts.onFail) opts.onFail(e);
        return false;
      }
    }

    return { canvas, update };
  }

  root.FalseHorizonScene = { create };
})(window);
