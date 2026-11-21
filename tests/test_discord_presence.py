import json
import os
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from backend.discord_presence import DISCORD_APPLICATION_ID, DiscordPresence, listening_activity
from backend.discord_rpc import DiscordRpc, DiscordRpcRejected
from test_regressions import DesktopApi


APPLICATION_ID = "123456789012345678"
LOGO_ID = "123456789012345679"
PLAYING = {
    "state": "playing", "file_path": r"C:\Music\track.flac", "filename": "track.flac",
    "time_ms": 20000, "duration_ms": 140000, "rate": 1.0, "playback_generation": 1,
}


def frame(opcode, payload):
    body = json.dumps(payload, ensure_ascii=False).encode()
    return struct.pack("<II", opcode, len(body)) + body


class FakePipe:
    def __init__(self, initial=None):
        self.received = bytearray(initial or frame(1, {"cmd": "DISPATCH", "evt": "READY"}))
        self.sent = []
        self.closed = False

    def write(self, data, deadline):
        opcode, length = struct.unpack("<II", data[:8])
        body = json.loads(data[8:])
        assert len(data) == 8 + length
        self.sent.append((opcode, body))
        if body.get("cmd") == "SET_ACTIVITY":
            self.received.extend(frame(1, {"cmd": "SET_ACTIVITY", "nonce": "unrelated"}))
            self.received.extend(frame(1, {"cmd": "SET_ACTIVITY", "nonce": body["nonce"], "evt": None}))

    def read_exact(self, size, deadline):
        if len(self.received) < size:
            raise TimeoutError()
        chunk = bytes(self.received[:size])
        del self.received[:size]
        return chunk

    def readable(self):
        return bool(self.received)

    def close(self):
        self.closed = True


class DiscordRpcTests(unittest.TestCase):
    def test_handshake_ping_set_activity_and_clear(self):
        pipe = FakePipe(frame(3, {"ping": True}) + frame(1, {"cmd": "DISPATCH", "evt": "READY"}))
        rpc = DiscordRpc(APPLICATION_ID, lambda: pipe)
        rpc.set_activity({"type": 2, "details": "Трек"})
        rpc.set_activity(None)
        rpc.close()

        self.assertEqual(pipe.sent[0], (0, {"v": 1, "client_id": APPLICATION_ID}))
        self.assertEqual(pipe.sent[1], (4, {"ping": True}))
        self.assertEqual(pipe.sent[2][1]["args"], {
            "pid": os.getpid(), "activity": {"type": 2, "details": "Трек"},
        })
        self.assertIsNone(pipe.sent[3][1]["args"]["activity"])
        self.assertTrue(pipe.closed)

    def test_handshake_closes_on_malformed_or_rejected_reply(self):
        for payload in (struct.pack("<II", 1, 65537), frame(2, {}), frame(1, {"evt": "ERROR"})):
            pipe = FakePipe(payload)
            with self.assertRaises(OSError):
                DiscordRpc(APPLICATION_ID, lambda: pipe)
            self.assertTrue(pipe.closed)


