// Themes and display choices. Loaded in <head> of every page (before the page paints), so the
// chosen look is there from the first frame. A theme is 11 colors, a name and a logo. Everything is
// kept in this browser (localStorage), per signed-in person, so nothing about it reaches the
// server. Colors only ever enter the page as validated #rrggbb values set with style.setProperty:
// no custom text becomes CSS.
(() => {
  const KEYS = [
    'bg',
    'bg2',
    'fg',
    'dim',
    'pink',
    'cyan',
    'lilac',
    'border',
    'bezel',
    'warn',
    'bad',
  ];
  const LABELS = {
    bg: 'Background',
    bg2: 'Background, lower half',
    fg: 'Text',
    dim: 'Quiet text',
    pink: 'Accent 1',
    cyan: 'Accent 2',
    lilac: 'Accent 3',
    border: 'Lines',
    bezel: 'Screen frame',
    warn: 'Warning',
    bad: 'Error',
  };
  const LOGOS = ['text', 'aperture'];
  const MAX_CUSTOM = 12,
    MAX_NAME = 30,
    MAX_FILE = 4096;
  const HEX = /^#[0-9a-f]{6}$/i;

  const t = (id, name, logo, colors, flags = {}) => ({
    id,
    name,
    logo,
    builtin: true,
    glow: true,
    scan: true,
    ...flags,
    colors: Object.fromEntries(KEYS.map((k, i) => [k, colors[i]])),
  });
  //  bg bg2 fg dim pink cyan lilac border bezel warn bad
  const BUILTIN = [
    t('dreamspace', 'Dreamspace', 'text', [
      '#0b0720',
      '#150c33',
      '#d6f6ff',
      '#9a8fd6',
      '#ff9de6',
      '#8af3ff',
      '#b9a2ff',
      '#3d2f82',
      '#1b1438',
      '#ffd98a',
      '#ff8fa3',
    ]),
    t(
      'ansi',
      'ANSI 16 colors',
      'text',
      [
        '#000000',
        '#0000aa',
        '#ffffff',
        '#aaaaaa',
        '#ff55ff',
        '#55ffff',
        '#5555ff',
        '#00aaaa',
        '#555555',
        '#ffff55',
        '#ff5555',
      ],
      { glow: false },
    ),
    t('phosphor', 'Phosphor green', 'text', [
      '#010a01',
      '#021602',
      '#33ff33',
      '#1fb21f',
      '#66ff66',
      '#99ff99',
      '#22cc22',
      '#135a13',
      '#0a1f0a',
      '#ccff33',
      '#ff6633',
    ]),
    t('amber', 'Amber monochrome', 'aperture', [
      '#0a0500',
      '#140a00',
      '#ffb000',
      '#b37b00',
      '#ffcc33',
      '#ffb000',
      '#ff8c00',
      '#5a3b00',
      '#1f1200',
      '#ffe066',
      '#ff6a3d',
    ]),
    t(
      'paper',
      'Paper (light)',
      'text',
      [
        '#f6f1e7',
        '#ebe3d3',
        '#1d1a14',
        '#5b5444',
        '#b3365f',
        '#0d6e86',
        '#5a46b3',
        '#c9bfa8',
        '#d8cfba',
        '#8a5a00',
        '#b3261e',
      ],
      { glow: false, scan: false },
    ),
    t(
      'contrast',
      'High contrast',
      'text',
      [
        '#000000',
        '#000000',
        '#ffffff',
        '#d0d0d0',
        '#ffff00',
        '#00ffff',
        '#ff80ff',
        '#ffffff',
        '#303030',
        '#ffd000',
        '#ff6060',
      ],
      { glow: false, scan: false },
    ),
  ];

  const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  const lum = hex => {
    const [r, g, b] = rgb(hex).map(v => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
  };
  const triplet = hex => rgb(hex).join(', ');
  const toHex = parts =>
    '#' +
    parts
      .map(v =>
        Math.max(0, Math.min(255, Math.round(v)))
          .toString(16)
          .padStart(2, '0'),
      )
      .join('');
  const darker = (hex, by) => toHex(rgb(hex).map(v => v * (1 - by)));

  // Returns { ok, error, theme } for anything: a built-in, an editor result, or an uploaded file.
  // Never trusts its input.
  function validate(raw) {
    const fail = error => ({ ok: false, error });
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fail('That is not a theme.');
    const name =
      typeof raw.name === 'string' ? raw.name.replace(/[\u0000-\u001f\u007f<>]/g, '').trim() : '';
    if (!name || name.length > MAX_NAME)
      return fail(`A theme needs a name of 1 to ${MAX_NAME} characters.`);
    const logo = raw.logo === undefined ? 'text' : raw.logo;
    if (!LOGOS.includes(logo)) return fail('The logo must be one of: ' + LOGOS.join(', ') + '.');
    const c = raw.colors;
    if (!c || typeof c !== 'object') return fail('The theme has no colors.');
    for (const k of KEYS)
      if (typeof c[k] !== 'string' || !HEX.test(c[k]))
        return fail(`"${k}" must be a color like #1a2b3c.`);
    const colors = Object.fromEntries(KEYS.map(k => [k, c[k].toLowerCase()]));
    if (contrast(colors.fg, colors.bg) < 4.5 || contrast(colors.fg, colors.bg2) < 4.5)
      return fail(
        'The text color is too close to the background to read. Pick colors that contrast more.',
      );
    if (contrast(colors.dim, colors.bg) < 3 || contrast(colors.dim, colors.bg2) < 3)
      return fail('The quiet text color is too close to the background to read.');
    if (contrast(colors.warn, colors.bg) < 3 || contrast(colors.bad, colors.bg) < 3)
      return fail('The warning and error colors are too close to the background to read.');
    return {
      ok: true,
      theme: { name, logo, glow: raw.glow !== false, scan: raw.scan !== false, colors },
    };
  }

  const meta = document.querySelector('meta[name="totr-user"]');
  const who = meta ? meta.content : '';
  let store = null;
  try {
    store = window.localStorage;
  } catch (e) {
    store = null;
  }
  const key = name => `totr.v1.${who || '_'}.${name}`;
  const read = (name, fallback) => {
    try {
      const v = store && store.getItem(key(name));
      return v ? JSON.parse(v) : fallback;
    } catch (e) {
      return fallback;
    }
  };
  const write = (name, value) => {
    try {
      if (store) store.setItem(key(name), JSON.stringify(value));
    } catch (e) {
      /* private mode or full: the choice just does not stick */
    }
  };
  const LAST = 'totr.v1.last'; // what was last applied by anyone here, so the sign-in page matches
  const remember = applied => {
    try {
      if (store) store.setItem(LAST, JSON.stringify(applied));
    } catch (e) {
      /* ignore */
    }
  };

  function customThemes() {
    return read('themes', [])
      .filter(x => x && typeof x.id === 'string' && /^c-[a-z0-9-]{1,40}$/.test(x.id))
      .map(x => {
        const v = validate(x);
        return v.ok ? { id: x.id, builtin: false, ...v.theme } : null;
      })
      .filter(Boolean)
      .slice(0, MAX_CUSTOM);
  }
  const all = () => BUILTIN.concat(customThemes());
  const find = id => all().find(x => x.id === id) || BUILTIN[0];
  // null = what the theme or device says
  const display = () => ({ scanlines: null, glow: null, motion: null, ...read('display', {}) });

  function apply(theme, disp = display()) {
    const root = document.documentElement,
      c = theme.colors,
      set = (k, v) => root.style.setProperty(k, v);
    KEYS.forEach(k => set('--' + k, c[k]));
    ['bg', 'bg2', 'pink', 'cyan', 'lilac'].forEach(k => set(`--${k}-rgb`, triplet(c[k])));
    const deep = darker(c.bg, lum(c.bg) > 0.5 ? 0.06 : 0.45);
    set('--bg-deep', deep);
    set('--bg-deep-rgb', triplet(deep));
    set('--on-accent', c.bg);
    const light = lum(c.bg) > 0.4;
    root.style.colorScheme = light ? 'light' : 'dark';
    set('--field-bg', light ? 'rgba(255, 255, 255, .55)' : 'rgba(0, 0, 0, .45)');
    set('--vignette', light ? 'rgba(90, 70, 40, .28)' : 'rgba(0, 0, 0, .7)');
    const glow = disp.glow === null ? theme.glow !== false : disp.glow,
      scan = disp.scanlines === null ? theme.scan !== false : disp.scanlines;
    const flag = (name, on) =>
      on ? root.removeAttribute('data-' + name) : root.setAttribute('data-' + name, 'off');
    flag('glow', glow);
    flag('scanlines', scan);
    flag('motion', disp.motion !== false);
    root.setAttribute('data-logo', theme.logo || 'text');
    remember({
      theme: { name: theme.name, logo: theme.logo, glow: theme.glow, scan: theme.scan, colors: c },
      display: disp,
    });
  }
  function applyCurrent() {
    if (!who && store) {
      // not signed in (the sign-in page): look like the last person here did
      try {
        const last = JSON.parse(store.getItem(LAST)),
          v = last && validate(last.theme);
        if (v && v.ok) {
          apply(v.theme, { scanlines: null, glow: null, motion: null, ...(last.display || {}) });
          return 'last';
        }
      } catch (e) {
        /* fall through to the default */
      }
    }
    const id = read('current', 'dreamspace');
    apply(find(id), display());
    return id;
  }

  function slug(name) {
    return (
      name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '') || 'theme'
    ).slice(0, 24);
  }
  const api = {
    KEYS,
    LABELS,
    LOGOS,
    MAX_CUSTOM,
    MAX_FILE,
    BUILTIN,
    validate,
    contrast,
    apply,
    all,
    find,
    display,
    current: () => read('current', 'dreamspace'),
    select(id) {
      const th = find(id);
      write('current', th.id);
      apply(th);
      return th;
    },
    setDisplay(patch) {
      const d = { ...display(), ...patch };
      write('display', d);
      apply(find(read('current', 'dreamspace')), d);
      return d;
    },
    // Saves (or replaces, when id is given) a custom theme from editor values. Returns { ok, error,
    // id }.
    saveCustom(raw, id) {
      const v = validate(raw);
      if (!v.ok) return v;
      const list = read('themes', []);
      if (!id) {
        if (list.length >= MAX_CUSTOM)
          return {
            ok: false,
            error: `You can keep ${MAX_CUSTOM} custom themes. Delete one first.`,
          };
        id = 'c-' + slug(v.theme.name) + '-' + Date.now().toString(36);
      }
      const entry = { id, ...v.theme };
      const at = list.findIndex(x => x.id === id);
      if (at >= 0) list[at] = entry;
      else list.push(entry);
      write('themes', list);
      write('current', id);
      apply(find(id));
      return { ok: true, id };
    },
    deleteCustom(id) {
      write(
        'themes',
        read('themes', []).filter(x => x.id !== id),
      );
      if (read('current', '') === id) write('current', 'dreamspace');
      applyCurrent();
    },
    exportTheme(theme) {
      return JSON.stringify(
        {
          'totr-theme': 1,
          name: theme.name,
          logo: theme.logo,
          glow: theme.glow !== false,
          scan: theme.scan !== false,
          colors: theme.colors,
        },
        null,
        2,
      );
    },
    // Reads the text of an uploaded .json file. Returns { ok, error, theme } and changes nothing.
    parseImport(text) {
      if (typeof text !== 'string' || text.length > MAX_FILE)
        return { ok: false, error: 'That file is too big to be a theme.' };
      let raw;
      try {
        raw = JSON.parse(text);
      } catch (e) {
        return { ok: false, error: 'That file is not valid JSON.' };
      }
      if (!raw || raw['totr-theme'] !== 1)
        return { ok: false, error: 'That file is not a TOTR theme (it has no "totr-theme": 1).' };
      return validate(raw);
    },
    reset() {
      write('current', 'dreamspace');
      write('display', {});
      applyCurrent();
    },
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
    return;
  }
  window.TOTRTheme = api;
  try {
    applyCurrent();
  } catch (e) {
    /* the default look from style.css stays */
  }
})();
