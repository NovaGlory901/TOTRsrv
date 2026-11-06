"""Every mixtape query. Rules this file keeps (same as auth/store.py):

* Every value is a bound parameter (%s); nothing is formatted into SQL.
* Anything that reaches a mixtape does so through `get_mixtape(conn, user_id, mixtape_id)` first,
  which only
  finds the signed-in user's own mixtapes. Another person's mixtape looks exactly like one that does not exist.
* Times come from Python, so the same SQL runs on SQLite in the tests.
"""

import sqlite3

import psycopg

INTEGRITY_ERRORS = (psycopg.errors.UniqueViolation, sqlite3.IntegrityError)


def list_mixtapes(conn, user_id: int):
    return conn.execute(
        "SELECT m.mixtape_id, m.name, m.updated_at, "
        "(SELECT count(*) FROM totr.mixtape_tracks t WHERE t.mixtape_id = m.mixtape_id) AS track_count "
        "FROM totr.mixtapes m WHERE m.user_id = %s ORDER BY lower(m.name)",
        (user_id,),
    ).fetchall()


def count_mixtapes(conn, user_id: int) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM totr.mixtapes WHERE user_id = %s", (user_id,)
    ).fetchone()["n"]


def create_mixtape(conn, user_id: int, name: str, now) -> int:
    return conn.execute(
        "INSERT INTO totr.mixtapes (user_id, name, created_at, updated_at) VALUES (%s, %s, %s, %s) RETURNING mixtape_id",
        (user_id, name, now, now),
    ).fetchone()["mixtape_id"]


def get_mixtape(conn, user_id: int, mixtape_id: int):
    return conn.execute(
        "SELECT mixtape_id, name, updated_at FROM totr.mixtapes WHERE mixtape_id = %s AND user_id = %s",
        (mixtape_id, user_id),
    ).fetchone()


def rename_mixtape(conn, user_id: int, mixtape_id: int, name: str, now):
    conn.execute(
        "UPDATE totr.mixtapes SET name = %s, updated_at = %s WHERE mixtape_id = %s AND user_id = %s",
        (name, now, mixtape_id, user_id),
    )


def touch(conn, mixtape_id: int, now):
    conn.execute(
        "UPDATE totr.mixtapes SET updated_at = %s WHERE mixtape_id = %s", (now, mixtape_id)
    )


def delete_mixtape(conn, user_id: int, mixtape_id: int):
    """Entries first (the database cascades too, but a plain test database does not)."""
    conn.execute(
        "DELETE FROM totr.mixtape_tracks WHERE mixtape_id = %s AND mixtape_id IN "
        "(SELECT mixtape_id FROM totr.mixtapes WHERE mixtape_id = %s AND user_id = %s)",
        (mixtape_id, mixtape_id, user_id),
    )
    conn.execute(
        "DELETE FROM totr.mixtapes WHERE mixtape_id = %s AND user_id = %s", (mixtape_id, user_id)
    )


def entries(conn, mixtape_id: int):
    return conn.execute(
        "SELECT entry_id, track_id, position FROM totr.mixtape_tracks WHERE mixtape_id = %s "
        "ORDER BY position, entry_id",
        (mixtape_id,),
    ).fetchall()


def next_position(conn, mixtape_id: int) -> int:
    return conn.execute(
        "SELECT coalesce(max(position), 0) + 1 AS n FROM totr.mixtape_tracks WHERE mixtape_id = %s",
        (mixtape_id,),
    ).fetchone()["n"]


def add_entry(conn, mixtape_id: int, track_id: int, position: int, now):
    conn.execute(
        "INSERT INTO totr.mixtape_tracks (mixtape_id, track_id, position, added_at) VALUES (%s, %s, %s, %s)",
        (mixtape_id, track_id, position, now),
    )


def remove_entry(conn, mixtape_id: int, entry_id: int) -> bool:
    return (
        conn.execute(
            "DELETE FROM totr.mixtape_tracks WHERE entry_id = %s AND mixtape_id = %s",
            (entry_id, mixtape_id),
        ).rowcount
        == 1
    )


def set_position(conn, mixtape_id: int, entry_id: int, position: int):
    conn.execute(
        "UPDATE totr.mixtape_tracks SET position = %s WHERE entry_id = %s AND mixtape_id = %s",
        (position, entry_id, mixtape_id),
    )
