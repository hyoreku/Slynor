# Карта кода Slynor

В VS Code открой файл, нажми Ctrl+F и введи имя функции из таблицы.
Номера строк намеренно не указаны: они меняются после добавления кода.

## HTML: frontend/index.html
| Область | Ответственность |
| --- | --- |
| app-shell | Общая сетка приложения |
| sidebar | Навигация и плейлисты слева |
| screen-home | Главная и карточки плейлистов |
| screen-music | Поиск, сортировка и библиотека |
| library-list | Контейнер строк песен; строки создаются JavaScript |
| screen-settings | Цвет, форма карточек, обои и эффекты |
| screen-playing | Крупная обложка, название, исполнитель и возврат к списку |
| playlist-editor | Правая панель редактирования плейлиста |
| wallpaper-layer | Изолированный слой обоев за интерфейсом |
| player-bar | Нижний плеер, видимый лишь при выбранной песне |
| player-cover | Встроенная обложка текущей песни |
| queue-panel | Правый диалог очереди |
| track-menu | Действия с выбранной песней |
| track-details-dialog | Просмотр сведений о файле |

## JavaScript: frontend/js/app.js
| Функция | Ответственность |
| --- | --- |
| renderLibrary | Создать строки песен: запуск, картинка, название, альбом, время, меню |
| getActiveLibraryFiles | Полный состав выбранной библиотеки/плейлиста |
| getOrderedLibraryFiles | Сортировка полного списка перед воспроизведением |
| getFilteredLibraryFiles | Поиск только для отображения, не для ограничения очереди |
| loadLibraryMetadata | Получить текстовые теги и URL картинок с сервера |
| sendCommand | Отправить запуск/паузу/стоп/переключение в FastAPI |
| highlightCurrentTrack | Отметить текущую песню в списке |
| updatePlayerState | Ежесекундное обновление нижней панели |
| flushVolume | Последовательные запросы громкости без кнопки «Применить» |
| renderPlaylists | Карточки плейлистов на главной |
| mutatePlaylists | Общая блокировка и обработка ошибок изменений плейлистов |
| renderSidebarPlaylists | Названия и обложки в боковой панели |
| showScreen | Переключение Главная / Музыка / Настройки / Сейчас играет |
| refreshQueue | Просмотр текущей очереди |
| openTrackMenu | Открытие ⋯ возле строки |
| showTrackDetails | Сведения о файле, без изменения тегов |
| addPlaylistCoverImage | Пользовательская картинка плейлиста |
| setSongCover | Встроенная картинка песни или заглушка |
| openPlaylistEditor / syncPlaylistEditor | Открытие панели и обновление полей без потери ввода |
| updatePlayingPresentation | Крупная обложка, название и исполнитель |
| applyStoredAppearance | Загрузка формы карточек и параметров обоев |
| applyStoredWallpaper | Проверка загрузки изображения и применение фона |
| previewWallpaperEffects | Предпросмотр затемнения и размытия через CSS |
| appearanceMutation | Блокировка сохранения, обработка ошибок |

## CSS: frontend/css/styles.css
В файле пронумерованы разделы:
1. Переменные темы; 2. Базовые элементы; 3. Каркас; 4. Главная/плейлисты;
5. Библиотека/меню; 6. Нижний плеер; 7. Настройки; 8. Очередь;
9. Маленькие окна; 10. Пользовательские обложки; 11. Обложки песен.
Раздел 35 — нейтральный стиль (плеер, «Сейчас играет», главная, библиотека,
настройки, очередь), перенесённый из пилотного слоя; он идёт последним намеренно.
Медиа-запросы переопределяют размеры. Для изменения сетки строк учитывай
раздел 11: он добавляет колонку миниатюры поверх первоначальной сетки раздела 5.

## Python
| Файл | Ответственность |
| --- | --- |
| start.py | Запустить сервер, дождаться готовности, открыть окно, завершить сервер |
| desktop.py | Диалоги Windows, сохранение библиотеки/плейлистов, выбор обложки плейлиста |
| backend/main.py | HTTP-маршруты, проверка запросов, выдача HTML/CSS/JS/JPEG |
| backend/metadata.py | Название, исполнитель, альбом, длительность, ссылка на обложку |
| backend/track_covers.py | Извлечение встроенных изображений и кэш обложек песен |
| backend/image_assets.py | Нормализация и хранение пользовательских картинок плейлистов |
| backend/wallpaper_assets.py | Проверка и хранение копий обоев |
| player/audio_engine.py | VLC, воспроизведение, очередь, shuffle, громкость, перемотка |
| tools/check_audio.py | Проверить локальный файл и распознавание формата |
| tools/test_audio.py | Отдельно проверить VLC без интерфейса и API |

