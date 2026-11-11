"""Assets are named by measured hash; limits stay visible and nothing is trimmed."""
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
import wave

from service import db
from service.assets import AssetStore
from service.errors import LimitError, ProviderError
from service.models import AudioAsset, AudioCandidate, Recording


def wav_file(path, frames=2400, rate=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x00\x00' * frames)
    return path


class AssetStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = AssetStore(self.root / 'store')
        self.incoming = self.root / 'incoming'

    def source(self, name='song.wav'):
        return wav_file(self.incoming / name)

    def test_store_measures_and_publishes_one_file(self):
        source = self.source()
        expected = sha256(source.read_bytes()).hexdigest()
        size = source.stat().st_size
        asset = self.store.store(source, origin='youtube')
        self.assertEqual(asset.sha256, expected)
        self.assertEqual(asset.size_bytes, size)
        self.assertEqual(asset.container, 'wav')
        self.assertGreater(asset.duration_ms, 100)
        self.assertIsNotNone(asset.codec)
        stored = self.store.resolve(asset)
        self.assertEqual(stored, (self.root / 'store' / asset.path).resolve())
        self.assertEqual(sha256(stored.read_bytes()).hexdigest(), expected)
        self.assertFalse(source.exists())
        self.assertEqual(self.store.usage(), asset.size_bytes)

    def test_identical_bytes_are_kept_once(self):
        first = self.store.store(self.source('first.wav'))
        second_source = self.source('second.wav')
        second = self.store.store(second_source)
        self.assertEqual(first.path, second.path)
        self.assertEqual(self.store.usage(), first.size_bytes)
        # The duplicate download is left for the caller to remove with its work folder.
        self.assertTrue(second_source.exists())
        self.assertEqual(len(list((self.root / 'store' / 'assets').rglob('*.wav'))), 1)

    def test_file_limit_reports_real_numbers(self):
        source = self.source()
        size = source.stat().st_size
        store = AssetStore(self.root / 'small', max_file_bytes=size - 1)
        with self.assertRaises(LimitError) as caught:
            store.store(source)
        self.assertEqual(caught.exception.code, 'file_too_large')
        self.assertIn(str(size), str(caught.exception))
        self.assertIn(str(size - 1), str(caught.exception))
        self.assertTrue(source.exists())

    def test_store_limit_reports_usage_and_refuses_without_writing(self):
        source = self.source()
        store = AssetStore(self.root / 'tiny', max_total_bytes=10)
        with self.assertRaises(LimitError) as caught:
            store.store(source)
        self.assertEqual(caught.exception.code, 'store_full')
        self.assertIn('10', str(caught.exception))
        self.assertFalse((self.root / 'tiny' / 'assets').exists())
        self.assertTrue(source.exists())

    def test_empty_missing_and_foreign_files_are_refused(self):
        self.incoming.mkdir(parents=True, exist_ok=True)
        empty = self.incoming / 'empty.wav'
        empty.write_bytes(b'')
        foreign = self.root / 'incoming' / 'song.exe'
        foreign.write_bytes(b'MZ')
        cases = ((empty, 'asset_empty'), (foreign, 'unsupported_media'),
                 (self.root / 'incoming' / 'missing.wav', 'asset_missing'))
        for path, code in cases:
            with self.assertRaises(ProviderError) as caught:
                self.store.store(path)
            self.assertEqual(caught.exception.code, code)

    def test_resolve_never_leaves_the_store(self):
        for path in ('../../escape.wav', '/absolute/song.wav', 'C:/Windows/song.wav'):
            with self.assertRaises(ProviderError) as caught:
                self.store.resolve(AudioAsset(container='wav', sha256='a' * 64, size_bytes=1,
                                              path=path))
            self.assertEqual(caught.exception.code, 'unsafe_asset_path')
        with self.assertRaises(ProviderError) as caught:
            self.store.resolve(AudioAsset(container='wav', sha256='a' * 64, size_bytes=1,
                                          path='assets/aa/' + 'a' * 64 + '.wav'))
        self.assertEqual(caught.exception.code, 'asset_missing')

    def test_a_damaged_stored_copy_is_replaced(self):
        first = self.store.store(self.source('first.wav'))
        stored = self.store.resolve(first)
        stored.write_bytes(b'broken')
        second = self.store.store(self.source('second.wav'))
        self.assertEqual(second.path, first.path)
        self.assertEqual(self.store.resolve(second).stat().st_size, second.size_bytes)
        self.assertEqual(sha256(self.store.resolve(second).read_bytes()).hexdigest(), second.sha256)

    def test_workdir_is_private_and_inside_the_store(self):
        work = self.store.new_workdir()
        self.assertTrue(work.is_dir())
        self.assertEqual(work.parent, self.store.work)
        self.assertNotEqual(work, self.store.new_workdir())
        self.assertIn(self.store.root.resolve(), work.resolve().parents)

    def test_limits_are_visible(self):
        limits = self.store.limits()
        self.assertGreater(limits['max_file_bytes'], 0)
        self.assertGreater(limits['max_total_bytes'], 0)
        self.assertIn('used_bytes', limits)


class DbLookupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.connection = db.connect(Path(temporary.name) / 'service.sqlite3')
        self.addCleanup(self.connection.close)

    def test_candidate_and_asset_lookups(self):
        recording = Recording(title='Song')
        db.save_recording(self.connection, recording)
        candidate = AudioCandidate(recording_id=recording.id, provider='youtube',
                                   provider_id='dQw4w9WgXcQ')
        db.save_candidate(self.connection, candidate)
        self.assertEqual(db.get_candidate(self.connection, candidate.id).provider_id,
                         'dQw4w9WgXcQ')
        self.assertEqual(db.find_candidate(self.connection, 'youtube', 'dQw4w9WgXcQ').id,
                         candidate.id)
        self.assertIsNone(db.find_candidate(self.connection, 'youtube', 'other'))
        self.assertIsNone(db.get_candidate(self.connection, 'missing'))

        asset = AudioAsset(container='mp3', sha256='b' * 64, size_bytes=5, path='assets/bb/x.mp3')
        db.save_asset(self.connection, asset)
        self.assertEqual(db.find_asset_by_sha256(self.connection, 'b' * 64).id, asset.id)
        self.assertIsNone(db.find_asset_by_sha256(self.connection, 'c' * 64))
        self.assertEqual(db.list_recordings(self.connection)[0].title, 'Song')


if __name__ == '__main__':
    unittest.main()
