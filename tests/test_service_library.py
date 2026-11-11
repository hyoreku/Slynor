"""Shared library and playlists: schema v2, repositories and HTTP contract."""
import sqlite3
import tempfile
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from service import db
from service.api import create_app


class LibraryRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'service.sqlite3'
        self.connection = db.connect(self.path)
        self.addCleanup(self.connection.close)

    def test_library_is_ordered_and_deduplicated_by_windows_path(self):
        tracks = db.replace_library(self.connection, ['C:/Music/a.mp3', 'c:\\music\\A.MP3', 'D:/b.mp3'])
        self.assertEqual(tracks, ['C:/Music/a.mp3', 'D:/b.mp3'])
        self.assertEqual(db.load_library(self.connection), tracks)
        self.assertEqual(db.library_state(self.connection)['revision'], 1)
        db.replace_library(self.connection, ['D:/b.mp3'])
        self.assertEqual(db.library_state(self.connection)['revision'], 2)

    def test_library_survives_reopen(self):
        db.replace_library(self.connection, ['C:/Music/a.mp3'])
        self.connection.close()
        reopened = db.connect(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(db.load_library(reopened), ['C:/Music/a.mp3'])

    def test_playlists_crud_order_and_cascade(self):
        first = db.create_playlist(self.connection, 'One')
        second = db.create_playlist(self.connection, 'Two')
        self.assertEqual([item['name'] for item in second], ['One', 'Two'])
        identifiers = [item['id'] for item in second]
        playlists = db.add_track_to_playlists(self.connection, identifiers, 'C:/a.mp3')
        self.assertEqual([item['tracks'] for item in playlists], [['C:/a.mp3'], ['C:/a.mp3']])
        playlists = db.add_track_to_playlist(self.connection, identifiers[0], 'c:\\A.MP3')
        self.assertEqual(playlists[0]['tracks'], ['C:/a.mp3'])
        playlists = db.rename_playlist(self.connection, identifiers[0], 'First')
        self.assertEqual(playlists[0]['name'], 'First')
        playlists = db.remove_track_from_playlist(self.connection, identifiers[0], 'C:/a.mp3')
        self.assertEqual(playlists[0]['tracks'], [])
        cover = 'a' * 32 + '.jpg'
        playlists = db.set_playlist_cover(self.connection, identifiers[1], cover)
        self.assertEqual(playlists[1]['cover'], cover)
        playlists = db.delete_playlist(self.connection, identifiers[1])
        self.assertEqual([item['name'] for item in playlists], ['First'])
        remaining = self.connection.execute('SELECT COUNT(*) FROM playlist_items').fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_validation_and_duplicates(self):
        db.create_playlist(self.connection, 'One')
        with self.assertRaises(ValueError):
            db.create_playlist(self.connection, 'one')
        with self.assertRaises(ValueError):
            db.create_playlist(self.connection, '  ')
        with self.assertRaises(ValueError):
            db.replace_library(self.connection, [''])
        with self.assertRaises(ValueError):
            db.replace_playlists(self.connection, [
                {'id': 'same', 'name': 'A', 'tracks': []},
                {'id': 'same', 'name': 'B', 'tracks': []}])
        with self.assertRaises(ValueError):
            db.set_playlist_cover(self.connection, db.load_playlists(self.connection)[0]['id'], '../x.jpg')
        with self.assertRaises(ValueError):
            db.add_track_to_playlist(self.connection, 'missing', 'C:/a.mp3')

    def test_import_preserves_ids_order_and_tracks(self):
        playlists = db.replace_playlists(self.connection, [
            {'id': 'keep-1', 'name': 'Old', 'tracks': ['C:/a.mp3', 'c:\\A.MP3'], 'cover': 'b' * 32 + '.jpg'},
            {'id': 'keep-2', 'name': 'New', 'tracks': ['D:/b.mp3']}])
        self.assertEqual([item['id'] for item in playlists], ['keep-1', 'keep-2'])
        self.assertEqual(playlists[0]['tracks'], ['C:/a.mp3'])
        self.assertEqual(playlists[0]['cover'], 'b' * 32 + '.jpg')
        self.assertNotIn('cover', playlists[1])

    def test_version_one_database_gains_library_tables_in_place(self):
        legacy_path = self.path.with_name('legacy.sqlite3')
        legacy = sqlite3.connect(legacy_path)
        legacy.execute('PRAGMA user_version=1')
        legacy.commit()
        legacy.close()
        upgraded = db.connect(legacy_path)
        self.addCleanup(upgraded.close)
        self.assertEqual(upgraded.execute('PRAGMA user_version').fetchone()[0], 2)
        self.assertEqual(db.load_library(upgraded), [])
        self.assertEqual(db.load_playlists(upgraded), [])


class LibraryApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'service.sqlite3'
        self.client = TestClient(create_app(self.path))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_library_roundtrip_and_revision(self):
        empty = self.client.get('/library')
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()['tracks'], [])
        first = self.client.put('/library', json={'tracks': ['C:/a.mp3', 'c:\\A.MP3', 'D:/b.mp3']})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['tracks'], ['C:/a.mp3', 'D:/b.mp3'])
        revision = first.json()['revision']
        self.assertEqual(self.client.get('/library/revision').json()['revision'], revision)
        again = self.client.put('/library', json={'tracks': ['D:/b.mp3']})
        self.assertEqual(again.json()['revision'], revision + 1)
        self.assertEqual(self.client.put('/library', json={'tracks': ['']}).status_code, 400)

    def test_playlist_contract(self):
        created = self.client.post('/playlists', json={'name': 'One'})
        self.assertEqual(created.status_code, 201)
        playlist_id = created.json()['playlists'][0]['id']
        self.assertEqual(self.client.post('/playlists', json={'name': 'one'}).status_code, 409)
        added = self.client.post(f'/playlists/{playlist_id}/tracks', json={'path': 'C:/a.mp3'})
        self.assertEqual(added.json()['playlists'][0]['tracks'], ['C:/a.mp3'])
        renamed = self.client.patch(f'/playlists/{playlist_id}', json={'name': 'First'})
        self.assertEqual(renamed.json()['playlists'][0]['name'], 'First')
        cover = 'a' * 32 + '.jpg'
        covered = self.client.post(f'/playlists/{playlist_id}/cover', json={'cover': cover})
        self.assertEqual(covered.json()['playlists'][0]['cover'], cover)
        removed = self.client.post(f'/playlists/{playlist_id}/tracks/remove', json={'path': 'C:/a.mp3'})
        self.assertEqual(removed.json()['playlists'][0]['tracks'], [])
        self.assertEqual(self.client.post('/playlists/tracks',
                                         json={'playlist_ids': [playlist_id], 'path': 'D:/b.mp3'}).status_code, 200)
        self.assertEqual(self.client.delete(f'/playlists/{playlist_id}').status_code, 200)
        self.assertEqual(self.client.delete(f'/playlists/{playlist_id}').status_code, 404)
        self.assertEqual(self.client.post('/playlists', json={'name': ''}).status_code, 422)

    def test_playlist_import_and_unknown_ids(self):
        imported = self.client.put('/playlists', json={'playlists': [
            {'id': 'keep-1', 'name': 'Old', 'tracks': ['C:/a.mp3']},
            {'id': 'keep-2', 'name': 'New', 'tracks': []}]})
        self.assertEqual(imported.status_code, 200)
        self.assertEqual([item['id'] for item in imported.json()['playlists']], ['keep-1', 'keep-2'])
        self.assertEqual(self.client.patch('/playlists/missing', json={'name': 'X'}).status_code, 404)
        self.assertEqual(self.client.post('/playlists/missing/tracks',
                                          json={'path': 'C:/a.mp3'}).status_code, 404)


if __name__ == '__main__':
    unittest.main()

