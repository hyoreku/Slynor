"""Run the owner-only Telegram bot with python -m telegram_bot."""
import argparse
import asyncio
from collections import deque
from contextlib import closing
import logging
import os
from pathlib import Path
import re
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
import httpx

from service.db import default_db_path
from service.providers import PROVIDERS

from .music import IDENTIFIER, MusicError, MusicNotFound, MusicService, search_target
from .state import BotState

LOGGER = logging.getLogger('slynor.telegram')
PAGE_SIZE = 5
FORMATS = {'original': 'Оригинал', 'mp3': 'MP3'}
PROVIDER_NAMES = {'youtube': 'YouTube', 'soundcloud': 'SoundCloud', 'spotify': 'Spotify'}
JOB_STATES = {
    'queued': 'В очереди', 'running': 'В работе', 'done': 'Готово',
    'failed': 'Ошибка', 'cancelled': 'Отменено',
}
HELP = (
    'Отправьте название, исполнителя или ссылку на трек.\n'
    '/search — поиск\n/queue — последние задания\n'
    '/cancel <номер> — отменить задание в очереди\n/settings — источник и формат'
)


def button(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def label(track):
    parts = [str(track.get(key) or '').strip() for key in ('artist', 'title')]
    return ' — '.join(filter(None, parts))[:140] or 'Без названия'


def track_information(track):
    duration = track.get('duration_ms')
    if isinstance(duration, int) and duration > 0:
        minutes, seconds = divmod(duration // 1000, 60)
        length = f'{minutes}:{seconds:02d}'
    else:
        length = 'длительность неизвестна'
    availability = {
        'available': 'полный файл доступен',
        'unavailable': 'аудио недоступно',
    }.get(track.get('availability'), 'доступность проверяется при загрузке')
    provider = PROVIDER_NAMES.get(track.get('provider'), 'каталог')
    return f'{provider} · {length} · {availability}'


async def edit_progress(message, text):
    try:
        await message.edit_text(text)
    except TelegramBadRequest:
        pass


class OwnerBot:
    def __init__(self, service, state):
        self.service = service
        self.state = state
        self.tracks = []
        self.search_key = ''
        self.last_query = ''
        self.last_provider = 'youtube'
        self.searching = False
        self.downloading = False
        self.callbacks = deque(maxlen=256)

    def settings(self):
        preferred = self.state.get('provider', 'youtube')
        audio_format = self.state.get('format', 'original')
        sources = [button(('✓ ' if preferred == code else '') + name, f'pref:provider:{code}')
                   for code, name in PROVIDER_NAMES.items()]
        formats = [button(('✓ ' if audio_format == code else '') + name, f'pref:format:{code}')
                   for code, name in FORMATS.items()]
        return 'Источник поиска и формат скачивания', keyboard([sources, formats])

    def results(self, page):
        start = page * PAGE_SIZE
        tracks = self.tracks[start:start + PAGE_SIZE]
        rows = [[button(f'{i}. {label(track)[:54]}', f'track:{self.search_key}:{i - 1}')]
                for i, track in enumerate(tracks, start + 1)
                if track.get('availability') != 'unavailable']
        navigation = []
        if page > 0:
            navigation.append(button('‹ Назад', f'page:{self.search_key}:{page - 1}'))
        if start + PAGE_SIZE < len(self.tracks):
            navigation.append(button('Далее ›', f'page:{self.search_key}:{page + 1}'))
        if navigation:
            rows.append(navigation)
        rows.append([button('Другой источник', f'source:{self.search_key}:0')])
        lines = [f'{i}. {label(track)}\n{track_information(track)}'
                 for i, track in enumerate(tracks, start + 1)]
        return '\n\n'.join(lines), keyboard(rows)

    async def search(self, message, query, *, source=None):
        if self.searching:
            await message.answer('Поиск уже идёт. Его статус доступен в /queue.')
            return
        self.searching = True
        notice = None
        try:
            provider, query = search_target(query, source or self.state.get('provider', 'youtube'))
            notice = await message.answer('Ставлю поиск в очередь…')
            identifier = await self.service.submit('search', {'query': query, 'provider': provider, 'limit': 10})
            self.state.add_job(identifier, 'Поиск')

            async def progress(status):
                if status == 'running':
                    await edit_progress(notice, 'Ищу в каталоге…')

            result = await self.service.wait(identifier, 120, progress)
            self.tracks = [
                track for track in result.get('items', [])
                if isinstance(track, dict) and isinstance(track.get('candidate_id'), str)
                and IDENTIFIER.fullmatch(track['candidate_id'])
            ][:10]
            self.search_key = uuid4().hex[:8]
            self.last_query = query
            self.last_provider = provider
            if self.tracks:
                text, markup = self.results(0)
                await notice.edit_text(text, reply_markup=markup)
            else:
                await notice.edit_text('Ничего не найдено. Попробуйте другой источник в /settings.')
        except MusicError as error:
            if notice is not None:
                await edit_progress(notice, str(error))
            else:
                await message.answer(str(error))
        finally:
            self.searching = False

    async def queue(self, message):
        lines = []
        for identifier, kind in self.state.recent_jobs():
            try:
                job = await self.service.job(identifier)
                status = JOB_STATES.get(job.get('status'), 'Неизвестно')
            except MusicError:
                status = 'Статус недоступен'
            lines.append(f'{kind} · {status}\n{identifier}')
        await message.answer('\n\n'.join(lines) or 'Заданий пока нет.')

    async def cancel(self, message, identifier):
        if not IDENTIFIER.fullmatch(identifier) or not self.state.owns_job(identifier):
            await message.answer('Укажите номер вашего задания из /queue.')
            return
        try:
            await self.service.cancel(identifier)
        except MusicError as error:
            await message.answer(str(error))
        else:
            await message.answer('Задание отменено.')

    async def download(self, message, track, audio_format):
        if self.downloading:
            await message.answer('Уже готовлю файл. Проверьте /queue.')
            return
        self.downloading = True
        track_key = f"{track['candidate_id']}:{audio_format}"
        notice = None
        identifier = None
        try:
            notice = await message.answer('Готовлю аудио…')
            known_file = self.state.get_file(track_key)
            if known_file is not None:
                file_id, mode = known_file
                try:
                    await self.send_audio(message, file_id, mode, track)
                except TelegramBadRequest:
                    self.state.forget_file(track_key)
                else:
                    await edit_progress(notice, 'Аудио отправлено.')
                    return

            identifier = self.state.job_for(track_key)
            if identifier is not None:
                try:
                    job = await self.service.job(identifier)
                except MusicNotFound:
                    self.state.forget_job(identifier)
                    identifier = None
                else:
                    if job.get('status') in ('failed', 'cancelled'):
                        identifier = None
            if identifier is None:
                identifier = await self.service.submit('download', {
                    'candidate_id': track['candidate_id'], 'audio_format': audio_format,
                })
                self.state.add_job(identifier, 'Загрузка', track_key)

            async def progress(status):
                text = 'Загрузка в очереди…' if status == 'queued' else 'Скачиваю и проверяю аудио…'
                await edit_progress(notice, text)

            result = await self.service.wait(identifier, 720, progress)
            async with self.service.upload_file(result.get('asset_id')) as path:
                await edit_progress(notice, 'Отправляю аудио в Telegram…')
                mode = 'audio' if path.suffix in ('.mp3', '.m4a') else 'document'
                name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '_', label(track)).strip(' .') or 'Slynor'
                upload = FSInputFile(path, filename=name + path.suffix)
                sent = await self.send_audio(message, upload, mode, track)
                media = sent.audio if mode == 'audio' else sent.document
                if media is not None:
                    self.state.save_file(track_key, media.file_id, mode)
            await edit_progress(notice, 'Аудио отправлено.')
        except (MusicError, TelegramAPIError) as error:
            if isinstance(error, MusicNotFound) and identifier is not None:
                self.state.forget_job(identifier)
            if notice is not None:
                text = str(error) if isinstance(error, MusicError) else 'Telegram не подтвердил отправку.'
                await edit_progress(notice, text)
        finally:
            self.downloading = False

    @staticmethod
    async def send_audio(message, file, mode, track):
        if mode == 'audio':
            return await message.answer_audio(
                file, title=str(track.get('title') or 'Slynor')[:64],
                performer=str(track.get('artist') or '')[:64] or None,
            )
        return await message.answer_document(file, caption=label(track))

    async def on_callback(self, callback):
        if callback.id in self.callbacks:
            await callback.answer()
            return
        self.callbacks.append(callback.id)
        await callback.answer()
        message = callback.message
        if not isinstance(message, Message):
            return
        action, key, value = callback.data.split(':', 2)
        if action == 'pref':
            allowed = {'format': FORMATS, 'provider': PROVIDERS}
            if value in allowed.get(key, ()):
                self.state.set(key, value)
                text, markup = self.settings()
                if message.reply_markup != markup:
                    await message.edit_text(text, reply_markup=markup)
            return
        if key != self.search_key:
            await message.answer('Этот список устарел. Запустите поиск ещё раз.')
            return
        if action == 'source':
            if '://' in self.last_query:
                await message.answer('Для поиска в другом каталоге отправьте название трека.')
                return
            await self.search(message, self.last_query,
                              source=PROVIDERS[(PROVIDERS.index(self.last_provider) + 1) % len(PROVIDERS)])
            return

        index_text, _, audio_format = value.partition(':')
        if not index_text.isdecimal():
            return
        index = int(index_text)
        if action == 'page':
            if index * PAGE_SIZE < len(self.tracks):
                text, markup = self.results(index)
                if message.reply_markup != markup:
                    await message.edit_text(text, reply_markup=markup)
            return
        if index >= len(self.tracks) or self.tracks[index].get('availability') == 'unavailable':
            return
        track = self.tracks[index]
        if action == 'track':
            audio_format = self.state.get('format', 'original')
            text = f'{label(track)}\n{track_information(track)}\n\nПодтвердите, что у вас есть право скачать запись.'
            await message.edit_text(text, reply_markup=keyboard([
                [button('Получить · ' + FORMATS[audio_format], f'get:{key}:{index}:{audio_format}')],
                [button('Назад', f'page:{key}:{index // PAGE_SIZE}')],
            ]))
        elif action == 'get' and audio_format in FORMATS:
            await self.download(message, track, audio_format)


def build_router(owner_id, workflow):
    router = Router()
    router.message.filter(F.chat.type == ChatType.PRIVATE, F.from_user.id == owner_id)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE, F.from_user.id == owner_id)

    @router.message(Command('start', 'help'))
    async def help_command(message):
        await message.answer(HELP)

    @router.message(Command('search'))
    async def search_command(message, command: CommandObject):
        if command.args:
            await workflow.search(message, command.args)
        else:
            await message.answer('Отправьте название, исполнителя или ссылку на трек.')

    @router.message(Command('settings'))
    async def settings_command(message):
        text, markup = workflow.settings()
        await message.answer(text, reply_markup=markup)

    @router.message(Command('queue'))
    async def queue_command(message):
        await workflow.queue(message)

    @router.message(Command('cancel'))
    async def cancel_command(message, command: CommandObject):
        await workflow.cancel(message, (command.args or '').strip())

    @router.message(F.text, ~F.text.startswith('/'))
    async def search_text(message):
        await workflow.search(message, message.text)

    @router.callback_query(F.data.regexp(r'^(track|page|source|get|pref):[^:]+:.+$'))
    async def button_press(callback: CallbackQuery):
        try:
            await workflow.on_callback(callback)
        except TelegramAPIError as error:
            LOGGER.warning('Сбой обращения к Telegram (%s).', type(error).__name__)

    @router.errors()
    async def report_error(event):
        LOGGER.error('Ошибка обработчика Telegram (%s).', type(event.exception).__name__)
        return True

    return router


