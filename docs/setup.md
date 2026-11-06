# Setup

## Requirements
- Python 3.11+ (production uses 3.14). Nothing else is needed to try TOTR: with no database settings it keeps everything in a built-in local
  database file (see "Where TOTR keeps its data" below). PostgreSQL 14+ is optional, for people who want their own server.
- A music folder the server can read, and write to if you want uploads and the collector.

## Install as a service (Linux, the easy way)
For a server that runs by itself, starts at boot and can be updated later, use the installer. It needs Python 3.11+, `openssl` and (on Debian / Ubuntu)
`python3-venv`; PostgreSQL only if you want to use your own.
```
cd TOTRsrv
sudo ./deploy/install.sh              # asks a few plain questions
sudo ./deploy/install.sh --yes        # no questions: the built-in SQLite database and the defaults (see --help for every option)
```
What it does: it puts the code in `/opt/totr/releases/<version>/` (read-only for the service; `/opt/totr/current` points at the one in use), keeps everything that must
survive an update in `/opt/totr/state/` (`.env`, the database, reports, certificates, app installers), makes a `totr` user and a systemd service
(`systemctl status totr`, log: `journalctl -u totr`; it restarts by itself and starts at boot), and writes `.env` with mode 600.
- **Database.** The built-in SQLite file is the default and needs nothing. For PostgreSQL it asks for the host, the database and an account that may create
  databases and roles; that login is used for that run only and is never stored. It builds the layout (`migrate.py`) and makes one role per job with random passwords
  (`totr_read`, `totr_write`, `totr_maint`, `totr_auth`, `totr_mixtape`; `--pg-single-role` for one login; `--pg-role-prefix` if names clash).
- **HTTPS.** By default this server makes its own certificate authority and a certificate for its address (private network addresses and `*.local` / `*.lan` names only) and
  turns plain HTTP off. At the end it prints the authority's fingerprint: an app or browser meeting this server for the first time shows the same code. `--tls own` uses a
  certificate you have; `--tls none` serves plain HTTP (only on a closed network, or behind a proxy that does HTTPS).
- **First admin.** It prints a one-time sign-in link (valid 7 days); you choose your own name and password in the browser. No password is typed into the installer.
- **Run it again any time** to repair. It never changes a setting or secret that already exists, never replaces the certificate authority, and re-checks the service.
- **Your music folder** (`--music-dir`) must be writable by the `totr` user if you want uploads and the collector; reading and playing work either way.
- **Testing the installer** on a disposable machine or VM: `sudo ./deploy/lab-test.sh [sqlite|postgres]` (it removes `/opt/totr` first).
Changing the layout later is `python migrate.py` (see `docs/database.md`); on an installed server run it as `TOTR_STATE_DIR=/opt/totr/state /opt/totr/current/.venv/bin/python migrate.py status`
from `/opt/totr/current`.

## Install by hand (development, or a server run from a checkout)
```
cd TOTRsrv
python -m venv .venv
# Linux/NAS:   source .venv/bin/activate
# Windows dev: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt     # only on a development machine (test client)
```
Runtime packages: `fastapi`, `uvicorn`, `psycopg[binary]`, `python-dotenv`, `mutagen` (audio tags), `python-multipart`
(uploads), `argon2-cffi` (password hashes).

## Configuration (`TOTRsrv/.env`)
Read once at startup, so **restart after editing**. The file is git-ignored; keep it `chmod 600` on the server. Never paste it into chat.

