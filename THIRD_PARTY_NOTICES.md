# Сторонние компоненты и их лицензии

Slynor распространяется под GNU General Public License версии 2 или более поздней
(GPL-2.0-or-later), полный текст — в файле [LICENSE](LICENSE).

Ниже перечислены сторонние компоненты, которые входят в поставку или используются во время работы.
Список составлен по метаданным установленных пакетов на 2026-11-11; точные версии фиксируются
при сборке. Каждый пакет распространяется со своим файлом лицензии — при сборке дистрибутива
эти файлы и тексты уведомлений должны попасть в поставку.

## Python-зависимости приложения

| Компонент | Версия | Лицензия |
|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | 0.143.3 | MIT |
| [Starlette](https://www.starlette.io/) | 1.8.0 | BSD-3-Clause |
| [Uvicorn](https://www.uvicorn.org/) | 0.54.0 | BSD-3-Clause |
| [pydantic](https://docs.pydantic.dev/) | 2.14.1 | MIT |
| [pywebview](https://pywebview.flowrl.com/) | 6.2.1 | BSD-3-Clause |
| [python-vlc](https://github.com/oaubert/python-vlc) | 3.0.21203 | LGPL-2.1-or-later |
| [mutagen](https://mutagen.readthedocs.io/) | 1.48.1 | GPL-2.0-or-later |
| [Pillow](https://python-pillow.org/) | 12.3.0 | MIT-CMU |
| [NumPy](https://numpy.org/) | 2.5.4 | BSD-3-Clause (и компоненты 0BSD, MIT, Zlib, CC0-1.0) |
| [PyAV](https://pyav.org/) | 18.1.0 | BSD-3-Clause |
| [pystray](https://github.com/moses-palmer/pystray) | 0.19.5 | LGPL-3.0 |
| [httpx](https://www.python-httpx.org/) | 0.28.1 | BSD-3-Clause |
| [socksio](https://github.com/sethmlarson/socksio) | 1.0.0 | MIT |
| [typing-extensions](https://github.com/python/typing_extensions) | 4.16.0 | PSF-2.0 |

## Инструменты поиска и загрузки (изолированное окружение .providers)

| Компонент | Версия | Лицензия |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 2026.8.19 | Unlicense |
| [yt-dlp-ejs](https://github.com/yt-dlp/yt-dlp-ejs) | 0.8.0 | Unlicense AND MIT AND ISC |
| [spotDL](https://github.com/spotDL/spotify-downloader) | 4.5.2 | MIT |
| [scdl](https://github.com/scdl-org/scdl) | 3.0.8 | GPL-2.0 (файл LICENSE в пакете) |

## Нативные компоненты

| Компонент | Лицензия | Примечание |
|---|---|---|
| LibVLC и плагины VLC | LGPL-2.1-or-later; часть плагинов — GPL | поставляется как отдельные динамические библиотеки; при распространении нужны уведомления и возможность замены |
| FFmpeg / ffprobe | LGPL-2.1-or-later или GPL-2.0+ — зависит от конфигурации сборки | условия определяет конкретный скачанный бинарник |
| Microsoft Edge WebView2 Runtime | лицензия Microsoft | устанавливается отдельно, в поставку Slynor не входит |

## Инструменты сборки (в поставку не входят)

| Компонент | Лицензия |
|---|---|
| [PyInstaller](https://pyinstaller.org/) | GPL-2.0-or-later с исключением для загрузчика |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | freeware, см. LICENSE.TXT в установке Inno Setup |

## Что это значит на практике

- Код Slynor под GPL-2.0-or-later, поэтому при распространении сборки или изменённой версии
  исходный код должен быть доступен получателям. Официальный репозиторий:
  <https://github.com/hyoreku/Slynor>.
- Компоненты под LGPL (python-vlc, LibVLC, pystray) используются как динамически загружаемые
  библиотеки; заменять их на изменённые версии пользователь может самостоятельно.
- Компоненты под GPL (mutagen, scdl) совместимы с лицензией проекта.
- Лицензия на код не даёт прав на музыку: скачивание и распространение записей регулируется
  условиями сервисов и законодательством.

Если вы нашли неточность в этом списке, откройте issue в репозитории проекта.
