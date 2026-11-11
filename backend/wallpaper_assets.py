"""Проверка и хранение пользовательских обоев."""

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


# Допустимое сгенерированное имя пользовательских обоев.
def is_cover_name(value):
    return isinstance(value, str) and COVER_NAME.fullmatch(value) is not None


# Разрешение только безопасного имени в папке обоев.
def get_wallpaper_path(filename):
    # Клиент получает доступ только к сгенерированному имени внутри wallpapers.
    if not is_cover_name(filename):
        raise ValueError("Некорректное имя обоев.")
    folder = (get_data_dir() / "wallpapers").resolve()
    path = folder / filename
    if path.resolve().parent != folder:
        raise ValueError("Недопустимый путь к обоям.")
    return path


# Удаление управляемой копии. Ошибка уборки не отменяет сохранение JSON.
def discard_wallpaper(filename):
    if not filename:
        return
    try:
        get_wallpaper_path(filename).unlink(missing_ok=True)
    except (OSError, ValueError):
        # Ошибка уборки не должна отменять уже сохранённое изменение JSON.
        logger.warning("Не удалось удалить старую копию обоев.", exc_info=True)


# Проверка, уменьшение и сохранение JPEG-копии выбранной картинки.
def import_wallpaper(source):
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
                oriented.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
                rgba = oriented.convert("RGBA")
                image = Image.new("RGB", rgba.size, "#18181b")
                image.paste(rgba, mask=rgba.getchannel("A"))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("Изображение слишком большое для безопасной обработки.") from error
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Не удалось прочитать изображение. Выбери другой JPG, PNG или WEBP.") from error

    filename = uuid4().hex + ".jpg"
    destination = get_wallpaper_path(filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    try:
        # Не переносим EXIF и другие метаданные исходного изображения.
        image.save(temporary, format="JPEG", quality=90, optimize=True)
        temporary.replace(destination)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Не удалось сохранить копию обоев.") from error
    finally:
        image.close()
        rgba.close()
    return filename

# Animated backgrounds have their own strict caps. Static imports keep the existing JPEG path.
_import_static=import_wallpaper
COVER_NAME=re.compile(r'[0-9a-f]{32}\.(jpg|gif|mp4|webm)')
def import_wallpaper(source):
    import json,shutil,subprocess
    source=Path(source);extension=source.suffix.lower()
    if extension not in ('.gif','.mp4','.webm'):return _import_static(source)
    if not source.is_file() or source.stat().st_size>50*1024*1024:raise ValueError('Анимированный фон: максимум 50 МБ.')
    if extension=='.gif':
        try:
            with Image.open(source) as image:
                if image.format!='GIF' or image.width>2560 or image.height>1440:raise ValueError('GIF: максимум 2560×1440.')
                if image.n_frames>300:raise ValueError('GIF: максимум 300 кадров.')
                duration=0
                for i in range(image.n_frames):
                    image.seek(i);duration+=max(20,int(image.info.get('duration',100)))
                if duration>30000:raise ValueError('GIF: максимум 30 секунд на цикл.')
        except (UnidentifiedImageError,Image.DecompressionBombError,OSError) as error:raise ValueError('Не удалось проверить GIF.') from error
    else:
        ffmpeg=os.environ.get('OWNPLAYER_FFMPEG');probe=None
        if ffmpeg:
            candidate=Path(ffmpeg).with_name('ffprobe.exe' if os.name=='nt' else 'ffprobe')
            if candidate.is_file():probe=str(candidate)
        probe=probe or shutil.which('ffprobe')
        if not probe:raise ValueError('Для видеофона нужен ffprobe: запустите INSTALL.ps1 или установите FFmpeg.')
        try:
            result=subprocess.run([probe,'-protocol_whitelist','file,pipe','-v','error','-show_entries','stream=codec_type,codec_name,width,height:format=duration','-of','json',str(source.resolve())],capture_output=True,timeout=8,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
            if result.returncode!=0 or len(result.stdout)>20000:raise ValueError()
            data=json.loads(result.stdout);duration=float(data.get('format',{}).get('duration',0))
            videos=[s for s in data.get('streams',[]) if s.get('codec_type')=='video']
            if not 0<duration<=30 or len(videos)!=1:raise ValueError()
            stream=videos[0]
            if stream.get('codec_name') not in ('h264','vp8','vp9','av1') or not 0<int(stream.get('width',0))<=2560 or not 0<int(stream.get('height',0))<=1440:raise ValueError()
        except (ValueError,KeyError,subprocess.SubprocessError,OSError):raise ValueError('Видео: до 30 с, 2560×1440, MP4 H.264 или WebM VP8/VP9/AV1. Файл не принят.') from None
    filename=uuid4().hex+extension;destination=get_wallpaper_path(filename);destination.parent.mkdir(parents=True,exist_ok=True);temporary=destination.with_suffix('.tmp')
    try:shutil.copyfile(source,temporary);os.replace(temporary,destination)
    finally:temporary.unlink(missing_ok=True)
    return filename
