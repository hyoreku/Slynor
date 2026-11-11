# Этап 0: безопасная база Windows — команды для владельца

Источник истины: [ТЗ, разделы 5, 14, 15](../Slynor_TZ_v0.1_RU.md).
Команды в этом документе на вашем Windows-ПК не выполнялись. Пути неизвестны и запрашиваются через Read-Host.
Подготовленный Git bundle уже содержит отдельный baseline этапа 23; инструкции git init ниже предназначены
для вашей исходной папки, если в ней ещё нет истории. Не накладывать два независимых репозитория друг на друга.

## План: 8 шагов

1. Закрыть плеер через выход из трея и завершить его Python-процесс при запуске из исходников.
2. Скопировать и сверить LOCALAPPDATA, исходный проект со всеми окружениями/dist и оригинальный ZIP вне проекта.
3. Проверить .gitignore по разделу 14 ТЗ; не удалять окружения, runtime_paths или сборки.
4. Выполнить последовательность Git из раздела 15 с ручной проверкой кандидатов и diff.
5. Отдельно отметить этап 23 как кандидат исходников, без ярлыка Windows stable.
6. Использовать README/CONTRIBUTING; исторические README_STAGE17…23 находятся в docs/archive.
7. В новом clone проверить группы зависимостей; закрепить только реально прошедший проверку набор.
8. Восстановить проект и контрольную копию данных в отдельные папки; провести приёмку Windows и live-эксперимент.

## 1. Реальные пути и проверенные копии

Запускать PowerShell обычным пользователем. Данные и код во время копирования не изменяются.
Внешние файлы музыки, на которые ссылается library.json, надо резервировать отдельно: их расположение неизвестно.
Если найдены junction/symlink, команды остановятся; нельзя молча считать их внешние цели зарезервированными.
Полные копии могут занимать много места. Убедиться, что места хватает для проекта и данных.

```powershell
$ErrorActionPreference = 'Stop'
$ProjectPath = (Resolve-Path -LiteralPath (Read-Host 'Полный путь к существующей папке исходников')).Path
$BackupRoot = (Resolve-Path -LiteralPath (Read-Host 'Полный путь к существующей папке для резервных копий вне проекта')).Path
$OriginalZip = (Resolve-Path -LiteralPath (Read-Host 'Полный путь к оригинальному OwnPlayerMusic_2.zip')).Path
$UserData = Join-Path $env:LOCALAPPDATA 'OwnPlayerMusic'

if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) { throw 'Нет папки проекта' }
if (-not (Test-Path -LiteralPath $BackupRoot -PathType Container)) { throw 'Нет папки backup' }
if (-not (Test-Path -LiteralPath $OriginalZip -PathType Leaf)) { throw 'Нет оригинального ZIP' }
if (-not (Test-Path -LiteralPath $UserData -PathType Container)) { throw 'Нет ожидаемой папки данных: выяснить фактическое расположение' }
if (Get-Process -Name Slynor,OwnPlayerMusic -ErrorAction SilentlyContinue) { throw 'Плеер ещё запущен' }
# При запуске через python.exe отдельно убедиться, что закрыт именно процесс плеера. Другие Python-процессы не завершать.

function Is-SameOrChild([string]$Child, [string]$Parent) {
    $c = [IO.Path]::GetFullPath($Child).TrimEnd([char[]]@('\','/'))
    $p = [IO.Path]::GetFullPath($Parent).TrimEnd([char[]]@('\','/'))
    return $c.Equals($p, [StringComparison]::OrdinalIgnoreCase) -or
        $c.StartsWith($p + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}
if ((Is-SameOrChild $BackupRoot $ProjectPath) -or (Is-SameOrChild $BackupRoot $UserData)) {
    throw 'Резервная папка должна быть вне проекта и данных'
}
$BackupDir = Join-Path $BackupRoot ('Slynor-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $BackupDir | Out-Null

function Get-TreeManifest([string]$Root) {
    $base = (Resolve-Path -LiteralPath $Root).Path.TrimEnd([char[]]@('\','/'))
    if ((Get-Item -LiteralPath $Root).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Корень является reparse point' }
    $items = @(Get-ChildItem -LiteralPath $Root -Force -Recurse)
    if (@($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) {
        throw 'Есть reparse points: требуется отдельный план копирования их целей'
    }
    $items | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName | ForEach-Object {
        [pscustomobject]@{
            RelativePath = $_.FullName.Substring($base.Length).TrimStart([char[]]@('\','/'))
            Length = $_.Length
            SHA256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    }
}
function Copy-VerifiedTree([string]$Source, [string]$Destination, [string]$ManifestName) {
    # Hash source BEFORE copying so concurrent changes also prevent acceptance.
    $before = @(Get-TreeManifest $Source)
    & robocopy $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { throw 'Robocopy сообщил ошибку; копия не принята' }
    $after = @(Get-TreeManifest $Source)
    $copy = @(Get-TreeManifest $Destination)
    if ($before.Count -ne $after.Count -or $before.Count -ne $copy.Count) { throw 'Число файлов различается' }
    if ($before.Count -gt 0) {
        if (@(Compare-Object $before $after -Property RelativePath,Length,SHA256).Count -or
            @(Compare-Object $before $copy -Property RelativePath,Length,SHA256).Count) { throw 'Содержимое изменилось или копия не совпадает' }
    }
    $before | Export-Csv -LiteralPath (Join-Path $BackupDir $ManifestName) -NoTypeInformation -Encoding UTF8
}

# Шаг 1 ТЗ: данные сначала. Не запускать плеер до окончания всех проверок.
Copy-VerifiedTree $UserData (Join-Path $BackupDir 'UserData') 'userdata-manifest.csv'
# Шаг 2: проект со всей имеющейся dist, .venv, .providers, .search и runtime_paths.json.
Copy-VerifiedTree $ProjectPath (Join-Path $BackupDir 'Project') 'project-manifest.csv'
$ZipBackup = Join-Path $BackupDir (Split-Path -Leaf $OriginalZip)
$zipHashBefore = (Get-FileHash -LiteralPath $OriginalZip -Algorithm SHA256).Hash
Copy-Item -LiteralPath $OriginalZip -Destination $ZipBackup
if ((Get-FileHash -LiteralPath $OriginalZip -Algorithm SHA256).Hash -ne $zipHashBefore -or
    (Get-FileHash -LiteralPath $ZipBackup -Algorithm SHA256).Hash -ne $zipHashBefore) { throw 'ZIP-копия не совпадает' }
[pscustomobject]@{ File = (Split-Path -Leaf $OriginalZip); SHA256 = $zipHashBefore } |
    Export-Csv -LiteralPath (Join-Path $BackupDir 'zip-manifest.csv') -NoTypeInformation -Encoding UTF8
Write-Host ('Проверенные копии: ' + $BackupDir)
```

