import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from highschoolphysics import diagnosis,diagnosis_reasoning as reasoning,learning,student_learning
from highschoolphysics.errors import InvalidRequest,PermissionDenied,StateConflict

class ReasoningDiagnosisTests(unittest.TestCase):
    def setUp(self):
        from tests.test_diagnosis import DiagnosisTests
        DiagnosisTests.setUp(self)
    def tearDown(self):self.c.close()
    def api(self,action,**p):return diagnosis.api(self.repo,self.user,'diagnosis-'+action,{**self.p,'protocol_version':2,**p})
    def start(self,report='unsure',mode='quick'):return self.api('start',self_report=report,mode=mode)['state']
    def event(self,s,event,request_key=None,**p):return self.api('event',session_id=s['session_id'],cursor=s['cursor'],event=event,request_key=request_key or event+str(s['cursor'])+str(self.c.execute('select count(*) from diagnostic_events').fetchone()[0]),**p)['state']
    def test_selection_only_and_reasoning_protocol_default(self):
        r=diagnosis.api(self.repo,self.user,'diagnosis-state',self.p);self.assertEqual(2,r['protocol_version'])
        with self.assertRaises(InvalidRequest):self.api('start',mode='quick',note='手写说明')
        text=(Path(diagnosis.__file__).parent/'assets/diagnosis.js').read_text()
        self.assertNotIn('<textarea',text);self.assertNotIn('name="note"',text);self.assertIn('说不清 / 不知道怎么选',text)
        s=self.start();self.assertEqual(2,s['protocol_version']);self.assertNotIn('note',s)
    def test_optional_entry_is_closed_and_does_not_eagerly_load(self):
        html=diagnosis.panel(self.w['id'],[self.w])
        self.assertTrue(html.startswith('<details '));self.assertNotIn(' open',html)
        self.assertIn('选择进行错题诊断（可选）',html)
        js=(Path(diagnosis.__file__).parent/'assets/diagnosis.js').read_text()
        self.assertIn("panel.addEventListener('toggle',()=>{if(panel.open)load();});",js)
        self.assertIn('if(current===generation)choose(r);',js)
        self.assertIn('粗略诊断',js);self.assertIn('精细诊断',js)
    def test_existing_empty_deep_record_can_choose_quick_then_deep(self):
        s=self.start(mode='deep');self.assertEqual(4,s['total'])
        s=self.event(s,'select-mode',mode='quick',self_report='knowledge_retrieval')
        self.assertEqual('quick',s['mode']);self.assertEqual(2,s['total']);self.assertEqual('模型与规律',s['step']['stage'])
        s=self.event(s,'select-mode',mode='deep',self_report='condition')
        self.assertEqual('deep',s['mode']);self.assertEqual(4,s['total']);self.assertEqual('条件理解',s['step']['stage'])
        self.assertFalse(s['assisted']);self.assertEqual(0,s['checked_count'])
    def test_mode_selection_preserves_answers_and_pending_reflection(self):
        s=self.start('knowledge_retrieval');s=self.event(s,'answer',answer=0)
        with self.assertRaises(StateConflict):self.event(s,'select-mode',mode='deep',self_report='condition')
        s=self.event(s,'locate',reason='not_recalled')
        before=[tuple(r) for r in self.c.execute("select * from diagnostic_events where action in ('answer','locate')")]
        prior=s
        s=self.event(s,'select-mode','choose-deep',mode='deep',self_report='condition')
        self.assertEqual(4,s['total']);self.assertEqual(1,s['checked_count']);self.assertEqual('knowledge_retrieval',s['self_report'])
        self.assertEqual(1,s['prior_checks'])
        self.assertEqual(before,[tuple(r) for r in self.c.execute("select * from diagnostic_events where action in ('answer','locate')")])
        self.assertEqual(s,self.event(prior,'select-mode','choose-deep',mode='deep',self_report='condition'))
    def test_knowledge_retrieval_distinct_from_forgetting(self):
        s=self.start('knowledge_retrieval');self.assertEqual('模型与规律',s['step']['stage'])
        s=self.event(s,'answer',answer=0);self.assertIn('reflection',s);self.assertEqual('',s['feedback']['explanation'])
        s=self.event(s,'locate',reason='not_recalled');s=self.event(s,'finish')
        self.assertEqual('knowledge_retrieval',s['summary']['category']);self.assertIn('回顾',s['summary']['strength']);self.assertTrue(s['summary']['next_action'])
    def test_forgotten_with_hint_retry_and_minimal_effective_help(self):
        s=self.start('knowledge_gap');s=self.event(s,'answer',answer=1)
        self.assertFalse(s['assisted']);self.assertEqual('',s['feedback']['explanation']);self.assertEqual('',s['findings'][0]['explanation'])
        with self.assertRaises(InvalidRequest):self.event(s,'hint')
        s=self.event(s,'locate',reason='forgot');s=self.event(s,'hint');self.assertTrue(s['step']['can_answer'])
        s=self.event(s,'answer',answer=0);self.assertEqual('提示后推进',s['summary']['trace'][0]['label'])
        s=self.event(s,'finish');self.assertEqual('knowledge_forgotten',s['summary']['category']);self.assertIn('相互支持',s['summary']['strength'])
        self.assertTrue(any('提示 1' in x for x in s['summary']['evidence']))
    def test_contradiction_is_visible_and_is_not_mastery(self):
        s=self.start('knowledge_gap');s=self.event(s,'answer',answer=0);s=self.event(s,'locate',reason='forgot');s=self.event(s,'finish')
        self.assertTrue(any('核对' in x for x in s['summary']['cautions']));self.assertIn('需核对',s['summary']['strength'])
        self.assertEqual(0,self.c.execute('select count(*) from redo_attempts').fetchone()[0])
    def test_final_explanation_counts_as_assistance(self):
        s=self.start('knowledge_gap');s=self.event(s,'answer',answer=1)
        s=self.event(s,'locate',reason='forgot');self.assertFalse(s['assisted'])
        s=self.event(s,'finish');self.assertTrue(s['assisted'])
        self.assertTrue(s['findings'][0]['explanation'])
    def test_reflection_before_feedback_does_not_count_as_assistance(self):
        s=self.start('knowledge_retrieval');s=self.event(s,'answer',answer=0)
        self.assertFalse(s['assisted']);self.assertEqual('',s['feedback']['explanation'])
        s=self.event(s,'locate',reason='not_recalled');self.assertTrue(s['assisted'])
        self.assertTrue(s['feedback']['explanation'])
    def test_migration_only_upgrades_untouched_sessions(self):
        s=diagnosis.api(self.repo,self.user,'diagnosis-start',{**self.p,'protocol_version':1,'mode':'quick','self_report':'unsure'})['state']
        before=tuple(self.c.execute('select effective_card_json,graph_mapping_json from diagnostic_sessions').fetchone())
        self.c.execute("delete from app_schema_migrations where feature='reasoning_diagnosis'");self.c.commit();reasoning.migrate(self.c)
        self.assertEqual(2,self.c.execute('select protocol_version from diagnostic_sessions').fetchone()[0])
        self.assertEqual(before,tuple(self.c.execute('select effective_card_json,graph_mapping_json from diagnostic_sessions').fetchone()))
        self.c.execute('update diagnostic_sessions set protocol_version=1');self.c.commit()
        diagnosis.api(self.repo,self.user,'diagnosis-event',{**self.p,'session_id':s['session_id'],'event':'answer','answer':1,'cursor':0,'request_key':'old-attempt'})
        self.c.execute("delete from app_schema_migrations where feature='reasoning_diagnosis'");self.c.commit();reasoning.migrate(self.c)
        self.assertEqual(1,self.c.execute('select protocol_version from diagnostic_sessions').fetchone()[0])
    def test_condition_omission_and_decoding_are_distinct(self):
        for reason,category in [('missed','condition_omission'),('untranslated','condition_decoding'),('term_unclear','knowledge_unclear')]:
            # Distinct test sessions; deleting only the in-memory fixture is safe.
            self.c.execute('delete from diagnostic_events');self.c.execute('delete from diagnostic_sessions');self.c.commit()
            s=self.start('condition');s=self.event(s,'answer',answer=1);s=self.event(s,'locate',reason=reason);s=self.event(s,'finish')
            self.assertEqual(category,s['summary']['category'])
    def test_model_chain_formula_calculation_result_categories(self):
        cases=[('model','model_unrecognized','model_identification'),('plan','chain_broken','knowledge_organization'),('execution','formula_misused','formula_application'),('execution','arithmetic','calculation'),('execution','unchecked','result_check')]
        for report,reason,category in cases:
            self.c.execute('delete from diagnostic_events');self.c.execute('delete from diagnostic_sessions');self.c.commit()
            s=self.start(report);s=self.event(s,'answer',answer=1);s=self.event(s,'locate',reason=reason);s=self.event(s,'finish')
            self.assertEqual(category,s['summary']['category'])
    def test_unsure_is_not_a_wrong_answer_or_automatic_model_label(self):
        s=self.start();s=self.event(s,'answer',answer=-1);self.assertIsNone(s['feedback']['passed'])
        s=self.event(s,'locate',reason='unsure');s=self.event(s,'finish')
        self.assertEqual('insufficient',s['summary']['category']);self.assertEqual('条件理解',s['summary']['location'])
        self.assertNotIn('知识遗忘',s['summary']['label'])
    def test_all_passed_does_not_invent_old_cause(self):
        s=self.start();s=self.event(s,'answer',answer=0);s=self.event(s,'next');s=self.event(s,'answer',answer=0);s=self.event(s,'next')
        self.assertEqual('completed',s['status']);self.assertEqual('insufficient',s['summary']['category'])
        self.assertEqual(['无提示通过','无提示通过'],[t['label'] for t in s['summary']['trace']])
    def test_deep_traces_remaining_unknown_and_two_help_limit(self):
        s=self.start(mode='deep');self.assertEqual(4,s['total']);s=self.event(s,'answer',answer=1);s=self.event(s,'locate',reason='untranslated')
        s=self.event(s,'hint');s=self.event(s,'answer',answer=1);s=self.event(s,'hint');s=self.event(s,'answer',answer=1)
        self.assertFalse(s['step']['can_hint']);s=self.event(s,'next');s=self.event(s,'answer',answer=1);self.assertFalse(s['step']['can_hint'])
        s=self.event(s,'finish');self.assertEqual('ended',s['status']);self.assertEqual(2,sum(t['label']=='尚未检查' for t in s['summary']['trace']))
    def test_time_context_is_not_an_ability_problem(self):
        s=self.start('time');self.assertIn('reflection',s);self.assertNotIn('step',s)
        s=self.event(s,'locate',reason='not_reached');self.assertEqual('completed',s['status']);self.assertFalse(s['assisted'])
        self.assertEqual('answer_context',s['summary']['category']);self.assertTrue(all(t['label']=='尚未检查' for t in s['summary']['trace']))
    def test_event_idempotency_stale_cursor_and_no_guessing_retries(self):
        s=self.start('knowledge_retrieval');s=self.event(s,'answer','stable-key',answer=1)
        repeated=self.event(s,'answer','stable-key',answer=1);self.assertEqual(s,repeated)
        with self.assertRaises(StateConflict):self.event(s,'answer','stable-key',answer=0)
        with self.assertRaises(StateConflict):self.event(s,'answer',answer=0)
        with self.assertRaises(InvalidRequest):self.event(s,'locate',reason='arithmetic')
        s=self.event(s,'locate',reason='not_recalled')
        with self.assertRaises(StateConflict):self.event(s,'answer',answer=0)
        with self.assertRaises(StateConflict):self.api('event',session_id=s['session_id'],cursor=99,event='next',request_key='out-of-date')
    def test_student_correction_preserves_old_reason_and_feedback(self):
        s=self.start('knowledge_retrieval');s=self.event(s,'answer',answer=0);s=self.event(s,'locate',reason='not_recalled');s=self.event(s,'finish',confirmation='different')
        self.assertEqual('学生认为不符合',s['summary']['strength'])
        s=self.event(s,'revise',reason='forgot');self.assertEqual('knowledge_forgotten',s['summary']['category']);self.assertEqual('',s['confirmation'])
        self.assertEqual(1,self.c.execute("select count(*) from diagnostic_events where action='locate'").fetchone()[0]);self.assertEqual(1,self.c.execute("select count(*) from diagnostic_events where action='revise'").fetchone()[0])
    def test_quick_to_deep_keeps_attempts_and_pinned_card(self):
        s=self.start();s=self.event(s,'answer',answer=0);s=self.event(s,'next');s=self.event(s,'answer',answer=0);s=self.event(s,'next')
        before=self.c.execute('select effective_card_json,graph_mapping_json from diagnostic_sessions').fetchone()
        s=self.event(s,'deepen');self.assertEqual(4,s['total']);self.assertEqual(2,s['cursor']);self.assertEqual('deep',s['mode'])
        self.assertEqual(tuple(before),tuple(self.c.execute('select effective_card_json,graph_mapping_json from diagnostic_sessions').fetchone()))
    def test_ownership_original_answers_and_backup_restore(self):
        originals=[tuple(r) for r in self.c.execute('select * from student_responses')];s=self.start('knowledge_gap');s=self.event(s,'answer',answer=1);s=self.event(s,'locate',reason='forgot')
        with self.assertRaises(PermissionDenied):diagnosis.api(self.repo,self.other,'diagnosis-event',{**self.p,'session_id':s['session_id'],'event':'finish','cursor':0,'request_key':'foreign'})
        self.assertEqual(originals,[tuple(r) for r in self.c.execute('select * from student_responses')])
        from highschoolphysics.backup import export_tables,restore_backup
        from highschoolphysics.db import connect,initialize_database
        backup=export_tables(self.c);destination=connect(':memory:');initialize_database(destination);learning.migrate(destination);restore_backup(destination,backup)
        restored=destination.execute('select * from diagnostic_sessions where id=?',(s['session_id'],)).fetchone();self.assertEqual(2,restored['protocol_version']);self.assertEqual(s,diagnosis.state(destination,restored));destination.close()
    def test_completed_classification_is_saved_with_version(self):
        s=self.start('knowledge_retrieval');s=self.event(s,'answer',answer=0);s=self.event(s,'locate',reason='not_recalled');s=self.event(s,'finish')
        stored=json.loads(self.c.execute("select result_json from diagnostic_events where action='finish'").fetchone()[0]);self.assertEqual('reasoning-v2',stored['classification']['classification_version'])
        changed=dict(reasoning.CATEGORIES);changed['knowledge_retrieval']=('future label','future advice')
        with patch.object(reasoning,'CATEGORIES',changed):
            self.assertEqual(s['summary'],self.api('state')['state']['summary'])
    def test_default_protocol_and_generated_cards_are_selection_tasks(self):
        s=diagnosis.api(self.repo,self.user,'diagnosis-start',{**self.p,'mode':'quick','self_report':'knowledge_gap'})['state'];self.assertEqual(2,s['protocol_version'])
        with self.assertRaises(InvalidRequest):diagnosis.api(self.repo,self.user,'diagnosis-start',{**self.p,'protocol_version':1,'mode':'quick','note':'不应接收文本'})
