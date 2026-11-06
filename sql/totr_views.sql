-- FRESH DATABASES ONLY (an existing hand-made database already has the same views). Run as the database owner, after
-- media_ontology.sql and auth_schema.sql (which creates schema totr). The app never runs it.
--
-- TOTR's helper views over the media ontology. They live in schema totr, not media: media stays the pure ontology.
-- They give the app the shape the old flat tables had (ids are the items' item_id), so reading code stays simple.
--
-- totr_created is true only for items carrying metadata.created_by_app = 'totr'; the app edits or deletes nothing else.
-- Values that are not in the expected shape come out as NULL rather than breaking the whole view.

CREATE VIEW totr.v_artists AS
SELECT i.item_id AS artist_id, i.title AS name,
       coalesce(i.metadata->>'created_by_app' = 'totr', false) AS totr_created   -- the app may only edit/delete what it created
FROM media.items i
WHERE i.type_id = (SELECT type_id FROM media.item_types WHERE item_type_slug = 'artist');

CREATE VIEW totr.v_albums AS
SELECT al.item_id AS album_id,
       ab.to_item_id AS artist_id,                       -- the owner the album is filed under (album_by)
       al.title,
       CASE WHEN al.metadata->>'year' ~ '^[0-9]{1,4}$' THEN CAST(al.metadata->>'year' AS integer) END AS year,
       al.metadata->>'cover_art_path' AS cover_art_path,
       coalesce(al.metadata->>'created_by_app' = 'totr', false) AS totr_created
FROM media.items al
LEFT JOIN media.item_relations ab
       ON ab.from_item_id = al.item_id
      AND ab.relation_type_id = (SELECT relation_type_id FROM media.relation_types WHERE relation_slug = 'album_by')
WHERE al.type_id = (SELECT type_id FROM media.item_types WHERE item_type_slug = 'album');

CREATE VIEW totr.v_tracks AS
SELECT s.item_id AS track_id,
       tb.to_item_id AS album_id,                        -- NULL = a loose song (no track_on_album relation)
       CASE WHEN s.metadata->>'track_number' ~ '^[0-9]{1,5}$' THEN CAST(s.metadata->>'track_number' AS integer) END AS track_number,
       s.title,
       s.metadata->>'file_path' AS file_path,
       CASE WHEN s.metadata->>'released' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN s.metadata->>'released' END AS released,
       coalesce(s.metadata->>'created_by_app' = 'totr', false) AS totr_created
FROM media.items s
LEFT JOIN media.item_relations tb
       ON tb.from_item_id = s.item_id
      AND tb.relation_type_id = (SELECT relation_type_id FROM media.relation_types WHERE relation_slug = 'track_on_album')
WHERE s.type_id = (SELECT type_id FROM media.item_types WHERE item_type_slug = 'song')
  AND s.metadata->>'file_path' IS NOT NULL;              -- only songs the site can play (a song without a file is not TOTR's to list)

CREATE VIEW totr.v_track_artists AS
SELECT r.from_item_id AS track_id, r.to_item_id AS artist_id
FROM media.item_relations r
JOIN media.items s ON s.item_id = r.from_item_id
WHERE r.relation_type_id = (SELECT relation_type_id FROM media.relation_types WHERE relation_slug = 'performed_by')
  AND s.type_id = (SELECT type_id FROM media.item_types WHERE item_type_slug = 'song')
  AND s.metadata->>'file_path' IS NOT NULL;
