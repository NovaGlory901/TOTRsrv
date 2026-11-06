-- FRESH DATABASES ONLY. Run as a superuser, after media_ontology.sql and totr_views.sql. The app never runs it.
-- (An existing hand-made database may already have these grants; see docs/database.md.)
-- Creates a limited write role used ONLY by the upload page.
-- Then put DB_WRITE_USER=totr_uploader and DB_WRITE_PASSWORD=... in .env.
-- Change CHANGE_ME inside pgAdmin, not in the repo file, and never commit the real password.
--
-- It adds artists, albums and songs to the media ontology as items and relations (it looks an artist up first, so it
-- needs SELECT). No UPDATE, DELETE or DDL on purpose.

CREATE ROLE totr_uploader LOGIN PASSWORD 'CHANGE_ME';
GRANT CONNECT ON DATABASE "totr" TO totr_uploader;     -- use your own database name
GRANT USAGE ON SCHEMA media, totr TO totr_uploader;
GRANT SELECT ON media.item_types, media.relation_types TO totr_uploader;
GRANT SELECT, INSERT ON media.items, media.item_relations TO totr_uploader;
GRANT SELECT ON totr.v_artists, totr.v_albums, totr.v_tracks, totr.v_track_artists TO totr_uploader;
GRANT USAGE, SELECT ON SEQUENCE media.items_item_id_seq, media.item_relations_relation_id_seq TO totr_uploader;
