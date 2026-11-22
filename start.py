"""Запуск локального сервера и окна одной командой."""

import argparse
from contextlib import contextmanager
import logging
import os
import sys
import socket
import time
from pathlib import Path
from threading import Thread

from backend.local_session import DESKTOP_HOST, DESKTOP_PORT


PROJECT_DIR = Path(__file__).resolve().parent
HOST = DESKTOP_HOST
PORT = DESKTOP_PORT


@contextmanager
def _desktop_lock():
    from backend.startup_runtime import DesktopAlreadyRunning, log_dir
    with (log_dir().parent / "desktop.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise DesktopAlreadyRunning(
                "Slynor уже запущен или ещё открывается. Проверьте окно и значок в трее."
            ) from error
        yield


def _reserve_desktop_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, PORT))
        except OSError:
            # Use the same reserved socket for Uvicorn to avoid a second bind.
            sock.bind((HOST, 0))
        return sock
    except BaseException:
        sock.close()
        raise


# Запуск Uvicorn в потоке и передача ошибок основному запускателю.
def _run_server(server, sock, errors):
    try:
        server.run(sockets=[sock])
    except BaseException as error:
        # В том числе SystemExit при невозможности запустить Uvicorn.
        errors.append(error)


# Ожидание готовности API перед открытием окна.
def _wait_until_ready(server, thread, errors, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if errors:
            raise RuntimeError(
                f"Ошибка запуска сервера: {type(errors[0]).__name__}: {errors[0]}"
            ) from errors[0]
        if not thread.is_alive():
            raise RuntimeError("Сервер завершился до открытия окна. Проверь вывод выше.")
        if server.started:
            return
        time.sleep(0.1)
    from backend.diagnostics import dump_stacks
    dump_stacks()
    raise RuntimeError(f"Локальный API Slynor не ответил за {timeout} с.")


# Штатное завершение сервера и его lifespan после закрытия окна.
def _stop_server(server, thread):
    # Uvicorn завершит запросы и вызовет lifespan приложения,
    # где закрывается аудиодвижок.
    server.should_exit = True
    thread.join(timeout=10)
    if thread.is_alive():
        server.force_exit = True
        thread.join(timeout=2)
        if thread.is_alive():
            print("Предупреждение: сервер не успел штатно завершиться.")


def main(debug=False):
    os.chdir(PROJECT_DIR)
    from backend.diagnostics import setup
    setup()
    with _desktop_lock(), _reserve_desktop_socket() as sock:
        _open_desktop(sock, debug)


def _open_desktop(sock, debug):
    import uvicorn
    from backend.main import app

    port = sock.getsockname()[1]
    desktop_url = f"http://{HOST}:{port}"
    app.state.desktop_url = desktop_url
    app.state.desktop_port = port
    logging.getLogger("ownplayer").info("Desktop API reserved %s", desktop_url)

    server = uvicorn.Server(
        uvicorn.Config(
            app, host=HOST, port=port, log_level="info", log_config=None,
            use_colors=False, access_log=False, loop="asyncio", lifespan="on",
            timeout_graceful_shutdown=6,
        )
    )
    errors = []
    thread = Thread(target=_run_server, args=(server, sock, errors), name="local-api", daemon=True)
    thread.start()
    try:
        _wait_until_ready(server, thread, errors)
        from desktop import main as open_window
        open_window(debug=debug, base_url=desktop_url)
    finally:
        _stop_server(server, thread)


def cli(argv=None):
    from backend.startup_runtime import prepare_runtime, show_failure
    prepare_runtime()
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--music-service"]:
        try:
            from service.__main__ import main as run_music_service
            run_music_service(argv[1:])
            return 0
        except KeyboardInterrupt:
            return 130
        except Exception:
            logging.getLogger("ownplayer.service").exception("Music Service startup failed")
            return 1
    parser = argparse.ArgumentParser(description="Запуск Slynor")
    parser.add_argument("--debug", action="store_true", help="Отладка интерфейса WebView")
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--check-runtime", action="store_true", help="Check packaged dependencies without opening a window")
    checks.add_argument("--smoke-test", action="store_true", help="Check API and UI resources without opening a window")
    args = parser.parse_args(argv)
    try:
        if args.check_runtime or args.smoke_test:
            from backend.packaged_check import run_check
            return run_check(smoke=args.smoke_test)
        main(debug=args.debug)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        show_failure()
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
