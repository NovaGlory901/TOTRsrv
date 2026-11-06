// Node test for static/theme.js (run by tests/test_settings.py). Prints "ok ..." / "BAD ..." lines
// and "ALL PASSED" / "FAILED".
const path = require('path');
let fails = 0;
const check = (name, cond, extra) => {
  if (!cond) fails++;
  console.log((cond ? 'ok  ' : 'BAD ') + name + (!cond && extra ? '   <- ' + extra : ''));
};

const storage = new Map();
function load(user) {
  const attrs = {},
    props = {};
  global.document = {
    querySelector: sel => (sel === 'meta[name="totr-user"]' && user ? { content: user } : null),
    documentElement: {
      style: {
        setProperty: (k, v) => {
          props[k] = v;
        },
        colorScheme: '',
      },
      setAttribute: (k, v) => {
        attrs[k] = v;
      },
      removeAttribute: k => {
        delete attrs[k];
      },
    },
  };
  global.window = {
    localStorage: {
      getItem: k => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
    },
  };
  const file = path.join(__dirname, '..', 'static', 'theme.js');
  delete require.cache[require.resolve(file)];
  return { T: require(file), attrs, props, root: global.document.documentElement };
}

let { T, attrs, props, root } = load('nova');

// ---- the built-in themes ----
check('there are six built-in themes', T.BUILTIN.length === 6, T.BUILTIN.length);
check(
  'every built-in theme passes its own validation (readable text, valid colors)',
  T.BUILTIN.every(t => T.validate({ name: t.name, logo: t.logo, colors: t.colors }).ok),
  T.BUILTIN.map(
    t => t.id + ':' + T.validate({ name: t.name, logo: t.logo, colors: t.colors }).error,
  ).join(' | '),
);
const by = id => T.BUILTIN.find(t => t.id === id);
check(
  'the default theme is the original Dreamspace colors',
  by('dreamspace').colors.bg === '#0b0720' &&
    by('dreamspace').colors.pink === '#ff9de6' &&
    T.BUILTIN[0].id === 'dreamspace',
);
check(
  'ANSI uses the 16-color ANSI values (black, white, bright magenta/cyan, dim #aaaaaa)',
  by('ansi').colors.bg === '#000000' &&
    by('ansi').colors.fg === '#ffffff' &&
    by('ansi').colors.pink === '#ff55ff' &&
    by('ansi').colors.cyan === '#55ffff' &&
    by('ansi').colors.dim === '#aaaaaa',
);
check(
  'phosphor green is green on near-black',
  by('phosphor').colors.fg === '#33ff33' && by('phosphor').colors.bg === '#010a01',
);
check(
  'amber is monochrome amber and swaps the logo to the iris mark',
  by('amber').logo === 'aperture' &&
    by('amber').colors.fg === '#ffb000' &&
    T.BUILTIN.filter(t => t.logo === 'aperture').length === 1,
);
check(
  'the light and high-contrast themes turn the glow and scanlines off',
  by('paper').glow === false && by('paper').scan === false && by('contrast').glow === false,
);
check(
  'contrast maths: black on white is 21',
  Math.abs(T.contrast('#000000', '#ffffff') - 21) < 0.01,
);
check('every built-in has a unique id', new Set(T.BUILTIN.map(t => t.id)).size === 6);

// ---- validation never trusts its input ----
const good = () => ({ name: 'Mine', logo: 'text', colors: { ...by('dreamspace').colors } });
const bad = (name, mutate, needle) => {
  const x = good();
  mutate(x);
  const v = T.validate(x);
  check(
    'validate refuses: ' + name,
    !v.ok && (!needle || v.error.includes(needle)),
    JSON.stringify(v),
  );
};
check(
  'validate accepts a good theme and lowercases colors',
  (() => {
    const x = good();
    x.colors.pink = '#FF9DE6';
    const v = T.validate(x);
    return v.ok && v.theme.colors.pink === '#ff9de6';
  })(),
);
bad(
  'a missing color',
  x => {
    delete x.colors.warn;
  },
  'warn',
);
bad(
  'a color that is not #rrggbb (3 digits)',
  x => {
    x.colors.bg = '#fff';
  },
  'bg',
);
bad(
  'CSS smuggled in a color',
  x => {
    x.colors.pink = 'red; background:url(//evil.example/x)';
  },
  'pink',
);
bad(
  'url() in a color',
  x => {
    x.colors.cyan = 'url(javascript:alert(1))';
  },
  'cyan',
);
bad(
  'a color given as a number',
  x => {
    x.colors.fg = 123456;
  },
  'fg',
);
bad(
  'no colors object',
  x => {
    delete x.colors;
  },
  'no colors',
);
bad(
  'an empty name',
  x => {
    x.name = '   ';
  },
  'name',
);
bad(
  'a name that is too long',
  x => {
    x.name = 'x'.repeat(31);
  },
  'name',
);
bad(
  'an unknown logo',
  x => {
    x.logo = 'https://evil.example/logo.svg';
  },
  'logo',
);
bad(
  'text too close to the background',
  x => {
    x.colors.fg = '#0c0820';
  },
  'too close',
);
bad(
  'quiet text too close to the background',
  x => {
    x.colors.dim = '#0e0a24';
  },
  'quiet',
);
bad(
  'error color invisible on the background',
  x => {
    x.colors.bad = '#0b0721';
  },
  'error',
);
check(
  'validate refuses non-objects',
  [null, undefined, 5, 'x', [], true].every(v => !T.validate(v).ok),
);
check(
  'control characters and angle brackets are stripped from names',
  T.validate({ ...good(), name: '<b>Hi\u0007</b>' }).theme.name === 'bHi/b',
);

