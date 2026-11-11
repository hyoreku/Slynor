# Этап 0 на Windows: проверки от 2026-10-27

Проверки выполнены на Windows 11 x64 (10.0.22631), CPython 3.13.15 x64, pip 26.2.1.
Плеер на момент backup был закрыт, порт 8765 свободен.

Окружение: VLC 3.0.24, FFmpeg/ffprobe N-127222-g151814650f-20261006 (PATH), WebView2 156.0.4314.50,
Deno отсутствует. Node 20.18.0, Git 2.55.0, PyInstaller 6.16.0 в `.buildenv`, Inno Setup не установлен.

## 1. Backup и контрольный restore

Резервная папка создана вне проекта и вне пользовательских данных. Манифесты с личными путями
и музыкой остаются в резервной папке и в Git не добавлены.

| Объект | Файлов | Размер | Проверка |
|---|---:|---:|---|
| Проект (с `.git`, окружениями, `dist`, ZIP) | 34 283 | 998 МБ | SHA-256 источника до/после копирования совпали; копия совпала с источником |
| `%LOCALAPPDATA%\OwnPlayerMusic` | 156 | 80 МБ | SHA-256 копии совпали |
| Контрольный restore данных в новый каталог | 156 | 80 МБ | SHA-256 совпали с исходным манифестом |
| Внешняя музыка из `library.json`/`playlists.json` | 5 | 68 МБ | Все ссылки найдены; SHA-256 источника и копии совпали |

`library.json` и `playlists.json` ссылаются на 5 файлов вне папки данных (3 в соседнем каталоге
проекта, 2 в FLAC-коллекциях). Сами коллекции целиком не резервировались: библиотека ссылается
только на эти записи. Это ограничение backup, а не доказательство целостности коллекций.

## 2. Чистое восстановление из clone

Клонирован только Git-набор (без `.venv`, `dist`, `runtime_paths.json` и музыки).

1. `py -3.13 -m venv .venv` в новом каталоге.
2. `.\.venv\Scripts\python.exe -m pip install -r requirements\gui.txt` — 37 пакетов, ошибок нет.
3. `pip check` — нарушений нет; импорты GUI/LibVLC/WebView-зависимостей прошли.
4. `python -m unittest discover -s tests` — **433 теста OK, 11 skipped**.
5. `start.py --smoke-test` с изолированным `LOCALAPPDATA` — **20 проверок, все `ok`**
   (импорты, `webview:winforms`, assets, `native:libvlc`, `native:ffmpeg`, `native:ffprobe`,
   `http:api-and-ui`).

Установленные версии совпали с `requirements/observed/gui-win-py313.constraints.txt` по всем
37 пакетам (различаются только написания имён: `clr_loader`/`clr-loader`, `PyYAML`/`pyyaml`).

## 3. Hash-locks: все четыре группы

Свежие venv из клона, установка `-r requirements/<group>.txt`, сверка с observed constraints:
версии совпали полностью, лишних и недостающих пакетов нет.

| Группа | Пакетов | pip check | Импорты | Офлайн hash-установка |
|---|---:|---|---|---|
| GUI | 37 | OK | fastapi, uvicorn, pywebview, vlc, mutagen, PIL, numpy, av, httpx, pystray | OK; 436 тестов OK |
| Provider | 66 | OK | spotdl, scdl, yt-dlp 2026.08.19, ytmusicapi, soundcloud 1.7.0 | OK |
| Search | 39 | OK | msoc, httpx | OK |
| Build | 44 | OK | PyInstaller 6.16.0 | OK, sdist `proxy_tools` собран из локального `setuptools` |

Файлы: `requirements/validated/<group>-win-py313.txt` и `.provenance.json`.
Установка: `--require-hashes --no-index --find-links <wheels>`; для build дополнительно
`--find-links <builddeps>` с `setuptools`/`wheel`.

Ограничения: хеши подтверждают целостность локально скачанных артефактов, но не сверены
с публичными хешами PyPI; системные VLC/FFmpeg/Deno/WebView2 не хешированы.

## 4. INSTALL.ps1 с validated-набором (2026-10-28)

`INSTALL.ps1` и `BUILD_WINDOWS.ps1` переведены на `--require-hashes` с файлами `requirements/validated/`;
при отсутствии lock-файла скрипт останавливается. Проверка установки: новый clone, `INSTALL.ps1 -SkipSystem`,
5,9 мин, код 0. Установлено 37/37, 66/66 и 39/39 пакетов для GUI, provider и search, `pip check` чист,
импорты GUI/LibVLC, spotDL/SCDL/yt-dlp и msoc/httpx прошли. `start.py --smoke-test` в установленном
окружении — все проверки `ok`.
`BUILD_WINDOWS.ps1` изменён так же, но не запускался: нет Inno Setup, native bundle и embed ZIP.

## Что этим НЕ проверено

- Слышимый звук на колонках и установка/обновление/удаление приложения.
- Установщик: Inno Setup на этом ПК отсутствует, `dist/installer` не пересобирался.
- Живые провайдеры, реальный поиск и настоящая сборка PyInstaller: проверены только установка,
  `pip check` и импорты.
- Live-провайдеры и выбор основного/резервного источника (раздел 4 ТЗ).
- Приёмка на чистой виртуальной машине.

## Следующие шаги этапа 0

1. Приёмка GUI со стороны автоматизации выполнена 2026-10-28: [отчёт](windows-stage0-gui-2026-10-28.md).
   Осталось подтверждение слышимого звука человеком.
2. `INSTALL.ps1` проверен в чистом clone с validated-набором (раздел 4); `BUILD_WINDOWS.ps1`
   ждёт Inno Setup и нативный bundle.
3. Ручной просмотр секретов и настройка приватного remote без push до решения владельца.
