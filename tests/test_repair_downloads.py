"""Repair of old download companions: duration and YouTube covers, never audio."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch

from PIL import Image

from backend.repair_downloads import fetch_youtube_cover, repair, youtube_cover_url


def wav_file(path):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\x00\x00' * 800)
    return path


def jpeg_bytes(size=24):
    from io import BytesIO
    buffer = BytesIO()
    Image.new('RGB', (size, size), '#3a2a5a').save(buffer, format='JPEG', quality=85)
    return buffer.getvalue()


class RepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.folder = self.root / 'Downloads'
        self.folder.mkdir()
        self.audio = wav_file(self.folder / 'track.wav')
        self.sidecar = self.folder / 'track.wav.slynor.json'

    def write_sidecar(self, recording):
        stat = self.audio.stat()
        payload = {'version': 1, 'file_name': self.audio.name, 'size_bytes': stat.st_size,
                   'mtime_ns': stat.st_mtime_ns, 'recording': recording}
        self.sidecar.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return payload

    def test_youtube_cover_url_is_strict(self):
        self.assertEqual(youtube_cover_url('https://www.youtube.com/watch?v=I-esFAcafYo'),
                         'https://i.ytimg.com/vi/I-esFAcafYo/hqdefault.jpg')
        self.assertEqual(youtube_cover_url('https://youtu.be/I-esFAcafYo'),
                         'https://i.ytimg.com/vi/I-esFAcafYo/hqdefault.jpg')
        for value in ('http://www.youtube.com/watch?v=I-esFAcafYo',
                      'https://example.com/watch?v=I-esFAcafYo',
                      'https://www.youtube.com/watch?v=short',
                      'https://www.youtube.com/playlist?list=PL1'):
            self.assertIsNone(youtube_cover_url(value))

    def test_dry_run_does_not_write(self):
        self.write_sidecar({'title': 'Track', 'artist': 'Artist'})
        before = self.sidecar.read_bytes()
        report = repair(self.folder)
        self.assertEqual(report['scanned'], 1)
        self.assertEqual(report['updated'], 0)
        self.assertEqual(report['files'][0]['changes'], ['duration_ms'])
        self.assertFalse(report['files'][0]['applied'])
        self.assertEqual(self.sidecar.read_bytes(), before)

    def test_cover_fetch_rejects_foreign_or_malformed_urls(self):
        for value in ('https://example.com/vi/I-esFAcafYo/hqdefault.jpg',
                      'http://i.ytimg.com/vi/I-esFAcafYo/hqdefault.jpg',
                      'https://i.ytimg.com/vi/short/hqdefault.jpg'):
            self.assertIsNone(fetch_youtube_cover(value))

    def test_dry_run_does_not_fetch_or_store_cover(self):
        self.write_sidecar({'title': 'Clip',
                            'permalink_url': 'https://www.youtube.com/watch?v=I-esFAcafYo'})
        calls = []
        report = repair(self.folder, fetch_cover=lambda url: calls.append(url) or jpeg_bytes())
        self.assertEqual(calls, [])
        self.assertTrue(report['files'][0]['would_fetch_cover'])
        self.assertEqual(list((self.root / 'OwnPlayerMusic' / 'covers' / 'tracks').glob('*')), [])

    def test_apply_adds_duration_and_keeps_other_fields(self):
        self.write_sidecar({'title': 'Track', 'artist': 'Artist', 'source': 'youtube'})
        report = repair(self.folder, apply=True)
        self.assertEqual(report['updated'], 1)
        saved = json.loads(self.sidecar.read_text(encoding='utf-8'))['recording']
        self.assertGreater(saved['duration_ms'], 0)
        self.assertEqual((saved['title'], saved['artist'], saved['source']),
                         ('Track', 'Artist', 'youtube'))
        self.assertTrue(Path(str(self.sidecar) + '.bak').is_file())
        # Idempotent: a second run finds nothing to add.
        self.assertEqual(repair(self.folder, apply=True)['files'], [])

    def test_cover_is_fetched_and_cached_for_youtube(self):
        self.write_sidecar({'title': 'Clip', 'artist': 'Artist',
                            'permalink_url': 'https://www.youtube.com/watch?v=I-esFAcafYo'})
        requested = []
        report = repair(self.folder, apply=True,
                        fetch_cover=lambda url: requested.append(url) or jpeg_bytes())
        self.assertEqual(requested, ['https://i.ytimg.com/vi/I-esFAcafYo/hqdefault.jpg'])
        saved = json.loads(self.sidecar.read_text(encoding='utf-8'))['recording']
        self.assertRegex(saved['cover_file'], r'^[0-9a-f]{64}\.jpg$')
        self.assertTrue((self.root / 'OwnPlayerMusic' / 'covers' / 'tracks' / saved['cover_file']).is_file())
        self.assertIn('cover_file', report['files'][0]['changes'])

    def test_stale_sidecar_is_skipped(self):
        self.write_sidecar({'title': 'Track'})
        stat = self.audio.stat()
        os.utime(self.audio, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        report = repair(self.folder, apply=True)
        self.assertEqual(report['scanned'], 1)
        self.assertEqual(report['skipped'], 1)
        self.assertEqual(report['files'], [])


if __name__ == '__main__':
    unittest.main()
