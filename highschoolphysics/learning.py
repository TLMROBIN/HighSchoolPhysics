"""Outcome-only classroom workflow. Scores are not learning evidence."""
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .errors import InvalidRequest, PermissionDenied, StateConflict
from .repository import loads, dumps
from .question_content import render_snapshot_content, render_snapshot_solution, snapshot_content

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
    if conn.execute("select 1 from sqlite_master where name='learning_state'").fetchone():
        from .response_workflow import migrate as migrate_evidence
        migrate_evidence(conn)
        from .student_learning import migrate as migrate_student
        migrate_student(conn)
        return
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
    from .response_workflow import migrate as migrate_evidence
    migrate_evidence(conn)
    from .student_learning import migrate as migrate_student
    migrate_student(conn)

def check(rule, answer):
    from .outcomes import decide
    return decide(rule, answer)['outcome']

def outcome_for_snapshot(conn, snapshot_row, school_id, answer):
    from .response_workflow import snapshot_decision
    return snapshot_decision(conn,snapshot_row,school_id,answer)['outcome']

def progress(conn, wrong, today=None):
    today=today or datetime.now(TZ).date()
    version=snapshot(conn,wrong)['question_version']
    originals=conn.execute("select coalesce(r.effective_from,r.created_at) from student_responses r join assessment_sessions a on a.id=r.assessment_id join question_version_snapshots s on s.id=r.snapshot_id join assessment_participants participant on participant.assessment_id=r.assessment_id and participant.student_id=r.student_id where participant.status='present' and r.student_id=? and r.question_id=? and s.question_version=? and r.outcome in ('wrong','blank') and a.grading_status='published'",(wrong['student_id'],wrong['question_id'],version)).fetchall()
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
    pending=any(a['outcome']=='pending' and c_active(conn,a['wrong_question_id']) for a in attempts)
    if not originals:
        return dict(count=0,due=str(today),status='本次错误记录已撤销',last='已更正',pending=False,available=False)
    status='待教师确认' if pending else '本题已巩固' if count>=3 else '等待下次验证' if due>today else '需再练'
    return dict(count=count,due=str(due),status=status,last=last,pending=pending,available=not pending and count<3 and due<=today)

def c_active(conn, wrong_id):
    row=conn.execute('select is_active from wrong_questions where id=?',(wrong_id,)).fetchone()
    return bool(row and row[0])

def snapshot(conn, wrong):
    result=dict(conn.execute('select s.* from student_responses r join question_version_snapshots s on s.id=r.snapshot_id where r.id=?',(wrong['response_id'],)).fetchone())
    result['question_type']=loads(result['grading_rule_json'],{}).get('type','fill')
    return result

