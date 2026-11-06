# Database

PostgreSQL. The server can share an existing, hand-made PostgreSQL database that holds a music library: **it is your existing database, not TOTR's**, and TOTR is one tenant of it (its own `totr` schema).
The app never creates or alters tables, schemas or roles in any database: the owner runs the scripts in `TOTRsrv/sql/`.
The one thing it does for people without PostgreSQL is **copy a ready-made SQLite file** (see "The built-in local database" below): copying a file, not DDL.

## Schema layout (the rule)
| Schema | Whose | Holds |
|---|---|---|
| `media` | the existing media ontology | `items`, `item_types`, `item_relations`, `relation_types`. **The music catalog lives here**, as items and relations |
| `totr` | TOTR | accounts, sessions, invites, mixtapes, and TOTR's helper views over `media` |
| `public` | a sandbox schema, if the database has one | **nothing of TOTR's, ever** (only extension functions) |
| `ag_catalog`, `demo_graph` | Apache AGE, if installed | never touched |

**New tables must fit this layout.** TOTR data goes in `totr`; anything that is a media thing is an item in `media` with a generic type and typed relations,
never a new TOTR-only table. Every script in `sql/` and every statement in the code names its schema (`totr.users`, `media.items`); a test (`tests/test_guards.py`)
fails on bare names, on `public.`, and on any DDL in the app. `python tools/schema_check.py` (read-only) lists anything in the database that is not in this layout.

## Rules for TOTR inside the ontology
1. **One real thing is one item.** An artist is a person/group item that other data in the database can link to. Before creating one, match it against
   existing items (normalised name; `catalog.get_or_create_artist`). Two matches are an error shown to the owner, never a guess.
2. **Generic types only** (`song`, `album`, `artist`; relations `track_on_album`, `performed_by`, `album_by`, `is_music_video_for`). New types are proposed to
   the owner first, never app-specific ones.
3. **Facts become relations** where possible; **provenance and outside ids go in `metadata`**: `source` (`upload`, `collector`, `library-import`, `legacy-public`),
   `created_by_app = "totr"`, and later `external_ids` (MusicBrainz MBID, Wikidata QID).
4. **Keep `media` pure.** TOTR's views live in `totr`.
5. **Hands off what TOTR did not create.** TOTR edits or deletes only items carrying `created_by_app = "totr"`, only relations of its own three types leaving such
   items, and never an item that something else links to (a music video, a place, a job). It never alters `media`'s structure, and does deletes in code
   (`catalog.py`), never with `ON DELETE CASCADE` on `item_relations`.

## How music is stored (in `media`)
| Thing | Item type | Notes |
|---|---|---|
| Artist | `artist` | `title` = name |
| Album | `album` | `metadata`: `year`, `cover_art_path`; `album_by` -> the owner artist it is filed under |
| Song | `song` | `metadata`: `file_path` (absolute path on the server, the source of truth for where a file is), `track_number`, `released` (ISO date, optional); `track_on_album` -> its album (none = a loose song); `performed_by` -> each credited artist |

- The album **owner** (`album_by`) is who it is filed under; the artists who performed are the **credits** (`performed_by`). The site browses by credit. A
  compilation's owner (for example "MixTapes") may be credited on nothing.
- Every item TOTR created also carries `source_table` / `source_id` only if it came from the old flat tables (the legacy id, kept so things can be traced).
  Old links like `#/artist/7` use the old ids and no longer work; ids are now `item_id`s.
- Songs without a `file_path` are not TOTR's to list: `totr.v_tracks` only shows playable songs.

## The views (`totr`, in `sql/totr_views.sql`)
```
v_artists        (artist_id, name, totr_created)
v_albums         (album_id, artist_id [the owner], title, year, cover_art_path, totr_created)
v_tracks         (track_id, album_id, track_number, title, file_path, released, totr_created)
v_track_artists  (track_id, artist_id)
```
ids are `item_id`s. Values are read out of `metadata` with `->>`, and malformed ones come out NULL instead of breaking a query. `totr_created` is true for
items TOTR made. All reads in the app go through these views, so the queries read like the old table queries. A view has no primary key, so a `GROUP BY`
over one must list every selected column.

