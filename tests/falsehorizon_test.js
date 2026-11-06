// Unit tests for FalseHorizon: the equation language (static/falsehorizon-expr.js) and the logic
// (static/falsehorizon-core.js, the built-in presets in static/falsehorizon-presets.js). Run by
// tests/test_visuals.py (needs Node); prints "ok  ..." / "BAD ..." per check.
const E = require('../static/falsehorizon-expr.js');
const C = require('../static/falsehorizon-core.js');
let fails = 0;
function check(name, cond, extra) {
  if (!cond) fails++;
  console.log(
    (cond ? 'ok  ' : 'BAD ') +
      name +
      (!cond && extra !== undefined ? '   <- ' + JSON.stringify(extra) : ''),
  );
}
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;
const run = (src, vars = {}, opts) => {
  const v = Object.assign(Object.create(null), vars);
  E.compile(src, opts).run(v);
  return v;
};
const value = (src, vars) => run('a = ' + src + ';', vars).a;
const bad = src => {
  const r = E.check(src);
  return !r.ok && typeof r.error === 'string' && r.error.length > 0;
};

// ================= the equation language =================
check(
  'expr: arithmetic and precedence',
  value('1 + 2 * 3') === 7 &&
    value('(1 + 2) * 3') === 9 &&
    value('10 - 4 - 3') === 3 &&
    value('2 * 3 % 4') === 2 &&
    value('8 / 2 / 2') === 2,
);
check(
  'expr: power is right to left and binds tighter than a minus sign',
  value('2 ^ 3 ^ 2') === 512 &&
    value('-2 ^ 2') === -4 &&
    value('2 ^ -1') === 0.5 &&
    value('(-2) ^ 2') === 4,
);
check('expr: numbers can be written .5, 1e3, 1.5e-1', value('.5 + 1e3 + 1.5e-1') === 1000.65);
check(
  'expr: comparisons and logic give 1 or 0',
  value('3 > 2 && 1 < 2') === 1 &&
    value('3 < 2 || 0') === 0 &&
    value('!0') === 1 &&
    value('!5') === 0 &&
    value('2 == 2') === 1 &&
    value('2 != 2') === 0 &&
    value('2 <= 2') === 1 &&
    value('3 >= 4') === 0,
);
check(
  'expr: if() only works out the branch it takes',
  (() => {
    let calls = 0;
    const rng = () => {
      calls++;
      return 0.5;
    };
    const v = value('if(1, 5, rand(1))', {});
    const w = run('a = if(1, 5, rand(1));', {}, { rng });
    const x = run('a = if(0, 5, rand(1));', {}, { rng });
    return v === 5 && w.a === 5 && calls === 1 && x.a === 0.5;
  })(),
);
check(
  'expr: the functions',
  near(value('sin(0)'), 0) &&
    near(value('cos(0)'), 1) &&
    value('abs(-3)') === 3 &&
    value('min(2, 5)') === 2 &&
    value('max(2, 5)') === 5 &&
    value('floor(2.7)') === 2 &&
    value('ceil(2.1)') === 3 &&
    value('int(-2.7)') === -2 &&
    near(value('frac(2.25)'), 0.25) &&
    value('sign(-9)') === -1 &&
    value('sqr(3)') === 9 &&
    value('sqrt(16)') === 4 &&
    value('pow(2, 10)') === 1024 &&
    value('mod(-1, 3)') === 2 &&
    value('above(2, 1)') === 1 &&
    value('below(2, 1)') === 0 &&
    value('equal(1, 1)') === 1 &&
    value('clamp(5, 0, 1)') === 1 &&
    value('clamp(-5, 0, 1)') === 0 &&
    value('mix(0, 10, 0.25)') === 2.5 &&
    near(value('sigmoid(0, 3)'), 0.5) &&
    value('smoothstep(0, 1, 2)') === 1 &&
    value('smoothstep(0, 1, 0.5)') === 0.5 &&
    near(value('atan2(1, 1)'), Math.PI / 4),
);
check(
  'expr: assignments and the shortcut forms',
  (() => {
    const v = run('a = 2; a += 3; a *= 2; a -= 1; a /= 3;');
    return v.a === 3;
  })(),
);
check(
  'expr: names are not case sensitive, and a name never set reads as 0',
  (() => {
    const v = run('A = 2; b = a + c;');
    return v.a === 2 && v.b === 2;
  })(),
);
check(
  'expr: inputs are read from the variables and results written back',
  (() => {
    const v = run('zoom = 1 + bass * 0.5;', { bass: 1 });
    return v.zoom === 1.5;
  })(),
);
check(
  'expr: dividing by zero, or mod 0, gives 0',
  value('1 / 0') === 0 && value('5 % 0') === 0 && run('a = 4; a /= 0;').a === 0,
);
check(
  'expr: a long chain of % stays fast (each % used to work out its left side twice, doubling the time at every step)',
  (() => {
    const t = Date.now();
    const v = value('7' + ' % 5'.repeat(60));
    return v === 2 && Date.now() - t < 500;
  })(),
);
check(
  'expr: results that are not numbers become 0 and huge ones are clamped',
  value('sqrt(0 - 4)') === 2 &&
    value('1e30 * 1e30') === 1e6 &&
    value('exp(1000)') === 1e6 &&
    Number.isFinite(value('log(0)')) &&
    value('asin(2)') === 0 &&
    value('pow(-8, 0.5)') === 0,
);
check(
  'expr: rand uses the given source of randomness',
  run('a = rand(10);', {}, { rng: () => 0.37 }).a === 3.7,
);
check(
  'expr: empty text is a program that does nothing',
  (() => {
    const v = run('', { a: 1 });
    return v.a === 1;
  })() &&
    (() => {
      const v = run(' ;; ; ', { a: 1 });
      return v.a === 1;
    })(),
);
check(
  'expr: reads and writes are reported',
  (() => {
    const p = E.compile('a = b + 1; c = a;');
    return p.writes.join() === 'a,c' && p.reads.includes('b');
  })(),
);

