"""Generate an offline review page from an existing source experiment."""
import argparse
import json
from pathlib import Path
import sys

if __package__:
    from .integrity import validate
    from .report import read_csv, read_jsonl
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from integrity import validate
    from report import read_csv, read_jsonl


def review_data(folder):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
    attempts = read_jsonl(folder / 'search.jsonl')
    cards = read_jsonl(folder / 'cards.jsonl')
    reviews = read_csv(folder / 'reviews.csv')
    availability = read_csv(folder / 'availability.csv')
    queries, adapters = validate(manifest, attempts, cards, reviews, availability)

    existing = {(row['attempt_id'], int(row['rank'])): row for row in reviews}
    public_cards = []
    for card in cards:
        row = existing.get((card['attempt_id'], card['rank']))
        review = row or {
            'attempt_id': card['attempt_id'], 'rank': card['rank'],
            'title': card['title'], 'artist': card['artist'], 'full_or_preview': 'unknown',
        }
        public_cards.append({
            **{key: card.get(key) for key in ('attempt_id', 'rank', 'title', 'artist', 'duration_ms',
                                              'recording_version', 'public_page')},
            'review': review,
        })
    return {
        'run': manifest['run_id'],
        'queries': [{key: query.get(key) for key in ('query_id', 'query', 'group', 'target_artist',
                    'target_title', 'target_version', 'reference_duration_ms', 'reference_recording_id')}
                    for query in queries],
        'adapters': adapters,
        'cards': public_cards,
        'availability': availability,
        'attempts': [{key: attempt.get(key) for key in ('attempt_id', 'status', 'error_category')}
                     for attempt in attempts],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_folder', type=Path)
    parser.add_argument('--output', type=Path, help='New HTML file (default: review.html in the run folder)')
    args = parser.parse_args()
    output = args.output or args.run_folder / 'review.html'
    try:
        payload = json.dumps(review_data(args.run_folder), ensure_ascii=False).replace('<', r'\u003c')
        template = Path(__file__).with_name('review.html').read_text(encoding='utf-8')
        with output.open('x', encoding='utf-8') as stream:
            stream.write(template.replace('__REVIEW_DATA__', payload, 1))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'Review page could not be created: {error}\n')
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
