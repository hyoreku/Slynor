"""Настоящий FFT по локальному файлу, синхронизированный с позицией VLC.

Не захватывает микрофон/системный выход и не меняет аудиовыход VLC.
Декодирование короткого окна выполняется отдельным рабочим потоком.
"""
from collections import deque
from pathlib import Path
from threading import Condition, Thread
from time import monotonic
import logging

try:
    import av
    import numpy as np
except ImportError:
    av = None
    np = None

logger = logging.getLogger(__name__)
RATE = 22050
FFT_SIZE = 4096
BANDS = 48


# Логарифмические частотные полосы: низкие слева, высокие справа.
def frequency_edges():
    return np.geomspace(50, 10000, BANDS + 1)


def fft_bands(samples, volume=100):
    """Окно Ханна, действительное FFT, пиковая амплитуда полосы, шкала -75..0 dBFS."""
    data = np.asarray(samples, dtype=np.float32)
    if len(data) != FFT_SIZE:
        raise ValueError("Некорректный размер окна FFT.")
    gain = max(0, min(100, volume if isinstance(volume, (int, float)) else 100)) / 100
    window = np.hanning(FFT_SIZE)
    magnitude = np.abs(np.fft.rfft(data * window * gain)) * 2 / window.sum()
    frequencies = np.fft.rfftfreq(FFT_SIZE, 1 / RATE)
    edges = frequency_edges()
    values = []
    for low, high in zip(edges[:-1], edges[1:]):
        indexes = (frequencies >= low) & (frequencies < high)
        amplitude = float(magnitude[indexes].max()) if indexes.any() else 0.0
        db = 20 * np.log10(max(amplitude, 1e-10))
        values.append(round(float(np.clip((db + 75) / 75, 0, 1)), 4))
    return values


class AudioWindowReader:
    """Декодирует по чуть-чуть; не хранит всю песню в RAM и не создаёт WAV-копий."""
    def __init__(self, path):
        self.container = av.open(str(path), mode="r")
        try:
            if not self.container.streams.audio:
                raise ValueError("В файле нет аудиопотока.")
            self.stream = self.container.streams.audio[0]
            self.stream.codec_context.thread_count = 1
            self.origin = float((self.stream.start_time or 0) * self.stream.time_base)
            self.chunks = deque()
            self.decoder = None
            self.eof = False
            self.seeks = 0
            self._seek(0)
        except Exception:
            self.container.close()
            raise

    # При перемотке переинициализируем ресемплер: старая задержка фильтра не переносится.
    def _seek(self, seconds):
        seek_to = max(0, seconds - .25) + self.origin
        self.container.seek(int(seek_to / float(self.stream.time_base)), stream=self.stream,
                            backward=True, any_frame=False)
        self.decoder = iter(self.container.decode(self.stream))
        self.resampler = av.AudioResampler(format="fltp", layout="mono", rate=RATE)
        self.chunks.clear()
        self.eof = False
        self.fallback_time = max(0, seconds - .25)
        self.seeks += 1

    def _append(self, frame):
        samples = frame.to_ndarray().reshape(-1).astype(np.float32, copy=False)
        start = float(frame.pts * frame.time_base) - self.origin if frame.pts is not None else self.fallback_time
        self.chunks.append((start, samples.copy()))
        self.fallback_time = start + len(samples) / RATE

    def read_window(self, seconds):
        span = FFT_SIZE / RATE
        start = max(0, seconds - span / 2)
        end = start + span
        if self.chunks:
            first = self.chunks[0][0]
            last = self.chunks[-1][0] + len(self.chunks[-1][1]) / RATE
            if start < first - .02 or start > last + .5:
                self._seek(start)
        elif seconds > 1:
            self._seek(start)
        # Ограничение итераций защищает от повреждённых/необычных таймстампов.
        for _ in range(512):
            last = self.chunks[-1][0] + len(self.chunks[-1][1]) / RATE if self.chunks else -1
            if last >= end or self.eof:
                break
            try:
                frame = next(self.decoder)
            except StopIteration:
                for output in self.resampler.resample(None):
                    self._append(output)
                self.eof = True
                break
            for output in self.resampler.resample(frame):
                self._append(output)
        else:
            raise ValueError("Не удалось найти аудиосэмплы по времени трека.")
        output = np.zeros(FFT_SIZE, dtype=np.float32)
        for chunk_start, samples in self.chunks:
            offset = round((chunk_start - start) * RATE)
            left, right = max(0, offset), min(FFT_SIZE, offset + len(samples))
            if right > left:
                output[left:right] = samples[left - offset:right - offset]
        # Оставляем только короткий хвост: память не растёт с длиной песни.
        while self.chunks and self.chunks[0][0] + len(self.chunks[0][1]) / RATE < start - .25:
            self.chunks.popleft()
        return output

    def close(self):
        self.container.close()
        self.chunks.clear()


