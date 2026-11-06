#!/usr/bin/env python3
"""Moves TOTR from its built-in database (SQLite) to a PostgreSQL server, in one go. The admin
Database page starts this; you can also run it yourself.

    echo '{"mode": "test", "host": "db.example.org", ...}' | python tools/move_to_postgres.py

It reads ONE json object from standard input (never from the command line or the environment, so the
database administrator's password is not in `ps`, in a file or in a log) and prints one json object
per line:  {"event": "step", "name": ..., "state": "doing"|"done"}, {"event": "progress", "percent":
n}, and last {"event": "result", "ok": true|false, ...}. Nothing it prints holds a password.

    mode "test"      connects and reports what it found (nothing is changed)
    mode "rehearse"  makes a scratch database, builds the layout, copies everything in, counts both sides, then drops the scratch database (nothing is kept)
    mode "move"      makes the database and one login role per purpose (or one for everything), builds the layout, copies everything in, counts both sides,
                     checks that each new role can really do its job, and only then writes the new settings to .env and sets the SQLite file aside as
                     totr.db.before-postgres. If anything is off, what it made is removed again and .env and the SQLite file stay exactly as they were.

The administrator's login is used for this run only. The new roles get random passwords that go into
.env (mode 600) and nowhere else.
"""

import json
import os
import pathlib
import re
import secrets
import sys

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
sys.path.insert(0, str(PF / "tools"))

MODES = ("test", "rehearse", "move")
SSL_MODES = ("", "disable", "allow", "prefer", "require", "verify-ca", "verify-full")
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
HOST = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
MIN_POSTGRES = 14
# The settings this tool owns in .env: whatever was there is replaced (the old lines are kept in
# .env.before-postgres).
DB_KEYS = (
    "DB_ENGINE",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_SSLMODE",
    "DB_SINGLE_ROLE",
    "DB_USER",
    "DB_PASSWORD",
    "DB_WRITE_USER",
    "DB_WRITE_PASSWORD",
    "DB_MAINT_USER",
    "DB_MAINT_PASSWORD",
    "DB_AUTH_USER",
    "DB_AUTH_PASSWORD",
    "DB_MIXTAPE_USER",
    "DB_MIXTAPE_PASSWORD",
)
ENV_FOR_PURPOSE = {
    "read": ("DB_USER", "DB_PASSWORD"),
    "write": ("DB_WRITE_USER", "DB_WRITE_PASSWORD"),
    "maint": ("DB_MAINT_USER", "DB_MAINT_PASSWORD"),
    "auth": ("DB_AUTH_USER", "DB_AUTH_PASSWORD"),
    "mixtape": ("DB_MIXTAPE_USER", "DB_MIXTAPE_PASSWORD"),
}
# What to show people, as (label, how to count it from media.items by type slug / table).
TYPE_LABELS = (("artists", "artist"), ("albums", "album"), ("songs", "song"))


class MoveError(Exception):
    """A refusal that is safe to show to the admin (it never holds a password)."""


def validate(raw) -> dict:
    """Checks and cleans the request. Anything unexpected is refused before any connection is made."""
    if not isinstance(raw, dict):
        raise MoveError("The request is not valid.")
    mode = raw.get("mode")
    if mode not in MODES:
        raise MoveError("Unknown mode.")
    host, dbname = str(raw.get("host", "")).strip(), str(raw.get("dbname", "")).strip()
    if not HOST.match(host):
        raise MoveError("The server address may hold only letters, digits and . _ : / -")
    if not IDENT.match(dbname):
        raise MoveError(
            "The database name must start with a letter and hold only letters, digits and underscores (up to 41 characters)."
        )
    port = str(raw.get("port", "") or "").strip()
    if port and (not port.isdigit() or not 0 < int(port) < 65536):
        raise MoveError("The port must be a number.")
    sslmode = str(raw.get("sslmode", "") or "").strip()
    if sslmode not in SSL_MODES:
        raise MoveError("Unknown encryption setting.")
    roles = raw.get("roles", "several")
    if roles not in ("several", "single"):
        raise MoveError("Unknown role setup.")
    admin_user, admin_password = raw.get("admin_user"), raw.get("admin_password")
    if (
        not isinstance(admin_user, str)
        or not 0 < len(admin_user) <= 63
        or any(ord(c) < 32 for c in admin_user)
    ):
        raise MoveError("Give the administrator account name.")
    if (
        not isinstance(admin_password, str)
        or not 0 < len(admin_password) <= 500
        or "\x00" in admin_password
    ):
        raise MoveError("Give the administrator account's password.")
    return {
        "mode": mode,
        "host": host,
        "port": port,
        "dbname": dbname,
        "sslmode": sslmode,
        "roles": roles,
        "admin_user": admin_user,
        "admin_password": admin_password,
    }


