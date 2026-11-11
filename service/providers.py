"""Audio providers: stable identifiers, ephemeral streams, verified downloads.

A metadata row never proves that audio exists. Search returns ProviderTrack rows
with a stable provider identifier and a public page; resolve returns an ephemeral
stream reference that must not be persisted; download returns exactly one regular
file inside the requested folder, and the caller measures it before publishing.

The service does not import yt-dlp or spotDL. WorkerProvider talks to the
existing player worker through a transport (see worker.py), so every source keeps
one parser in this repository instead of a second copy in the service.
"""
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .errors import LimitError, ProviderError
from .media import AUDIO_EXTENSIONS
from .models import Availability

PROVIDERS = ('youtube', 'spotify', 'soundcloud')
HOSTS = {
    'youtube': ('www.youtube.com', 'youtube.com', 'music.youtube.com', 'youtu.be'),
    'spotify': ('open.spotify.com',),
    'soundcloud': ('soundcloud.com', 'www.soundcloud.com'),
}
SOUNDCLOUD_RESERVED = frozenset({'sets', 'tracks', 'albums', 'popular-tracks', 'reposts', 'likes'})
STREAM_HOSTS = ('.googlevideo.com',)
MAX_QUERY = 200
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_DURATION_MS = 7 * 24 * 60 * 60 * 1000
CONTROL = re.compile(r'[\x00-\x1f\x7f]')
STABLE_ID = re.compile(r'^[A-Za-z0-9_-]{1,100}(?:/[A-Za-z0-9_-]{1,100})?$')
YOUTUBE_ID = re.compile(r'[A-Za-z0-9_-]{11}')
SPOTIFY_ID = re.compile(r'[A-Za-z0-9]{1,100}')
HEADER_ALLOW = ('user-agent', 'referer')


@dataclass(frozen=True)
class ProviderTrack:
    """A catalogue row with a stable identifier; it makes no audio claim."""

    provider: str
    provider_id: str
    title: str
    artist: str = ''
    duration_ms: int | None = None
    cover_url: str | None = None

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ProviderError('Неизвестный источник.', code='unsupported_provider')
        if not isinstance(self.provider_id, str) or not STABLE_ID.fullmatch(self.provider_id):
            raise ProviderError('Источник вернул нестабильный идентификатор записи.',
                                code='unstable_identifier')
        if not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 500:
            raise ProviderError('Источник вернул запись без названия.', code='invalid_title')
        if not isinstance(self.artist, str) or len(self.artist) > 200:
            raise ProviderError('Источник вернул некорректного исполнителя.',
                                code='invalid_artist')
        if self.duration_ms is not None and (type(self.duration_ms) is not int
                                             or not 0 < self.duration_ms <= MAX_DURATION_MS):
            raise ProviderError('Источник вернул некорректную длительность.',
                                code='invalid_duration')

    @property
    def public_url(self):
        return track_url(self.provider, self.provider_id)


@dataclass(frozen=True)
class ProviderSearch:
    tracks: tuple
    warnings: tuple = ()


@dataclass(frozen=True)
class ResolvedStream:
    """An expiring direct media resource; never store it as an identifier."""

    url: str
    headers: dict


def stable_identifier(provider, url):
    """Extract the durable identifier of an individual track from a public URL."""
    if provider not in HOSTS:
        raise ProviderError('Неизвестный источник.', code='unsupported_provider')
    if not isinstance(url, str) or len(url) > 4096:
        raise ProviderError('Некорректная ссылка источника.', code='unsupported_url')
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise ProviderError('Некорректная ссылка источника.', code='unsupported_url') from None
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS[provider]
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ProviderError('Неподдерживаемая ссылка источника.', code='unsupported_url')
    parts = [part for part in parsed.path.strip('/').split('/') if part]
    if provider == 'youtube':
        if parsed.hostname == 'youtu.be' and len(parts) == 1:
            identifier = parts[0]
        elif parsed.path == '/watch':
            values = parse_qs(parsed.query).get('v', [])
            identifier = values[0] if len(values) == 1 else ''
        elif len(parts) == 2 and parts[0] in ('shorts', 'embed'):
            identifier = parts[1]
        else:
            identifier = ''
        if not YOUTUBE_ID.fullmatch(identifier):
            raise ProviderError('Ссылка не указывает на отдельное видео YouTube.',
                                code='not_an_individual_track')
        return identifier
    if provider == 'spotify':
        if len(parts) != 2 or parts[0] != 'track' or not SPOTIFY_ID.fullmatch(parts[1]):
            raise ProviderError('Ссылка не указывает на отдельный трек Spotify.',
                                code='not_an_individual_track')
        return parts[1]
    if len(parts) != 2 or not all(parts) or parts[1] in SOUNDCLOUD_RESERVED:
        raise ProviderError('Ссылка не указывает на отдельный трек SoundCloud.',
                            code='not_an_individual_track')
    return '/'.join(parts)


