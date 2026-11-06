-- 0001 base (PostgreSQL): the media ontology, accounts, mixtapes and TOTR's views over media, in a NEW database.
-- Applied by migrate.py (the installer, the updater or `python migrate.py up`), never by the running server, and never against an existing hand-made database:
-- one that already has all of this is brought under version control with `migrate.py adopt` (see docs/database.md).
-- It creates no roles and sets no passwords: roles and their grants are migrate.apply_grants, because role names differ per install.
-- Safe to run twice (IF NOT EXISTS / ON CONFLICT / CREATE OR REPLACE).

-- media: the ontology
-- This is the `media` schema, exactly as the reference dump has it (sql/reference/media_schema_reference.sql is the pg_dump of it; tests/test_media_schema.py and
-- deploy/lab-test.sh compare this block with that file). Do not trim, rename or extend it here: every TOTR install uses this schema.
-- Written so that a second run changes nothing.
CREATE SCHEMA IF NOT EXISTS media;
COMMENT ON SCHEMA media IS 'for the TOTR project  to store media including audio , images , and video ';

CREATE OR REPLACE FUNCTION media.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS media.item_types (
    type_id integer GENERATED ALWAYS AS IDENTITY (SEQUENCE NAME media.item_types_type_id_seq START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1),
    data_id uuid DEFAULT gen_random_uuid() NOT NULL,
    item_type_slug character varying(100) NOT NULL,
    description text,
    parent_type_id integer,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT item_types_pkey PRIMARY KEY (type_id),
    CONSTRAINT item_types_data_id_key UNIQUE (data_id),
    CONSTRAINT item_types_item_type_slug_key UNIQUE (item_type_slug),
    CONSTRAINT item_types_parent_type_id_fkey FOREIGN KEY (parent_type_id) REFERENCES media.item_types(type_id)
);

CREATE TABLE IF NOT EXISTS media.relation_types (
    relation_type_id integer GENERATED ALWAYS AS IDENTITY (SEQUENCE NAME media.relation_types_relation_type_id_seq START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1),
    data_id uuid DEFAULT gen_random_uuid() NOT NULL,
    relation_slug character varying(100) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT relation_types_pkey PRIMARY KEY (relation_type_id),
    CONSTRAINT relation_types_data_id_key UNIQUE (data_id),
    CONSTRAINT relation_types_relation_slug_key UNIQUE (relation_slug)
);

CREATE TABLE IF NOT EXISTS media.items (
    item_id integer GENERATED ALWAYS AS IDENTITY (SEQUENCE NAME media.items_item_id_seq START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1),
    data_id uuid DEFAULT gen_random_uuid() NOT NULL,
    type_id integer NOT NULL,
    title text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    title_search text GENERATED ALWAYS AS (lower(NORMALIZE(title, NFKC))) STORED,
    CONSTRAINT items_pkey PRIMARY KEY (item_id),
    CONSTRAINT items_data_id_key UNIQUE (data_id),
    CONSTRAINT items_type_id_fkey FOREIGN KEY (type_id) REFERENCES media.item_types(type_id)
);

CREATE TABLE IF NOT EXISTS media.item_relations (
    relation_id integer GENERATED ALWAYS AS IDENTITY (SEQUENCE NAME media.item_relations_relation_id_seq START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1),
    from_item_id integer NOT NULL,
    relation_type_id integer NOT NULL,
    to_item_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT item_relations_pkey PRIMARY KEY (relation_id),
    CONSTRAINT item_relations_from_item_id_relation_type_id_to_item_id_key UNIQUE (from_item_id, relation_type_id, to_item_id),
    CONSTRAINT item_relations_from_item_id_fkey FOREIGN KEY (from_item_id) REFERENCES media.items(item_id),
    CONSTRAINT item_relations_relation_type_id_fkey FOREIGN KEY (relation_type_id) REFERENCES media.relation_types(relation_type_id),
    CONSTRAINT item_relations_to_item_id_fkey FOREIGN KEY (to_item_id) REFERENCES media.items(item_id)
);

