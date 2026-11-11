"""Music Service HTTP contract: jobs API over a temporary SQLite file."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from service.api import create_app


class ServiceApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / 'service.sqlite3'
        self.client = TestClient(create_app(self.db_path))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_health_reports_schema_and_wal(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['schema_version'], 2)
        self.assertEqual(body['journal_mode'], 'wal')
        self.assertEqual(body['jobs']['queued'], 0)
        self.assertTrue(self.db_path.is_file())

    def test_job_lifecycle(self):
        created = self.client.post('/jobs', json={'kind': 'search', 'payload': {'query': 'test'}})
        self.assertEqual(created.status_code, 201)
        job = created.json()
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['payload'], {'query': 'test'})
        self.assertEqual(job['attempts'], 0)

        listed = self.client.get('/jobs', params={'status': 'queued'})
        self.assertEqual([item['id'] for item in listed.json()], [job['id']])

        fetched = self.client.get(f"/jobs/{job['id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()['id'], job['id'])

        cancelled = self.client.post(f"/jobs/{job['id']}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()['status'], 'cancelled')

        conflict = self.client.post(f"/jobs/{job['id']}/cancel")
        self.assertEqual(conflict.status_code, 409)

    def test_unknown_job_is_404_and_bad_kind_is_422(self):
        self.assertEqual(self.client.get('/jobs/missing').status_code, 404)
        self.assertEqual(self.client.post('/jobs/missing/cancel').status_code, 404)
        self.assertEqual(self.client.post('/jobs', json={'kind': 'teleport'}).status_code, 422)

    def test_jobs_survive_application_restart(self):
        created = self.client.post('/jobs', json={'kind': 'download', 'payload': {'path': 'x'}}).json()
        with TestClient(create_app(self.db_path)) as second:
            stored = second.get(f"/jobs/{created['id']}").json()
        self.assertEqual(stored['id'], created['id'])
        self.assertEqual(stored['kind'], 'download')
