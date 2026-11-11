"""Run the fixed source-search experiment; writes only .local-artifacts, never music/settings."""
import argparse
import contextlib
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import queue
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

if __package__:
    from .common import (ADAPTERS, DEFAULT_ADAPTERS, DATASET_SHA256, DOWNLOADER_FAMILY, MAX_CARDS, MAX_LINE,
                         ROOT, SEARCH_TIMEOUT, SOURCE_FAMILY, ERROR_CODES, Failure, query_text, route_record,
                         safe_output_root, protocol_card)
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import (ADAPTERS, DEFAULT_ADAPTERS, DATASET_SHA256, DOWNLOADER_FAMILY, MAX_CARDS, MAX_LINE,
                        ROOT, SEARCH_TIMEOUT, SOURCE_FAMILY, ERROR_CODES, Failure, query_text, route_record,
                        safe_output_root, protocol_card)

WORKER = Path(__file__).with_name('worker.py')
NETWORKS = ('home_owner', 'vps_ru', 'sandbox')
INFRA = {'network', 'dns', 'timeout', 'access_denied', 'auth_required', 'rate_limited', 'dependency_missing'}
CSV_FIELDS = ['attempt_id','query_id','adapter','phase','status','error_category','http_status','technical_success',
              'cards','startup_ms','first_card_ms','complete_ms','first_card_end_to_end_ms','total_ms',
              'cpu_observed_tree_s','rss_tree_sum_peak_bytes','resource_samples','resource_method',
              'adapter_init_ms','adapter_search_ms','catalogue_provider_ms','catalogue_normalization_ms','failed_stage',
              'resolve_ms','download_ms','conversion_ms','delivery_ms','full_or_preview','cache_scope']


def csv_safe(value):
    return "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value


def load_queries(root=ROOT):
    p = Path(root)/'docs/experiment/queries-v1.csv'
    if sha256(p.read_bytes()).hexdigest() != DATASET_SHA256:
        raise Failure('dataset_changed_new_version_required')
    with p.open(encoding='utf-8-sig', newline='') as f: rows = list(csv.DictReader(f))
    if len(rows) < 50 or len({r['query_id'] for r in rows}) != len(rows): raise Failure('invalid_dataset')
    for row in rows: query_text(row['query'])
    return rows