CREATE INDEX IF NOT EXISTS idx_item_relations_from ON media.item_relations USING btree (from_item_id);
CREATE INDEX IF NOT EXISTS idx_item_relations_to ON media.item_relations USING btree (to_item_id);
CREATE INDEX IF NOT EXISTS idx_items_type_id ON media.items USING btree (type_id);
CREATE UNIQUE INDEX IF NOT EXISTS items_source_uniq ON media.items USING btree (((metadata ->> 'source_table'::text)), ((metadata ->> 'source_id'::text)));

CREATE OR REPLACE TRIGGER trg_item_types_updated_at BEFORE UPDATE ON media.item_types FOR EACH ROW EXECUTE FUNCTION media.set_updated_at();
CREATE OR REPLACE TRIGGER trg_items_updated_at BEFORE UPDATE ON media.items FOR EACH ROW EXECUTE FUNCTION media.set_updated_at();
CREATE OR REPLACE TRIGGER trg_relation_types_updated_at BEFORE UPDATE ON media.relation_types FOR EACH ROW EXECUTE FUNCTION media.set_updated_at();

-- The two title-search indexes use the pg_bigm extension (it lives in schema public). An extension is not something this script can count on, so they are
-- made when the extension is there (or can be added by the account running this) and left out, with a notice, when it is not. TOTR itself does not use them.
DO $bigm$
DECLARE ext_schema text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_bigm') AND EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_bigm') THEN
        BEGIN
            CREATE EXTENSION pg_bigm SCHEMA public;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE NOTICE 'pg_bigm is available but this account may not add it; the two pg_bigm title indexes were not made';
        END;
    END IF;
    SELECT n.nspname INTO ext_schema FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_bigm';
    IF ext_schema IS NULL THEN
        RAISE NOTICE 'pg_bigm is not installed: idx_items_title_bigm and idx_items_title_search_bigm were not made';
    ELSE
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_items_title_bigm ON media.items USING gin (title %I.gin_bigm_ops)', ext_schema);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_items_title_search_bigm ON media.items USING gin (title_search %I.gin_bigm_ops)', ext_schema);
    END IF;
END
$bigm$;

-- The kinds of item and of link TOTR uses (the same rows, in the same order, as in the reference dump).
INSERT INTO media.item_types (item_type_slug, description) VALUES
    ('song',   'A single music track or audio recording'),
    ('video',  'A video file or moving-image recording'),
    ('image',  'A still image or photograph'),
    ('artist', 'A person, band, or group that makes music'),
    ('album',  'A collection of songs released together')
ON CONFLICT (item_type_slug) DO NOTHING;

INSERT INTO media.relation_types (relation_slug, description) VALUES
    ('is_music_video_for', 'The from-item is a music video for the to-item'),
    ('track_on_album',     'The from-item (a song) appears on the to-item (an album)'),
    ('performed_by',       'The from-item (a song) is performed by the to-item (an artist)'),
    ('album_by',           'The from-item (an album) is by the to-item (an artist)')
ON CONFLICT (relation_slug) DO NOTHING;

-- Nobody gets in by default; roles are granted what they need by migrate.apply_grants.
REVOKE ALL ON SCHEMA media FROM PUBLIC;

-- totr: accounts
-- Passwords are stored only as Argon2id hashes. Session and invite tokens are stored only as SHA-256 hashes, so a leaked database
-- does not hand out working logins or invite links.
CREATE SCHEMA IF NOT EXISTS totr;
COMMENT ON SCHEMA totr IS 'TOTR application data: accounts, sessions, invites, mixtapes, and TOTR helper views over media. Not part of the media ontology.';

CREATE TABLE IF NOT EXISTS totr.users (
    user_id       serial PRIMARY KEY,
    username      text        NOT NULL CHECK (username ~ '^[A-Za-z0-9_.-]{3,32}$'),
    password_hash text        NOT NULL,
    role          text        NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin')),
    disabled      boolean     NOT NULL DEFAULT false,
    permissions   text        NOT NULL DEFAULT '' CHECK (permissions ~ '^[a-z_,]*$'),   -- extra rights for members
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);
-- Usernames are unique ignoring case ("Nova" and "nova" are the same person).
CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx ON totr.users (lower(username));

