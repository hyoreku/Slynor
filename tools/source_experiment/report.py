"""Build a local summary; manual relevance and independent availability remain separate."""
import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys

if __package__:
    from .common import ADAPTERS, Failure, safe_output_root
    from .integrity import boolean, validate
else:
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from common import ADAPTERS, Failure, safe_output_root
    from integrity import boolean, validate


def read_jsonl(path):
    if not path.exists(): return []
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)(): raise Failure('report_reparse_point')
    if path.stat().st_size > 25*1024*1024: raise Failure('report_file_too_large')
    with path.open(encoding='utf-8') as f: return [json.loads(line) for line in f if line.strip()]


def read_csv(path):
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)(): raise Failure('report_reparse_point')
    if path.stat().st_size > 25*1024*1024: raise Failure('report_file_too_large')
    with path.open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))


def relevance(attempt, cards, reviews):
    """No unreviewed/partially reviewed card can become an automatic match."""
    own = [c for c in cards if c['attempt_id']==attempt['attempt_id']]
    judged=[];first=[];unjudged_times=[]
    for c in own:
        r=reviews.get((attempt['attempt_id'],str(c['rank'])))
        if not r or not r.get('reviewer','').strip():
            judged.append(None);unjudged_times.append(c['first_seen_end_to_end_ms']);continue
        value=boolean(r.get('relevant'))
        if value is True and not all(boolean(r.get(k)) is True for k in ('artist_match','duration_match','version_match')):
            raise Failure('relevant_card_missing_match_checks')
        judged.append(value)
        if value is None: unjudged_times.append(c['first_seen_end_to_end_ms'])
        if value is True: first.append(c['first_seen_end_to_end_ms'])
    if first:
        earliest=min(first)
        return True,earliest if not any(t < earliest for t in unjudged_times) else None
    if attempt['status']=='no_results': return False,None
    if not own or any(v is None for v in judged): return None,None
    return False,None


def summarize(folder):
    folder=Path(folder)
    manifest_path=folder/'manifest.json'
    if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)() or manifest_path.is_symlink():
        raise Failure('report_reparse_point')
    if manifest_path.stat().st_size > 256*1024: raise Failure('report_file_too_large')
    manifest=json.loads(manifest_path.read_text())
    attempts=read_jsonl(folder/'search.jsonl');cards=read_jsonl(folder/'cards.jsonl')
    review_rows=read_csv(folder/'reviews.csv');availability_rows=read_csv(folder/'availability.csv')
    queries,adapters=validate(manifest,attempts,cards,review_rows,availability_rows)
    reviews={}
    for r in review_rows:
        key=(r['attempt_id'],r['rank'])
        if key in reviews: raise Failure('duplicate_manual_review')
        reviews[key]=r
    availability={}
    for r in availability_rows:
        key=(r['adapter'],r['query_id'])
        if key in availability: raise Failure('duplicate_availability')
        availability[key]=r
    rows=[]
    for adapter in adapters:
        for phase in ('cold','warm'):
            group=[a for a in attempts if a['adapter']==adapter and a['phase']==phase]
            eligible=[];incomplete=False;not_reviewed=0;useful=[];matched=0
            for (available_adapter,qid),r in availability.items():
                if available_adapter != adapter: continue
                if boolean(r.get('negative_control')) is True: continue
                available=boolean(r.get('available'))
                if available is None: incomplete=True;continue
                if not (r.get('reviewer','').strip() and r.get('reference_evidence','').strip()):
                    raise Failure('available_without_independent_evidence')
                if not available: continue
                eligible.append(qid)
                a=next((x for x in group if x['query_id']==qid),None)
                if a is None: not_reviewed+=1;incomplete=True;continue
                if a['status']=='failed' and not any(c['attempt_id']==a['attempt_id'] for c in cards):
                    value,first=False,None  # Independently available: technical failure stays in denominator.
                else: value, first=relevance(a,cards,reviews)
                if value is None: not_reviewed+=1;incomplete=True
                elif value:
                    matched+=1
                    if first is not None: useful.append(first)
            latency=[a['total_ms'] for a in group if a['status'] in ('ok','no_results')]
            first_card=[a['first_card_end_to_end_ms'] for a in group if a['first_card_end_to_end_ms'] is not None]
            rows.append({'adapter':adapter,'phase':phase,'attempted':len(group),
                         'expected_queries':len(queries),
                         'technical_success':sum(a['technical_success'] for a in group),
                         'with_cards':sum(a['cards']>0 for a in group),
                         'eligible_queries':len(eligible),'manual_matches':matched,
                         'quality_rate':matched/len(eligible) if eligible and not incomplete else None,
                         'quality_review_incomplete':incomplete,'manual_unreviewed':not_reviewed,
                         'first_card_p50_ms':statistics.median(first_card) if first_card else None,
                         'complete_success_p50_ms':statistics.median(latency) if latency else None,
                         'first_useful_p50_ms':statistics.median(useful) if useful and len(useful)==matched else None,
                         'first_useful_timing_samples':len(useful),
                         'first_useful_timing_incomplete':len(useful)!=matched,
                         'download_rate':None,
                         'negative_controls':sum(q['group']=='negative_control' for q in queries),
                         'negative_with_cards':sum(a['cards']>0 and next(q for q in queries if q['query_id']==a['query_id'])['group']=='negative_control' for a in group),
                         'errors':{code:sum(a.get('error_category')==code for a in group)
                                   for code in sorted({a['error_category'] for a in group if a.get('error_category')})}})
    return {'run_id':manifest['run_id'],'network_label':manifest['network_label'],'profile':manifest['profile'],
            'route':manifest.get('route',{'mode':'direct','endpoint':None}),
            'downloads_run':False,'dataset_sha256':manifest['dataset_sha256'],
            'query_ids':[q['query_id'] for q in queries],
            'protocol_version':manifest.get('protocol_version','search-v0.1'),
            'tool_hashes':manifest.get('tool_hashes',{}),'preflight':manifest['preflight'],
            'environment':{k:manifest.get(k) for k in ('python_controller','platform','psutil')},
            'evidence_sha256':{n:sha256((folder/n).read_bytes()).hexdigest() if (folder/n).exists() else None for n in ('manifest.json','search.jsonl','cards.jsonl','reviews.csv','availability.csv')},
            'manual_inputs_sha256':{n:sha256((folder/n).read_bytes()).hexdigest() for n in ('reviews.csv','availability.csv')},
            'qualification':'not assessed; dataset/reference approval, downloads and network evidence incomplete',
            'rows':rows,'adapter_gates':manifest.get('adapter_gates',{}),
            'selection':'not made; metadata phase and incomplete rights/network/manual evidence'}


