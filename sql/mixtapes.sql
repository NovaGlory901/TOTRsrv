-- FRESH DATABASES ONLY. Run as a superuser / the database owner (e.g. in pgAdmin). The app never runs it.
-- (An existing hand-made database may already have these tables; see docs/database.md.)
-- Creates the tables for mixtapes (each person's own playlists) and a small role used ONLY by the mixtape feature.
-- Then put DB_MIXTAPE_USER=totr_mixtapes and DB_MIXTAPE_PASSWORD=... in .env and restart the app.
--
-- Run this AFTER sql/media_ontology.sql and sql/auth_schema.sql (it points at media.items and totr.users).
--
-- Design notes
--  * A mixtape belongs to one user and is private to them. Every query in the app is limited to the signed-in user.
--  * Deleting a user deletes their mixtapes (ON DELETE CASCADE). Deleting a song item (for example when the Collector
--    undoes an add) removes it from every mixtape: the foreign key points at media.items and cascades on THIS table only.
--  * totr_mixtapes can only touch these two tables. It cannot read the music (the app reads song titles with the
--    read-only "robots" role) and cannot read users or sessions.
--  * Change CHANGE_ME below inside pgAdmin, not in the repo file, and never commit the real password.

CREATE TABLE totr.mixtapes (
    mixtape_id serial      PRIMARY KEY,
    user_id    integer     NOT NULL REFERENCES totr.users (user_id) ON DELETE CASCADE,
    name       text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- One person cannot have two mixtapes with the same name (ignoring case).
CREATE UNIQUE INDEX mixtapes_user_name_idx ON totr.mixtapes (user_id, lower(name));

CREATE TABLE totr.mixtape_tracks (
    entry_id   serial      PRIMARY KEY,
    mixtape_id integer     NOT NULL REFERENCES totr.mixtapes (mixtape_id) ON DELETE CASCADE,
    track_id   integer     NOT NULL REFERENCES media.items (item_id) ON DELETE CASCADE,
    position   integer     NOT NULL,                       -- play order; gaps are fine
    added_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mixtape_tracks_order_idx ON totr.mixtape_tracks (mixtape_id, position);

REVOKE ALL ON totr.mixtapes, totr.mixtape_tracks FROM PUBLIC;

CREATE ROLE totr_mixtapes LOGIN PASSWORD 'CHANGE_ME';
GRANT CONNECT ON DATABASE "totr" TO totr_mixtapes;
GRANT USAGE ON SCHEMA totr TO totr_mixtapes;
GRANT SELECT, INSERT, UPDATE, DELETE ON totr.mixtapes, totr.mixtape_tracks TO totr_mixtapes;
GRANT USAGE, SELECT ON SEQUENCE totr.mixtapes_mixtape_id_seq, totr.mixtape_tracks_entry_id_seq TO totr_mixtapes;
