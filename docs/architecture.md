# Architecture

## The pieces
```
 Browser ──HTTP──> uvicorn ──> gate (auth/web.py middleware) ──> route handlers (main.py, auth/, collector/)
                                   │                                   │
                                   │ session lookup                    ├─ music queries ........ PostgreSQL via role "robots" (read-only)
                                   v                                   ├─ uploads .............. role "totr_uploader" + files on disk
                          PostgreSQL via "totr_auth"                   ├─ approved fixes/adds .. role "totr_maint"   + files on disk
                          (users, sessions, invites)                   └─ spiders/collector state: JSON files in reports/
                                                                   Outbound: collector/http.py only (allow-listed hosts)
```
One Python process (FastAPI on uvicorn), no background services. Two background threads exist: the collector's
scheduler (wakes every 10 minutes, searches weekly) and the collector's download worker (one download at a time).
Spider scans and collector searches run in short-lived threads when started from the Admin page.

## Request flow
1. Every request passes the **gate** (`auth/web.py::install`): size/Origin checks for changing requests, session lookup
   (cookie -> SHA-256 -> `sessions` row), then access rules: public paths, member paths, admin-only prefixes, CSRF token for
   POST/PUT/PATCH/DELETE. The gate adds security headers to every response.
2. Page routes call `render_page`, which fills `{{HEADER}}`/`{{FOOTER}}`, inserts the CSRF `<meta>` tag and `app.js`, and adds a
   CSP nonce to every inline `<script>`.
3. The page's JavaScript calls JSON endpoints through `apiFetch`.

## Module map (`TOTRsrv/`)
| Module | Responsibility |
|---|---|
| `main.py` | App creation, lifespan (starts the collector and spider schedulers, saves the traffic totals on exit), page routes, music/radio/upload/health/maintenance routes |
| `app_settings.py` | Non-secret site settings edited on the Settings page, Site tab (the contact), with `.env` as the fallback |
| `library_search.py` | In-memory index and ranking for archive search (same name matching as the spiders) |
| `db.py` | The connection helpers (`get_conn`, `get_write_conn`, `get_maint_conn`, `get_auth_conn`, `get_mixtape_conn`) and `*_enabled()` checks |
| `catalog.py` | Every write to the music catalog, which lives in the `media` ontology: find-or-create artists (matched by normalised name), add albums and songs as items and relations, edits, deletes and merges. Enforces "only what TOTR created, never what something else links to" |
| `textnorm.py` | `norm` and `squash`, the name matching shared by spiders, search and the catalog |
| `uploader.py` | Tag reading, safe file names, `save_album`, and `insert_tracks` (shared by uploads, imports and collector adds; it calls `catalog`) |
| `helpers.py` | Finds the machine's LAN IP |
| `auth/` | `passwords` (Argon2id + policy), `limiter` (login throttling), `store` (all account SQL), `service` (rules), `web` (gate, pages, endpoints), `cli` |
| `spiders/` | `base` (Finding, Snapshot, helpers), `files`, `duplicates`, `metadata`, `enrich`, `musicbrainz`, `actions` (fix handlers; they call `catalog`), `store` (report/ignored list/audit), `run`, `scheduler` |
| `updates/` | Signed updates: `signing` (release format, signature check, safe unpacking), `fetch` (GitHub through `collector/http.py`, download, verify, file the request), `web` (admin API), `__main__` (command line). The installing is `deploy/apply-update.py` (root, run by systemd), see `updates.md` |
| `mixtapes/` | Personal playlists: `store` (every query, limited to the owner) and `web` (the API) |
| `monitor/` | The Status tab's live numbers: `system` (CPU, memory, drives, temperatures), `traffic` (bytes sent, counted by an ASGI wrapper), `listeners` (who is playing what), `errors` (recent problems) |
| `collector/` | `quality` (the 0-100 score), `licenses`, `http` (network guard, retries, resume), `sources/` (one file per site), `finder`, `acquire` (download, import, undo), `jobs`, `scheduler`, `state`, `web` |
| `templates/` | One HTML file per page |
| `static/` | (copies of the DejaVuAmp and FalseHorizons scripts, made by `tools/sync-vendor.cjs`; the originals are in the DejaVuAmp and FalseHorizons repositories) `style.css`, `app.js` (also the listening heartbeat), `radio-core.js` (radio logic, tested under Node), `dejavuamp-core.js` / `dejavuamp.js` / `dejavuamp.css` (the DejaVuAmp player rack: logic, audio graph and UI), `falsehorizon-*.js` / `falsehorizon.css` (the FalseHorizon visualizer: formula language, logic, presets, WebGL engine, controls), `radio-viz.js` (the classic photo-smear style), `radio-viz.js` (the WebGL visualizer) |
| `sql/` | Scripts the owner runs, for fresh databases |
| `tools/` | Read-only helpers: `schema_inventory.py`, `schema_check.py` (does the database match the documented layout?) |
| `tests/` | Test suites and real-data fixtures |

## Data
- **Music library:** items and relations in the `media` ontology (artist, album and song items; `track_on_album`, `performed_by`, `album_by`), read through
  views in schema `totr`, plus the files under `MUSIC_ROOT`. A song's `file_path` (in the item's metadata) is authoritative. See `database.md`.
- **Accounts and mixtapes:** schema `totr` (separate roles).
- **Everything else is JSON in `reports/`:** spider report, ignored findings, spider schedule, site settings (`app_settings.py`), audit log, collector settings/suggestions/wanted/added log,
  MusicBrainz cache. Machine-specific and git-ignored. Writes are atomic (temp file + rename).

## How the spiders work (see `spiders.md`)
`run.py` loads a **Snapshot** of the library (rows + music root), hands it to each spider (`check(snap) -> [Finding]`), assigns
stable ids, removes permanently ignored findings, merges with earlier findings of spiders that did not run, and writes the report.
A finding may carry an **action** `{type, label, detail, params, destructive}`. Approving one (`/api/maintenance/approve`)
sends only the finding id; `spiders/actions.py` re-checks preconditions and runs the handler, usually in one transaction on
the maintenance role.

## How the collector works (see `collector.md`)
`finder.search_all` asks each enabled **source** for releases (by genre, and by artist/title for the wanted list), drops anything
owned/dismissed/already offered, and saves **suggestions**. Approving one queues a job; `acquire.acquire` re-validates the license
and hosts, downloads with retries/resume, verifies the audio, tags it, adds items via `uploader.insert_tracks` / `catalog`, and records
provenance. `enrich` (a spider) feeds the **wanted list**, which the finder searches first.

## Design rules that shape the code
- Deny by default; fail closed.
- Third-party data is hostile: validate, host-check, escape.
- Propose, then approve; re-check at approval time.
- Never hard-delete files (`.trash`); never leave half-done work (clean up on any failure).
- Bookkeeping failures after a commit are logged, never turned into rollbacks of committed work.
- SQL is parameterised and portable; times come from Python.
