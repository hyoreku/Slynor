"""Media facts come from the file itself, not from its catalogue card."""
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
import wave

from service.media import AUDIO_EXTENSIONS, file_sha256, measure, probe_audio


def wav_file(path, frames=2400, rate=8000):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x00\x00' * frames)
    return path


class MediaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_measure_reads_hash_size_container_and_audio(self):
        path = wav_file(self.root / 'tone.wav')
        facts = measure(path)
        self.assertEqual(facts.sha256, sha256(path.read_bytes()).hexdigest())
        self.assertEqual(facts.size_bytes, path.stat().st_size)
        self.assertEqual(facts.container, 'wav')
        self.assertIsNotNone(facts.codec)
        self.assertGreater(facts.duration_ms, 100)
        self.assertLess(facts.duration_ms, 1000)

    def test_broken_media_keeps_unknown_audio_facts(self):
        path = self.root / 'broken.mp3'
        path.write_bytes(b'not an mp3 at all')
        facts = measure(path)
        self.assertEqual(facts.container, 'mp3')
        self.assertEqual(facts.size_bytes, len(b'not an mp3 at all'))
        self.assertIsNone(facts.duration_ms)
        codec, duration = probe_audio(path)
        self.assertIsNone(duration)
        self.assertIsNone(codec)

    def test_empty_links_and_foreign_containers_are_refused(self):
        empty = self.root / 'empty.wav'
        empty.write_bytes(b'')
        target = self.root / 'song.exe'
        target.write_bytes(b'MZ')
        for path in (empty, target, self.root / 'missing.mp3'):
            with self.assertRaises(ValueError):
                measure(path)
        link = self.root / 'link.wav'
        try:
            link.symlink_to(wav_file(self.root / 'real.wav'))
        except (OSError, NotImplementedError):
            self.skipTest('creating a symlink is unavailable on this machine')
        with self.assertRaises(ValueError):
            measure(link)

    def test_supported_containers_are_explicit(self):
        self.assertIn('.mp3', AUDIO_EXTENSIONS)
        self.assertIn('.opus', AUDIO_EXTENSIONS)
        self.assertNotIn('.exe', AUDIO_EXTENSIONS)


if __name__ == '__main__':
    unittest.main()
