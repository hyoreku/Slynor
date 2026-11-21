"""Exercise the mini-player's spectrum source with actual LibVLC PCM."""
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import wave

from player.post_eq_spectrum import PostEqSpectrumAnalyzer, np


def main():
    with TemporaryDirectory() as folder:
        path = str(Path(folder) / 'frequencies.wav')
        seconds = np.arange(96_000) / 48_000
        with wave.open(path, 'wb') as wav:
            wav.setparams((1, 2, 48_000, 0, 'NONE', 'not compressed'))
            for frequency in (170, 1400):
                wav.writeframes((14_000 * np.sin(2 * np.pi * frequency * seconds)).astype('<i2').tobytes())

        analyzer = PostEqSpectrumAnalyzer()
        try:
            start = monotonic()
            bands = set()
            config = {'enabled': False, 'preamp': 0, 'bands': [0] * 10}
            while monotonic() - start < 3.75:
                snapshot = analyzer.snapshot({
                    'file_path': path, 'state': 'playing', 'playback_generation': 1,
                    'time_ms': int((monotonic() - start) * 1000), 'volume': 80,
                }, config)
                if snapshot['status'] == 'error':
                    raise RuntimeError(snapshot['message'])
                if snapshot['status'] == 'ready':
                    bands.add(max(range(48), key=snapshot['bands'].__getitem__))
                sleep(.09)
        finally:
            analyzer.close()

    if not (bands & set(range(8, 13)) and bands & set(range(24, 29))):
        raise RuntimeError(f'Expected both tone groups, received bands {sorted(bands)}')
    print(f'Native VLC returned the expected tone groups: {sorted(bands)}')


if __name__ == '__main__':
    main()
