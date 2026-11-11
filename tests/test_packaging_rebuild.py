"""Packaging/startup contracts. These tests do not certify Windows binaries."""
import ast
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from backend.worker_runtime import worker_python, worker_available
import start

ROOT = Path(__file__).resolve().parents[1]


class BuildContractTests(unittest.TestCase):
    def test_explicit_server_import(self):
        tree = ast.parse((ROOT / 'start.py').read_text(encoding='utf-8'))
        self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == 'backend.main' and any(a.name == 'app' for a in n.names) for n in ast.walk(tree)))
        self.assertNotIn('"backend.main:app"', (ROOT / 'start.py').read_text(encoding='utf-8'))

    def test_windowed_logging_does_not_use_uvicorn_console(self):
        text = (ROOT / 'start.py').read_text(encoding='utf-8')
        self.assertIn('log_config=None', text)
        self.assertIn('use_colors=False', text)

    def test_spec_is_the_only_packaging_recipe(self):
        text = (ROOT / 'BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("'--clean','Slynor.spec'", text)
        self.assertNotIn("'--add-data'", text)
        spec = (ROOT / 'Slynor.spec').read_text(encoding='utf-8')
        self.assertIn("collect_submodules('backend')", spec)
        self.assertIn("collect_submodules('player')", spec)
        self.assertIn("'backend.main'", spec)
        self.assertIn('console=False', spec)
        self.assertIn('upx=False', spec)

    def test_installer_follows_checks(self):
        text = (ROOT / 'BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig')
        self.assertLess(text.index("Check-Packaged '--smoke-test'"), text.index("Run $iscc @('packaging"))
        self.assertIn('WaitForExit(90000)', text)
        self.assertIn('data.frozen', text)

    def test_isolated_requirements(self):
        self.assertNotIn('msoc', (ROOT / 'requirements.txt').read_text(encoding='utf-8'))
        self.assertNotIn('requirements-search.txt', (ROOT / 'requirements-build.txt').read_text(encoding='utf-8'))
        text = (ROOT / 'INSTALL.ps1').read_text(encoding='utf-8-sig')
        self.assertNotIn("'requirements.txt','-r','requirements-search.txt'", text)
        installer = (ROOT / 'packaging/Slynor.iss').read_text(encoding='utf-8')
        self.assertNotIn('INSTALL.ps1', installer)
        self.assertNotIn('powershell.exe', installer)
        self.assertIn('prepare_portable.py', (ROOT / 'BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig'))

    def test_scripts_install_only_validated_hash_locked_sets(self):
        install = (ROOT / 'INSTALL.ps1').read_text(encoding='utf-8-sig')
        build = (ROOT / 'BUILD_WINDOWS.ps1').read_text(encoding='utf-8-sig')
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
'''
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                env={**os.environ, 'LOCALAPPDATA': tmp}, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('windowed-test-marker', (Path(tmp) / 'OwnPlayerMusic/logs/startup.log').read_text(encoding='utf-8'))
