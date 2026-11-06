// Unit tests for static/dejavuamp-core.js. Run by tests/test_radio.py (needs Node); prints "ok
// ..." / "BAD ..." per check.
const C = require('../static/dejavuamp-core.js');
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
const D = C.sanitize(null);

// ---- volume in decibels
check('dB: 0 dB is full volume', C.dbToGain(0) === 1);
check('dB: -6 dB is about half the amplitude', near(C.dbToGain(-6), 0.501187, 1e-4));
check('dB: -20 dB is a tenth', near(C.dbToGain(-20), 0.1));
check('dB: at or below the floor is silence', C.dbToGain(-60) === 0 && C.dbToGain(-200) === 0);
check('dB: nothing goes above full volume', C.dbToGain(12) === 1);
check(
  'dB: odd input means silence, never a blast',
  [NaN, Infinity, -Infinity, undefined, null, 'loud'].every(v => C.dbToGain(v) === 0),
);
check(
  'dB: readable text',
  C.formatDb(-60) === 'muted' &&
    C.formatDb(0) === '0.0 dB' &&
    C.formatDb(-0.01) === '0.0 dB' &&
    C.formatDb(-12.34) === '-12.3 dB' &&
    C.signedDb(3) === '+3.0 dB' &&
    C.signedDb(-1.5) === '-1.5 dB',
);

// ---- balance
check(
  'balance: -100..100 maps to -1..1 and is clamped',
  C.balanceToPan(-100) === -1 &&
    C.balanceToPan(50) === 0.5 &&
    C.balanceToPan(999) === 1 &&
    C.balanceToPan(NaN) === 0 &&
    C.balanceToPan('x') === 0,
);
check(
  'balance: readable text',
  C.formatBalance(0) === 'center' &&
    C.formatBalance(-30) === '30% left' &&
    C.formatBalance(100) === '100% right',
);

// ---- the ten bands and the presets
check(
  'ten bands from 60 Hz to 16 kHz, with labels',
  C.BANDS_HZ.length === 10 &&
    C.BAND_LABELS.length === 10 &&
    C.BANDS_HZ[0] === 60 &&
    C.BANDS_HZ[9] === 16000 &&
    C.BANDS_HZ.every((f, i) => i === 0 || f > C.BANDS_HZ[i - 1]),
);
check(
  'filters: lowest is a low shelf, highest a high shelf, the eight between are bells',
  (s =>
    s.length === 10 &&
    s[0].type === 'lowshelf' &&
    s[9].type === 'highshelf' &&
    s.slice(1, 9).every(x => x.type === 'peaking') &&
    s.every((x, i) => x.frequency === C.BANDS_HZ[i] && x.Q > 0))(C.filterSpecs()),
);
check(
  'presets: 19 of them, the first is Flat, names are unique',
  C.PRESETS.length === 19 &&
    C.PRESETS[0].name === 'Flat' &&
    new Set(C.PRESETS.map(p => p.name)).size === 19,
);
check(
  'presets: every one has ten bands and stays inside +-12 dB (bands and preamp)',
  C.PRESETS.every(
    p =>
      p.bands.length === 10 &&
      p.bands.every(v => Math.abs(v) <= C.EQ_LIMIT && v * 2 === Math.round(v * 2)) &&
      Math.abs(p.preamp) <= C.EQ_LIMIT,
  ),
);
check(
  'presets: the loud curves lower the preamp so they do not clip',
  C.PRESETS.filter(p => Math.max(...p.bands) >= 6).every(p => p.preamp <= -2),
  C.PRESETS.filter(p => Math.max(...p.bands) >= 6 && p.preamp > -2).map(p => p.name),
);
check(
  'presets: Flat really is flat; Full bass lifts the lows and cuts the highs',
  C.PRESETS[0].bands.every(v => v === 0) &&
    (p => p.bands[0] > 0 && p.bands[9] < 0)(C.presetByName('Full bass')) &&
    (p => p.bands[0] < 0 && p.bands[9] > 0)(C.presetByName('Full treble')),
);
check(
  'presets: lookup by name, unknown is null',
  C.presetByName('Rock').name === 'Rock' &&
    C.presetByName('nope') === null &&
    C.presetByName('__proto__') === null,
);
check(
  'presets are frozen (the app cannot change a built-in by accident)',
  Object.isFrozen(C.PRESETS) &&
    Object.isFrozen(C.PRESETS[1]) &&
    Object.isFrozen(C.PRESETS[1].bands),
);

