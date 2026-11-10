"""Daily review tasks: turn a large wrong-question backlog into a few concrete items per day.

The spaced-review rule itself stays in ``learning.progress`` (1 → 3 → 7 days, three
independent verifications consolidate a question).  This module only decides *which*
due questions a student sees today, keeps that choice stable for the day, and offers a
similar bank question as an alternative verification once the original has been redone.
"""
import hashlib
import re
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import quote

from .errors import InvalidRequest, StateConflict
from .exam_views import esc
from .repository import loads, dumps
from . import learning

DEFAULT_SIZE = 3


def migrate(c):
    c.execute('''create table if not exists student_daily_plans(
        student_id text not null references users(id),plan_date text not null,
        items_json text not null default '[]',created_at text not null,updated_at text not null,
        primary key(student_id,plan_date))''')
    c.execute('''create table if not exists daily_similar_links(
        id text primary key,student_id text not null references users(id),
        wrong_id text not null,question_id text not null,
        trial_id text not null unique references student_bank_trials(id),created_at text not null)''')
    c.execute('create index if not exists idx_daily_similar_student_question on daily_similar_links(student_id,question_id)')


def today():
    return datetime.now(learning.TZ).date()


def similar_events(conn, student_id, question_id):
    """Submitted similar-question verifications linked to one wrong question."""
    try:
        return conn.execute('''select t.submitted_at,t.outcome from daily_similar_links l
            join student_bank_trials t on t.id=l.trial_id
            where l.student_id=? and l.question_id=? and t.outcome is not null and t.submitted_at is not null''',
            (student_id, question_id)).fetchall()
    except sqlite3.OperationalError:
        return []


def size(c, user):
    try:
        row = c.execute('select daily_limit from learning_settings where class_id=?', (user.get('class_id') or '',)).fetchone()
    except sqlite3.OperationalError:
        row = None
    return int(row[0]) if row else DEFAULT_SIZE


def _class_rates(c, user, groups):
    """Class first-attempt accuracy per (assessment, question) for the student's own class."""
    class_id = user.get('class_id') or ''
    wanted = {(w['assessment_id'], w['question_id']) for g in groups for w in g['members'] if not w['personal']}
    rates = {}
    for aid in sorted({a for a, _ in wanted}):
        rows = c.execute('''select r.question_id,
                avg(case when r.outcome='correct' then 1.0 else 0.0 end),count(*)
            from student_responses r
            join assessment_participants p on p.assessment_id=r.assessment_id and p.student_id=r.student_id
            join users u on u.id=r.student_id
            where r.assessment_id=? and p.status='present' and r.outcome in ('correct','wrong','blank')
              and (?='' or u.class_id=?)
            group by r.question_id''', (aid, class_id, class_id)).fetchall()
        for qid, rate, n in rows:
            if (aid, qid) in wanted and n:
                rates[(aid, qid)] = rate
    return rates


def _group_rate(g, rates):
    values = [rates[(w['assessment_id'], w['question_id'])] for w in g['members']
              if not w['personal'] and (w['assessment_id'], w['question_id']) in rates]
    return sum(values) / len(values) if values else None


def _started(g):
    return max((w['progress']['count'] for w in g['members']), default=0) >= 1


def pick(groups, rates, n, exclude=()):
    """Spaced reviews that are due come first; then the easiest points to win back."""
    pool = [g for g in groups if g['id'] not in exclude]
    pool.sort(key=lambda g: g['anchor'].get('created_at') or '', reverse=True)
    def key(g):
        if _started(g):
            return (0, min(w['progress']['due'] for w in g['members']), 0.0)
        rate = _group_rate(g, rates)
        return (1, '', -(rate if rate is not None else 0.5))
    pool.sort(key=key)
    return pool[:n]


def _find(groups, wid):
    return next((g for g in groups if any(w['id'] == wid for w in g['members'])), None)