CREATE TABLE IF NOT EXISTS totr.sessions (
    token_hash text        PRIMARY KEY,                       -- sha256 hex of the cookie value
    user_id    integer     NOT NULL REFERENCES totr.users (user_id) ON DELETE CASCADE,
    csrf_token text        NOT NULL,
    created_at timestamptz NOT NULL,
    last_seen  timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    ip         text,
    user_agent text
);
CREATE INDEX IF NOT EXISTS sessions_user_idx    ON totr.sessions (user_id);
CREATE INDEX IF NOT EXISTS sessions_expires_idx ON totr.sessions (expires_at);

CREATE TABLE IF NOT EXISTS totr.invites (
    token_hash text        PRIMARY KEY,                       -- sha256 hex of the link's token
    role       text        NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin')),
    permissions text       NOT NULL DEFAULT '' CHECK (permissions ~ '^[a-z_,]*$'),
    created_by integer     REFERENCES totr.users (user_id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    used_by    integer     REFERENCES totr.users (user_id) ON DELETE SET NULL
);

-- Nobody else gets in, including the read-only role the website uses for music queries.
REVOKE ALL ON totr.users, totr.sessions, totr.invites FROM PUBLIC;

-- totr: mixtapes (each person's own playlists, private to them)
CREATE TABLE IF NOT EXISTS totr.mixtapes (
    mixtape_id serial      PRIMARY KEY,
    user_id    integer     NOT NULL REFERENCES totr.users (user_id) ON DELETE CASCADE,
    name       text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- One person cannot have two mixtapes with the same name (ignoring case).
CREATE UNIQUE INDEX IF NOT EXISTS mixtapes_user_name_idx ON totr.mixtapes (user_id, lower(name));

CREATE TABLE IF NOT EXISTS totr.mixtape_tracks (
    entry_id   serial      PRIMARY KEY,
    mixtape_id integer     NOT NULL REFERENCES totr.mixtapes (mixtape_id) ON DELETE CASCADE,
    track_id   integer     NOT NULL REFERENCES media.items (item_id) ON DELETE CASCADE,
    position   integer     NOT NULL,                       -- play order; gaps are fine
    added_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mixtape_tracks_order_idx ON totr.mixtape_tracks (mixtape_id, position);

REVOKE ALL ON totr.mixtapes, totr.mixtape_tracks FROM PUBLIC;

-- totr: TOTR's helper views over media
-- They give the app the shape the old flat tables had (ids are the items' item_id); they are the same as sql/totr_views.sql (tests/test_migrate.py builds a
-- database both ways and compares them). totr_created is true only for items carrying metadata.created_by_app = 'totr';
-- the app edits or deletes nothing else. Values not in the expected shape come out as NULL rather than breaking the whole view.
CREATE OR REPLACE VIEW totr.v_artists AS
SELECT i.item_id AS artist_id, i.title AS name,
       coalesce(i.metadata->>'created_by_app' = 'totr', false) AS totr_created   -- the app may only edit/delete what it created
FROM media.items i
WHERE i.type_id = (SELECT type_id FROM media.item_types WHERE item_type_slug = 'artist');

CREATE OR REPLACE VIEW totr.v_albums AS
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

CREATE OR REPLACE VIEW totr.v_tracks AS
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

CREATE OR REPLACE VIEW totr.v_track_artists AS
SELECT r.from_item_id AS track_id, r.to_item_id AS artist_id
FROM media.item_relations r
JOIN media.items s ON s.item_id = r.from_item_id
WHERE r.relation_type_id = (SELECT relation_type_id FROM media.relation_types WHERE relation_slug = 'performed_by')
  AND s.type_id = (SELECT type_id FROM media.item_types WHERE item_type_slug = 'song')
  AND s.metadata->>'file_path' IS NOT NULL;
