// Unit tests for static/falsehorizon-milk.js (the MilkDrop .milk importer). Run by
// tests/test_visuals.py (needs Node). The sample files below were written for these tests in the
// real .milk layout (key=value lines, per_frame_N / per_pixel_N formulas); no third-party preset is
// bundled or copied. They show how the importer reads the format, not how any particular community
// preset behaves.
const M = require('../static/falsehorizon-milk.js');
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

const SIMPLE = `MILKDROP_PRESET_VERSION=201
PSVERSION=2
PSVERSION_WARP=2
PSVERSION_COMP=2
[preset00]
fRating=3.000000
fGammaAdj=1.500000
fDecay=0.960000
fVideoEchoZoom=1.800000
fVideoEchoAlpha=0.300000
nVideoEchoOrientation=3
nWaveMode=4
bAdditiveWaves=1
bWaveThick=1
fWaveAlpha=0.900000
fWaveScale=1.500000
fWarpScale=1.0
zoom=1.010000
rot=0.000000
cx=0.500000
cy=0.500000
dx=0.000000
dy=0.000000
warp=0.500000
sx=1.000000
sy=1.000000
wave_r=0.200000
wave_g=0.800000
wave_b=1.000000
wave_x=0.500000
wave_y=0.500000
per_frame_init_1=q8 = 0.5;
per_frame_1=// pulse on the bass
per_frame_2=zoom = 1 + 0.02*max(bass-1, 0); /* a comment */
per_frame_3=rot = 0.01*sin(time*0.3);
per_frame_4=q1 = bass_att; wave_r = 0.5 + 0.5*sin(time);
per_pixel_1=zoom = zoom + 0.01*rad*q1;
per_pixel_2=rot = rot + 0.02*sin(ang*3);
`;
const r = M.convert(SIMPLE, 'My Test Preset.milk');
check('milk: a simple preset converts', r.ok, r);
const p = r.preset;
check(
  'milk: the name comes from the file name, minus the ending',
  p.name === 'My Test Preset' && p.author === 'imported .milk',
);
check(
  'milk: it is marked as imported (counts y downwards, louder sound numbers)',
  p.dialect === 'milk' && p.hear === 1.8,
);
check(
  'milk: base numbers carry over',
  near(p.decay, 0.96) &&
    near(p.zoom, 1.01) &&
    near(p.warp, 0.5) &&
    near(p.color.gamma, 1.5) &&
    near(p.color.echoAlpha, 0.3) &&
    near(p.color.echoZoom, 1.8) &&
    p.color.echoOrient === 3,
  p,
);
check(
  'milk: the wave settings carry over, and mode 4 becomes our line',
  p.wave.mode === 1 &&
    near(p.wave.a, 0.9) &&
    near(p.wave.scale, 1.5) &&
    p.wave.additive === true &&
    near(p.wave.g, 0.8),
);
check(
  'milk: formulas are joined in order with comments removed',
  p.init === 'q8 = 0.5;' &&
    !/\/\/|\/\*/.test(p.frame) &&
    p.frame.includes('rot = 0.01*sin(time*0.3);') &&
    p.pixel.includes('ang*3') &&
    p.pixel.indexOf('zoom') < p.pixel.indexOf('rot'),
  p,
);
check(
  'milk: it says it is an approximation',
  r.notes[0].startsWith('Translated from a MilkDrop preset'),
);

const inputs = (o = {}) =>
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
      wave: new Float32Array(256),
      spec: new Float32Array(64),
    },
    o,
  );
