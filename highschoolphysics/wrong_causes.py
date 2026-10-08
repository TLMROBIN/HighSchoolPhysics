"""Student-selected loss reasons, independent of teacher tags and diagnosis findings."""
import hashlib
import json
import re

from .diagnosis import dumps, now
from .errors import InvalidRequest, StateConflict
from .exam_views import esc

CAUSES = {
    'knowledge_forgotten': '知识没记住',
    'knowledge_unclear': '概念没理解',
    'knowledge_retrieval': '记得，但没想到用',
    'condition_omission': '漏看关键条件',
    'condition_decoding': '条件没读懂',
    'model_identification': '模型没认出',
    'knowledge_organization': '思路接不下去',
    'formula_application': '公式用错',
    'calculation': '计算、符号或单位错',
    'transcription': '看错或抄错数据',
    'expression': '步骤或理由没写清',
    'result_check': '结果没检查',
    'ran_out': '时间不够或没做完',
    'omitted': '会做，但漏答了',
    'unsure': '还不确定',
}


def migrate(c):
    row = c.execute("select version from app_schema_migrations where feature='student_wrong_causes'").fetchone()
    if row and row[0] > 20:
        raise RuntimeError('Student wrong causes schema is newer than this application')
    c.execute('''create table if not exists student_wrong_cause_events(
        student_id text not null references users(id), school_id text not null,
        scope_key text not null, group_key text not null, sources_json text not null,
        revision integer not null, causes_json text not null,
        request_key text not null, created_at text not null,
        primary key(student_id,scope_key,revision), unique(student_id,request_key))''')
    c.execute("insert or ignore into app_schema_migrations(feature,version) values('student_wrong_causes',20)")
    c.commit()


def scope(group):
    # A new source answer or question snapshot must not inherit an old self-report.
    sources = sorted((w['id'], w['question_id'], w['trial_id'] if w['personal'] else w['snapshot']['id']) for w in group['members'])
    frozen = dumps(sources)
    return hashlib.sha256(frozen.encode()).hexdigest(), frozen


def state(c, user, group):
    key, _ = scope(group)
    row = c.execute('''select revision,causes_json from student_wrong_cause_events
        where student_id=? and school_id=? and scope_key=? order by revision desc limit 1''',
        (user['id'], user['school_id'], key)).fetchone()
    return dict(scope_key=key, revision=row['revision'] if row else 0,
                causes=json.loads(row['causes_json']) if row else [])


def save(repo, user, p):
    # Caller holds the student-learning write transaction and validates the role.
    from .student_learning import group_for
    group = group_for(repo, user, p.get('wrong_id', ''))
    key, sources = scope(group)
    if p.get('scope_key') != key:
        raise StateConflict('这道错题的记录已变化，请刷新后再选')
    causes = p.get('causes')
    if not isinstance(causes, list) or len(causes) > len(CAUSES) or any(not isinstance(x, str) or x not in CAUSES for x in causes):
        raise InvalidRequest('请选择列表中的失分原因')
    causes = [x for x in CAUSES if x in causes]
    if 'unsure' in causes and len(causes) > 1:
        raise InvalidRequest('“还不确定”不能与具体原因同时选择')
    revision = p.get('revision')
    if type(revision) is not int or revision < 0:
        raise InvalidRequest('缺少有效的记录版本，请刷新')
    request_key = p.get('request_key')
    if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_key):
        raise InvalidRequest('缺少有效的提交标识')
    old = repo.conn.execute('select * from student_wrong_cause_events where student_id=? and request_key=?',
                            (user['id'], request_key)).fetchone()
    if old:
        if old['scope_key'] != key or old['causes_json'] != dumps(causes) or old['revision'] != revision + 1:
            raise StateConflict('重复请求内容不一致')
        return state(repo.conn, user, group)
    current = state(repo.conn, user, group)
    if current['revision'] != revision:
        raise StateConflict('此题的原因已在另一页面更新，请刷新后再选')
    repo.conn.execute('insert into student_wrong_cause_events values(?,?,?,?,?,?,?,?,?)',
                      (user['id'], user['school_id'], key, group['key'], sources,
                       revision + 1, dumps(causes), request_key, now()))
    return state(repo.conn, user, group)


def panel(c, user, group):
    current = state(c, user, group)
    buttons = ''.join('<button type="button" class="wrong-cause-chip" data-cause="%s" aria-pressed="%s">%s</button>' %
                      (code, 'true' if code in current['causes'] else 'false', esc(label)) for code, label in CAUSES.items())
    status = '已保存：' + '、'.join(CAUSES[x] for x in current['causes']) if current['causes'] else '尚未标记'
    return '''<section class="wrong-causes" data-wrong-causes="%s" data-scope-key="%s" data-revision="%s"
        aria-label="我认为的失分原因"><h4>我认为的失分原因</h4>
        <p>可多选，点选即保存，再点取消。记录你的判断；不清楚时可选“还不确定”。</p>
        <div class="wrong-cause-options">%s</div><p role="status" aria-live="polite">%s</p></section>''' % (
            esc(group['id']), current['scope_key'], current['revision'], buttons, esc(status))
