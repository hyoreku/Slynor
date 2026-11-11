"""Hermetic regressions for Windows reports; no real VLC/Windows certification."""
import io,json,tempfile,time,types,unittest
from pathlib import Path
from threading import Event,Thread
from unittest.mock import patch,Mock
import httpx
from PIL import Image
from test_regressions import AudioEngine
from test_stage12_shell_api import Window
from backend.catalog_music import CatalogueLibrary
from backend.internet_music import ProviderError
from backend.provider_diagnostics import snapshot
from desktop_shell import DesktopShell

PUBLIC=[(2,1,6,'',('8.8.8.8',443))]
class LibraryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.lib=CatalogueLibrary(self.tmp.name);self.ref='online:catalog:'+'a'*32
  self.lib.catalogue[self.ref]={'path':self.ref,'source':'youtube','title':'Song','artist':'Artist','permalink_url':'https://www.youtube.com/watch?v=abcdefghijk'}
 def tearDown(self):self.lib.close();self.tmp.cleanup()
 def network(self,callback):
  self.lib.web.close();self.lib.web=httpx.Client(transport=httpx.MockTransport(callback),follow_redirects=False)
  return patch('socket.getaddrinfo',return_value=PUBLIC)
 def ready(self,headers=None):
  self.remote='https://rr1.googlevideo.com/audio?secret=PRIVATE_SIGNED_URL'
  self.lib.ready_streams[self.ref]={'url':self.remote,'until':time.monotonic()+100,'headers':headers or {}}
  local=self.lib.play_url(self.ref);parts=local.split('/');return local,parts[-2]
 def key(self):return self.lib.cached_cover_url('https://i.ytimg.com/vi/abc/hq720.jpg').split('/')[-1][:-4]
 def test_resolved_stream_is_reused_for_later_plays(self):
  key=self.lib._new_job('stream')
  with patch.object(self.lib,'_run_provider',return_value={'stream_url':'https://rr1.googlevideo.com/a?x=1','http_headers':{}}),patch('socket.getaddrinfo',return_value=PUBLIC):
   self.lib._prepare_stream(key,{'path':self.ref,'source':'youtube','permalink_url':'https://www.youtube.com/watch?v=abcdefghijk'})
  job=self.lib.job(key)
  self.assertEqual(job['status'],'done',job.get('error'))
  ready=self.lib.ready_streams[self.ref]
  # A resolved googlevideo URL is valid for hours; reuse it instead of paying
  # a new yt-dlp extraction on every play.
  self.assertGreater(ready['until']-time.monotonic(),600)
 def wait(self,key):
  deadline=time.monotonic()+2
  while time.monotonic()<deadline:
   data=self.lib.job(key)
   if data['status'] not in ('queued','running'):return data
   time.sleep(.005)
  self.fail('mock job not terminated')
 def test_optional_msoc_installed_fallback_never_uses_network(self):
  with patch.object(self.lib,'_run_provider',return_value={'tracks':[]}),patch.object(self.lib.provider,'configured',return_value=False),patch('backend.internet_music.importlib.util.find_spec',return_value=object()),patch.object(self.lib,'_msoc',return_value=[]) as msoc,patch.object(self.lib,'_archive',return_value=[]),patch.object(self.lib.web,'send',side_effect=AssertionError('live HTTP forbidden')),patch('subprocess.Popen',side_effect=AssertionError('live process forbidden')):
   data=self.wait(self.lib.start_search('test song')['id'])
  self.assertEqual(data['status'],'done');self.assertEqual(msoc.call_count,2)
 def test_malformed_result_terminates_even_with_msoc(self):
  with patch.object(self.lib,'_run_provider',return_value=None),patch.object(self.lib.provider,'configured',return_value=False),patch('backend.internet_music.importlib.util.find_spec',return_value=object()),patch.object(self.lib,'_msoc',return_value=[]),patch.object(self.lib,'_archive',return_value=[]):self.assertEqual(self.wait(self.lib.start_search('test song')['id'])['status'],'done')
 def test_proxy_keeps_signed_url_out_of_handle(self):
  local,handle=self.ready();self.assertNotIn('PRIVATE',local);self.assertEqual(self.lib.resource(handle,'master'),self.remote)
 def test_proxy_preserves_range_and_safe_resolver_headers(self):
  local,handle=self.ready({'User-Agent':'resolver-agent','Referer':'https://www.youtube.com/','Cookie':'private','Authorization':'private','X-Test':'bad','bad':'a\r\nb'})
  seen=[]
  def response(r):seen.append(r);return httpx.Response(206,headers={'content-type':'audio/mp4','content-range':'bytes 0-3/4'},content=b'abcd')
  with self.network(response):
   result,_=self.lib.open_media(self.lib.resource(handle,'master'),'bytes=0-3');self.assertEqual(result.read(),b'abcd');result.close()
  self.assertEqual(seen[0].headers['range'],'bytes=0-3');self.assertEqual(seen[0].headers['user-agent'],'resolver-agent');self.assertNotIn('cookie',seen[0].headers);self.assertNotIn('authorization',seen[0].headers)
 def test_headers_reject_newlines_and_oversize(self):self.assertEqual(self.lib._stream_headers({'User-Agent':'a\r\nb','Referer':'a'*2049,'cookie':'a'}),{})
 def test_unregistered_stream_rejected_before_network(self):
  with patch.object(self.lib.web,'send',side_effect=AssertionError('not registered')),self.assertRaises(ProviderError):self.lib.open_media('https://rr1.googlevideo.com/audio')
 def test_proxy_rejects_private_dns(self):
  self.ready()
  with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),self.assertRaises(ProviderError):self.lib.open_media(self.remote)
 def test_proxy_rejects_redirect_outside_cdn(self):
  self.ready()
  with self.network(lambda r:httpx.Response(302,headers={'location':'https://127.0.0.1/private'})),self.assertRaises(ProviderError):self.lib.open_media(self.remote)
 def test_proxy_http_error_has_safe_diagnostic(self):
  self.ready()
  with self.network(lambda r:httpx.Response(403,content=b'private details')),self.assertRaises(ProviderError):self.lib.open_media(self.remote)
  event=snapshot()[-1];self.assertEqual(event['code'],'stream_http_403');self.assertNotIn('PRIVATE',json.dumps(event))
 def test_proxy_html_is_not_playable(self):
  self.ready()
  with self.network(lambda r:httpx.Response(200,headers={'content-type':'text/html'},content=b'login')),self.assertRaises(ProviderError):self.lib.open_media(self.remote)
 def test_cover_timeout_no_double_delay_and_negative_cache(self):
  key=self.key();self.lib.cover_fallbacks[key]='https://i.ytimg.com/vi/abc/hqdefault.jpg';calls=[]
  def fail(r):calls.append(r);raise httpx.ConnectTimeout('sensitive url '+str(r.url))
  with self.network(fail):
   for _ in range(3):
    with self.assertRaises(ProviderError):self.lib.cover_file(key)
  self.assertEqual(len(calls),1);self.assertEqual(snapshot()[-1]['code'],'image_connect_timeout');self.assertNotIn('sensitive',json.dumps(snapshot()[-1]))
 def test_cover_http_diagnostic(self):
  key=self.key()
  with self.network(lambda r:httpx.Response(404)),self.assertRaises(ProviderError):self.lib.cover_file(key)
  self.assertEqual(snapshot()[-1]['code'],'image_http_404')
 def test_cover_dns_diagnostic(self):
  import socket
  key=self.key()
  with patch('socket.getaddrinfo',side_effect=socket.gaierror('private')),self.assertRaises(ProviderError):self.lib.cover_file(key)
  self.assertEqual(snapshot()[-1]['code'],'image_dns')
 def test_cover_concurrent_requests_share_fetch(self):
  key=self.key();started=Event();release=Event();calls=[];results=[];errors=[];b=io.BytesIO();Image.new('RGB',(4,4),'red').save(b,'PNG')
  def response(r):calls.append(r);started.set();release.wait(2);return httpx.Response(200,content=b.getvalue())
  def load():
   try:results.append(self.lib.cover_file(key))
   except Exception as e:errors.append(e)
  with self.network(response):
   first=Thread(target=load);first.start();self.assertTrue(started.wait(1));second=Thread(target=load);second.start();release.set();first.join(2);second.join(2)
  self.assertFalse(errors);self.assertEqual(len(results),2);self.assertEqual(len(calls),1)
 def test_stale_proxy_error_cannot_poison_new_song(self):
  e=AudioEngine()
  try:
   e._queue=['new.mp3'];e._queue_index=0;e._current_stream_url='new';e.report_stream_error('old','expired');self.assertIsNone(e.get_state()['playback_error'])
  finally:e.close()