## Путь одной команды
1. Нажатие ▶ вызывает sendCommand("play") в JavaScript.
2. JavaScript собирает полную очередь и отправляет POST /api/player/queue/play.
3. backend/main.py проверяет запрос через QueuePlayRequest.
4. AudioEngine.play_queue() запускает файл в VLC.
5. updatePlayerState() получает состояние и обновляет нижнюю панель.
6. Метаданные загружаются отдельно: backend/metadata.py + track_covers.py.

Не изменяй id и имена API-маршрутов в одном файле без изменений в другом:
HTML, JavaScript и Python связаны этими именами.


## Дополнение этапа 5
- `setPlayerVisible`: панель существует только при выбранной песне.
- `applyAccent`: цвета CSS из выбранного HEX.
- `previewAccent`: изменения только на экране.
- `initializeSettings`: чтение цвета при готовности Python-моста.
- `saveAccent`: сохранение через DesktopApi.
- `DesktopApi.load_settings/save_settings`: settings.json отдельно от музыки.
- `AudioEngine.stop`: теперь снимает песню и очередь; пауза их сохраняет.
- CSS-разделы 12 и 13: видимость панели/анимация и настройки акцента.

## Последнее обновление
Разделы JS 14–16 и CSS 14–17: карточки, боковой редактор, экран песни и обои.
Подробности: `PLAYLISTS_NOW_PLAYING_WALLPAPER.md`. Старые инструкции кнопок на карточке заменены панелью.

## Этап визуальных эффектов
Подробная инструкция: VISUAL_EFFECTS_GUIDE.md.
- `cover-atmosphere` / `spectrum-canvas`: слои за контентом рабочей области.
- `visuals.js`: настройки эффектов, RGB-кластеры, подгонка обложки, заполнение
  ползунков, запрос FFT, сглаживание и canvas. Блоки пронумерованы 1–4.
- `player/spectrum.py`: `fft_bands`, `AudioWindowReader` (малый PCM-буфер),
  `SpectrumAnalyzer` (один поток, только последний запрос, защита устаревших данных).
- `DesktopApi.save_visual_effects`: boolean-переключатели, атомарное сохранение.
- CSS 18–22: скроллбары, небольшой плеер, полосы, анимации и градиент строки,
  экран без прокрутки, фон/спектр и настройки.

## Этап 8 — время и эквалайзер
- `playerMotion.state/playback/seek/stop`: плавное время без дополнительных seek.
- `equalizer.js`, блоки 1–4: управление, частоты, последовательный предпросмотр, сохранение.
- `AudioEngine.get_equalizer_frequencies/set_equalizer/get_equalizer`: настоящий фильтр VLC.
- `EqualizerController`: строгая проверка, пресеты, current/saved и атомарная запись.
- CSS 23–24: появление панелей и адаптивные частотные ползунки.
Подробности и ограничения: EQUALIZER_AND_MOTION_GUIDE.md.

## Этап 9 — актуальная архитектура
- `PostEqSpectrumAnalyzer` и `ProcessedReader`: worker, ограниченный PCM-буфер,
  беззвучная LibVLC-ветка после EQ; основной вывод не перенастраивается.
- `EqualizerController.create_preset/delete_preset`: именованные пресеты, атомарная запись.
- `equalizerPanel.layout/open/close`: overlay под обложкой, фокус и Escape.
- `theme.js`, `DesktopApi.save_theme`: полная светлая/тёмная палитра и сохранение.
- CSS 25–26: темы, компактные иконки и responsive-панель эквалайзера.
- `visuals.js`: непрерывный requestAnimationFrame, интерполяция входящих FFT-пакетов.

## Этап 10 — стабильность и локализация
Актуальные изменения и карта новых файлов: `STAGE10_FIXES_AND_LANGUAGE.md`.
`i18n.js` содержит читаемый каталог RU/EN, `preferences.js` — сохранение языка
и режима диагностики, `audio_engine.py` — constant-filter bypass и скорость.


## Этап 11 — навигация и скорость

- `frontend/js/sidebar.js`: ручное/60-секундное сворачивание, блокирующие состояния,
  lifecycle окна, последовательная запись только настройки панели.
- `app.js`: `captureLibraryContext`, `restorePlaybackLibraryContext`,
  `renderPlaylistSummary`, перенос кнопки в topbar.
- `equalizer.js`: десять частот, предусиление отдельно в заголовке, компактная высота.
- `visuals.js`: пересчёт оформления при изменении ширины панели.
- `styles.css`, разделы 30–32: 80 px, карточка плейлиста, горизонтальное предусиление.
- `DesktopApi.save_sidebar_preferences`: строгий boolean и атомарная запись.
- `AudioEngine._process_events`: Playing/Ended/Error с поколением; скорость
  нового декодера, без методов управления VLC в callback.
