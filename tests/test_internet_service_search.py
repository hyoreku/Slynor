"""Music Service search and download bridge inside the desktop online library."""
from hashlib import sha256
from pathlib import Path
import tempfile
import time
import unittest
import wave

from backend.download_metadata import read_download_metadata
from backend.internet_music import InternetLibrary
from backend.service_client import ServiceError, ServiceUnavailable

CANDIDATE = 'a' * 32
ASSET = 'b' * 32


def wav_bytes(frames=2400, rate=8000):
    import io
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x00\x00' * frames)
    return buffer.getvalue()


class FakeService:
    def __init__(self, items=None, error=None, payload=None):
        self.items = list(items or [])
        self.error = error
        self.payload = payload if payload is not None else wav_bytes()
        self.downloaded = []

    def search(self, query, limit=10, timeout=90):
        if self.error is not None:
            raise self.error
        return self.items, ['Music Service: проверено']

    def download(self, candidate_id, audio_format='original', timeout=600):
        self.downloaded.append((candidate_id, audio_format))
        return {'asset_id': ASSET, 'sha256': sha256(self.payload).hexdigest(),
                'container': 'wav', 'size_bytes': len(self.payload),
                'path': 'assets/bb/' + ASSET + '.wav'}

    def asset_content(self, asset_id, destination, max_bytes=200 * 1024 * 1024):
        Path(destination).write_bytes(self.payload)
        return len(self.payload)


def item(**overrides):
    value = {'candidate_id': CANDIDATE, 'provider': 'youtube', 'provider_id': 'dQw4w9WgXcQ',
             'title': 'Группа крови', 'artist': 'Кино', 'duration_ms': 276000,
             'public_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
             'cover_url': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg'}
    value.update(overrides)
    return value


class ServiceSearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.service = FakeService([item()])
        self.library = InternetLibrary(self.root, service=self.service)
        self.addCleanup(self.library.close)

    def wait(self, job_id, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.library.job(job_id)
            if job['status'] in ('done', 'failed'):
                return job
            time.sleep(0.02)
        self.fail('job did not finish')

    def test_service_search_maps_stable_candidates(self):
        job = self.library.start_search('Кино Группа крови', 'service')
        result = self.wait(job['id'])
        self.assertEqual(result['status'], 'done', result.get('error'))
        self.assertEqual(result['warnings'], ['Music Service: проверено'])
        track = result['tracks'][0]
        self.assertEqual(track['path'], 'online:service:' + CANDIDATE)
        self.assertEqual(track['source'], 'youtube')
        self.assertEqual(track['title'], 'Группа крови')
        self.assertEqual(track['access'], 'prepare')
        self.assertEqual(track['via'], 'service')
        self.assertEqual(track['permalink_url'], 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

    def test_malformed_and_unavailable_service_rows_are_ignored_or_reported(self):
        self.service.items = [item(candidate_id='not-hex'), item(provider='vk'),
                              item(title=''), item(candidate_id='c' * 32, provider='soundcloud',
                                                    title='Ok', public_url='http://insecure')]
        result = self.wait(self.library.start_search('query', 'service')['id'])
        self.assertEqual(result['status'], 'done')
        self.assertEqual([track['title'] for track in result['tracks']], ['Ok'])
        self.assertIsNone(result['tracks'][0]['permalink_url'])
        self.service.error = ServiceUnavailable('Music Service не запущен.')
        failed = self.wait(self.library.start_search('query', 'service')['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('недоступен', failed['error'])
        self.service.error = ServiceError('Задание Music Service не выполнено.')
        failed = self.wait(self.library.start_search('query', 'service')['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('не выполнено', failed['error'])

    def test_service_download_copies_verified_file_into_downloads(self):
        found = self.wait(self.library.start_search('Кино', 'service')['id'])
        ref = found['tracks'][0]['path']
        job = self.library.start_download(ref, True, 'original')
        result = self.wait(job['id'], timeout=20)
        self.assertEqual(result['status'], 'done', result.get('error'))
        path = Path(result['path'])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, Path(self.root) / 'Downloads')
        self.assertTrue(path.read_bytes().startswith(b'RIFF'))
        saved = read_download_metadata(path)
        self.assertEqual(saved['title'], 'Группа крови')
        self.assertGreater(saved['duration_ms'], 100)
        self.assertEqual(self.service.downloaded, [(CANDIDATE, 'original')])
        self.assertTrue(path.with_name(path.name + '.slynor.json').is_file())

    def test_service_download_requires_fresh_search_and_confirmation(self):
        with self.assertRaises(Exception):
            self.library.start_download('online:service:' + CANDIDATE, True)
        found = self.wait(self.library.start_search('Кино', 'service')['id'])
        with self.assertRaises(Exception):
            self.library.start_download(found['tracks'][0]['path'], False)


if __name__ == '__main__':
    unittest.main()
