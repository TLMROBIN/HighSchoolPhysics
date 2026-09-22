"""Outcome-only classroom workflow. Scores are not learning evidence."""
import csv
import io
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .errors import InvalidRequest, PermissionDenied, StateConflict
from .grading import grade_answer
from .repository import loads, dumps

LABELS = {'correct':'本次正确','wrong':'需再练','blank':'空白','pending':'待教师确认'}
TZ = ZoneInfo('Asia/Shanghai')

def now():
    return datetime.now(timezone.utc).isoformat()

def instant(value):
    dt = datetime.fromisoformat(value.replace('Z','+00:00'))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

def day(value):
    return instant(value).astimezone(TZ).date()

def enabled(conn):
    return bool(conn.execute("select 1 from sqlite_master where name='learning_state'").fetchone())

def migrate(conn):
    if conn.execute("select 1 from sqlite_master where name='learning_state'").fetchone(): return
    # Keep a private, reversible pre-migration archive; never overwrite one.
    path = conn.execute('pragma database_list').fetchone()[2]
    if path:
        from pathlib import Path
        backup = Path(path).with_name('pre-outcome-' + uuid.uuid4().hex[:8] + '.sqlite3')
        with sqlite3.connect(str(backup)) as target: conn.backup(target)
        backup.chmod(0o600)
    conn.commit()
    conn.execute('pragma foreign_keys=off')
    try:
        conn.execute('begin immediate')
        schema = conn.execute("select sql from sqlite_master where name='wrong_questions'").fetchone()[0]
        schema = re.sub(r'CREATE TABLE (?:IF NOT EXISTS )?["`]?wrong_questions["`]?', 'CREATE TABLE wrong_questions_new', schema, flags=re.I)
        schema = re.sub(r'\b(score|max_score) integer not null', r'\1 integer', schema, flags=re.I)
        indexes = [r[0] for r in conn.execute("select sql from sqlite_master where tbl_name='wrong_questions' and type='index' and sql is not null")]
        conn.execute(schema)
        conn.execute('insert into wrong_questions_new select * from wrong_questions')
        conn.execute('drop table wrong_questions')
        conn.execute('alter table wrong_questions_new rename to wrong_questions')
        for sql in indexes: conn.execute(sql)
        for table in ('student_responses','redo_attempts'):
            conn.execute("alter table %s add column outcome text not null default 'pending'" % table)
            conn.execute("update %s set outcome=case when score is null then 'pending' when trim(coalesce(%s,''))='' then 'blank' when score=max_score then 'correct' else 'wrong' end" % (table,'final_answer' if table=='student_responses' else 'answer'))
        conn.execute("alter table student_responses add column initial_answer text")
        conn.execute("update student_responses set initial_answer=coalesce(final_answer,raw_answer,'')")
        conn.execute("alter table redo_attempts add column purpose text not null default 'legacy'")
        conn.execute("alter table redo_attempts add column request_key text")
        conn.execute('create unique index redo_request_key on redo_attempts(student_id,request_key) where request_key is not null')
        conn.execute('create table learning_views(student_id text, question_id text, viewed_at text, primary key(student_id,question_id))')
        conn.execute('create table learning_settings(class_id text primary key, daily_limit integer not null default 5)')
        for table in ('student_responses','redo_attempts','wrong_questions'):
            conn.execute('update %s set score=null,max_score=null' % table)
        conn.execute("update student_responses set confirmed_score=null,score_reason=''")
        # Remove numeric grading metadata from active stores; retain original private archive.
        for table,cols in (('assessment_sessions',['full_score']),('paper_questions',['points']),('question_version_snapshots',['points'])):
            for col in cols: conn.execute('update %s set %s=0' % (table,col))
        for table,col in (('question_version_snapshots','grading_rule_json'),('question_version_snapshots','answer_json'),('questions','answer_json')):
            for row in conn.execute('select rowid,%s from %s' % (col,table)).fetchall():
                value=loads(row[1],{})
                if isinstance(value,dict):
                    for key in ('points','partial_points','score','max_score'): value.pop(key,None)
                    conn.execute('update %s set %s=? where rowid=?' % (table,col),(dumps(value),row[0]))
        conn.execute('delete from exam_upload_chunks')
        for row in conn.execute('select id,title from assessment_sessions').fetchall():
            title=re.sub(r'[（(]\s*\d+(?:\.\d+)?分范围\s*[）)]','',row['title']).strip()
            conn.execute('update assessment_sessions set title=? where id=?',(title,row['id']))

        for row in conn.execute('select rowid,result_json from exam_imports').fetchall():
            value=loads(row[1],{})
            def clean(v):
                if isinstance(v,dict): return {k:clean(x) for k,x in v.items() if k not in ('score','max_score','full_score','average','confirmed_score')}
                if isinstance(v,list): return [clean(x) for x in v]
                return v
            conn.execute('update exam_imports set result_json=? where rowid=?',(dumps(clean(value)),row[0]))
        conn.execute("create trigger immutable_first_answer before update of initial_answer on student_responses when (select grading_status from assessment_sessions where id=old.assessment_id)='published' and new.initial_answer is not old.initial_answer begin select raise(abort,'Published first answers are immutable'); end")
        conn.execute('create table learning_state(version integer, migrated_at text)')
        conn.execute('insert into learning_state values(1,?)',(now(),))
        if conn.execute('pragma foreign_key_check').fetchall(): raise RuntimeError('Outcome migration foreign-key check failed')
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally: conn.execute('pragma foreign_keys=on')

