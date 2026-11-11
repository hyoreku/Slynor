"""SQLite storage for the Music Service.

WAL keeps readers working while the dispatcher writes; the schema version is
explicit because the service will outlive the current code. The skeleton uses
the standard library instead of an ORM so every query is visible and testable.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import ntpath
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .models import AudioAsset, AudioCandidate, Delivery, DeliveryStatus, Recording

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE recordings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artists TEXT NOT NULL DEFAULT '[]',
    album TEXT,
    duration_ms INTEGER,
    version TEXT,
    external_ids TEXT NOT NULL DEFAULT '{}',
    metadata_origin TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE audio_candidates (
    id TEXT PRIMARY KEY,
    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    duration_ms INTEGER,
    availability TEXT NOT NULL,
    version_hint TEXT,
    score REAL,
    created_at TEXT NOT NULL,
    UNIQUE (provider, provider_id)
);
CREATE TABLE audio_assets (
    id TEXT PRIMARY KEY,
    candidate_id TEXT REFERENCES audio_candidates(id) ON DELETE SET NULL,
    container TEXT NOT NULL,
    codec TEXT,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    duration_ms INTEGER,
    origin TEXT,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE deliveries (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES audio_assets(id) ON DELETE CASCADE,
    target TEXT NOT NULL,
    status TEXT NOT NULL,
    external_ref TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX jobs_status_created ON jobs(status, created_at);
CREATE INDEX candidates_recording ON audio_candidates(recording_id);
CREATE INDEX deliveries_asset ON deliveries(asset_id);
"""

