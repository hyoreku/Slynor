"""Stage 15 deterministic tests. No native Windows/audio or real network assertions."""
import io,json,os,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from PIL import Image
from test_regressions import AudioEngine
from desktop import DesktopApi
from backend.catalog_music import CatalogueLibrary
from backend.internet_music import ProviderError
from backend.wallpaper_assets import import_wallpaper,get_wallpaper_path,is_cover_name
from backend.appearance import Appearance,publish,snapshot

class WallpaperTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.env=patch.dict(os.environ,{'LOCALAPPDATA':self.tmp.name});self.env.start()
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def gif(self,frames=2):
  p=self.root/'bg.gif';images=[Image.new('RGB',(16,16),(i*40%255,0,20)) for i in range(frames)];images[0].save(p,save_all=True,append_images=images[1:],duration=120,loop=0);return p
 def test_gif_preserved_animated(self):
  name=import_wallpaper(self.gif());self.assertTrue(name.endswith('.gif'))
  with Image.open(get_wallpaper_path(name)) as im:self.assertEqual(im.n_frames,2)
 def test_gif_corrupt_rejected(self):
  p=self.root/'bad.gif';p.write_bytes(b'not a gif')
  with self.assertRaises(ValueError):import_wallpaper(p)
 def test_gif_long_rejected(self):
  p=self.gif()
  with Image.open(p) as im:im.save(self.root/'long.gif',save_all=True,append_images=[Image.new('RGB',(16,16),'green')],duration=20000)
  with self.assertRaises(ValueError):import_wallpaper(self.root/'long.gif')
 def test_wallpaper_path_guard(self):
  for value in ('../a.gif','a.mp4','a'*32+'.exe','a'*32+'.GIF'):
   self.assertFalse(is_cover_name(value))
   with self.assertRaises(ValueError):get_wallpaper_path(value)
 def test_static_still_normalized_jpeg(self):
  p=self.root/'bg.png';Image.new('RGB',(8,8),'blue').save(p);self.assertTrue(import_wallpaper(p).endswith('.jpg'))
 def test_playlist_guard_still_jpeg_only(self):
  from backend.image_assets import is_cover_name as playlist_name
  self.assertFalse(playlist_name('a'*32+'.mp4'));self.assertFalse(playlist_name('a'*32+'.gif'))
 def probe(self,duration=5,codec='h264',width=100):
  return types.SimpleNamespace(returncode=0,stdout=json.dumps({'format':{'duration':duration},'streams':[{'codec_type':'video','codec_name':codec,'width':width,'height':100}]}).encode())
 def test_video_validated_copied(self):
  p=self.root/'short.mp4';p.write_bytes(b'fixture')
  with patch('shutil.which',return_value='/ffprobe'),patch('subprocess.run',return_value=self.probe()) as run:name=import_wallpaper(p)
  self.assertEqual(get_wallpaper_path(name).read_bytes(),b'fixture');self.assertEqual(run.call_args.kwargs['timeout'],8)
 def test_video_duration_resolution_codec_rejected(self):
  p=self.root/'bad.webm';p.write_bytes(b'fixture')
  for probe in (self.probe(31),self.probe(codec='mpeg2video'),self.probe(width=9999)):
   with patch('shutil.which',return_value='/ffprobe'),patch('subprocess.run',return_value=probe),self.assertRaises(ValueError):import_wallpaper(p)
 def test_video_without_ffprobe_friendly_error(self):
  p=self.root/'v.mp4';p.write_bytes(b'x')
  with patch.dict(os.environ,{'OWNPLAYER_FFMPEG':''}),patch('shutil.which',return_value=None),self.assertRaisesRegex(ValueError,'ffprobe'):import_wallpaper(p)
 def test_settings_persist_modes_and_audio(self):
  api=DesktopApi();api.save_mini_background('colors');api.save_audio_output('device-1');data=api.load_settings();self.assertEqual(data['mini_background'],'colors');self.assertEqual(data['audio_output'],'device-1')
 def test_invalid_mode_does_not_write(self):
  api=DesktopApi()
  with self.assertRaises(ValueError):api.save_mini_background('arbitrary')
  self.assertFalse(api._settings_path.exists())

