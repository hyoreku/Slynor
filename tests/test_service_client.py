"""Loopback Music Service client and runtime supervisor, without a live service."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from backend.service_client import ServiceClient, ServiceError, ServiceUnavailable
from backend.service_runtime import ServiceRuntime


def client_with(handler):
    transport = httpx.MockTransport(handler)
    raw = httpx.Client(base_url='http://127.0.0.1:8766', transport=transport, trust_env=False)
    return ServiceClient(client=raw)


class ServiceClientTests(unittest.TestCase):
    def test_search_polls_jobs_and_returns_items_and_warnings(self):
        calls = []
        state = {'polls': 0}
        item = {'candidate_id': 'b' * 32, 'provider': 'youtube', 'provider_id': 'dQw4w9WgXcQ',
                'title': 'Song', 'artist': 'Artist', 'duration_ms': 200000,
                'public_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'cover_url': None}
        def handler(request):
            calls.append((request.method, request.url.path))
            if request.url.path == '/health':
                return httpx.Response(200, json={'status': 'ok'})
            if request.url.path == '/jobs' and request.method == 'POST':
                body = json.loads(request.content)
                self.assertEqual(body['kind'], 'search')
                self.assertEqual(body['payload'], {'query': 'song', 'limit': 5})
                return httpx.Response(201, json={'id': 'a' * 32, 'status': 'queued'})
            state['polls'] += 1
            if state['polls'] == 1:
                return httpx.Response(200, json={'id': 'a' * 32, 'status': 'running'})
            return httpx.Response(200, json={'id': 'a' * 32, 'status': 'done',
                                             'result': {'items': [item], 'warnings': ['источник: сеть']}})
        client = client_with(handler)
        self.addCleanup(client.close)
        items, warnings = client.search('song', limit=5, timeout=10)
        self.assertEqual(items[0]['candidate_id'], item['candidate_id'])
        self.assertEqual(warnings, ['источник: сеть'])
        self.assertEqual(calls.count(('GET', '/jobs/' + 'a' * 32)), 2)

    def test_download_and_asset_content_copy_the_measured_file(self):
        payload = b'RIFF....WAVEfmt'
        def handler(request):
            if request.url.path == '/jobs' and request.method == 'POST':
                body = json.loads(request.content)
                self.assertEqual(body['kind'], 'download')
                self.assertEqual(body['payload']['candidate_id'], 'c' * 32)
                return httpx.Response(201, json={'id': 'd' * 32})
            if request.url.path == '/jobs/' + 'd' * 32:
                return httpx.Response(200, json={'id': 'd' * 32, 'status': 'done',
                    'result': {'asset_id': 'e' * 32, 'sha256': 'f' * 64, 'container': 'wav',
                               'size_bytes': len(payload), 'path': 'assets/ff/x.wav'}})
            if request.url.path == '/assets/' + 'e' * 32 + '/content':
                return httpx.Response(200, content=payload,
                                      headers={'content-type': 'application/octet-stream'})
            return httpx.Response(404)
        client = client_with(handler)
        self.addCleanup(client.close)
        asset = client.download('c' * 32, timeout=10)
        self.assertEqual(asset['asset_id'], 'e' * 32)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'file.wav'
            size = client.asset_content('e' * 32, target)
            self.assertEqual(size, len(payload))
            self.assertEqual(target.read_bytes(), payload)
            small = client_with(lambda request: httpx.Response(200, content=b'x' * 5000))
            self.addCleanup(small.close)
            limited = Path(tmp) / 'limited.wav'
            with self.assertRaises(ServiceError):
                small.asset_content('e' * 32, limited, max_bytes=100)
            self.assertFalse(limited.exists())

    def test_library_and_playlist_calls_use_service_routes(self):
        state = {'library': [], 'playlists': []}
        calls = []
        def handler(request):
            path, method = request.url.path, request.method
            calls.append((method, path))
            body = json.loads(request.content) if request.content else {}
            if path == '/library' and method == 'GET':
                return httpx.Response(200, json={'tracks': state['library'], 'revision': 1})
            if path == '/library' and method == 'PUT':
                state['library'] = body['tracks']
                return httpx.Response(200, json={'tracks': state['library'], 'revision': 2})
            if path == '/library/revision':
                return httpx.Response(200, json={'revision': 2, 'updated_at': 'x'})
            if path == '/playlists' and method == 'GET':
                return httpx.Response(200, json={'playlists': state['playlists']})
            if path == '/playlists' and method == 'POST':
                state['playlists'].append({'id': 'p-1', 'name': body['name'], 'tracks': []})
                return httpx.Response(201, json={'playlists': state['playlists']})
            if path == '/playlists/tracks':
                state['playlists'][0]['tracks'].append(body['path'])
                return httpx.Response(200, json={'playlists': state['playlists']})
            if path == '/playlists/p-1' and method == 'PATCH':
                state['playlists'][0]['name'] = body['name']
                return httpx.Response(200, json={'playlists': state['playlists']})
            if path == '/playlists/p-1/cover':
                state['playlists'][0]['cover'] = body['cover']
                return httpx.Response(200, json={'playlists': state['playlists']})
            if path == '/playlists/p-1/tracks/remove':
                state['playlists'][0]['tracks'] = []
                return httpx.Response(200, json={'playlists': state['playlists']})
            if path == '/playlists/p-1' and method == 'DELETE':
                state['playlists'] = []
                return httpx.Response(200, json={'playlists': []})
            return httpx.Response(404)
        client = client_with(handler)
        self.addCleanup(client.close)
        self.assertEqual(client.library()['tracks'], [])
        self.assertEqual(client.replace_library(['C:/a.mp3']), ['C:/a.mp3'])
        self.assertEqual(client.library_revision()['revision'], 2)
        self.assertEqual(client.playlists(), [])
        self.assertEqual(client.create_playlist('One')[0]['name'], 'One')
        self.assertEqual(client.add_track_to_playlists(['p-1'], 'C:/a.mp3')[0]['tracks'], ['C:/a.mp3'])
        self.assertEqual(client.rename_playlist('p-1', 'First')[0]['name'], 'First')
        self.assertEqual(client.set_playlist_cover('p-1', 'a' * 32 + '.jpg')[0]['cover'], 'a' * 32 + '.jpg')
        self.assertEqual(client.remove_track_from_playlist('p-1', 'C:/a.mp3')[0]['tracks'], [])
        self.assertEqual(client.delete_playlist('p-1'), [])
        self.assertEqual(calls[0], ('GET', '/library'))

    def test_unavailable_failed_and_invalid_requests_are_reported(self):
        def refused(request):
            raise httpx.ConnectError('refused', request=request)
        unavailable = client_with(refused)
        self.addCleanup(unavailable.close)
        self.assertFalse(unavailable.health())
        with self.assertRaises(ServiceUnavailable):
            unavailable.search('song', timeout=5)
        with self.assertRaises(ServiceUnavailable):
            unavailable.asset_content('e' * 32, Path('unused.wav'))
        def failed(request):
            if request.url.path == '/jobs' and request.method == 'POST':
                return httpx.Response(201, json={'id': 'a' * 32})
            return httpx.Response(200, json={'id': 'a' * 32, 'status': 'failed', 'error': 'ProviderError: нет источника'})
        broken = client_with(failed)
        self.addCleanup(broken.close)
        with self.assertRaises(ServiceError) as caught:
            broken.search('song', timeout=5)
        self.assertIn('нет источника', str(caught.exception))
        for query in ('', 'x' * 201):
            with self.assertRaises(ServiceError):
                broken.search(query, timeout=5)
        with self.assertRaises(ServiceError):
            broken.download('not-hex', timeout=5)
        with self.assertRaises(ServiceError):
            broken.download('a' * 32, audio_format='flac', timeout=5)
        with self.assertRaises(ServiceError):
            ServiceClient('https://example.invalid')


class FakeRuntimeClient:
    base_url = 'http://127.0.0.1:8766'
    def __init__(self, healthy=False):
        self.healthy = healthy
    def health(self, timeout=1.5):
        return self.healthy


class RuntimeTests(unittest.TestCase):
    def test_status_reports_health_and_error(self):
        runtime = ServiceRuntime(FakeRuntimeClient(healthy=True))
        status = runtime.status(refresh=True)
        self.assertTrue(status['available'])
        self.assertIsNone(status['error'])
        offline = ServiceRuntime(FakeRuntimeClient(healthy=False))
        status = offline.status(refresh=True)
        self.assertFalse(status['available'])
        self.assertIn('не запущен', status['error'])

    def test_frozen_build_does_not_spawn_python(self):
        runtime = ServiceRuntime(FakeRuntimeClient(healthy=False))
        with patch('backend.service_runtime.sys.frozen', True, create=True):
            self.assertFalse(runtime.ensure_started(timeout=1))
        self.assertIn('недоступен', runtime.error)


if __name__ == '__main__':
    unittest.main()