def check(rule, answer):
    if not str(answer).strip(): return 'blank'
    if rule.get('type') not in ('single_choice','multiple_choice','fill'): raise InvalidRequest('目前只支持选择题和填空题')
    if rule.get('type') in ('single_choice','multiple_choice') and not re.fullmatch(r'[A-Fa-f,，、;；\s]+',str(answer)): return 'pending'
    if grade_answer(dict(rule,points=1),answer)['correct']: return 'correct'
    return 'pending' if rule.get('type')=='fill' else 'wrong'

def progress(conn, wrong, today=None):
    today=today or datetime.now(TZ).date()
    version=snapshot(conn,wrong)['question_version']
    originals=conn.execute("select r.created_at from student_responses r join assessment_sessions a on a.id=r.assessment_id join question_version_snapshots s on s.id=r.snapshot_id where r.student_id=? and r.question_id=? and s.question_version=? and r.outcome in ('wrong','blank') and a.grading_status='published'",(wrong['student_id'],wrong['question_id'],version)).fetchall()
    events=[(r[0],'wrong','original') for r in originals]
    attempts=conn.execute('select a.* from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id join student_responses r on r.id=w.response_id join question_version_snapshots s on s.id=r.snapshot_id where a.student_id=? and w.question_id=? and s.question_version=? order by a.submitted_at,a.id',(wrong['student_id'],wrong['question_id'],version)).fetchall()
    events += [(a['submitted_at'],a['outcome'],a['purpose']) for a in attempts if a['purpose']=='verify']
    count=0; due=today; last='需再练'
    for date,result,purpose in sorted(events,key=lambda event: instant(event[0])):
        d=day(date)
        if result=='pending': continue
        last=LABELS[result]
        if result in ('wrong','blank'): count=0; due=d+timedelta(days=1)
        elif count<3 and d>=due:
            count+=1; due=d+timedelta(days=(3 if count==1 else 7))
    pending=any(a['outcome']=='pending' for a in attempts)
    status='待教师确认' if pending else '本题已巩固' if count>=3 else '等待下次验证' if due>today else '需再练'
    return dict(count=count,due=str(due),status=status,last=last,pending=pending,available=not pending and count<3 and due<=today)

def snapshot(conn, wrong):
    result=dict(conn.execute('select s.* from student_responses r join question_version_snapshots s on s.id=r.snapshot_id where r.id=?',(wrong['response_id'],)).fetchone())
    result['question_type']=loads(result['grading_rule_json'],{}).get('type','fill')
    return result

