"""
Account management from the server's command line. This is how the first admin is made, and how a
locked-out admin gets back in. Passwords are typed at a hidden prompt, never on the command line
(where they would land in shell history and process lists).

    python -m auth.cli create-admin NAME
    python -m auth.cli create-user NAME
    python -m auth.cli reset-password NAME
    python -m auth.cli list
    python -m auth.cli invite-admin      prints a one-time sign-in link (path) for a new admin: no password is typed anywhere, the person sets their own
"""

import argparse
import getpass
import sys

import db

from . import service


def _ask_password() -> str:
    first = getpass.getpass("New password (12+ characters): ")
    if first != getpass.getpass("Again: "):
        sys.exit("The two passwords did not match. Nothing was changed.")
    return first


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "command", choices=["create-admin", "create-user", "reset-password", "list", "invite-admin"]
    )
    ap.add_argument("username", nargs="?")
    args = ap.parse_args()

    if not db.auth_enabled():
        sys.exit("Set DB_AUTH_USER and DB_AUTH_PASSWORD in .env first (see sql/auth_schema.sql).")
    if args.command not in ("list", "invite-admin") and not args.username:
        ap.error(f"{args.command} needs a username")

    try:
        if args.command == "list":
            with service.connect() as conn:
                rows = conn.execute(
                    "SELECT username, role, disabled, last_login_at FROM totr.users ORDER BY lower(username)"
                ).fetchall()
            for r in rows:
                print(
                    f"{r['username']:<34}{r['role']:<8}{'DISABLED' if r['disabled'] else 'active':<10}"
                    f"last login: {r['last_login_at'] or 'never'}"
                )
            print(f"{len(rows)} account(s)")
        elif args.command == "invite-admin":
            token = service.create_invite({"role": "admin", "user_id": None}, "admin")
            print(f"/invite/{token}")
        elif args.command in ("create-admin", "create-user"):
            role = "admin" if args.command == "create-admin" else "member"
            service.create_user(args.username, _ask_password(), role)
            print(f"Created {role} '{args.username}'.")
        else:
            service.reset_password(args.username, _ask_password())
            print(f"Password changed for '{args.username}'. They were signed out everywhere.")
    except service.AuthError as e:
        sys.exit(e.message)


if __name__ == "__main__":
    main()
