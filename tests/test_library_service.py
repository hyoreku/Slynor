"""Shared library service: migration, service sync and offline cache."""
from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest

from backend.library_service import (LibraryService, local_add_track_many,
                                     local_create_playlist, local_delete_playlist,
                                     local_remove_track, local_rename_playlist,
                                     local_set_cover)


class FakeService:
    def __init__(self, library=None, playlists=None, available=True):
        self.available = available
        self.library_tracks = list(library or [])
        self.playlists_data = deepcopy(playlists or [])
        self.calls = []

    def health(self, timeout=1.5):
        return self.available

    def library(self):
        return {'tracks': list(self.library_tracks), 'revision': 1}

    def playlists(self):
        return deepcopy(self.playlists_data)

    def replace_library(self, tracks):
        self.calls.append(('library', list(tracks)))
        self.library_tracks = list(tracks)
        return list(self.library_tracks)

    def replace_playlists(self, playlists):
        self.calls.append(('playlists', deepcopy(playlists)))
        self.playlists_data = deepcopy(playlists)
        return deepcopy(self.playlists_data)

    def _playlist(self, function, *args):
        self.playlists_data = function(deepcopy(self.playlists_data), *args)
        return deepcopy(self.playlists_data)

    def create_playlist(self, name):
        return self._playlist(local_create_playlist, name)

    def rename_playlist(self, playlist_id, name):
        return self._playlist(local_rename_playlist, playlist_id, name)

    def delete_playlist(self, playlist_id):
        return self._playlist(local_delete_playlist, playlist_id)

    def add_track_to_playlist(self, playlist_id, path):
        return self._playlist(local_add_track_many, [playlist_id], path)

    def add_track_to_playlists(self, playlist_ids, path):
        return self._playlist(local_add_track_many, playlist_ids, path)

    def remove_track_from_playlist(self, playlist_id, path):
        return self._playlist(local_remove_track, playlist_id, path)

    def set_playlist_cover(self, playlist_id, cover):
        return self._playlist(local_set_cover, playlist_id, cover)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class LibraryServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library_path = self.root / 'library.json'
        self.playlists_path = self.root / 'playlists.json'

    def write_local(self, tracks, playlists):
        write_json(self.library_path, {'version': 1, 'files': tracks})
        write_json(self.playlists_path, {'version': 1, 'playlists': playlists})

    def test_empty_service_is_migrated_from_local_json_with_backup(self):
        self.write_local(['C:/a.mp3'], [{'id': 'p-1', 'name': 'One', 'tracks': ['C:/a.mp3']}])
        client = FakeService()
        service = LibraryService(self.root, client)
        self.assertTrue(service.sync())
        self.assertEqual(client.library_tracks, ['C:/a.mp3'])
        self.assertEqual(client.playlists_data[0]['tracks'], ['C:/a.mp3'])
        self.assertTrue((self.root / 'library.json.pre-service.bak').is_file())
        self.assertTrue((self.root / 'playlists.json.pre-service.bak').is_file())
        self.assertFalse((self.root / 'library.sync.json').exists())
        self.assertEqual(service.load_library(), ['C:/a.mp3'])

    def test_service_data_is_pulled_into_the_cache(self):
        self.write_local(['C:/old.mp3'], [{'id': 'p-old', 'name': 'Old', 'tracks': []}])
        client = FakeService(['D:/new.mp3'], [{'id': 'p-new', 'name': 'New', 'tracks': ['D:/new.mp3']}])
        service = LibraryService(self.root, client)
        self.assertTrue(service.sync())
        self.assertEqual(service.load_library(), ['D:/new.mp3'])
        self.assertEqual(service.load_playlists()[0]['name'], 'New')
        self.assertTrue((self.root / 'library.json.pre-service.bak').is_file())
        self.assertEqual(json.loads(self.library_path.read_text(encoding='utf-8'))['files'], ['D:/new.mp3'])

    def test_offline_edits_are_marked_dirty_and_pushed_later(self):
        client = FakeService(available=False)
        service = LibraryService(self.root, client)
        self.assertTrue(service.save_library(['C:/offline.mp3']))
        self.assertTrue((self.root / 'library.sync.json').is_file())
        self.assertEqual(client.library_tracks, [])
        service.create_playlist('Offline')
        playlist_id = service.load_playlists()[0]['id']
        service.add_track_to_playlists([playlist_id], 'C:/offline.mp3')
        self.assertEqual(service.load_playlists()[0]['tracks'], ['C:/offline.mp3'])
        client.available = True
        self.assertEqual(service.load_library(), ['C:/offline.mp3'])
        self.assertEqual(client.library_tracks, ['C:/offline.mp3'])
        self.assertEqual(client.playlists_data[0]['tracks'], ['C:/offline.mp3'])
        self.assertFalse((self.root / 'library.sync.json').exists())

    def test_local_dirty_wins_over_service_data(self):
        self.write_local(['C:/local.mp3'], [])
        write_json(self.root / 'library.sync.json', {'version': 1, 'dirty': True})
        client = FakeService(['D:/remote.mp3'], [])
        service = LibraryService(self.root, client)
        self.assertTrue(service.sync())
        self.assertEqual(client.library_tracks, ['C:/local.mp3'])
        self.assertEqual(service.load_library(), ['C:/local.mp3'])

    def test_corrupted_local_cache_is_not_overwritten(self):
        self.library_path.write_text('{broken', encoding='utf-8')
        client = FakeService(['D:/remote.mp3'], [])
        service = LibraryService(self.root, client)
        self.assertFalse(service.sync())
        with self.assertRaises(RuntimeError):
            service.load_library()
        self.assertEqual(self.library_path.read_text(encoding='utf-8'), '{broken')

    def test_playlist_mutations_use_service_and_refresh_cache(self):
        client = FakeService()
        service = LibraryService(self.root, client)
        playlists = service.create_playlist('One')
        identifier = playlists[0]['id']
        service.add_track_to_playlist(identifier, 'C:/a.mp3')
        service.add_track_to_playlists([identifier], 'D:/b.mp3')
        playlists = service.rename_playlist(identifier, 'First')
        self.assertEqual(playlists[0]['name'], 'First')
        playlists = service.remove_track_from_playlist(identifier, 'C:/a.mp3')
        self.assertEqual(playlists[0]['tracks'], ['D:/b.mp3'])
        playlists = service.set_playlist_cover(identifier, 'a' * 32 + '.jpg')
        self.assertEqual(playlists[0]['cover'], 'a' * 32 + '.jpg')
        playlists = service.delete_playlist(identifier)
        self.assertEqual(playlists, [])
        self.assertEqual(service.load_playlists(), [])


if __name__ == '__main__':
    unittest.main()
