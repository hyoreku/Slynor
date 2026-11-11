"""Deterministic tests with a LibVLC double, NOT an actual audio/Windows test.
Run: python -m unittest discover -s tests -v
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from copy import deepcopy
from unittest.mock import patch

class FakeEvents:
    def __init__(self): self.callbacks = {}
    def event_attach(self, kind, callback): self.callbacks[kind] = callback; return 0
    def event_detach(self, kind): self.callbacks.pop(kind, None)
    def emit(self, kind):
        if kind in self.callbacks: self.callbacks[kind](None)

class FakePlayer:
    def __init__(self):
        self.events = FakeEvents(); self.state = 'idle'; self.rate = 1.
        self.position = 34567; self.volume = 50; self.rate_calls=[]
        self.play_calls=0; self.stop_calls=0; self.seek_calls=[]; self.eq_calls=0
        self.reject_rate=False; self.reject_eq=False
    def event_manager(self): return self.events
    def audio_set_volume(self, value): self.volume=value; return 0
    def audio_get_volume(self): return self.volume
    def get_state(self): return self.state
    def get_rate(self): return self.rate
    def get_time(self): return self.position
    def get_length(self): return 180000
    def is_seekable(self): return True
    def set_time(self, position): self.position=position; self.seek_calls.append(position)
    def set_media(self, media): self.media=media; self.rate=1.
    def play(self): self.play_calls+=1; self.state='opening'; return 0
    def started(self): self.state='playing'; self.events.emit('playing')
    def stop(self): self.stop_calls+=1; self.state='stopped'
    def set_rate(self, rate):
        self.rate_calls.append(rate)
        if self.reject_rate: return -1
        self.rate=rate; return 0
    def set_pause(self, value):
        self.state='paused' if value else 'playing'
        if not value: self.events.emit('playing')
    def set_equalizer(self, eq): self.eq_calls+=1; return -1 if self.reject_eq else 0
    def release(self): pass

class FakeEQ:
    def __init__(self): self.preamp=0; self.bands=[0.]*10
    def set_preamp(self, value): self.preamp=value; return 0
    def set_amp_at_index(self, value, i): self.bands[i]=value; return 0
    def release(self): pass

class FakeInstance:
    def __init__(self, *args): self.player=FakePlayer()
    def media_player_new(self): return self.player
    def media_new_path(self, path): return types.SimpleNamespace(release=lambda: None)
    def release(self): pass

vlc=types.ModuleType('vlc')
vlc.Instance=FakeInstance; vlc.AudioEqualizer=FakeEQ
vlc.State=types.SimpleNamespace(**dict(zip(['NothingSpecial','Opening','Buffering','Playing','Paused','Stopped','Ended','Error'], ['idle','opening','buffering','playing','paused','stopped','ended','error'])))
vlc.EventType=types.SimpleNamespace(MediaPlayerPlaying='playing',MediaPlayerEndReached='ended',MediaPlayerEncounteredError='error')
# Deliberately only in the test runner process: application code is unmodified.
sys.modules['vlc']=vlc
sys.modules['webview']=types.ModuleType('webview')
from player.audio_engine import AudioEngine
from player.equalizer import DEFAULT_EQ
from desktop import DesktopApi

class EngineRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.paths=[]
        for name in ('first.wav','second.wav'):
            path=Path(self.tmp.name)/name; path.touch(); self.paths.append(str(path))
        self.engine=AudioEngine(); self.native=self.engine._player
        self.engine.set_equalizer(DEFAULT_EQ)
    def tearDown(self): self.engine.close(); self.tmp.cleanup()
    def wait(self, predicate):
        deadline=time.monotonic()+1
        while not predicate() and time.monotonic()<deadline: time.sleep(.001)
        self.assertTrue(predicate())
    def mode(self, mode):
        config=self.engine.get_equalizer(); config['speed_mode']=mode
        self.engine.set_equalizer(config)
    def start(self):
        self.engine.play_queue(self.paths,0); self.native.started()
    def test_speed_idle_deferred_until_decoder_starts(self):
        self.mode('speedup'); self.assertEqual(self.native.rate_calls,[])
        self.start(); self.wait(lambda:self.native.rate==1.25)
    def test_opening_uses_latest_requested_mode(self):
        self.engine.play(self.paths[0]); self.mode('speedup'); self.mode('slowed')
        self.assertEqual(self.native.rate_calls,[])
        self.native.started(); self.wait(lambda:self.native.rate==.8)
    def test_modes_never_restart_or_seek_track(self):
        self.start(); calls=(self.native.play_calls,self.native.stop_calls)
        for mode,rate in [('speedup',1.25),('slowed',.8),('standard',1.)]:
            self.mode(mode); self.assertEqual(self.native.rate,rate)
            self.assertEqual(self.native.position,34567)
        self.assertEqual(calls,(self.native.play_calls,self.native.stop_calls))
        self.assertEqual(self.native.seek_calls,[])
    def test_speed_only_does_not_reattach_equalizer(self):
        self.start(); count=self.native.eq_calls
        self.mode('speedup'); self.assertEqual(self.native.eq_calls,count)
    def test_identical_config_is_no_op(self):
        count=self.native.eq_calls
        self.engine.set_equalizer(DEFAULT_EQ)
        self.assertEqual(self.native.eq_calls,count)
    def test_unchanged_mode_does_not_touch_rate(self):
        self.start(); self.mode('standard'); self.assertEqual(self.native.rate_calls,[])
    def test_speed_changes_on_pause_keep_pause_and_position(self):
        self.start(); self.engine.pause(); self.mode('slowed')
        self.assertEqual(self.native.state,'paused'); self.assertEqual(self.native.position,34567)
        calls=len(self.native.rate_calls); self.engine.resume(); time.sleep(.02)
        self.assertEqual(len(self.native.rate_calls),calls)
    def test_next_track_reapplies_rate_after_decoder_reset(self):
        self.start(); self.mode('slowed'); self.engine.next_track()
        self.assertEqual(self.native.rate,1.)
        self.native.started(); self.wait(lambda:self.native.rate==.8)
        self.assertEqual(self.engine.get_state()['queue_index'],1)
    def test_seek_in_each_speed_preserves_queue_and_mode(self):
        self.start()
        for mode in ('standard','speedup','slowed'):
            self.mode(mode); self.assertEqual(self.engine.seek(90000,self.paths[0]),90000)
            self.assertEqual(self.engine.get_state()['speed_mode'],mode)
            self.assertEqual(self.engine.get_queue()['index'],0)
    def test_stale_track_event_is_ignored(self):
        self.start(); old=self.native.events.callbacks['ended']; self.engine.next_track()
        old(None); time.sleep(.02); self.assertEqual(self.engine.get_queue()['index'],1)
    def test_stale_seek_is_rejected(self):
        self.start(); self.engine.next_track(); self.native.started()
        with self.assertRaises(ValueError): self.engine.seek(10,self.paths[0])
    def test_rate_failure_keeps_confirmed_config(self):
        self.start(); self.native.reject_rate=True
        with self.assertRaises(RuntimeError): self.mode('speedup')
        self.assertEqual(self.engine.get_equalizer()['speed_mode'],'standard')
    def test_equalizer_failure_restores_native_values_and_rate(self):
        self.start(); self.native.reject_eq=True
        config=deepcopy(DEFAULT_EQ); config.update(enabled=True,preamp=-4,speed_mode='slowed')
        with self.assertRaises(RuntimeError): self.engine.set_equalizer(config)
        self.assertEqual(self.engine.get_equalizer(),DEFAULT_EQ)
        self.assertEqual(self.native.rate,1.)
        self.assertEqual(self.engine._native_eq.preamp,0.)
    def test_decoder_error_is_visible_without_skipping(self):
        self.start(); self.native.events.emit('error')
        self.wait(lambda:self.engine.get_state()['playback_error'] is not None)
        self.assertEqual(self.engine.get_queue()['index'],0)
    def test_auto_advance_applies_speed_on_next_decoder(self):
        self.start(); self.mode('speedup'); self.native.events.emit('ended')
        self.wait(lambda:self.engine.get_queue()['index']==1)
        self.native.started(); self.wait(lambda:self.native.rate==1.25)
    def test_stop_makes_old_callbacks_harmless(self):
        self.start(); old=self.native.events.callbacks['ended']; self.engine.stop(); old(None)
        time.sleep(.02); self.assertEqual(self.engine.get_state()['state'],'idle')

class SettingsRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.env=patch.dict(os.environ,LOCALAPPDATA=self.tmp.name); self.env.start(); self.api=DesktopApi()
    def tearDown(self): self.env.stop(); self.tmp.cleanup()
    def test_default_is_enabled(self): self.assertIs(self.api.load_settings()['sidebar_auto_collapse'],True)
    def test_old_settings_migrate_without_writing(self):
        self.api._data_dir.mkdir(); text='{"version":1,"accent":"#8655eb"}'
        self.api._settings_path.write_text(text)
        self.assertIs(self.api.load_settings()['sidebar_auto_collapse'],True)
        self.assertEqual(self.api._settings_path.read_text(),text)
    def test_preference_roundtrip_preserves_other_fields(self):
        self.api.save_theme('light'); self.api.save_preferences('en',True)
        self.api.save_sidebar_preferences(False)
        state=DesktopApi().load_settings()
        self.assertFalse(state['sidebar_auto_collapse']); self.assertEqual(state['theme'],'light')
        self.assertEqual(state['language'],'en'); self.assertTrue(state['developer_mode'])
        self.assertFalse(self.api._settings_path.with_suffix('.json.tmp').exists())
    def test_strict_boolean(self):
        for bad in (1,0,'false',None):
            with self.subTest(bad=bad),self.assertRaises(ValueError): self.api.save_sidebar_preferences(bad)
    def test_corrupted_file_is_not_overwritten(self):
        self.api._data_dir.mkdir(); self.api._settings_path.write_text('{broken')
        with self.assertRaises(RuntimeError): self.api.save_sidebar_preferences(False)
        self.assertEqual(self.api._settings_path.read_text(),'{broken')
    def test_invalid_saved_value_is_not_treated_as_false(self):
        self.api._data_dir.mkdir(); self.api._settings_path.write_text(json.dumps({'version':1,'accent':'#8655eb','sidebar_auto_collapse':'no'}))
        with self.assertRaises(RuntimeError): self.api.load_settings()
    def test_track_is_added_to_several_playlists_without_duplicates(self):
        self.api.create_playlist('One'); self.api.create_playlist('Two')
        identifiers=[item['id'] for item in self.api.load_playlists()]
        self.api.add_track_to_playlists(identifiers,'C:/Music/a.mp3')
        self.api.add_track_to_playlists(identifiers,'c:\\music\\A.MP3')
        self.assertEqual([item['tracks'] for item in self.api.load_playlists()],[['C:/Music/a.mp3'],['C:/Music/a.mp3']])
        with self.assertRaises(ValueError): self.api.add_track_to_playlists([],'C:/Music/a.mp3')
        with self.assertRaises(ValueError): self.api.add_track_to_playlists(['missing'],'C:/Music/a.mp3')
        with self.assertRaises(ValueError): self.api.add_track_to_playlists(['only-a-string'],'C:/Music/a.mp3')

if __name__=='__main__': unittest.main()

# A user's environment/Windows proxy must never turn deterministic unit tests into live traffic.
# Explicit system-proxy tests override these doubles locally; real-local-proxy tests set manual mode.
_network_environment_proxy = patch('backend.network.getproxies', return_value={})
_network_registry_proxy = patch('backend.network.registry_proxies', return_value={})
_network_environment_proxy.start()
_network_registry_proxy.start()
