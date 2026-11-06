from dataclasses import asdict, dataclass, field


@dataclass
class Track:
    title: str
    number: int | None
    url: str
    filename: str
    size: int = 0


@dataclass
class Release:
    source: str  # "internet_archive", "ccmixter", "jamendo"
    source_id: str
    title: str
    artist: str
    year: int | None
    license_code: str
    license_label: str
    license_url: str
    needs_credit: bool
    page_url: str
    genre: str
    tracks: list = field(default_factory=list)
    cover_url: str = ""
    single: bool = False  # one loose song rather than an album
    # facts the source gave us for the quality score (downloads, reviews, ...)
    signals: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(t.size for t in self.tracks)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["size"] = self.size
        return d
