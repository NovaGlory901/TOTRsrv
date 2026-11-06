// Runs AFTER the Admin page's own script. Looks at what the Status tab drew, then checks that it
// stops polling in the background.
(function () {
  const out = { steps: {}, errors: [] };
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const $ = id => document.getElementById(id);
  const rows = id =>
    [...$(id).querySelectorAll('tr')].map(tr => [...tr.children].map(c => c.textContent.trim()));
  const calls = () => ({ health: window.__calls.health, live: window.__calls.live });
  let visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  const setVisibility = v => {
    visibility = v;
    document.dispatchEvent(new Event('visibilitychange'));
  };

  (async () => {
    try {
      await wait(600);
      out.steps.firstLoad = calls();
      out.steps.overall = $('overall').textContent;
      out.steps.listeners = rows('listeners');
      out.steps.injection = {
        pwned: window.__pwned || null,
        imgs: $('listeners').querySelectorAll('img, script, b').length,
      };
      out.steps.bps = $('bps').textContent;
      out.steps.today = $('today').textContent;
      out.steps.trafficUsers = rows('trafficUsers');
      out.steps.trafficDays = rows('trafficDays').map(r => [r[0], r[2]]);
      out.steps.sys = rows('sys').map(r => r.join(' | '));
      out.steps.disks = rows('disks').map(r => r.join(' | '));
      out.steps.diskBars = {
        hot: $('disks').querySelectorAll('.usage.hot').length,
        bars: $('disks').querySelectorAll('.usage').length,
      };
      out.steps.jobs = rows('jobs').map(r => r.join(' | '));
      out.steps.problems = rows('problems');
      out.steps.problemBold = $('problems').querySelectorAll('b').length;

      await wait(5300); // visible: the quick numbers refresh by themselves
      out.steps.visiblePolling = calls();

      setVisibility('hidden');
      const before = calls();
      await wait(12000); // hidden: nothing is fetched
      out.steps.hiddenPolling = { before, after: calls() };
      setVisibility('visible');
      await wait(300);
      out.steps.backAgain = { before, after: calls() }; // coming back refreshes at once

      location.hash = '#maintenance';
      await wait(400);
      const away = calls();
      await wait(12000);
      // another tab: the status polling stops
      out.steps.otherTab = { liveHidden: $('statusLive').hidden, before: away, after: calls() };
      location.hash = '#status';
      await wait(400);
      out.steps.returned = { liveHidden: $('statusLive').hidden, after: calls() };
    } catch (e) {
      out.errors.push('driver: ' + String((e && e.stack) || e));
    }
    out.errors.push(...window.__errors);
    $('out').textContent = 'RESULT ' + JSON.stringify(out);
  })();
})();
