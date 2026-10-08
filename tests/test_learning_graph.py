import copy
import json
import sqlite3
import unittest
from highschoolphysics import learning_graph as graph, diagnosis, student_learning
from highschoolphysics.errors import InvalidRequest, PermissionDenied, StateConflict
from tests.test_diagnosis import card

class LearningGraphTests(unittest.TestCase):
    def setUp(self):
        from tests.test_diagnosis import DiagnosisTests
        DiagnosisTests.setUp(self)
        self.teacher=dict(self.c.execute("select * from users where id='user-teacher-li'").fetchone())
        self.card_id=self.c.execute('select id from diagnostic_cards').fetchone()[0]
        nodes=graph.catalog(self.c,self.user['school_id'])
        self.targets={kind:next(n['id'] for n in nodes if n['kind']==kind) for kind in ('knowledge','ability','literacy')}
    def tearDown(self):self.c.close()
    def save(self,title='新版诊断'):
        release,_=graph.latest(self.c,self.user['school_id'])
        content=card();content['title']=title
        return graph.api(self.repo,self.teacher,'graph-card-save',dict(version=release['version'] if release else 0,card_id=self.card_id,card=content,mappings=[dict(name='识别平衡条件',definition='能区分各力与合力。',knowledge_id=self.targets['knowledge'],ability_id=self.targets['ability'],literacy_id=self.targets['literacy']) for _ in content['steps']]))
    def test_teacher_reviews_card_and_sessions_pin_both_versions(self):
        before=[tuple(r) for r in self.c.execute('select * from student_responses')]
        self.save('第一版')
        s=diagnosis.api(self.repo,self.user,'diagnosis-start',dict(protocol_version=1,wrong_id=self.w['id'],question_id=self.w['question_id'],mode='deep'))['state']
        pinned=dict(self.c.execute('select * from diagnostic_sessions').fetchone());self.save('第二版')
        self.assertEqual(pinned,dict(self.c.execute('select * from diagnostic_sessions').fetchone()))
        self.assertEqual('第一版',graph.session_card(self.c,pinned)['title'])
        result=diagnosis.api(self.repo,self.user,'diagnosis-event',dict(wrong_id=self.w['id'],question_id=self.w['question_id'],session_id=s['session_id'],cursor=0,event='answer',answer=0,request_key='graph-test'))['state']
        self.assertTrue(result['findings'][0]['graph_node_id'])
        data=graph.student_data(self.repo,self.user);self.assertEqual('本步独立通过',data['evidence'][0]['status'])
        self.assertEqual(before,[tuple(r) for r in self.c.execute('select * from student_responses')])
        self.assertEqual(0,self.c.execute('select count(*) from redo_attempts').fetchone()[0])
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("update learning_graph_releases set source='tampered'")
        self.c.rollback()
    def test_permissions_concurrency_and_private_evidence(self):
        with self.assertRaises(PermissionDenied):graph.api(self.repo,self.user,'graph-card-save',{})
        self.save()
        with self.assertRaises(StateConflict):graph.api(self.repo,self.teacher,'graph-edge-review',dict(version=0,ids=['x'],status='approved'))
        data=graph.student_data(self.repo,self.other);self.assertEqual([],data['evidence'])
        with self.assertRaises(PermissionDenied):graph.student_data(self.repo,self.teacher)
        other=dict(self.teacher,school_id='another-school')
        with self.assertRaises(PermissionDenied):graph.api(self.repo,other,'graph-card-save',dict(version=0,card_id=self.card_id,card=card(),mappings=[]))
    def test_strict_edges_validate_cycles_and_sources(self):
        self.save();release,data=graph.latest(self.c,self.user['school_id']);ids=[n['id'] for n in data['nodes']]
        args=dict(version=release['version'],source=ids[0],target=ids[1],kind='prerequisite',reason='先判断合力才能判断加速度。',locator='牛顿第二定律',conditions='选定研究对象')
        graph.api(self.repo,self.teacher,'graph-edge-save',args)
        args.update(version=2,source=ids[1],target=ids[0])
        with self.assertRaises(InvalidRequest):graph.api(self.repo,self.teacher,'graph-edge-save',args)
        args.update(source=self.targets['knowledge'],target=ids[0])
        with self.assertRaises(InvalidRequest):graph.api(self.repo,self.teacher,'graph-edge-save',args)
        args.update(source=ids[0],target=ids[2],reason='')
        with self.assertRaises(InvalidRequest):graph.api(self.repo,self.teacher,'graph-edge-save',args)
    def test_disabled_nodes_do_not_leak_or_reappear(self):
        self.save();kid=self.targets['knowledge'];self.c.execute('update knowledge_nodes set enabled=0 where id=?',(kid,));self.c.commit()
        data=graph.student_data(self.repo,self.user);ids={n['id'] for n in data['nodes']}
        self.assertNotIn(kid,ids);self.assertFalse(any(n['kind']=='objective' for n in data['nodes']))
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in data['edges']))
    def test_migration_idempotent_html_and_empty_state(self):
        graph.migrate(self.c);graph.migrate(self.c)
        self.assertIn('data-graph-mode',graph.student_page(self.repo,self.user))
        self.assertIn('诊断目标与关系审核',graph.teacher_page(self.repo,self.teacher,{}))
        self.save();self.assertIn('data-graph-step',graph.teacher_page(self.repo,self.teacher,{}))
        # Student payload never contains the correct choice or complete diagnostic card.
        payload=graph.student_data(self.repo,self.user);self.assertNotIn('"correct"',json.dumps(payload))
    def test_manifest_covers_92_specific_checks_without_tag_blanket(self):
        from pathlib import Path
        rows=json.loads((Path(diagnosis.__file__).with_name('diagnostic_data')/'graph-20261007.json').read_text())
        cards=json.loads((Path(diagnosis.__file__).with_name('diagnostic_data')/'reviewed-20261007.json').read_text())
        self.assertEqual(23,len(rows));self.assertEqual(92,sum(len(r['steps']) for r in rows))
        old={r['question_id']:r for r in cards}
        for r in rows:
            self.assertEqual(old[r['question_id']]['fingerprint'],r['fingerprint'])
            self.assertEqual([s['prompt'] for s in old[r['question_id']]['card']['steps']],[s['source_prompt'] for s in r['steps']])
        windy=next(r for r in rows if r['question_id']=='q-7ed58356ca0144c8')
        self.assertTrue(all('e1-c01-s03' not in s['knowledge_id'] for s in windy['steps']))
    def test_all_existing_cards_backfill_and_idempotency(self):
        from pathlib import Path
        manifest=json.loads((Path(diagnosis.__file__).with_name('diagnostic_data')/'reviewed-20261007.json').read_text())
        q=dict(self.c.execute('select * from questions limit 1').fetchone())
        keys=list(q)
        for r in manifest:
            row=dict(q,id=r['question_id'])
            self.c.execute('insert into questions('+','.join(keys)+') values('+','.join('?' for _ in keys)+')',[row[k] for k in keys])
            self.c.execute('insert into diagnostic_cards values(?,?,?,?,?,?,?,?)',('test-'+r['question_id'],self.user['school_id'],r['question_id'],r['fingerprint'],'{}',diagnosis.dumps(r['card']),'assistant-reviewed',diagnosis.now()))
        self.c.commit();originals=[tuple(r) for r in self.c.execute('select * from student_responses')]
        self.assertEqual(23,graph.backfill(self.repo)['mapped_cards'])
        release,g=graph.latest(self.c,self.user['school_id']);self.assertEqual(92,len(g['nodes']));self.assertEqual(299,len(g['edges']))
        self.assertEqual(0,graph.backfill(self.repo)['mapped_cards']);self.assertEqual(release,graph.latest(self.c,self.user['school_id'])[0])
        self.assertEqual(originals,[tuple(r) for r in self.c.execute('select * from student_responses')])
    def test_future_model_targets_are_private_review_candidates(self):
        graph.prepare_candidate(self.repo,self.card_id);self.c.commit()
        _,g=graph.latest(self.c,self.user['school_id']);self.assertEqual('draft',g['cards'][self.card_id]['status'])
        data=graph.student_data(self.repo,self.user);self.assertFalse(any(n['kind']=='objective' for n in data['nodes']))
        s=diagnosis.api(self.repo,self.user,'diagnosis-start',dict(protocol_version=1,wrong_id=self.w['id'],question_id=self.w['question_id'],mode='deep'))['state']
        row=self.c.execute('select * from diagnostic_sessions where id=?',(s['session_id'],)).fetchone();self.assertEqual('[]',row['graph_mapping_json'])
        self.save();self.assertTrue(any(n['kind']=='objective' for n in graph.student_data(self.repo,self.user)['nodes']))
    def test_publication_and_pinned_mapping_export_restore(self):
        from highschoolphysics.backup import export_tables,restore_backup
        from highschoolphysics.db import connect,initialize_database
        self.save()
        diagnosis.api(self.repo,self.user,'diagnosis-start',dict(protocol_version=1,wrong_id=self.w['id'],question_id=self.w['question_id'],mode='deep'))
        backup=export_tables(self.c);destination=connect(':memory:');initialize_database(destination)
        from highschoolphysics.learning import migrate as migrate_learning
        migrate_learning(destination)
        restore_backup(destination,backup)
        self.assertEqual(graph.latest(self.c,self.user['school_id']),graph.latest(destination,self.user['school_id']))
        original=self.c.execute('select effective_card_json,graph_mapping_json,graph_release_id from diagnostic_sessions').fetchone()
        restored=destination.execute('select effective_card_json,graph_mapping_json,graph_release_id from diagnostic_sessions').fetchone()
        self.assertEqual(tuple(original),tuple(restored));destination.close()
    def test_http_routes_origin_and_role_controls(self):
        import tempfile
        from pathlib import Path
        from tests.http_support import LivePhysicsServer
        self.save()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'graph.sqlite3'
            with sqlite3.connect(path) as destination:self.c.backup(destination)
            with LivePhysicsServer(path,seed=False) as server:
                _,student,_=server.login('stu_1001','student123')
                code,_,html=server.request('GET','/app?module=graph',headers={'Cookie':student,'X-Forwarded-Prefix':'/physics'})
                self.assertEqual(200,code);self.assertIn('data-learning-graph',html.decode())
                code,_,_=server.request('GET','/teacher?module=graph',headers={'Cookie':student})
                self.assertEqual(403,code)
                _,teacher,_=server.login('teacher_li','teacher123')
                code,_,html=server.request('GET','/teacher?module=graph',headers={'Cookie':teacher,'X-Forwarded-Prefix':'/physics'})
                self.assertEqual(200,code);self.assertIn('data-graph-card',html.decode());self.assertIn('data-base-path="/physics"',html.decode())
                payload=json.dumps(dict(version=1,ids=['invalid'],status='approved')).encode()
                code,_,_=server.request('POST','/api/learning/graph-edge-review',payload,{'Cookie':teacher,'Content-Type':'application/json'})
                self.assertEqual(403,code)
                origin='http://127.0.0.1:'+str(server.address[1])
                code,_,_=server.request('POST','/api/learning/graph-edge-review',payload,{'Cookie':student,'Content-Type':'application/json','Origin':origin})
                self.assertEqual(403,code)
                release,g=graph.latest(self.c,self.user['school_id']);payload=json.dumps(dict(version=1,ids=[g['edges'][0]['id']],status='rejected')).encode()
                code,_,data=server.request('POST','/api/learning/graph-edge-review',payload,{'Cookie':teacher,'Content-Type':'application/json','Origin':origin})
                self.assertEqual(200,code,data);self.assertEqual(2,json.loads(data)['result']['version'])