def track_url(provider, provider_id):
    """The public page of a stable identifier; a share link, not a media URL."""
    if provider == 'youtube':
        if not YOUTUBE_ID.fullmatch(provider_id):
            raise ProviderError('Некорректный идентификатор видео.', code='unstable_identifier')
        return 'https://www.youtube.com/watch?v=' + provider_id
    if provider == 'spotify':
        if not SPOTIFY_ID.fullmatch(provider_id):
            raise ProviderError('Некорректный идентификатор трека.', code='unstable_identifier')
        return 'https://open.spotify.com/track/' + provider_id
    if provider == 'soundcloud':
        if not STABLE_ID.fullmatch(provider_id) or '/' not in provider_id:
            raise ProviderError('Некорректный идентификатор трека.', code='unstable_identifier')
        return 'https://soundcloud.com/' + provider_id
    raise ProviderError('Неизвестный источник.', code='unsupported_provider')


def clean_text(value, limit=500):
    """Short user-facing text without control characters or URLs."""
    if not isinstance(value, str):
        return None
    value = CONTROL.sub(' ', value)
    value = re.sub(r'(?i)(?:https?|socks5h?)://\S+', '[url]', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value[:limit] or None


def https_url(value, limit=2048):
    if not isinstance(value, str) or len(value) > limit:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        return None
    return value


class WorkerProvider:
    """One source served by the existing provider worker; no local parser copy."""

    def __init__(self, transport, provider, *, search_timeout=20.0, resolve_timeout=15.0,
                 download_timeout=600.0, max_file_bytes=MAX_FILE_BYTES):
        if provider not in PROVIDERS:
            raise ProviderError('Неизвестный источник.', code='unsupported_provider')
        self.transport = transport
        self.name = provider
        self.search_timeout = search_timeout
        self.resolve_timeout = resolve_timeout
        self.download_timeout = download_timeout
        self.max_file_bytes = int(max_file_bytes)

    def _query(self, query):
        if not isinstance(query, str):
            raise ProviderError('Поисковый запрос должен быть строкой.', code='invalid_query')
        value = CONTROL.sub('', query).strip()
        if not value or len(value) > MAX_QUERY:
            raise ProviderError('Некорректный поисковый запрос.', code='invalid_query')
        if '://' in value:
            if not value.lower().startswith('https://'):
                raise ProviderError('Принимаются только https-ссылки поддерживаемых источников.',
                                    code='unsupported_url')
            return track_url(self.name, stable_identifier(self.name, value))
        return value

    @staticmethod
    def _refused(result):
        error = result.get('error')
        if error is not None:
            raise ProviderError(clean_text(error) or 'Источник отказал в запросе.',
                                code='provider_refused')

    def _track(self, row):
        if not isinstance(row, dict) or row.get('kind') not in (None, 'track'):
            raise ProviderError('Источник вернул не трек.', code='not_a_track')
        identifier = stable_identifier(self.name, row.get('url'))
        duration = row.get('duration_ms')
        if type(duration) is not int or not 0 < duration <= MAX_DURATION_MS:
            duration = None
        artist = row.get('artist')
        artist = artist.strip() if isinstance(artist, str) else ''
        title = row.get('title')
        title = title.strip() if isinstance(title, str) else ''
        return ProviderTrack(provider=self.name, provider_id=identifier,
                             title=title, artist=artist[:200],
                             duration_ms=duration,
                             cover_url=https_url(row.get('cover_url')))

    def search(self, query, limit=8):
        value = self._query(query)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ProviderError('Лимит выдачи должен быть от 1 до 20.', code='invalid_limit')
        result = self.transport.request({'operation': 'search', 'query': value,
                                         'provider': self.name}, timeout=self.search_timeout)
        self._refused(result)
        rows = result.get('tracks')
        if not isinstance(rows, list):
            raise ProviderError('Источник вернул некорректную выдачу.', code='invalid_response')
        tracks, seen = [], set()
        for row in rows[:400]:
            try:
                track = self._track(row)
            except ProviderError:
                continue
            if track.provider_id in seen:
                continue
            seen.add(track.provider_id)
            tracks.append(track)
            if len(tracks) >= limit:
                break
        warnings = tuple(filter(None, (clean_text(item, 300) for item in
                                       (result.get('warnings') or [])[:5])))
        return ProviderSearch(tuple(tracks), warnings)

    def resolve(self, track):
        """Only YouTube exposes a supported direct audio stream today."""
        if self.name != 'youtube':
            raise ProviderError('Прямой аудиопоток поддерживается только для YouTube.',
                                code='resolve_unsupported')
        if not isinstance(track, ProviderTrack) or track.provider != self.name:
            raise ProviderError('Запись не принадлежит этому источнику.', code='provider_mismatch')
        result = self.transport.request({'operation': 'stream', 'url': track.public_url},
                                        timeout=self.resolve_timeout)
        self._refused(result)
        url = result.get('stream_url')
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
        except ValueError:
            parsed = None
        if (parsed is None or parsed.scheme != 'https' or parsed.username or parsed.password
                or parsed.port not in (None, 443)
                or not (parsed.hostname or '').endswith(STREAM_HOSTS)):
            raise ProviderError('Источник не вернул поддерживаемый аудиопоток.',
                                code='unsupported_stream')
        headers = {}
        for key, value in (result.get('http_headers') or {}).items():
            if (isinstance(key, str) and key.lower() in HEADER_ALLOW
                    and isinstance(value, str) and len(value) <= 2048
                    and '\r' not in value and '\n' not in value):
                headers[key] = value
        return ResolvedStream(url, headers)

    def download(self, track, folder, audio_format='original'):
        if not isinstance(track, ProviderTrack) or track.provider != self.name:
            raise ProviderError('Запись не принадлежит этому источнику.', code='provider_mismatch')
        if audio_format not in ('original', 'mp3'):
            raise ProviderError('Неизвестный формат аудио.', code='invalid_format')
        folder = Path(folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise ProviderError('Не удалось создать папку загрузки.',
                                code='download_folder_unavailable') from None
        if folder.is_symlink():
            raise ProviderError('Папка загрузки не должна быть ссылкой.', code='unsafe_folder')
        result = self.transport.request(
            {'operation': 'download', 'provider': self.name, 'url': track.public_url,
             'folder': str(folder), 'audio_format': audio_format},
            timeout=self.download_timeout)
        self._refused(result)
        raw = result.get('file')
        if not isinstance(raw, str) or not raw:
            raise ProviderError('Загрузчик не вернул готовый файл.', code='download_missing_file')
        try:
            path = Path(raw).resolve(strict=True)
        except OSError:
            raise ProviderError('Загрузчик вернул несуществующий файл.',
                                code='download_missing_file') from None
        if path.is_symlink() or not path.is_file():
            raise ProviderError('Загрузчик вернул не обычный файл.', code='unsafe_download')
        try:
            path.relative_to(folder.resolve())
        except ValueError:
            raise ProviderError('Загрузчик вернул файл вне папки загрузки.',
                                code='download_outside_folder') from None
        size = path.stat().st_size
        if size <= 0:
            raise ProviderError('Загрузчик вернул пустой файл.', code='download_empty_file')
        if size > self.max_file_bytes:
            raise LimitError('Файл ' + str(size) + ' байт больше предела '
                             + str(self.max_file_bytes) + ' байт.', code='file_too_large')
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            raise ProviderError('Загрузчик вернул неподдерживаемый контейнер.',
                                code='unsupported_container')
        return path
