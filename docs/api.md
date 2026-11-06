# Routes

Access levels: **public** (no login), **member** (any signed-in user), **admin**. Changing requests (POST) also need the
`X-CSRF-Token` header (sent by `apiFetch`) and a `Content-Length`. Errors are JSON `{"detail": "..."}`.
Not signed in: pages redirect to `/login?next=...` (303); `/api` routes return 401. Missing permission: 403.
**Admin** areas each need one permission (see `security.md`): `upload`, `collector`, `maintenance`, `status`, `settings`, `users`; the admin role holds all of them.

## Pages
| Route | Access | Notes |
|---|---|---|
| `/login` | public | Redirects home if already signed in |
| `/invite/{token}` | public | Create an account from an invite link |
| `/` | member | Landing page with the library counts |
| `/archive` | member | Artists -> albums/discography; hash routes `#/`, `#/compilations`, `#/artist/7`, `#/artist/7/album/3`, `#/album/3` |
| `/music` | member | Redirects to `/archive` |
| `/radio` | member | Nonstop radio |
| `/settings` (also `/account`) | member | Account, Appearance, Site tabs: profile, password, devices, themes; the Site tab needs the `settings` permission |
| `/admin/database` | admin | Which database runs, role checks, how to move to PostgreSQL |
| `/admin/updates` | admin | Server updates: installed version, check for a signed release, install (see `updates.md`) |
| `/download` | public | "Get the app": the visitor's installer first, then the steps for their device (data from `GET /api/downloads`) |
| `/.well-known/totr-ca.pem` | public | This server's certificate authority (public part only), for an app meeting the server for the first time; 404 when the server uses a certificate every device already trusts |
| `/upload` | `upload` | Upload songs/albums |
| `/admin` | any tab permission | Tabs: `#status` (`status`), `#maintenance` (`maintenance`), `#collector` (`collector`); people see only their tabs |
| `/admin/users` | `users` | Invites (shown as pending users), permissions, disable, sign out, delete |
| `/status`, `/discover` | `status`, `collector` | Redirect to `/admin#status` / `/admin#collector` |
| `/health` | public | `{"status": "ok"}` (200) or `{"status": "degraded"}` (503). Cheap: one `SELECT 1` |
| `/static/*` | public | CSS, JS; `static/downloads/` holds shell installers (not in git) |
| `/app/*` | public | The React listener app (built by `TOTRspa`'s `npm run publish:web`); `/app/version.json` = `{buildId, minShell, downloads, android}` (`android` = `{version, versionCode, size, sha256, url}` of the newest APK), which running apps poll to offer "Update now" and, on Android, the app update. Files only; the API is still gated. Own CSP (`script-src 'self'`, no nonce needed); `index.html` and `version.json` are re-checked every time, `assets/*` are cached for a year |

## Music (member)
| Route | Returns |
|---|---|
| `GET /api/artists` | Artists credited on at least one track: `artist_id, name, track_count, cover_album_id` |
| `GET /api/artists/{id}` | `artist_id, name` (ids are the media items' ids, so links from before the 2026-10-05 move do not work any more) |
| `GET /api/artists/{id}/albums` | Albums holding one of their tracks: `album_id, title, year, owner, their_tracks, total_tracks` |
| `GET /api/artists/{id}/discography` | Their loose songs, the page's **Singles** (oldest first) |
| `GET /api/artists/{id}/songs` | Every song credited to them, on albums or loose, A to Z: `track_id, title, album, artists` (the page's **All songs**) |
| `GET /api/compilations` | Albums credited to 4+ artists |
| `GET /api/albums/{id}` | `album_id, title, year, owner, credit` (`credit` is null unless the collector added the album; then `license, license_url, page_url, source, needs_credit, attribution`) |
| `GET /api/albums/{id}/tracks` | Tracks with joined artist names |
| `GET /api/albums/{id}/cover` | The cover image (404 if none) |
| `GET /api/tracks/{id}/audio` | Audio stream with Range support and the right content type |
| `GET /api/search?q=` | `{query, artists, albums, songs, totals}`: up to 40 per group, ranked. Ignores case, punctuation, spaces and full-width letters; every word must match, in any order |
| `GET /api/library/stats` | `{artists, albums, tracks}` |
| `GET /api/radio/next?exclude=1,2,3` | One random track `{track_id, title, album_id, artists}` |
| `GET /api/radio/media` | Collage pictures/videos: album covers + files in `MEDIA_ROOT` |
| `GET /api/radio/media/file/{path}` | A file from `MEDIA_ROOT` (path-traversal safe) |

| `POST /api/listening/ping` | `{track_id|null, state: playing|paused|stopped, source: radio|archive}`: the pages send it while audio plays (about every 30 s) so the Status tab can show who is listening. Any signed-in person. Kept in memory for 75 s |

## Mixtapes (member; each person sees and changes only their own)
Not found and not yours look the same (404). Until the mixtape tables and role exist (`sql/mixtapes.sql`) and sets `DB_MIXTAPE_*`, `GET /api/mixtapes` returns `{enabled: false, setup: "..."}` and changes return 503.
| Route | Purpose |
|---|---|
| `GET /api/mixtapes` | `{enabled, mixtapes: [{mixtape_id, name, track_count, updated_at}]}` |
| `POST /api/mixtapes` | `{name, track_ids?}` (name 1-80 chars, unique per person ignoring case; up to 200 starting songs). At most 100 mixtapes each |
| `GET /api/mixtapes/{id}` | `{mixtape_id, name, tracks: [{entry_id, track_id, title, artists, album_id}]}` in play order |
| `POST /api/mixtapes/{id}/rename` | `{name}` |
| `POST /api/mixtapes/{id}/delete` | Deletes the mixtape (the songs stay in the archive) |
| `POST /api/mixtapes/{id}/tracks` | `{track_ids: [1-200 ids]}`: appends the ones that exist, once each. At most 1000 songs per mixtape |
| `POST /api/mixtapes/{id}/tracks/{entry_id}/remove` | Removes one entry |
| `POST /api/mixtapes/{id}/tracks/{entry_id}/move` | `{direction: "up"|"down"}`: swaps with the neighbour |

## Your own app data (member; each person sees and changes only their own)
Used by the app to keep settings and "continue listening" the same on every device (`TOTRspa/TOTRspa_web/src/sync.js`). The server stores text and never interprets it. Until `sql/user_data.sql` has been run (see `database.md`) every call answers 503 and the app keeps settings on the device only.
| Endpoint | What |
|---|---|
| `GET /api/me/data` | `{items: {name: {value, updated_ms}}}` |
| `PUT /api/me/data/{name}` | `{value, updated_ms}` (value up to 30000 characters; name `^[a-z0-9][a-z0-9.-]{0,39}$`; at most 24 items each). Answers `{kept, updated_ms}`: `kept: false` means the stored copy was newer or equally new, and nothing changed |
| `POST /api/me/data/{name}/delete` | Removes one item (POST like the other deletes: the gate wants a body with every change) |

## Account (public where noted)
| Route | Access | Body / result |
|---|---|---|
| `GET /api/auth/setup` | public | `{configured, has_users}` for the login page |
| `POST /api/auth/login` | public | `{username, password}`; sets the session cookie; 401 with one generic message; 429 with `Retry-After` |
| `POST /api/auth/accept-invite` | public | `{token, username, password}`; creates the account and signs in |
| `GET /api/downloads` | public | The newest installer of each app (`apps`: platform, label, version, file, url, size, updated, note); read from `static/downloads/`; also `certificate` (`{url, fingerprint}`, or null) when this server publishes its own certificate authority. The `/download` page shows it, puts the installer for the visitor's device first and lists the steps for that device (including the certificate step when there is one). `.apk` files are served as `application/vnd.android.package-archive` |
| `GET /api/auth/me` | member | `{username, role, csrf, permissions: [...], admin_page}` (admins list every permission) |
| `GET /api/account` | member | `{username, role, created_at, last_login_at, sessions: [{id, current, created_at, last_seen, ip, user_agent}]}`; `id` is the first 16 characters of the session hash, never the cookie |
| `POST /api/account/sessions/{id}/revoke` | member | Signs out one of your other devices (404 for the current one, one that is gone, or someone else's) |
| `GET /api/admin/database` | admin | The Database page's report (read-only, no passwords) |
| `GET /api/admin/database/move` | admin | Move to PostgreSQL: `{engine, stage: idle/running/done/failed, mode, steps, percent, message, result, restart, counts, private_channel}`; never holds a password |
| `POST /api/admin/database/move/test` | admin | Body `{host, port, dbname, sslmode, roles, admin_user, admin_password}`: connects as the administrator and returns `{ok, checks: [{what, ok, why?, warn?}]}`. Refused (400) unless the connection is https or local |
| `POST /api/admin/database/move/start` | admin | The same body plus `mode: "rehearse"` or `"move"`. Starts the one-time helper (409 if one runs); poll the GET route. Same https rule |
| `POST /api/admin/database/move/dismiss` | admin | Hides the finished report (409 while running) |
| `POST /api/auth/logout` | member | Ends this session |
| `POST /api/auth/signout-all` | member | Ends every session of this user |
| `POST /api/auth/change-password` | member | `{current, new}`; other sessions are signed out |

## Join requests (public where noted)
How an app on the network asks to join. Off until an admin turns it on (Users page). Nothing is created until an admin approves.
| Endpoint | What |
|---|---|
| `GET /api/join` | Public. `{open, name}`: does this server take requests, and its site name |
| `POST /api/join` | Public. `{payload, key, sig}`: `payload` is JSON text `{v:1, name, note, platform, app_version, history, invite_hash, ts}`, `key` the install's public key (base64): 32 bytes Ed25519 (desktop) or a 65-byte uncompressed P-256 point (Android); the key's size picks the algorithm, `sig` is then a DER ECDSA signature over SHA-256, `sig` its signature over `"totr-join-request\n" + payload`. `invite_hash` is the SHA-256 of a one-time token the app keeps. Answers `{request_id}`. 5 requests per address and 3 per install per hour; 200 waiting at most; the clock must be within 10 minutes |
| `GET /api/join/{request_id}` | Public. `{status}`: `pending`, `approved` or `declined`. 404 once the row has been purged |
| `POST /api/join/{request_id}/withdraw` | Public. Removes a waiting request |
| `GET /api/admin/join` | `users` permission. `{open, requests: [...]}` with name, note, install fingerprint, platform, version, what the app reported, whether this install asked before |
| `POST /api/admin/join/open` | `{open: true|false}`. Also starts or stops the network announcement (`discovery.py`); the answer has `announcing`: `announcing`, `silent`, or why it could not |
| `POST /api/admin/join/{id}/approve` | `{permissions: [...]}` (only ones the approver holds). Writes a member invite under the app's `invite_hash`; the app then opens `/invite/<its token>` |
| `POST /api/admin/join/{id}/decline` | Nothing is created |

### Your counts, for a join request
| Endpoint | What |
|---|---|
| `GET /api/me/summary` | Member. `{since, mixtapes, songs}` (any may be missing): the day your account began, your mixtape count, the songs in this library. The app sends these to another server only if you tick the box when asking to join. Play counts are not kept per person, so there are none |

## Song offers (member unless noted)
A person offers songs they saved in the app; someone who may add music (the `upload` permission, or admin) accepts some; the owner then sends exactly those files. Until `sql/song_offers.sql` has been run, 503.
| Endpoint | What |
|---|---|
| `POST /api/songs/offers` | `{note, items: [{title, artist, album, ext, size, sha256}]}` (1 to 100 songs; ext mp3/flac/ogg/opus/m4a/wav; size up to 300 MB; 3 waiting offers per person). Answers `{offer_id, songs}` |
| `GET /api/songs/offers` | Your offers with each song's status: `offered`, `accepted`, `receiving`, `declined`, `uploaded`, `failed` (+ `error`), and who answered |
| `POST /api/songs/offers/{id}/cancel` | Withdraws an offer |
| `PUT /api/songs/offers/{id}/items/{n}` | The raw bytes of one **accepted** song. Must match the offered size and SHA-256 and read as audio; goes into the library (source `offer`, `offered_by` your name). Failed sends can be repeated |
| `GET /api/songs/requests` | Admin asks waiting for you: `{request_id, note, asked_by}` |
| `POST /api/songs/requests/{id}/accept` | `{items: [...]}` as above. Makes an offer that is already accepted (the person who asked is recorded as approving) |
| `POST /api/songs/requests/{id}/decline` | Says no |
| `GET /api/admin/offers` | `upload` permission. Waiting and recently answered offers, with who offered and every song. A song in a waiting offer that looks like one the library already has carries `library: {level: "likely"|"title", track_id, title, artists, album}` (`likely`: same title and artist; `title`: same title only; case, spacing and punctuation ignored). A hint only: the Upload page unticks the likely ones, and nothing is blocked |
| `POST /api/admin/offers/{id}/answer` | `{accept: [item numbers]}`: takes those, declines the rest. Once only |
| `GET /api/admin/requests`, `POST /api/admin/requests` | `upload` permission. List asks; ask a person `{username, note}` (5 waiting per person) |
| `POST /api/admin/requests/{id}/cancel` | Withdraws an ask |

## Admin
| Route | Purpose |
|---|---|
| `GET /api/health` | Full status: database, every audio file present, music folder writable, uploads, accounts, maintenance, radio media. The slow check: the Status tab asks every 30 s |
| `GET /api/health/live` | The fast-moving numbers (needs `status`): `listeners` (who, song, state, source, device, address), `traffic` (`bps`, `today`, `users_today`, `days`), `system` (CPU, memory, load, uptime, temperatures, drives), `library` (counts, size on disk measured in the background), `spiders`, `collector`, `problems`. Asked every 5 s while the tab is visible |
| `POST /api/upload/inspect` | Reads tags from chosen files (saves nothing) |
| `POST /api/upload` | Multipart: `artist`, `album` (blank = loose songs; each at most 300 characters), `year`, `files[]`, `cover`. A file whose name says audio but that the tag reader does not recognise as audio is refused (422, naming it) and the whole upload stores nothing. Files are copied to disk in pieces |
| `GET /api/admin/users` | Users and pending invites (never hashes or links), `permissions_enabled`, the permission names and labels |
| `POST /api/admin/invites` | `{role, permissions: [...]}` -> `{token, path, days, invite_id}`; the link is shown once. The invite is listed at once as a pending user |
| `POST /api/admin/invites/{invite_id}/permissions` | `{permissions}`: change what an unused invite starts with |
| `POST /api/admin/invites/{invite_id}/revoke` | Cancels an unused invite (its link stops working) |
| `POST /api/admin/users/{id}/permissions` | `{permissions}`: replace a member's permissions (needs the `users` permission; only ones you hold; not your own; not an admin's) |
| `POST /api/admin/users/{id}/disable` `/enable` `/signout` | Manage a user (cannot disable yourself or the last admin; only admins can touch admins) |
| `POST /api/admin/users/{id}/delete` | `{confirm_username}` must equal the username exactly. Cannot delete yourself or the last active admin. Removes the account and its sessions |

### Maintenance (spiders)
| Route | Purpose |
|---|---|
| `GET /api/maintenance` | The report plus `running`, `progress`, `actions_enabled`, `online_enabled` |
| `POST /api/maintenance/run[?only=a,b]` | Scan in the background. No `only` = quick spiders; `only=enrich` = online lookup (the schedule runs all of them) |
| `POST /api/maintenance/approve` | `{finding_id}`: runs that finding's saved action (audited) |
| `POST /api/maintenance/dismiss` | `{finding_id}`: hides it until the next scan of its spider |
| `POST /api/maintenance/ignore` | `{finding_id}`: hides it permanently (`ignored.json`) |
| `POST /api/maintenance/unignore-all` | Empties the permanent ignore list |
| `GET/POST /api/admin/settings` | Site settings. POST `{contact}` (email or https address; empty = use `.env`). Returns `{contact, contact_source: settings|env|none}` |
| `POST /api/maintenance/schedule` | `{auto_scan, interval_days, run_hour}`: the spider schedule (also returned as `schedule` by `GET /api/maintenance`) |

### Collector
| Route | Purpose |
|---|---|
| `GET /api/collector` | `config`, `sources_available`, `adds_enabled`, `searching`, `last_search`, `wanted`, `suggestions`, `jobs` (with `key` = suggestion id), `added` |
| `POST /api/collector/config` | Save settings (validated); genres are `[{name, enabled}]` |
| `POST /api/collector/search` | Search now (background) |
| `POST /api/collector/approve` | `{id}`: queue one suggestion for download |
| `POST /api/collector/approve-artist` | `{id}`: queue every suggestion by that artist; already-queued ones are skipped |
| `GET /api/collector/review-added` | Adds scoring below the minimum quality: `{min_quality, items:[{id,title,artist,source,tracks,single,at,quality,reasons,scored_when_added}]}` |
| `POST /api/collector/undo-many` | `{ids:[...]}` (1-100): undoes each add (files to `.trash`); `{results:[{id,ok,message}], undone}` |
| `POST /api/collector/dismiss` | `{id, block_artist}`: no thanks; optionally never suggest that artist |
| `POST /api/collector/undo` | `{id}`: remove an added release (files go to `.trash`) |
| `POST /api/collector/wanted/add` | `{artist, title?}` |
| `POST /api/collector/wanted/remove` | `{artist, title}` |

## Updates (admin role only)
| Route | What |
|---|---|
| `GET /api/admin/updates` | The installed version, whether updates are set up (and why not), the last check, what the web app is doing, and what the update service last reported |
| `POST /api/admin/updates/check` | Asks GitHub for the newest signed release and reports it (never an unsigned one) |
| `POST /api/admin/updates/install` | Starts fetching and verifying the newest release in the background and files the request for the update service; poll the GET route. See `updates.md` |

## Limits
Request bodies: 64 KB, except `/api/upload` (`MAX_UPLOAD_MB`). Id fields are length-limited by the request models.

Every changing request (POST, PUT, PATCH, DELETE) must carry a `Content-Length` (411 without) and must not use `Transfer-Encoding` (411: chunked bodies would have no length to check); too big is 413. A number too large for the database in a path or body (an id of 20 digits) is a 422 "That number is out of range." An error the server did not foresee is a 500 with the JSON `{"detail": "Something went wrong on the server."}` and the usual security headers; the cause is only in the log. Too many wrong invite links from one address (20 in 15 minutes) is a 429 with `Retry-After`.
