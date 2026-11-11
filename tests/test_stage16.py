"""Stage16 tests use doubles. Windows, real stream decoding and signing need native acceptance."""
import json,os,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import patch
from test_regressions import AudioEngine
from desktop import DesktopApi
from backend.catalog_music import CatalogueLibrary
from backend.internet_music import ProviderError
from backend.appearance import Appearance
from backend.provider_diagnostics import classify,record,snapshot

class ControlTests(unittest.TestCase):
 def setUp(self):self.e=AudioEngine()
 def tearDown(self):self.e.close()
 def test_pause_while_opening_no_error(self):self.e._player.state='opening';self.assertFalse(self.e.pause())
 def test_resume_while_buffering_no_error(self):self.e._player.state='buffering';self.assertFalse(self.e.resume())
 def test_repeated_pause_idempotent(self):self.e._player.state='paused';self.assertTrue(self.e.pause())
 def test_repeated_resume_idempotent(self):self.e._player.state='playing';self.assertTrue(self.e.resume())
 def test_output_probe_excludes_device_names(self):
  report=self.e.output_diagnostics();self.assertIn("device_count",report);self.assertNotIn("devices",report);self.assertTrue(report["uses_system_default"])
 def test_idle_still_not_valid_pause(self):
  with self.assertRaises(ValueError):self.e.pause()
 def test_native_preopening_exposed_as_opening(self):
  self.e._queue=['/fixture.mp3'];self.e._queue_index=0;self.e._current_filename='fixture.mp3';self.e._player.state='idle';self.assertEqual(self.e.get_state()['state'],'opening');self.assertFalse(self.e.pause())

class SettingsTests(unittest.TestCase):
 def test_transparent_and_icon_persist(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):
   api=DesktopApi();api.save_theme('transparent');api.save_app_icon('8');self.assertEqual(api.load_settings()['theme'],'transparent');self.assertEqual(api.load_settings()['app_icon'],'8')
 def test_icon_path_injection_rejected(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):
   with self.assertRaises(ValueError):DesktopApi().save_app_icon('../icon.svg')
 def test_transparent_appearance_model(self):self.assertEqual(Appearance(theme='transparent').theme,'transparent')
 def test_all_icon_files_exist(self):
  for i in range(1,9):self.assertTrue((Path(__file__).parents[1]/f'frontend/assets/app-icons/{i}.svg').is_file())

class CatalogueFastTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.lib=CatalogueLibrary(self.tmp.name);self.ref='online:catalog:'+'a'*32;self.track={'path':self.ref,'source':'youtube','title':'Song','artist':'Artist','access':'prepare','permalink_url':'https://www.youtube.com/watch?v=PPIZ5SpWRGA'};self.lib.catalogue[self.ref]=self.track
 def tearDown(self):self.lib.close();self.tmp.cleanup()
 def wait(self,key):
  deadline=time.monotonic()+3
  while time.monotonic()<deadline:
   job=self.lib.job(key)
   if job['status'] not in ('queued','running'):return job
   time.sleep(.01)
  self.fail('job did not finish')
 def test_catalogue_metadata_readable(self):self.assertEqual(self.lib.metadata(self.ref)['title'],'Song')
 def test_stream_no_download_or_ffmpeg(self):
  with patch.object(self.lib,'_run_provider',return_value={'stream_url':'https://rr1.googlevideo.com/videoplayback?token=secret'}),patch('socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]):job=self.wait(self.lib.start_stream(self.ref)['id'])
  self.assertEqual(job['status'],'done');self.assertEqual(job['ref'],self.ref);self.assertNotIn('stream_url',job);self.assertTrue(self.lib.play_url(self.ref).startswith('http://127.0.0.1:8765/api/online/media/'));self.assertFalse((Path(self.tmp.name)/'Downloads').exists())
 def test_stream_reuses_live_resolution(self):
  self.lib.ready_streams[self.ref]={'url':'https://rr1.googlevideo.com/a','until':time.monotonic()+120}
  with patch.object(self.lib,'_run_provider',side_effect=AssertionError('should not resolve again')):job=self.wait(self.lib.start_stream(self.ref)['id'])
  self.assertEqual(job['status'],'done')
 def test_stream_arbitrary_or_private_rejected(self):
  for url in ('https://evil.test/a','http://rr1.googlevideo.com/a','https://rr1.googlevideo.com:88/a'):
   with patch.object(self.lib,'_run_provider',return_value={'stream_url':url}):self.assertEqual(self.wait(self.lib.start_stream(self.ref)['id'])['status'],'failed')
 def test_stream_private_dns_rejected(self):
  with patch.object(self.lib,'_run_provider',return_value={'stream_url':'https://rr1.googlevideo.com/a'}),patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):self.assertEqual(self.wait(self.lib.start_stream(self.ref)['id'])['status'],'failed')
 def test_stream_wrong_provider_rejected(self):
  self.lib.catalogue[self.ref]['source']='spotify'
  with self.assertRaises(ProviderError):self.lib.start_stream(self.ref)
 def test_cached_file_fast_path(self):
  folder=Path(self.tmp.name)/'Downloads';folder.mkdir();file=folder/'cached.mp3';file.write_bytes(b'fixture');self.lib.downloaded[self.ref]=str(file)
  with patch.object(self.lib,'_run_provider',side_effect=AssertionError('must reuse')):job=self.wait(self.lib.start_stream(self.ref)['id'])
  self.assertEqual(job['path'],str(file));self.assertEqual(self.lib.play_url(self.ref),file.as_uri())
 def test_cache_persists_after_restart(self):
  folder=Path(self.tmp.name)/'Downloads';folder.mkdir();file=folder/'cached.mp3';file.write_bytes(b'fixture');self.lib.downloaded[self.ref]=str(file);self.lib._write_download_index()
  other=CatalogueLibrary(self.tmp.name)
  try:self.assertEqual(Path(other.downloaded[self.ref]).resolve(),file.resolve())
  finally:other.close()
 def test_cache_index_cannot_escape_downloads(self):
  self.lib.download_index.write_text(json.dumps({self.ref:'../secret.mp3'}));self.lib._read_download_index();self.assertNotIn(self.ref,self.lib.downloaded)
 def test_download_jobs_deduplicated(self):
  from threading import Event
  gate=Event()
  def block(*args):gate.wait(2)
  try:
   with patch.object(self.lib,'_catalogue_download',side_effect=block):a=self.lib.start_download(self.ref,True);b=self.lib.start_download(self.ref,True);self.assertEqual(a['id'],b['id'])
  finally:gate.set()
 def test_artwork_alternative_once(self):
  key='b'*64;self.lib.cover_fallbacks[key]='https://i.ytimg.com/vi/PPIZ5SpWRGA/hqdefault.jpg';self.lib.cover_sources={key:'https://i.ytimg.com/primary.jpg'}
  with patch.object(self.lib,'_fetch_cover_file',side_effect=[ProviderError('unavailable'),Path('/fixture.jpg')]) as fetch:self.assertEqual(self.lib.cover_file(key),Path('/fixture.jpg'));self.assertEqual(fetch.call_count,2)

class DiagnosticsTests(unittest.TestCase):
 def test_error_classification(self):self.assertEqual(classify('Sign in to confirm you are not a bot'),'auth_required');self.assertEqual(classify('No supported JavaScript runtime'),'runtime_missing')
 def test_no_raw_secrets_recorded(self):
  record('youtube','stream',1.23,classify('https://googlevideo.com/?token=secret cookies required'))
  text=json.dumps(snapshot()[-1]);self.assertNotIn('https:',text);self.assertNotIn('secret',text);self.assertNotIn('cookies',text)
 def test_events_bounded(self):
  for i in range(80):record('youtube','stream',0)
  self.assertLessEqual(len(snapshot()),60)

class StallApiTests(unittest.TestCase):
 def setUp(self):
  from fastapi.testclient import TestClient
  from backend.main import app
  from backend.local_session import APP_TOKEN
  self.client=TestClient(app,base_url='http://127.0.0.1:8765',headers={'X-OwnPlayer-Token':APP_TOKEN})
 def tearDown(self):self.client.close()
 def test_stall_schema_rejects_private_text(self):
  self.assertEqual(self.client.post('/api/diagnostics/ui-stall',json={'duration_ms':5000,'screen':'C:/private/song.mp3'}).status_code,422)
 def test_stall_rate_limited(self):
  with patch('backend.main._stall_report_at',0),patch('time.monotonic',return_value=100),patch('backend.diagnostics.dump_stacks') as dump:
   a=self.client.post('/api/diagnostics/ui-stall',json={'duration_ms':5000,'screen':'playing'});b=self.client.post('/api/diagnostics/ui-stall',json={'duration_ms':5000,'screen':'playing'})
   self.assertTrue(a.json()['recorded']);self.assertFalse(b.json()['recorded']);dump.assert_called_once()
