"""Start and supervise the local Music Service process for the desktop app.

The service stays a separate process and owns its provider worker. Launching it
from the app is a convenience: if another instance is already healthy, it is
reused and never stopped by this process. The Windows app launches its
headless Music Service mode with the bundled runtime.
"""
import os
from pathlib import Path
import subprocess
import sys
from threading import RLock
from time import monotonic, sleep
from urllib.parse import urlsplit

from .service_client import ServiceClient

PROJECT_DIR = Path(__file__).resolve().parent.parent


class ServiceRuntime:
    def __init__(self, client=None, port=None, database=None):
        self.client = client or ServiceClient()
        self.port = int(port if port is not None else urlsplit(self.client.base_url).port or 8766)
        self.database = Path(database) if database else None
        self.process = None
        self.error = None
        self._lock = RLock()
        self._last_check = 0.0
        self._last_ok = False
        self._starting = False
        self._stopped = False

    def status(self, refresh=False):
        if refresh or monotonic() - self._last_check > 2:
            self._last_ok = self.client.health()
            self._last_check = monotonic()
        with self._lock:
            starting = self._starting
        if self._last_ok:
            error = None
        elif starting:
            error = 'Music Service запускается…'
        else:
            error = self.error or 'Music Service не запущен.'
        return {'available': self._last_ok, 'running': self._last_ok,
                'starting': starting, 'url': self.client.base_url, 'error': error}

    @staticmethod
    def _proxy():
        try:
            from backend.network import NetworkSettings
            from backend.image_assets import get_data_dir
            value = NetworkSettings(get_data_dir()).resolve() or ''
            if not value:
                return ''
            parsed = urlsplit(value)
            # Credentials must never appear in a child command line.
            if parsed.username is not None or parsed.password is not None:
                return ''
            return value
        except Exception:
            return ''

    def _command(self):
        entry = ['--music-service'] if getattr(sys, 'frozen', False) else ['-m', 'service']
        command = [sys.executable, *entry, '--port', str(self.port)]
        if self.database:
            command += ['--db', str(self.database)]
        proxy = self._proxy()
        if proxy:
            command += ['--proxy', proxy]
        return command

    def ensure_started(self, timeout=25.0):
        if self.client.health():
            self.error = None
            return True
        with self._lock:
            if self._stopped:
                return False
            process = self.process
            already = self._starting
            if not already:
                self._starting = True
        if not already:
            try:
                options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
                if getattr(sys, 'frozen', False):
                    options['env'] = {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
                process = subprocess.Popen(self._command(), cwd=PROJECT_DIR,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           **options)
            except OSError:
                with self._lock:
                    self._starting = False
                self.error = 'Не удалось запустить Music Service.'
                return False
            with self._lock:
                stopped = self._stopped
                if not stopped:
                    self.process = process
            if stopped:
                self._terminate(process)
                return False
        deadline = monotonic() + max(3.0, float(timeout))
        try:
            while monotonic() < deadline:
                if self.client.health():
                    self.error = None
                    return True
                with self._lock:
                    if self._stopped:
                        return False
                    current = self.process
                if current is not None and current.poll() is not None:
                    self.error = 'Music Service завершился при запуске.'
                    return False
                sleep(0.3)
            self.error = 'Music Service не ответил за ' + str(int(timeout)) + ' с.'
            return False
        finally:
            with self._lock:
                self._starting = False

    def stop(self):
        with self._lock:
            self._stopped = True
            process = self.process
            self.process = None
        self._terminate(process)

    @staticmethod
    def _terminate(process):
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
