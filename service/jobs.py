"""Persistent job queue and a single dispatcher.

A job survives a service restart: the row is committed before any work starts.
The dispatcher claims one row at a time; the queue lock keeps the shared SQLite
connection safe for the polling thread and the API thread.
"""
from datetime import datetime, timezone
import json
import logging
from threading import Event, RLock, Thread

from . import db
from .models import Job, JobKind, JobStatus

LOGGER = logging.getLogger('ownplayer.service')


def _job(row) -> Job:
    return Job(id=row['id'], kind=row['kind'], status=row['status'],
               payload=json.loads(row['payload']),
               result=None if row['result'] is None else json.loads(row['result']),
               error=row['error'], attempts=row['attempts'],
               created_at=db.parse(row['created_at']), started_at=db.parse(row['started_at']),
               finished_at=db.parse(row['finished_at']))


def _now() -> str:
    return db.iso(datetime.now(timezone.utc))


class JobQueue:
    def __init__(self, connection):
        self._connection = connection
        self._lock = RLock()

    def enqueue(self, kind: JobKind, payload: dict | None = None) -> Job:
        job = Job(kind=kind, payload=payload or {})
        with self._lock:
            self._connection.execute(
                'INSERT INTO jobs (id, kind, status, payload, created_at) VALUES (?,?,?,?,?)',
                (job.id, job.kind.value, job.status.value,
                 json.dumps(job.payload, ensure_ascii=False), db.iso(job.created_at)))
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return None if row is None else _job(row)

    def list(self, status: JobStatus | None = None, limit: int = 50) -> list[Job]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            if status is None:
                rows = self._connection.execute(
                    'SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
            else:
                rows = self._connection.execute(
                    'SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?',
                    (JobStatus(status).value, limit)).fetchall()
        return [_job(row) for row in rows]

    def claim_next(self) -> Job | None:
        with self._lock:
            self._connection.execute('BEGIN IMMEDIATE')
            try:
                row = self._connection.execute(
                    "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1"
                ).fetchone()
                if row is None:
                    self._connection.execute('COMMIT')
                    return None
                self._connection.execute(
                    "UPDATE jobs SET status='running', started_at=?, attempts=attempts+1"
                    " WHERE id=? AND status='queued'", (_now(), row['id']))
                self._connection.execute('COMMIT')
            except BaseException:
                self._connection.execute('ROLLBACK')
                raise
        return self.get(row['id'])

    def complete(self, job_id: str, result: dict | None = None) -> Job:
        with self._lock:
            row = self._connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('Задание не найдено.')
            if row['status'] != JobStatus.RUNNING.value:
                raise ValueError('Завершить можно только выполняющееся задание.')
            self._connection.execute(
                "UPDATE jobs SET status='done', result=?, finished_at=? WHERE id=?",
                (None if result is None else json.dumps(result, ensure_ascii=False),
                 _now(), job_id))
        return self.get(job_id)

    def fail(self, job_id: str, error: str, max_attempts: int = 3) -> Job:
        if max_attempts < 1:
            raise ValueError('max_attempts must be positive')
        with self._lock:
            row = self._connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('Задание не найдено.')
            if row['status'] != JobStatus.RUNNING.value:
                raise ValueError('Провалить можно только выполняющееся задание.')
            retry = row['attempts'] < max_attempts
            self._connection.execute(
                'UPDATE jobs SET status=?, error=?, finished_at=?, started_at=? WHERE id=?',
                (JobStatus.QUEUED.value if retry else JobStatus.FAILED.value,
                 str(error)[:2000], None if retry else _now(), None if retry else row['started_at'],
                 job_id))
        return self.get(job_id)

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            row = self._connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('Задание не найдено.')
            status = row['status']
            if status == JobStatus.RUNNING.value:
                raise ValueError('Задание уже выполняется; отмена не поддерживается.')
            if status != JobStatus.QUEUED.value:
                raise ValueError('Отменить можно только задание в очереди.')
            self._connection.execute(
                "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?", (_now(), job_id))
        return self.get(job_id)


class Dispatcher:
    """One thread, one job at a time; handlers never run inside the queue lock."""

    def __init__(self, queue: JobQueue, handlers: dict[JobKind, object], poll_seconds: float = 0.2):
        self.queue = queue
        self.handlers = {JobKind(kind): handler for kind, handler in handlers.items()}
        self.poll_seconds = max(0.05, float(poll_seconds))
        self._stop = Event()
        self._thread = None
        self._lock = RLock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name='music-service-dispatch', daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stop.set()
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=timeout)

    def run_once(self) -> bool:
        job = self.queue.claim_next()
        if job is None:
            return False
        handler = self.handlers.get(job.kind)
        if handler is None:
            self.queue.fail(job.id, f'Нет обработчика для задания {job.kind.value}.', max_attempts=1)
            return True
        try:
            result = handler(job)
        except Exception as error:
            LOGGER.warning('job_failed kind=%s error=%s', job.kind.value, type(error).__name__)
            code = getattr(error, 'code', None)
            detail = f'{type(error).__name__}: {code}: {error}' if isinstance(code, str) \
                else f'{type(error).__name__}: {error}'
            self.queue.fail(job.id, detail)
        else:
            self.queue.complete(job.id, result if isinstance(result, dict) else None)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                LOGGER.exception('dispatch_loop_error')
                worked = False
            if not worked:
                self._stop.wait(self.poll_seconds)
