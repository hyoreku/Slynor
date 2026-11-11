"""Native/provider test doubles. Does not certify Windows or live downloads."""
import json
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import patch
from test_regressions import AudioEngine
from test_stage12_shell_api import Window, Event
from desktop_shell import DesktopShell
from backend.catalog_music import CatalogueLibrary, cover_url
from backend.provider_worker import validate_url, search
from backend.internet_music import ProviderError

class NativeModeTests(unittest.TestCase):
 def setUp(self):
  self.window=Window();self.api=types.SimpleNamespace(load_settings=lambda:{},save_mini_position=lambda x,y:None);self.shell=DesktopShell(self.window,self.api)
 def tearDown(self):self.shell.quit()
 def mini(self):
  mini=Window()
  with patch('desktop_shell.webview.create_window',return_value=mini,create=True) as create:self.shell.show_mini()
  return create.call_args.kwargs
 def test_compact_size(self):
  args=self.mini();self.assertEqual((args['width'],args['height']),(360,112));self.assertEqual(args['min_size'],(320,108))
 def test_maximized_restored_not_normalized(self):
  self.window.maximize=lambda:self.window.calls.append('maximize');self.shell.main_mode='maximized';self.mini();self.shell.show_main()
  self.assertIn('maximize',self.window.calls);self.assertNotIn('restore',self.window.calls)
 def test_normal_show_does_not_call_restore(self):self.mini();self.shell.show_main();self.assertNotIn('restore',self.window.calls)
 def test_fullscreen_preserved(self):
  self.window.fullscreen=True;self.window.toggle_fullscreen=lambda:self.fail('Already fullscreen; do not toggle off');self.mini();self.shell.show_main();self.assertEqual(self.shell.return_mode,'fullscreen')
 def test_minimized_can_restore(self):self.shell.main_minimized=True;self.shell.show_main();self.assertIn('restore',self.window.calls)

class CatalogueTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.lib=CatalogueLibrary(self.tmp.name)
  # Optional msoc and user OAuth configuration must never turn unit tests into live searches.
  for target,method,value in [(self.lib,'_msoc',[]),(self.lib.provider,'configured',False)]:
   fixture=patch.object(target,method,return_value=value);fixture.start();self.addCleanup(fixture.stop)
 def tearDown(self):self.lib.close();self.tmp.cleanup()
 def wait(self,key):
  deadline=time.monotonic()+3
  while time.monotonic()<deadline:
   data=self.lib.job(key)
   if data['status'] not in ('running','queued'):return data
   time.sleep(.01)
  self.fail('Job did not terminate')
 def row(self):return {'provider':'spotify','url':'https://open.spotify.com/track/abc','title':'Track','artist':'Artist','cover_url':'https://i.scdn.co/image/a','duration_ms':1000}
 def get(self):
  with patch.object(self.lib,'_run_provider',return_value={'tracks':[self.row()]}):return self.wait(self.lib.start_search('test song')['id'])['tracks'][0]
 def test_spotify_metadata_preserved(self):
  row=self.get();self.assertEqual(row['title'],'Track');self.assertTrue(row['cover_url'].startswith('/api/assets/online-covers/'));self.assertIn('https://i.scdn.co/image/a',self.lib.cover_sources.values());self.assertEqual(row['access'],'prepare')
 def test_catalogue_does_not_advertise_direct_spotify_audio(self):self.assertNotIn('_url',self.get())
 def test_unknown_provider_url_rejected(self):
  for url,p in [('http://open.spotify.com/track/1','spotify'),('https://open.spotify.com.evil.test/t','spotify'),('https://user:secret@soundcloud.com/a/b','soundcloud'),('https://youtube.com:88/watch?v=x','youtube')]:
   with self.subTest(url=url),self.assertRaises(ValueError):validate_url(url,p)
 def test_artwork_allowlist(self):self.assertIsNone(cover_url('https://evil.test/a.jpg'));self.assertIsNone(cover_url('http://i.scdn.co/a'));self.assertEqual(cover_url('https://i.ytimg.com/a'),'https://i.ytimg.com/a')
 def test_arbitrary_input_url_not_fetched(self):
  with self.assertRaises(RuntimeError):search('https://127.0.0.1/private')
 def test_search_falls_back_between_catalogues(self):
  def run(key,payload,timeout):
   if payload['provider']=='spotify':raise ProviderError('unavailable')
   return {'tracks':[{**self.row(),'provider':'youtube','url':'https://www.youtube.com/watch?v=abcdefghijk'}]}
  with patch.object(self.lib,'_run_provider',side_effect=run):data=self.wait(self.lib.start_search('test song')['id'])
  self.assertEqual(data['tracks'][0]['source'],'youtube')
 def test_public_fallback_when_catalogues_empty(self):
  with patch.object(self.lib,'_run_provider',return_value={'tracks':[]}),patch.object(self.lib,'_archive',return_value=[]):data=self.wait(self.lib.start_search('test song')['id'])
  self.assertEqual(data['status'],'done')
 def test_download_confirmation_still_required_by_api(self):
  with self.assertRaises(ProviderError):self.lib.start_download(self.get()['path'],False)
 def test_failed_download_has_row_job_error(self):
  row=self.get()
  with patch.object(self.lib,'_run_provider',side_effect=ProviderError('FFmpeg not found')):data=self.wait(self.lib.start_download(row['path'],True)['id'])
  self.assertEqual(data['status'],'failed');self.assertIn('FFmpeg',data['error'])
 def test_external_path_rejected(self):
  row=self.get()
  with patch.object(self.lib,'_run_provider',return_value={'file':'/etc/passwd'}):data=self.wait(self.lib.start_download(row['path'],True)['id'])
  self.assertEqual(data['status'],'failed')
 def test_missing_provider_python_uses_public_fallback(self):
  self.lib.provider_python='/does/not/exist/python'
  with patch.object(self.lib,'_archive',return_value=[]):data=self.wait(self.lib.start_search('test song')['id'])
  self.assertEqual(data['status'],'done')
 def test_malformed_provider_response_does_not_leave_job_running(self):
  with patch.object(self.lib,'_run_provider',return_value=None),patch.object(self.lib,'_archive',return_value=[]):data=self.wait(self.lib.start_search('test song')['id'])
  self.assertEqual(data['status'],'done')
 def test_successful_download_validated_and_cached(self):
  import base64,re
  payload=re.search(r"base64.b64decode\('([^']+)'\)",Path(__file__).with_name('test_stage13.py').read_text(encoding='utf-8')).group(1);audio=base64.b64decode(payload);row=self.get()
  def run(key,payload,timeout):
   p=Path(payload['folder'])/'audio.mp3';p.write_bytes(audio);return {'file':str(p)}
  with patch.object(self.lib,'_run_provider',side_effect=run):data=self.wait(self.lib.start_download(row['path'],True)['id'])
  from audio_assertions import mpeg_payload
  from backend.metadata import read_track_metadata
  self.assertEqual(data['status'],'done');self.assertEqual(mpeg_payload(Path(data['path']).read_bytes()),mpeg_payload(audio))
  self.assertEqual(read_track_metadata(data['path'])['artist'],'Artist')
  with patch.object(self.lib,'_run_provider',side_effect=AssertionError('must use cache')):second=self.wait(self.lib.start_download(row['path'],True)['id'])
  self.assertEqual(second['path'],data['path'])

