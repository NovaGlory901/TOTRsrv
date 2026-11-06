-- Join requests: people who found this server from their TOTR app and asked to join. A row is a question for the admin, not an account.
-- Run as the database owner, on an existing database or a fresh one (after sql/auth_schema.sql). The app never runs it.
--   psql -h /run/postgresql -U Admin -d totr -X -v ON_ERROR_STOP=1 -f sql/join_requests.sql
-- Safe to run twice. It needs no new role: it uses the accounts role (totr_auth), like invites.
--
-- Design notes
--  * `invite_hash` is the SHA-256 of a one-time token the asking app made up itself. Approving turns it into an ordinary invite (totr.invites),
--    so the server never stores or sends a secret. `device_key` is the app install's public key; the request was signed with the private half.
--  * `history` is a small JSON of counts the app reported about the person's home server: a hint for the admin, not proof.
--  * Answered rows are purged after 7 days, waiting ones after 14. Nothing here touches the music or the accounts.

BEGIN;

CREATE TABLE IF NOT EXISTS totr.join_requests (
    request_id   text        PRIMARY KEY CHECK (request_id ~ '^[0-9a-f]{32}$'),
    display_name text        NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 40),
    note         text        NOT NULL DEFAULT '' CHECK (char_length(note) <= 300),
    device_key   text        NOT NULL CHECK (char_length(device_key) = 44),
    platform     text        NOT NULL DEFAULT '' CHECK (char_length(platform) <= 40),
    app_version  text        NOT NULL DEFAULT '' CHECK (char_length(app_version) <= 20),
    history      text        NOT NULL DEFAULT '' CHECK (char_length(history) <= 600),
    invite_hash  text        NOT NULL CHECK (invite_hash ~ '^[0-9a-f]{64}$'),
    status       text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'declined')),
    ip           text        NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL,
    answered_at  timestamptz,
    answered_by  integer     REFERENCES totr.users (user_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS join_requests_status_idx ON totr.join_requests (status, created_at);
CREATE INDEX IF NOT EXISTS join_requests_device_idx ON totr.join_requests (device_key);

REVOKE ALL ON totr.join_requests FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON totr.join_requests TO totr_auth;

COMMIT;

-- Check: the table exists, is empty, and the accounts role can use it. Expect: one row, 0, true, true.
SELECT to_regclass('totr.join_requests') IS NOT NULL                        AS table_exists,
       (SELECT count(*) FROM totr.join_requests)                            AS rows_now,
       has_table_privilege('totr_auth', 'totr.join_requests', 'SELECT')     AS auth_can_read,
       has_table_privilege('totr_auth', 'totr.join_requests', 'INSERT')     AS auth_can_write;