class ActivityTests(unittest.TestCase):
    def test_only_track_display_fields_and_timestamps(self):
        activity = listening_activity(
            {**PLAYING, "rate": 2}, {
                "title": "  Мелодия  ", "artist": "Автор", "path": PLAYING["file_path"],
                "cover_url": "https://private.example/art", "permalink_url": "https://private.example/song",
            }, 1000,
        )
        self.assertEqual(activity, {
            "type": 2, "details": "Мелодия", "state": "Автор",
            "timestamps": {"start": 990, "end": 1060},
        })

    def test_pause_keeps_cover_and_fixed_position(self):
        activity = listening_activity(
            {**PLAYING, "state": "paused", "time_ms": 29000, "duration_ms": 191000},
            {"title": "Трек", "artist": "Автор", "public_cover_url": "https://i.scdn.co/image/id?token=hidden"},
            1000, LOGO_ID,
        )
        self.assertEqual(activity["state"], "Автор · На паузе · 00:29 / 03:11")
        self.assertNotIn("timestamps", activity)
        self.assertEqual(activity["assets"]["large_image"], "https://i.scdn.co/image/id")
        self.assertEqual(activity["assets"]["small_image"], LOGO_ID)

    def test_catalogue_recovers_only_the_current_cover_origin(self):
        from backend.catalog_music import CatalogueLibrary

        with tempfile.TemporaryDirectory() as root:
            catalogue = CatalogueLibrary(root)
            self.addCleanup(catalogue.close)
            url = "https://i.ytimg.com/vi/video/hqdefault.jpg?size=large"
            asset = catalogue.cached_cover_url(url)
            self.assertEqual(catalogue.cover_origin(asset), url)
            key = asset.rsplit("/", 1)[-1].removesuffix(".jpg")
            catalogue.cover_failures[key] = float("inf")
            self.assertIsNone(catalogue.cover_origin(asset))
            self.assertIsNone(catalogue.cover_origin("/api/assets/online-covers/" + "0" * 64 + ".jpg"))

    def test_only_public_artwork_is_shared(self):
        for value in (
            "/api/assets/online-covers/ref.jpg", "http://i.ytimg.com/cover.jpg",
            "https://127.0.0.1/cover.jpg", "https://i.scdn.co.example.com/a.jpg",
            "https://user:pw@i.scdn.co/cover.jpg", "https://i.scdn.co:444/cover.jpg",
        ):
            with self.subTest(value=value):
                activity = listening_activity(PLAYING, {"public_cover_url": value}, 1000)
                self.assertNotIn("assets", activity)

    def test_private_values_and_invalid_lengths_are_filtered(self):
        for private in (PLAYING["file_path"], "//home/local", "Track https://host/?token=ABC", "file:/local", "online:secret"):
            activity = listening_activity({**PLAYING, "duration_ms": 0}, {"title": private, "artist": private}, 1000)
            self.assertEqual(activity["details"], "Slynor")
            self.assertNotIn("state", activity)
            self.assertNotIn("timestamps", activity)
        details = listening_activity(PLAYING, {"title": "🎵" * 100}, 1000)["details"]
        self.assertEqual(len(details.encode()), 128)


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = dict(PLAYING)
        self.player = Mock(side_effect=lambda: self.snapshot.copy())
        self.metadata = Mock(return_value={"title": "Трек", "artist": "Исполнитель"})
        self.rpc = Mock()
        self.connect = Mock(return_value=self.rpc)
        self.artwork = Mock()
        self.artwork.logo.return_value = LOGO_ID
        self.artwork.lookup.return_value = None
        self.presence = DiscordPresence(self.player, self.metadata, self.connect, artwork=self.artwork)
        self.clock = self.enterContext(patch("backend.discord_presence.time"))
        self.clock.time.return_value = 1000
        self.clock.monotonic.return_value = 10

    def tick(self, elapsed=0):
        self.clock.monotonic.return_value = 10 + elapsed
        self.clock.time.return_value = 1000 + elapsed
        self.presence._sync(self.presence._configured_id(), 10 + elapsed)

    def enable(self):
        self.presence.configure(True)

    def test_default_off_and_no_connection_on_idle(self):
        self.tick()
        self.player.assert_not_called()
        self.enable()
        self.snapshot["state"] = "opening"
        self.tick(1)
        self.connect.assert_not_called()
        self.metadata.assert_not_called()

    def test_throttle_seek_speed_change_and_pause(self):
        self.enable()
        self.tick()
        self.snapshot["time_ms"] += 6000
        self.tick(6)
        self.rpc.set_activity.assert_called_once()
        self.snapshot["time_ms"] += 30000
        self.snapshot["seek_revision"] = 1
        self.tick(6.2)
        self.assertEqual(self.rpc.set_activity.call_count, 2)
        self.snapshot["rate"] = 1.5
        self.tick(6.5)
        self.assertEqual(self.rpc.set_activity.call_count, 3)
        self.snapshot["state"] = "paused"
        self.tick(7)
        paused = self.rpc.set_activity.call_args.args[0]
        self.assertIn("На паузе", paused["state"])
        self.assertNotIn("timestamps", paused)
        self.tick(7.4)
        self.assertEqual(self.rpc.set_activity.call_count, 4)
        self.snapshot.update(time_ms=53000, seek_revision=2)
        self.tick(7.8)
        self.assertIn("На паузе · 00:53", self.rpc.set_activity.call_args.args[0]["state"])
        self.assertEqual(self.rpc.set_activity.call_count, 5)
        self.snapshot["state"] = "playing"
        self.tick(8)
        self.assertIn("timestamps", self.rpc.set_activity.call_args.args[0])
        self.snapshot["state"] = "idle"
        self.tick(16)
        self.rpc.set_activity.assert_called_with(None)
        self.connect.assert_called_once_with(DISCORD_APPLICATION_ID)

    def test_seek_updates_timestamps_during_the_normal_update_interval(self):
        self.enable()
        self.tick()
        self.snapshot.update(time_ms=80000, seek_revision=1)
        self.tick(.4)
        self.assertEqual(self.rpc.set_activity.call_args.args[0]["timestamps"], {"start": 920, "end": 1061})

    def test_buffering_same_track_keeps_activity(self):
        self.enable()
        self.tick()
        self.snapshot.update(state="buffering")
        self.tick(.4)
        self.rpc.set_activity.assert_called_once()
        self.snapshot.update(state="playing", time_ms=90000, seek_revision=1)
        self.tick(.8)
        self.assertEqual(self.rpc.set_activity.call_count, 2)

    def test_external_artwork_rejection_falls_back_to_logo(self):
        self.enable()
        self.metadata.return_value = {"title": "Track", "public_cover_url": "https://i.scdn.co/image/id"}
        self.artwork.lookup.side_effect = [self.metadata.return_value["public_cover_url"], None, None]
        self.rpc.set_activity.side_effect = [DiscordRpcRejected("bad image"), DiscordRpcRejected("bad image"), None, None]
        self.tick()
        first, no_logo, fallback = [call.args[0] for call in self.rpc.set_activity.call_args_list]
        self.assertIn("small_image", first["assets"])
        self.assertNotIn("small_image", no_logo["assets"])
        self.assertEqual(fallback["assets"]["large_image"], LOGO_ID)
        self.snapshot["state"] = "paused"
        self.tick(1)
        self.assertEqual(self.rpc.set_activity.call_args.args[0]["assets"]["large_image"], LOGO_ID)

    def test_rejected_logo_does_not_discard_valid_cover(self):
        self.enable()
        url = "https://i.scdn.co/image/id"
        self.artwork.lookup.return_value = url
        self.rpc.set_activity.side_effect = [DiscordRpcRejected("bad logo"), None, None]
        self.tick()
        self.assertEqual(self.rpc.set_activity.call_args.args[0]["assets"], {
            "large_image": url, "large_text": "Обложка трека",
        })
        self.snapshot.update(state="paused")
        self.tick(.4)
        self.assertNotIn("small_image", self.rpc.set_activity.call_args.args[0]["assets"])

    def test_rate_limit_retries_with_the_latest_position_and_cover(self):
        self.enable()
        cover = "https://i.scdn.co/image/id"
        self.artwork.lookup.return_value = cover
        self.rpc.set_activity.side_effect = [DiscordRpcRejected("rate limit", rate_limited=True), None]
        self.tick()
        self.snapshot.update(time_ms=80000, seek_revision=1)
        self.tick(.4)
        self.rpc.set_activity.assert_called_once()
        self.tick(5)
        sent = self.rpc.set_activity.call_args.args[0]
        self.assertEqual(sent["assets"]["large_image"], cover)
        self.assertEqual(sent["timestamps"]["start"], 925)

    def test_stop_and_same_track_replay_publish_before_throttle_expires(self):
        self.enable()
        self.tick()
        self.snapshot.update(state="idle", file_path=None, playback_generation=2)
        self.tick(1)
        self.rpc.set_activity.assert_called_with(None)
        self.snapshot.update(PLAYING, time_ms=50, playback_generation=3)
        self.tick(2)
        self.assertEqual(self.rpc.set_activity.call_count, 3)
        self.assertEqual(self.rpc.set_activity.call_args.args[0]["details"], "Трек")

    def test_replay_detected_without_idle_snapshot(self):
        self.enable()
        self.tick()
        self.snapshot.update(playback_generation=3, time_ms=50)
        self.tick(1)
        self.assertEqual(self.rpc.set_activity.call_count, 2)

    def test_clear_failure_does_not_block_replay(self):
        self.enable()
        self.rpc.set_activity.side_effect = [None, DiscordRpcRejected("clear"), None]
        self.tick()
        self.snapshot.update(state="idle", file_path=None)
        self.tick(1)
        self.rpc.close.assert_called_once()
        self.snapshot.update(PLAYING)
        self.tick(2)
        self.assertEqual(self.connect.call_count, 2)
        self.assertIsNotNone(self.rpc.set_activity.call_args.args[0])

    def test_background_artwork_arrives_without_restarting_the_track(self):
        self.enable()
        self.metadata.return_value = {"title": "Трек", "artist": "Артист", "album": "Альбом"}
        self.tick()
        self.artwork.lookup.assert_called_once_with("Трек", "Артист", "Альбом", 140000, allow_apple=True)
        self.assertEqual(self.rpc.set_activity.call_args.args[0]["assets"]["large_image"], LOGO_ID)
        url = "https://archive.org/download/mbid-11111111-1111-1111-1111-111111111111/front.jpg"
        self.artwork.lookup.return_value = url
        self.tick(.4)
        self.assertEqual(self.rpc.set_activity.call_args.args[0]["assets"]["large_image"], url)

    def test_rejected_provider_picture_uses_alternative_artwork(self):
        self.enable()
        self.metadata.return_value = {
            "title": "Track", "artist": "Artist", "source": "youtube",
            "permalink_url": "https://www.youtube.com/watch?v=abcdefghijk",
            "public_cover_url": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
        }
        alternate = "https://archive.org/download/mbid-11111111-1111-1111-1111-111111111111/front.jpg"
        self.artwork.lookup.side_effect = [self.metadata.return_value["public_cover_url"],
                                           self.metadata.return_value["public_cover_url"], alternate]
        self.rpc.set_activity.side_effect = [DiscordRpcRejected("bad image"), DiscordRpcRejected("bad image"), None]
        self.tick()
        self.assertEqual(self.rpc.set_activity.call_args.args[0]["assets"]["large_image"], alternate)

    def test_disabling_clears_and_close_is_idempotent_for_connection(self):
        self.enable()
        self.tick()
        self.presence.configure(False)
        self.tick(1)
        self.rpc.set_activity.assert_called_with(None)
        self.rpc.close.assert_called_once()
        self.presence._disconnect(clear=True)
        self.rpc.close.assert_called_once()

    def test_disable_during_metadata_does_not_publish(self):
        self.enable()

        def disable(_):
            self.presence.configure(False)
            return {"title": "Трек"}

        self.metadata.side_effect = disable
        self.tick()
        self.rpc.set_activity.assert_not_called()
        self.tick()
        self.rpc.close.assert_called_once()

    def test_retry_after_pipe_break_and_shutdown_clears(self):
        self.enable()
        self.tick()
        self.rpc.poll.side_effect = OSError()
        second_rpc = Mock()
        self.connect.return_value = second_rpc
        self.clock.monotonic.side_effect = [11, 11, 12, 21, 21]
        self.presence._wake = Mock()
        def stop_after_reconnect(_timeout):
            if self.presence._wake.wait.call_count == 3:
                self.presence._stopping.set()

        self.presence._wake.wait.side_effect = stop_after_reconnect
        self.presence._run()

        self.assertEqual(self.connect.call_count, 2)
        self.rpc.close.assert_called_once()
        first_update, clear = second_rpc.set_activity.call_args_list
        self.assertEqual(first_update.args[0]["type"], 2)
        self.assertIsNone(clear.args[0])
        second_rpc.close.assert_called_once()


