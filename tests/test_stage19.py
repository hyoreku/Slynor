"""Slynor branding and stage18 user-data compatibility; no Windows certification."""
import json, os, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch
from test_regressions import DesktopApi
from test_stage12_shell_api import Window
from backend.image_assets import get_data_dir
from backend.wallpaper_assets import get_data_dir as wallpaper_data_dir
from desktop_shell import DesktopShell, _tray_image
from backend.main import app

ROOT=Path(__file__).resolve().parents[1]
class BrandingTests(unittest.TestCase):
 def test_main_and_mini_titles(self):
  for filename in ('index.html','mini.html'):
   text=(ROOT/'frontend'/filename).read_text(encoding='utf-8')
   self.assertIn('<title>Slynor',text);self.assertNotIn('OwnPlayer',text)
 def test_sidebar_brand(self):
  text=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
  self.assertIn('class="brand-name">Slynor</span>',text);self.assertIn('SLYNOR',text)
 def test_api_title(self):self.assertEqual(app.title,'Slynor API')
 def test_build_names_and_installer_identity(self):
  build=(ROOT/'BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig');iss=(ROOT/'packaging/Slynor.iss').read_text(encoding='utf-8')
  self.assertIn("'Slynor.spec'",build);self.assertIn("name='Slynor'",(ROOT/'Slynor.spec').read_text(encoding='utf-8'));self.assertIn('packaging\\Slynor.iss',build)
  self.assertIn('AppId={{93CA6C34-26B8-4058-8A93-61D7DE129233}',iss)
  self.assertIn('Slynor.exe',iss);self.assertNotIn('OwnPlayerMusic.exe',iss)
 def test_launcher_prefers_sources_and_never_old_executable(self):
  text=(ROOT/'START.cmd').read_text(encoding='utf-8')
  self.assertLess(text.index('if exist "start.py"'),text.index('if exist "Slynor.exe"'))
  self.assertNotIn('OwnPlayerMusic.exe',text)
 def test_mini_window_title(self):
  shell=DesktopShell(Window(),types.SimpleNamespace(load_settings=lambda:{},save_mini_position=lambda *a:None))
  try:
   with patch('desktop_shell.webview.create_window',return_value=Window(),create=True) as create:
    shell.show_mini();self.assertEqual(create.call_args.args[0],'Slynor · Миниплеер')
  finally:shell.quit();shell.worker.join(1);shell.watchdog.join(1)
 def test_tray_image_and_fallback(self):
  self.assertEqual(_tray_image().size,(64,64));self.assertEqual(_tray_image('/missing/slynor.png').size,(64,64))
 def test_tray_hide_does_not_reuse_old_mini_maximized_mode(self):
  window=Window();window.maximize=lambda:window.calls.append('maximize')
  shell=DesktopShell(window,types.SimpleNamespace(load_settings=lambda:{},save_mini_position=lambda *a:None))
  try:
   shell.return_mode='maximized';shell.main_mode='normal';shell.hide_main();shell.show_main()
   self.assertEqual(shell.return_mode,'normal');self.assertNotIn('maximize',window.calls)
   shell.main_mode='maximized';shell.hide_main();shell.show_main()
   self.assertEqual(shell.return_mode,'maximized');self.assertIn('maximize',window.calls)
  finally:shell.quit();shell.worker.join(1);shell.watchdog.join(1)

class ExistingDataTests(unittest.TestCase):
 def test_existing_library_settings_and_playlists_not_migrated_or_rewritten(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'LOCALAPPDATA':temp}):
   folder=Path(temp)/'OwnPlayerMusic';folder.mkdir()
   files={'library.json':{'version':1,'files':['C:\\Music\\song.mp3']},
    'playlists.json':{'version':1,'playlists':[{'id':'kept','name':'Мой плейлист','tracks':['C:\\Music\\song.mp3']}]},
    'settings.json':{'version':1,'accent':'#d47712','theme':'transparent','app_icon':'8'}}
   for name,value in files.items():(folder/name).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
   before={p.name:p.read_bytes() for p in folder.iterdir()}
   api=DesktopApi();self.assertEqual(api._data_dir,folder)
   self.assertEqual(get_data_dir(),folder);self.assertEqual(wallpaper_data_dir(),folder)
   self.assertEqual(api.load_library(),files['library.json']['files']);self.assertEqual(api.load_playlists()[0]['name'],'Мой плейлист')
   self.assertEqual(api.load_settings()['accent'],'#d47712');self.assertEqual(api.load_settings()['theme'],'transparent')
   self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()});self.assertFalse((Path(temp)/'Slynor').exists())
 def test_save_preferences_preserves_network_and_downloads(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'LOCALAPPDATA':temp}):
   api=DesktopApi();api._data_dir.mkdir();network=api._data_dir/'network.json';network.write_text('{"mode":"direct"}')
   download=api._data_dir/'Downloads';download.mkdir();song=download/'song.mp3';song.write_bytes(b'original')
   api.save_theme('light');self.assertEqual(network.read_text(),'{"mode":"direct"}');self.assertEqual(song.read_bytes(),b'original')
