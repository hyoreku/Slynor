# Передача состояния: следующий чат

Дата: 2026-10-28. Ветка `main`, коммит `7637133`; эта передача — следующий коммит.
Рабочее дерево чистое.
Проект: личный Windows-плеер **Slynor** (Python 3.13 + pywebview + локальный FastAPI + LibVLC).
Источник истины — [ТЗ](../Slynor_TZ_v0.1_RU.md); карта кода — [CODE_MAP](CODE_MAP.md).

## Что прочитать сначала

1. [README](../README.md) — состав проекта, запуск, тесты, этап 0.
2. [CODE_MAP](CODE_MAP.md) — где что лежит; раздел CSS 35 — нейтральный стиль.
3. [SERVICE_STAGE1](SERVICE_STAGE1.md) — срез Music Service (этап 1 ТЗ): провайдеры,
   хранилище по хешу, задания и API.
4. [UI_PILOT_RU](UI_PILOT_RU.md) — принятый нейтральный стиль.
5. [validation/windows-stage0-2026-10-27](validation/windows-stage0-2026-10-27.md) и
   [validation/windows-stage0-gui-2026-10-28](validation/windows-stage0-gui-2026-10-28.md) —
   что уже проверено на Windows и что нет.
6. [validation/windows-service-stage1-2026-10-28](validation/windows-service-stage1-2026-10-28.md) —
   живой прогон сервиса: поиск, скачивание с измерением, доставка в outbox.
7. [experiment/RESULTS_SEARCH_03_RU](experiment/RESULTS_SEARCH_03_RU.md) — полный домашний
   metadata-прогон 60 запросов по proxy-маршруту; ручная оценка и VPS ещё открыты.

## Состояние одной строкой

Этап 0 почти закрыт (нет Inno Setup, чистой VM и push), UI-пилот принят и влит в основные CSS,
Music Service (этап 1) прошёл вертикальный срез «поиск → кандидат → скачивание с измерением →
доставка в outbox»; Telegram-бот ещё не начат.
Обязательный эксперимент ТЗ §4: полный домашний metadata-прогон 60 запросов × 5 адаптеров
выполнен по явному proxy-маршруту. yt-dlp прошёл без ошибок, независимый резерв не подтверждён;
ручная оценка, VPS и аудиозагрузки ещё открыты.
Критичные баги скачивания/поиска/скорости yt-dlp исправлены.
`main` опережает устаревший `origin/main` на 18 коммитов; GitHub push заблокирован доступом к
аккаунту (см. «Грабли»). Все коммиты лежат в локальном bare-remote.

