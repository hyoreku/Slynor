"""Public metadata contract. A catalogue item is neither an audio candidate nor a file."""
from dataclasses import asdict, dataclass
import math
import re
from urllib.parse import urlsplit, urlunsplit

PROVIDER = 'youtube_music'
KINDS = ('track', 'artist', 'album', 'playlist')
ID_PATTERNS = {'track': r'[A-Za-z0-9_-]{11}', 'artist': r'UC[A-Za-z0-9_-]{22}',
               'album': r'MPREb_[A-Za-z0-9_-]{1,160}', 'playlist': r'[A-Za-z0-9_-]{2,180}'}


class CatalogueError(ValueError):
    """Only a fixed code, never a remote error message or credentials."""
    def __init__(self, code, http_status=None):
        self.code, self.http_status = code, http_status
        super().__init__(code)


def clean_text(value, limit=500):
    if not isinstance(value, str): return None
    value = re.sub(r'(?i)(?:https?|socks5h?)://\S+', '[url]', value[:4000])
    value = re.sub(r'\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b', '[redacted]', value)
    value = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,})\b', '[redacted]', value)
    value = re.sub(r'(?i)(?:bearer\s+|(?:token|secret|password|api_key)[=: ]+)[A-Za-z0-9_+./=-]+', '[redacted]', value)
    return ''.join(c for c in value if ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF).strip()[:limit] or None


@dataclass(frozen=True)
class EntityRef:
    kind: str
    provider_id: str
    provider: str = PROVIDER

    def __post_init__(self):
        if self.provider != PROVIDER or self.kind not in KINDS or not isinstance(self.provider_id, str):
            raise CatalogueError('invalid_entity_ref')
        identifier = self.provider_id
        if self.kind == 'playlist' and identifier.startswith('VL'): identifier = identifier[2:]
        if not re.fullmatch(ID_PATTERNS[self.kind], identifier): raise CatalogueError('invalid_entity_ref')
        object.__setattr__(self, 'provider_id', identifier)

    @property
    def key(self): return self.provider + ':' + self.kind + ':' + self.provider_id

    @property
    def public_page(self):
        if self.kind == 'track': return 'https://music.youtube.com/watch?v=' + self.provider_id
        if self.kind == 'playlist': return 'https://music.youtube.com/playlist?list=' + self.provider_id
        return 'https://music.youtube.com/browse/' + self.provider_id


@dataclass(frozen=True)
class ArtistCredit:
    name: str | None
    ref: EntityRef | None


@dataclass(frozen=True)
class CatalogueItem:
    ref: EntityRef
    title: str | None
    artists: tuple[ArtistCredit, ...] = ()
    album: EntityRef | None = None
    album_title: str | None = None
    duration_ms: int | None = None
    cover_url: str | None = None
    cover_origin: str | None = None
    availability: str = 'unknown'
    full_or_preview: str = 'unknown'
    version_classification: str = 'unknown'
    title_version_hints: tuple[str, ...] = ()
    explicit_reported: bool | None = None
    author: str | None = None
    metadata_origin: str = 'youtube_music_catalogue'

    def to_dict(self):
        return {**asdict(self), 'key': self.ref.key, 'public_page': self.ref.public_page,
                'audio_candidate': None, 'audio_asset': None, 'download_permission': 'not_established'}


def optional_ref(kind, value):
    try: return EntityRef(kind, value)
    except CatalogueError: return None


def credits(row):
    values = row.get('artists')
    result = []
    for value in values[:20] if isinstance(values, list) else []:
        if not isinstance(value, dict): continue
        name, ref = clean_text(value.get('name')), optional_ref('artist', value.get('id'))
        if name or ref: result.append(ArtistCredit(name, ref))
    if not result and isinstance(row.get('artist'), str): result.append(ArtistCredit(clean_text(row['artist']), None))
    return tuple(result)


def reported_duration(row):
    number = row.get('duration_seconds')
    if type(number) in (int, float) and math.isfinite(number) and 0 < number <= 604800:
        return round(number * 1000)
    value = row.get('duration')
    if isinstance(value, str) and re.fullmatch(r'\d{1,3}(?::[0-5]\d){1,2}', value):
        seconds = 0
        for part in value.split(':'): seconds = seconds * 60 + int(part)
        if 0 < seconds <= 604800: return seconds * 1000
    return None


def cover_url(row):
    values = row.get('thumbnails')
    for entry in reversed(values[:20] if isinstance(values, list) else []):
        if not isinstance(entry, dict) or not isinstance(entry.get('url'), str): continue
        try:
            url = urlsplit(entry['url'])
            if url.scheme == 'https' and url.hostname in ('lh3.googleusercontent.com', 'yt3.ggpht.com', 'i.ytimg.com') and not url.username and not url.password and url.port in (None, 443):
                return urlunsplit((url.scheme, url.netloc, url.path, '', ''))
        except ValueError: pass
    return None


def version_hints(title):
    value = title or ''
    patterns = {'live': r'\blive\b|концерт|вживую', 'remix': r'\bremix\b|ремикс',
                'cover': r'\bcover\b|кавер', 'acoustic': r'\bacoustic\b|акуст',
                'remaster': r'\bremaster(?:ed)?\b|ремастер', 'slowed': r'\bslowed\b',
                'sped_up': r'\bsped\s*up\b|\bspeed\s*up\b'}
    return tuple(key for key, pattern in patterns.items() if re.search(pattern, value, re.I))


def normalize(row, context=None):
    """No videos/profiles/podcasts become tracks. Container context must be explicit."""
    if not isinstance(row, dict): raise CatalogueError('invalid_catalogue_row')
    types = {'song': 'track', 'artist': 'artist', 'album': 'album', 'playlist': 'playlist'}
    kind = types.get(row.get('resultType')) if 'resultType' in row else context
    if kind not in KINDS: raise CatalogueError('unsupported_result_type')
    video_type = row.get('videoType')
    if kind == 'track' and video_type not in (None, 'MUSIC_VIDEO_TYPE_ATV'):
        raise CatalogueError('non_audio_catalogue_entry')
    identifier = row.get('videoId') if kind == 'track' else row.get('browseId') or row.get('playlistId')
    ref = EntityRef(kind, identifier)
    title = clean_text(row.get('title') or (row.get('artist') if kind == 'artist' else None))
    album = row.get('album') if isinstance(row.get('album'), dict) else {}
    cover = cover_url(row)
    author = row.get('author')
    if isinstance(author, dict): author = author.get('name')
    return CatalogueItem(ref=ref, title=title, artists=() if kind == 'artist' else credits(row),
        album=optional_ref('album', album.get('id')), album_title=clean_text(album.get('name')),
        duration_ms=reported_duration(row) if kind == 'track' else None,
        cover_url=cover, cover_origin=PROVIDER if cover else None,
        availability='reported_unavailable' if row.get('isAvailable') is False else 'reported_available' if row.get('isAvailable') is True else 'unknown',
        full_or_preview='preview' if row.get('isPreview') is True else 'unknown',
        title_version_hints=version_hints(title) if kind == 'track' else (),
        explicit_reported=row.get('isExplicit') if type(row.get('isExplicit')) is bool else None,
        author=clean_text(author))


def request_key(query, kind, language, location, route):
    """Identity for future caches; this module does not cache network results."""
    if kind not in ('all', *KINDS): raise CatalogueError('invalid_filter')
    return (PROVIDER, kind, query, language, location, route)