class OutputTests(unittest.TestCase):
 def setUp(self):self.engine=AudioEngine()
 def tearDown(self):self.engine.close()
 def test_unavailable_enum_keeps_system_option(self):self.assertEqual(self.engine.output_devices()['devices'][0]['id'],'')
 def enumeration(self):
  tail=types.SimpleNamespace(contents=types.SimpleNamespace(device=b'usb',description='Наушники'.encode(),next=None))
  head=types.SimpleNamespace(contents=types.SimpleNamespace(device=b'speaker',description=b'Speakers',next=tail));self.engine._player.audio_output_device_enum=lambda:head;return head
 def test_enum_release_and_descriptions(self):
  head=self.enumeration()
  with patch('player.audio_engine.vlc.libvlc_audio_output_device_list_release',create=True) as free:
   result=self.engine.output_devices();free.assert_called_once_with(head)
  self.assertEqual(result['devices'][-1]['name'],'Наушники');self.assertTrue(result['available'])
 def test_select_does_not_restart_or_seek(self):
  self.enumeration();calls=[];self.engine._player.audio_output_device_set=lambda module,device:calls.append(device)
  before=(self.engine._player.stop_calls,self.engine._player.play_calls,self.engine._player.position)
  with patch('player.audio_engine.vlc.libvlc_audio_output_device_list_release',create=True):self.engine.set_output_device('usb')
  self.assertEqual(calls,['usb']);self.assertEqual(before,(self.engine._player.stop_calls,self.engine._player.play_calls,self.engine._player.position))
 def test_unknown_device_rejected(self):
  with self.assertRaises(ValueError):self.engine.set_output_device('unknown')
 def test_default_resets_to_none(self):
  calls=[];self.engine._player.audio_output_device_set=lambda m,d:calls.append(d);self.engine.set_output_device('');self.assertEqual(calls,[None])

class CoverProxyTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.lib=CatalogueLibrary(self.tmp.name)
 def tearDown(self):self.lib.close();self.tmp.cleanup()
 def image(self):
  b=io.BytesIO();Image.new('RGB',(16,12),'red').save(b,'PNG');return b.getvalue()
 def network(self,response):
  self.lib.web.close();self.lib.web=httpx.Client(transport=httpx.MockTransport(response),follow_redirects=False)
  return patch('socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))])
 def test_proxy_image_cached_and_normalized(self):
  url=self.lib.cached_cover_url('https://i.scdn.co/image/test');key=url.rsplit('/',1)[-1][:-4]
  with self.network(lambda r:httpx.Response(200,content=self.image())):
   p=self.lib.cover_file(key)
  with Image.open(p) as im:self.assertEqual(im.format,'JPEG');self.assertEqual(im.size,(16,12))
  with patch.object(self.lib.web,'send',side_effect=AssertionError('cached')):self.assertEqual(self.lib.cover_file(key),p)
 def test_untrusted_redirect_rejected(self):
  key=self.lib.cached_cover_url('https://i.scdn.co/image/test').rsplit('/',1)[-1][:-4]
  with self.network(lambda r:httpx.Response(302,headers={'location':'https://127.0.0.1/private'})),self.assertRaises(ProviderError):self.lib.cover_file(key)
 def test_private_dns_rejected(self):
  key=self.lib.cached_cover_url('https://i.scdn.co/image/test').rsplit('/',1)[-1][:-4]
  with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),self.assertRaises(ProviderError):self.lib.cover_file(key)
 def test_corrupt_image_error(self):
  key=self.lib.cached_cover_url('https://i.scdn.co/image/test').rsplit('/',1)[-1][:-4]
  with self.network(lambda r:httpx.Response(200,content=b'<html>')),self.assertRaises(ProviderError):self.lib.cover_file(key)
 def test_bad_cover_name(self):
  with self.assertRaises(ProviderError):self.lib.cover_file('../x')
 def test_cover_missing_not_fabricated(self):self.assertIsNone(self.lib.cached_cover_url(None));self.assertIsNone(self.lib.cached_cover_url('https://evil.test/a'))
 def test_enrichment_preserves_audio_origin(self):
  key=self.lib._new_job('search');track={'path':'online:web:'+'a'*32,'source':'muzbomb','title':'Song','artist':'Artist','_url':'https://muzbomb.net/audio/a.mp3'};self.lib.public[track['path']]=track.copy()
  with patch.object(self.lib,'_run_provider',return_value={'tracks':[{'path':track['path'],'cover_url':'https://i.scdn.co/image/matched','metadata_url':'https://open.spotify.com/track/a'}]}):self.lib.enrich_tracks(key,[track])
  self.assertEqual(track['source'],'muzbomb');self.assertEqual(track['_url'],'https://muzbomb.net/audio/a.mp3');self.assertEqual(track['cover_source'],'spotify')