def role_names(p: dict) -> dict:
    """purpose -> role name. Role names live for the whole server, so they start with the database's
    name."""
    import migrate

    if p["roles"] == "single":
        return {purpose: f"{p['dbname']}_app" for purpose in migrate.PURPOSES}
    return {purpose: f"{p['dbname']}_{purpose}" for purpose in migrate.PURPOSES}


def connect(p: dict, dbname: str, user: str, password: str):
    import psycopg
    from psycopg.rows import dict_row

    kw = {
        "host": p["host"],
        "dbname": dbname,
        "user": user,
        "password": password,
        "connect_timeout": 10,
        "autocommit": True,
        "row_factory": dict_row,
        "sslmode": p["sslmode"] or ("disable" if p["host"].startswith("/") else "require"),
    }
    if p["port"]:
        kw["port"] = int(p["port"])
    return psycopg.connect(**kw)


def admin_connect(p: dict, dbname: str):
    """The administrator's login. Tries the 'postgres' database first (always there), then 'template1'."""
    if dbname != "postgres":
        return connect(p, dbname, p["admin_user"], p["admin_password"])
    try:
        return connect(p, "postgres", p["admin_user"], p["admin_password"])
    except Exception as first:
        try:
            return connect(p, "template1", p["admin_user"], p["admin_password"])
        except Exception:
            raise first


def plain_error(e: Exception, secrets_to_hide=()) -> str:
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    for s in secrets_to_hide:
        if s:
            text = text.replace(s, "***")
    return text[:300]


def database_state(conn, dbname) -> str:
    """'absent', 'empty' (exists, no TOTR or media schema: fine to use) or 'used' (has one: never
    touched)."""
    if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
        return "absent"
    return "empty"


def schemas_in_use(p: dict) -> bool:
    c = admin_connect(p, p["dbname"])
    try:
        return bool(
            c.execute("SELECT 1 FROM pg_namespace WHERE nspname IN ('totr', 'media')").fetchone()
        )
    finally:
        c.close()


def probe(p: dict, names: dict) -> list:
    """The `test` mode: what the administrator's login can do. Returns [{what, ok, warn?}] and
    changes nothing."""
    checks = []
    try:
        conn = admin_connect(p, "postgres")
    except Exception as e:
        return [
            {
                "what": "connect to " + (p["host"] + (":" + p["port"] if p["port"] else "")),
                "ok": False,
                "why": plain_error(e, (p["admin_password"],)),
            }
        ]
    try:
        version = conn.info.server_version
        shown = f"{version // 10000}.{version % 10000}"
        checks.append(
            {
                "what": f"connected to {p['host']}{':' + p['port'] if p['port'] else ''} (PostgreSQL {shown})",
                "ok": version // 10000 >= MIN_POSTGRES,
            }
        )
        if version // 10000 < MIN_POSTGRES:
            checks[-1]["why"] = f"TOTR is tested with PostgreSQL {MIN_POSTGRES} or newer"
        me = (
            conn.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
            or {}
        )
        sup = bool(me.get("rolsuper"))
        checks.append(
            {"what": "this account may create databases", "ok": sup or bool(me.get("rolcreatedb"))}
        )
        if p["mode"] != "rehearse":
            checks.append(
                {
                    "what": "this account may create roles",
                    "ok": sup or bool(me.get("rolcreaterole")),
                }
            )
        encoding = (
            conn.execute(
                "SELECT pg_encoding_to_char(encoding) AS e FROM pg_database WHERE datname = 'template1'"
            ).fetchone()
            or {}
        ).get("e")
        if encoding != "UTF8":
            checks.append(
                {
                    "what": "new databases on this server use the " + str(encoding) + " encoding",
                    "ok": False,
                    "why": "TOTR needs UTF8; make the database yourself with ENCODING 'UTF8' and give its name here",
                }
            )
        state = database_state(conn, p["dbname"])
        if state == "absent":
            checks.append(
                {
                    "what": f"the database {p['dbname']} does not exist yet; it will be made",
                    "ok": True,
                }
            )
        else:
            try:
                used = schemas_in_use(p)
            except Exception as e:
                checks.append(
                    {
                        "what": f"the database {p['dbname']} exists but this account cannot open it",
                        "ok": False,
                        "why": plain_error(e, (p["admin_password"],)),
                    }
                )
            else:
                if used:
                    checks.append(
                        {
                            "what": f"the database {p['dbname']} already holds TOTR or media tables",
                            "ok": False,
                            "why": "choose a new name: this move never writes into a database that is in use",
                        }
                    )
                else:
                    checks.append(
                        {
                            "what": f"the database {p['dbname']} exists and holds no TOTR tables; it will be used",
                            "ok": True,
                        }
                    )
        taken = [
            r
            for r in sorted(set(names.values()))
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r,)).fetchone()
        ]
        if taken:
            checks.append(
                {
                    "what": "the login roles "
                    + ", ".join(taken)
                    + (" exist" if len(taken) > 1 else " exists")
                    + " already",
                    "ok": False,
                    "why": "choose another database name (role names start with it)",
                }
            )
        if (
            conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_bigm'").fetchone() is None
            and not conn.execute(
                "SELECT 1 FROM pg_available_extensions WHERE name = 'pg_bigm'"
            ).fetchone()
        ):
            checks.append(
                {
                    "what": "the optional pg_bigm extension is not installed: two title-search indexes will be left out (TOTR does not need them)",
                    "ok": True,
                    "warn": True,
                }
            )
        if not p["host"].startswith("/") and p["sslmode"] in ("disable", "allow", "prefer"):
            checks.append(
                {
                    "what": "the connection to the server is not required to be encrypted",
                    "ok": True,
                    "warn": True,
                }
            )
    finally:
        conn.close()
    return checks


