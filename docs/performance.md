# Performance

Measured on a 12-thread desktop, one server worker (as installed), SQLite, signed-in listeners who each browse about once a second (search, artist list, albums, tracks)
and pause 0.2-1 s between clicks. Tools: `TOTRsrv/tools/bench_library.py` (builds a test library and times each page) and `TOTRsrv/tools/load_test.py` (many listeners at once).
Audio streaming is not in these runs: it is a plain file read and costs the server almost nothing next to a database page.

| Library | Listeners | Requests/s | Median | 95% under | Worst | Errors |
|---|---|---|---|---|---|---|
| 5,000 songs | 50 | 78 | 24 ms | 74 ms | 0.4 s | 0 |
| 5,000 songs | 200 | 183 (the limit) | 0.4 s | 1.0 s | 2.1 s | 0 |
| 100,000 songs | 50 | 21 (the limit) | 1.2 s | 4.7 s | 9.5 s | 0 |
| 100,000 songs | 200 | 21 | 7.6 s | 12 s | 16 s | 0 |

Reading it: a home server (a few thousand songs) copes with 50 people browsing at once without effort and degrades smoothly, not with errors, at 200. A 100,000-song
library on SQLite is slow under load (single page times: search 50-110 ms, artist list 270 ms, album tracks and an artist's songs 100-250 ms; first search 3.5 s,
built at start-up). The cause is views that SQLite cannot filter early (the track's album comes from a relation join). Ideas if that size matters: the same
rewrite used for an artist's albums (start from the filtered side), a short cache of the artist list, or PostgreSQL (measured below).

Fixed on the way (2026-10-11): the search index took 20 s to build (now 3.5 s, in the background) and an artist's album list took 0.6-1 s (now 11 ms).

## PostgreSQL at 100,000 songs (measured 2026-10-12)

PostgreSQL 18.6 started as an ordinary user on the test machine (no root: the package was unpacked into a scratch folder, one cluster, `fsync` off, 512 MB shared buffers,
localhost). The database was made by the installer's own step (`tools/install_db.py`: the real `media` schema from `sql/`, never a reduced copy); the same 1,000 artists, 5,000
albums and 100,000 songs were then added with SQL (`BENCH_PG=... python tools/bench_library.py 100000`, 5 s; 67 MB). One server worker, sign-in replaced by a stand-in.

| Page (100,000 songs) | SQLite | PostgreSQL |
|---|---|---|
| first search (builds the index in the background at start-up) | 3.5 s | 3.7 s |
| search again / no match | 107 / 61 ms | 106 / 57 ms |
| artist list | 280 ms | 175 ms (was 930 ms, see below) |
| one artist / an artist's albums | 4 / 10 ms | 8 / 13 ms |
| an album's tracks / an artist's songs | 100-150 ms (see note) | 11 / 17 ms |

Note: the SQLite figures for album tracks and an artist's songs come from the earlier run (100-250 ms); the benchmark's album id only lines up with PostgreSQL's numbering, so those two SQLite cells are approximate.

| Listeners (PostgreSQL, 100,000 songs) | Requests/s | Median | 95% under | Worst | Errors |
|---|---|---|---|---|---|
| 50 | 27 | 1.1 s | 3.0 s | 3.9 s | 0 |
| 200 | 32 (the limit) | 5.4 s | 7.6 s | 8.3 s | 0 |

Reading it: single pages are fast on PostgreSQL (tracks and songs about ten times faster than on SQLite), and under load it is about 1.3-1.5 times SQLite's throughput at this
size, with no errors. It is still one Python worker doing the JSON, so at 100,000 songs and 200 people at once it saturates at about 30 requests a second (a few thousand songs:
see the first table). Not measured: PostgreSQL on a separate machine (network latency), more than one worker.

Fixed on the way: the artist list picked each artist's cover album with a per-artist subquery that walked all that artist's tracks. On PostgreSQL that took 930 ms (an artist
with no covers is the worst case); it is now one grouped pass (`main.py` `list_artists`, identical output, checked row for row on 1,000 artists with 80% of albums having a cover):
175-360 ms depending on covers, and the same query is a little faster on SQLite too.

## App size (measured 2026-10-11)

| What | Size | Notes |
|---|---|---|
| Web app (what every shell loads) | 412 KB script + 21 KB styles; 136 KB + about 6 KB when compressed | one script file for all pages |
| What is in that script | react-dom 55%, react-router 33%, the app's own code 9%, react and scheduler 3% (by source size, from a source map) | splitting by page would save almost nothing: the frameworks are needed on every page |
| Android app | 4 MB | |
| Linux app (AppImage) | 125 MB | an Electron shell is about 100 MB by itself; includes the two native programs |
| Windows installer | 144 MB (not re-measured on this machine) | about 90 ffmpeg DLLs: trimming ffmpeg is the one big win there |

Conclusion: the web app is already small. The only big size item is the Windows installer's ffmpeg (trimming it needs a Windows machine). Not measured yet: time from opening the app to music playing (needs a window and a real server).