## Проверка (всегда выполнять перед коммитом)

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests    # 528 тестов OK, 13 skipped
.\.venv\Scripts\python.exe tools\stage0_audit.py            # аудит секретов, эвристика
git status --short                                          # должно быть пусто
```

Ожидаемый результат на этой машине: `Ran 528 tests`, `OK (skipped=13)`.
Запускать именно через `discover`: тесты импортируют друг друга (`from test_regressions import ...`),
поэтому `python -m unittest tests.test_stage17` может падать на импортах.

## Последние коммиты (что уже сделано)

```
7637133 fix(experiment): force UTF-8 worker output on a cp1251 console
4c16cd3 feat(experiment): allow an explicit proxy route in the source stand
2f76dc9 feat(service): run search, download and delivery jobs
796ddc8 feat(service): store measured assets under a content-addressed root
97ba554 feat(service): add bounded provider worker and yt-dlp adapter
cf3e2bf refactor(ui): merge the neutral pilot into the main stylesheets
784f18a feat(ui): quiet settings and queue panel in the neutral pilot
e240498 feat(ui): give the mini player a neutral palette driven by the accent
c50d7e7 feat(ui): extend the neutral pilot to home and library
6ea1eba perf(online): reuse resolved YouTube streams for 30 minutes
d13f642 feat(downloads): add repair tool for old download metadata
ef92371 fix(search): keep auto search going until enough results are found
e35e791 feat(ui): add neutral-accent pilot for the playing screen and player bar
3cae1b7 fix(downloads): keep measured duration and a durable cover copy
55dde30 feat(service): add Music Service skeleton with persistent jobs
ddf7c62 feat(player): log playback start latency
c030e03 docs(stage0): note INSTALL verified with validated hash-locked sets
074c394 test: require validated hash-locked sets in install and build scripts
5397ddb build: install dependencies only from validated hash-locked sets
```

### По областям

- **Этап 0**: проверенный backup (проект/данные/внешняя музыка, контрольный restore), чистый clone
  с воспроизведением окружения и 433 тестами, hash-локи четырёх групп, `INSTALL.ps1` проверен в
  чистом clone (`--require-hashes`, 5,9 мин), smoke-тест приложения. Windows GUI-приёмка автоматикой:
  звук подтверждён владельцем, плеер/трей/мини-плеер/офлайн-старт проверены.
- **Скачивания**: `publish_download` измеряет длительность (PyAV) и копирует онлайн-обложку в
  `covers/tracks`; `read_track_metadata` подхватывает их. Инструмент
  [`tools/repair_download_metadata.py`](../tools/repair_download_metadata.py) починил старые файлы
  (применён к реальным данным: 3 sidecar, обновлён 1 WebM; рядом лежат `.bak`).
- **Поиск**: авто-поиск больше не останавливается на первом источнике с одним играбельным треком —
  копит до 20 треков или 25 секунд (`SEARCH_MIN_PLAYABLE`/`SEARCH_BUDGET_SECONDS` в
  [`backend/internet_music.py`](../backend/internet_music.py)).
- **Скорость yt-dlp**: кеш разрешённых потоков увеличен с 5 до 30 минут
  (`STREAM_CACHE_SECONDS` в [`backend/catalog_music.py`](../backend/catalog_music.py)).
  Замеры и выводы: [YTDLP_PERFORMANCE_RU](YTDLP_PERFORMANCE_RU.md).
- **UI**: нейтральный акцент по умолчанию `#c9c9d1` + пресет, стили перенесены в
  `styles.css` (раздел 35) и `mini.css`; отдельные `minimal.css`/`mini-minimal.css` удалены.
- **Music Service**: [`service/`](../service) — модели `Recording/AudioCandidate/AudioAsset/Job/Delivery`,
  SQLite WAL со схемой v1, персистентная очередь с повторами и отменой, один диспетчер.
  Добавлены `WorkerProvider` поверх `.providers` (без второго парсера), измерение файла
  (SHA-256, кодек, длительность), хранилище `assets/<ab>/<sha256>.<ext>` с видимыми
  лимитами 200 МиБ/файл и 2 ГиБ, задания `search`/`download`/`deliver`, доставка в `outbox`.
  API: `/health`, `/providers`, `/limits`, `/jobs`, `/recordings/{id}`, `/assets/{id}`,
  `/deliveries`. Запуск: `python -m service --port 8766 --proxy http://127.0.0.1:10809`.
- **Эксперимент источников**: полный домашний metadata-прогон
  `20261028T172158Z-home_owner-proxy-8d1112b2` (60 запросов × 5 адаптеров, 481 из 600 попыток,
  Hitmo остановлен на 403). Протокол v0.3: явный `--proxy`, маршрут в manifest и run id,
  `-X utf8` для Windows. Итоги: [RESULTS_SEARCH_03_RU](experiment/RESULTS_SEARCH_03_RU.md).
  Всего тестов 528.

## Что делать дальше (приоритеты)

### 1. Эксперимент источников — ручная оценка и второй маршрут (главное)

Полный домашний metadata-прогон выполнен по proxy-маршруту; технические данные и ограничения —
в [RESULTS_SEARCH_03_RU](experiment/RESULTS_SEARCH_03_RU.md). Осталось:

1. **Ручная оценка**: владельцу заполнить `reviews.csv` и `availability.csv` в
   `docs/experiment/evidence/home-owner-proxy-2026-10-28/`, затем
   `report.py --network home_owner --route proxy`. Только это даёт доли качества и первый полезный.
2. **Hitmo и Muzbomb**: разобрать 403 и пустую выдачу отдельными прогонами; доступ не обходить.
3. **VPS в РФ**: после выбора сервера повторить набор с `--network vps_ru` по прямому маршруту;
   `compare.py` не смешивает маршруты.
4. **Полное аудио**: resolve → download → ffprobe/хеш → конвертация → доставка на записях
   с подтверждёнными правами (кандидаты Q055–Q057).

