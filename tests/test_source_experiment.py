"""Research protocol/safety/measurement tests. No live requests or audio files."""
from collections import namedtuple
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools.source_experiment import common, report, run

class ProtocolTests(unittest.TestCase):
    def test_fixed_dataset_is_60_unique_queries_with_downloads_disabled(self):
        rows=run.load_queries()
        self.assertEqual(len(rows),60)
        self.assertEqual(len({r['query_id'] for r in rows}),60)
        self.assertTrue(all(r['download_allowed']=='false' for r in rows))
    def test_dataset_edits_require_new_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'docs/experiment/queries-v1.csv';p.parent.mkdir(parents=True);p.write_text('modified')
            with self.assertRaises(common.Failure):run.load_queries(Path(tmp))
    def test_queries_are_data_without_url_or_control_characters(self):
        for value in ['https://example.invalid/x','Artist\nOther','file:/tmp/a','']:
            with self.assertRaises(common.Failure):common.query_text(value)
        self.assertEqual(common.query_text('Song --exec harmless'),'Song --exec harmless')
    def test_signed_urls_and_generated_tokens_are_removed(self):
        token='123456789:'+'A'*35
        credentials='user:password'
        url='https://'+credentials+'@example.invalid/media?signature=secret'
        text=common.safe_text('Song '+url+' '+token)
        self.assertNotIn(token,text);self.assertNotIn('signature',text);self.assertNotIn('password',text)
    def test_safe_http_policy_checks_host_and_resolved_addresses(self):
        for url in ['http://archive.org/x','https://archive.org.evil.invalid/x','https://evilarchive.org/x']:
            with self.assertRaises(common.Failure):common.checked_url(url,'archive.org',False)
        address=[(0,0,0,'',('127.0.0.1',443))]
        with patch.object(common.socket,'getaddrinfo',return_value=address):
            with self.assertRaises(common.Failure):common.checked_url('https://archive.org/metadata/x','archive.org')
    def test_protocol_strips_extra_fields_and_cannot_assert_full_audio(self):
        result=common.protocol_card('yt-dlp.youtube',{'title':'Title','artist':'Artist','raw_signed_url':'secret',
            'public_page':'https://www.youtube.com/watch?v=abcdefghijk','duration_ms':200000,
            'full_or_preview':'full','recording_version':'studio','rank':999})
        self.assertNotIn('raw_signed_url',result);self.assertNotIn('rank',result)
        self.assertEqual(result['full_or_preview'],'unknown');self.assertEqual(result['recording_version'],'unknown')
        self.assertEqual(result['duration_ms'],200000)
    def test_signed_or_foreign_public_page_is_rejected(self):
        with self.assertRaises(common.Failure):
            common.protocol_card('archive.http',{'public_page':'https://example.invalid/?token=sensitive'})
    def test_audio_resource_is_hashed_and_never_opened(self):
        url='https://mp3uk.net/private/audio?signature=synthetic'
        item=SimpleNamespace(title='T',artist='A',url=url)
        result=common.msoc_card(item)
        self.assertNotIn(url,str(result));self.assertEqual(len(result['resource_fingerprint']),64)
        self.assertIsNone(common.msoc_card(SimpleNamespace(url='file:/tmp/private')))
    def test_duration_and_http_errors_do_not_invent_facts(self):
        self.assertEqual(common.duration_ms(1.316),1316)
        for value in [None,0,-3,float('nan'),float('inf')]:self.assertIsNone(common.duration_ms(value))
        self.assertEqual(common.http_failure(403).code,'access_denied')
        self.assertEqual(common.http_failure(429).code,'rate_limited')
    def test_csv_formula_fields_are_escaped(self):
        self.assertEqual(run.csv_safe('=HYPERLINK(x)'),"'=HYPERLINK(x)")
        self.assertEqual(run.csv_safe(3.5),3.5)
    def test_output_symlink_cannot_point_at_user_data(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root=Path(tmp)
            try:(root/'.local-artifacts').symlink_to(other,target_is_directory=True)
            except OSError:self.skipTest('Symlinks unavailable')
            with self.assertRaises(common.Failure):common.safe_output_root(root)
    def test_no_external_secrets_or_proxy_settings_are_inherited(self):
        with patch.dict(run.os.environ,{'OWNPLAYER_SPOTIFY_CLIENT_SECRET':'synthetic','HTTPS_PROXY':'synthetic','PYTHONPATH':'synthetic'}):
            result=run.clean_environment()
        for name in ['OWNPLAYER_SPOTIFY_CLIENT_SECRET','HTTPS_PROXY','PYTHONPATH']:self.assertNotIn(name,result)
    def test_worker_protocol_is_always_utf8_even_on_a_cp1251_console(self):
        values=run.clean_environment()
        self.assertEqual(values['PYTHONIOENCODING'],'utf-8')
        self.assertEqual(values['PYTHONUTF8'],'1')

class RouteTests(unittest.TestCase):
    def test_explicit_proxy_is_validated_and_normalized(self):
        self.assertEqual(common.proxy_url('http://127.0.0.1:10809'), 'http://127.0.0.1:10809')
        self.assertEqual(common.proxy_url('socks5h://[::1]:1080'), 'socks5h://[::1]:1080')
        for value in ('', ' ', '127.0.0.1:10809', 'http://127.0.0.1', 'http://user:pass@127.0.0.1:10809',
                      'http://127.0.0.1:10809/path', 'http://127.0.0.1:10809?x=1',
                      'ftp://127.0.0.1:21', 123):
            with self.assertRaises(common.Failure):
                common.proxy_url(value)

    def test_route_record_marks_direct_and_proxy(self):
        self.assertEqual(common.route_record(), {'mode': 'direct', 'endpoint': None})
        self.assertEqual(common.route_record('http://127.0.0.1:10809'),
                         {'mode': 'proxy', 'endpoint': 'http://127.0.0.1:10809'})

    def test_preflight_only_manifest_records_the_route(self):
        dataset = Path(common.ROOT) / 'docs/experiment/queries-v1.csv'
        if not dataset.is_file():
            self.skipTest('local experiment dataset is not published in the repository')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'docs/experiment').mkdir(parents=True)
            shutil.copy(dataset, root / 'docs/experiment/queries-v1.csv')
            folder = run.run('home_owner', 'smoke', ['archive.http'], preflight_only=True,
                             root=root, proxy='http://127.0.0.1:10809')
            manifest = json.loads((folder / 'manifest.json').read_text())
        self.assertEqual(manifest['route'], {'mode': 'proxy', 'endpoint': 'http://127.0.0.1:10809'})
        self.assertEqual(manifest['protocol_version'], 'search-v0.3')
        self.assertEqual(manifest['preflight'][0]['routing'], 'explicit_proxy; environment and OS proxy settings ignored')


