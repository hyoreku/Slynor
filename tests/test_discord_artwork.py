from pathlib import Path
import tempfile
from time import time
import unittest
from unittest.mock import Mock, patch

import httpx

from backend.discord_artwork import DiscordArtwork, _releases, archive_cover_url
from backend.catalog_music import CatalogueLibrary


RELEASE = "11111111-1111-1111-1111-111111111111"
ARTWORK = f"https://archive.org/download/mbid-{RELEASE}/cover.jpg"
GROUP = "22222222-2222-2222-2222-222222222222"
YOUTUBE_PAGE = "https://www.youtube.com/watch?v=abcdefghijk"
YOUTUBE_IMAGE = "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"


class DiscordArtworkTests(unittest.TestCase):
    def recording(self, **overrides):
        return {
            "title": "Мой ненаглядный", "length": 190000,
            "artist-credit": [{"name": "IOWA", "joinphrase": " & "}, {"name": "Acid Minerale"}],
            "releases": [{"id": RELEASE, "title": "Песня", "status": "Official"}],
            **overrides,
        }

    def test_matches_release_artist_version_and_duration(self):
        original = self.recording()
        wrong = [
            self.recording(title="Мой ненаглядный (Live)"),
            self.recording(length=250000),
            self.recording(**{"artist-credit": [{"name": "Another musician"}]}),
            self.recording(releases=[{"id": RELEASE, "title": "Сборник", "status": "Bootleg"}]),
        ]
        self.assertEqual(_releases({"recordings": wrong}, "Мой ненаглядный", "IOWA & Acid Minerale", "", 190000), [])
        self.assertEqual(_releases({"recordings": [original]}, "Мой ненаглядный", "Iowa & Acid Minerale", "Песня", 190500), [RELEASE])
        self.assertEqual(_releases({"recordings": [original]}, "Мой ненаглядный", "Iowa & Acid Minerale", "Другой альбом", 190500), [])

    def test_public_cover_with_safe_redirect(self):
        requests = []

        def server(request):
            requests.append(request)
            if request.url.host == "musicbrainz.org":
                self.assertIn('recording:"Мой ненаглядный"', request.url.params["query"])
                return httpx.Response(200, json={"recordings": [self.recording()]})
            if request.url.host == "coverartarchive.org":
                return httpx.Response(307, headers={"Location": ARTWORK.replace("https://", "http://")})
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"})

        client = httpx.Client(transport=httpx.MockTransport(server))
        with patch("backend.discord_artwork.httpx.Client", return_value=client):
            url = DiscordArtwork._search("Мой ненаглядный", "Iowa & Acid Minerale", "Песня", 190000)
        self.assertEqual(url, ARTWORK)
        self.assertEqual([request.method for request in requests], ["GET", "HEAD", "HEAD"])
        self.assertEqual(requests[-1].url.scheme, "https")
        self.assertIsNone(archive_cover_url("https://archive.org/download/unrelated/a.jpg"))
        self.assertIsNone(archive_cover_url(ARTWORK.replace("archive.org", "127.0.0.1")))

    def test_persistent_cache_validates_url_and_expires(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "covers.sqlite3"
            worker = DiscordArtwork(path, "12345")
            worker._cached("track", (time() + 60, ARTWORK))
            worker._cached("old", (time() - 1, ARTWORK))
            worker._cached("private", (time() + 60, "http://localhost/secret.jpg"))
            restarted = DiscordArtwork(path, "12345")
            self.assertEqual(restarted._cached("track")[1], ARTWORK)
            self.assertIsNone(restarted._cached("old"))
            self.assertIsNone(restarted._cached("private"))

    def test_download_without_cached_image_uses_its_youtube_page(self):
        def server(request):
            self.assertEqual(str(request.url), YOUTUBE_IMAGE)
            self.assertEqual(request.method, "HEAD")
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"})

        client = httpx.Client(transport=httpx.MockTransport(server))
        with patch("backend.discord_artwork.httpx.Client", return_value=client):
            cover = DiscordArtwork._search(None, None, "", 0, source="youtube", page=YOUTUBE_PAGE)
        self.assertEqual(cover, YOUTUBE_IMAGE)

    def test_release_group_fallback_and_isrc_matching(self):
        release = {"id": RELEASE, "title": "Single", "status": "Official", "release-group": {"id": GROUP}}
        exact = self.recording(isrcs=["USABC1234567"], releases=[release])
        wrong = self.recording(isrcs=["USABC1234568"])

        def server(request):
            if request.url.host == "musicbrainz.org":
                self.assertIn("isrc:USABC1234567", request.url.params["query"])
                return httpx.Response(200, json={"recordings": [wrong, exact]})
            if request.url.path == f"/release/{RELEASE}/front-250":
                return httpx.Response(404)
            if request.url.path == f"/release-group/{GROUP}/front-250":
                return httpx.Response(307, headers={"Location": ARTWORK})
            self.assertEqual(str(request.url), ARTWORK)
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"})

        client = httpx.Client(transport=httpx.MockTransport(server))
        with patch("backend.discord_artwork.httpx.Client", return_value=client):
            cover = DiscordArtwork._search(None, None, "", 190000, isrc="USABC1234567")
        self.assertEqual(cover, ARTWORK)

    def test_track_page_thumbnail_from_oembed_is_public(self):
        examples = [
            ("spotify", "open.spotify.com", "https://open.spotify.com/track/123", "https://i.scdn.co/image/123"),
            ("soundcloud", "soundcloud.com", "https://soundcloud.com/artist/song", "https://i1.sndcdn.com/art.jpg"),
        ]
        for source, host, page, image in examples:
            with self.subTest(source=source):
                def server(request):
                    if request.url.host == host:
                        self.assertEqual(request.url.params["url"], page)
                        return httpx.Response(200, json={"thumbnail_url": image + "?token=private"})
                    self.assertEqual(str(request.url), image)
                    return httpx.Response(200, headers={"Content-Type": "image/jpeg"})

                client = httpx.Client(transport=httpx.MockTransport(server))
                with patch("backend.discord_artwork.httpx.Client", return_value=client):
                    self.assertEqual(DiscordArtwork._search(None, None, "", 0, source=source, page=page), image)

    def test_catalogue_reuses_safe_image_links_for_downloads_and_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            catalogue = CatalogueLibrary(Path(folder))
            self.addCleanup(catalogue.close)
            track = {"source": "youtube", "permalink_url": YOUTUBE_PAGE, "cover_url": None}
            restored = catalogue.track_cover(track)
            self.assertEqual(catalogue.cover_origin(restored["cover_url"]), YOUTUBE_IMAGE)
            worker = Mock()
            worker.lookup.return_value = ARTWORK
            played = catalogue.track_cover({**track, "path": "online:test"}, worker)
            self.assertEqual(catalogue.cover_origin(played["cover_url"]), ARTWORK)
            self.assertIsNone(track["cover_url"])

    def test_logo_resolves_a_registered_asset_id(self):
        worker = DiscordArtwork(Path("unused"), "12345")
        response = httpx.Response(200, request=httpx.Request("GET", "https://discord.com"),
                                  json=[{"name": "slynor", "id": "123456789012345679"}])
        with patch("backend.discord_artwork.httpx.get", return_value=response):
            worker._load_logo()
        self.assertEqual(worker.logo(), "123456789012345679")
