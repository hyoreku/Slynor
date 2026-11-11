"""Concurrency/format regressions. No live services or actual Windows audio."""
import base64
import json
import re
import tempfile
import time
import types
import unittest
import wave
from pathlib import Path
from threading import Event
from unittest.mock import patch

from test_regressions import AudioEngine
from backend.catalog_music import CatalogueLibrary
from backend.internet_music import ProviderError
from backend.provider_worker import download
from backend.audio_validation import validate_audio_file
from backend.main import DownloadRequest, MetadataRequest, QueuePlayRequest


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lib = CatalogueLibrary(self.temp.name)
        self.lib.network.config['mode'] = 'direct'
        self.ref = 'online:catalog:' + 'a' * 32
        self.lib.catalogue[self.ref] = self.row_track()
        self.release = Event()

    def tearDown(self):
        self.release.set()
        self.lib.close()
        self.temp.cleanup()

    def row_track(self):
        return {'path': self.ref, 'title': 'Song', 'artist': 'Artist', 'source': 'youtube',
                'permalink_url': 'https://www.youtube.com/watch?v=abcdefghijk'}

    def row(self, provider='youtube'):
        urls = {'youtube': 'https://www.youtube.com/watch?v=abcdefghijk',
                'spotify': 'https://open.spotify.com/track/abc',
                'soundcloud': 'https://soundcloud.com/artist/song'}
        return {'provider': provider, 'url': urls[provider], 'title': 'Song', 'artist': 'Artist'}

    def wait(self, key, predicate=None):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.lib.job(key)
            if predicate(job) if predicate else job['status'] not in ('queued', 'running'):
                return job
            time.sleep(.005)
        self.fail('Job deadline exceeded')

    def test_partial_results_arrive_before_slow_provider(self):
        entered = Event()
        def run(key, payload, timeout):
            if payload['provider'] == 'spotify':
                entered.set()
                self.release.wait(2)
            return {'tracks': [self.row(payload['provider'])]}
        with patch.object(self.lib, '_run_provider', side_effect=run):
            key = self.lib.start_search('track')['id']
            self.assertTrue(entered.wait(1))
            partial = self.wait(key, lambda j: bool(j['tracks']))
            self.assertEqual(partial['status'], 'running')
            self.assertIn('youtube', [t['source'] for t in partial['tracks']])
            self.release.set()
            final = self.wait(key)
            self.assertEqual(len(final['tracks']), 3)

    def test_cache_avoids_provider_calls_and_reregisters_tracks(self):
        with patch.object(self.lib, '_run_provider', return_value={'tracks': [self.row()]}) as run:
            first = self.wait(self.lib.start_search('Some Song')['id'])
            self.assertFalse(first['cache_hit'])
            self.lib.catalogue.clear()
            second = self.wait(self.lib.start_search('  some   SONG  ')['id'])
            self.assertTrue(second['cache_hit'])
            self.assertEqual(run.call_count, 3)
            self.assertEqual(self.lib.metadata(second['tracks'][0]['path'])['title'], 'Song')

    def test_cache_expiry_and_network_preference(self):
        with patch.object(self.lib, '_run_provider', return_value={'tracks': [self.row()]}) as run:
            self.wait(self.lib.start_search('same')['id'])
            with self.lib.lock:
                for item in self.lib.search_cache.values():
                    item['until'] = 0
            self.wait(self.lib.start_search('same')['id'])
            self.lib.network.config['search_first'] = 'youtube'
            self.wait(self.lib.start_search('same')['id'])
            self.assertEqual(run.call_count, 9)

    def test_explicit_url_only_queries_its_provider(self):
        with patch.object(self.lib, '_run_provider', return_value={'tracks': [self.row()]}) as run:
            self.wait(self.lib.start_search(self.row()['url'])['id'])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[1]['provider'], 'youtube')

    def test_cancelled_or_evicted_job_is_safe(self):
        key = self.lib._new_job('search')
        self.lib.cancel_job(key)
        self.assertTrue(self.lib._cancelled(key))
        with self.lib.job_lock:
            self.lib.jobs.pop(key)
        self.assertTrue(self.lib._cancelled(key))

    def test_stream_resolution_does_not_wait_for_download_queue(self):
        entered = Event()
        def occupy():
            entered.set()
            self.release.wait(2)
        self.lib.download_pool.submit(occupy)
        self.assertTrue(entered.wait(1))
        def prepare(key, track):
            self.lib._patch(key, status='done', ref=track['path'])
        with patch.object(self.lib, '_prepare_stream', side_effect=prepare):
            job = self.wait(self.lib.start_stream(self.ref)['id'])
        self.assertEqual(job['status'], 'done')
        self.assertFalse(self.release.is_set())

    def test_original_wav_keeps_extension_and_content(self):
        def run(key, payload, timeout):
            self.assertEqual(payload['audio_format'], 'original')
            path = Path(payload['folder']) / 'source.wav'
            with wave.open(str(path), 'wb') as audio:
                audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\0\0' * 800)
            return {'file': str(path)}
        with patch.object(self.lib, '_run_provider', side_effect=run):
            job = self.wait(self.lib.start_download(self.ref, True)['id'])
        self.assertEqual(job['status'], 'done')
        self.assertEqual(Path(job['path']).suffix, '.wav')
        validate_audio_file(job['path'])

    def test_original_does_not_satisfy_mp3_request(self):
        folder = Path(self.temp.name) / 'Downloads'
        folder.mkdir()
        path = folder / 'existing.opus'
        path.write_bytes(b'cached-fixture')
        self.lib.downloaded[self.ref] = str(path)
        with patch.object(self.lib, '_run_provider', side_effect=ProviderError('MP3 requested')):
            job = self.wait(self.lib.start_download(self.ref, True, 'mp3')['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertIn('MP3 requested', job['error'])

    def test_mp3_cache_separate_and_persisted(self):
        encoded = re.search(r"base64.b64decode\('([^']+)'\)", Path(__file__).with_name('test_stage13.py').read_text(encoding='utf-8')).group(1)
        def run(key, payload, timeout):
            self.assertEqual(payload['audio_format'], 'mp3')
            path = Path(payload['folder']) / 'source.mp3'
            path.write_bytes(base64.b64decode(encoded))
            return {'file': str(path)}
        with patch.object(self.lib, '_run_provider', side_effect=run):
            job = self.wait(self.lib.start_download(self.ref, True, 'mp3')['id'])
        self.assertEqual(job['status'], 'done')
        self.assertEqual(self.lib.downloaded[self.ref + '|mp3'], job['path'])
        other = CatalogueLibrary(self.temp.name)
        try:
            self.assertEqual(other.downloaded[self.ref + '|mp3'], job['path'])
        finally:
            other.close()

    def test_invalid_audio_rejected(self):
        def run(key, payload, timeout):
            path = Path(payload['folder']) / 'fake.m4a'
            path.write_bytes(b'<html>not music</html>')
            return {'file': str(path)}
        with patch.object(self.lib, '_run_provider', side_effect=run):
            job = self.wait(self.lib.start_download(self.ref, True)['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertFalse(list((Path(self.temp.name) / 'Downloads').glob('*.m4a')))

    def test_unknown_format_and_unconfirmed_download_rejected(self):
        for fmt in ('exe', '../mp3', ''):
            with self.assertRaises(ProviderError):
                self.lib.start_download(self.ref, True, fmt)
        with self.assertRaises(ProviderError):
            self.lib.start_download(self.ref, False)


class WorkerFormatTests(unittest.TestCase):
    def extract(self, provider, fmt='original'):
        seen = {}
        module = types.ModuleType('yt_dlp')
        with tempfile.TemporaryDirectory() as folder:
            class Downloader:
                def __init__(self, opts): seen.update(opts)
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def download(self, urls):
                    seen['urls'] = urls
                    (Path(folder) / ('audio.mp3' if fmt == 'mp3' else 'audio.webm')).write_bytes(b'fixture')
            module.YoutubeDL = Downloader
            modules = {'yt_dlp': module}
            if provider == 'spotify':
                spot = types.ModuleType('spotdl')
                songs = types.ModuleType('spotdl.types.song')
                class Spot:
                    def __init__(self, **kwargs): pass
                    def get_download_urls(self, songs): return ['https://www.youtube.com/watch?v=abcdefghijk']
                    def download(self, song): raise AssertionError('Original must not use conversion path')
                spot.Spotdl = Spot
                songs.Song = types.SimpleNamespace(from_url=lambda url: object())
                modules.update({'spotdl': spot, 'spotdl.types.song': songs})
            urls = {'youtube': 'https://www.youtube.com/watch?v=abcdefghijk', 'soundcloud': 'https://soundcloud.com/artist/song', 'spotify': 'https://open.spotify.com/track/abc'}
            with patch.dict('sys.modules', modules), patch('backend.provider_worker.shutil.which', return_value='/ffmpeg'):
                result = download({'provider': provider, 'url': urls[provider], 'folder': folder, 'audio_format': fmt})
            self.assertEqual(Path(result['file']).suffix, '.mp3' if fmt == 'mp3' else '.webm')
        return seen

    def test_youtube_original_skips_postprocessing(self):
        opts = self.extract('youtube')
        self.assertEqual(opts['format'], 'bestaudio')
        self.assertNotIn('postprocessors', opts)
        self.assertNotIn('writethumbnail', opts)

    def test_soundcloud_original_uses_direct_adapter(self):
        self.assertNotIn('postprocessors', self.extract('soundcloud'))

    def test_spotify_original_resolves_match_without_spotdl_conversion(self):
        self.assertIn('youtube.com', self.extract('spotify')['urls'][0])

    def test_youtube_mp3_keeps_conversion(self):
        opts = self.extract('youtube', 'mp3')
        self.assertEqual(opts['postprocessors'][0]['preferredcodec'], 'mp3')

    def test_api_defaults_and_request_bounds_remain(self):
        self.assertEqual(DownloadRequest(path='ref').audio_format, 'original')
        for cls, kwargs in ((MetadataRequest, {'paths': ['x'] * 101}), (QueuePlayRequest, {'paths': ['x'] * 10001, 'index': 0})):
            with self.assertRaises(ValueError): cls(**kwargs)


class QueueChunkTests(unittest.TestCase):
    def test_large_queue_keeps_all_paths_with_small_chunks(self):
        from backend.queue_uploads import QueueUploads
        store = QueueUploads()
        key = store.begin()
        paths = [f'C:/Music/{i}.opus' for i in range(12001)]
        for offset in range(0, len(paths), 1000):
            store.append(key, offset, paths[offset:offset+1000])
        self.assertEqual(store.take(key, len(paths)), paths)
        self.assertFalse(store.sessions)

    def test_out_of_order_and_incomplete_commit_rejected(self):
        from backend.queue_uploads import QueueUploads, QueueUploadError
        store = QueueUploads(); key = store.begin()
        with self.assertRaises(QueueUploadError): store.append(key, 10, ['x'])
        store.append(key, 0, ['x'])
        with self.assertRaises(QueueUploadError): store.take(key, 2)
        self.assertEqual(store.take(key, 1), ['x'])

    def test_expiry_session_limit_and_byte_budget(self):
        from backend.queue_uploads import QueueUploads, QueueUploadError
        store = QueueUploads(budget=70, max_sessions=1)
        key = store.begin()
        with self.assertRaises(QueueUploadError): store.begin()
        with self.assertRaises(QueueUploadError): store.append(key, 0, ['x'*20])
        self.assertFalse(store.sessions)
        key = store.begin(); store.sessions[key]['until'] = 0
        with self.assertRaises(QueueUploadError): store.take(key, 1)
        self.assertFalse(store.sessions)

    def test_oversized_chunk_and_path_rejected(self):
        from backend.queue_uploads import QueueUploads, QueueUploadError
        store = QueueUploads(); key = store.begin()
        for paths in ([], ['x']*1001, ['x'*4097], ['   ']):
            with self.assertRaises(QueueUploadError): store.append(key, 0, paths)
        store.cancel(key); self.assertFalse(store.sessions)


class PrivateRuntimeTests(unittest.TestCase):
    def test_embedded_interpreter_preferred_over_legacy_venv(self):
        import sys
        from backend.worker_runtime import worker_python
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); (base / '.providers').mkdir()
            private = base / '.providers/python.exe'; private.touch()
            with patch('backend.worker_runtime.application_dir', return_value=base), patch.object(sys, 'frozen', True, create=True), patch.dict('os.environ', {}, clear=True):
                self.assertEqual(worker_python('provider'), str(private))

    def test_frozen_startup_uses_only_bundle_paths(self):
        import os, sys
        from backend.startup_runtime import configure_paths
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for relative in ('ffmpeg/ffmpeg.exe', 'deno/deno.exe', 'vlc/libvlc.dll', 'webview2/msedgewebview2.exe'):
                file = base / relative; file.parent.mkdir(exist_ok=True); file.touch()
            with patch('backend.startup_runtime.application_dir', return_value=base), patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, {}, clear=True):
                configure_paths()
                self.assertEqual(os.environ['OWNPLAYER_FFMPEG'], str(base / 'ffmpeg/ffmpeg.exe'))
                self.assertEqual(os.environ['OWNPLAYER_DENO'], str(base / 'deno/deno.exe'))
                self.assertEqual(os.environ['WEBVIEW2_BROWSER_EXECUTABLE_FOLDER'], str(base / 'webview2'))
                self.assertIn(str(base / 'vlc'), os.environ['PATH'])

    def test_embed_paths_are_relative_and_traversal_is_rejected(self):
        import zipfile
        from tools.prepare_portable import extract_embed
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); archive=base/'runtime.zip'; target=base/'target';target.mkdir()
            with zipfile.ZipFile(archive, 'w') as z:
                for name in ('python.exe','python313.zip','python313._pth'):z.writestr(name, b'fixture')
            extract_embed(archive,target)
            paths=(target/'python313._pth').read_text()
            self.assertIn('../_internal',paths)
            self.assertNotIn(str(base),paths)
            with zipfile.ZipFile(archive,'w') as z:z.writestr('../escape.exe',b'fixture')
            with self.assertRaises(ValueError):extract_embed(archive,target)

    def test_installer_has_no_user_side_python_or_shell_setup(self):
        root=Path(__file__).resolve().parents[1]
        installer=(root/'packaging/Slynor.iss').read_text(encoding='utf-8')
        for forbidden in ('INSTALL.ps1','powershell','winget','pip install'):
            self.assertNotIn(forbidden,installer)
        self.assertIn('Slynor-Portable.zip',(root/'BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig'))


class QueueAPITests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.local_session import APP_TOKEN
        from backend.queue_uploads import QueueUploads
        self.app = app
        self.old_engine = getattr(app.state, 'audio_engine', None)
        self.engine = types.SimpleNamespace(play_queue=lambda paths,index: {'message':'ok'},set_shuffle=lambda enabled,paths: 'ok')
        app.state.audio_engine = self.engine
        self.upload_patch = patch('backend.main._queue_uploads', QueueUploads())
        self.upload_patch.start()
        self.client = TestClient(app, base_url='http://127.0.0.1:8765', headers={'X-OwnPlayer-Token':APP_TOKEN})

    def tearDown(self):
        self.client.close()
        self.upload_patch.stop()
        self.app.state.audio_engine = self.old_engine

    def test_chunk_route_auth_origin_bounds_and_cancel(self):
        from backend.local_session import APP_TOKEN
        from fastapi.testclient import TestClient
        guest=TestClient(self.app,base_url='http://127.0.0.1:8765')
        try:
            self.assertEqual(guest.post('/api/player/queue/uploads').status_code,401)
        finally:
            guest.close()
        self.assertEqual(self.client.post('/api/player/queue/uploads',headers={'Origin':'https://other.test'}).status_code,403)
        key=self.client.post('/api/player/queue/uploads').json()['id']
        base='/api/player/queue/uploads/'+key
        self.assertEqual(self.client.post(base+'/chunks',json={'offset':0,'paths':['a']*1001}).status_code,422)
        self.assertEqual(self.client.post(base+'/chunks',json={'offset':0,'paths':['a']}).json()['accepted'],1)
        self.assertEqual(self.client.post(base+'/chunks',json={'offset':0,'paths':['b']}).status_code,409)
        self.assertEqual(self.client.post(base+'/commit',json={'expected_count':2}).status_code,409)
        self.assertEqual(self.client.delete(base).status_code,200)
        self.assertEqual(self.client.post(base+'/commit',json={'expected_count':1}).status_code,409)

    def test_large_queue_committed_exactly_once_and_shuffle(self):
        from unittest.mock import Mock
        self.engine.play_queue=Mock(return_value='ok')
        self.engine.set_shuffle=Mock(return_value='ok')
        paths=[f'C:/Music/{i}.opus' for i in range(10001)]
        for shuffle in (False,True):
            key=self.client.post('/api/player/queue/uploads').json()['id']
            base='/api/player/queue/uploads/'+key
            for offset in range(0,len(paths),1000):
                self.assertEqual(self.client.post(base+'/chunks',json={'offset':offset,'paths':paths[offset:offset+1000]}).status_code,200)
            self.assertEqual(self.client.post(base+'/commit',json={'expected_count':len(paths),'index':10000,'shuffle':shuffle}).status_code,200)
            self.assertEqual(self.client.post(base+'/commit',json={'expected_count':len(paths)}).status_code,409)
        self.engine.play_queue.assert_called_once_with(paths,10000)
        self.engine.set_shuffle.assert_called_once_with(True,paths)

class AudioProbeSafetyTests(unittest.TestCase):
    def test_disguised_playlist_is_not_autodetected(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'disguised.webm'
            file.write_text('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128000\nhttps://example.invalid/audio\n')
            probe=Mock(side_effect=ValueError('Not Matroska'))
            with patch('av.open',probe), patch('mutagen.File',return_value=None):
                with self.assertRaises(ValueError):validate_audio_file(file)
            self.assertEqual(probe.call_args.kwargs['format'],'matroska')
            self.assertEqual(probe.call_args.kwargs['options']['protocol_whitelist'],'file,pipe')