class DesktopSettingsTests(unittest.TestCase):
    def test_opt_in_and_legacy_settings(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LOCALAPPDATA": root}):
            api = DesktopApi()
            api._discord_presence = Mock()
            self.assertFalse(api.load_settings()["discord_presence_enabled"])
            api.save_settings("#112233")
            data = json.loads(api._settings_path.read_text(encoding="utf-8"))
            data["discord_application_id"] = APPLICATION_ID
            data["discord_presence_enabled"] = True
            api._settings_path.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(api.load_settings()["discord_presence_enabled"])
            self.assertNotIn("discord_application_id", api.load_settings())

            settings_bytes = api._settings_path.read_bytes()
            for invalid in (None, "0", 1):
                with self.assertRaises(ValueError):
                    api.save_discord_presence(invalid)
                self.assertEqual(api._settings_path.read_bytes(), settings_bytes)
            api.save_discord_presence(True)
            self.assertTrue(DesktopApi().load_settings()["discord_presence_enabled"])
            api._discord_presence.configure.assert_called_once_with(True)
            api.save_discord_presence(False)
            prefs = DesktopApi().load_settings()
            self.assertFalse(prefs["discord_presence_enabled"])
            self.assertNotIn("discord_application_id", prefs)
            self.assertEqual(prefs["accent"], "#112233")
