// Runs AFTER the archive page's own script. Clicks through search, the add-to-mixtape dialog and
// the mixtape views.
(function () {
  const out = { steps: {}, errors: [] };
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const $ = id => document.getElementById(id);
  const all = sel => [...document.querySelectorAll(sel)];
  const texts = sel => all(sel).map(e => e.textContent.trim());
  const type = (el, value) => {
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  };

  (async () => {
    try {
      await wait(300);
      out.steps.home = { cards: texts('.card h3'), tabs: texts('.tabs a') };

      type($('search'), 'kanal vi');
      await wait(700);
      out.steps.search = {
        hash: location.hash,
        headings: texts('#content h2'),
        cards: texts('.card h3'),
        songs: texts('.track-item span:nth-child(2)'),
        box: $('search').value,
      };
      type($('search'), 'zzz');
      await wait(700);
      out.steps.searchNothing = { hash: location.hash, text: texts('#content p') };
      type($('search'), '');
      await wait(700);
      out.steps.searchCleared = { hash: location.hash };

      location.hash = '#/artist/1';
      await wait(400);
      out.steps.artist = {
        headings: texts('#content h1, #content h2'),
        rows: texts('.track-item span:nth-child(2)'),
        artists: texts('.track-item .track-artist'),
        numbers: texts('.track-item .track-number'),
      };

      location.hash = '#/album/1';
      await wait(400);
      out.steps.album = {
        rows: texts('.track-item span:nth-child(2)'),
        buttons: texts('#content > div:nth-child(2) button, #content button').slice(0, 4),
      };
      all('.track-item .row-buttons button')[0].click();
      await wait(300);
      out.steps.dialogOpen = {
        open: $('mixDialog').open,
        title: ($('mixDialog').querySelector('h3') || {}).textContent,
        message: ($('mixDialog').querySelector('p') || {}).textContent,
      };
      const nameBox = $('mixDialog').querySelector('input[type=text]');
      type(nameBox, 'Road');
      $('mixDialog')
        .querySelector('form')
        .dispatchEvent(new Event('submit', { cancelable: true }));
      await wait(400);
      out.steps.created = {
        message: $('mixDialog').querySelector('p').textContent,
        buttons: [...$('mixDialog').querySelectorAll('div button')].map(b => b.textContent),
        mixes: window.__mixes.map(m => [m.name, m.entries.length]),
      };
      nameBox.value = 'road';
      $('mixDialog')
        .querySelector('form')
        .dispatchEvent(new Event('submit', { cancelable: true }));
      await wait(300);
      out.steps.duplicate = $('mixDialog').querySelector('p').textContent;
      [...$('mixDialog').querySelectorAll('button')].find(b => b.textContent === 'Close').click();
      await wait(100);
      out.steps.closed = !$('mixDialog').open;

      all('.track-item')[1].click();
      await wait(300); // play the second song
      out.steps.playing = $('np').textContent;
      out.steps.pings = JSON.parse(JSON.stringify(window.__pings));
      $('addNow').click();
      await wait(300);
      [...$('mixDialog').querySelectorAll('div button')]
        .find(b => b.textContent.startsWith('Road'))
        .click();
      await wait(400);
      out.steps.addNow = {
        message: $('mixDialog').querySelector('p').textContent,
        mixes: window.__mixes.map(m => [m.name, m.entries.map(e => e.track_id)]),
      };
      $('mixDialog').close();
      all('.track-item')[0].querySelector('.row-buttons button').click();
      await wait(200);
      $('mixDialog').close(); // + on one song must not start it playing
      out.steps.plusDoesNotPlay = $('np').textContent;

      all('button')
        .find(b => b.textContent.startsWith('+ Add all'))
        .click();
      await wait(300);
      [...$('mixDialog').querySelectorAll('div button')]
        .find(b => b.textContent.startsWith('Road'))
        .click();
      await wait(400);
      out.steps.addAll = window.__mixes[0].entries.map(e => e.track_id);
      $('mixDialog').close();

      location.hash = '#/mixtapes';
      await wait(400);
      out.steps.mixtapes = {
        cards: texts('.card h3'),
        subs: texts('.card .sub'),
        heading: texts('#content h2'),
        tabs: texts('.tabs a'),
      };
      location.hash = '#/mixtape/1';
      await wait(400);
      out.steps.mixtape = {
        title: texts('#content h1'),
        rows: texts('.track-item span:nth-child(2)'),
        crumbs: $('breadcrumb').textContent,
      };
      const first = () => texts('.track-item span:nth-child(2)')[0];
      const was = first();
      all('.track-item')[0].querySelectorAll('.row-buttons button')[1].click();
      await wait(500); // move first one later
      out.steps.moved = {
        was,
        now: first(),
        upDisabledOnFirst:
          all('.track-item')[0].querySelectorAll('.row-buttons button')[0].disabled,
      };
      const countBefore = all('.track-item').length;
      all('.track-item')[0].querySelectorAll('.row-buttons button')[2].click();
      await wait(500); // remove the first
      out.steps.removed = { before: countBefore, after: all('.track-item').length };
      all('button')
        .find(b => b.textContent === 'Rename')
        .click();
      await wait(500);
      out.steps.renamed = texts('#content h1');
      all('button')
        .find(b => b.textContent.endsWith('Play mixtape'))
        .click();
      await wait(300);
      out.steps.playsMixtape = $('np').textContent;
      all('button')
        .find(b => b.textContent === 'Delete mixtape')
        .click();
      await wait(600);
      out.steps.deleted = {
        hash: location.hash,
        cards: texts('.card h3'),
        mixes: window.__mixes.length,
      };
    } catch (e) {
      out.errors.push('driver: ' + String((e && e.stack) || e));
    }
    out.errors.push(...window.__errors);
    $('out').textContent = 'RESULT ' + JSON.stringify(out);
  })();
})();
