import json
from pathlib import Path
import ntpath
import re
from threading import RLock
from uuid import uuid4

import webview

from backend.image_assets import discard_cover, get_data_dir, import_cover, is_cover_name


from backend.wallpaper_assets import import_wallpaper, discard_wallpaper, is_cover_name as is_wallpaper_name


class DesktopApi:
    # Создание объектов и приватного состояния; вызывается один раз при запуске.
    def __init__(self):
        self._window = None
        self._storage_lock = RLock()

        self._data_dir = get_data_dir()
        self._library_path = self._data_dir / "library.json"
        self._playlists_path = self._data_dir / "playlists.json"
        self._settings_path = self._data_dir / "settings.json"

    # Диалог Windows для выбора одного или нескольких музыкальных файлов.
    def _choose_files(self, allow_multiple: bool):
        if self._window is None:
            raise RuntimeError("Окно приложения ещё не создано.")

        english = self._read_settings().get("language") == "en"
        selected_files = self._window.create_file_dialog(
            dialog_type=webview.FileDialog.OPEN,
            allow_multiple=allow_multiple,
            file_types=(
                ("Audio files (*.mp3;*.flac;*.wav;*.ogg;*.m4a;*.aac;*.opus;*.webm)" if english else "Аудиофайлы (*.mp3;*.flac;*.wav;*.ogg;*.m4a;*.aac;*.opus;*.webm)"),
                ("All files (*.*)" if english else "Все файлы (*.*)"),
            ),
        )

        return list(selected_files) if selected_files else []

    # Выбор одного файла для ручного запуска.
    def choose_audio_file(self):
        files = self._choose_files(allow_multiple=False)
        return files[0] if files else None

    # Выбор нескольких файлов для добавления в библиотеку.
    def choose_audio_files(self):
        return self._choose_files(allow_multiple=True)

    # Общее хранилище сервиса, если бэкенд уже открыл его в этом процессе.
    def _shared_library(self):
        cached = getattr(self, "_library_service_cache", None)
        if cached is not None:
            return cached
        try:
            from backend.main import app
            service = getattr(app.state, "library_service", None)
        except Exception:
            service = None
        if service is not None:
            self._library_service_cache = service
        return service

    # Чтение общей библиотеки: сервис, затем локальный JSON-кеш.
    def load_library(self):
        shared = self._shared_library()
        if shared is not None:
            return shared.load_library()
        with self._storage_lock:
            if not self._library_path.exists():
                return []

            try:
                data = json.loads(
                    self._library_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    "Не удалось прочитать library.json. "
                    "Файл не был изменён."
                ) from error

            if not isinstance(data, dict) or data.get("version") != 1:
                raise RuntimeError("Неизвестный формат library.json.")

            files = data.get("files")

            if not isinstance(files, list) or not all(
                isinstance(path, str) and path.strip()
                for path in files
            ):
                raise RuntimeError(
                    "Некорректный список файлов в library.json.")

            return files

    # Сохранение библиотеки: сервис, затем локальный JSON-кеш.
    def save_library(self, files):
        if not isinstance(files, list) or not all(
            isinstance(path, str) and path.strip()
            for path in files
        ):
            raise ValueError("Ожидался список непустых путей к файлам.")
        shared = self._shared_library()
        if shared is not None:
            return shared.save_library(files)

        data = {
            "version": 1,
            "files": files,
        }

        with self._storage_lock:
            self._data_dir.mkdir(parents=True, exist_ok=True)

            temporary_path = self._data_dir / "library.json.tmp"

            try:
                temporary_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                temporary_path.replace(self._library_path)
            except OSError as error:
                raise RuntimeError(
                    "Не удалось сохранить библиотеку. "
                    "Проверь доступ к папке данных приложения."
                ) from error

        return True


    # Проверка названия плейлиста перед сохранением.
    def _playlist_name(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Введите название плейлиста.")
        name = name.strip()
        if len(name) > 100:
            raise ValueError("Название должно быть не длиннее 100 символов.")
        return name

    # Чтение и проверка плейлистов; старые записи без обложек остаются допустимыми.
    def _read_playlists(self):
        if not self._playlists_path.exists():
            return []
        try:
            data = json.loads(self._playlists_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Не удалось прочитать playlists.json. Файл не был изменён."
            ) from error
        if not isinstance(data, dict) or data.get("version") != 1:
            raise RuntimeError("Неизвестный формат playlists.json.")
        playlists = data.get("playlists")
        if not isinstance(playlists, list):
            raise RuntimeError("Некорректный список плейлистов.")
        seen_ids = set()
        for item in playlists:
            if not isinstance(item, dict):
                raise RuntimeError("Некорректная запись плейлиста.")
            identifier = item.get("id")
            name = item.get("name")
            if (
                not isinstance(identifier, str) or not identifier
                or identifier in seen_ids
                or not isinstance(name, str) or not name.strip()
            ):
                raise RuntimeError("Некорректный идентификатор или название плейлиста.")
            tracks = item.get("tracks", [])
            if not isinstance(tracks, list) or not all(
                isinstance(path, str) and path.strip() for path in tracks
            ):
                raise RuntimeError("Некорректный список треков плейлиста.")
            cover = item.get("cover")
            if cover is not None and not is_cover_name(cover):
                raise RuntimeError("Некорректная ссылка на обложку плейлиста.")
            item["tracks"] = tracks
            seen_ids.add(identifier)
        return playlists

    # Запись плейлистов: сначала временный JSON, затем замена основного файла.
    def _write_playlists(self, playlists):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = self._data_dir / "playlists.json.tmp"
        data = {"version": 1, "playlists": playlists}
        try:
            temporary_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary_path.replace(self._playlists_path)
        except OSError as error:
            raise RuntimeError("Не удалось сохранить плейлисты.") from error

    def load_playlists(self):
        shared = self._shared_library()
        if shared is not None:
            return shared.load_playlists()
        with self._storage_lock:
            return self._read_playlists()

    # Создание пустого плейлиста с уникальным идентификатором.
    def create_playlist(self, name):
        name = self._playlist_name(name)
        shared = self._shared_library()
        if shared is not None:
            return shared.create_playlist(name)
        with self._storage_lock:
            playlists = self._read_playlists()
            if any(item["name"].casefold() == name.casefold() for item in playlists):
                raise ValueError("Плейлист с таким названием уже существует.")
            playlists.append({"id": str(uuid4()), "name": name, "tracks": []})
            self._write_playlists(playlists)
            return playlists

    # Переименование без потери треков и пользовательской обложки.
    def rename_playlist(self, playlist_id, name):
        name = self._playlist_name(name)
        shared = self._shared_library()
        if shared is not None:
            return shared.rename_playlist(playlist_id, name)
        with self._storage_lock:
            playlists = self._read_playlists()
            target = next((p for p in playlists if p["id"] == playlist_id), None)
            if target is None:
                raise ValueError("Плейлист не найден.")
            if any(
                p["id"] != playlist_id and p["name"].casefold() == name.casefold()
                for p in playlists
            ):
                raise ValueError("Плейлист с таким названием уже существует.")
            target["name"] = name
            self._write_playlists(playlists)
            return playlists

    # Удаление плейлиста и ненужной копии его обложки. Музыку не удаляем.
    def delete_playlist(self, playlist_id):
        shared = self._shared_library()
        if shared is not None:
            playlists = shared.load_playlists()
            deleted = next((p for p in playlists if p["id"] == playlist_id), None)
            if deleted is None:
                raise ValueError("Плейлист не найден.")
            remaining = shared.delete_playlist(playlist_id)
            old_cover = deleted.get("cover")
            if not any(p.get("cover") == old_cover for p in remaining):
                discard_cover(old_cover)
            return remaining
        with self._storage_lock:
            playlists = self._read_playlists()
            remaining = [p for p in playlists if p["id"] != playlist_id]
            if len(remaining) == len(playlists):
                raise ValueError("Плейлист не найден.")
            self._write_playlists(remaining)
            deleted = next(p for p in playlists if p["id"] == playlist_id)
            old_cover = deleted.get("cover")
            if not any(p.get("cover") == old_cover for p in remaining):
                discard_cover(old_cover)
            return remaining

    # Сравнение путей Windows без учёта регистра и типа слешей.
    def _track_key(self, path):
        return ntpath.normcase(ntpath.normpath(path.strip().strip('"')))

    # Добавление ссылки на песню в плейлист без дублей.
    def add_track_to_playlist(self, playlist_id, path):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Некорректный путь к треку.")
        path = path.strip().strip('"')
        if not path:
            raise ValueError("Некорректный путь к треку.")
        shared = self._shared_library()
        if shared is not None:
            return shared.add_track_to_playlist(playlist_id, path)
        with self._storage_lock:
            playlists = self._read_playlists()
            target = next((p for p in playlists if p["id"] == playlist_id), None)
            if target is None:
                raise ValueError("Плейлист не найден.")
            key = self._track_key(path)
            if not any(self._track_key(p) == key for p in target["tracks"]):
                target["tracks"].append(path)
                self._write_playlists(playlists)
            return playlists

    # Добавление ссылки сразу в несколько плейлистов одной записью.
    def add_track_to_playlists(self, playlist_ids, path):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Некорректный путь к треку.")
        if not isinstance(playlist_ids, (list, tuple)) or not playlist_ids:
            raise ValueError("Выберите хотя бы один плейлист.")
        if len(playlist_ids) > 100 or any(
            not isinstance(identifier, str) or not identifier for identifier in playlist_ids
        ):
            raise ValueError("Некорректный список плейлистов.")
        path = path.strip().strip('"')
        if not path:
            raise ValueError("Некорректный путь к треку.")
        shared = self._shared_library()
        if shared is not None:
            return shared.add_track_to_playlists(list(playlist_ids), path)
        with self._storage_lock:
            playlists = self._read_playlists()
            by_id = {item["id"]: item for item in playlists}
            if any(identifier not in by_id for identifier in playlist_ids):
                raise ValueError("Плейлист не найден.")
            key = self._track_key(path)
            changed = False
            for identifier in dict.fromkeys(playlist_ids):
                target = by_id[identifier]
                if not any(self._track_key(item) == key for item in target["tracks"]):
                    target["tracks"].append(path)
                    changed = True
            if changed:
                self._write_playlists(playlists)
            return playlists

    # Удаление ссылки из одного плейлиста, не самого файла.
    def remove_track_from_playlist(self, playlist_id, path):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Некорректный путь к треку.")
        shared = self._shared_library()
        if shared is not None:
            return shared.remove_track_from_playlist(playlist_id, path)
        with self._storage_lock:
            playlists = self._read_playlists()
            target = next((p for p in playlists if p["id"] == playlist_id), None)
            if target is None:
                raise ValueError("Плейлист не найден.")
            key = self._track_key(path)
            target["tracks"] = [
                p for p in target["tracks"] if self._track_key(p) != key
            ]
            self._write_playlists(playlists)
            return playlists

    # Диалог изображения и безопасное сохранение копии для плейлиста.
    def choose_playlist_cover(self, playlist_id):
        shared = self._shared_library()
        if shared is not None:
            playlists = shared.load_playlists()
            target = next((p for p in playlists if p["id"] == playlist_id), None)
            if target is None:
                raise ValueError("Плейлист не найден.")
            if self._window is None:
                raise RuntimeError("Окно приложения ещё не создано.")
            chosen = self._window.create_file_dialog(
                dialog_type=webview.FileDialog.OPEN, allow_multiple=False,
                file_types=(("Images (*.jpg;*.jpeg;*.png;*.webp)" if self._read_settings().get("language") == "en" else "Изображения (*.jpg;*.jpeg;*.png;*.webp)"),),
            )
            if not chosen:
                return {"changed": False, "playlists": shared.load_playlists()}
            filename = import_cover(chosen[0])
            old_cover = target.get("cover")
            try:
                playlists = shared.set_playlist_cover(playlist_id, filename)
            except Exception:
                discard_cover(filename)
                raise
            if not any(p.get("cover") == old_cover for p in playlists):
                discard_cover(old_cover)
            return {"changed": True, "playlists": playlists}
        # Диалог не держит блокировку: пользователь может выбирать долго.
        with self._storage_lock:
            if not any(p["id"] == playlist_id for p in self._read_playlists()):
                raise ValueError("Плейлист не найден.")
        if self._window is None:
            raise RuntimeError("Окно приложения ещё не создано.")
        chosen = self._window.create_file_dialog(
            dialog_type=webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=(("Images (*.jpg;*.jpeg;*.png;*.webp)" if self._read_settings().get("language") == "en" else "Изображения (*.jpg;*.jpeg;*.png;*.webp)"),),
        )
        if not chosen:
            return {"changed": False, "playlists": self.load_playlists()}
        filename = import_cover(chosen[0])
        try:
            with self._storage_lock:
                playlists = self._read_playlists()
                target = next((p for p in playlists if p["id"] == playlist_id), None)
                if target is None:
                    raise ValueError("Плейлист не найден.")
                old_cover = target.get("cover")
                target["cover"] = filename
                self._write_playlists(playlists)
                if not any(p.get("cover") == old_cover for p in playlists):
                    discard_cover(old_cover)
                return {"changed": True, "playlists": playlists}
        except Exception:
            # При ошибке JSON новую копию не оставляем; старая ещё на месте.
            discard_cover(filename)
            raise

    # Сброс пользовательской обложки и очистка её ненужной копии.
    def reset_playlist_cover(self, playlist_id):
        shared = self._shared_library()
        if shared is not None:
            playlists = shared.load_playlists()
            target = next((p for p in playlists if p["id"] == playlist_id), None)
            if target is None:
                raise ValueError("Плейлист не найден.")
            old_cover = target.get("cover")
            playlists = shared.set_playlist_cover(playlist_id, None)
            if not any(p.get("cover") == old_cover for p in playlists):
                discard_cover(old_cover)
            return playlists
        with self._storage_lock:
            playlists = self._read_playlists()
            target = next((p for p in playlists if p["id"] == playlist_id), None)
            if target is None:
                raise ValueError("Плейлист не найден.")
            old_cover = target.pop("cover", None)
            self._write_playlists(playlists)
            if not any(p.get("cover") == old_cover for p in playlists):
                discard_cover(old_cover)
            return playlists

    # Настройки отдельны от музыкальной библиотеки и плейлистов.
    def _read_settings(self):
        if not self._settings_path.exists():
            return {"version": 1, "accent": "#c9c9d1", "playlist_shape": "square",
                    "wallpaper": None, "wallpaper_dim": 65, "wallpaper_blur": 0,
                    "cover_background": True, "spectrum_enabled": False, "theme": "dark", "language": "ru", "developer_mode": False, "sidebar_auto_collapse": True, "close_to_tray": True, "mini_background":"classic", "audio_output":"", "playing_screen_enabled":True}
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Не удалось прочитать settings.json. Файл не был изменён.") from error
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise RuntimeError("Неизвестный формат settings.json. Файл не был изменён.")
        if not isinstance(data.get("accent"), str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", data["accent"]):
            raise RuntimeError("Некорректный цвет в settings.json. Файл не был изменён.")
        data["accent"] = data["accent"].lower()
        # Настройки предыдущей версии автоматически получают новые значения по умолчанию.
        data.setdefault("language", "ru")
        data.setdefault("developer_mode", False)
        if data["language"] not in ("ru", "en") or type(data["developer_mode"]) is not bool:
            raise RuntimeError("Некорректные настройки языка или режима разработчика.")
        position=data.get('mini_position')
        if position is not None and (not isinstance(position,list) or len(position)!=2 or any(type(n) is not int or abs(n)>100000 for n in position)):
            raise RuntimeError('Некорректная позиция миниплеера.')
        data.setdefault("close_to_tray", True)
        if type(data["close_to_tray"]) is not bool:
            raise RuntimeError("Некорректная настройка трея.")
        data.setdefault("playing_screen_enabled", True)
        if type(data["playing_screen_enabled"]) is not bool:
            raise RuntimeError("Некорректная настройка экрана песни.")
        data.setdefault("sidebar_auto_collapse", True)
        if type(data["sidebar_auto_collapse"]) is not bool:
            raise RuntimeError("Некорректная настройка боковой панели.")
        data.setdefault('app_icon','1')
        if data['app_icon'] not in tuple(str(i) for i in range(1,9)):raise RuntimeError('Некорректный значок приложения.')
        data.setdefault('mini_background','classic')
        if data['mini_background'] not in ('classic','wallpaper','colors'):raise RuntimeError('Некорректный фон миниплеера.')
        data.setdefault('audio_output','')
        if not isinstance(data['audio_output'],str) or len(data['audio_output'])>2048:raise RuntimeError('Некорректный аудиовыход.')
        data.setdefault("theme", "dark")
        if data["theme"] not in ("dark", "light", "transparent"):
            raise RuntimeError("Некорректная тема в settings.json.")
        data.setdefault("cover_background", True)
        data.setdefault("spectrum_enabled", False)
        for key in ("cover_background", "spectrum_enabled"):
            if type(data[key]) is not bool:
                raise RuntimeError("Некорректный переключатель эффектов в settings.json.")
        data.setdefault("playlist_shape", "square")
        data.setdefault("wallpaper", None)
        data.setdefault("wallpaper_dim", 65)
        data.setdefault("wallpaper_blur", 0)
        if data["playlist_shape"] not in ("square", "landscape"):
            raise RuntimeError("Некорректная форма карточек в settings.json.")
        if data["wallpaper"] is not None and not is_wallpaper_name(data["wallpaper"]):
            raise RuntimeError("Некорректное имя обоев в settings.json.")
        for key, maximum in (("wallpaper_dim", 90), ("wallpaper_blur", 30)):
            if type(data[key]) is not int or not 0 <= data[key] <= maximum:
                raise RuntimeError("Некорректные параметры обоев в settings.json.")
        return data

    # При запуске интерфейс читает сохранённый цвет через Python-мост.
    def load_settings(self):
        with self._storage_lock:
            return self._read_settings()

    # Разрешаем только #RRGGBB, а не CSS-код. Запись атомарна.
    def save_settings(self, accent):
        if not isinstance(accent, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
            raise ValueError("Ожидался цвет в формате #RRGGBB.")
        with self._storage_lock:
            data = self._read_settings()
            data["accent"] = accent.lower()
            return self._write_settings(data)

    # Атомарная запись, только под _storage_lock. Библиотека и плейлисты не затрагиваются.
    def _write_settings(self, data):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._settings_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self._settings_path)
        except OSError as error:
            raise RuntimeError("Не удалось сохранить настройки. Предыдущие настройки не изменены.") from error
        return data

    # Тема отдельна от акцента и обоев; старые файлы получают dark по умолчанию.
    def save_theme(self, theme):
        if theme not in ("dark", "light", "transparent"):raise ValueError("Неизвестная тема.")
        with self._storage_lock:
            data = self._read_settings()
            data["theme"] = theme
            return self._write_settings(data)

    def save_desktop_preferences(self, close_to_tray):
        if type(close_to_tray) is not bool:
            raise ValueError("Некорректная настройка трея.")
        with self._storage_lock:
            data = self._read_settings()
            data["close_to_tray"] = close_to_tray
            result=self._write_settings(data)
            from backend.desktop_controls import get_controller
            shell=get_controller()
            if shell is not None:shell.close_to_tray=close_to_tray
            return result

    def save_mini_position(self, x, y):
        if type(x) is not int or type(y) is not int or abs(x)>100000 or abs(y)>100000:
            raise ValueError('Некорректная позиция миниплеера.')
        with self._storage_lock:
            data=self._read_settings();data['mini_position']=[x,y];return self._write_settings(data)

    def save_sidebar_preferences(self, auto_collapse):
        if type(auto_collapse) is not bool:
            raise ValueError("Некорректная настройка боковой панели.")
        with self._storage_lock:
            data = self._read_settings()
            data["sidebar_auto_collapse"] = auto_collapse
            return self._write_settings(data)

    def save_preferences(self, language, developer_mode):
        if language not in ("ru", "en") or type(developer_mode) is not bool:
            raise ValueError("Некорректные настройки языка или режима разработчика.")
        with self._storage_lock:
            data = self._read_settings()
            data["language"] = language; data["developer_mode"] = developer_mode
            return self._write_settings(data)

    # Сохранение двух независимых переключателей без изменения прочих настроек.
    def save_visual_effects(self, cover_background, spectrum_enabled):
        if type(cover_background) is not bool or type(spectrum_enabled) is not bool:
            raise ValueError("Переключатели эффектов должны быть True или False.")
        with self._storage_lock:
            data = self._read_settings()
            data["cover_background"] = cover_background
            data["spectrum_enabled"] = spectrum_enabled
            return self._write_settings(data)

    # Меняется только форма; неизвестные будущие поля настроек сохраняются.
    def save_playlist_shape(self, shape):
        if shape not in ("square", "landscape"):
            raise ValueError("Неизвестная форма карточек.")
        with self._storage_lock:
            data = self._read_settings()
            data["playlist_shape"] = shape
            return self._write_settings(data)

    # Эффекты CSS не меняют изображение; разрешены только целые числа в пределах.
    def save_mini_background(self, mode):
        if mode not in ('classic','wallpaper','colors'):raise ValueError('Неизвестный фон миниплеера.')
        with self._storage_lock:
            data=self._read_settings();data['mini_background']=mode;return self._write_settings(data)

    def save_playing_screen(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Некорректная настройка экрана песни.")
        with self._storage_lock:
            data=self._read_settings();data["playing_screen_enabled"]=enabled
            return self._write_settings(data)

    def save_app_icon(self, value):
        if value not in tuple(str(i) for i in range(1,9)):raise ValueError('Неизвестный значок.')
        with self._storage_lock:
            data=self._read_settings();data['app_icon']=value;return self._write_settings(data)

    def save_audio_output(self, device):
        if not isinstance(device,str) or len(device)>2048:raise ValueError('Некорректное устройство.')
        with self._storage_lock:
            data=self._read_settings();data['audio_output']=device;return self._write_settings(data)

    def save_wallpaper_effects(self, dim, blur):
        if type(dim) is not int or not 0 <= dim <= 90 or type(blur) is not int or not 0 <= blur <= 30:
            raise ValueError("Затемнение: 0–90%, размытие: 0–30 пикселей.")
        with self._storage_lock:
            data = self._read_settings()
            data["wallpaper_dim"], data["wallpaper_blur"] = dim, blur
            return self._write_settings(data)

    # Долгий диалог и декодирование выполняются вне блокировки JSON.
    def choose_wallpaper(self):
        with self._storage_lock:
            self._read_settings()  # Повреждённый JSON нельзя перезаписывать.
        if self._window is None:
            raise RuntimeError("Окно приложения ещё не создано.")
        chosen = self._window.create_file_dialog(
            dialog_type=webview.FileDialog.OPEN, allow_multiple=False,
            file_types=(("Images (*.jpg;*.jpeg;*.png;*.webp;*.gif;*.mp4;*.webm)" if self._read_settings().get("language") == "en" else "Изображения (*.jpg;*.jpeg;*.png;*.webp;*.gif;*.mp4;*.webm)"),),
        )
        if not chosen:
            return {"changed": False, "settings": self.load_settings()}
        filename = import_wallpaper(chosen[0])
        try:
            with self._storage_lock:
                data = self._read_settings()
                old = data["wallpaper"]
                data["wallpaper"] = filename
                self._write_settings(data)
                discard_wallpaper(old)
                return {"changed": True, "settings": data}
        except Exception:
            discard_wallpaper(filename)  # Откат: прежняя копия ещё на месте.
            raise

    # Убираем ссылку из JSON, затем удаляем лишь управляемую копию, не исходник.
    def reset_wallpaper(self):
        with self._storage_lock:
            data = self._read_settings()
            old = data["wallpaper"]
            data["wallpaper"] = None
            data["wallpaper_dim"], data["wallpaper_blur"] = 65, 0
            self._write_settings(data)
            discard_wallpaper(old)
            return data


    def save_diagnostics_report(self, text):
        if not isinstance(text,str) or len(text)>128000:raise ValueError('Некорректный отчёт.')
        try:data=json.loads(text)
        except ValueError:raise ValueError('Отчёт должен быть JSON.') from None
        if not isinstance(data,dict) or set(data)-{'events','providers','native_audio','network','note','checks'}:raise ValueError('Неизвестный формат отчёта.')
        if self._window is None:raise RuntimeError('Окно приложения недоступно.')
        selected=self._window.create_file_dialog(webview.FileDialog.SAVE,save_filename='Slynor-diagnostics.json',file_types=('JSON (*.json)',))
        if not selected:return {'saved':False}
        from pathlib import Path
        path=Path(selected[0] if isinstance(selected,(tuple,list)) else selected)
        if path.suffix.lower()!='.json':path=path.with_suffix('.json')
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        return {'saved':True}


# Запуск окна PyWebView и Python-моста.
def main(debug=False):
    desktop_api = DesktopApi()

    from window_geometry import position
    center=position(1000,800)
    window = webview.create_window(
        title="Slynor",
        url="http://127.0.0.1:8765/ui",
        js_api=desktop_api,
        width=1000,
        height=800,
        min_size=(900, 740),
        x=center[0],y=center[1],
    )

    desktop_api._window = window
    from desktop_shell import DesktopShell
    from backend.desktop_controls import register
    shell = DesktopShell(window, desktop_api)
    register(shell)
    window.events.closing += shell.closing
    window.events.closed += shell.stop_tray
    shell.start_tray()
    # WebView2 visibilitychange is not reliable on native minimize/restore.
    # Notify the UI; do not pause or restart the main audio player.
    def native_visibility(minimized):
        shell.notify_hidden(minimized)
    if hasattr(window.events, 'minimized'):
        window.events.minimized += lambda *args: (setattr(shell,'main_minimized',True),native_visibility(True))
    if hasattr(window.events, 'restored'):
        window.events.restored += lambda *args: (setattr(shell,'main_mode','normal'),setattr(shell,'main_minimized',False),native_visibility(False))
    # Restoring an already maximized window may emit maximized, not restored.
    if hasattr(window.events, 'maximized'):
        window.events.maximized += lambda *args: (setattr(shell,'main_mode','maximized'),native_visibility(False))

    try:
        webview.start(debug=debug)
    finally:
        shell.stop_tray()
        register(None)


if __name__ == "__main__":
    main()