def plan(repo, user, all_groups, due):
    c, uid, d = repo.conn, user['id'], str(today())
    row = c.execute('select items_json from student_daily_plans where student_id=? and plan_date=?', (uid, d)).fetchone()
    if row is not None:
        return loads(row[0], [])
    ids = [g['id'] for g in pick(due, _class_rates(c, user, due), size(c, user))]
    now = learning.now()
    c.execute('insert or ignore into student_daily_plans values(?,?,?,?,?)', (uid, d, dumps(ids), now, now))
    c.commit()
    return loads(c.execute('select items_json from student_daily_plans where student_id=? and plan_date=?', (uid, d)).fetchone()[0], [])


def _activity_dates(c, uid, question_ids=None):
    """Local dates of every independent practice submission (optionally limited to questions)."""
    filt, args = '', ()
    if question_ids is not None:
        if not question_ids:
            return set()
        filt = ' and %s in (' + ','.join('?' for _ in question_ids) + ')'
        args = tuple(question_ids)
    rows = c.execute('select a.submitted_at from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id where a.student_id=?'
                     + (filt % 'w.question_id' if filt else ''), (uid, *args)).fetchall()
    rows += c.execute('''select r.submitted_at from student_personal_redos r join student_personal_wrongs w on w.id=r.wrong_id
        join student_bank_trials t on t.id=w.trial_id where r.student_id=?''' + (filt % 't.question_id' if filt else ''), (uid, *args)).fetchall()
    if question_ids is None:
        rows += c.execute('select submitted_at from student_bank_trials where student_id=? and submitted_at is not null', (uid,)).fetchall()
    else:
        try:
            rows += c.execute('''select t.submitted_at from daily_similar_links l join student_bank_trials t on t.id=l.trial_id
                where l.student_id=? and t.submitted_at is not null''' + filt % 'l.question_id', (uid, *args)).fetchall()
        except sqlite3.OperationalError:
            pass
    return {learning.day(r[0]) for r in rows if r[0]}


def done_today(c, uid, g):
    return today() in _activity_dates(c, uid, [w['question_id'] for w in g['members']])


def streak(c, uid):
    dates = _activity_dates(c, uid)
    day = today() if today() in dates else today() - timedelta(days=1)
    count = 0
    while day in dates:
        count += 1
        day -= timedelta(days=1)
    return count


def _eligible_member(g):
    """A redone-once exam question that is due again can be verified with a similar question."""
    return next((w for w in g['members'] if not w['personal'] and w['progress']['count'] >= 1
                 and w['progress']['available']), None)


def similar_question(repo, user, w):
    c, uid = repo.conn, user['id']
    tags = loads(w['snapshot'].get('tag_snapshot_json'), []) if w.get('snapshot') else w.get('tag_list', [])
    know = {t['tag_id'] for t in tags if t.get('tag_type') == 'knowledge'}
    ability = {t['tag_id'] for t in tags if t.get('tag_type') == 'ability'}
    if not know:
        return None
    excluded = {w['question_id']}
    excluded.update(r[0] for r in c.execute('select question_id from wrong_questions where student_id=?', (uid,)))
    excluded.update(r[0] for r in c.execute('select question_id from student_bank_trials where student_id=?', (uid,)))
    group = c.execute('select group_id from question_content_bindings where question_id=?', (w['question_id'],)).fetchone()
    if group:
        excluded.update(r[0] for r in c.execute('select question_id from question_content_bindings where group_id=?', (group[0],)))
    unverified = {r[0] for r in c.execute('''select b.question_id from question_content_bindings b
        join question_content_groups g on g.id=b.group_id join question_content_revisions r on r.id=g.current_revision_id
        where r.review_state!='verified' ''')}
    scores = {}
    for qid, kind, tag in c.execute('''select qt.question_id,qt.tag_type,qt.tag_id from question_tags qt
            join questions q on q.id=qt.question_id
            where q.school_id=? and qt.enabled=1 and qt.tag_type in ('knowledge','ability')''', (user['school_id'],)):
        if qid in excluded or qid in unverified:
            continue
        k, a = scores.get(qid, (0, 0))
        scores[qid] = (k + (tag in know and kind == 'knowledge'), a + (tag in ability and kind == 'ability'))
    ranked = [(k * 3 + a, hashlib.sha256((uid + qid).encode()).hexdigest(), qid) for qid, (k, a) in scores.items() if k]
    if not ranked:
        return None
    ranked.sort(key=lambda r: (-r[0], r[1]))
    return ranked[0][2]


