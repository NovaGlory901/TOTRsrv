// Runs BEFORE the archive page's own script. A tiny in-memory stand-in for the server: library,
// search and mixtapes.
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.message)));
window.addEventListener('unhandledrejection', e =>
  window.__errors.push('rejection: ' + String((e.reason && e.reason.message) || e.reason)),
);

const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const SONGS = [
  { track_id: 11, track_number: 1, title: 'In Your Eyes', artists: 'Kanal Vier' },
  { track_id: 12, track_number: 2, title: 'Dreams', artists: 'Kanal Vier' },
];
const mixes = [];
let nextMix = 1,
  nextEntry = 1;
window.__mixes = mixes;
const find = id => mixes.find(m => m.id === Number(id));
const view = m => ({
  mixtape_id: m.id,
  name: m.name,
  tracks: m.entries.map(e => ({
    entry_id: e.id,
    track_id: e.track_id,
    title: (SONGS.find(s => s.track_id === e.track_id) || {}).title,
    artists: 'Kanal Vier',
    album_id: 1,
  })),
});
const addTo = (m, ids) => {
  let added = 0;
  for (const t of ids)
    if (SONGS.some(s => s.track_id === t)) {
      m.entries.push({ id: nextEntry++, track_id: t });
      added++;
    }
  return { added, skipped: ids.length - added };
};

window.__pings = [];
window.apiFetch = async (url, opts = {}) => {
  if (url === '/api/listening/ping') {
    window.__pings.push(JSON.parse(opts.body));
    return reply({ ok: true });
  }
  const method = (opts.method || 'GET').toUpperCase(),
    body = opts.body ? JSON.parse(opts.body) : {};
  let m;
  if (url === '/api/artists')
    return reply([{ artist_id: 1, name: 'Kanal Vier', track_count: 2, cover_album_id: null }]);
  if (url === '/api/artists/1') return reply({ artist_id: 1, name: 'Kanal Vier' });
  if (url === '/api/artists/1/albums')
    return reply([
      {
        album_id: 1,
        title: 'Cold Wave',
        year: 2015,
        owner: 'Kanal Vier',
        their_tracks: 2,
        total_tracks: 2,
      },
    ]);
  if (url === '/api/artists/1/discography') return reply([]);
  if (url === '/api/artists/1/songs')
    return reply(
      SONGS.map(s => ({
        track_id: s.track_id,
        title: s.title,
        album: 'Cold Wave',
        artists: 'Kanal Vier',
      })),
    );
  if (url === '/api/albums/1')
    return reply({ album_id: 1, title: 'Cold Wave', year: 2015, owner: 'Kanal Vier' });
  if (url === '/api/albums/1/tracks') return reply(SONGS);
  if (url.startsWith('/api/albums/') && url.endsWith('/cover')) return reply({}, 404);
  if (url.startsWith('/api/search')) {
    const q = decodeURIComponent(url.split('q=')[1] || '');
    const hit = /kanal|dream|eyes|cold/i.test(q);
    return reply({
      query: q,
      totals: { artists: hit ? 1 : 0, albums: hit ? 1 : 0, songs: hit ? 2 : 0 },
      artists: hit ? [{ artist_id: 1, name: 'Kanal Vier', track_count: 2 }] : [],
      albums: hit
        ? [{ album_id: 1, title: 'Cold Wave', year: 2015, owner: 'Kanal Vier', tracks: 2 }]
        : [],
      songs: hit
        ? SONGS.map(s => ({
            track_id: s.track_id,
            title: s.title,
            artists: 'Kanal Vier',
            album_id: 1,
            album: 'Cold Wave',
          }))
        : [],
    });
  }
  if (url === '/api/mixtapes' && method === 'GET')
    return reply({
      enabled: true,
      mixtapes: mixes.map(x => ({ mixtape_id: x.id, name: x.name, track_count: x.entries.length })),
    });
  if (url === '/api/mixtapes' && method === 'POST') {
    if (mixes.some(x => x.name.toLowerCase() === body.name.toLowerCase()))
      return reply({ detail: 'You already have a mixtape with that name.' }, 409);
    const x = { id: nextMix++, name: body.name.trim(), entries: [] };
    mixes.push(x);
    return reply({ mixtape_id: x.id, name: x.name, ...addTo(x, body.track_ids || []) });
  }
  if ((m = url.match(/^\/api\/mixtapes\/(\d+)$/)) && method === 'GET')
    return find(m[1])
      ? reply(view(find(m[1])))
      : reply({ detail: 'That mixtape does not exist.' }, 404);
  if ((m = url.match(/^\/api\/mixtapes\/(\d+)\/tracks$/)))
    return reply(addTo(find(m[1]), body.track_ids));
  if ((m = url.match(/^\/api\/mixtapes\/(\d+)\/rename$/))) {
    find(m[1]).name = body.name;
    return reply({ ok: true });
  }
  if ((m = url.match(/^\/api\/mixtapes\/(\d+)\/delete$/))) {
    mixes.splice(mixes.indexOf(find(m[1])), 1);
    return reply({ ok: true });
  }
  if ((m = url.match(/^\/api\/mixtapes\/(\d+)\/tracks\/(\d+)\/remove$/))) {
    const x = find(m[1]);
    x.entries = x.entries.filter(e => e.id !== Number(m[2]));
    return reply({ ok: true });
  }
  if ((m = url.match(/^\/api\/mixtapes\/(\d+)\/tracks\/(\d+)\/move$/))) {
    const x = find(m[1]),
      i = x.entries.findIndex(e => e.id === Number(m[2])),
      j = body.direction === 'up' ? i - 1 : i + 1;
    if (j >= 0 && j < x.entries.length) [x.entries[i], x.entries[j]] = [x.entries[j], x.entries[i]];
    return reply({ ok: true });
  }
  return reply({ detail: 'not stubbed: ' + method + ' ' + url }, 404);
};
window.prompt = () => 'Road Two';
window.confirm = () => true;

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
