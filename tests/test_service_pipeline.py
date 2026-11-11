"""The service slice: search -> candidate -> download -> delivery, end to end."""
import tempfile
import unittest
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from service import db
from service.api import create_app
from service.assets import AssetStore
from service.delivery import FileDeliverySink
from service.errors import ProviderError
from service.jobs import Dispatcher, JobQueue
from service.models import AudioCandidate, Availability, DeliveryStatus, Job, JobKind
from service.pipeline import Pipeline
from service.providers import ProviderSearch, ProviderTrack

TRACK = ProviderTrack('youtube', 'dQw4w9WgXcQ', 'Song', 'Artist', 300)


def wav_file(path, frames=2400, rate=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x00\x00' * frames)
    return path


class FakeProvider:
    name = 'youtube'

    def __init__(self, tracks=(TRACK,)):
        self.tracks = list(tracks)
        self.searches = 0
        self.downloads = 0

    def search(self, query, limit=8):
        self.searches += 1
        return ProviderSearch(tuple(self.tracks[:limit]), ())

    def download(self, track, folder, audio_format='original'):
        self.downloads += 1
        return wav_file(Path(folder) / 'song.wav')


class CountingSink(FileDeliverySink):
    def __init__(self, outbox):
        super().__init__(outbox)
        self.calls = 0

    def send(self, path, asset, target):
        self.calls += 1
        return super().send(path, asset, target)


class FailingSink:
    def __init__(self):
        self.calls = 0

    def send(self, path, asset, target):
        self.calls += 1
        raise RuntimeError('telegram is down')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.connection = db.connect(self.root / 'service.sqlite3')
        self.addCleanup(self.connection.close)
        self.store = AssetStore(self.root / 'store')
        self.sink = CountingSink(self.root / 'outbox')
        self.provider = FakeProvider()
        self.pipeline = Pipeline(self.connection, {'youtube': self.provider}, self.store,
                                 sink=self.sink)

    def search(self):
        return self.pipeline.search(Job(kind=JobKind.SEARCH, payload={'query': 'song'}))

    def download(self, candidate_id):
        return self.pipeline.download(Job(kind=JobKind.DOWNLOAD,
                                          payload={'candidate_id': candidate_id}))

    def prepare_asset(self):
        item = self.search()['items'][0]
        return self.download(item['candidate_id'])['asset_id']

    def test_search_persists_recording_and_candidate_once(self):
        result = self.search()
        self.assertEqual(result['provider'], 'youtube')
        self.assertEqual(result['warnings'], [])
        item = result['items'][0]
        self.assertTrue(item['created'])
        self.assertEqual(item['provider_id'], 'dQw4w9WgXcQ')
        self.assertEqual(item['public_url'], 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(item['availability'], 'unknown')
        recording = db.get_recording(self.connection, item['recording_id'])
        self.assertEqual(recording.title, 'Song')
        self.assertEqual(recording.artists, ['Artist'])
        self.assertEqual(recording.external_ids, {'youtube': 'dQw4w9WgXcQ'})
        self.assertEqual(recording.metadata_origin, 'youtube')
        again = self.search()
        self.assertFalse(again['items'][0]['created'])
        self.assertEqual(again['items'][0]['candidate_id'], item['candidate_id'])
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM recordings').fetchone()[0], 1)
        self.assertEqual(self.connection.execute(
            'SELECT COUNT(*) FROM audio_candidates').fetchone()[0], 1)

    def test_search_refuses_an_unconnected_provider(self):
        pipeline = Pipeline(self.connection, {}, self.store)
        with self.assertRaises(ProviderError) as caught:
            pipeline.search(Job(kind=JobKind.SEARCH, payload={'query': 'song',
                                                             'provider': 'spotify'}))
        self.assertEqual(caught.exception.code, 'unknown_provider')

    def test_download_measures_and_persists_an_asset(self):
        asset_id = self.prepare_asset()
        asset = db.get_asset(self.connection, asset_id)
        self.assertEqual(asset.container, 'wav')
        self.assertEqual(asset.origin, 'youtube')
        self.assertEqual(len(asset.sha256), 64)
        self.assertGreater(asset.size_bytes, 0)
        self.assertGreater(asset.duration_ms, 100)
        self.assertTrue(asset.path.startswith('assets/'))
        stored = self.store.resolve(asset)
        self.assertEqual(stored.stat().st_size, asset.size_bytes)
        self.assertEqual(list(self.store.work.iterdir()), [])

    def test_download_refuses_unknown_and_unavailable_candidates(self):
        with self.assertRaises(ProviderError) as caught:
            self.pipeline.download(Job(kind=JobKind.DOWNLOAD, payload={}))
        self.assertEqual(caught.exception.code, 'invalid_payload')
        with self.assertRaises(ProviderError) as caught:
            self.download('missing')
        self.assertEqual(caught.exception.code, 'unknown_candidate')

        recording_id = self.search()['items'][0]['recording_id']
        unavailable = AudioCandidate(recording_id=recording_id, provider='youtube',
                                     provider_id='zzzzzzzzzzz',
                                     availability=Availability.UNAVAILABLE)
        db.save_candidate(self.connection, unavailable)
        with self.assertRaises(ProviderError) as caught:
            self.download(unavailable.id)
        self.assertEqual(caught.exception.code, 'candidate_unavailable')
        self.assertEqual(self.provider.downloads, 0)

    def test_identical_downloads_are_recorded_once(self):
        second = ProviderTrack('youtube', 'aaaaaaaaaaa', 'Song (live)', 'Artist', 300)
        self.provider.tracks.append(second)
        items = self.search()['items']
        first = self.download(items[0]['candidate_id'])
        duplicate = self.download(items[1]['candidate_id'])
        self.assertFalse(first['deduplicated'])
        self.assertTrue(duplicate['deduplicated'])
        self.assertEqual(duplicate['asset_id'], first['asset_id'])
        self.assertEqual(self.connection.execute(
            'SELECT COUNT(*) FROM audio_assets').fetchone()[0], 1)
        self.assertEqual(len(list((self.root / 'store' / 'assets').rglob('*.wav'))), 1)

    def test_delivery_writes_the_outbox_and_is_idempotent(self):
        asset_id = self.prepare_asset()
        job = Job(kind=JobKind.DELIVER, payload={'asset_id': asset_id, 'target': 'telegram:42'})
        delivered = self.pipeline.deliver(job)
        self.assertEqual(delivered['status'], 'sent')
        self.assertTrue(delivered['external_ref'].endswith('.wav'))
        self.assertTrue((self.root / 'outbox' / delivered['external_ref']).is_file())
        sidecar = self.root / 'outbox' / (delivered['external_ref'] + '.delivery.json')
        self.assertTrue(sidecar.is_file())
        self.assertEqual(self.sink.calls, 1)
        again = self.pipeline.deliver(job)
        self.assertTrue(again['deduplicated'])
        self.assertEqual(again['delivery_id'], delivered['delivery_id'])
        self.assertEqual(self.sink.calls, 1)

    def test_delivery_failure_is_recorded_and_retryable(self):
        asset_id = self.prepare_asset()
        failure = FailingSink()
        pipeline = Pipeline(self.connection, {'youtube': self.provider}, self.store, sink=failure)
        job = Job(kind=JobKind.DELIVER, payload={'asset_id': asset_id, 'target': 'telegram:42'})
        with self.assertRaises(ProviderError) as caught:
            pipeline.deliver(job)
        self.assertEqual(caught.exception.code, 'delivery_failed')
        stored = db.find_delivery(self.connection, asset_id, 'telegram:42')
        self.assertEqual(stored.status, DeliveryStatus.FAILED)
        with self.assertRaises(ProviderError):
            pipeline.deliver(job)
        self.assertEqual(failure.calls, 2)
        self.assertEqual(len(db.list_deliveries(self.connection, asset_id)), 1)

    def test_delivery_requires_a_sink_and_a_valid_target(self):
        asset_id = self.prepare_asset()
        without = Pipeline(self.connection, {'youtube': self.provider}, self.store)
        with self.assertRaises(ProviderError) as caught:
            without.deliver(Job(kind=JobKind.DELIVER,
                                payload={'asset_id': asset_id, 'target': 'telegram:42'}))
        self.assertEqual(caught.exception.code, 'delivery_not_configured')
        with self.assertRaises(ProviderError) as caught:
            self.pipeline.deliver(Job(kind=JobKind.DELIVER,
                                      payload={'asset_id': asset_id, 'target': 'telegram'}))
        self.assertEqual(caught.exception.code, 'invalid_target')
        with self.assertRaises(ProviderError) as caught:
            self.pipeline.deliver(Job(kind=JobKind.DELIVER,
                                      payload={'asset_id': 'missing', 'target': 'telegram:42'}))
        self.assertEqual(caught.exception.code, 'unknown_asset')

    def test_dispatcher_runs_the_whole_slice(self):
        queue = JobQueue(self.connection)
        dispatcher = Dispatcher(queue, self.pipeline.handlers(), poll_seconds=0.05)
        search = queue.enqueue(JobKind.SEARCH, {'query': 'song'})
        self.assertTrue(dispatcher.run_once())
        stored = queue.get(search.id)
        self.assertEqual(stored.status.value, 'done')
        candidate_id = stored.result['items'][0]['candidate_id']

        download = queue.enqueue(JobKind.DOWNLOAD, {'candidate_id': candidate_id})
        dispatcher.run_once()
        asset_id = queue.get(download.id).result['asset_id']

        deliver = queue.enqueue(JobKind.DELIVER, {'asset_id': asset_id, 'target': 'telegram:7'})
        dispatcher.run_once()
        self.assertEqual(queue.get(deliver.id).result['status'], 'sent')
        self.assertEqual(self.provider.searches, 1)
        self.assertEqual(self.provider.downloads, 1)
        self.assertEqual(self.sink.calls, 1)

    def test_limits_are_exposed(self):
        limits = self.pipeline.limits()
        self.assertIn('max_total_bytes', limits['store'])
        self.assertEqual(limits['search_limit_max'], 20)


class ApiPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db_path = self.root / 'service.sqlite3'
        self.store = AssetStore(self.root / 'store')
        self.sink = CountingSink(self.root / 'outbox')
        self.provider = FakeProvider()

        def factory(connection):
            return Pipeline(connection, {'youtube': self.provider}, self.store, sink=self.sink)

        self.client = TestClient(create_app(self.db_path, factory))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.app = self.client.app

    def run_job(self, kind, payload):
        created = self.client.post('/jobs', json={'kind': kind, 'payload': payload})
        self.assertEqual(created.status_code, 201)
        self.assertTrue(self.app.state.dispatcher.run_once())
        job = self.client.get(f"/jobs/{created.json()['id']}").json()
        self.assertEqual(job['status'], 'done', job.get('error'))
        return job['result']

    def test_the_api_runs_search_download_and_delivery(self):
        found = self.run_job('search', {'query': 'song'})
        self.assertEqual(len(found['items']), 1)
        item = found['items'][0]
        recording = self.client.get(f"/recordings/{item['recording_id']}")
        self.assertEqual(recording.status_code, 200)
        self.assertEqual(recording.json()['recording']['title'], 'Song')
        self.assertEqual(len(recording.json()['candidates']), 1)

        downloaded = self.run_job('download', {'candidate_id': item['candidate_id']})
        asset = self.client.get(f"/assets/{downloaded['asset_id']}")
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.json()['sha256'], downloaded['sha256'])
        content = self.client.get(f"/assets/{downloaded['asset_id']}/content")
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.content, (self.root / 'store' / downloaded['path']).read_bytes())
        self.assertEqual(self.client.get('/assets/missing/content').status_code, 404)

        delivered = self.run_job('deliver', {'asset_id': downloaded['asset_id'],
                                             'target': 'telegram:42'})
        self.assertEqual(delivered['status'], 'sent')
        deliveries = self.client.get('/deliveries',
                                     params={'asset_id': downloaded['asset_id']}).json()
        self.assertEqual([row['status'] for row in deliveries], ['sent'])

    def test_catalog_endpoints_and_missing_records(self):
        providers = self.client.get('/providers').json()['providers']
        self.assertEqual(providers, [{'name': 'youtube', 'operations': ['search', 'download',
                                                                       'resolve']}])
        limits = self.client.get('/limits').json()
        self.assertIn('store', limits)
        self.assertEqual(self.client.get('/recordings/missing').status_code, 404)
        self.assertEqual(self.client.get('/assets/missing').status_code, 404)
        self.assertEqual(self.client.get('/jobs/missing').status_code, 404)

    def test_unknown_provider_fails_the_job_with_a_clear_error(self):
        created = self.client.post('/jobs', json={'kind': 'search',
                                                  'payload': {'query': 'song',
                                                              'provider': 'vk'}}).json()
        self.app.state.dispatcher.run_once()
        stored = self.client.get(f"/jobs/{created['id']}").json()
        self.assertEqual(stored['status'], 'queued')  # retried while attempts remain
        self.assertIn('unknown_provider', stored['error'])


class SkeletonApiTests(unittest.TestCase):
    def test_service_without_a_pipeline_keeps_jobs_and_reports_no_dispatcher(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'service.sqlite3'
        with TestClient(create_app(path)) as client:
            health = client.get('/health').json()
            self.assertFalse(health['dispatcher'])
            self.assertEqual(client.get('/providers').json()['providers'], [])
            self.assertEqual(client.get('/limits').status_code, 404)


if __name__ == '__main__':
    unittest.main()
