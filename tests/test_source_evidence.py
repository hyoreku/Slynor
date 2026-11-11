"""Prevent incomplete or mismatched evidence from qualifying source candidates."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.source_experiment import common, compare, report, run


class EvidenceTests(unittest.TestCase):
    def test_documented_isolated_cli_entry_points_start_without_import_errors(self):
        for script in ('run.py','report.py','compare.py'):
            with self.subTest(script=script):
                result=subprocess.run([sys.executable,'-I',str(common.ROOT/'tools/source_experiment'/script),'--help'],
                    cwd=common.ROOT,capture_output=True,timeout=5,check=False)
                self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
                self.assertIn(b'usage:',result.stdout)

    def folder(self, network='home_owner', profile='smoke'):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        queries = run.load_queries()
        if profile == 'smoke': queries = [queries[i] for i in (0, 24, 57)]
        manifest = {'run_id':network + '-fixture','network_label':network,'profile':profile,
            'dataset_sha256':common.DATASET_SHA256,'selected_queries':len(queries),
            'query_ids':[q['query_id'] for q in queries],'protocol_version':'search-v0.2',
            'preflight':[{'adapter':'archive.http','available':True,'packages':{}}],
            'search_attempts_planned':len(queries)*2,'actual_search_attempts':1,'remaining_attempts_not_run':len(queries)*2-1,
            'tool_hashes':{'worker.py':'a'*64},'python_controller':'3.13.0','platform':'test fixture','psutil':'7.2.2'}
        (folder/'manifest.json').write_text(json.dumps(manifest))
        a = {'attempt_id':'Q001-archive.http-cold','query_id':'Q001','adapter':'archive.http','phase':'cold',
             'status':'ok','technical_success':True,'cards':1,'total_ms':300,'first_card_end_to_end_ms':200,'error_category':None}
        (folder/'search.jsonl').write_text(json.dumps(a)+'\n')
        c = {'attempt_id':a['attempt_id'],'rank':1,'query_id':'Q001','adapter':'archive.http','phase':'cold','first_seen_end_to_end_ms':200}
        (folder/'cards.jsonl').write_text(json.dumps(c)+'\n')
        review = {'attempt_id':a['attempt_id'],'rank':1,'relevant':'true','artist_match':'true','duration_match':'true',
                  'version_match':'true','reviewer':'Synthetic reviewer','full_or_preview':'unknown'}
        run.write_csv(folder/'reviews.csv',[review],list(review))
        available = [{'adapter':'archive.http','query_id':q['query_id'],'available':'true' if q['query_id']=='Q001' else 'false',
                      'reviewer':'Synthetic reviewer','reference_evidence':'Synthetic fixture only',
                      'negative_control':q['group']=='negative_control'} for q in queries]
        run.write_csv(folder/'availability.csv',available,list(available[0]))
        return folder

    def edit_csv(self, folder, name, edit):
        path=folder/name
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);rows=list(reader);fields=reader.fieldnames
        edit(rows)
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fields);writer.writeheader();writer.writerows(rows)

    def assert_rejected(self, folder, code):
        with self.assertRaises(common.Failure) as error: report.summarize(folder)
        self.assertEqual(error.exception.code,code)

    def test_removing_unreviewed_queries_cannot_create_a_full_set_percentage(self):
        p=self.folder(profile='full')
        def unknown(rows):
            for row in rows[1:]: row.update(available='',reviewer='',reference_evidence='')
        self.edit_csv(p,'availability.csv',unknown)
        self.edit_csv(p,'availability.csv',lambda rows:rows.__setitem__(slice(None),rows[:1]))
        self.assert_rejected(p,'availability_coverage_mismatch')

    def test_positive_query_cannot_be_relabelled_as_negative_control(self):
        p=self.folder()
        self.edit_csv(p,'availability.csv',lambda rows:rows[1].update(negative_control='true'))
        self.assert_rejected(p,'negative_control_mismatch')

    def test_duplicate_success_cannot_inflate_attempt_count(self):
        p=self.folder();path=p/'search.jsonl';path.write_text(path.read_text()*2)
        self.assert_rejected(p,'duplicate_search_attempt')

    def test_review_from_a_different_run_or_card_is_rejected(self):
        p=self.folder()
        self.edit_csv(p,'reviews.csv',lambda rows:rows[0].update(attempt_id='Q025-archive.http-cold'))
        self.assert_rejected(p,'orphan_manual_review')

    def test_manifest_and_journal_count_must_agree(self):
        p=self.folder();path=p/'manifest.json';m=json.loads(path.read_text());m['actual_search_attempts']=100
        path.write_text(json.dumps(m));self.assert_rejected(p,'actual_attempts_mismatch')

    def test_reported_cards_must_exist(self):
        p=self.folder();(p/'cards.jsonl').write_text('')
        self.assert_rejected(p,'search_card_count_mismatch')

    def test_invalid_manual_boolean_is_not_silently_ignored(self):
        p=self.folder();self.edit_csv(p,'reviews.csv',lambda rows:rows[0].update(relevant='yes'))
        self.assert_rejected(p,'invalid_manual_boolean')

    def test_nan_metric_is_rejected(self):
        p=self.folder();path=p/'search.jsonl';a=json.loads(path.read_text());a['total_ms']=float('nan')
        path.write_text(json.dumps(a)+'\n');self.assert_rejected(p,'search_metric_invalid')

    def test_matching_card_after_unreviewed_card_has_unknown_first_useful_time(self):
        p=self.folder();path=p/'search.jsonl';a=json.loads(path.read_text());a['cards']=2;path.write_text(json.dumps(a)+'\n')
        path=p/'cards.jsonl';first=json.loads(path.read_text());second={**first,'rank':2,'first_seen_end_to_end_ms':250}
        path.write_text(json.dumps(first)+'\n'+json.dumps(second)+'\n')
        self.edit_csv(p,'reviews.csv',lambda rows:rows[0].update(rank='2'))
        result=report.summarize(p)['rows'][0]
        self.assertEqual(result['quality_rate'],1)
        self.assertIsNone(result['first_useful_p50_ms'])

    def test_smoke_percentage_does_not_qualify_the_dataset_or_downloads(self):
        data=report.summarize(self.folder())
        self.assertEqual(data['rows'][0]['quality_rate'],1)
        self.assertIn('not assessed',data['qualification'])
        self.assertFalse(data['downloads_run']);self.assertIsNone(data['rows'][0]['download_rate'])

    def pair(self):
        return report.summarize(self.folder()),report.summarize(self.folder('vps_ru'))

    def test_matching_versions_can_be_compared_without_selecting_an_audio_path(self):
        home,vps=self.pair();vps['environment']['platform']='another OS fixture'
        data=compare.compare(home,vps)
        self.assertTrue(data['same_protocol_and_versions'])
        self.assertEqual(data['rows'][0]['quality_delta_percentage_points'],0)
        self.assertFalse(data['downloads_compared']);self.assertIn('not made',data['selection'])

    def test_missing_manual_quality_is_not_treated_as_zero_in_comparison(self):
        home,vps=self.pair();vps['rows'][0]['quality_rate']=None
        self.assertIsNone(compare.compare(home,vps)['rows'][0]['quality_delta_percentage_points'])

    def test_changed_code_blocks_numerical_quality_delta(self):
        home,vps=self.pair();vps['tool_hashes']['worker.py']='b'*64
        data=compare.compare(home,vps)
        self.assertFalse(data['same_protocol_and_versions'])
        self.assertIsNone(data['rows'][0]['quality_delta_percentage_points'])

    def test_changed_provider_version_blocks_comparison(self):
        home,vps=self.pair()
        home['preflight'][0]['packages']={'fixture-package':{'version':'1','metadata_sha256':'a'*64}}
        vps['preflight'][0]['packages']={'fixture-package':{'version':'2','metadata_sha256':'b'*64}}
        data=compare.compare(home,vps)
        self.assertIn('package_identity_mismatch',data['rows'][0]['blocking_reasons'])

    def test_sandbox_cannot_be_presented_as_a_home_or_vps_run(self):
        home,vps=self.pair();vps['network_label']='sandbox'
        with self.assertRaises(common.Failure): compare.compare(home,vps)

    def test_changed_query_set_blocks_comparison(self):
        home,vps=self.pair();vps['query_ids']=['Q001']
        self.assertFalse(compare.compare(home,vps)['same_protocol_and_versions'])

    def test_run_id_cannot_escape_the_research_folder(self):
        for name in ['../private','C:\\private','https://example.invalid','x/y']:
            with self.assertRaises(common.Failure): compare.run_folder(name,'home_owner')

    def test_comparison_output_cannot_follow_symlink_to_user_data(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root=Path(tmp);runs=common.safe_output_root(root)
            try:(runs.parent/'comparisons').symlink_to(other,target_is_directory=True)
            except OSError:self.skipTest('Symlinks unavailable')
            with self.assertRaises(common.Failure):compare.save({},root)