Команды создают новые каталоги; здесь нет удаления, зеркалирования с удалением или перезаписи рабочей библиотеки.
Сохранить резервную папку с ограниченным доступом: она может содержать секреты настроек. Манифесты/полные копии не коммитить.
Сверка файлов не является проверкой EXE на Windows и не подтверждает резервирование внешних путей музыки.

## 2. Проверить ignore и выполнить Git в точном порядке

Приложенный [.gitignore](../.gitignore) повторяет раздел 14; дополнительно указан фактический `OwnPlayerMusic_2.zip`.
Не игнорируются все JSON, SQL, SQLite или изображения. Убрано старое слишком широкое `*_backup*`.
Перед add убедиться, что Downloads/музыка вообще не находятся среди исходников; если лежат там, сначала
определить их точные каталоги и добавить их явно. Не угадывать имя папки и не удалять музыку.

```powershell
Set-Location -LiteralPath $ProjectPath
Get-Content -LiteralPath .gitignore
# До следующего блока сверить содержимое с разделом 14 ТЗ.

git init
if ($LASTEXITCODE -ne 0) { throw 'git init failed' }
git status --short --untracked-files=all
git add --dry-run .
# РУЧНАЯ ПРОВЕРКА: нет токенов, cookies, музыки, venv, build/dist, runtime_paths и личных путей.
# Здесь остановиться, если любой кандидат сомнителен. В stage-0 копии также запустить аудитор.
```

Только после проверки списка выполнить второй блок; PowerShell не делает эту проверку за владельца.

```powershell
git add .
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git diff --cached --stat
git diff --cached
# РУЧНАЯ ПРОВЕРКА DIFF. Не сохранять секреты из diff в чат/отчёт.
# Если всё проверено, ниже commit; при сомнении остановиться и исправить staging.
git commit -m "chore: baseline before telegram bot"
if ($LASTEXITCODE -ne 0) { throw 'commit failed; проверить также локальные user.name/user.email' }
git branch -M main
if ($LASTEXITCODE -ne 0) { throw 'branch rename failed' }
git status --short --untracked-files=all
```

