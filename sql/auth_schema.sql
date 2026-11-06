-- FRESH DATABASES ONLY. Run ON the database server as a superuser / the database owner (e.g. in pgAdmin). The app never runs it.
-- (An existing hand-made database may already have these tables; see docs/database.md.)
-- Creates schema "totr" (TOTR's own data) with the accounts tables, and a dedicated role for them.
-- Then put DB_AUTH_USER=totr_auth and DB_AUTH_PASSWORD=... in .env and create the first admin with:
--     python -m auth.cli create-admin YOURNAME
-- Run order for a fresh database: media_ontology.sql, auth_schema.sql, totr_views.sql, mixtapes.sql, ...
--
-- Design notes
--  * Passwords are stored only as Argon2id hashes. Session and invite tokens are stored only as
--    SHA-256 hashes, so a leaked database does not hand out working logins or invite links.
--  * The website's music queries use the read-only "robots" role, which gets NO access to these
--    tables (it sees only TOTR's music views). A SQL injection bug in a music route could not read password hashes.
--  * totr_auth can only touch these three tables.

CREATE SCHEMA totr;
COMMENT ON SCHEMA totr IS 'TOTR application data: accounts, sessions, invites, mixtapes, and TOTR helper views over media. Not part of the media ontology.';

CREATE TABLE totr.users (
    user_id       serial PRIMARY KEY,
    username      text        NOT NULL CHECK (username ~ '^[A-Za-z0-9_.-]{3,32}$'),
    password_hash text        NOT NULL,
    role          text        NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin')),
    disabled      boolean     NOT NULL DEFAULT false,
    permissions   text        NOT NULL DEFAULT '' CHECK (permissions ~ '^[a-z_,]*$'),   -- extra rights for members; see user_permissions.sql
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);
-- Usernames are unique ignoring case ("Nova" and "nova" are the same person).
CREATE UNIQUE INDEX users_username_lower_idx ON totr.users (lower(username));

CREATE TABLE totr.sessions (
    token_hash text        PRIMARY KEY,                       -- sha256 hex of the cookie value
    user_id    integer     NOT NULL REFERENCES totr.users (user_id) ON DELETE CASCADE,
    csrf_token text        NOT NULL,
    created_at timestamptz NOT NULL,
    last_seen  timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    ip         text,
    user_agent text
);
CREATE INDEX sessions_user_idx    ON totr.sessions (user_id);
CREATE INDEX sessions_expires_idx ON totr.sessions (expires_at);

CREATE TABLE totr.invites (
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

CREATE ROLE totr_auth LOGIN PASSWORD 'CHANGE_ME';
GRANT CONNECT ON DATABASE "totr" TO totr_auth;
GRANT USAGE ON SCHEMA totr TO totr_auth;
GRANT SELECT, INSERT, UPDATE, DELETE ON totr.users, totr.sessions, totr.invites TO totr_auth;
GRANT USAGE, SELECT ON SEQUENCE totr.users_user_id_seq TO totr_auth;
