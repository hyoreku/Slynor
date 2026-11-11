# Проверенные наборы зависимостей: Windows x64 / CPython 3.13

Lock-файлы для четырёх групп. Проверены 2026-10-27 на Windows 11 x64 (10.0.22631),
CPython 3.13.15 x64, pip 26.2.1.

| Группа | Пакетов | Проверка |
|---|---:|---|
| `gui-win-py313.txt` | 37 | офлайн hash-установка, `pip check`, импорты, 438 тестов OK |
| `provider-win-py313.txt` | 66 | офлайн hash-установка, `pip check`, импорты spotdl/scdl/yt-dlp/ytmusicapi/soundcloud |
| `search-win-py313.txt` | 39 | офлайн hash-установка, `pip check`, импорты msoc/httpx |
| `build-win-py313.txt` | 44 | офлайн hash-установка, `pip check`, импорт PyInstaller 6.16.0 |

Версии в каждом наборе совпали с `requirements/observed/*-win-py313.constraints.txt`
полностью: недостающих, лишних пакетов и расхождений версий нет.

`INSTALL.ps1` ставит GUI, provider и search только из этих файлов с `--require-hashes`,
`BUILD_WINDOWS.ps1` — сборочное окружение из `build-win-py313.txt`. Если файла нет,
скрипт останавливается.

## Как установить офлайн

```powershell
python -m pip install --require-hashes --no-index --find-links <wheels> -r requirements/validated/gui-win-py313.txt
```

Для `build-win-py313.txt` нужен второй каталог с build-зависимостями (`setuptools`, `wheel`),
потому что `proxy_tools` поставляется только sdist:

```powershell
python -m pip install --require-hashes --no-index --find-links <wheels> --find-links <builddeps> `
    -r requirements/validated/build-win-py313.txt
```

## Проверка после установки

```powershell
python -m pip check
python -m unittest discover -s tests      # только для GUI-набора
```

## Чего это не доказывает

- Наборы привязаны к Windows x64 и CPython 3.13; другие платформы не покрыты.
- Проверены установка, `pip check` и импорты. Живые провайдеры, реальный поиск, сборка
  установщика и звук не запускались.
- Inno Setup на этом ПК отсутствует, `dist/installer` не пересобирался.
- VLC, FFmpeg, Deno и WebView2 — системные компоненты, не поставляются wheel'ами.
  Их версии записаны в provenance, но не хешированы.
- SHA-256 подтверждают целостность локально скачанных артефактов, но не сверены
  с публичными хешами PyPI. Независимая сверка — отдельный шаг.
- `proxy_tools` собирается из sdist: результат зависит от `setuptools` и `wheel`,
  которые не входят в lock.

## Перегенерация

```powershell
python -m pip download -r requirements/<group>.txt -d <wheels>
python tools/validated_lock.py --python <venv-python> --wheels <wheels> `
    --out requirements/validated/<group>-win-py313.txt `
    --provenance requirements/validated/<group>-win-py313.provenance.json `
    --title "Slynor <group>" --field "check=..."
```

`validated-lock.provenance.json`-файлы содержат версии Python/pip, платформу, список
артефактов и их хеши. `tests/test_validated_lock.py` проверяет, что lock и provenance
согласованы, у каждого пакета есть хеш и в файлах нет машинных путей.
