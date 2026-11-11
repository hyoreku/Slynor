"""Packaged preflight and real local HTTP smoke test. No playback or online queries."""
import importlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.request import build_opener, ProxyHandler
from backend.startup_runtime import log_dir


def _smoke(app):
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
    finally:
        server.should_exit = True
        thread.join(12)
        sock.close()
        if thread.is_alive():
            server.force_exit = True
            raise TimeoutError('Smoke server did not stop')


def run_check(smoke=False):
    data = {'app': 'Slynor', 'check_version': 1, 'frozen': bool(getattr(sys, 'frozen', False)),
            'python': sys.version.split()[0], 'checks': {},
            'scope': 'Dependency imports, assets, native initialization; no sound, online availability or visible GUI is tested.'}
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
        check('webview:winforms', lambda: importlib.import_module('webview.platforms.winforms'))
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
                             'ffmpeg/ffprobe.exe', 'deno/deno.exe', 'webview2/msedgewebview2.exe'):
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
