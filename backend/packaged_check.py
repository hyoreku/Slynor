"""Packaged preflight with real local HTTP and LibVLC decoding checks."""
from contextlib import contextmanager
import importlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import wave
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import URLError
from backend.startup_runtime import log_dir


@contextmanager
def _isolated_music_service():
    with TemporaryDirectory(prefix="slynor-music-check-") as temporary:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        settings = {"SLYNOR_SERVICE_URL": url, "SLYNOR_SERVICE_DB": str(Path(temporary) / "smoke.sqlite3")}
        previous = {key: os.environ.get(key) for key in settings}
        os.environ.update(settings)
        try:
            yield url
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _smoke(app):
    with _isolated_music_service() as music_url:
        # CI hosts can lack a speaker. The same engine and decoder send PCM to
        # a dummy output, while the normal application uses the selected device.
        app.state.smoke_audio_options = ('--aout=dummy',)
        try:
            _smoke_api(app, music_url)
        finally:
            del app.state.smoke_audio_options


def _smoke_api(app, music_url):
    import uvicorn
    # Bind once to an ephemeral port; don't race another process for a free port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        loop='asyncio', http='h11', ws='none', lifespan='on', log_config=None,
        use_colors=False, access_log=False))
    errors = []
    def serve():
        try:
            server.run(sockets=[sock])
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=serve, name='packaged-smoke', daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            if errors or not thread.is_alive():
                raise RuntimeError('Smoke server failed to start') from (errors[0] if errors else None)
            if time.monotonic() > deadline:
                raise TimeoutError('Smoke server startup timeout')
            time.sleep(.05)
        opener = build_opener(ProxyHandler({}))
        for route in ('/', '/ui', '/mini', '/static/js/app.js'):
            with opener.open(f'http://127.0.0.1:{port}' + route, timeout=5) as response:
                if response.status != 200 or not response.read(32):
                    raise RuntimeError('Empty or failed HTTP response: ' + route)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with opener.open(music_url + '/health', timeout=2) as response:
                    health = json.load(response)
                if health.get('status') == 'ok' and health.get('processing_jobs'):
                    break
            except URLError:
                pass
            time.sleep(.2)
        else:
            raise RuntimeError('Music Service did not start its job dispatchers')
        _smoke_local_audio(opener, f'http://127.0.0.1:{port}')
    finally:
        server.should_exit = True
        thread.join(12)
        sock.close()
        if thread.is_alive():
            server.force_exit = True
            raise TimeoutError('Smoke server did not stop')


def _smoke_local_audio(opener, base_url):
    from backend.local_session import APP_TOKEN

    def request(route, body=None, method=None, status=200):
        payload = None if body is None else json.dumps(body).encode()
        headers = {'X-OwnPlayer-Token': APP_TOKEN, 'Content-Type': 'application/json'}
        with opener.open(Request(base_url + route, data=payload, headers=headers, method=method), timeout=4) as response:
            if response.status != status:
                raise RuntimeError(f'Unexpected playback smoke HTTP status: {response.status}')
            return json.load(response)

    def wait_for_playing(path):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = request('/api/player/state')
            if state['state'] == 'error':
                raise RuntimeError('LibVLC reported a playback error')
            if state.get('file_path') == path and state['state'] == 'playing' and state['time_ms'] > 0:
                return
            time.sleep(.1)
        raise TimeoutError('LibVLC did not advance playback for the local WAV')

    with TemporaryDirectory(prefix='Slynor local audio ') as temporary:
        paths = [str(Path(temporary) / name) for name in ('Первый.wav', 'Second.wav')]
        for path in paths:
            with wave.open(path, 'wb') as audio:
                audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\x00\x00' * 8000 * 8)
        started = request('/api/player/queue/play?deferred=true', {'paths': paths, 'index': 0}, status=202)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = request('/api/player/queue/starts/' + started['id'])
            if result['status'] == 'ok':
                break
            if result['status'] == 'error':
                raise RuntimeError('Local audio command failed: ' + result['message'])
            time.sleep(.1)
        else:
            raise TimeoutError('Local audio command did not complete')
        try:
            wait_for_playing(paths[0])
            request('/api/player/next', method='POST')
            wait_for_playing(paths[1])
        finally:
            request('/api/player/stop', method='POST')
    logging.getLogger('ownplayer').info('Verified local WAV playback and queue advancement using LibVLC')


