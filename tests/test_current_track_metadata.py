import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_regressions import AudioEngine

from backend.current_track_metadata import cache_metadata, resolve_current_track_metadata
from backend.main import app, get_player_state
from backend.online_music import ProviderError


class CurrentTrackMetadataTests(unittest.TestCase):
    def test_local_lookup_uses_normalized_path_and_preserves_cache(self):
        path = r"C:\Music\metadata-cache-fixture.wav"
        load_online = Mock()
        cache_metadata({"path": path, "title": "Local track"})

        resolved = resolve_current_track_metadata("c:/music/METADATA-CACHE-FIXTURE.wav", load_online)
        self.assertEqual(resolved, {"path": path, "title": "Local track"})
        resolved["title"] = "Changed by consumer"
        self.assertEqual(resolve_current_track_metadata(path, load_online)["title"], "Local track")
        self.assertIsNone(resolve_current_track_metadata("c:/music/uncached-fixture.wav", load_online))
        load_online.assert_not_called()

    def test_online_lookup_uses_reference_and_handles_provider_error(self):
        ref = "online:catalog:metadata-lookup-fixture"
        track = {"path": ref, "title": "Online track"}
        load_online = Mock(return_value=track)
        self.assertEqual(resolve_current_track_metadata(ref, load_online), track)
        load_online.assert_called_once_with(ref)
        load_online.side_effect = ProviderError("Unknown track")
        self.assertIsNone(resolve_current_track_metadata(ref, load_online))

    def test_state_keeps_metadata_and_queue_revision_from_one_snapshot(self):
        first = "online:catalog:metadata-switch-fixture"
        second = r"C:\Music\metadata-after-switch.wav"
        cache_metadata({"path": second, "title": "Next track"})
        engine = Mock(spec=AudioEngine)
        engine.get_state.return_value = {"file_path": first, "queue_revision": 3}

        def lookup_online(ref):
            engine.get_state.return_value = {"file_path": second, "queue_revision": 4}
            return {"path": ref, "title": "First track"}

        online = SimpleNamespace(metadata=Mock(side_effect=lookup_online))
        with patch.object(app, "state", SimpleNamespace(audio_engine=engine, online=online)):
            before = get_player_state()
            after = get_player_state()

        self.assertEqual((before["file_path"], before["queue_revision"]), (first, 3))
        self.assertEqual(before["current_metadata"]["path"], first)
        self.assertEqual((after["file_path"], after["queue_revision"]), (second, 4))
        self.assertEqual(after["current_metadata"]["path"], second)
        online.metadata.assert_called_once_with(first)
