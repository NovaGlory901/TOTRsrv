// Shared by every signed-in page. The server puts this session's CSRF token in a <meta> tag;
// apiFetch sends it with every request that changes something, and sends you to the login
// page if the session has ended.
(() => {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const csrf = meta ? meta.content : '';

  // Shared by every page: getElementById, and a new element with a class and text. Text goes in
  // with textContent only, so nothing from a report or a website is ever parsed as HTML.
  window.$ = id => document.getElementById(id);
  window.el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };

  window.apiFetch = async (url, opts = {}) => {
    opts = { credentials: 'same-origin', ...opts };
    const method = (opts.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') {
      opts.headers = { ...(opts.headers || {}), 'X-CSRF-Token': csrf };
    }
    const response = await fetch(url, opts);
    if (response.status === 401) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
    }
    return response;
  };

  // Tells the server this device is still playing a song (about every 30 seconds), so an admin's
  // Status tab can show who is listening. `trackId()` returns the id of the song now playing.
  // Pausing or leaving the page says "stopped".
  window.trackListening = (audio, source, trackId) => {
    let timer = null;
    const send = (state, extra = {}) =>
      window
        .apiFetch('/api/listening/ping', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ track_id: trackId() || null, state, source }),
          ...extra,
        })
        .catch(() => {});
    audio.addEventListener('play', () => {
      send('playing');
      clearInterval(timer);
      timer = setInterval(() => {
        if (!audio.paused) send('playing');
      }, 30000);
    });
    audio.addEventListener('pause', () => {
      clearInterval(timer);
      send('paused');
    });
    window.addEventListener('pagehide', () => send('stopped', { keepalive: true }));
  };

  document.addEventListener('DOMContentLoaded', () => {
    const out = document.getElementById('logout');
    if (out) {
      out.onclick = async () => {
        await window.apiFetch('/api/auth/logout', { method: 'POST' });
        // signed out by hand: the app must not sign this person straight back in
        if (window.TotrAccounts) await window.TotrAccounts.signedOut();
        location.href = '/login';
      };
    }
  });
})();
