import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from highschoolphysics import diagnosis, learning, student_learning
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.errors import InvalidRequest, PermissionDenied, StateConflict
from highschoolphysics.server import render_admin_app


def card():
    return {'title':'受力检查','steps':[dict(stage=stage,prompt='合力为零意味着什么？',options=['加速度为零','每个力为零'],correct=0,explanation='合力决定加速度，各个力可以互相平衡。',hints=['先写牛顿第二定律。','合力为零意味着加速度为零。']) for stage in diagnosis.STAGES]}

class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.c=connect(':memory:');initialize_database(self.c);seed_demo_data(self.c)
        self.repo=PhysicsRepository(self.c)
        self.repo.resolve_review_item('user-teacher-li','resp-1001-q2','C','核对')
        self.repo.grade_assessment('user-teacher-li','assess-week-1',publish=True);learning.migrate(self.c)
        self.user=dict(self.c.execute("select * from users where id='stu-1001'").fetchone())
        self.other=dict(self.c.execute("select * from users where id='stu-1002'").fetchone())
        self.admin=dict(self.c.execute("select * from users where role='admin' limit 1").fetchone())
        self.w=student_learning.wrongs(self.repo,self.user)[0]
        self.data=diagnosis.question_input(self.repo,self.w['question_id'],snapshot=self.w['snapshot'])
        diagnosis.store_card(self.repo,self.w['question_id'],self.data,card(),'test-reviewed');self.c.commit()
        self.p={'wrong_id':self.w['id'],'question_id':self.w['question_id']}
    def tearDown(self):self.c.close()
    def api(self,action,**p):return diagnosis.api(self.repo,self.user,'diagnosis-'+action,{**self.p,'protocol_version':1,**p})
    def start(self,mode='quick',report='unsure'):return self.api('start',mode=mode,self_report=report)['state']
    def event(self,s,event,key=None,**p):return self.api('event',session_id=s['session_id'],cursor=s['cursor'],event=event,request_key=key or event+str(s['cursor']),**p)['state']

    def test_quick_deep_no_original_changes_or_premature_answer(self):
        before=[tuple(r) for r in self.c.execute('select * from student_responses')]
        s=self.start();self.assertEqual(2,s['total']);self.assertNotIn('correct',json.dumps(s));self.assertNotIn('explanation',s['step'])
        s=self.event(s,'answer',answer=1);self.assertFalse(s['feedback']['passed']);self.assertTrue(s['assisted'])
        s=self.event(s,'next');s=self.event(s,'answer',answer=0);s=self.event(s,'next');self.assertEqual('completed',s['status'])
        s=self.event(s,'deepen');self.assertEqual(4,s['total']);self.assertEqual(2,s['cursor']);self.assertEqual('deep',s['mode'])
        self.assertEqual(before,[tuple(r) for r in self.c.execute('select * from student_responses')]);self.assertEqual(0,self.c.execute('select count(*) from redo_attempts').fetchone()[0])
        self.assertEqual(s,self.api('state')['state'])

    def test_hint_order_idempotence_and_stale_cursor(self):
        s=self.start('deep');s=self.event(s,'hint','hint-1');self.assertEqual(1,s['step']['hint_level'])
        same=self.event(s,'hint','hint-1');self.assertEqual(1,same['step']['hint_level'])
        s=self.event(s,'hint','hint-2');self.assertEqual(2,s['step']['hint_level'])
        with self.assertRaises(InvalidRequest):self.event(s,'hint','hint-3')
        with self.assertRaises(StateConflict):self.api('event',session_id=s['session_id'],cursor=99,event='answer',answer=0,request_key='stale')
        s=self.event(s,'answer',answer=0);self.assertTrue(s['feedback']['assisted'])
        with self.assertRaises(StateConflict):self.event(s,'answer','another-answer',answer=0)

    def test_ownership_role_and_config_privileges(self):
        with self.assertRaises(PermissionDenied):diagnosis.api(self.repo,self.other,'diagnosis-state',self.p)
        with self.assertRaises(PermissionDenied):diagnosis.api(self.repo,self.admin,'diagnosis-state',self.p)
        with self.assertRaises(PermissionDenied):diagnosis.save_config(self.repo,self.user,dict(baseurl='https://api.example.com/v1',model='x',apikey='secret'))
        with self.assertRaises(PermissionDenied):self.api('state',question_id='q1')

    def test_ending_can_deepen_and_confirmation_is_saved(self):
        s=self.start();s=self.event(s,'finish',confirmation='different');self.assertEqual('ended',s['status'])
        s=self.event(s,'deepen');self.assertEqual('active',s['status']);self.assertEqual(4,s['total'])
        self.assertEqual(1,self.c.execute("select count(*) from diagnostic_events where result_json like '%different%'").fetchone()[0])

    def test_started_self_report_does_not_mark_assisted_but_checks_do(self):
        s=self.start();self.assertFalse(diagnosis.assisted_today(self.c,self.user['id'],self.w['question_id']))
        self.event(s,'answer',answer=0)
        self.assertTrue(diagnosis.assisted_today(self.c,self.user['id'],self.w['question_id']))
        result=learning.submit(self.repo,self.user['id'],dict(wrong_id=self.w['id'],unified='1',answer='A',request_key='after-diagnosis'))
        self.assertEqual('learn',result['purpose'])

    def test_stale_fingerprint_never_serves_old_card(self):
        with patch.object(diagnosis,'question_input',return_value={'stem':'new revision'}):
            result=self.api('state');self.assertFalse(result['available'])
        self.assertEqual('waiting_config',self.c.execute('select status from diagnostic_jobs').fetchone()[0])

    def test_admin_three_fields_preserve_secret_model_change_and_real_test(self):
        payload=dict(baseurl='https://api.example.com/v1',model='model-a',apikey='secret-test-value')
        diagnosis.save_config(self.repo,self.admin,payload)
        cfg=diagnosis.provider(self.c,self.admin['school_id']);self.assertNotEqual(payload['apikey'],cfg['secret_ciphertext'])
        payload.update(model='model-b',apikey='');diagnosis.save_config(self.repo,self.admin,payload)
        cfg=diagnosis.provider(self.c,self.admin['school_id']);self.assertEqual('secret-test-value',self.repo._provider_secret_store().decrypt(cfg['secret_ciphertext']))
        self.assertEqual(1,self.c.execute("select count(*) from provider_configs where provider_kind='diagnosis' and enabled=1").fetchone()[0])
        html=render_admin_app(self.admin,self.repo.admin_dashboard(self.admin['id']));self.assertIn('name="baseurl"',html);self.assertNotIn('secret-test-value',html)
        from tests.test_diagnosis_adaptive import sample
        response=unittest.mock.MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'choices':[{'message':{'content':json.dumps(dict(card(),adaptive=sample()))}}],'usage':{'prompt_tokens':10,'completion_tokens':20}}).encode()
        with patch.object(diagnosis.request,'urlopen',return_value=response) as call:
            diagnosis.test_config(self.repo,self.admin)
            req=call.call_args[0][0];self.assertEqual('https://api.example.com/v1/chat/completions',req.full_url)
            sent=json.loads(req.data);self.assertNotIn('student',sent['messages'][1]['content']);self.assertEqual('model-b',sent['model'])
        self.assertEqual('success',self.c.execute('select outcome from provider_usage_events').fetchone()[0])

    def test_invalid_model_output_and_failure_hide_provider_error(self):
        bad=copy.deepcopy(card());bad['steps'][0]['correct']=True
        with self.assertRaises(InvalidRequest):diagnosis.validate_card(bad)
        diagnosis.save_config(self.repo,self.admin,dict(baseurl='https://api.example.com/v1',model='x',apikey='secret-test'))
        with patch.object(diagnosis.request,'urlopen',side_effect=RuntimeError('Authorization: secret-test')):
            with self.assertRaisesRegex(Exception,'诊断调用或内容校验失败'):diagnosis.test_config(self.repo,self.admin)
        usage=self.c.execute('select detail_json from provider_usage_events').fetchone()[0];self.assertNotIn('secret-test',usage)

    def test_admin_model_unavailable_is_actionable_domain_error(self):
        from io import BytesIO
        from urllib.error import HTTPError
        diagnosis.save_config(self.repo,self.admin,dict(baseurl='https://api.example.com/v1',model='x',apikey='secret-test'))
        failure=HTTPError('https://api.example.com/v1/chat/completions',503,'secret-test',{},BytesIO(b'secret-test'))
        with patch.object(diagnosis.request,'urlopen',side_effect=failure):
            with self.assertRaisesRegex(InvalidRequest,'HTTP 503.*当前模型不可用') as caught:
                diagnosis.test_config(self.repo,self.admin)
        self.assertNotIn('secret-test',str(caught.exception))
        usage=self.c.execute('select error_category,detail_json from provider_usage_events').fetchone()
        self.assertEqual('diagnosis_http_503',usage[0]);self.assertNotIn('secret-test',usage[1])

    def test_backfill_all_reviewed_cards_and_fingerprint_validation(self):
        manifest=json.loads(Path('highschoolphysics/diagnostic_data/reviewed-20261007.json').read_text())
        self.assertEqual(23,len(manifest));self.assertEqual(92,sum(len(diagnosis.validate_card(x['card'])['steps']) for x in manifest))
        item={'question_id':self.w['question_id'],'fingerprint':'wrong-version','card':card()}
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'cards.json';p.write_text(json.dumps([item]));result=diagnosis.backfill(self.repo,p)
        self.assertEqual(0,result['covered']);self.assertEqual(1,result['skipped'])

    def test_worker_waits_for_config_and_claims_retry_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            path=str(Path(root)/'db.sqlite');c=connect(path);initialize_database(c);seed_demo_data(c);r=PhysicsRepository(c)
            diagnosis.reconcile(r);self.assertIsNone(diagnosis.run_once(path))
            user=dict(c.execute("select * from users where role='admin' limit 1").fetchone());diagnosis.save_config(r,user,dict(baseurl='https://api.example.com/v1',model='x',apikey='secret-test'))
            with patch.object(diagnosis,'generate',return_value=card()):self.assertIsNotNone(diagnosis.run_once(path))
            self.assertEqual(1,c.execute('select count(*) from diagnostic_cards').fetchone()[0])
            with patch.object(diagnosis,'generate',side_effect=RuntimeError('fail')):
                for _ in range(12):diagnosis.run_once(path)
            self.assertFalse(c.execute('select 1 from diagnostic_jobs where attempts>3').fetchone());c.close()
