# Collector

Finds music that is **legally free to download** in the genres you choose and suggests it. **Nothing is downloaded until an admin presses
"Add to library".** Lives on **Admin -> Collector**; code in `collector/`.

## What counts as "legally free"
Only a release whose own structured data carries a license URL that matches (`collector/licenses.py`):
- Creative Commons `by`, `by-sa`, `by-nc`, `by-nc-sa`, `by-nd`, `by-nc-nd` (any version), or
- CC0 (`publicdomain/zero/1.0`), or the Public Domain Mark (`publicdomain/mark/1.0`).

Everything else is rejected: missing data, free text, look-alike URLs, and deprecated licenses such as **Sampling Plus**.
The license is checked when a release is found **and again when you approve it**. "Free to stream" is never treated as free to download.

Duties that come with the licenses: most require **crediting the artist** (the credit line is stored with each add and shown in "Recently added"),
and the `NC` ones forbid commercial use, so keep the library private or non-commercial.

## Sources (`collector/sources/`)
| Source | Key needed | Finds | Notes |
|---|---|---|---|
| Internet Archive | no | Albums/netlabel releases (`subject` = genre, CC license) | **Main source.** Netlabel collection searched first, then anything else with a free license to fill the quota. Sorted by popularity. One audio file per song, MP3 preferred. Album titles are cleaned of `[cat123]` and `Artist -` clutter. Downloads, reviews, rating and collections are kept for the quality score |
| ccMixter | no | Single songs (`tags` = genre) | **Off by default** (mostly stock-style single tracks; the code stays, tick it to use it). Downloads need a `Referer` from the release's own page (hotlink protection); the collector sends exactly that, with an honest User-Agent |
| Jamendo | `JAMENDO_CLIENT_ID` | Albums/songs marked downloadable | Skipped without a key. **Written to the documented API; never run live.** |

Each source module exposes `NAME`, `search(genre, limit, max_bytes, skip)` and optionally `search_wanted(artist, title, limit, max_bytes, skip)`.
Internet Archive and Jamendo support wanted-searches; ccMixter does not.

## Quality score (`collector/quality.py`)
The legal sources hold a lot of stock filler, and the point of the Collector is real music by real artists. Every release gets a **0-100 score** with the reasons
shown on its card. Starts at 10, then adds or subtracts:

| Signal | Points |
|---|---|
| Downloads at the source (Internet Archive): 50 / 200 / 1,000 / 10,000+ | +4 / +8 / +14 / +20 |
| Reviews (+5 each, up to +10); average rating 4 or more | +10 max; +5 |
| In the Internet Archive `netlabels` collection | +15 |
| Release structure: 4+ songs / 2-3 songs / single | +15 / +8 / 0 |
| A single under 1.5 MB (probably a loop or clip) | -10 |
| Has cover art / has a year | +3 / +2 |
| Title, artist, tags or description contains stock wording (`royalty free`, `background music`, `no copyright`, `stock music`, `for youtube`, `sfx`, `sample pack`, ... see `BLOCKLIST`) | -40 |
| In a spoken-word or non-music collection (podcasts, audiobooks, ...) | -30 |
| Artist found on MusicBrainz with 1-3 / 4+ real releases (exact name match) | +12 / +18 |

The **minimum score** (Collector tab, default 25, 0 = suggest everything) decides what is suggested. Wanted-list items ignore the minimum (you asked for them) but still show a score.
The MusicBrainz check needs a contact (Account page), costs two cached requests per new artist, only runs when it could change the outcome, and is limited to 40 artists per search
and three errors in a row. Listener counts from ListenBrainz are not used yet. The score judges worth, never legality: the license check is separate and unchanged.

**Clean up earlier adds:** the Collector tab's "Clean up low-quality adds" lists adds below the minimum. New adds keep the score they had when added. Older adds were never scored, so they are
scored from the record (title, artist, song count, source, no online checks): expect old ccMixter singles to show up. Tick some or all and "Undo selected" (`/api/collector/undo-many`, up to 100 at a time)
uses the normal undo: rows deleted, files moved to `.trash`.

