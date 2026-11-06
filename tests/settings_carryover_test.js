// Run: node tests/settings_carryover_test.js
const C = require('../static/settings-carryover.js');
let failed = 0;
function check(name, ok) {
  console.log((ok ? 'ok  ' : 'BAD ') + ' ' + name);
  if (!ok) failed++;
}
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

const a = fakeStore({
  'totr.dejavuamp.v1.nova': '{"volumeDb":-9}',
  'totr.falsehorizon.v1.nova': '{"on":false}',
});
check(
  'copies both old settings to the new keys',
  C.run(a, ['dejavuamp.v1.nova', 'falsehorizon.v1.nova']) === 2 &&
    a.m['dejavuamp.v1.nova'] === '{"volumeDb":-9}' &&
    a.m['falsehorizon.v1.nova'] === '{"on":false}',
);
check(
  'the old keys are left alone',
  a.m['totr.dejavuamp.v1.nova'] === '{"volumeDb":-9}' && 'totr.falsehorizon.v1.nova' in a.m,
);
check(
  'a second run changes nothing',
  C.run(a, ['dejavuamp.v1.nova', 'falsehorizon.v1.nova']) === 0,
);

const b = fakeStore({
  'totr.dejavuamp.v1.n': '{"volumeDb":-9}',
  'dejavuamp.v1.n': '{"volumeDb":-20}',
});
check(
  'new settings win over old ones',
  C.run(b, ['dejavuamp.v1.n']) === 0 && b.m['dejavuamp.v1.n'] === '{"volumeDb":-20}',
);
check('nothing old, nothing copied', C.run(fakeStore(), ['dejavuamp.v1.x']) === 0);
check(
  "another person's settings are not touched",
  (() => {
    const s = fakeStore({ 'totr.dejavuamp.v1.friend': '{}' });
    C.run(s, ['dejavuamp.v1.nova']);
    return !('dejavuamp.v1.friend' in s.m);
  })(),
);
check(
  'blocked, missing or odd storage is safe',
  C.run(
    {
      getItem() {
        throw new Error('denied');
      },
      setItem() {
        throw new Error('denied');
      },
    },
    ['a'],
  ) === 0 &&
    C.run(null, ['a']) === 0 &&
    C.run(fakeStore(), null) === 0 &&
    C.run(fakeStore(), [1, null, {}]) === 0,
);

console.log(failed ? `\n${failed} FAILED` : '\nALL PASSED');
process.exit(failed ? 1 : 0);