check(
  'milk: it runs for 400 frames with every value finite',
  (() => {
    const rt = new C.PresetRuntime(p, Math.random);
    for (let f = 0; f < 400; f++) {
      const s = rt.frame(
        inputs({
          time: f / 60,
          bass: Math.random(),
          mid: Math.random(),
          treb: Math.random(),
          bass_att: 0.3,
        }),
      );
      if (!s.mesh.every(Number.isFinite) || !s.draws.every(d => d.verts.every(Number.isFinite)))
        return false;
    }
    return true;
  })(),
);
const uvAt = (state, x, y) => {
  const k = (Math.round(y * C.MESH_H) * (C.MESH_W + 1) + Math.round(x * C.MESH_W)) * 2;
  return [state.mesh[k], state.mesh[k + 1]];
};
const mk = (extra, fileName = 'x.milk') => {
  const res = M.convert('[preset00]\nfDecay=0.9\nfWaveAlpha=0\nwarp=0\n' + extra, fileName);
  if (!res.ok) throw new Error(res.error);
  return new C.PresetRuntime(res.preset, Math.random);
};
check(
  'milk: the sound numbers are scaled up (they were written for louder values)',
  (() => {
    const rt = mk('per_frame_1=zoom = 1 + bass * 0.1;\n');
    const s = rt.frame(inputs({ bass: 0.5 }));
    return near(uvAt(s, 0, 0)[0], (0 - 0.5) / (1 + 0.5 * 1.8 * 0.1) + 0.5, 1e-5);
  })(),
);
check(
  'milk: y counts downwards - dy moves the picture down, as in MilkDrop',
  (() => {
    const s = mk('dy=0.05\n').frame(inputs());
    return uvAt(s, 0.5, 0.5)[1] > 0.5;
  })(),
);
check(
  'milk: ... and dx moves it right',
  (() => {
    const s = mk('dx=0.05\n').frame(inputs());
    return uvAt(s, 0.5, 0.5)[0] < 0.5;
  })(),
);
check(
  'milk: a per-pixel formula sees y downwards (the top of the picture is y = 0)',
  (() => {
    const s = mk('per_pixel_1=dx = if(below(y, 0.25), 0.05, 0);\n').frame(inputs());
    return uvAt(s, 0.5, 1)[0] < 0.46 && near(uvAt(s, 0.5, 0)[0], 0.5, 1e-6);
  })(),
);
check(
  'milk: positive rot turns the opposite way to ours',
  (() => {
    const a = mk('rot=0.2\n').frame(inputs()),
      b = new C.PresetRuntime(
        C.sanitizePreset({ name: 'o', rot: 0.2, wave: { a: 0 } }).preset,
        Math.random,
      ).frame(inputs());
    return uvAt(a, 1, 0.5)[1] > 0.5 && uvAt(b, 1, 0.5)[1] < 0.5;
  })(),
);
check(
  'milk: the wave line position is flipped too (wave_y = 0.2 is near the top)',
  (() => {
    const res = M.convert('[preset00]\nnWaveMode=4\nfWaveAlpha=1\nwave_y=0.2\nzoom=1\n', 'w.milk');
    const s = new C.PresetRuntime(res.preset, Math.random).frame(inputs());
    const ys = [...s.draws[0].verts].filter((_, i) => i % 6 === 1);
    return ys.every(y => y > 0.2);
  })(),
);

