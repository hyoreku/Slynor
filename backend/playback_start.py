"""Keep a local playback request responsive while audio or the filesystem opens."""
import logging
from secrets import token_urlsafe
from threading import Event, Lock, Thread
from time import monotonic

from fastapi import HTTPException

logger = logging.getLogger('ownplayer')


class PlaybackStart:
    def __init__(self):
        self._lock = Lock()
        self._done = Event()
        self._done.set()
        self._id = None
        self._started = 0
        self._result = None

    def submit(self, command):
        with self._lock:
            if not self._done.is_set():
                raise HTTPException(409, 'Плеер ещё открывает предыдущий трек.')
            self._id = token_urlsafe(12)
            self._result = None
            self._started = monotonic()
            self._done.clear()
            try:
                Thread(target=self._run, args=(command,), daemon=True, name='playback-start').start()
            except RuntimeError:
                self._done.set()
                raise
            return self._id

    def status(self, identifier):
        with self._lock:
            if identifier != self._id:
                raise HTTPException(404, 'Запрос воспроизведения уже завершён.')
            return self._result or {'status': 'opening'}

    def opening(self):
        return not self._done.is_set()

    def wait(self, timeout):
        return self._done.wait(timeout)

    def _run(self, command):
        logger.info('Local playback request started')
        try:
            result = command()
        except HTTPException as error:
            result = {'status': 'error', 'message': error.detail}
        except Exception:
            logger.exception('Local playback request failed')
            result = {'status': 'error', 'message': 'Не удалось запустить аудиодвижок.'}
        with self._lock:
            logger.info('Local playback request finished status=%s time_ms=%d',
                        result['status'], (monotonic() - self._started) * 1000)
            self._result = result
            self._done.set()
