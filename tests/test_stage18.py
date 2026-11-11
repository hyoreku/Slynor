"""Proxy regressions: deterministic HTTP/API doubles and real local HTTP/SOCKS servers."""
import io,json,os,socket,socketserver,tempfile,time,types,unittest
from pathlib import Path
from threading import Thread
from unittest.mock import patch,Mock
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import httpx
from test_regressions import AudioEngine
from backend.network import NetworkSettings,RoutedClient,NetworkError,endpoint,system_proxy
from backend.proxy_worker import safe_error,requests_proxy
from backend.catalog_music import CatalogueLibrary
from backend.internet_music import ProviderError

class NetworkTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.settings=NetworkSettings(self.tmp.name);self.system=patch('backend.network.getproxies',return_value={});self.system.start()
 def tearDown(self):self.system.stop();self.tmp.cleanup()
 def manual(self,**extra):return self.settings.apply({'mode':'manual','url':'http://127.0.0.1:8123','search_first':'youtube'},**extra)
 def test_default_system_without_proxy(self):self.assertIsNone(self.settings.resolve());self.assertFalse(self.settings.view()['proxy_detected'])
 def test_system_windows_or_environment_proxy(self):
  with patch('backend.network.getproxies',return_value={'https':'127.0.0.1:8123'}):self.assertEqual(self.settings.resolve(),'http://127.0.0.1:8123')
 def test_system_auth_never_in_view_or_report(self):
  with patch('backend.network.getproxies',return_value={'https':'http://private:secret@127.0.0.1:8123'}):
   self.assertIn('secret',self.settings.resolve());self.assertNotIn('secret',json.dumps(self.settings.view()));self.assertNotIn('private',json.dumps(self.settings.report()))
 def test_windows_registry_fallback_when_only_no_proxy_is_set(self):
  with patch('backend.network.getproxies',return_value={'no':'localhost'}),patch('backend.network.registry_proxies',return_value={'https':'http://127.0.0.1:8124'}):self.assertEqual(self.settings.resolve(),'http://127.0.0.1:8124')
 def test_direct_ignores_system_proxy(self):
  self.settings.apply({'mode':'direct'})
  with patch('backend.network.getproxies',side_effect=AssertionError('must not read system')):self.assertIsNone(self.settings.resolve());self.assertEqual(self.settings.worker_env()['HTTPS_PROXY'],'')
 def test_manual_and_credentials_only_in_memory(self):
  self.manual(username='u@ser',password='p@ss:word')
  raw=self.settings.path.read_text();self.assertNotIn('p@ss',raw);self.assertNotIn('u@ser',raw);self.assertIn('u%40ser:p%40ss%3Aword@',self.settings.resolve());self.assertNotIn('p%40ss',json.dumps(self.settings.view()))
  restored=NetworkSettings(self.tmp.name);self.assertIsNone(restored.auth);self.assertEqual(restored.resolve(),'http://127.0.0.1:8123')
 def test_clear_credentials(self):self.manual(username='u',password='p');self.manual(clear_credentials=True);self.assertIsNone(self.settings.auth)
 def test_other_changes_keep_session_auth(self):self.manual(username='u',password='p');self.manual();self.assertIsNotNone(self.settings.auth)
 def test_host_change_clears_old_credentials(self):
  self.manual(username='u',password='p');self.settings.apply({'mode':'manual','url':'http://127.0.0.1:8124'});self.assertIsNone(self.settings.auth)
 def test_inline_credentials_rejected_without_echo(self):
  with self.assertRaises(NetworkError) as error:endpoint('http://u:private-password@127.0.0.1:8123')
  self.assertNotIn('private-password',str(error.exception))
 def test_bad_proxy_urls(self):
  for value in ('127.0.0.1:8123','ftp://host:99','http://host:0','http://host:65536','http://host','http://host:bad','http://host:123/path','http://host:123?x=1','http://host:123\r\nx:a'):
   with self.subTest(value=value),self.assertRaises(NetworkError):endpoint(value)
 def test_ipv6_and_socks(self):self.assertEqual(endpoint('socks5h://[::1]:123'),'socks5h://[::1]:123')
 def test_corrupt_file_not_silently_used_or_rewritten(self):
  self.settings.path.write_text('{bad');other=NetworkSettings(self.tmp.name)
  with self.assertRaises(NetworkError):other.resolve()
  self.assertEqual(other.path.read_text(),'{bad');self.assertTrue(other.view()['configuration_error'])
 def test_worker_environment_explicit_and_bypasses_only_local(self):
  self.manual();data=self.settings.worker_env();self.assertEqual(data['HTTPS_PROXY'],data['OWNPLAYER_PROXY']);self.assertIn('127.0.0.1',data['NO_PROXY']);self.assertNotEqual(data['NO_PROXY'],'*')
 def test_socks_remote_dns_for_requests(self):
  with patch.dict(os.environ,{'OWNPLAYER_PROXY':'socks5://127.0.0.1:123'}):self.assertEqual(requests_proxy(),'socks5h://127.0.0.1:123')
 def test_secret_redaction(self):
  with patch.dict(os.environ,{'OWNPLAYER_PROXY':'http://user:secret@127.0.0.1:8123'}):
   text=safe_error('proxy http://user:secret@127.0.0.1:8123 secret');self.assertNotIn('secret',text);self.assertNotIn('user:',text)
 def test_search_priority(self):
  self.manual();self.assertEqual(self.settings.search_order(),['youtube','spotify','soundcloud'])
 def test_proxy_failure_does_not_fall_back_to_direct(self):
  self.manual();client=RoutedClient(self.settings,1)
  try:
   with patch.object(httpx.Client,'send',side_effect=httpx.ProxyError('failed')) as send:
    with self.assertRaises(httpx.ProxyError):client.get('https://www.youtube.com/robots.txt')
    self.assertEqual(send.call_count,1);self.assertEqual(list(client.clients),['http://127.0.0.1:8123'])
  finally:client.close()
 def test_socks_dependency_missing_is_explicit_and_not_saved(self):
  with patch('backend.network.httpx.Client',side_effect=ImportError('socksio')):
   with self.assertRaisesRegex(NetworkError,'SOCKS'):self.manual()
  self.assertFalse(self.settings.path.exists())
 def test_check_uses_only_fixed_service_urls_no_secret(self):
  self.manual(username='u',password='hidden-password');seen=[]
  class Response:
   status_code=200
   def __enter__(self):return self
   def __exit__(self,*args):pass
  class Client:
   def __init__(self,**kw):self.kw=kw
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def stream(self,method,url):seen.append((self.kw['proxy'],url));return Response()
  with patch('backend.network.httpx.Client',Client):data=self.settings.check()
  self.assertEqual(len(seen),4);self.assertTrue(all('hidden-password' in r[0] for r in seen));self.assertNotIn('hidden-password',json.dumps(data));self.assertTrue(all(x['code']=='http_200' for x in data['checks']))

class IntegrationTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.lib=CatalogueLibrary(self.tmp.name);self.lib.network.apply({'mode':'manual','url':'http://127.0.0.1:8123'})
 def tearDown(self):self.lib.close();self.tmp.cleanup()
 def test_proxy_does_not_need_local_youtube_dns(self):
  with patch('socket.getaddrinfo',side_effect=AssertionError('target DNS must be remote')):self.assertEqual(self.lib._validate_stream_url('https://rr1.googlevideo.com/audio'),'https://rr1.googlevideo.com/audio')
 def test_proxy_cannot_widen_stream_allowlist(self):
  with self.assertRaises(ProviderError):self.lib._validate_stream_url('https://127.0.0.1/private')
 def test_cover_with_proxy_does_not_need_local_cdn_dns(self):
  from PIL import Image
  b=io.BytesIO();Image.new('RGB',(4,4),'red').save(b,'PNG');key=self.lib.cached_cover_url('https://i.ytimg.com/vi/abc/hqdefault.jpg').split('/')[-1][:-4]
  with patch('socket.getaddrinfo',side_effect=AssertionError('target DNS must be remote')),patch.object(self.lib.web,'build_request',return_value=httpx.Request('GET','https://i.ytimg.com/x')),patch.object(self.lib.web,'send',return_value=httpx.Response(200,content=b.getvalue())):self.assertTrue(self.lib.cover_file(key).is_file())
 def test_settings_api_requires_local_session(self):
  from fastapi.testclient import TestClient
  from backend.main import app
  with patch.object(app.state,'online',self.lib,create=True):
   client=TestClient(app,base_url='http://127.0.0.1:8765')
   try:self.assertEqual(client.get('/api/network/settings').status_code,401);self.assertEqual(client.post('/api/network/settings',json={'mode':'direct'}).status_code,401)
   finally:client.close()
 def test_apply_cancels_jobs_clears_bad_cache_not_downloads(self):
  from fastapi.testclient import TestClient
  from backend.main import app
  from backend.local_session import APP_TOKEN
  key=self.lib._new_job('search');self.lib.ready_streams['ref']={'url':'x'};self.lib.cover_failures['cover']=123;self.lib.downloaded['cached']='keep.mp3'
  with patch.object(app.state,'online',self.lib,create=True):
   client=TestClient(app,base_url='http://127.0.0.1:8765',headers={'X-OwnPlayer-Token':APP_TOKEN})
   try:
    result=client.post('/api/network/settings',json={'mode':'manual','url':'http://127.0.0.1:8124','username':'a','password':'secret'})
    self.assertEqual(result.status_code,200);self.assertNotIn('secret',result.text);self.assertEqual(self.lib.job(key)['status'],'cancelled');self.assertFalse(self.lib.ready_streams);self.assertFalse(self.lib.cover_failures);self.assertEqual(self.lib.downloaded['cached'],'keep.mp3')
   finally:client.close()
 def test_invalid_settings_do_not_change_previous_route(self):
  before=self.lib.network.resolve()
  with self.assertRaises(NetworkError):self.lib.network.apply({'mode':'manual','url':'file:///bad'})
  self.assertEqual(self.lib.network.resolve(),before)

