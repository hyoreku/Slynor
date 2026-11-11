"""Generate a hash-locked requirements file from a verified environment.

Stage 0 wants reproducibly installable sets, not just observed version pins.
Run it in a clone whose environment matches the observed constraints, with all
artifacts already downloaded:

    python tools/validated_lock.py --python .venv/Scripts/python.exe \
        --wheels ../wheels --out requirements/validated/gui-win-py313.txt \
        --provenance requirements/validated/gui-win-py313.provenance.json \
        --field tests="433 tests OK, 11 skipped"

The result installs with:
    python -m pip install --require-hashes --no-index --find-links <dir> -r <lock>
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import re
import subprocess
import sys

NORMALIZE = re.compile(r'[-_.]+')


def canonical(name):
    return NORMALIZE.sub('-', name).lower()


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def artifacts(directory):
    found = {}
    for path in sorted(Path(directory).iterdir()):
        if path.suffix == '.whl':
            parts = path.name[:-4].split('-')
        elif path.name.endswith('.tar.gz'):
            parts = path.name[:-7].split('-')
        else:
            continue
        if len(parts) < 2:
            continue
        found[(canonical(parts[0]), parts[1])] = path
    return found


def frozen(python):
    result = subprocess.run([python, '-m', 'pip', 'freeze'], capture_output=True,
                            text=True, check=True)
    rows = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '==' not in line:
            continue
        name, version = line.split('==', 1)
        rows.append((name.strip(), version.strip()))
    return rows


def pip_version(python):
    result = subprocess.run([python, '-m', 'pip', '--version'], capture_output=True,
                            text=True, check=True)
    parts = result.stdout.split()
    return parts[1] if len(parts) > 1 else 'unknown'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--python', required=True)
    parser.add_argument('--wheels', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--provenance')
    parser.add_argument('--title', default='Slynor dependencies')
    parser.add_argument('--field', action='append', default=[])
    args = parser.parse_args(argv)

    python = str(Path(args.python).resolve())
    available = artifacts(args.wheels)
    rows = []
    missing = []
    for name, version in frozen(python):
        key = (canonical(name), version)
        path = available.get(key)
        if path is None:
            missing.append(f'{name}=={version}')
            continue
        rows.append((name, version, path))
    if missing:
        print(json.dumps({'error': 'no downloaded artifact for pinned version',
                          'missing': missing}, indent=2))
        return 1

    version = subprocess.run([python, '-c', 'import sys;print(sys.version.split()[0])'],
                             capture_output=True, text=True, check=True).stdout.strip()
    pip = pip_version(python)
    extra = {}
    for item in args.field:
        key, _, value = item.partition('=')
        extra[key.strip()] = value.strip()

    lines = [
        f'# {args.title} — hash-locked set for Windows x64 / CPython {version}.',
        f'# pip {pip}; '
        f'generated {datetime.now(timezone.utc).isoformat(timespec="seconds")}.',
        '# Install offline:',
        '#   python -m pip install --require-hashes --no-index --find-links <wheels> \\',
        f'#       -r {args.out.replace(chr(92), "/")}',
        '# Every requirement is both pinned and hashed; do not edit by hand.',
        '',
    ]
    for name, ver, path in rows:
        lines.append(f'{name}=={ver} \\')
        lines.append(f'    --hash=sha256:{digest(path)}')
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')

    if args.provenance:
        payload = {
            'title': args.title,
            'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'python': version,
            'pip': pip,
            'platform': platform.platform(),
            'machine': platform.machine(),
            'packages': len(rows),
            'fields': extra,
            'requirements': {name: {'version': ver,
                                    'artifact': path.name,
                                    'sha256': digest(path)} for name, ver, path in rows},
        }
        Path(args.provenance).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                         encoding='utf-8')
    print(json.dumps({'locked': len(rows), 'out': str(out),
                      'provenance': args.provenance, 'missing': missing}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
