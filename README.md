# TOTR server (TOTRsrv)

TOTR ("TV on the Radio") is a self-hosted music server for one person's home or small group: browse by artist, album and loose songs, a nonstop radio, mixtapes,
invite-only accounts, and tools that keep the library tidy (spiders that find problems, a collector that finds legally free music). Nothing changes without an
admin's approval. This folder is the server: Python, FastAPI, and either the built-in SQLite database or PostgreSQL. The listener app is the separate TOTRspa project.

## Install it as a service (Linux)

```sh
sudo ./deploy/install.sh              # asks a few plain questions
sudo ./deploy/install.sh --yes        # no questions: the built-in SQLite database and the defaults (--help lists every option)
```
It makes a `totr` user, a systemd service that starts at boot and restarts after a crash, and HTTPS from its own certificate authority. At the end it prints the
address and a one-time link to make the first admin account. Run it again any time to repair. Python 3.11 or newer and OpenSSL are needed.

## Run it from a checkout (development)

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt        # Windows: .venv\Scripts\pip
.venv/bin/python main.py                                                  # prints the address (port 8080)
.venv/bin/python -m auth.cli create-admin NAME                            # the first admin account
```
With no settings it uses the built-in SQLite database in `data/`. Settings go in a `.env` file in this folder.

## Settings (`.env`)

| Setting | What it does |
|---|---|
| `DB_ENGINE`, `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL instead of SQLite (the admin Database page can move an existing install over) |
| `MUSIC_ROOT`, `MEDIA_ROOT` | where the music library and the radio's pictures and videos live (defaults: `music/` and `media/` beside the data) |
| `TLS_CERT`, `TLS_KEY`, `TLS_PORT`, `HTTP_ENABLED` | HTTPS (`tools/make_certs.py` makes a private certificate authority and server certificates) and closing plain HTTP |
| `COOKIE_SECURE`, `TRUSTED_PROXY` | when TOTR sits behind a reverse proxy |
| `MAX_UPLOAD_MB` | the largest upload |
| `UPDATE_REPO` | the GitHub repository the admin Updates page watches for signed releases |

## Test it

```sh
.venv/bin/python tests/run_all.py            # offline: no network, no writes to a real database
```
Every suite prints one line. `test_library` and `test_live_library` need a real music library and fail on any other machine; that is expected.
`deploy/lab-test.sh` is a full install, update, rollback and crash test; run it only on a disposable machine, because it removes `/opt/totr` first.

## Security

- Every page and API call needs a signed-in session; accounts are invite-only (`member` and `admin`, with per-person permissions). Passwords are Argon2id hashes.
- Strong defaults: CSRF tokens, a per-request content security policy, bound SQL parameters only, outbound fetches limited to public hosts, uploads checked, files moved to a trash folder instead of deleted.
- Server updates install only if signed with the developer's Ed25519 key (the public half is in `update_pubkey.txt`; the private half stays with the developer); a failed update rolls back by itself.
- The server's network identity (its certificate authority and a short fingerprint) is shown on the admin Updates page so apps can check it the first time they connect.
- Never put `.env`, `certs/*.key` or `data/` in a public place.
- Running it safely: [docs/hardening.md](docs/hardening.md). To report a security problem privately, see [SECURITY.md](SECURITY.md).

## Licence

This is **source-available for non-commercial use**, under the PolyForm Noncommercial License 1.0.0 (the `LICENSE` file; https://polyformproject.org/licenses/noncommercial/1.0.0). You may use, change and share it for free for any non-commercial purpose; you may not sell it or use it commercially. It is not an OSI "open source" licence, because it forbids commercial use. Code from other projects that ships inside (for example in `third_party/` or `static/fonts/licenses`) keeps its own licence.
