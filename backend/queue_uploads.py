"""Bounded, short-lived assembly of queues larger than one API request.

The normal 10,000-item request limit remains. Large queues use <=1,000 paths
per chunk, a byte budget and a TTL; there is no unlimited request body.
"""
from secrets import token_urlsafe
from threading import RLock
from time import monotonic


class QueueUploadError(ValueError):
    pass


class QueueUploads:
    def __init__(self, budget=16 * 1024 * 1024, ttl=60, max_sessions=2):
        self.budget, self.ttl, self.max_sessions = budget, ttl, max_sessions
        self.lock = RLock()
        self.sessions = {}

    def _expire(self):
        for key in list(self.sessions):
            if self.sessions[key]['until'] <= monotonic():
                del self.sessions[key]

    def begin(self):
        with self.lock:
            self._expire()
            if len(self.sessions) >= self.max_sessions:
                raise QueueUploadError('Подготовка другой очереди ещё не завершена.')
            key = token_urlsafe(24)
            self.sessions[key] = {'paths': [], 'bytes': 0, 'until': monotonic() + self.ttl}
            return key

    def append(self, key, offset, paths):
        if not 1 <= len(paths) <= 1000 or any(not isinstance(p, str) or not p.strip() or len(p) > 4096 for p in paths):
            raise QueueUploadError('Некорректная порция очереди.')
        size = sum(len(p.encode('utf-8')) + 64 for p in paths)
        if size > 512 * 1024:
            raise QueueUploadError('Порция очереди слишком велика.')
        with self.lock:
            self._expire()
            session = self.sessions.get(key)
            if session is None:
                raise QueueUploadError('Подготовка очереди устарела. Повторите запуск.')
            if offset != len(session['paths']):
                raise QueueUploadError('Нарушен порядок порций очереди.')
            if session['bytes'] + size > self.budget:
                del self.sessions[key]
                raise QueueUploadError('Очередь превысила безопасный объём памяти. Выберите меньший плейлист.')
            session['paths'].extend(paths)
            session['bytes'] += size
            session['until'] = monotonic() + self.ttl
            return len(session['paths'])

    def take(self, key, expected_count):
        with self.lock:
            self._expire()
            session = self.sessions.get(key)
            if not session or len(session['paths']) != expected_count or expected_count == 0:
                raise QueueUploadError('Очередь не загружена полностью.')
            del self.sessions[key]
            return session['paths']

    def cancel(self, key):
        with self.lock:
            self.sessions.pop(key, None)