- `tests/test_regressions.py`: 21 тест с имитацией VLC/desktop-моста.
- `tools/test_ui.cjs`: Chromium-регрессии с имитацией API и Python-моста.
- `STAGE11_STABILITY_AND_NAVIGATION.md`: установка, Windows-проверки и следующий план.


## Этап 12 — текущие новые модули

- `backend/online_music.py`: SoundCloud client credentials/refresh, поиск,
  нормализация, полные HLS, публичный импорт/пагинация, атомарный файл,
  непредсказуемые сессии прокси и allowlist URI.
- `backend/local_session.py`, middleware в `main.py`: процессная сессия и Origin/Host.
- `backend/desktop_controls.py`: связь API с нативным контроллером.
- `desktop_shell.py`: pystray, mini window, lifecycle и явный выход.
- `frontend/mini.html`, `css/mini.css`, `js/mini.js`: лёгкий UI того же движка.
- `frontend/js/online.js`: поиск, статусы доступа, публичный импорт, онлайн-очередь.
- `frontend/js/desktop.js`: окно/трей и сохранение настройки.
- `app.js`: безопасные remote artwork, обложки очереди, cancellable close.
- `sidebar.js`: настоящая интерполяция ширины/прерывание анимации.
- `equalizer.js` + API: command_apply_ms (НЕ измерение слышимой задержки).
- `AudioEngine.stream_resolver/report_stream_error`: онлайн-reference → localhost,
  ошибки только текущей media-сессии. Самостоятельные произвольные URL не принимаются.
- `tools/start_with_soundcloud.py`: локальный скрытый ввод ключа без записи на диск.
- `tests/test_online_music.py`, `tests/test_stage12_shell_api.py`,
  `tools/test_stage12_ui.cjs`: новые регрессии на имитациях; не Windows/audio приёмка.

## Исследовательский стенд источников

| Файл | Ответственность |
|---|---|
| `tools/source_experiment/run.py` | контроллер: фиксированные 60 запросов, cold/warm, маршрут direct/proxy, сроки и журналы |
| `tools/source_experiment/worker.py` | metadata-адаптеры yt-dlp, msoc Hitmo/Muzbomb, Archive и каталог B; без скачивания |
| `tools/source_experiment/common.py` | общий протокол, валидация запроса/URL/маршрута, очистка карточек |
| `tools/source_experiment/report.py` | сводка; ручная оценка и доступность остаются отдельными файлами |
| `tools/source_experiment/integrity.py` | проверка полноты набора, связей журналов и отрицательных контролей |
| `tools/source_experiment/compare.py` | сравнение домашнего и VPS-прогонов; разные маршруты не смешивает |
| `tools/source_experiment/START_SEARCH.ps1` | Windows-запуск, подготовка research-env, `-ProxyUrl` |
| `tests/test_source_experiment.py` | протокол, маршрут, ресурсы, отчёт и ручные файлы |
| `tests/test_catalogue_http.py` | транспорт каталога B: allowlist, лимиты, явный прокси |

## Music Service (этап 1)

| Файл | Ответственность |
|---|---|
| `service/models.py` | `Recording`, `AudioCandidate`, `AudioAsset`, `Job`, `Delivery`; строгая валидация полей |
| `service/errors.py` | `ServiceError` и стабильные машинные коды без секретов |
| `service/worker.py` | один JSON-запрос к изолированному обработчику: срок, предел ответа, allowlist окружения |
| `service/providers.py` | стабильные ID, публичные страницы, `WorkerProvider` поверх `backend/provider_worker.py` |
| `service/media.py` | SHA-256, размер, контейнер, кодек и измеренная длительность |
| `service/assets.py` | хранилище `assets/<ab>/<sha256>.<ext>` с видимыми лимитами |
| `service/delivery.py` | `FileDeliverySink`: выдача в `outbox`; контракт для Telegram |
| `service/pipeline.py` | задания поиска, скачивания и доставки поверх записей |
| `service/db.py` | SQLite WAL, схема версии 1, репозитории без ORM |
| `service/jobs.py` | персистентная очередь, повторы, отмена, один диспетчер |
| `service/api.py` | локальный FastAPI: здоровье, провайдеры, лимиты, задания, запись, ассет, доставки |
| `service/__main__.py` | запуск `python -m service` с реальным конвейером, `--proxy`, `--api-only` |
| `tests/test_service_core.py` | модели, схема, каскады, цикл задания, диспетчер |
| `tests/test_service_api.py` | HTTP-контракт и сохранность заданий |
| `tests/test_service_providers.py` | разбор ID и ссылок, выдача обработчика, скачивание, транспорт |
| `tests/test_service_media.py` | измерение файла против каталога |
| `tests/test_service_assets.py` | хеш-имена, лимиты, защита пути |
| `tests/test_service_pipeline.py` | поиск → кандидат → скачивание → доставка и HTTP-поток |
| `docs/SERVICE_STAGE1.md` | контракт, запуск, гарантии и ограничения среза |
