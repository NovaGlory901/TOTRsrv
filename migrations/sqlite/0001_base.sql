-- 0001 base (SQLite): the media ontology, accounts, mixtapes and TOTR's views over media.
-- It mirrors the PostgreSQL layout in docs/database.md: schema `totr.x` is the table `totr_x`, `media.x` is `media_x`.
-- Nobody runs this by hand: migrate.py applies it (and tools/build_local_template.py uses it to build the first-run template).
-- (The regex CHECKs of the PostgreSQL tables are not repeated: SQLite cannot, and the app validates the same rules.)

-- media: the ontology
-- The same tables, columns, constraints, indexes and seed rows as the PostgreSQL `media` schema (see sql/reference/media_schema_reference.sql);
-- tests/test_media_schema.py checks this. SQLite cannot do everything PostgreSQL does, so these differ, and only these:
--   * identity columns are INTEGER PRIMARY KEY AUTOINCREMENT, uuid is TEXT made by a default expression, timestamptz is ISO text (UTC)
--   * jsonb is TEXT (the `->>` operator reads it)
--   * title_search is lower(title): SQLite has no NORMALIZE(title, NFKC), and its lower() only knows ASCII
--   * set_updated_at() is a trigger on each table (SQLite has no trigger functions)
--   * no pg_bigm indexes (a PostgreSQL extension)
CREATE TABLE media_item_types (
  type_id INTEGER PRIMARY KEY AUTOINCREMENT,
  data_id TEXT NOT NULL DEFAULT (lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)), 2) || '-' || substr('89ab', abs(random()) % 4 + 1, 1) || substr(hex(randomblob(2)), 2) || '-' || hex(randomblob(6)))),
  item_type_slug TEXT NOT NULL,
  description TEXT,
  parent_type_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CONSTRAINT item_types_data_id_key UNIQUE (data_id),
  CONSTRAINT item_types_item_type_slug_key UNIQUE (item_type_slug),
  CONSTRAINT item_types_parent_type_id_fkey FOREIGN KEY (parent_type_id) REFERENCES media_item_types(type_id));
CREATE TABLE media_relation_types (
  relation_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
  data_id TEXT NOT NULL DEFAULT (lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)), 2) || '-' || substr('89ab', abs(random()) % 4 + 1, 1) || substr(hex(randomblob(2)), 2) || '-' || hex(randomblob(6)))),
  relation_slug TEXT NOT NULL,
  description TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CONSTRAINT relation_types_data_id_key UNIQUE (data_id),
  CONSTRAINT relation_types_relation_slug_key UNIQUE (relation_slug));
CREATE TABLE media_items (
  item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  data_id TEXT NOT NULL DEFAULT (lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)), 2) || '-' || substr('89ab', abs(random()) % 4 + 1, 1) || substr(hex(randomblob(2)), 2) || '-' || hex(randomblob(6)))),
  type_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  title_search TEXT GENERATED ALWAYS AS (lower(title)) STORED,
  CONSTRAINT items_data_id_key UNIQUE (data_id),
  CONSTRAINT items_type_id_fkey FOREIGN KEY (type_id) REFERENCES media_item_types(type_id));
CREATE TABLE media_item_relations (
  relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_item_id INTEGER NOT NULL,
  relation_type_id INTEGER NOT NULL,
  to_item_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  CONSTRAINT item_relations_from_item_id_relation_type_id_to_item_id_key UNIQUE (from_item_id, relation_type_id, to_item_id),
  CONSTRAINT item_relations_from_item_id_fkey FOREIGN KEY (from_item_id) REFERENCES media_items(item_id),
  CONSTRAINT item_relations_relation_type_id_fkey FOREIGN KEY (relation_type_id) REFERENCES media_relation_types(relation_type_id),
  CONSTRAINT item_relations_to_item_id_fkey FOREIGN KEY (to_item_id) REFERENCES media_items(item_id));
CREATE INDEX idx_item_relations_from ON media_item_relations (from_item_id);
CREATE INDEX idx_item_relations_to ON media_item_relations (to_item_id);
CREATE INDEX idx_items_type_id ON media_items (type_id);
CREATE UNIQUE INDEX items_source_uniq ON media_items ((metadata->>'source_table'), (metadata->>'source_id'));
CREATE TRIGGER trg_item_types_updated_at AFTER UPDATE ON media_item_types FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
  BEGIN UPDATE media_item_types SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE type_id = NEW.type_id; END;
CREATE TRIGGER trg_items_updated_at AFTER UPDATE ON media_items FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
  BEGIN UPDATE media_items SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE item_id = NEW.item_id; END;
