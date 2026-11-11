# Лицензии: проверено чтением исходников/метаданных, не заключение о готовом релизе

Дата проверки: 2026-10-22. Проверки дистрибутива и прав на конкретную музыку нет.
Лицензия загрузчика не предоставляет права на скачивание/распространение треков.

| Компонент | Что установлено | Что сделать до включения/распространения |
|---|---|---|
| yt-dlp 2026.8.19 | METADATA в .providers: Unlicense; официальный upstream различает wheel/source и bundled EXE | зафиксировать конкретный artifact, wheel-хеш/версию и THIRD_PARTY_LICENSES; готовый PyInstaller EXE upstream может содержать GPLv3+ компоненты |
| yt-dlp-ejs 0.8.0 | METADATA: Unlicense AND MIT AND ISC | включить соответствующие notices и совместимый приватный JS runtime; проверить точные бинарники |
| FFmpeg/ffprobe | upstream: LGPL-2.1+; выбранные GPL-компоненты меняют условия | проверять конфигурацию и лицензию конкретной сборки, комплект библиотек и условия распространения; версия Windows не проверена |
| spotDL 4.5.2 | локальная METADATA и официальный upstream MIT | проверить транзитивные зависимости/источники; исходный мастер Spotify не получается таким matching |
| scdl 3.0.8 | в архиве LICENSE GNU GPL v2; upstream описывает GPL-2.0 и обёртку yt-dlp с версии 3 | уточнить applicable only/or-later по версии и пакетам, obligations для поставки, не считать независимым от yt-dlp |
| musicdl | в архиве отсутствует; текущий upstream __init__ сообщает 2.14.2, LICENSE PolyForm Noncommercial 1.0.0 | коммерческое применение не считать разрешённым автоматически; проверить точный tag/commit и нужный provider до установки |
| SomeDL | текущий upstream pyproject: 1.7.1, GPL-3.0-only, yt-dlp в зависимостях | до измеренной пользы не включать в обязательный runtime; проверить совместимость и source/notices при поставке |
| music-lib guohuiyuan | официальный LICENSE AGPL-3.0, Go-проект | перед использованием проверить provider/commit; для модифицированного сетевого сервиса оценить предоставление соответствующего исходного кода пользователям |
| msoc 0.3.1 | точная версия и ссылка https://github.com/paranoik1/msoc взяты из METADATA; текст лицензии в dist-info не найден; upstream не удалось прочитать | лицензия/исходники/происхождение и права провайдеров не подтверждены; не объявлять допустимым обязательным резервом |
| Mutagen 1.48.1 | METADATA архива: GPL-2.0-or-later | проверить совместимость распространения приложения и уведомления/соответствующие исходники |
| python-vlc / LibVLC | python-vlc METADATA LGPL-2.1+; нативный VLC и plugins — отдельные компоненты | проверить точный Windows bundle, notices, условия заменяемости/исходников и плагины; не переносить лицензию Python binding на весь набор |
| pystray 0.19.5 | METADATA: LGPLv3 | включить применимые notices/исходники/иные условия выбранной поставки |
| MusicBrainz | метаданные/идентификаторы; API требует User-Agent и <=1 запроса/сек | проверить применимые условия данных; не считать аудиоисточником и не скачивать обложки без проверки происхождения |
| Собственный код/графика | корневой LICENSE в переданном дереве не найден; есть SoundCloud brand attribution | владельцу выбрать лицензию собственного кода и подтвердить происхождение assets; не присваивать сторонним компонентам общую MIT-лицензию |

Отдельный процесс не снимает автоматически обязательства GPL/AGPL или условия API/правообладателя.
Приватный репозиторий не отменяет требования распространения приложения приглашённым пользователям.

У текущего musicdl requirements содержит, среди прочего, av, nodejs-wheel и pywidevine. Само присутствие пакета
не доказывает поведение конкретного адаптера, но ставить весь агрегатор автоматически нельзя. DRM-функции
и обход ограничений в Slynor не допускаются. Нужен отдельный разрешённый provider и проверенный набор зависимостей.
Удалённые сервисы отдельных адаптеров проверять по исходникам, авторизации и условиям; отсутствие собственного
API-ключа не доказывает независимость от платного/непроверенного посредника.

## Первичные источники

- [yt-dlp: зависимости и лицензирование artifacts](https://github.com/yt-dlp/yt-dlp)
- [yt-dlp EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
- [spotDL: источники аудио и лицензия](https://github.com/spotDL/spotify-downloader)
- [scdl: статус v3 и лицензия](https://github.com/scdl-org/scdl)
- [musicdl LICENSE](https://github.com/CharlesPikachu/musicdl/blob/master/LICENSE)
- [musicdl __init__](https://github.com/CharlesPikachu/musicdl/blob/master/musicdl/__init__.py)
- [musicdl requirements](https://github.com/CharlesPikachu/musicdl/blob/master/requirements.txt)
- [SomeDL pyproject](https://github.com/ChemistryGull/SomeDL/blob/main/pyproject.toml)
- [music-lib LICENSE](https://github.com/guohuiyuan/music-lib/blob/main/LICENSE)
- [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API)
- [MusicBrainz rate limiting](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting)
- [FFmpeg licensing](https://www.ffmpeg.org/legal.html)

Просмотр master/main — состояние документации на дату, не immutable provenance установленного пакета.
Перед выпуском нужны tag/commit, оригинальные artifacts, SHA-256 и полные license/notices выбранного набора.
