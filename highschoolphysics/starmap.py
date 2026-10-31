"""Knowledge star map: knowledge points and abilities coloured by first-attempt evidence.

Every published, present first attempt counts once per task family. A question
credits each knowledge tag (and its textbook ancestors) and each ability tag.
Students are compared with their own class on exactly the same tasks, which
removes question difficulty from the colour. Links come from the curriculum
tree and from tags that occur together in published questions.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape as esc
from itertools import combinations
import json
from urllib.parse import quote

from .errors import InvalidRequest, PermissionDenied
from .repository import loads

ASSET_VERSION = '20261031-starmap-v1'
WINDOWS = (('all', '全部测评'), ('90', '最近90天'), ('30', '最近30天'))


def _window(params):
    value = (params.get('window') or ['all'])[0]
    if value not in ('all', '90', '30'):
        raise InvalidRequest('请选择全部测评、最近90天或最近30天')
    if value == 'all':
        return value, None
    return value, datetime.now(timezone.utc) - timedelta(days=int(value))


def _nodes(c, school):
    nodes = []
    for r in c.execute('select id,name,level,parent_id,stable_code from knowledge_nodes '
                       'where school_id=? and enabled=1 and deleted_at is null', (school,)):
        nodes.append(dict(id=r['id'], kind='knowledge', name=r['name'], level=r['level'],
                          parent=r['parent_id'], sort=r['stable_code'] or r['id']))
    for r in c.execute('select id,name,stable_code from ability_tags '
                       'where school_id=? and enabled=1 and deleted_at is null', (school,)):
        nodes.append(dict(id=r['id'], kind='ability', name=r['name'], level=0, parent=None,
                          sort=r['stable_code'] or r['id']))
    return nodes


def _scope(repo, user, params):
    """Return (students in the comparison class, viewed student id or None, classes, class id)."""
    c = repo.conn
    value = lambda k: (params.get(k) or [''])[0]
    if user.get('status', 'active') != 'active':
        raise PermissionDenied('需要有效的用户身份')
    if user['role'] == 'student':
        if value('student_id') and value('student_id') != user['id']:
            raise PermissionDenied('只能查看自己的学习证据')
        class_id = user.get('class_id') or ''
        peers = {r['id']: dict(r) for r in c.execute(
            "select id,display_name,student_no from users where school_id=? and role='student' "
            "and status='active' and class_id=?", (user['school_id'], class_id))} if class_id else {}
        peers[user['id']] = dict(id=user['id'], display_name=user.get('display_name', ''), student_no='')
        return peers, user['id'], [], class_id
    if user['role'] not in ('teacher', 'admin'):
        raise PermissionDenied('需要学生、教师或管理员身份')
    available = {r['id'] for r in repo.class_groups(user['id'])}
    classes = [dict(r) for r in c.execute(
        'select g.id,g.name,g.grade from class_groups g where g.school_id=? order by '
        "exists(select 1 from users u where u.class_id=g.id and u.role='student' and u.status='active') desc,"
        'grade,name,id', (user['school_id'],)) if r['id'] in available]
    class_id = value('class_id') or (classes[0]['id'] if classes else '')
    if class_id and class_id not in {r['id'] for r in classes}:
        raise PermissionDenied('不能查看未授权班级的学情')
    peers = {r['id']: dict(r) for r in c.execute(
        "select id,display_name,student_no from users where school_id=? and role='student' "
        "and status='active' and class_id=? order by student_no,id", (user['school_id'], class_id))}
    selected = value('student_id') or None
    if selected and selected not in peers:
        raise PermissionDenied('不能查看未授权学生的学情')
    return peers, selected, classes, class_id


def build(repo, user, params=None):
    params = params or {}
    c = repo.conn
    school = user['school_id']
    peers, viewed, classes, class_id = _scope(repo, user, params)
    window, cutoff = _window(params)
    nodes = _nodes(c, school)
    ids = {n['id'] for n in nodes}
    from .learning_regions import _stamp, task_family
    from .question_content import snapshot_content

    published = {r['id']: dict(r) for r in c.execute(
        "select id,title,published_at,created_at from assessment_sessions "
        "where school_id=? and grading_status='published'", (school,))
        if not cutoff or _stamp(r['published_at'] or r['created_at']) >= cutoff}
    snaps = {}
    for r in c.execute('select s.id,s.question_id,s.assessment_id,s.position,s.tag_snapshot_json '
                       'from question_version_snapshots s join assessment_sessions a on a.id=s.assessment_id '
                       "where a.school_id=? and a.grading_status='published'", (school,)):
        tags = loads(r['tag_snapshot_json'], [])
        k = sorted({t.get('tag_id') for t in tags if t.get('tag_type') == 'knowledge' and t.get('tag_id') in ids})
        a = sorted({t.get('tag_id') for t in tags if t.get('tag_type') == 'ability' and t.get('tag_id') in ids})
        snaps[r['id']] = dict(question_id=r['question_id'], assessment_id=r['assessment_id'],
                              position=r['position'], k=k, a=a)

    # Links: question co-occurrence across all published school questions, one vote per task family.
    families = {}
    def family(sid):
        if sid not in families:
            s = snaps[sid]
            content = snapshot_content(c, sid, school) or {}
            number = str((content.get('document') or {}).get('number') or s['position'])
            label = (number + ' ' + (content.get('child_label') or '')).strip()
            families[sid] = (task_family(c, content, s['question_id']), label)
        return families[sid]
    linked = {}
    for sid, s in snaps.items():
        if s['k'] or s['a']:
            linked[family(sid)[0] + '@' + s['assessment_id']] = s
    kk, ka, aa = defaultdict(int), defaultdict(int), defaultdict(int)
    for s in linked.values():
        for p in combinations(s['k'], 2): kk[p] += 1
        for p in combinations(s['a'], 2): aa[p] += 1
        for x in s['k']:
            for y in s['a']: ka[(x, y)] += 1
    edges = [dict(s=a, t=b, kind='cotest', w=n) for (a, b), n in kk.items()]
    edges += [dict(s=a, t=b, kind='ability', w=n) for (a, b), n in ka.items()]
    edges += [dict(s=a, t=b, kind='ability', w=n, aa=1) for (a, b), n in aa.items() if n >= 3]

    # First attempts of the comparison class, one per student and task.
    first = {}
    if peers:
        marks = ','.join('?' for _ in peers)
        for r in c.execute(
                'select r.id,r.student_id,r.snapshot_id,r.outcome,r.assessment_id from student_responses r '
                'join assessment_participants p on p.assessment_id=r.assessment_id and p.student_id=r.student_id '
                f"where r.school_id=? and p.status='present' and r.student_id in ({marks}) "
                "and r.outcome in ('correct','wrong','blank') order by r.id", [school, *peers]):
            if r['assessment_id'] not in published or r['snapshot_id'] not in snaps:
                continue
            s = snaps[r['snapshot_id']]
            if not (s['k'] or s['a']):
                continue
            key = family(r['snapshot_id'])[0] + '@' + r['assessment_id']
            first.setdefault((r['student_id'], key), (r['id'], r['snapshot_id'], r['outcome']))

    wrong_by_response = {}
    if user['role'] == 'student' and viewed:
        wrong_by_response = {r['response_id']: r['id'] for r in c.execute(
            'select id,response_id from wrong_questions where school_id=? and student_id=? and is_active=1 '
            'and id not in (select wrong_id from student_mastered_wrongs where student_id=?)',
            (school, viewed, viewed)) if r['response_id']}

    tally = defaultdict(lambda: [0, 0])
    for (sid, key), (_, snap_id, outcome) in first.items():
        tally[key][0] += outcome == 'correct'
        tally[key][1] += 1
    questions = {}
    def question(key, snap_id, response_id=None):
        if key not in questions:
            s = snaps[snap_id]
            a = published[s['assessment_id']]
            right, total = tally[key]
            if response_id and response_id in wrong_by_response:
                url = 'app?practice=' + quote(wrong_by_response[response_id])
            else:
                url = 'exams?id=' + quote(s['assessment_id'])
                if viewed:
                    url += '#student-card-' + quote(viewed)
            questions[key] = dict(title=a['title'], no=family(snap_id)[1], k=s['k'], a=s['a'],
                                  cr=round(right / total, 3) if total else None, url=url,
                                  date=(a['published_at'] or a['created_at'] or '')[:10])
        return key
    records = []
    if viewed:
        for (sid, key), (resp_id, snap_id, outcome) in first.items():
            if sid == viewed:
                records.append([question(key, snap_id, resp_id), outcome[0]])
    else:
        seen = set()
        for (sid, key), (_, snap_id, _) in first.items():
            if key not in seen:
                seen.add(key)
                right, total = tally[key]
                records.append([question(key, snap_id), right, total])
    records.sort(key=lambda r: (questions[r[0]]['date'], r[0]))

    if viewed:
        person = peers.get(viewed, {})
        label = '我的知识星图' if user['role'] == 'student' else (person.get('display_name') or '学生') + ' 的知识星图'
    else:
        label = '班级整体星图'
    class_name = next((x['name'] for x in classes if x['id'] == class_id), '')
    return dict(nodes=nodes, edges=edges, questions=questions, records=records,
                mode='student' if viewed else 'class', label=label, audience=user['role'],
                class_size=len(peers), class_name=class_name, classes=classes, class_id=class_id,
                students=[dict(id=k, name=v.get('display_name') or '', no=v.get('student_no') or '')
                          for k, v in peers.items()] if user['role'] != 'student' else [],
                viewed=viewed or '', window=window,
                assessments=len({questions[r[0]]['title'] for r in records}))


def assets():
    return ('<link rel="stylesheet" href="assets/starmap.css?v=%s">'
            '<script src="assets/starmap.js?v=%s" defer></script>' % (ASSET_VERSION, ASSET_VERSION))


def _picker(data, teacher):
    if not teacher:
        return ''
    out = ['<form method="get" class="starmap-picker"><input type="hidden" name="module" value="starmap">'
           '<label>班级<select name="class_id" data-starmap-autosubmit>']
    for g in data['classes']:
        out.append('<option value="%s"%s>%s</option>' % (esc(g['id']), ' selected' if g['id'] == data['class_id'] else '',
                                                       esc((g.get('grade') or '') + ' ' + g['name']).strip()))
    out.append('</select></label><label>查看<select name="student_id" data-starmap-autosubmit>'
               '<option value="">全班整体</option>')
    for s in data['students']:
        out.append('<option value="%s"%s>%s</option>' % (esc(s['id']), ' selected' if s['id'] == data['viewed'] else '',
                                                       esc((s['no'] + ' ' + s['name']).strip())))
    out.append('</select></label><label>范围<select name="window" data-starmap-autosubmit>')
    for k, label in WINDOWS:
        out.append('<option value="%s"%s>%s</option>' % (k, ' selected' if k == data['window'] else '', label))
    out.append('</select></label><noscript><button>查看</button></noscript></form>')
    return ''.join(out)


def page(repo, user, params=None):
    data = build(repo, user, params)
    teacher = user['role'] != 'student'
    encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('&', '\\u0026')
    empty = '' if data['records'] else ('<p class="starmap-empty-note">还没有已发布测评的作答记录，星星暂时都是暗色。'
                                        '测评发布后，这里会按作答情况自动点亮。</p>')
    noscript = ''.join('<li>%s</li>' % esc(n['name']) for n in data['nodes'] if n['kind'] == 'ability' or n.get('level') == 2)
    return ('<section class="starmap" data-starmap aria-label="知识星图">'
            '<header class="starmap-head"><div><h2>知识星图</h2><p>每颗星是一个知识点或能力，颜色表示作答表现，'
            '连线表示知识与能力之间的联系。</p></div>' + _picker(data, teacher) + '</header>' + empty +
            '<div class="starmap-toolbar">'
            '<div class="starmap-seg" role="group" aria-label="着色口径" data-starmap-modes>'
            '<button type="button" data-mode="relative" aria-pressed="true" title="与同班同学做同样题目时的正确率比较，排除试题难度影响">对比同班同题</button>'
            '<button type="button" data-mode="absolute" aria-pressed="false" title="直接按正确率着色">按正确率</button></div>'
            '<div class="starmap-search"><label>查找<input type="search" data-starmap-search placeholder="搜索知识点或能力" autocomplete="off" maxlength="50"></label>'
            '<div class="starmap-results" data-starmap-results hidden></div></div>'
            '<button type="button" class="secondary" data-starmap-reset>全景</button></div>'
            '<div class="starmap-body"><div class="starmap-stage" data-starmap-stage>'
            '<canvas data-starmap-canvas role="img" aria-label="知识星图画布：拖动平移，滚轮或双指缩放，点击星星查看详情；右侧列表提供同样内容"></canvas>'
            '<div class="starmap-tip" data-starmap-tip hidden></div>'
            '<div class="starmap-overlay starmap-layers" role="group" aria-label="连线图层"><span>连线</span>'
            '<label><input type="checkbox" data-layer="tree" checked><i class="line tree"></i>教材脉络</label>'
            '<label><input type="checkbox" data-layer="cotest" checked><i class="line cotest"></i>知识关联</label>'
            '<label><input type="checkbox" data-layer="ability" checked><i class="line ability"></i>能力关联</label></div>'
            '<div class="starmap-overlay starmap-legend"><div><span><i class="dot weak"></i>薄弱</span><span><i class="dot shaky"></i>待巩固</span>'
            '<span><i class="dot solid"></i>掌握较好</span><span><i class="dot none"></i>题目不足/未考</span></div>'
            '<div class="starmap-legend-shapes"><span>● 知识点（越大题越多）</span><span>✦ 能力</span><span>◎ 章节/教材</span><span>星云 = 章节整体</span></div></div>'
            '<div class="starmap-overlay starmap-zoom"><button type="button" data-zoom="in" aria-label="放大">＋</button>'
            '<button type="button" data-zoom="out" aria-label="缩小">−</button></div>'
            '</div><aside class="starmap-panel" data-starmap-panel tabindex="-1" aria-live="polite"></aside></div>'
            '<noscript><p>启用脚本后可查看知识星图。章节与能力：</p><ul>' + noscript + '</ul></noscript>'
            '<script type="application/json" data-starmap-data>' + encoded + '</script></section>' + assets())