class MetadataSnapshotTests(unittest.TestCase):
 def test_state_snapshot_uses_warmed_cache_not_disk(self):
  from backend.main import app,cache_metadata,get_player_state
  app.state.audio_engine=AudioEngine()
  try:
   path='/test/song.mp3';cache_metadata({'path':path,'title':'Cached','cover_url':None})
   with patch.object(app.state.audio_engine,'get_state',return_value={'file_path':path}),patch('backend.main.read_track_metadata',side_effect=AssertionError('No disk read in state polling')):
    self.assertEqual(get_player_state()['current_metadata']['title'],'Cached')
  finally:app.state.audio_engine.close()

class ProviderAdapterTests(unittest.TestCase):
 def test_scdl_fixed_arguments_and_output(self):
  import sys
  from backend.provider_worker import download
  module=types.ModuleType('scdl.scdl');parent=types.ModuleType('scdl');parent.scdl=module;arguments=[]
  with tempfile.TemporaryDirectory() as folder:
   def run():arguments.extend(sys.argv);(Path(folder)/'fixture.mp3').write_bytes(b'fixture')
   module._main=run
   with patch.dict(sys.modules,{'scdl':parent,'scdl.scdl':module}),patch.object(sys,'argv',[]),patch('backend.provider_worker.shutil.which',return_value='/ffmpeg'),patch.dict('os.environ',{},clear=False):
    result=download({'provider':'soundcloud','url':'https://soundcloud.com/a/b','folder':folder,'audio_format':'mp3'})
   self.assertEqual(Path(result['file']).name,'fixture.mp3');self.assertIn('--no-playlist',arguments);self.assertNotIn('--remove',arguments);self.assertNotIn('--sync',arguments)
 def test_spotdl_keeps_native_bitrate_and_metadata(self):
  import sys
  from backend.provider_worker import download
  package=types.ModuleType('spotdl');songmodule=types.ModuleType('spotdl.types.song');settings={}
  with tempfile.TemporaryDirectory() as folder:
   class FakeSpotdl:
    def __init__(self,**kwargs):settings.update(kwargs['downloader_settings'])
    def download(self,song):p=Path(folder)/'id.mp3';p.write_bytes(b'fixture');return song,p
   package.Spotdl=FakeSpotdl;songmodule.Song=types.SimpleNamespace(from_url=lambda u:object())
   with patch.dict(sys.modules,{'spotdl':package,'spotdl.types.song':songmodule}),patch('backend.provider_worker.shutil.which',return_value='/ffmpeg'):
    download({'provider':'spotify','url':'https://open.spotify.com/track/abc','folder':folder,'audio_format':'mp3'})
   self.assertEqual(settings['bitrate'],'auto');self.assertIn('youtube-music',settings['audio_providers']);self.assertEqual(settings['threads'],1)
 def test_worker_requires_ffmpeg_before_download(self):
  from backend.provider_worker import download
  with tempfile.TemporaryDirectory() as folder,patch('backend.provider_worker.shutil.which',return_value=None),patch.dict('os.environ',{'OWNPLAYER_FFMPEG':''}):
   with self.assertRaisesRegex(RuntimeError,'FFmpeg'):download({'provider':'youtube','url':'https://www.youtube.com/watch?v=abcdefghijk','folder':folder,'audio_format':'mp3'})