## Account tables (`totr`; `sql/auth_schema.sql` for a fresh database)
```
users     (user_id, username CHECK ^[A-Za-z0-9_.-]{3,32}$, password_hash, role member|admin, disabled, permissions, created_at, last_login_at)
          unique index on lower(username)
sessions  (token_hash PK = sha256 of cookie, user_id -> users ON DELETE CASCADE, csrf_token, created_at, last_seen, expires_at, ip, user_agent)
invites   (token_hash PK = sha256 of link token, role, permissions, created_by, created_at, expires_at, used_at, used_by)
```
`permissions` is comma-separated names. Deleting a user cascades to their sessions; invites keep running with `created_by` set to null. An invite is named
by the first 16 characters of its token hash. Only hashes of secrets are stored. No role except `totr_auth` can read them.

## Mixtape tables (`totr`; `sql/mixtapes.sql`)
```
mixtapes        (mixtape_id, user_id -> users ON DELETE CASCADE, name 1-80 chars, created_at, updated_at)   unique on (user_id, lower(name))
mixtape_tracks  (entry_id, mixtape_id -> mixtapes ON DELETE CASCADE, track_id -> media.items(item_id) ON DELETE CASCADE, position, added_at)
```
Private to the owner (every query is limited to the signed-in user's id). Mixtapes stay relational (ordered and private). Deleting a user deletes their
mixtapes; deleting a song item removes it from every mixtape (the cascade is on this table's foreign key only). Song titles are read with the read-only
role, so `totr_mixtapes` has no access to `media`.

## Per-person app data (`totr.user_data`; `sql/user_data.sql`, added 2026-10-08)
```
user_data  (user_id -> users ON DELETE CASCADE, name, value, updated_ms, saved_at)   primary key (user_id, name)
```
Settings and "continue listening" that follow a person between devices (themes, recently played, where they stopped, DejaVuAmp and FalseHorizon settings). `name` is a short lowercase word (`^[a-z0-9][a-z0-9.-]{0,39}$`), `value` is text of up to 30000 characters that the server stores and returns without reading it, `updated_ms` is when the device made the change (a save older than the stored one is ignored). At most 24 items per person. No new role: `totr_auth` gets `SELECT, INSERT, UPDATE, DELETE` on it. The file is idempotent and ends with a check query. Before it is run, `/api/me/data` answers 503 and the app keeps its settings on each device. The local (SQLite) layout has it from layout version 2; a version-1 file still works the same way (503 until a new one is made).

## Join requests (`totr.join_requests`; `sql/join_requests.sql`, added 2026-10-12)
```
join_requests  (request_id, display_name, note, device_key, platform, app_version, history, invite_hash, status, ip, created_at, answered_at, answered_by -> users ON DELETE SET NULL)
```
People who found this server from their TOTR app and asked to join. A row is a question for the admin, not an account. `invite_hash` is the SHA-256 of a one-time token the asking app made up
itself; approving writes it as an ordinary invite, so no secret is stored or sent. `device_key` is the app install's Ed25519 public key as sent (44 characters), or, for an Android install (P-256), the base64 of the SHA-256 of its public point (also 44 characters, so no schema change). Answered rows are purged after 7 days, waiting ones after 14.
The table is not part of the export for "Move to PostgreSQL" (it is a short-lived queue, not data to keep). An existing database: run `sql/join_requests.sql` once; until then the join endpoints answer 503.

## Song offers (`totr.song_offers`, `song_offer_items`, `song_requests`; `sql/song_offers.sql`, added 2026-10-12)
```
song_requests    (request_id, user_id -> users CASCADE, asked_by -> users SET NULL, asked_by_name, note, status pending|accepted|declined|cancelled, offer_id, created_at, answered_at)
song_offers      (offer_id, user_id -> users CASCADE, request_id -> song_requests SET NULL, note, status pending|answered|cancelled, created_at, answered_at, answered_by -> users SET NULL, answered_by_name)
song_offer_items (offer_id -> song_offers CASCADE, item_no, title, artist, album, ext, size, sha256, status offered|accepted|receiving|declined|uploaded|failed, error)   primary key (offer_id, item_no)
```
Song offers: a person offers songs they saved in the app (or says yes to an admin's ask); someone who may add music accepts some; the owner then sends exactly those files. The items are the list of what was
offered so what arrives can be checked (size and SHA-256). `*_by_name` keeps who decided even if that account is deleted. Answered rows are purged after 30 days. Not part of the PostgreSQL move
export (a short-lived queue). An existing database: run `sql/song_offers.sql` once; until then the endpoints answer 503.

## Version bookkeeping (`totr.schema_version`)
`migrate.py` records which numbered upgrades a database has in `totr.schema_version` (`version`, `name`, `applied_at`; `totr_schema_version` on SQLite). A database made by the installer has it; a hand-made database gets it
only if the owner runs `migrate.py adopt`. `tools/schema_check.py` accepts it when present and does not require it.

## Roles
| Role | Created by | On `totr` | On `media` |
|---|---|---|---|
| `robots` | `robots_role.sql` (fresh); already there in an existing database | USAGE; `SELECT` on the four views | none |
| `totr_auth` | `auth_schema.sql`, `user_data.sql`, `join_requests.sql`, `song_offers.sql` | USAGE; `SELECT, INSERT, UPDATE, DELETE` on `users`, `sessions`, `invites`, `user_data`, `join_requests`, `song_requests`, `song_offers`, `song_offer_items`; the `users` sequence | none |
| `totr_mixtapes` | `mixtapes.sql` | USAGE; `SELECT, INSERT, UPDATE, DELETE` on the mixtape tables; their sequences | none |
| `totr_uploader` | `upload_role.sql` | USAGE; `SELECT` on the views | USAGE; `SELECT` on both type tables; `SELECT, INSERT` on `items`, `item_relations`; sequences |
| `totr_maint` | `maintenance_role.sql` | USAGE; `SELECT` on the views | USAGE; `SELECT` on the type tables; `SELECT, INSERT, UPDATE, DELETE` on `items`, `item_relations`; sequences. **No DDL** |

A table privilege cannot see `metadata`, so "only what TOTR created" is enforced in `catalog.py` and tested, not by the database. (Option, if the owner wants the
database to enforce it: `SECURITY DEFINER` functions for deletes.) `totr_uploader`/`totr_maint` can read `media.items`, which may later hold other media; they
cannot reach other schemas.

## Connection helpers (`db.py`)
| Function | Role | Used by |
|---|---|---|
| `get_conn()` | `robots` | All music routes, spiders' scans, the collector's "what do I own" check |
| `get_write_conn()` | `totr_uploader` | `uploader.save_album` |
| `get_maint_conn()` | `totr_maint` | `spiders/actions.py`, `collector/acquire.py` |
| `get_auth_conn()` | `totr_auth` | `auth/service.py`, `auth/cli.py`, `userdata/web.py` |
| `get_mixtape_conn()` | `totr_mixtapes` | `mixtapes/web.py` |

If `DB_HOST` starts with `/` it is a socket and SSL is not used; otherwise `sslmode=require` (override with `DB_SSLMODE`; `DB_PORT` is optional).
`db.engine()` picks the database: PostgreSQL when `DB_HOST` is set (or `DB_ENGINE=postgres`), otherwise the built-in local one. With `DB_SINGLE_ROLE=true`
every function above uses the one `DB_USER`. In local mode every feature is on and there are no roles.

## The built-in local database (`localdb.py`, `sql/sqlite/`)
For people who run TOTR without their own PostgreSQL. Same layout as above in one SQLite file: `totr.x` is the table `totr_x`, `media.x` is `media_x`
(`migrations/sqlite/`). `tools/build_local_template.py` runs the migrations to make `sql/sqlite/totr_template.db` (commit it; a test fails if it differs);
on first use `localdb.ensure()` copies the template to `data/totr.db` and never touches an existing file. The layout version is `PRAGMA user_version`
(`localdb.SCHEMA_VERSION`, the number of the newest migration); a file with another version is refused. `localdb.Conn` looks like psycopg's connection to the rest of the app and translates
`schema.name`, `= ANY(%s::int[])` and `string_agg` (also `string_agg(DISTINCT x, ', ')`). Differences from PostgreSQL: no roles (one connection does everything),
foreign keys and cascades are real (`PRAGMA foreign_keys`), no regex `CHECK`s (the app validates), no advisory lock (SQLite has one writer at a time), times are text.
The admin **Database** page (`dbadmin.py`, admin role only) shows which engine runs, the file and versions, or for PostgreSQL each role's connection and rights;
the report never shows passwords.

**Move to PostgreSQL** (the same page, built-in database only; `dbmove.py` + `tools/move_to_postgres.py`). Steps: start, where to (server, port, new database name,
encryption, one role or several, the database administrator's account and password), *Test the connection*, ready (the counts that will be copied), then *Copy
everything* (or *Rehearse first*, which does everything in a scratch database, counts, and removes it). The server never changes the layout itself, so it starts a
one-time helper and hands it the form on its standard input: the administrator's password is in no argument, environment variable, file or log, and the page
refuses to send it unless the connection is https (or on the server itself). The helper: makes the database (or uses an empty one; never one that already holds
`totr` or `media` tables), builds the layout with `migrate.upgrade` (the same `media` and `totr` layout as every TOTR), makes the login roles
(`<database>_read`, `_write`, `_maint`, `_auth`, `_mixtape`, or `<database>_app`) with random passwords, grants them what each purpose needs, copies everything
with `tools/migrate_db.py` in one transaction (accounts, playlists, each person's synced settings, the music; **not** sessions, so everyone signs in again),
counts both sides, logs in as each new role to prove it works, checks the built-in file did not change meanwhile, and only then writes the new settings to `.env`
(owner-only; the replaced lines are kept in `.env.before-postgres`) and sets the SQLite file aside as `totr.db.before-postgres`. Anything wrong before that point removes
what the run made and leaves `.env` and the SQLite file exactly as they were. While it copies, the gate refuses every request that changes something (`moving.py`) and
the collector and spiders do not start. Afterwards TOTR restarts itself (under systemd; otherwise it asks you to). The music files are not moved. Test it for real with
`sudo ./deploy/lab-test.sh sqlite move`.

## Conventions
- All values are bound parameters (`%s`). No string-built SQL. Schema, view, type and relation names in f-strings come only from constants.
- Writes to the catalog go through `catalog.py` (statements in `catalog.SQL`, so tests can `PREPARE` them against the real schema); nothing else writes music.
- SQL stays plain and portable so the tests run it on SQLite (`tests/sqlite_layout.py` maps `totr.x` to `totr_x`): times are passed from Python, metadata is read with
  `->>` and `CAST(... AS integer)`, never `::int`, and arrays only where a route is PostgreSQL-only (and then covered by `tests/test_live_library.py`).
- Upserts: there is no unique index on names in `media` (it is an existing table), so "get or create" is a lookup then an insert inside one transaction under
  `pg_advisory_xact_lock`.
- Transactions: `with conn:` commits on success and rolls back on any exception.

## Versions and upgrades (`migrate.py`, `migrations/`)
The layout of the database is a numbered list of upgrade files, `migrations/sqlite/NNNN_name.sql` and `migrations/postgres/NNNN_name.sql` (same numbers and names;
0001 is the whole base layout, 0002 `user_data`, 0003 `join_requests`, 0004 `song_offers`). Each database records the ones it has in `schema_version` (`totr.schema_version`, or `totr_schema_version` on SQLite).
`python migrate.py status | up | adopt | grants`:
- **up** applies the missing upgrades, each in its own transaction (a failed one is undone completely). Idempotent SQL, so running one twice is harmless.
- **adopt** records a hand-made database as being at the version it really has, after checking the tables are there. It changes nothing else.
- **grants** gives each role in `.env` the access it needs (`migrate.apply_grants`; the same rights the old role scripts gave; with `DB_SINGLE_ROLE` one role gets the sum).
- The migrations contain no role names and no passwords. Roles are made by the installer (`migrate.create_role`).
- Only an admin, the installer or the updater runs `migrate.py`; it asks for a database account that may create tables (`DB_ADMIN_USER` / `DB_ADMIN_PASSWORD`, used for that run only).
  The running server never changes the layout: at start it reads the version (`migrate.check_database`) and **refuses to start on a database newer than itself**; a database
  that is behind, or not versioned yet, only logs a warning. An older SQLite file that has only `PRAGMA user_version` is recognised: `up` records its versions.
- `tests/test_migrate.py` checks all of this on SQLite and, when PostgreSQL's tools are installed, on a throwaway cluster, including that the migrations make the same
  tables, views and grants as the old scripts below.

## The `media` schema is the same everywhere
`media` is a fixed ontology design and every install uses it unchanged: `sql/reference/media_schema_reference.sql` is the `pg_dump --schema-only` of it (the reference). The migration
(`migrations/postgres/0001_base.sql`) and `sql/media_ontology.sql` reproduce it exactly: identity ids, `data_id`, `created_at`/`updated_at` with the `media.set_updated_at()` triggers, the generated
`title_search`, the same constraint, index and sequence names, the same five item types and four relation types. `tests/test_media_schema.py` fails if they drift, and `deploy/lab-test.sh` dumps
what the installer built on a real PostgreSQL and compares it with the reference. Never trim or extend it for TOTR's sake; TOTR's own tables go in `totr`.
- The two `pg_bigm` title indexes need that extension: they are made when it is installed (or the installing account can add it) and skipped with a notice otherwise. TOTR does not use them.
- The ids are `GENERATED ALWAYS`: insert without an id, or add `OVERRIDING SYSTEM VALUE` (as `tools/migrate_db.py` does on PostgreSQL).
- The built-in SQLite copy has the same columns, constraints, indexes, triggers and seed rows; only what SQLite cannot do differs (ids are AUTOINCREMENT, uuid and timestamps are text, `title_search` is
  `lower(title)` without Unicode normalisation, `set_updated_at()` is a trigger per table, no `pg_bigm`).

## Fresh databases (not an existing hand-made one)
Use `migrate.py up` then `migrate.py grants`. The older hand-run scripts in `sql/` still work and are what the tests compare the migrations with; the order was: `media_ontology.sql`, `auth_schema.sql`, `totr_views.sql`, `robots_role.sql`, `mixtapes.sql`,
`upload_role.sql`, `maintenance_role.sql` (and `user_permissions.sql` only for a database made before permissions existed). They create the same layout as an existing hand-made database
(the `media` schema, identical, plus `totr`), so TOTR keeps one set of queries. An existing database stays under its own management.

## Changing the schema
Add the next numbered file to **both** `migrations/postgres/` and `migrations/sqlite/` (same number and name; idempotent; no role names or passwords; new tables get their
grants in `migrate.grant_statements`), rebuild the template (`python tools/build_local_template.py`), and run `python tests/test_migrate.py`. `localdb.SCHEMA_VERSION` follows
the newest SQLite file by itself. On an existing hand-made database the owner runs the new Postgres file by hand (after a backup, as with any change to a database), then `python migrate.py adopt` or `up`.
Do not run DDL from the app or from a session. New tables must fit the layout above.
