"""Compare two validated metadata runs; never select an unmeasured audio path."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

if __package__:
    from .common import ROOT, Failure, safe_output_root
    from .report import latest, summarize
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ROOT, Failure, safe_output_root
    from report import latest, summarize


def package_identity(flight):
    return {name: {k: info.get(k) for k in ('version', 'metadata_sha256')}
            for name, info in flight.get('packages', {}).items()}


def compare(home, vps):
    if home['network_label'] != 'home_owner' or vps['network_label'] != 'vps_ru':
        raise Failure('comparison_network_labels_required')
    reasons = []
    for field in ('dataset_sha256', 'profile', 'query_ids', 'protocol_version'):
        if home.get(field) != vps.get(field): reasons.append(field + '_mismatch')
    if home.get('route') != vps.get('route'): reasons.append('route_mismatch')
    hashes = home.get('tool_hashes', {})
    if not hashes or hashes != vps.get('tool_hashes'):
        reasons.append('tool_hashes_missing_or_mismatch')
    home_flights = {p['adapter']: p for p in home['preflight']}
    vps_flights = {p['adapter']: p for p in vps['preflight']}
    if set(home_flights) != set(vps_flights): reasons.append('adapter_set_mismatch')
    rows = []
    for adapter in sorted(set(home_flights) | set(vps_flights)):
        local_reasons = list(reasons)
        hflight, vflight = home_flights.get(adapter), vps_flights.get(adapter)
        if not hflight or not vflight:
            local_reasons.append('adapter_not_in_both_runs')
        elif not hflight.get('available') or not vflight.get('available'):
            local_reasons.append('worker_not_available_in_both_runs')
        elif package_identity(hflight) != package_identity(vflight):
            local_reasons.append('package_identity_mismatch')
        for phase in ('cold', 'warm'):
            h = next((r for r in home['rows'] if (r['adapter'], r['phase']) == (adapter, phase)), None)
            v = next((r for r in vps['rows'] if (r['adapter'], r['phase']) == (adapter, phase)), None)
            comparable = not local_reasons and h is not None and v is not None
            delta = None
            if comparable and h['quality_rate'] is not None and v['quality_rate'] is not None:
                delta = round((v['quality_rate'] - h['quality_rate']) * 100, 6)
            rows.append({'adapter': adapter, 'phase': phase, 'same_protocol_and_versions': comparable,
                         'blocking_reasons': local_reasons, 'home': h, 'vps': v,
                         'quality_delta_percentage_points': delta})
    return {'home_run_id': home['run_id'], 'vps_run_id': vps['run_id'],
            'same_protocol_and_versions': bool(rows) and all(r['same_protocol_and_versions'] for r in rows),
            'environment': {'home': home.get('environment'), 'vps': vps.get('environment')},
            'evidence_sha256': {'home': home.get('evidence_sha256'), 'vps': vps.get('evidence_sha256')},
            'rows': rows, 'downloads_compared': False,
            'selection': 'not made; metadata comparison does not verify full audio, rights or independent fallback',
            'caveat': 'Network labels are supplied by the operator; OS, hardware and routing are not independently attested. Latency differences cannot be attributed to the network alone. Package identity compares installed METADATA, not verified wheel hashes.'}


def render(data):
    def quality(row):
        if row is None or row['quality_rate'] is None: return 'не измерено'
        return str(row['manual_matches']) + '/' + str(row['eligible_queries'])
    lines = ['# Slynor: домашняя сеть и VPS в РФ', '',
             'Прогоны: ' + data['home_run_id'] + ' / ' + data['vps_run_id'] + '.',
             'Маршруты сравниваются явно; direct и proxy не смешиваются.',
             'Метки сети указывает оператор. ОС, оборудование и маршрут могут различаться; разницу времени нельзя приписывать только сети.', '',
             '| Адаптер | Режим | Совпадают протокол/версии | Дом: релевантные/доступные | VPS: релевантные/доступные | Δ, п.п. |',
             '|---|---|---|---|---|---|']
    for r in data['rows']:
        delta = r['quality_delta_percentage_points']
        lines.append('| ' + ' | '.join((r['adapter'], r['phase'], 'да' if r['same_protocol_and_versions'] else 'нет',
            quality(r['home']), quality(r['vps']), 'не измерено' if delta is None else str(delta))) + ' |')
    lines += ['', 'Причины несопоставимости:', '']
    found = False
    for r in data['rows']:
        if r['phase'] == 'cold' and r['blocking_reasons']:
            found = True; lines.append('- ' + r['adapter'] + ': ' + ', '.join(r['blocking_reasons']))
    if not found: lines.append('Несовпадений протокола/версий не выявлено; полнота ручной оценки указана в исходных сводках.')
    lines += ['', 'Полное аудио, права и независимость резерва не подтверждены. Основной путь и резерв не выбраны.',
              '90%/95% остаются целями эксперимента. Сравнение smoke не подтверждает приёмку полного набора.', '']
    return '\n'.join(lines)


def run_folder(identifier, network):
    if identifier is None: return latest(network)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', identifier): raise Failure('invalid_run_id')
    folder = safe_output_root() / identifier
    if not folder.is_dir(): raise Failure('run_not_found')
    return folder


def save(data, root=ROOT):
    # Reuse the same ancestor/reparse-point checks as run output.
    runs = safe_output_root(root)
    parent = runs.parent / 'comparisons'
    if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)(): raise Failure('output_reparse_point')
    parent.mkdir(exist_ok=True)
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    folder = parent / identifier; folder.mkdir()
    (folder/'comparison.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (folder/'comparison.md').write_text(render(data), encoding='utf-8')
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home-run', help='Run ID under research runs; default: latest home_owner')
    parser.add_argument('--vps-run', help='Run ID under research runs; default: latest vps_ru')
    args = parser.parse_args()
    try:
        home = summarize(run_folder(args.home_run, 'home_owner'))
        vps = summarize(run_folder(args.vps_run, 'vps_ru'))
        data = compare(home, vps); folder = save(data)
        print(render(data)); print('COMPARISON_FOLDER', str(folder.relative_to(ROOT)))
    except Exception as error:
        print('Comparison stopped:', error.code if isinstance(error, Failure) else type(error).__name__)
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