| Команда | Зачем / что проверить |
|---|---|
| git init | создать локальную историю без сети и GitHub |
| git status --short --untracked-files=all | увидеть каждый ещё не отслеживаемый файл |
| git add --dry-run . | показать точный список будущего staging с учётом ignore |
| ручной просмотр | исключить секреты, runtime, музыку и личные конфиги; расширение JSON само по себе не ошибка |
| git add . | добавить только уже проверенный набор в staging |
| git diff --cached --stat | проверить масштаб, неожиданные большие/бинарные файлы |
| git diff --cached | просмотреть содержимое, секреты и реальные изменения |
| git commit | сохранить проверенный исходный снимок; это не приёмка Windows-релиза |
| git branch -M main | задать имя основной ветки после коммита |

Git user.name/user.email при необходимости задаются владельцем локально реальными выбранными им значениями;
глобальные настройки и GitHub remote этим документом не меняются. Команд push здесь нет.

## 3. Отдельный baseline этапа 23

Если есть подтверждённое более раннее состояние, сначала коммитить именно его, затем импортировать этап 23
в отдельной ветке и отдельным коммитом `chore: import stage 23 source candidate (Windows unverified)`.
Не заявлять, что найденный вложенный ZIP этапа 22 уже подтверждён на Windows.

В подготовленном bundle нет выдуманного «рабочего раннего релиза»: первый коммит фиксирует полученные исходники
этапа 23 с исправленным ignore, второй — подготовку этапа 0 в `chore/stage0`.
Для восстановления этой истории в НОВЫЙ несуществующий путь:

```powershell
$Bundle = (Resolve-Path -LiteralPath (Read-Host 'Полный путь к slynor-stage0.bundle из комплекта')).Path
$NewClone = Read-Host 'Новый полный путь для clone (ещё не существует)'
if (Test-Path -LiteralPath $NewClone) { throw 'Нужен новый путь; существующее не перезаписываем' }
git clone --branch chore/stage0 $Bundle $NewClone
if ($LASTEXITCODE -ne 0) { throw 'clone failed' }
Set-Location -LiteralPath $NewClone
git status --short --untracked-files=all
py -3.13 tools/stage0_audit.py
if ($LASTEXITCODE -ne 0) { throw 'Аудит требует разбора' }
```

`origin` такого clone указывает на локальный bundle, не GitHub. Не выполнять в нём git init и не переносить туда старую .git.

## 4. Чистое окружение — только новый clone

Следующий блок — эксперимент восстановления наблюдавшихся constraints, не готовая подтверждённая установка.
Python 3.13 x64 с pip, Git и нативные prerequisites должны быть получены из проверенных источников владельцем.
Не выполнять INSTALL.ps1/UPDATE_NETWORK.cmd для доказательства фиксации версий.

```powershell
py -3.13 -c "import struct,sys; assert struct.calcsize('P') == 8 and sys.version_info[:2] == (3,13); print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw 'Нужен Python 3.13 x64' }
foreach ($name in @('.venv','.providers','.search','.buildenv')) {
    if (Test-Path -LiteralPath $name) { throw ('Окружение уже существует: ' + $name + '; использовать новый clone') }
}
py -3.13 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'GUI venv failed' }
.\.venv\Scripts\python.exe -m pip install -r requirements/gui.txt
if ($LASTEXITCODE -ne 0) { throw 'GUI install failed; не обновлять версии наугад' }
.\.venv\Scripts\python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'GUI pip check failed' }

py -3.13 -m venv .providers
if ($LASTEXITCODE -ne 0) { throw 'provider venv failed' }
.\.providers\Scripts\python.exe -m pip install -r requirements/provider.txt
if ($LASTEXITCODE -ne 0) { throw 'provider install failed' }
.\.providers\Scripts\python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'provider pip check failed' }

py -3.13 -m venv .search
if ($LASTEXITCODE -ne 0) { throw 'search venv failed' }
.\.search\Scripts\python.exe -m pip install -r requirements/search.txt
if ($LASTEXITCODE -ne 0) { throw 'search install failed' }
.\.search\Scripts\python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'search pip check failed' }

.\.venv\Scripts\python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Тесты не пройдены' }
# Импорты GUI/LibVLC и приёмка выполняются при закрытом плеере отдельно, не через тестовые двойники.
.\.providers\Scripts\python.exe -c "import spotdl,scdl,yt_dlp; print('Provider imports OK')"
if ($LASTEXITCODE -ne 0) { throw 'Provider imports failed' }
.\.search\Scripts\python.exe -c "import msoc,httpx; print('Search imports OK')"
if ($LASTEXITCODE -ne 0) { throw 'Search imports failed' }
git status --short --untracked-files=all
```