// ---- settings are never trusted
check(
  'settings: nothing stored gives the defaults, and the matching preset is Flat',
  JSON.stringify(C.sanitize(undefined)) === JSON.stringify(D) &&
    D.volumeDb === -2 &&
    D.preset === 'Flat' &&
    D.eqOn === true &&
    D.limiter === true &&
    D.mono === false &&
    D.balance === 0,
);
for (const junk of ['text', 5, true, [], [1, 2], () => 1])
  check(
    `settings: ${JSON.stringify(junk)} is ignored`,
    JSON.stringify(C.sanitize(junk)) === JSON.stringify(D),
  );
const wild = C.sanitize({
  volumeDb: 99,
  balance: 500,
  speed: 7,
  preamp: -99,
  bands: [99, -99, 'x', NaN, Infinity, 1.26, 2, 3, 4, 5, 6, 7],
  mono: 'yes',
  eqOn: 1,
  limiter: 'no',
  timeMode: 'sideways',
});
check(
  'settings: out-of-range values are pulled into range, bad types fall back',
  wild.volumeDb === 0 &&
    wild.balance === 100 &&
    wild.speed === 2 &&
    wild.preamp === -12 &&
    wild.bands.length === 10 &&
    wild.bands[0] === 12 &&
    wild.bands[1] === -12 &&
    wild.bands[2] === 0 &&
    wild.bands[3] === 0 &&
    wild.bands[4] === 0 &&
    wild.bands[5] === 1.5 &&
    wild.mono === false &&
    wild.eqOn === true &&
    wild.limiter === true &&
    wild.timeMode === 'elapsed',
  wild,
);
check(
  'settings: speeds snap to the real choices',
  [
    [1.3, 1.25],
    [0.1, 0.5],
    [0.8, 0.75],
    [1.9, 2],
    ['fast', 1],
    [1, 1],
  ].every(([a, b]) => C.sanitize({ speed: a }).speed === b),
);
check(
  'settings: volume to a tenth of a dB, bands and preamp to half a dB, balance to a whole number',
  C.sanitize({ volumeDb: -12.345 }).volumeDb === -12.3 &&
    C.sanitize({ preamp: 3.3 }).preamp === 3.5 &&
    C.sanitize({ balance: 12.6 }).balance === 13,
);
check(
  'settings: too few or too many bands are padded or cut to ten',
  C.sanitize({ bands: [1, 2] }).bands.length === 10 &&
    C.sanitize({ bands: new Array(40).fill(3) }).bands.length === 10 &&
    C.sanitize({ bands: 'abc' }).bands.every(v => v === 0),
);
check(
  'settings: a __proto__ payload changes nothing',
  (() => {
    C.sanitize(JSON.parse('{"__proto__": {"polluted": true}, "mono": true}'));
    return {}.polluted === undefined && C.sanitize(JSON.parse('{"mono": true}')).mono === true;
  })(),
);
check(
  'settings: the preset name is worked out from the sliders, never trusted from storage',
  C.sanitize({ preset: 'Rock', bands: new Array(10).fill(0) }).preset === 'Flat' &&
    C.sanitize({ bands: [1, 0, 0, 0, 0, 0, 0, 0, 0, 0] }).preset === null &&
    (r => C.sanitize({ preamp: r.preamp, bands: r.bands }).preset === 'Rock')(
      C.presetByName('Rock'),
    ),
);
check(
  'settings: the result is a fresh object each time (nobody can mutate the defaults)',
  C.sanitize(null).bands !== C.sanitize(null).bands && C.sanitize(null) !== C.sanitize(null),
);