def api(repo, user, action, p, groups, due):
    """Runs inside student_learning.api's transaction."""
    from . import student_learning
    c, uid = repo.conn, user['id']
    key = str(p.get('request_key', ''))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key):
        raise InvalidRequest('缺少有效提交标识')
    if action == 'daily-more':
        ids = plan(repo, user, groups, due)
        current = [g for g in (_find(groups, i) for i in ids) if g]
        if any(not done_today(c, uid, g) for g in current):
            return {'url': 'app?module=today'}
        extra = [g['id'] for g in pick(due, _class_rates(c, user, due), size(c, user),
                                      exclude={w['id'] for g in current for w in g['members']} | set(ids))]
        if not extra:
            return {'url': 'app?module=today&empty=1'}
        c.execute('update student_daily_plans set items_json=?,updated_at=? where student_id=? and plan_date=?',
                  (dumps(ids + extra), learning.now(), uid, str(today())))
        return {'url': 'app?module=today'}
    if action == 'daily-similar':
        g = _find(groups, str(p.get('wrong_id', '')))
        if not g:
            raise StateConflict('错题不存在或已移出错题本')
        trial_key = 'similar-' + key
        old = c.execute('''select l.trial_id from daily_similar_links l join student_bank_trials t on t.id=l.trial_id
            where l.student_id=? and t.request_key=?''', (uid, trial_key)).fetchone()
        if old:
            return {'url': 'app?trial=' + quote(old[0]) + '&back=today'}
        w = _eligible_member(g)
        if not w:
            raise StateConflict('先完成一次原题订正，下次复测时再换相似题')
        qid = similar_question(repo, user, w)
        if not qid:
            raise StateConflict('题库中暂时没有考查内容相似的题，请重做原题')
        url = student_learning._api(repo, user, 'bank-start', {'question_id': qid, 'request_key': trial_key})['url']
        tid = url.split('trial=', 1)[1]
        c.execute('insert into daily_similar_links values(?,?,?,?,?,?)',
                  ('similar-' + hashlib.sha256((uid + tid).encode()).hexdigest()[:20], uid, w['id'], w['question_id'], tid, learning.now()))
        return {'url': 'app?trial=' + quote(tid) + '&back=today'}
    raise InvalidRequest('未知学生操作')


def _title(c, g):
    w = g['anchor']
    number = ('第 %s 题' % g['number']) if g.get('number') else '错题'
    if w['personal']:
        return '题库试做 · ' + number
    row = c.execute('select title from assessment_sessions where id=?', (w['assessment_id'],)).fetchone()
    return (row[0] + ' · ' if row else '') + number


