"""Loopback client for the Music Service.

The desktop keeps its own parser libraries, but a service search returns stable
candidate IDs; downloads are measured and stored by the service and copied over
loopback HTTP. No provider library is imported here, and environment proxies are
never inherited.
"""
from hashlib import sha256
import os
from pathlib import Path
import re
from time import monotonic, sleep

import httpx

DEFAULT_URL = 'http://127.0.0.1:8766'
IDENTIFIER = re.compile(r'^[a-f0-9]{1,64}$')
PLAYLIST_ID = re.compile(r'^[A-Za-z0-9-]{1,64}$')
MAX_AUDIO = 200 * 1024 * 1024
LOOPBACK_PREFIXES = ('http://127.0.0.1', 'http://localhost', 'http://[::1]')


class ServiceError(RuntimeError):
    """The service answered with an error; the message is safe to show."""


class ServiceUnavailable(ServiceError):
    """The service is not running or did not answer in time."""


class ServiceClient:
    def __init__(self, base_url=None, *, client=None, timeout=10.0):
        base_url = (base_url or os.environ.get('SLYNOR_SERVICE_URL') or DEFAULT_URL).rstrip('/')
        if not base_url.startswith(LOOPBACK_PREFIXES):
            raise ServiceError('Music Service должен слушать loopback.')
        self.base_url = base_url
        self.timeout = max(1.0, float(timeout))
        self._client = client or httpx.Client(base_url=base_url, trust_env=False,
                                              timeout=httpx.Timeout(self.timeout, connect=3))

    def close(self):
        self._client.close()

    def health(self, timeout=1.5):
        try:
            response = self._client.get('/health', timeout=timeout)
            data = response.json()
            return response.status_code == 200 and isinstance(data, dict) and data.get('status') == 'ok'
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    def _json(self, method, path, **kwargs):
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError:
            raise ServiceUnavailable('Music Service не запущен.') from None
        except httpx.TimeoutException:
            raise ServiceUnavailable('Music Service не ответил вовремя.') from None
        except httpx.HTTPError:
            raise ServiceUnavailable('Music Service недоступен.') from None
        if response.status_code == 404:
            raise ServiceError('Задание или файл Music Service не найдены.')
        if response.status_code >= 400:
            detail = None
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = body.get('detail')
            except ValueError:
                pass
            message = str(detail)[:300] if isinstance(detail, str) and detail else 'Music Service отклонил запрос.'
            raise ServiceError(message)
        try:
            data = response.json()
        except ValueError:
            raise ServiceError('Music Service вернул некорректный ответ.') from None
        if not isinstance(data, dict):
            raise ServiceError('Music Service вернул некорректный ответ.')
        return data

    def _job(self, kind, payload):
        job = self._json('POST', '/jobs', json={'kind': kind, 'payload': payload})
        identifier = job.get('id')
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise ServiceError('Music Service вернул некорректный ID задания.')
        return identifier

    def _wait(self, job_id, deadline):
        while monotonic() < deadline:
            job = self._json('GET', '/jobs/' + job_id)
            status = job.get('status')
            if status == 'done':
                result = job.get('result')
                return result if isinstance(result, dict) else {}
            if status in ('failed', 'cancelled'):
                error = job.get('error')
                raise ServiceError(str(error)[:300] if isinstance(error, str) and error
                                   else 'Задание Music Service не выполнено.')
            sleep(0.2)
        raise ServiceError('Music Service не завершил задание вовремя.')

    def search(self, query, limit=10, timeout=90):
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ServiceError('Некорректный поисковый запрос.')
        limit = min(20, max(1, int(limit)))
        job_id = self._job('search', {'query': query.strip(), 'limit': limit})
        result = self._wait(job_id, monotonic() + max(5.0, float(timeout)))
        items = result.get('items')
        if not isinstance(items, list):
            raise ServiceError('Music Service вернул некорректную выдачу.')
        warnings = [str(item)[:300] for item in (result.get('warnings') or []) if isinstance(item, str)][:5]
        return items, warnings

    def download(self, candidate_id, audio_format='original', timeout=600):
        if not isinstance(candidate_id, str) or not IDENTIFIER.fullmatch(candidate_id):
            raise ServiceError('Некорректный кандидат Music Service.')
        if audio_format not in ('original', 'mp3'):
            raise ServiceError('Неизвестный формат аудио.')
        job_id = self._job('download', {'candidate_id': candidate_id, 'audio_format': audio_format})
        return self._wait(job_id, monotonic() + max(30.0, float(timeout)))

    def library(self):
        data = self._json('GET', '/library')
        return {'tracks': self.library_from(data), 'revision': data.get('revision')}

    @staticmethod
    def library_from(data):
        tracks = data.get('tracks') if isinstance(data, dict) else None
        if not isinstance(tracks, list) or not all(isinstance(path, str) for path in tracks):
            raise ServiceError('Music Service вернул некорректную библиотеку.')
        return tracks

    @staticmethod
    def playlists_from(data):
        playlists = data.get('playlists') if isinstance(data, dict) else None
        if not isinstance(playlists, list) or not all(isinstance(item, dict) for item in playlists):
            raise ServiceError('Music Service вернул некорректный список плейлистов.')
        return playlists

    def replace_library(self, tracks):
        if not isinstance(tracks, list):
            raise ServiceError('Некорректный список треков.')
        return self.library_from(self._json('PUT', '/library', json={'tracks': tracks}))

    def library_revision(self):
        data = self._json('GET', '/library/revision')
        return {'revision': data.get('revision'), 'updated_at': data.get('updated_at')}

    def playlists(self):
        return self.playlists_from(self._json('GET', '/playlists'))

    def replace_playlists(self, playlists):
        if not isinstance(playlists, list):
            raise ServiceError('Некорректный список плейлистов.')
        return self.playlists_from(self._json('PUT', '/playlists', json={'playlists': playlists}))

    def create_playlist(self, name):
        return self.playlists_from(self._json('POST', '/playlists', json={'name': name}))

    def rename_playlist(self, playlist_id, name):
        return self.playlists_from(
            self._json('PATCH', '/playlists/' + _playlist_identifier(playlist_id), json={'name': name}))

    def delete_playlist(self, playlist_id):
        return self.playlists_from(
            self._json('DELETE', '/playlists/' + _playlist_identifier(playlist_id)))

    def add_track_to_playlist(self, playlist_id, path):
        return self.playlists_from(self._json(
            'POST', '/playlists/' + _playlist_identifier(playlist_id) + '/tracks', json={'path': path}))

    def add_track_to_playlists(self, playlist_ids, path):
        if not isinstance(playlist_ids, list) or not playlist_ids:
            raise ServiceError('Выберите хотя бы один плейлист.')
        return self.playlists_from(self._json(
            'POST', '/playlists/tracks', json={'playlist_ids': playlist_ids, 'path': path}))

    def remove_track_from_playlist(self, playlist_id, path):
        return self.playlists_from(self._json(
            'POST', '/playlists/' + _playlist_identifier(playlist_id) + '/tracks/remove',
            json={'path': path}))

    def set_playlist_cover(self, playlist_id, cover):
        return self.playlists_from(self._json(
            'POST', '/playlists/' + _playlist_identifier(playlist_id) + '/cover', json={'cover': cover}))

    def asset_content(self, asset_id, destination, max_bytes=MAX_AUDIO):
        if not isinstance(asset_id, str) or not IDENTIFIER.fullmatch(asset_id):
            raise ServiceError('Некорректный файл Music Service.')
        destination = Path(destination)
        size = 0
        try:
            with self._client.stream('GET', '/assets/' + asset_id + '/content',
                                     timeout=httpx.Timeout(60, connect=5)) as response:
                if response.status_code != 200:
                    raise ServiceError('Music Service не отдал файл.')
                with destination.open('xb') as stream:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ServiceError('Файл Music Service больше 200 МБ.')
                        stream.write(chunk)
        except httpx.HTTPError:
            destination.unlink(missing_ok=True)
            raise ServiceUnavailable('Music Service недоступен.') from None
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        if size <= 0:
            destination.unlink(missing_ok=True)
            raise ServiceError('Music Service вернул пустой файл.')
        return size


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ServiceError('Некорректный идентификатор Music Service.')
    return value


def _playlist_identifier(value):
    if not isinstance(value, str) or not PLAYLIST_ID.fullmatch(value):
        raise ServiceError('Некорректный идентификатор плейлиста.')
    return value


def file_sha256(path, chunk=1024 * 1024):
    digest = sha256()
    with Path(path).open('rb') as stream:
        while True:
            block = stream.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()
