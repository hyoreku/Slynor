"""Packaging/startup contracts. These tests do not certify Windows binaries."""
import ast
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
from backend.worker_runtime import worker_python, worker_available
import start

ROOT = Path(__file__).resolve().parents[1]


class BuildContractTests(unittest.TestCase):
    def test_pending_native_playback_does_not_block_its_http_status(self):
        from fastapi.testclient import TestClient
        from backend.local_session import APP_TOKEN
        from backend.main import app
        from backend.playback_start import PlaybackStart

        release = Event()

        def slow_play(paths, index):
            if not release.wait(5):
                raise TimeoutError('test did not release playback')
            return 'track.wav'

        starting = PlaybackStart()
        state = SimpleNamespace(audio_engine=SimpleNamespace(play_queue=slow_play), playback_start=starting)
        client = TestClient(app, base_url='http://127.0.0.1:8765', headers={'X-OwnPlayer-Token': APP_TOKEN})
        with patch.object(app, 'state', state):
            try:
                body = {'paths': ['track.wav'], 'index': 0}
                response = client.post('/api/player/queue/play?deferred=true', json=body)
                self.assertEqual(response.status_code, 202)
                status = '/api/player/queue/starts/' + response.json()['id']
                self.assertEqual(client.get(status).json(), {'status': 'opening'})
                self.assertEqual(client.get('/api/player/state').json(), {'state': 'opening', 'pending_start': True})
                self.assertEqual(client.post('/api/player/queue/play?deferred=true', json=body).status_code, 409)
                release.set()
                self.assertTrue(starting.wait(1))
                self.assertEqual(client.get(status).json()['status'], 'ok')
            finally:
                release.set()
                client.close()

    def test_desktop_selects_windows_webview_with_either_runtime(self):
        import desktop

        for runtime in (None, Path('bundled-webview2')):
            webview = SimpleNamespace(settings={})
            with (
                self.subTest(runtime=runtime),
                patch.object(desktop, 'os', SimpleNamespace(name='nt')),
                patch.object(desktop, 'webview', webview),
                patch('backend.startup_runtime.require_webview2', return_value=runtime) as require,
                patch('backend.startup_runtime.load_packaged_dotnet') as load_dotnet,
            ):
                self.assertEqual(desktop.configure_webview(), 'edgechromium')
                require.assert_called_once_with()
                load_dotnet.assert_called_once_with()
                expected = {'WEBVIEW2_RUNTIME_PATH': str(runtime)} if runtime else {}
                self.assertEqual(webview.settings, expected)

    def test_frozen_app_initializes_coreclr_from_its_own_directory(self):
        from backend.startup_runtime import load_packaged_dotnet

        with tempfile.TemporaryDirectory(prefix='Slynor runtime ') as root:
            dotnet = Path(root) / 'dotnet'
            dotnet.mkdir()
            config = dotnet / 'Slynor.runtimeconfig.json'
            config.touch()
            runtime = SimpleNamespace(kind='CoreCLR', initialized=True)
            clr = SimpleNamespace(AddReference=Mock())
            with (
                patch.object(sys, 'frozen', True, create=True),
                patch.object(sys, 'executable', str(Path(root) / 'Slynor.exe')),
                patch.dict(sys.modules, {'pythonnet': SimpleNamespace(), 'clr': clr}),
                patch('pythonnet.load', create=True) as load,
                patch('pythonnet.get_runtime_info', return_value=runtime, create=True),
            ):
                load_packaged_dotnet()
                load.assert_called_once_with('coreclr', dotnet_root=str(dotnet), runtime_config=str(config))
                self.assertEqual(clr.AddReference.call_args_list, [
                    call('Microsoft.Win32.SystemEvents'), call('System.Drawing.Primitives'), call('System.Drawing.Common'),
                ])

    def test_explicit_server_import(self):
        tree = ast.parse((ROOT / 'start.py').read_text(encoding='utf-8'))
        self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == 'backend.main' and any(a.name == 'app' for a in n.names) for n in ast.walk(tree)))
        self.assertNotIn('"backend.main:app"', (ROOT / 'start.py').read_text(encoding='utf-8'))

    def test_windowed_logging_does_not_use_uvicorn_console(self):
        text = (ROOT / 'start.py').read_text(encoding='utf-8')
        self.assertIn('log_config=None', text)
        self.assertIn('use_colors=False', text)

    def test_spec_is_the_only_packaging_recipe(self):
        text = (ROOT / 'packaging/windows/BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("'--clean','packaging/windows/Slynor.spec'", text)
        self.assertNotIn("'--add-data'", text)
        spec = (ROOT / 'packaging/windows/Slynor.spec').read_text(encoding='utf-8')
        self.assertIn("collect_submodules('backend')", spec)
        self.assertIn("collect_submodules('player')", spec)
        self.assertIn("'backend.main'", spec)
        self.assertIn('console=False', spec)
        self.assertIn('upx=False', spec)

    def test_installer_follows_checks(self):
        text = (ROOT / 'packaging/windows/BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig')
        self.assertLess(text.index("Check-Packaged '--smoke-test'"), text.index('Run $iscc $installerArgs'))
        self.assertIn('WaitForExit(90000)', text)
        self.assertIn('data.frozen', text)

    def test_isolated_requirements(self):
        self.assertNotIn('msoc', (ROOT / 'requirements/base/gui.txt').read_text(encoding='utf-8'))
        self.assertEqual((ROOT / 'requirements/base/build.txt').read_text(encoding='utf-8').splitlines()[0], '-r gui.txt')
        text = (ROOT / 'INSTALL.ps1').read_text(encoding='utf-8-sig')
        self.assertNotIn("'requirements.txt','-r','requirements-search.txt'", text)
        installer = (ROOT / 'packaging/Slynor.iss').read_text(encoding='utf-8')
        self.assertNotIn('INSTALL.ps1', installer)
        self.assertNotIn('powershell.exe', installer)
        self.assertIn('prepare_portable.py', (ROOT / 'packaging/windows/BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig'))

    def test_scripts_install_only_validated_hash_locked_sets(self):
        install = (ROOT / 'INSTALL.ps1').read_text(encoding='utf-8-sig')
        build = (ROOT / 'packaging/windows/BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig')
        for group in ('gui', 'provider', 'search'):
            lock = f'requirements/validated/{group}-win-py313.txt'
            self.assertTrue((ROOT / lock).is_file(), lock)
            self.assertIn(f'{group}-win-py313.txt', install)
        self.assertIn('build-win-py313.txt', build)
        for text in (install, build):
            self.assertIn('--require-hashes', text)
        for legacy in ("'-r','requirements.txt'", "'-r','requirements-download.txt'",
                       "'-r','requirements-search.txt'"):
            self.assertNotIn(legacy, install)
        self.assertNotIn("'-r','requirements-build.txt'", build)

    def test_no_frozen_gui_interpreter_for_search(self):
        text = (ROOT / 'backend/internet_music.py').read_text(encoding='utf-8')
        self.assertIn("worker_python('search')", text)
        self.assertNotIn("[sys.executable,'-m','backend.msoc_worker']", text)


class WorkerTests(unittest.TestCase):
    def test_isolated_worker_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.search' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            path.parent.mkdir(parents=True)
            path.touch()
            with patch('backend.worker_runtime.application_dir', return_value=Path(tmp)), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(worker_python('search'), str(path))

    def test_frozen_search_availability_uses_external_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.search' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            path.parent.mkdir(parents=True)
            path.touch()
            with patch('backend.worker_runtime.application_dir', return_value=Path(tmp)), patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, {}, clear=True):
                self.assertTrue(worker_available('search'))

    def test_missing_frozen_search_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp, patch('backend.worker_runtime.application_dir', return_value=Path(tmp)), patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, {}, clear=True):
            self.assertFalse(worker_available('search'))

    def test_missing_frozen_worker_never_returns_gui(self):
        with tempfile.TemporaryDirectory() as tmp, patch('backend.worker_runtime.application_dir', return_value=Path(tmp)), patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'INSTALL.ps1'):
                worker_python('provider')

    def test_explicit_missing_interpreter_is_error(self):
        with patch.dict(os.environ, {'OWNPLAYER_SEARCH_PYTHON': '/missing/search/python'}):
            with self.assertRaises(RuntimeError):
                worker_python('search')

    def test_explicit_gui_is_rejected(self):
        with patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, {'OWNPLAYER_SEARCH_PYTHON': sys.executable}):
            with self.assertRaisesRegex(RuntimeError, 'must not point'):
                worker_python('search')


