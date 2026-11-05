import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from highschoolphysics import ingestion_pipeline as workflow
from highschoolphysics import document_worker, document_ingestion, question_bank
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.repository import PhysicsRepository


def good_review(repo, actor, purpose, packet, provider_id=None):
    patches = []
    if purpose == 'answers':
        patches = [{'target': 'parent', 'field': 'answer_md', 'value': 'A'},
                   {'target': 'parent', 'field': 'analysis_md', 'value': '由原卷参考答案得选A。'}]
    return {'verdict': 'pass', 'reason': '逐项核对题号条件选项及对应原卷答案一致。',
            'checks': {key: True for key in ('number','stem','options','formulas','figures','children','answer','tags')},
            'patches': patches, 'resolved_issue_ids': [i['id'] for i in packet.get('issues', [])] if not purpose.startswith('watchdog') else []}


def converter(source_path, original_name, store, school_id, document_id, conversion_id, work_dir, **kwargs):
    answer = 'answers' in original_name
    texts = ['1. 【答案】A\n【解析】牛顿第二定律。', '2. 【答案】A\n【解析】加速度增大。'] if answer else [
        '1. 根据牛顿第二定律，小车所受合力增大，则加速度如何变化？\nA. 增大\nB. 不变\nC. 减小\nD. 无法判断',
        '2. 小车质量不变，合力增大，则加速度如何变化？\nA. 增大\nB. 不变\nC. 减小\nD. 无法判断']
    blocks = [{'id': 'b'+str(n), 'type': 'paragraph', 'page': 1, 'column': 0, 'order': n,
        'bbox': [0,0,1,1], 'markdown': text, 'asset_ids': [], 'source_locator': {'kind':'pdf', 'page':1}, 'issues': []} for n,text in enumerate(texts)]
    return {'document': {'schema_version':1, 'document_id':document_id, 'conversion_id':conversion_id,
        'source_sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),
        'pages':[{'page':1,'width':100,'height':100,'rotation_applied':0}], 'blocks':blocks,'assets':[],'issues':[]},
        'markdown':'\n\n'.join(texts), 'manifest': {'method':'synthetic','issues':[],'formula_count':0},
        'assets':[], 'adapter_name':'test','adapter_version':'1'}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.db = self.root/'app.sqlite3'; self.run = self.root/'run'
        c = connect(self.db); initialize_database(c); seed_demo_data(c)
        self.actor = 'user-teacher-li'
        self.initial_count = c.execute('select count(*) from questions').fetchone()[0]
        self.initial_papers = c.execute('select count(*) from papers').fetchone()[0]
        self.knowledge = c.execute("select id from knowledge_nodes where enabled=1 limit 1").fetchone()[0]
        c.close()
        files = {}
        for role in ('paper','answers'):
            path = self.root/(role+'.pdf'); path.write_bytes(b'%PDF-1.7\nsynthetic fixture '+role.encode())
            files[role] = {'path': str(path), 'sha256': workflow.digest_file(path)}
        self.paper = {'title': '固定流程测试', 'files': files, 'issues': [], 'expected_numbers':['1','2'], 'partial':False}
        workflow.create_run(self.run, self.db, self.actor, self.paper)

    def candidate(self, actor, qid, force=False):
        return {'id':'test-'+qid, 'model_version':'test-llm', 'knowledge_tags':[{'id':self.knowledge,'confidence':0.9}], 'ability_tags':[], 'literacy_tags':[]}

    def tick(self, reviewer=good_review):
        with workflow.locked(self.run):
            p = workflow.Pipeline(self.run, reviewer=reviewer)
            try:
                with patch.object(PhysicsRepository, 'generate_llm_candidates', side_effect=self.candidate):
                    stage = p.tick()
            finally:
                p.close()
        if stage in ('wait_paper','wait_answers'):
            document_worker.run_once(self.db, converter=converter)
        return stage

    def advance(self, goal):
        for _ in range(100):
            stage = self.tick()
            if stage == goal:
                return
        self.fail('did not reach '+goal)

    def test_end_to_end_and_restart_every_step(self):
        self.advance('complete')
        p = workflow.Pipeline(self.run)
        self.addCleanup(p.close)
        self.assertEqual(p.conn.execute('select count(*) from questions').fetchone()[0], self.initial_count+2)
        self.assertEqual(p.conn.execute('select count(*) from papers').fetchone()[0], self.initial_papers+1)
        qids = [qid for i in p.items() for qid in i['published_question_ids']]
        for qid in qids:
            self.assertEqual(p.repo.get_question(qid)['answer']['answer'], 'A')
            self.assertEqual(question_bank.tag_revision(p.conn,qid), 1)
        report = json.loads((self.run/'report.json').read_text())
        self.assertEqual(report['main_questions'], 2)
        self.assertEqual(report['scope'], 'question_bank_only')
        self.assertEqual(self.tick(), 'complete')

    def test_number_mismatch_stops_before_review_or_publication(self):
        self.advance('review_paper')
        p = workflow.Pipeline(self.run, reviewer=lambda *a,**k: self.fail('must not call model'))
        self.addCleanup(p.close)
        p.state['paper']['expected_numbers'] = ['1','2','3']; p.save()
        with self.assertRaisesRegex(workflow.Halt, 'question_number_or_order_mismatch'):
            p.tick()
        self.assertEqual(p.conn.execute('select count(*) from questions').fetchone()[0], self.initial_count)

    def test_adversarial_model_cannot_skip_or_change_structure(self):
        self.advance('review_paper')
        p = workflow.Pipeline(self.run); self.addCleanup(p.close)
        doc = p.items()[0]['document']
        result = good_review(None,None,'paper',{})
        result['patches'] = [{'target':'parent','field':'number','value':'99'}]
        with self.assertRaisesRegex(workflow.Halt, 'patch_outside_stage'):
            workflow.apply_patches(doc,result,'paper')
        result['patches'] = [{'target':'parent','field':'stem_md','value':'![图](asset:injected)'}]
        with self.assertRaisesRegex(workflow.Halt, 'figure_references_changed'):
            workflow.apply_patches(doc,result,'paper')
        result['patches'] = []; result['checks']['formulas'] = 'true'
        with self.assertRaisesRegex(workflow.Halt, 'review_checks_incomplete'):
            workflow.apply_patches(doc,result,'paper')

    def test_stop_arriving_during_model_call_blocks_mutation(self):
        self.advance('review_paper')
        def stop(*args, **kwargs):
            workflow.atomic_json(self.run/'STOP.json', {'reason':'external watchdog'})
            return good_review(*args,**kwargs)
        p = workflow.Pipeline(self.run,reviewer=stop); self.addCleanup(p.close)
        before = p.items()[0]['review_revision']
        with self.assertRaisesRegex(workflow.Halt,'watchdog_stop'):
            p.tick()
        self.assertEqual(p.items()[0]['review_revision'], before)

    def test_watchdog_rejects_content_before_publication(self):
        self.advance('watchdog_content')
        def reject(*args,**kwargs):
            return {'verdict':'blocked','reason':'发现原卷答案和录入答案不一致'}
        p = workflow.Pipeline(self.run,reviewer=reject); self.addCleanup(p.close)
        with self.assertRaisesRegex(workflow.Halt,'watchdog_quality_failure'):
            p.tick()
        self.assertEqual(p.conn.execute('select count(*) from questions').fetchone()[0],self.initial_count)

    def test_replays_exact_save_after_crash_between_db_commit_and_state_save(self):
        self.advance('review_paper')
        p = workflow.Pipeline(self.run,reviewer=good_review)
        original = p.save
        def crash():
            actions = p.state.get('actions', {})
            if any('result' in a for a in actions.values()):
                raise RuntimeError('simulated power loss')
            original()
        p.save = crash
        with self.assertRaisesRegex(RuntimeError,'power loss'):
            p.tick()
        revision = p.items()[0]['review_revision']; p.close()
        self.tick()
        p = workflow.Pipeline(self.run); self.addCleanup(p.close)
        self.assertEqual(p.items()[0]['review_revision'], revision)

    def test_teacher_edit_invalidates_review(self):
        self.advance('watchdog_content')
        p = workflow.Pipeline(self.run); self.addCleanup(p.close)
        item = p.items()[0]; doc = copy.deepcopy(item['document']); doc['stem_md'] += ' changed'
        document_ingestion.save_candidate(p.conn,p.actor,item['id'],{'request_key':'teacher-edit-001','expected_revision':item['review_revision'],'document':doc})
        with self.assertRaisesRegex(workflow.Halt,'reviewed_document_changed'):
            p.tick()

    def test_source_drift_and_stale_worker_are_stopped_independently(self):
        state = json.loads((self.run/'state.json').read_text()); state['updated_at'] = time.time()-700
        workflow.atomic_json(self.run/'state.json',state)
        self.assertEqual(workflow.monitor(self.run)['reason'],'runner_stale')
        (self.run/'STOP.json').unlink()
        Path(self.paper['files']['paper']['path']).write_bytes(b'changed')
        self.assertTrue(workflow.monitor(self.run)['reason'].startswith('source_file_changed'))

    def test_lock_excludes_second_runner(self):
        with workflow.locked(self.run):
            with self.assertRaisesRegex(workflow.Halt,'runner_already_active'):
                with workflow.locked(self.run):
                    self.fail('second runner entered')

    def test_tag_crash_recovery_and_manual_edit_protection(self):
        self.advance('adopt_tags')
        p = workflow.Pipeline(self.run); self.addCleanup(p.close)
        entries = [{k:v for k,v in e.items() if k != 'candidate_id'} for e in p.state['tag_proposals'].values()]
        payload = {'entries':entries}
        p.adopt(payload)
        self.assertTrue(p.adopt(payload)['recovered'])
        qid = entries[0]['question_id']
        e = dict(entries[0],expected_revision=question_bank.tag_revision(p.conn,qid),knowledge=[])
        question_bank.save_tags(p.repo,p.actor,{'entries':[e]})
        with self.assertRaises(workflow.Halt):
            p.adopt(payload)

    def test_watchdog_sample_is_frozen_and_covers_risks_and_ends(self):
        items = [{'id':str(i),'document':{'issues':[], 'asset_refs':[], 'children':[]}} for i in range(20)]
        items[8]['document']['asset_refs'] = ['figure']
        sample = workflow.sample_ids(items,'seed')
        self.assertTrue({'0','8','19'} <= set(sample))
        self.assertEqual(sample,workflow.sample_ids(items,'seed'))


if __name__ == '__main__':
    unittest.main()