def submit(repo, actor, payload):
    wrong=repo._require_wrong_question_student(actor,payload['wrong_id'])
    if repo.conn.execute('select grading_status from assessment_sessions where id=?',(wrong['assessment_id'],)).fetchone()[0]!='published': raise PermissionDenied('尚未发布')
    if not c_active(repo.conn,wrong['id']): raise StateConflict('该错误来源已撤销，请刷新错题本')
    if payload.get('self_outcome') and payload.get('unified')!='1':
        raise InvalidRequest('请通过学生自评入口提交')
    answer=payload.get('answer','')
    if isinstance(answer,list): answer=','.join(sorted(set(answer)))
    key=str(payload.get('request_key',''))
    if not key or len(key)>100: raise InvalidRequest('缺少提交标识，请刷新后重试')
    c=repo.conn
    c.execute('begin immediate')
    try:
        old=c.execute('select * from redo_attempts where student_id=? and request_key=?',(actor,key)).fetchone()
        if old:
            if old['answer']!=answer or old['wrong_question_id']!=wrong['id'] or (payload.get('self_outcome') and old['outcome']!=payload['self_outcome']): raise StateConflict('重复请求内容不一致')
            c.rollback();return dict(old)
        if c.execute("select 1 from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id where a.student_id=? and w.question_id=? and w.is_active=1 and a.outcome='pending'",(actor,wrong['question_id'])).fetchone(): raise StateConflict('这道题有一次作答待教师确认，请勿重复提交')
        p=progress(c,wrong)
        viewed=c.execute('select viewed_at from learning_views where student_id=? and question_id=?',(actor,wrong['question_id'])).fetchone()
        purpose='verify' if payload.get('unified')=='1' or (payload.get('purpose')=='verify' and not (viewed and day(viewed[0])==datetime.now(TZ).date())) else 'learn'
        question_snapshot = snapshot(c, wrong)
        if question_snapshot["question_type"] in ("single_choice", "multiple_choice") and not str(answer).strip():
            raise InvalidRequest("请先选择选项，再提交作答")
        if payload.get('unified')=='1':
            from .student_learning import self_outcome
            outcome = self_outcome(question_snapshot['question_type'],payload,viewed)
        else:
            outcome = None
        outcome = outcome or outcome_for_snapshot(c, question_snapshot, wrong["school_id"], answer)
        aid='redo-'+uuid.uuid4().hex[:12]
        c.execute('insert into redo_attempts(id,school_id,wrong_question_id,student_id,answer,status,outcome,purpose,request_key,submitted_at) values(?,?,?,?,?,?,?,?,?,?)',(aid,wrong['school_id'],wrong['id'],actor,answer,'submitted' if outcome=='pending' else 'reviewed',outcome,purpose,key,now()))
        if payload.get('self_outcome'):
            c.execute('update redo_attempts set self_reported=1 where id=?',(aid,))
        c.commit()
        return dict(c.execute('select * from redo_attempts where id=?',(aid,)).fetchone())
    except Exception: c.rollback();raise

def staff_assessment(repo,user,aid):
    if user['role'] not in ('admin','teacher'): raise PermissionDenied('需要教师身份')
    a=repo.assessment_detail(user['id'],aid)
    if a['school_id']!=user['school_id']: raise PermissionDenied('不可跨学校操作')
    return a


def _validate_complete_question_selection(repo, question_ids, school_id):
    supported_types = {'single_choice', 'multiple_choice', 'fill', 'short_answer', 'structured', 'experiment'}
    if len(question_ids) != len(set(question_ids)):
        raise InvalidRequest('题目列表包含重复小问')
    selected_by_group = {}
    for question_id in question_ids:
        question = repo.get_question(question_id)
        if question is None or question['school_id'] != school_id:
            raise InvalidRequest('题目不在本次范围')
        if question['question_type'] not in supported_types:
            raise InvalidRequest('暂不支持该题型，请先完成题型规范化')
        tags = repo.tags_for_question(question_id)
        from .question_bank import tags_ready
        if not tags_ready(repo,question_id,tags):
            raise InvalidRequest('请先在题库确认标签，每道题至少需要一个知识点；能力和素养可按实际依据留空')
        binding = repo.conn.execute(
            """select binding.group_id,binding.child_key,revision.document_json,revision.review_state
               from question_content_bindings binding
               join question_content_groups content_group on content_group.id=binding.group_id
               join question_content_revisions revision on revision.id=content_group.current_revision_id
               where binding.question_id=? and content_group.school_id=?""",
            (question_id, school_id),
        ).fetchone()
        if not binding:
            continue
        if binding['review_state'] != 'verified':
            raise InvalidRequest('原题结构尚未完成复核，暂不能加入试卷')
        document = loads(binding['document_json'], {})
        children = document.get('children', []) if isinstance(document, dict) else []
        if not children:
            continue
        group_id = binding['group_id']
        selected_by_group.setdefault(group_id, []).append((question_id, binding['child_key']))

    for group_id, selected in selected_by_group.items():
        binding_rows = repo.conn.execute(
            """select binding.question_id,binding.child_key,revision.document_json
               from question_content_bindings binding
               join question_content_groups content_group on content_group.id=binding.group_id
               join question_content_revisions revision on revision.id=content_group.current_revision_id
               where binding.group_id=? and content_group.school_id=? and revision.review_state='verified'""",
            (group_id, school_id),
        ).fetchall()
        document = loads(binding_rows[0]['document_json'], {}) if binding_rows else {}
        expected_keys = [child.get('key') for child in document.get('children', [])]
        expected_by_key = {row['child_key']: row['question_id'] for row in binding_rows}
        if (
            not expected_keys
            or len(binding_rows) != len(expected_keys)
            or set(expected_by_key) != set(expected_keys)
            or {question_id for question_id, _ in selected} != set(expected_by_key.values())
        ):
            raise InvalidRequest('请把整道大题及其全部小问一起加入试卷')
        expected_ids = [expected_by_key[key] for key in expected_keys]
        positions = [question_ids.index(question_id) for question_id in expected_ids]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise InvalidRequest('同一道大题的小问必须相邻排列')
        if [question_ids[position] for position in positions] != expected_ids:
            raise InvalidRequest('请按原题顺序排列大题中的小问')

