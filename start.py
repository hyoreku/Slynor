"""Запуск локального сервера и окна одной командой."""

import argparse
import os
import sys
import socket
import time
from pathlib import Path
from threading import Thread


PROJECT_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765


# Отказываемся запускаться, если порт уже занят; чужой процесс не завершаем.
def _check_port_free():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((HOST, PORT))
    except OSError as error:
        raise RuntimeError(
            f"Не удалось занять порт {PORT}. Если старый сервер ещё работает, "
            "останови его через Ctrl + C и повтори запуск."
        ) from error


# Запуск Uvicorn в потоке и передача ошибок основному запускателю.
def _run_server(server, errors):
    try:
        server.run()
    except BaseException as error:
        # В том числе SystemExit при невозможности запустить Uvicorn.
        errors.append(error)


# Ожидание готовности API перед открытием окна.
def _wait_until_ready(server, thread, errors, timeout=20):
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
    raise RuntimeError("Сервер не запустился за 20 секунд. Проверь вывод терминала.")


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


# Запуск программы: подготовка ресурсов, работа, завершение.
def main(debug=False):
    # Пути и импорты остаются корректными независимо от папки терминала.
    os.chdir(PROJECT_DIR)
    from backend.diagnostics import setup
    setup()
    _check_port_free()

    import uvicorn
    from backend.main import app

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
            port=PORT,
            log_level="info",
            log_config=None,
            use_colors=False,
            access_log=False,
            loop="asyncio",
            lifespan="on",
        )
    )
    errors = []
    thread = Thread(
        target=_run_server,
        args=(server, errors),
        name="local-api",
        daemon=True,
    )
    thread.start()

    try:
        _wait_until_ready(server, thread, errors)

        # Графическое окно запускается в основном потоке.
        from desktop import main as open_window

        open_window(debug=debug)
    finally:
        _stop_server(server, thread)


def cli(argv=None):
    from backend.startup_runtime import prepare_runtime, show_failure
    prepare_runtime()
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
