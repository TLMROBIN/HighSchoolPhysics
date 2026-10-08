"""Student workspace and personal review evidence, separate from imported exam answers."""
import json
import hashlib
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from .errors import InvalidRequest, PermissionDenied, ResourceNotFound, StateConflict
from .repository import loads, dumps
from . import learning, question_bank
from .question_difficulty import statistics, badge
from .question_rendering import render_question, render_markdown
from .question_content import render_snapshot_solution, snapshot_content
from .exam_views import esc

TYPES = {'single_choice': '单选题', 'multiple_choice': '多选题', 'fill': '填空题',
         'experiment': '实验题', 'structured': '解答题', 'short_answer': '简答题'}
LEVELS = ('送分', '基础', '巩固', '拔高', '挑战', '未分级')
KINDS = {'knowledge': '知识点', 'ability': '能力点', 'literacy': '素养点'}
CHOICE = ('single_choice', 'multiple_choice')


def migrate(c):
    c.execute('''create table if not exists student_learning_preferences(
        student_id text primary key references users(id),types_json text not null default '[]',
        levels_json text not null default '[]',updated_at text not null)''')
    c.execute('''create table if not exists student_bank_trials(
        id text primary key,school_id text not null,student_id text not null references users(id),
        question_id text not null references questions(id),question_version integer not null,
        revision_id text,child_key text not null default '',document_json text not null,
        rule_json text not null,tags_json text not null,question_type text not null,
        answer text not null default '',outcome text,viewed_at text,submitted_at text,
        request_key text not null,unique(student_id,request_key))''')
    c.execute('''create table if not exists student_personal_wrongs(
        id text primary key,student_id text not null references users(id),
        trial_id text not null unique references student_bank_trials(id),created_at text not null)''')
    c.execute('''create table if not exists student_personal_redos(
        id text primary key,student_id text not null references users(id),
        wrong_id text not null references student_personal_wrongs(id),answer text not null,
        outcome text not null,self_reported integer not null default 0,submitted_at text not null,
        request_key text not null,unique(student_id,request_key))''')
    c.execute('''create table if not exists student_mastered_wrongs(
        student_id text not null references users(id),wrong_id text not null,
        mastered_at text not null,primary key(student_id,wrong_id))''')
    personal_cols = {r['name'] for r in c.execute('pragma table_info(student_personal_redos)')}
    if 'purpose' not in personal_cols:
        c.execute("alter table student_personal_redos add column purpose text not null default 'verify'")
    cols = {r['name'] for r in c.execute('pragma table_info(redo_attempts)')}
    if 'self_reported' not in cols:
        c.execute('alter table redo_attempts add column self_reported integer not null default 0')
    c.commit()


def require_student(user):
    if user['role'] != 'student' or user.get('status', 'active') != 'active':
        raise PermissionDenied('需要有效的学生身份')


def owned_trial(c, user, tid):
    require_student(user)
    row = c.execute('select * from student_bank_trials where id=? and student_id=? and school_id=?',
                    (tid, user['id'], user['school_id'])).fetchone()
    if not row:
        raise ResourceNotFound('试做记录不存在')
    return dict(row)


def own_question(repo, user, qid):
    require_student(user)
    q = repo.get_question(qid)
    if not q or q['school_id'] != user['school_id']:
        raise ResourceNotFound('题目不存在')
    return q


def preferences(c, uid):
    r = c.execute('select * from student_learning_preferences where student_id=?', (uid,)).fetchone()
    return (loads(r['types_json'], []), loads(r['levels_json'], [])) if r else ([], [])


def frozen_question(repo, user, qid):
    q = own_question(repo, user, qid)
    content = question_bank.content(repo.conn, qid, user['school_id'])
    document = content['document'] if content else dict(number=q.get('original_question_number') or '',
        kind=q['question_type'], stem_md=q['stem'], options=[dict(key=k, markdown=v) for k,v in q['options'].items()],
        children=[], answer_md=str(q['answer'].get('answer', '') if isinstance(q['answer'], dict) else q['answer']),
        analysis_md=q['analysis'])
    child_key = content['child_key'] if content else ''
    unit = next((child for child in document.get('children', []) if child['key'] == child_key), document)
    if content:
        rule = {**(unit.get('grading_rule') or {}), 'type': unit.get('kind', q['question_type'])}
        rule['verified'] = unit.get('answer_state') == 'verified'
    else:
        rule = {**(q['answer'] if isinstance(q['answer'], dict) else {'answer': q['answer']}), 'type': q['question_type']}
    return dict(question_id=qid, question_version=q['version'], revision_id=content['current_revision_id'] if content else None,
                child_key=child_key, document_json=dumps(document), rule_json=dumps(rule),
                tags_json=dumps(repo.tags_for_question(qid)), question_type=unit.get('kind', q['question_type']))


def trial_render(trial, base_path='', solution=False, options=True, whole_group=False):
    url = lambda aid: base_path + '/api/student-question-assets/' + quote(aid) + '?trial_id=' + quote(trial['id'])
    return render_question(loads(trial['document_json'], {}), asset_url=url, include_solution=solution,
                           child_key=None if whole_group else trial['child_key'] or None, include_options=options, compact_layout=True)


def trial_outcome(t, answer):
    from .outcomes import decide
    rule=loads(t['rule_json'], {})
    document=loads(t['document_json'], {})
    unit=next((child for child in document.get('children', []) if child['key']==t['child_key']), document)
    return decide(rule, answer, {o['key']:o['markdown'] for o in unit.get('options',[])},
                  verified=rule.get('verified',True))['outcome']


def self_outcome(kind, payload, viewed):
    if kind in CHOICE:
        if payload.get('self_outcome'):
            raise InvalidRequest('选择题由系统核对选项')
        return None
    result = payload.get('self_outcome')
    if result not in ('correct', 'wrong') or not viewed:
        raise InvalidRequest('请先查看答案，再标注做对了或做错了')
    return result


def api(repo, user, action, p, base_path=''):
    require_student(user)
    repo.conn.execute('begin immediate')
    try:
        result = _api(repo,user,action,p,base_path)
        repo.conn.commit()
        return result
    except Exception:
        repo.conn.rollback()
        raise