### 2. Этап 1 ТЗ — бот и доставка

Срез «поиск → кандидат → скачивание → доставка» готов и проверен живьём; провайдер,
хранилище по хешу и HTTP-контракт уже есть. Дальше:

1. **Правила качества выдачи**: шаг 03 показал версионные конфликты в top-1 (Live Aid для
   студийной цели, каверы, аранжировки), сборники длиннее 15 минут и мусорные совпадения
   каталога на синтетических контролях. Правила версии/сборников и защиту от мусора делать
   по этим данным и ручной оценке, а не по догадке.
2. **Telegram-бот** (`bot/`): aiogram 3, allowlist по числовым `user_id`, `/start /help /search /queue
   /cancel /settings`, выдача 5–10 результатов, кнопки «Получить аудио/Другой источник/Отмена»,
   понятные статусы задания, лимиты параллельных загрузок, кеш и TTL.
3. **Доставка в Telegram**: приёмник поверх `Delivery` (сейчас есть только файловый `outbox`),
   MP3/M4A до 50 МБ, `file_id` как кеш, отдельный Delivery-статус.
4. **Параллельность**: один диспетчер = одна загрузка; ТЗ §6 допускает до двух. Нужен
   ограниченный пул без потери персистентности и отмены.
5. **Зависимости**: отдельная группа `requirements/service.txt` с фиксацией версий и хешей по той же
   схеме, что GUI (`tools/validated_lock.py`), затем lock и импорты в чистом окружении.
6. **Аутентификация**: сервис пока только loopback; перед публикацией нужен токен устройства/бота.

Эксперимент уже дал технические данные, но основной и резервный путь ещё не подтверждены:
нужны ручная оценка, разбор Hitmo/Muzbomb и независимый источник. Бота начинать после
решения по качеству и резерву.

### 3. Остатки этапа 0

- Установщик: Inno Setup не установлен, `dist/installer` не пересобран; нужен `-NativeBundle`,
  `-EmbedZip`, `-EmbedSha256` и `ISCC.exe`.
- Приёмка установки/обновления/удаления с сохранением данных и чистая VM.
- Ручной просмотр секретов и push в приватный GitHub.
- Обновить Playwright-проверки вёрстки (`tools/test_stage*_ui.cjs`): `node_modules` отсутствуют,
  размеры изменились после пилота.

### 4. UI (необязательное продолжение)

- Плавающий плеер и единый нижний лист для EQ/очереди — отдельная задача с изменением раскладки.
- Боковая панель не переработана (нейтральна за счёт акцента).

## Грабли и ограничения (важно)

- **Кодировки**: на этой Windows локаль cp1251. Любое чтение файлов репозитория в тестах — только
  `encoding='utf-8'` или `utf-8-sig`; иначе UnicodeDecodeError/мусор. Уже исправлено, не ломать.
- **Скриншоты WebView2**: снимать `PrintWindow(hwnd, hdc, 2)` после `SetProcessDPIAware()`.
  Запуск с `--remote-debugging-port` ломает захват содержимого (виден только фон); DevTools
  использовать только для DOM-инспекции, не для скриншотов.
- **Клики по UI**: `--force-renderer-accessibility` + UIAutomation (найти по `Name`, клик по центру
  `BoundingRectangle`), либо `SetWindowPos(HWND_TOPMOST)` + клик; иначе окно под другим окном.
- **CSS**: нельзя `backdrop-filter`, `color-mix`, большие тени на плеере — на текущем WebView2
  в прозрачной теме ломают отрисовку (проверено). Раздел 35 в `styles.css` должен оставаться последним.
- **Высота строк библиотеки** не менялась намеренно: JS-виртуализация использует жёсткие `step`
  96/128 px; менять только вместе с JS.
- **Системный прокси** `127.0.0.1:10809`: httpx-скрипты к локальному API запускать с
  `trust_env=False` (или `NO_PROXY=127.0.0.1`), иначе плавающие таймауты. Без прокси yt-dlp/YouTube
  может не отдавать превью; приложение использует свои настройки сети.
- **Music Service**: `python -m service` берёт обработчик из `.providers` (или
  `OWNPLAYER_PROVIDER_PYTHON`); без него задания падают с `worker_unavailable`. Прокси
  передаётся только через `--proxy`, `network.json` плеера сервис не читает. Отчёты с
  кириллицей писать в UTF-8-файл: консоль cp1251 портит вывод. Сам сервис — только loopback.
