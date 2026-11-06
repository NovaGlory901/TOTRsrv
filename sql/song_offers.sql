-- Song offers: a person offers songs they saved in the app to this server, or an admin asks a person for theirs. Nothing enters the library until a person who may add
-- music accepts some songs and the owner of the songs then sends exactly those files. Run as the database owner, on an existing database or a fresh one (after sql/auth_schema.sql).
-- The app never runs it:   psql -h /run/postgresql -U Admin -d totr -X -v ON_ERROR_STOP=1 -f sql/song_offers.sql
-- Safe to run twice. It uses the accounts role (totr_auth), like invites. Answered offers are purged after 30 days. Nothing here touches the music itself.

BEGIN;

CREATE TABLE IF NOT EXISTS totr.song_requests (
    request_id    text        PRIMARY KEY CHECK (request_id ~ '^[0-9a-f]{32}$'),
    user_id       integer     NOT NULL REFERENCES totr.users (user_id) ON DELETE CASCADE,
    asked_by      integer     REFERENCES totr.users (user_id) ON DELETE SET NULL,
    asked_by_name text        NOT NULL DEFAULT '',
    note          text        NOT NULL DEFAULT '' CHECK (char_length(note) <= 300),
    status        text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled')),
    offer_id      text,
    created_at    timestamptz NOT NULL,
    answered_at   timestamptz
);
CREATE INDEX IF NOT EXISTS song_requests_user_idx ON totr.song_requests (user_id, status);

CREATE TABLE IF NOT EXISTS totr.song_offers (
    offer_id         text        PRIMARY KEY CHECK (offer_id ~ '^[0-9a-f]{32}$'),
    user_id          integer     NOT NULL REFERENCES totr.users (user_id) ON DELETE CASCADE,
    request_id       text        REFERENCES totr.song_requests (request_id) ON DELETE SET NULL,
    note             text        NOT NULL DEFAULT '' CHECK (char_length(note) <= 300),
    status           text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'answered', 'cancelled')),
    created_at       timestamptz NOT NULL,
    answered_at      timestamptz,
    answered_by      integer     REFERENCES totr.users (user_id) ON DELETE SET NULL,
    answered_by_name text        NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS song_offers_user_idx ON totr.song_offers (user_id, status);

CREATE TABLE IF NOT EXISTS totr.song_offer_items (
    offer_id text    NOT NULL REFERENCES totr.song_offers (offer_id) ON DELETE CASCADE,
    item_no  integer NOT NULL CHECK (item_no >= 0),
    title    text    NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
    artist   text    NOT NULL DEFAULT '' CHECK (char_length(artist) <= 200),
    album    text    NOT NULL DEFAULT '' CHECK (char_length(album) <= 200),
    ext      text    NOT NULL CHECK (ext IN ('.mp3', '.flac', '.ogg', '.opus', '.m4a', '.wav')),
    size     bigint  NOT NULL CHECK (size BETWEEN 1 AND 314572800),
    sha256   text    NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    status   text    NOT NULL DEFAULT 'offered' CHECK (status IN ('offered', 'accepted', 'receiving', 'declined', 'uploaded', 'failed')),
    error    text    NOT NULL DEFAULT '' CHECK (char_length(error) <= 300),
    PRIMARY KEY (offer_id, item_no)
);

REVOKE ALL ON totr.song_requests, totr.song_offers, totr.song_offer_items FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON totr.song_requests, totr.song_offers, totr.song_offer_items TO totr_auth;

COMMIT;

-- Check: the tables exist and the accounts role can use them. Expect: one row, true, true, true.
SELECT to_regclass('totr.song_offers') IS NOT NULL AND to_regclass('totr.song_offer_items') IS NOT NULL AND to_regclass('totr.song_requests') IS NOT NULL AS tables_exist,
       has_table_privilege('totr_auth', 'totr.song_offers', 'INSERT')       AS auth_can_write_offers,
       has_table_privilege('totr_auth', 'totr.song_offer_items', 'UPDATE')  AS auth_can_update_items;