class MatchingTests(unittest.TestCase):
 def test_cyrillic_artist_matches_catalogue_transliteration(self):
  from backend.provider_worker import match_text
  from difflib import SequenceMatcher
  self.assertGreaterEqual(SequenceMatcher(None,match_text('Пошлая Молли'),match_text('Poshlaya Molly')).ratio(),.86)
 def test_unrelated_artist_not_accepted(self):
  from backend.provider_worker import match_text
  from difflib import SequenceMatcher
  self.assertLess(SequenceMatcher(None,match_text('Пошлая Молли'),match_text('tenderlybae')).ratio(),.86)

class AppearanceTests(unittest.TestCase):
 def test_revision_changes_only_with_values(self):
  a=publish(Appearance(accent='#123456',mini_background='colors'));b=publish(Appearance(accent='#123456',mini_background='colors'));self.assertEqual(a,b)
 def test_snapshot_is_copy(self):
  a=snapshot();a['accent']='bad';self.assertNotEqual(a,snapshot())
 def test_invalid_appearance(self):
  for data in ({'wallpaper':'../bad.mp4'},{'accent':'url(x)'},{'mini_background':'remote'},{'dim':99}):
   with self.assertRaises(ValueError):Appearance(**data)

class ApiTests(unittest.TestCase):
 def setUp(self):
  from backend.main import app
  from backend.local_session import APP_TOKEN
  from fastapi.testclient import TestClient
  self.app=app;self.engine=AudioEngine();app.state.audio_engine=self.engine;self.client=TestClient(app,base_url='http://127.0.0.1:8765',headers={'X-OwnPlayer-Token':APP_TOKEN})
 def tearDown(self):self.client.close();self.engine.close()
 def test_output_devices(self):self.assertEqual(self.client.get('/api/player/output-devices').json()['devices'][0]['id'],'')
 def test_output_unknown_is_400(self):self.assertEqual(self.client.post('/api/player/output-device',json={'device':'bad'}).status_code,400)
 def test_output_oversized_is_422(self):self.assertEqual(self.client.post('/api/player/output-device',json={'device':'x'*2049}).status_code,422)
 def test_appearance_validation(self):self.assertEqual(self.client.post('/api/appearance',json={'wallpaper':'../../evil.mp4'}).status_code,422)
 def test_appearance_published_to_player_snapshot(self):
  r=self.client.post('/api/appearance',json={'theme':'light','accent':'#ffffff','mini_background':'colors'});self.assertEqual(r.status_code,200);self.assertEqual(self.client.get('/api/player/state').json()['appearance']['accent'],'#ffffff')
 def test_cover_malformed_is_404(self):self.assertEqual(self.client.get('/api/assets/online-covers/not-a-file.jpg').status_code,404)

class TrayAssetTests(unittest.TestCase):
 def test_external_tray_icon(self):
  from desktop_shell import _tray_image
  with tempfile.TemporaryDirectory() as root:
   p=Path(root)/'tray.png';Image.new('RGBA',(32,32),'red').save(p)
   with _tray_image(p) as im:self.assertEqual(im.size,(64,64));self.assertEqual(im.getpixel((32,32)),(255,0,0,255))
 def test_corrupt_asset_uses_fallback(self):
  from desktop_shell import _tray_image
  with _tray_image('/does/not/exist.png') as im:self.assertEqual(im.size,(64,64))
