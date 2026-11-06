// Reads a community MilkDrop preset (a ".milk" text file) and turns it into a FalseHorizon preset.
// None are bundled: you bring your own files, at your own risk. Translated: base numbers, init /
// per-frame / per-pixel formulas, up to two custom waveforms and four shapes. Not translated: loops
// and memory arrays (the preset is refused, since FalseHorizon's language has neither and must
// always finish); custom GPU shaders are ignored, so many presets look simpler; textured shapes are
// drawn plain; borders, motion vectors and invert/brighten/darken are dropped. The result is an
// approximation and a note says so. The file is untrusted text: size-limited, known keys only, and
// every formula goes through FalseHorizonCore.sanitizePreset. Runs under Node too; in the browser
// it is window.FalseHorizonMilk.
(function (root) {
  const Core =
    typeof require !== 'undefined' ? require('./falsehorizon-core.js') : root.FalseHorizonCore;

  const MAX_TEXT = 100 * 1024,
    MAX_LINES = 6000,
    MAX_LINE = 8000,
    HEAR = 1.8;
  // loops and memory arrays
  const UNSUPPORTED = /\b(megabuf|gmegabuf|loop|while|exec2|exec3|memcpy|assign)\s*\(/i;
  // single & and | (and/or on bits) are not supported either
  const BITWISE = /(?<![&|])[&|](?![&|])/;
  const KNOWN =
    /^(milkdrop_preset_version|fdecay|zoom|rot|per_frame_\d+|per_pixel_\d+|per_frame_init_\d+|nwavemode|fwavealpha|fgammaadj|fvideoechoalpha)$/;
  // MilkDrop's eight built-in waveform shapes -> our five (circle, line, spectrum spokes, spectrum
  // line, two lines): the closest look
  const WAVE_MODE = [0, 0, 0, 0, 1, 0, 1, 4];

  const clean = text =>
    String(text)
      .replace(/[\u0000-\u001f\u007f]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

  // "key=value" lines, keys lowercased. Section headers ([preset00]) and anything that is not
  // key=value are skipped.
  function readLines(text) {
    const map = new Map();
    const lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length && i < MAX_LINES; i++) {
      const at = lines[i].indexOf('=');
      if (at < 1) continue;
      const key = lines[i].slice(0, at).trim().toLowerCase();
      if (!/^[a-z0-9_]{1,64}$/.test(key)) continue;
      map.set(key, lines[i].slice(at + 1, at + 1 + MAX_LINE));
    }
    return map;
  }

  // per_frame_1=..., per_frame_2=... joined in number order, with // and /* */ comments removed
  function code(map, base) {
    const re = new RegExp('^' + base + '(\\d+)$'),
      parts = [];
    for (const [key, value] of map) {
      const m = re.exec(key);
      if (m) parts.push([Number(m[1]), value]);
    }
    parts.sort((a, b) => a[0] - b[0]);
    return parts
      .slice(0, 999)
      .map(p => p[1].replace(/\/\*.*?\*\//g, ' ').replace(/\/\/.*$/, ' '))
      .join(' ')
      .trim();
  }

  function convert(textIn, fileName) {
    if (typeof textIn !== 'string' || !textIn.trim())
      return { ok: false, error: 'That file is empty.' };
    if (textIn.length > MAX_TEXT)
      return {
        ok: false,
        error: 'That file is too big to be a MilkDrop preset (the limit is 100 KB).',
      };
    if (textIn.includes('\u0000'))
      return { ok: false, error: 'That is not a text file, so it is not a MilkDrop preset.' };
    const map = readLines(textIn);
    if (![...map.keys()].some(k => KNOWN.test(k)))
      return { ok: false, error: 'That does not look like a MilkDrop preset (.milk file).' };
    const num = (key, fallback) => {
      const x = parseFloat(map.get(key));
      return Number.isFinite(x) ? x : fallback;
    };
    const on = key => num(key, 0) >= 1;
    const notes = [
      'Translated from a MilkDrop preset: it will not look exactly the same as it does there.',
    ];

    const pieces = {
      init: code(map, 'per_frame_init_'),
      frame: code(map, 'per_frame_'),
      pixel: code(map, 'per_pixel_'),
    };
    const waves = [],
      shapes = [];
    let extraWaves = 0,
      extraShapes = 0,
      textured = false;
    for (let i = 0; i < 4; i++) {
      if (on(`wavecode_${i}_enabled`)) {
        if (waves.length >= 2) extraWaves++;
        else
          waves.push({
            samples: Math.round(num(`wavecode_${i}_samples`, 128)),
            r: num(`wavecode_${i}_r`, 1),
            g: num(`wavecode_${i}_g`, 1),
            b: num(`wavecode_${i}_b`, 1),
            a: num(`wavecode_${i}_a`, 1),
            additive: on(`wavecode_${i}_badditive`),
            dots: on(`wavecode_${i}_busedots`),
            thick: on(`wavecode_${i}_bdrawthick`) ? 2 : 1,
            spectrum: on(`wavecode_${i}_bspectrum`),
            init: code(map, `wave_${i}_init`),
            frame: code(map, `wave_${i}_per_frame`),
            point: code(map, `wave_${i}_per_point`),
          });
      }
      if (on(`shapecode_${i}_enabled`)) {
        textured = textured || on(`shapecode_${i}_textured`);
        if (shapes.length >= 4) extraShapes++;
        else
          shapes.push({
            sides: Math.round(num(`shapecode_${i}_sides`, 4)),
            x: num(`shapecode_${i}_x`, 0.5),
            y: num(`shapecode_${i}_y`, 0.5),
            rad: num(`shapecode_${i}_rad`, 0.1),
            ang: num(`shapecode_${i}_ang`, 0),
            r: num(`shapecode_${i}_r`, 1),
            g: num(`shapecode_${i}_g`, 0),
            b: num(`shapecode_${i}_b`, 0),
            a: num(`shapecode_${i}_a`, 1),
            r2: num(`shapecode_${i}_r2`, 0),
            g2: num(`shapecode_${i}_g2`, 1),
            b2: num(`shapecode_${i}_b2`, 0),
            a2: num(`shapecode_${i}_a2`, 0),
            additive: on(`shapecode_${i}_additive`),
            instances: Math.round(num(`shapecode_${i}_num_inst`, 1)),
            init: code(map, `shape_${i}_init`),
            frame: code(map, `shape_${i}_per_frame`),
          });
      }
    }

    // what FalseHorizon cannot run: refuse, with the reason, rather than guess
    const all = [
      pieces.init,
      pieces.frame,
      pieces.pixel,
      ...waves.flatMap(w => [w.init, w.frame, w.point]),
      ...shapes.flatMap(s => [s.init, s.frame]),
    ].join(' ');
    const bad = UNSUPPORTED.exec(all);
    if (bad)
      return {
        ok: false,
        error: `This preset uses ${bad[1].toLowerCase()}(), a loop or memory-array feature that FalseHorizon does not have (and must not, so a preset can never run forever).`,
      };
    if (BITWISE.test(all))
      return {
        ok: false,
        error:
          'This preset uses bitwise operators (a single & or |), which FalseHorizon does not support.',
      };

    if ([...map.keys()].some(k => /^(warp|comp)_\d+$/.test(k)))
      notes.push(
        'Its custom shader effects were ignored, so it will look simpler than in MilkDrop.',
      );
    if (extraWaves)
      notes.push(`Only the first 2 of its custom waveforms were kept (${extraWaves} left out).`);
    if (extraShapes)
      notes.push(`Only the first 4 of its shapes were kept (${extraShapes} left out).`);
    if (textured) notes.push('Its textured shapes are drawn as plain colours.');
    if (
      [...map.keys()].some(
        k => /^(ob_|ib_|mv_)/.test(k) && num(k, 0) > 0 && /_(a|x|y)$|_size$/.test(k),
      )
    )
      notes.push('Its borders and motion vectors were left out.');

    const raw = {
      name:
        clean(String(fileName || '').replace(/\.[^.]*$/, '')).slice(0, Core.MAX_NAME) ||
        'Imported preset',
      author: 'imported .milk',
      dialect: 'milk',
      hear: HEAR,
      decay: num('fdecay', 0.98),
      zoom: num('zoom', 1),
      rot: num('rot', 0),
      cx: num('cx', 0.5),
      cy: num('cy', 0.5),
      dx: num('dx', 0),
      dy: num('dy', 0),
      sx: num('sx', 1),
      sy: num('sy', 1),
      warp: num('warp', 1),
      wave: {
        mode: WAVE_MODE[Math.max(0, Math.min(7, Math.round(num('nwavemode', 0))))],
        r: num('wave_r', 1),
        g: num('wave_g', 1),
        b: num('wave_b', 1),
        a: num('fwavealpha', 0.8),
        scale: num('fwavescale', 1),
        x: num('wave_x', 0.5),
        y: num('wave_y', 0.5),
        additive: on('badditivewaves'),
        thick: on('bwavethick') ? 2 : 1,
      },
      color: {
        gamma: num('fgammaadj', 1),
        echoAlpha: num('fvideoechoalpha', 0),
        echoZoom: num('fvideoechozoom', 1),
        echoOrient: Math.round(num('nvideoechoorientation', 0)),
        solarize: on('bsolarize') ? 0.5 : 0,
      },
      init: pieces.init,
      frame: pieces.frame,
      pixel: pieces.pixel,
      waves,
      shapes,
    };
    const r = Core.sanitizePreset(raw);
    if (!r.ok) return { ok: false, error: `This preset could not be translated. ${r.error}` };
    return { ok: true, preset: r.preset, notes };
  }

  const api = { convert, MAX_TEXT, HEAR };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FalseHorizonMilk = api;
})(typeof window !== 'undefined' ? window : globalThis);
