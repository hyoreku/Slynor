"""Проверка и хранение пользовательских обложек плейлистов."""

import logging
import os
import re
import warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
COVER_NAME = re.compile(r"[0-9a-f]{32}\.jpg")


# Единая папка данных пользователя вне исходников проекта.
def get_data_dir():
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise RuntimeError("Не найдена папка LOCALAPPDATA Windows.")
    # Compatibility: Slynor keeps the stage18 data directory; do not rename it.
    return Path(root) / "OwnPlayerMusic"


# Допустимое сгенерированное имя пользовательской обложки.
def is_cover_name(value):
    return isinstance(value, str) and COVER_NAME.fullmatch(value) is not None


# Разрешение только безопасного имени в папке обложек плейлистов.
def get_cover_path(filename):
    # Клиент получает доступ только к сгенерированному имени внутри covers.
    if not is_cover_name(filename):
        raise ValueError("Некорректное имя обложки.")
    folder = (get_data_dir() / "covers" / "playlists").resolve()
    path = folder / filename
    if path.resolve().parent != folder:
        raise ValueError("Недопустимый путь к обложке.")
    return path


# Удаление управляемой копии. Ошибка уборки не отменяет сохранение JSON.
def discard_cover(filename):
    if not filename:
        return
    try:
        get_cover_path(filename).unlink(missing_ok=True)
    except (OSError, ValueError):
        # Ошибка уборки не должна отменять уже сохранённое изменение JSON.
        logger.warning("Не удалось удалить старую копию обложки.", exc_info=True)


# Проверка, уменьшение и сохранение JPEG-копии выбранной картинки.
def import_cover(source):
    """Создать уменьшенную JPEG-копию; исходник не изменять."""
    source = Path(source)
    try:
        if not source.is_file():
            raise ValueError("Изображение не найдено.")
        if source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Изображение должно быть не больше 20 МБ.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Поддерживаются JPG, PNG и WEBP.")
                if original.width * original.height > MAX_PIXELS:
                    raise ValueError("Изображение слишком большое: максимум 40 млн пикселей.")
                original.load()
                oriented = ImageOps.exif_transpose(original)
                oriented.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                rgba = oriented.convert("RGBA")
                image = Image.new("RGB", rgba.size, "#18181b")
                image.paste(rgba, mask=rgba.getchannel("A"))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("Изображение слишком большое для безопасной обработки.") from error
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Не удалось прочитать изображение. Выбери другой JPG, PNG или WEBP.") from error

    filename = uuid4().hex + ".jpg"
    destination = get_cover_path(filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    try:
        # Не переносим EXIF и другие метаданные исходного изображения.
        image.save(temporary, format="JPEG", quality=90, optimize=True)
        temporary.replace(destination)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Не удалось сохранить копию обложки.") from error
    finally:
        image.close()
        rgba.close()
    return filename
