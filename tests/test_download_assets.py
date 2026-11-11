"""Downloaded files keep measured duration and a durable cover copy."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch

from PIL import Image

from backend.download_metadata import (publish_download, read_download_metadata, sidecar_path)
from backend.metadata import read_track_metadata
from backend.track_covers import track_cover_url


def wav_file(path):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\x00\x00' * 2400)
    return path


def jpeg_bytes(size=32):
    from io import BytesIO
    buffer = BytesIO()
    Image.new('RGB', (size, size), '#4a3a7a').save(buffer, format='JPEG', quality=85)
    return buffer.getvalue()


class DownloadAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # get_data_dir() reads LOCALAPPDATA on every call, so the test never
        # touches the real user cover cache.
        self.environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.data = self.root / 'OwnPlayerMusic'
        self.folder = self.root / 'Downloads'
        self.online = self.data / 'online-covers'
        self.online.mkdir(parents=True)
        self.track = {'title': 'Тестовая песня', 'artist': 'Исполнитель',
                      'source': 'youtube',
                      'permalink_url': 'https://www.youtube.com/watch?v=abcdefghijk'}

    def test_publish_stores_measured_duration_and_cover(self):
        key = 'a' * 64
        (self.online / (key + '.jpg')).write_bytes(jpeg_bytes())
        track = {**self.track, 'cover_url': '/api/assets/online-covers/' + key + '.jpg'}
        target = publish_download(wav_file(self.root / 'input.wav'), self.folder, track)
        saved = read_download_metadata(target)
        self.assertGreater(saved['duration_ms'], 100)
        self.assertRegex(saved['cover_file'], r'^[0-9a-f]{64}\.jpg$')
        self.assertTrue((self.data / 'covers' / 'tracks' / saved['cover_file']).is_file())
        read = read_track_metadata(str(target))
        self.assertEqual(read['cover_url'], '/api/assets/track-covers/' + saved['cover_file'])
        self.assertGreater(read['duration_ms'], 100)

    def test_malformed_measured_fields_are_ignored(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        target = wav_file(self.folder / 'song.wav')
        stat = target.stat()
        payload = {'version': 1, 'file_name': target.name, 'size_bytes': stat.st_size,
                   'mtime_ns': stat.st_mtime_ns,
                   'recording': {'title': 'Песня', 'artist': 'Автор',
                                 'duration_ms': -5, 'cover_file': '../evil.jpg'}}
        sidecar_path(target).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        saved = read_download_metadata(target)
        self.assertEqual(saved['title'], 'Песня')
        self.assertNotIn('duration_ms', saved)
        self.assertNotIn('cover_file', saved)
        self.assertIsNone(track_cover_url('../evil.jpg'))

    def test_webm_without_tags_gets_duration_from_measurement(self):
        target = self.folder / 'clip.webm'
        self.folder.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'not really a webm container')
        stat = target.stat()
        payload = {'version': 1, 'file_name': target.name, 'size_bytes': stat.st_size,
                   'mtime_ns': stat.st_mtime_ns,
                   'recording': {'title': 'Клип', 'artist': 'Автор', 'source': 'youtube'}}
        sidecar_path(target).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        with patch('backend.metadata.probe_duration_ms', return_value=1234):
            read = read_track_metadata(str(target))
        self.assertEqual(read['title'], 'Клип')
        self.assertEqual(read['artist'], 'Автор')
        self.assertEqual(read['duration_ms'], 1234)
        self.assertIsNone(read['cover_url'])


if __name__ == '__main__':
    unittest.main()
