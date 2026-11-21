"""Resolve public artwork for a recording without uploading local files."""

from collections import OrderedDict
from hashlib import sha256
import json
import re
import sqlite3
from contextlib import closing
from threading import Lock, Thread
from time import monotonic, time
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import httpx

from backend.provider_worker import validate_track_url


_MBID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_USER_AGENT = "Slynor/0.1 (https://github.com/hyoreku/Slynor)"
_MUSICBRAINZ = "https://musicbrainz.org/ws/2/recording/"
_APPLE_SEARCH = "https://itunes.apple.com/search"
_MAX_CACHE = 2048
_NOT_STUDIO = re.compile(r"\b(?:live|remix|instrumental|demo|karaoke|cover|acoustic)\b", re.I)
_COVER_HOSTS = {
    "i.scdn.co", "mosaic.scdn.co", "i.ytimg.com", "img.youtube.com",
    "lh3.googleusercontent.com", "yt3.ggpht.com",
}
_OEMBED = {
    "spotify": "https://open.spotify.com/oembed",
    "soundcloud": "https://soundcloud.com/oembed",
}


def archive_cover_url(value):
    """Accept image locations in the archive's MusicBrainz collection."""
    if not isinstance(value, str) or len(value) > 1024 or re.search(r"[\s\\]", value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme not in ("http", "https") or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)):
            return None
        if ((host == "archive.org" or host.endswith(".archive.org"))
                and re.search(r"/(?:download|items)/mbid-" + _MBID + r"/[0-9A-Za-z_.-]+\.(?:jpg|jpeg|png|webp)$", parsed.path)):
            return urlunsplit(("https", host, parsed.path, "", ""))
    except ValueError:
        pass
    return None


def public_cover_url(value):
    if not isinstance(value, str) or len(value) > 1024 or re.search(r"[\s\\]", value):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if (url.scheme == "https" and url.username is None and url.password is None
                and url.port in (None, 443) and url.path not in ("", "/")
                and (host in _COVER_HOSTS or host.endswith(".sndcdn.com")
                     or (re.fullmatch(r"is[0-9]+-ssl\.mzstatic\.com", host)
                         and url.path.startswith("/image/thumb/Music")))):
            # The public path is enough. The query can hold temporary credentials.
            return urlunsplit(("https", host, url.path, "", ""))
    except ValueError:
        return None
    return archive_cover_url(value) if value.startswith("https://") else None


def track_page(value, source):
    if not isinstance(value, str) or len(value) > 1024 or re.search(r"[\s\\]", value):
        return None
    try:
        page = validate_track_url(value, source)
        if source == "soundcloud" and not re.fullmatch(
                r"https://soundcloud\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", page):
            return None
        return page
    except (ValueError, TypeError):
        return None


def youtube_thumbnail(page):
    canonical = track_page(page, "youtube")
    if canonical:
        return f"https://i.ytimg.com/vi/{parse_qs(urlsplit(canonical).query)['v'][0]}/hqdefault.jpg"
    return None


def recording_code(value):
    if isinstance(value, str):
        code = value.upper().replace("-", "").strip()
        if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}", code):
            return code
    return None


def _query_text(value):
    if isinstance(value, str) and not re.search(r"://|\b(?:file|online|data):|[a-z]:[\\/]|\\|(?:^|\s)/\S", value, re.I):
        return " ".join(value.split())[:200] or None
    return None


def _normalized(value):
    return " ".join(re.findall(r"[^\W_]+", value.casefold())) if isinstance(value, str) else ""


