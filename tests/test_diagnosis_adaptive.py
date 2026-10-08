import copy
import json
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from highschoolphysics import diagnosis,diagnosis_adaptive as adaptive,student_learning
from highschoolphysics.errors import InvalidRequest,StateConflict,PermissionDenied


def sample():
    def probe(prompt,options):return dict(prompt=prompt,options=options,correct=0,explanation='合外力决定加速度，匀速直线运动加速度为零。')
    foundation=probe('牛顿第二定律里的力是什么？',['所有外力的合力','只取最大一个力'])
    practice=probe('2kg物体受合力4N，加速度多少？',['2m/s²','8m/s²'])
    verify=probe('3kg物体受合力6N，加速度多少？',['2m/s²','18m/s²'])
    actions=[]
    for i,(kind,name,prompt,options) in enumerate([
        ('condition','条件转译','匀速直线运动时加速度如何？',['为零','沿速度方向']),
        ('model','研究对象与模型','确定物体加速度，应该分析哪个力？',['合外力','只看重力']),
        ('formula','公式与列式','质量m、合力F，怎样求加速度？',['F/m','Fm'])]):
        actions.append(dict(probe(prompt,options),kind=kind,name=name,cue='先区分每个外力与它们的合力。',prerequisites=list(range(i)),foundation=foundation,practice=practice,verify=verify))
    return dict(version='adaptive-v1',title='牛顿第二定律',actions=actions,foundation=foundation,practice=practice,verify=verify)


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        from tests.test_diagnosis import DiagnosisTests
        DiagnosisTests.setUp(self)
        self.content=sample();self.card_id=self.c.execute('select id from diagnostic_cards').fetchone()[0]
        self.c.execute('insert into adaptive_diagnostic_cards values(?,?,?,?)',(self.card_id,'adaptive-v1',diagnosis.dumps(self.content),diagnosis.now()));self.c.commit()
    def tearDown(self):self.c.close()
    def api(self,action,**p):return diagnosis.api(self.repo,self.user,'diagnosis-'+action,{**self.p,'protocol_version':3,**p})
    def start(self,mode='deep',report='unsure'):return self.api('start',mode=mode,self_report=report)['state']
    def event(self,s,event,**p):
        key=p.pop('request_key',event+str(s['cursor']))
        return self.api('event',session_id=s['session_id'],event=event,cursor=s['cursor'],request_key=key,**p)['state']
    def block_model(self):
        s=self.start();s=self.event(s,'probe',answer=0);s=self.event(s,'probe',answer=1)
        return self.event(s,'locate',reason='not_recalled')

    def test_adjacent_causes_have_different_branches_and_evidence(self):
        s=self.block_model();self.assertEqual('foundation',s['step']['node']);self.assertNotIn('correct',json.dumps(s))
        s=self.event(s,'probe',answer=1);self.assertEqual('completed',s['status']);self.assertEqual('knowledge_gap',s['summary']['category']);self.assertFalse(s['exercise']['answered'])
        self.c.execute('delete from diagnostic_events');self.c.execute('delete from diagnostic_sessions');self.c.commit()
        s=self.block_model();s=self.event(s,'probe',answer=0);self.assertEqual('cue',s['step']['node']);self.assertTrue(s['assisted'])
        s=self.event(s,'probe',answer=0);self.assertEqual('knowledge_retrieval',s['summary']['category']);self.assertTrue(s['exercise']['answered']);self.assertTrue(any('最小线索' in x for x in s['summary']['evidence']))

    def test_quick_keeps_candidates_deep_checks_prerequisites_and_preserves_records(self):
        s=self.start('quick','model');s=self.event(s,'probe',answer=1);s=self.event(s,'locate',reason='model_unrecognized');self.assertEqual('insufficient',s['summary']['category']);self.assertTrue(s['summary']['alternatives'])
        original=[tuple(r) for r in self.c.execute('select * from diagnostic_events')]
        s=self.event(s,'deepen');self.assertEqual('a:0',s['step']['node']);s=self.event(s,'probe',answer=1)
        self.assertEqual('completed',s['status']);self.assertIn('条件转译',s['summary']['location']);self.assertEqual(original,[tuple(r) for r in self.c.execute('select * from diagnostic_events order by created_at,id')][:len(original)])

    def test_unknown_does_not_force_cause_exit_all_pass_and_time(self):
        s=self.start();s=self.event(s,'probe',answer=-1);s=self.event(s,'locate',reason='unsure');s=self.event(s,'probe',answer=-1);self.assertEqual('insufficient',s['summary']['category'])
        self.c.execute('delete from diagnostic_events');self.c.execute('delete from diagnostic_sessions');self.c.commit()
        s=self.start();s=self.event(s,'finish');self.assertEqual('ended',s['status']);self.assertFalse(s['assisted']);self.assertTrue(any('退出' in x for x in s['summary']['cautions']))
        self.c.execute('delete from diagnostic_events');self.c.execute('delete from diagnostic_sessions');self.c.commit()
        s=self.start(report='time');self.assertIn('reflection',s);s=self.event(s,'locate',reason='ran_out');self.assertEqual('answer_context',s['summary']['category']);self.assertFalse(s['assisted'])

    def test_delayed_verification_cannot_be_done_early_or_change_mastery(self):
        before=[tuple(r) for r in self.c.execute('select * from student_responses')]
        s=self.block_model();s=self.event(s,'probe',answer=1);s=self.event(s,'practice',answer=0)
        self.assertFalse(s['verification']['ready']);self.assertNotIn('options',s['verification'])
        with self.assertRaises(StateConflict):self.event(s,'verify',answer=0)
        row=self.c.execute("select id,result_json from diagnostic_events where action='practice'").fetchone();r=json.loads(row['result_json']);r['at']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat();self.c.execute('update diagnostic_events set result_json=? where id=?',(diagnosis.dumps(r),row['id']));self.c.commit()
        s=self.api('state')['state'];self.assertTrue(s['verification']['ready']);self.assertNotEqual(s['exercise']['prompt'],s['verification']['prompt']);s=self.event(s,'verify',answer=0);self.assertTrue(s['verification']['passed'])
        self.assertEqual(before,[tuple(r) for r in self.c.execute('select * from student_responses')]);self.assertEqual(0,self.c.execute('select count(*) from redo_attempts').fetchone()[0])

    def test_idempotency_stale_cursor_permissions_and_tampering(self):
        s=self.start();old=s;s=self.event(s,'probe',answer=0,request_key='stable');self.assertEqual(s,self.event(old,'probe',answer=0,request_key='stable'))
        with self.assertRaises(StateConflict):self.event(old,'probe',answer=1,request_key='stable')
        with self.assertRaises(StateConflict):self.event(old,'probe',answer=0,request_key='stale')
        with self.assertRaises(InvalidRequest):self.event(s,'probe',answer=True)
        with self.assertRaises(PermissionDenied):diagnosis.api(self.repo,self.other,'diagnosis-state',self.p)
        with self.assertRaises(InvalidRequest):self.event(s,'practice',answer=0)

    def test_correction_is_separate_and_session_content_pinned(self):
        s=self.block_model();s=self.event(s,'probe',answer=1);before=s['summary']['category'];s=self.event(s,'finish',confirmation='different');s=self.event(s,'revise',reason='arithmetic')
        self.assertEqual('insufficient',s['summary']['category']);self.assertNotEqual('calculation',s['summary']['category']);self.assertTrue(any('更正' in x for x in s['summary']['evidence']))
        self.c.execute("update adaptive_diagnostic_cards set content_json='{}'");self.c.commit();self.assertEqual(s,self.api('state')['state'])

    def test_no_old_performed_session_rewritten_and_empty_can_upgrade_on_selection(self):
        old=diagnosis.api(self.repo,self.user,'diagnosis-start',{**self.p,'protocol_version':2,'mode':'deep','self_report':'unsure'})['state']
        self.assertEqual(2,self.api('state')['state']['protocol_version'])
        s=self.api('event',session_id=old['session_id'],cursor=0,event='select-mode',mode='quick',self_report='model',request_key='upgrade')['state'];self.assertEqual(3,s['protocol_version'])
        self.c.execute('delete from diagnostic_events');self.c.execute('delete from diagnostic_sessions');self.c.commit()
        old=diagnosis.api(self.repo,self.user,'diagnosis-start',{**self.p,'protocol_version':2,'mode':'deep','self_report':'unsure'})['state']
        diagnosis.api(self.repo,self.user,'diagnosis-event',{**self.p,'session_id':old['session_id'],'cursor':0,'event':'answer','answer':0,'request_key':'old-answer'})
        self.assertEqual(2,self.api('state')['state']['protocol_version']);self.assertEqual(2,self.c.execute('select protocol_version from diagnostic_sessions').fetchone()[0])

    def test_reviewed_content_and_dependency_validation(self):
        items=json.loads((__import__('pathlib').Path(adaptive.__file__).with_name('diagnostic_data')/'adaptive-reviewed-20261008.json').read_text());self.assertEqual(23,len(items))
        for e in items:
            adaptive.validate(e['content'])
            for a in e['content']['actions']:
                self.assertNotEqual(a['practice']['prompt'],a['verify']['prompt'])
        self.assertEqual('knowledge',items[0]['content']['actions'][0]['kind']);self.assertEqual('knowledge',items[0]['content']['actions'][3]['kind']);self.assertEqual(3,len(items[2]['content']['actions']))
        broken=copy.deepcopy(sample());broken['actions'][0]['prerequisites']=[0]
        with self.assertRaises(InvalidRequest):adaptive.validate(broken)
        broken=copy.deepcopy(sample());broken['foundation']['correct']=True
        with self.assertRaises(InvalidRequest):adaptive.validate(broken)

    def test_explanations_before_deepening_do_not_prove_original_retrieval(self):
        s=self.start('quick','model');s=self.event(s,'probe',answer=1);s=self.event(s,'locate',reason='not_recalled');self.assertTrue(s['assisted'])
        s=self.event(s,'deepen');s=self.event(s,'probe',answer=0);s=self.event(s,'probe',answer=0);s=self.event(s,'probe',answer=0)
        self.assertEqual('insufficient',s['summary']['category']);self.assertTrue(any('讲解之后' in x for x in s['summary']['cautions']))

    def test_teacher_correction_takes_priority_over_bundled_supplement(self):
        from tests.test_diagnosis import card
        changed=dict(card(),title='教师修订的检查')
        with patch('highschoolphysics.learning_graph.effective',return_value=(changed,[],'reviewed-release')):
            self.assertEqual(2,self.api('state')['protocol_version'])
        changed['adaptive']=sample()
        with patch('highschoolphysics.learning_graph.effective',return_value=(changed,[],'reviewed-release')):
            self.assertEqual(3,self.api('state')['protocol_version'])
