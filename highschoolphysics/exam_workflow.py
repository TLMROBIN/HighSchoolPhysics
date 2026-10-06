"""School-scoped exams with explicit class sets and imported score evidence."""
from .db import _ensure_column
from .errors import InvalidRequest
from .repository import dumps, loads


def migrate(conn):
    current=conn.execute("select version from app_schema_migrations where feature='exam_workflow'").fetchone()
    if current:
        if current[0] != 17: raise InvalidRequest('不支持的考试工作流版本')
        migrate_scan_progress(conn)
        return
    _ensure_column(conn, 'assessment_sessions', 'created_by text references users(id)')
    _ensure_column(conn, 'assessment_sessions', "scope_json text not null default '{}'")
    _ensure_column(conn, 'response_evidence', 'imported_score real')
    _ensure_column(conn, 'response_evidence', 'imported_max_score real')
    conn.execute("""create table if not exists response_scan_jobs (
        id text primary key, school_id text not null references schools(id),
        assessment_id text not null references assessment_sessions(id),
        created_by text not null references users(id), request_key text not null,
        payload_hash text not null, status text not null, files_json text not null,
        result_json text not null default '{}', error text not null default '',
        created_at text not null, started_at text, unique(created_by,request_key))""")
    conn.execute("insert into app_schema_migrations(feature,version) values('exam_workflow',17)")
    conn.commit()
    migrate_scan_progress(conn)


def migrate_scan_progress(conn):
    conn.commit()
    conn.execute('begin immediate')
    try:
        current = conn.execute("select version from app_schema_migrations where feature='response_scan_progress'").fetchone()
        if current:
            if current[0] != 18: raise InvalidRequest('不支持的扫描进度版本')
        else:
            _ensure_column(conn, 'response_scan_jobs', "progress_json text not null default '{}'")
            conn.execute("insert into app_schema_migrations(feature,version) values('response_scan_progress',18)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def classes(conn, school_id):
    rows = [dict(r) for r in conn.execute(
        'select id,name,grade from class_groups where school_id=?', (school_id,))]
    import re
    return sorted(rows, key=lambda r: (r['grade'], int(re.search(r'\d+', r['name']).group())
                                      if re.search(r'\d+', r['name']) else 0, r['name']))


def resolve_scope(conn, user, payload):
    selected = payload.get('class_ids', payload.get('class_id', [])) or []
    if isinstance(selected, str):
        selected = [selected]
    if not isinstance(selected, list) or any(not isinstance(v, str) for v in selected):
        raise InvalidRequest('班级列表格式错误')
    selected = list(dict.fromkeys(selected))
    available = classes(conn, user['school_id'])
    grade = str(payload.get('grade') or '高三')
    if selected:
        chosen = [r for r in available if r['id'] in selected]
        if len(chosen) != len(selected):
            raise InvalidRequest('班级不存在或不属于本校')
        if not payload.get('grade'): grade=chosen[0]['grade']
        if any(r['grade'] != grade for r in chosen):
            raise InvalidRequest('请选择当前年级的班级')
    else:
        chosen = [r for r in available if r['grade'] == grade]
    if not chosen:
        raise InvalidRequest('该年级尚无班级')
    return grade, chosen, not selected


def scope_label(assessment):
    scope = loads(assessment.get('scope_json'), {})
    if scope.get('whole_grade'):
        return scope.get('grade', '') + '全年级'
    return '、'.join(scope.get('class_names', [])) or assessment.get('class_name', '')
