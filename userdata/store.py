"""Every user_data query (same rules as auth/store.py: bound parameters only, always limited to one
person's id, times passed in)."""


def list_items(conn, user_id: int):
    return conn.execute(
        "SELECT name, value, updated_ms FROM totr.user_data WHERE user_id = %s ORDER BY name",
        (user_id,),
    ).fetchall()


def count_items(conn, user_id: int) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM totr.user_data WHERE user_id = %s", (user_id,)
    ).fetchone()["n"]


def get_item(conn, user_id: int, name: str):
    return conn.execute(
        "SELECT name, value, updated_ms FROM totr.user_data WHERE user_id = %s AND name = %s",
        (user_id, name),
    ).fetchone()


def save_item(conn, user_id: int, name: str, value: str, updated_ms: int, now) -> bool:
    """Stores the item unless the stored copy is newer (or equally new). True when this save was kept."""
    changed = conn.execute(
        "UPDATE totr.user_data SET value = %s, updated_ms = %s, saved_at = %s "
        "WHERE user_id = %s AND name = %s AND updated_ms < %s",
        (value, updated_ms, now, user_id, name, updated_ms),
    ).rowcount
    if changed:
        return True
    return bool(
        conn.execute(
            "INSERT INTO totr.user_data (user_id, name, value, updated_ms, saved_at) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (user_id, name) DO NOTHING",
            (user_id, name, value, updated_ms, now),
        ).rowcount
    )


def delete_item(conn, user_id: int, name: str):
    conn.execute("DELETE FROM totr.user_data WHERE user_id = %s AND name = %s", (user_id, name))