def render(data):
    def number(value): return 'не измерено' if value is None else str(round(value,2))
    route=data.get('route') or {'mode':'direct','endpoint':None}
    route_text='direct' if route.get('mode')!='proxy' else 'explicit proxy '+str(route.get('endpoint'))
    lines=['# Поиск источников Slynor — отчёт','',
           'Сеть: '+data['network_label']+'. Профиль: '+data['profile']+'. Маршрут: '+route_text+'.',
           'Технический успех не означает релевантность. Полное аудио, download/resolve/conversion/delivery не проверялись.',
           'При неизвестной доступности или неполной ручной оценке доля качества не вычисляется.','',
           'Процент относится только к выбранному профилю и подтверждённой доступной части. Smoke не подтверждает критерий 90%.',
           'Набор и эталоны ещё не согласованы; приёмка 90%/95% не выполнена.','',
           '| Адаптер | Режим | Выполнено | Технический успех | С карточками | Релевантность | Первая карточка p50, мс | Первый полезный p50, мс |',
           '|---|---|---:|---:|---:|---|---:|---:|']
    for r in data['rows']:
        quality='не измерено' if r['quality_rate'] is None else str(r['manual_matches'])+'/'+str(r['eligible_queries'])+' ('+str(round(r['quality_rate']*100,2))+'%)'
        lines.append('| '+r['adapter']+' | '+r['phase']+' | '+str(r['attempted'])+' | '+str(r['technical_success'])+' | '+str(r['with_cards'])+' | '+quality+' | '+number(r['first_card_p50_ms'])+' | '+number(r['first_useful_p50_ms'])+' |')
    lines += ['', '## Категории ошибок и невыполненные части','']
    for r in data['rows']:
        if r['errors']: lines.append('- '+r['adapter']+' / '+r['phase']+': '+json.dumps(r['errors'],ensure_ascii=False))
    for name,reason in data['adapter_gates'].items(): lines.append('- '+name+': '+reason)
    lines += ['', 'Основной путь и независимый резерв не выбраны. 90%/95% — цели эксперимента, а не эти результаты.',
              'MusicBrainz, spotDL matching и выбранный musicdl-provider в этот первый прогон не включены.',
              'Лицензия msoc, права на конкретные файлы, Windows и второй сетевой маршрут остаются отдельными проверками.','']
    return '\n'.join(lines)


def latest(network, route=None):
    candidates=[]
    for p in safe_output_root().iterdir():
        if p.is_symlink() or not p.is_dir(): continue
        manifest=p/'manifest.json'
        if not manifest.is_file(): continue
        m=json.loads(manifest.read_text())
        if m.get('network_label')!=network or m.get('preflight_only'): continue
        if route is not None and (m.get('route') or {}).get('mode','direct')!=route: continue
        candidates.append(p)
    if not candidates: raise Failure('no_run_for_network')
    return max(candidates,key=lambda p:p.name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--network',choices=('home_owner','vps_ru','sandbox'),required=True)
    parser.add_argument('--route',choices=('direct','proxy'),default=None)
    args=parser.parse_args()
    try:
        folder=latest(args.network,args.route);data=summarize(folder)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        md=folder/('summary-'+stamp+'.md');md.write_text(render(data),encoding='utf-8')
        (folder/('summary-'+stamp+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        print(render(data));print('SUMMARY_FILE',str(md))
    except Exception as error:
        print('Report stopped:',error.code if isinstance(error,Failure) else type(error).__name__);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