def submit(repo, actor, payload):
    wrong=repo._require_wrong_question_student(actor,payload['wrong_id'])
    if repo.conn.execute('select grading_status from assessment_sessions where id=?',(wrong['assessment_id'],)).fetchone()[0]!='published': raise PermissionDenied('尚未发布')
    answer=payload.get('answer','')
    if isinstance(answer,list): answer=','.join(sorted(set(answer)))
    key=str(payload.get('request_key',''))
    if not key or len(key)>100: raise InvalidRequest('缺少提交标识，请刷新后重试')
    c=repo.conn
    c.execute('begin immediate')
    try:
        old=c.execute('select * from redo_attempts where student_id=? and request_key=?',(actor,key)).fetchone()
        if old:
            if old['answer']!=answer or old['wrong_question_id']!=wrong['id']: raise StateConflict('重复请求内容不一致')
            c.rollback();return dict(old)
        if c.execute("select 1 from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id where a.student_id=? and w.question_id=? and a.outcome='pending'",(actor,wrong['question_id'])).fetchone(): raise StateConflict('这道题有一次作答待教师确认，请勿重复提交')
        p=progress(c,wrong)
        viewed=c.execute('select viewed_at from learning_views where student_id=? and question_id=?',(actor,wrong['question_id'])).fetchone()
        purpose='verify' if payload.get('purpose')=='verify' and not (viewed and day(viewed[0])==datetime.now(TZ).date()) else 'learn'
        outcome=check(loads(snapshot(c,wrong)['grading_rule_json'],{}),answer)
        aid='redo-'+uuid.uuid4().hex[:12]
        c.execute('insert into redo_attempts(id,school_id,wrong_question_id,student_id,answer,status,outcome,purpose,request_key,submitted_at) values(?,?,?,?,?,?,?,?,?,?)',(aid,wrong['school_id'],wrong['id'],actor,answer,'submitted' if outcome=='pending' else 'reviewed',outcome,purpose,key,now()))
        c.commit()
        return dict(c.execute('select * from redo_attempts where id=?',(aid,)).fetchone())
    except Exception: c.rollback();raise

def staff_assessment(repo,user,aid):
    if user['role'] not in ('admin','teacher'): raise PermissionDenied('需要教师身份')
    a=repo.assessment_detail(user['id'],aid)
    if a['school_id']!=user['school_id']: raise PermissionDenied('不可跨学校操作')
    return a

