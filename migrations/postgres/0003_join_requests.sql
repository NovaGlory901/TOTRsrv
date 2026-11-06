-- 0003 join requests (PostgreSQL): people who found this server from their app and asked to join.
-- A row is a question for the admin, not an account. `invite_hash` is the SHA-256 of a one-time token the asking app made up itself;
-- approving turns it into an ordinary invite (totr.invites), so the server never stores or sends a secret. `device_key` is the app install's
-- public key (base64); the request was signed with its private half. `history` is a small JSON of counts the app reported about the person's
-- home server (the admin sees it as a hint, not proof). Rows are purged a few days after they are answered.
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