Сборочная группа восстанавливается отдельно через requirements/build.txt и `.buildenv`, но сейчас сборку не запускать.
Service/bot — [план](../requirements/service-plan.md): проверенного lock пока нет, это открытый критерий этапа.
Снимок pip freeze после успеха не заменяет wheel-хеши и проверку нативных runtime. Переход INSTALL/BUILD на validated-набор — отдельный обязательный шаг.

## 5. Контрольное восстановление данных

Не копировать данные поверх действующей LOCALAPPDATA. В той же PowerShell-сессии после успешного backup
использовать функции выше; новый каталог создаётся без перезаписи существующего.

```powershell
$RestoreParent = (Resolve-Path -LiteralPath (Read-Host 'Существующая папка вне проекта и данных для контрольного restore')).Path
if ((Is-SameOrChild $RestoreParent $ProjectPath) -or (Is-SameOrChild $RestoreParent $UserData) -or
    (Is-SameOrChild $RestoreParent (Join-Path $BackupDir 'UserData'))) { throw 'Контрольный restore должен быть вне исходных деревьев' }
$RestoreTest = Join-Path $RestoreParent ('Slynor-restore-test-' + [Guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $RestoreTest) { throw 'Контрольная папка уже существует' }
Copy-VerifiedTree (Join-Path $BackupDir 'UserData') $RestoreTest 'restore-manifest.csv'
$expected = @(Import-Csv -LiteralPath (Join-Path $BackupDir 'userdata-manifest.csv') | ForEach-Object {
    [pscustomobject]@{ RelativePath = $_.RelativePath; Length = [long]$_.Length; SHA256 = $_.SHA256 }
})
$restored = @(Get-TreeManifest $RestoreTest)
if ($expected.Count -ne $restored.Count) { throw 'Restore: число файлов различается' }
if ($expected.Count -gt 0 -and @(Compare-Object $expected $restored -Property RelativePath,Length,SHA256).Count) {
    throw 'Restore: содержимое не совпадает с исходным манифестом'
}
Write-Host ('Контрольная копия проверена: ' + $RestoreTest)
```

Плеер пока не запускать. После отдельного решения владельца приложение можно проверить с изолированным
LOCALAPPDATA в новом процессе; это всё ещё не подтверждает внешние файлы библиотеки.

Понижение версии приложения может быть несовместимо с форматом данных; сначала копия и отдельная тестовая папка.
Для отката законченного изменения кода: git revert в чистом дереве. Для просмотра старого кода — отдельный worktree.
Git не резервирует скачанную музыку и не заменяет контрольный restore.

## 6. Чек-лист приёмки

- [ ] Плеер закрыт; все процессы записи остановлены.
- [ ] Проверены копии данных, проекта, оригинального ZIP, единственной рабочей dist и внешней музыки.
- [ ] Проверен контрольный restore в новый каталог; данные и плейлисты сохранены.
- [x] Подготовлен ignore по разделу 14 без глобального игнорирования JSON/SQL/изображений.
- [x] В копии создан отдельный Git baseline источников этапа 23; stable-тега и remote нет.
- [x] README_STAGE17…23 перенесены в docs/archive в копии; исправлены действующие ссылки.
- [x] Сохранены код, assets, тесты, инструменты и исходный SOURCE_SHA256.json.
- [x] Подготовлены раздельные наблюдавшиеся constraints; они явно не названы проверенным lock.
- [ ] Новый Windows clone воспроизводит окружение без файлов личного ПК; полный unittest и импорты прошли.
- [ ] Зафиксированы проверенные wheel-хеши/нативные версии и актуализированы INSTALL/BUILD без бесконтрольных обновлений.
- [ ] Полный просмотр секретов перед push выполнен; Git не содержит музыки/credentials/machine paths.
- [ ] Этап 0 принят владельцем. До этого продуктовый код бота не начинается.

Никаких автоматических удалений .venv/.providers/.search/runtime_paths/dist и никакого push эти инструкции не выполняют.
PowerShell-синтаксис и исполнение именно этих новых блоков на Windows — **не проверено**.