// ---- the equalizer switch and presets
const rock = C.presetByName('Rock');
const onS = C.sanitize({ preamp: rock.preamp, bands: rock.bands, eqOn: true }),
  offS = C.sanitize({ preamp: rock.preamp, bands: rock.bands, eqOn: false });
check(
  'eq off: every band and the preamp are zero, but the sliders keep their values',
  C.effectiveBands(offS).every(v => v === 0) &&
    C.effectivePreamp(offS) === 0 &&
    offS.bands[0] === 5 &&
    C.effectiveBands(onS)[0] === 5 &&
    C.effectivePreamp(onS) === -2,
);

let s = C.sanitize({ preamp: -1, bands: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] });
let r = C.savePreset(s, '  My Curve  ');
check(
  'save preset: it is stored, trimmed, and becomes the matching preset',
  r.ok &&
    r.settings.userPresets.length === 1 &&
    r.settings.userPresets[0].name === 'My Curve' &&
    r.settings.preset === 'My Curve',
  r,
);
s = r.settings;
r = C.savePreset({ ...s, bands: [0, 0, 0, 0, 0, 0, 0, 0, 0, 12] }, 'My Curve');
check(
  'save preset: the same name replaces, it does not add',
  r.ok && r.settings.userPresets.length === 1 && r.settings.userPresets[0].bands[9] === 12,
);
check(
  'save preset: an empty name, a built-in name and a name of only control characters are refused',
  !C.savePreset(s, '   ').ok &&
    !C.savePreset(s, 'Rock').ok &&
    !C.savePreset(s, '\u0007<>').ok &&
    /built-in/.test(C.savePreset(s, 'Rock').error),
);
check(
  'save preset: names are cut to 24 characters and stripped of angle brackets',
  C.savePreset(s, 'x'.repeat(40)).settings.userPresets.at(-1).name.length === 24 &&
    C.savePreset(s, '<b>Hi</b>').settings.userPresets.at(-1).name === 'bHi/b',
);
let many = s;
for (let i = 0; i < 25; i++) {
  const q = C.savePreset(many, 'P' + i);
  if (q.ok) many = q.settings;
}
check(
  'save preset: at most 20 are kept, and the 21st is refused with a clear message',
  many.userPresets.length === 20 && /20/.test(C.savePreset(many, 'One more').error || ''),
  many.userPresets.length,
);
check(
  'delete preset: removes it and the sliders then match nothing',
  (d => d.userPresets.length === 0 && d.preset === null)(C.deletePreset(s, 'My Curve')),
);
check(
  'user presets with a built-in name, duplicates or junk are dropped when loading',
  C.sanitize({
    userPresets: [
      { name: 'Rock', bands: [] },
      { name: 'A' },
      { name: 'A' },
      null,
      5,
      { name: '' },
      { bands: [1] },
    ],
  })
    .userPresets.map(p => p.name)
    .join() === 'A',
);

// ---- preset files
const exported = C.exportPreset({
  name: 'Mine',
  preamp: -1,
  bands: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
});
const back = C.parsePresetFile(exported);
check(
  'export then import gives the same preset',
  back.ok &&
    back.preset.name === 'Mine' &&
    back.preset.preamp === -1 &&
    back.preset.bands.join() === '1,2,3,4,5,6,7,8,9,10' &&
    JSON.parse(exported)['dva-eq'] === 1,
);
check(
  'import: refuses text that is not JSON, or JSON without the marker, or too big',
  !C.parsePresetFile('{nope').ok &&
    /JSON/.test(C.parsePresetFile('{nope').error) &&
    !C.parsePresetFile('{"name":"a","bands":[]}').ok &&
    !C.parsePresetFile(' '.repeat(C.MAX_FILE + 1)).ok,
);
const file = o =>
  JSON.stringify({
    'dva-eq': 1,
    name: 'A',
    preamp: 0,
    bands: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    ...o,
  });