class ExportTests(unittest.TestCase):
 def test_save_report_uses_native_dialog_and_selected_path(self):
  from desktop import DesktopApi
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):
   api=DesktopApi();file=Path(root)/'report.json';api._window=types.SimpleNamespace(create_file_dialog=Mock(return_value=[str(file)]))
   with patch('desktop.webview.FileDialog',types.SimpleNamespace(SAVE=1),create=True):self.assertTrue(api.save_diagnostics_report('{"events":[]}')['saved'])
   self.assertEqual(json.loads(file.read_text()),{'events':[]})
 def test_cancel_report_save_does_not_write(self):
  from desktop import DesktopApi
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):
   api=DesktopApi();api._window=types.SimpleNamespace(create_file_dialog=Mock(return_value=None))
   with patch('desktop.webview.FileDialog',types.SimpleNamespace(SAVE=1),create=True):self.assertFalse(api.save_diagnostics_report('{"events":[]}')['saved'])
 def test_report_rejects_non_json_and_unknown_top_level(self):
  from desktop import DesktopApi
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):
   api=DesktopApi()
   for text in ('bad','[]','{"password":"secret"}'):
    with self.assertRaises(ValueError):api.save_diagnostics_report(text)

class LocalProxyTests(unittest.TestCase):
 def run_http(self,https=False):
  seen=[]
  class Proxy(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    seen.append(('GET',self.path));self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'ok')
   def do_CONNECT(self):seen.append(('CONNECT',self.path));self.send_error(502)
  server=ThreadingHTTPServer(('127.0.0.1',0),Proxy);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory() as root:
    policy=NetworkSettings(root);policy.apply({'mode':'manual','url':'http://127.0.0.1:'+str(server.server_port)});client=RoutedClient(policy,2)
    try:
     if https:
      with self.assertRaises(httpx.ProxyError):client.get('https://target.invalid/path')
     else:self.assertEqual(client.get('http://target.invalid/path').text,'ok')
    finally:client.close()
  finally:server.shutdown();server.server_close();thread.join(1)
  return seen
 def test_actual_http_request_traverses_local_proxy(self):self.assertEqual(self.run_http(),[('GET','http://target.invalid/path')])
 def test_actual_https_uses_connect_no_direct_fallback(self):self.assertEqual(self.run_http(True),[('CONNECT','target.invalid:443')])
 def test_actual_socks5_sends_target_domain_to_proxy(self):
  seen=[]
  def exact(sock,n):
   data=b''
   while len(data)<n:
    chunk=sock.recv(n-len(data))
    if not chunk:raise EOFError()
    data+=chunk
   return data
  class Socks(socketserver.BaseRequestHandler):
   def handle(self):
    sock=self.request;sock.settimeout(3);head=exact(sock,2);exact(sock,head[1]);sock.sendall(b'\x05\x00');head=exact(sock,4)
    if head[3]!=3:raise ValueError('expected remote domain')
    host=exact(sock,exact(sock,1)[0]).decode();port=int.from_bytes(exact(sock,2),'big');seen.append((host,port));sock.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50');request=b''
    while b'\r\n\r\n' not in request:request+=sock.recv(4096)
    sock.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
  server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Socks);server.daemon_threads=True;thread=Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory() as root:
    policy=NetworkSettings(root);policy.apply({'mode':'manual','url':'socks5://127.0.0.1:'+str(server.server_address[1])});client=RoutedClient(policy,2)
    try:self.assertEqual(client.get('http://unresolvable.invalid/music').text,'ok')
    finally:client.close()
  finally:server.shutdown();server.server_close();thread.join(1)
  self.assertEqual(seen,[('unresolvable.invalid',80)])

