-- FRESH DATABASES ONLY. Run first, as a superuser / the database owner, in a NEW, EMPTY database. The app never runs it.
-- NOT for an existing hand-made database that already has this schema (see docs/database.md).
-- (`python migrate.py up` makes the same thing; this file is kept because the tests compare the two.)
--
-- The `media` schema is a fixed ontology, identical in every install: sql/reference/media_schema_reference.sql is the pg_dump of the original.
--
--   media.item_types       what kinds of item exist (song, video, image, artist, album)
--   media.relation_types   what kinds of link exist (track_on_album, performed_by, album_by, is_music_video_for)
--   media.items            every thing: a title, its type, and `metadata` (file path, year, cover, provenance, outside ids)
--   media.item_relations   typed links: from-item, relation type, to-item
--
-- Run order for a fresh database: media_ontology.sql, auth_schema.sql, totr_views.sql, mixtapes.sql, upload_role.sql,
-- maintenance_role.sql (and a read-only role: see robots_role.sql).

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
