"""FFT после настоящего фильтра LibVLC в отдельной БЕЗЗВУЧНОЙ ветке.

Основной MediaPlayer и его аудиовыход не перенастраиваются. Ветка получает тот же
локальный файл, позицию, громкость и EQ; amem отдаёт PCM после фильтров только в RAM.
Это не захват выхода звуковой карты и не искусственное умножение полос FFT.
"""
import ctypes
from collections import deque
from copy import deepcopy
from pathlib import Path
from threading import Condition, Thread
from time import monotonic
import vlc
from player.equalizer import SPEED_RATES
try:
    import numpy as np
except ImportError:
    np = None

RATE, SIZE, BANDS = 48000, 4096, 48
if np is not None:
    WINDOW = np.hanning(SIZE)
    FREQUENCIES = np.fft.rfftfreq(SIZE, 1 / RATE)
    EDGES = np.geomspace(50, 20000, BANDS + 1)
    MASKS = [(FREQUENCIES >= low) & (FREQUENCIES < high) for low, high in zip(EDGES[:-1], EDGES[1:])]


def fft_processed(samples):
    data = np.asarray(samples, dtype=np.float32)
    if len(data) != SIZE:
        raise ValueError("Неверный размер обработанного окна.")
    magnitude = np.abs(np.fft.rfft(data * WINDOW)) * 2 / WINDOW.sum()
    result = []
    for index, mask in enumerate(MASKS):
        # Узкие низкие полосы: интерполяция между соседними FFT-бинами, не случайные числа.
        amp = float(magnitude[mask].max()) if mask.any() else float(np.interp((EDGES[index] * EDGES[index + 1])**.5, FREQUENCIES, magnitude))
        result.append(round(float(np.clip((20 * np.log10(max(amp, 1e-10)) + 75) / 75, 0, 1)), 4))
    return result


class ProcessedReader:
    """VLC callbacks лишь копируют короткие PCM-блоки; FFT работает вне callback."""
    def __init__(self, path):
        self.condition = Condition()
        self.closed = False
        self.chunks = deque()
        self.frames = 0
        self.last_pcm = 0
        self.skip_until = 0
        self.seek_done = False
        self.seek_requested_at = 0
        self.current_eq = None
        self.volume = None
        self.wanted_volume = 100
        self.paused = False
        self.instance = vlc.Instance('--no-video', '--no-osd', '--quiet', '--no-audio-time-stretch')
        if self.instance is None:
            raise RuntimeError('Не удалось создать аналитическую ветку VLC.')
        self.player = self.instance.media_player_new()
        try:
            # Ссылки живут до stop/release; иначе C может вызвать освобождённую Python-функцию.
            self.callbacks = [
                vlc.CallbackDecorators.AudioPlayCb(self._play),
                vlc.CallbackDecorators.AudioPauseCb(self._pause),
                vlc.CallbackDecorators.AudioResumeCb(self._resume),
                vlc.CallbackDecorators.AudioFlushCb(self._flush),
                vlc.CallbackDecorators.AudioDrainCb(lambda opaque: None),
            ]
            self.player.audio_set_callbacks(*self.callbacks, None)
            self.player.audio_set_format('S16N', RATE, 2)
            media = self.instance.media_new_path(str(Path(path).expanduser().resolve()))
            try:self.player.set_media(media)
            finally:media.release()
            if self.player.play() == -1:
                raise RuntimeError('VLC не запустил аналитическую ветку.')
        except Exception:
            self.close()
            raise

    def _clear(self):
        self.chunks.clear(); self.frames = 0; self.last_pcm = 0

    def _play(self, opaque, pointer, count, pts):
        try:
            # amem не играет через колонки. Замедляем ветку до временных меток VLC,
            # чтобы она не декодировала всю песню вперёд и не занимала всю память.
            with self.condition:
                while not self.closed and not self.paused:
                    ahead = (pts - vlc.libvlc_clock()) / 1_000_000 - .02
                    if ahead <= 0:break
                    self.condition.wait(timeout=min(.04, ahead))
                if self.closed or self.paused or monotonic() < self.skip_until:return
                if count <= 0 or count > RATE * 2:return
                raw = ctypes.string_at(pointer, count * 4)
                mono = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2).astype(np.float32).mean(axis=1) / 32768
                self.chunks.append(mono); self.frames += len(mono)
                while len(self.chunks) > 1 and self.frames - len(self.chunks[0]) >= SIZE:
                    self.frames -= len(self.chunks.popleft())
                self.last_pcm = monotonic()
        except Exception:
            # Исключение нельзя пропускать через ctypes callback в LibVLC.
            with self.condition:self._clear()

    def _pause(self, opaque, pts):
        with self.condition:self.paused = True; self._clear(); self.condition.notify_all()
    def _resume(self, opaque, pts):
        with self.condition:self.paused = False; self.condition.notify_all()
    def _flush(self, opaque, *ignored):
        with self.condition:self._clear(); self.condition.notify_all()

    def configure(self, config, volume):
        # Mirror the main filter: bypass is flat, speed includes pitch change.
        if config != self.current_eq:
            equalizer = vlc.AudioEqualizer()
            try:
                preamp = config['preamp'] if config['enabled'] else 0
                bands = config['bands'] if config['enabled'] else [0] * 10
                if equalizer is None or equalizer.set_preamp(preamp) != 0:
                    raise RuntimeError('Не удалось настроить анализ после EQ.')
                for i, value in enumerate(bands):
                    if equalizer.set_amp_at_index(value, i) != 0:
                        raise RuntimeError('Не удалось настроить полосу анализа.')
                if self.player.set_equalizer(equalizer) != 0:
                    raise RuntimeError('VLC не применил EQ аналитической ветки.')
                rate = SPEED_RATES[config.get('speed_mode','standard')]
                if self.player.set_rate(rate) != 0:
                    raise RuntimeError('VLC не изменил скорость анализа.')
                self.current_eq = deepcopy(config)
            finally:
                if equalizer is not None:equalizer.release()
            with self.condition:self._clear(); self.skip_until = monotonic() + .025
        self.wanted_volume = volume
        self._sync_volume()

    def _sync_volume(self):
        if self.wanted_volume != self.volume and self.player.get_state() == vlc.State.Playing:
            if self.player.audio_set_volume(self.wanted_volume) == -1:
                raise RuntimeError('Не удалось синхронизировать громкость анализа.')
            self.volume = self.wanted_volume
            with self.condition:self._clear()

    def read(self, position):
        state = self.player.get_state()
        if state == vlc.State.Error:raise RuntimeError('VLC не декодирует этот файл для спектра.')
        if state not in (vlc.State.Playing, vlc.State.Ended):return None
        self._sync_volume()
        actual = max(0, self.player.get_time())
        if not self.seek_done or (abs(actual - position) > 320 and monotonic() - self.seek_requested_at > .35):
            with self.condition:self._clear(); self.skip_until = monotonic() + .06
            self.player.set_time(int(position)); self.seek_done = True; self.seek_requested_at = monotonic()
            return None
        if abs(actual - position) > 320:return None
        with self.condition:
            if self.frames < SIZE or monotonic() - self.last_pcm > .22:return None
            samples = np.concatenate(tuple(self.chunks))[-SIZE:].copy()
        return fft_processed(samples)

    def close(self):
        with self.condition:self.closed = True; self._clear(); self.condition.notify_all()
        if self.player is not None:
            self.player.stop(); self.player.release(); self.player = None
        self.instance.release()


