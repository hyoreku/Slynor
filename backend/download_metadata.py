"""Metadata for newly downloaded files; existing library files are never migrated."""
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

SIDECAR_SUFFIX = '.slynor.json'
MAX_RECORD = 32 * 1024
TAG_FORMATS = {'.mp3', '.m4a', '.flac', '.ogg', '.opus', '.wav'}
COVER_FILE = re.compile(r'[0-9a-f]{64}\.jpg')
COVER_PREFIX = '/api/assets/online-covers/'
MAX_MEASURED_MS = 24 * 60 * 60 * 1000


def text(value, limit=1000):
    if not isinstance(value, str): return None
    value = ''.join(c for c in value if ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF).strip()
    return value[:limit] or None


def probe_duration_ms(file):
    """Measure the downloaded file itself; a catalogue duration is not proof."""
    try:
        import av
        with av.open(str(file), mode='r', metadata_errors='ignore') as container:
            duration = container.duration
        if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
            value = int(round(duration / 1000))
            if 0 < value <= MAX_MEASURED_MS:
                return value
    except Exception:
        pass
    return None


def _cached_cover_file(track):
    """Copy an already fetched online cover into the durable track-cover cache."""
    url = track.get('cover_url')
    if not isinstance(url, str) or not url.startswith(COVER_PREFIX):
        return None
    name = url[len(COVER_PREFIX):]
    if not COVER_FILE.fullmatch(name):
        return None
    try:
        from backend.image_assets import MAX_FILE_BYTES, get_data_dir
        from backend.track_covers import store_cover_bytes
        path = get_data_dir() / 'online-covers' / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
        if not data or len(data) > MAX_FILE_BYTES:
            return None
        return store_cover_bytes(data)
    except Exception:
        return None


def measured(file, track):
    """Facts about this exact file: measured duration and a durable cover copy."""
    fields = {}
    duration = probe_duration_ms(file)
    if duration is not None:
        fields['duration_ms'] = duration
    cover = _cached_cover_file(track)
    if cover is not None:
        fields['cover_file'] = cover
    return fields


def recording(track):
    result = {key: text(track.get(key), 500 if key == 'artist' else 1000)
              for key in ('title', 'artist', 'album')}
    source = track.get('source')
    if source in ('youtube', 'spotify', 'soundcloud', 'hitmo', 'muzbomb', 'archive'):
        result['source'] = source
    if source in ('youtube', 'spotify', 'soundcloud'):
        from backend.provider_worker import validate_track_url
        try:
            result['permalink_url'] = validate_track_url(track.get('permalink_url'), source)
        except (ValueError, TypeError):
            pass
    # Neither signed media URLs, credentials nor machine paths are persisted.
    # A catalogue duration is not proof of this downloaded file's duration.
    return result


def display_stem(track):
    meta = recording(track)
    parts = [p for p in (meta.get('artist'), meta.get('title')) if p]
    value = ' - '.join(parts) or 'track'
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', value).strip(' .')
    value = value.encode('utf-16-le')[:200].decode('utf-16-le', 'ignore').rstrip(' .') or 'track'
    if re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def sidecar_path(file):
    file = Path(file)
    return file.with_name(file.name + SIDECAR_SUFFIX)


def read_download_metadata(file):
    """Read only a bounded companion record matching this file's size and mtime."""
    file = Path(file); companion = sidecar_path(file)
    try:
        if companion.is_symlink() or not companion.is_file() or companion.stat().st_size > MAX_RECORD:
            return {}
        with companion.open('rb') as stream:
            raw = stream.read(MAX_RECORD + 1)
        if len(raw) > MAX_RECORD: return {}
        data = json.loads(raw)
        stat = file.stat()
        if not isinstance(data, dict) or data.get('version') != 1 or data.get('file_name') != file.name:
            return {}
        if data.get('size_bytes') != stat.st_size or data.get('mtime_ns') != stat.st_mtime_ns:
            return {}
        item = data.get('recording')
        if not isinstance(item, dict):
            return {}
        result = recording(item)
        duration = item.get('duration_ms')
        if isinstance(duration, int) and not isinstance(duration, bool) and 0 < duration <= MAX_MEASURED_MS:
            result['duration_ms'] = duration
        cover = item.get('cover_file')
        if isinstance(cover, str) and COVER_FILE.fullmatch(cover):
            result['cover_file'] = cover
        return result
    except (OSError, ValueError, TypeError):
        return {}


