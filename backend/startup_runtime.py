"""Standard-library-only startup support, including PyInstaller --windowed."""
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

_streams = []
_dll_directories = []


def application_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def log_dir():
    directory = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'OwnPlayerMusic' / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class _NonTtyStream:
    """Delegate to a real stream but never report a console.

    Windows NUL is a character device, so its isatty() is True; a windowed
    build must not look interactive to third-party packages.
    """
    def __init__(self, stream):
        self._stream = stream
    def __getattr__(self, name):
        return getattr(self._stream, name)
    def isatty(self):
        return False


def prepare_runtime():
    # Third-party packages sometimes access .write/.flush/.isatty unconditionally.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            stream = _NonTtyStream(open(os.devnull, 'w', encoding='utf-8'))
            _streams.append(stream)
            setattr(sys, name, stream)
    root = logging.getLogger()
    if not any(getattr(h, '_slynor_startup', False) for h in root.handlers):
        handler = RotatingFileHandler(log_dir() / 'startup.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
        handler._slynor_startup = True
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    configure_paths()


def configure_paths():
    base = application_dir()
    if getattr(sys, 'frozen', False):
        # The application is self-contained: prefer its components over PATH or
        # a builder's saved absolute paths. Handles must stay alive on Windows.
        for folder in ('ffmpeg', 'deno', 'vlc'):
            directory = base / folder
            if directory.is_dir():
                os.environ['PATH'] = str(directory) + os.pathsep + os.environ.get('PATH', '')
        ffmpeg = base / 'ffmpeg' / 'ffmpeg.exe'
        if ffmpeg.is_file():os.environ['OWNPLAYER_FFMPEG'] = str(ffmpeg)
        deno = base / 'deno' / 'deno.exe'
        if deno.is_file():os.environ['OWNPLAYER_DENO'] = str(deno)
        vlc = base / 'vlc'
        if (vlc / 'libvlc.dll').is_file():
            os.environ['PYTHON_VLC_LIB_PATH'] = str(vlc / 'libvlc.dll')
            os.environ['PYTHON_VLC_MODULE_PATH'] = str(vlc)
            os.environ['VLC_PLUGIN_PATH'] = str(vlc / 'plugins')
            if os.name == 'nt':_dll_directories.append(os.add_dll_directory(str(vlc)))
        webview = base / 'webview2'
        if (webview / 'msedgewebview2.exe').is_file():
            os.environ['WEBVIEW2_BROWSER_EXECUTABLE_FOLDER'] = str(webview)
        return
    try:
        config = json.loads((application_dir() / 'runtime_paths.json').read_text(encoding='utf-8-sig'))
        value = config.get('ffmpeg')
        if isinstance(value, str) and Path(value).is_file() and Path(value).name.lower() in ('ffmpeg', 'ffmpeg.exe'):
            os.environ.setdefault('OWNPLAYER_FFMPEG', value)
            os.environ['PATH'] = str(Path(value).parent) + os.pathsep + os.environ.get('PATH', '')
    except (OSError, ValueError, AttributeError):
        pass


def show_failure():
    logging.getLogger('ownplayer').exception('Application startup failed')
    message = 'Slynor could not start. Details:\n' + str(log_dir() / 'startup.log')
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        ctypes.windll.user32.MessageBoxW(None, message, 'Slynor - startup error', 0x10)
    else:
        print(message, file=sys.stderr)
