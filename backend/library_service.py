"""Shared library and playlists: Music Service state with a local JSON cache.

The service owns the shared state while it runs, so the player and the future
bot read one library. JSON files stay as the offline cache and as the migration
source. Offline edits are marked dirty and pushed back when the service returns;
a successful service write refreshes the cache. Conflicts are last-writer-wins
and the previous JSON files are copied to *.pre-service.bak before the first
service sync.
"""
import json
import ntpath
from pathlib import Path
from threading import RLock, Thread
from time import monotonic, sleep
from uuid import uuid4

from .image_assets import get_data_dir, is_cover_name
from .service_client import ServiceError, ServiceUnavailable


class LibraryService:
    def __init__(self, data_dir=None, client=None):
        self.root = Path(data_dir) if data_dir else get_data_dir()
        self.client = client
        self.library_path = self.root / 'library.json'
        self.playlists_path = self.root / 'playlists.json'
        self.dirty_path = self.root / 'library.sync.json'
        self.lock = RLock()
        self._synced = False
        self._sync_started = False
        self._last_error = None

    # --- local cache -----------------------------------------------------
    def _read_library_file(self):
        if not self.library_path.exists():
            return []
        try:
            data = json.loads(self.library_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError('Не удалось прочитать library.json. Файл не был изменён.') from error
        if not isinstance(data, dict) or data.get('version') != 1:
            raise RuntimeError('Неизвестный формат library.json.')
        files = data.get('files')
        if not isinstance(files, list) or not all(
                isinstance(path, str) and path.strip() for path in files):
            raise RuntimeError('Некорректный список файлов в library.json.')
        return files

    def _write_library_file(self, files):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / 'library.json.tmp'
        try:
            temporary.write_text(json.dumps({'version': 1, 'files': files},
                                            ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.library_path)
        except OSError as error:
            raise RuntimeError('Не удалось сохранить библиотеку. Проверьте папку данных.') from error

    def _read_playlists_file(self):
        if not self.playlists_path.exists():
            return []
        try:
            data = json.loads(self.playlists_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError('Не удалось прочитать playlists.json. Файл не был изменён.') from error
        if not isinstance(data, dict) or data.get('version') != 1:
            raise RuntimeError('Неизвестный формат playlists.json.')
        playlists = data.get('playlists')
        if not isinstance(playlists, list):
            raise RuntimeError('Некорректный список плейлистов.')
        seen = set()
        for item in playlists:
            if not isinstance(item, dict):
                raise RuntimeError('Некорректная запись плейлиста.')
            identifier, name = item.get('id'), item.get('name')
            if (not isinstance(identifier, str) or not identifier or identifier in seen
                    or not isinstance(name, str) or not name.strip()):
                raise RuntimeError('Некорректный идентификатор или название плейлиста.')
            tracks = item.get('tracks', [])
            if not isinstance(tracks, list) or not all(
                    isinstance(path, str) and path.strip() for path in tracks):
                raise RuntimeError('Некорректный список треков плейлиста.')
            cover = item.get('cover')
            if cover is not None and not is_cover_name(cover):
                raise RuntimeError('Некорректная ссылка на обложку плейлиста.')
            item['tracks'] = tracks
            seen.add(identifier)
        return playlists

    def _write_playlists_file(self, playlists):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / 'playlists.json.tmp'
        try:
            temporary.write_text(json.dumps({'version': 1, 'playlists': playlists},
                                            ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.playlists_path)
        except OSError as error:
            raise RuntimeError('Не удалось сохранить плейлисты.') from error

    # --- dirty marker ----------------------------------------------------
    def _dirty(self):
        if not self.dirty_path.exists():
            return False
        try:
            data = json.loads(self.dirty_path.read_text(encoding='utf-8'))
            return bool(data.get('dirty'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True

    def _mark_dirty(self, value):
        try:
            if value:
                self.root.mkdir(parents=True, exist_ok=True)
                self.dirty_path.write_text(json.dumps(
                    {'version': 1, 'dirty': True, 'updated_at': _now()}), encoding='utf-8')
            else:
                self.dirty_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _backup(self, path):
        if not path.is_file():
            return
        backup = path.with_name(path.name + '.pre-service.bak')
        if backup.exists():
            return
        try:
            backup.write_bytes(path.read_bytes())
        except OSError:
            pass

    def _cache_library(self, tracks):
        try:
            if self._read_library_file() == tracks:
                return
        except RuntimeError:
            pass
        self._write_library_file(tracks)

    def _cache_playlists(self, playlists):
        try:
            if self._read_playlists_file() == playlists:
                return
        except RuntimeError:
            pass
        self._write_playlists_file(playlists)

    # --- service sync ----------------------------------------------------
    def _service_ready(self):
        return self.client is not None and self.client.health()

    def _ensure_synced(self):
        if self.client is None:
            return False
        if self._synced and not self._dirty():
            return True
        if not self._service_ready():
            return False
        return self.sync()

    def sync(self):
        with self.lock:
            if self.client is None or not self.client.health():
                return False
            try:
                local_library = self._read_library_file()
                local_playlists = self._read_playlists_file()
            except RuntimeError as error:
                self._last_error = str(error)
                return False
            try:
                service_library = self.client.library()['tracks']
                service_playlists = self.client.playlists()
            except (ServiceUnavailable, ServiceError) as error:
                self._last_error = str(error)
                return False
            dirty = self._dirty()
            service_empty = not service_library and not service_playlists
            local_has_data = bool(local_library or local_playlists)
            try:
                if dirty or (service_empty and local_has_data):
                    self._backup(self.library_path)
                    self._backup(self.playlists_path)
                    stored_library = self.client.replace_library(local_library)
                    stored_playlists = self.client.replace_playlists(local_playlists)
                    self._cache_library(stored_library)
                    self._cache_playlists(stored_playlists)
                else:
                    if service_library != local_library or service_playlists != local_playlists:
                        self._backup(self.library_path)
                        self._backup(self.playlists_path)
                    self._cache_library(service_library)
                    self._cache_playlists(service_playlists)
                self._mark_dirty(False)
                self._synced = True
                self._last_error = None
                return True
            except (ServiceUnavailable, ServiceError) as error:
                self._last_error = str(error)
                return False

    def start_background(self):
        with self.lock:
            if self._sync_started or self.client is None:
                return
            self._sync_started = True
        Thread(target=self._background_sync, name='library-sync', daemon=True).start()

    def _background_sync(self):
        try:
            for _ in range(30):
                if self._service_ready() and self.sync():
                    return
                sleep(1.0)
        finally:
            with self.lock:
                self._sync_started = False

    # --- library ---------------------------------------------------------
    def load_library(self):
        with self.lock:
            if self._ensure_synced():
                try:
                    data = self.client.library()
                except (ServiceUnavailable, ServiceError):
                    self._synced = False
                else:
                    self._cache_library(data['tracks'])
                    return data['tracks']
            else:
                self.start_background()
            return self._read_library_file()

    def save_library(self, files):
        if not isinstance(files, list) or not all(
                isinstance(path, str) and path.strip() for path in files):
            raise ValueError('Ожидался список непустых путей к файлам.')
        with self.lock:
            if self._ensure_synced():
                try:
                    stored = self.client.replace_library(files)
                except ServiceUnavailable:
                    self._synced = False
                except ServiceError as error:
                    raise ValueError(str(error)) from error
                else:
                    self._cache_library(stored)
                    self._mark_dirty(False)
                    self._synced = True
                    return True
            self._mark_dirty(True)
            self._write_library_file(files)
            self._synced = False
            self.start_background()
            return True

    # --- playlists -------------------------------------------------------
    def load_playlists(self):
        with self.lock:
            if self._ensure_synced():
                try:
                    playlists = self.client.playlists()
                except (ServiceUnavailable, ServiceError):
                    self._synced = False
                else:
                    self._cache_playlists(playlists)
                    return playlists
            else:
                self.start_background()
            return self._read_playlists_file()

    def _playlist_write(self, service_call, local_call):
        with self.lock:
            if self._ensure_synced():
                try:
                    playlists = service_call()
                except ServiceUnavailable:
                    self._synced = False
                except ServiceError as error:
                    raise ValueError(str(error)) from error
                else:
                    self._cache_playlists(playlists)
                    self._mark_dirty(False)
                    self._synced = True
                    return playlists
            playlists = local_call(self._read_playlists_file())
            self._mark_dirty(True)
            self._write_playlists_file(playlists)
            self._synced = False
            self.start_background()
            return playlists

    def create_playlist(self, name):
        name = clean_name(name)
        return self._playlist_write(
            lambda: self.client.create_playlist(name),
            lambda items: local_create_playlist(items, name))

    def rename_playlist(self, playlist_id, name):
        name = clean_name(name)
        return self._playlist_write(
            lambda: self.client.rename_playlist(playlist_id, name),
            lambda items: local_rename_playlist(items, playlist_id, name))

    def delete_playlist(self, playlist_id):
        return self._playlist_write(
            lambda: self.client.delete_playlist(playlist_id),
            lambda items: local_delete_playlist(items, playlist_id))

    def add_track_to_playlist(self, playlist_id, path):
        path = clean_path(path)
        return self._playlist_write(
            lambda: self.client.add_track_to_playlist(playlist_id, path),
            lambda items: local_add_track(items, playlist_id, path))

    def add_track_to_playlists(self, playlist_ids, path):
        if not isinstance(playlist_ids, list) or not playlist_ids:
            raise ValueError('Выберите хотя бы один плейлист.')
        path = clean_path(path)
        return self._playlist_write(
            lambda: self.client.add_track_to_playlists(playlist_ids, path),
            lambda items: local_add_track_many(items, playlist_ids, path))

    def remove_track_from_playlist(self, playlist_id, path):
        path = clean_path(path)
        return self._playlist_write(
            lambda: self.client.remove_track_from_playlist(playlist_id, path),
            lambda items: local_remove_track(items, playlist_id, path))

    def set_playlist_cover(self, playlist_id, cover):
        if cover is not None and not is_cover_name(cover):
            raise ValueError('Некорректная ссылка на обложку плейлиста.')
        return self._playlist_write(
            lambda: self.client.set_playlist_cover(playlist_id, cover),
            lambda items: local_set_cover(items, playlist_id, cover))


# --- pure local mutations, shared with the offline branch --------------------
def clean_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Введите название плейлиста.')
    name = value.strip()
    if len(name) > 100:
        raise ValueError('Название должно быть не длиннее 100 символов.')
    return name


def clean_path(value):
    if not isinstance(value, str):
        raise ValueError('Некорректный путь к треку.')
    path = value.strip().strip('"')
    if not path or len(path) > 4096 or any(ord(c) < 32 for c in path):
        raise ValueError('Некорректный путь к треку.')
    return path


def _key(path):
    return ntpath.normcase(ntpath.normpath(path))


def _find(items, playlist_id):
    target = next((item for item in items if item.get('id') == playlist_id), None)
    if target is None:
        raise ValueError('Плейлист не найден.')
    return target


def local_create_playlist(items, name):
    if any(item['name'].casefold() == name.casefold() for item in items):
        raise ValueError('Плейлист с таким названием уже существует.')
    return [*items, {'id': str(uuid4()), 'name': name, 'tracks': []}]


def local_rename_playlist(items, playlist_id, name):
    target = _find(items, playlist_id)
    if any(item is not target and item['name'].casefold() == name.casefold() for item in items):
        raise ValueError('Плейлист с таким названием уже существует.')
    target['name'] = name
    return items


def local_delete_playlist(items, playlist_id):
    _find(items, playlist_id)
    return [item for item in items if item.get('id') != playlist_id]


def local_add_track(items, playlist_id, path):
    return local_add_track_many(items, [playlist_id], path)


def local_add_track_many(items, playlist_ids, path):
    key = _key(path)
    targets = [_find(items, identifier) for identifier in dict.fromkeys(playlist_ids)]
    for target in targets:
        if not any(_key(item) == key for item in target.get('tracks', [])):
            target.setdefault('tracks', []).append(path)
    return items


def local_remove_track(items, playlist_id, path):
    target = _find(items, playlist_id)
    key = _key(path)
    target['tracks'] = [item for item in target.get('tracks', []) if _key(item) != key]
    return items


def local_set_cover(items, playlist_id, cover):
    target = _find(items, playlist_id)
    if cover is None:
        target.pop('cover', None)
    else:
        target['cover'] = cover
    return items


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
