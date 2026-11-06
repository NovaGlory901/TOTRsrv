# Testing

```
cd TOTRsrv
pip install -r requirements.txt -r requirements-dev.txt     # dev adds the test client (httpx2)
python tests/run_all.py            # all suites, offline
python tests/run_all.py --live     # also a few read-only lookups on the real Internet Archive / ccMixter
python tests/test_auth.py          # one suite; prints "ok"/"BAD" per check and a final PASSED/FAILED
```
About 1,000 checks, a few minutes (the browser tests take most of it). Each suite is a plain script (no test framework): `check(name, condition)` prints `ok`/`BAD`, exit code 1 on any failure.

## What touches what
- A few checks **read** your real database through the read-only role (the real music routes in `test_live_library.py`, the search index and song lookup in
  `test_library.py`), so a working `.env` and LAN access are needed. They **never write** to it. They need a database set up from `sql/` (the views must exist);
  until then they print `skip`, which is not a pass. Run `python tests/test_live_library.py` after the migration to see them run.
- Everything else runs on throwaway **SQLite** files laid out like the PostgreSQL schemas (`tests/sqlite_layout.py`): schemas become table-name prefixes (`totr.users` is `totr_users`),
  the media ontology and TOTR's views are built there, and `add_item` / `relate` seed it. Accounts (`test_auth`, `test_collector`, `test_enrich`, `test_monitor`),
  mixtapes (`test_library`) and the whole music catalog (`test_catalog`, `test_actions`, `test_collector` add/undo). The SQL is plain and portable for this reason.
- The statements in `catalog.SQL` are additionally checked with `PREPARE` against the real schema (parses without executing; needs the migration).
- `test_guards.py` guards the layout: no DDL in the app, no bare or `public.` table names (view names too), schema-qualified fresh-database scripts, and the rules of `tools/schema_check.py`.
- No test downloads audio. Downloads are tested against a fake transport. `--live` only reads search results and headers.