def api(repo,user,action,p,base_path=""):
    c=repo.conn;actor=user['id']
    from . import response_workflow
    if action in ('student-preferences','bank-start','bank-solution','bank-submit','bank-add-wrong','personal-solution','personal-submit'):
        from .student_learning import api as student_api
        return student_api(repo,user,action,p,base_path)
    if action=='answers':
        if p.get('upload_id'):
            from .exam_import import staged_bundle
            p=staged_bundle(repo,actor,p['upload_id'])
        return response_workflow.import_answers(repo,user,p)
    if action=='missing-student':
        return response_workflow.confirm_missing_student(repo,user,p)
    if action=='unmatched-cards':
        return response_workflow.unmatched_cards(repo,user,p)
    if action in ('scan-upload','scan-status'):
        from .response_scans import api as scan_api
        return scan_api(repo,user,action,p)
    if action=='response-upload-chunk':
        from .exam_import import stage_chunk
        return stage_chunk(repo,actor,p)
    if action=='answers-file':
        repo._require_question_bank_actor(actor)
        from .response_files import read_file
        if p.get('upload_id'):
            from .exam_import import staged_bundle
            p=staged_bundle(repo,actor,p['upload_id'])
        return {'csv':read_file(p)}
    if action=='publish': return response_workflow.publish(repo,user,p)
    if action=='response-history': return response_workflow.history(repo,user,p)
    if action=='response-review': return response_workflow.review_or_correct(repo,user,p)
    if action=='response-correct': return response_workflow.review_or_correct(repo,user,p,correction=True)
    if action=='submit': return submit(repo,actor,p)
    if action=='solution':
        w=repo._require_wrong_question_student(actor,p['wrong_id'])
        if c.execute('select grading_status from assessment_sessions where id=?',(w['assessment_id'],)).fetchone()[0]!='published': raise PermissionDenied('尚未发布')
        c.execute('insert into learning_views values(?,?,?) on conflict(student_id,question_id) do update set viewed_at=excluded.viewed_at',(actor,w['question_id'],now()));c.commit()
        s=snapshot(c,w)
        q=repo.get_question(w['question_id'])
        school_id = user.get("school_id") if hasattr(user, "get") else None
        if not school_id:
            owner = c.execute("select school_id from users where id=?", (actor,)).fetchone()
            school_id = owner["school_id"] if owner else ""
        content = snapshot_content(c, s["id"], school_id)
        if content is not None:
            item = content["document"]
            if content["child_key"]:
                child = next((row for row in item["children"] if row["key"] == content["child_key"]), {})
                answer = child.get("answer_md", "")
                analysis = child.get("analysis_md", "")
            else:
                answer = item.get("answer_md", "")
                analysis = item.get("analysis_md", "")
            return {
                "answer": answer,
                "analysis": analysis,
                "solution_html": render_snapshot_solution(c,s["id"],school_id,base_path),
                "message": "答案与解析已显示",
                "solution_available": bool(render_snapshot_content(c,s["id"],school_id,base_path,include_solution=True) and (answer or analysis)),
            }
        from .fill_rules import reference_answer
        return dict(answer=reference_answer(loads(s['grading_rule_json'],{})),analysis=q['analysis'],message='答案与解析已显示')
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
        if not p.get('knowledge') or not p.get('ability') or not p.get('literacy'):
            raise InvalidRequest('请同时选择知识点、能力和素养标签')
        for qid in ids:
            repo.confirm_question_tags(
                actor,
                qid,
                knowledge_node_ids=[p['knowledge']],
                ability_tag_ids=[p['ability']],
                literacy_tag_ids=[p['literacy']],
            )
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
        if not p.get('knowledge') or not p.get('ability') or not p.get('literacy') or not p.get('stem','').strip() or not p.get('answer','').strip(): raise InvalidRequest('请填写题干、答案并明确选择知识点、能力和素养标签')
        from .fill_rules import teacher_rule
        try:
            rule=teacher_rule(p) if kind=='fill' else {'answer':p['answer'],'match':'exact'}
        except (ValueError, ArithmeticError) as exc:
            raise InvalidRequest(str(exc)) from exc
        q=repo.create_question(actor,p['stem'],options,rule,p.get('analysis',''),kind,'教师录入','高三','', 'medium')
        repo.confirm_question_tags(actor,q['id'],knowledge_node_ids=[p['knowledge']],ability_tag_ids=[p['ability']],literacy_tag_ids=[p['literacy']])
        if p.get('image'):
            import base64
            raw=base64.b64decode(p['image'],validate=True)
            if len(raw)>650000 or not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise InvalidRequest('原题图片需为 PNG 且小于 650KB')
            c.execute('insert into exam_assets(id,school_id,question_id,png) values(?,?,?,?)',('asset-'+uuid.uuid4().hex[:16],user['school_id'],q['id'],raw));c.commit()

        return {'message':'题目与标签已保存'}
    if action=='assessment':
        from .exam_workflow import resolve_scope
        grade, groups, whole_grade = resolve_scope(c,user,p)
        group=groups[0]
        paper_id = p.get('paper_id')
        if paper_id:
            if not c.execute('select 1 from papers where id=? and school_id=?',(paper_id,user['school_id'])).fetchone():
                raise InvalidRequest('试卷不存在')
            ids=[r[0] for r in c.execute('select question_id from paper_questions where paper_id=? order by position',(paper_id,))]
        else:
            ids=p.get('questions',[]);ids=[ids] if isinstance(ids,str) else ids
        if not ids: raise InvalidRequest('请至少选择一道题')
        _validate_complete_question_selection(repo, ids, user['school_id'])
        if not c.execute("select 1 from knowledge_ontology_versions where status='active'").fetchone(): raise InvalidRequest('请管理员先在系统管理中发布知识体系，再创建周测')
        if not paper_id:
            paper=repo.assemble_paper(actor,p['title'],'教师录入',[dict(question_id=q,points=0) for q in ids])
            paper_id=paper['paper']['id']
        a=repo.create_assessment_from_paper(actor,paper_id,group['id'],p['title'],'',grade,p.get('date',''),class_ids=[r['id'] for r in groups],whole_grade=whole_grade)
        return {'message':'周测已建立，请录入并核对作答','url':'exams?id='+a['id']}
    a=staff_assessment(repo,user,p['assessment_id'])
    if action in ('answers','participant','publish') and a['grading_status']=='published': raise StateConflict('已发布的首次作答不可覆盖；请保留原记录')
    if action=='participant':
        if p['status'] not in ('present','absent','not_included'): raise InvalidRequest('无效状态')
        c.execute('update assessment_participants set status=? where assessment_id=? and student_id=?',(p['status'],a['id'],p['student_id']));c.commit();return {'message':'纳入范围已更新'}
    raise InvalidRequest('未知操作')