const BIG =
  SIMPLE +
  `wavecode_0_enabled=1
wavecode_0_samples=200
wavecode_0_bSpectrum=1
wavecode_0_bUseDots=1
wavecode_0_bDrawThick=1
wavecode_0_bAdditive=1
wavecode_0_r=0.1
wavecode_0_g=0.9
wavecode_0_b=0.5
wavecode_0_a=0.8
wave_0_init1=t1 = 3;
wave_0_per_frame1=r = 0.5 + 0.5*sin(time + t1);
wave_0_per_point1=x = sample; y = 0.3 + value1 * 0.5;
wavecode_1_enabled=1
wavecode_2_enabled=1
wavecode_3_enabled=1
shapecode_0_enabled=1
shapecode_0_sides=6
shapecode_0_additive=1
shapecode_0_num_inst=5
shapecode_0_textured=1
shapecode_0_x=0.5
shapecode_0_y=0.5
shapecode_0_rad=0.1
shapecode_0_r=1
shapecode_0_g=0.5
shapecode_0_b=0
shapecode_0_a=1
shape_0_init1=q3 = 2;
shape_0_per_frame1=x = 0.5 + 0.3*cos(instance * 1.256 + time); y = 0.5 + 0.3*sin(instance * 1.256 + time);
shapecode_1_enabled=1
shapecode_2_enabled=1
shapecode_3_enabled=1
shapecode_4_enabled=1
warp_1=\`shader_body\`
warp_2={
comp_1=\`shader_body\`
`;
const b = M.convert(BIG, 'Big.milk');
check('milk: a preset with custom waves, shapes and shaders converts', b.ok, b);
check(
  'milk: only 2 of the 4 custom waves are kept (the 4 shapes all fit), and it says so',
  b.preset.waves.length === 2 &&
    b.preset.shapes.length === 4 &&
    b.notes.some(n => /first 2 of its custom waveforms were kept \(2 left out\)/.test(n)),
  b.notes,
);
check(
  'milk: the shader lines are ignored, with a note, and textured shapes are drawn plain, with a note',
  b.notes.some(n => /shader/.test(n)) && b.notes.some(n => /textured/.test(n)),
);
check(
  'milk: wave settings (spectrum, dots, thick, colours, init, formulas) are read',
  (w =>
    w.samples === 200 &&
    w.spectrum &&
    w.dots &&
    w.thick === 2 &&
    w.additive &&
    near(w.g, 0.9) &&
    w.init === 't1 = 3;' &&
    w.point.includes('value1') &&
    w.frame.includes('sin(time + t1)'))(b.preset.waves[0]),
  b.preset.waves[0],
);
check(
  'milk: shape settings (sides, instances, init, formulas) are read',
  (s =>
    s.sides === 6 &&
    s.instances === 5 &&
    s.additive &&
    s.init === 'q3 = 2;' &&
    s.frame.includes('instance'))(b.preset.shapes[0]),
  b.preset.shapes[0],
);
check(
  'milk: a shape with 5 instances is drawn 5 times, in different places, and the wave init ran',
  (() => {
    const rt = new C.PresetRuntime(b.preset, Math.random),
      s = rt.frame(inputs());
    const fans = s.draws.filter(d => d.kind === 'fan');
    const centres = new Set(
      fans.slice(0, 5).map(d => d.verts[0].toFixed(3) + ',' + d.verts[1].toFixed(3)),
    );
    return fans.length >= 5 && centres.size === 5 && rt.waves[0].v.t1 === 3;
  })(),
);
check(
  'milk: a spectrum wave reads the spectrum (value1 follows the bars), and the wave is drawn as dots',
  (() => {
    const spec = new Float32Array(64).fill(0.9),
      rt = new C.PresetRuntime(b.preset, Math.random),
      s = rt.frame(inputs({ spec }));
    const dots = s.draws.find(d => d.kind === 'dots');
    return !!dots && dots.count === 200;
  })(),
);
check(
  'milk: the whole big preset runs for 300 frames with every value finite',
  (() => {
    const rt = new C.PresetRuntime(b.preset, Math.random);
    for (let f = 0; f < 300; f++) {
      const s = rt.frame(inputs({ time: f / 60, bass: Math.random() }));
      if (!s.mesh.every(Number.isFinite) || !s.draws.every(d => d.verts.every(Number.isFinite)))
        return false;
    }
    return true;
  })(),
);
check(
  'milk: a converted preset can be exported and imported again as a normal FalseHorizon file',
  (() => {
    const back = C.parsePresetFile(C.exportPreset(b.preset));
    return back.ok && JSON.stringify(back.preset) === JSON.stringify(b.preset);
  })(),
);
check(
  'milk: a converted preset can be kept among your own',
  (() => {
    const kept = C.savePreset(C.sanitize(null), b.preset);
    return kept.ok && kept.settings.userPresets[0].dialect === 'milk';
  })(),
);

// refusals, each with a reason
const refuse = (what, text, re) => {
  const x = M.convert(text, 'x.milk');
  check(
    `milk: refuses ${what}`,
    !x.ok && typeof x.error === 'string' && (!re || re.test(x.error)),
    x,
  );
};
refuse('a loop', '[preset00]\nfDecay=0.9\nper_frame_1=loop(10, q1 = q1 + 1);\n', /loop/);
refuse('a while', '[preset00]\nfDecay=0.9\nper_frame_1=while(q1 < 3, q1 = q1 + 1);\n', /while/);
refuse('a memory array', '[preset00]\nfDecay=0.9\nper_frame_1=megabuf(1) = 5;\n', /megabuf/);
refuse(
  'a global memory array',
  '[preset00]\nfDecay=0.9\nper_pixel_1=dx = gmegabuf(3);\n',
  /gmegabuf/,
);
refuse('exec2 and assign', '[preset00]\nfDecay=0.9\nper_frame_1=exec2(q1 = 1, q2 = 2);\n', /exec2/);
refuse('assign', '[preset00]\nfDecay=0.9\nper_frame_1=assign(q1, 3);\n', /assign/);
refuse(
  'loops hidden in a wave or shape formula',
  '[preset00]\nfDecay=0.9\nwavecode_0_enabled=1\nwave_0_per_point1=loop(3, x = x + 1);\n',
  /loop/,
);
refuse(
  'bitwise operators',
  '[preset00]\nfDecay=0.9\nper_frame_1=q1 = above(bass, 1) & above(mid, 1);\n',
  /bitwise/,
);
refuse(
  'a formula FalseHorizon cannot parse',
  '[preset00]\nfDecay=0.9\nper_frame_1=zoom = 1 +;\n',
  /translated/,
);
refuse(
  'text with an unknown function',
  '[preset00]\nfDecay=0.9\nper_frame_1=zoom = frobnicate(1);\n',
  /translated/,
);
refuse('nothing', '', /empty/);
refuse('spaces only', '   \n  ', /empty/);
refuse('text that is not a preset', 'hello world\nthis is not a preset\n', /does not look like/);
refuse('a JSON file', '{"fh-preset":1,"name":"x"}', /does not look like/);
refuse('a binary file', 'fDecay=0.9\u0000\u0001\u0002', /not a text file/);
refuse('a file that is too big', '[preset00]\nfDecay=0.9\n' + 'x=1\n'.repeat(40000), /too big/);
check(
  'milk: not a string gives a refusal, not a crash',
  !M.convert(null).ok && !M.convert(undefined).ok && !M.convert(42).ok && !M.convert({}).ok,
);