class WorkerAdapterTests(unittest.TestCase):
 def test_ytdlp_search_uses_explicit_proxy(self):
  import sys
  from backend.provider_worker import ytdlp_rows
  seen={};module=types.ModuleType('yt_dlp')
  class Downloader:
   def __init__(self,options):seen.update(options)
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def extract_info(self,*args,**kw):return {'entries':[]}
  module.YoutubeDL=Downloader
  with patch.dict(sys.modules,{'yt_dlp':module}),patch.dict(os.environ,{'OWNPLAYER_PROXY':'socks5://127.0.0.1:8123'}):ytdlp_rows('song','youtube')
  self.assertEqual(seen['proxy'],'socks5://127.0.0.1:8123')
 def test_ytdlp_stream_uses_same_proxy(self):
  import sys
  from backend.provider_worker import resolve_stream
  seen={};module=types.ModuleType('yt_dlp')
  class Downloader:
   def __init__(self,options):seen.update(options)
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def extract_info(self,*args,**kw):return {'url':'https://rr1.googlevideo.com/audio','vcodec':'none'}
  module.YoutubeDL=Downloader
  with patch.dict(sys.modules,{'yt_dlp':module}),patch.dict(os.environ,{'OWNPLAYER_PROXY':'http://127.0.0.1:8123'}):resolve_stream({'url':'https://www.youtube.com/watch?v=abcdefghijk'})
  self.assertEqual(seen['proxy'],'http://127.0.0.1:8123')
 def test_spotdl_proxy_is_in_yt_args_not_unsupported_proxy_setting(self):
  import sys,shlex
  from backend.provider_worker import download
  options={};module=types.ModuleType('spotdl');songs=types.ModuleType('spotdl.types.song')
  with tempfile.TemporaryDirectory() as folder:
   class Downloader:
    def __init__(self,**kw):options.update(kw['downloader_settings'])
    def download(self,song):file=Path(folder)/'fixture.mp3';file.write_bytes(b'fixture');return song,file
   module.Spotdl=Downloader;songs.Song=types.SimpleNamespace(from_url=lambda u:object())
   with patch.dict(sys.modules,{'spotdl':module,'spotdl.types.song':songs}),patch.dict(os.environ,{'OWNPLAYER_PROXY':'socks5://127.0.0.1:8123'}),patch('backend.provider_worker.shutil.which',return_value='/ffmpeg'):
    download({'provider':'spotify','url':'https://open.spotify.com/track/abc','folder':folder,'audio_format':'mp3'})
  self.assertEqual(shlex.split(options['yt_dlp_args']),['--proxy','socks5://127.0.0.1:8123']);self.assertNotIn('proxy',options)
 def test_scdl_fixed_args_contain_same_proxy(self):
  import sys,shlex
  from backend.provider_worker import download
  parent=types.ModuleType('scdl');module=types.ModuleType('scdl.scdl');parent.scdl=module;seen=[]
  with tempfile.TemporaryDirectory() as folder:
   def run():seen.extend(sys.argv);(Path(folder)/'fixture.mp3').write_bytes(b'fixture')
   module._main=run
   with patch.dict(sys.modules,{'scdl':parent,'scdl.scdl':module}),patch.object(sys,'argv',[]),patch.dict(os.environ,{'OWNPLAYER_PROXY':'socks5://127.0.0.1:8123'}),patch('backend.provider_worker.shutil.which',return_value='/ffmpeg'):
    download({'provider':'soundcloud','url':'https://soundcloud.com/artist/song','folder':folder,'audio_format':'mp3'})
   args=shlex.split(seen[seen.index('--yt-dlp-args')+1]);self.assertEqual(args[args.index('--proxy')+1],'socks5://127.0.0.1:8123')
 def test_parent_passes_proxy_to_isolated_worker_environment(self):
  with tempfile.TemporaryDirectory() as root:
   lib=CatalogueLibrary(root)
   try:
    lib.network.apply({'mode':'manual','url':'http://127.0.0.1:8123'});key=lib._new_job('search');child=Mock();child.communicate.return_value=(b'{"tracks":[]}',b'');child.poll.return_value=0;child.pid=999999
    with patch('backend.catalog_music.subprocess.Popen',return_value=child) as create,patch('os.killpg',create=True):lib._run_provider(key,{'operation':'search','provider':'youtube','query':'test'},1)
    self.assertEqual(create.call_args.kwargs['env']['OWNPLAYER_PROXY'],'http://127.0.0.1:8123');self.assertEqual(create.call_args.kwargs['env']['HTTPS_PROXY'],'http://127.0.0.1:8123')
   finally:lib.close()