### Database connection (PostgreSQL only; leave all of these out to use the built-in local database)
| Variable | Meaning |
|---|---|
| `DB_ENGINE` | Optional: `sqlite` (built-in local database) or `postgres`. By default PostgreSQL is used when `DB_HOST` is set, otherwise the local database |
| `DB_NAME` | The database name (for example `totr`) |
| `DB_HOST` | `/run/postgresql` when PostgreSQL runs on the same machine (unix socket, no SSL); the server address from another machine (SSL is then required) |
| `DB_PORT`, `DB_SSLMODE` | Optional. Default port 5432. `DB_SSLMODE` is `disable` for a socket, otherwise `require`; set e.g. `verify-full` to override |
| `DB_USER`, `DB_PASSWORD` | The **read-only** role (`robots`) used for every music query |
| `DB_SINGLE_ROLE` | `true` = the simple setup: every feature uses `DB_USER`/`DB_PASSWORD`, so the optional roles below are not needed (less isolated: the music routes could then read accounts) |
| `TOTR_DATA_DIR` | Local database only: where `totr.db` lives. Default `TOTRsrv/data/` |

Database passwords belong in `.env` and nowhere else: the admin **Database** page shows what TOTR found and writes out the `.env` lines for you, but never asks for or stores a password.

### Optional database roles (each enables a feature when set)
| Variables | Enables | Created by |
|---|---|---|
| `DB_AUTH_USER`, `DB_AUTH_PASSWORD` | **Sign-in.** Without these the whole site is locked (fails closed) | `sql/auth_schema.sql` |
| `DB_WRITE_USER`, `DB_WRITE_PASSWORD` | Uploads | `sql/upload_role.sql` |
| `DB_MAINT_USER`, `DB_MAINT_PASSWORD` | Approving spider fixes; adding/undoing collector music | `sql/maintenance_role.sql` |
| `DB_MIXTAPE_USER`, `DB_MIXTAPE_PASSWORD` | Mixtapes (personal playlists) | `sql/mixtapes.sql` |

### Paths
| Variable | Default | Meaning |
|---|---|---|
| `MUSIC_ROOT` | `music/` in the state folder (the code folder when run from a checkout) | The library. Uploads and collector adds write to `<Artist>/Albums/<Album>/` or `<Artist>/Singles/` |
| `MEDIA_ROOT` | `media/` in the state folder (the code folder when run from a checkout) | Pictures/videos for the radio collage (jpg png gif webp mp4 webm) |
| `MAINT_REPORT` | `TOTRsrv/reports/maintenance.json` | Spider report; ignored findings, the schedule and the audit log sit beside it |
| `COLLECTOR_DIR` | `TOTRsrv/reports/` | Collector state and MusicBrainz cache (`DISCOVER_DIR` still accepted) |

### Collector and online lookups
| Variable | Meaning |
|---|---|
| `COLLECTOR_CONTACT` | Fallback for the contact email or web address put in the User-Agent. Normally set by an admin under Account -> Site settings, which wins over this. **Optional.** With none, online lookups use Deezer's public catalogue, which needs no contact, key or account. Set one to use MusicBrainz instead (their rule: it needs contact details) |
| `APP_SETTINGS` | `TOTRsrv/reports/app_settings.json` | Settings edited on the Account page (not secrets) |
| `JAMENDO_CLIENT_ID` | Free key from developer.jamendo.com; Jamendo is skipped without it |

### Web behaviour
| Variable | Default | Meaning |
|---|---|---|
| `COOKIE_SECURE` | `auto` | `true` when served over HTTPS (sets the cookie `Secure` flag and HSTS); `auto` detects HTTPS directly or via a trusted proxy |
| `TRUSTED_PROXY` | none | IP of a reverse proxy whose `X-Forwarded-For`/`-Proto` headers are believed |
| `MAX_UPLOAD_MB` | `2048` | Largest allowed upload request |
| `TLS_CERT`, `TLS_KEY` | none | Paths to the server certificate and key (relative paths start in `TOTRsrv/`). Both set = the server also speaks HTTPS (see HTTPS below) |
| `TLS_PORT` | `8443` | The HTTPS port |
| `HTTP_ENABLED` | `true` | `false` closes the plain HTTP port (8080) once every app uses https |

Removed: `UPLOAD_PASSWORD` and `ADMIN_PASSWORD`. Admin accounts replaced them.