- **Стенд источников**: маршрут только явный (`-ProxyUrl`/`--proxy`), системный прокси не
  наследуется; обработчик запускается с `-X utf8` (изолированный режим игнорирует `PYTHON*`),
  иначе кириллица в NDJSON ломает протокол. Метка сети не равна маршруту: direct и proxy
  не смешивать. Полные прогоны не запускать повторно подряд без паузы — лимиты источника.
- **Deno** не установлен: yt-dlp предупреждает о неполных форматах. Для полноты поставить в PATH
  (`winget install DenoLand.Deno`), для скорости это не нужно. Портативный Deno для замеров лежал
  во временной папке и может быть удалён.
- **GitHub**: `origin` = `https://hyoreku@github.com/hyoreku/Slynor.git` (публичный, без графа).
  Автор и коммиттер — `hyoreku`: `user.name`/`user.email` заданы локально в этом репозитории
  (noreply-адрес GitHub), глобальная подпись других проектов не меняется. Креды для `hyoreku`
  вводятся один раз при первом push и дальше берутся из GCM.
- **Аудит секретов** — эвристика; перед push всё равно ручной просмотр diff.
- **Музыка, скриншоты, `dist`, окружения и манифесты backup** в Git не добавлять.

## Локальные пути этой машины (для ориентира)

- Проект: `%CD%` = корень с `backend/`, `frontend/`, `service/`.
- Окружение: `.venv` (Python 3.13.15), `.providers`, `.search`, `.buildenv`.
- Данные приложения: `%LOCALAPPDATA%\OwnPlayerMusic` (библиотека, плейлисты, настройки, `Downloads`,
  `covers`). Настройки: акцент `#c9c9d1`, тема «Полупрозрачная».
- Данные сервиса: `%LOCALAPPDATA%\Slynor\service` (`service.sqlite3`, `assets/`, `work/`, `outbox/`);
  для проверок удобнее `--db .local-artifacts\...` — папка в `.gitignore`.
- Стенд источников: `.local-artifacts\source-experiment\` (`envs/`, `runs/`); сырые файлы
  последнего полного прогона также в `docs/experiment/evidence/home-owner-proxy-2026-10-28`.
- Backup этапа 0: `D:\Projects\ownwork\Slynor_Stage0_Backup_20261027-220822` (+ `-restore`).
- Чистый clone: `D:\Projects\ownwork\Slynor_stage0_clean_20261027-220823`.
- Проверка установщика: `D:\Projects\ownwork\Slynor_stage0_install_20261028-171825`.
- Окружения для локов: `D:\Projects\ownwork\Slynor_stage0_envs` (venv + колёса).
- Bare-remote: `D:\Projects\ownwork\Slynor_remote_test.git`.
- Скриншоты приёмки/пилота: `D:\Projects\ownwork\Slynor_Stage0_GUI_20261028` (вне репозитория).

## Готовый промпт для нового чата

> Продолжаем проект Slynor в `D:\Projects\ownwork\OwnPlayerMusic`. Сначала прочитай
> `docs/HANDOFF_NEXT_CHAT.md`, `README.md`, `docs/CODE_MAP.md`, `docs/SERVICE_STAGE1.md` и
> `docs/UI_PILOT_RU.md`. Затем проверь состояние: `git log --oneline -5`, `git status`,
> `python -m unittest discover -s tests` (ожидается 528 OK) и `tools/stage0_audit.py`.
> Продолжаем этап 1 по ТЗ. Сервисный срез «поиск → кандидат → скачивание → доставка» готов,
> полный домашний metadata-прогон источников выполнен по proxy-маршруту
> (`docs/experiment/RESULTS_SEARCH_03_RU.md`); дальше ручная оценка/доступность, разбор
> Hitmo/Muzbomb, VPS и лишь затем Telegram-бот и доставка поверх `Delivery`. Работаем
> локальными коммитами (GitHub push недоступен), перед каждым коммитом — тесты и аудит.
> Не ломай ограничения из раздела «Грабли»: кодировки, CSS-слои, высоты строк библиотеки,
> скриншоты WebView2, прокси, маршрут стенда и loopback-сервис.
