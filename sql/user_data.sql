-- Per-person app data (settings and "continue listening" that follow a person from device to device).
-- Run as the database owner, on an existing database or a fresh one (after sql/auth_schema.sql). The app never runs it.
--   psql -h /run/postgresql -U Admin -d totr -X -v ON_ERROR_STOP=1 -f sql/user_data.sql
-- Safe to run twice (IF NOT EXISTS; the grant can be repeated). It needs no new role or .env setting: it uses the existing accounts role
-- (totr_auth), because this is data that belongs to one signed-in person, like their sessions.
--
-- Design notes
--  * One row per person and name. `value` is text the app stores for the person and hands back to them; the server never
--    interprets it (the app checks it again when it reads it). Capped at 30000 characters per row, names are a short lowercase word.
--  * `updated_ms` is the time (milliseconds) the device made the change. A save with an older time than the one stored is ignored,
--    so two devices cannot overwrite newer data with older data.
--  * Deleting a person deletes their rows (ON DELETE CASCADE).
--  * Nothing here touches the music. Run it only after a backup, as with any change to a database.

BEGIN;

CREATE TABLE IF NOT EXISTS totr.user_data (
    user_id    integer     NOT NULL REFERENCES totr.users (user_id) ON DELETE CASCADE,
    name       text        NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9.-]{0,39}$'),
    value      text        NOT NULL CHECK (char_length(value) <= 30000),
    updated_ms bigint      NOT NULL CHECK (updated_ms >= 0),
    saved_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name)
);

REVOKE ALL ON totr.user_data FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON totr.user_data TO totr_auth;

COMMIT;

-- Check: the table exists, is empty, and the accounts role can use it. Expect: one row, 0, true, true.
SELECT to_regclass('totr.user_data') IS NOT NULL                        AS table_exists,
       (SELECT count(*) FROM totr.user_data)                            AS rows_now,
       has_table_privilege('totr_auth', 'totr.user_data', 'SELECT')     AS auth_can_read,
       has_table_privilege('totr_auth', 'totr.user_data', 'INSERT')     AS auth_can_write;
