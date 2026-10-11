"""One explainable evidence model for private student and class learning maps.

Exam tags locate tasks that need checking, not the failed step in a complex task.
Only explicitly reviewed, pinned action bindings aggregate across questions.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from html import escape as esc
from urllib.parse import urlencode, quote

from .errors import InvalidRequest, PermissionDenied
from .learning_goals import STAGES, ACTION_STAGES
from .repository import loads

RULE_VERSION = 'learning-regions-v1'
LABELS = {
    'unobserved': '尚未观察', 'check': '需要补查', 'repeated': '反复受阻',
    'practice': '练后待验证', 'passed': '近期检查通过', 'mixed': '表现不一致',
}
OUTCOMES = {'correct': '首次答对', 'wrong': '首次答错', 'blank': '空白作答',
            'pending': '待确认'}


def evidence_url(evidence, teacher=False):
    if evidence.get('assessment_id'):
        return 'exams?id=' + quote(evidence['assessment_id']) + '#student-card-' + quote(evidence['student_id'])
    return 'app?module=bank'


def task_family(c, content, question_id):
    revision = (content or {}).get('revision_id')
    if revision:
        group = c.execute('select group_id from question_content_revisions where id=?', (revision,)).fetchone()
        if group:
            # Revisions and sibling parts of one item share a task identity.
            return 'group:' + group[0]
    return 'question:' + question_id


def _stamp(value):
    try:
        d = datetime.fromisoformat((value or '').replace('Z', '+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _window(params):
    value = (params.get('window') or ['90'])[0]
    if value not in ('30', '90', 'all'):
        raise InvalidRequest('请选择最近30天、90天或全部记录')
    cutoff = None if value == 'all' else datetime.now(timezone.utc) - timedelta(days=int(value))
    return value, cutoff


def _catalog(c, school):
    nodes = {r['id']: dict(r) for r in c.execute('select * from knowledge_nodes '
        'where school_id=? and enabled=1 and deleted_at is null', (school,))}
    def topic(kid):
        node = nodes.get(kid)
        seen = set()
        while node and node['level'] > 2 and node['id'] not in seen:
            seen.add(node['id'])
            parent = nodes.get(node.get('parent_id'))
            if not parent:
                break
            node = parent
        return node
    return nodes, topic


def _scope(repo, user, params):
    c = repo.conn
    if user.get('status', 'active') != 'active':
        raise PermissionDenied('需要有效的用户身份')
    if user['role'] == 'student':
        selected = (params.get('student_id') or [user['id']])[0]
        if selected != user['id']:
            raise PermissionDenied('只能查看自己的学习证据')
        students = {user['id']: dict(user)}
        classes = []
        class_id = ''
        assessments = [dict(r) for r in c.execute('select a.* from assessment_sessions a '
            'join assessment_participants p on p.assessment_id=a.id '
            "where p.student_id=? and a.school_id=? and a.grading_status='published' "
            'order by a.published_at desc,a.id', (user['id'], user['school_id']))]
    elif user['role'] in ('teacher', 'admin'):
        available = {r['id'] for r in repo.class_groups(user['id'])}
        classes = [dict(r) for r in c.execute('select id,name,grade from class_groups '
            'where school_id=? order by grade,name,id', (user['school_id'],)) if r['id'] in available]
        class_id = (params.get('class_id') or [classes[0]['id'] if classes else ''])[0]
        if class_id and class_id not in {r['id'] for r in classes}:
            raise PermissionDenied('不能查看未授权班级的学情')
        students = {r['id']: dict(r) for r in c.execute('select * from users where school_id=? '
            "and role='student' and status='active' and class_id=? order by student_no,id",
            (user['school_id'], class_id))}
        assessment_ids = {a['id'] for a in repo.assessment_overview(user['id'])}
        assessments = [dict(r) for r in c.execute('select distinct a.* from assessment_sessions a '
            'join assessment_participants p on p.assessment_id=a.id '
            'join users u on u.id=p.student_id '
            "where a.school_id=? and u.class_id=? and a.grading_status='published' "
            'order by a.published_at desc,a.id', (user['school_id'], class_id))
            if r['id'] in assessment_ids]
        selected = (params.get('student_id') or [''])[0]
        if selected and selected not in students:
            raise PermissionDenied('不能查看未授权学生的学情')
    else:
        raise PermissionDenied('需要学生、教师或管理员身份')
    assessment_id = (params.get('assessment_id') or [''])[0]
    if assessment_id and assessment_id not in {a['id'] for a in assessments}:
        raise PermissionDenied('不能查看未授权或未发布测评')
    return students, classes, class_id, assessments, assessment_id


def observations(c, school, student_ids, cutoff=None, assessment_ids=None):
    """Read all historical sessions, including corrected/retired wrong questions.