## Where TOTR keeps its data
(An installed server sets `TOTR_STATE_DIR=/opt/totr/state`, which moves `.env`, `data/`, `reports/`, `certs/` and `downloads/` out of the code folder; `paths.py` has the rules. The paths below are for a checkout.)

**Built-in local database (the default; nothing to install or know).** The first time TOTR needs it, it copies a ready-made SQLite file
(`sql/sqlite/totr_template.db`) to `TOTRsrv/data/totr.db`. That file already has every table TOTR needs, with no accounts and no music.
Back it up by copying that one file while TOTR is stopped. Requires SQLite 3.38+ (any current Python has it; the admin Database page checks).
Then follow "First run" below. Set `MUSIC_ROOT` in `.env` to your music folder (its default is a `music` folder inside the server's own folder).

**Your own PostgreSQL (optional, later).** Easiest: admin **Database** page > *Move to PostgreSQL* (see `database.md`): it makes the database and roles, copies
everything, checks it, and switches TOTR over. By hand instead: set up a database and the scripts below, fill the connection lines into `.env`, then, to bring your accounts,
playlists, settings and music list with you from the local database: `python tools/migrate_db.py export totr-export.json`, then (with `TARGET_DB_HOST`, `TARGET_DB_NAME`, `TARGET_DB_USER`,
`TARGET_DB_PASSWORD` set in your terminal) `python tools/migrate_db.py import totr-export.json`. The import only goes into an empty TOTR layout
and has a `--dry-run`. Copy your music folder yourself. Sessions are not moved: people sign in again.

## Database scripts (PostgreSQL only)
**An existing hand-made database is already set up.** Its layout is `media` (the ontology that holds the music) + `totr` (accounts, mixtapes, TOTR's views); see `database.md`.
New SQL for it is written into `sql/` by a session and run by the owner in pgAdmin.

**A fresh, empty PostgreSQL database** (someone else's install). Run as a superuser in this order, changing
every `CHANGE_ME` inside pgAdmin, never in the repo file, and the database name in the `GRANT CONNECT` lines:
1. `sql/media_ontology.sql`: schema `media` (the ontology, identical in every install: types, items, relations).
2. `sql/auth_schema.sql`: schema `totr`, the accounts tables and `totr_auth`.
3. `sql/totr_views.sql`: TOTR's views over `media`.
4. `sql/robots_role.sql`: the read-only role.
5. `sql/mixtapes.sql`: mixtape tables and `totr_mixtapes`. Then add `DB_MIXTAPE_USER` / `DB_MIXTAPE_PASSWORD` to `.env`.
6. `sql/upload_role.sql` (`totr_uploader`) and `sql/maintenance_role.sql` (`totr_maint`).
7. `sql/user_permissions.sql` only for a database made before per-user permissions existed.

## First run
1. Put the `.env` in place. 2. With PostgreSQL, make sure the database scripts above have been run (the local database needs nothing). 3. Create the first admin (password typed at a hidden prompt):
   `python -m auth.cli create-admin YOURNAME`
4. `python main.py`; open the printed address; sign in. 5. **Admin -> Status** should be green (the radio-media row is yellow until you add files).
6. **Users** page: create invite links for other people.

## Deploying to your server
See `operations.md` (restart, logs). In short: commit, push, `git pull` on the server,
`pip install -r requirements.txt` if it changed, run any new SQL, restart. **The built web app and the app installers do not travel by git**: the TOTRspa repository's `shared/tools/deploy.cjs` sends them.

## HTTPS (TOTR's own certificate authority)
The server can speak HTTPS itself, with a certificate from a small private authority you make once (no purchase, no outside service). The authority
is **name-constrained**: it can only vouch for private network addresses (10.x, 192.168.x, 172.16-31.x) and names ending `.local` or `.lan`, so even a
leaked key could not be used against real websites. The apps carry the authority's public certificate, so they need no setup on the device.

**Once, on a dev machine, in `TOTRsrv/`:**
1. `python tools/make_certs.py ca` makes `certs/totr-ca.key` (SECRET, protected by a passphrase you choose) and `certs/totr-ca.pem` (public).
   Back the key up somewhere safe. It never goes on the server and never in git (`certs/` is git-ignored). If you lose it, run `ca` again (delete the old files first) and rebuild the apps.
2. `python tools/make_certs.py server --ip 192.0.2.10 --name totr.local` makes `certs/server.key` + `certs/server.pem` (asks for the passphrase). The certificate lasts
   825 days: run this again, with the same command, before it runs out (`python tools/make_certs.py check` shows the date).
3. Copy **only** the server pair to the server: `ssh admin@192.0.2.10 'mkdir -p /srv/totr/TOTRsrv/certs'` then
   `scp certs/server.key certs/server.pem admin@192.0.2.10:/srv/totr/TOTRsrv/certs/` (and `chmod 600` the key there).
4. On the server add to `TOTRsrv/.env`: `TLS_CERT=certs/server.pem` and `TLS_KEY=certs/server.key`, then restart. The server now answers on `https://192.0.2.10:8443/` **and** still on `http://192.0.2.10:8080/`.
   Open port 8443 in the server's firewall (firewalld) for the home network only: `sudo firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="192.0.2.0/24" port port="8443" protocol="tcp" accept'` and the same command without `--permanent`. Check from another machine: `curl --cacert certs/totr-ca.pem https://192.0.2.10:8443/health`. Cookies get the `Secure` flag and HSTS over https by themselves.

**Moving the apps** (the installed apps can only update through the address they already know, so do it in this order):
1. Build the apps so they carry the authority (the builds copy `certs/totr-ca.pem` in by themselves; commit the public copies it writes): `npm run android:release`, `npm run linux:release`, `npm run win:release`.
2. Let each app update itself over http as usual, so every device has the version that trusts the authority. **Desktop apps need 0.5.2 or later** to update over https (0.5.0 and 0.5.1 load the page but their update check fails); run the new AppImage by hand once if needed.
3. On each device change the server address to `https://192.0.2.10:8443` (the You tab's Servers panel in app builds made after 2026-10-09; before that, Android: clear the app's data in Android Settings > Apps > TOTR > Storage and enter the address when asked; desktop: menu TOTR > Change server..., or put `{ "server": "https://192.0.2.10:8443" }` in `~/.config/totr-spa/config.json` on Linux). Sign in again (it is a new address, so the sign-in does not carry over; settings and place come back through the sync).
4. When every device has moved, set `HTTP_ENABLED=false` on the server and restart. The Android app refuses plain http from the first APK built after 2026-10-09 (`cleartextTrafficPermitted="false"` in `TOTRspa/shared/tools/sync-ca.cjs`): an install that still has an `http://` server saved shows NO SIGNAL until you press Change server and enter the https address.

**A normal browser** (the portal) warns until it trusts the authority: import `certs/totr-ca.pem` once (Firefox: Settings > Privacy & Security > View Certificates > Authorities > Import; Windows: double-click the file > Install Certificate > Local Machine > Trusted Root Certification Authorities; Chrome and Edge on Linux use the system or NSS store). The Windows/Linux/Android apps do not need this.

### The Windows warning (SmartScreen)
The Windows installer is unsigned. SmartScreen only checks files that arrive through a web browser (they carry a "downloaded from the internet" mark). It does **not** check a file copied over the network, from a USB stick, or downloaded by the app itself, so: install the first time from a copy you move yourself (scp, a shared folder, a USB stick), and updates (downloaded by the app) show no warning. If you ever do get the warning from a browser download: More info > Run anyway. Code signing (a paid certificate) is the only way to remove it for browser downloads.

### Without the private authority
You can also put a TLS reverse proxy in front instead (for example Caddy: `reverse_proxy 192.0.2.10:8080`) and set `COOKIE_SECURE=true` and `TRUSTED_PROXY=<the proxy's address>`.
