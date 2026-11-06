// Runs BEFORE the Admin page's own script: a stand-in server for the Status tab, with canned
// numbers and a call counter.
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.message)));
window.addEventListener('unhandledrejection', e =>
  window.__errors.push('rejection: ' + String((e.reason && e.reason.message) || e.reason)),
);
window.__calls = { health: 0, live: 0, other: [] };
const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });

const HEALTH = {
  status: 'ok',
  uptime_s: 90061,
  host: 'server',
  checks: {
    database: { ok: true, latency_ms: 3.2, counts: { artists: 56, albums: 5, tracks: 142 } },
    audio_files: { ok: true, checked: 142, missing: 0, missing_examples: [] },
    music_root: { ok: true, path: '/srv/music', writable: true },
    uploads: { ok: true, db_write_role: true },
    accounts: { ok: true },
    maintenance: { ok: true, actions_enabled: true },
    radio_media: { ok: true, path: '/srv/media', exists: true, files: 12 },
  },
};

const GB = 1024 ** 3;
const LIVE = {
  at: '2026-10-03T12:00:00+00:00',
  listeners: [
    {
      user: 'alice',
      track: { track_id: 5, title: 'In Your Eyes', artists: 'Tigerberry' },
      state: 'playing',
      source: 'radio',
      device: 'Edge on Windows',
      where: '192.168.1.50 (home network)',
      listening_s: 125,
      last_s: 4,
    },
    {
      user: '<img src=x onerror=window.__pwned=1>',
      track: { track_id: 6, title: '<b>Bold</b>', artists: '<script>window.__pwned=2</script>' },
      state: 'paused',
      source: 'archive',
      device: 'Safari on iPhone',
      where: '10.0.0.7 (home network)',
      listening_s: 7,
      last_s: 30,
    },
    {
      user: 'bob',
      track: null,
      state: 'playing',
      source: 'archive',
      device: 'a browser',
      where: 'unknown address',
      listening_s: 3700,
      last_s: 1,
    },
  ],
  traffic: {
    bps: 1572864,
    today: { audio: 3 * GB, media: 5 * 1024 * 1024, total: 3 * GB + 5 * 1024 * 1024 },
    users_today: [
      { user: 'alice', bytes: 2 * GB },
      { user: 'bob', bytes: GB },
    ],
    days: [
      { date: '2026-10-02', audio: GB, media: 1024 },
      { date: '2026-10-03', audio: 3 * GB, media: 5 * 1024 * 1024 },
    ],
  },
  system: {
    platform: 'linux',
    cores: 4,
    cpu_percent: 37.5,
    load: [0.5, 0.4, 0.3],
    memory: { total: 16 * GB, used: 6 * GB, available: 10 * GB, percent: 37.5 },
    uptime_s: 864000,
    temps: [{ label: 'coretemp Package id 0', celsius: 52 }],
    disks: [
      {
        label: 'music library',
        path: '/srv/music',
        ok: true,
        total: 4000 * GB,
        used: 3700 * GB,
        free: 300 * GB,
        percent: 92.5,
      },
      { label: 'dream folder', path: '/nope', ok: false },
    ],
  },
  library: {
    counts: { artists: 56, albums: 5, tracks: 142 },
    size: { bytes: 12 * GB, files: 142, measuring: false },
  },
  spiders: {
    running: false,
    progress: '',
    last_scan: '2026-10-03T03:00:00+00:00',
    findings: 7,
    ran_at: {},
  },
  collector: {
    searching: false,
    progress: '',
    last_search: '2026-10-01T03:00:00+00:00',
    suggestions: 24,
    downloads_active: 2,
    added: 11,
  },
  problems: [
    {
      at: '2026-10-03 11:59:00',
      level: 'ERROR',
      logger: 'totr',
      message: 'disk <b>full</b> (OSError)',
    },
    {
      at: '2026-10-03 11:58:00',
      level: 'WARNING',
      logger: 'uvicorn.error',
      message: 'slow request',
    },
  ],
};

window.apiFetch = async url => {
  if (url === '/api/auth/me')
    return reply({
      username: 'Nova',
      role: 'admin',
      permissions: ['upload', 'collector', 'maintenance', 'status', 'settings', 'users'],
      admin_page: true,
      csrf: 'x',
    });
  if (url === '/api/health') {
    window.__calls.health++;
    return reply(HEALTH);
  }
  if (url === '/api/health/live') {
    window.__calls.live++;
    return reply(LIVE);
  }
  window.__calls.other.push(url);
  return reply({}, 404);
};