An old empty mapping remains empty. Later review never invents a historical
binding. Unknown answers and prerequisite probes cannot become goal failures.
"""
    if not student_ids:
        return []
    placeholders = ','.join('?' for _ in student_ids)
    rows = c.execute('select s.*,w.assessment_id,r.snapshot_id,a.grading_status '
        'from diagnostic_sessions s left join wrong_questions w on w.id=s.wrong_id '
        'left join student_responses r on r.id=w.response_id '
        'left join assessment_sessions a on a.id=w.assessment_id '
        f'where s.school_id=? and s.student_id in ({placeholders})', [school, *student_ids]).fetchall()
    out = []
    from .question_content import snapshot_content
    for row in rows:
        s = dict(row)
        if s['assessment_id']:
            if s['grading_status'] != 'published' or (assessment_ids is not None and s['assessment_id'] not in assessment_ids):
                continue
            participant = c.execute('select status from assessment_participants where assessment_id=? '
                'and student_id=?', (s['assessment_id'], s['student_id'])).fetchone()
            if not participant or participant[0] != 'present':
                continue
        elif assessment_ids is not None:
            # Class views deliberately use school work, not personal bank trials.
            continue
        maps = loads(s.get('graph_mapping_json'), [])
        frozen = loads(s.get('effective_card_json'), {})
        if s.get('graph_mapping_json') is None and s['protocol_version'] != 3:
            # Compatibility display only: recover a completely matching old
            # card from its first immutable publication. Never rewrite the
            # session or treat a retrospectively recovered binding as approved.
            from .learning_graph import session_card
            frozen = session_card(c, s)
            for old in c.execute('select id,graph_json from learning_graph_releases '
                                 'where school_id=? order by version', (school,)):
                entry = loads(old['graph_json'], {}).get('cards', {}).get(s['card_id'])
                if entry and entry['card'] == frozen:
                    maps = [dict(m, status='curated') for m in entry['mappings']]
                    s['graph_release_id'] = old['id']
                    break
        if not maps:
            continue
        events = [dict(r) for r in c.execute('select * from diagnostic_events where session_id=? '
            'order by created_at,rowid', (s['id'],))]
        results = [(e, loads(e['result_json'], {})) for e in events]
        blocked = next((int(r['node'][2:]) for e, r in results if e['action'] == 'probe'
            and r.get('node', '').startswith('a:') and r.get('passed') is not True), None)
        content = snapshot_content(c, s['snapshot_id'], school) if s['snapshot_id'] else None
        family = task_family(c, content, s['question_id'])
        for e, r in results:
            if s['protocol_version'] == 3:
                node = r.get('node', '')
                if e['action'] == 'probe' and node.startswith('a:'):
                    index = int(node[2:]); source = 'check'
                elif e['action'] in ('practice', 'verify') or (e['action'] == 'probe' and node == 'cue'):
                    if blocked is None:
                        continue
                    index = blocked; source = 'verify' if e['action'] == 'verify' else 'practice'
                else:
                    continue
            else:
                if e['action'] != 'answer':
                    continue
                index = e['step']; source = 'check'
            if not 0 <= index < len(maps) or not maps[index]:
                continue
            m = maps[index]
            if source == 'verify':
                m = m.get('verification', {})
                if not m:
                    # Historical checks without their own binding remain raw
                    # diagnosis evidence and cannot prove a different goal.
                    continue
            if not m.get('knowledge_id'):
                continue
            if s['protocol_version'] == 3:
                action = frozen.get('adaptive', {}).get('actions', [])
                if index >= len(action):
                    continue
                stage = m.get('stage', ACTION_STAGES[action[index]['kind']])
            else:
                steps = frozen.get('steps', [])
                if index >= len(steps):
                    continue
                stage = {'condition': 'model', 'model': 'model', 'plan': 'equation',
                         'execution': 'execution'}.get(steps[index]['stage'], 'model')
            date = r.get('at') or e['created_at']
            if cutoff and _stamp(date) < cutoff:
                continue
            # Delayed checks use an immutable, different task. Repeated attempts
            # at the same verification item never increase independent coverage.
            task = family
            if source == 'verify':
                a = frozen['adaptive']['actions'][index]
                verify = a.get('verify', frozen['adaptive']['verify'])
                task = 'verify:' + hashlib.sha256(verify['prompt'].encode()).hexdigest()
            approved = m.get('status') == 'approved' and m.get('goal_status', 'approved') == 'approved'
            out.append(dict(id=e['id'], student_id=s['student_id'], question_id=s['question_id'],
                session_id=s['id'], wrong_id=s['wrong_id'], assessment_id=s['assessment_id'],
                goal_id=m.get('goal_id') or m['node_id'], node_id=m.get('node_id', ''),
                name=m.get('goal_name') or m.get('objective_name', '本题检查目标'),
                definition=m.get('goal_definition') or m.get('objective_definition', ''),
                knowledge_id=m['knowledge_id'], stage=stage, date=date, source=source,
                passed=r.get('passed'), assisted=bool(r.get('assisted')) or source == 'practice',
                approved=approved, task=task, context=m.get('context', '本题'),
                version=s.get('graph_release_id'), conditions=m.get('conditions', ''),
                label=m.get('prompt') or '检查点 ' + str(index + 1)))
    return out


def goal_state(evidence):
    usable = sorted((e for e in evidence if e['approved']), key=lambda e: (_stamp(e['date']), e['id']))
    direct = {}
    for e in usable:
        if not e['assisted'] and e['passed'] is not None:
            # Preserve the first independent check of a task, not a helped retry.
            direct.setdefault(e['task'], e)
    failures = [e for e in direct.values() if e['passed'] is False]
    passes = [e for e in direct.values() if e['passed'] is True]
    latest = max(direct.values(), key=lambda e: (_stamp(e['date']), e['id']), default=None)
    practice = next((e for e in reversed(usable) if e['assisted'] and e['passed'] is True), None)
    if practice and (not latest or _stamp(practice['date']) > _stamp(latest['date'])):
        state = 'practice'
    elif latest and latest['source'] == 'verify' and latest['passed']:
        state = 'passed'
    elif failures and passes:
        state = 'mixed'
    elif len(failures) >= 2:
        state = 'repeated'
    elif failures:
        state = 'check'
    elif passes:
        state = 'passed'
    else:
        state = 'unobserved'
    return dict(state=state, label=LABELS[state], independent=len(direct),
        failed=len(failures), passed=len(passes), assisted=sum(e['assisted'] for e in usable),
        unreviewed=sum(not e['approved'] for e in evidence),
        uncertain=sum(e['passed'] is None for e in usable),
        sufficient=len(direct) >= 2, latest=latest['date'] if latest else None)


def analyze(repo, user, params=None):
    params = params or {}; c = repo.conn
    students, classes, class_id, assessments, assessment_id = _scope(repo, user, params)
    window, cutoff = _window(params)
    nodes, topic = _catalog(c, user['school_id'])
    selected = {a['id'] for a in assessments if not assessment_id or a['id'] == assessment_id}
    if cutoff:
        selected = {a['id'] for a in assessments if a['id'] in selected and
                    _stamp(a['published_at'] or a['created_at']) >= cutoff}
    regions = {n['id']: dict(id=n['id'], name=n['name'], sort=n['stable_code'],
        parent=n['parent_id'], tasks=defaultdict(list), goals=defaultdict(list))
        for n in nodes.values() if n['level'] == 2}
    records = []
    if students and selected:
        rows = c.execute('select r.*,s.tag_snapshot_json,s.position,'
            'a.title,a.published_at from student_responses r '
            'join question_version_snapshots s on s.id=r.snapshot_id '
            'join assessment_sessions a on a.id=r.assessment_id '
            'join assessment_participants p on p.assessment_id=a.id and p.student_id=r.student_id '
            "where r.school_id=? and a.school_id=? and a.grading_status='published' "
            "and p.status='present' order by a.published_at,r.id",
            (user['school_id'], user['school_id'])).fetchall()
        from .question_content import snapshot_content
        families = {}
        numbers = {}
        for row in rows:
            r = dict(row)
            if r['student_id'] not in students or r['assessment_id'] not in selected:
                continue
            if r['snapshot_id'] not in families:
                content = snapshot_content(c, r['snapshot_id'], user['school_id']) or {}
                families[r['snapshot_id']] = task_family(c, content, r['question_id'])
                numbers[r['snapshot_id']] = str(content.get('document', {}).get('number') or r['position']) + ' ' + content.get('child_label', '')
            rec = dict(id=r['id'], student_id=r['student_id'], question_id=r['question_id'],
                family=families[r['snapshot_id']], outcome=r['outcome'], initial_answer=r['initial_answer'],
                assessment_id=r['assessment_id'], title=r['title'], date=r['published_at'],
                number=numbers[r['snapshot_id']].strip())
            records.append(rec)
            tids = set()
            for tag in loads(r['tag_snapshot_json'], []):
                n = topic(tag.get('tag_id')) if tag.get('tag_type') == 'knowledge' else None
                if n and n['id'] in regions:
                    tids.add(n['id'])
            for tid in tids:
                regions[tid]['tasks'][r['student_id']].append(rec)
    obs = observations(c, user['school_id'], list(students), cutoff,
        selected if user['role'] != 'student' or assessment_id else None)
    combined = defaultdict(list)
    for e in obs:
        combined[(e['student_id'], e['goal_id'])].append(e)
    for e in obs:
        n = topic(e['knowledge_id'])
        if n and n['id'] in regions:
            regions[n['id']]['goals'][(e['student_id'], e['goal_id'])].append(e)
    from .learning_graph import latest
    release, graph = latest(c, user['school_id'])
    available_goals = defaultdict(set)
    for entry in graph.get('adaptive_bindings', {}).values():
        for b in entry['bindings']:
            if not b:
                continue
            n = topic(b['knowledge_id'])
            if n and n['id'] in regions:
                available_goals[n['id']].add(b['goal_id'])
    summary = []
    for rid, region in regions.items():
        goals = []
        for (sid, gid), es in region['goals'].items():
            local_stages = {e['stage'] for e in es}
            all_evidence = combined[(sid, gid)]
            g = dict(id=gid, student_id=sid, name=es[0]['name'], definition=es[0]['definition'],
                stage=es[0]['stage'], stages=local_stages, evidence=all_evidence, **goal_state(all_evidence))
            goals.append(g)
        task_summary = {}
        for sid, tasks in region['tasks'].items():
            families = defaultdict(list)
            for t in tasks:
                families[t['family']].append(t)
            counts = {k: sum(any(t['outcome'] == k for t in ts) for ts in families.values())
                      for k in OUTCOMES}
            eligible = sum(any(t['outcome'] in ('correct', 'wrong') for t in ts) for ts in families.values())
            task_summary[sid] = dict(questions=len(families), eligible=eligible, **counts)
        cells = {}
        for stage in STAGES:
            # Teacher stages count observations of that stage only. A shared
            # goal observed at another stage cannot inflate this cell.
            stage_goals = []
            for g in goals:
                if stage not in g['stages']:
                    continue
                evidence = [e for e in g['evidence'] if e['stage'] == stage]
                stage_goals.append(dict(g, evidence=evidence, **goal_state(evidence)))
            seen = {g['student_id'] for g in stage_goals if g['independent']}
            sufficient = {g['student_id'] for g in stage_goals if g['sufficient']}
            repeated = {g['student_id'] for g in stage_goals if g['state'] == 'repeated' and g['sufficient']}
            check = {g['student_id'] for g in stage_goals if g['state'] in ('check', 'mixed')}
            passed = {g['student_id'] for g in stage_goals if g['state'] == 'passed'}
            cells[stage] = dict(repeated=len(repeated), sufficient=len(sufficient),
                insufficient=len(students)-len(sufficient), observed=len(seen),
                check=len(check), passed=len(passed), goals=stage_goals)
        troubled = {sid for sid, t in task_summary.items() if t['wrong'] >= 2}
        observed = {sid for sid, t in task_summary.items() if t['eligible'] >= 2}
        missing = len(students)-len(observed)
        item = dict(id=rid, name=region['name'], parent=region['parent'], sort=region['sort'],
            goals=goals, tasks=dict(region['tasks']), task_summary=task_summary, cells=cells,
            observed=len(observed), troubled=len(troubled), insufficient=missing,
            available_goals=len(available_goals[rid]), active=bool(task_summary or goals))
        if user['role'] == 'student':
            ts = task_summary.get(user['id'], dict(questions=0, correct=0, wrong=0, blank=0, pending=0))
            states = {g['state'] for g in goals}
            item['state'] = 'repeated' if 'repeated' in states else 'practice' if 'practice' in states else \
                'mixed' if 'mixed' in states else 'check' if 'check' in states or ts['wrong'] or ts['blank'] else \
                'passed' if 'passed' in states else 'unobserved'
            item['task_counts'] = ts
            item['priority'] = ({'repeated': 0, 'practice': 1, 'mixed': 2, 'check': 3,
                                 'passed': 4, 'unobserved': 5}[item['state']], -ts['wrong'], -ts['questions'])
        summary.append(item)
    inputs = [(r['id'], r['outcome'], r['family']) for r in records]
    inputs += [(e['id'], e['version'], e['passed'], e['approved']) for e in obs]
    fingerprint = hashlib.sha256(json.dumps([RULE_VERSION, window, class_id, sorted(students),
        release['version'] if release else 0, sorted(selected), inputs],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    classified = {t['id'] for r in summary for tasks in r['tasks'].values() for t in tasks}
    return dict(regions=summary, students=students, classes=classes, class_id=class_id,
        assessments=assessments, assessment_id=assessment_id, window=window,
        observations=obs, records=records, version=release['version'] if release else 0,
        fingerprint=fingerprint, rules=RULE_VERSION,
        unclassified=[r for r in records if r['id'] not in classified])


def assets():
    return '<link rel="stylesheet" href="assets/learning-regions.css?v=20261011-v1">'


def _filters(data, teacher=False):
    out = ['<form method="get" class="region-filters"><input type="hidden" name="module" value="%s">' %
           ('regions' if teacher else 'graph')]
    if teacher:
        out.append('<label>班级<select name="class_id">')
        for c in data['classes']:
            out.append('<option value="%s"%s>%s</option>' % (esc(c['id']),
                ' selected' if c['id'] == data['class_id'] else '', esc(c['name'])))
        out.append('</select></label>')
    out.append('<label>记录范围<select name="window">')
    for value, label in (('30', '最近30天'), ('90', '最近90天'), ('all', '全部记录')):
        out.append('<option value="%s"%s>%s</option>' % (value,
            ' selected' if value == data['window'] else '', label))
    out.append('</select></label><label>测评<select name="assessment_id"><option value="">范围内全部测评</option>')
    for a in data['assessments']:
        out.append('<option value="%s"%s>%s</option>' % (esc(a['id']),
            ' selected' if a['id'] == data['assessment_id'] else '', esc(a['title'])))
    form = ''.join(out) + '</select></label><button>更新视图</button></form>'
    if teacher:
        return form
    scope = {'30':'最近30天', '90':'最近90天', 'all':'全部记录'}[data['window']]
    assessment = next((a['title'] for a in data['assessments'] if a['id'] == data['assessment_id']), '全部测评')
    return '<details class="region-filter-drawer"><summary>筛选记录 · %s · %s</summary>%s</details>' % (
        scope, esc(assessment), form)


def _teacher_url(data, **values):
    return 'teacher?' + urlencode(dict(module='regions', class_id=data['class_id'],
        window=data['window'], assessment_id=data['assessment_id'], **values)) + '#region-detail'


def _rule_html(data):
    return ('<details class="region-rules"><summary>怎样理解这些判断</summary><p>考试标签只定位需要补查的内容。'
        '不同题目中的具体动作，须有教师确认的对应关系才能合并。反复受阻至少来自两道不同任务的无提示检查；'
        '同一大题的小问和同一道题的重复检查不增加独立任务数。空白、待确认与无记录分别保留。'
        '练习和借助帮助完成不算独立通过；延迟再检查也只证明该目标的一次选择表现。</p>'
        '<p>共同目标可合并不同内容区域的检查，但只在实际出现观察的区域显示，'
        '不会自动推断其他区域。另题复查的目标对应须单独确认。</p>'
        '<p>状态与证据数量分别显示；不会沿关联线把相邻目标判为薄弱。规则版本：%s · '
        '图谱版本：%s · 本次证据指纹：<code>%s</code></p></details>' % (
            esc(data['rules']), data['version'], data['fingerprint'][:16]))


def _evidence_html(region, data, sid, teacher=False, stage=''):
    tasks = region['tasks'].get(sid, [])
    out = ['<details class="region-evidence"><summary>首次作答依据 · %s 条记录</summary>' % len(tasks)]
    for t in sorted(tasks, key=lambda t: (t['date'] or '', t['id']), reverse=True):
        out.append('<p><a href="exams?id=%s#student-card-%s">%s · 第%s题</a> · %s'
            '<br>首次答案：%s</p>' % (quote(t['assessment_id']), quote(sid), esc(t['title']),
            esc(str(t['number'])), OUTCOMES.get(t['outcome'], '待确认'),
            esc(t['initial_answer'] or ('未填写' if t['outcome'] == 'blank' else '无文字答案，查看原始证据'))))
    if not tasks:
        out.append('<p>此范围没有已发布的首次作答记录。</p>')
    out.append('</details>')
    pool = region['cells'][stage]['goals'] if stage else region['goals']
    goals = [g for g in pool if g['student_id'] == sid]
    for g in sorted(goals, key=lambda g: (-g['failed'], g['name'])):
        out.append('<details class="region-goal"><summary>%s · %s · %s 道独立检查</summary>' % (
            esc(g['name']), esc(g['label']), g['independent']))
        out.append('<p>%s</p><p>无提示通过 %s 道 · 受阻 %s 道 · 受帮助 %s 次 · '
            '对应待教师确认 %s 次 · 说不清 %s 次</p>' % (esc(g['definition']),
            g['passed'], g['failed'], g['assisted'], g['unreviewed'], g['uncertain']))
        for e in sorted(g['evidence'], key=lambda e: _stamp(e['date']), reverse=True):
            status = '说不清' if e['passed'] is None else '通过' if e['passed'] else '受阻'
            source = '延迟再检查' if e['source'] == 'verify' else '针对性练习' if e['source'] == 'practice' else '选择检查'
            url = evidence_url(e, teacher)
            out.append('<p><a href="%s">%s · %s</a>：%s%s%s<br>%s<br>适用条件：%s</p>' % (
                esc(url), esc(e['context']), source, status, '（借助帮助）' if e['assisted'] else '',
                '（目标对应待教师确认）' if not e['approved'] else '', esc(e['label']), esc(e['conditions'])))
        out.append('</details>')
    if not goals:
        out.append('<p>具体环节尚无检查证据；暂不能区分概念、建模、列式或计算困难。</p>')
    return ''.join(out)


def _next_action(repo, user, region):
    # This is a navigation suggestion only. No attempt or assignment is created.
    from .student_learning import group_wrongs
    from .diagnosis_adaptive import state as adaptive_state
    from .diagnosis import question_input, fingerprint
    ids = {t['question_id'] for t in region['tasks'].get(user['id'], []) if t['outcome'] in ('wrong', 'blank')}
    candidates = []
    for group in group_wrongs(repo, user):
        for w in group['members']:
            if w['question_id'] not in ids:
                continue
            practice_url = 'app?' + urlencode(dict(practice=group['id'], diagnosis_question=w['question_id'])) + '#diagnosis'
            session = repo.conn.execute('select * from diagnostic_sessions where school_id=? '
                'and student_id=? and wrong_id=? and question_id=?',
                (user['school_id'], user['id'], group['id'], w['question_id'])).fetchone()
            if session and session['protocol_version'] == 3:
                s = adaptive_state(repo.conn, dict(session))
                if s.get('verification', {}).get('ready') and not s['verification']['answered']:
                    return '完成到期的不同题面再检查', practice_url
                if s.get('verification', {}).get('available_at') and not s['verification']['answered']:
                    due = _stamp(s['verification']['available_at']).astimezone(timezone(timedelta(hours=8)))
                    candidates.append(('已练习；北京时间%s后再检查' % due.strftime('%m月%d日 %H:%M'),
                                       practice_url))
                elif s.get('exercise') and not s['exercise']['answered']:
                    return '完成针对性小练习', practice_url
            fp = fingerprint(question_input(repo, w['question_id'], trial=w if w.get('personal') else None,
                snapshot=None if w.get('personal') else w['snapshot']))
            if repo.conn.execute('select 1 from diagnostic_cards where school_id=? and question_id=? and fingerprint=?',
                                (user['school_id'], w['question_id'], fp)).fetchone():
                candidates.append(('打开一题，补查具体卡点', practice_url))
            else:
                candidates.append(('回看本区域的一道错题', 'app?practice=' + quote(group['id'])))
    if candidates:
        return candidates[0]
    return '选择一道不同题目检查', 'app?module=bank&knowledge=' + quote(region['id'])


def student_page(repo, user, params):
    data = analyze(repo, user, params)
    active = sorted((r for r in data['regions'] if r['active']), key=lambda r: r['priority'])
    selected = (params.get('region') or [''])[0]
    focus = next((r for r in data['regions'] if r['id'] == selected), None)
    if selected and not focus:
        raise InvalidRequest('学习区域不存在')
    out = ['<section class="learning-map"><h2>我的学习地图</h2>'
        '<p>先找到当前值得处理的区域，再看题目依据与下一步。已会的部分也会保留。</p>', _filters(data)]
    priorities = [r for r in active if r['state'] in ('repeated', 'mixed', 'check', 'practice')][:3]
    out.append('<h3>当前值得先处理</h3>')
    if not priorities:
        out.append('<p class="region-empty">当前没有足够线索列出待处理区域。尚未观察的内容保留在地图中；'
                   '可以选择不同题目补充检查。</p>')
    if data['unclassified']:
        out.append('<p>还有 %s 条首次作答记录尚未对应有效学习区域，暂不用于区域判断。'
                   '<a href="app?module=history">查看历史测试依据</a></p>' % len(data['unclassified']))
    for region in priorities:
        counts = region['task_counts']
        goals = sorted((g for g in region['goals'] if g['state'] in ('repeated', 'check', 'mixed', 'practice')),
                       key=lambda g: (-g['failed'], g['name']))
        title = region['name'] + ('：' + goals[0]['name'] if goals else '')
        text = LABELS[region['state']] if goals else '题目表现提示需要补查'
        out.append('<article class="region-priority"><h3>%s</h3><p class="region-state state-%s">%s</p>'
            '<p>首次答错涉及 %s 道不同任务；答对涉及 %s 道。空白 %s 道；待确认 %s 道。</p>' % (
            esc(title), region['state'], text, counts['wrong'], counts['correct'], counts['blank'], counts['pending']))
        if not goals:
            out.append('<p>这些题目涉及本区域，具体哪个环节受阻还需核对。</p>')
        passed = [g['name'] for g in region['goals'] if g['state'] == 'passed']
        if passed:
            out.append('<p>已有通过证据：%s。</p>' % esc('；'.join(passed[:2])))
        label, url = _next_action(repo, user, region)
        out.append('<a class="region-action" href="%s">%s</a>%s</article>' % (
            esc(url), esc(label), _evidence_html(region, data, user['id'])))
    out.append('<h3>全部学习区域</h3><p>“尚未观察”表示没有足够记录，不能据此判断薄弱。'
               '点击内容可查看具体目标与证据。</p><div class="region-directory">')
    by_parent = defaultdict(list)
    for r in data['regions']:
        by_parent[r['parent']].append(r)
    nodes, _ = _catalog(repo.conn, user['school_id'])
    for parent, regions in by_parent.items():
        out.append('<details%s><summary>%s</summary><ul>' % (' open' if any(r['active'] for r in regions) else '',
                   esc(nodes.get(parent, {}).get('name', '学习内容'))))
        for r in sorted(regions, key=lambda r: (r['sort'], r['id'])):
            label = LABELS[r['state']]
            if r['state'] == 'check' and not any(g['independent'] for g in r['goals']):
                label = '题目线索待补查'
            if r['state'] == 'unobserved' and r['task_counts']['correct']:
                label = '有首次答对证据'
            url = 'app?' + urlencode(dict(module='graph', window=data['window'],
                assessment_id=data['assessment_id'], region=r['id'])) + '#region-detail'
            out.append('<li><a href="%s"><span>%s</span><span class="region-state state-%s">%s</span></a></li>' % (
                       esc(url), esc(r['name']), r['state'], label))
        out.append('</ul></details>')
    out.append('</div>')
    if focus:
        out.append('<section id="region-detail" class="region-detail" tabindex="-1"><h3>%s</h3>%s</section>' % (
            esc(focus['name']), _evidence_html(focus, data, user['id'])))
    out.append(_rule_html(data) + '</section>')
    from .learning_graph import student_page as constellation
    out.append('<details class="region-constellation"><summary>展开知识关系图</summary>' + constellation(repo, user) + '</details>')
    return ''.join(out) + assets()


def teacher_page(repo, user, params):
    if user['role'] not in ('teacher', 'admin'):
        raise PermissionDenied('需要教师或管理员身份')
    data = analyze(repo, user, params)
    out = ['<section class="learning-map teacher-learning-map"><h2>班级问题分布</h2>'
        '<p>先看哪些内容反复失分，再区分具体学习环节。每格按学生人数统计。</p>', _filters(data, True)]
    if not data['students']:
        return ''.join(out) + '<p class="region-empty">当前班级没有可查看的在读学生。</p></section>' + assets()
    active = sorted((r for r in data['regions'] if r['active']), key=lambda r: (
        -sum(c['repeated'] for c in r['cells'].values()), -r['troubled'], r['sort'], r['name']))
    out.append('<p>当前班级 %s 人。分母为相应区域或环节至少有两道不同任务证据的学生；'
               '证据不足人数单列。一次错误先补查，不计为反复受阻。</p>' % len(data['students']))
    if not active:
        out.append('<p class="region-empty">当前范围尚无已发布、已标注内容的作答证据。'
                   '可以切换记录范围或测评；无记录不按零分处理。</p>')
    unreviewed = sum(not e['approved'] for e in data['observations'])
    if data['unclassified']:
        out.append('<p>有 %s 条首次作答记录缺少有效内容标签，暂未归入区域；'
                   '<a href="teacher?module=exams">核对测评与题目标签</a>。</p>' % len(data['unclassified']))
    out.append('<p><a href="teacher?module=graph">审核诊断卡与共同目标</a> · '
        '当前 %s 条检查记录的目标对应尚待教师确认。</p>' % unreviewed)
    out.append('<div class="region-matrix-scroll" tabindex="0" role="region" aria-label="班级问题分布表，可横向滚动">'
               '<table class="region-matrix"><caption>内容区域与学习环节（受阻人数 / 证据足够人数）</caption>'
               '<thead><tr><th scope="col">内容区域</th><th scope="col">首次作答线索</th>')
    for stage, name in STAGES.items():
        out.append('<th scope="col">%s</th>' % name)
    out.append('</tr></thead><tbody>')
    for region in active:
        out.append('<tr><th scope="row">%s</th><td><a href="%s">%s / %s 人反复失分</a>'
            '<small>证据不足 %s 人；具体环节待查</small></td>' % (esc(region['name']),
            esc(_teacher_url(data, region=region['id'])), region['troubled'], region['observed'], region['insufficient']))
        for stage in STAGES:
            cell = region['cells'][stage]
            label = '%s / %s 人反复受阻' % (cell['repeated'], cell['sufficient']) if cell['sufficient'] else '尚无足够检查'
            state = 'repeated' if cell['repeated'] else 'check' if cell['check'] else 'unobserved'
            out.append('<td class="state-%s"><a href="%s">%s</a><small>证据不足 %s 人；需补查 %s 人</small></td>' % (
                state, esc(_teacher_url(data, region=region['id'], stage=stage)), label,
                cell['insufficient'], cell['check']))
        out.append('</tr>')
    out.append('</tbody></table></div>')
    rid = (params.get('region') or [''])[0]
    stage = (params.get('stage') or [''])[0]
    if stage and stage not in STAGES:
        raise InvalidRequest('学习环节不存在')
    region = next((r for r in data['regions'] if r['id'] == rid), None)
    if rid and not region:
        raise InvalidRequest('学习区域不存在')
    if region:
        out.append('<section id="region-detail" class="region-detail" tabindex="-1"><h3>%s%s</h3>' % (
            esc(region['name']), ' · ' + STAGES[stage] if stage else ''))
        groups = defaultdict(list)
        for sid, student in data['students'].items():
            if stage:
                goals = [g for g in region['cells'][stage]['goals'] if g['student_id'] == sid]
                states = {g['state'] for g in goals}
                state = 'repeated' if 'repeated' in states else 'practice' if 'practice' in states else \
                    'check' if states & {'check', 'mixed'} else 'passed' if 'passed' in states else 'unobserved'
                label = LABELS[state]
            else:
                counts = region['task_summary'].get(sid, {})
                label = '反复失分，环节待查' if counts.get('wrong', 0) >= 2 else \
                    '有失分或空白，需补查' if counts.get('wrong') or counts.get('blank') else \
                    '有首次答对证据' if counts.get('correct') else '证据不足或尚未观察'
            groups[label].append((sid, student))
        out.append('<p>按证据安排共同回顾、小组支持或个别再检查；下方名单只用于本班教学。'
                   '点击学生展开原始作答与具体目标。</p>')
        for label, members in groups.items():
            out.append('<details class="region-student-group" open><summary>%s · %s 人</summary>' % (label, len(members)))
            for sid, student in members:
                out.append('<details><summary>%s · %s</summary>%s</details>' % (
                    esc(student['display_name']), esc(student.get('student_no') or ''),
                    _evidence_html(region, data, sid, True, stage)))
            out.append('</details>')
        out.append('</section>')
    return ''.join(out) + _rule_html(data) + '</section>' + assets()
