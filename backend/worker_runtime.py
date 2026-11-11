"""Resolve isolated worker Python; a frozen GUI is never a Python interpreter."""
import os
from pathlib import Path
import sys


def application_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def worker_python(kind):
    options = {'provider': ('OWNPLAYER_PROVIDER_PYTHON', '.providers'),
               'search': ('OWNPLAYER_SEARCH_PYTHON', '.search')}
    variable, folder = options[kind]
    configured = os.environ.get(variable)
    private = application_dir() / folder / 'python.exe'
    legacy = application_dir() / folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    candidate = Path(configured) if configured else (private if private.is_file() else legacy)
    if candidate.is_file():
        if getattr(sys, 'frozen', False) and candidate.resolve() == Path(sys.executable).resolve():
            raise RuntimeError('Worker Python must not point to Slynor.exe.')
        return str(candidate)
    if configured or getattr(sys, 'frozen', False):
        raise RuntimeError(f'Worker environment {folder} is missing. Re-extract the complete portable package. Source developers: run INSTALL.ps1.')
    return sys.executable


def worker_available(kind):
    """Check the isolated interpreter, not packages in the frozen GUI environment."""
    try:
        executable = worker_python(kind)
    except RuntimeError:
        return False
    if Path(executable).resolve() != Path(sys.executable).resolve():
        return True  # INSTALL.ps1 validates packages inside this environment.
    import importlib.util
    return importlib.util.find_spec('msoc' if kind == 'search' else 'spotdl') is not None
