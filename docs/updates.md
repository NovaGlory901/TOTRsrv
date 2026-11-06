# Server updates (signed)

A server can update itself from the developer's releases. Nothing is installed unless it is **signed with the developer's key**; the web app only downloads and
checks, and a separate root service (not the web app) does the installing, so a hacked web app still cannot replace its own code.

## The admin page

Admin role only: **Admin > updates** (`/admin/updates`). It shows the installed version, where updates come from, the developer's key (with a short fingerprint to compare by eye), when it last checked, then a check/install button, the install progress (it polls and rides out the restart), and what to do when an update needs a database administrator or was rolled back. It never checks by itself.

## How it works
1. The developer builds a release (`tools/release_tool.py build`): a bundle `TOTRsrv-<version>.tar.gz`, a manifest `release.json` (the version, the bundle's SHA-256 and
   size, notes) and `release.json.sig` (the Ed25519 signature of the manifest). They are attached to a GitHub release of the repository named in `UPDATE_REPO`.
2. An admin asks for a check (Admin API `POST /api/admin/updates/check`, or `python -m updates check`). The server reads the newest release from GitHub
   (only `github.com` and `githubusercontent.com`, through `collector/http.py`) and verifies the signature with `update_pubkey.txt`. An unsigned or wrongly signed release
   is never reported as an update.
3. `POST /api/admin/updates/install` (or `python -m updates install`) downloads the bundle, checks the signature, the size and the checksum again, puts it in
   `<state>/updates/pending/` and writes `<state>/updates/request.json`.
4. systemd starts `totr-update.service` (root) because `totr-update.path` sees the request. `deploy/apply-update.py` **checks everything again by itself**
   (it does not trust the web app), unpacks the bundle into `releases/<version>/` (plain files only, nothing outside that folder), builds its Python environment, and
   looks at the database:
   - SQLite: backed up, then brought forward by the new release's migrations.
   - PostgreSQL that needs new migrations: **not touched** (the admin login is never stored). The release is staged and the service stops with `needs_db_admin`; an
     administrator finishes it with `sudo <prefix>/current/deploy/apply-update.py --prefix <prefix> --continue`, which asks for a database login that may create tables
     (used for that run only).
5. It points `current` at the new release, restarts the service and waits for `/health`. If the new version does not answer, it switches back, restores the SQLite
   backup if migrations had run, and restarts the old version. `<state>/updates/status.json` records each step (`working`, `done`, `rolled_back`, `failed`,
   `needs_db_admin`); the newest 3 releases are kept.

## What it protects against
- A fake or tampered update (signature + checksum, checked twice). A release signed by anyone else.
- An old genuine release replayed to undo a fix (installing only goes forward). A release that skips a required step (`min_from`).
- A malicious archive (absolute paths, `..`, links, devices, huge files) and a bundle whose code disagrees with its signed version.
- A bad update taking the server down (automatic rollback, with the database restored).

## What it does not protect against (read this)
- **The signing key.** Whoever holds the private key can make every server install anything. Keep it on one machine, with a passphrase, backed up, never in git or
  on a server. If it is lost, servers cannot trust new releases until each is given a new public key by hand; if it leaks, the same.
- **The first key.** A server trusts whatever is in its `update_pubkey.txt`. Install it from a source you trust (the installer ships the one in the release).
- GitHub being unreachable (no update, nothing breaks) or a repository taken over: a taken-over repository still cannot make a valid signature.
- The updater cannot back up PostgreSQL (no stored admin login): take your own backup before an update that changes the database.

## Setting it up
- Developer, once: `python tools/release_tool.py keygen --out ~/.totr-signing`, put the printed line in `TOTRsrv/update_pubkey.txt`, commit that file (it is public).
- Developer, each release: raise `SERVER_VERSION` in `version.py`, `python tools/release_tool.py build --key ~/.totr-signing/signing.key --out dist-release --notes "..."` (add `--app <folder>` with a web build from TOTRspa's `npm run publish:web` to carry the listener app as `static/app`, instead of the copy kept in this folder),
  `python tools/release_tool.py verify --dir dist-release`, attach the three files to a GitHub release (not a draft or pre-release).
- Admin: set `UPDATE_REPO=owner/name` in `.env` (or install with `--update-repo owner/name`) and restart. A server with no repository or no key says so instead of updating.
- The install is needed on the server for this to work: `deploy/install.sh` sets up `totr-update.path` and `totr-update.service`. A server installed before that needs the installer run again.

## Files
`updates/` (signing, fetch, API, CLI), `deploy/apply-update.py`, `deploy/db-state.py`, `deploy/totr-update.service.in`, `deploy/totr-update.path.in`, `tools/release_tool.py`,
`update_pubkey.txt`. Tests: `tests/test_updates.py`, `tests/test_apply_update.py`; the real-systemd test is `deploy/lab-test.sh sqlite update`.