// ---- applying a theme ----
T.select('dreamspace');
check(
  'apply sets every color variable',
  T.KEYS.every(k => props['--' + k] === by('dreamspace').colors[k]),
  JSON.stringify(props),
);
check(
  'apply sets the rgb triplets the CSS needs for rgba()',
  props['--pink-rgb'] === '255, 157, 230' &&
    props['--cyan-rgb'] === '138, 243, 255' &&
    props['--bg-rgb'] === '11, 7, 32',
);
check(
  'apply derives a darker "deep" background and the on-accent text color',
  /^#[0-9a-f]{6}$/.test(props['--bg-deep']) &&
    props['--on-accent'] === '#0b0720' &&
    T.contrast(props['--bg-deep'], '#ffffff') > T.contrast('#0b0720', '#ffffff'),
);
check(
  'a dark theme sets the dark color-scheme (form controls match)',
  root.style.colorScheme === 'dark',
);
T.select('paper');
check('a light theme sets the light color-scheme', root.style.colorScheme === 'light');
check(
  'a light theme gets pale form fields and a soft screen edge; a dark one the dark tint',
  props['--field-bg'].startsWith('rgba(255') && !props['--vignette'].startsWith('rgba(0,'),
);
T.select('dreamspace');
check(
  'dark themes get dark fields and the black vignette',
  props['--field-bg'] === 'rgba(0, 0, 0, .45)' && props['--vignette'] === 'rgba(0, 0, 0, .7)',
);
T.select('paper');
check('the logo attribute follows the theme', attrs['data-logo'] === 'text');
T.select('amber');
check('amber turns on the iris logo', attrs['data-logo'] === 'aperture');
T.select('ansi');
check(
  'a theme with glow off sets data-glow=off, scanlines stay on',
  attrs['data-glow'] === 'off' && !('data-scanlines' in attrs),
);
T.select('dreamspace');
check(
  'the default look has no "off" flags at all',
  !('data-glow' in attrs) && !('data-scanlines' in attrs) && !('data-motion' in attrs),
);
T.setDisplay({ scanlines: false, glow: false, motion: false });
check(
  'display choices turn scanlines, glow and motion off',
  attrs['data-scanlines'] === 'off' &&
    attrs['data-glow'] === 'off' &&
    attrs['data-motion'] === 'off',
);
T.select('paper');
T.setDisplay({ glow: true });
check(
  'a display choice overrides the theme default (glow on for a theme that has it off)',
  !('data-glow' in attrs),
);
T.setDisplay({ scanlines: null, glow: null, motion: true });
check(
  'null means "whatever the theme says"',
  attrs['data-glow'] === 'off' && attrs['data-scanlines'] === 'off' && !('data-motion' in attrs),
);
T.reset();
check(
  'reset goes back to Dreamspace with no flags',
  T.current() === 'dreamspace' && !('data-glow' in attrs) && !('data-scanlines' in attrs),
);

