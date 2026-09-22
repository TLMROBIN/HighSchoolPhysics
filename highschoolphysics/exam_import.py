"""Validated, atomic import of reviewed choice/fill exam bundles.

The bundle contains transcribed answers and evidence, not unreviewed OCR output.
"""
import base64
import hashlib
import json
import uuid

from .errors import InvalidRequest, PermissionDenied, StateConflict
from .repository import PhysicsRepository, dumps


class _TransactionConnection:
    """Reuse repository operations without allowing their intermediate commits."""
    def __init__(self, conn):
        self.conn = conn
    def __getattr__(self, name):
        return getattr(self.conn, name)
    def commit(self):
        pass


def _image(value):
    try:
        data = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise InvalidRequest('图片编码无效') from exc
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 8 * 1024 * 1024:
        raise InvalidRequest('仅接收8MB以内的PNG题图与答题图')
    return data


def validate_bundle(repo, actor_id, bundle):
    actor = repo._require_question_bank_actor(actor_id)
    if not repo.conn.execute("select 1 from knowledge_ontology_versions where school_id=? and status='active'", (actor['school_id'],)).fetchone():
        raise StateConflict('请先在管理员知识体系页面发布已核对的知识与能力体系，再导入考试')
    if bundle.get('format') != 'hsp-exam-v1':
        raise InvalidRequest('请选择hsp-exam-v1考试整理包')
    if not bundle.get('batch_key') or not bundle.get('title'):
        raise InvalidRequest('缺少批次标识或考试名称')
    questions = bundle.get('questions', [])
    classes = bundle.get('classes', [])
    if not questions or not classes or len(questions) > 300:
        raise InvalidRequest('需要题目与班级作答数据（最多300个评分项）')
    codes = set()
    points = {}
    for q in questions:
        code = q.get('number')
        if not code or code in codes:
            raise InvalidRequest('题号缺失或重复')
        codes.add(code)
        if q.get('question_type') not in ('single_choice', 'multiple_choice', 'fill'):
            raise InvalidRequest('目前仅导入选择题与填空题')
        p = q.get('points')
        if not isinstance(p, int) or isinstance(p, bool) or not 0 < p <= 100:
            raise InvalidRequest('每项分值须为1至100的整数')
        points[code] = p
        if not q.get('stem') or not q.get('answer'):
            raise InvalidRequest('题干和标准答案不能为空')
        if not isinstance(q['answer'], dict) or 'answer' not in q['answer']:
            raise InvalidRequest('评分规则需要answer字段')
        if not q.get('knowledge_node_ids') or not q.get('ability_tag_ids'):
            raise InvalidRequest('每个评分项都必须有知识点与能力标签')
        for kind, key in [('knowledge','knowledge_node_ids'),('ability','ability_tag_ids')]:
            repo._validate_tag_limit(kind, q[key])
            repo._assert_active_tags(actor['school_id'], kind, q[key])
        for image in q.get('images', []):
            _image(image)
        partial = q['answer'].get('partial_points', 0)
        if not isinstance(partial, int) or not 0 <= partial <= p:
            raise InvalidRequest('多选部分分超出范围')
    all_students = set()
    seen_classes = set()
    reports = []
    for group in classes:
        cid = group['class_id']
        repo._require_assessment_class_actor(actor_id, cid)
        cls = repo.conn.execute('select * from class_groups where id=?', (cid,)).fetchone()
        if not cls or cls['school_id'] != actor['school_id'] or cid in seen_classes:
            raise InvalidRequest('班级不存在、重复或不属于当前学校')
        seen_classes.add(cid)
        students = group.get('students', [])
        if not students:
            raise InvalidRequest('班级没有待导入作答')
        roster = {s['id']:s for s in repo.students_for_class(cid)}
        for student in students:
            sid = student['student_id']
            if sid not in roster or sid in all_students:
                raise InvalidRequest('学生不在目标班级或重复出现')
            if student.get('name') != roster[sid]['display_name']:
                raise InvalidRequest('学生姓名与账号不匹配')
            all_students.add(sid)
            responses = student.get('responses', {})
            if set(responses) != codes:
                raise InvalidRequest('学生逐题记录不完整；空白也需明确记录')
            _image(student['scan_image'])
            for code, item in responses.items():
                if not isinstance(item.get('answer'), str):
                    raise InvalidRequest('作答必须为文本，确认空白请用空字符串')
                if 'confirmed_score' in item:
                    score = item['confirmed_score']
                    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= points[code]:
                        raise InvalidRequest('复核分数超出题目满分')
                    if not item.get('score_reason'):
                        raise InvalidRequest('复核分数需要记录依据')
        missing = [s['display_name'] for sid,s in roster.items() if sid not in all_students]
        reports.append({'class_id':cid,'class_name':cls['name'],'students':len(students),'not_included':missing})
    return {'title':bundle['title'],'question_count':len(questions),'full_score':sum(points.values()),
            'student_count':len(all_students),'response_count':len(all_students)*len(codes),'classes':reports}