class ResourceTests(unittest.TestCase):
    def test_unavailable_monitor_returns_null_instead_of_zero_metrics(self):
        with patch.dict(sys.modules,{'psutil':None}):r=run.Resources(1)
        data=r.end();r.close()
        self.assertIsNone(data['cpu_observed_tree_s']);self.assertIsNone(data['rss_tree_sum_peak_bytes'])
    def test_tree_cpu_does_not_add_children_cpu_twice(self):
        class Proc:
            def __init__(self,pid,cpu,rss):self.pid=pid;self.value=cpu;self.rss=rss
            def create_time(self):return 1
            def oneshot(self):
                import contextlib
                return contextlib.nullcontext()
            def cpu_times(self):return SimpleNamespace(user=self.value,system=0,children_user=9999)
            def memory_info(self):return SimpleNamespace(rss=self.rss)
            def children(self,recursive=True):return [child] if self.pid==1 else []
        parent=Proc(1,1,100);child=Proc(2,2,200)
        fake=SimpleNamespace(Process=lambda _:parent,Error=RuntimeError)
        with patch.dict(sys.modules,{'psutil':fake}):
            r=run.Resources(1);r.begin();parent.value=3;child.value=5;data=r.end();r.close()
        self.assertEqual(data['cpu_observed_tree_s'],5)
        self.assertEqual(data['rss_tree_sum_peak_bytes'],300)

