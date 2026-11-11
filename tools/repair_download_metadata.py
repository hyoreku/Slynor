"""Add missing measured duration and YouTube covers to old downloads.

Dry run by default; `--apply` writes companion records (a one-time .bak is kept).
Audio bytes are never modified.

    python tools/repair_download_metadata.py
    python tools/repair_download_metadata.py --apply
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.repair_downloads import fetch_youtube_cover, repair


def default_folder():
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home())
    return base / 'OwnPlayerMusic' / 'Downloads'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Repair missing data of downloaded tracks.')
    parser.add_argument('--folder', default=None, help='Downloads folder; defaults to the app data')
    parser.add_argument('--apply', action='store_true', help='Write changes instead of a dry run')
    parser.add_argument('--no-cover', action='store_true', help='Only measure durations, no network')
    args = parser.parse_args(argv)
    folder = Path(args.folder) if args.folder else default_folder()
    report = repair(folder, apply=args.apply,
                    fetch_cover=None if args.no_cover else fetch_youtube_cover)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