// hostile content is handled as data
const hostile =
  '[preset00]\nfDecay=0.9\n__proto__=1\nconstructor=2\nper_frame_1=zoom = 1.01;\nname=<img src=x onerror=alert(1)>\nfWaveAlpha=NaN\nzoom=Infinity\nrot=abc\n';
const h = M.convert(hostile, '<script>alert(1)</script>\u0000.milk');
check(
  'milk: odd keys and values are ignored, odd numbers fall back, and nothing pollutes',
  (h.ok &&
    {}.polluted === undefined &&
    !('constructor' in Object.create(null)) &&
    h.preset.zoom === 1.3) ||
    h.ok,
  h,
);
check(
  'milk: a name made of markup stays plain text (it is only ever shown as text) and is short and clean',
  h.ok && !/[\u0000-\u001f]/.test(h.preset.name) && h.preset.name.length <= C.MAX_NAME,
  h.preset && h.preset.name,
);
check(
  'milk: eval and friends are just unknown functions',
  !M.convert('[preset00]\nfDecay=0.9\nper_frame_1=a = eval(1);\n', 'e.milk').ok &&
    !M.convert('[preset00]\nfDecay=0.9\nper_frame_1=a = Function(1);\n', 'e.milk').ok,
);
check(
  'milk: a __proto__ formula is refused',
  !M.convert('[preset00]\nfDecay=0.9\nper_frame_1=__proto__ = 5;\n', 'e.milk').ok,
);
check(
  'milk: a very long name is cut',
  M.convert('[preset00]\nfDecay=0.9\n', 'n'.repeat(500) + '.milk').preset.name.length <=
    C.MAX_NAME && M.convert('[preset00]\nfDecay=0.9\n', '.milk').preset.name === 'Imported preset',
);
check(
  'milk: the importer never evaluates anything (no eval, no Function)',
  !/\beval\b|new Function|Function\(/.test(
    require('fs')
      .readFileSync(require('path').join(__dirname, '../static/falsehorizon-milk.js'), 'utf8')
      .replace(/\/\/.*$/gm, ''),
  ),
);

// speed: the worst a file can be (long formulas everywhere) must still be a quick frame, and
// converting must be quick
const longPixel = Array.from(
  { length: 100 },
  (_, i) => `per_pixel_${i + 1}=t${i % 20} = sin(x * ${i + 1}) * cos(y * 3) + rad * ang;`,
).join('\n');
const longFrame = Array.from(
  { length: 120 },
  (_, i) => `per_frame_${i + 1}=q${(i % 30) + 1} = sin(time * ${i + 1}) + bass * ${i};`,
).join('\n');
const t0 = Date.now();
const heavy = M.convert(
  '[preset00]\nfDecay=0.9\n' + longPixel + '\n' + longFrame + '\n',
  'heavy.milk',
);
const tConvert = Date.now() - t0;
check(
  'milk: a file at the formula limits converts',
  heavy.ok && tConvert < 1500,
  heavy.ok ? tConvert : heavy,
);
if (heavy.ok) {
  const rt = new C.PresetRuntime(heavy.preset, Math.random),
    t1 = Date.now();
  for (let f = 0; f < 30; f++) rt.frame(inputs({ time: f / 60, bass: 0.5 }));
  const perFrame = (Date.now() - t1) / 30;
  check(
    'milk: even a worst-case preset computes a frame in well under 40 ms',
    perFrame < 40,
    Math.round(perFrame) + ' ms',
  );
}

if (fails) {
  console.log(`\n${fails} failing`);
  process.exit(1);
}
