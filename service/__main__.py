"""Run the local Music Service: python -m service [--db PATH] [--port 8766].

The service reuses the player's isolated provider worker, so it never imports
yt-dlp itself. The provider interpreter is the private folder [.providers] of
this project unless OWNPLAYER_PROVIDER_PYTHON points elsewhere.
"""
import argparse
from pathlib import Path
import sys

import uvicorn

from . import db
from .api import create_app
from .assets import AssetStore
from .delivery import FileDeliverySink
from .pipeline import Pipeline
from .providers import PROVIDERS, WorkerProvider
from .worker import SubprocessTransport

PROXY_VARIABLES = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                   'http_proxy', 'https_proxy', 'all_proxy')


def worker_settings():
    """The isolated provider environment that already owns the parser libraries."""
    project = Path(__file__).resolve().parent.parent
    try:
        from backend.worker_runtime import worker_python
        executable = worker_python('provider')
    except (ImportError, RuntimeError):
        executable = sys.executable
    return executable, ['-m', 'backend.provider_worker'], project


def build_pipeline(connection, root, proxy=''):
    executable, arguments, project = worker_settings()
    environment = {}
    if proxy:
        environment['OWNPLAYER_PROXY'] = proxy
        environment.update({name: proxy for name in PROXY_VARIABLES})
        environment['NO_PROXY'] = environment['no_proxy'] = 'localhost,127.0.0.1,::1'
    transport = SubprocessTransport(executable, arguments, project, timeout=20,
                                    environment=environment)
    providers = {name: WorkerProvider(transport, name) for name in PROVIDERS}
    root = Path(root)
    return Pipeline(connection, providers, AssetStore(root), sink=FileDeliverySink(root / 'outbox'))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description='Slynor Music Service (loopback only)')
    parser.add_argument('--db', default=None, help='SQLite file; defaults to %%LOCALAPPDATA%%\\Slynor\\service')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--proxy', default='',
                        help='HTTP/SOCKS proxy for the isolated provider worker, e.g. http://127.0.0.1:10809')
    parser.add_argument('--api-only', action='store_true', help='serve the API without running jobs')
    args = parser.parse_args(argv)
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('The service must stay on loopback until device authentication exists.')
    database = Path(args.db) if args.db else db.default_db_path()
    root = database.parent
    root.mkdir(parents=True, exist_ok=True)

    def factory(connection):
        return build_pipeline(connection, root, args.proxy)

    uvicorn.run(create_app(database, factory, run_jobs=not args.api_only),
                host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