class WorkerTests(unittest.TestCase):
    def worker(self,body,proxy=None):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)/'fixture_worker.py';p.write_text(body)
        w=run.Worker(sys.executable,'archive.http',Path(self.tmp.name),p,proxy=proxy);self.addCleanup(w.close)
        return w
    def test_explicit_proxy_is_passed_only_on_the_command_line(self):
        body='''import json,sys
print(json.dumps({'event':'ready'}),flush=True)
for line in sys.stdin:
 print(json.dumps({'event':'card','card':{'title':' '.join(sys.argv),'artist':'Fixture','source_id':'fixture'}}),flush=True)
 print(json.dumps({'event':'done','cards':1,'status':'ok'}),flush=True)
'''
        w=self.worker(body,proxy='http://127.0.0.1:10809');items=[]
        w.search('fixture',True,items.append)
        self.assertIn('--proxy',w.process.args)
        self.assertIn('http://127.0.0.1:10809',w.process.args)
        self.assertIn('--proxy',items[0]['title'])
    def test_same_process_warm_attempt_has_separate_startup_and_query_stays_in_stdin(self):
        body='''import json,sys
print(json.dumps({'event':'ready'}),flush=True)
for line in sys.stdin:
 q=json.loads(line)['query']
 print(json.dumps({'event':'card','card':{'title':q,'artist':'Fixture','source_id':'fixture'}}),flush=True)
 print(json.dumps({'event':'done','cards':1,'status':'ok'}),flush=True)
'''
        w=self.worker(body);items=[]
        cold=w.search('Fixture --exec never-run',True,items.append)
        warm=w.search('Fixture --exec never-run',False,items.append)
        self.assertTrue(cold['technical_success']);self.assertTrue(warm['technical_success'])
        self.assertIsNotNone(cold['startup_ms']);self.assertIsNone(warm['startup_ms'])
        self.assertGreaterEqual(cold['first_card_end_to_end_ms'],cold['first_card_ms'])
        self.assertEqual(warm['first_card_end_to_end_ms'],warm['first_card_ms'])
        self.assertEqual(items[0]['title'],'Fixture --exec never-run')
        self.assertNotIn('Fixture --exec never-run',w.process.args)
    def test_worker_deadline_kills_hanging_process(self):
        w=self.worker("import json,sys,time\nprint(json.dumps({'event':'ready'}),flush=True)\nsys.stdin.readline()\ntime.sleep(5)\n")
        began=time.monotonic()
        with patch.object(run,'SEARCH_TIMEOUT',.15):result=w.search('fixture',True,lambda _:None)
        self.assertEqual(result['error_category'],'timeout');self.assertLess(time.monotonic()-began,2)
        self.assertIsNotNone(w.process.poll())
    def test_unknown_error_text_is_not_exported(self):
        w=self.worker("import json,sys\nprint(json.dumps({'event':'ready'}),flush=True)\nsys.stdin.readline()\nprint(json.dumps({'event':'error','code':'https://private.invalid/?secret=synthetic'}),flush=True)\n")
        result=w.search('fixture',True,lambda _:None)
        self.assertEqual(result['error_category'],'worker_protocol_error');self.assertNotIn('private.invalid',str(result))