def import_bundle(repo, actor_id, bundle, preview=True):
    report = validate_bundle(repo, actor_id, bundle)
    digest = hashlib.sha256(dumps(bundle).encode()).hexdigest()
    actor = repo._actor(actor_id)
    prior = repo.conn.execute('select * from exam_imports where school_id=? and batch_key=?',
                             (actor['school_id'],bundle['batch_key'])).fetchone()
    if prior:
        if prior['payload_hash'] != digest:
            raise StateConflict('同一批次内容已变化，请使用成绩修订；不会覆盖已导入数据')
        report.update(json.loads(prior['result_json']))
        report['already_imported'] = True
        return report
    if preview:
        report['status'] = 'preview'
        return report
    conn = repo.conn
    conn.execute('BEGIN IMMEDIATE')
    tx = PhysicsRepository(_TransactionConnection(conn))
    try:
        # Recheck while holding the write lock (concurrent double submission).
        if conn.execute('select 1 from exam_imports where school_id=? and batch_key=?',
                        (actor['school_id'],bundle['batch_key'])).fetchone():
            raise StateConflict('该批次正在或已经导入，请重新预览')
        question_ids = {}
        def asset(image, question_id=None, assessment_id=None, student_id=None):
            aid = 'media-' + uuid.uuid4().hex
            conn.execute('insert into exam_assets(id,school_id,question_id,assessment_id,student_id,png) values(?,?,?,?,?,?)',
                         (aid,actor['school_id'],question_id,assessment_id,student_id,_image(image)))
            return aid
        for item in bundle['questions']:
            q = tx.create_question(actor_id=actor_id, stem=item['stem'],options=item.get('options',{}),
                answer=item['answer'],analysis=item.get('analysis',''),question_type=item['question_type'],
                source=bundle['title'],grade=bundle.get('grade',''),chapter=item.get('chapter','综合'),
                difficulty='medium',quality_status='reviewed',original_question_number=item['number'])
            question_ids[item['number']] = q['id']
            tx.confirm_question_tags(actor_id,q['id'],knowledge_node_ids=item['knowledge_node_ids'],ability_tag_ids=item['ability_tag_ids'])
            for im in item.get('images',[]):
                asset(im,question_id=q['id'])
        paper = tx.assemble_paper(actor_id,bundle['title'],bundle.get('source','周测整理包'),
             [{'question_id':question_ids[q['number']],'points':q['points']} for q in bundle['questions']])
        assessments = []
        for group in bundle['classes']:
            a = tx.create_assessment_from_paper(actor_id,paper['paper']['id'],group['class_id'],
                group.get('title',bundle['title']),bundle.get('term',''),bundle.get('grade',''),bundle.get('scheduled_at',''))
            aid = a['id']
            included = {s['student_id'] for s in group['students']}
            # Keep non-scanned classmates explicit, never silently create zero scores.
            conn.execute("update assessment_participants set status='not_included' where assessment_id=?",(aid,))
            items = []
            evidence = {}
            for student in group['students']:
                sid = student['student_id']
                conn.execute("update assessment_participants set status='present' where assessment_id=? and student_id=?",(aid,sid))
                media_id = asset(student['scan_image'],assessment_id=aid,student_id=sid)
                for number,r in student['responses'].items():
                    qid=question_ids[number]
                    items.append({'student_id':sid,'question_id':qid,'answer':r['answer'],'confidence':1,
                                  'media_id':media_id,'source_pages':student.get('source_pages',[]),
                                  'transcription_method':bundle.get('transcription_method','reviewed')})
                    evidence[(sid,qid)] = r
            tx.import_ocr_responses(actor_id,aid,bundle['title'],'reviewed-bundle','v1',items)
            for (sid,qid),r in evidence.items():
                conn.execute("update student_responses set confirmed_score=?,score_reason=?,reviewed_by=?,reviewed_at=current_timestamp,review_status='confirmed' where assessment_id=? and student_id=? and question_id=?",
                    (r.get('confirmed_score'),r.get('score_reason','原图核对作答后按标准答案判分'),actor_id,aid,sid,qid))
            result=tx.grade_assessment(actor_id,aid,publish=True)
            if result['status']!='published':
                raise StateConflict('存在未复核项，导入已回滚')
            assessments.append({'id':aid,'title':a['title'],'students':len(included),'wrong_count':result['wrong_question_count']})
        report.update({'status':'published','assessments':assessments})
        conn.execute('insert into exam_imports(school_id,batch_key,payload_hash,result_json,created_by) values(?,?,?,?,?)',
                     (actor['school_id'],bundle['batch_key'],digest,dumps(report),actor_id))
        tx.audit(actor_id,'exam_bundle_published','exam_import',bundle['batch_key'],report)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return report


