"""One bounded JSON request to an isolated worker interpreter.

Provider libraries (yt-dlp, spotDL, SCDL) live in private environments that the
service never imports. This transport is the only bridge: JSON in, one JSON
object out, a hard deadline and a hard response cap. A worker inherits only an
allowlist of environment variables, so proxy credentials and unrelated parent
settings cannot leak into it.
"""
import json
import os
import subprocess

from .errors import ServiceError

MAX_REQUEST_BYTES = 12_000
MAX_RESPONSE_BYTES = 160 * 1024
INHERITED_ENVIRONMENT = (
    'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'USERPROFILE',
    'LOCALAPPDATA', 'APPDATA', 'LANG', 'PYTHONIOENCODING', 'PYTHONUTF8',
)


class WorkerError(ServiceError):
    """The worker could not be started, timed out or answered with garbage."""

    code = 'worker_error'


class SubprocessTransport:
    """Run one worker command per request; never keep a worker process around."""

    def __init__(self, executable, arguments, cwd, *, timeout=20.0,
                 environment=None, max_response_bytes=MAX_RESPONSE_BYTES):
        self.executable = str(executable)
        self.arguments = tuple(str(argument) for argument in arguments)
        self.cwd = str(cwd)
        self.timeout = max(0.5, float(timeout))
        self.environment = {str(key): str(value) for key, value in (environment or {}).items()}
        self.max_response_bytes = max(1024, int(max_response_bytes))

    def child_environment(self):
        values = {name: os.environ[name] for name in INHERITED_ENVIRONMENT if name in os.environ}
        values['PYTHONIOENCODING'] = 'utf-8'
        values['PYTHONUTF8'] = '1'
        values.update(self.environment)
        return values

    def request(self, payload, timeout=None):
        if not isinstance(payload, dict):
            raise WorkerError('Запрос к внешнему обработчику должен быть объектом.',
                              code='worker_bad_request')
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if len(data) > MAX_REQUEST_BYTES:
            raise WorkerError('Запрос к внешнему обработчику слишком велик.',
                              code='worker_request_too_large')
        deadline = self.timeout if timeout is None else max(0.5, float(timeout))
        options = {}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(
                [self.executable, *self.arguments], cwd=self.cwd,
                env=self.child_environment(), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
        except OSError:
            raise WorkerError('Внешнее окружение загрузчика недоступно.',
                              code='worker_unavailable') from None
        try:
            output, _ = process.communicate(data, timeout=deadline)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            raise WorkerError('Внешний обработчик не ответил вовремя.',
                              code='worker_timeout') from None
        if len(output) > self.max_response_bytes:
            raise WorkerError('Ответ внешнего обработчика слишком велик.',
                              code='worker_response_too_large')
        try:
            result = json.loads(output.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise WorkerError('Внешний обработчик вернул некорректный ответ.',
                              code='worker_bad_response') from None
        if not isinstance(result, dict):
            raise WorkerError('Внешний обработчик вернул некорректный ответ.',
                              code='worker_bad_response')
        return result
