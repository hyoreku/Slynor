"""Async client for the local Music Service and bounded Telegram uploads."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import httpx
from pydantic import ValidationError

from service.errors import ProviderError
from service.models import AudioAsset
from service.providers import HOSTS, stable_identifier, track_url

IDENTIFIER = re.compile(r'[a-f0-9]{32}')
MAX_UPLOAD_BYTES = 50_000_000
SUPPORTED_EXTENSIONS = {'.mp3', '.m4a', '.aac', '.webm', '.opus', '.ogg', '.flac', '.wav', '.mp4'}


class MusicError(Exception):
    """A message safe to display in the owner's chat."""


class MusicNotFound(MusicError):
    """A persisted reference can no longer be used."""


def search_target(query, preferred):
    query = query.strip()
    if not 1 <= len(query) <= 200:
        raise MusicError('Введите название, исполнителя или ссылку длиной до 200 символов.')
    if '://' not in query:
        return preferred, query
    for provider in HOSTS:
        try:
            identifier = stable_identifier(provider, query)
        except (ProviderError, ValueError):
            continue
        return provider, track_url(provider, identifier)
    raise MusicError('Нужна ссылка HTTPS на отдельный трек поддерживаемого источника.')


class MusicService:
    def __init__(self, client):
        self.client = client

    async def request(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise MusicError('Music Service вернул нераспознанный ответ.')
            return data
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                raise MusicNotFound('Задание или файл уже недоступны.') from None
            if error.response.status_code == 409:
                raise MusicError('Можно отменить только задание, которое ещё в очереди.') from None
            raise MusicError('Music Service отклонил запрос.') from None
        except (httpx.HTTPError, ValueError):
            raise MusicError('Music Service не отвечает. Проверьте локальный запуск.') from None

    async def submit(self, kind, payload):
        health = await self.request('GET', '/health')
        if not health.get('processing_jobs'):
            raise MusicError('Music Service запущен без обработчиков заданий.')
        job = await self.request('POST', '/jobs', json={'kind': kind, 'payload': payload})
        identifier = job.get('id', '')
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise MusicError('Music Service вернул неверный номер задания.')
        return identifier

    async def job(self, identifier):
        return await self.request('GET', f'/jobs/{identifier}')

    async def cancel(self, identifier):
        await self.request('POST', f'/jobs/{identifier}/cancel')

    async def wait(self, identifier, timeout, progress):
        try:
            async with asyncio.timeout(timeout):
                previous = None
                while True:
                    job = await self.job(identifier)
                    status = job.get('status')
                    if status == 'done':
                        result = job.get('result')
                        if isinstance(result, dict):
                            return result
                        raise MusicError('Music Service не вернул результат задания.')
                    if status == 'cancelled':
                        raise MusicError('Задание отменено.')
                    if status == 'failed':
                        raise MusicError('Задание не завершилось. Можно повторить запрос.')
                    if status not in ('queued', 'running'):
                        raise MusicError('Music Service вернул неизвестный статус.')
                    if status != previous:
                        await progress(status)
                        previous = status
                    await asyncio.sleep(.8)
        except TimeoutError:
            raise MusicError('Время ожидания истекло. Проверьте /queue.') from None

    @asynccontextmanager
    async def upload_file(self, asset_id):
        if not isinstance(asset_id, str) or not IDENTIFIER.fullmatch(asset_id):
            raise MusicError('Music Service вернул неверный номер файла.')
        try:
            asset = AudioAsset.model_validate(await self.request('GET', f'/assets/{asset_id}'))
        except ValidationError:
            raise MusicError('Music Service вернул неверные данные файла.') from None
        if not 0 < asset.size_bytes <= MAX_UPLOAD_BYTES:
            raise MusicError('Файл превышает лимит Telegram в 50 МБ либо пуст.')
        suffix = Path(asset.path).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise MusicError('Формат файла пока не поддерживается ботом.')
        with TemporaryDirectory(prefix='slynor-telegram-') as temporary:
            path = Path(temporary) / ('audio' + suffix)
            count = 0
            digest = hashlib.sha256()
            try:
                async with self.client.stream('GET', f'/assets/{asset_id}/content') as response:
                    if response.status_code == 404:
                        raise MusicNotFound('Аудиофайл больше недоступен.')
                    response.raise_for_status()
                    with path.open('wb') as file:
                        async for chunk in response.aiter_bytes():
                            count += len(chunk)
                            if count > asset.size_bytes:
                                raise MusicError('Размер полученного файла отличается от ожидаемого.')
                            digest.update(chunk)
                            file.write(chunk)
            except (httpx.HTTPError, OSError):
                raise MusicError('Не удалось получить файл из Music Service.') from None
            if count != asset.size_bytes or digest.hexdigest() != asset.sha256:
                raise MusicError('Полученный файл не прошёл проверку целостности.')
            yield path
