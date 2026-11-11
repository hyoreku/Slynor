"""Read-only candidate inventory. Heuristics, never proof that all secrets are absent."""
import argparse
from fnmatch import fnmatch
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

BLOCKED_DIRS = {'.venv', '.providers', '.search', '.buildenv', 'dist', 'build',
                'build-logs', '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache',
                '.local-data', '.local-cache', '.local-backups', '.local-artifacts'}
MEDIA = {'.mp3', '.m4a', '.aac', '.opus', '.ogg', '.flac', '.wav', '.webm', '.mp4'}
PATTERNS = {
    'telegram_token': re.compile(r'\b[0-9]{8,12}:[A-Za-z0-9_-]{32,}\b'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{50,})\b'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'aws_key': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    'slack_token': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{20,}\b'),
    'literal_credential': re.compile(r'''(?i)(?:bot_token|client_secret|access_token|refresh_token|api_key|password)\s*[=:]\s*['"][A-Za-z0-9_+./=-]{24,}['"]'''),
    'credential_url': re.compile(r'(?i)(?:https?|socks5h?)://[^\s/@]+:[^\s/@]+@'),
    'windows_personal_path': re.compile(r'(?i)[a-z]:[\\/]+Users[\\/][^\\/\s]+'),
    'unix_personal_path': re.compile(r'/(?:home|Users)/[A-Za-z0-9._-]+/'),
}

def audit(root):
    root = Path(root).resolve()
    try:
        result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                                cwd=root, capture_output=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        return {'status': 'blocked', 'reason': 'Run git init and review .gitignore first.',
                'security_review_complete': False, 'findings': []}
    names = sorted(set(n for n in result.stdout.decode('utf-8').split('\0') if n))
    allow_file = Path(__file__).with_name('stage0_reviewed_fixtures.json')
    reviewed = json.loads(allow_file.read_text())
    findings, reviewed_count, text_files = [], 0, 0
    for name in names:
        path = root / name
        def flag(category, line=None):
            item = {'file': name, 'category': category}
            if line is not None: item['line'] = line
            findings.append(item)
        if path.is_symlink():
            flag('symlink_requires_review'); continue
        if not path.is_file():
            flag('missing_or_nonregular_candidate'); continue
        blocked_name = (name.startswith('.env') and Path(name).name != '.env.example') or Path(name).name in {
            'runtime_paths.json', 'OwnPlayerMusic.zip', 'OwnPlayerMusic_2.zip'}
        if set(Path(name).parts) & BLOCKED_DIRS or blocked_name or fnmatch(path.name, 'cookies*.txt') or path.suffix.lower() in {'.pfx', '.p12', '.pem', '.key', '.exe', '.dll'}:
            flag('excluded_material_in_candidate')
        if path.suffix.lower() in MEDIA:
            flag('media_requires_explicit_fixture_review')
        if path.stat().st_size > 4 * 1024 * 1024:
            flag('large_file_requires_review'); continue
        try:
            value = path.read_text(encoding='utf-8-sig')
        except UnicodeError:
            continue
        text_files += 1
        for i, line in enumerate(value.splitlines(), 1):
            for category, pattern in PATTERNS.items():
                if not pattern.search(line): continue
                if category == 'credential_url' and sha256(line.encode()).hexdigest() in reviewed.get(name, []):
                    reviewed_count += 1; continue
                flag(category, i)
    return {'status': 'findings' if findings else 'no_pattern_findings',
            'candidate_files': len(names), 'text_files': text_files,
            'reviewed_synthetic_fixture_lines': reviewed_count,
            'security_review_complete': False, 'findings': findings,
            'note': 'Values are never printed. Review staged names/diff and run a maintained secret scanner before push.'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = audit(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'no_pattern_findings' else 1

if __name__ == '__main__':
    raise SystemExit(main())
