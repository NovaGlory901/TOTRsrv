-- 0002 user data (PostgreSQL): per-person app data (settings and "continue listening" that follow a person from device to device).
-- One row per person and name. `value` is text the app stores for the person and hands back to them; the server never interprets it.
-- `updated_ms` is the time (milliseconds) the device made the change; a save with an older time than the one stored is ignored.
-- Deleting a person deletes their rows (ON DELETE CASCADE). Nothing here touches the music.
CREATE TABLE IF NOT EXISTS totr.user_data (
    user_id    integer     NOT NULL REFERENCES totr.users (user_id) ON DELETE CASCADE,
    name       text        NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9.-]{0,39}$'),
    value      text        NOT NULL CHECK (char_length(value) <= 30000),
    updated_ms bigint      NOT NULL CHECK (updated_ms >= 0),
    saved_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name)
);

REVOKE ALL ON totr.user_data FROM PUBLIC;
