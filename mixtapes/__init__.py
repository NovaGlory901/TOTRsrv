"""Mixtapes: each person's own playlists. Private to their owner. See sql/mixtapes.sql for the tables.

store.py holds every query (all bound parameters, all limited to the owner); web.py is the API.
"""

MAX_MIXTAPES = 100  # per person
MAX_ENTRIES = 1000  # songs per mixtape
MAX_NAME = 80