## Genres and the wanted list
- **Genres:** a remembered list of `{name, enabled}`. Tick the ones to search; add new ones any time (even while switched off); quick-add chips suggest common ones.
  Everything autosaves. Up to 60 remembered, 20 switched on.
- **Wanted list:** specific artists/albums you are missing. Added by hand, or from the Maintenance tab (the `enrich` spider's "look for this when searching").
  Every search looks for these **first** (marked "you were looking for this").

## Settings (autosaved)
Defaults in `collector/state.py::DEFAULT_CONFIG`: sources Internet Archive and Jamendo on, ccMixter off; minimum quality 25; releases per genre per source 12 (max 100), skip releases over 800 MB (max 10000), keep 120 suggestions (max 500),
search every 7 days (min 1) at 3 am server time, automatic search on. For more aggressive *legal* collecting: more genres, a larger per-genre count, a shorter interval.

## Flow
1. **Search** (scheduled or "Search now"; `finder.search_all`): wanted items first, then each enabled genre x each enabled source. Scores each release and drops those below the minimum (counted in the last-search note). Drops anything you already own
   (artist+title match ignoring case/punctuation), dismissed, from blocked artists, already offered or already added. Saves suggestions (wanted first, then
   artists you have, then newest). Page links are kept only if `https` on an allowed site.
2. **Approve** (`/api/collector/approve` or "Add all by this artist"): queued in `jobs.py`, one download at a time. The card shows **Waiting / Downloading (progress bar) /
   Failed (reason, press Add to retry) / Added**.
3. **Acquire** (`acquire.acquire`): re-validates license and hosts; checks disk space (size x1.25 + 2 GB free); refuses if files with those names exist; downloads each track with
   retries and resume; verifies it plays (mutagen); writes missing tags; saves a cover if offered; writes `_source_<id>.json`; inserts rows via `uploader.insert_tracks`
   on the maintenance role; logs provenance. Layout: `<Artist>/Albums/<Album>/` or `<Artist>/Singles/`. **Any failure removes everything this add wrote**,
   including folders it created.
4. **Undo** (`/api/collector/undo`): deletes the rows and moves the recorded files to `MUSIC_ROOT/.trash/<time>/`.

## Network rules (`collector/http.py`)
https only; hosts must be in `ALLOWED_SUFFIXES` (`archive.org`, `ccmixter.org`, `jamendo.com`, `musicbrainz.org`, `coverartarchive.org`); no IP-literal hosts or odd ports;
redirects re-checked hop by hop; every resolved address must be public; one request per second per host; responses capped; identifying User-Agent (with the contact from Account -> Site settings, or `COLLECTOR_CONTACT`).
Temporary failures (429, 500, 502, 503, 504, timeouts, dropped connections) retry up to 3 times with waits of 5/15/45 s; partial files resume with `Range`; 403/404 do not retry.

## Files (`COLLECTOR_DIR`, default `reports/`)
`collector_config.json`, `collector.json` (suggestions + last search), `collector_wanted.json`, `collector_hidden.json` (dismissed ids and blocked artists),
`collector_added.jsonl` (provenance: source, page, license, credit line, files, who, quality score and reasons), and `musicbrainz_cache.json`. Old `discover_*` names are migrated automatically on first start.

## Adding a source
1. New `collector/sources/<site>.py` returning `Release` objects (see `models.py`) using `collector.http` only, classifying the license with `licenses.classify`, and
   preferring structured fields over free text.
2. Register it in `collector/finder.py::MODULES` and `state.SOURCES`; add its host to `http.ALLOWED_SUFFIXES`; add a checkbox label in `admin.html`.
3. Save a real response under `tests/fixtures/` and test the parsing. Check the site's terms and `robots.txt` first.

## What it will not do
No stream ripping, torrents, or bypassing a site's rules (Bandcamp's `robots.txt`, for example, disallows its stream and download routes). Popular commercial artists are
almost never legally free; expect netlabels and independent artists who chose a free license.