async def run(token, owner_id, port, state_path):
    bot = Bot(token)
    if state_path is None:
        state_path = default_db_path().parent / f'telegram-{bot.id}-{owner_id}.sqlite3'
    async with bot.context(auto_close=True), httpx.AsyncClient(
        base_url=f'http://127.0.0.1:{port}', timeout=15, trust_env=False,
    ) as client:
        with closing(BotState(state_path)) as state:
            router = build_router(owner_id, OwnerBot(MusicService(client), state))
            dispatcher = Dispatcher()
            dispatcher.include_router(router)
            await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types(),
                                           close_bot_session=False, tasks_concurrency_limit=16)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Slynor Telegram bot')
    parser.add_argument('--port', type=int, default=8766, help='Порт локального Music Service')
    parser.add_argument('--state', type=Path, help='Путь к локальной SQLite бота')
    args = parser.parse_args(argv)
    token = os.environ.get('SLYNOR_TELEGRAM_TOKEN', '').strip()
    owner = os.environ.get('SLYNOR_TELEGRAM_OWNER_ID', '').strip()
    if not token or not owner.isdecimal() or not 0 < int(owner) < 2 ** 52:
        parser.error('Задайте SLYNOR_TELEGRAM_TOKEN и числовой SLYNOR_TELEGRAM_OWNER_ID.')
    if not 1 <= args.port <= 65535:
        parser.error('Порт Music Service должен быть в диапазоне 1–65535.')
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run(token, int(owner), args.port, args.state))
    except Exception as error:
        parser.exit(1, f'Не удалось запустить Telegram-бот ({type(error).__name__}).\n')


if __name__ == '__main__':
    main()
