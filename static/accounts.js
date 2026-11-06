// Remembered sign-ins. The Android app and the desktop apps can remember a person's username and
// password for each server and sign in for them (the app, not this page, keeps the password: the
// page only ever gets a yes or no). In a plain browser there is no such thing and every call here
// does nothing. Shared by every page the server renders. auto() -> true when the app has just
// signed this browser window in; save(username, password) asks the app to offer to remember it (the
// app asks the person, on its own screen); signedOut() tells the app the person signed out by hand
// (so it does not sign them straight back in); suggestPassword() -> a strong random password or ''.
(() => {
  const android = () =>
    window.TotrNative && typeof window.TotrNative.accountCall === 'function'
      ? window.TotrNative
      : null;
  const desktop = () =>
    window.totrShell &&
    window.totrShell.accounts &&
    typeof window.totrShell.accounts.call === 'function'
      ? window.totrShell.accounts
      : null;
  const waiting = new Map();
  let next = 0;

  window.__totrAccountsDone = (id, json) => {
    const f = waiting.get(id);
    if (f) {
      waiting.delete(id);
      f(json);
    }
  };

  function call(op, args, ms) {
    const d = desktop();
    if (d)
      return Promise.race([
        d.call(op, args || {}),
        new Promise(r => setTimeout(() => r(null), ms)),
      ]).catch(() => null);
    const a = android();
    if (!a) return Promise.resolve(null);
    return new Promise(resolve => {
      const id = ++next;
      const timer = setTimeout(() => {
        waiting.delete(id);
        resolve(null);
      }, ms);
      waiting.set(id, json => {
        clearTimeout(timer);
        try {
          resolve(JSON.parse(json));
        } catch {
          resolve(null);
        }
      });
      try {
        a.accountCall(String(id), op, JSON.stringify(args || {}));
      } catch {
        waiting.delete(id);
        clearTimeout(timer);
        resolve(null);
      }
    });
  }

  window.TotrAccounts = {
    supported: () => !!(android() || desktop()),
    auto: async () => {
      const r = await call('auto', {}, 40000);
      return !!(r && r.signedIn === true);
    },
    save: (username, password) => call('save', { username, password }, 130000),
    signedOut: () => call('signedOut', {}, 10000),
    suggestPassword: async () => {
      const r = await call('suggest', {}, 10000);
      return r && typeof r.password === 'string' ? r.password : '';
    },
  };
})();