## Suites
| Suite | Checks | Covers |
|---|---|---|
| `test_spiders.py` | 17 | Detectors against a library built with known defects (missing/unreadable/duplicate files, similar artists, missing info), proposals, outage handling |
| `test_guards.py` | 18 | The schema-layout guards above |
| `test_catalog.py` | 30 | The ontology rules on SQLite: match-before-create (case/width), ambiguity refused, provenance on everything TOTR creates, undo removes the album/artist TOTR made but never one something else links to, songs with a music video or foreign relation are kept, never edit or delete what TOTR did not create, hostile names are only text |
| `test_actions.py` | 38 | Every fix handler run for real on the SQLite stand-in (refusals, trash behaviour, merges, edits, the "not TOTR's" and "something links to it" rules), `PREPARE` of all catalog SQL (skipped until the migration), unmounted-drive guard |
| `test_live_library.py` | 14 | Every music read route against the real library (read-only): the only place the PostgreSQL-only route SQL runs. Skips until the migration |
| `test_auth.py` | 279 | Anonymous lockout, hashing, sessions, CSRF/Origin/size limits, invites, roles, throttling, expiry, SQL-injection strings, open redirects, headers/CSP, password changes, admin controls, maintenance as a signed-in admin |
| `test_http.py` | 21 | Retry/backoff, resume, size caps, Referer rules, JSON retry (and, with `--live`, the real ccMixter hotlink behaviour by headers only) |
| `test_collector.py` | 220 | License rules, host/redirect/DNS guards, parsing real Internet Archive and ccMixter data, Jamendo parsing, settings, scheduler, finder filters, the full add/undo/failure-cleanup pipeline, web layer |
| `test_library.py` | 106 | Archive search ranking (case, punctuation, full-width, word order, caps, hostile text), the index SQL against the real library (read-only), mixtapes on a throwaway SQLite database (create/rename/delete, privacy between people, limits, add/remove/move, validation), the real song lookup, and the archive page clicked through in a browser (search, the add-to-mixtape chooser, mixtape views) |
| `test_radio.py` | 88 | The radio's logic under Node (`tests/radio_core_test.js`: song history, band energies, beat detector) and DejaVuAmp's (`tests/dejavuamp_core_test.js`: dB maths, balance, the ten bands and 19 presets, settings validation and storage, saved presets and preset files, migration from the old 3-band settings, time and ticker text), page/script agreement, the visualizer in real WebGL (compiles, draws, moves, calm mode, stops), and the whole radio page clicked through in a browser (tune in, Prev/Next history, the rack: graph shape, speed, bands, volume, balance, mono, presets, saving a curve, the spectrum drawing, transport buttons; visuals toggle; saved settings) |
| `test_monitor.py` | 103 | CPU/memory/temperature parsers on sample data, drives, data-streamed counting (rate window, days, people, persistence, caps, the byte-counting wrapper on full, partial, streamed, errored and HEAD responses, and in the real app), the listener list (states, timeout, flood cap, devices, addresses), recent problems (redaction, limits), the endpoints and their permissions, and the Status tab clicked through in a browser (every panel, HTML in names stays text, no polling while hidden) |
| `test_visuals.py` (one browser check is flaky: "every preset puts a real picture on screen" can fail for `Spiral Lullaby` / `Kaleidoscope Nap` in software WebGL) | 30 + 198 under Node | FalseHorizon. Under Node (`tests/falsehorizon_test.js`): the equation language (precedence, every function, 25 kinds of refused input, limits, no prototype or global access, divide by zero), preset files (clamping, junk, `__proto__`, bad formulas, size), running a preset (zoom/spin/drift directions, motion scale, q numbers, persistence, all 17 built-ins for 400 frames of silence to full blast), fading between presets, the flash guard (cap, rise limit, a strobe at five speeds stays under 3 flashes a second by the WCAG rule), hearing (bands, beat rate, waveform), the preset picker, settings and migration, your own presets; and the MilkDrop importer (`tests/falsehorizon_milk_test.js`, 49 checks on sample files written for the tests: base values, formulas and comments, y-down and sound scaling, custom waves, shape instances, shaders and extra waves noted, 20 kinds of refusal incl. loops, memory arrays, bitwise, binary, too big, hostile names, worst-case speed). Source checks (nothing from outside, no eval or HTML, themed CSS). In headless Edge: the shaders, all 17 presets with real pixels, a fade, silence, calm, next/previous, stop |
| `test_localdb.py` | 77 | The built-in local database: template matches its schema, first-run copy (never overwrites), refusal of old SQLite or another layout version, the SQL translations, every music route, sign-in, sessions and the cascades on the real template, how `db.py` picks the engine and builds PostgreSQL settings, the admin Database page (permissions, no password in the report), and export/import (ids kept, hashes kept, refuses a non-empty target). Not run: a real PostgreSQL connection or import |
| `test_updates_page.py` | 23 | The admin Updates page and API through the real gate: admin-only, fields (key fingerprint, engine), the page draws text only |
| `test_move_to_postgres.py` | 88 | Move to PostgreSQL without a PostgreSQL server: request checks, `.env` rewrite and restore, setting the SQLite file aside, secrets never output, copy and counts (SQLite to SQLite), the API with a stand-in helper (https rule, pause, progress, failure, restart); the real server part is `deploy/lab-test.sh sqlite move` |
| `test_deezer.py` | 25 | The no-contact lookup source: Deezer client on real recorded answers, exact matching, the same-name rule, quota errors not cached, the spider running with no contact, host allow-list |
| `test_settings.py` | 32 + 58 in Node | Themes (`theme_test.js`: built-ins readable, validation refuses hostile input, apply, per-person storage, import/export, 12-theme cap, blocked storage), the stylesheet has no unthemed colors, the Settings page, `/api/account` and signing out one device (CSRF, other people's devices, junk ids) |
| `test_enrich.py` | 141 | MusicBrainz client on real recorded answers and its cache, the gap-filling spider, the runner's merging, `add_wanted`/`fetch_cover`, wanted list, wanted-searches, old-file migration, Admin page, dismiss vs ignore, spider schedule, site settings (contact) |
| `test_app_hosting.py` | 12 | The listener app is served at `/app/` without sign-in with its own headers; the API stays locked |
| `test_foundations.py` | 13 | The server has a version, listen settings come from HOST/PORT, the page footer names the admin's site |
| `test_userdata.py` | 33 | The per-person data API (settings and listening position that follow a person between devices): privacy between people, limits |
| `test_tls.py` | 24 | HTTPS settings, the certificate tool (a name-constrained private authority) and a real TLS handshake |
| `test_tlsid.py` | 23 | The server's public certificate authority and its fingerprint (`GET /.well-known/totr-ca.pem`), and the code an admin's invite link carries |
| `test_downloads.py` | 15 | The download page: the newest installer of each app is found; the page and its list are public |
| `test_vendor.py` | 13 | The DejaVuAmp and FalseHorizons scripts in `static/` are copies of the originals; the lock file catches drift |
| `test_migrate.py` | 34 | Database versions and upgrades (`migrate.py`): SQLite always, PostgreSQL on a throwaway cluster when its tools are installed (skipped otherwise) |
| `test_paths.py` | 5 | Where TOTR keeps what is not code (next to the code, or one state folder with `TOTR_STATE_DIR`) |
| `test_installer.py` | 42 | The installer's logic that needs no root and no systemd, and the first-admin link |
| `test_media_schema.py` | 38 | The `media` schema is identical in every install (checked against the reference dump) |
| `test_updates.py` | 106 | Signed updates, web-app half: release format and signature check, safe unpacking, the developer's release tool, finding and downloading |
| `test_apply_update.py` | 34 | The update service: installing a verified release, bringing the database forward, switching versions, going back |
| `test_sql_audit.py` | 40 | SQL injection guard: every query built from pieces of text is listed with what it puts in |
| `test_file_serving.py` | 15 | Path traversal on the route that serves media files by name |
| `test_fuzz.py` | 11 | Junk in, clean refusal out: thousands of random and mangled inputs to the parsers that take untrusted input |
| `test_fuzz_media.py` | 3 | Damaged songs in, a clean answer out (not in `run_all.py`; run it by hand: `python tests/test_fuzz_media.py`) |
| `test_join.py` | 93 | Join requests: a signed request (Ed25519 and P-256), the admin's answer, what is and is not left behind |
| `test_discovery.py` | 22 | LAN announcement: only while joining is open (logic tested with a stand-in for the network) |
| `test_offers.py` | 77 | Song offers: offering, accepting, sending files, admin asks, the checks in between |
| `test_hardening.py` | 64 | The holes the server break-it run found (2026-10-15): a chunked body refused, unhandled errors and out-of-range numbers, uploads (long names, files that are not songs, copied in pieces, spooled on disk), a made-up invite link costing nothing and limited per address, invite links hidden in the log, HSTS and cookie flags, the statements that decide an invite race, the disabled-account lookup, the move helper's error text, no markup sinks in our pages. Every check was mutation-checked |

Counts are for a machine with a real library, Node and a browser; elsewhere those checks print `skip` and the number is lower (`test_library` and `test_live_library` fail without the library, and `test_radio`, `test_monitor` skip their browser checks).

Node and a browser are optional: without them the radio's Node tests and all browser tests print `skip` instead of passing. Install Node and Edge/Chromium on your development machine to run everything.

## Fixtures (`tests/fixtures/`)
Real answers saved from the live services (read-only requests): `ia_SCL160.json` (an Internet Archive item), `ccm_chiptune.json` (ccMixter uploads, including
Sampling Plus ones), `mb_artist_search.json`, `mb_release_groups.json`, `mb_releases.json` (MusicBrainz, for the artist "slowerpace 音楽"). Refresh them by repeating the
request with a descriptive User-Agent, at most one request per second.

## Mutation-checking a test
A test that cannot fail proves nothing. After writing one, undo the protection it names in the code (a `False` in front of the condition, a removed line), run the suite, see it fail, put the protection back and check `git diff` shows only what you meant. The 2026-10-15 audit (about 125 changes, listed in `threat-model.md`) found nine tests that did not fail when they should: a test that resets the limits to its own numbers, a check that sits behind a cheaper one, a header nobody asked for. Test the *innermost* layer directly as well as the outer one, and read the numbers from the code, not from the test.

## Conventions when adding tests
- Put the test in the suite for that area; add a new `tests/test_<area>.py` and list it in `run_all.py` only if nothing fits.
- Test **behaviour at the boundary**: refusals, cleanup after failure, hostile input, "what if the drive is unmounted".
- Use the helpers already there (`tests/sqlite_layout.py` for the SQLite database layout, connection and seeding, `script()` for the HTTP layer).
- Never point a test at the production database for writes, and never put a real password in a test.
- When a check fails, decide whether the test or the code is wrong. Several real bugs were found this way; fix the code when the test is right.

## Browser checks (manual, headless Edge)
Used for CSP and rendering, not part of `run_all.py`: load each page signed in (a throwaway test server with a SQLite account database and a test-only login route),
capture console output, grep for "Content Security Policy" / "Uncaught", and look at a screenshot. See `frontend.md`.

## The Android app's own tests (JUnit, on a PC)
`TOTRspa/TOTRspa_apk/android/app/src/test/java/org/totr/app/`: `AccessListTest`, `PinsTest`, `PinnedTlsTest`, `JoinLinkTest`, `DiscoverTest`, `JoinRequestTest` (64 + 14 checks). The classes under test are plain Java with no Android classes, so they run without a phone. `gradlew testDebugUnitTest` cannot run offline (Mockito is not cached), so run them directly with the JUnit jars that ship in the Gradle distribution:
```
cd TOTRspa/TOTRspa_apk/android/app/src
G=~/.gradle/wrapper/dists/gradle-*/*/gradle-*/lib; CP=$(ls $G/junit-4.13.2.jar):$(ls $G/hamcrest-core-1.3.jar); B=$(mktemp -d)
javac -d $B -cp $CP main/java/org/totr/app/{AccessList,Pins,JoinLink,PinnedTls,Discover,JoinRequest}.java test/java/org/totr/app/*Test.java
java -cp $B:test/resources:$CP org.junit.runner.JUnitCore org.totr.app.{AccessList,Pins,JoinLink,PinnedTls,Discover,JoinRequest}Test
```
(skip `ExampleUnitTest`, which needs the Android stubs). `test/resources/pins/*.pem` are real public certificates made by `test/make-pin-fixtures.sh`; running that script again changes the authority's fingerprint, which `PinsTest.CA_CODE` then has to follow (compute it with `python -c "import tlsid,pathlib; print(tlsid.fingerprint(pathlib.Path('.../ca.pem').read_bytes()))"` in `TOTRsrv`). Parts that need a phone (NsdManager, the key store, the WebView's certificate hook) are checked by hand; the web side of the same calls is `TOTRspa_web/tests/native.test.mjs` (`npm test`).
