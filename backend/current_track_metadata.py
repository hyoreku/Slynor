from collections import OrderedDict
from collections.abc import Callable
import ntpath
from threading import RLock

from backend.online_music import ProviderError


_CACHE_LIMIT = 512
_cache: OrderedDict[str, dict] = OrderedDict()
_lock = RLock()


def _key(path: str) -> str:
    if path.startswith("online:"):
        return path
    return ntpath.normcase(ntpath.normpath(path))


def cache_metadata(track: dict) -> None:
    with _lock:
        _cache[_key(track["path"])] = track.copy()
        if len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)


def get_cached_metadata(path: str) -> dict | None:
    with _lock:
        track = _cache.get(_key(path))
        return track.copy() if track is not None else None


def resolve_current_track_metadata(path: str, load_online: Callable[[str], dict]) -> dict | None:
    """Resolve the track selected by a playback snapshot. Local reads use the warm cache."""
    cached = get_cached_metadata(path)
    if cached is not None or not path.startswith("online:"):
        return cached
    try:
        return load_online(path)
    except ProviderError:
        return None