def count_pg(conn) -> dict:
    out = {}
    import migrate_db

    for name, table in migrate_db.TABLES.items():
        out[name] = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
    for row in conn.execute(
        "SELECT t.item_type_slug AS slug, count(*) AS n FROM media.items i JOIN media.item_types t ON t.type_id = i.type_id GROUP BY 1"
    ).fetchall():
        out["type:" + row["slug"]] = row["n"]
    return out


def count_local(conn) -> dict:
    out = {}
    import migrate_db

    for name, table in migrate_db.TABLES.items():
        try:
            out[name] = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        except Exception:
            if name not in migrate_db.OPTIONAL:
                raise
            out[name] = 0
    for row in conn.execute(
        "SELECT t.item_type_slug AS slug, count(*) AS n FROM media.items i JOIN media.item_types t ON t.type_id = i.type_id GROUP BY 1"
    ).fetchall():
        out["type:" + row["slug"]] = row["n"]
    return out


def summary(counts: dict) -> dict:
    """The numbers people care about, by the names the page shows."""
    out = {label: counts.get("type:" + slug, 0) for label, slug in TYPE_LABELS}
    out["accounts"], out["playlists"] = counts.get("users", 0), counts.get("mixtapes", 0)
    return out


PLAIN = {
    "items": "music items",
    "relations": "links between music items",
    "users": "accounts",
    "mixtapes": "playlists",
    "mixtape_tracks": "playlist entries",
    "user_data": "saved settings",
    "type:song": "songs",
    "type:artist": "artists",
    "type:album": "albums",
}


def mismatches(before: dict, after: dict) -> list:
    keys = sorted(set(before) | set(after))
    return [
        f"{PLAIN.get(k, k.replace('type:', '') + 's')}: {before.get(k, 0)} in SQLite, {after.get(k, 0)} in PostgreSQL"
        for k in keys
        if before.get(k, 0) != after.get(k, 0)
    ]


def rewrite_env(text: str, settings: dict) -> str:
    """Drops every line that sets one of DB_KEYS and appends the new settings. Comments and all
    other settings stay as they were."""
    keep = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if key in DB_KEYS and "=" in line and not line.lstrip().startswith("#"):
            continue
        keep.append(line)
    while keep and not keep[-1].strip():
        keep.pop()
    block = [
        "",
        "# Set by the Move to PostgreSQL step on the admin Database page (the lines it replaced are in .env.before-postgres).",
    ]
    block += [f"{k}={v}" for k, v in settings.items()]
    return "\n".join(keep + block) + "\n"


