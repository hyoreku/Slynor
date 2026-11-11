"""Track/container regressions and durable new-download metadata, without live services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from unittest.mock import patch

from backend.download_metadata import (display_stem, publish_download, read_download_metadata,
                                       sidecar_path, write_missing_tags, remember_cached_metadata)
from backend.metadata import read_track_metadata
from backend.provider_worker import search_row, validate_track_url, ytdlp_rows
from backend.catalog_music import CatalogueLibrary


TRACK = {'title':'Тестовая песня','artist':'Исполнитель','album':'Тестовый альбом',
         'source':'youtube','permalink_url':'https://www.youtube.com/watch?v=abcdefghijk'}


def wav_file(path):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000)
        audio.writeframes(b'\x00\x00' * 2400)
    return path


class TrackValidationTests(unittest.TestCase):
    def extract(self, data):
        class FakeYoutubeDL:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, *args, **kwargs): return data
        with patch.dict(sys.modules, {'yt_dlp':types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)}):
            return ytdlp_rows('Synthetic query','youtube')

    def test_empty_search_container_cannot_become_a_track(self):
        for entries in ([],None,iter(())):
            with self.subTest(entries=type(entries).__name__):
                self.assertEqual(self.extract({'_type':'playlist','id':'A4','title':'A4','entries':entries}),[])

    def test_channel_playlist_and_container_rows_are_rejected(self):
        cases=[{'url':'https://www.youtube.com/channel/UCfixture','title':'A4'},
               {'url':'https://www.youtube.com/playlist?list=PLfixture','title':'Playlist'},
               {'_type':'playlist','id':'abcdefghijk','title':'Container'},
               {'url':'https://www.youtube.com/watch?v=abcdefghijk','entries':[]}]
        for item in cases:
            with self.subTest(item=item):self.assertIsNone(search_row(item,'youtube'))

    def test_mixed_flat_search_preserves_only_individual_media(self):
        result=self.extract({'_type':'playlist','entries':[
            {'_type':'url','url':'https://www.youtube.com/channel/UCfixture','title':'Channel'},
            {'_type':'url','id':'abcdefghijk','url':'abcdefghijk','title':'Song','artist':'Artist','duration':123.5},
            {'_type':'playlist','entries':[],'title':'Playlist'}]})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['url'],'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(result[0]['duration_ms'],123500)

    def test_malformed_video_ids_and_conflicting_ids_are_rejected(self):
        for identifier in ('A4','', 'abcdefghijkl', '../abcdefghijk'):
            with self.subTest(identifier=identifier),self.assertRaises(ValueError):
                validate_track_url('https://www.youtube.com/watch?v='+identifier,'youtube')
        self.assertIsNone(search_row({'id':'12345678901','url':'https://www.youtube.com/watch?v=abcdefghijk'},'youtube'))

    def test_track_url_normalization_removes_unrelated_query_parameters(self):
        for url in ('https://youtu.be/abcdefghijk?si=synthetic',
                    'https://music.youtube.com/watch?v=abcdefghijk&list=PLfixture',
                    'https://www.youtube.com/shorts/abcdefghijk'):
            self.assertEqual(validate_track_url(url,'youtube'),'https://www.youtube.com/watch?v=abcdefghijk')
        with self.assertRaises(ValueError):
            validate_track_url('https://www.youtube.com/watch?v=abcdefghijk&v=12345678901','youtube')

    def test_profiles_and_playlists_of_other_providers_are_not_tracks(self):
        for url,provider in [('https://soundcloud.com/artist','soundcloud'),
                             ('https://soundcloud.com/artist/sets/list','soundcloud'),
                             ('https://open.spotify.com/artist/abc','spotify'),
                             ('https://open.spotify.com/playlist/abc','spotify')]:
            with self.subTest(url=url),self.assertRaises(ValueError):validate_track_url(url,provider)
        self.assertEqual(validate_track_url('https://soundcloud.com/artist/song','soundcloud'),
                         'https://soundcloud.com/artist/song')

    def test_catalogue_rechecks_worker_rows_before_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib=CatalogueLibrary(tmp)
            try:
                rows=lib._catalogue_tracks([
                    {'provider':'youtube','url':'https://www.youtube.com/watch?v=A4','title':'A4'},
                    {'provider':'youtube','url':'https://www.youtube.com/channel/UCfixture','title':'Channel'},
                    {'provider':'youtube','kind':'artist','url':'https://www.youtube.com/watch?v=abcdefghijk'},
                    {'provider':'youtube','url':'https://www.youtube.com/watch?v=abcdefghijk','title':'Song'}])
                self.assertEqual([r['title'] for r in rows],['Song'])
            finally:lib.close()


class DownloadMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.folder=self.root/'Downloads'

    def source(self, name='input.wav'):
        return wav_file(self.root/name)

    def test_new_file_has_readable_name_and_metadata_after_reader_restart(self):
        target=publish_download(self.source(),self.folder,TRACK)
        self.assertEqual(target.name,'Исполнитель - Тестовая песня.wav')
        data=read_track_metadata(str(target))
        self.assertEqual((data['title'],data['artist'],data['album']),
                         (TRACK['title'],TRACK['artist'],TRACK['album']))
        # A fresh interpreter cannot use the old in-memory catalogue/cache.
        code='import json,sys;from backend.metadata import read_track_metadata;print(json.dumps(read_track_metadata(sys.stdin.read())))'
        result=subprocess.run([sys.executable,'-c',code],input=str(target),text=True,capture_output=True,check=True,
                              cwd=Path(__file__).resolve().parents[1],timeout=10)
        self.assertEqual(json.loads(result.stdout)['artist'],TRACK['artist'])

    def test_name_collision_does_not_replace_existing_audio_or_metadata(self):
        first=publish_download(self.source('first.wav'),self.folder,TRACK)
        before=first.read_bytes();metadata=sidecar_path(first).read_bytes()
        second=publish_download(self.source('second.wav'),self.folder,TRACK)
        self.assertEqual(second.name,'Исполнитель - Тестовая песня (2).wav')
        self.assertEqual(first.read_bytes(),before);self.assertEqual(sidecar_path(first).read_bytes(),metadata)

    def test_existing_orphan_companion_is_preserved(self):
        self.folder.mkdir()
        old=self.folder/'Исполнитель - Тестовая песня.wav.slynor.json';old.write_text('preserved')
        target=publish_download(self.source(),self.folder,TRACK)
        self.assertTrue(target.stem.endswith('(2)'));self.assertEqual(old.read_text(),'preserved')

    def test_stale_companion_cannot_relabel_replaced_file(self):
        target=publish_download(self.source(),self.folder,TRACK)
        self.assertTrue(read_download_metadata(target))
        st=target.stat();os.utime(target,ns=(st.st_atime_ns,st.st_mtime_ns+1000000000))
        self.assertEqual(read_download_metadata(target),{})

    def test_legacy_cache_reuse_keeps_audio_bytes_name_and_mtime(self):
        self.folder.mkdir();old=wav_file(self.folder/'legacy_abcd1234.wav')
        before=old.read_bytes();mtime=old.stat().st_mtime_ns
        self.assertTrue(remember_cached_metadata(old,TRACK))
        self.assertEqual(old.name,'legacy_abcd1234.wav');self.assertEqual(old.read_bytes(),before)
        self.assertEqual(old.stat().st_mtime_ns,mtime)
        self.assertEqual(read_track_metadata(str(old))['artist'],TRACK['artist'])

    def test_legacy_companion_is_not_replaced_on_cache_reuse(self):
        old=self.source();sidecar_path(old).write_text('user file; preserved')
        self.assertFalse(remember_cached_metadata(old,TRACK))
        self.assertEqual(sidecar_path(old).read_text(),'user file; preserved')

    def test_corrupt_oversized_and_symlink_companions_are_ignored(self):
        target=publish_download(self.source(),self.folder,TRACK);companion=sidecar_path(target)
        for value in ('not JSON','x'*40000,'[]'):
            companion.write_text(value);self.assertEqual(read_download_metadata(target),{})
        companion.unlink();other=self.root/'other.json';other.write_text('{}')
        try:companion.symlink_to(other)
        except OSError:self.skipTest('Symlinks unavailable')
        self.assertEqual(read_download_metadata(target),{})

    def test_companion_does_not_store_credentials_or_signed_media_urls(self):
        track={**TRACK,'access_token':'synthetic secret','url':'https://media.invalid/?signature=synthetic',
               'permalink_url':TRACK['permalink_url']+'&token=synthetic'}
        target=publish_download(self.source(),self.folder,track)
        data=sidecar_path(target).read_text(encoding='utf-8')
        self.assertNotIn('synthetic',data);self.assertNotIn('access_token',data);self.assertNotIn('signature',data)

    def test_failed_publication_preserves_existing_files_and_source(self):
        source=self.source();before=source.read_bytes()
        with patch('backend.download_metadata.os.replace',side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):publish_download(source,self.folder,TRACK)
        self.assertEqual(source.read_bytes(),before)
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_wav_text_tags_preserve_audio_samples(self):
        source=self.source()
        with wave.open(str(source),'rb') as f:before=f.readframes(f.getnframes())
        self.assertEqual(write_missing_tags(source,TRACK),'written')
        with wave.open(str(source),'rb') as f:self.assertEqual(f.readframes(f.getnframes()),before)
        data=read_track_metadata(str(source))
        self.assertEqual(data['title'],TRACK['title']);self.assertEqual(data['artist'],TRACK['artist'])

    def test_existing_tags_are_not_overwritten(self):
        source=self.source();write_missing_tags(source,TRACK);before=source.read_bytes()
        self.assertEqual(write_missing_tags(source,{'title':'Other','artist':'Other'}),'preserved')
        self.assertEqual(source.read_bytes(),before)

    def test_failed_tag_edit_keeps_original_audio(self):
        source=self.source();before=source.read_bytes()
        with patch('mutagen.wave.WAVE.save',side_effect=OSError('synthetic tag failure')):
            self.assertEqual(write_missing_tags(source,TRACK),'failed')
        self.assertEqual(source.read_bytes(),before)
        self.assertFalse(list(self.root.glob('.slynor-tags-*')))

    def test_unsupported_tag_container_uses_companion_metadata(self):
        source=self.root/'original.webm';source.write_bytes(b'synthetic container; unit metadata fixture only')
        self.assertEqual(write_missing_tags(source,TRACK),'unsupported')
        target=publish_download(source,self.folder,TRACK)
        with patch('backend.metadata.File',return_value=None):data=read_track_metadata(str(target))
        self.assertEqual(data['title'],TRACK['title']);self.assertEqual(data['artist'],TRACK['artist'])

    def test_catalogue_duration_does_not_become_an_unmeasured_file_duration(self):
        source=self.root/'original.webm';source.write_bytes(b'synthetic metadata fixture')
        target=publish_download(source,self.folder,{**TRACK,'duration_ms':300000})
        with patch('backend.metadata.File',return_value=None):data=read_track_metadata(str(target))
        self.assertIsNone(data['duration_ms'])

    def test_filename_handles_windows_names_and_unknown_artist(self):
        self.assertEqual(display_stem({'title':'CON'}),'_CON')
        self.assertEqual(display_stem({'title':'Name','artist':''}),'Name')
        for char in '<>:"/\\|?*':self.assertNotIn(char,display_stem({'title':'A'+char+'B'}))
        self.assertLessEqual(len(display_stem({'title':'😀'*150}).encode('utf-16-le')),200)


class CodecPreservationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required for generated codec fixtures')
    def test_supported_formats_keep_encoded_audio_packets(self):
        codecs={'.mp3':'libmp3lame','.m4a':'aac','.opus':'libopus','.ogg':'libvorbis',
                '.flac':'flac','.webm':'libopus','.aac':'aac'}
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);original=wav_file(folder/'source.wav')
            for extension,codec in codecs.items():
                with self.subTest(container=extension):
                    source=folder/('audio'+extension)
                    subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(original),'-c:a',codec,str(source)],
                                   check=True,capture_output=True,timeout=10)
                    def packets(p):
                        return subprocess.check_output(['ffmpeg','-v','error','-nostdin','-i',str(p),'-map','0:a:0',
                            '-c:a','copy','-f','hash','-hash','sha256','-'],timeout=10)
                    before=packets(source);status=write_missing_tags(source,TRACK)
                    self.assertEqual(status,'unsupported' if extension in ('.aac','.webm') else 'written')
                    self.assertEqual(packets(source),before)
                    target=publish_download(source,folder/'Downloads',TRACK)
                    data=read_track_metadata(str(target))
                    self.assertEqual(data['title'],TRACK['title']);self.assertEqual(data['artist'],TRACK['artist'])


if __name__=='__main__':unittest.main()
