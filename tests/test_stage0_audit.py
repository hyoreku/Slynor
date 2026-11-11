"""Meaningful safety checks; all generated secrets/media live in temporary folders."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from tools.stage0_audit import audit

class Stage0AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True, capture_output=True)
    def tearDown(self):
        self.tmp.cleanup()
    def put(self, name, value):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)
    def test_tokens_are_found_without_returning_the_value(self):
        token = '123456789:' + 'A' * 35
        self.put('config.py', 'BOT_TOKEN = ' + repr(token))
        result = audit(self.root)
        self.assertIn('telegram_token', [v['category'] for v in result['findings']])
        self.assertNotIn(token, str(result))
    def test_forced_tracked_environment_is_detected(self):
        self.put('.gitignore', '.env\n')
        self.put('.env', 'MODE=private\n')
        subprocess.run(['git', 'add', '-f', '.env'], cwd=self.root, check=True, capture_output=True)
        self.assertTrue(any(v['file'] == '.env' for v in audit(self.root)['findings']))
    def test_json_sql_and_images_are_preserved(self):
        for n, value in [('tests/fixture.json', '{}'), ('migrations/001.sql', 'SELECT 1;'), ('frontend/icon.svg', '<svg/>')]:
            self.put(n, value)
        result = audit(self.root)
        self.assertEqual(result['candidate_files'], 3)
        self.assertEqual(result['findings'], [])
    def test_unreviewed_media_is_detected(self):
        self.put('song.mp3', 'fixture-only; no audio')
        self.assertIn('media_requires_explicit_fixture_review', [v['category'] for v in audit(self.root)['findings']])
    def test_new_credentials_in_tests_are_not_blanket_ignored(self):
        self.put('tests/test_online_music.py', 'url = "https://' + 'new:credential@example.invalid/x"')
        self.assertIn('credential_url', [v['category'] for v in audit(self.root)['findings']])
    def test_symlink_is_not_read(self):
        try: (self.root / 'outside.txt').symlink_to(self.root.parent / 'outside-unreadable')
        except OSError: self.skipTest('Symlink creation unavailable')
        self.assertIn('symlink_requires_review', [v['category'] for v in audit(self.root)['findings']])
