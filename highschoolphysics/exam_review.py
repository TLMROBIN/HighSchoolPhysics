"""Versioned, teacher-editable exam review packages. Never alters exam evidence."""
import hashlib
import json
import re

from .errors import InvalidRequest, PermissionDenied, ResourceNotFound, StateConflict
from .exam_statistics import METRIC_RULES, question_statistics
from .question_content import snapshot_content
from .question_export import _question_markdown, _markdown_assets, QuestionExportError
from .repository import loads, dumps
from .response_workflow import assessment, digest, identifier, timestamp


VERSION = 'exam-review-v1'
CONTENT_FIELDS = {
    'knowledge_notes': '考查知识与适用条件',
    'solution_steps': '解题思路',
    'option_analysis': '逐项辨析',
    'possible_errors': '可能的错误原因（待验证）',
    'teacher_errors': '教师补充的已确认错因与依据',
    'classroom_question': '课堂追问与检查',
    'notes': '讲课备注',
}
AI_FIELDS = tuple(key for key in CONTENT_FIELDS if key != 'teacher_errors')


def migrate(conn):
    row = conn.execute("select version from app_schema_migrations where feature='exam_review'").fetchone()
    if row:
        if row[0] != 1:
            raise RuntimeError('不支持的考试讲评资料版本')
        return
    from .db import _execute_sqlite_script
    conn.commit()
    conn.execute('begin immediate')
    try:
        _execute_sqlite_script(conn, '''
        create table if not exists exam_review_packages(
            id text primary key, school_id text not null references schools(id),
            assessment_id text not null references assessment_sessions(id),
            created_by text not null references users(id), request_key text not null,
            request_hash text not null, source_hash text not null, source_json text not null,
            settings_json text not null, revision integer not null default 1,
            created_at text not null, updated_at text not null,
            unique(created_by,request_key));
        create index if not exists idx_exam_review_assessment on exam_review_packages(assessment_id,created_at);
        create table if not exists exam_review_items(
            id text primary key, package_id text not null references exam_review_packages(id),
            snapshot_id text not null references question_version_snapshots(id),
            position integer not null, source_json text not null, generated_json text not null,
            overrides_json text not null default '{}', revision integer not null default 1,
            included integer not null default 1, emphasis text not null default 'detail',
            reviewed integer not null default 0, status text not null,
            attempts integer not null default 0, lease_token text, lease_until text,
            error text not null default '', model_name text not null default '',
            updated_at text not null, unique(package_id,snapshot_id));
        create index if not exists idx_exam_review_queue on exam_review_items(status,updated_at);
        ''')
        conn.execute("insert or ignore into app_schema_migrations(feature,version) values('exam_review',1)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def provider(conn, school_id):
    row = conn.execute("""select * from provider_configs where school_id=? and provider_kind='llm' and enabled=1
        order by updated_at desc,created_at desc,id limit 1""", (school_id,)).fetchone()
    return dict(row) if row else None


def _settings(payload):
    minutes = payload.get('minutes', 45)
    if type(minutes) is not int or not 1 <= minutes <= 180:
        raise InvalidRequest('课堂时长应为 1—180 分钟的整数')
    if any(key in payload and type(payload[key]) is not bool for key in
           ('show_answer', 'show_statistics', 'show_knowledge', 'show_solution', 'show_errors')):
        raise InvalidRequest('课堂展示内容应使用明确开关')
    return {'minutes': minutes, **{key: payload.get(key, True) is True for key in
            ('show_answer', 'show_statistics', 'show_knowledge', 'show_solution', 'show_errors')}}


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise InvalidRequest('缺少有效的请求标识，请刷新后重试')
    return value


def _audit(conn, user, action, resource, detail):
    conn.execute('''insert into audit_events(id,school_id,actor_id,action,resource_type,resource_id,detail_json)
        values(?,?,?,?,?,?,?)''', (identifier('audit'), user['school_id'], user['id'], action,
                                 'exam_review', resource, dumps(detail)))


def _exam_scope(repo, user, aid, class_ids=None):
    exam = assessment(repo, user, aid)
    participants = [dict(r) for r in repo.conn.execute('''select p.student_id,p.status,u.class_id,c.name class_name
        from assessment_participants p join users u on u.id=p.student_id
        left join class_groups c on c.id=u.class_id where p.assessment_id=? order by p.student_id''', (aid,))]
    allowed = set(loads(exam.get('scope_json'), {}).get('class_ids', [])) | {exam['class_id']}
    allowed.update(p['class_id'] for p in participants)
    classes = [dict(r) for r in repo.conn.execute('select id,name from class_groups where school_id=? order by name,id', (user['school_id'],))
               if r['id'] in allowed]
    if class_ids is None:
        class_ids = [r['id'] for r in classes]
    if (not isinstance(class_ids, list) or not class_ids or any(not isinstance(x, str) for x in class_ids)
            or not set(class_ids) <= {r['id'] for r in classes}):
        raise InvalidRequest('请选择本次考试范围内的班级')
    selected = set(class_ids)
    return exam, [p for p in participants if p['class_id'] in selected], [r for r in classes if r['id'] in selected]


def _source_question(conn, row, school_id):
    binding = snapshot_content(conn, row['id'], school_id)
    if binding:
        doc, child = binding['document'], binding['child_key'] or None
        part = next((x for x in doc['children'] if x['key'] == child), doc)
        raw_md = _question_markdown(doc, child, True)
        prompt_md = _question_markdown(doc, child, False)
        revision_id = binding['revision_id']
        options = part.get('options', [])
        state = binding['answer_state']
        answer = '\n\n'.join(x for x in (doc.get('answer_md', ''), part.get('answer_md', '') if child else '') if x)
        analysis = '\n\n'.join(x for x in (doc.get('analysis_md', ''), part.get('analysis_md', '') if child else '') if x)
        number = doc['number'] + (' ' + part['label'] if child else '')
    else:
        options = loads(row['options_json'], {})
        if isinstance(options, list):
            options = {chr(65+i): x for i, x in enumerate(options)}
        options = [{'key': str(k), 'markdown': str(v)} for k, v in options.items()]
        rule = loads(row['grading_rule_json'], {})
        from .fill_rules import reference_answer
        answer = reference_answer(rule)
        if answer is None:
            answer = ''
        elif not isinstance(answer, str):
            answer = dumps(answer)
        state = 'verified' if answer else 'missing'
        # Legacy snapshots have no analysis field: never silently use the live bank's analysis.
        analysis, revision_id = '', None
        number = str(row.get('original_question_number') or row['position'])
        prompt_md = '# %s\n\n%s\n\n%s' % (
            number, row['stem'], '\n\n'.join('**%s.** %s' % (x['key'], x['markdown']) for x in options))
        for asset in conn.execute('select id from exam_assets where question_id=? and school_id=? and student_id is null order by id', (row['question_id'], school_id)):
            prompt_md += '\n\n![原题图](asset:%s)' % asset['id']
        raw_md = prompt_md + '\n\n## 答案\n\n' + answer
    assets = {}
    for aid in sorted(_markdown_assets(raw_md)):
        if revision_id:
            asset = conn.execute('''select a.id,a.sha256,a.mime_type from document_assets a
                join content_asset_refs r on r.asset_id=a.id where r.revision_id=? and a.id=? and a.school_id=?''',
                                 (revision_id, aid, school_id)).fetchone()
            if asset:
                assets[aid] = dict(asset)
        else:
            asset = conn.execute('select png from exam_assets where id=? and question_id=? and school_id=? and student_id is null',
                                 (aid, row['question_id'], school_id)).fetchone()
            if asset:
                assets[aid] = {'id': aid, 'sha256': hashlib.sha256(asset['png']).hexdigest(), 'mime_type': 'image/png'}
        if aid not in assets:
            assets[aid] = {'id': aid, 'missing': True}
    rule = loads(row['grading_rule_json'], {})
    correct_options = []
    if rule.get('type') in ('single_choice', 'multiple_choice') and state == 'verified':
        key = rule.get('answer', '')
        key = ''.join(key) if isinstance(key, list) and all(isinstance(x, str) for x in key) else str(key)
        if re.fullmatch(r'[A-H,，、;；\s]+', key):
            correct_options = sorted(set(re.findall('[A-H]', key)))
        if not set(correct_options) <= {x['key'] for x in options}:
            correct_options = []
        if binding and part.get('kind') in ('single_choice', 'multiple_choice'):
            from .choice_answers import extract_choice_answer
            content_key = extract_choice_answer(part.get('answer_md', ''), options, part['kind'])
            if content_key and list(content_key) != correct_options:
                state, correct_options = 'needs_review', []
    return dict(snapshot_id=row['id'], question_id=row['question_id'], position=row['position'],
                number=number, revision_id=revision_id, child_key=binding['child_key'] if binding else '',
                markdown=raw_md, prompt_markdown=prompt_md, options=options, grading_rule=rule, correct_options=correct_options,
                answer=answer, answer_state=state, analysis=analysis,
                source_spans=(doc.get('source_spans', []) + (part.get('source_spans', []) if child else [])) if binding else [],
                tags=loads(row['tag_snapshot_json'], []), assets=assets)


def _teacher_evidence(conn, aid, student_ids):
    by_snapshot = {}
    for row in conn.execute('''select r.snapshot_id,w.student_id,t.id tag_id,t.name,wt.note
        from wrong_questions w join student_responses r on r.id=w.response_id
        join wrong_question_error_tags wt on wt.wrong_question_id=w.id
        join error_reason_tags t on t.id=wt.error_reason_tag_id
        where w.assessment_id=? and w.is_active=1''', (aid,)):
        if row['student_id'] not in student_ids:
            continue
        tags = by_snapshot.setdefault(row['snapshot_id'], {})
        group = tags.setdefault(row['tag_id'], {'label': row['name'], 'students': set(), 'notes': set()})
        group['students'].add(row['student_id'])
        if row['note']:
            group['notes'].add(row['note'])
    # Do not export free-text tag notes: they can contain student identities.
    return {sid: [{'label': x['label'], 'count': len(x['students']), 'source': 'teacher_tag'}
                  for _, x in sorted(tags.items())] for sid, tags in by_snapshot.items()}


def _student_reports(conn, aid, student_ids, school_id):
    """Only current, exam-local self-reports; a whole-question report stays so."""
    from .wrong_causes import CAUSES
    active = {r['id']: dict(r) for r in conn.execute('''select w.id,w.student_id,w.question_id,r.snapshot_id
        from wrong_questions w join student_responses r on r.id=w.response_id
        where w.assessment_id=? and w.is_active=1''', (aid,)) if r['student_id'] in student_ids}
    grouped = {}
    rows = conn.execute('''select e.* from student_wrong_cause_events e
        where e.school_id=? and exists(select 1 from assessment_participants p
            where p.assessment_id=? and p.student_id=e.student_id and p.status='present')
        and not exists(select 1 from student_wrong_cause_events newer where newer.student_id=e.student_id
        and newer.scope_key=e.scope_key and newer.revision>e.revision) order by e.student_id,e.scope_key''', (school_id, aid))
    for row in rows:
        if row['student_id'] not in student_ids:
            continue
        sources = loads(row['sources_json'], [])
        if not sources or any(not isinstance(x, list) or len(x) != 3 or x[0] not in active
                              or active[x[0]]['student_id'] != row['student_id']
                              or [active[x[0]]['question_id'], active[x[0]]['snapshot_id']] != x[1:] for x in sources):
            continue
        snapshots = tuple(sorted(x[2] for x in sources))
        for cause in loads(row['causes_json'], []):
            if cause in CAUSES:
                grouped.setdefault((snapshots, cause), set()).add(row['student_id'])
    result = {}
    for (snapshots, cause), students in sorted(grouped.items()):
        for sid in snapshots:
            result.setdefault(sid, []).append({'label': CAUSES[cause], 'count': len(students),
                'source': 'student_self_report', 'scope': 'whole_question', 'snapshot_ids': list(snapshots)})
    return result


def collect_source(repo, user, aid, class_ids=None):
    exam, participants, classes = _exam_scope(repo, user, aid, class_ids)
    present = [p for p in participants if p['status'] == 'present']
    included = {p['student_id'] for p in present}
    rows = [dict(r) for r in repo.conn.execute('''select r.*,d.evidence_id,e.imported_score,e.imported_max_score
        from student_responses r left join response_decisions d on d.id=r.effective_decision_id
        left join response_evidence e on e.id=d.evidence_id where r.assessment_id=? order by r.id''', (aid,))
            if r['student_id'] in included]
    evidence = _teacher_evidence(repo.conn, aid, included)
    reports = _student_reports(repo.conn, aid, included, user['school_id'])
    sources = []
    for row in repo.conn.execute('''select s.*,q.original_question_number from question_version_snapshots s
        join questions q on q.id=s.question_id where s.assessment_id=? order by s.position''', (aid,)):
        source = _source_question(repo.conn, dict(row), user['school_id'])
        unit_rows = [r for r in rows if r['snapshot_id'] == row['id']]
        source['statistics'] = question_statistics(source, unit_rows, len(present))
        source['classes'] = [{'id': cls['id'], 'name': cls['name'], 'statistics': question_statistics(source,
            [r for r in unit_rows if r['student_id'] in {p['student_id'] for p in present if p['class_id'] == cls['id']}],
            sum(p['class_id'] == cls['id'] for p in present))} for cls in classes]
        source['error_evidence'] = evidence.get(row['id'], [])
        source['student_reports'] = reports.get(row['id'], [])
        sources.append(source)
    if not sources:
        raise InvalidRequest('本次考试没有可讲评的题目')
    public = dict(version=VERSION, assessment_id=aid, title=exam['title'], grading_status=exam['grading_status'],
                  classes=classes, attendance={status: sum(p['status'] == status for p in participants)
                  for status in sorted({p['status'] for p in participants} | {'present'})}, metrics=METRIC_RULES)
    # Freeze identities only inside a hash, never in external materials.
    fingerprint = digest([public, sources, participants,
                          [(r['id'], r['effective_decision_id'], r['outcome'], r['final_answer'],
                            r['review_status'], r['imported_score'], r['imported_max_score']) for r in rows]])
    return public, sources, fingerprint


def _reference_content(source):
    result = {key: '' for key in CONTENT_FIELDS}
    result['knowledge_notes'] = '、'.join(t['name'] for t in source['tags'] if t.get('tag_type') == 'knowledge')
    result['solution_steps'] = source['analysis']
    return result


def create(repo, user, p):
    aid, key = p.get('assessment_id', ''), _key(p.get('request_key'))
    assessment(repo, user, aid)
    settings = _settings(p)
    request_hash = digest([aid, p.get('class_ids'), settings])
    conn = repo.conn
    conn.execute('begin immediate')
    try:
        old = conn.execute('select * from exam_review_packages where created_by=? and request_key=?', (user['id'], key)).fetchone()
        if old:
            if old['request_hash'] != request_hash:
                raise StateConflict('重复生成请求的内容不一致')
            conn.commit()
            return get(repo, user, old['id'])
        public, sources, fingerprint = collect_source(repo, user, aid, p.get('class_ids'))
        pid, now = identifier('review'), timestamp()
        conn.execute('''insert into exam_review_packages(id,school_id,assessment_id,created_by,request_key,request_hash,
            source_hash,source_json,settings_json,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?)''',
                     (pid, user['school_id'], aid, user['id'], key, request_hash, fingerprint, dumps(public), dumps(settings), now, now))
        cfg = provider(conn, user['school_id'])
        for source in sources:
            status = 'queued' if cfg and source['answer_state'] == 'verified' else 'reference_only'
            stats = source['statistics']
            emphasis = 'detail' if stats['correct_rate'] is None or stats['correct_rate'] < .7 else 'brief'
            conn.execute('''insert into exam_review_items(id,package_id,snapshot_id,position,source_json,generated_json,
                emphasis,status,updated_at) values(?,?,?,?,?,?,?,?,?)''',
                         (identifier('review-item'), pid, source['snapshot_id'], source['position'], dumps(source),
                          dumps(_reference_content(source)), emphasis, status, now))
        _audit(conn, user, 'exam_review_created', pid, {'source_hash': fingerprint, 'questions': len(sources)})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get(repo, user, pid)


def owned_package(repo, user, pid):
    if user['role'] not in ('teacher', 'admin'):
        raise PermissionDenied('讲评资料仅供教师使用')
    row = repo.conn.execute('select * from exam_review_packages where id=? and school_id=?', (pid, user['school_id'])).fetchone()
    if not row:
        raise ResourceNotFound('讲评资料不存在')
    assessment(repo, user, row['assessment_id'])
    return dict(row)


def item_payload(row):
    item = dict(row)
    generated = loads(item['generated_json'], {})
    overrides = loads(item['overrides_json'], {})
    return dict(id=item['id'], snapshot_id=item['snapshot_id'], position=item['position'],
                revision=item['revision'], included=bool(item['included']), emphasis=item['emphasis'],
                reviewed=bool(item['reviewed']), status=item['status'], error=item['error'],
                model_name=item['model_name'], source=loads(item['source_json'], {}),
                content={**generated, **overrides}, edited_fields=list(overrides),
                field_sources={key: 'teacher' if key in overrides else ('ai' if item['status'] == 'ready' else 'reference')
                               for key in CONTENT_FIELDS})


def get(repo, user, pid, check_stale=True):
    package = owned_package(repo, user, pid)
    source = loads(package['source_json'], {})
    items = [item_payload(r) for r in repo.conn.execute('select * from exam_review_items where package_id=? order by position', (pid,))]
    stale, unavailable = False, False
    if check_stale:
        try:
            _, _, current_hash = collect_source(repo, user, package['assessment_id'], [x['id'] for x in source['classes']])
            stale = current_hash != package['source_hash']
        except (InvalidRequest, ResourceNotFound):
            stale, unavailable = True, True
    counts = {status: sum(x['status'] == status for x in items) for status in ('queued', 'running', 'ready', 'failed', 'reference_only')}
    return dict(id=pid, revision=package['revision'], source_hash=package['source_hash'], source=source,
                settings=loads(package['settings_json'], {}), created_at=package['created_at'],
                updated_at=package['updated_at'], items=items, counts=counts, stale=stale,
                source_unavailable=unavailable, total=len(items), completed=len(items)-counts['queued']-counts['running'])


def save_item(repo, user, p):
    package = owned_package(repo, user, p.get('package_id', ''))
    content = p.get('content')
    if not isinstance(content, dict) or set(content) - set(CONTENT_FIELDS):
        raise InvalidRequest('讲评内容字段无效')
    if any(not isinstance(value, str) or len(value) > 20000 for value in content.values()):
        raise InvalidRequest('每个讲评字段应为不超过 20000 字的文本')
    if p.get('emphasis') not in ('detail', 'brief') or type(p.get('included')) is not bool or type(p.get('reviewed')) is not bool:
        raise InvalidRequest('请选择讲评范围和详略')
    if type(p.get('revision')) is not int:
        raise InvalidRequest('缺少讲评版本，请刷新后重试')
    conn = repo.conn
    conn.execute('begin immediate')
    try:
        row = conn.execute('select * from exam_review_items where id=? and package_id=?', (p.get('item_id'), package['id'])).fetchone()
        if not row or row['revision'] != p['revision']:
            raise StateConflict('本题讲评已更新，请刷新后核对；当前输入可先复制保留')
        overrides = {**loads(row['overrides_json'], {}), **content}
        conn.execute('''update exam_review_items set overrides_json=?,included=?,emphasis=?,reviewed=?,
            revision=revision+1,updated_at=? where id=?''',
                     (dumps(overrides), p['included'], p['emphasis'], p['reviewed'], timestamp(), row['id']))
        _audit(conn, user, 'exam_review_item_saved', row['id'], {'fields': list(content), 'revision': p['revision'] + 1})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return item_payload(conn.execute('select * from exam_review_items where id=?', (row['id'],)).fetchone())


def retry_item(repo, user, p):
    package = owned_package(repo, user, p.get('package_id', ''))
    conn = repo.conn
    conn.execute('begin immediate')
    try:
        row = conn.execute('select * from exam_review_items where id=? and package_id=?', (p.get('item_id'), package['id'])).fetchone()
        if not row or row['status'] not in ('failed', 'reference_only'):
            raise StateConflict('只需重试失败或尚未生成的题目')
        source = loads(row['source_json'], {})
        if source['answer_state'] != 'verified':
            raise StateConflict('本题参考答案尚未确认；请先校对答案后生成新版资料')
        if not provider(conn, user['school_id']):
            raise StateConflict('尚未配置可用的讲评模型，请管理员配置大模型后重试')
        conn.execute("update exam_review_items set status='queued',attempts=0,error='',updated_at=? where id=?", (timestamp(), row['id']))
        _audit(conn, user, 'exam_review_item_retried', row['id'], {})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {'message': '本题已排队，教师编辑内容会保留'}


def api(repo, user, action, p):
    if not isinstance(p, dict):
        raise InvalidRequest('请求应为 JSON 对象')
    if action == 'review-create':
        return create(repo, user, p)
    if action == 'review-status':
        return get(repo, user, p.get('package_id', ''))
    if action == 'review-save':
        return save_item(repo, user, p)
    if action == 'review-retry':
        return retry_item(repo, user, p)
    raise ResourceNotFound('讲评操作不存在')


def load_asset(repo, user, source, asset_id, db_path):
    """Read only a frozen question asset, with revision, school and hash checks."""
    expected = source['assets'].get(asset_id)
    if not expected or expected.get('missing'):
        raise QuestionExportError('原题图片缺失；请补齐后生成新版讲评资料')
    conn = repo.conn
    if source['revision_id']:
        row = conn.execute('''select a.* from document_assets a join content_asset_refs r on r.asset_id=a.id
            where a.id=? and a.school_id=? and r.revision_id=?''', (asset_id, user['school_id'], source['revision_id'])).fetchone()
        if not row:
            raise QuestionExportError('图片与本次讲评题目版本不匹配')
        from .document_ingestion import _store_for_db
        from .document_store import DocumentStoreError
        try:
            data = _store_for_db(db_path).read(row['storage_key'], expected['sha256'], max_bytes=25*1024*1024)
        except (OSError, DocumentStoreError) as exc:
            raise QuestionExportError('原题图片无法读取或完整性校验失败') from exc
    else:
        row = conn.execute('select png from exam_assets where id=? and school_id=? and question_id=? and student_id is null',
                           (asset_id, user['school_id'], source['question_id'])).fetchone()
        if not row:
            raise QuestionExportError('原题图片无法读取')
        data = bytes(row['png'])
    if hashlib.sha256(data).hexdigest() != expected['sha256']:
        raise QuestionExportError('图片版本发生变化，请生成新版资料')
    return {'data': data, 'mime_type': expected['mime_type'], 'sha256': expected['sha256']}
