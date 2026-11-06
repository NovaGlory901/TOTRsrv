# Operations runbook

Everything on the server is run as your server user from `/srv/totr/TOTRsrv` with the venv activated (`source .venv/bin/activate`).

## Start, stop, restart
- Start: `python main.py` (prints the address, e.g. `http://192.0.2.10:8080/`). Stop with Ctrl+C.
- **Restart after** a `git pull` that changed Python files, and after any `.env` change (it is read once at startup).
- Templates and static files are read per request, so a pure template change shows after a refresh (Ctrl+F5 for cached CSS/JS).
- Optional: a systemd unit that runs `ExecStart=/srv/totr/TOTRsrv/.venv/bin/python main.py` with `WorkingDirectory` set to that folder. If you run it as
  a service, the **service's user** must be in `smbusers` and able to read `.env`. A locked-down unit for a checkout, with the steps, is `deploy/totr-checkout.service.example`.

## Logs
The server prints to the terminal it was started in: one line per request (an invite link in it shows as `/invite/[hidden]`), and a traceback for unexpected errors (the browser only ever sees a generic message).
The audit trail of approved/refused/dismissed fixes is `reports/actions.log`; collector adds are in `reports/collector_added.jsonl`.

## Health
**Admin -> Status** (or `/api/health`). The page also shows who is listening now, how much data is being streamed (and per day and person), the computer's CPU, memory, drives and temperatures, library size, background jobs and recent problems; it refreshes every few seconds only while you are looking at it. Expected on a healthy server: database OK, all audio files found, music folder writable, uploads enabled, accounts enabled.
Yellow rows (maintenance without the role, empty Dream folder) are optional. `/health` is the tiny public check for monitors.

## Accounts
| Need | Do |
|---|---|
| First admin | `python -m auth.cli create-admin NAME` (hidden password prompt) |
| Add a person | Admin -> Users -> pick a role and permissions -> Create invite link (one-time, 7 days). They appear in the list as invited until they sign up |
| Give or take away abilities | Admin -> Users -> tick permissions -> Save permissions (takes effect on their next click) |
| Remove a person | Admin -> Users -> Delete user, then type their username. Not yourself, not the last admin |
| Forgot/lost admin password | `python -m auth.cli reset-password NAME` (signs them out everywhere) |
| See accounts | `python -m auth.cli list` |
| Disable someone | Admin -> Users -> Disable (their sessions end immediately) |

## Backups
The database is yours to back up (pg_dump on the database server, e.g. `pg_dump -h /run/postgresql -U <admin> totr > totr.sql`). Music files are the library itself.
App state worth copying: `TOTRsrv/.env` (secrets, keep private) and `TOTRsrv/reports/` (audit log, wanted list, ignored findings, collector history).
Removed files are in `MUSIC_ROOT/.trash/<timestamp>/` (restore by moving them back and re-adding rows with the maintenance "import" fix). Empty it yourself when sure.

## Scheduled work
- The collector searches by itself (default weekly at 3 am) while the server is running. It only searches; downloads wait for approval.
- Spider scans run by themselves nightly (schedule on Admin -> Maintenance); you can also press Scan now or run `python -m spiders.run`.

## Troubleshooting (real cases)
| Symptom | Cause and fix |
|---|---|
| Users page says permissions are not switched on | The database predates per-user permissions: run `sql/user_permissions.sql` in pgAdmin; it notices within a minute |
| Whole site returns 503 "Accounts are not set up" | `DB_AUTH_*` missing in `.env`, or the `totr` schema (`sql/auth_schema.sql`) not set up, or no restart. Fail-closed by design |
| `password authentication failed for user "totr_xxx"` in the log | The role's password and the one in `.env` differ. `ALTER ROLE totr_xxx PASSWORD '...'` in pgAdmin, make `.env` match exactly (no quotes/spaces), restart. Test: `psql -h /run/postgresql -U totr_xxx totr -c "select current_user"` |
| Status: "music folder read-only"; uploads fail with permission errors | `sudo chmod g+w /srv/totr/music`; `sudo usermod -aG smbusers <your user>`; log out and in; restart from the new login |
| Status: "audio files missing" on a development machine | Expected: the files are on the server |
| Approve says "Fix actions are not set up" | `DB_MAINT_*` missing or role not created (`sql/maintenance_role.sql`) |
| A fix or undo says "not added by TOTR" or "something else links to it" | By design: TOTR only changes items it created and never ones something else in the database links to. Change those where you keep that data |
| Upload says "Two artists in your database are called ..." | Two artist items in `media` share a name (ignoring case/spacing); TOTR will not guess which. Merge or rename one in your database |
| `relation "totr.v_..." does not exist` | `sql/totr_views.sql` has not been run, or the role lacks the grants from file 003 |
| Collector: `403 from ccmixter.org` | Hotlink protection: downloads need a `Referer` from the release page. Fixed in `collector/http.py`; make sure you have the latest code |
| Collector: `500/503 from archive.org`, `handshake timed out` | The Internet Archive was overloaded. Retries with resume are built in; press Add to library again later |
| Collector: "Adding needs the maintenance database role" | Same as the maintenance role above |
| Online lookup finds nothing or stops | It uses Deezer (no setup). "Stopped after repeated errors" means the site was unreachable or refused; try again later. Setting a contact switches it to MusicBrainz |
| Pages load but nothing works, console says script refused (`text/plain`) | Windows MIME table; `main.py` sets `.js`/`.css` types itself, so this means stale code |
| Approved fix says "The music folder is not available" | The drive is unmounted; the guard refuses to touch rows. Mount it and scan again |
| Cover art never shows | The album's `cover_art_path` points to a missing file (the `missing_cover` spider finds these) |
| Locked out after too many wrong passwords | Wait 15 minutes, or restart the server (throttling is in memory) |

## Security housekeeping
Rotate database passwords occasionally and after any paste into chat: `ALTER ROLE ... PASSWORD '...'`, update `.env`, restart. Keep `.env` at `chmod 600`.
Review `python -m auth.cli list` for accounts you no longer need.