class SpectrumAnalyzer:
    """Одна задача последней позиции, без растущей очереди и без вызовов VLC из потока."""
    def __init__(self):
        self._condition = Condition()
        self._closed = False
        self._request = None
        self._identity = None
        self._epoch = 0
        self._last = None
        self._last_poll = monotonic()
        self._available = av is not None and np is not None
        self._worker = Thread(target=self._run, name="spectrum-decoder", daemon=True)
        if self._available:
            self._worker.start()

    # HTTP-обработчик возвращает готовый результат сразу, FFT не блокирует API плеера.
    def snapshot(self, state):
        path = state.get("file_path")
        playing = state.get("state") == "playing" and bool(path)
        empty = {"bands": [0.0] * BANDS, "file_path": path, "time_ms": state.get("time_ms", 0),
                 "state": state.get("state", "idle"), "status": "paused" if path else "idle", "message": ""}
        if not self._available:
            return {**empty, "status": "unavailable", "message": "Для спектра установите av и numpy, затем перезапустите приложение."}
        with self._condition:
            self._last_poll = monotonic()
            identity = path if playing else None
            if identity != self._identity:
                self._identity = identity
                self._epoch += 1
                self._last = None
            if not playing or self._closed:
                self._request = None
                self._condition.notify()
                return empty
            now = monotonic()
            last = self._last
            if last and last["epoch"] == self._epoch and last["status"] == "error" and now - last["computed_at"] < 2:
                return {**empty, "status": "error", "message": last["message"]}
            time_ms = max(0, state.get("time_ms", 0))
            self._request = (self._epoch, path, time_ms, state.get("volume", 100))
            self._condition.notify()
            if last and last["epoch"] == self._epoch and last["status"] == "ready" and now - last["computed_at"] < .6 and abs(last["time_ms"] - time_ms) < 450:
                return {**empty, "status": "ready", "bands": last["bands"], "sample_time_ms": last["time_ms"]}
            return {**empty, "status": "preparing"}

    def _run(self):
        reader, opened_signature = None, None
        try:
            while True:
                with self._condition:
                    if self._request is None and not self._closed:
                        self._condition.wait(timeout=.8)
                    if self._closed:
                        return
                    request, self._request = self._request, None
                    idle = monotonic() - self._last_poll > 1.4 or self._identity is None
                if request is None:
                    if idle and reader is not None:
                        reader.close(); reader, opened_signature = None, None
                    continue
                epoch, path, time_ms, volume = request
                try:
                    source = Path(path.strip().strip('"')).expanduser().resolve()
                    stat = source.stat()
                    signature = (str(source), stat.st_mtime_ns, stat.st_size)
                    if opened_signature != signature:
                        if reader is not None:
                            reader.close()
                        reader, opened_signature = None, None
                        reader = AudioWindowReader(source)
                        opened_signature = signature
                    bands = fft_bands(reader.read_window(time_ms / 1000), volume)
                    result = {"epoch": epoch, "time_ms": time_ms, "bands": bands, "status": "ready", "message": ""}
                except Exception:
                    logger.debug("Не удалось построить спектр локального трека.", exc_info=True)
                    if reader is not None:
                        reader.close(); reader, opened_signature = None, None
                    result = {"epoch": epoch, "time_ms": time_ms, "status": "error",
                              "message": "Не удалось декодировать этот трек для спектра. Воспроизведение VLC не затронуто."}
                with self._condition:
                    if not self._closed and epoch == self._epoch:
                        result["computed_at"] = monotonic()
                        self._last = result
        finally:
            if reader is not None:
                reader.close()

    def close(self):
        with self._condition:
            self._closed = True
            self._request = None
            self._condition.notify_all()
        if self._available:
            self._worker.join(timeout=3)
