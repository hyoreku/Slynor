"""Local Telegram workflow checks without calling a provider or Telegram."""
from contextlib import asynccontextmanager, closing
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import httpx

from telegram_bot.music import MAX_UPLOAD_BYTES, MusicError, MusicService, search_target
from telegram_bot.state import BotState


class MusicServiceClientTests(unittest.IsolatedAsyncioTestCase):
    def test_detects_public_track_links_and_rejects_other_hosts(self):
        expected = ('youtube', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(search_target('https://youtu.be/dQw4w9WgXcQ?si=unused', 'spotify'), expected)
        self.assertEqual(search_target(' Artist — Track ', 'soundcloud'), ('soundcloud', 'Artist — Track'))
        with self.assertRaises(MusicError):
            search_target('https://example.test/file.mp3', 'youtube')

    async def test_jobs_and_cancel_use_the_local_api(self):
        identifier = 'a' * 32
        seen = []

        def api(request):
            route = (request.method, request.url.path)
            seen.append(route)
            if route == ('GET', '/health'):
                return httpx.Response(200, json={'processing_jobs': True})
            if route == ('POST', '/jobs'):
                self.assertEqual(json.loads(request.content), {'kind': 'search', 'payload': {'query': 'song'}})
                return httpx.Response(201, json={'id': identifier})
            if route == ('GET', f'/jobs/{identifier}'):
                return httpx.Response(200, json={'status': 'done', 'result': {'items': []}})
            return httpx.Response(200, json={'status': 'cancelled'})

        async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(api)) as client:
            service = MusicService(client)
            self.assertEqual(await service.submit('search', {'query': 'song'}), identifier)
            self.assertEqual(await service.wait(identifier, 1, AsyncMock()), {'items': []})
            await service.cancel(identifier)
        self.assertEqual(seen[-1], ('POST', f'/jobs/{identifier}/cancel'))

    async def test_upload_checks_size_hash_and_releases_temporary_file(self):
        asset_id = 'b' * 32
        content = b'test audio'
        asset = {
            'id': asset_id, 'container': 'mp3', 'codec': 'mp3', 'size_bytes': len(content),
            'sha256': hashlib.sha256(content).hexdigest(), 'path': 'assets/example.mp3',
        }
        content_requests = []

        def api(request):
            if request.url.path.endswith('/content'):
                content_requests.append(request.url.path)
                return httpx.Response(200, content=content)
            return httpx.Response(200, json=asset)

        async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(api)) as client:
            service = MusicService(client)
            async with service.upload_file(asset_id) as file:
                self.assertEqual(file.suffix, '.mp3')
                self.assertEqual(file.read_bytes(), content)
            self.assertFalse(file.exists())

            asset['sha256'] = '0' * 64
            with self.assertRaisesRegex(MusicError, 'целостности'):
                async with service.upload_file(asset_id):
                    self.fail('Invalid bytes must not be exposed for upload.')

            asset['size_bytes'] = MAX_UPLOAD_BYTES + 1
            with self.assertRaisesRegex(MusicError, '50 МБ'):
                async with service.upload_file(asset_id):
                    self.fail('Oversized assets must not be read.')
        self.assertEqual(len(content_requests), 2)


class BotStateTests(unittest.TestCase):
    def test_job_and_telegram_file_references_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bot.sqlite3'
            with closing(BotState(path)) as state:
                state.set('format', 'mp3')
                state.add_job('a' * 32, 'Загрузка', 'track:mp3')
                state.save_file('track:mp3', 'telegram-file', 'audio')
            with closing(BotState(path)) as state:
                self.assertEqual(state.get('format', 'original'), 'mp3')
                self.assertEqual(state.job_for('track:mp3'), 'a' * 32)
                self.assertEqual(state.get_file('track:mp3'), ('telegram-file', 'audio'))
                state.forget_job('a' * 32)
                self.assertIsNone(state.job_for('track:mp3'))
                self.assertTrue(state.owns_job('a' * 32))


@unittest.skipUnless(importlib.util.find_spec('aiogram'), 'Install requirements/bot.txt for aiogram routing.')
class TelegramRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_owner_in_private_chat_reaches_workflow(self):
        from aiogram import Bot, Dispatcher
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.types import Update

        from telegram_bot.__main__ import build_router

        owner_id = 12345
        owner_workflow = AsyncMock()
        session = AiohttpSession()
        session.make_request = AsyncMock(return_value=True)
        bot = Bot(':'.join(('12345', 'test-only')), session=session)
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(owner_id, owner_workflow))
        try:
            for index, (sender, chat_type) in enumerate(
                    [(owner_id + 1, 'private'), (owner_id, 'group'), (owner_id, 'private')], 1):
                update = Update.model_validate({
                    'update_id': index,
                    'message': {
                        'message_id': index, 'date': 0, 'text': 'A track',
                        'chat': {'id': sender, 'type': chat_type},
                        'from': {'id': sender, 'is_bot': False, 'first_name': 'Test'},
                    },
                })
                await dispatcher.feed_update(bot, update)
                self.assertEqual(owner_workflow.search.await_count, int(index == 3))
        finally:
            await bot.session.close()

    async def test_download_uses_original_container_and_reuses_telegram_file(self):
        from telegram_bot.__main__ import OwnerBot

        with tempfile.TemporaryDirectory() as folder, closing(BotState(Path(folder) / 'bot.db')) as state:
            audio = Path(folder) / 'original.webm'
            audio.write_bytes(b'fixture')
            asset_id = 'a' * 32
            job_id = 'b' * 32
            track = {'candidate_id': 'c' * 32, 'title': 'Track', 'artist': 'Artist'}

            @asynccontextmanager
            async def upload_file(identifier):
                self.assertEqual(identifier, asset_id)
                yield audio

            service = Mock(submit=AsyncMock(return_value=job_id),
                           wait=AsyncMock(return_value={'asset_id': asset_id}),
                           upload_file=upload_file)
            notice = SimpleNamespace(edit_text=AsyncMock())
            sent = SimpleNamespace(document=SimpleNamespace(file_id='telegram-file'))
            message = SimpleNamespace(answer=AsyncMock(return_value=notice),
                                      answer_document=AsyncMock(return_value=sent),
                                      answer_audio=AsyncMock())
            workflow = OwnerBot(service, state)
            await workflow.download(message, track, 'original')
            await workflow.download(message, track, 'original')
            self.assertEqual(message.answer_document.await_args_list[0].args[0].filename,
                             'Artist — Track.webm')
            self.assertEqual(message.answer_document.await_args_list[1].args[0], 'telegram-file')
            message.answer_audio.assert_not_awaited()
            service.submit.assert_awaited_once_with(
                'download', {'candidate_id': track['candidate_id'], 'audio_format': 'original'})