class ReportTests(unittest.TestCase):
    def attempt(self,status='ok'):
        return {'attempt_id':'Q001-archive.http-cold','query_id':'Q001','adapter':'archive.http','phase':'cold',
                'status':status,'technical_success':status!='failed','cards':0 if status=='failed' else 1,
                'total_ms':300,'first_card_end_to_end_ms':None if status=='failed' else 200,'error_category':'timeout' if status=='failed' else None}
    def folder(self,status='ok',available='',reviewer='',evidence='',review=None):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);p=Path(tmp.name)
        (p/'manifest.json').write_text(json.dumps({'run_id':'test','network_label':'sandbox','profile':'smoke',
            'dataset_sha256':common.DATASET_SHA256,'selected_queries':3,'search_attempts_planned':6,
            'actual_search_attempts':1,'remaining_attempts_not_run':5,
            'preflight':[{'adapter':'archive.http','available':True,'packages':{}}]}))
        a=self.attempt(status);(p/'search.jsonl').write_text(json.dumps(a)+'\n')
        cards=[] if status=='failed' else [{'attempt_id':a['attempt_id'],'rank':1,'first_seen_end_to_end_ms':200,
            'query_id':'Q001','adapter':'archive.http','phase':'cold'}]
        (p/'cards.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in cards))
        fields=['attempt_id','rank','relevant','artist_match','duration_match','version_match','full_or_preview','reviewer']
        row={'attempt_id':a['attempt_id'],'rank':1,**(review or {})}
        run.write_csv(p/'reviews.csv',[row] if cards else [],fields)
        run.write_csv(p/'availability.csv',[
            {'adapter':'archive.http','query_id':'Q001','available':available,'reviewer':reviewer,'reference_evidence':evidence,'negative_control':'false'},
            {'adapter':'archive.http','query_id':'Q025','available':'false','reviewer':'Tester','reference_evidence':'independent synthetic fixture','negative_control':'false'},
            {'adapter':'archive.http','query_id':'Q058','available':'','reviewer':'','reference_evidence':'','negative_control':'true'},
        ],['adapter','query_id','available','reviewer','reference_evidence','negative_control'])
        return p
    def test_route_is_part_of_the_summary_and_report(self):
        p=self.folder()
        manifest=json.loads((p/'manifest.json').read_text())
        self.assertEqual(report.summarize(p)['route'],{'mode':'direct','endpoint':None})
        manifest['route']={'mode':'proxy','endpoint':'http://127.0.0.1:10809'}
        (p/'manifest.json').write_text(json.dumps(manifest))
        data=report.summarize(p)
        self.assertEqual(data['route']['mode'],'proxy')
        self.assertIn('explicit proxy http://127.0.0.1:10809',report.render(data))
    def test_http_success_without_manual_review_has_no_quality_percentage(self):
        data=report.summarize(self.folder())
        self.assertEqual(data['rows'][0]['technical_success'],1)
        self.assertIsNone(data['rows'][0]['quality_rate'])
        self.assertIsNone(data['rows'][0]['first_useful_p50_ms'])
        self.assertIsNone(data['rows'][0]['download_rate'])
    def test_reviewed_match_produces_first_useful_time(self):
        review={'relevant':'true','artist_match':'true','duration_match':'true','version_match':'true','reviewer':'Tester'}
        data=report.summarize(self.folder(available='true',reviewer='Tester',evidence='independent fixture evidence',review=review))
        self.assertEqual(data['rows'][0]['quality_rate'],1)
        self.assertEqual(data['rows'][0]['first_useful_p50_ms'],200)
        self.assertIsNone(data['rows'][1]['quality_rate'])  # The warm attempt was not run.
    def test_technical_failure_of_available_recording_remains_in_denominator(self):
        data=report.summarize(self.folder(status='failed',available='true',reviewer='Tester',evidence='independent fixture evidence'))
        self.assertEqual(data['rows'][0]['eligible_queries'],1)
        self.assertEqual(data['rows'][0]['quality_rate'],0)
    def test_excluding_unavailable_query_requires_evidence(self):
        with self.assertRaises(common.Failure):report.summarize(self.folder(available='false'))
    def test_relevance_cannot_ignore_version_and_duration(self):
        p=self.folder(available='true',reviewer='Tester',evidence='fixture evidence',review={'relevant':'true','reviewer':'Tester','artist_match':'true','duration_match':'','version_match':'false'})
        with self.assertRaises(common.Failure):report.summarize(p)
