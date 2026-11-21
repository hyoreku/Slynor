"""New UI contracts/settings/startup metrics; doubles are not Windows latency proof."""
import json,os,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from test_regressions import DesktopApi,AudioEngine

ROOT=Path(__file__).resolve().parents[1]
class ViewPreferenceTests(unittest.TestCase):
 def test_default_on(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):self.assertIs(DesktopApi().load_settings()['playing_screen_enabled'],True)
 def test_roundtrip_preserves_settings(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):
   a=DesktopApi();a.save_theme('transparent');a.save_app_icon('7');a.save_playing_screen(False)
   b=DesktopApi();self.assertIs(b.load_settings()['playing_screen_enabled'],False);self.assertEqual(b.load_settings()['theme'],'transparent');self.assertEqual(b.load_settings()['app_icon'],'7')
 def test_reject_non_boolean(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):
   a=DesktopApi()
   for v in ('false',0,1,None,[]):
    with self.assertRaises(ValueError):a.save_playing_screen(v)
 def test_legacy_settings_migrate(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):
   a=DesktopApi();a.save_settings('#123456');data=json.loads(a._settings_path.read_text());data.pop('playing_screen_enabled',None);a._settings_path.write_text(json.dumps(data));self.assertIs(a.load_settings()['playing_screen_enabled'],True)
 def test_invalid_saved_setting_does_not_overwrite(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root}):
   a=DesktopApi();a.save_settings('#123456');data=json.loads(a._settings_path.read_text());data['playing_screen_enabled']='false';text=json.dumps(data);a._settings_path.write_text(text)
   with self.assertRaises(RuntimeError):a.save_playing_screen(True)
   self.assertEqual(a._settings_path.read_text(),text)


class VisualizerPreferenceTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"LOCALAPPDATA": root}))
        self.api = DesktopApi()

    def test_legacy_file_keeps_the_users_spectrum_choice(self):
        self.api.save_settings("#123456")
        for enabled in (False, True):
            legacy = json.dumps({"version": 1, "accent": "#123456", "spectrum_enabled": enabled})
            self.api._settings_path.write_text(legacy, encoding="utf-8")
            settings = self.api.load_settings()
            self.assertEqual(settings["visualizer_mode"], "bars" if enabled else "off")
            self.assertEqual(settings["cover_background_style"], "soft")
            self.assertEqual(settings["wave_style"], "surf")
            self.assertEqual(self.api._settings_path.read_text(encoding="utf-8"), legacy)

    def test_each_mode_survives_reload_and_preserves_other_preferences(self):
        self.api.save_theme("light")
        for mode in ("off", "bars", "wave", "rays"):
            self.api.save_visual_effects(False, mode != "off", mode)
            settings = DesktopApi().load_settings()
            self.assertEqual(settings["visualizer_mode"], mode)
            self.assertIs(settings["spectrum_enabled"], mode != "off")
            self.assertIs(settings["cover_background"], False)
            self.assertEqual(settings["theme"], "light")

    def test_invalid_mode_does_not_modify_saved_preferences(self):
        self.api.save_visual_effects(True, True, "wave")
        before = self.api._settings_path.read_bytes()
        with self.assertRaises(ValueError):
            self.api.save_visual_effects(False, False, "unknown")
        self.assertEqual(self.api._settings_path.read_bytes(), before)

    def test_wave_shape_persists_when_switching_visualizer_modes(self):
        from backend.appearance import Appearance

        for shape in ("surf", "ion", "ember"):
            self.api.save_visual_effects(True, True, "wave", wave_style=shape)
            self.api.save_visual_effects(True, True, "bars")
            value = DesktopApi().load_settings()["wave_style"]
            self.assertEqual(Appearance(wave_style=value).wave_style, shape)
        with self.assertRaises(ValueError):
            self.api.save_visual_effects(True, True, "wave", wave_style="unknown")
        self.assertEqual(DesktopApi().load_settings()["wave_style"], "ember")

    def test_cover_style_and_mini_mode_are_independent(self):
        from backend.appearance import Appearance

        self.api.save_visual_effects(True, True, "rays", "liquid")
        self.api.save_mini_background("colors")
        self.api.save_visual_effects(False, False, "off")
        settings = DesktopApi().load_settings()
        self.assertEqual(settings["cover_background_style"], "liquid")
        self.assertEqual(settings["mini_background"], "colors")
        self.api.save_mini_background("liquid")
        settings = DesktopApi().load_settings()
        self.assertIs(settings["cover_background"], False)
        self.assertEqual(Appearance(mini_background=settings["mini_background"]).mini_background, "liquid")

    def test_invalid_background_style_does_not_modify_saved_preferences(self):
        self.api.save_visual_effects(True, True, "wave", "liquid")
        before = self.api._settings_path.read_bytes()
        with self.assertRaises(ValueError):
            self.api.save_visual_effects(False, False, "off", "unknown")
        self.assertEqual(self.api._settings_path.read_bytes(), before)

    def test_tester_mode_is_opt_in_and_preserves_the_legacy_effects(self):
        self.api.save_settings("#123456")
        legacy = '{"version": 1, "accent": "#123456", "mini_background": "liquid", "cover_background_style": "liquid"}'
        self.api._settings_path.write_text(legacy, encoding="utf-8")
        settings = self.api.load_settings()
        self.assertIs(settings["tester_mode"], False)
        self.assertIs(settings["mini_liquid"], True)
        self.assertEqual(settings["cover_background_style"], "liquid")
        self.assertEqual(self.api._settings_path.read_text(encoding="utf-8"), legacy)

    def test_tester_mode_and_mini_override_save_independently(self):
        self.api.save_mini_background("wallpaper")
        self.api.save_mini_liquid(True)
        self.api.save_tester_mode(True)
        self.api.save_tester_mode(False)
        settings = DesktopApi().load_settings()
        self.assertEqual(settings["mini_background"], "wallpaper")
        self.assertIs(settings["mini_liquid"], True)
        self.assertIs(settings["tester_mode"], False)
        before = self.api._settings_path.read_bytes()
        for save in (self.api.save_tester_mode, self.api.save_mini_liquid):
            with self.assertRaises(ValueError):
                save("false")
        self.assertEqual(self.api._settings_path.read_bytes(), before)

class StartupTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.file=Path(self.tmp.name)/'audio.wav';self.file.touch();self.e=AudioEngine()
 def tearDown(self):self.e.close();self.tmp.cleanup()
 def test_first_play_does_not_stop_unused_decoder(self):
  self.e.play(str(self.file));self.assertEqual(self.e._player.stop_calls,0)
 def test_switch_still_stops_previous_decoder(self):
  self.e.play(str(self.file));self.e.play(str(self.file));self.assertEqual(self.e._player.stop_calls,1)
 def test_local_caching_only_on_local_media(self):
  options=[];media=types.SimpleNamespace(add_option=options.append,release=lambda:None)
  with patch.object(self.e._instance,'media_new_path',return_value=media):self.e.play(str(self.file))
  self.assertEqual(options,[':file-caching=150'])
 def test_network_buffering_not_lowered(self):
  options=[];media=types.SimpleNamespace(add_option=options.append,release=lambda:None);self.e.stream_resolver=lambda p:('http://127.0.0.1/media','Song')
  with patch.object(self.e._instance,'media_new',return_value=media,create=True):self.e.play('online:fixture:1')
  self.assertEqual(options,[]);self.assertEqual(self.e.get_state()['startup']['source'],'online')
 def test_metrics_have_no_paths_urls_or_titles(self):
  self.e.play(str(self.file));self.e._player.started();deadline=time.monotonic()+1
  while self.e.get_state()['startup']['request_to_playing_event_ms'] is None and time.monotonic()<deadline:time.sleep(.001)
  m=self.e.get_state()['startup'];self.assertEqual(set(m),{'source','request_to_play_ms','request_to_playing_event_ms'});self.assertEqual(m['source'],'local');self.assertIsInstance(m['request_to_play_ms'],int);self.assertIsInstance(m['request_to_playing_event_ms'],int)
 def test_resume_does_not_reset_startup_metrics(self):
  self.e.play(str(self.file));before=self.e.get_state()['startup']['request_to_play_ms'];self.e._player.started();self.e.pause();self.e.resume();self.assertEqual(self.e.get_state()['startup']['request_to_play_ms'],before)

class AssetsTests(unittest.TestCase):
 def test_all_export_styles_decode_and_differ(self):
  values=[]
  for i in range(1,9):
   with Image.open(ROOT/f'frontend/assets/app-icons/{i}.webp') as image:self.assertEqual(image.size,(48,48));values.append(image.convert('RGBA').tobytes())
  self.assertEqual(len(set(values)),8)
 def test_masks_use_real_transparency(self):
  for name in ("mask.png","pixel-mask.png"):
   with Image.open(ROOT/"frontend/assets/app-icons"/name) as image:
    self.assertEqual(image.mode,"RGBA");self.assertEqual(image.getchannel("A").getextrema(),(0,255))
 def test_application_and_tray_are_distinct(self):
  with Image.open(ROOT/'frontend/assets/icons/app.ico') as icon:self.assertIn((256,256),icon.ico.sizes())
  self.assertNotEqual((ROOT/'frontend/assets/logo.png').read_bytes(),(ROOT/'frontend/assets/icons/tray.png').read_bytes())
 def test_build_recreates_app_icon_not_tray(self):
  text=(ROOT/"packaging/windows/BUILD_WINDOWS.ps1").read_text(encoding='utf-8-sig');self.assertIn("tools\\build_app_icon.py",text);self.assertLess(text.index("tools\\build_app_icon.py"),text.index("PyInstaller','--noconfirm"))
 def test_no_obsolete_letter_fallback(self):
  text=(ROOT/'frontend/js/settings16.js').read_text(encoding='utf-8');self.assertNotIn("brand.textContent='S'",text);self.assertIn('mask', (ROOT/'frontend/css/modern22.css').read_text(encoding='utf-8'))