def env_settings(p: dict, names: dict, passwords: dict) -> dict:
    s = {"DB_ENGINE": "postgres", "DB_HOST": p["host"]}
    if p["port"]:
        s["DB_PORT"] = p["port"]
    s["DB_NAME"] = p["dbname"]
    if p["sslmode"]:
        s["DB_SSLMODE"] = p["sslmode"]
    if p["roles"] == "single":
        s["DB_SINGLE_ROLE"] = "true"
        s["DB_USER"], s["DB_PASSWORD"] = names["read"], passwords[names["read"]]
        return s
    for purpose, (user_key, password_key) in ENV_FOR_PURPOSE.items():
        s[user_key], s[password_key] = names[purpose], passwords[names[purpose]]
    return s


def write_env(path: pathlib.Path, settings: dict):
    """Writes the new .env next to the old one and swaps it in; the old one is kept as
    .env.before-postgres. Both are readable by the owner only."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    backup = path.with_name(path.name + ".before-postgres")
    had_file = path.exists()
    if had_file:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(old)
    part = path.with_name(path.name + ".part")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(rewrite_env(old, settings))
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)
    return backup if had_file else None


def restore_env(path: pathlib.Path, backup):
    """Puts .env back as it was before write_env (used when a later step failed)."""
    if backup is not None and pathlib.Path(backup).exists():
        os.replace(backup, path)
    else:
        path.unlink(missing_ok=True)


def set_sqlite_aside(path: pathlib.Path) -> pathlib.Path:
    """totr.db -> totr.db.before-postgres (a number is added if that name is taken). The old file is
    kept, never deleted."""
    target = path.with_name(path.name + ".before-postgres")
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.name}.before-postgres-{n}")
    os.replace(path, target)
    for extra in ("-wal", "-shm"):
        side = path.with_name(path.name + extra)
        if side.exists():
            os.replace(side, target.with_name(target.name + extra))
    return target


class Out:
    """One json object per line on stdout; every string passes through a filter that hides the secrets."""

    def __init__(self, hide=()):
        self.hide = [h for h in hide if h]

    def add_secret(self, s):
        if s:
            self.hide.append(s)

    def send(self, obj):
        text = json.dumps(obj, ensure_ascii=False)
        for h in self.hide:
            text = text.replace(json.dumps(h)[1:-1], "***")
        print(text, flush=True)

    def step(self, name, state):
        self.send({"event": "step", "name": name, "state": state})

    def progress(self, percent):
        self.send({"event": "progress", "percent": int(percent)})


def _drop_database(admin, name):
    from psycopg import sql

    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def _drop_role(admin, name):
    from psycopg import sql

    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


def run(p: dict, out: Out) -> dict:
    """Does a rehearse or a move. Raises MoveError (with a reason fit to show) if anything is off;
    whatever was made by then is removed first."""
    import migrate_db
    from psycopg import sql

    import localdb
    import migrate

    rehearse = p["mode"] == "rehearse"
    target = p["dbname"] + "_rehearsal" if rehearse else p["dbname"]
    names = {} if rehearse else role_names(p)
    passwords = {n: secrets.token_urlsafe(24) for n in set(names.values())}
    for pw in passwords.values():
        out.add_secret(pw)
    db_path = localdb.db_path()
    if not db_path.exists():
        raise MoveError("There is no built-in database file to move (TOTR is not using one).")

    admin = admin_connect(p, "postgres")
    made_db, made_roles, work = False, [], None
    try:
        # 1. check again, right before changing anything
        for c in probe(p, names if not rehearse else {}):
            if not c["ok"]:
                raise MoveError(c["what"] + (": " + c["why"] if c.get("why") else ""))
        if (
            rehearse
            and admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,)).fetchone()
        ):
            raise MoveError(
                f"A database called {target} already exists; remove it or pick another name for the rehearsal."
            )

        # 2. database and roles
        out.step("database and roles made", "doing")
        if (
            admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,)).fetchone()
            is None
        ):
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
            made_db = True
        work = admin_connect(p, target)
        for role in sorted(set(names.values())):
            if migrate.create_role(admin, role, passwords[role]):
                made_roles.append(role)
        out.step("database and roles made", "done")

        # 3. the layout (the same media and totr layout as every TOTR)
        out.step("layout built", "doing")
        migrate.upgrade(work)
        if names:
            migrate.apply_grants(work, names)
        out.step("layout built", "done")

        # 4. copy
        out.step("copying", "doing")
        local = localdb.connect()
        try:
            before = count_local(local)
            data = migrate_db.export_data(local)
        finally:
            local.close()
        with work.transaction():  # all or nothing
            migrate_db.import_data(
                work, data, progress=lambda done, total: out.progress(100 * done / max(total, 1))
            )
            out.step("copying", "done")
            out.step("counting both sides", "doing")
            after = count_pg(work)
            bad = mismatches(before, after)
            if bad:
                raise MoveError("The counts did not match: " + "; ".join(bad[:3]))
            if rehearse:
                out.step("counting both sides", "done")
                raise _Rollback()
        out.step("counting both sides", "done")

        # 5. does each new role really work?
        out.step("checking the new accounts", "doing")
        for purpose, role in names.items():
            c = connect(p, target, role, passwords[role])
            try:
                for probe_sql in {
                    "read": ["SELECT count(*) FROM totr.v_tracks"],
                    "auth": [
                        "SELECT count(*) FROM totr.users",
                        "SELECT count(*) FROM totr.user_data",
                    ],
                    "mixtape": ["SELECT count(*) FROM totr.mixtapes"],
                    "write": ["SELECT count(*) FROM totr.v_tracks"],
                    "maint": ["SELECT count(*) FROM totr.v_tracks"],
                }[purpose]:
                    c.execute(probe_sql)
            except Exception as e:
                raise MoveError(f"The new {purpose} account cannot do its job ({plain_error(e)}).")
            finally:
                c.close()
        out.step("checking the new accounts", "done")

        # 6. nothing changed on the SQLite side meanwhile?
        local = localdb.connect()
        try:
            now = count_local(local)
        finally:
            local.close()
        if now != before:
            raise MoveError(
                "The built-in database changed while it was being copied ("
                + "; ".join(mismatches(before, now)[:2] or ["something was written"])
                + "). Nothing was switched; try again."
            )

        # 7. switch
        out.step("switching TOTR over", "doing")
        import sqlite3

        import paths

        env_path = paths.env_file()
        backup_env = write_env(env_path, env_settings(p, names, passwords))
        try:
            flush = sqlite3.connect(db_path)
            try:
                # everything in the main file before it is set aside
                flush.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                flush.close()
            kept = set_sqlite_aside(db_path)
        except BaseException:
            # the settings must never point at a database that is about to be removed again
            restore_env(env_path, backup_env)
            raise
        out.step("switching TOTR over", "done")
        return {
            "ok": True,
            "before": summary(before),
            "after": summary(after),
            "kept_file": str(kept),
            "env_backup": str(backup_env) if backup_env else "",
            "roles": sorted(set(names.values())),
        }
    except _Rollback:
        return {"ok": True, "rehearsal": True, "before": summary(before), "after": summary(after)}
    except BaseException:
        for conn_to_close in (work,):
            if conn_to_close is not None:
                conn_to_close.close()
        work = None
        try:  # remove what this run made (never anything that was there before)
            if made_db:
                _drop_database(admin, target)
            for role in made_roles:
                _drop_role(admin, role)
        except Exception:
            pass
        raise
    finally:
        if work is not None:
            work.close()
        if rehearse and made_db:
            try:
                _drop_database(admin, target)
            except Exception:
                pass
        admin.close()


class _Rollback(Exception):
    """Ends a rehearsal: the copy is undone, and the scratch database is dropped."""


def main(stdin=None, stdout_out=None) -> int:
    import db  # noqa: F401  (loads .env, so the helper sees the same settings the server does)

    out = stdout_out or Out()
    try:
        p = validate(json.loads((stdin or sys.stdin).read()))
    except (ValueError, MoveError) as e:
        out.send(
            {
                "event": "result",
                "ok": False,
                "message": str(e) if isinstance(e, MoveError) else "The request is not valid.",
            }
        )
        return 1
    out.add_secret(p["admin_password"])
    try:
        if p["mode"] == "test":
            checks = probe(p, role_names(p))
            out.send({"event": "result", "ok": all(c["ok"] for c in checks), "checks": checks})
            return 0
        result = run(p, out)
        out.send({"event": "result", **result})
        return 0
    except MoveError as e:
        out.send({"event": "result", "ok": False, "message": str(e)})
        return 1
    except Exception as e:
        out.send(
            {
                "event": "result",
                "ok": False,
                "message": "It did not finish: "
                + plain_error(e, (p["admin_password"],))
                + ". Nothing was switched.",
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
