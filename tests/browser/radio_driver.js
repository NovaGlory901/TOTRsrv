// Runs AFTER the radio page's own script. Clicks through the real page and writes what it saw into
// #out as JSON.
(function () {
  const out = { steps: {}, errors: [] };
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const set = (id, value, type) => {
    const e = document.getElementById(id);
    e.value = value;
    e.dispatchEvent(new Event(type, { bubbles: true }));
  };
  const text = id => document.getElementById(id).textContent;
  const hidden = id => document.getElementById(id).hidden;

  (async () => {
    try {
      await wait(300);
      out.steps.beforeTune = {
        introHidden: hidden('intro'),
        title: text('title'),
        prevHidden: hidden('prev'),
      };
      document.getElementById('tune').click();
      await wait(400);
      out.steps.afterTune = {
        introHidden: hidden('intro'),
        title: text('title'),
        prevHidden: hidden('prev'),
        skipHidden: hidden('skip'),
        asked: document.getElementById('audio').__asked,
        haveGraph: rack.inspect().built,
        ctxState: rack.context() ? rack.context().state : null,
        vizHidden: hidden('viz'),
      };
      out.steps.pingsAfterTune = JSON.parse(JSON.stringify(window.__calls.pings));
      const titles = [];
      for (const id of ['skip', 'skip', 'prev', 'prev', 'prev']) {
        document.getElementById(id).click();
        await wait(250);
        titles.push(text('title'));
      }
      out.steps.skipSkipPrevPrevPrev = titles; // expect Song 2, Song 3, Song 2, Song 1, Song 1
      const before = window.__calls.next;
      document.getElementById('skip').click();
      await wait(250);
      // expect Song 2 with no new request
      out.steps.nextRetraces = { title: text('title'), askedServer: window.__calls.next - before };
      document.getElementById('skip').click();
      await wait(250);
      document.getElementById('skip').click();
      await wait(250);
      out.steps.nextAtEndPicksNew = {
        title: text('title'),
        askedServer: window.__calls.next - before,
      }; // expect Song 4, one new request

      document.getElementById('gear').click();
      out.steps.settingsOpen = {
        hidden: hidden('settings'),
        expanded: document.getElementById('gear').getAttribute('aria-expanded'),
      };
      const R = document.getElementById('rack'),
        q = sel => R.querySelector(sel),
        qa = sel => [...R.querySelectorAll(sel)];
      const setv = (el, v, type) => {
        el.value = v;
        el.dispatchEvent(new Event(type, { bubbles: true }));
      };
      const bandIn = qa('.da-bands input.da-v'); // preamp first, then the ten bands
      const insp = () => rack.inspect().applied;
      out.steps.rackShape = {
        ticker: q('.da-ticker span').textContent,
        bandInputs: bandIn.length,
        presetOptions: qa('select[aria-label="Equalizer preset"] option').length,
        graph: rack.inspect().built,
        types: rack.inspect().bandTypes,
        fft: rack.inspect().analyserFft,
        panner: rack.inspect().hasPanner,
      };
      setv(q('select[aria-label="Playback speed"]'), '1.5', 'change');
      setv(bandIn[1], '6', 'input');
      setv(bandIn[5], '-3.5', 'input');
      setv(bandIn[10], '2', 'input');
      setv(q('.da-vol'), '-19.5', 'input');
      setv(q('.da-bal'), '-40', 'input');
      q('[data-key="mono"]').click();
      out.steps.controls = {
        rate: document.getElementById('audio').playbackRate,
        defaultRate: document.getElementById('audio').defaultPlaybackRate,
        bands: insp().bands,
        vol: text('volText'),
        rackVol: q('.da-vol').nextSibling.textContent,
        stripVol: document.getElementById('vol').value,
        balText: q('.da-bal').nextSibling.textContent,
        pan: insp().pan,
        mono: insp().mono,
        monoPressed: q('[data-key="mono"]').getAttribute('aria-pressed'),
        preset: q('select[aria-label="Equalizer preset"]').value,
        info: q('.da-info').textContent,
      };
      setv(q('select[aria-label="Equalizer preset"]'), 'Rock', 'change');
      out.steps.rock = {
        bands: insp().bands,
        preampDb: insp().preampDb,
        shown: q('select[aria-label="Equalizer preset"]').value,
        firstBand: bandIn[1].value,
      };
      q('[data-key="eqOn"]').click();
      out.steps.eqOff = {
        bands: insp().bands,
        preampDb: insp().preampDb,
        pressed: q('[data-key="eqOn"]').getAttribute('aria-pressed'),
        sliderKept: bandIn[1].value,
      };
      q('[data-key="eqOn"]').click();
      // a curve of its own (an exact copy of Rock would show as Rock)
      setv(bandIn[3], '1', 'input');
      setv(q('input[aria-label="Name for a new preset"]'), 'Mine', 'input');
      qa('button')
        .find(b => b.getAttribute('aria-label') === 'Save the current curve as a preset')
        .click();
      out.steps.saveMine = {
        yours: qa('optgroup[label="Yours"] option').map(o => o.textContent),
        shown: q('select[aria-label="Equalizer preset"]').value,
        deleteHidden: qa('button').find(
          b => b.getAttribute('aria-label') === 'Delete this saved preset',
        ).hidden,
      };
      qa('button')
        .find(b => b.getAttribute('aria-label') === 'Reset the equalizer to flat')
        .click();
      out.steps.eqReset = {
        bands: insp().bands,
        preampDb: insp().preampDb,
        shown: q('select[aria-label="Equalizer preset"]').value,
      };
      const cv = q('canvas.da-vis');
      let lit = 0;
      for (let tries = 0; tries < 12 && lit === 0; tries++) {
        // the page is busy drawing the visuals too: look a few times, not once
        await wait(150);
        const px = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data,
          first = [px[0], px[1], px[2]];
        for (let k = 0; k < px.length; k += 4)
          if (
            Math.abs(px[k] - first[0]) +
              Math.abs(px[k + 1] - first[1]) +
              Math.abs(px[k + 2] - first[2]) >
            60
          )
            lit++;
      }
      out.steps.display = {
        time: q('.da-time').textContent,
        litPixels: lit,
        seekEnabled: !q('.da-seek').disabled,
      };
      qa('button')
        .find(b => b.getAttribute('aria-label') === 'Next song')
        .click();
      await wait(250);
      out.steps.rackNext = { title: text('title'), ticker: q('.da-ticker span').textContent };
      const before2 = out.steps.rackNext.title;
      qa('button')
        .find(b => b.getAttribute('aria-label') === 'Play or pause')
        .click();
      await wait(150);
      out.steps.rackPause = {
        toggleText: text('toggle'),
        button: q('.da-btn[aria-label="Play or pause"]').dataset.shown,
      };
      qa('button')
        .find(b => b.getAttribute('aria-label') === 'Play or pause')
        .click();
      await wait(300);
      out.steps.rackPlay = {
        toggleText: text('toggle'),
        button: q('.da-btn[aria-label="Play or pause"]').dataset.shown,
        playing: !document.getElementById('audio').paused,
      };

      document.getElementById('vizToggle').click();
      await wait(100);
      out.steps.vizOff = { label: text('vizToggle'), hidden: hidden('viz'), running: fh.running };
      document.getElementById('vizToggle').click();
      await wait(300);
      out.steps.vizOn = {
        label: text('vizToggle'),
        hidden: hidden('viz'),
        running: fh.running,
        supported: fh.supported,
        scene: fh.inspect().hasScene,
        stageClass: document.getElementById('stage').classList.contains('fh-on'),
      };

      // FalseHorizon's controls
      const V = document.getElementById('visuals'),
        vq = sel => V.querySelector(sel),
        vqa = sel => [...V.querySelectorAll(sel)];
      const vbtn = label => vqa('button').find(b => b.getAttribute('aria-label') === label);
      setv(vq('input[aria-label="Visuals intensity"]'), '40', 'input');
      out.steps.amt = vq('input[aria-label="Visuals intensity"]').nextSibling.textContent;
      out.steps.fhShape = ui.inspect();
      setv(vq('select[aria-label="Visual preset"]'), 'Heartbeat', 'change');
      await wait(100);
      out.steps.fhPick = { current: fh.current(), shown: ui.inspect().shown };
      const before3 = fh.current();
      vbtn('Next preset').click();
      await wait(100);
      const after3 = fh.current();
      vbtn('Previous preset').click();
      await wait(100);
      out.steps.fhNextPrev = { moved: before3 !== after3, back: fh.current() === before3 };
      vbtn('Keep this preset: stop changing automatically').click();
      out.steps.fhLock = { locked: fh.locked, pressed: ui.inspect().lockPressed };
      vbtn('Favourite this preset').click();
      out.steps.fhFav = { pressed: ui.inspect().favPressed, groups: ui.inspect().groups };
      const code = vq('textarea[aria-label="Preset text"]');
      out.steps.fhCode = {
        json: (() => {
          try {
            return JSON.parse(code.value)['fh-preset'];
          } catch (e) {
            return String(e);
          }
        })(),
      };
      setv(code, '{"fh-preset":1,"name":"Broken","frame":"zoom = ("}', 'input');
      vbtn('Show the preset in the box now').click();
      out.steps.fhBad = { bad: ui.inspect().sayBad, say: ui.inspect().say };
      setv(
        code,
        '{"fh-preset":1,"name":"Mine Vis","frame":"zoom = 1.01;","wave":{"mode":1,"a":0.5}}',
        'input',
      );
      vbtn('Show the preset in the box now').click();
      await wait(100);
      out.steps.fhTry = { current: fh.current() };
      vbtn('Keep this preset under its name').click();
      await wait(100);
      out.steps.fhSave = {
        current: fh.current(),
        groups: ui.inspect().groups,
        deleteHidden: ui.inspect().deleteHidden,
      };
      const milkIn = vq('input[aria-label="MilkDrop preset files"]');
      const give = (input, files) => {
        const dt = new DataTransfer();
        files.forEach(f => dt.items.add(f));
        input.files = dt.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      };
      const sayFirst = ui.inspect().say;
      give(milkIn, [
        new File(
          [
            '[preset00]\nfDecay=0.95\nzoom=1.01\nper_frame_1=rot = 0.01*sin(time);\nper_pixel_1=zoom = zoom + 0.01*rad;\n',
          ],
          'Imported Sky.milk',
        ),
        new File(['[preset00]\nfDecay=0.9\nper_frame_1=loop(5, q1 = q1 + 1);\n'], 'Loopy.milk'),
        new File(['hello'], 'Junk.milk'),
      ]);
      for (let i = 0; i < 40 && ui.inspect().say === sayFirst; i++) await wait(50);
      await wait(50);
      out.steps.fhMilk = {
        current: fh.current(),
        groups: ui.inspect().groups,
        say: ui.inspect().say,
        sayBad: ui.inspect().sayBad,
      };
      const sayBefore = ui.inspect().say;
      give(milkIn, [new File(['junk'], 'Only Junk.milk')]);
      // wait for the file to be read, not a fixed time
      for (let i = 0; i < 40 && ui.inspect().say === sayBefore; i++) await wait(50);
      out.steps.fhMilkBad = {
        say: ui.inspect().say,
        sayBad: ui.inspect().sayBad,
        current: fh.current(),
      };
      setv(vq('select[aria-label="Which visualizer"]'), 'classic', 'change');
      await wait(400);
      out.steps.fhClassic = {
        fhHidden: hidden('viz'),
        classicHidden: hidden('viz2'),
        fhRunning: fh.running,
        presetRowHidden: ui.inspect().presetRowHidden,
      };
      setv(vq('select[aria-label="Which visualizer"]'), 'horizon', 'change');
      await wait(300);
      out.steps.fhBack = {
        fhHidden: hidden('viz'),
        classicHidden: hidden('viz2'),
        fhRunning: fh.running,
      };

      await wait(600);
      try {
        out.steps.saved = JSON.parse(localStorage.getItem('dejavuamp.v1.tester'));
        out.steps.savedViz = JSON.parse(localStorage.getItem('falsehorizon.v1.tester'));
      } catch (e) {
        out.steps.saved = 'storage unavailable: ' + e.message;
      }
      document.getElementById('toggle').click();
      await wait(200);
      out.steps.pingsAtEnd = JSON.parse(JSON.stringify(window.__calls.pings));
      out.steps.tunedOut = {
        toggleText: text('toggle'),
        vizHidden: hidden('viz'),
        running: fh.running,
      };
    } catch (e) {
      out.errors.push('driver: ' + String((e && e.message) || e));
    }
    out.errors.push(...window.__errors);
    document.getElementById('out').textContent = 'RESULT ' + JSON.stringify(out);
  })();
})();