class DispatchTests(unittest.TestCase):
    def test_desktop_keeps_a_reserved_socket_when_default_port_is_occupied(self):
        with socket.socket() as occupied:
            occupied.bind((start.HOST, 0))
            occupied.listen()
            default = occupied.getsockname()[1]
            with patch.object(start, 'PORT', default), start._reserve_desktop_socket() as reserved:
                host, port = reserved.getsockname()
                self.assertEqual(host, '127.0.0.1')
                self.assertNotEqual(port, default)
                reserved.listen()
                with socket.create_connection((host, port), timeout=2):
                    pass

    def test_desktop_lock_prevents_overlapping_launches(self):
        from backend.startup_runtime import DesktopAlreadyRunning
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LOCALAPPDATA': tmp}):
            with start._desktop_lock():
                with self.assertRaises(DesktopAlreadyRunning), start._desktop_lock():
                    self.fail('A second desktop launch acquired the lock')
            with start._desktop_lock():
                pass

    def test_idle_desktop_does_not_initialize_vlc(self):
        from player.audio_engine import AudioEngine
        with patch('player.audio_engine.vlc.Instance') as instance:
            engine = AudioEngine(defer_native=True)
            engine.set_volume(25)
            self.assertEqual(engine.get_state()['volume'], 25)
            self.assertEqual(engine.get_state()['state'], 'idle')
            engine.close()
            instance.assert_not_called()

    def test_check_runtime_does_not_open_window(self):
        with patch('backend.startup_runtime.prepare_runtime'), patch('backend.packaged_check.run_check', return_value=0) as check, patch('start.main') as main:
            self.assertEqual(start.cli(['--check-runtime']), 0)
            check.assert_called_once_with(smoke=False)
            main.assert_not_called()

    def test_smoke_does_not_open_window(self):
        with patch('backend.startup_runtime.prepare_runtime'), patch('backend.packaged_check.run_check', return_value=1) as check, patch('start.main') as main:
            self.assertEqual(start.cli(['--smoke-test']), 1)
            check.assert_called_once_with(smoke=True)
            main.assert_not_called()

    def test_music_service_mode_does_not_open_window(self):
        with patch('backend.startup_runtime.prepare_runtime'), \
                patch('service.__main__.main') as service, patch('start.main') as main:
            self.assertEqual(start.cli(['--music-service', '--port', '18766']), 0)
            service.assert_called_once_with(['--port', '18766'])
            main.assert_not_called()

    def test_startup_failure_is_reported(self):
        with patch('backend.startup_runtime.prepare_runtime'), patch('backend.startup_runtime.show_failure') as failure, patch('start.main', side_effect=RuntimeError('test')):
            self.assertEqual(start.cli([]), 1)
            failure.assert_called_once()

    def test_windowed_streams_and_file_log_in_real_subprocess(self):
        code = '''import sys,logging
sys.stdout=None;sys.stderr=None
from backend.startup_runtime import prepare_runtime
prepare_runtime()
assert sys.stdout is not None and not sys.stdout.isatty()
assert sys.stderr is not None
logging.getLogger('ownplayer').error('windowed-test-marker')
from backend.diagnostics import setup, dump_stacks
setup()
dump_stacks()
'''
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                env={**os.environ, 'LOCALAPPDATA': tmp}, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            logs = Path(tmp) / 'Slynor' / 'logs'
            self.assertIn('windowed-test-marker', (logs / 'startup.log').read_text(encoding='utf-8'))
            self.assertTrue((logs / 'app.log').stat().st_size)
            self.assertTrue((logs / 'stacks.log').stat().st_size)
            self.assertFalse((Path(tmp) / 'OwnPlayerMusic' / 'logs').exists())