def _api(repo, user, action, p, base_path=''):
    require_student(user)
    c, uid = repo.conn, user['id']
    if action == 'wrong-causes':
        from .wrong_causes import save
        return save(repo, user, p)
    if action == 'wrong-mastered':
        if p.get('confirmed') is not True:
            raise InvalidRequest('请先确认已掌握并移出错题本')
        wid = str(p.get('wrong_id', ''))
        if c.execute('select 1 from student_mastered_wrongs where student_id=? and wrong_id=?', (uid, wid)).fetchone():
            return {'message': '已移出错题本，不再提示复习', 'url': 'app?module=wrong&mastered=1'}
        g = group_for(repo, user, wid)
        # Retain source errors and attempts. Dismiss all existing duplicate sources of each member.
        ids = {w['id'] for w in g['members']}
        for w in g['members']:
            ids.update(r[0] for r in c.execute('select id from wrong_questions where student_id=? and question_id=?', (uid, w['question_id'])))
            ids.update(r[0] for r in c.execute('''select w.id from student_personal_wrongs w
                join student_bank_trials t on t.id=w.trial_id where w.student_id=? and t.question_id=?''', (uid, w['question_id'])))
        c.executemany('insert or ignore into student_mastered_wrongs values(?,?,?)', [(uid, item, learning.now()) for item in ids])
        return {'message': '已移出错题本，不再提示复习', 'url': 'app?module=wrong&mastered=1'}
    if action in ('group-solution','group-submit'):
        g=group_for(repo,user,p.get('wrong_id',''))
        members=review_members(g)
        if action=='group-solution':
            if not members:raise StateConflict('当前没有可提交的小问')
            fragments=[];available=True
            for w in members:
                result=_api(repo,user,'personal-solution',dict(wrong_id=w['id']),base_path) if w['personal'] else learning.api(repo,user,'solution',dict(wrong_id=w['id']),base_path)
                content=result.get('solution_html') or render_markdown('参考答案：'+str(result.get('answer') or '尚未导入'))+render_markdown(result.get('analysis') or '')
                fragments.append('<section><h4>%s</h4>%s</section>'%(esc(w['part_label']),content))
                available=available and bool(result.get('can_self_report',result.get('solution_available',result.get('answer') or result.get('solution_html'))))
            return {'solution_html':''.join(fragments),'can_self_report':available}
        key=str(p.get('request_key',''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',key):raise InvalidRequest('缺少有效提交标识')
        prefix='group:'+key+':'
        previous=[dict(r) for r in c.execute("select wrong_question_id wrong_id,answer,outcome from redo_attempts where student_id=? and substr(request_key,1,?)=? union all select wrong_id,answer,outcome from student_personal_redos where student_id=? and substr(request_key,1,?)=?",(uid,len(prefix),prefix,uid,len(prefix),prefix))]
        if previous:
            old={r['wrong_id']:r for r in previous}
            if not set(old).issubset({w['id'] for w in g['members']}):raise StateConflict('重复请求内容不一致')
            saved=[w for w in g['members'] if w['id'] in old];results=[]
            for i,w in enumerate(saved):
                answer=p.get('answer_%s'%i,'');answer=','.join(sorted(set(answer))) if isinstance(answer,list) else str(answer)
                if old[w['id']]['answer']!=answer or (w['kind'] not in CHOICE and p.get('result_%s'%i)!=old[w['id']]['outcome']):raise StateConflict('重复请求内容不一致')
                results.append(dict(label=w['part_label'],outcome=old[w['id']]['outcome']))
            return group_result(results)
        if not members:raise StateConflict('当前没有可提交的小问')
        if loads(p.get('member_ids','[]'),[])!=[w['id'] for w in members]:raise StateConflict('需要复习的小问已变化，请刷新页面')
        # Validate every result before saving any member.
        payloads=[]
        for i,w in enumerate(members):
            item=dict(wrong_id=w['id'],unified='1',request_key='group:'+key+':'+hashlib.sha256(w['id'].encode()).hexdigest()[:12],_group_transaction=True)
            if w['kind'] in CHOICE:
                item['answer']=p.get('answer_%s'%i,'')
                if not item['answer']:raise InvalidRequest('请完成%s的选项'%w['part_label'])
            else:
                item['self_outcome']=p.get('result_%s'%i)
                if item['self_outcome'] not in ('correct','wrong'):raise InvalidRequest('请标注%s的对错'%w['part_label'])
            payloads.append((w,item))
        results=[]
        for w,item in payloads:
            result=_api(repo,user,'personal-submit',item,base_path) if w['personal'] else learning.submit(repo,uid,item,_transaction=False)
            results.append(dict(label=w['part_label'],outcome=result['outcome']))
        return group_result(results)
    if action == 'student-preferences':
        def selected(key, allowed):
            value = p.get(key, [])
            value = [value] if isinstance(value, str) else value
            if not isinstance(value, list) or any(v not in allowed for v in value):
                raise InvalidRequest('关注设置无效')
            return sorted(set(value))
        types, levels = selected('types', TYPES), selected('levels', LEVELS)
        c.execute('''insert into student_learning_preferences values(?,?,?,?) on conflict(student_id)
                     do update set types_json=excluded.types_json,levels_json=excluded.levels_json,updated_at=excluded.updated_at''',
                  (uid, dumps(types), dumps(levels), learning.now()))
        c.commit()
        return {'message': '关注设置已保存', 'url': 'app'}
    if action == 'bank-start':
        key = str(p.get('request_key', ''))
        if not key or len(key) > 100:
            raise InvalidRequest('缺少提交标识')
        old = c.execute('select * from student_bank_trials where student_id=? and request_key=?', (uid, key)).fetchone()
        if old:
            if old['question_id'] != p.get('question_id'):
                raise StateConflict('重复请求内容不一致')
            return {'url': 'app?trial=' + quote(old['id'])}
        frozen = frozen_question(repo, user, p.get('question_id', ''))
        tid = 'trial-' + uuid.uuid4().hex
        c.execute('''insert into student_bank_trials(id,school_id,student_id,question_id,question_version,
                     revision_id,child_key,document_json,rule_json,tags_json,question_type,request_key)
                     values(?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (tid, user['school_id'], uid, *[frozen[k] for k in ('question_id','question_version','revision_id','child_key',
                   'document_json','rule_json','tags_json','question_type')], key))
        c.commit()
        return {'url': 'app?trial=' + quote(tid)}
    if action in ('bank-solution', 'bank-submit', 'bank-add-wrong'):
        t = owned_trial(c, user, p.get('trial_id', ''))
        if action == 'bank-solution':
            c.execute('update student_bank_trials set viewed_at=? where id=?', (learning.now(), t['id']))
            c.commit()
            rendered = trial_render(t, base_path, solution=True)
            marker = '<section class="question-solution">'
            solution = marker + rendered.split(marker, 1)[1].split('</section>', 1)[0] + '</section>' if marker in rendered else '<p>答案与解析尚未导入，暂不能自评。</p>'
            return {'solution_html': solution, 'can_self_report': marker in rendered}
        if action == 'bank-add-wrong':
            if t['outcome'] not in ('wrong', 'blank'):
                raise InvalidRequest('只有做错的试做题可以加入错题本')
            existing = c.execute('''select w.id from student_personal_wrongs w join student_bank_trials t on t.id=w.trial_id
                 where w.student_id=? and t.question_id=? and t.question_version=?''', (uid, t['question_id'], t['question_version'])).fetchone()
            if not existing:
                c.execute('insert into student_personal_wrongs values(?,?,?,?)', ('personal-' + uuid.uuid4().hex, uid, t['id'], learning.now()))
            c.commit()
            return {'message': '已加入错题本', 'url': 'app?module=wrong'}
        answer = p.get('answer', '')
        answer = ','.join(sorted(set(answer))) if isinstance(answer, list) else str(answer)
        result = self_outcome(t['question_type'], p, t['viewed_at'])
        if t['outcome']:
            if t['answer'] != answer or (result and t['outcome'] != result):
                raise StateConflict('本次试做已经提交')
            return {'outcome': t['outcome'], 'trial_id': t['id']}
        if t['question_type'] in CHOICE and not answer.strip():
            raise InvalidRequest('请先选择选项')
        result = result or trial_outcome(t, answer)
        c.execute('update student_bank_trials set answer=?,outcome=?,submitted_at=? where id=?',
                  (answer, result, learning.now(), t['id']))
        c.commit()
        return {'outcome': result, 'trial_id': t['id']}
    if action in ('personal-solution', 'personal-submit'):
        w = c.execute('select * from student_personal_wrongs where id=? and student_id=?', (p.get('wrong_id'), uid)).fetchone()
        if not w:
            raise ResourceNotFound('错题不存在')
        t = owned_trial(c, user, w['trial_id'])
        if action == 'personal-solution':
            result = _api(repo,user,'bank-solution',{'trial_id':t['id']},base_path)
            c.execute('insert into learning_views values(?,?,?) on conflict(student_id,question_id) do update set viewed_at=excluded.viewed_at', (uid,t['question_id'],learning.now()))
            c.commit()
            return result
        viewed = c.execute('select viewed_at from learning_views where student_id=? and question_id=?', (uid,t['question_id'])).fetchone()
        answer = p.get('answer', '')
        answer = ','.join(sorted(set(answer))) if isinstance(answer,list) else str(answer)
        result = self_outcome(t['question_type'],p,viewed)
        key = str(p.get('request_key',''))
        if not key or len(key)>100: raise InvalidRequest('缺少提交标识')
        old = c.execute('select * from student_personal_redos where student_id=? and request_key=?',(uid,key)).fetchone()
        if old:
            if old['wrong_id']!=w['id'] or old['answer']!=answer or (result and old['outcome']!=result):
                raise StateConflict('重复请求内容不一致')
            return dict(old)
        if t['question_type'] in CHOICE and not answer.strip(): raise InvalidRequest('请先选择选项')
        result = result or trial_outcome(t, answer)
        rid='personal-redo-'+uuid.uuid4().hex
        from .diagnosis import assisted_today
        purpose='learn' if assisted_today(c,uid,t['question_id']) else 'verify'
        c.execute('insert into student_personal_redos(id,student_id,wrong_id,answer,outcome,self_reported,submitted_at,request_key,purpose) values(?,?,?,?,?,?,?,?,?)',
                  (rid,uid,w['id'],answer,result,int(t['question_type'] not in CHOICE),learning.now(),key,purpose))
        if not p.get('_group_transaction'):c.commit()
        return {'outcome':result}
    raise InvalidRequest('未知学生操作')


def group_result(results):
    outcome='pending' if any(r['outcome']=='pending' for r in results) else 'wrong' if any(r['outcome'] in ('wrong','blank') for r in results) else 'correct'
    return {'outcome':outcome,'parts':results}


def personal_progress(c, w):
    today=datetime.now(learning.TZ).date()
    due=learning.day(w['submitted_at'])+timedelta(days=1)
    count=0; last='需再练'; pending=False
    for a in c.execute('select * from student_personal_redos where wrong_id=? order by submitted_at,id',(w['id'],)):
        if a['purpose']=='learn': continue
        if a['outcome']=='pending': pending=True; continue
        d=learning.day(a['submitted_at']);last=learning.LABELS[a['outcome']]
        if a['outcome'] in ('wrong','blank'): count=0; due=d+timedelta(days=1)
        elif count<3 and d>=due: count+=1;due=d+timedelta(days=3 if count==1 else 7)
    return dict(count=count,due=str(due),last=last,pending=pending,
                status='待确认' if pending else '本题已巩固' if count>=3 else '等待复习' if due>today else '需再练',
                available=not pending and count<3 and due<=today)


def wrongs(repo,user):
    c=repo.conn
    rows=[dict(r) for r in c.execute('''select w.* from wrong_questions w join assessment_sessions a on a.id=w.assessment_id
        where w.student_id=? and w.is_active=1 and a.grading_status='published' order by w.created_at desc,w.id''',(user['id'],))]
    mastered={r[0] for r in c.execute('select wrong_id from student_mastered_wrongs where student_id=?', (user['id'],))}
    unique={}
    for w in rows:
        if w['id'] in mastered: continue
        if w['question_id'] in unique: continue
        s=learning.snapshot(c,w)
        w.update(snapshot=s,kind=s['question_type'],tag_list=loads(s['tag_snapshot_json'],[]),stem=s['stem'],personal=False)
        w['progress']=learning.progress(c,w)
        unique[w['question_id']]=w
    for r in c.execute('''select w.id,w.trial_id,w.created_at,t.* from student_personal_wrongs w
        join student_bank_trials t on t.id=w.trial_id where w.student_id=? order by w.created_at desc''',(user['id'],)):
        w=dict(r);w['id']=r[0];w['trial_id']=r[1]
        if w['id'] in mastered or w['question_id'] in unique: continue
        w.update(personal=True,kind=w['question_type'],tag_list=loads(w['tags_json'],[]),stem=loads(w['document_json'],{})['stem_md'])
        w['progress']=personal_progress(c,w);unique[w['question_id']]=w
    stats=statistics(c,list(unique))
    for w in unique.values():
        w['level']=stats[w['question_id']]['label']
        q=repo.get_question(w['question_id'])
        bank_kind=q.get('bank_type','unknown')
        w['filter_kind']='structured' if bank_kind=='solution' else bank_kind if bank_kind!='unknown' else w['kind']
    return list(unique.values())


def group_wrongs(repo,user):
    """Merge display/review by the frozen parent revision, never by a paper number."""
    groups={}
    for w in wrongs(repo,user):
        content=None if w['personal'] else snapshot_content(repo.conn,w['snapshot']['id'],user['school_id'])
        revision=w.get('revision_id') if w['personal'] else (content or {}).get('revision_id')
        document=loads(w['document_json'],{}) if w['personal'] else (content or {}).get('document',{})
        child=w.get('child_key','') if w['personal'] else (content or {}).get('child_key','')
        key=('revision:'+revision) if revision and document.get('children') else 'question:'+w['question_id']
        g=groups.setdefault(key,dict(id=w['id'],key=key,members=[],document=document,revision_id=revision))
        w['child_key']=child
        labels={part['key']:part.get('label',part['key']) for part in document.get('children',[])}
        w['part_label']=labels.get(child,'本题')
        g['members'].append(w)
    for g in groups.values():
        order={part['key']:i for i,part in enumerate(g['document'].get('children',[]))}
        g['members'].sort(key=lambda w:order.get(w['child_key'],0))
        g['anchor']=g['members'][0]
        g['number']=g['document'].get('number') or ''
    return list(groups.values())


def review_members(g):
    return [w for w in g['members'] if w['progress']['count']<3 and not w['progress']['pending']]


def group_for(repo,user,wid):
    g=next((g for g in group_wrongs(repo,user) if any(w['id']==wid for w in g['members'])),None)
    if not g:raise PermissionDenied('错题不存在或尚未发布')
    return g


def group_first_record(c,g,user):
    if not g['document'].get('children'):
        return first_record(c,g['anchor'])
    needed={w['question_id'] for w in review_members(g)}
    by_question={w['question_id']:w for w in g['members']}
    records=[]
    anchor=g['anchor']
    labels={part['key']:part.get('label',part['key']) for part in g['document']['children']}
    if not anchor['personal']:
        rows=c.execute("""select s.id snapshot_id,s.question_id,
            coalesce(correction.child_key,b.child_key,'') child_key,r.id response_id
            from question_version_snapshots s
            left join snapshot_content_bindings b on b.snapshot_id=s.id
            left join historical_content_corrections correction on correction.snapshot_id=s.id and correction.state='active'
            left join student_responses r on r.snapshot_id=s.id and r.student_id=?
            where s.assessment_id=? and coalesce(correction.revision_id,b.revision_id)=? order by s.position""",
            (user['id'],anchor['assessment_id'],g['revision_id'])).fetchall()
        for r in rows:
            e=c.execute('select imported_score,imported_max_score from response_evidence where response_id=? and imported_score is not null order by rowid limit 1',((by_question.get(r['question_id']) or {}).get('response_id',r['response_id']),)).fetchone()
            score=('%s%s'%(e[0],(' / '+str(e[1])) if e[1] is not None else '')) if e else '未导入得分'
            records.append((r['question_id'],labels.get(r['child_key'],'本题'),score))
    if not records:
        records=[(w['question_id'],w['part_label'],'题库试做 · '+learning.LABELS[w['outcome']] if w['personal'] else '未导入得分') for w in g['members']]
    return '<section class="first-score"><h4>首次作答得分情况</h4><table><thead><tr><th>小问</th><th>首次得分</th><th>复习提示</th></tr></thead><tbody>'+''.join(
        '<tr><td>%s</td><td>%s</td><td>%s</td></tr>'%(esc(label),esc(score),'需要复习' if qid in needed else '等待教师确认' if (by_question.get(qid) or {}).get('progress',{}).get('pending') else '无需复习') for qid,label,score in records)+'</tbody></table></section>'


def group_attempt_count(c,user,g):
    ids=[w['id'] for w in g['members']];placeholders=','.join('?' for _ in ids)
    rows=c.execute('select id,request_key from redo_attempts where student_id=? and wrong_question_id in ('+placeholders+')',(user['id'],*ids)).fetchall()
    rows+=c.execute('select id,request_key from student_personal_redos where student_id=? and wrong_id in ('+placeholders+')',(user['id'],*ids)).fetchall()
    return len({r['request_key'].split(':')[1] if (r['request_key'] or '').startswith('group:') else r['id'] for r in rows})


def group_practice(repo,user,g,base_path,next_url):
    from .learning_views import hidden
    c=repo.conn;members=review_members(g)
    # Preserve the ordinary choice workflow for a stand-alone question.
    if not g['document'].get('children'):
        return practice(repo,user,g['anchor'],base_path,next_url)
    from .diagnosis import panel
    body=wrong_render(c,g['anchor'],user,base_path)+group_first_record(c,g,user)+panel(g['id'],g['members'])
    fields=[]
    for i,w in enumerate(members):
        label=esc(w['part_label'])
        if w['kind'] in CHOICE:
            from .question_content import render_snapshot_options
            options=render_snapshot_options(c,w['snapshot']['id'],user['school_id'],base_path) if not w['personal'] else None
            if options is None:
                unit=next(part for part in g['document']['children'] if part['key']==w['child_key'])
                options=[dict(key=o['key'],html=render_markdown(o['markdown'])) for o in unit.get('options',[])]
            fields.append('<section class="group-choice"><h4>%s</h4>%s</section>'%(label,controls(options,w['kind']).replace('name="answer"','name="answer_%s"'%i)))
        else:
            fields.append('<label class="group-self-label">%s<select name="result_%s" required><option value="">请选择本次结果</option><option value="correct">我做对了</option><option value="wrong">我做错了</option></select></label>'%(label,i))
    form=''
    if members:
        form='<form class="student-practice-form" data-action="group-submit" data-next="%s">%s<fieldset data-group-controls hidden><legend>标注需要复习的小问</legend>%s<button type="submit">提交本题复习结果</button></fieldset><div role="status"></div></form>'%(esc(next_url),hidden('wrong_id',g['id'])+hidden('member_ids',dumps([w['id'] for w in members])),''.join(fields))
    else:
        body+='<p>当前没有可提交的小问，待确认的小问需等待教师处理。</p>'
    # Load all active-exercise figures, including options, without a viewport gate.
    return ('<article class="student-practice" id="practice"><h2>第 %s 次重做</h2>%s%s<button type="button" class="secondary" data-student-solution="group-solution" data-id="%s">查看答案与解析</button><div class="solution-output" role="status"></div><a class="practice-next" href="%s" hidden>下一题</a></article>'%(group_attempt_count(c,user,g)+1,body,form,esc(g['id']),esc(next_url))).replace('loading="lazy"', 'loading="eager"')


def library_layout(sidebar,body):
    return '<div class="student-library-layout"><button type="button" class="student-filter-backdrop" aria-label="关闭筛选条件" hidden></button>%s<div class="student-library-content">%s</div></div>'%(sidebar,body)


def tag_filters(repo,user,params):
    catalog=question_bank.taxonomy(repo,user);result={}
    for kind in KINDS:
        selected=(params.get(kind) or [''])[0]
        if not selected: continue
        ids={selected}
        while True:
            children={t['id'] for t in catalog[kind] if t.get('parent_id') in ids}
            if children.issubset(ids):break
            ids.update(children)
        result[kind]=ids
    return result


def match(w,params,tag_sets=None):
    value=lambda key:(params.get(key) or [''])[0]
    return ((not value('type') or w.get('filter_kind',w['kind'])==value('type')) and
            (not value('level') or w['level']==value('level')) and
            (not value('search') or value('search').lower() in w['stem'].lower()) and
            all(not value(k) or any(t['tag_type']==k and t['tag_id'] in ((tag_sets or {}).get(k) or {value(k)}) for t in w['tag_list']) for k in KINDS))


def knowledge_picker(items,selected):
    """Native disclosure controls keep large taxonomies navigable without JS."""
    by_id={t['id']:t for t in items};children=defaultdict(list)
    for t in items:children[t.get('parent_id') if t.get('parent_id') in by_id else None].append(t)
    ancestors=set();current=selected
    while current in by_id and current not in ancestors:
        ancestors.add(current);current=by_id[current].get('parent_id')
    def choice(value,label):
        return '<label class="student-knowledge-choice"><input type="radio" name="knowledge" value="%s"%s><span>%s</span></label>'%(esc(value),' checked' if value==selected else '',esc(label))
    def branch(t,seen):
        if t['id'] in seen:return ''
        subs=children[t['id']]
        if not subs:return choice(t['id'],t['name'])
        return '<details class="student-knowledge-branch"%s><summary>%s</summary><div>%s%s</div></details>'%(' open' if t['id'] in ancestors else '',esc(t['name']),choice(t['id'],'全部：'+t['name']),''.join(branch(child,seen|{t['id']}) for child in subs))
    return '<fieldset class="student-knowledge-tree"><legend>知识点</legend>'+choice('','全部知识点')+''.join(branch(t,set()) for t in children[None])+'</fieldset>'


def filters(repo,user,module,params,bank=False):
    from .learning_views import hidden
    from .wrong_causes import CAUSES
    catalog=question_bank.taxonomy(repo,user)
    selected=lambda key:(params.get(key) or [''])[0]
    def picker(key,label,items):
        return '<label>%s<select name="%s"><option value="">全部</option>%s</select></label>'%(label,key,''.join(
            '<option value="%s"%s>%s</option>'%(esc(k),' selected' if selected(key)==k else '',esc(v)) for k,v in items))
    return '<aside class="student-filter-sidebar"><details class="student-filter-drawer" data-filter-scope="'+module+'" open><summary aria-label="展开或收起筛选条件" title="筛选条件"><svg class="student-filter-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h16l-6 7v6l-4 2v-8Z"/></svg><span class="student-filter-title">筛选条件</span></summary><form method="get" class="student-filters">'+hidden('module',module)+'<label>搜索题目<input type="search" name="search" value="'+esc(selected('search'))+'"></label>'+knowledge_picker(catalog['knowledge'],selected('knowledge'))+''.join(
        picker(k,label,[(t['id'],t['path_text']) for t in catalog[k]]) for k,label in KINDS.items() if k!='knowledge')+(picker('cause','我标记的错因',[('unmarked','尚未标记')]+list(CAUSES.items())) if module=='wrong' else '')+picker('type','题型',list(TYPES.items()))+picker('level','难度',[(l,l) for l in LEVELS])+(
        picker('batch','导入批次',[(r['id'],r['source_file_name']) for r in repo.conn.execute('select id,source_file_name from question_import_batches where school_id=? order by created_at desc',(user['school_id'],))])+
        picker('paper','试卷',[(r['id'],r['title']) for r in question_bank.list_papers(repo,user)]) if bank else '')+'<button>筛选</button><a href="app?module='+module+'">重置筛选</a></form></details></aside>'


def first_record(c,w):
    if w['personal']:
        source='<p>来源：题库试做 · %s</p>'%learning.LABELS[w['outcome']]
        if w['kind'] in CHOICE:
            source+='<details class="first-answer"><summary>首次作答记录</summary><p>%s</p></details>'%esc(w['answer'] or '空白')
        return source
    r=c.execute('select initial_answer from student_responses where id=?',(w['response_id'],)).fetchone()
    if w['kind'] in CHOICE:
        return '<details class="first-answer"><summary>首次作答记录</summary><p>%s</p></details>'%esc(r[0] or '空白')
    evidence=c.execute('''select imported_score,imported_max_score from response_evidence
        where response_id=? and imported_score is not null order by rowid limit 1''',(w['response_id'],)).fetchone()
    score = ('%s%s'% (evidence[0],(' / '+str(evidence[1])) if evidence[1] is not None else '')) if evidence else '未导入得分'
    return '<p class="first-score">首次作答得分：%s</p>'%esc(score)


def wrong_render(c,w,user,base_path):
    from .learning_views import question_fragment,question_part_context
    if w['personal']:
        t=dict(w,id=w['trial_id'])
        return trial_render(t,base_path,whole_group=True)
    return question_fragment(c,w['snapshot'],user,base_path,whole_group=True)


def controls(rows,kind):
    control='checkbox' if kind=='multiple_choice' else 'radio'
    return '<fieldset class="student-options"><legend>选择你的答案%s</legend>%s</fieldset>'%('（可多选）' if control=='checkbox' else '', ''.join(
        '<label class="student-option"><input type="%s" name="answer" value="%s"><span class="option-key">%s.</span><span class="option-body">%s</span></label>'%(control,esc(r['key']),esc(r['key']),r['html']) for r in rows))


def practice(repo,user,w,base_path,next_url):
    from .learning_views import hidden,form,question_fragment,question_part_context
    from .question_content import render_snapshot_options
    c=repo.conn;personal=w.get('personal',False);trial='snapshot' not in w and 'trial_id' not in w and 'document_json' in w and not personal
    if trial:
        kind=w['question_type']; unit=loads(w['document_json'],{})
        if w['child_key']: unit=next(child for child in unit['children'] if child['key']==w['child_key'])
        url=lambda aid:base_path+'/api/student-question-assets/'+quote(aid)+'?trial_id='+quote(w['id'])
        options=[dict(key=o['key'],html=render_markdown(o['markdown'],url)) for o in unit.get('options',[])]
        stem=trial_render(w,base_path,options=kind not in CHOICE)
        action='bank-submit';identifier=hidden('trial_id',w['id']);solution='bank-solution';sid=w['id'];number='题库试做'
    else:
        kind=w['kind'];action='personal-submit' if personal else 'submit';identifier=hidden('wrong_id',w['id'])+hidden('unified','1')
        solution='personal-solution' if personal else 'solution';sid=w['id']
        count=c.execute('select count(*) from '+('student_personal_redos where wrong_id=?' if personal else 'redo_attempts a join wrong_questions w on w.id=a.wrong_question_id where a.student_id=? and w.question_id=?'),(w['id'],) if personal else (user['id'],w['question_id'])).fetchone()[0]
        number='第 %s 次重做'%(count+1)
        if personal:
            t=dict(w,id=w['trial_id']);stem=trial_render(t,base_path,options=kind not in CHOICE)
            document=loads(w['document_json'],{});unit=next((part for part in document.get('children',[]) if part['key']==w['child_key']),document)
            url=lambda aid:base_path+'/api/student-question-assets/'+quote(aid)+'?trial_id='+quote(w['trial_id'])
            options=[dict(key=o['key'],html=render_markdown(o['markdown'],url)) for o in unit.get('options',[])]
        else:
            s=w['snapshot'];stem=question_part_context(c,s,user['school_id'])+question_fragment(c,s,user,base_path,include_options=kind not in CHOICE,whole_group=True)
            options=render_snapshot_options(c,s['id'],user['school_id'],base_path)
            if options is None:
                raw=loads(s['options_json'],{})
                if isinstance(raw,list):raw={chr(65+i):v for i,v in enumerate(raw)}
                options=[dict(key=k,html=render_markdown(v)) for k,v in raw.items()]
    if not trial:
        from .diagnosis import panel
        stem+=panel(w['id'],[w])
    choice=kind in CHOICE
    answer=controls(options,kind)+'<button type="submit">提交作答</button>' if choice else '<p>在纸上完成这道题，查看答案后标注本次结果。</p><div data-self-controls hidden><button type="submit" name="self_outcome" value="correct">我做对了</button><button type="submit" name="self_outcome" value="wrong">我做错了</button></div>'
    f=form(action,identifier+answer)
    f=f.replace('class="learning-form"','class="student-practice-form" data-next="'+esc(next_url)+'"')
    result=''
    if trial and w.get('outcome'):
        result='<p>本次结果：%s</p>'%learning.LABELS[w['outcome']]
        if w['outcome'] in ('wrong','blank'):
            result+=form('bank-add-wrong',hidden('trial_id',w['id'])+'<p>是否将这道题加入错题本？</p><button>加入错题本</button><a href="app?module=bank">暂不加入</a>')
        f=''
    # Load all active-exercise figures, including options, without a viewport gate.
    return ('<article class="student-practice" id="practice"><h2>%s</h2>%s%s%s<button type="button" class="secondary" data-student-solution="%s" data-id="%s">查看答案与解析</button><div class="solution-output" role="status"></div><a class="practice-next" href="%s" hidden>下一题</a></article>'%(number,stem,f,result,solution,esc(sid),esc(next_url))).replace('loading="lazy"', 'loading="eager"')


def graph(repo,user):
    c=repo.conn;uid=user['id'];groups=defaultdict(lambda:dict(total=0,wrong=0,questions=set()))
    rows=[dict(r) for r in c.execute('''select r.question_id,r.outcome,s.tag_snapshot_json tags_json from student_responses r
        join question_version_snapshots s on s.id=r.snapshot_id join assessment_sessions a on a.id=r.assessment_id
        join assessment_participants p on p.assessment_id=a.id and p.student_id=r.student_id
        where r.student_id=? and p.status='present' and a.grading_status='published'
        union all select w.question_id,a.outcome,s.tag_snapshot_json from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id
        join student_responses r on r.id=w.response_id join question_version_snapshots s on s.id=r.snapshot_id
        where a.student_id=? and w.is_active=1''',(uid,uid))]
    rows += [dict(r) for r in c.execute('select question_id,outcome,tags_json from student_bank_trials where student_id=? and outcome is not null',(uid,))]
    rows += [dict(r) for r in c.execute('''select t.question_id,r.outcome,t.tags_json from student_personal_redos r
        join student_personal_wrongs w on w.id=r.wrong_id join student_bank_trials t on t.id=w.trial_id where r.student_id=?''',(uid,))]
    for r in rows:
        if r['outcome'] not in ('correct','wrong','blank'):continue
        seen=set()
        for t in loads(r['tags_json'],[]):
            key=(t['tag_type'],t['tag_id'])
            if key in seen or key[0] not in KINDS:continue
            seen.add(key);g=groups[key];g['name']=t['name'];g['total']+=1;g['wrong']+=r['outcome']!='correct';g['questions'].add(r['question_id'])
    out=['<p>错误率＝错误及空白次数 / 已确认作答次数，包含考试、重做与试做；非选择题含学生自评，待确认记录不计入。</p>']
    for kind,title in KINDS.items():
        out.append('<section class="student-tag-chart"><h3>%s</h3><ol>'%title)
        entries=sorted([(key,g) for key,g in groups.items() if key[0]==kind],key=lambda pair:(-pair[1]['wrong']/pair[1]['total'],-pair[1]['total'],pair[1]['name']))
        for key,g in entries:
            rate=100*g['wrong']/g['total']
            out.append('<li><a href="app?module=bank&%s=%s"><span>%s</span><meter min="0" max="100" value="%.2f">%.1f%%</meter><strong>%.1f%%</strong><small>%s / %s 次 · %s 道题</small></a></li>'%(kind,quote(key[1]),esc(g['name']),rate,rate,rate,g['wrong'],g['total'],len(g['questions'])))
        if not entries:out.append('<li>暂无已确认的%s作答记录。</li>'%title)
        out.append('</ol></section>')
    return ''.join(out)


def history(repo,user):
    c=repo.conn;uid=user['id']
    out=['<h3>历史考试</h3><ul class="student-exam-list">']
    rows=c.execute('''select a.id,a.title,a.created_at from assessment_sessions a join assessment_participants p on p.assessment_id=a.id
        where p.student_id=? and p.status='present' and a.grading_status='published' order by a.created_at desc,a.id''',(uid,)).fetchall()
    for r in rows:out.append('<li><a href="exams?id=%s">%s</a><time>%s</time></li>'%(quote(r['id']),esc(r['title']),esc(r['created_at'][:10])))
    if not rows:out.append('<li>暂无已发布的考试记录。</li>')
    out.append('</ul><h3>最近练习结果</h3><p>考试首次作答与后续练习分别保存；自评结果会明确标注。</p><ul class="student-history-list">')
    attempts=[dict(r) for r in c.execute('''select a.submitted_at,a.answer,a.outcome,a.self_reported,s.stem,w.id wrong_id
       from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id join student_responses r on r.id=w.response_id
       join question_version_snapshots s on s.id=r.snapshot_id where a.student_id=?''',(uid,))]
    for a in attempts:a['url']='app?practice='+quote(a['wrong_id']);a['source']='重做'
    for r in c.execute('select * from student_bank_trials where student_id=? and outcome is not null',(uid,)):
        a=dict(r);a.update(stem=loads(a['document_json'],{})['stem_md'],self_reported=a['question_type'] not in CHOICE,url='app?trial='+quote(a['id']),source='题库试做');attempts.append(a)
    for r in c.execute('''select r.*,t.document_json from student_personal_redos r join student_personal_wrongs w on w.id=r.wrong_id
                         join student_bank_trials t on t.id=w.trial_id where r.student_id=?''',(uid,)):
        a=dict(r);a.update(stem=loads(a['document_json'],{})['stem_md'],url='app?practice='+quote(a['wrong_id']),source='重做');attempts.append(a)
    for a in sorted(attempts,key=lambda a:a['submitted_at'],reverse=True)[:50]:
        out.append('<li><a href="%s">%s</a><p>%s · %s%s · %s</p></li>'%(esc(a['url']),esc(a['stem'][:90]),esc(a['submitted_at'][:16].replace('T',' ')),a['source'],' · 学生自评' if a['self_reported'] else (' · 选择：'+esc(a['answer']) if a['answer'] else ''),learning.LABELS[a['outcome']]))
    if not attempts:out.append('<li>暂无练习记录。</li>')
    out.append('</ul>');return ''.join(out)


def bank(repo,user,params,base_path):
    value=lambda k:(params.get(k) or [''])[0]
    # Reuse the teacher library's grouping, source order, content and taxonomy.
    result=question_bank.library(repo,user,batch_id=value('batch'),paper_id=value('paper'),search=value('search'),page_size=50,base_path=base_path)
    ids=result['scope_question_ids'];stats=statistics(repo.conn,ids)
    matching=[];tag_sets=tag_filters(repo,user,params)
    for qid in ids:
        q=repo.get_question(qid);w=dict(kind=q['question_type'],filter_kind='structured' if q.get('bank_type')=='solution' else q.get('bank_type') if q.get('bank_type','unknown')!='unknown' else q['question_type'],level=stats[qid]['label'],stem=q['stem'],tag_list=repo.tags_for_question(qid))
        if match(w,params,tag_sets):matching.append(qid)
    try:page=max(1,int(value('page') or '1'))
    except ValueError:raise InvalidRequest('页码无效')
    visible=set(matching[(page-1)*10:page*10]);sidebar=filters(repo,user,'bank',params,bank=True);out=['<p>匹配 %s 个作答单元</p>'%len(matching)]
    from .learning_views import form,hidden
    rendered=set()
    for qid in matching:
        if qid not in visible:continue
        binding=question_bank.content(repo.conn,qid,user['school_id']);group=binding['group_id'] if binding else qid
        if group in rendered:continue
        rendered.add(group);q=repo.get_question(qid)
        url=lambda aid:base_path+'/api/student-question-assets/'+quote(aid)+'?question_id='+quote(qid)
        if binding:
            content=render_question(binding['document'],asset_url=url,compact_layout=True)
        else:
            content=render_question(loads(frozen_question(repo,user,qid)['document_json'],{}),compact_layout=True)
        out.append('<article class="student-bank-question">'+content)
        group_ids=[mid for mid in matching if mid in visible and (question_bank.content(repo.conn,mid,user['school_id']) or {}).get('group_id',mid)==group]
        labels={part['key']:part['label'] for part in (binding['document'].get('children',[]) if binding else [])}
        for mid in group_ids:
            m=repo.get_question(mid);b=question_bank.content(repo.conn,mid,user['school_id'])
            label='小问 '+labels.get(b['child_key'],b['child_key']) if b and b['child_key'] else '本题'
            out.append(badge(stats[mid],label)+'<p>'+''.join('<span class="pill">%s：%s</span>'%(KINDS.get(t['tag_type'],'标签'),esc(t['name'])) for t in repo.tags_for_question(mid))+'</p>'+form('bank-start',hidden('question_id',mid)+'<button>试做%s</button>'%esc(label)))
        out.append('</article>')
    if not matching:out.append('<p>没有匹配的题目，请调整筛选条件。</p>')
    out.append('<nav class="student-pagination" aria-label="题库翻页">')
    for p,label in ((page-1,'上一页'),(page+1,'下一页')):
        if p>=1 and (p-1)*10<len(matching):
            query={k:v[0] for k,v in params.items()};query.update(module='bank',page=str(p));out.append('<a href="app?%s">%s</a>'%(esc(urlencode(query)),label))
    out.append('</nav>');return library_layout(sidebar,''.join(out))


def page(repo,user,params,base_path=''):
    require_student(user);c=repo.conn;uid=user['id']
    all_wrongs=group_wrongs(repo,user);types,levels=preferences(c,uid)
    due=[g for g in all_wrongs if any(w['progress']['available'] and (not types or w.get('filter_kind',w['kind']) in types) and (not levels or w['level'] in levels) for w in g['members'])]
    value=lambda k:(params.get(k) or [''])[0]
    wid=value('practice')
    if value('review') and not wid and due:wid=due[0]['id']
    module=value('module') or 'home'
    out=['<section class="panel learning student-workspace"><header class="student-workspace-heading"><h1>我的学习</h1><details class="student-settings"><summary>关注设置</summary>']
    from .learning_views import form,hidden
    settings='<fieldset><legend>关注题型</legend>'+''.join('<label><input type="checkbox" name="types" value="%s"%s>%s</label>'%(k,' checked' if k in types else '',v) for k,v in TYPES.items())+'</fieldset>'
    settings+='<fieldset><legend>关注难度</legend>'+''.join('<label><input type="checkbox" name="levels" value="%s"%s>%s</label>'%(l,' checked' if l in levels else '',l) for l in LEVELS)+'</fieldset><p>未选择表示关注全部；只影响待复习队列，错题仍完整保留。</p><button>保存设置</button>'
    out.append(form('student-preferences',settings)+'</details></header>')
    out.append('<a class="student-review-count" href="app?review=1">待复习 <strong>%s</strong> 题</a>'%len(due))
    # Keep the student graph entry offline until the user explicitly requests restoration.
    modules=(('wrong','错题本'),('history','历史测试'),('bank','题库'))
    out.append('<nav class="student-module-nav" aria-label="学习模块">'+''.join('<a href="app?module=%s"%s>%s</a>'%(k,' aria-current="page"' if k==module else '',v) for k,v in modules)+'</nav>')
    if value('trial'):
        t=owned_trial(c,user,value('trial'));out.append(practice(repo,user,t,base_path,'app?module=bank'))
    elif wid:
        w=next((g for g in all_wrongs if any(member['id']==wid for member in g['members'])),None)
        if not w:raise PermissionDenied('错题不存在或尚未发布')
        # Carry the remaining queue through sequential requests; after saving only the next question appears.
        remaining=(params.get('queue') or [''])[0].split(',') if value('queue') else [item['id'] for item in due]
        remaining=[rid for rid in remaining if rid and rid!=w['id'] and any(item['id']==rid for item in due)]
        next_url='app?practice='+quote(remaining[0])+'&queue='+quote(','.join(remaining[1:])) if remaining else 'app?module=history&completed=1'
        out.append(group_practice(repo,user,w,base_path,next_url))
    elif module=='wrong':
        out.append('<h2>错题本</h2>')
        if value('mastered'):out.append('<p role="status">已移出错题本，不再提示复习。</p>')
        tag_sets=tag_filters(repo,user,params)
        from .wrong_causes import matches as cause_matches
        matched=[g for g in all_wrongs if any(match(w,params,tag_sets) for w in g['members']) and cause_matches(c,user,g,value('cause'))]
        cards=['<p>共 %s 道错题</p>'%len(matched)]
        from .wrong_causes import panel as cause_panel
        for i,g in enumerate(matched,1):
            w=g['anchor'];title=('第 %s 题'%g['number']) if g['number'] else ('错题 %s'%i)
            cards.append('<article class="student-wrong"><h3>%s</h3>%s%s%s<a href="app?practice=%s">查看、诊断与重做</a>%s</article>'%(esc(title),wrong_render(c,w,user,base_path),group_first_record(c,g,user),cause_panel(c,user,g),quote(g['id']),form('wrong-mastered',hidden('wrong_id',g['id'])+'<button type="submit">已掌握</button>')))
        if not matched:cards.append('<p>没有匹配的错题，请调整筛选条件。</p>')
        out.append(library_layout(filters(repo,user,'wrong',params),''.join(cards)))
    elif module=='history':
        out.append('<h2>历史测试</h2>'+('<p role="status">本轮复习已完成。</p>' if value('completed') else '')+history(repo,user))
    elif module=='graph':
        from .learning_graph import student_page
        out.append('<h2>知识图谱</h2>'+student_page(repo,user))
    elif module=='bank':out.append('<h2>题库</h2>'+bank(repo,user,params,base_path))
    elif module!='home':raise InvalidRequest('学习模块不存在')
    if value('review') and not wid:out.append('<p>当前关注范围内暂无待复习题目，可进入题库试做。</p>')
    out.append('<link rel="stylesheet" href="assets/diagnosis.css?v=20261008-adaptive-v1"><script src="assets/diagnosis.js?v=20261008-adaptive-v1" defer></script>')
    out.append('</section><link rel="stylesheet" href="assets/student-learning.css?v=20261008-wrong-causes-v1"><script src="assets/student-learning.js?v=20261008-wrong-causes-v1" defer></script>')
    from .learning_views import footer
    return ''.join(out)+footer()