def run_check(smoke=False):
    data = {'app': 'Slynor', 'check_version': 1, 'frozen': bool(getattr(sys, 'frozen', False)),
            'python': sys.version.split()[0], 'checks': {},
            'scope': ('Dependency imports, assets and native initialization. Smoke also checks local WAV decoding '
                      'and queue advancement with a dummy audio output.')}
    def check(name, fn):
        try:
            fn()
            data['checks'][name] = {'ok': True}
        except Exception as exc:
            logging.getLogger('ownplayer').exception('Runtime check failed: %s', name)
            data['checks'][name] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    for module in ('fastapi', 'uvicorn', 'webview', 'clr_loader', 'pythonnet', 'mutagen',
                   'PIL.Image', 'numpy', 'av', 'pystray', 'httpx', 'socksio', 'vlc', 'backend.main'):
        check('import:' + module, lambda m=module: importlib.import_module(m))
    if os.name == 'nt':
        def desktop_webview():
            from desktop import configure_webview
            if configure_webview() != 'edgechromium':
                raise RuntimeError('Windows desktop did not select WebView2')
        check('webview:runtime', desktop_webview)
        if data['checks']['webview:runtime']['ok']:
            def winforms():
                from webview.platforms import winforms
                if winforms.renderer != 'edgechromium':
                    raise RuntimeError(f'WinForms selected an unexpected renderer: {winforms.renderer}')
            check('webview:winforms', winforms)
    def assets():
        root = Path(__file__).resolve().parent.parent
        for relative in ('frontend/index.html', 'frontend/mini.html', 'frontend/js/app.js',
                         'frontend/assets/icons/app.ico', 'backend/provider_worker.py',
                         'backend/msoc_worker.py', 'backend/proxy_worker.py', 'backend/__init__.py'):
            if not (root / relative).is_file():
                raise FileNotFoundError(relative)
    check('assets', assets)
    def audio():
        from player.audio_engine import AudioEngine
        engine = AudioEngine()
        engine.close()
    check('native:libvlc', audio)
    def ffmpeg(name):
        executable = (os.environ.get('OWNPLAYER_FFMPEG') if name == 'ffmpeg' else None) or shutil.which(name)
        if not executable:
            raise FileNotFoundError(name + ' is missing from PATH')
        result = subprocess.run([executable, '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        if result.returncode:
            raise RuntimeError(name + ' exited with code ' + str(result.returncode))
    check('native:ffmpeg', lambda: ffmpeg('ffmpeg'))
    check('native:ffprobe', lambda: ffmpeg('ffprobe'))
    if getattr(sys, 'frozen', False):
        from backend.worker_runtime import application_dir, worker_python
        def private_worker(kind):
            executable = Path(worker_python(kind)).resolve()
            base = application_dir().resolve()
            if executable.parent != (base / ('.providers' if kind == 'provider' else '.search')):
                raise RuntimeError('Self-contained checks require a private embedded worker')
            code = ('import spotdl,scdl,yt_dlp,yt_dlp_ejs; import backend.provider_worker'
                    if kind == 'provider' else 'import msoc,httpx; import backend.msoc_worker')
            result = subprocess.run([str(executable), '-c', code], cwd=Path(__file__).resolve().parent.parent,
                                    capture_output=True, timeout=30,
                                    **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
            if result.returncode:raise RuntimeError('Private worker import check failed')
        check('private:provider', lambda: private_worker('provider'))
        check('private:search', lambda: private_worker('search'))
        def native_bundle():
            base = application_dir()
            for relative in ('vlc/libvlc.dll', 'vlc/libvlccore.dll', 'ffmpeg/ffmpeg.exe',
                             'ffmpeg/ffprobe.exe', 'deno/deno.exe'):
                if not (base / relative).is_file():raise FileNotFoundError(relative)
            if not (base / 'vlc/plugins').is_dir():raise FileNotFoundError('vlc/plugins')
            result = subprocess.run([str(base / 'deno/deno.exe'), '--version'],
                                    capture_output=True, timeout=10,
                                    **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
            if result.returncode:raise RuntimeError('Private Deno check failed')
        check('private:native-bundle', native_bundle)
    if smoke and all(v['ok'] for v in data['checks'].values()):
        from backend.main import app
        check('http:api-and-ui', lambda: _smoke(app))
    data['ok'] = all(v['ok'] for v in data['checks'].values())
    filename = 'smoke-check.json' if smoke else 'runtime-check.json'
    (log_dir() / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data['ok'] else 1
