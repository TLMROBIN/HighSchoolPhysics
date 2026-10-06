import base64
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from tests.http_support import LivePhysicsServer
from highschoolphysics import learning, question_bank, document_ingestion
from highschoolphysics.db import connect, initialize_database
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.document_worker import run_once


class QuestionBankTests(unittest.TestCase):
    def setUp(self):
        self.flag=patch.dict(os.environ,{'HSP_DOCUMENT_INGESTION_ENABLED':'1'})
        self.flag.start();self.addCleanup(self.flag.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.context=LivePhysicsServer(Path(self.tmp.name)/'bank.sqlite3')
        self.server=self.context.__enter__();self.addCleanup(lambda:self.context.__exit__(None,None,None))
        self.conn=connect(self.server.db_path);self.addCleanup(self.conn.close)
        learning.migrate(self.conn)
        self.repo=PhysicsRepository(self.conn)
        self.user=dict(self.conn.execute("select * from users where username='teacher_li'").fetchone())
        _,self.cookie,_=self.server.login('teacher_li','teacher123')
        self.origin='http://%s:%s'%self.server.address

    def get(self,path,headers=None):
        status,_,body=self.server.request('GET',path,headers=headers or {'Cookie':self.cookie})
        return status,json.loads(body)

    def post(self,path,payload,headers=None):
        status,_,body=self.server.request('POST',path,json.dumps(payload).encode(),headers or {'Cookie':self.cookie,'Origin':self.origin,'Content-Type':'application/json'})
        return status,json.loads(body)

    def entry(self,qid='q-newton-1'):
        return {'question_id':qid,'question_version':self.repo.get_question(qid)['version'],'expected_revision':question_bank.tag_revision(self.conn,qid),**{kind:[tags[0]['id']] for kind,tags in question_bank.taxonomy(self.repo,self.user).items()}}

    def import_batch(self,mode='paper',bank_type=None):
        source=b'%PDF-1.7\nbank test fixture\n';digest=hashlib.sha256(source).hexdigest()
        _,data=self.post('/api/documents/uploads',{'name':'bank.pdf','size':len(source),'sha256':digest,'role':'paper','import_mode':mode,'tagging_policy':'question_bank','title':'整卷验证','request_key':'bank-upload-'+mode})
        upload=data['result']
        self.post('/api/documents/uploads/'+upload['upload_id']+'/parts',{'index':0,'sha256':digest,'data_base64':base64.b64encode(source).decode()})
        _,data=self.post('/api/documents/uploads/'+upload['upload_id']+'/complete',{})
        task=data['result']['task_id']
        def converter(source_path,original_name,store,school_id,document_id,conversion_id,work_dir,**kwargs):
            output=io.BytesIO();Image.new('RGB',(12,12),(100,130,160)).save(output,format='PNG')
            asset=store.store_asset(school_id,output.getvalue(),{'page':1})
            blocks=[{'id':'b'+str(n),'type':'paragraph','page':1,'column':0,'order':n,'bbox':[0,0,1,1],'markdown':f'第{n}题 小车受力分析。'+ ('\n\n![图](asset:'+asset['id']+')' if n==1 else '')+'\n\nA. 加速\n\nB. 匀速','asset_ids':[asset['id']] if n==1 else [],'source_locator':{'kind':'pdf','page':1},'issues':[]} for n in (1,2)]
            return {'document':{'schema_version':1,'document_id':document_id,'conversion_id':conversion_id,'source_sha256':digest,'pages':[{'page':1,'width':100,'height':100,'rotation_applied':0}],'blocks':blocks,'assets':[{k:asset[k] for k in ('id','sha256','mime_type','byte_size','width_px','height_px')}],'issues':[]},'markdown':'\n\n'.join(b['markdown'] for b in blocks),'manifest':{'method':'bank-test','issues':[],'formula_count':0},'assets':[asset],'adapter_name':'test','adapter_version':'1','preview_pdf':source,'preview_converter':'test'}
        self.assertEqual(run_once(self.server.db_path,converter=converter)['status'],'parsed')
        items=document_ingestion.get_task_items(self.conn,self.user,task)
        for item in items:
            d=item['document'];d['answer_md']='A';d['analysis_md']='使用牛顿第二定律。';d['answer_state']='verified'
            if bank_type:
                d['bank_type'] = bank_type
            saved=document_ingestion.save_candidate(self.conn,self.user,item['id'],{'request_key':'save-'+item['id'],'expected_revision':item['review_revision'],'document':d})
            item['review_revision']=saved['review_revision']
        refs=[{'id':item['id'],'expected_revision':item['review_revision']} for item in items]
        document_ingestion.confirm_paper_review(self.conn,self.user,task,{'request_key':'review-'+task,'items':refs})
        result=document_ingestion.confirm_candidates(self.conn,self.user,task,{'request_key':'publish-'+task,'items':refs,'reviewed':True})
        return result

    def test_choice_category_and_next_assessment_use_the_same_controls(self):
        result=self.import_batch()
        qid=question_bank.batch_question_ids(self.conn,result['batch_id'],self.user['school_id'])[0]
        q=self.repo.get_question(qid)
        self.post('/api/question-bank/type',dict(question_id=qid,question_version=q['version'],bank_type='multiple_choice'))
        q=self.repo.get_question(qid)
        self.assertEqual(q['question_type'],'multiple_choice')
        self.assertEqual(q['answer']['type'],'multiple_choice')
        self.post('/api/question-bank/type',dict(question_id=qid,question_version=q['version'],bank_type='single_choice'))
        self.assertEqual(self.repo.get_question(qid)['answer']['type'],'single_choice')
        self.assertEqual(question_bank.content(self.conn,qid,self.user['school_id'])['document']['kind'],'single_choice')
        self.post('/api/question-bank/tags',{'entries':[self.entry(qid)]})
        aid=learning.api(self.repo,self.user,'assessment',dict(title='单选按钮验证',class_id='class-physics-1',questions=[qid]))['url'].split('=')[1]
        from highschoolphysics.learning_views import answer_controls
        snapshot=dict(self.conn.execute('select * from question_version_snapshots where assessment_id=?',(aid,)).fetchone())
        snapshot['question_type']=json.loads(snapshot['grading_rule_json'])['type']
        controls=answer_controls(self.conn,snapshot,self.user)
        self.assertIn('type="radio"',controls)
        self.assertNotIn('type="checkbox"',controls)


    def test_recovery_only_fills_empty_unused_draft_snapshots(self):
        from highschoolphysics.answer_recovery import recover
        from highschoolphysics.document_models import canonical_sha256
        result=self.import_batch()
        ids=question_bank.batch_question_ids(self.conn,result['batch_id'],self.user['school_id'])
        task=self.conn.execute('select parser_task_id from questions where id=?',(ids[0],)).fetchone()[0]
        for qid in ids:
            binding=question_bank.content(self.conn,qid,self.user['school_id'])
            d=binding['document'];d.update(answer_md='',analysis_md='',answer_state='missing',grading_rule=None)
            self.conn.execute('update question_content_revisions set document_json=?,content_sha256=?,answer_state=? where id=?',(json.dumps(d),canonical_sha256(d),'missing',binding['current_revision_id']))
            self.conn.execute("update questions set answer_json='{}',analysis='' where id=?",(qid,))
        self.conn.commit()
        for qid in ids:
            self.post('/api/question-bank/tags',{'entries':[self.entry(qid)]})
        aid=learning.api(self.repo,self.user,'assessment',dict(title='空答案修复',class_id='class-physics-1',questions=ids))['url'].split('=')[1]
        published=learning.api(self.repo,self.user,'assessment',dict(title='冻结考试保护',class_id='class-physics-1',questions=ids))['url'].split('=')[1]
        self.conn.execute("update assessment_sessions set grading_status='published' where id=?",(published,));self.conn.commit()
        frozen=[tuple(r) for r in self.conn.execute('select * from question_version_snapshots where assessment_id=? order by id',(published,))]
        # Protect one draft snapshot with an existing teacher key.
        self.conn.execute('update question_version_snapshots set answer_json=? where assessment_id=? and position=2',(json.dumps('B'),aid));self.conn.commit()
        old_key=self.conn.execute('select answer_json from question_version_snapshots where assessment_id=? and position=2',(aid,)).fetchone()[0]
        entries={str(n):[dict(markdown='A\n\n故选A。',source_spans=[],asset_refs=[])] for n in (1,2)}
        with patch('highschoolphysics.answer_recovery._answer_groups',return_value=entries):
            preview=recover(self.repo,self.user,task,task)
            self.assertEqual(len(preview['recovered']),2)
            self.assertEqual(self.repo.get_question(ids[0])['answer'],{})
            applied=recover(self.repo,self.user,task,task,apply=True)
            self.assertEqual(len(applied['snapshots_filled']),1)
            repeated=recover(self.repo,self.user,task,task,apply=True)
            self.assertEqual(repeated['recovered'],[])
        self.assertEqual(self.conn.execute('select answer_json from question_version_snapshots where assessment_id=? and position=2',(aid,)).fetchone()[0],old_key)
        self.assertEqual(self.conn.execute('select count(*) from student_responses where assessment_id=?',(aid,)).fetchone()[0],0)
        self.assertFalse(self.conn.execute('pragma foreign_key_check').fetchall())
        self.assertEqual(frozen,[tuple(r) for r in self.conn.execute('select * from question_version_snapshots where assessment_id=? order by id',(published,))])

    def test_imported_choice_answer_grades_first_and_redo_separately(self):
        result = self.import_batch()
        ids = question_bank.batch_question_ids(self.conn,result['batch_id'],self.user['school_id'])
        for qid in ids:
            self.assertEqual(self.repo.get_question(qid)['answer']['answer'], 'A')
            self.post('/api/question-bank/tags',{'entries':[self.entry(qid)]})
        listing = question_bank.library(self.repo,self.user,batch_id=result['batch_id'])
        self.assertIn('使用牛顿第二定律', listing['groups'][0]['solution_html'])
        aid = learning.api(self.repo,self.user,'assessment',dict(title='答案闭环',class_id='class-physics-1',questions=ids))['url'].split('=')[1]
        users = self.conn.execute('select u.* from users u join assessment_participants p on p.student_id=u.id where p.assessment_id=?',(aid,)).fetchall()
        records = [dict(student=u['username'],number=str(n+1),answer='B') for u in users for n in range(len(ids))]
        payload = dict(assessment_id=aid,records=records,request_key='first-choice-records')
        preview = learning.api(self.repo,self.user,'answers',payload)
        self.assertTrue(all(r['outcome']=='wrong' for r in preview['records']))
        learning.api(self.repo,self.user,'answers',dict(payload,confirm=True,preview_token=preview['preview_token']))
        learning.api(self.repo,self.user,'publish',{'assessment_id':aid})
        wrong = self.conn.execute('select * from wrong_questions where assessment_id=? and student_id=?',(aid,users[0]['id'])).fetchone()
        for n,(answer,outcome) in enumerate([('B','wrong'),('A','correct')]):
            attempt=learning.submit(self.repo,users[0]['id'],dict(wrong_id=wrong['id'],answer=answer,request_key='redo-choice-'+str(n)))
            self.assertEqual(attempt['outcome'],outcome)
        self.assertEqual(self.conn.execute('select initial_answer from student_responses where id=?',(wrong['response_id'],)).fetchone()[0],'B')
        from highschoolphysics.learning_views import student
        page=student(self.repo,dict(users[0]),{})
        self.assertIn('后续复习记录',page)
        self.assertIn('选择：B',page)
        self.assertIn('选择：A',page)

    def test_imported_bank_type_filters_and_full_stem_html(self):
        result = self.import_batch(bank_type='experiment')
        ids = question_bank.batch_question_ids(self.conn, result['batch_id'], self.user['school_id'])
        self.assertEqual({self.repo.get_question(qid)['bank_type'] for qid in ids}, {'experiment'})
        status,data = self.get('/api/question-bank/library?question_type=experiment&page_size=10&batch_id='+result['batch_id'])
        self.assertEqual(status, 200, data)
        self.assertEqual(data['result']['total'], 2)
        rendered = data['result']['groups'][0]['html']
        self.assertIn('小车受力分析', rendered)
        self.assertIn('choice-figures', rendered)
        self.assertIn('question-lower-text', rendered)
        self.assertIn('question_id=', rendered)
        self.assertEqual(self.get('/api/question-bank/library?question_type=solution&batch_id='+result['batch_id'])[1]['result']['total'], 0)
        self.assertEqual(self.get('/api/question-bank/library?page_size=30')[0], 400)
        self.assertEqual(self.get('/api/question-bank/library?question_type=invalid')[0], 400)

    def test_parent_tag_filter_includes_descendants_and_page_sizes(self):
        catalog = question_bank.taxonomy(self.repo,self.user)
        leaf = next(t for t in catalog['knowledge'] if t.get('parent_id'))
        e = self.entry();e['knowledge'] = [leaf['id']]
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[e]})[0], 200)
        query = '?tag_family=knowledge&tag_id='+leaf['parent_id']
        result = self.get('/api/question-bank/library'+query)[1]['result']
        self.assertIn(e['question_id'],result['scope_question_ids'])
        self.assertEqual(self.get('/api/question-bank/library?tag_family=knowledge&tag_id=foreign')[0],400)
        for n in range(53):
            self.repo.create_question(self.user['id'],'分页题 '+str(n),{},'','', 'short_answer','测试','高三','','medium')
        for size in (10,20,50):
            status,data=self.get('/api/question-bank/library?page_size='+str(size))
            self.assertEqual(status,200,data)
            self.assertEqual(len(data['result']['groups']),size)
            self.assertGreater(data['result']['total'],50)
        page1=self.get('/api/question-bank/library?page_size=10')[1]['result']
        page2=self.get('/api/question-bank/library?page_size=10&offset=10')[1]['result']
        self.assertFalse({g['id'] for g in page1['groups']} & {g['id'] for g in page2['groups']})
        self.assertEqual(page1['scope_question_ids'],page2['scope_question_ids'])

    def test_type_changes_require_version_and_are_not_agent_permissions(self):
        q=self.repo.get_question('q-newton-1')
        payload={'question_id':q['id'],'question_version':q['version'],'bank_type':'experiment'}
        self.assertEqual(self.post('/api/question-bank/type',payload)[0],200)
        self.assertEqual(self.repo.get_question(q['id'])['bank_type'],'experiment')
        self.assertEqual(self.post('/api/question-bank/type',payload)[0],409)
        made=self.post('/api/question-bank/tokens',{'name':'test','days':1})[1]['result']
        self.assertEqual(self.post('/api/question-bank/type',payload,{'Authorization':'Bearer '+made['token'],'Content-Type':'application/json'})[0],403)
        initialize_database(self.conn)
        self.assertEqual(self.repo.get_question(q['id'])['bank_type'],'experiment')

    def test_imported_paper_batch_tags_edit_and_assessment_are_connected(self):
        result=self.import_batch()
        self.assertTrue(result['paper_id'])
        self.assertEqual(self.conn.execute("select count(*) from question_tag_jobs where source='document_import'").fetchone()[0],0)
        status,data=self.get('/api/question-bank/library?batch_id='+result['batch_id'])
        self.assertEqual(status,200,data);ids=data['result']['scope_question_ids'];self.assertEqual(len(ids),2)
        self.assertEqual([g['number'] for g in data['result']['groups']],['1','2'])
        status,data=self.post('/api/question-bank/tags',{'entries':[self.entry(qid) for qid in ids]})
        self.assertEqual(status,200,data)
        status,data=self.post('/api/learning/assessment',{'paper_id':result['paper_id'],'title':'考试一','class_id':'class-physics-1','date':'2026-10-06'})
        self.assertEqual(status,200,data)
        assessment_id=data['result']['url'].split('=')[1]
        snapshot=self.conn.execute('select * from question_version_snapshots where assessment_id=? order by position',(assessment_id,)).fetchone()
        old_tags=snapshot['tag_snapshot_json']
        old_binding=self.conn.execute('select revision_id from snapshot_content_bindings where snapshot_id=?',(snapshot['id'],)).fetchone()[0]
        status,data=self.get('/api/question-bank/question?id='+ids[0]);self.assertEqual(status,200,data)
        detail=data['result'];doc=detail['document'];doc['stem_md']='修改后的题干，仍使用原图。';doc['answer_md']='B'
        status,data=self.post('/api/question-bank/content',{'question_id':ids[0],'question_version':detail['question']['version'],'expected_content_revision':detail['content_revision_id'],'document':doc})
        self.assertEqual(status,200,data)
        self.assertNotEqual(data['result']['content_revision_id'],old_binding)
        self.assertEqual(self.conn.execute('select revision_id from snapshot_content_bindings where snapshot_id=?',(snapshot['id'],)).fetchone()[0],old_binding)
        e=self.entry(ids[0]);e['ability']=[]
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[e]})[0],200)
        self.assertEqual(self.conn.execute('select tag_snapshot_json from question_version_snapshots where id=?',(snapshot['id'],)).fetchone()[0],old_tags)
        # Saving new content retains image authorization and original image bytes.
        asset=self.conn.execute('select asset_id from content_asset_refs where revision_id=?',(data['result']['content_revision_id'],)).fetchone()[0]
        status,_,body=self.server.request('GET','/api/question-bank/assets/'+asset+'?question_id='+ids[0],headers={'Cookie':self.cookie})
        self.assertEqual(status,200);self.assertTrue(body.startswith(b'\x89PNG'))

    def test_question_only_import_does_not_create_paper(self):
        result=self.import_batch('questions')
        self.assertIsNone(result['paper_id'])
        self.assertEqual(self.conn.execute('select count(*) from question_bank_imports where paper_id is not null').fetchone()[0],0)

    def test_tag_batch_is_atomic_and_detects_concurrent_or_content_edits(self):
        ids=[r[0] for r in self.conn.execute('select id from questions limit 2')]
        entries=[self.entry(qid) for qid in ids]
        before=self.repo.tags_for_question(ids[0])
        entries[1]['knowledge']=['made-up']
        status,_=self.post('/api/question-bank/tags',{'entries':entries});self.assertEqual(status,400)
        self.assertEqual(self.repo.tags_for_question(ids[0]),before)
        first=self.entry(ids[0]);self.assertEqual(self.post('/api/question-bank/tags',{'entries':[first]})[0],200)
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[first]})[0],409)
        first=self.entry(ids[0]);self.conn.execute('update questions set version=version+1 where id=?',(ids[0],));self.conn.commit()
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[first]})[0],409)

    def test_agent_scope_revocation_and_csrf(self):
        _,data=self.post('/api/question-bank/tokens',{'name':'Test Agent','days':1})
        token=data['result']['token'];tid=data['result']['id']
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
        self.assertEqual(self.get('/api/question-bank/library',headers)[0],200)
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[self.entry()]},headers)[0],200)
        self.assertEqual(self.repo.get_question_tags('q-newton-1')[0]['source'],'agent')
        self.assertEqual(self.post('/api/question-bank/content',{},headers)[0],403)
        self.assertEqual(self.post('/api/question-bank/generate',{},headers)[0],403)
        self.assertEqual(self.post('/api/question-bank/tokens',{'name':'escape'},headers)[0],403)
        self.assertEqual(self.get('/api/question-bank/tokens',headers)[0],404)
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[self.entry()]},{'Cookie':self.cookie,'Content-Type':'application/json','Origin':'https://evil.invalid'})[0],403)
        self.assertEqual(self.post('/api/question-bank/tokens/revoke',{'id':tid})[0],200)
        self.assertEqual(self.get('/api/question-bank/library',headers)[0],403)
        self.assertNotIn(token,self.conn.execute('select token_hash from question_bank_agent_tokens where id=?',(tid,)).fetchone()[0])

    def test_expired_agent_and_students_cannot_write(self):
        _,data=self.post('/api/question-bank/tokens',{'name':'Test Agent','days':1})
        token=data['result']['token']
        self.conn.execute("update question_bank_agent_tokens set expires_at='2000-01-01T00:00:00Z'");self.conn.commit()
        self.assertEqual(self.get('/api/question-bank/library',{'Authorization':'Bearer '+token})[0],403)
        _,cookie,_=self.server.login('stu_1001','student123')
        self.assertEqual(self.get('/api/question-bank/library',{'Cookie':cookie})[0],403)

    def test_cross_school_question_and_tags_are_rejected(self):
        self.conn.execute("insert into schools(id,name,org_scope) values('other','Other','other')")
        self.conn.execute("update questions set school_id='other' where id='q-newton-1'");self.conn.commit()
        self.assertEqual(self.get('/api/question-bank/question?id=q-newton-1')[0],404)
        e={'question_id':'q-newton-1','question_version':1,'expected_revision':0,'knowledge':[],'ability':[],'literacy':[]}
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[e]})[0],404)

    def test_queue_model_suggestions_never_overwrite_manual_tags(self):
        # Configure an enabled provider row without performing network requests.
        self.conn.execute("insert into provider_configs(id,school_id,provider_kind,provider_name,enabled) values('bank-llm','school-demo','llm','test',1)")
        self.conn.commit()
        status,data=self.post('/api/question-bank/generate',{'question_ids':['q-newton-1'],'only_missing':False,'request_key':'bank-model-queue'})
        self.assertEqual(status,200,data);jobs=data['result']['job_ids']
        self.assertEqual(self.post('/api/question-bank/generate',{'question_ids':['q-newton-1'],'only_missing':False,'request_key':'bank-model-queue'})[1]['result']['job_ids'],jobs)
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[self.entry()]})[0],200)
        before=self.repo.tags_for_question('q-newton-1')
        with patch.object(PhysicsRepository,'generate_llm_candidates',return_value={'id':None}):
            result=run_once(self.server.db_path)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['result']['status'],'stale')
        self.assertEqual(self.repo.tags_for_question('q-newton-1'),before)

    def test_model_candidates_are_persisted_and_can_be_saved_after_reload(self):
        self.repo.save_provider_config(actor_id='user-admin',provider_kind='llm',provider_name='bank-test',model_name='test-model',secret='private-unit-test-key',api_endpoint='https://llm.example.test/v1',enabled=True,daily_call_limit=10)
        result=question_bank.queue_tags(self.repo,self.user,{'question_ids':['q-newton-1'],'only_missing':False,'request_key':'persisted-bank-ai'})
        taxonomy=question_bank.taxonomy(self.repo,self.user)
        generated={kind+'_tags':[{'id':tags[0]['id'],'confidence':0.9,'rationale':'根据实际物理规律判断'}] for kind,tags in taxonomy.items()}
        generated.update({'cache_key':'unit-bank-candidate','model_version':'test-model','prompt_version':'physics-tri-family-tags-v1','input_units':100,'output_units':100})
        with patch('highschoolphysics.repository.generate_model_candidate_tags',return_value=generated):
            processed=run_once(self.server.db_path)
        self.assertEqual(processed['result']['status'],'suggested',processed)
        self.assertEqual(question_bank.tag_revision(self.conn,'q-newton-1'),0)
        reloaded=question_bank.detail(self.repo,self.user,'q-newton-1')
        self.assertIn('suggestion',reloaded['units'][0])
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[self.entry()]})[0],200)
        self.assertNotIn('suggestion',question_bank.detail(self.repo,self.user,'q-newton-1')['units'][0])

    def test_legacy_content_edit_preserves_numeric_rule_and_invalidates_tag_write(self):
        self.conn.execute("update questions set question_type='fill',answer_json=? where id='q-newton-1'",(json.dumps({'answer':'3','match':'numeric_quantity','unit':'m','unit_required':True}),));self.conn.commit()
        old=self.entry()
        data=question_bank.detail(self.repo,self.user,'q-newton-1')
        result=question_bank.save_content(self.repo,self.user,'q-newton-1',{'question_version':data['question']['version'],'fields':{'stem':'题干','options':{},'answer':'4','analysis':'解析'}})
        self.assertEqual(result['question']['answer']['answer'],'4')
        self.assertEqual(result['question']['answer']['unit'],'m')
        self.assertEqual(result['question']['answer']['match'],'numeric_quantity')
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':[old]})[0],409)

    def test_explicitly_confirmed_knowledge_allows_empty_ability_and_literacy(self):
        result=self.import_batch('paper')
        ids=question_bank.batch_question_ids(self.conn,result['batch_id'],self.user['school_id'])
        entries=[{**self.entry(qid),'ability':[],'literacy':[]} for qid in ids]
        self.assertEqual(self.post('/api/question-bank/tags',{'entries':entries})[0],200)
        self.assertEqual(self.post('/api/learning/assessment',{'paper_id':result['paper_id'],'title':'标签依据验证','class_id':'class-physics-1'})[0],200)

    def test_migration_and_bank_page_keep_proxy_paths(self):
        initialize_database(self.conn);initialize_database(self.conn)
        self.assertEqual(self.conn.execute('pragma integrity_check').fetchone()[0],'ok')
        self.assertEqual(self.conn.execute('pragma foreign_key_check').fetchall(),[])
        status,_,body=self.server.request('GET','/question-bank',headers={'Cookie':self.cookie,'X-Forwarded-Prefix':'/physics'})
        self.assertEqual(status,200);self.assertIn(b'data-question-bank',body);self.assertIn(b'data-base-path="/physics"',body)
        self.assertIn(b'question-bank.js',body)


if __name__=='__main__':
    unittest.main()
