-- 0003 join requests (SQLite): people who found this server from their app and asked to join. See the PostgreSQL file for what each column means.
CREATE TABLE totr_join_requests (request_id TEXT PRIMARY KEY CHECK (length(request_id) = 32),
  display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 40), note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 300),
  device_key TEXT NOT NULL CHECK (length(device_key) = 44), platform TEXT NOT NULL DEFAULT '', app_version TEXT NOT NULL DEFAULT '',
  history TEXT NOT NULL DEFAULT '', invite_hash TEXT NOT NULL CHECK (length(invite_hash) = 64),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','declined')), ip TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, answered_at TEXT, answered_by INTEGER REFERENCES totr_users(user_id) ON DELETE SET NULL);
CREATE INDEX join_requests_status_idx ON totr_join_requests (status, created_at);
CREATE INDEX join_requests_device_idx ON totr_join_requests (device_key);
