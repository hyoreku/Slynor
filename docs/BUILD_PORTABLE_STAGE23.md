# Сборка Slynor для Windows x64

Workflow [Windows packages](../.github/workflows/windows-package.yml) собирает Setup и portable на Windows x64. После успешного запуска оба файла и их SHA-256 доступны в артефакте `Slynor-Windows-x64`. Сборка в Actions неподписанная. Ручная сборка поддерживает подпись.

Облегчённая сборка использует установленный Microsoft Edge WebView2 Runtime. Для неё передайте `-UseSystemWebView2` скриптам `GET_BUILD_INPUTS.ps1` и `BUILD_WINDOWS.ps1`. Установщик и portable сообщат, если WebView2 нужно установить. Без этого флага скрипты собирают автономный пакет с WebView2 Fixed Runtime. [Загрузки Microsoft WebView2](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download)

## 1. Компьютер сборки

- Windows 10/11 x64, Python **3.13 x64** с pip/venv, Inno Setup 6 (`ISCC.exe` в PATH либо стандартной пользовательской папке).
- Доступ в интернет для получения Python-зависимостей. Это происходит только при сборке.
- Достаточно места для PyInstaller и двух отдельных приватных окружений.
- Для подписания необязательны сертификат Code Signing в хранилище пользователя и Windows SDK Signing Tools. Без подписи возможны предупреждения SmartScreen.

`INSTALL.ps1` можно использовать для разработки, но для нового `BUILD.cmd` он больше не запускается автоматически. Сам сборщик создаёт `.buildenv` и устанавливает туда зависимости из `requirements/validated/build-win-py313.txt`.

## 2. Входные redistributable

Чтобы повторить набор компонентов из Actions, выполните в PowerShell `.\packaging\windows\GET_BUILD_INPUTS.ps1 -Destination 'C:\SlynorDeps'`. Скрипт скачивает архивы закреплённых версий, сверяет SHA-512 для .NET и SHA-256 для остальных компонентов. Он возвращает пути и хеш Python для команды сборки и сохраняет список источников в `native\licenses`.

Получить из официальных источников, проверить происхождение и условия распространения:

- **CPython 3.13 x64 Windows embeddable ZIP**, например с https://www.python.org/downloads/windows/. Именно embeddable, не установщик и не скопированная venv. Передать проверенный SHA-256; скрипт сверит архив с переданным значением. Само значение должно быть получено/проверено независимо, а не считаться гарантией доверия к произвольному ZIP.
- VLC x64 из https://www.videolan.org/vlc/ — полный переносимый комплект, включая библиотеки, плагины, данные и лицензии.
- FFmpeg/ffprobe x64 — согласованный Windows-комплект из источника сборок, указанного на https://ffmpeg.org/download.html. Для shared build нужны и DLL, не только два EXE.
- Deno x64 из https://deno.com/ / официальных выпусков.
- .NET Runtime и .NET Desktop Runtime x64 версии из `packaging/windows/Slynor.runtimeconfig.json`. Распакуйте оба архива в `dotnet` и скопируйте туда файл конфигурации.
- Для автономной сборки подготовьте распакованный **WebView2 Fixed Version Runtime x64**. Файл `msedgewebview2.exe` должен лежать непосредственно в папке `webview2`. [Microsoft WebView2](https://developer.microsoft.com/microsoft-edge/webview2/)
- Тексты лицензий всех этих компонентов и Python-зависимостей, уведомления/другие материалы, необходимые выбранными лицензиями. Наличие папки `licenses` не заменяет юридическую проверку LGPL/GPL и других условий.

Структура подготовленной папки (внутри каждой — **все** необходимые файлы):

```text
C:\SlynorDeps\native\
  vlc\libvlc.dll
      libvlccore.dll
      plugins\...
      ...
  ffmpeg\ffmpeg.exe
         ffprobe.exe
         ...
  deno\deno.exe
        ...
  dotnet\Slynor.runtimeconfig.json
          host\fxr\...
          shared\Microsoft.NETCore.App\...
          shared\Microsoft.WindowsDesktop.App\...
  webview2\msedgewebview2.exe
           ...
  licenses\...
```

Не брать исполняемые файлы из непроверенных перепакованных архивов. Скрипт проверяет структуру, но не подлинность всех нативных компонентов. Зафиксируйте версии и их хеши в журнале выпуска.

## 3. Команда

Распаковать проект в новую папку, например `C:\SlynorBuild`. В PowerShell из корня проекта:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\BUILD_WINDOWS.ps1 `
  -PythonPath 'C:\Path\Python313\python.exe' `
  -NativeBundle 'C:\SlynorDeps\native' `
  -EmbedZip 'C:\SlynorDeps\python-3.13.x-embed-amd64.zip' `
  -EmbedSha256 'ЗАМЕНИТЬ_НА_ПРОВЕРЕННЫЙ_SHA256_64_СИМВОЛА'
```

Заменить `3.13.x`, пути и SHA-256 реальными значениями. Вместо параметров NativeBundle/EmbedZip/EmbedSha256 можно заранее задать `OWNPLAYER_BUILD_NATIVE`, `OWNPLAYER_BUILD_EMBED`, `OWNPLAYER_BUILD_EMBED_SHA256` и запустить `BUILD.cmd`. При запуске из cmd `BUILD.cmd` также передаёт свои аргументы в PowerShell.

Необязательная подпись: добавить `-SignThumbprint '40_HEX_СИМВОЛОВ_ОТПЕЧАТКА'`. Секретный ключ не передаётся в исходниках; сертификат должен быть установлен на компьютере сборки.

## 4. Что делает сборщик

1. Создаёт чистый GUI build-env, собирает по `packaging/windows/Slynor.spec`.
2. Проверяет SHA-256 embeddable ZIP, разворачивает **отдельные приватные runtime** `.providers` и `.search`, устанавливает зависимости в их `Lib/site-packages` только на машине сборки.
3. Устанавливает относительный `python313._pth`; Python не регистрируется в системе. Не копирует venv и абсолютные пути компьютера разработчика.
4. Копирует нативные компоненты и лицензии в папку приложения.
5. Запускает импорты приватных воркеров и проверки готового EXE `--check-runtime` и `--smoke-test` с временной папкой LOCALAPPDATA. Smoke также запускает Music Service внутри сборки на отдельном порту с временной SQLite и проверяет, что очередь готова принимать задания.
6. При необходимости подписывает EXE, проверяет запуск из распакованного portable ZIP в пути с пробелами, затем собирает Inno Setup. Хеши и журналы остаются в `build-logs`, отчёт распакованной версии — в `portable-smoke-check.json`.

Приватные окружения устанавливаются через `--only-binary=:all: --require-hashes` из Windows x64 lock-файлов `provider-win-py313.txt` и `search-win-py313.txt`. Проверка входного архива Python и структуры нативного комплекта выполняется до пересборки.

## 5. Результат успешной сборки

- `dist\Slynor-Portable.zip` — распаковать **всю папку** и запускать `Slynor.exe`.
- `dist\installer\Slynor-Setup.exe` — обычная установка готовых файлов в пользовательскую папку, ярлык и удаление; без pip/winget и системного Python.
- `build-logs\` — отчёты, pip freeze GUI-окружения, журналы и SHA-256 результатов.

Отдельные приватные Python увеличивают размер, зато не смешивают несовместимые зависимости поиска/скачивания с GUI. Вложения должны быть целиком: один `Slynor.exe` без `_internal`, VLC или воркеров не автономен.

## 6. Обязательная приёмка на чистой Windows

На Windows 10 WebView2 Fixed Runtime требует доступа на чтение для контейнеров приложений. Примените команды `icacls` из документации Microsoft к папке `webview2` внутри установленной или распакованной сборки. [Развёртывание WebView2 Fixed Runtime](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution#details-about-the-fixed-version-runtime-distribution-mode)

Использовать новую VM/учётную запись без Python, VLC, FFmpeg, Deno и установленного WebView2. Нельзя считать успешную проверку на машине сборки доказательством автономности.

- Распаковать portable в путь с пробелами и кириллицей. Отключить сеть: проверить старт, локальное MP3/M4A/Opus/WebM, плейлисты, очередь, поиск локальных записей, настройки, мини-плеер и трей.
- Проверить, что приложение не вызывает установщики и не требует прав администратора; перенести папку в другой путь и повторить запуск.
- Подключить сеть: поиск по названию/исполнителю и прямой ссылке; первый и повторный запрос; обработка недоступного источника; загрузка исходного и MP3, повторное использование кеша.
- Во время большой загрузки запустить другой поток. Проверить отмену, остановку, закрытие и отсутствие оставшихся процессов.
- Проверить медиатеку 25 000 записей и очередь >10 000: начало/конец, фильтр, прокрутка, shuffle, следующий/предыдущий.
- Установить Setup в чистой системе; проверить запуск, обновление поверх предыдущей версии и удаление. Сохранить резервную копию данных до проверки.
- Проверить реальный звук, устройства вывода, прогресс, длительность и зависания. GUI/аудио не покрываются HTTP smoke полностью.
- Зафиксировать версии redistributable/зависимостей, хеши и логи. Только после этого помечать выпуск как проверенный portable/установщик.

Пользовательские данные остаются в `%LOCALAPPDATA%\OwnPlayerMusic`; они не входят в portable ZIP. Сеть нужна для онлайн-музыки, но не для установки Python.
