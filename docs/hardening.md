# Hardening a TOTR server

A checklist for running TOTR on a machine you care about. The server is built to be safe on its own ([security.md](security.md)); this
page is about the machine around it: who the process runs as, what it may touch, which ports are open and which database rights each part holds.

Paths below assume the installer's layout (`/opt/totr`). If you run from a git checkout, use the checkout folder instead.

## 1. Run it as a locked-down service
Run TOTR as its own system user under systemd, not by hand from a terminal. The sandbox lets a hijacked web process write only to
`reports/` and the music folder, gain no privileges and change nothing else on the machine.

- The installer (`deploy/install.sh`) sets this up with `deploy/totr.service.in`.
- For a plain checkout, use `deploy/totr-checkout.service.example`: copy it to `/etc/systemd/system/totr.service`, change the paths marked `CHANGE`
  (`WorkingDirectory`, `ExecStart`, and `ReadWritePaths` for your music folder), then:
  ```
  systemd-analyze verify /etc/systemd/system/totr.service
  sudo systemctl daemon-reload && sudo systemctl enable --now totr
  systemctl status totr --no-pager
  systemd-analyze security totr --no-pager | tail -1
  ```
  Success: `active (running)`, and a security score around 1 to 2.
- Make the service user without a login or home: `sudo useradd --system --no-create-home --shell /usr/bin/nologin totr`. Give it only what it needs
  (`.env`, `certs/`, `reports/`); see the table in section 2.
- If a path differs from the example and the service will not start, `journalctl -u totr` says why. Remove the `SystemCallFilter` lines first if you suspect them.

**What the unit allows**
- **Write:** `reports/` (state, logs, the upload spool), the music folder (uploads, the collector, `.trash`), and `data/` only on SQLite installs.
- **Read:** the code, `.env`, certificates and the PostgreSQL socket.
- **Cannot:** gain privileges, see `/home`, use devices, change kernel settings, the clock or the hostname, map writable-and-executable memory, open socket
  types other than TCP/UDP, Unix and netlink, or use mount, swap, reboot, raw-IO or module system calls.
- **Open files:** 65536. The default of 1024 is used up by about a thousand idle connections.
- `UMask=0007` keeps new music files usable by the group that shares them (for example a file share). Use `UMask=0027` if you do not want group write.

If uploads and the collector are off, remove the music folder from `ReadWritePaths` so the service cannot write there at all.

## 2. File permissions
| What | Should be | Check |
|---|---|---|
| `.env` | mode `600`, owned by the service user | `stat -c '%a %U %n' .env` |
| `certs/server.key` | mode `600`, owned by the service user; `certs/` mode `700` | `stat -c '%a %U %n' certs certs/server.key` |
| `certs/totr-ca.key` (the certificate authority's key) | kept on a separate machine you control, **not** on the server | `ls certs` shows no `totr-ca.key` |
| Code and `.venv` | readable, **not writable**, by the service user | `sudo -u totr test -w main.py && echo BAD` prints nothing |
| `reports/` | writable by the service user only | `stat -c '%a %U %n' reports` |
| Music folder | writable by the group only if uploads or the collector are used | `ls -ld` |

Installs made by `deploy/install.sh` start with these permissions. Edit `.env` with `sudoedit`, never by making it world-readable.

## 3. Database roles
Use one PostgreSQL role per purpose ([database.md](database.md)). None is a superuser, none can create roles, databases or tables.

| Role | Used for | Rights |
|---|---|---|
| `robots` (read) | every music read and scan | `SELECT` on the `totr.v_*` views only; no access to account tables |
| `totr_uploader` | the Upload page | `INSERT` into `media.items` and `media.item_relations`; `SELECT` on types and views |
| `totr_maint` | approved spider fixes, collector adds | `SELECT/INSERT/UPDATE/DELETE` on `media.items` and `media.item_relations`; no DDL |
| `totr_auth` | accounts, saved data, join requests, song offers | `SELECT/INSERT/UPDATE/DELETE` on the `totr.*` account tables |
| `totr_mixtapes` | mixtapes | `SELECT/INSERT/UPDATE/DELETE` on `totr.mixtapes` and `mixtape_tracks` |
| administrator | installer, migrations, the "Move to PostgreSQL" helper | typed once, never stored |

At run time the server never uses the administrator, never runs DDL (`tests/test_guards.py`) and never imports the migration code.
`totr_maint` reaches every item in the shared `media` tables, not only the ones TOTR created. If other applications share the database, keep that in mind.

**Passwords.** Give each role its own long random password (for example `openssl rand -base64 24`), set it with `ALTER ROLE ... PASSWORD`
fed from standard input rather than the command line, and put the same value in `.env` (mode `600`). Restart the service after every change and test sign-in,
playback and the feature that role serves. Never keep real passwords in tracked SQL files; the files in `sql/` hold the placeholder `CHANGE_ME`.
Rotate after any paste into chat or a ticket.

## 4. Network
- Serve over HTTPS and turn plain HTTP off (`HTTP_ENABLED=false`). Only TLS 1.2 and 1.3 are accepted.
- Open only the ports you use (HTTPS for TOTR, SSH for you, PostgreSQL only if another machine needs it), and only to your own network, with the
  firewall's source restrictions. Check with `firewall-cmd --list-all` and `--list-rich-rules`, or your firewall's equivalent.
- Keep PostgreSQL on the local socket when TOTR runs on the same machine; if it must listen on the network, require passwords and prefer `hostssl`.
- Use key-only SSH (`PasswordAuthentication no`).
- "Servers near you" uses mDNS (UDP 5353). A firewall that drops it means apps will not see the server in the list, even though it works when typed in.

## 5. The update service
Signed updates are applied by a separate root service (`deploy/totr-update.service.in`, `deploy/apply-update.py`). It is sandboxed too
(`systemd-analyze security --offline=true totr-update.service`). The server only installs releases signed with the Ed25519 key whose public half is in
`update_pubkey.txt`. Keep the matching private key and its passphrase off the server ([updates.md](updates.md)).

## 6. Keep an eye on it
- `journalctl -u totr` for errors; the Status tab for CPU and memory.
- Back up the database and `.env` ([operations.md](operations.md)). Test a restore.
- Run `pip-audit -r requirements.txt` now and then, and update Python, OpenSSL, SQLite and ffmpeg with your system.
- Re-run `tests/run_all.py` after changing any setting that touches security.

## Limits
This page describes settings that were tried on a test machine and a real server; it is not a guarantee. The unit has not been checked against
every distribution. Report anything that does not work as written: see [SECURITY.md](../SECURITY.md).
