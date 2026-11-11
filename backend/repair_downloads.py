"""Repair missing metadata of already downloaded files; audio is never modified.

Only fields that are absent are added: a measured duration and, for YouTube
tracks, a durable cover copy from the public thumbnail. Existing text tags,
unknown fields and the audio bytes stay untouched.
"""
import json
import os
from pathlib import Path
import re

from .download_metadata import COVER_FILE, MAX_RECORD, probe_duration_ms
from .track_covers import store_cover_bytes

YOUTUBE_URL = re.compile(
    r'^https://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})(?:[&#?].*)?$')
THUMBNAIL_URL = re.compile(
    r'^https://i\.ytimg\.com/vi/[A-Za-z0-9_-]{11}/(?:hq|mq|sd)default\.jpg$')
COVER_TIMEOUT = 15
MAX_COVER_RESPONSE = 5 * 1024 * 1024


def youtube_cover_url(permalink):
    """Public thumbnail of an individual YouTube video; no other hosts are used."""
    if not isinstance(permalink, str):
        return None
    match = YOUTUBE_URL.match(permalink)
    if not match:
        return None
    return 'https://i.ytimg.com/vi/' + match.group(1) + '/hqdefault.jpg'


def fetch_youtube_cover(url):
    """Bounded fetch of a thumbnail; any failure simply leaves the cover absent."""
    if not isinstance(url, str) or not THUMBNAIL_URL.fullmatch(url):
        return None
    try:
        import httpx
        with httpx.Client(timeout=COVER_TIMEOUT, follow_redirects=False, trust_env=True) as client:
            response = client.get(url)
        if response.status_code != 200:
            return None
        if not response.headers.get('content-type', '').startswith('image/'):
            return None
        data = response.content
        if not data or len(data) > MAX_COVER_RESPONSE:
            return None
        return data
    except Exception:
        return None


def load_sidecar(sidecar, audio):
    """Raw sidecar payload only when it still matches this exact file."""
    sidecar, audio = Path(sidecar), Path(audio)
    try:
        if sidecar.is_symlink() or not sidecar.is_file() or sidecar.stat().st_size > MAX_RECORD:
            return None
        data = json.loads(sidecar.read_bytes())
        stat = audio.stat()
        if (not isinstance(data, dict) or data.get('version') != 1
                or data.get('file_name') != audio.name
                or data.get('size_bytes') != stat.st_size
                or data.get('mtime_ns') != stat.st_mtime_ns):
            return None
        return data
    except (OSError, ValueError, TypeError):
        return None


def _valid_duration(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 24 * 60 * 60 * 1000


def _write_sidecar(sidecar, data):
    sidecar = Path(sidecar)
    backup = sidecar.with_name(sidecar.name + '.bak')
    if not backup.exists():
        backup.write_bytes(sidecar.read_bytes())
    temporary = sidecar.with_name(sidecar.name + '.repair.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, sidecar)
    finally:
        temporary.unlink(missing_ok=True)


def repair(folder, apply=False, fetch_cover=None):
    """Scan companion records and add only missing measured fields."""
    folder = Path(folder)
    report = {'folder': str(folder), 'apply': bool(apply), 'scanned': 0, 'updated': 0,
              'files': [], 'skipped': 0, 'errors': []}
    if not folder.is_dir():
        report['errors'].append('folder_missing')
        return report
    for sidecar in sorted(folder.glob('*' + '.slynor.json')):
        audio = sidecar.with_name(sidecar.name[:-len('.slynor.json')])
        if not audio.is_file() or audio.is_symlink():
            report['skipped'] += 1
            continue
        report['scanned'] += 1
        data = load_sidecar(sidecar, audio)
        if data is None:
            report['skipped'] += 1
            continue
        record = data.get('recording')
        record = dict(record) if isinstance(record, dict) else {}
        changes = []
        pending_cover = False
        if not _valid_duration(record.get('duration_ms')):
            duration = probe_duration_ms(audio)
            if duration is not None:
                record['duration_ms'] = duration
                changes.append('duration_ms')
        cover = record.get('cover_file')
        if not (isinstance(cover, str) and COVER_FILE.fullmatch(cover)):
            url = youtube_cover_url(record.get('permalink_url'))
            if url and fetch_cover is not None:
                if apply:
                    data_bytes = fetch_cover(url)
                    if data_bytes:
                        try:
                            record['cover_file'] = store_cover_bytes(data_bytes)
                            changes.append('cover_file')
                        except (OSError, ValueError):
                            report['errors'].append(audio.name + ': cover_rejected')
                else:
                    # A dry run must not write the cover cache either.
                    pending_cover = True
        if not changes and not pending_cover:
            continue
        data['recording'] = record
        entry = {'file': audio.name, 'changes': changes, 'applied': False}
        if pending_cover:
            entry['would_fetch_cover'] = True
        if apply:
            try:
                _write_sidecar(sidecar, data)
                entry['applied'] = True
                report['updated'] += 1
            except OSError:
                report['errors'].append(audio.name + ': write_failed')
                continue
        report['files'].append(entry)
    return report
