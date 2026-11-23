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
WEBVIEW2_DOWNLOAD = 'https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download'
WEBVIEW2_MIN_MAJOR = 111


class WebView2Unavailable(RuntimeError):
    pass


class DesktopAlreadyRunning(RuntimeError):
    pass


def application_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def load_packaged_dotnet():
    """Select the private CoreCLR before pywebview imports WinForms."""
    if not getattr(sys, 'frozen', False):
        return
    import pythonnet

    dotnet = application_dir() / 'dotnet'
    config = dotnet / 'Slynor.runtimeconfig.json'
    if not config.is_file():
        raise FileNotFoundError(f'Packaged .NET Desktop configuration is missing: {config}')
    try:
        pythonnet.load('coreclr', dotnet_root=str(dotnet), runtime_config=str(config))
    except Exception as exc:
        raise RuntimeError(f'Could not initialize the packaged .NET Desktop Runtime at {dotnet}') from exc
    runtime = pythonnet.get_runtime_info()
    if runtime is None or runtime.kind.casefold() != 'coreclr' or not runtime.initialized:
        raise RuntimeError(f'Expected the packaged CoreCLR, found {runtime}')

    import clr

    # pywebview imports these types directly. CoreCLR keeps them in separate
    # assemblies that are not loaded just by referencing System.Windows.Forms.
    for assembly in ('Microsoft.Win32.SystemEvents', 'System.Drawing.Primitives', 'System.Drawing.Common'):
        clr.AddReference(assembly)
    logging.getLogger('ownplayer').info('Packaged .NET Desktop Runtime initialized (%s)', runtime.kind)


def require_webview2():
    """Return the fixed Runtime folder, or validate the registered Evergreen Runtime."""
    fixed = application_dir() / 'webview2'
    if (fixed / 'msedgewebview2.exe').is_file():
        return fixed
    import winreg
    path = r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive, view in ((winreg.HKEY_CURRENT_USER, 0), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY)):
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as key:
                version, _ = winreg.QueryValueEx(key, 'pv')
                if int(str(version).split('.', 1)[0]) >= WEBVIEW2_MIN_MAJOR:
                    return None
        except (OSError, ValueError):
            continue
    raise WebView2Unavailable('Требуется актуальный Microsoft Edge WebView2 Runtime.')


def log_dir():
    directory = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Slynor' / 'logs'
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
    error = sys.exc_info()[1]
    logger = logging.getLogger('ownplayer')
    if isinstance(error, DesktopAlreadyRunning):
        logger.info('%s', error)
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            ctypes.windll.user32.MessageBoxW(None, str(error), 'Slynor', 0x40)
        else:
            print(str(error), file=sys.stderr)
        return
    logger.exception('Application startup failed')
    message = 'Slynor could not start. Details:\n' + str(log_dir() / 'startup.log')
    if os.name == 'nt' and isinstance(error, WebView2Unavailable):
        result = ctypes.windll.user32.MessageBoxW(
            None, 'Требуется актуальный Microsoft Edge WebView2 Runtime. Открыть страницу загрузки Microsoft?',
            'Slynor', 0x24)
        if result == 6:
            import webbrowser
            webbrowser.open(WEBVIEW2_DOWNLOAD)
    elif os.name == 'nt' and getattr(sys, 'frozen', False):
        ctypes.windll.user32.MessageBoxW(None, message, 'Slynor - startup error', 0x10)
    else:
        print(message, file=sys.stderr)
