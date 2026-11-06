# Maintenance spiders

Spiders read the library and **propose** fixes. Nothing changes until an admin approves it on **Admin -> Maintenance**.

## Running
- **Scan now** (button, or `python -m spiders.run`): the quick spiders: `files`, `duplicates`, `metadata`. Read-only, uses the `robots` role, takes seconds.
- **Online lookup** (`python -m spiders.run --only enrich`, or `POST /api/maintenance/run?only=enrich`): the `enrich` spider. Slow (a few minutes), needs no contact: it uses Deezer's public catalogue (`spiders/deezer.py`), or MusicBrainz if a contact is set (Account page or `COLLECTOR_CONTACT`). It has no button; the schedule runs it.
- `--only a,b` runs a subset. Running some spiders **keeps the earlier findings of the ones that did not run**.
- Output: `reports/maintenance.json` (path via `MAINT_REPORT`), `ignored.json`, `actions.log`, `spider_schedule.json` beside it.
- **Schedule** (`spiders/scheduler.py`, set on the Maintenance tab, default nightly at 3 am): runs **all** spiders, `enrich` included, in the background. Checked every 10 minutes; due when the oldest spider's last run is over `interval_days` (minus an hour of slack) old and the local hour equals `run_hour`. Without a contact the `enrich` part reports `enrich_needs_contact` instead of running.

## The checks

### `files`: does the disk match the database?
| Kind | Severity | Proposed fix |
|---|---|---|
| `library_unavailable` | error | none. The music folder is missing, or over half the songs are gone: looks like an unmounted drive, so **no per-song fixes are proposed** |
| `missing_file` | error | `remove_missing_track`: delete that song's row (refused if the file is back or the library is not mounted) |
| `unreadable_audio` | error | none |
| `orphan_file` | info | `import_track`: add the file using its tags (only if it has an artist tag) |
| `missing_cover` | warn | `set_cover`: use a cover image found next to the songs |

### `duplicates`
| Kind | Severity | Proposed fix |
|---|---|---|
| `duplicate_path` | error | `remove_tracks`: keep the oldest row; the shared file is kept |
| `duplicate_content` | warn | `remove_tracks`: keep one copy, move the other identical files to `.trash` (hashes only files with equal sizes) |
| `duplicate_song` | warn | same, with a warning that these may be different versions |
| `similar_artist` | warn | `merge_artists`: fold names that differ only by case/width/spacing/punctuation into the one with most songs |
| `combined_credit` | info | none (may be a real collaboration) |

### `metadata`
| Kind | Severity | Proposed fix |
|---|---|---|
| `track_without_artist` | error | none |
| `missing_track_number` | info | `set_track_number` from the file's tag |
| `tag_mismatch` | info | `set_title` from the file's tag |
| `missing_year` | info | `set_year` from the file's tags |
| `artist_without_songs` | info | none (often a label-like album owner) |

### `enrich`: fills gaps from the internet (MusicBrainz + Cover Art Archive)
For each artist with credited songs it finds the MusicBrainz artist (**exact name or alias match only**, score >= 90), lists their releases,
and compares with what you own.

| Kind | Meaning | Proposed fix |
|---|---|---|
| `online_album_year` | An album of yours with no year has a known release year | `set_year` |
| `online_track_number` | A song with no track number has one online (single-disc albums only) | `set_track_number` |
| `online_missing_tracks` | "You have 8 of 10 songs on X. Missing: A, B" | `add_wanted` for that album |
| `online_missing_releases` | Releases (albums, EPs, singles; not live/demo/interview) you do not have at all | `add_wanted` for up to 15 titles |
| `online_cover` | The Cover Art Archive has a front cover and yours is missing | `fetch_cover` |
| `online_unmatched` | Artists that could not be found (too obscure) | none |
| `enrich_stopped` | Stopped after 5 consecutive errors | none |

Matching ignores case, width and punctuation but is otherwise exact, so a differently titled album ("BSOD (Deluxe)") is not matched and gets no
facts guessed onto it. MusicBrainz is asked at most once per second, answers are cached for 30 days in `reports/musicbrainz_cache.json`.

Real example (a real library, artist "slowerpace 音楽"): 71 releases online; found covers for 3 albums, years for 2, "8 of 10 songs on ENCARDIA '99",
and 68 releases not owned.

## Actions (`spiders/actions.py`)
Ten handlers, each takes `(conn, params, after_commit_hooks)`:
`remove_missing_track`, `remove_tracks`, `import_track`, `set_cover`, `merge_artists`, `set_title`, `set_year`, `set_track_number`,
`add_wanted` (no database write; updates the collector's wanted list), `fetch_cover` (downloads an image, verifies it is a JPEG/PNG by its first bytes, never overwrites an existing cover).

Rules: re-check preconditions at approval time; run DB changes in one transaction on `totr_maint`; move files to `.trash` only after the commit; refuse
when the library is unmounted; never accept parameters from the browser.

## Approval flow
1. The report stores each finding with a stable id (`kind-<hash of spider, kind, refs>`) and its `action`.
2. The page shows buttons. `POST /api/maintenance/approve {finding_id}` looks the finding up **server-side** and runs its saved action.
3. Outcome is written to `actions.log` (who, what, params, result); the finding leaves the report. Refusals and failures are logged too and the finding stays.
4. `Dismiss until next scan` just removes the finding from the saved report. The next scan of its spider brings it back if the problem is still there. `Ignore for good` adds the id to `ignored.json`, so scans skip it; `Stop ignoring everything` empties that list. (The old `dismissed.json` is no longer read.)

## Adding a spider
1. Create `spiders/<name>.py` with `NAME = "<name>"` and `check(snap) -> [Finding]` (use `propose(...)` for fixes). Set `SLOW = True` if it is slow or uses the internet.
2. Add it to `SPIDERS` in `spiders/run.py`; add labels for its kinds in `templates/admin.html` (`KIND`).
3. New fix types: add a handler and any SQL to `spiders/actions.py` (put statements in `SQL`), register it in `HANDLERS`, and add tests.
