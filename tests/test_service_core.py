"""Music Service core: domain models, SQLite storage and job lifecycle."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from service import db
from service.jobs import Dispatcher, JobQueue
from service.models import (AudioAsset, AudioCandidate, Availability, Delivery, DeliveryStatus,
                            JobKind, JobStatus, Recording)


class ModelTests(unittest.TestCase):
    def test_recording_is_cleaned_and_identified(self):
        recording = Recording(title='  Song  ', artists=[' Artist '], external_ids={'musicbrainz': 'abc'})
        self.assertEqual(recording.title, 'Song')
        self.assertEqual(recording.artists, ['Artist'])
        self.assertEqual(len(recording.id), 32)
        self.assertEqual(recording.external_ids, {'musicbrainz': 'abc'})

    def test_recording_rejects_empty_and_malformed_values(self):
        with self.assertRaises(ValueError):
            Recording(title='   ')
        with self.assertRaises(ValueError):
            Recording(title='Song', artists=['  '])
        with self.assertRaises(ValueError):
            Recording(title='Song', external_ids={'Bad Key': 'x'})
        with self.assertRaises(ValueError):
            Recording(title='Song', duration_ms=-1)

    def test_candidate_keeps_stable_identifiers_only(self):
        with self.assertRaises(ValueError):
            AudioCandidate(recording_id='r', provider='yt', provider_id='https://cdn.example/x?expires=1')
        with self.assertRaises(ValueError):
            AudioCandidate(recording_id='r', provider='YT', provider_id='abc')
        candidate = AudioCandidate(recording_id='r', provider='soundcloud', provider_id='123')
        self.assertEqual(candidate.availability, Availability.UNKNOWN)

    def test_asset_requires_a_measured_hash(self):
        with self.assertRaises(ValueError):
            AudioAsset(container='mp3', sha256='abc', size_bytes=1, path='x')
        asset = AudioAsset(container='mp3', sha256='A' * 64, size_bytes=10, path='x')
        self.assertEqual(asset.sha256, 'a' * 64)

    def test_delivery_target_is_explicit(self):
        with self.assertRaises(ValueError):
            Delivery(asset_id='a', target='telegram')
        delivery = Delivery(asset_id='a', target='telegram:12345')
        self.assertEqual(delivery.status, DeliveryStatus.PENDING)


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'service.sqlite3'
        self.connection = db.connect(self.path)
        self.addCleanup(self.connection.close)

    def test_schema_version_and_wal(self):
        self.assertEqual(self.connection.execute('PRAGMA user_version').fetchone()[0], db.SCHEMA_VERSION)
        self.assertEqual(self.connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(self.connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)

    def test_records_roundtrip(self):
        recording = Recording(title='Song', artists=['A'], external_ids={'musicbrainz': 'abc'})
        db.save_recording(self.connection, recording)
        loaded = db.get_recording(self.connection, recording.id)
        self.assertEqual(loaded.title, 'Song')
        self.assertEqual(loaded.artists, ['A'])
        self.assertEqual(loaded.external_ids, {'musicbrainz': 'abc'})

        candidate = AudioCandidate(recording_id=recording.id, provider='yt', provider_id='abc')
        db.save_candidate(self.connection, candidate)
        self.assertEqual([item.provider_id for item in db.list_candidates(self.connection, recording.id)], ['abc'])
        with self.assertRaises(sqlite3.IntegrityError):
            db.save_candidate(self.connection, AudioCandidate(
                recording_id=recording.id, provider='yt', provider_id='abc'))

        asset = AudioAsset(container='mp3', sha256='b' * 64, size_bytes=5,
                           path='C:/music/a.mp3', candidate_id=candidate.id)
        db.save_asset(self.connection, asset)
        self.assertEqual(db.get_asset(self.connection, asset.id).sha256, 'b' * 64)

        delivery = Delivery(asset_id=asset.id, target='telegram:42')
        db.save_delivery(self.connection, delivery)
        self.assertEqual([item.target for item in db.list_deliveries(self.connection, asset.id)],
                         ['telegram:42'])

    def test_deleting_recording_cascades_candidates(self):
        recording = Recording(title='Song')
        db.save_recording(self.connection, recording)
        db.save_candidate(self.connection, AudioCandidate(
            recording_id=recording.id, provider='yt', provider_id='abc'))
        self.connection.execute('DELETE FROM recordings WHERE id=?', (recording.id,))
        self.assertEqual(db.list_candidates(self.connection, recording.id), [])


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.connection = db.connect(Path(temporary.name) / 'service.sqlite3')
        self.addCleanup(self.connection.close)
        self.queue = JobQueue(self.connection)

    def test_enqueue_claim_complete(self):
        job = self.queue.enqueue(JobKind.SEARCH, {'query': 'test'})
        self.assertEqual(job.status, JobStatus.QUEUED)
        claimed = self.queue.claim_next()
        self.assertEqual(claimed.id, job.id)
        self.assertEqual(claimed.status, JobStatus.RUNNING)
        self.assertEqual(claimed.attempts, 1)
        self.assertIsNone(self.queue.claim_next())
        done = self.queue.complete(job.id, {'tracks': 2})
        self.assertEqual(done.status, JobStatus.DONE)
        self.assertEqual(done.result, {'tracks': 2})
        self.assertIsNotNone(done.finished_at)

    def test_fail_retries_then_becomes_terminal(self):
        job = self.queue.enqueue(JobKind.DOWNLOAD)
        for _ in range(3):
            self.assertIsNotNone(self.queue.claim_next())
            stored = self.queue.fail(job.id, 'boom', max_attempts=3)
        self.assertEqual(stored.status, JobStatus.FAILED)
        self.assertEqual(stored.attempts, 3)
        self.assertIn('boom', stored.error)
        self.assertIsNotNone(stored.finished_at)
        self.assertIsNone(self.queue.claim_next())

    def test_fail_returns_job_to_queue_while_attempts_remain(self):
        job = self.queue.enqueue(JobKind.SEARCH)
        self.queue.claim_next()
        stored = self.queue.fail(job.id, 'temporary', max_attempts=2)
        self.assertEqual(stored.status, JobStatus.QUEUED)
        self.assertIsNone(stored.finished_at)

    def test_cancel_rules(self):
        queued = self.queue.enqueue(JobKind.SEARCH)
        self.assertEqual(self.queue.cancel(queued.id).status, JobStatus.CANCELLED)
        with self.assertRaises(ValueError):
            self.queue.cancel(queued.id)
        running = self.queue.enqueue(JobKind.SEARCH)
        self.queue.claim_next()
        with self.assertRaises(ValueError):
            self.queue.cancel(running.id)

    def test_list_filters_by_status(self):
        first = self.queue.enqueue(JobKind.SEARCH)
        second = self.queue.enqueue(JobKind.DOWNLOAD)
        self.queue.claim_next()
        self.queue.cancel(second.id)
        self.assertEqual([job.id for job in self.queue.list(JobStatus.CANCELLED)], [second.id])
        self.assertEqual([job.id for job in self.queue.list(JobStatus.RUNNING)], [first.id])

    def test_dispatcher_runs_handler_and_completes(self):
        seen = []
        dispatcher = Dispatcher(self.queue, {JobKind.SEARCH: lambda job: seen.append(job.id) or {'ok': True}},
                                poll_seconds=0.05)
        job = self.queue.enqueue(JobKind.SEARCH)
        self.assertTrue(dispatcher.run_once())
        self.assertFalse(dispatcher.run_once())
        stored = self.queue.get(job.id)
        self.assertEqual(stored.status, JobStatus.DONE)
        self.assertEqual(stored.result, {'ok': True})
        self.assertEqual(seen, [job.id])

    def test_dispatcher_records_failure_and_retries(self):
        def fail(job):
            raise RuntimeError('nope')
        dispatcher = Dispatcher(self.queue, {JobKind.SEARCH: fail}, poll_seconds=0.05)
        job = self.queue.enqueue(JobKind.SEARCH)
        dispatcher.run_once()
        stored = self.queue.get(job.id)
        self.assertEqual(stored.status, JobStatus.QUEUED)
        self.assertIn('nope', stored.error)

    def test_dispatcher_fails_job_without_handler(self):
        dispatcher = Dispatcher(self.queue, {}, poll_seconds=0.05)
        job = self.queue.enqueue(JobKind.DELIVER)
        dispatcher.run_once()
        self.assertEqual(self.queue.get(job.id).status, JobStatus.FAILED)
