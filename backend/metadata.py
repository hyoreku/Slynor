import logging
import math
from functools import lru_cache
from pathlib import Path

from mutagen import File

from backend.track_covers import read_track_cover, track_cover_url
from backend.download_metadata import probe_duration_ms, read_download_metadata


logger = logging.getLogger(__name__)


# Длительность измеряется по файлу один раз на версию (путь + mtime + размер).
@lru_cache(maxsize=1024)
def _probed_duration(path: str, modified_ns: int, size: int):
    return probe_duration_ms(path)


def _duration(file_path: Path):
    try:
        stat = file_path.stat()
    except OSError:
        return None
    return _probed_duration(str(file_path), stat.st_mtime_ns, stat.st_size)


# Первое непустое значение текстового тега Mutagen.
def _first_tag(audio, key: str):
    values = audio.get(key)

    # WAVE keeps ID3 frames even when File(..., easy=True) is used.
    if not values and getattr(audio, 'tags', None) is not None:
        frame = audio.tags.get({'title': 'TIT2', 'artist': 'TPE1', 'album': 'TALB'}.get(key, ''))
        values = getattr(frame, 'text', None)

    if not values:
        return None

    if isinstance(values, str):
        return values.strip() or None

    return str(values[0]).strip() or None


# Название, исполнитель, альбом, длительность и обложка одной локальной песни.
def read_track_metadata(path: str):
    result = {
        "path": path,
        "title": None,
        "artist": None,
        "album": None,
        "duration_ms": None,
        "error": None,
        "cover_url": None,
        "cover_error": None,
    }

    try:
        file_path = Path(
            path.strip().strip('"')
        ).expanduser().resolve()

        result["title"] = file_path.stem

        if not file_path.is_file():
            result["error"] = "Файл не найден."
            return result

        saved = read_download_metadata(file_path)
        for key in ('title', 'artist', 'album', 'duration_ms', 'source', 'permalink_url'):
            if saved.get(key) is not None:
                result[key] = saved[key]
        saved_cover = track_cover_url(saved.get('cover_file'))

        audio = File(str(file_path), easy=True)

        if audio is None:
            result["cover_url"] = saved_cover
            if result["duration_ms"] is None:
                result["duration_ms"] = _duration(file_path)
            if saved:
                return result
            result["error"] = (
                "Не удалось прочитать метаданные этого формата."
            )
            return result

        result["title"] = (
            _first_tag(audio, "title") or saved.get('title') or file_path.stem
        )
        result["artist"] = _first_tag(audio, "artist") or saved.get('artist')
        result["album"] = _first_tag(audio, "album") or saved.get('album')

        length = getattr(
            getattr(audio, "info", None),
            "length",
            None,
        )

        if isinstance(length, (int, float)) and math.isfinite(length):
            result["duration_ms"] = max(
                0,
                round(length * 1000),
            )

        # Отдельное чтение картинки: его ошибка не портит текстовые теги.
        result["cover_url"], result["cover_error"] = read_track_cover(file_path)
        if result["cover_url"] is None:
            result["cover_url"] = saved_cover
        if result["duration_ms"] is None:
            result["duration_ms"] = _duration(file_path)

    except Exception:
        # Повреждённый файл не должен обрушить чтение всего списка.
        logger.exception("Ошибка чтения метаданных: %s", path)
        result["error"] = "Не удалось прочитать метаданные файла."

    return result
