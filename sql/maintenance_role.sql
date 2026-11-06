-- FRESH DATABASES ONLY. Run as a superuser, after media_ontology.sql and totr_views.sql. The app never runs it.
-- (An existing hand-made database may already have these grants; see docs/database.md.)
-- Creates the role the maintenance spiders and the Collector use AFTER YOU APPROVE a fix or an add in the app.
-- Then put DB_MAINT_USER=totr_maint and DB_MAINT_PASSWORD=... in .env.
-- Change CHANGE_ME inside pgAdmin, not in the repo file, and never commit the real password.
--
-- This role can change and delete items and relations, which is why it is separate from the upload role and the
-- read-only "robots" role. It can NOT create, alter or drop tables. The app itself only edits or deletes items that TOTR
-- created (metadata.created_by_app = 'totr') and never what something else links to; a table privilege cannot see that,
-- so it is enforced in catalog.py and tested in tests/test_catalog.py.

CREATE ROLE totr_maint LOGIN PASSWORD 'CHANGE_ME';
GRANT CONNECT ON DATABASE "totr" TO totr_maint;        -- use your own database name
GRANT USAGE ON SCHEMA media, totr TO totr_maint;
GRANT SELECT ON media.item_types, media.relation_types TO totr_maint;
GRANT SELECT, INSERT, UPDATE, DELETE ON media.items, media.item_relations TO totr_maint;
GRANT SELECT ON totr.v_artists, totr.v_albums, totr.v_tracks, totr.v_track_artists TO totr_maint;
GRANT USAGE, SELECT ON SEQUENCE media.items_item_id_seq, media.item_relations_relation_id_seq TO totr_maint;