# The player library and playlists live in the same database, so the bot and
# the desktop read and write one state. Version 1 databases gain these tables
# in place; nothing in the earlier tables is rewritten.
LIBRARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_tracks (
    path_key TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cover TEXT,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    path_key TEXT NOT NULL,
    path TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (playlist_id, path_key)
);
CREATE TABLE IF NOT EXISTS library_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO library_meta (id, revision, updated_at)
VALUES (1, 0, '1970-01-01T00:00:00+00:00');
CREATE INDEX IF NOT EXISTS playlist_items_order ON playlist_items(playlist_id, position);
"""
SCHEMA = SCHEMA + LIBRARY_SCHEMA


def default_db_path() -> Path:
    override = os.environ.get('SLYNOR_SERVICE_DB')
    if override:
        return Path(override)
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home())
    return base / 'Slynor' / 'service' / 'service.sqlite3'


def connect(path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA synchronous=NORMAL')
    migrate(connection)
    return connection


def migrate(connection) -> None:
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f'Database schema {version} is newer than supported {SCHEMA_VERSION}.')
    if version == 0:
        connection.executescript(SCHEMA)
        connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    elif version == 1:
        connection.executescript(LIBRARY_SCHEMA)
        connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def save_recording(connection, recording: Recording) -> Recording:
    connection.execute(
        'INSERT INTO recordings (id, title, artists, album, duration_ms, version,'
        ' external_ids, metadata_origin, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (recording.id, recording.title, json.dumps(recording.artists, ensure_ascii=False),
         recording.album, recording.duration_ms, recording.version,
         json.dumps(recording.external_ids, ensure_ascii=False),
         recording.metadata_origin, iso(recording.created_at)))
    return recording


def _recording(row) -> Recording:
    return Recording(
        id=row['id'], title=row['title'], artists=json.loads(row['artists']),
        album=row['album'], duration_ms=row['duration_ms'], version=row['version'],
        external_ids=json.loads(row['external_ids']), metadata_origin=row['metadata_origin'],
        created_at=parse(row['created_at']))


def _candidate(row) -> AudioCandidate:
    return AudioCandidate(
        id=row['id'], recording_id=row['recording_id'], provider=row['provider'],
        provider_id=row['provider_id'], duration_ms=row['duration_ms'],
        availability=row['availability'], version_hint=row['version_hint'],
        score=row['score'], created_at=parse(row['created_at']))


def _asset(row) -> AudioAsset:
    return AudioAsset(
        id=row['id'], candidate_id=row['candidate_id'], container=row['container'],
        codec=row['codec'], sha256=row['sha256'], size_bytes=row['size_bytes'],
        duration_ms=row['duration_ms'], origin=row['origin'], path=row['path'],
        created_at=parse(row['created_at']))


def get_recording(connection, recording_id: str) -> Recording | None:
    row = connection.execute('SELECT * FROM recordings WHERE id=?', (recording_id,)).fetchone()
    return None if row is None else _recording(row)


def list_recordings(connection, limit: int = 100) -> list[Recording]:
    rows = connection.execute(
        'SELECT * FROM recordings ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
    return [_recording(row) for row in rows]


def save_candidate(connection, candidate: AudioCandidate) -> AudioCandidate:
    connection.execute(
        'INSERT INTO audio_candidates (id, recording_id, provider, provider_id, duration_ms,'
        ' availability, version_hint, score, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (candidate.id, candidate.recording_id, candidate.provider, candidate.provider_id,
         candidate.duration_ms, candidate.availability.value, candidate.version_hint,
         candidate.score, iso(candidate.created_at)))
    return candidate


def list_candidates(connection, recording_id: str) -> list[AudioCandidate]:
    rows = connection.execute(
        'SELECT * FROM audio_candidates WHERE recording_id=? ORDER BY created_at',
        (recording_id,)).fetchall()
    return [_candidate(row) for row in rows]


def get_candidate(connection, candidate_id: str) -> AudioCandidate | None:
    row = connection.execute(
        'SELECT * FROM audio_candidates WHERE id=?', (candidate_id,)).fetchone()
    return None if row is None else _candidate(row)


def find_candidate(connection, provider: str, provider_id: str) -> AudioCandidate | None:
    row = connection.execute(
        'SELECT * FROM audio_candidates WHERE provider=? AND provider_id=?',
        (provider, provider_id)).fetchone()
    return None if row is None else _candidate(row)


def save_asset(connection, asset: AudioAsset) -> AudioAsset:
    connection.execute(
        'INSERT INTO audio_assets (id, candidate_id, container, codec, sha256, size_bytes,'
        ' duration_ms, origin, path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
        (asset.id, asset.candidate_id, asset.container, asset.codec, asset.sha256,
         asset.size_bytes, asset.duration_ms, asset.origin, asset.path,
         iso(asset.created_at)))
    return asset


def get_asset(connection, asset_id: str) -> AudioAsset | None:
    row = connection.execute('SELECT * FROM audio_assets WHERE id=?', (asset_id,)).fetchone()
    return None if row is None else _asset(row)


def find_asset_by_sha256(connection, digest: str) -> AudioAsset | None:
    row = connection.execute(
        'SELECT * FROM audio_assets WHERE sha256=? ORDER BY created_at LIMIT 1', (digest,)).fetchone()
    return None if row is None else _asset(row)


def save_delivery(connection, delivery: Delivery) -> Delivery:
    connection.execute(
        'INSERT INTO deliveries (id, asset_id, target, status, external_ref, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (delivery.id, delivery.asset_id, delivery.target, delivery.status.value,
         delivery.external_ref, iso(delivery.created_at)))
    return delivery


def _delivery(row) -> Delivery:
    return Delivery(id=row['id'], asset_id=row['asset_id'], target=row['target'],
                    status=row['status'], external_ref=row['external_ref'],
                    created_at=parse(row['created_at']))


def get_delivery(connection, delivery_id: str) -> Delivery | None:
    row = connection.execute(
        'SELECT * FROM deliveries WHERE id=?', (delivery_id,)).fetchone()
    return None if row is None else _delivery(row)


def find_delivery(connection, asset_id: str, target: str) -> Delivery | None:
    row = connection.execute(
        'SELECT * FROM deliveries WHERE asset_id=? AND target=? ORDER BY created_at DESC LIMIT 1',
        (asset_id, target)).fetchone()
    return None if row is None else _delivery(row)


def set_delivery_status(connection, delivery_id: str, status, external_ref=None) -> Delivery:
    connection.execute(
        'UPDATE deliveries SET status=?, external_ref=? WHERE id=?',
        (DeliveryStatus(status).value, external_ref, delivery_id))
    stored = get_delivery(connection, delivery_id)
    if stored is None:
        raise ValueError('Доставка не найдена.')
    return stored


def list_deliveries(connection, asset_id: str) -> list[Delivery]:
    rows = connection.execute(
        'SELECT * FROM deliveries WHERE asset_id=? ORDER BY created_at', (asset_id,)).fetchall()
    return [_delivery(row) for row in rows]

# --- Shared player library and playlists ------------------------------------

PLAYLIST_ID = re.compile(r'^[A-Za-z0-9-]{1,64}$')
PLAYLIST_COVER = re.compile(r'^[0-9a-f]{32}\.jpg$')
MAX_LIBRARY_TRACKS = 200000
MAX_PLAYLISTS = 2000
MAX_PLAYLIST_TRACKS = 100000


def _now() -> str:
    return iso(datetime.now(timezone.utc))


@contextmanager
def transaction(connection):
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
    except BaseException:
        connection.execute('ROLLBACK')
        raise
    else:
        connection.execute('COMMIT')


def _bump_library(connection) -> None:
    connection.execute(
        'UPDATE library_meta SET revision=revision+1, updated_at=? WHERE id=1', (_now(),))


def clean_track_path(value):
    if not isinstance(value, str):
        raise ValueError('Некорректный путь к треку.')
    path = value.strip().strip('"')
    if not path or len(path) > 4096 or any(ord(c) < 32 for c in path):
        raise ValueError('Некорректный путь к треку.')
    return path


def track_path_key(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def clean_track_paths(values):
    if not isinstance(values, list) or len(values) > MAX_LIBRARY_TRACKS:
        raise ValueError('Некорректный список треков.')
    paths = []
    seen = set()
    for value in values:
        path = clean_track_path(value)
        key = track_path_key(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def clean_playlist_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Введите название плейлиста.')
    name = value.strip()
    if len(name) > 100:
        raise ValueError('Название должно быть не длиннее 100 символов.')
    return name


def clean_playlist_id(value):
    if not isinstance(value, str) or not PLAYLIST_ID.fullmatch(value):
        raise ValueError('Некорректный идентификатор плейлиста.')
    return value


def clean_playlist_cover(value):
    if value is None:
        return None
    if (not isinstance(value, str) or not PLAYLIST_COVER.fullmatch(value)
            or value in ('.', '..') or '..' in value):
        raise ValueError('Некорректная ссылка на обложку плейлиста.')
    return value


def library_state(connection):
    row = connection.execute(
        'SELECT revision, updated_at FROM library_meta WHERE id=1').fetchone()
    return {'revision': row['revision'], 'updated_at': row['updated_at']}


def load_library(connection):
    rows = connection.execute(
        'SELECT path FROM library_tracks ORDER BY position, rowid').fetchall()
    return [row['path'] for row in rows]


def replace_library(connection, paths):
    cleaned = clean_track_paths(paths)
    now = _now()
    with transaction(connection):
        connection.execute('DELETE FROM library_tracks')
        connection.executemany(
            'INSERT INTO library_tracks (path_key, path, position, added_at) VALUES (?,?,?,?)',
            [(track_path_key(path), path, index, now) for index, path in enumerate(cleaned)])
        _bump_library(connection)
    return load_library(connection)


def load_playlists(connection):
    playlists = connection.execute(
        'SELECT id, name, cover FROM playlists ORDER BY position, rowid').fetchall()
    items = connection.execute(
        'SELECT playlist_id, path FROM playlist_items ORDER BY playlist_id, position, rowid').fetchall()
    tracks = {row['id']: [] for row in playlists}
    for item in items:
        if item['playlist_id'] in tracks:
            tracks[item['playlist_id']].append(item['path'])
    result = []
    for row in playlists:
        item = {'id': row['id'], 'name': row['name'], 'tracks': tracks[row['id']]}
        if row['cover'] is not None:
            item['cover'] = row['cover']
        result.append(item)
    return result


def playlist_state(connection):
    return {'playlists': load_playlists(connection), **library_state(connection)}


def _validate_playlists(values):
    if not isinstance(values, list) or len(values) > MAX_PLAYLISTS:
        raise ValueError('Некорректный список плейлистов.')
    result = []
    seen_ids = set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('Некорректная запись плейлиста.')
        identifier = clean_playlist_id(item.get('id'))
        name = clean_playlist_name(item.get('name'))
        if identifier in seen_ids:
            raise ValueError('Повторяющийся плейлист.')
        tracks = clean_track_paths(item.get('tracks') or [])
        if len(tracks) > MAX_PLAYLIST_TRACKS:
            raise ValueError('Слишком много треков в плейлисте.')
        cover = clean_playlist_cover(item.get('cover'))
        result.append({'id': identifier, 'name': name, 'tracks': tracks, 'cover': cover})
        seen_ids.add(identifier)
    return result


def _insert_playlist(connection, item, position, now):
    connection.execute(
        'INSERT INTO playlists (id, name, cover, position, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?)',
        (item['id'], item['name'], item['cover'], position, now, now))
    connection.executemany(
        'INSERT INTO playlist_items (playlist_id, path_key, path, position, added_at)'
        ' VALUES (?,?,?,?,?)',
        [(item['id'], track_path_key(path), path, index, now)
         for index, path in enumerate(item['tracks'])])


def replace_playlists(connection, values):
    cleaned = _validate_playlists(values)
    now = _now()
    with transaction(connection):
        connection.execute('DELETE FROM playlists')  # playlist_items cascade
        for position, item in enumerate(cleaned):
            _insert_playlist(connection, item, position, now)
        _bump_library(connection)
    return load_playlists(connection)


def _find_playlist(connection, playlist_id):
    return connection.execute(
        'SELECT * FROM playlists WHERE id=?', (playlist_id,)).fetchone()


def _next_playlist_position(connection):
    row = connection.execute(
        'SELECT COALESCE(MAX(position), -1) AS value FROM playlists').fetchone()
    return row['value'] + 1


def create_playlist(connection, name):
    name = clean_playlist_name(name)
    with transaction(connection):
        existing = connection.execute('SELECT name FROM playlists').fetchall()
        if any(row['name'].casefold() == name.casefold() for row in existing):
            raise ValueError('Плейлист с таким названием уже существует.')
        now = _now()
        connection.execute(
            'INSERT INTO playlists (id, name, cover, position, created_at, updated_at)'
            ' VALUES (?,?,?,?,?,?)',
            (str(uuid4()), name, None, _next_playlist_position(connection), now, now))
        _bump_library(connection)
    return load_playlists(connection)


def rename_playlist(connection, playlist_id, name):
    playlist_id = clean_playlist_id(playlist_id)
    name = clean_playlist_name(name)
    with transaction(connection):
        if _find_playlist(connection, playlist_id) is None:
            raise ValueError('Плейлист не найден.')
        others = connection.execute(
            'SELECT id, name FROM playlists WHERE id<>?', (playlist_id,)).fetchall()
        if any(row['name'].casefold() == name.casefold() for row in others):
            raise ValueError('Плейлист с таким названием уже существует.')
        connection.execute('UPDATE playlists SET name=?, updated_at=? WHERE id=?',
                           (name, _now(), playlist_id))
        _bump_library(connection)
    return load_playlists(connection)


def delete_playlist(connection, playlist_id):
    playlist_id = clean_playlist_id(playlist_id)
    with transaction(connection):
        if _find_playlist(connection, playlist_id) is None:
            raise ValueError('Плейлист не найден.')
        connection.execute('DELETE FROM playlists WHERE id=?', (playlist_id,))
        _bump_library(connection)
    return load_playlists(connection)


def add_track_to_playlist(connection, playlist_id, path):
    playlist_id = clean_playlist_id(playlist_id)
    path = clean_track_path(path)
    key = track_path_key(path)
    with transaction(connection):
        if _find_playlist(connection, playlist_id) is None:
            raise ValueError('Плейлист не найден.')
        exists = connection.execute(
            'SELECT 1 FROM playlist_items WHERE playlist_id=? AND path_key=?',
            (playlist_id, key)).fetchone()
        if exists is None:
            row = connection.execute(
                'SELECT COALESCE(MAX(position), -1) AS value FROM playlist_items WHERE playlist_id=?',
                (playlist_id,)).fetchone()
            connection.execute(
                'INSERT INTO playlist_items (playlist_id, path_key, path, position, added_at)'
                ' VALUES (?,?,?,?,?)', (playlist_id, key, path, row['value'] + 1, _now()))
            _bump_library(connection)
    return load_playlists(connection)


def add_track_to_playlists(connection, playlist_ids, path):
    if (not isinstance(playlist_ids, (list, tuple)) or not playlist_ids
            or len(playlist_ids) > 100):
        raise ValueError('Выберите хотя бы один плейлист.')
    identifiers = list(dict.fromkeys(clean_playlist_id(value) for value in playlist_ids))
    path = clean_track_path(path)
    key = track_path_key(path)
    changed = False
    with transaction(connection):
        for identifier in identifiers:
            if _find_playlist(connection, identifier) is None:
                raise ValueError('Плейлист не найден.')
        for identifier in identifiers:
            exists = connection.execute(
                'SELECT 1 FROM playlist_items WHERE playlist_id=? AND path_key=?',
                (identifier, key)).fetchone()
            if exists is not None:
                continue
            row = connection.execute(
                'SELECT COALESCE(MAX(position), -1) AS value FROM playlist_items WHERE playlist_id=?',
                (identifier,)).fetchone()
            connection.execute(
                'INSERT INTO playlist_items (playlist_id, path_key, path, position, added_at)'
                ' VALUES (?,?,?,?,?)', (identifier, key, path, row['value'] + 1, _now()))
            changed = True
        if changed:
            _bump_library(connection)
    return load_playlists(connection)


def remove_track_from_playlist(connection, playlist_id, path):
    playlist_id = clean_playlist_id(playlist_id)
    key = track_path_key(clean_track_path(path))
    with transaction(connection):
        if _find_playlist(connection, playlist_id) is None:
            raise ValueError('Плейлист не найден.')
        cursor = connection.execute(
            'DELETE FROM playlist_items WHERE playlist_id=? AND path_key=?', (playlist_id, key))
        if cursor.rowcount:
            _bump_library(connection)
    return load_playlists(connection)


def set_playlist_cover(connection, playlist_id, cover):
    playlist_id = clean_playlist_id(playlist_id)
    cover = clean_playlist_cover(cover)
    with transaction(connection):
        if _find_playlist(connection, playlist_id) is None:
            raise ValueError('Плейлист не найден.')
        connection.execute('UPDATE playlists SET cover=?, updated_at=? WHERE id=?',
                           (cover, _now(), playlist_id))
        _bump_library(connection)
    return load_playlists(connection)