// ---- custom themes, files, and per-person storage ----
const r1 = T.saveCustom({
  name: 'Ocean',
  logo: 'text',
  colors: { ...by('dreamspace').colors, pink: '#ff00aa' },
});
check(
  'saving a custom theme works, selects it and applies it',
  r1.ok && /^c-ocean-/.test(r1.id) && T.current() === r1.id && props['--pink'] === '#ff00aa',
);
check(
  'it is listed after the built-in ones, marked as not built in',
  T.all().length === 7 && T.all()[6].builtin === false && T.all()[6].name === 'Ocean',
);
const r2 = T.saveCustom(
  { name: 'Ocean v2', logo: 'aperture', colors: { ...by('dreamspace').colors } },
  r1.id,
);
check(
  'saving with an id replaces that theme instead of adding one',
  r2.ok &&
    r2.id === r1.id &&
    T.all().length === 7 &&
    T.find(r1.id).name === 'Ocean v2' &&
    T.find(r1.id).logo === 'aperture',
);
check(
  'an invalid theme is not saved',
  !T.saveCustom({ name: 'Bad', colors: { ...by('dreamspace').colors, fg: '#0c0820' } }).ok &&
    T.all().length === 7,
);
const exported = T.exportTheme(T.find(r1.id));
check(
  'export is plain JSON with the format marker',
  JSON.parse(exported)['totr-theme'] === 1 && JSON.parse(exported).name === 'Ocean v2',
);
const back = T.parseImport(exported);
check(
  'an exported file imports back to the same theme',
  back.ok && back.theme.name === 'Ocean v2' && back.theme.colors.bg === '#0b0720',
);
check(
  'import refuses text that is not JSON',
  !T.parseImport('{nope').ok && /JSON/.test(T.parseImport('{nope').error),
);
check(
  'import refuses JSON without the TOTR marker',
  !T.parseImport(JSON.stringify(good())).ok &&
    /not a TOTR theme/.test(T.parseImport(JSON.stringify(good())).error),
);
check('import refuses a file that is too big', !T.parseImport(' '.repeat(T.MAX_FILE + 1)).ok);
check(
  'import refuses a marked file with bad colors',
  !T.parseImport(
    JSON.stringify({ 'totr-theme': 1, ...good(), colors: { ...good().colors, bg: 'url(x)' } }),
  ).ok,
);
check(
  'import refuses a prototype-pollution style payload without crashing',
  (() => {
    try {
      return !T.parseImport(
        '{"totr-theme":1,"__proto__":{"x":1},"name":"a","colors":{"__proto__":{"bg":"#000000"}}}',
      ).ok;
    } catch (e) {
      return false;
    }
  })(),
);
for (let i = 0; i < 20; i++)
  T.saveCustom({ name: 'T' + i, colors: { ...by('dreamspace').colors } });
check(
  'no more than 12 custom themes can be kept',
  T.all().length === 6 + T.MAX_CUSTOM &&
    /12/.test(
      T.saveCustom({ name: 'One more', colors: { ...by('dreamspace').colors } }).error || '',
    ),
  T.all().length,
);
const del = T.all().at(-1).id;
T.select(del);
T.deleteCustom(del);
check(
  'deleting the selected custom theme falls back to Dreamspace',
  T.current() === 'dreamspace' && !T.all().some(t => t.id === del),
);
check(
  'a tampered stored theme is dropped, not applied',
  (() => {
    storage.set(
      'totr.v1.nova.themes',
      JSON.stringify([
        { id: 'c-evil', name: 'Evil', logo: 'text', colors: { ...good().colors, bg: 'red;x:y' } },
        { id: 'not-c', name: 'X', colors: good().colors },
      ]),
    );
    return !T.all().some(t => t.name === 'Evil' || t.id === 'not-c');
  })(),
);

// a second person on the same browser has their own themes and choice
({ T, attrs, props, root } = load('gast'));
check(
  "another person on the same browser starts from the default and sees none of the first person's themes",
  T.current() === 'dreamspace' && T.all().length === 6,
);
T.select('phosphor');
({ T } = load('nova'));
check("...and choosing a theme does not change the first person's", T.current() === 'dreamspace');

// the sign-in page (no person) looks like the last theme applied here
({ T, attrs, props, root } = load(''));
const stored = JSON.parse(storage.get('totr.v1.last'));
check(
  'the last applied theme is remembered for the sign-in page',
  stored.theme.name && stored.theme.colors.fg,
);
// theme.js applies on load only in a browser; in Node simulate what it does through the exported
// API with the stored snapshot
check('the snapshot is a valid theme', T.validate(stored.theme).ok);

// storage that throws (private mode) must not break anything
global.window = {
  get localStorage() {
    throw new Error('denied');
  },
};
global.document = {
  querySelector: () => null,
  documentElement: { style: { setProperty() {} }, setAttribute() {}, removeAttribute() {} },
};
const file = path.join(__dirname, '..', 'static', 'theme.js');
delete require.cache[require.resolve(file)];
let ok = true;
try {
  const T2 = require(file);
  T2.select('amber');
  T2.saveCustom({ name: 'a', colors: by('dreamspace').colors });
} catch (e) {
  ok = false;
  console.log(e.message);
}
check('with storage blocked, choosing and saving do not throw', ok);

console.log(fails ? 'FAILED' : 'ALL PASSED');
process.exit(fails ? 1 : 0);