class OfficialCoverTests(unittest.TestCase):
 def test_official_search_and_metadata_cover_use_app_proxy(self):
  with tempfile.TemporaryDirectory() as root:
   lib=CatalogueLibrary(root);track={'path':'online:soundcloud:1','title':'Song','artist':'Artist','source':'soundcloud','cover_url':'https://i1.sndcdn.com/art.jpg','access':'playable'}
   try:
    with patch.object(lib.provider,'search',return_value=[track]):rows=lib.search('soundcloud','song')
    self.assertTrue(rows[0]['cover_url'].startswith('/api/assets/online-covers/'));self.assertEqual(lib.metadata(track['path'])['cover_url'],rows[0]['cover_url']);self.assertEqual(track['cover_url'],'https://i1.sndcdn.com/art.jpg')
   finally:lib.close()
 def test_imported_cover_conversion_preserves_persisted_remote_metadata(self):
  with tempfile.TemporaryDirectory() as root:
   lib=CatalogueLibrary(root);track={'path':'online:soundcloud:1','cover_url':'https://i1.sndcdn.com/art.jpg'};lib.playlists=[{'id':'1','name':'List','tracks':[track]}]
   try:
    self.assertTrue(lib.imported()[0]['tracks'][0]['cover_url'].startswith('/api/assets/online-covers/'));self.assertEqual(lib.playlists[0]['tracks'][0]['cover_url'],'https://i1.sndcdn.com/art.jpg')
   finally:lib.close()
