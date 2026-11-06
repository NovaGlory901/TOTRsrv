-- FRESH DATABASES ONLY (an existing hand-made database may already have the read-only role "robots"). Run as a superuser, after totr_views.sql.
-- Creates the read-only role the website uses for every music query. It sees the music only through TOTR's views
-- (schema totr) and has no access to the media tables themselves or to the account tables.
-- Then put DB_USER=robots and DB_PASSWORD=... in .env. Change CHANGE_ME inside pgAdmin, not in this file, and never commit it.

CREATE ROLE robots LOGIN PASSWORD 'CHANGE_ME';
GRANT CONNECT ON DATABASE "totr" TO robots;     -- use your own database name
GRANT USAGE ON SCHEMA totr TO robots;
GRANT SELECT ON totr.v_artists, totr.v_albums, totr.v_tracks, totr.v_track_artists TO robots;