class PostEqSpectrumAnalyzer:
    """Последний запрос вместо растущей очереди; ветка закрывается при простое."""
    def __init__(self):
        self.condition = Condition(); self.closed = False; self.request = None
        self.identity = None; self.epoch = 0; self.last = None; self.last_poll = monotonic()
        self.thread = Thread(target=self._run, name='post-eq-spectrum', daemon=True)
        if np is not None:self.thread.start()

    def snapshot(self, state, config):
        path = state.get('file_path'); playing = state.get('state') == 'playing' and bool(path)
        empty = {'bands': [0.0]*BANDS, 'file_path': path, 'state': state.get('state','idle'),
                 'time_ms': state.get('time_ms',0), 'status': 'paused' if path else 'idle',
                 'source': 'vlc_post_eq_branch', 'message': ''}
        if np is None:return {**empty, 'status':'unavailable', 'message':'Для спектра установите NumPy и перезапустите приложение.'}
        volume = state.get('volume'); volume = int(volume) if isinstance(volume, (int,float)) else 100
        identity = (path, config['enabled'], config['preamp'], tuple(config['bands']), config.get('speed_mode','standard'), volume) if playing else None
        with self.condition:
            self.last_poll = monotonic()
            if identity != self.identity:
                self.identity = identity; self.epoch += 1; self.last = None
            if not playing or self.closed:
                self.request = None; self.condition.notify(); return empty
            self.request = (self.epoch, path, max(0,state.get('time_ms',0)), deepcopy(config), volume)
            self.condition.notify()
            last = self.last
            if last and last['epoch'] == self.epoch:
                if last['status']=='error' and monotonic()-last['at']<2:return {**empty,'status':'error','message':last['message']}
                if last['status']=='ready' and monotonic()-last['at']<.22 and abs(last['time_ms']-state['time_ms'])<180:
                    return {**empty,'status':'ready','bands':last['bands']}
            return {**empty, 'status':'preparing'}

    def _run(self):
        reader = None; path_opened = None
        try:
            while True:
                with self.condition:
                    if self.request is None and not self.closed:self.condition.wait(timeout=.5)
                    if self.closed:return
                    request, self.request = self.request, None
                    idle = monotonic()-self.last_poll > .9 or self.identity is None
                    error_backoff = self.last and self.last['status']=='error' and monotonic()-self.last['at']<2
                if request is None:
                    if idle and reader is not None:reader.close(); reader = None; path_opened = None
                    continue
                epoch, path, position, config, volume = request
                if error_backoff:continue
                try:
                    if path != path_opened:
                        if reader is not None:reader.close(); reader = None
                        reader = ProcessedReader(path); path_opened = path
                    reader.configure(config, volume)
                    bands = reader.read(position)
                    result = {'status':'ready' if bands is not None else 'preparing','bands':bands,'message':''}
                except Exception:
                    if reader is not None:reader.close(); reader = None; path_opened = None
                    result = {'status':'error','bands':None,'message':'Анализ после EQ недоступен для этого файла. Основное воспроизведение не затронуто.'}
                with self.condition:
                    if epoch == self.epoch and not self.closed:
                        self.last = {**result,'epoch':epoch,'time_ms':position,'at':monotonic()}
        finally:
            if reader is not None:reader.close()

    def suspend(self):
        with self.condition:
            self.identity = None; self.epoch += 1; self.last = None; self.request = None
            self.condition.notify_all()

    def close(self):
        with self.condition:self.closed = True; self.request = None; self.condition.notify_all()
        if np is not None:self.thread.join(timeout=4)
