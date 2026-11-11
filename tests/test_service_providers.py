"""Provider contract: stable identifiers, bounded worker calls, verified downloads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

from backend.provider_worker import validate_track_url
from service.errors import LimitError, ProviderError
from service.providers import (ProviderTrack, WorkerProvider, stable_identifier,
                               track_url)
from service.worker import SubprocessTransport, WorkerError


def wav_file(path, frames=2400, rate=8000):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x00\x00' * frames)
    return path


class FakeTransport:
    def __init__(self, results=None, error=None):
        self.requests = []
        self.results = list(results or [])
        self.error = error

    def request(self, payload, timeout=None):
        self.requests.append((payload, timeout))
        if self.error is not None:
            raise self.error
        return self.results.pop(0) if self.results else {}


class IdentifierTests(unittest.TestCase):
    def test_youtube_links_yield_the_bare_video_identifier(self):
        expected = 'dQw4w9WgXcQ'
        for url in ('https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                    'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42',
                    'https://youtu.be/dQw4w9WgXcQ',
                    'https://music.youtube.com/watch?v=dQw4w9WgXcQ',
                    'https://www.youtube.com/shorts/dQw4w9WgXcQ'):
            self.assertEqual(stable_identifier('youtube', url), expected)
        self.assertEqual(track_url('youtube', expected),
                         'https://www.youtube.com/watch?v=' + expected)

    def test_other_providers_keep_their_own_identity_rules(self):
        spotify = stable_identifier('spotify', 'https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT')
        self.assertEqual(spotify, '4cOdK2wGLETKBW3PvgPWqT')
        self.assertEqual(track_url('spotify', spotify),
                         'https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT')
        soundcloud = stable_identifier('soundcloud', 'https://soundcloud.com/artist/song')
        self.assertEqual(soundcloud, 'artist/song')
        self.assertEqual(track_url('soundcloud', soundcloud),
                         'https://soundcloud.com/artist/song')

    def test_channels_playlists_and_plain_http_are_rejected(self):
        rejected = (
            'https://www.youtube.com/playlist?list=PL123',
            'https://www.youtube.com/@channel',
            'https://www.youtube.com/watch?v=short',
            'http://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://music.apple.com/track/1',
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ&v=abcdefghijk',
        )
        for url in rejected:
            with self.assertRaises(ProviderError):
                stable_identifier('youtube', url)
        for url in ('https://open.spotify.com/album/abc', 'https://soundcloud.com/artist/sets/x'):
            with self.assertRaises(ProviderError):
                stable_identifier('spotify' if 'spotify' in url else 'soundcloud', url)

    def test_identity_rules_match_the_existing_player_worker(self):
        """One repository keeps one URL rule: this test catches drift."""
        accepted = ('https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                    'https://youtu.be/dQw4w9WgXcQ',
                    'https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT',
                    'https://soundcloud.com/artist/song')
        for url in accepted:
            provider = ('youtube' if 'youtu' in url else
                        'spotify' if 'spotify' in url else 'soundcloud')
            canonical = validate_track_url(url, provider)
            self.assertEqual(track_url(provider, stable_identifier(provider, url)), canonical)
        rejected = ('https://www.youtube.com/playlist?list=PL123',
                    'https://open.spotify.com/album/abc',
                    'https://soundcloud.com/artist/sets/x',
                    'https://example.com/track/1')
        for url in rejected:
            provider = ('youtube' if 'youtu' in url else
                        'spotify' if 'spotify' in url else
                        'soundcloud' if 'soundcloud' in url else 'youtube')
            with self.assertRaises(ValueError):
                validate_track_url(url, provider)
            with self.assertRaises(ProviderError):
                stable_identifier(provider, url)


class SearchTests(unittest.TestCase):
    def provider(self, results, **kwargs):
        transport = FakeTransport(results)
        return WorkerProvider(transport, 'youtube', **kwargs), transport

    def test_metadata_rows_become_tracks_with_stable_identifiers(self):
        rows = [
            {'provider': 'youtube', 'kind': 'track', 'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
             'title': ' Song ', 'artist': 'Artist', 'duration_ms': 213000,
             'cover_url': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg'},
            {'provider': 'youtube', 'kind': 'track', 'url': 'https://youtu.be/aaaaaaaaaaa',
             'title': 'Second', 'artist': '', 'duration_ms': None, 'cover_url': 'http://insecure/x.jpg'},
        ]
        provider, transport = self.provider([{'tracks': rows, 'warnings': ['youtube: нет сети']}])
        found = provider.search('  song  ', limit=5)
        self.assertEqual(transport.requests[0][0],
                         {'operation': 'search', 'query': 'song', 'provider': 'youtube'})
        self.assertEqual([track.provider_id for track in found.tracks],
                         ['dQw4w9WgXcQ', 'aaaaaaaaaaa'])
        first = found.tracks[0]
        self.assertEqual(first.title, 'Song')
        self.assertEqual(first.duration_ms, 213000)
        self.assertIsNone(found.tracks[1].cover_url)
        self.assertEqual(found.warnings, ('youtube: нет сети',))

    def test_unusable_rows_are_dropped_instead_of_becoming_tracks(self):
        rows = [
            {'kind': 'playlist', 'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'title': 'x'},
            {'kind': 'track', 'url': 'https://www.youtube.com/playlist?list=PL1', 'title': 'x'},
            {'kind': 'track', 'url': 'https://youtu.be/dQw4w9WgXcQ', 'title': '   '},
            {'kind': 'track', 'url': 'https://youtu.be/aaaaaaaaaaa', 'title': 'Good'},
            {'kind': 'track', 'url': 'https://youtu.be/aaaaaaaaaaa', 'title': 'Duplicate'},
        ]
        provider, _ = self.provider([{'tracks': rows}])
        found = provider.search('query', limit=10)
        self.assertEqual([track.title for track in found.tracks], ['Good'])

    def test_limit_is_a_visible_budget(self):
        rows = [{'kind': 'track', 'url': 'https://youtu.be/' + str(index) * 11, 'title': 'T'}
                for index in range(10)]
        provider, _ = self.provider([{'tracks': rows}])
        self.assertEqual(len(provider.search('query', limit=3).tracks), 3)
        for limit in (0, 21, '5', None, True):
            with self.assertRaises(ProviderError):
                provider.search('query', limit=limit)

    def test_bad_queries_are_refused_before_the_worker_runs(self):
        provider, transport = self.provider([{'tracks': []}])
        for query in ('', '   ', 'x' * 201, None, 42, 'http://example.com/watch?v=1'):
            with self.assertRaises(ProviderError):
                provider.search(query)
        self.assertEqual(transport.requests, [])
        provider.search('https://youtu.be/dQw4w9WgXcQ')
        self.assertEqual(transport.requests[0][0]['query'],
                         'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

    def test_worker_refusal_is_reported_without_urls(self):
        provider, _ = self.provider([{'error': 'сеть недоступна: https://signed.example/x?token=1'}])
        with self.assertRaises(ProviderError) as caught:
            provider.search('query')
        self.assertEqual(caught.exception.code, 'provider_refused')
        self.assertNotIn('signed.example', str(caught.exception))
        provider, _ = self.provider([{'tracks': 'not a list'}])
        with self.assertRaises(ProviderError):
            provider.search('query')


class ResolveTests(unittest.TestCase):
    def test_youtube_stream_is_ephemeral_and_host_checked(self):
        transport = FakeTransport([{'stream_url': 'https://rr1---sn.googlevideo.com/videoplayback?x=1',
                                    'http_headers': {'User-Agent': 'ua', 'Cookie': 'secret',
                                                     'Referer': 'https://youtube.com/'}}])
        provider = WorkerProvider(transport, 'youtube')
        track = ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song')
        stream = provider.resolve(track)
        self.assertTrue(stream.url.startswith('https://rr1---sn.googlevideo.com/'))
        self.assertEqual(stream.headers, {'User-Agent': 'ua', 'Referer': 'https://youtube.com/'})

    def test_foreign_host_and_other_providers_are_refused(self):
        transport = FakeTransport([{'stream_url': 'https://evil.example/audio.mp3'}])
        provider = WorkerProvider(transport, 'youtube')
        with self.assertRaises(ProviderError):
            provider.resolve(ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song'))
        spotify = WorkerProvider(FakeTransport(), 'spotify')
        with self.assertRaises(ProviderError):
            spotify.resolve(ProviderTrack('spotify', '4cOdK2wGLETKBW3PvgPWqT', 'Song'))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / 'work'

    def writer(self, name='song.wav', payload=None, outside=False):
        target = (self.root if outside else None)
        def request(payload_request, timeout=None):
            destination = self.folder if not outside else self.root
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / name
            path.write_bytes(payload if payload is not None else b'audio')
            return {'file': str(path), 'audio_format': payload_request['audio_format']}
        return request

    def provider(self, request, **kwargs):
        transport = FakeTransport()
        transport.request = request
        return WorkerProvider(transport, 'youtube', **kwargs)

    def test_download_returns_one_file_inside_the_folder(self):
        provider = self.provider(self.writer())
        path = provider.download(ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song'), self.folder)
        self.assertEqual(path, (self.folder / 'song.wav').resolve())

    def test_download_refuses_files_outside_the_folder(self):
        provider = self.provider(self.writer(name='outside.wav', outside=True))
        with self.assertRaises(ProviderError) as caught:
            provider.download(ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song'), self.folder)
        self.assertEqual(caught.exception.code, 'download_outside_folder')

    def test_download_refuses_empty_large_and_unknown_files(self):
        cases = ((b'', 'song.wav', 'download_empty_file', {}),)
        for payload, name, code, options in cases:
            provider = self.provider(self.writer(name=name, payload=payload), **options)
            with self.assertRaises(ProviderError) as caught:
                provider.download(ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song'), self.folder)
            self.assertEqual(caught.exception.code, code)
        provider = self.provider(self.writer(payload=b'x' * 100), max_file_bytes=10)
        with self.assertRaises(LimitError):
            provider.download(ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song'), self.folder)
        provider = self.provider(self.writer(name='song.exe'))
        with self.assertRaises(ProviderError) as caught:
            provider.download(ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song'), self.folder)
        self.assertEqual(caught.exception.code, 'unsupported_container')

    def test_download_reports_worker_failure(self):
        provider = self.provider(lambda payload, timeout=None: {'error': 'источник недоступен'})
        with self.assertRaises(ProviderError) as caught:
            provider.download(ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song'), self.folder)
        self.assertEqual(caught.exception.code, 'provider_refused')

    def test_unknown_format_and_foreign_track_are_refused(self):
        provider = self.provider(self.writer())
        with self.assertRaises(ProviderError):
            provider.download(ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song'), self.folder,
                              audio_format='flac')
        with self.assertRaises(ProviderError):
            provider.download(ProviderTrack('spotify', '4cOdK2wGLETKBW3PvgPWqT', 'Song'), self.folder)


ECHO = "import json,sys; print(json.dumps({'echo': json.load(sys.stdin)['value']}))"


class TransportTests(unittest.TestCase):
    def transport(self, script=ECHO, **kwargs):
        return SubprocessTransport(sys.executable, ['-c', script], os.getcwd(), **kwargs)

    def test_request_and_response_roundtrip(self):
        result = self.transport(timeout=15).request({'value': 'привет'})
        self.assertEqual(result, {'echo': 'привет'})

    def test_timeout_kills_the_worker(self):
        transport = self.transport('import time; time.sleep(10)', timeout=0.5)
        with self.assertRaises(WorkerError) as caught:
            transport.request({'value': 'x'})
        self.assertEqual(caught.exception.code, 'worker_timeout')

    def test_bad_and_oversized_responses_are_refused(self):
        with self.assertRaises(WorkerError) as caught:
            self.transport("print('not json')", timeout=15).request({'value': 'x'})
        self.assertEqual(caught.exception.code, 'worker_bad_response')
        with self.assertRaises(WorkerError) as caught:
            self.transport("print('x' * 4000)", timeout=15,
                           max_response_bytes=1024).request({'value': 'x'})
        self.assertEqual(caught.exception.code, 'worker_response_too_large')

    def test_missing_interpreter_is_reported(self):
        transport = SubprocessTransport('definitely-not-a-python', ['-c', 'pass'], os.getcwd())
        with self.assertRaises(WorkerError) as caught:
            transport.request({'value': 'x'})
        self.assertEqual(caught.exception.code, 'worker_unavailable')

    def test_request_size_is_bounded(self):
        with self.assertRaises(WorkerError) as caught:
            self.transport(timeout=15).request({'value': 'x' * 20000})
        self.assertEqual(caught.exception.code, 'worker_request_too_large')

    def test_environment_is_allowlisted(self):
        script = ("import json,os; print(json.dumps({'secret': 'SLYNOR_TEST_SECRET' in os.environ,"
                  " 'proxy': os.environ.get('OWNPLAYER_PROXY'),"
                  " 'system_proxy': os.environ.get('HTTP_PROXY')}))")
        with patch.dict(os.environ, {'SLYNOR_TEST_SECRET': 'x', 'HTTP_PROXY': 'http://system:1'}):
            result = self.transport(script, timeout=15,
                                    environment={'OWNPLAYER_PROXY': 'http://manual:2'}).request({})
        self.assertEqual(result, {'secret': False, 'proxy': 'http://manual:2', 'system_proxy': None})


if __name__ == '__main__':
    unittest.main()