def get_asset(repo, actor_id, asset_id):
    user=repo._actor(actor_id)
    row=repo.conn.execute('select * from exam_assets where id=?',(asset_id,)).fetchone()
    if not row or row['school_id'] != user['school_id']:
        raise PermissionDenied('不能查看此图片')
    if row['student_id']:
        if user['role']=='student':
            if row['student_id']!=actor_id:
                raise PermissionDenied('仅能查看自己的答题图')
            repo.assessment_detail(actor_id,row['assessment_id'])
        else:
            repo.assessment_detail(actor_id,row['assessment_id'])
    elif user['role']=='student' and row['question_id'] not in repo.student_published_question_ids(actor_id):
        raise PermissionDenied('题目尚未向你发布')
    return bytes(row['png'])


def stage_chunk(repo, actor_id, payload):
    """Small authenticated requests also work behind a 1MB reverse proxy limit."""
    repo._require_question_bank_actor(actor_id)
    index, total, value = payload.get('index'), payload.get('total'), payload.get('text')
    if (not isinstance(index, int) or not isinstance(total, int) or
            not 1 <= total <= 512 or not 0 <= index < total or
            not isinstance(value, str) or len(value.encode('utf-8')) > 900000):
        raise InvalidRequest('上传分块格式或大小无效')
    conn = repo.conn
    conn.execute("delete from exam_upload_chunks where created_at < datetime('now','-1 day')")
    upload_id = payload.get('upload_id')
    if not upload_id:
        if index != 0: raise InvalidRequest('请从第一个分块开始上传')
        upload_id = uuid.uuid4().hex
    else:
        first = conn.execute('select actor_id,total from exam_upload_chunks where upload_id=? limit 1',(upload_id,)).fetchone()
        if not first or first['actor_id'] != actor_id: raise PermissionDenied('不能使用此上传批次')
        if first['total'] != total: raise InvalidRequest('分块总数不一致')
    size = conn.execute('select coalesce(sum(length(cast(content as blob))),0) from exam_upload_chunks where upload_id=? and chunk_index!=?',(upload_id,index)).fetchone()[0]
    if size + len(value.encode('utf-8')) > 64*1024*1024: raise InvalidRequest('整理包不能超过64MB')
    conn.execute('insert into exam_upload_chunks(upload_id,actor_id,chunk_index,total,content) values(?,?,?,?,?) on conflict(upload_id,chunk_index) do update set content=excluded.content',
                 (upload_id,actor_id,index,total,value))
    conn.commit()
    return {'upload_id':upload_id,'received':index+1,'total':total}


def staged_bundle(repo, actor_id, upload_id):
    repo._require_question_bank_actor(actor_id)
    rows=repo.conn.execute('select * from exam_upload_chunks where upload_id=? order by chunk_index',(upload_id,)).fetchall()
    if not rows or rows[0]['actor_id'] != actor_id: raise PermissionDenied('上传不存在或不属于当前账号')
    if len(rows) != rows[0]['total'] or [r['chunk_index'] for r in rows] != list(range(len(rows))):
        raise StateConflict('整理包尚未上传完整，请重新检查导入内容')
    try: return json.loads(''.join(r['content'] for r in rows))
    except ValueError as exc: raise InvalidRequest('整理包JSON格式无效') from exc