check(
  'import: needs exactly ten numeric bands and a name',
  !C.parsePresetFile(file({ bands: [0, 0] })).ok &&
    !C.parsePresetFile(file({ bands: new Array(11).fill(0) })).ok &&
    !C.parsePresetFile(file({ bands: new Array(10).fill('3') })).ok &&
    !C.parsePresetFile(file({ bands: [null, 0, 0, 0, 0, 0, 0, 0, 0, 0] })).ok &&
    !C.parsePresetFile(file({ name: '  ' })).ok &&
    !C.parsePresetFile(file({ name: 5 })).ok,
);
check(
  'import: out-of-range values are clamped, not trusted',
  (p => p.ok && p.preset.bands[0] === 12 && p.preset.bands[1] === -12 && p.preset.preamp === 12)(
    C.parsePresetFile(file({ preamp: 99, bands: [99, -99, 0, 0, 0, 0, 0, 0, 0, 0] })),
  ),
);
check(
  'import: a prototype-pollution payload does not crash or pollute',
  (() => {
    const q = C.parsePresetFile(
      '{"dva-eq":1,"__proto__":{"x":1},"name":"a","bands":[0,0,0,0,0,0,0,0,0,0]}',
    );
    return q.ok && {}.x === undefined;
  })(),
);

// ---- storage per person
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
  C.saveSettings(st, 'nova', {
    ...D,
    volumeDb: -10,
    balance: -20,
    mono: true,
    speed: 1.5,
    preamp: -3,
    bands: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
  }) &&
    (x =>
      x.volumeDb === -10 &&
      x.balance === -20 &&
      x.mono === true &&
      x.speed === 1.5 &&
      x.preamp === -3 &&
      x.bands[9] === 10)(C.loadSettings(st, 'nova')),
);
check(
  'storage: another person on the same browser has their own settings',
  C.loadSettings(st, 'friend').volumeDb === D.volumeDb &&
    C.storageKey('nova') !== C.storageKey('friend') &&
    C.storageKey() === C.storageKey('guest') &&
    C.storageKey('x'.repeat(500)).length <= C.storageKey('').length + 64,
);
check(
  'storage: only valid values are ever written',
  (() => {
    C.saveSettings(st, 'x', { volumeDb: 'a lot', speed: 9, bands: 'no' });
    const w = JSON.parse(st.m[C.storageKey('x')]);
    return w.volumeDb === D.volumeDb && w.speed === 2 && w.bands.length === 10;
  })(),
);
check(
  'storage: broken JSON, tampered values, blocked storage and no storage all give safe settings',
  C.loadSettings(fakeStore({ [C.storageKey('n')]: '{not json' }), 'n').volumeDb === D.volumeDb &&
    C.loadSettings(
      fakeStore({ [C.storageKey('n')]: JSON.stringify({ volumeDb: 500, speed: 1.3 }) }),
      'n',
    ).volumeDb === 0 &&
    C.loadSettings(
      {
        getItem() {
          throw new Error('denied');
        },
      },
      'n',
    ).volumeDb === D.volumeDb &&
    C.loadSettings(null, 'n').volumeDb === D.volumeDb &&
    C.saveSettings(null, 'n', D) === false,
);

// ---- the player window's text
check(
  'time: mm:ss, and h:mm:ss past an hour',
  C.formatTime(0) === '00:00' &&
    C.formatTime(65.9) === '01:05' &&
    C.formatTime(3599) === '59:59' &&
    C.formatTime(3661) === '1:01:01',
);
check(
  'time: remaining has a minus sign; unknown lengths show dashes instead of NaN',
  C.formatTime(65, true) === '-01:05' &&
    C.formatTime(NaN) === '--:--' &&
    C.formatTime(Infinity, true) === '-:--' &&
    C.formatTime(-5) === '--:--' &&
    C.formatTime(undefined) === '--:--',
);
check(
  'ticker: artist - title, with missing parts left out and control characters removed',
  C.tickerText('Song', 'Band') === 'Band - Song' &&
    C.tickerText('Song', '') === 'Song' &&
    C.tickerText('', 'Band') === 'Band' &&
    C.tickerText('', '') === 'DejaVuAmp' &&
    C.tickerText('a\nb\u0000c', 'x') === 'x - a b c' &&
    C.tickerText('y'.repeat(500), '').length === 200,
);

console.log(fails ? 'FAILED' : 'ALL PASSED');
process.exit(fails ? 1 : 0);