// things that must be refused, with a message a person can read
const refuse = {
  'unknown function': 'a = foo(1);',
  'wrong number of arguments': 'a = sin(1, 2);',
  'not an assignment': '1 + 2;',
  'missing value': 'a = ;',
  'dangling operator': 'a = 1 +',
  'a function name used as a value': 'a = sin;',
  'setting a function name': 'sin = 3;',
  'a string': 'a = "hi";',
  'a single quote': "a = 'x';",
  'a dot': 'a = b.c;',
  brackets: 'a = b[0];',
  braces: 'a = {};',
  'an unclosed bracket': 'a = (1 + 2;',
  'an extra bracket': 'a = 1 + 2);',
  eval: 'a = eval(1);',
  'a semicolon-less pair': 'a = 1 b = 2;',
  'a lone equals': 'a == 1;',
  backticks: 'a = `1`;',
  __proto__: '__proto__ = 1;',
  constructor: 'a = constructor;',
  prototype: 'prototype = 3;',
  'a name that is far too long': 'a' + 'b'.repeat(40) + ' = 1;',
  'a comment-looking thing': 'a = 1; // x',
  unicode: 'a = ２;',
  'an arrow function': 'a = () => 1;',
};
for (const [what, src] of Object.entries(refuse)) check(`expr: refuses ${what}`, bad(src), src);
check(
  'expr: refuses a formula nested too deeply',
  bad('a = ' + '('.repeat(40) + '1' + ')'.repeat(40) + ';') && bad('a = ' + '-'.repeat(60) + '1;'),
);
check(
  'expr: refuses a formula that is too long, has too many statements or too many names',
  bad('a = 1;'.repeat(1200)) &&
    bad('a=1;'.repeat(251)) &&
    bad(Array.from({ length: 121 }, (_, i) => `n${i} = 1;`).join('')) &&
    bad('a = ' + '1+'.repeat(1600) + '1;'),
);
check(
  'expr: the limits are the ones documented',
  E.LIMITS.source === 6000 &&
    E.LIMITS.statements === 250 &&
    E.LIMITS.names === 120 &&
    E.LIMITS.depth === 24,
);
check(
  'expr: a formula at the limits still compiles and runs',
  (() => {
    const src = Array.from({ length: 250 }, (_, i) => `v${i % 100} = ${i} + 1;`).join('');
    return src.length <= 6000 && E.check(src).ok;
  })(),
);
check(
  'expr: nothing can reach outside the variables (no prototype or global access)',
  (() => {
    const v = run(
      'a = tostring + valueof + hasownproperty + window + globalthis + process + require;',
    );
    return (
      v.a === 0 && {}.polluted === undefined && typeof Object.prototype.toString === 'function'
    );
  })(),
);
check('expr: error messages say where', /character \d+/.test(E.check('a = 1 + ;').error));
check(
  'expr: no eval, Function or dynamic code in the source',
  !/\beval\b|new Function|Function\(|setTimeout|document\.|window\./.test(
    require('fs')
      .readFileSync(require('path').join(__dirname, '../static/falsehorizon-expr.js'), 'utf8')
      .replace(/\/\/.*$/gm, ''),
  ),
);

// ================= presets: files and checking =================
const builtins = C.builtins();
check(
  'presets: the built-in set is 17 originals, every one valid',
  builtins.length === 17 && builtins.every(p => p.name && p.name.length <= C.MAX_NAME),
  builtins.length,
);
check(
  'presets: names are unique and there is a mix of waves, shapes and pixel formulas',
  new Set(builtins.map(p => p.name)).size === 17 &&
    builtins.some(p => p.waves.length) &&
    builtins.some(p => p.shapes.length) &&
    builtins.some(p => p.pixel),
);
check(
  'presets: the built-ins are frozen (nothing can change them later)',
  Object.isFrozen(builtins) && Object.isFrozen(builtins[0]),
);
check(
  'presets: none of them borrows a name from elsewhere',
  builtins.every(p => p.author === 'FalseHorizons'),
);

const ok = C.sanitizePreset({ name: 'T', frame: 'zoom = 1.01;' });
check(
  'presets: a tiny preset is filled in with defaults',
  ok.ok &&
    ok.preset.decay === 0.97 &&
    ok.preset.zoom === 1 &&
    ok.preset.wave.mode === 0 &&
    ok.preset.shapes.length === 0 &&
    ok.preset.frame === 'zoom = 1.01;',
  ok,
);
for (const junk of [null, undefined, 'x', 5, [], [1], true])
  check(`presets: ${JSON.stringify(junk)} is not a preset`, !C.sanitizePreset(junk).ok);
check(
  'presets: a name is required, and is cleaned and shortened',
  !C.sanitizePreset({}).ok &&
    !C.sanitizePreset({ name: '   ' }).ok &&
    C.sanitizePreset({ name: 'a\u0000b\nc' + 'x'.repeat(100) }).preset.name.length <= C.MAX_NAME &&
    !/[\u0000-\u001f]/.test(C.sanitizePreset({ name: 'a\u0000b\nc' }).preset.name),
);
const wild = C.sanitizePreset({
  name: 'w',
  decay: 5,
  zoom: 100,
  rot: -9,
  warp: 99,
  dx: 5,
  sx: 0,
  wave: { mode: 99, a: 7, thick: 50 },
  color: { gamma: 0, echoOrient: 9, cover: 3 },
  shapes: [{ sides: 1000, rad: 99 }, { sides: 1 }],
}).preset;
check(
  'presets: out-of-range numbers are pulled into range',
  wild.decay === 0.99 &&
    wild.zoom === 1.3 &&
    wild.rot === -0.5 &&
    wild.warp === 4 &&
    wild.dx === 0.1 &&
    wild.sx === 0.5 &&
    wild.wave.mode === 4 &&
    wild.wave.a === 1 &&
    wild.wave.thick === 3 &&
    wild.color.gamma === 0.5 &&
    wild.color.echoOrient === 3 &&
    wild.color.cover === 1 &&
    wild.shapes[0].sides === 100 &&
    wild.shapes[0].rad === 2 &&
    wild.shapes[1].sides === 3,
  wild,
);
check(
  'presets: NaN, Infinity and text fall back to the defaults',
  (p => p.zoom === 1 && p.decay === 0.97 && p.warp === 0)(
    C.sanitizePreset({ name: 'n', zoom: NaN, decay: Infinity, warp: 'lots' }).preset,
  ),
);
check(
  'presets: no more than 2 custom waves and 4 shapes are kept',
  (p => p.waves.length === 2 && p.shapes.length === 4)(
    C.sanitizePreset({ name: 'm', waves: [{}, {}, {}, {}], shapes: [{}, {}, {}, {}, {}, {}] })
      .preset,
  ),
);
check(
  'presets: unknown keys are dropped, and a __proto__ payload changes nothing',
  (() => {
    const p = C.sanitizePreset(
      JSON.parse(
        '{"name":"p","evil":"x","__proto__":{"polluted":true},"wave":{"__proto__":{"polluted":true}}}',
      ),
    ).preset;
    return !('evil' in p) && {}.polluted === undefined && !('polluted' in p);
  })(),
);
const bf = C.sanitizePreset({ name: 'f', frame: 'zoom = sin(;' });
check(
  'presets: a broken formula refuses the whole preset and says which one',
  !bf.ok && /frame formula/.test(bf.error) && /character/.test(bf.error),
  bf,
);
check(
  'presets: the pixel, init, wave and shape formulas are checked too',
  ['init', 'pixel'].every(k => !C.sanitizePreset({ name: 'f', [k]: 'a = (' }).ok) &&
    !C.sanitizePreset({ name: 'f', waves: [{ point: 'a = (' }] }).ok &&
    !C.sanitizePreset({ name: 'f', shapes: [{ frame: 'a = (' }] }).ok,
);
check(
  'presets: formulas that add up to too much are refused',
  (() => {
    const big = 'a = ' + '1 + '.repeat(1100) + '1;';
    return (
      big.length < 6000 &&
      E.check(big).ok &&
      !C.sanitizePreset({
        name: 'big',
        init: big,
        frame: big,
        pixel: big,
        waves: [{ init: big, frame: big, point: big }],
        shapes: [{ frame: big }],
      }).ok
    );
  })(),
);
check(
  'presets: checking twice changes nothing',
  builtins.every(p => JSON.stringify(C.sanitizePreset(p).preset) === JSON.stringify(p)),
);

const file = C.exportPreset(builtins[0]);
check(
  'files: a preset exports and imports back to the same preset',
  (r => r.ok && JSON.stringify(r.preset) === JSON.stringify(builtins[0]))(C.parsePresetFile(file)),
);
check(
  'files: every built-in fits in a file',
  builtins.every(p => C.exportPreset(p).length <= C.MAX_FILE),
  Math.max(...builtins.map(p => C.exportPreset(p).length)),
);
check(
  'files: too big, not JSON, no marker, wrong marker version are all refused with a reason',
  !C.parsePresetFile('x'.repeat(C.MAX_FILE + 1)).ok &&
    !C.parsePresetFile('{nope').ok &&
    !C.parsePresetFile('{"name":"x"}').ok &&
    !C.parsePresetFile('{"fh-preset":2,"name":"x"}').ok &&
    !C.parsePresetFile(null).ok &&
    !C.parsePresetFile('[]').ok &&
    !C.parsePresetFile('{"fh-preset":1}').ok,
);
check(
  'files: a file with a bad formula is refused',
  !C.parsePresetFile('{"fh-preset":1,"name":"x","frame":"zoom = ;"}').ok,
);

// ================= running a preset =================
const rng = (() => {
  let s = 12345;
  return () => (s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296;
})();
const wave = new Float32Array(256).map((_, i) => Math.sin(i / 9)),
  spec = new Float32Array(64).map((_, i) => 0.5 + 0.5 * Math.sin(i / 5));
const baseInputs = (o = {}) =>
  Object.assign(
    {
      time: 1,
      frame: 1,
      bass: 0,
      mid: 0,
      treb: 0,
      bass_att: 0,
      mid_att: 0,
      treb_att: 0,
      level: 0,
      beat: 0,
      aspect: 1,
      wave,
      spec,
    },
    o,
  );
const neutral = (extra = {}) =>
  new C.PresetRuntime(
    C.sanitizePreset({ name: 'n', decay: 0.9, wave: { a: 0 }, ...extra }).preset,
    rng,
  );
check(
  'run: a preset that does nothing maps every point to itself',
  (() => {
    const s = neutral().frame(baseInputs(), { motion: 1 });
    let worst = 0,
      k = 0;
    for (let j = 0; j <= C.MESH_H; j++)
      for (let i = 0; i <= C.MESH_W; i++) {
        worst = Math.max(
          worst,
          Math.abs(s.mesh[k++] - i / C.MESH_W),
          Math.abs(s.mesh[k++] - j / C.MESH_H),
        );
      }
    return worst < 1e-6;
  })(),
);
const uvAt = (state, x, y) => {
  const k = (Math.round(y * C.MESH_H) * (C.MESH_W + 1) + Math.round(x * C.MESH_W)) * 2;
  return [state.mesh[k], state.mesh[k + 1]];
};
check(
  'run: zooming in samples nearer the centre',
  (u => u[0] > 0 && u[1] > 0 && u[0] < 0.5)(uvAt(neutral({ zoom: 1.2 }).frame(baseInputs()), 0, 0)),
);
check(
  'run: positive rot turns the picture counter-clockwise (the right edge comes from below)',
  uvAt(neutral({ rot: 0.2 }).frame(baseInputs()), 1, 0.5)[1] < 0.5,
);
check(
  'run: positive dx moves the picture right (each point comes from its left)',
  uvAt(neutral({ dx: 0.05 }).frame(baseInputs()), 0.5, 0.5)[0] < 0.5 &&
    uvAt(neutral({ dy: 0.05 }).frame(baseInputs()), 0.5, 0.5)[1] < 0.5,
);
check(
  'run: motion 0 turns every effect off',
  (() => {
    const s = neutral({ zoom: 1.2, rot: 0.3, dx: 0.05, warp: 3 }).frame(baseInputs(), {
      motion: 0,
    });
    let worst = 0,
      k = 0;
    for (let j = 0; j <= C.MESH_H; j++)
      for (let i = 0; i <= C.MESH_W; i++) {
        worst = Math.max(
          worst,
          Math.abs(s.mesh[k++] - i / C.MESH_W),
          Math.abs(s.mesh[k++] - j / C.MESH_H),
        );
      }
    return worst < 1e-6;
  })(),
);
check(
  'run: half the motion moves half as far',
  (() => {
    const full = uvAt(neutral({ dx: 0.04 }).frame(baseInputs(), { motion: 1 }), 0.5, 0.5)[0],
      half = uvAt(neutral({ dx: 0.04 }).frame(baseInputs(), { motion: 0.5 }), 0.5, 0.5)[0];
    return near(0.5 - half, (0.5 - full) / 2, 1e-6);
  })(),
);
check(
  'run: frame formulas see the music',
  neutral({ frame: 'zoom = 1 + bass * 0.2;' }).frame(baseInputs({ bass: 1 })).mesh[0] > 0.05 &&
    near(
      neutral({ frame: 'zoom = 1 + bass * 0.2;' }).frame(baseInputs({ bass: 0 })).mesh[0],
      0,
      1e-6,
    ),
);
check(
  'run: pixel formulas change the mesh point by point',
  (() => {
    const s = neutral({ pixel: 'dx = x * 0.05;' }).frame(baseInputs());
    return uvAt(s, 0, 0.5)[0] === 0 && uvAt(s, 1, 0.5)[0] < 1 - 0.04;
  })(),
);
check(
  'run: q numbers pass from the frame formula to the pixel formula',
  (() => {
    const s = neutral({ frame: 'q1 = 0.05;', pixel: 'dx = q1;' }).frame(baseInputs());
    return near(uvAt(s, 0.5, 0.5)[0], 0.45, 1e-6);
  })(),
);
check(
  'run: your own variables keep their value from frame to frame',
  (() => {
    const r = neutral({ frame: 'n = n + 1; zoom = 1 + n * 0.001;' });
    r.frame(baseInputs());
    r.frame(baseInputs());
    const s = r.frame(baseInputs());
    return (
      s.mesh[0] > uvAt(neutral({ zoom: 1.0025 }).frame(baseInputs()), 0, 0)[0] - 1e-9 && r.v.n === 3
    );
  })(),
);
check(
  'run: init runs once, at the start',
  (() => {
    const r = neutral({ init: 'q1 = 7;', frame: 'q2 = q1 + 1;' });
    r.frame(baseInputs());
    return r.v.q1 === 7 && r.v.q2 === 8;
  })(),
);
check(
  'run: a formula cannot push values outside their range',
  (() => {
    const s = neutral({
      frame:
        'zoom = 99; decay = 5; rot = 99; warp = 99; gamma = 0; hue = 1e9; bright = 50; echo_alpha = 3; cover = 9;',
    }).frame(baseInputs());
    return (
      s.decay === 0.99 &&
      s.color.gamma === 0.5 &&
      s.color.bright === 2 &&
      s.color.echoAlpha === 0.5 &&
      s.color.cover === 1 &&
      s.color.hue === 1000 &&
      s.mesh.every(Number.isFinite)
    );
  })(),
);
check(
  'run: shapes and waves come out in clip coordinates with colours between 0 and 1',
  (() => {
    const r = new C.PresetRuntime(
      C.sanitizePreset({
        name: 's',
        wave: { mode: 0, a: 1 },
        shapes: [{ sides: 5, a: 1, a2: 1 }],
        waves: [{ samples: 32, a: 1 }],
      }).preset,
      rng,
    );
    const s = r.frame(baseInputs());
    const kinds = s.draws.map(d => d.kind).join();
    return (
      kinds === 'strip,strip,fan' &&
        s.draws.every(
          d =>
            d.verts.length === d.count * 6 &&
            d.verts.every(Number.isFinite) &&
            [...d.verts].every((v, i) => (i % 6 < 2 ? Math.abs(v) <= 4 : v >= 0 && v <= 1)),
        ),
      kinds
    );
  })(),
);
check(
  'run: the shape of a polygon follows its number of sides (plus a centre and a closing point)',
  (() => {
    const s = new C.PresetRuntime(
      C.sanitizePreset({ name: 's', wave: { a: 0 }, shapes: [{ sides: 7, a: 1 }] }).preset,
      rng,
    ).frame(baseInputs());
    return s.draws[0].count === 9;
  })(),
);
check(
  'run: weight scales how visible the drawing is (used while fading between presets)',
  (() => {
    const r = new C.PresetRuntime(
      C.sanitizePreset({ name: 's', wave: { mode: 1, a: 1 } }).preset,
      rng,
    );
    const a = r.frame(baseInputs(), { weight: 1 }).draws[0].verts[5],
      b = r.frame(baseInputs(), { weight: 0.25 }).draws[0].verts[5];
    return near(b, a / 4, 1e-6) && r.frame(baseInputs(), { weight: 0 }).draws.length === 0;
  })(),
);
check(
  'run: drawn light is at 40 percent strength, ordinary drawing is not',
  (() => {
    const add = new C.PresetRuntime(
      C.sanitizePreset({ name: 'a', wave: { mode: 1, a: 1, additive: true } }).preset,
      rng,
    ).frame(baseInputs()).draws[0].verts[5];
    const norm = new C.PresetRuntime(
      C.sanitizePreset({ name: 'a', wave: { mode: 1, a: 1, additive: false } }).preset,
      rng,
    ).frame(baseInputs()).draws[0].verts[5];
    return near(add, 0.4) && near(norm, 1);
  })(),
);
check(
  'run: the five built-in waveform modes all draw',
  [0, 1, 2, 3, 4].every(
    mode =>
      new C.PresetRuntime(C.sanitizePreset({ name: 'w', wave: { mode, a: 1 } }).preset, rng).frame(
        baseInputs(),
      ).draws.length === (mode === 4 ? 2 : 1),
  ),
);
check('run: wave_a at 0 draws no built-in wave', neutral().frame(baseInputs()).draws.length === 0);
check(
  'run: custom wave points are read from the formula (x, y, colour) and clamped',
  (() => {
    const s = new C.PresetRuntime(
      C.sanitizePreset({
        name: 'c',
        wave: { a: 0 },
        waves: [{ samples: 16, a: 1, point: 'x = sample; y = 9; r = 5;' }],
      }).preset,
      rng,
    ).frame(baseInputs());
    const v = s.draws[0].verts;
    return near(v[0], -1) && near(v[15 * 6], 1) && v[1] <= 4 && v[2] === 1;
  })(),
);

// every built-in preset, many frames, ordinary and extreme music
const extremes = [
  baseInputs(),
  baseInputs({ bass: 1, mid: 1, treb: 1, bass_att: 0, level: 1, beat: 1 }),
  baseInputs({ bass: 0, bass_att: 1, mid_att: 1, treb_att: 1 }),
  baseInputs({ aspect: 0.5 }),
  baseInputs({ aspect: 3 }),
];
check(
  'run: every built-in stays finite and inside its limits for 400 frames of everything from silence to full blast',
  (() => {
    for (const p of builtins) {
      const r = new C.PresetRuntime(p, rng);
      for (let f = 0; f < 400; f++) {
        const inp = extremes[f % extremes.length],
          s = r.frame(
            {
              ...inp,
              time: f / 60,
              frame: f,
              bass: Math.min(1, inp.bass + rng()),
              mid: rng(),
              treb: rng(),
              beat: rng() < 0.05 ? 1 : 0,
            },
            { motion: 1, weight: 1 },
          );
        if (!(s.decay <= 0.99 && s.decay >= 0.5)) return `${p.name}: decay ${s.decay}`;
        for (const x of s.mesh)
          if (!Number.isFinite(x) || Math.abs(x) > 20) return `${p.name}: mesh ${x}`;
        for (const d of s.draws)
          for (let i = 0; i < d.verts.length; i++) {
            const x = d.verts[i];
            if (!Number.isFinite(x)) return `${p.name}: vertex ${x}`;
            if (i % 6 >= 2 && (x < 0 || x > 1)) return `${p.name}: colour ${x}`;
          }
      }
    }
    return true;
  })(),
);
check(
  'run: every built-in draws something at some point in its first 5 seconds',
  builtins.every(p => {
    const r = new C.PresetRuntime(p, rng);
    for (let f = 0; f < 300; f++)
      if (r.frame({ ...baseInputs({ bass: 0.5, mid: 0.4, treb: 0.3, time: f / 60 }) }).draws.length)
        return true;
    return false;
  }),
);
check(
  'run: one frame is quick (all 17 presets, 60 frames each, well under a second of work)',
  (() => {
    const t = Date.now();
    for (const p of builtins) {
      const r = new C.PresetRuntime(p, rng);
      for (let f = 0; f < 60; f++) r.frame(baseInputs({ time: f / 60 }));
    }
    return Date.now() - t < 2500;
  })(),
);

// ================= the fractal: a surface that melts between a simple wave and a Mandelbrot height
// field =================
const homeMesh = () => {
  const m = new Float32Array((C.MESH_W + 1) * (C.MESH_H + 1) * 2);
  for (let g = 0; g < m.length / 2; g++) {
    m[g * 2] = (g % (C.MESH_W + 1)) / C.MESH_W;
    m[g * 2 + 1] = Math.floor(g / (C.MESH_W + 1)) / C.MESH_H;
  }
  return m;
};
const warped = (o = {}, amount = 1, calm = false) =>
  C.fractalWarp(
    homeMesh(),
    baseInputs({ time: 3, bass: 0.4, mid: 0.4, aspect: 1.78, ...o }),
    amount,
    calm,
  );
const moved = (a, b) => {
  let n = 0,
    big = 0;
  for (let i = 0; i < a.length; i++) {
    const d = Math.abs(a[i] - b[i]);
    n += d;
    if (d > big) big = d;
  }
  return { mean: n / a.length, big };
};
const quarter = Math.PI / 2 / C.MORPH_SPEED; // the time the modulation first peaks
check(
  'fractal: the modulation index is (sin(time * speed) + 1) / 2, always 0..1, 0.5 at time 0, 1 at the peak, 0 at the trough',
  (() => {
    for (let t = -50; t < 50; t += 0.1) {
      const m = C.modulationIndex(t);
      if (!(m >= 0 && m <= 1)) return false;
    }
    return (
      Math.abs(C.modulationIndex(0) - 0.5) < 1e-9 &&
      Math.abs(C.modulationIndex(quarter) - 1) < 1e-9 &&
      Math.abs(C.modulationIndex(3 * quarter)) < 1e-9 &&
      Number.isFinite(C.modulationIndex(NaN))
    );
  })(),
);
check(
  'fractal: the height is smooth (no steps), 0..1, 1 inside the set, and low far outside it',
  (() => {
    let lo = 1,
      hi = 0,
      jump = 0,
      prev = null;
    for (let x = -2; x <= 1; x += 0.002) {
      const h = C.fractalHeight(x, 0.3);
      lo = Math.min(lo, h);
      hi = Math.max(hi, h);
      if (prev !== null) jump = Math.max(jump, Math.abs(h - prev));
      prev = h;
    }
    return (
      lo >= 0 &&
      hi <= 1 &&
      C.fractalHeight(0, 0) === 1 &&
      C.fractalHeight(-0.5, 0) === 1 &&
      C.fractalHeight(3, 3) < 0.1 &&
      new Set([0.9, 1.1, 1.3, 1.7].map(x => C.fractalHeight(x, 0.1))).size === 4 &&
      jump < 0.5
    );
  })(),
);
check(
  'fractal: the base wave is a ring pattern around the middle, -0.5..0.5',
  (() => {
    let lo = 0,
      hi = 0;
    for (let x = -1; x <= 1; x += 0.05)
      for (let y = -1; y <= 1; y += 0.05) {
        const h = C.baseHeight(x, y, 2);
        lo = Math.min(lo, h);
        hi = Math.max(hi, h);
      }
    return (
      lo >= -0.5 &&
      hi <= 0.5 &&
      hi > 0.4 &&
      Math.abs(C.baseHeight(0.3, 0.4, 1) - C.baseHeight(0.5, 0, 1)) < 1e-9
    );
  })(),
);
check(
  'fractal warp: it bends the picture itself (the mesh moves), by a sane amount, and stays finite, at every moment of the cycle',
  (() => {
    for (let t = 0; t < 40; t += 0.5) {
      const m = warped({ time: t, bass: 1, mid: 1 }),
        d = moved(m, homeMesh());
      if (!m.every(Number.isFinite) || d.mean < 0.0005 || d.big > 0.3) return false;
    }
    return true;
  })(),
);
check(
  'fractal warp: geometry 0 leaves the mesh exactly alone, and more geometry bends it more',
  (() => {
    const z = moved(warped({}, 0), homeMesh()),
      lo = moved(warped({}, 0.3), homeMesh()),
      hi = moved(warped({}, 1), homeMesh());
    return z.big === 0 && hi.mean > lo.mean && lo.mean > 0;
  })(),
);
check(
  'fractal warp: it is added to whatever warp the preset made, not instead of it',
  (() => {
    const base = homeMesh();
    for (let i = 0; i < base.length; i++) base[i] += 0.1;
    const m = C.fractalWarp(
        base.slice(),
        baseInputs({ time: 3, bass: 0.4, mid: 0.4, aspect: 1.78 }),
        1,
      ),
      plain = warped();
    return m.every((v, i) => Math.abs(v - 0.1 - plain[i]) < 1e-5);
  })(),
);
check(
  'fractal warp: at the trough it is the plain wave (the music and the noise do nothing), at the peak the fractal has its own shape',
  (() => {
    const trough = 3 * quarter,
      peak = quarter,
      loud = new Float32Array(256).fill(0.9),
      quiet = new Float32Array(256);
    const a = warped({ time: trough, wave: loud, bass: 0.4 }),
      b = warped({ time: trough, wave: quiet, bass: 0.4 });
    const c = warped({ time: peak, wave: loud, bass: 0.4 }),
      d = warped({ time: peak, wave: quiet, bass: 0.4 });
    return moved(a, b).big < 1e-9 && moved(c, d).mean > 0.001 && moved(c, a).mean > 0.001;
  })(),
);
check(
  'fractal warp: the glitch builds toward the peak (the shaking is stronger near the top of the cycle than a third of the way up)',
  (() => {
    const loud = new Float32Array(256).fill(0.9),
      quiet = new Float32Array(256),
      at = t => moved(warped({ time: t, wave: loud }), warped({ time: t, wave: quiet })).mean;
    return at(quarter) > at(quarter * 0.5) && at(quarter * 0.5) > at(0);
  })(),
);
check(
  'fractal warp: the surface is always in motion (changes with time), and calm mode cycles four times slower',
  (() => {
    const a = warped({ time: 1 }),
      b = warped({ time: 4 });
    const fast = warped({ time: quarter }, 1, false),
      slow = warped({ time: quarter }, 1, true);
    return moved(a, b).mean > 0.0003 && moved(fast, slow).mean > 0.0003;
  })(),
);
check(
  'fractal warp: blended with a preset change, a frame of every preset stays finite and in range',
  (() => {
    for (const p of builtins) {
      const st = new C.PresetRuntime(p, rng).frame(baseInputs({ time: 5 }), {
        motion: 1,
        weight: 1,
      });
      C.fractalWarp(st.mesh, baseInputs({ time: 5, aspect: 1.78 }), 1);
      if (!st.mesh.every(x => Number.isFinite(x) && Math.abs(x) < 20)) return false;
    }
    return true;
  })(),
);
check(
  'fractal warp: nothing is drawn on top any more (the old wireframe is gone)',
  C.fractalDraws === undefined && C.FractalClap === undefined,
);
check(
  'fractal warp: it is quick (a frame of it a few milliseconds at most)',
  (() => {
    const m = homeMesh(),
      t = process.hrtime.bigint();
    for (let i = 0; i < 300; i++)
      C.fractalWarp(m, baseInputs({ time: i / 60, aspect: 1.78, bass: 0.5 }), 1);
    return Number(process.hrtime.bigint() - t) / 1e6 / 300 < 4;
  })(),
);
check(
  'fractal warp: a waveform shorter than 256 points still gives a finite mesh (the C++ port found this)',
  (() => {
    const m = C.fractalWarp(
      homeMesh(),
      baseInputs({
        time: 4.5,
        bass: 0.4,
        mid: 0.4,
        aspect: 1.78,
        wave: new Float32Array(100).fill(0.5),
      }),
      1,
    );
    return m.every(Number.isFinite);
  })(),
);
check(
  'settings: the geometry amount defaults to 60 and is kept in 0..100',
  C.DEFAULTS.geometry === 60 &&
    C.sanitize({ geometry: 999 }).geometry === 100 &&
    C.sanitize({ geometry: -3 }).geometry === 0 &&
    C.sanitize({ geometry: 'x' }).geometry === 60,
);

// ================= blending two presets =================
const N = (C.MESH_W + 1) * (C.MESH_H + 1) * 2,
  home = () => {
    const m = new Float32Array(N);
    for (let g = 0; g < N / 2; g++) {
      m[g * 2] = (g % (C.MESH_W + 1)) / C.MESH_W;
      m[g * 2 + 1] = Math.floor(g / (C.MESH_W + 1)) / C.MESH_H;
    }
    return m;
  };
const shifted = (dx, dy) => {
  const m = home();
  for (let i = 0; i < N; i += 2) {
    m[i] += dx;
    m[i + 1] += dy;
  }
  return m;
};
const A = {
  decay: 0.9,
  mesh: shifted(0.1, 0),
  color: {
    gamma: 1,
    hue: 0,
    bright: 1,
    solarize: 0,
    echoAlpha: 0,
    echoZoom: 1,
    echoOrient: 0,
    cover: 0,
  },
  draws: [{ id: 'a' }],
};
const B = {
  decay: 0.98,
  mesh: shifted(0, 0.2),
  color: {
    gamma: 2,
    hue: 1,
    bright: 0,
    solarize: 1,
    echoAlpha: 0.4,
    echoZoom: 3,
    echoOrient: 2,
    cover: 1,
  },
  draws: [{ id: 'b' }],
};
check(
  'blend: 0 is the first preset and 1 is the second',
  C.blendStates(A, B, 0).decay === 0.9 &&
    C.blendStates(A, B, 1) === B &&
    C.blendStates(A, null, 0.5) === A,
);
check(
  'blend: halfway averages the colours, and keeps both sets of drawing',
  (s => near(s.color.gamma, 1.5) && near(s.color.solarize, 0.5) && s.draws.length === 2)(
    C.blendStates(A, B, 0.5),
  ),
);
check(
  'blend: the ends are the plain warps, the middle has both warps at full strength at once (they interfere)',
  (() => {
    const a = C.blendStates(A, B, 0.001),
      m = C.blendStates(A, B, 0.5),
      h = home();
    return (
      near(a.mesh[0] - h[0], 0.1, 0.01) &&
      near(a.mesh[1] - h[1], 0, 0.01) &&
      near(m.mesh[0] - h[0], 0.1, 1e-6) &&
      near(m.mesh[1] - h[1], 0.2, 1e-6)
    );
  })(),
);
check(
  'blend: the middle smears more (more echo, longer trails) than either end',
  (() => {
    const m = C.blendStates(A, B, 0.5),
      e = C.blendStates(A, B, 0.01);
    return (
      m.color.echoAlpha > 0.4 * 0.5 + 0.2 &&
      m.decay > 0.94 &&
      m.decay <= 0.99 &&
      e.color.echoAlpha < 0.05
    );
  })(),
);
check(
  'blend: it stays finite and in range for every built-in pair at every step',
  (() => {
    const bs = builtins;
    for (let i = 0; i < bs.length; i++) {
      const x = new C.PresetRuntime(bs[i], rng).frame(baseInputs(), { motion: 1, weight: 1 }),
        y = new C.PresetRuntime(bs[(i + 1) % bs.length], rng).frame(baseInputs(), {
          motion: 1,
          weight: 1,
        });
      for (let t = 0.05; t < 1; t += 0.15) {
        const s = C.blendStates(x, y, t);
        if (
          !(s.decay >= 0.5 && s.decay <= 0.99) ||
          !s.mesh.every(Number.isFinite) ||
          s.color.echoAlpha > 0.5
        )
          return false;
      }
    }
    return true;
  })(),
);
check(
  'blend: the echo direction switches over at the halfway point instead of being averaged',
  C.blendStates(A, B, 0.4).color.echoOrient === 0 &&
    C.blendStates(A, B, 0.6).color.echoOrient === 2,
);

// ================= the flash guard =================
const guardRun = (levels, stepMs, calm = false) => {
  const g = new C.FlashGuard(calm),
    shown = [];
  levels.forEach((L, i) => {
    const gain = g.feed(L, i * stepMs);
    shown.push(L * gain);
  });
  return { shown, g };
};
check(
  'guard: a dark picture is left alone (after the first moment, when everything fades up from black)',
  (r => r.shown.slice(2).every(s => near(s, 0.2)) && r.shown[0] < 0.2)(
    guardRun(new Array(40).fill(0.2), 100),
  ),
);
check(
  'guard: a bright steady picture is held under the brightness cap',
  (r => Math.max(...r.shown) <= 0.45 + 1e-9 && near(r.shown[r.shown.length - 1], 0.45, 1e-6))(
    guardRun(new Array(80).fill(0.95), 100),
  ),
);
check(
  'guard: the calm cap is lower',
  (r => Math.max(...r.shown) <= 0.35 + 1e-9)(guardRun(new Array(80).fill(0.95), 100, true)),
);
// as fast as the guard is fed: a strobe
const strobe = Array.from({ length: 200 }, (_, i) => (i % 2 ? 0.95 : 0.02));
check(
  'guard: a strobe cannot get brighter faster than the limit',
  (() => {
    const r = guardRun(strobe, 100);
    for (let i = 1; i < r.shown.length; i++)
      if (r.shown[i] - r.shown[i - 1] > 1.0 * 0.1 + 1e-9) return false;
    return true;
  })(),
);
check(
  'guard: in calm mode the limit on getting brighter is lower still',
  (() => {
    const r = guardRun(strobe, 100, true);
    for (let i = 1; i < r.shown.length; i++)
      if (r.shown[i] - r.shown[i - 1] > 0.5 * 0.1 + 1e-9) return false;
    return true;
  })(),
);
check(
  'guard: a sudden jump from black to bright is brought up over a moment, not at once',
  (() => {
    const r = guardRun([0, 0, 0, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9], 100);
    return r.shown[3] < 0.2 && r.shown[8] > r.shown[3];
  })(),
);
check(
  'guard: getting darker is always at once',
  (() => {
    const r = guardRun([0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.05], 100);
    return near(r.shown[7], 0.05);
  })(),
);
// The accessibility rule of thumb (WCAG 2.3.1): no more than 3 "flashes" a second, a flash being
// two opposite swings of at least 0.1 in relative (linear) luminance. Measured here on the worst
// input (a strobe at every speed the guard can see), counting a swing as one unbroken rise or fall,
// so it is stricter than counting single steps.
const flashesPerSecond = (shown, stepMs) => {
  const lin = shown.map(v => Math.pow(v, 2.2)),
    swings = [];
  let start = 0,
    dir = 0;
  for (let i = 1; i < lin.length; i++) {
    const d = Math.sign(lin[i] - lin[i - 1]);
    if (d !== 0 && dir !== 0 && d !== dir) {
      swings.push({ at: i * stepMs, size: lin[i - 1] - lin[start] });
      start = i - 1;
    }
    if (d !== 0) dir = d;
  }
  swings.push({ at: lin.length * stepMs, size: lin[lin.length - 1] - lin[start] });
  const flashes = [];
  for (let i = 1; i < swings.length; i++)
    if (
      Math.abs(swings[i].size) >= 0.1 &&
      Math.abs(swings[i - 1].size) >= 0.1 &&
      Math.sign(swings[i].size) !== Math.sign(swings[i - 1].size)
    )
      flashes.push(swings[i].at);
  return Math.max(0, ...flashes.map(t => flashes.filter(u => u >= t && u < t + 1000).length));
};
check(
  'guard: even a strobe at any speed shows at most 3 flashes in any second (and the unguarded strobe would show many)',
  (() => {
    for (const stepMs of [20, 50, 100, 200, 333])
      for (const calm of [false, true]) {
        const levels = Array.from({ length: Math.round(20000 / stepMs) }, (_, i) =>
          i % 2 ? 0.95 : 0.0,
        );
        if (flashesPerSecond(guardRun(levels, stepMs, calm).shown, stepMs) > 3)
          return `step ${stepMs} calm ${calm}`;
      }
    return (
      flashesPerSecond(
        Array.from({ length: 100 }, (_, i) => (i % 2 ? 0.95 : 0)),
        100,
      ) > 3
    );
  })(),
);
check(
  'guard: gains are always between 0 and 1, and bad readings are survived',
  (() => {
    const g = new C.FlashGuard();
    return [NaN, Infinity, -5, 7, undefined, null, 0.5].every((L, i) => {
      const x = g.feed(L, i * 100);
      return x >= 0 && x <= 1;
    });
  })(),
);
check(
  'guard: a long pause (a hidden tab) does not let it jump',
  (() => {
    const g = new C.FlashGuard();
    g.feed(0, 0);
    const gain = g.feed(0.95, 600000);
    return 0.95 * gain <= 0.8 + 1e-9;
  })(),
);

// ================= what the visuals hear =================
const bins = fn => Uint8Array.from({ length: 512 }, (_, i) => fn(i));
check(
  'hearing: silence is zero',
  (f =>
    f.update(
      bins(() => 0),
      new Uint8Array(1024).fill(128),
      44100,
      0,
    ) &&
    f.bass === 0 &&
    f.beat === 0 &&
    f.wave.every(v => Math.abs(v) < 1e-9) &&
    f.spec.every(v => v === 0))(new C.AudioFeatures()),
);
check(
  'hearing: a low tone is bass, a high one is treble',
  (() => {
    const f = new C.AudioFeatures();
    for (let i = 0; i < 20; i++)
      f.update(
        bins(k => (k < 6 ? 255 : 0)),
        null,
        44100,
        i * 16,
      );
    const g = new C.AudioFeatures();
    for (let i = 0; i < 20; i++)
      g.update(
        bins(k => (k >= 100 && k < 250 ? 255 : 0)),
        null,
        44100,
        i * 16,
      );
    return f.bass > 0.7 && f.treb < 0.05 && g.treb > 0.4 && g.bass < 0.05;
  })(),
);
check(
  'hearing: the 64 bars follow the pitch, low to high',
  (() => {
    const f = new C.AudioFeatures();
    for (let i = 0; i < 20; i++)
      f.update(
        bins(k => (k < 6 ? 255 : 0)),
        null,
        44100,
        i * 16,
      );
    return f.spec[1] > f.spec[40] && f.spec.slice(50).every(v => v < 0.05);
  })(),
);
check(
  'hearing: bass_att is a slow average, so a hit stands out from it',
  (() => {
    const f = new C.AudioFeatures();
    for (let i = 0; i < 100; i++)
      f.update(
        bins(() => 20),
        null,
        44100,
        i * 16,
      );
    const calmAtt = f.bass_att;
    f.update(
      bins(k => (k < 6 ? 255 : 20)),
      null,
      44100,
      2000,
    );
    return f.bass - f.bass_att > 0.2 && calmAtt < 0.2;
  })(),
);
check(
  'hearing: a beat is never faster than every 400 ms (so never more than 2.5 a second)',
  (() => {
    const f = new C.AudioFeatures();
    let beats = 0,
      t = 0;
    for (; t < 10000; t += 20) {
      f.update(
        bins(k => (k < 6 ? (t % 100 < 40 ? 255 : 5) : 5)),
        null,
        44100,
        t,
      );
      if (f.beat === 1) beats++;
    }
    return beats > 3 && beats <= 25;
  })(),
);
check(
  'hearing: the beat fades after it happens, and fades faster than it can repeat',
  (() => {
    const f = new C.AudioFeatures();
    f.update(
      bins(k => (k < 6 ? 255 : 0)),
      null,
      44100,
      1000,
    );
    const first = f.beat;
    f.update(
      bins(() => 0),
      null,
      44100,
      1016,
    );
    return first === 1 && f.beat < 1 && f.beat > 0.5;
  })(),
);
check(
  'hearing: the waveform is -1 to 1',
  (() => {
    const f = new C.AudioFeatures();
    f.update(
      bins(() => 0),
      new Uint8Array(1024).fill(255),
      44100,
      0,
    );
    const hi = f.wave[10];
    f.update(
      bins(() => 0),
      new Uint8Array(1024).fill(0),
      44100,
      16,
    );
    return near(hi, 1, 1e-6) && near(f.wave[10], -1, 1e-6);
  })(),
);
check(
  'hearing: everything is heard 20% louder (capped at 1), the beat detector is not fooled by it',
  (() => {
    const quiet = bins(k => (k < 6 ? 100 : 0)),
      f = new C.AudioFeatures();
    for (let i = 0; i < 30; i++) f.update(quiet, null, 44100, i * 16);
    const raw = C.bandEnergies(quiet, 44100).bass,
      loud = new C.AudioFeatures();
    for (let i = 0; i < 30; i++)
      loud.update(
        bins(() => 255),
        null,
        44100,
        i * 16,
      );
    return (
      near(f.bass, raw * 1.2, 0.01) && loud.bass <= 1 && loud.level <= 1 && C.SENSITIVITY === 1.2
    );
  })(),
);
check('settings: the default intensity is 10% above the old 70', C.DEFAULTS.intensity === 77);
check(
  'hearing: with no analyser everything fades to calm instead of freezing',
  (() => {
    const f = new C.AudioFeatures();
    for (let i = 0; i < 10; i++)
      f.update(
        bins(() => 200),
        new Uint8Array(1024).fill(255),
        44100,
        i * 16,
      );
    const loud = f.bass;
    for (let i = 0; i < 200; i++) f.update(null, null, 44100, 1000 + i * 16);
    return loud > 0.5 && f.bass < 0.01 && f.beat < 0.01 && Math.abs(f.wave[5]) < 0.01;
  })(),
);
check(
  'hearing: a tiny array does not break it',
  (f => {
    f.update(new Uint8Array(4), new Uint8Array(4), 44100, 0);
    return [f.bass, f.mid, f.treb, f.level].every(Number.isFinite) && f.spec.every(Number.isFinite);
  })(new C.AudioFeatures()),
);

// ================= picking presets =================
check(
  'picker: random, and never one of the last few shown',
  (() => {
    const p = new C.PresetPicker(rng),
      pool = 'abcdefghij'.split('');
    const seq = [];
    for (let i = 0; i < 200; i++) seq.push(p.next(pool));
    for (let i = 4; i < seq.length; i++) if (seq.slice(i - 4, i).includes(seq[i])) return false;
    return new Set(seq).size === 10;
  })(),
);
check(
  'picker: Previous steps back and Next retraces before picking again',
  (() => {
    const p = new C.PresetPicker(rng),
      pool = 'abcdefgh'.split('');
    const a = p.next(pool),
      b = p.next(pool),
      c = p.next(pool);
    return (
      p.prev() === b &&
      p.prev() === a &&
      p.prev() === null &&
      p.next(pool) === b &&
      p.next(pool) === c
    );
  })(),
);
check(
  'picker: a preset chosen by hand joins the history',
  (() => {
    const p = new C.PresetPicker(rng);
    p.show('x');
    p.show('y');
    return p.prev() === 'x' && p.next(['x', 'y', 'z']) === 'y';
  })(),
);
check(
  'picker: with one preset, or none, it does not break',
  (() => {
    const p = new C.PresetPicker(rng);
    return p.next([]) === null && p.next(['only']) === 'only' && p.next(['only']) === 'only';
  })(),
);
check(
  'picker: going back, then picking by hand, drops what was ahead',
  (() => {
    const p = new C.PresetPicker(rng);
    p.show('a');
    p.show('b');
    p.show('c');
    p.prev();
    p.prev();
    p.show('z');
    return p.history.join() === 'a,z' && p.prev() === 'a';
  })(),
);

// ================= settings =================
const D = C.DEFAULTS;
check(
  'settings: nothing stored gives the defaults',
  JSON.stringify(C.sanitize(null)) === JSON.stringify(C.sanitize(undefined)) &&
    C.sanitize({}).intensity === D.intensity &&
    C.sanitize({}).on === true &&
    C.sanitize({}).mode === 'horizon' &&
    C.sanitize({}).auto === true,
);
for (const junk of ['text', 5, true, [], [1, 2], () => 1])
  check(
    `settings: ${JSON.stringify(junk)} is ignored`,
    JSON.stringify(C.sanitize(junk)) === JSON.stringify(C.sanitize(null)),
  );
const sw = C.sanitize({
  on: 'yes',
  intensity: 9999,
  mode: 'weird',
  auto: 1,
  cycleSec: 3,
  covers: -50,
  last: 'x'.repeat(200),
  favorites: 'nope',
  userPresets: 'nope',
});
check(
  'settings: out-of-range values are pulled in and unknown choices fall back',
  sw.on === true &&
    sw.intensity === 100 &&
    sw.mode === 'horizon' &&
    sw.auto === true &&
    sw.cycleSec === 10 &&
    sw.covers === 0 &&
    sw.last.length <= C.MAX_NAME &&
    sw.favorites.length === 0 &&
    sw.userPresets.length === 0,
  sw,
);
check('settings: the classic mode is allowed', C.sanitize({ mode: 'classic' }).mode === 'classic');
check(
  'settings: the time between changes stays between 10 and 300 seconds',
  C.sanitize({ cycleSec: 99999 }).cycleSec === 300 &&
    C.sanitize({ cycleSec: 45.4 }).cycleSec === 45,
);
check(
  'settings: favourites are unique, short and at most 30',
  (f => f.length === 30 && new Set(f).size === 30)(
    C.sanitize({
      favorites: Array.from({ length: 80 }, (_, i) => 'p' + (i % 40)).concat(['p1', 'p1']),
    }).favorites,
  ),
);
check(
  'settings: only valid presets are kept, no duplicates, at most 30',
  (() => {
    const list = Array.from({ length: 80 }, (_, i) => ({
      name: 'mine ' + (i % 45),
      frame: 'zoom = 1;',
    })).concat([{ name: 'broken', frame: 'zoom = (' }, 5, null]);
    const s = C.sanitize({ userPresets: list });
    return (
      s.userPresets.length === 30 &&
      new Set(s.userPresets.map(p => p.name)).size === 30 &&
      !s.userPresets.some(p => p.name === 'broken')
    );
  })(),
);
check(
  'settings: a __proto__ payload changes nothing',
  (() => {
    C.sanitize(JSON.parse('{"__proto__": {"polluted": true}, "on": false}'));
    return {}.polluted === undefined && C.sanitize(JSON.parse('{"on": false}')).on === false;
  })(),
);
check(
  'settings: keys are per person and bounded',
  C.storageKey('nova') !== C.storageKey('friend') &&
    C.storageKey() === C.storageKey('guest') &&
    C.storageKey('x'.repeat(500)).length <= 'falsehorizon.v1.'.length + 64,
);
function fakeStore(initial = {}) {
  const m = { ...initial };
  return {
    getItem: k => (k in m ? m[k] : null),
    setItem: (k, v) => {
      m[k] = v;
    },
    m,
  };
}
const st = fakeStore();
check(
  'storage: round trip per person',
  C.saveSettings(st, 'nova', { on: false, intensity: 30, covers: 10, favorites: ['Slow Tide'] }) &&
    (s =>
      s.on === false && s.intensity === 30 && s.covers === 10 && s.favorites[0] === 'Slow Tide')(
      C.loadSettings(st, 'nova'),
    ),
);
check(
  'storage: another person on the same browser has their own settings',
  C.loadSettings(st, 'friend').intensity === D.intensity,
);
check(
  'storage: only valid values are ever written',
  (() => {
    C.saveSettings(st, 'x', { on: 'maybe', intensity: 900 });
    const w = JSON.parse(st.m[C.storageKey('x')]);
    return w.on === true && w.intensity === 100;
  })(),
);
check(
  'storage: broken JSON gives the defaults, tampered values are cleaned',
  C.loadSettings(fakeStore({ [C.storageKey('n')]: '{not json' }), 'n').intensity === D.intensity &&
    C.loadSettings(
      fakeStore({ [C.storageKey('n')]: JSON.stringify({ intensity: 500, mode: 'x' }) }),
      'n',
    ).intensity === 100,
);
const blocked = {
  getItem() {
    throw new Error('denied');
  },
  setItem() {
    throw new Error('denied');
  },
};
check(
  'storage: a browser that blocks storage, or none at all, still works',
  C.loadSettings(blocked, 'n').intensity === D.intensity &&
    C.saveSettings(blocked, 'n', D) === false &&
    C.loadSettings(null, 'n').intensity === D.intensity &&
    C.saveSettings(null, 'n', D) === false,
);
check(
  'storage: a person with nothing stored gets visuals on',
  C.loadSettings(fakeStore(), 'brand-new').on === true,
);

// ================= your own presets =================
let me = C.sanitize(null);
const mine = { name: 'My Sky', frame: 'zoom = 1.01;' };
const s1 = C.savePreset(me, mine);
check(
  'mine: a preset can be saved under a new name',
  s1.ok && s1.settings.userPresets.length === 1 && s1.settings.userPresets[0].name === 'My Sky',
);
check(
  'mine: the same name again replaces it',
  (r =>
    r.ok &&
    r.settings.userPresets.length === 1 &&
    r.settings.userPresets[0].frame === 'zoom = 1.02;')(
    C.savePreset(s1.settings, { ...mine, frame: 'zoom = 1.02;' }),
  ),
);
check(
  'mine: a built-in name is refused, whatever the capitals',
  !C.savePreset(me, { name: 'Slow Tide' }).ok &&
    !C.savePreset(me, { name: 'slow tide' }).ok &&
    /built-in/i.test(C.savePreset(me, { name: 'SLOW TIDE' }).error),
);
check(
  'mine: a broken preset is refused with a reason',
  (r => !r.ok && /formula/.test(r.error))(C.savePreset(me, { name: 'x', frame: 'a = (' })),
);
check(
  'mine: no more than 30',
  (() => {
    let s = me;
    for (let i = 0; i < 30; i++) s = C.savePreset(s, { name: 'p' + i }).settings;
    const r = C.savePreset(s, { name: 'one more' });
    return (
      s.userPresets.length === 30 &&
      !r.ok &&
      /30/.test(r.error) &&
      C.savePreset(s, { name: 'p3', frame: 'zoom = 1;' }).ok
    );
  })(),
);
check(
  'mine: deleting removes it and its favourite mark',
  (() => {
    let s = C.toggleFavorite(s1.settings, 'My Sky');
    const gone = C.deletePreset(s, 'My Sky');
    return (
      s.favorites.includes('My Sky') &&
      gone.userPresets.length === 0 &&
      !gone.favorites.includes('My Sky')
    );
  })(),
);
check(
  'mine: favourites toggle on and off',
  (() => {
    const a = C.toggleFavorite(me, 'Heartbeat'),
      b = C.toggleFavorite(a, 'Heartbeat');
    return a.favorites.join() === 'Heartbeat' && b.favorites.length === 0;
  })(),
);

if (fails) {
  console.log(`\n${fails} failing`);
  process.exit(1);
}
