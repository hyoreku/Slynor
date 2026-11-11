"""Deterministic network/native doubles. No Windows/VLC/audio-device claims."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
from backend.internet_music import InternetLibrary, ProviderError, public_url, MAX_AUDIO
from window_geometry import position

class GeometryTests(unittest.TestCase):
 def test_center(self):self.assertEqual(position(1000,800,area=(0,0,1920,1040)),(460,120))
 def test_top_center(self):self.assertEqual(position(440,250,top=True,area=(0,0,1920,1040)),(740,16))
 def test_negative_monitor(self):self.assertEqual(position(440,250,top=True,area=(-1920,0,1920,1040)),(-1180,16))
 def test_offscreen_returns_to_top(self):self.assertEqual(position(440,250,[-8000,-3000],top=True,area=(0,0,1920,1040)),(740,16))
 def test_clamp(self):self.assertEqual(position(440,250,[1900,1000],area=(0,0,1920,1040)),(1480,790))

class InternetTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.lib=InternetLibrary(self.tmp.name)
 def tearDown(self):self.lib.close();self.tmp.cleanup()
 def track(self):return self.lib.register('Title','Artist','https://archive.org/download/item/audio.mp3','archive')
 def wait(self,key):
  deadline=time.monotonic()+3
  while time.monotonic()<deadline:
   result=self.lib.job(key)
   if result['status'] not in ('queued','running'):return result
   time.sleep(.01)
  self.fail('Job did not terminate')
 def mock_web(self,handler):
  self.lib.web.close();self.lib.web=httpx.Client(transport=httpx.MockTransport(handler))
 def test_url_allowlist(self):
  for u in ['http://archive.org/audio.mp3','https://archive.org.evil.test/a','https://evilarchive.org/a','https://u:p@archive.org/a','https://archive.org:8888/a','https://127.0.0.1/a','file:///etc/passwd']:
   with self.subTest(u=u),self.assertRaises(ProviderError):public_url(u)
 def test_private_dns_rejected(self):
  with patch('backend.internet_music.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),self.assertRaises(ProviderError):public_url('https://archive.org/a',True)
 def test_internal_url_not_exposed(self):self.assertNotIn('_url',self.track())
 def test_metadata_survives_pin_restart(self):
  t=self.track();self.lib.pin(t['path']);other=InternetLibrary(self.tmp.name)
  try:self.assertEqual(other.metadata(t['path'])['title'],'Title')
  finally:other.close()
 def test_corrupt_registry_not_overwritten(self):
  path=Path(self.tmp.name)/'public_tracks.json';path.write_text('broken');other=InternetLibrary(self.tmp.name)
  try:
   track=other.register('T','A','https://archive.org/a.mp3','archive')
   with self.assertRaises(ProviderError):other.pin(track['path'])
   self.assertEqual(path.read_text(),'broken')
  finally:other.close()
 def test_stream_uses_opaque_local_handle(self):
  t=self.track();url=self.lib.play_url(t['path']);self.assertTrue(url.startswith('http://127.0.0.1:8765/api/online/media/'))
  h=url.split('/')[-2];self.assertEqual(self.lib.resource(h,'master'),'https://archive.org/download/item/audio.mp3')
 def test_wrong_resource_rejected(self):
  url=self.lib.play_url(self.track()['path'])
  with self.assertRaises(ProviderError):self.lib.resource(url.split('/')[-2],'arbitrary')
 def test_search_job_completes(self):
  with patch.object(self.lib,'_archive',return_value=[self.track()]):
   result=self.wait(self.lib.start_search('hello','archive')['id'])
  self.assertEqual(result['status'],'done');self.assertEqual(len(result['tracks']),1)
 def test_search_failure_does_not_crash_worker(self):
  with patch.object(self.lib,'_archive',side_effect=RuntimeError('private-url-secret')):
   result=self.wait(self.lib.start_search('hello','archive')['id'])
  self.assertEqual(result['status'],'done');self.assertNotIn('private-url-secret',str(result));self.assertTrue(result['warnings'])
 def test_vk_status_honest(self):self.assertFalse(next(s for s in self.lib.status()['sources'] if s['id']=='vk')['configured'])
 def test_vk_job_reports_not_connected(self):self.assertEqual(self.wait(self.lib.start_search('hello','vk')['id'])['status'],'failed')
 def test_download_requires_confirmation(self):
  with self.assertRaises(ProviderError):self.lib.start_download(self.track()['path'],False)
 def test_download_rejects_unknown_ref(self):
  with self.assertRaises(ProviderError):self.lib.start_download('online:web:'+'0'*32,True)
 def test_download_rejects_html(self):
  self.mock_web(lambda r:httpx.Response(200,text='<html>login</html>',headers={'content-type':'text/html'}))
  with patch('backend.internet_music.public_url',side_effect=lambda url,resolve=False:url):r=self.wait(self.lib.start_download(self.track()['path'],True)['id'])
  self.assertEqual(r['status'],'failed');self.assertFalse(list(Path(self.tmp.name).rglob('*.part')))
 def test_download_rejects_oversize(self):
  self.mock_web(lambda r:httpx.Response(200,content=b'bad',headers={'content-type':'audio/mpeg','content-length':str(MAX_AUDIO+1)}))
  with patch('backend.internet_music.public_url',side_effect=lambda url,resolve=False:url):r=self.wait(self.lib.start_download(self.track()['path'],True)['id'])
  self.assertEqual(r['status'],'failed');self.assertIn('200',r['error'])
 def test_download_rejects_invalid_mp3(self):
  self.mock_web(lambda r:httpx.Response(200,content=b'not-an-mp3',headers={'content-type':'audio/mpeg'}))
  with patch('backend.internet_music.public_url',side_effect=lambda url,resolve=False:url):r=self.wait(self.lib.start_download(self.track()['path'],True)['id'])
  self.assertEqual(r['status'],'failed');self.assertFalse(list(Path(self.tmp.name).rglob('*.mp3')))
 def test_redirect_to_untrusted_host_rejected(self):
  self.mock_web(lambda r:httpx.Response(302,headers={'location':'https://evil.test/audio.mp3'}))
  with patch('backend.internet_music.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),self.assertRaises(ProviderError):self.lib.open_media('https://archive.org/audio.mp3')
 def test_archive_maps_mp3_only(self):
  def handle(r):
   if r.url.path=='/advancedsearch.php':return httpx.Response(200,json={'response':{'docs':[{'identifier':'item'}]}})
   return httpx.Response(200,json={'metadata':{'creator':'A','licenseurl':'https://creativecommons.org/licenses/by/4.0/'},'files':[{'name':'one.mp3','format':'VBR MP3','size':'123'},{'name':'test.txt','format':'Text'}]})
  self.mock_web(handle);r=self.wait(self.lib.start_search('hello','archive')['id']);self.assertEqual(len(r['tracks']),1);self.assertTrue(r['tracks'][0]['download_available'])
 def test_latest_search_cancels_previous(self):
  import threading
  entered=threading.Event();release=threading.Event()
  def search(key,query):
   if query=='first':entered.set();release.wait(1)
   return []
  with patch.object(self.lib,'_archive',side_effect=search):
   first=self.lib.start_search('first','archive')['id'];self.assertTrue(entered.wait(1));second=self.lib.start_search('second','archive')['id'];release.set()
   self.assertEqual(self.wait(first)['status'],'cancelled');self.assertEqual(self.wait(second)['status'],'done')

 def test_download_valid_mp3_saved_atomically(self):
  import base64
  audio=base64.b64decode('SUQzBAAAAAAAIlRTU0UAAAAOAAADTGF2ZjYxLjEuMTAwAAAAAAAAAAAAAAD/+0DAAAAAAAAAAAAAAAAAAAAAAABJbmZvAAAADwAAAAcAAAZsADw8PDw8PDw8PDw8PDw8XV1dXV1dXV1dXV1dXV19fX19fX19fX19fX19fZ6enp6enp6enp6enp6enr6+vr6+vr6+vr6+vr6+39/f39/f39/f39/f39///////////////////wAAAABMYXZjNjEuMy4AAAAAAAAAAAAAAAAkA2kAAAAAAAAGbAbYDggAAAAAAP/7UMQAAAosQy5VlIABgRVmIzrQAAeqw44zhdNk8aHBCBxYH14fGBxGBB6J5bMuWg+oOxNr8PhgUBQEAwKCRAxOaNGjbB94gBAEMTg/wQdOdPnOX85y/u6fdy4Pg+D4fBAMSgDB/SABCAABBAAYajoYe3EIAEMU5GRoMbTKNbiFAIHG+wdKdnMwOKJmJYTGEYSLe6/WwR0B0+A5RJgtX4Wodowolv+O4YYS4kR6/+SJkXi8Yl3/8umReLyJdLv8RBUFREe/4KoAUrGAOAHZgDz/+1LEBIIKYCEQvfEAAUuEoaHP7EgB2YE4BcmAIATxgI4HAZbSsBmR7Co5hXANyYKQBzmBJgXhgVIFIYFyBFGAYgFUDUcOgs7Vmadfqoo4e/Kt/612/TVo6v3pxv//7wIIowCBTGA0M1nk2tCjCBQ1EzYJnLM0xDUzCDgSg+/BN7iTbng23ZM/OS6DvxinsAnRwozrcrFqN+So78P3fQRceVY29Fn7l5zQgBv6K5h7utHRMBJAUjAUAHEwHMC4MDIBbzB3RIAzLiM4MxjEZjBtAf/7UsQPg8s0VwoP7EbBjQrhAc/USDs2VKMkMTc145bJNgNQwWabLrPVTb6Qq+nIo2iZ87WAN8x3iDV705BX3orYlrOSj23611bH7+jdzW1Bg4EGSySaYTxyeqmFbCFho+cB8aMIIYGFhgv4HQoABqReAaTVYGkJUBlYqAFA8UARcuF9Nn+3OfT9Z7SLsfmGV17C5JjxzbG98otcZS7Ze+5zb8sbsIJjGJvI9mUIUZFLpgdcAMiqtGTF5lqIaA7m8+hglgYqZB+wqmQCBgZgiIGk//tSxA8CCcwjEM3/QkFdCuGV/YjYbJeZUkcZmd/wbssgbA1nstul9+jyF8Xyj1I0zrkenuo/f/s7L5NoyYL2pD5Vv06CwgMAwAmYBsAWGA5gN5gcAHoYQ6GImcML65m4YYqYRICLH63pu8WbM/Gx8pnJ6XYf+UU9gj6snyKv642i+9PU/vRlHfXfJJNI2429XtFaPn+utiDqt9/83QBASY7Ihj4ymWVMacppgvog4ZSJAbGT/iAxgqwJGaCnGUDJtKMcNOmrEokHOrLrPXnXbAv/+1LEGgKKgCUMrn9iQXMK4UHP1Eh2Cju9/VkWeb2KT+vqkLdHShyuq5X91E0haJRdFCe96QUIDIozNFHo4XDTCdg1g0GFn6M/8DXjCfwTcDjjYA0UpAM6pcDN0MAx8SgDgGLkIuXDNND1N5r/5xtVjFWKqR5RX0dd5XvdexA7M7yVqKX7ETGpyfXWraXRdeaWaQAAALVC4mkkWdBIYwBkycYwTBDzKXjdMoQQkwQwPAYNS4DROMgw0C2nWuvpG37PRdHa3fhL9Hy2vv/2f3eijf/7UsQfggfsHyGtewJhOwShwr4QAIVQAswB0BRMCfAczAsgY4wboNqM+cSEzN3xnkw3cJtMHPA0TAgwRMwPcDbMDrA5zAIwEUSAA23jFiwGPnfU3b77pL78//////9Psrrt//eqAAAiUZjUblbiSAIAAABQGMdT4y0ETac+Wk5wu3iYDnAygY7GStrATC4MMLhMxUTQqKkLGVmvCoBDTyDDr8usPDg7IDKJAYCByetKs4Em4KaYBjgYJDBSNMFRKvHm8nncbSXmsWmNTCh0wh8E//tSxDYAEwC5N7nNABEejNjDnsAAFst6x/Ccz7hsGgVzoiID0+k1d4/vf97+ff+ejUpoJVNyj/BMEQ4Czf+sVQ8uwR4OZRiSg5RwvB9BegZQjKMDiAzDGPUI6FSGY8dGS67J0ZH0bJi7AuodH3snruLutbauhIGQV4lOg0PDZIsHRKt39T+DUNVMQU1FMy4xMDBVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVU=')
  self.mock_web(lambda r:httpx.Response(200,content=audio,headers={'content-type':'audio/mpeg'}))
  with patch('backend.internet_music.public_url',side_effect=lambda url,resolve=False:url):r=self.wait(self.lib.start_download(self.track()['path'],True)['id'])
  from audio_assertions import mpeg_payload
  from backend.metadata import read_track_metadata
  self.assertEqual(r['status'],'done');self.assertEqual(mpeg_payload(Path(r['path']).read_bytes()),mpeg_payload(audio));self.assertFalse(list(Path(self.tmp.name).rglob('*.part')))
  self.assertEqual(read_track_metadata(r['path'])['title'],'Title')

class NativeSafetyTests(unittest.TestCase):
 def test_hide_mini_without_tray_restores_main(self):
  from test_stage12_shell_api import Window
  from desktop_shell import DesktopShell
  import types
  window=Window();shell=DesktopShell(window,types.SimpleNamespace(load_settings=lambda:{},save_mini_position=lambda x,y:None));shell.mini=Window();window.hidden=True
  try:shell.hide_mini();self.assertFalse(window.hidden);self.assertTrue(shell.mini.hidden)
  finally:shell.quit()
 def test_moved_uses_event_coordinates_not_ui_getters(self):
  from test_stage12_shell_api import Window, Event
  from desktop_shell import DesktopShell
  import types
  class Mini(Window):
   @property
   def x(self):raise AssertionError('Native x getter must not be called in event')
   @property
   def y(self):raise AssertionError('Native y getter must not be called in event')
  mini=Mini();mini.events.moved=Event();shell=DesktopShell(Window(),types.SimpleNamespace(load_settings=lambda:{},save_mini_position=lambda x,y:None))
  try:
   with patch('desktop_shell.webview.create_window',return_value=mini,create=True):shell.show_mini()
   mini.events.moved.handlers[0](120,35);self.assertEqual(shell._pending_position,(120,35))
  finally:shell.quit()

class NewHTTPTests(unittest.TestCase):
 def setUp(self):
  from backend.main import app
  from fastapi.testclient import TestClient
  from backend.local_session import APP_TOKEN
  from test_regressions import AudioEngine
  self.app=app;self.tmp=tempfile.TemporaryDirectory();self.lib=InternetLibrary(self.tmp.name)
  app.state.online=self.lib;app.state.audio_engine=AudioEngine()
  self.client=TestClient(app,base_url='http://127.0.0.1:8765');self.auth={'X-OwnPlayer-Token':APP_TOKEN}
 def tearDown(self):
  self.client.close();self.app.state.audio_engine.close();self.lib.close();self.tmp.cleanup()
 def test_new_search_requires_local_session(self):self.assertEqual(self.client.post('/api/online/search-jobs',json={'query':'test'}).status_code,401)
 def test_invalid_provider_rejected(self):self.assertEqual(self.client.post('/api/online/search-jobs',json={'query':'test','source':'arbitrary-url'},headers=self.auth).status_code,422)
 def test_download_confirmation_is_strict_boolean(self):self.assertEqual(self.client.post('/api/online/downloads',json={'path':'anything','confirmed':'yes'},headers=self.auth).status_code,422)
 def test_jobs_return_no_private_url(self):
  track=self.lib.register('Title','Artist','https://archive.org/a.mp3','archive')
  with patch.object(self.lib,'_archive',return_value=[track]):
   response=self.client.post('/api/online/search-jobs',json={'query':'test','source':'archive'},headers=self.auth);self.assertEqual(response.status_code,200)
   key=response.json()['id'];deadline=time.monotonic()+2
   while time.monotonic()<deadline:
    result=self.client.get('/api/online/jobs/'+key,headers=self.auth)
    if result.json()['status']=='done':break
    time.sleep(.01)
   self.assertEqual(result.json()['status'],'done');self.assertTrue(all('_url' not in t for t in result.json()['tracks']));self.assertNotIn('cancel',result.json())
 def test_unknown_download_not_started(self):self.assertEqual(self.client.post('/api/online/downloads',json={'path':'missing','confirmed':True},headers=self.auth).status_code,400)
 def test_visibility_sent_without_native_js(self):
  from backend.desktop_controls import register
  import types
  register(types.SimpleNamespace(main_visible=False,mini_visible=True))
  try:
   result=self.client.get('/api/player/state',headers=self.auth);self.assertEqual(result.status_code,200);self.assertEqual(result.json()['window_visibility'],{'main':False,'mini':True})
  finally:register(None)
