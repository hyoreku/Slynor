"""Validated dependency locks must stay pinned, hashed and consistent."""
import json
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
VALIDATED = ROOT / 'requirements/validated'
GROUPS = {'gui', 'provider', 'search', 'build'}


def parse_lock(path):
    rows = {}
    current = None
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('--hash=sha256:'):
            if current is None:
                raise AssertionError(f'{path.name}: hash line without a requirement')
            if not re.fullmatch(r'--hash=sha256:[a-f0-9]{64}', line):
                raise AssertionError(f'{path.name}: malformed hash {line}')
            rows[current][1].append(line.split(':', 1)[1])
            continue
        match = re.fullmatch(r'([A-Za-z0-9._-]+)==([^\s\\]+)\s*\\?', line)
        if match is None:
            raise AssertionError(f'{path.name}: unexpected line {line!r}')
        current = match.group(1)
        rows[current] = (match.group(2), [])
    return rows


class ValidatedLockTests(unittest.TestCase):
    def locks(self):
        found = sorted(VALIDATED.glob('*-win-py313.txt'))
        self.assertTrue(found, 'no validated locks found')
        return found

    def test_expected_groups_have_locks(self):
        names = {path.name.split('-win-py313')[0] for path in self.locks()}
        self.assertEqual(names, GROUPS)

    def test_every_requirement_is_pinned_and_hashed(self):
        for path in self.locks():
            with self.subTest(lock=path.name):
                rows = parse_lock(path)
                self.assertGreater(len(rows), 20)
                for name, (version, hashes) in rows.items():
                    self.assertTrue(version, name)
                    self.assertTrue(hashes, name)

    def test_provenance_matches_lock(self):
        for path in self.locks():
            provenance = path.with_suffix('.provenance.json')
            with self.subTest(lock=path.name):
                self.assertTrue(provenance.is_file(), provenance.name)
                rows = parse_lock(path)
                packages = json.loads(provenance.read_text(encoding='utf-8'))['requirements']
                self.assertEqual(set(packages), set(rows))
                for name, (version, hashes) in rows.items():
                    self.assertEqual(packages[name]['version'], version)
                    self.assertEqual(packages[name]['sha256'], hashes[0])

    def test_no_machine_paths_are_recorded(self):
        for path in self.locks():
            provenance = path.with_suffix('.provenance.json')
            with self.subTest(lock=path.name):
                text = path.read_text(encoding='utf-8') + provenance.read_text(encoding='utf-8')
                self.assertNotRegex(text, r'[A-Za-z]:\\')


if __name__ == '__main__':
    unittest.main()
