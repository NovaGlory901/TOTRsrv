// Runs BEFORE the radio page's own script (tests/test_radio.py assembles the page). Replaces the
// server with canned answers and makes every song "play" a generated tone, so the real page script
// can be driven in a browser with no server.
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.message)));
window.addEventListener('unhandledrejection', e =>
  window.__errors.push('rejection: ' + String((e.reason && e.reason.message) || e.reason)),
);

window.__calls = { next: 0, urls: [], pings: [] };
const TRACKS = [1, 2, 3, 4, 5].map(i => ({
  track_id: i,
  title: 'Song ' + i,
  artists: 'Artist ' + i,
  album_id: null,
}));
const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
window.apiFetch = async (url, opts = {}) => {
  window.__calls.urls.push(url);
  if (url === '/api/listening/ping') {
    window.__calls.pings.push(JSON.parse(opts.body));
    return reply({ ok: true });
  }
  if (url.startsWith('/api/auth/me'))
    return reply({ username: 'tester', role: 'member', permissions: [], csrf: 'x' });
  if (url.startsWith('/api/radio/next'))
    return reply(TRACKS[window.__calls.next++ % TRACKS.length]);
  if (url.startsWith('/api/radio/media')) return reply([]);
  return reply({}, 404);
};

// a two-second 440 Hz tone as a blob: same origin as the page, so the audio graph may read it
(function () {
  const rate = 8000,
    n = rate * 2,
    buf = new ArrayBuffer(44 + n * 2),
    v = new DataView(buf);
  const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, 'RIFF');
  v.setUint32(4, 36 + n * 2, true);
  w(8, 'WAVEfmt ');
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  w(36, 'data');
  v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++)
    v.setInt16(44 + i * 2, Math.sin((i / rate) * 2 * Math.PI * 440) * 12000, true);
  const tone = URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
  const real = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
  Object.defineProperty(HTMLMediaElement.prototype, 'src', {
    configurable: true,
    get() {
      return this.__asked || real.get.call(this);
    },
    set(value) {
      this.__asked = value;
      real.set.call(this, tone);
    },
  });
})();
