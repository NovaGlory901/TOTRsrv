-- 0004 song offers (SQLite): see the PostgreSQL file for what each table is for.
CREATE TABLE totr_song_requests (request_id TEXT PRIMARY KEY CHECK (length(request_id) = 32), user_id INTEGER NOT NULL REFERENCES totr_users(user_id) ON DELETE CASCADE,
  asked_by INTEGER REFERENCES totr_users(user_id) ON DELETE SET NULL, asked_by_name TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 300),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','declined','cancelled')), offer_id TEXT, created_at TEXT NOT NULL, answered_at TEXT);
CREATE INDEX song_requests_user_idx ON totr_song_requests (user_id, status);
CREATE TABLE totr_song_offers (offer_id TEXT PRIMARY KEY CHECK (length(offer_id) = 32), user_id INTEGER NOT NULL REFERENCES totr_users(user_id) ON DELETE CASCADE,
  request_id TEXT REFERENCES totr_song_requests(request_id) ON DELETE SET NULL, note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 300),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','answered','cancelled')), created_at TEXT NOT NULL, answered_at TEXT,
  answered_by INTEGER REFERENCES totr_users(user_id) ON DELETE SET NULL, answered_by_name TEXT NOT NULL DEFAULT '');
CREATE INDEX song_offers_user_idx ON totr_song_offers (user_id, status);
CREATE TABLE totr_song_offer_items (offer_id TEXT NOT NULL REFERENCES totr_song_offers(offer_id) ON DELETE CASCADE, item_no INTEGER NOT NULL CHECK (item_no >= 0),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200), artist TEXT NOT NULL DEFAULT '' CHECK (length(artist) <= 200), album TEXT NOT NULL DEFAULT '' CHECK (length(album) <= 200),
  ext TEXT NOT NULL CHECK (ext IN ('.mp3','.flac','.ogg','.opus','.m4a','.wav')), size INTEGER NOT NULL CHECK (size BETWEEN 1 AND 314572800), sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  status TEXT NOT NULL DEFAULT 'offered' CHECK (status IN ('offered','accepted','receiving','declined','uploaded','failed')), error TEXT NOT NULL DEFAULT '' CHECK (length(error) <= 300),
  PRIMARY KEY (offer_id, item_no));