CREATE TRIGGER trg_relation_types_updated_at AFTER UPDATE ON media_relation_types FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
  BEGIN UPDATE media_relation_types SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE relation_type_id = NEW.relation_type_id; END;
INSERT INTO media_item_types (item_type_slug, description) VALUES
  ('song', 'A single music track or audio recording'), ('video', 'A video file or moving-image recording'),
  ('image', 'A still image or photograph'), ('artist', 'A person, band, or group that makes music'),
  ('album', 'A collection of songs released together');
INSERT INTO media_relation_types (relation_slug, description) VALUES
  ('is_music_video_for', 'The from-item is a music video for the to-item'),
  ('track_on_album', 'The from-item (a song) appears on the to-item (an album)'),
  ('performed_by', 'The from-item (a song) is performed by the to-item (an artist)'),
  ('album_by', 'The from-item (an album) is by the to-item (an artist)');

-- totr: accounts
CREATE TABLE totr_users (user_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member','admin')), disabled BOOLEAN NOT NULL DEFAULT 0,
  permissions TEXT NOT NULL DEFAULT '', created_at TEXT, last_login_at TEXT);
CREATE UNIQUE INDEX totr_users_username_lower_idx ON totr_users (lower(username));
CREATE TABLE totr_sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES totr_users(user_id) ON DELETE CASCADE,
  csrf_token TEXT NOT NULL, created_at TEXT, last_seen TEXT, expires_at TEXT, ip TEXT, user_agent TEXT);
CREATE INDEX sessions_user_idx ON totr_sessions (user_id);
CREATE INDEX sessions_expires_idx ON totr_sessions (expires_at);
CREATE TABLE totr_invites (token_hash TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member','admin')),
  permissions TEXT NOT NULL DEFAULT '', created_by INTEGER REFERENCES totr_users(user_id) ON DELETE SET NULL,
  created_at TEXT, expires_at TEXT, used_at TEXT, used_by INTEGER REFERENCES totr_users(user_id) ON DELETE SET NULL);

-- totr: mixtapes
CREATE TABLE totr_mixtapes (mixtape_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES totr_users(user_id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80), created_at TEXT, updated_at TEXT);
CREATE UNIQUE INDEX totr_mixtapes_user_name_idx ON totr_mixtapes (user_id, lower(name));
CREATE TABLE totr_mixtape_tracks (entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
  mixtape_id INTEGER NOT NULL REFERENCES totr_mixtapes(mixtape_id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES media_items(item_id) ON DELETE CASCADE, position INTEGER NOT NULL, added_at TEXT);
CREATE INDEX mixtape_tracks_order_idx ON totr_mixtape_tracks (mixtape_id, position);

-- totr: TOTR's views over media (same columns as the PostgreSQL views)
CREATE VIEW totr_v_artists AS
SELECT i.item_id AS artist_id, i.title AS name, coalesce(i.metadata->>'created_by_app' = 'totr', 0) AS totr_created
FROM media_items i WHERE i.type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'artist');
CREATE VIEW totr_v_albums AS
SELECT al.item_id AS album_id, ab.to_item_id AS artist_id, al.title, al.metadata->>'year' AS year,
       al.metadata->>'cover_art_path' AS cover_art_path, coalesce(al.metadata->>'created_by_app' = 'totr', 0) AS totr_created
FROM media_items al
LEFT JOIN media_item_relations ab ON ab.from_item_id = al.item_id
  AND ab.relation_type_id = (SELECT relation_type_id FROM media_relation_types WHERE relation_slug = 'album_by')
WHERE al.type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'album');
CREATE VIEW totr_v_tracks AS
SELECT s.item_id AS track_id, tb.to_item_id AS album_id, s.metadata->>'track_number' AS track_number, s.title,
       s.metadata->>'file_path' AS file_path, s.metadata->>'released' AS released,
       coalesce(s.metadata->>'created_by_app' = 'totr', 0) AS totr_created
FROM media_items s
LEFT JOIN media_item_relations tb ON tb.from_item_id = s.item_id
  AND tb.relation_type_id = (SELECT relation_type_id FROM media_relation_types WHERE relation_slug = 'track_on_album')
WHERE s.type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'song') AND s.metadata->>'file_path' IS NOT NULL;
CREATE VIEW totr_v_track_artists AS
SELECT r.from_item_id AS track_id, r.to_item_id AS artist_id
FROM media_item_relations r JOIN media_items s ON s.item_id = r.from_item_id
WHERE r.relation_type_id = (SELECT relation_type_id FROM media_relation_types WHERE relation_slug = 'performed_by')
  AND s.type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'song') AND s.metadata->>'file_path' IS NOT NULL;
