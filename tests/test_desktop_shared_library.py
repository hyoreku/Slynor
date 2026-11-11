"""The pywebview bridge delegates library and playlists to the shared store."""
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.library_service import LibraryService
from desktop import DesktopApi
from test_library_service import FakeService


class DesktopSharedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, LOCALAPPDATA=self.tmp.name)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.api = DesktopApi()
        self.client = FakeService()
        self.shared = LibraryService(self.api._data_dir, self.client)
        self.api._library_service_cache = self.shared

    def test_library_and_playlists_go_through_the_shared_store(self):
        self.assertTrue(self.api.save_library(['C:/a.mp3']))
        self.assertEqual(self.client.library_tracks, ['C:/a.mp3'])
        self.assertEqual(self.api.load_library(), ['C:/a.mp3'])
        playlists = self.api.create_playlist('One')
        identifier = playlists[0]['id']
        self.api.add_track_to_playlist(identifier, 'C:/a.mp3')
        playlists = self.api.add_track_to_playlists([identifier], 'D:/b.mp3')
        self.assertEqual(playlists[0]['tracks'], ['C:/a.mp3', 'D:/b.mp3'])
        playlists = self.api.rename_playlist(identifier, 'First')
        self.assertEqual(playlists[0]['name'], 'First')
        playlists = self.api.remove_track_from_playlist(identifier, 'C:/a.mp3')
        self.assertEqual(playlists[0]['tracks'], ['D:/b.mp3'])
        self.assertEqual(self.api.load_playlists()[0]['tracks'], ['D:/b.mp3'])
        self.assertEqual(self.api.delete_playlist(identifier), [])

    def test_bridge_reports_service_validation_errors(self):
        self.api.create_playlist('One')
        with self.assertRaises(ValueError):
            self.api.create_playlist('one')
        with self.assertRaises(ValueError):
            self.api.save_library([''])


if __name__ == '__main__':
    unittest.main()