def render(repo, user, all_groups, due, params):
    from .learning_views import form, hidden
    c, uid = repo.conn, user['id']
    value = lambda k: (params.get(k) or [''])[0]
    ids = plan(repo, user, all_groups, due)
    rates = _class_rates(c, user, [g for g in (_find(all_groups, i) for i in ids) if g])
    items = []
    for i in ids:
        g = _find(all_groups, i)
        if g and all(g is not item[0] for item in items):
            items.append((g, done_today(c, uid, g)))
    done = sum(1 for _, ok in items if ok)
    counts = [w['progress']['count'] for g in all_groups for w in g['members']]
    consolidated = sum(1 for n in counts if n >= 3)
    reviewing = sum(1 for n in counts if 1 <= n < 3)
    fresh = sum(1 for n in counts if n == 0)
    days = streak(c, uid)
    out = ['<section class="daily-tasks" aria-labelledby="daily-title">']
    out.append('<header class="daily-head"><div><h2 id="daily-title">今日任务</h2><p class="daily-sub">每天只做几道，做完就是进步。</p></div>')
    out.append('<div class="daily-progress" role="group" aria-label="今日进度"><strong>%s / %s</strong><span>已完成</span>'
               '<meter min="0" max="%s" value="%s">%s / %s</meter></div></header>' % (done, len(items), max(len(items), 1), done, done, len(items)))
    out.append('<ul class="daily-stats"><li><strong>%s</strong><span>连续练习天数</span></li><li><strong>%s</strong><span>已巩固</span></li>'
               '<li><strong>%s</strong><span>复测中</span></li><li><strong>%s</strong><span>待首次订正</span></li></ul>' % (days, consolidated, reviewing, fresh))
    if value('completed'):
        out.append('<p class="daily-status" role="status">本轮练习已提交。</p>')
    if value('empty'):
        out.append('<p class="daily-status" role="status">当前关注范围内没有更多到期的错题了。</p>')
    pending = [g for g, ok in items if not ok]
    if pending:
        rest = [g['id'] for g in pending[1:]]
        out.append('<a class="daily-start-all" href="app?practice=%s&amp;queue=%s&amp;back=today">%s</a>' % (
            quote(pending[0]['id']), quote(','.join(rest)), '开始今日任务' if not done else '继续今日任务'))
    elif items:
        out.append('<div class="daily-done"><p><strong>今天的任务完成了！</strong>坚持隔天复测，错题才会真正消掉。</p>')
        if any(g['id'] not in ids for g in due):
            out.append(form('daily-more', '<button type="submit">再来 %s 题</button>' % size(c, user)))
        out.append('</div>')
    else:
        out.append('<p class="daily-empty">今天没有到期的错题。可以去<a href="app?module=bank">题库</a>试做新题，或在<a href="app?module=starmap">知识星图</a>里看看薄弱星区。</p>')
    out.append('<ol class="daily-list">')
    for g, ok in items:
        top = max((w['progress']['count'] for w in g['members']), default=0)
        if ok:
            badge, reason = '今日已完成', '明天以后再按间隔复测'
        elif top >= 1:
            badge, reason = '间隔复测 · 第 %s / 3 次' % (top + 1), '上次做对了，今天到期再验证一次'
        else:
            badge = '首次订正'
            rate = _group_rate(g, rates)
            reason = ('同班 %d%% 的同学做对了，最容易拿回来的分' % round(rate * 100) if rate is not None and rate >= 0.6
                      else '同班 %d%% 的同学做对了' % round(rate * 100) if rate is not None else '你在题库试做时做错的题')
        names = []
        for t in g['anchor'].get('tag_list', []):
            if t.get('tag_type') == 'knowledge' and t.get('name') and t['name'] not in names:
                names.append(t['name'])
        actions = ''
        if not ok:
            actions = '<a class="daily-go" href="app?practice=%s&amp;back=today">%s</a>' % (quote(g['id']), '重做原题')
            if _eligible_member(g):
                actions += form('daily-similar', hidden('wrong_id', g['id']) + '<button type="submit" class="secondary">换一道相似题复测</button>')
        out.append('<li class="daily-item%s"><div class="daily-item-main"><span class="daily-badge">%s</span><h3>%s</h3><p>%s</p>%s</div><div class="daily-actions">%s</div></li>' % (
            ' is-done' if ok else '', esc(badge), esc(_title(c, g)), esc(reason),
            ('<p class="daily-tags">' + ''.join('<span class="pill">%s</span>' % esc(n) for n in names[:3]) + '</p>') if names else '', actions))
    out.append('</ol>')
    out.append('<details class="daily-rules"><summary>错题怎样才算"巩固"？</summary><ul>'
               '<li>每道错题需要<strong>隔天以上</strong>独立做对 <strong>3 次</strong>（间隔约 1 天、3 天、7 天）才算巩固；中途做错就从头开始。</li>'
               '<li>选择题由系统判对错；其他题型在纸上完成，查看答案后如实标注"做对了 / 做错了"。</li>'
               '<li>原题订正过一次后，之后的复测可以换一道考查内容相似的题库题，做对同样计数。</li>'
               '<li>今日任务优先安排到期的复测题，其次是同班多数同学做对、你却做错的题。</li></ul></details>')
    out.append('</section>')
    return ''.join(out)
