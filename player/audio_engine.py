import logging
import random
from pathlib import Path
from time import perf_counter
from queue import SimpleQueue
from threading import RLock, Thread

import vlc

from copy import deepcopy
from player.equalizer import DEFAULT_EQ, SPEED_RATES, validate_equalizer


logger = logging.getLogger(__name__)


class AudioEngine:
    # Создание объектов и приватного состояния; вызывается один раз при запуске.
    def __init__(self):
        self._lock = RLock()

        self._instance = vlc.Instance("--no-video", "--no-audio-time-stretch")
        if self._instance is None:
            raise RuntimeError("Не удалось создать движок VLC.")

        self._player = self._instance.media_player_new()
        self._player.audio_set_volume(50)

        self._equalizer_config = deepcopy(DEFAULT_EQ)
        self._native_eq = None
        self._current_filename = None
        self.stream_resolver = None
        self._current_stream_url = None
        self._output_device = ""
        self._startup = None
        self._startup_started = None

        # Исходный порядок списка и фактический порядок воспроизведения.
        self._source_paths = []
        self._queue = []
        self._queue_index = None

        self._shuffle_enabled = False
        self._playback_error = None
        self._auto_advance_enabled = False
        self._generation = 0
        self._closed = False

        self._events = SimpleQueue()
        self._event_manager = self._player.event_manager()

        self._worker = Thread(
            target=self._process_events,
            name="audio-events",
            daemon=True,
        )
        self._worker.start()

    # Проверка состава очереди до изменения состояния плеера.
    def _validate_paths(self, paths):
        if not paths:
            raise ValueError("Список треков пуст.")

        if not all(
            isinstance(path, str) and path.strip()
            for path in paths
        ):
            raise ValueError("В списке есть некорректный путь.")

    # Случайная перестановка: выбранная песня первая, без повтора на границе циклов.
    def _shuffled_paths(self, paths, first_path=None, avoid_first=None):
        ordered = list(paths)

        if first_path is not None:
            ordered.remove(first_path)
            random.shuffle(ordered)
            return [first_path] + ordered

        random.shuffle(ordered)

        # Не повторяем последний трек прошлого прохода
        # сразу в начале нового прохода.
        if len(ordered) > 1 and ordered[0] == avoid_first:
            swap_index = next(
                (
                    index
                    for index, path in enumerate(ordered[1:], start=1)
                    if path != avoid_first
                ),
                None,
            )

            if swap_index is not None:
                ordered[0], ordered[swap_index] = (
                    ordered[swap_index],
                    ordered[0],
                )

        return ordered

    # Загрузка файла в VLC и запуск. События старого трека становятся неактуальными.
    def _play_file(self, path: str):
        started = perf_counter()
        online = path.startswith('online:')
        if online:
            if self.stream_resolver is None: raise ValueError('Интернет-источник не подключён.')
            media_url, display_name = self.stream_resolver(path)
            file_path = None
        else:
            file_path = Path(path.strip().strip('"')).expanduser().resolve()
            if not file_path.is_file():
                raise FileNotFoundError(f"Музыкальный файл не найден: {file_path}")
            display_name = file_path.name

        with self._lock:
            self._auto_advance_enabled = False
            self._generation += 1
            generation = self._generation

            for event_type in (vlc.EventType.MediaPlayerEndReached,
                               vlc.EventType.MediaPlayerPlaying,
                               vlc.EventType.MediaPlayerEncounteredError):
                self._event_manager.event_detach(event_type)
            if self._current_filename is not None:
                self._player.stop()

            media = self._instance.media_new(media_url) if online else self._instance.media_new_path(str(file_path))

            try:
                # Local disk only; do not lower network buffering or parse metadata before play.
                if not online and hasattr(media, 'add_option'):
                    media.add_option(':file-caching=150')
                self._startup_started = started
                self._startup = {'source':'online' if online else 'local', 'request_to_play_ms':None, 'request_to_playing_event_ms':None}
                self._player.set_media(media)
                if self._output_device:self._player.audio_output_device_set(None,self._output_device)
                self._current_filename = display_name
                self._current_stream_url = media_url if online else None
                self._playback_error = None

                # Callbacks enqueue only; never call LibVLC recursively from its thread.
                for event_type, kind in (
                    (vlc.EventType.MediaPlayerEndReached, "ended"),
                    (vlc.EventType.MediaPlayerPlaying, "playing"),
                    (vlc.EventType.MediaPlayerEncounteredError, "error"),
                ):
                    def callback(event, kind=kind):
                        self._events.put((generation, kind))
                    if self._event_manager.event_attach(event_type, callback) != 0:
                        raise RuntimeError("Не удалось подключить событие VLC.")

                if self._player.play() == -1:
                    raise RuntimeError(
                        "VLC не смог запустить воспроизведение."
                    )

                # set_rate during Opening may be ignored/reset by the new decoder.
                # The generation-checked Playing event applies the latest requested mode.
                self._startup['request_to_play_ms'] = round((perf_counter()-started)*1000)
                self._auto_advance_enabled = True
            finally:
                media.release()

        return display_name

    # Запуск песни и сохранение исходного/реального порядка очереди под блокировкой.
    def _start_queue(self, source_paths, ordered_paths, index):
        filename = self._play_file(ordered_paths[index])

        self._source_paths = list(source_paths)
        self._queue = list(ordered_paths)
        self._queue_index = index

        return filename

    # Одиночный запуск через общий механизм очереди.
    def play(self, path: str):
        return self.play_queue([path], 0)

    # Запуск выбранной песни и всего списка в обычном или случайном режиме.
    def play_queue(self, paths: list[str], index: int):
        self._validate_paths(paths)

        if not 0 <= index < len(paths):
            raise ValueError(
                "Выбранный трек отсутствует в списке."
            )

        with self._lock:
            if self._shuffle_enabled:
                ordered = self._shuffled_paths(
                    paths,
                    first_path=paths[index],
                )
                return self._start_queue(paths, ordered, 0)

            return self._start_queue(paths, paths, index)

    # Переключение режима: случайный старт или восстановление обычного порядка.
    def set_shuffle(self, enabled: bool, paths: list[str]):
        with self._lock:
            if enabled:
                self._validate_paths(paths)

                ordered = self._shuffled_paths(paths)
                filename = self._start_queue(paths, ordered, 0)

                self._shuffle_enabled = True

                return (
                    "Случайный режим включён. "
                    f"Запускаем: {filename}"
                )

            # Выключаем shuffle, не прерывая текущий трек.
            if self._queue_index is not None:
                current_path = self._queue[self._queue_index]

                self._queue = list(self._source_paths)
                self._queue_index = self._queue.index(current_path)

            self._shuffle_enabled = False

            return (
                "Случайный режим выключен. "
                "Следующие треки пойдут по порядку списка."
            )

    # Запуск конкретной позиции существующей очереди.
    def _play_queue_index(self, index: int):
        filename = self._play_file(self._queue[index])
        self._queue_index = index
        return filename

    # Автоматический переход и повтор списка; для shuffle создаём новый цикл.
    def _advance_queue(self):
        if self._queue_index is None:
            raise ValueError("Сначала запусти трек из списка.")

        next_index = self._queue_index + 1

        if next_index < len(self._queue):
            return self._play_queue_index(next_index)

        if not self._shuffle_enabled:
            return self._play_queue_index(0)

        last_path = self._queue[self._queue_index]

        ordered = self._shuffled_paths(
            self._source_paths,
            avoid_first=last_path,
        )

        return self._start_queue(self._source_paths, ordered, 0)

    # Команда перехода вперёд в текущей очереди.
    def next_track(self):
        with self._lock:
            return self._advance_queue()

    # Команда перехода назад в текущем проходе.
    def previous_track(self):
        with self._lock:
            if self._queue_index is None:
                raise ValueError(
                    "Сначала запусти трек из списка."
                )

            previous_index = self._queue_index - 1

            if previous_index < 0:
                raise ValueError(
                    "Это первый трек текущего прохода."
                )

            return self._play_queue_index(previous_index)

    # Фоновая обработка завершения VLC. Не вызываем VLC из его собственного callback.
    def _process_events(self):
        while True:
            item = self._events.get()

            if item is None:
                return
            generation, kind = item

            with self._lock:
                if self._closed or generation != self._generation:
                    continue
                if kind == "playing":
                    if self._startup and self._startup["request_to_playing_event_ms"] is None:
                        self._startup["request_to_playing_event_ms"] = round((perf_counter()-self._startup_started)*1000)
                        logging.getLogger("ownplayer").info(
                            "playback_start source=%s request_to_play_ms=%s request_to_playing_event_ms=%s",
                            self._startup.get("source"), self._startup.get("request_to_play_ms"),
                            self._startup.get("request_to_playing_event_ms"))
                    try:
                        rate = SPEED_RATES[self._equalizer_config["speed_mode"]]
                        # Resume at the same speed does not touch the audio output.
                        if abs(self._player.get_rate() - rate) > .001:
                            if self._player.set_rate(rate) != 0:
                                raise RuntimeError("VLC не применил скорость нового трека.")
                    except Exception as error:
                        self._playback_error = str(error)
                        logger.exception("Ошибка скорости нового трека")
                    continue
                if kind == "error":
                    self._auto_advance_enabled = False
                    if not self._playback_error:
                        self._playback_error = "VLC не смог запустить воспроизведение."
                    continue

                if not self._auto_advance_enabled:
                    continue

                self._auto_advance_enabled = False

                if self._queue_index is None:
                    continue

                try:
                    self._advance_queue()
                except Exception as error:
                    self._auto_advance_enabled = False
                    self._playback_error = (
                        "Не удалось автоматически запустить "
                        f"следующий трек: {error}"
                    )
                    logger.exception(
                        "Ошибка автоматического перехода"
                    )

    # Пауза только при воспроизведении.
    def pause(self):
        with self._lock:
            state=self._player.get_state()
            if state==vlc.State.Paused:return True
            if state in (vlc.State.Opening,vlc.State.Buffering) or (self._current_filename and state in (vlc.State.NothingSpecial,vlc.State.Stopped)):return False
            if state != vlc.State.Playing:
                raise ValueError(
                    "Пауза доступна во время воспроизведения."
                )

            self._player.set_pause(1)

    # Продолжение только после паузы.
    def resume(self):
        with self._lock:
            state=self._player.get_state()
            if state==vlc.State.Playing:return True
            if state in (vlc.State.Opening,vlc.State.Buffering) or (self._current_filename and state in (vlc.State.NothingSpecial,vlc.State.Stopped)):return False
            if state != vlc.State.Paused:
                raise ValueError(
                    "Продолжить можно только после паузы."
                )

            self._player.set_pause(0)

    # Стоп снимает текущую песню и очередь. Пауза их не меняет.
    def stop(self):
        with self._lock:
            self._auto_advance_enabled = False
            self._generation += 1
            self._playback_error = None
            self._player.stop()
            self._current_filename = None
            self._current_stream_url = None
            self._source_paths = []
            self._queue = []
            self._queue_index = None
            # Выбор shuffle и громкость сохраняем для следующего запуска.

    def report_stream_error(self, expected_url, message):
        """Errors from stale proxy sessions must not affect a new/current track."""
        with self._lock:
            if self._current_stream_url == expected_url and self._queue_index is not None:
                self._playback_error = str(message)
                self._auto_advance_enabled = False

    # Копия очереди под блокировкой: интерфейс не меняет внутренний список.
    def get_queue(self):
        with self._lock:
            return {
                "paths": list(self._queue),
                "index": self._queue_index,
                "shuffle_enabled": self._shuffle_enabled,
            }

    # Снимок состояния VLC, времени, громкости и позиции очереди.
    def get_state(self):
        state_names = {
            vlc.State.NothingSpecial: "idle",
            vlc.State.Opening: "opening",
            vlc.State.Buffering: "buffering",
            vlc.State.Playing: "playing",
            vlc.State.Paused: "paused",
            vlc.State.Stopped: "stopped",
            vlc.State.Ended: "ended",
            vlc.State.Error: "error",
        }

        with self._lock:
            native_state = self._player.get_state()
            volume = self._player.audio_get_volume()

            time_ms = max(0, self._player.get_time())
            duration_ms = max(0, self._player.get_length())

            can_seek = (
                native_state in (
                    vlc.State.Playing,
                    vlc.State.Paused,
                )
                and duration_ms > 0
                and bool(self._player.is_seekable())
            )

            state = state_names.get(native_state, "unknown")
            if self._queue_index is not None and state in ("idle","stopped") and self._current_filename:
                state="opening"

            if self._queue_index is None:
                # VLC может помнить длину остановленного media. В UI трека уже нет.
                state = "idle"
                time_ms = 0
                duration_ms = 0
                can_seek = False
            elif self._playback_error:
                state = "error"

            return {
                "state": state,
                "startup": deepcopy(self._startup),
                "filename": self._current_filename,
                "file_path": (
                    self._queue[self._queue_index]
                    if self._queue_index is not None
                    else None
                ),
                "volume": volume if volume >= 0 else None,
                "time_ms": time_ms,
                "duration_ms": duration_ms,
                "can_seek": can_seek,
                "queue_index": self._queue_index,
                "queue_size": len(self._queue),
                "shuffle_enabled": self._shuffle_enabled,
                "playback_error": self._playback_error,
                "rate": (self._player.get_rate() if native_state in
                         (vlc.State.Playing, vlc.State.Paused) else
                         SPEED_RATES[self._equalizer_config["speed_mode"]]),
                "speed_mode": self._equalizer_config["speed_mode"],
            }

    # Эквалайзер применяется к аудиовыходу VLC, без второго плеера/перезапуска песни.
    def get_equalizer_frequencies(self):
        with self._lock:
            try:
                count = vlc.libvlc_audio_equalizer_get_band_count()
                if count != 10:
                    raise RuntimeError("Ожидалось 10 частотных полос.")
                return [float(vlc.libvlc_audio_equalizer_get_band_frequency(i)) for i in range(count)]
            except (NameError, AttributeError, OSError) as error:
                raise RuntimeError("Нет поддержки LibVLC equalizer.") from error

    def get_equalizer(self):
        with self._lock:
            return deepcopy(self._equalizer_config)

    def set_equalizer(self, data):
        """Bypass = flat filter, NEVER detach EQ mid-stream (VLC output restart bug).

        Object stays alive until close. Speed is independent of the EQ checkbox.
        The first call is made by EqualizerController before the first song.
        """
        config = validate_equalizer(data)
        with self._lock:
            if self._closed:
                raise RuntimeError("Плеер уже закрыт.")
            previous = self._equalizer_config
            if config == previous and self._native_eq is not None:
                return
            def effective(c):
                return (c["preamp"], tuple(c["bands"])) if c["enabled"] else (0.0, (0.0,) * 10)
            rate_changed = config["speed_mode"] != previous["speed_mode"]
            # With no active decoder, keep the preference and apply on Playing.
            apply_rate_now = rate_changed and self._player.get_state() in (
                vlc.State.Playing, vlc.State.Paused)
            if apply_rate_now and self._player.set_rate(SPEED_RATES[config["speed_mode"]]) != 0:
                raise RuntimeError("VLC не поддерживает изменение скорости этого файла.")
            try:
                if self._native_eq is None or effective(config) != effective(previous):
                    if self._native_eq is None:
                        self._native_eq = vlc.AudioEqualizer()
                        if self._native_eq is None:
                            raise RuntimeError("VLC не создал эквалайзер.")
                    preamp, bands = effective(config)
                    if self._native_eq.set_preamp(preamp) != 0:
                        raise RuntimeError("Не удалось изменить предусиление.")
                    for i, value in enumerate(bands):
                        if self._native_eq.set_amp_at_index(value, i) != 0:
                            raise RuntimeError("Не удалось изменить частотную полосу.")
                    if self._player.set_equalizer(self._native_eq) != 0:
                        raise RuntimeError("VLC не применил эквалайзер.")
                self._equalizer_config = config
            except Exception:
                if apply_rate_now:
                    self._player.set_rate(SPEED_RATES[previous["speed_mode"]])
                # The persistent native object was mutated above. Restore its values
                # too, otherwise the reported previous config and audio can diverge.
                if self._native_eq is not None:
                    preamp, bands = effective(previous)
                    try:
                        self._native_eq.set_preamp(preamp)
                        for i, value in enumerate(bands):
                            self._native_eq.set_amp_at_index(value, i)
                        self._player.set_equalizer(self._native_eq)
                    except Exception:
                        logger.exception("Не удалось восстановить эквалайзер")
                raise

    # Изменение громкости VLC, не системной громкости Windows.
    def set_volume(self, volume: int):
        if not 0 <= volume <= 100:
            raise ValueError(
                "Громкость должна быть от 0 до 100."
            )

        with self._lock:
            result = self._player.audio_set_volume(volume)

            if result == -1:
                raise RuntimeError(
                    "VLC не смог изменить громкость."
                )

    # Перемотка внутри длительности текущего трека.
    def seek(self, position_ms: int, expected_path: str | None = None):
        with self._lock:
            if expected_path is not None:
                actual = self._queue[self._queue_index] if self._queue_index is not None else None
                def same_path(a, b):
                    if a.startswith('online:') or b.startswith('online:'): return a == b
                    return str(Path(a).expanduser().resolve()).casefold() == str(Path(b).expanduser().resolve()).casefold()
                if actual is None or not same_path(actual, expected_path):
                    raise ValueError("Текущая песня изменилась. Повтори перемотку.")
            state = self._player.get_state()
            duration_ms = self._player.get_length()

            if state not in (
                vlc.State.Playing,
                vlc.State.Paused,
            ):
                raise ValueError(
                    "Перемотка доступна во время "
                    "воспроизведения или паузы."
                )

            if duration_ms <= 0 or not self._player.is_seekable():
                raise ValueError(
                    "Этот трек пока нельзя перемотать."
                )

            if position_ms < 0:
                raise ValueError(
                    "Позиция не может быть отрицательной."
                )

            target_ms = min(position_ms, duration_ms - 1)
            self._player.set_time(target_ms)

            return target_ms

    # Остановка фонового обработчика и освобождение ресурсов VLC.
    def close(self):
        with self._lock:
            if self._closed:
                return

            self._closed = True
            self._auto_advance_enabled = False
            self._generation += 1

        self._events.put(None)
        self._worker.join()

        with self._lock:
            for event_type in (vlc.EventType.MediaPlayerEndReached,
                               vlc.EventType.MediaPlayerPlaying,
                               vlc.EventType.MediaPlayerEncounteredError):
                self._event_manager.event_detach(event_type)
            self._player.stop()
            self._player.release()
            self._instance.release()
            if self._native_eq is not None:
                self._native_eq.release(); self._native_eq = None

    def output_devices(self):
        """Enumerate the active VLC audio module. Only Windows/VLC can certify real IDs."""
        with self._lock:
            rows=[{'id':'','name':'По умолчанию в Windows'}]
            head=None
            try:
                head=self._player.audio_output_device_enum();node=head;seen=set()
                while node and len(rows)<128:
                    item=node.contents
                    decode=lambda value:value.decode('utf-8','replace') if isinstance(value,bytes) else str(value or '')
                    identifier=decode(item.device)
                    if identifier and identifier not in seen:
                        seen.add(identifier);rows.append({'id':identifier,'name':decode(item.description) or identifier})
                    node=item.next
            except (AttributeError,ValueError):pass
            finally:
                if head:vlc.libvlc_audio_output_device_list_release(head)
            return {'devices':rows,'selected':getattr(self,'_output_device',''),'available':len(rows)>1}

    def set_output_device(self, device):
        if not isinstance(device,str) or len(device)>2048:raise ValueError('Некорректное устройство вывода.')
        with self._lock:
            if device and device not in {d['id'] for d in self.output_devices()['devices']}:
                raise ValueError('Устройство не найдено. Обновите список или выберите системное.')
            self._player.audio_output_device_set(None,device or None)
            self._output_device=device
            return {'selected':device,'message':'Команда выбора передана VLC. Проверьте звук на выбранном выходе.'}

    def output_diagnostics(self):
        """Privacy-safe native probe: counts/availability, never paths or device IDs."""
        with self._lock:
            result=self.output_devices()
            try:
                version=vlc.libvlc_get_version()
                version=version.decode('utf-8','replace') if isinstance(version,bytes) else str(version)
            except (AttributeError,OSError):version='unknown'
            selected=result.get('selected','')
            return {'vlc_version':version[:120],'enumeration_available':result['available'],
                    'device_count':max(0,len(result['devices'])-1),'uses_system_default':not bool(selected),
                    'saved_device_present':selected in {d['id'] for d in result['devices']}}
