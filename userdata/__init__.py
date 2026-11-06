"""Per-person app data: settings and "continue listening" that follow a person between devices. See
sql/user_data.sql.

store.py holds the queries (bound parameters, always limited to the signed-in person's id); web.py
is the API. The server stores and returns text and never interprets it.
"""

# characters per item (the gate refuses any request body over 64 KB, so this stays well under it)
MAX_VALUE = 30000
MAX_ITEMS = 24  # items per person
NAME_PATTERN = r"^[a-z0-9][a-z0-9.-]{0,39}$"
