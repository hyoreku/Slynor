"""Auto search keeps asking public sources until enough playable rows are found."""
import tempfile
import time
import unittest
from unittest.mock import patch

from backend.internet_music import SEARCH_MIN_PLAYABLE, InternetLibrary


def row(path):
    return {'path': path, 'access': 'playable', 'title': 'Track', 'artist': 'Artist'}


class AutoSearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = InternetLibrary(temporary.name)
        self.addCleanup(self.library.close)
        self.library.provider.configured = lambda: True
        self.calls = []

    def run_search(self, soundcloud_rows, archive_rows):
        self.library.search = lambda source, query: (self.calls.append('soundcloud')
                                                     or list(soundcloud_rows))
        self.library._archive = lambda key, query: (self.calls.append('archive')
                                                    or list(archive_rows))
        job = self.library.start_search('query', 'auto')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = self.library.job(job['id'])
            if current['status'] in ('done', 'failed'):
                return current
            time.sleep(0.02)
        self.fail('search did not finish')

    @patch('backend.internet_music.worker_available', return_value=False)
    def test_short_first_source_does_not_stop_the_search(self, _worker):
        result = self.run_search([row('online:soundcloud:1')],
                                 [row('online:web:' + 'a' * 32)])
        self.assertEqual(self.calls, ['soundcloud', 'archive'])
        self.assertEqual(result['status'], 'done')
        self.assertEqual(len(result['tracks']), 2)

    @patch('backend.internet_music.worker_available', return_value=False)
    def test_enough_first_source_skips_later_sources(self, _worker):
        rows = [row(f'online:soundcloud:{index}') for index in range(SEARCH_MIN_PLAYABLE)]
        result = self.run_search(rows, [row('online:web:' + 'a' * 32)])
        self.assertEqual(self.calls, ['soundcloud'])
        self.assertEqual(result['status'], 'done')
        self.assertEqual(len(result['tracks']), SEARCH_MIN_PLAYABLE)
