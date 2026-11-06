// Unit tests for static/accounts.js (remembered sign-ins, the page's side). Run by
// tests/test_accounts.py (needs Node); prints "ok  ..." / "BAD ..." per check.
const fs = require('fs'),
  vm = require('vm'),
  path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', 'static', 'accounts.js'), 'utf8');
let fails = 0;
const check = (name, cond, extra) => {
  if (!cond) fails++;
  console.log(
    (cond ? 'ok  ' : 'BAD ') +
      name +
      (!cond && extra !== undefined ? '   <- ' + JSON.stringify(extra) : ''),
  );
};

// loads the script into a fresh page; `timers` collects every timeout so a test can fire it by hand
function page(win, timers = []) {
  const sandbox = {
    window: win,
    JSON,
    Map,
    Promise,
    setTimeout: (f, ms) => {
      timers.push({ f, ms });
      return timers.length;
    },
    clearTimeout: n => {
      if (timers[n - 1]) timers[n - 1].f = () => {};
    },
  };
  vm.runInNewContext(code, sandbox);
  return win.TotrAccounts;
}
const phone = (replies, log = [], delay = () => 0) => ({
  accountCall(id, op, args) {
    log.push({ id, op, args: JSON.parse(args) });
    setImmediate(() =>
      setTimeout(
        () => win_.__totrAccountsDone(Number(id), replies(op, JSON.parse(args))),
        delay(op),
      ),
    );
  },
});
let win_ = null;
const mk = (extra = {}) => {
  win_ = { ...extra };
  return win_;
};

process.on('exit', code => {
  if (!done) {
    console.log('BAD the test never finished (a call never settled)');
    process.exitCode = 1;
  }
});
let done = false;
(async () => {
  let A = page(mk());
  check(
    'in a plain browser nothing is supported and every call does nothing',
    !A.supported() &&
      (await A.auto()) === false &&
      (await A.save('u', 'p')) === null &&
      (await A.suggestPassword()) === '' &&
      (await A.signedOut()) === null,
  );

  A = page(mk({ TotrNative: { version: () => 1 } }));
  check(
    'an install without the accounts call (an older app) is not supported',
    !A.supported() && (await A.auto()) === false,
  );

  let log = [];
  A = page(
    mk({
      TotrNative: phone(
        op =>
          JSON.stringify(
            op === 'auto'
              ? { signedIn: true }
              : op === 'suggest'
                ? { password: 'Abc23456789abcdefghjkmnp' }
                : { ok: true },
          ),
        log,
      ),
    }),
  );
  check('with the Android bridge it is supported', A.supported());
  check(
    'auto() is true when the app signed this window in',
    (await A.auto()) === true && log[0].op === 'auto',
  );
  check(
    'save() passes the username and password to the app and gets its answer',
    JSON.stringify(await A.save('rae', 'secret')) === '{"ok":true}' &&
      JSON.stringify(log[1].args) === '{"username":"rae","password":"secret"}',
  );
  check(
    'signedOut() tells the app',
    (await A.signedOut()).ok === true && log[2].op === 'signedOut',
  );
  check(
    "suggestPassword() returns the app's password",
    (await A.suggestPassword()) === 'Abc23456789abcdefghjkmnp',
  );

  A = page(mk({ TotrNative: phone(() => JSON.stringify({ signedIn: false })) }));
  check('auto() is false when the app did not sign in', (await A.auto()) === false);
  A = page(mk({ TotrNative: phone(() => JSON.stringify({ signedIn: 'yes' })) }));
  check('only a real true counts (a truthy string does not)', (await A.auto()) === false);
  A = page(mk({ TotrNative: phone(() => 'not json') }));
  check(
    'an answer that is not JSON is nothing, not a crash',
    (await A.auto()) === false && (await A.suggestPassword()) === '',
  );
  A = page(mk({ TotrNative: phone(() => JSON.stringify({ password: 42 })) }));
  check('a password that is not text is ignored', (await A.suggestPassword()) === '');
  A = page(
    mk({
      TotrNative: {
        accountCall() {
          throw new Error('boom');
        },
      },
    }),
  );
  check(
    'a bridge that throws gives nothing',
    (await A.auto()) === false && (await A.save('u', 'p')) === null,
  );

  A = page(
    mk({
      TotrNative: phone(
        op => JSON.stringify({ op }),
        [],
        op => ({ save: 40, signedOut: 20, auto: 0 })[op],
      ),
    }),
  );
  const [s, g, au] = await Promise.all([A.save('u', 'p'), A.signedOut(), A.auto()]);
  check(
    'three calls at once, answered in the opposite order, each get their own answer',
    s.op === 'save' && g.op === 'signedOut' && au === false,
    [s, g, au],
  );

  const timers = [];
  A = page(
    mk({
      TotrNative: {
        accountCall() {
          /* never answers */
        },
      },
    }),
    timers,
  );
  const p1 = A.auto(),
    p2 = A.save('u', 'p'),
    p3 = A.suggestPassword();
  check(
    'the waits are 40 s for a sign-in, 130 s for a save (the person answers a dialog) and 10 s for a suggestion',
    JSON.stringify(timers.map(t => t.ms)) === '[40000,130000,10000]',
    timers.map(t => t.ms),
  );
  timers.forEach(t => t.f());
  check(
    'a phone that never answers ends each call cleanly',
    (await p1) === false && (await p2) === null && (await p3) === '',
  );

  const dlog = [];
  const shell = {
    accounts: {
      call: async (op, args) => {
        dlog.push({ op, args });
        return op === 'auto' ? { signedIn: true } : { ok: true };
      },
    },
  };
  const alog = [];
  A = page(mk({ totrShell: shell, TotrNative: phone(() => '{}', alog) }));
  check(
    'the desktop shell is used when there is one, and the Android bridge is not touched',
    A.supported() &&
      (await A.auto()) === true &&
      (await A.save('u', 'p')).ok === true &&
      dlog.length === 2 &&
      alog.length === 0,
  );
  A = page(
    mk({
      totrShell: {
        accounts: {
          call: async () => {
            throw new Error('ipc');
          },
        },
      },
    }),
  );
  check('a desktop call that fails gives nothing', (await A.auto()) === false);
  A = page(mk({ totrShell: { accounts: {} } }));
  check('a shell without the call is not supported', !A.supported());

  done = true;
  console.log(fails ? `\n${fails} FAILED` : '\nALL PASSED');
  process.exit(fails ? 1 : 0);
})();