def _quoted(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _releases(search, title, artist, album, duration_ms, *, isrc=None, release_groups=False):
    candidates = []
    for recording in search.get("recordings", [])[:12]:
        exact_isrc = bool(isrc and isrc in (recording.get("isrcs") or []))
        if not exact_isrc and _normalized(recording.get("title")) != _normalized(title):
            continue
        credits = recording.get("artist-credit") or []
        credit = "".join(row.get("name", "") + row.get("joinphrase", "") for row in credits)
        artist_key = _normalized(artist)
        individual_names = " ".join(_normalized(row.get("name")) for row in credits)
        if not exact_isrc and artist_key not in {_normalized(credit), individual_names}:
            continue
        length = recording.get("length")
        length_matches = type(length) is int and duration_ms > 0
        difference = abs(length - duration_ms) if length_matches else 0
        if difference > 5000 and not exact_isrc:
            continue
        if (not exact_isrc and _NOT_STUDIO.search(recording.get("disambiguation") or "")
                and not _NOT_STUDIO.search((title or "") + " " + album)):
            continue
        for release in (recording.get("releases") or [])[:20]:
            release_id = release.get("id", "")
            status = release.get("status")
            if status not in (None, "Official") or not re.fullmatch(_MBID, release_id):
                continue
            group = release.get("release-group") or {}
            exact_album = bool(album) and _normalized(album) in {
                _normalized(release.get("title")), _normalized(group.get("title")),
            }
            if album and not exact_album and not (release_groups and (exact_isrc or length_matches)):
                continue
            if not exact_album and (group.get("secondary-types") or _NOT_STUDIO.search(release.get("title", ""))):
                continue
            date = release.get("date") or group.get("first-release-date") or "9999"
            group_id = group.get("id") or ""
            candidates.append((not exact_isrc, not exact_album, status != "Official", difference, date, release_id,
                               group_id if re.fullmatch(_MBID, group_id) else ""))
    if not release_groups:
        return list(dict.fromkeys(row[-2] for row in sorted(candidates)))[:3]
    return list(dict.fromkeys(row[-2:] for row in sorted(candidates)))[:3]


class DiscordArtwork:
    def __init__(self, cache_path, application_id, network=None):
        self._cache_path = cache_path
        self._application_id = application_id
        self._network = network
        self._lock = Lock()
        self._cache = OrderedDict()
        self._running = False
        self._logo = None
        self._logo_until = 0.0
        self._logo_running = False
        self._search_after = 0.0

    def logo(self):
        """Use the ID of the registered asset. Omit it if Discord has no such asset."""
        with self._lock:
            if not self._logo_running and time() >= self._logo_until:
                self._logo_running = True
                Thread(target=self._load_logo, name="discord-logo", daemon=True).start()
            return self._logo

    def _load_logo(self):
        logo, ttl = None, 120
        try:
            response = httpx.get(
                f"https://discord.com/api/v10/oauth2/applications/{self._application_id}/assets",
                headers={"User-Agent": _USER_AGENT}, timeout=3,
            )
            rows = response.raise_for_status().json()
            logo = next((row["id"] for row in rows if row.get("name") == "slynor"
                         and re.fullmatch(r"[0-9]{17,20}", str(row.get("id", "")))), None)
            logo, ttl = str(logo) if logo else None, 1800
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass
        finally:
            with self._lock:
                self._logo = logo
                self._logo_until = time() + ttl
                self._logo_running = False

    def lookup(self, title, artist, album, duration_ms, *, source=None, permalink_url=None, isrc=None, allow_apple=False):
        title, artist, album = map(_query_text, (title, artist, album))
        page = track_page(permalink_url, source)
        isrc = recording_code(isrc)
        if not (title and artist) and not page and not isrc:
            return None
        milliseconds = duration_ms if type(duration_ms) is int and duration_ms > 0 else 0
        key = sha256(json.dumps([3, _normalized(title), _normalized(artist), _normalized(album),
                                 round(milliseconds / 1000), page, isrc, allow_apple], ensure_ascii=False).encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time():
                self._cache.move_to_end(key)
                return cached[1]
            if not self._running and monotonic() >= self._search_after:
                self._running = True
                Thread(target=self._resolve, args=(key, title, artist, album or "", milliseconds,
                                                   source if page else None, page, isrc, allow_apple),
                       name="discord-cover", daemon=True).start()
        return None

    def _cached(self, key, result=None):
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._cache_path, timeout=1)) as conn, conn:
                conn.execute("CREATE TABLE IF NOT EXISTS cover (key TEXT PRIMARY KEY, until REAL, url TEXT)")
                if result is None:
                    row = conn.execute("SELECT until,url FROM cover WHERE key=? AND until>?", (key, time())).fetchone()
                    if row and (row[1] is None or public_cover_url(row[1]) == row[1]):
                        return row
                    return None
                conn.execute("INSERT OR REPLACE INTO cover VALUES (?,?,?)", (key, *result))
                conn.execute("DELETE FROM cover WHERE key IN (SELECT key FROM cover ORDER BY until DESC LIMIT -1 OFFSET ?)",
                             (_MAX_CACHE,))
        except (OSError, sqlite3.Error):
            return None

    def _resolve(self, key, title, artist, album, milliseconds, source, page, isrc, allow_apple):
        result = (time() + 90, None)
        try:
            cached = self._cached(key)
            if cached is not None:
                result = cached
            else:
                with self._lock:
                    self._search_after = monotonic() + 1.1
                proxy = self._network.resolve() if self._network is not None else None
                cover = self._search(title, artist, album, milliseconds, source=source, page=page,
                                     isrc=isrc, proxy=proxy, allow_apple=allow_apple)
                result = (time() + (30 * 86400 if cover else 12 * 3600), cover)
                self._cached(key, result)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, OSError):
            pass  # Short retry on provider failures, without making missing artwork permanent.
        finally:
            with self._lock:
                self._cache[key] = result
                self._cache.move_to_end(key)
                if len(self._cache) > _MAX_CACHE:
                    self._cache.popitem(last=False)
                self._running = False

    @staticmethod
    def _image_at(client, url, safe_url, deadline):
        for _ in range(4):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise httpx.TimeoutException("Artwork lookup timed out")
            response = client.head(url, timeout=min(3, remaining))
            if response.status_code in (403, 405):
                # Some image hosts reject HEAD. Inspect GET headers without reading the image.
                with client.stream("GET", url, headers={"Range": "bytes=0-0"}, timeout=min(3, remaining)) as get:
                    response = get
            if response.status_code in (404, 410):
                return None
            if response.is_redirect:
                url = safe_url(urljoin(url, response.headers.get("location", "")))
                if not url:
                    return None
                continue
            response.raise_for_status()
            if response.headers.get("content-type", "").startswith("image/"):
                return safe_url(url)
            return None
        return None

    @classmethod
    def _apple_artwork(cls, client, title, artist, album, milliseconds):
        response = client.get(_APPLE_SEARCH, params={"term": f"{artist} {title}", "entity": "song", "limit": 15})
        candidates = []
        for item in response.raise_for_status().json().get("results", [])[:15]:
            if (_normalized(item.get("trackName")) != _normalized(title)
                    or _normalized(item.get("artistName")) != _normalized(artist)):
                continue
            length = item.get("trackTimeMillis")
            if milliseconds and (type(length) is not int or abs(length - milliseconds) > 5000):
                continue
            url = public_cover_url(item.get("artworkUrl100"))
            if url:
                candidates.append((_normalized(item.get("collectionName")) != _normalized(album),
                                   abs(length - milliseconds) if milliseconds else 0, url))
        deadline = monotonic() + 4
        for _, _, url in sorted(set(candidates))[:3]:
            try:
                if cover := cls._image_at(client, url, public_cover_url, deadline):
                    return cover
            except httpx.HTTPError:
                continue
        return None

    @classmethod
    def _search(cls, title, artist, album, milliseconds, *, source=None, page=None, isrc=None, proxy=None, allow_apple=False):
        with httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=3,
                          follow_redirects=False, trust_env=False, proxy=proxy) as client:
            provider_failed = False
            if page:
                try:
                    cover = youtube_thumbnail(page) if source == "youtube" else None
                    if source in _OEMBED:
                        response = client.get(_OEMBED[source], params={"url": page})
                        if response.status_code not in (401, 403, 404, 410):
                            cover = public_cover_url(response.raise_for_status().json().get("thumbnail_url"))
                    if cover:
                        verified = cls._image_at(client, cover, public_cover_url, monotonic() + 3)
                        if verified:
                            return verified
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    provider_failed = True

            query = f"recording:{_quoted(title)} AND artist:{_quoted(artist)}" if title and artist else None
            if isrc:
                query = f"(isrc:{isrc} OR ({query}))" if query else f"isrc:{isrc}"
            if query:
                deadline = monotonic() + 8
                try:
                    search = client.get(_MUSICBRAINZ, params={
                        "query": f"({query}) AND status:official", "fmt": "json", "limit": 12,
                    })
                    for release_id, group_id in _releases(search.raise_for_status().json(), title, artist, album,
                                                          milliseconds, isrc=isrc, release_groups=True):
                        urls = [f"https://coverartarchive.org/release/{release_id}/front-250"]
                        if group_id:
                            urls.append(f"https://coverartarchive.org/release-group/{group_id}/front-250")
                        for url in urls:
                            try:
                                if cover := cls._image_at(client, url, archive_cover_url, deadline):
                                    return cover
                            except httpx.HTTPError:
                                provider_failed = True
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    provider_failed = True

            if allow_apple and title and artist:
                try:
                    if cover := cls._apple_artwork(client, title, artist, album, milliseconds):
                        return cover
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    provider_failed = True
            if provider_failed:
                raise httpx.RequestError("Track thumbnail provider unavailable")
        return None
