-- 0002 user data (SQLite): per-person app data (settings and listening position that follow a person between devices).
CREATE TABLE totr_user_data (user_id INTEGER NOT NULL REFERENCES totr_users(user_id) ON DELETE CASCADE, name TEXT NOT NULL,
  value TEXT NOT NULL, updated_ms INTEGER NOT NULL, saved_at TEXT, PRIMARY KEY (user_id, name));