def worker_python(adapter, root=ROOT):
    suffix = Path('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    environments = Path(root)/'.local-artifacts/source-experiment/envs'
    if adapter == 'yt-dlp.youtube': choices = [environments/'yt'/suffix, Path(root)/'.providers'/suffix]
    elif adapter == 'ytmusicapi.catalogue': choices = [environments/'catalogue'/suffix]
    elif adapter.startswith('msoc.'): choices = [environments/'msoc'/suffix, Path(root)/'.search'/suffix]
    else: return sys.executable
    for path in choices:
        if path.is_file(): return str(path)
    return None


def clean_environment():
    allowed = {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE','LOCALAPPDATA','LANG',
               'SSL_CERT_FILE','SSL_CERT_DIR','LD_LIBRARY_PATH'}
    # Never inherit tokens, browser cookies, proxies, PYTHONPATH or player settings.
    values = {k:v for k,v in os.environ.items() if k.upper() in allowed}
    # The NDJSON protocol is UTF-8 on every line. A Windows console locale would
    # otherwise encode Cyrillic cards in cp1251 and the controller should not guess.
    values['PYTHONIOENCODING'] = 'utf-8'
    values['PYTHONUTF8'] = '1'
    return values


def preflight(adapter, root=ROOT, proxy=None):
    python = worker_python(adapter, root)
    if python is None:
        return {'adapter': adapter, 'available': False, 'reason': 'worker_environment_missing', 'packages': {}}
    try:
        arguments = [python, '-I', '-X', 'utf8', '-u', str(WORKER), '--adapter', adapter, '--preflight']
        if proxy: arguments += ['--proxy', proxy]
        result = subprocess.run(arguments,
                                cwd=root, env=clean_environment(), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=12, check=False)
        if result.returncode or len(result.stdout) > MAX_LINE: raise Failure('preflight_protocol_error')
        data = json.loads(result.stdout)
        if data.get('event') != 'preflight': raise Failure('preflight_protocol_error')
        if not data.get('available') and data.get('reason') not in ERROR_CODES: data['reason'] = 'dependency_missing'
        return data
    except Exception:
        return {'adapter': adapter, 'available': False, 'reason': 'preflight_failed', 'packages': {}}


class Resources:
    """Sample process-tree CPU/RSS; exited-between-samples work may be missed."""
    def __init__(self, pid):
        self.available = False; self.active = False; self.max_cpu = {}; self.span_cpu = 0
        self.span_peak = 0; self.samples = 0; self.stop_event = threading.Event(); self.lock = threading.RLock()
        try:
            import psutil
        except ImportError: return
        try:
            self.psutil = psutil; self.process = psutil.Process(pid); self.available = True
        except (psutil.Error, OSError): return
        self.thread = threading.Thread(target=self.loop, daemon=True); self.thread.start()
    def tick(self):
        if not self.available: return
        with self.lock:
            try: processes = [self.process, *self.process.children(recursive=True)]
            except self.psutil.Error: return
            rss = 0; seen = False
            for proc in processes:
                try:
                    with proc.oneshot():
                        key = (proc.pid, proc.create_time()); cpu = proc.cpu_times(); memory = proc.memory_info()
                    self.max_cpu[key] = max(self.max_cpu.get(key, 0), cpu.user + cpu.system)
                    rss += memory.rss; seen = True
                except self.psutil.Error: continue
            if self.active and seen:
                self.span_peak = max(self.span_peak, rss); self.samples += 1
    def loop(self):
        while not self.stop_event.wait(.1): self.tick()
    def begin(self):
        self.tick()
        with self.lock:
            self.span_cpu = sum(self.max_cpu.values()); self.span_peak = 0; self.samples = 0; self.active = True
        self.tick()
    def end(self):
        self.tick()
        with self.lock:
            self.active = False
            measured = self.available and self.samples > 0
            return {'cpu_observed_tree_s': round(max(0, sum(self.max_cpu.values())-self.span_cpu),6) if measured else None,
                    'rss_tree_sum_peak_bytes': self.span_peak if measured else None,
                    'resource_samples': self.samples, 'resource_method': 'psutil_tree_sample_100ms' if measured else 'not_measured'}
    def over_budget(self):
        with self.lock: return self.available and self.span_peak > 512*1024*1024
    def close(self):
        self.stop_event.set()
        if self.available: self.thread.join(timeout=1)


class Worker:
    def __init__(self, python, adapter, root=ROOT, script=WORKER, proxy=None):
        self.adapter = adapter; self.start = time.perf_counter(); self.q = queue.Queue(); self.stdout_bytes = 0
        extra = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        # -I is required, and it also ignores PYTHON* variables, so UTF-8 output
        # has to be requested with an interpreter flag rather than the environment.
        command = [python, '-I', '-X', 'utf8', '-u', str(script), '--adapter', adapter]
        if proxy: command += ['--proxy', proxy]
        self.process = subprocess.Popen(command,
            cwd=root, env=clean_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, **extra)
        self.resources = Resources(self.process.pid); self.resources.begin()
        threading.Thread(target=self.read, daemon=True).start()
        try:
            ready = self.receive(12)
            if ready.get('event') != 'ready': raise Failure('worker_protocol_error')
            self.ready = ready; self.startup_ms = (time.perf_counter()-self.start)*1000
        except BaseException:
            self.close(); raise
    def read(self):
        while True:
            line = self.process.stdout.readline(MAX_LINE+1)
            self.stdout_bytes += len(line)
            if self.stdout_bytes > 1024*1024:
                self.q.put({'event':'error','code':'worker_output_limit'}); return
            if not line: self.q.put(None); return
            if len(line) > MAX_LINE: self.q.put({'event':'error','code':'worker_output_limit'}); return
            try: event = json.loads(line)
            except (ValueError, UnicodeError): event = {'event':'error','code':'worker_protocol_error'}
            self.q.put(event)
    def receive(self, timeout):
        try: event = self.q.get(timeout=max(.001,timeout))
        except queue.Empty: raise Failure('timeout') from None
        if event is None: raise Failure('worker_exited')
        if not isinstance(event,dict): raise Failure('worker_protocol_error')
        return event
    def search(self, query, cold, publish):
        started = time.perf_counter(); cards = []; status = None; code = None; http = None; phases = {}; failed_stage = None
        if not cold: self.resources.begin()
        try:
            self.process.stdin.write((json.dumps({'query':query},ensure_ascii=False)+'\n').encode())
            self.process.stdin.flush()
            deadline = started + SEARCH_TIMEOUT
            bytes_seen = 0
            while True:
                if self.resources.over_budget(): raise Failure('memory_limit')
                remaining = deadline-time.perf_counter()
                if remaining <= 0: raise Failure('timeout')
                try: event = self.receive(min(.1, remaining))
                except Failure as error:
                    if error.code == 'timeout' and time.perf_counter() < deadline: continue
                    raise
                bytes_seen += len(json.dumps(event).encode())
                if bytes_seen > 256*1024: raise Failure('worker_output_limit')
                now_ms = (time.perf_counter()-started)*1000
                kind = event.get('event')
                if kind in ('done','error'):
                    for field in ('adapter_init_ms','adapter_search_ms','catalogue_provider_ms','catalogue_normalization_ms'):
                        value=event.get(field)
                        if value is not None:
                            import math
                            if type(value) not in (int,float) or not math.isfinite(value) or value < 0: raise Failure('worker_protocol_error')
                        phases[field]=round(value,3) if value is not None else None
                if kind == 'card':
                    if len(cards) >= MAX_CARDS or not isinstance(event.get('card'),dict): raise Failure('worker_protocol_error')
                    item = {'rank':len(cards)+1,'first_seen_ms':round(now_ms,3),
                            'first_seen_end_to_end_ms':round(now_ms+(self.startup_ms if cold else 0),3), **protocol_card(self.adapter,event['card'])}
                    cards.append(item); publish(item)
                elif kind == 'done':
                    status = event.get('status')
                    if status not in ('ok','no_results') or event.get('cards') != len(cards): raise Failure('worker_protocol_error')
                    break
                elif kind == 'error':
                    status = 'failed'; code = event.get('code')
                    if code not in ERROR_CODES: code = 'worker_protocol_error'
                    http = event.get('http_status')
                    if type(http) is not int or not 100 <= http <= 599: http = None
                    failed_stage=event.get('failed_stage') if event.get('failed_stage') in ('validate','adapter_initialization','search') else None
                    break
                else: raise Failure('worker_protocol_error')
        except Exception as error:
            status = 'failed'; code = error.code if isinstance(error,Failure) else 'worker_io_error'
        elapsed = (time.perf_counter()-started)*1000
        if time.perf_counter() > started+SEARCH_TIMEOUT and status != 'failed': status,code = 'failed','timeout'
        result = {'status':status,'error_category':code,'http_status':http,
                  'technical_success':status in ('ok','no_results'),'cards':len(cards),
                  'startup_ms':round(self.startup_ms,3) if cold else None,
                  'first_card_ms':cards[0]['first_seen_ms'] if cards else None,'complete_ms':round(elapsed,3),
                  'first_card_end_to_end_ms':cards[0]['first_seen_end_to_end_ms'] if cards else None,
                  'total_ms':round(elapsed+(self.startup_ms if cold else 0),3),
                  'cache_scope':'disabled; warm means same process, no result cache',
                  'failed_stage':failed_stage, **phases,
                  'resolve_ms':None,'download_ms':None,'conversion_ms':None,'delivery_ms':None,
                  'full_or_preview':'unknown', **self.resources.end()}
        if code in ('timeout','memory_limit','worker_io_error','worker_protocol_error','worker_output_limit','worker_exited'): self.close()
        return result
    def close(self):
        if self.process.poll() is None:
            if os.name == 'nt':
                system_root = os.environ.get('SystemRoot')
                if system_root:
                    killer = Path(system_root)/'System32/taskkill.exe'
                    with contextlib.suppress(OSError,subprocess.SubprocessError):
                        subprocess.run([str(killer),'/PID',str(self.process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
            else:
                with contextlib.suppress(ProcessLookupError,PermissionError): os.killpg(self.process.pid, signal.SIGKILL)
            if self.process.poll() is None:
                with contextlib.suppress(OSError): self.process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired): self.process.wait(timeout=2)
        for stream in (self.process.stdin,self.process.stdout):
            if stream:
                with contextlib.suppress(OSError): stream.close()
        self.resources.close()


def append_json(path, row):
    with path.open('a',encoding='utf-8') as f: f.write(json.dumps(row,ensure_ascii=False)+'\n')


def write_csv(path, rows, fields):
    with path.open('x',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fields);w.writeheader()
        for row in rows: w.writerow({k:csv_safe(row.get(k)) for k in fields})


def run(network, profile, adapters, preflight_only=False, root=ROOT, proxy=None):
    queries=load_queries(root)
    if profile == 'smoke': queries=[queries[i] for i in (0,24,57)]
    route=route_record(proxy)
    identifier=(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+network+'-'+route['mode']
                +'-'+uuid4().hex[:8])
    folder=safe_output_root(root)/identifier;folder.mkdir()
    flights=[preflight(a,root,route['endpoint']) for a in adapters]
    resource_available = False
    try:
        import psutil
        resource_available=True;resource_version=psutil.__version__
    except ImportError: resource_version=None
    manifest={'run_id':identifier,'network_label':network,'profile':profile,'dataset_sha256':DATASET_SHA256,
              'protocol_version':'search-v0.3','route':route,
              'query_ids':[q['query_id'] for q in queries],
              'dataset_queries':60,'selected_queries':len(queries),'dataset_approval':'proposal; owner/reference review still pending',
              'mode':'metadata_only','downloads_enabled':False,'python_controller':platform.python_version(),
              'platform':platform.system()+' '+platform.release(),'psutil':resource_version,
              'source_families':{a:SOURCE_FAMILY[a] for a in adapters},'downloader_families':{a:DOWNLOADER_FAMILY[a] for a in adapters},
              'preflight':flights,'adapter_gates':{},'preflight_only':preflight_only,
              'sampling_note':'100 ms plus boundary samples; RSS sum includes shared pages; short-lived children can be missed',
              'tool_hashes':{p.name:sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}}
    if 'ytmusicapi.catalogue' in adapters:
        manifest['catalogue_scope'] = 'songs filter only; metadata, no audio check'
        manifest['catalogue_locale'] = {'language':'en','location':'','route':route['mode']}
        for name in ('catalogue_contract.py','ytmusic_catalogue.py','catalogue_http.py'):
            p=Path(root)/'backend'/name
            manifest['tool_hashes']['backend/'+name]=sha256(p.read_bytes()).hexdigest()
        p=Path(__file__).with_name('requirements-catalogue.txt')
        manifest['tool_hashes'][p.name]=sha256(p.read_bytes()).hexdigest()
    origin_path=Path(root)/'.local-artifacts/source-experiment/envs/yt/sandbox-origin.json'
    if network=='sandbox' and origin_path.is_file():
        manifest['sandbox_runtime_origin']=json.loads(origin_path.read_text())
    def save(): (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    save();results=[];review_rows=[]
    try:
        if not preflight_only:
            for flight in flights:
                adapter=flight['adapter']
                if not flight.get('available'):
                    manifest['adapter_gates'][adapter]=flight.get('reason','preflight_failed');continue
                if profile=='full' and not resource_available:
                    manifest['adapter_gates'][adapter]='psutil_required_for_full_measurement';continue
                consecutive=0;paused=None
                for query in queries:
                    if paused: break
                    worker=None
                    try:
                        worker=Worker(worker_python(adapter,root),adapter,root,proxy=route['endpoint'])
                        for phase in ('cold','warm'):
                            attempt_id=query['query_id']+'-'+adapter+'-'+phase
                            def publish(item):
                                row={'attempt_id':attempt_id,'query_id':query['query_id'],'adapter':adapter,'phase':phase,**item}
                                append_json(folder/'cards.jsonl',row)
                                review_rows.append({'attempt_id':attempt_id,'rank':item['rank'],'relevant':'',
                                    'artist_match':'','duration_match':'','version_match':'','full_or_preview':'unknown',
                                    'reviewer':'','title':item['title'],'artist':item['artist']})
                            result=worker.search(query['query'],phase=='cold',publish)
                            row={'attempt_id':attempt_id,'query_id':query['query_id'],'adapter':adapter,'phase':phase,**result}
                            results.append(row);append_json(folder/'search.jsonl',row)
                            print(adapter,query['query_id'],phase,result['status'],result['error_category'] or '',flush=True)
                            if result['error_category'] in INFRA: consecutive+=1
                            else: consecutive=0
                            if result['error_category'] in {'rate_limited','auth_required','access_denied'} or consecutive>=2:
                                paused='provider_paused_after_'+str(result['error_category']);break
                            if worker.process.poll() is not None:
                                paused='worker_stopped_after_'+str(result['error_category']);break
                            if sum(p.stat().st_size for p in folder.iterdir() if p.is_file())>25*1024*1024:
                                paused='run_disk_budget';break
                            time.sleep(1)  # At most one sequential request per second, no hidden retries.
                    except Exception as error:
                        manifest['adapter_gates'][adapter]=error.code if isinstance(error,Failure) else 'worker_start_failed'
                        break
                    finally:
                        if worker: worker.close()
                if paused: manifest['adapter_gates'][adapter]=paused
    except BaseException as error:
        manifest['interrupted']=isinstance(error,KeyboardInterrupt)
        manifest['controller_error']=type(error).__name__
        raise
    finally:
        manifest['actual_search_attempts']=len(results);manifest['search_attempts_planned']=0 if preflight_only else len(queries)*len(adapters)*2
        manifest['remaining_attempts_not_run']=manifest['search_attempts_planned']-len(results)
        save();write_csv(folder/'search.csv',results,CSV_FIELDS)
        write_csv(folder/'reviews.csv',review_rows,['attempt_id','rank','relevant','artist_match','duration_match','version_match','full_or_preview','reviewer','title','artist'])
        write_csv(folder/'availability.csv',[{'adapter':a,'query_id':q['query_id'],'available':'','reference_evidence':'','reviewer':'','negative_control':q['group']=='negative_control'} for a in adapters for q in queries],['adapter','query_id','available','reference_evidence','reviewer','negative_control'])
        print('REPORT_FOLDER',str(folder.relative_to(root)),flush=True)
    return folder


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--network',choices=NETWORKS,required=True)
    parser.add_argument('--profile',choices=('smoke','full'),default='smoke')
    parser.add_argument('--adapters',choices=ADAPTERS,nargs='+',default=list(DEFAULT_ADAPTERS))
    parser.add_argument('--proxy',default=None,help='explicit route, e.g. http://127.0.0.1:10809')
    parser.add_argument('--preflight',action='store_true')
    args=parser.parse_args()
    try: run(args.network,args.profile,list(dict.fromkeys(args.adapters)),args.preflight,proxy=args.proxy)
    except KeyboardInterrupt: print('Cancelled; completed rows are retained.');return 130
    except Exception as error:
        print('Experiment stopped:',error.code if isinstance(error,Failure) else type(error).__name__);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
