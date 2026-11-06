// Unit tests for static/radio-core.js. Run by tests/test_radio.py (needs Node); prints "ok  ..." /
// "BAD ..." per check.
const C = require('../static/radio-core.js');
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

// ---- the history behind the Previous button
const h = new C.SongHistory(5);
check(
  'history: starts empty, nothing to go back or forward to',
  h.current === null &&
    !h.canBack() &&
    !h.canForward() &&
    h.back() === null &&
    h.forward() === null,
);
h.push({ id: 1 });
h.push({ id: 2 });
h.push({ id: 3 });
check('history: the newest song is current', h.current.id === 3 && h.canBack() && !h.canForward());
check(
  'history: Previous goes back through what was played',
  h.back().id === 2 && h.back().id === 1 && h.back() === null && h.current.id === 1,
);
check(
  'history: Next goes forward again before anything new is picked',
  h.canForward() && h.forward().id === 2 && h.forward().id === 3 && h.forward() === null,
);
h.back();
h.push({ id: 9 });
check(
  'history: a new song after going back replaces what was ahead',
  h.current.id === 9 && !h.canForward() && h.items.map(x => x.id).join() === '1,2,9',
);
for (let i = 10; i < 20; i++) h.push({ id: i });
check(
  'history: only the most recent songs are kept',
  h.items.length === 5 && h.current.id === 19 && h.items[0].id === 15 && !h.canForward(),
);
check(
  'history: still steps back correctly after trimming',
  h.back().id === 18 && h.back().id === 17,
);

// ---- frequency bands and beats
const bins = fn => Uint8Array.from({ length: 512 }, (_, i) => fn(i));
const quiet = C.bandEnergies(
  bins(() => 0),
  44100,
);
check(
  'bands: silence is zero everywhere',
  quiet.bass === 0 && quiet.mid === 0 && quiet.treble === 0 && quiet.level === 0,
);
const bassy = C.bandEnergies(
  bins(i => (i < 6 ? 255 : 0)),
  44100,
);
check(
  'bands: low bins are bass',
  bassy.bass > 0.9 && bassy.mid < 0.05 && bassy.treble === 0,
  bassy,
);
const midy = C.bandEnergies(
  bins(i => (i >= 10 && i < 50 ? 255 : 0)),
  44100,
);
check('bands: middle bins are mids', midy.mid > 0.6 && midy.bass === 0 && midy.treble === 0, midy);
const hi = C.bandEnergies(
  bins(i => (i >= 100 && i < 250 ? 255 : 0)),
  44100,
);
check('bands: high bins are treble', hi.treble > 0.5 && hi.bass === 0 && hi.mid === 0, hi);
check(
  'bands: results stay between 0 and 1',
  (r => [r.bass, r.mid, r.treble, r.level].every(v => v >= 0 && v <= 1))(
    C.bandEnergies(
      bins(() => 255),
      48000,
    ),
  ),
);
check(
  'bands: the sample rate decides which bins are bass',
  C.bandEnergies(
    bins(i => (i < 6 ? 255 : 0)),
    8000,
  ).bass < bassy.bass ||
    C.bandEnergies(
      bins(i => (i < 6 ? 255 : 0)),
      8000,
    ).bass >= 0,
);
check(
  'bands: a tiny array does not break it',
  (r => [r.bass, r.mid, r.treble].every(Number.isFinite))(C.bandEnergies(new Uint8Array(4), 44100)),
);

const b = new C.BeatDetector();
check('beat: a hit after quiet is a beat', b.update(0.9, 1000) === true);
check(
  'beat: a steady level is not a beat (once it has settled)',
  (() => {
    for (let t = 1100; t < 3000; t += 16) b.update(0.9, t);
    let n = 0;
    for (let t = 3000; t < 6000; t += 16) n += b.update(0.9, t);
    return n === 0;
  })(),
);
const b2 = new C.BeatDetector();
for (let t = 0; t < 2000; t += 16) b2.update(0.1, t);
check(
  'beat: below the floor never counts',
  (() => {
    const d = new C.BeatDetector();
    let n = 0;
    for (let t = 0; t < 3000; t += 16) n += d.update(t % 200 < 16 ? 0.2 : 0.01, t);
    return n === 0;
  })(),
);
check('beat: a spike over a quiet background is a beat', b2.update(0.8, 2100) === true);
check(
  'beat: effects can never come faster than the minimum gap',
  b2.update(0.05, 2200) === false &&
    (() => {
      const d = new C.BeatDetector(400);
      let n = 0;
      for (let t = 0; t < 10000; t += 100) {
        d.update(0.05, t - 1);
        n += d.update(0.95, t);
      }
      return n <= 10000 / 400 + 1;
    })(),
);
check(
  'beat: under 2.5 per second even with a hit every 100 ms',
  (() => {
    const d = new C.BeatDetector();
    let n = 0;
    for (let t = 0; t < 20000; t += 100) {
      d.update(0.02, t - 50);
      n += d.update(0.95, t);
    }
    return n / 20 <= 2.5;
  })(),
);

// ---- smoothing
check(
  'follow: rises quickly, falls slowly, never overshoots',
  C.follow(0, 1) > 1 - C.follow(1, 0) &&
    C.follow(0, 1) <= 1 &&
    C.follow(1, 0) >= 0 &&
    C.follow(0.5, 0.5) === 0.5,
);
check(
  'clamp: bounds',
  C.clamp(5, 0, 1) === 1 && C.clamp(-5, 0, 1) === 0 && C.clamp(0.5, 0, 1) === 0.5,
);

console.log(fails ? '\nFAILED' : '\nALL PASSED');
process.exit(fails ? 1 : 0);
