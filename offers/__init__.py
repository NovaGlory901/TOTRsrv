"""Song offers: a person offers songs they saved in the TOTR app to a server they have joined, or an
admin asks a person for theirs.

Nothing enters the library by itself. A person who may add music (the Upload permission, or an
admin) accepts some of the offered songs; only then may the owner of the songs send those files, and
each file must be exactly what was offered (size and SHA-256) and must read as audio. Who decided
and what happened is written to the audit file (reports/actions.log, the same one the maintenance
spiders use). See sql/song_offers.sql for the tables.
"""

MAX_ITEMS = 100  # songs in one offer (the request body is capped at 64 KB by the gate)
MAX_PENDING_PER_PERSON = 3  # offers waiting for an answer, per person
MAX_PENDING_REQUESTS = 5  # admin asks waiting for a person's answer, per person
MAX_SONG_BYTES = 300 * 1024 * 1024
# all the songs of one offer together (an offer an admin asked for is accepted at once, so this is
# its ceiling)
MAX_OFFER_BYTES = 4 * 1024**3
# a song is refused when taking it would leave the music disk with less than this
KEEP_FREE_BYTES = 1024**3
KEEP_DAYS = 30  # answered offers and requests are forgotten after this long
AUDIO_EXTS = (".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav")