def write_missing_tags(file, track, suffix=None):
    """Fill missing text tags on a temporary copy, without transcoding audio."""
    file = Path(file); suffix = suffix or file.suffix.lower()
    values = {k: v for k, v in recording(track).items() if k in ('title', 'artist', 'album') and v}
    if not values or suffix not in TAG_FORMATS: return 'unsupported'
    temporary = file.with_name('.slynor-tags-' + uuid4().hex + suffix)
    try:
        from mutagen import File
        from mutagen.wave import WAVE
        from mutagen.id3 import TIT2, TPE1, TALB
        shutil.copyfile(file, temporary)
        audio = File(temporary, easy=True)
        if audio is None: return 'unsupported'
        changed = False
        if isinstance(audio, WAVE):
            if audio.tags is None: audio.add_tags()
            for key, frame in (('title', TIT2), ('artist', TPE1), ('album', TALB)):
                if values.get(key) and not audio.tags.getall(frame.__name__):
                    audio.tags.add(frame(encoding=3, text=[values[key]])); changed = True
        else:
            if audio.tags is None: audio.add_tags()
            for key, value in values.items():
                if not audio.get(key): audio[key] = [value]; changed = True
        if not changed: return 'preserved'
        audio.save()
        # Only our unpublished temporary download is replaced.
        os.replace(temporary, file)
        return 'written'
    except Exception:
        # A failed tag edit leaves the source copy byte-for-byte intact.
        return 'failed'
    finally:
        temporary.unlink(missing_ok=True)


def remember_cached_metadata(file, track):
    """Add missing display metadata on explicit cache reuse; never edit the audio."""
    file = Path(file); companion = sidecar_path(file)
    if file.is_symlink() or not file.is_file(): return False
    if companion.exists() or companion.is_symlink():
        return bool(read_download_metadata(file))
    temporary = companion.with_name(companion.name + '.' + uuid4().hex + '.tmp')
    reserved = False
    try:
        with companion.open('xb'): pass
        reserved = True; stat = file.stat()
        payload = {'version': 1, 'file_name': file.name, 'size_bytes': stat.st_size,
                   'mtime_ns': stat.st_mtime_ns,
                   'recording': {**recording(track), **measured(file, track)}}
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2); stream.write('\n')
        os.replace(temporary, companion); reserved = False
        return True
    except OSError:
        return False
    finally:
        temporary.unlink(missing_ok=True)
        if reserved: companion.unlink(missing_ok=True)


def publish_download(file, folder, track, suffix=None):
    """Publish a new file and companion metadata, reserving both names exclusively."""
    file, folder = Path(file), Path(folder)
    suffix = (suffix or file.suffix).lower()
    if suffix not in {'.mp3', '.m4a', '.opus', '.ogg', '.webm', '.aac', '.flac', '.wav'}:
        raise ValueError('Unsupported download container')
    stem = display_stem(track); number = 1
    folder.mkdir(parents=True, exist_ok=True)
    while True:
        name = stem + ('' if number == 1 else ' (' + str(number) + ')') + suffix
        target = folder / name; companion = sidecar_path(target)
        # Never replace a previous audio file or its companion record.
        try:
            with target.open('xb'): pass
        except FileExistsError:
            number += 1; continue
        try:
            with companion.open('xb'): pass
        except FileExistsError:
            target.unlink(); number += 1; continue
        except BaseException:
            target.unlink(); raise
        break
    pending = companion.with_name(companion.name + '.' + uuid4().hex + '.tmp')
    audio_published = False
    try:
        stat = file.stat()
        payload = {'version': 1, 'file_name': target.name, 'size_bytes': stat.st_size,
                   'mtime_ns': stat.st_mtime_ns,
                   'recording': {**recording(track), **measured(file, track)}}
        with pending.open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        # Publish metadata first: it does not match the empty reservation.
        os.replace(pending, companion)
        os.replace(file, target); audio_published = True
        return target
    finally:
        pending.unlink(missing_ok=True)
        if not audio_published:
            # These names were created exclusively by this invocation.
            target.unlink(missing_ok=True); companion.unlink(missing_ok=True)
