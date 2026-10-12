import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from highschoolphysics import diagnosis, learning_goals as goals, learning_graph as graph
from highschoolphysics import learning_regions as regions, student_learning
from highschoolphysics.errors import InvalidRequest, PermissionDenied, StateConflict
from tests import test_diagnosis_adaptive as adaptive_tests


def observation(task, passed=False, **values):
    return dict(id='event-'+task, student_id='stu-1001', task=task, passed=passed,
        assisted=False, approved=True, date='2026-10-11T00:00:00+00:00', source='check',
        **values)


class LearningRegionsTests(unittest.TestCase):
    setUp = adaptive_tests.AdaptiveTests.setUp
    tearDown = adaptive_tests.AdaptiveTests.tearDown
    api = adaptive_tests.AdaptiveTests.api
    start = adaptive_tests.AdaptiveTests.start
    event = adaptive_tests.AdaptiveTests.event
    block_model = adaptive_tests.AdaptiveTests.block_model

    def teacher(self):
        return dict(self.c.execute("select * from users where id='user-teacher-li'").fetchone())

    def test_empty_class_does_not_hide_available_students_by_default(self):
        populated = self.user['class_id']
        row = dict(self.c.execute('select * from class_groups where id=?', (populated,)).fetchone())
        row.update(id='class-empty-first', name='000空班')
        columns = ','.join(row)
        self.c.execute('insert into class_groups (' + columns + ') values (' + ','.join('?' for _ in row) + ')', list(row.values()))
        self.c.execute("insert into teacher_classes (teacher_id,class_id,subject) values (?,?,'physics')",
                       (self.teacher()['id'], row['id']))
        self.c.commit()
        default = regions.analyze(self.repo, self.teacher(), {})
        self.assertEqual(populated, default['class_id'])
        explicit = regions.analyze(self.repo, self.teacher(), {'class_id':[row['id']]})
        self.assertEqual({}, explicit['students'])
        self.assertEqual(row['id'], explicit['class_id'])

    def review(self, index=0, **values):
        release, _ = graph.latest(self.c, self.user['school_id'])
        return graph.api(self.repo, self.teacher(), 'graph-goal-save', dict(
            version=release['version'] if release else 0, card_id=self.card_id,
            action_index=index, knowledge_id='kn-pep2019-r1-c04-s03',
            name='判断合力与加速度关系', definition='在指定物体和方向上选择合力与加速度的关系。',
            conditions='惯性参考系、质量不变，明确研究对象和方向。', **values))

    def test_unknown_helped_retry_and_duplicate_tasks_do_not_inflate(self):
        self.assertEqual('check', regions.goal_state([observation('one'), observation('one')])['state'])
        self.assertEqual('repeated', regions.goal_state([observation('one'), observation('two')])['state'])
        state = regions.goal_state([observation('one'), observation('two', None),
            dict(observation('three', True), assisted=True, date='2026-10-12T00:00:00Z'),
            dict(observation('four'), approved=False)])
        self.assertEqual('practice', state['state'])
        self.assertEqual(1, state['independent']); self.assertFalse(state['sufficient'])
        self.assertEqual(1, state['uncertain']); self.assertEqual(1, state['unreviewed'])
        same = [observation('one'), dict(observation('one', True), date='2026-10-12T00:00:00Z')]
        self.assertEqual('check', regions.goal_state(same)['state'])

    def test_shared_bindings_are_explicit_versioned_and_frozen(self):
        before = [tuple(r) for r in self.c.execute('select * from student_responses')]
        self.review()
        _, data = graph.latest(self.c, self.user['school_id'])
        gid = next(iter(data['shared_goals']))
        self.review(1, goal_id=gid)
        s = self.start(); pinned = dict(self.c.execute('select * from diagnostic_sessions').fetchone())
        self.assertEqual(gid, json.loads(pinned['graph_mapping_json'])[1]['goal_id'])
        self.review(1)  # Explicitly create a different target for future sessions.
        self.assertEqual(pinned, dict(self.c.execute('select * from diagnostic_sessions').fetchone()))
        self.event(s, 'probe', answer=0)
        evidence = regions.observations(self.c, self.user['school_id'], [self.user['id']])
        self.assertEqual(gid, evidence[0]['goal_id']); self.assertTrue(evidence[0]['approved'])
        self.assertEqual(before, [tuple(r) for r in self.c.execute('select * from student_responses')])
        with self.assertRaises(StateConflict):
            graph.api(self.repo, self.teacher(), 'graph-goal-save', dict(version=0))
        with self.assertRaises(PermissionDenied):
            graph.api(self.repo, self.user, 'graph-goal-save', {})
        with self.assertRaises(InvalidRequest): self.review(9)

    def test_old_empty_mapping_is_not_rewritten_by_new_review(self):
        s = self.start(); self.event(s, 'probe', answer=0)
        row = dict(self.c.execute('select * from diagnostic_sessions').fetchone())
        self.review()
        self.assertEqual(row, dict(self.c.execute('select * from diagnostic_sessions').fetchone()))
        self.assertEqual([], regions.observations(self.c, self.user['school_id'], [self.user['id']]))

    def test_verification_requires_its_own_target_review(self):
        self.review(1)
        _, data = graph.latest(self.c, self.user['school_id']); gid = next(iter(data['shared_goals']))
        # Candidate verification is deliberately not implied by check approval.
        binding = data['adaptive_bindings'][self.card_id]['bindings'][1]
        binding['verification'] = dict(binding, status='curated')
        graph.publish(self.c, self.user['school_id'], data, source='test-candidate'); self.c.commit()
        s = self.block_model(); s = self.event(s, 'probe', answer=1); s = self.event(s, 'practice', answer=0)
        row = self.c.execute("select id,result_json from diagnostic_events where action='practice'").fetchone()
        result = json.loads(row['result_json']); result['at'] = (datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
        self.c.execute('update diagnostic_events set result_json=? where id=?', (json.dumps(result), row['id'])); self.c.commit()
        s = self.api('state')['state']; self.event(s, 'verify', answer=0)
        evidence = regions.observations(self.c, self.user['school_id'], [self.user['id']])
        verification = next(e for e in evidence if e['source'] == 'verify')
        self.assertFalse(verification['approved'])
        self.assertNotEqual('passed', regions.goal_state(evidence)['state'])
        self.review(1, goal_id=gid, phase='verify')
        # Review cannot retrospectively approve this already pinned check.
        self.assertFalse(next(e for e in regions.observations(self.c, self.user['school_id'],
            [self.user['id']]) if e['source'] == 'verify')['approved'])

    def test_shared_goal_combines_only_observed_regions_and_stage_counts_stay_local(self):
        base = dict(student_id=self.user['id'], goal_id='shared', name='共同目标', definition='具体可观察表现',
            stage='concept', context='测试', question_id=self.w['question_id'], conditions='指定对象',
            label='无提示检查', node_id='', wrong_id=self.w['id'], assessment_id='assess-week-1', version='test')
        evidence = [dict(observation('one'), **base, knowledge_id='kn-pep2019-r1-c04-s03'),
                    dict(observation('two'), **base, knowledge_id='kn-pep2019-r2-c06-s03')]
        with patch.object(regions, 'observations', return_value=evidence):
            data = regions.analyze(self.repo, self.user, {'window':['all']})
            observed = [r for r in data['regions'] if r['goals']]
            self.assertEqual(2, len(observed))
            self.assertTrue(all(r['goals'][0]['state'] == 'repeated' for r in observed))
            teacher = regions.analyze(self.repo, self.teacher(), {'window':['all']})
            self.assertEqual(1, next(r for r in teacher['regions'] if r['id']=='kn-pep2019-r1-c04')['cells']['concept']['repeated'])
        evidence[1]['stage'] = 'equation'
        with patch.object(regions, 'observations', return_value=evidence):
            teacher = regions.analyze(self.repo, self.teacher(), {'window':['all']})
            cell = next(r for r in teacher['regions'] if r['id']=='kn-pep2019-r1-c04')['cells']['concept']
            self.assertEqual(0, cell['repeated']); self.assertEqual(0, cell['sufficient'])
            region = next(r for r in teacher['regions'] if r['id']=='kn-pep2019-r1-c04')
            detail = regions._evidence_html(region, teacher, self.user['id'], True, 'concept')
            self.assertIn('需要补查 · 1 道独立检查', detail)
            self.assertNotIn('反复受阻 · 2 道独立检查', detail)

    def test_null_legacy_mapping_recovers_display_but_empty_mapping_stays_empty(self):
        from tests.test_diagnosis import card
        catalogue = graph.catalog(self.c, self.user['school_id'])
        targets = {kind:next(n['id'] for n in catalogue if n['kind']==kind)
                   for kind in ('knowledge','ability','literacy')}
        graph.api(self.repo, self.teacher(), 'graph-card-save', dict(version=0,
            card_id=self.card_id, card=card(), mappings=[dict(name='旧检查目标',
            definition='限定本题的具体检查。', knowledge_id=targets['knowledge'],
            ability_id=targets['ability'], literacy_id=targets['literacy']) for _ in card()['steps']]))
        s = diagnosis.api(self.repo,self.user,'diagnosis-start',dict(self.p,
            protocol_version=1,mode='deep'))['state']
        diagnosis.api(self.repo,self.user,'diagnosis-event',dict(self.p,protocol_version=1,
            session_id=s['session_id'],cursor=0,event='answer',answer=0,request_key='old-event'))
        self.c.execute('update diagnostic_sessions set graph_mapping_json=null,effective_card_json=null,graph_release_id=null')
        self.c.commit(); before = dict(self.c.execute('select * from diagnostic_sessions').fetchone())
        evidence = regions.observations(self.c,self.user['school_id'],[self.user['id']])
        self.assertEqual(1,len(evidence)); self.assertFalse(evidence[0]['approved'])
        self.assertEqual(before,dict(self.c.execute('select * from diagnostic_sessions').fetchone()))
        self.c.execute("update diagnostic_sessions set graph_mapping_json='[]'");self.c.commit()
        self.assertEqual([],regions.observations(self.c,self.user['school_id'],[self.user['id']]))

    def test_scoped_read_only_pages_preserve_first_answers_pending_and_histories(self):
        from tests.http_support import seed_other_class
        seed_other_class(self.c); self.c.commit()
        originals = [tuple(r) for r in self.c.execute('select * from student_responses')]
        for params in ({'class_id':['class-physics-2']}, {'student_id':['stu-2001']},
                       {'assessment_id':['unknown']}):
            with self.assertRaises(PermissionDenied): regions.analyze(self.repo, self.teacher(), params)
        with self.assertRaises(PermissionDenied): regions.analyze(self.repo, self.user, {'student_id':[self.other['id']]})
        with self.assertRaises(PermissionDenied): regions.teacher_page(self.repo, self.user, {})
        data = regions.analyze(self.repo, self.user, {'window':['all']})
        self.assertTrue(any(r['outcome']=='correct' for r in data['records']))
        self.assertNotIn(self.other['id'], {r['student_id'] for r in data['records']})
        page = regions.student_page(self.repo, self.user, {'window':['all']})
        self.assertIn('全部学习区域', page)
        teacher_page = regions.teacher_page(self.repo, self.teacher(), {'window':['all']})
        self.assertNotIn('0 / 0 人反复失分', teacher_page)
        self.assertEqual(originals, [tuple(r) for r in self.c.execute('select * from student_responses')])
        self.assertEqual(0, self.c.execute('select count(*) from redo_attempts').fetchone()[0])
        self.assertEqual(0, self.c.execute('select count(*) from diagnostic_sessions').fetchone()[0])

    def test_install_is_exact_and_idempotent_without_changing_source_evidence(self):
        card = dict(self.c.execute('select * from diagnostic_cards').fetchone())
        manifest = dict(version='fixture-goals', goals=[dict(id='fixture-goal', name='合力判断',
            definition='在指定对象中辨别合力', conditions='惯性参考系')], cards=[dict(
            question_id=card['question_id'], fingerprint=card['fingerprint'], content_hash=goals.content_hash(self.content),
            bindings=[dict(goal_id='fixture-goal', knowledge_id='kn-pep2019-r1-c04-s03',
                           source='fixture', prompt=a['prompt'], context='测试') for a in self.content['actions']])])
        before = [tuple(r) for r in self.c.execute('select * from diagnostic_cards')]
        with patch.object(Path, 'read_text', return_value=json.dumps(manifest)):
            goals.install(self.c); goals.install(self.c)
        self.assertEqual(1, self.c.execute('select count(*) from learning_graph_releases').fetchone()[0])
        self.assertEqual(before, [tuple(r) for r in self.c.execute('select * from diagnostic_cards')])
        mapping, _ = goals.session_mapping(self.c, card, self.content)
        self.assertEqual(3, len(mapping)); self.assertEqual('curated', mapping[0]['status'])
        changed = copy.deepcopy(self.content); changed['actions'][0]['prompt'] += '新版本'
        self.assertEqual([], goals.session_mapping(self.c, card, changed)[0])

    def test_parent_parts_and_revisions_keep_one_independent_task(self):
        from tests.test_student_learning import StudentLearningTests
        group = StudentLearningTests.grouped_fixture(self)
        data = regions.analyze(self.repo, self.user, {'window':['all']})
        region = next(r for r in data['regions'] if r['id']=='kn-pep2019-r1-c04')
        parts = [r for r in region['tasks'][self.user['id']] if r['family']=='group:student-group-fixture']
        self.assertEqual(3, len(parts)); self.assertEqual(2, sum(r['outcome']=='wrong' for r in parts))
        self.assertEqual(1, region['task_summary'][self.user['id']]['wrong'])
        self.assertEqual('group:student-group-fixture', regions.task_family(self.c,
            {'revision_id':group['revision_id']}, parts[0]['question_id']))

    def test_retiring_a_wrong_does_not_hide_historical_diagnostic_evidence(self):
        self.review(0); s = self.start(); self.event(s, 'probe', answer=0)
        before = regions.observations(self.c,self.user['school_id'],[self.user['id']])
        student_learning.api(self.repo,self.user,'wrong-mastered',{'wrong_id':self.w['id'],'confirmed':True})
        self.assertEqual(before, regions.observations(self.c,self.user['school_id'],[self.user['id']]))
        self.assertTrue(regions.evidence_url(before[0]).startswith('exams?id='))

    def test_manifest_matches_all_current_action_and_verification_prompts(self):
        root = Path(diagnosis.__file__).with_name('diagnostic_data')
        manifest = json.loads((root/'shared-goals-20261011.json').read_text())
        source = {c['question_id']:c for c in json.loads((root/'adaptive-reviewed-20261008.json').read_text())}
        valid = {r[0] for r in self.c.execute('select id from knowledge_nodes')}
        self.assertEqual(23, len(manifest['cards']))
        self.assertEqual(91, sum(len(c['bindings']) for c in manifest['cards']))
        for card in manifest['cards']:
            content = source[card['question_id']]['content']
            self.assertEqual(goals.content_hash(content), card['content_hash'])
            for action, binding in zip(content['actions'], card['bindings']):
                self.assertEqual(action['prompt'], binding['prompt'])
                self.assertEqual(action['verify']['prompt'], binding['verification']['prompt'])
                self.assertIn(binding['knowledge_id'], valid)


class LearningRegionsHTTPTests(unittest.TestCase):
    def test_student_teacher_navigation_proxy_assets_and_access_controls(self):
        from tests.http_support import LivePhysicsServer
        with tempfile.TemporaryDirectory() as folder:
            with LivePhysicsServer(Path(folder)/'school.sqlite3') as server:
                from highschoolphysics.db import connect
                from highschoolphysics import learning
                from highschoolphysics.repository import PhysicsRepository
                c = connect(Path(folder)/'school.sqlite3')
                repo = PhysicsRepository(c)
                repo.resolve_review_item('user-teacher-li','resp-1001-q2','C','核对')
                repo.grade_assessment('user-teacher-li','assess-week-1',publish=True)
                learning.migrate(c); c.close()
                _, student, _ = server.login('stu_1001', 'student123')
                _, teacher, _ = server.login('teacher_li', 'teacher123')
                for url, cookie, expected in [('/app', student, '错题本'),
                    ('/app?module=graph&window=all', student, '全部学习区域'),
                    ('/teacher?module=regions&window=all', teacher, '班级问题分布')]:
                    status, _, html = server.request('GET', url, headers={'Cookie':cookie,'X-Forwarded-Prefix':'/physics'})
                    self.assertEqual(200, status); self.assertIn(expected, html.decode())
                    if url == '/app':
                        self.assertNotIn('app?module=graph', html.decode())
                        self.assertNotIn('我的学习地图', html.decode())
                    else:
                        self.assertIn('assets/learning-regions.css', html.decode())
                self.assertEqual(403, server.request('GET', '/teacher?module=regions', headers={'Cookie':student})[0])
                self.assertEqual(403, server.request('GET', '/app?student_id=stu-1002', headers={'Cookie':student})[0])
                self.assertEqual(200, server.request('GET', '/assets/learning-regions.css')[0])
