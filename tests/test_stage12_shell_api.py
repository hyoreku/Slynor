"""Window/HTTP mocks: NOT native Windows tray or actual LibVLC playback tests."""
import os
import tempfile
import types
import unittest
from unittest.mock import patch
import httpx
from test_regressions import AudioEngine, FakeInstance, DEFAULT_EQ
from desktop_shell import DesktopShell
from backend.online_music import SoundCloud, OnlineLibrary, ProviderError
from backend.local_session import APP_TOKEN, COOKIE_NAME
from backend.main import app
from fastapi.testclient import TestClient

class Event:
    def __init__(self):self.handlers=[]
    def __iadd__(self, handler):self.handlers.append(handler);return self
    def emit(self):
        for handler in self.handlers:handler()
class Window:
    def __init__(self):self.hidden=False;self.destroyed=False;self.calls=[];self.events=types.SimpleNamespace(closed=Event())
    def hide(self):self.hidden=True;self.calls.append('hide')
    def show(self):self.hidden=False;self.calls.append('show')
    def restore(self):self.calls.append('restore')
    def evaluate_js(self,js):self.calls.append(js)
    def destroy(self):self.destroyed=True;self.events.closed.emit()
class NativeShellTests(unittest.TestCase):
    def setUp(self):
        self.window=Window();self.settings={'close_to_tray':True}
        self.api=types.SimpleNamespace(load_settings=lambda:self.settings,save_mini_position=lambda x,y:None)
        self.shell=DesktopShell(self.window,self.api)
    def tearDown(self):
        self.shell.quit()
    def test_ready_tray_intercepts_close_without_stopping_audio(self):
        self.shell.tray_ready=True
        self.assertIs(self.shell.closing(),False);self.shell.commands.join();self.assertTrue(self.window.hidden)
        self.assertFalse(self.shell.exiting)
        self.assertFalse(self.shell.main_visible);self.assertFalse(any('minimized:' in c for c in self.window.calls))
    def test_unavailable_tray_does_not_hide_window_on_close(self):
        self.assertIs(self.shell.closing(),False);self.shell.commands.join();self.assertTrue(self.window.destroyed);self.assertFalse(self.window.hidden)
    def test_setting_disables_close_to_tray(self):
        self.shell.tray_ready=True;self.shell.close_to_tray=False
        self.assertIs(self.shell.closing(),False);self.shell.commands.join();self.assertTrue(self.window.destroyed)
    def test_mini_reuses_window_and_hides_main(self):
        mini=Window()
        with patch('desktop_shell.webview.create_window',return_value=mini,create=True) as create:
            self.shell.show_mini();self.shell.show_mini()
            self.assertEqual(create.call_count,1);self.assertTrue(self.window.hidden)
    def test_closing_mini_restores_main(self):
        mini=Window()
        with patch('desktop_shell.webview.create_window',return_value=mini,create=True):
            self.shell.show_mini();mini.destroy();self.shell.commands.join()
        self.assertIsNone(self.shell.mini);self.assertFalse(self.window.hidden)
    def test_expand_hides_mini_and_resumes_visuals(self):
        mini=Window();self.shell.mini=mini;self.window.hidden=True;self.shell.show_main()
        self.assertTrue(mini.hidden);self.assertFalse(self.window.hidden)
        self.assertTrue(self.shell.main_visible);self.assertFalse(any('minimized:' in c for c in self.window.calls))
    def test_explicit_quit_destroys_all_windows_and_tray(self):
        self.shell.mini=Window();mini=self.shell.mini
        stopped=[];self.shell.icon=types.SimpleNamespace(stop=lambda:stopped.append(True))
        self.shell.quit();self.assertTrue(self.window.destroyed);self.assertTrue(mini.destroyed)
        self.assertEqual(stopped,[True]);self.assertTrue(self.shell.exiting)
    def test_failed_tray_reports_status(self):
        with patch.dict('sys.modules',{'pystray':None}):self.shell.start_tray()
        self.assertFalse(self.shell.tray_ready);self.assertIsNotNone(self.shell.tray_error)

class LocalAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.calls=[]
        self.env=patch.dict(os.environ,{'SOUNDCLOUD_ACCESS_TOKEN':'fixture-token'},clear=True);self.env.start()
        def handle(request):
            self.calls.append(request)
            if request.url.path=='/tracks/1':return httpx.Response(200,json={'id':1,'access':'playable'})
            if request.url.path=='/tracks/1/streams':return httpx.Response(200,json={'hls_aac_160_url':'https://cdn.sndcdn.com/master.m3u8'})
            if request.url.path=='/master.m3u8':return httpx.Response(200,content=b'#EXTM3U\n#EXTINF:10\nsegment.ts\n',headers={'content-type':'application/vnd.apple.mpegurl'})
            if request.url.path=='/segment.ts':return httpx.Response(200,content=b'fixture-segment',headers={'content-type':'audio/mp2t'})
            return httpx.Response(404)
        provider=SoundCloud(httpx.Client(transport=httpx.MockTransport(handle)))
        self.library=OnlineLibrary(self.tmp.name,provider)
        self.library.remember([{'path':'online:soundcloud:1','provider_id':'1','title':'Track','artist':'Artist','access':'playable','cover_url':None}])
        app.state.online=self.library;app.state.audio_engine=AudioEngine()
        self.client=TestClient(app,base_url='http://127.0.0.1:8765')
    def tearDown(self):
        self.client.close();self.library.close();app.state.audio_engine.close();self.env.stop();self.tmp.cleanup()
    def test_api_requires_session(self):self.assertEqual(self.client.get('/api/player/state').status_code,401)
    def test_ui_issues_httponly_same_site_cookie(self):
        response=self.client.get('/ui');self.assertEqual(response.status_code,200)
        self.assertIn('HttpOnly',response.headers['set-cookie']);self.assertIn('SameSite=strict',response.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/player/state').status_code,200)
    def test_native_header_authorizes_tray(self):
        self.assertEqual(self.client.get('/api/player/state',headers={'X-OwnPlayer-Token':APP_TOKEN}).status_code,200)
    def test_cross_site_origin_rejected_even_with_token(self):
        response=self.client.post('/api/player/stop',headers={'X-OwnPlayer-Token':APP_TOKEN,'Origin':'https://evil.test'})
        self.assertEqual(response.status_code,403)
    def test_cross_site_fetch_rejected(self):
        self.assertEqual(self.client.get('/api/player/state',headers={'X-OwnPlayer-Token':APP_TOKEN,'Sec-Fetch-Site':'cross-site'}).status_code,403)
    def test_bad_host_cannot_mint_cookie(self):
        response=self.client.get('/ui',headers={'Host':'evil.test:8765'})
        self.assertEqual(response.status_code,403);self.assertNotIn('set-cookie',response.headers)
    def test_expired_cookie_returns401(self):
        self.client.cookies.set(COOKIE_NAME,'stale');self.assertEqual(self.client.get('/api/player/state').status_code,401)
    def test_provider_status_explicit(self):
        response=self.client.get('/api/online/status',headers={'X-OwnPlayer-Token':APP_TOKEN})
        self.assertEqual(response.json()['sources'][1]['status'],'official_access_unconfirmed')
    def test_missing_online_record_returns_metadata_error(self):
        response=self.client.post('/api/library/metadata',json={'paths':['online:soundcloud:999']},headers={'X-OwnPlayer-Token':APP_TOKEN})
        self.assertEqual(response.status_code,200);self.assertTrue(response.json()['tracks'][0]['error'])
    def test_media_capability_works_without_browser_cookie(self):
        url=self.library.play_url('online:soundcloud:1');endpoint=url.replace('http://127.0.0.1:8765','')
        response=self.client.get(endpoint);self.assertEqual(response.status_code,200)
        self.assertIn('#EXTM3U',response.text);self.assertNotIn('fixture-token',response.text);self.assertNotIn('sndcdn.com',response.text)
        segment=[x for x in response.text.splitlines() if x.startswith('/api/')][0]
        response=self.client.get(segment);self.assertEqual(response.content,b'fixture-segment')
    def test_unknown_capability_rejected(self):
        self.assertEqual(self.client.get('/api/online/media/guessed/master').status_code,503)
    def test_stale_stream_error_does_not_change_new_session(self):
        engine=app.state.audio_engine
        engine._current_stream_url='http://127.0.0.1:8765/api/online/media/new/master'
        engine.report_stream_error('http://127.0.0.1:8765/api/online/media/old/master','stale')
        self.assertIsNone(engine.get_state()['playback_error'])
    def test_stream_path_uses_provider_reference_not_raw_url(self):
        app.state.audio_engine.stream_resolver=lambda ref:(self.library.play_url(ref),'Track')
        with patch.object(app.state.audio_engine._instance,'media_new',return_value=types.SimpleNamespace(release=lambda:None),create=True):
            app.state.audio_engine.play('online:soundcloud:1')
        self.assertEqual(app.state.audio_engine.get_state()['file_path'],'online:soundcloud:1')

if __name__=='__main__':unittest.main()
