// FalseHorizon's controls: on/off, intensity, album art, preset choice, previous/next, lock,
// favourite, auto-change, and a place to read, change, save, export and import presets. Built from
// falsehorizon-core.js and an engine from falsehorizon.js. It knows nothing about the page it sits in:
// FalseHorizonUI.create({ engine, mount, username, storage, calls }).
//   calls.onChange(settings)  something in the settings changed (the page decides what to show or run)
//   calls.onPreset(name)      the picture moved to another preset (the page shows its name for a moment)
// Everything typed or loaded is untrusted: it goes through FalseHorizonCore before the engine and is
// shown with textContent.
(function () {
  function create(opts) {
    const Core = window.FalseHorizonCore,
      engine = opts.engine,
      calls = opts.calls || {},
      storage = opts.storage || null;
    let user = opts.username || 'guest',
      settings = Core.loadSettings(storage, user),
      saveTimer = null,
      draft = null;
    const els = {};

    const el = (tag, cls, text) => {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    };
    const button = (text, label, onclick) => {
      const b = el('button', 'fh-btn', text);
      b.type = 'button';
      if (label) b.setAttribute('aria-label', label);
      b.addEventListener('click', onclick);
      return b;
    };
    // Typed symbols (arrows, stars) turn into coloured emoji on some phones, so these are drawn.
    const ICONS = {
      prev: 'M5.5 5h2.5v14H5.5zM19 5v14L9 12z',
      next: 'M16 5h2.5v14H16zM5 5v14l10-7z',
      star: 'M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.2l-5.9 3.4 1.3-6.6-4.9-4.6 6.6-.8z',
    };
    const icon = (name, hollow) => {
      const NS = 'http://www.w3.org/2000/svg',
        svg = document.createElementNS(NS, 'svg'),
        path = document.createElementNS(NS, 'path');
      svg.setAttribute('viewBox', '0 0 24 24');
      svg.setAttribute('class', hollow ? 'fh-ico hollow' : 'fh-ico');
      svg.setAttribute('aria-hidden', 'true');
      path.setAttribute('d', ICONS[name]);
      svg.append(path);
      return svg;
    };
    const iconButton = (name, label, onclick) => {
      const b = button('', label, onclick);
      b.append(icon(name));
      return b;
    };
    const range = (min, max, label, oninput) => {
      const r = document.createElement('input');
      r.type = 'range';
      r.min = min;
      r.max = max;
      r.step = 1;
      r.setAttribute('aria-label', label);
      r.addEventListener('input', () => oninput(Number(r.value)));
      return r;
    };
    const labelled = (text, input, valueEl) => {
      const l = el('label', 'fh-lab');
      l.append(el('span', undefined, text), input);
      if (valueEl) l.append(valueEl);
      return l;
    };
    const say = (t, bad) => {
      els.say.textContent = t;
      els.say.className = 'fh-say ' + (bad ? 'bad' : 'ok');
    };

    function apply() {
      engine.setIntensity(settings.intensity);
      engine.setCovers(settings.covers);
      engine.setGeometry(settings.geometry);
      engine.setAuto(settings.auto, settings.cycleSec);
      engine.setUserPresets(settings.userPresets);
      engine.setFavorites(settings.favorites);
    }
    function persist() {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => {
        if (storage && !Core.saveSettings(storage, user, settings))
          say(
            'This browser would not keep your settings (storage is full or blocked), so they will be forgotten when you close the page.',
            true,
          );
      }, 300);
    }
    function change(patch, fromUser = true) {
      settings = Core.sanitize({ ...settings, ...patch });
      apply();
      sync();
      persist();
      if (fromUser && calls.onChange) calls.onChange(settings);
    }

    const presetObject = name =>
      Core.builtins().find(p => p.name === name) ||
      settings.userPresets.find(p => p.name === name) ||
      null;
    function rebuildList() {
      const select = els.preset;
      select.textContent = '';
      const group = (label, names) => {
        if (!names.length) return;
        const g = document.createElement('optgroup');
        g.label = label;
        names.forEach(n => {
          const o = el('option', undefined, n);
          o.value = n;
          g.append(o);
        });
        select.append(g);
      };
      const builtin = Core.builtins().map(p => p.name),
        yours = settings.userPresets.map(p => p.name);
      group(
        'Favourites',
        settings.favorites.filter(n => builtin.includes(n) || yours.includes(n)),
      );
      group('Built in', builtin);
      group('Yours', yours);
    }
    function sync() {
      const now = engine.current ? engine.current() : '';
      els.on.checked = settings.on;
      els.amt.value = settings.intensity;
      els.amtText.textContent = settings.intensity + '%';
      els.art.value = settings.covers;
      els.artText.textContent = settings.covers + '%';
      els.geo.value = settings.geometry;
      els.geoText.textContent = settings.geometry + '%';
      els.mode.value = settings.mode;
      els.auto.checked = settings.auto;
      els.cycle.value = String(settings.cycleSec);
      const fh = settings.mode === 'horizon';
      els.presetRow.hidden = !fh;
      els.artLab.hidden = !fh;
      els.geoLab.hidden = !fh;
      els.edit.hidden = !fh;
      els.amt.disabled = !settings.on;
      els.art.disabled = !settings.on;
      els.geo.disabled = !settings.on;
      rebuildList();
      if (now) els.preset.value = now;
      const fav = !!now && settings.favorites.includes(now);
      els.fav.setAttribute('aria-pressed', String(fav));
      els.fav.replaceChildren(icon('star', !fav));
      const locked = !!engine.locked;
      els.lock.setAttribute('aria-pressed', String(locked));
      els.lock.textContent = locked ? 'Locked' : 'Lock';
      els.cycleLab.hidden = !settings.auto;
      els.del.hidden = !(now && settings.userPresets.some(p => p.name === now));
      if (draft === null) {
        const p = presetObject(now);
        els.code.value = p ? Core.exportPreset(p) : els.code.value;
        els.name.value = '';
      }
    }

    const root = el('div', 'fh');
    root.setAttribute('role', 'group');
    root.setAttribute('aria-label', 'FalseHorizon visuals');
    const top = el('div', 'fh-row');
    els.on = document.createElement('input');
    els.on.type = 'checkbox';
    els.on.addEventListener('change', () => change({ on: els.on.checked }));
    const onLab = el('label', 'fh-lab');
    onLab.append(els.on, el('span', undefined, 'on'));
    els.amt = range(0, 100, 'Visuals intensity', v => change({ intensity: v }));
    els.amtText = el('span', 'fh-val');
    els.art = range(0, 100, 'How much album art shows in the visuals', v => change({ covers: v }));
    els.artText = el('span', 'fh-val');
    els.artLab = labelled('album art', els.art, els.artText);
    els.geo = range(0, 100, 'How much 3D fractal geometry shows in the visuals', v =>
      change({ geometry: v }),
    );
    els.geoText = el('span', 'fh-val');
    els.geoLab = labelled('geometry', els.geo, els.geoText);
    els.mode = document.createElement('select');
    els.mode.setAttribute('aria-label', 'Which visualizer');
    [
      ['horizon', 'FalseHorizon'],
      ['classic', 'Classic photo-smear'],
    ].forEach(([v, t]) => {
      const o = el('option', undefined, t);
      o.value = v;
      els.mode.append(o);
    });
    els.mode.addEventListener('change', () => change({ mode: els.mode.value }));
    top.append(
      onLab,
      labelled('intensity', els.amt, els.amtText),
      els.artLab,
      els.geoLab,
      labelled('style', els.mode),
    );

    els.presetRow = el('div', 'fh-row');
    els.preset = document.createElement('select');
    els.preset.setAttribute('aria-label', 'Visual preset');
    els.preset.addEventListener('change', () => {
      draft = null;
      engine.setPreset(els.preset.value);
    });
    els.lock = button('Lock', 'Keep this preset: stop changing automatically', () => {
      engine.lock(!engine.locked);
      sync();
    });
    els.lock.setAttribute('aria-pressed', 'false');
    els.fav = button('', 'Favourite this preset', () => {
      const n = engine.current();
      if (n) change({ favorites: Core.toggleFavorite(settings, n).favorites });
    });
    els.auto = document.createElement('input');
    els.auto.type = 'checkbox';
    els.auto.addEventListener('change', () => change({ auto: els.auto.checked }));
    els.cycle = document.createElement('select');
    els.cycle.setAttribute('aria-label', 'Seconds between automatic changes');
    Core.CYCLE_CHOICES.forEach(s => {
      const o = el('option', undefined, s + ' s');
      o.value = String(s);
      els.cycle.append(o);
    });
    els.cycle.addEventListener('change', () => change({ cycleSec: Number(els.cycle.value) }));
    const autoLab = el('label', 'fh-lab');
    autoLab.append(els.auto, el('span', undefined, 'change by itself'));
    els.cycleLab = el('label', 'fh-lab');
    els.cycleLab.append(el('span', undefined, 'every'), els.cycle);
    els.presetRow.append(
      els.preset,
      iconButton('prev', 'Previous preset', () => {
        draft = null;
        engine.prev();
      }),
      iconButton('next', 'Next preset', () => {
        draft = null;
        engine.next();
      }),
      els.lock,
      els.fav,
      autoLab,
      els.cycleLab,
    );

    els.edit = document.createElement('details');
    els.edit.className = 'fh-edit';
    els.edit.append(el('summary', undefined, 'Look inside, change or keep a preset'));
    els.code = document.createElement('textarea');
    els.code.className = 'fh-code';
    els.code.rows = 12;
    els.code.spellcheck = false;
    els.code.maxLength = Core.MAX_FILE;
    els.code.setAttribute('aria-label', 'Preset text');
    els.code.addEventListener('input', () => {
      draft = els.code.value;
    });
    els.name = document.createElement('input');
    els.name.type = 'text';
    els.name.maxLength = Core.MAX_NAME;
    els.name.placeholder = 'new name (optional)';
    els.name.setAttribute('aria-label', 'Name to save the preset under');
    const parseCode = () => {
      const r = Core.parsePresetFile(els.code.value);
      if (!r.ok) {
        say(r.error, true);
        return null;
      }
      const name = els.name.value.trim();
      return name ? { ...r.preset, name } : r.preset;
    };
    els.apply = button('Try it', 'Show the preset in the box now', () => {
      const p = parseCode();
      if (!p) return;
      engine.setPreset(p);
      say('Showing it now. Press Save to keep it.', false);
    });
    els.save = button('Save', 'Keep this preset under its name', () => {
      const p = parseCode();
      if (!p) return;
      const r = Core.savePreset(settings, p);
      if (!r.ok) {
        say(r.error, true);
        return;
      }
      draft = null;
      settings = r.settings;
      apply();
      persist();
      engine.setPreset(r.preset.name);
      sync();
      say(`Saved "${r.preset.name}".`, false);
      if (calls.onChange) calls.onChange(settings);
    });
    els.del = button('Delete', 'Delete this saved preset', () => {
      const n = engine.current();
      if (n && settings.userPresets.some(p => p.name === n)) {
        settings = Core.deletePreset(settings, n);
        apply();
        persist();
        engine.next();
        sync();
        say(`Deleted "${n}".`, false);
      }
    });
    els.exp = button('Export', 'Save this preset as a file', () => {
      const p = parseCode();
      if (!p) return;
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([Core.exportPreset(p)], { type: 'application/json' }));
      a.download = p.name.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.fh-preset.json';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });
    els.file = document.createElement('input');
    els.file.type = 'file';
    els.file.accept = '.json,application/json';
    els.file.hidden = true;
    els.file.setAttribute('aria-label', 'Preset file');
    els.file.addEventListener('change', async () => {
      const f = els.file.files[0];
      els.file.value = '';
      if (!f) return;
      if (f.size > Core.MAX_FILE) {
        say('That file is too big to be a visual preset.', true);
        return;
      }
      const r = Core.parsePresetFile(await f.text());
      if (!r.ok) {
        say(r.error, true);
        return;
      }
      const saved = Core.savePreset(settings, r.preset);
      if (!saved.ok) {
        say(saved.error, true);
        return;
      }
      draft = null;
      settings = saved.settings;
      apply();
      persist();
      engine.setPreset(saved.preset.name);
      sync();
      say(`Added "${saved.preset.name}".`, false);
      if (calls.onChange) calls.onChange(settings);
    });
    els.imp = button('Import', 'Add a preset from a file', () => els.file.click());
    const tools = el('div', 'fh-row');
    tools.append(els.name, els.apply, els.save, els.del, els.exp, els.imp, els.file);

    // community MilkDrop presets: your own files, translated as far as possible (falsehorizon-
    // milk.js)
    els.milkFile = document.createElement('input');
    els.milkFile.type = 'file';
    els.milkFile.accept = '.milk,.txt,text/plain';
    els.milkFile.multiple = true;
    els.milkFile.hidden = true;
    els.milkFile.setAttribute('aria-label', 'MilkDrop preset files');
    els.milkFile.addEventListener('change', async () => {
      const files = [...els.milkFile.files].slice(0, 20);
      els.milkFile.value = '';
      const Milk = window.FalseHorizonMilk;
      if (!files.length) return;
      if (!Milk) {
        say('The MilkDrop importer is not loaded on this page.', true);
        return;
      }
      const added = [],
        refused = [],
        notes = new Set();
      for (const f of files) {
        if (f.size > Milk.MAX_TEXT) {
          refused.push(`${f.name}: too big`);
          continue;
        }
        const r = Milk.convert(await f.text(), f.name);
        if (!r.ok) {
          refused.push(`${f.name}: ${r.error}`);
          continue;
        }
        let preset = r.preset;
        if (Core.builtins().some(p => p.name.toLowerCase() === preset.name.toLowerCase()))
          preset = { ...preset, name: (preset.name + ' (imported)').slice(0, Core.MAX_NAME) };
        const saved = Core.savePreset(settings, preset);
        if (!saved.ok) {
          refused.push(`${f.name}: ${saved.error}`);
          continue;
        }
        settings = saved.settings;
        added.push(saved.preset.name);
        r.notes.slice(1).forEach(n => notes.add(n));
      }
      if (added.length) {
        draft = null;
        apply();
        persist();
        engine.setPreset(added[added.length - 1]);
        sync();
        if (calls.onChange) calls.onChange(settings);
      }
      say(
        (added.length
          ? `Added ${added.length}: ${added.join(', ')}. They will not look exactly as they do in MilkDrop. ${[...notes].join(' ')}`
          : 'Nothing was added.') + (refused.length ? ` Not added: ${refused.join(' | ')}` : ''),
        !added.length,
      );
    });
    els.milkBtn = button(
      'Import MilkDrop preset (.milk)',
      'Add presets from MilkDrop files you already have',
      () => els.milkFile.click(),
    );
    els.milkNote = el(
      'p',
      'fh-note',
      'MilkDrop presets are made by other people for a different program, and none are included. If you have some, this translates the common parts and refuses what it cannot run safely (such as loops). ' +
        "The picture will differ from MilkDrop, and using other people's files is at your own risk. Presets from the internet are not checked for taste or content.",
    );
    const milkRow = el('div', 'fh-row');
    milkRow.append(els.milkBtn, els.milkFile);
    els.edit.append(els.code, tools, milkRow, els.milkNote);

    els.say = el('p', 'fh-say');
    els.say.setAttribute('role', 'status');
    const note = el(
      'p',
      'fh-note',
      'FalseHorizon draws a new picture from the music every frame, using the formulas in a preset. It stays gentle: it never flashes faster than a few times a second, and is calmer if your device asks for reduced motion. ' +
        'Turn it off here, or with the Visuals button, if moving images bother you. Settings are remembered for you on this browser.',
    );
    els.status = el('p', 'fh-note');
    root.append(top, els.presetRow, els.edit, els.say, els.status, note);
    opts.mount.append(root);

    engine.onPreset(name => {
      settings = Core.sanitize({ ...settings, last: name });
      persist();
      sync();
      if (calls.onPreset) calls.onPreset(name);
    });
    apply();
    sync();
    const startName = presetObject(settings.last) ? settings.last : '';
    if (startName) engine.setPreset(startName, { instant: true, quiet: true });

    return {
      settings: () => settings,
      change: patch => change(patch, true),
      setStatus: t => {
        els.status.textContent = t || '';
      },
      load(username) {
        user = username || 'guest';
        settings = Core.loadSettings(storage, user);
        draft = null;
        apply();
        sync();
        if (presetObject(settings.last))
          engine.setPreset(settings.last, { instant: true, quiet: true });
        sync();
      },
      destroy() {
        clearTimeout(saveTimer);
        root.remove();
      },
      inspect: () => ({
        built: !!els.preset.options.length,
        presetOptions: els.preset.options.length,
        groups: [...els.preset.querySelectorAll('optgroup')].map(g => g.label),
        shown: els.preset.value,
        favPressed: els.fav.getAttribute('aria-pressed'),
        lockPressed: els.lock.getAttribute('aria-pressed'),
        codeLength: els.code.value.length,
        say: els.say.textContent,
        sayBad: els.say.classList.contains('bad'),
        deleteHidden: els.del.hidden,
        styleValue: els.mode.value,
        presetRowHidden: els.presetRow.hidden,
        milkInput: !!els.milkFile,
      }),
    };
  }

  window.FalseHorizonUI = { create };
})();
