"""Sets up a PostgreSQL database for TOTR. Run by tools/install_wizard.py with the server's own
Python (it needs psycopg); not for people to run by hand.

Reads one JSON object on standard input (so no password ever appears in a command line or a process
list):
    {"admin": {"host", "port", "dbname", "user", "password", "sslmode"},
     "roles": {"<role name>": "<password>", ...},                    the login roles to make (each only if it does not exist yet)
     "purposes": {"read": "<role name>", "auth": "<role name>", ...}} which role is used for what (the same name for all five = one role)
What it does, in order: connect with the admin login (making the database if it does not exist),
make the roles, build or upgrade the layout (migrate.upgrade, one transaction per upgrade), give
each role its access (migrate.apply_grants). The admin login is used for this run only. Prints one
line of JSON: {"ok": true, ...} or {"ok": false, "error": "..."}; the exit code says the same.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import psycopg
from psycopg import sql

import migrate


def connect(a, dbname):
    return psycopg.connect(
        host=a["host"],
        port=int(a["port"]),
        dbname=dbname,
        user=a["user"],
        password=a["password"],
        sslmode=a.get("sslmode") or "prefer",
        autocommit=True,
        connect_timeout=10,
    )


def main():
    req = json.load(sys.stdin)
    a = req["admin"]
    made_db = False
    try:
        try:
            conn = connect(a, a["dbname"])
        except psycopg.OperationalError as e:
            if "does not exist" not in str(e):
                raise
            boot = connect(a, "postgres")
            boot.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(a["dbname"])))
            boot.close()
            made_db, conn = True, connect(a, a["dbname"])
        made = [
            name
            for name, password in req["roles"].items()
            if migrate.create_role(conn, name, password)
        ]
        state = migrate.state(conn)
        if state["state"] == "unversioned":
            raise migrate.MigrateError(
                "This database already has TOTR's tables from an older, hand-made setup. Run `python migrate.py adopt` on it first "
                "(it changes nothing), then run the installer again."
            )
        applied = migrate.upgrade(conn)
        migrate.apply_grants(conn, req["purposes"])
        conn.close()
    except (migrate.MigrateError, psycopg.Error) as e:
        print(json.dumps({"ok": False, "error": str(e).strip()[:600]}))
        return 1
    print(
        json.dumps(
            {"ok": True, "created_database": made_db, "created_roles": made, "applied": applied}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