def api(repo,user,action,p):
    c=repo.conn;actor=user['id']
    if action=='submit': return submit(repo,actor,p)
    if action=='solution':
        w=repo._require_wrong_question_student(actor,p['wrong_id'])
        if c.execute('select grading_status from assessment_sessions where id=?',(w['assessment_id'],)).fetchone()[0]!='published': raise PermissionDenied('尚未发布')
        c.execute('insert into learning_views values(?,?,?) on conflict(student_id,question_id) do update set viewed_at=excluded.viewed_at',(actor,w['question_id'],now()));c.commit()
        s=snapshot(c,w)
        q=repo.get_question(w['question_id'])
        return dict(answer=loads(s['grading_rule_json'],{}).get('answer'),analysis=q['analysis'],message='已进入学习练习，本日不增加验证次数')
    if user['role'] not in ('admin','teacher'): raise PermissionDenied('需要教师身份')
    if action=='settings':
        group=c.execute('select * from class_groups where id=? and school_id=?',(p['class_id'],user['school_id'])).fetchone()
        if not group: raise InvalidRequest('班级不存在')
        repo._require_assessment_class_actor(actor,group['id'])
        limit=int(p['daily_limit'])
        if not 1<=limit<=20: raise InvalidRequest('每天每组题数应在 1—20 之间')
        c.execute('insert into learning_settings values(?,?) on conflict(class_id) do update set daily_limit=excluded.daily_limit',(group['id'],limit));c.commit()
        return {'message':'每日练习组大小已更新'}
    if action=='tags':
        ids=p.get('questions',[]);ids=[ids] if isinstance(ids,str) else ids
        if not ids: raise InvalidRequest('请先选择题目')
        for qid in ids:
            if repo.get_question(qid)['school_id']!=user['school_id']: raise PermissionDenied('不可跨学校操作')
        for qid in ids: repo.confirm_question_tags(actor,qid,knowledge_node_ids=[p['knowledge']],ability_tag_ids=[p['ability']])
        return {'message':'题库标签已确认；已发布周测的标签快照保持原样'}
    if action=='review':
        outcome=p.get('outcome')
        if outcome not in ('correct','wrong','blank'): raise InvalidRequest('请选择正确、错误或空白')
        a=c.execute('select * from redo_attempts where id=?',(p['attempt_id'],)).fetchone()
        if not a: raise InvalidRequest('作答不存在')
        repo._require_wrong_question_reviewer(actor,a['wrong_question_id'])
        c.execute('update redo_attempts set outcome=?,status=\'reviewed\',feedback=?,reviewed_by=?,reviewed_at=? where id=?',(outcome,p.get('feedback',''),actor,now(),a['id']));c.commit()
        return {'message':'已复核；复习进度按原提交时间重新计算'}
    if action=='question':
        kind=p.get('question_type')
        if kind not in ('single_choice','multiple_choice','fill'): raise InvalidRequest('只支持选择题、填空题')
        options={chr(65+i):x.strip() for i,x in enumerate(p.get('options','').splitlines()) if x.strip()}
        if not p.get('knowledge') or not p.get('ability') or not p.get('stem','').strip() or not p.get('answer','').strip(): raise InvalidRequest('请填写题干、答案并明确选择标签')
        q=repo.create_question(actor,p['stem'],options,{'answer':p['answer'],'match':'exact'},p.get('analysis',''),kind,'教师录入','高三','', 'medium')
        repo.confirm_question_tags(actor,q['id'],knowledge_node_ids=[p['knowledge']],ability_tag_ids=[p['ability']])
        if p.get('image'):
            import base64
            raw=base64.b64decode(p['image'],validate=True)
            if len(raw)>650000 or not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise InvalidRequest('原题图片需为 PNG 且小于 650KB')
            c.execute('insert into exam_assets(id,school_id,question_id,png) values(?,?,?,?)',('asset-'+uuid.uuid4().hex[:16],user['school_id'],q['id'],raw));c.commit()

        return {'message':'题目与标签已保存'}
    if action=='assessment':
        group=c.execute('select * from class_groups where id=? and school_id=?',(p['class_id'],user['school_id'])).fetchone()
        if not group: raise InvalidRequest('班级不存在')
        repo._require_assessment_class_actor(actor,group['id'])
        ids=p.get('questions',[]);ids=[ids] if isinstance(ids,str) else ids
        if not ids: raise InvalidRequest('请至少选择一道题')
        for qid in ids:
            q=repo.get_question(qid)
            if q['school_id']!=user['school_id'] or q['question_type'] not in ('single_choice','multiple_choice','fill'): raise InvalidRequest('题目不在本次范围')
            tags=repo.tags_for_question(qid)
            if not all(any(t['tag_type']==kind for t in tags) for kind in ('knowledge','ability')): raise InvalidRequest('请先为每道题确认知识点和能力标签')
        if not c.execute("select 1 from knowledge_ontology_versions where status='active'").fetchone(): raise InvalidRequest('请管理员先在系统管理中发布知识体系，再创建周测')
        paper=repo.assemble_paper(actor,p['title'],'教师录入',[dict(question_id=q,points=0) for q in ids])
        a=repo.create_assessment_from_paper(actor,paper['paper']['id'],group['id'],p['title'],'','高三',p.get('date',''))
        return {'message':'周测已建立，请录入并核对作答','url':'exams?id='+a['id']}
    a=staff_assessment(repo,user,p['assessment_id'])
    if action in ('answers','participant','publish') and a['grading_status']=='published': raise StateConflict('已发布的首次作答不可覆盖；请保留原记录')
    if action=='participant':
        if p['status'] not in ('present','absent','not_included'): raise InvalidRequest('无效状态')
        c.execute('update assessment_participants set status=? where assessment_id=? and student_id=?',(p['status'],a['id'],p['student_id']));c.commit();return {'message':'纳入范围已更新'}
    if action=='answers':
        reader=csv.DictReader(io.StringIO(p.get('csv','').lstrip('\ufeff')))
        rows=list(reader)
        if not set(('学生','题号','作答','结果')).issubset(reader.fieldnames or []): raise InvalidRequest('表格列名必须包含：学生,题号,作答,结果')
        if not rows: raise InvalidRequest('请提供 CSV 表格，列名：学生,题号,作答,结果')
        prepared=[]
        for row in rows:
            if any(row.get(k) is None for k in ('学生','题号','作答','结果')): raise InvalidRequest('表格有缺失单元格，请明确填写空白或待确认，不要把缺失当空白')
            if row['结果'] not in ('','正确','错误','空白','待确认'): raise InvalidRequest('结果只能填正确、错误、空白或待确认')
            students=c.execute('select u.* from users u join assessment_participants p on p.student_id=u.id where p.assessment_id=? and p.status=\'present\' and (u.username=? or u.display_name=? or u.student_no=?)',(a['id'],row['学生'],row['学生'],row['学生'])).fetchall()
            if len(students)!=1: raise InvalidRequest('学生无法唯一匹配：'+row['学生'])
            s=c.execute('select s.* from question_version_snapshots s where assessment_id=? and position=?',(a['id'],row['题号'])).fetchone()
            if not s: raise InvalidRequest('题号不存在：'+row['题号'])
            answer=row.get('作答','');outcome={'正确':'correct','错误':'wrong','空白':'blank','待确认':'pending'}.get(row.get('结果','')) or check(loads(s['grading_rule_json'],{}),answer)
            if outcome=='blank' and answer.strip(): raise InvalidRequest('非空答案不可标为空白')
            prepared.append((students[0],s,answer,outcome))
        if len({(u['id'],s['id']) for u,s,_,_ in prepared})!=len(prepared): raise InvalidRequest('表格包含重复学生题号')
        if not p.get('confirm'): return {'message':'预览通过：%s 条作答，%s 条待确认。确认后保存。'%(len(prepared),sum(x[3]=='pending' for x in prepared)),'preview':True}
        with c:
            for u,s,answer,outcome in prepared:
                c.execute('''insert into student_responses(id,school_id,assessment_id,student_id,question_id,snapshot_id,raw_answer,final_answer,initial_answer,outcome,review_status,grading_status) values(?,?,?,?,?,?,?,?,?,?,?,?) on conflict(assessment_id,student_id,question_id) do update set final_answer=excluded.final_answer,initial_answer=excluded.initial_answer,outcome=excluded.outcome,review_status=excluded.review_status''',('resp-'+uuid.uuid4().hex[:12],user['school_id'],a['id'],u['id'],s['question_id'],s['id'],answer,answer,answer,outcome,'pending' if outcome=='pending' else 'reviewed','reviewed'))
        return {'message':'作答已保存；发布前仍可核对修正'}
    if action=='publish':
        students=c.execute("select student_id from assessment_participants where assessment_id=? and status='present'",(a['id'],)).fetchall()
        qs=c.execute('select * from question_version_snapshots where assessment_id=?',(a['id'],)).fetchall()
        if not students or not qs: raise InvalidRequest('没有纳入学生或题目')
        rows=[]
        for u in students:
            for s in qs:
                r=c.execute('select * from student_responses where assessment_id=? and student_id=? and question_id=?',(a['id'],u[0],s['question_id'])).fetchone()
                if not r or r['outcome']=='pending': raise StateConflict('仍有缺失或待确认作答，不能发布')
                rows.append((r,s))
        with c:
            for r,s in rows:
                if r['outcome']!='correct':
                    c.execute('insert into wrong_questions(id,school_id,assessment_id,student_id,question_id,response_id,wrong_answer,correct_answer_json,score,max_score) values(?,?,?,?,?,?,?,?,null,null)',('wrong-'+uuid.uuid4().hex[:12],user['school_id'],a['id'],r['student_id'],r['question_id'],r['id'],r['initial_answer'],s['answer_json']))
            c.execute("update assessment_sessions set grading_status='published',status='published' where id=?",(a['id'],))
        return {'message':'已发布，学生可查看自己的结果和错题'}
    raise InvalidRequest('未知操作')