class ShellLifecycleTests(unittest.TestCase):
 def test_quit_wakes_workers_and_does_not_save_stale_position(self):
  save=Mock();s=DesktopShell(Window(),types.SimpleNamespace(load_settings=lambda:{},save_mini_position=save));s._pending_position=(120,35);s.quit();s.worker.join(1);s.watchdog.join(1)
  self.assertFalse(s.worker.is_alive());self.assertFalse(s.watchdog.is_alive());save.assert_not_called();self.assertEqual(s.commands.unfinished_tasks,0)
 def test_position_persisted_while_running(self):
  save=Mock();s=DesktopShell(Window(),types.SimpleNamespace(load_settings=lambda:{},save_mini_position=save))
  # Immediate deterministic wakeup without a two-second sleep.
  class Wake:
   def __init__(self):self.calls=0
   def wait(self,n):self.calls+=1;return self.calls>1
  try:
   s.shutdown_event.set();s.watchdog.join(1);s.shutdown_event=Wake();s._pending_position=(12,34);s._watchdog();save.assert_called_once_with(12,34)
  finally:s.shutdown_event=Event();s.quit();s.worker.join(1)

class ProxyApiTests(unittest.TestCase):
 def test_upstream_failure_reaches_current_playback_state(self):
  from fastapi.testclient import TestClient
  from backend.main import app
  handle='stage17';local='http://127.0.0.1:8765/api/online/media/'+handle+'/master';engine=AudioEngine()
  engine._queue=['online:catalog:'+'1'*32];engine._queue_index=0;engine._current_stream_url=local
  fake=types.SimpleNamespace(resource=lambda h,r:'https://rr1.googlevideo.com/audio',open_media=Mock(side_effect=ProviderError('Источник отказал (HTTP 403).')))
  client=TestClient(app,base_url='http://127.0.0.1:8765')
  try:
   with patch.object(app.state,'online',fake,create=True),patch.object(app.state,'audio_engine',engine,create=True):
    result=client.get('/api/online/media/'+handle+'/master')
    self.assertEqual(result.status_code,503);self.assertIn('403',engine.get_state()['playback_error'])
  finally:client.close();engine.close()
 def test_midstream_network_failure_reports_safe_error(self):
  from fastapi.testclient import TestClient
  from backend.main import app
  handle='stage17';local='http://127.0.0.1:8765/api/online/media/'+handle+'/master';engine=AudioEngine()
  engine._queue=['online:catalog:'+'1'*32];engine._queue_index=0;engine._current_stream_url=local
  class Broken(httpx.SyncByteStream):
   def __iter__(self):
    yield b'audio'
    raise httpx.ReadTimeout('sensitive signed URL')
  response=httpx.Response(200,headers={'content-type':'audio/mp4'},stream=Broken())
  fake=types.SimpleNamespace(resource=lambda h,r:'https://rr1.googlevideo.com/audio',open_media=lambda u,r:(response,u));client=TestClient(app,base_url='http://127.0.0.1:8765')
  try:
   with patch.object(app.state,'online',fake,create=True),patch.object(app.state,'audio_engine',engine,create=True):
    result=client.get('/api/online/media/'+handle+'/master')
    self.assertEqual(result.status_code,200);self.assertIn('прервалось',engine.get_state()['playback_error']);self.assertNotIn('sensitive',json.dumps(snapshot()[-1]));self.assertTrue(response.is_closed)
  finally:client.close();engine.close()
