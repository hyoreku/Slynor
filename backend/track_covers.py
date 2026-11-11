"""Встроенные обложки локальных песен. Музыкальные файлы только читаются."""

import base64
import hashlib
import logging
import re
import warnings
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import RLock
from uuid import uuid4

from mutagen import File
from mutagen.flac import Picture
from PIL import Image, ImageOps

from backend.image_assets import get_data_dir, MAX_FILE_BYTES, MAX_PIXELS

logger = logging.getLogger(__name__)
TRACK_COVER_NAME = re.compile(r"[0-9a-f]{64}\.jpg")
_cache_lock = RLock()


# Сервер выдаёт только JPEG-копии из выделенной папки, не произвольные пути.
def get_track_cover_path(filename):
    if not isinstance(filename, str) or not TRACK_COVER_NAME.fullmatch(filename):
        raise ValueError("Некорректное имя обложки песни.")
    folder = (get_data_dir() / "covers" / "tracks").resolve()
    path = folder / filename
    if path.resolve().parent != folder:
        raise ValueError("Недопустимый путь к обложке.")
    return path


# Разные форматы хранят картинки по-разному: APIC, pictures, covr, base64.
# Сначала пробуем лицевую обложку (type=3), затем остальные изображения.
def _picture_candidates(audio):
    pictures = getattr(audio, "pictures", None)
    if pictures:
        for picture in sorted(pictures, key=lambda p: getattr(p, "type", None) != 3):
            yield picture.data
    tags = getattr(audio, "tags", None)
    if tags is None:
        return
    if hasattr(tags, "getall"):
        for frame in sorted(tags.getall("APIC"), key=lambda p: getattr(p, "type", None) != 3):
            yield frame.data
    for data in tags.get("covr", []):
        yield bytes(data)
    # Ogg/Vorbis/Opus могут хранить структуру FLAC Picture в текстовом теге.
    for encoded in tags.get("metadata_block_picture", []):
        try:
            if len(encoded) > (MAX_FILE_BYTES * 4 // 3) + 4096:
                continue
            yield Picture(base64.b64decode(encoded, validate=True)).data
        except Exception:
            logger.debug("Пропущен некорректный тег metadata_block_picture.")
    for encoded in tags.get("coverart", []):
        try:
            if len(encoded) <= (MAX_FILE_BYTES * 4 // 3) + 4096:
                yield base64.b64decode(encoded, validate=True)
        except Exception:
            logger.debug("Пропущен некорректный тег coverart.")


# Создаём маленькую безопасную JPEG-копию; EXIF не переносится.
# SHA-256 содержимого объединяет одинаковые обложки нескольких песен.
def _store_picture(data):
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError("Встроенная обложка слишком большая или пустая.")
    filename = hashlib.sha256(data).hexdigest() + ".jpg"
    destination = get_track_cover_path(filename)
    with _cache_lock:
        if destination.is_file():
            return filename
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Формат встроенной картинки не поддерживается.")
                if original.width * original.height > MAX_PIXELS:
                    raise ValueError("Встроенная картинка слишком большая.")
                original.load()
                oriented = ImageOps.exif_transpose(original)
                oriented.thumbnail((512, 512), Image.Resampling.LANCZOS)
                with oriented.convert("RGBA") as rgba:
                    with Image.new("RGB", rgba.size, "#18181b") as image:
                        image.paste(rgba, mask=rgba.getchannel("A"))
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        temporary = destination.with_name(uuid4().hex + ".tmp")
                        try:
                            image.save(temporary, format="JPEG", quality=88, optimize=True)
                            temporary.replace(destination)
                        finally:
                            temporary.unlink(missing_ok=True)
    return filename


# В памяти кэшируются также песни без картинки. Изменение размера/mtime
# файла создаёт новый ключ: «Обновить сведения» увидит изменённые теги.
@lru_cache(maxsize=2048)
def _read_cached(path, modified_ns, size):
    try:
        audio = File(path, easy=False)
        if audio is None:
            return None, None
        found = False
        for data in _picture_candidates(audio):
            found = True
            try:
                return _store_picture(data), None
            except Exception:
                logger.debug("Встроенная картинка не прочитана: %s", path, exc_info=True)
        return None, "Не удалось прочитать встроенную обложку." if found else None
    except Exception:
        # Ошибка картинки не должна ломать названия песен или воспроизведение.
        logger.debug("Ошибка чтения встроенных обложек: %s", path, exc_info=True)
        return None, "Не удалось прочитать встроенную обложку."


# Метаданные возвращают URL копии, а не длинную base64-строку изображения.
def read_track_cover(file_path):
    try:
        file_path = Path(file_path).resolve()
        stat = file_path.stat()
        filename, error = _read_cached(str(file_path), stat.st_mtime_ns, stat.st_size)
        if filename and not get_track_cover_path(filename).is_file():
            # Если пользователь удалил кэш, извлекаем копию ещё раз.
            _read_cached.cache_clear()
            filename, error = _read_cached(str(file_path), stat.st_mtime_ns, stat.st_size)
        return ("/api/assets/track-covers/" + filename if filename else None), error
    except Exception:
        logger.debug("Обложка песни недоступна: %s", file_path, exc_info=True)
        return None, "Не удалось прочитать встроенную обложку."


# Кнопка «Обновить сведения» позволяет повторить и неудачное чтение.
def clear_track_cover_cache():
    _read_cached.cache_clear()


# Уже скачанная обложка проходит тот же безопасный JPEG-кэш, что и встроенная.
def store_cover_bytes(data):
    return _store_picture(data)


# Ссылка на копию в кэше; произвольные имена и пути не принимаются.
def track_cover_url(filename):
    if not isinstance(filename, str) or not TRACK_COVER_NAME.fullmatch(filename):
        return None
    try:
        path = get_track_cover_path(filename)
    except ValueError:
        return None
    return "/api/assets/track-covers/" + filename if path.is_file() else None
