"""Recover missing published answers from an explicitly selected source task.

Creates content revisions and only fills empty, unused draft snapshots. Never
changes published assessments or first responses. Existing reference keys can
only be normalized to the confirmed bank single/multiple-choice classification.
"""
import copy
import json
import uuid
from .choice_answers import prepare_answers, row_answer
from .document_models import canonical_sha256, validate_question_document
from .document_ingestion import (_answer_groups, _audit, _legacy_question_text,
    _paper_asset_ids, _question_rows, _split_child_answer_markdown, _normalize_part_label,
    _task_for_actor)
from .errors import StateConflict
from .repository import dumps, loads


def recover(repo, actor, paper_task_id, answer_task_id, apply=False, choice_overrides=None):
    c = repo.conn
    paper = _task_for_actor(c, actor, paper_task_id, allow_admin=False)
    answers = _task_for_actor(c, actor, answer_task_id, allow_admin=False)
    if paper['original_paper_id'] != answers['original_paper_id']:
        raise StateConflict('答案任务不属于同一原卷')
    db_path = c.execute("pragma database_list").fetchone()["file"] or None
    groups = _answer_groups(c, actor, answer_task_id, db_path=db_path)
    assets = _paper_asset_ids(c, actor['school_id'], paper['original_paper_id'], paper['created_by'])
    result = {'recovered': [], 'skipped': [], 'snapshots_filled': []}
    plans = []
    c.execute('begin immediate')
    try:
        items = c.execute('''select i.*,p.group_id,g.current_revision_id,r.document_json current_document
            from parsed_question_items i join import_item_publications p on p.parsed_item_id=i.id
            join question_content_groups g on g.id=p.group_id
            join question_content_revisions r on r.id=g.current_revision_id
            where i.parse_task_id=? and i.school_id=? order by i.item_index''',
            (paper_task_id, actor['school_id'])).fetchall()
        for item in items:
            doc = loads(item['current_document'], {})
            number = doc['number']
            entries = groups.get(number, [])
            bank = c.execute('select q.bank_type from questions q join question_content_bindings b on b.question_id=q.id where b.group_id=? and b.child_key=?',(item['group_id'],'')).fetchone() if not doc.get('children') else None
            choice_kind = bank[0] if bank and bank[0] in ('single_choice','multiple_choice') else None
            existing = bool(doc.get('answer_md') or doc.get('analysis_md') or any(p.get('answer_md') or p.get('analysis_md') for p in doc.get('children', [])))
            type_only = existing and choice_kind and doc.get('kind') in ('single_choice','multiple_choice') and doc['kind'] != choice_kind
            if (existing and not type_only) or len(entries) != 1:
                result['skipped'].append(number)
                continue
            entry = entries[0]
            if not set(entry['asset_refs']) <= assets:
                raise StateConflict('答案资源不属于当前原卷')
            doc = copy.deepcopy(doc)
            if not type_only:
                doc['answer_md'] = entry['markdown']
                doc['answer_state'] = 'needs_review'
                doc['source_spans'] += entry['source_spans']
                doc['asset_refs'] = sorted(set(doc['asset_refs']) | set(entry['asset_refs']))
            parts = _split_child_answer_markdown(entry['markdown'])
            for part in doc.get('children', []):
                value = parts.get(_normalize_part_label(part['label']))
                if value:
                    part['answer_md'] = value
                    part['answer_state'] = 'needs_review'
            override = (choice_overrides or {}).get(number)
            if override:
                doc['answer_md'] = override
                doc['analysis_md'] = entry['markdown'] + '\n\n教师确认标准答案为 '+override+'；原文冲突保留供核查。'
            doc = prepare_answers(doc, reviewed=True, choice_kind=choice_kind)
            doc = validate_question_document(doc, known_asset_ids=assets)
            units = _question_rows(doc, doc['stem_md'])
            result['recovered'].append({'number': number, 'state': doc['answer_state'],
                'answer': (doc.get('grading_rule') or {}).get('answer'), 'source_task':answer_task_id})
            plans.append((item, doc, units, bool(type_only)))
        if not apply:
            c.rollback()
            return result
        for item, doc, units, type_only in plans:
            revision = 'revision-'+uuid.uuid4().hex
            n = c.execute('select max(revision_no)+1 from question_content_revisions where group_id=?',(item['group_id'],)).fetchone()[0]
            c.execute('''insert into question_content_revisions(id,group_id,revision_no,schema_version,document_json,content_sha256,review_state,answer_state,created_by,change_reason)
                values(?,?,?,1,?,?,'verified',?,?,?)''',
                (revision,item['group_id'],n,dumps(doc),canonical_sha256(doc),doc['answer_state'],actor['id'],('同步单选多选分类与批改规则；来源 ' if type_only else '恢复已上传但未保存的答案解析；来源 ')+answer_task_id))
            for asset in doc['asset_refs']:
                c.execute('insert into content_asset_refs(revision_id,asset_id,field_path) values(?,?,?)',(revision,asset,'document'))
            c.execute('update question_content_groups set current_revision_id=? where id=?',(revision,item['group_id']))
            by_key = {r['child_key']:r for r in units}
            for binding in c.execute('select * from question_content_bindings where group_id=?',(item['group_id'],)).fetchall():
                unit = by_key[binding['child_key']]
                qid = binding['question_id']
                answer = dumps(row_answer(unit))
                c.execute('update questions set answer_json=?,analysis=?,question_type=?,version=version+1 where id=?',
                    (answer,_legacy_question_text(unit['analysis_md']),unit['kind'],qid))
                # This explicit repair fills only empty snapshots before any answers exist.
                for snap in c.execute('''select s.* from question_version_snapshots s join assessment_sessions a on a.id=s.assessment_id
                    where s.question_id=? and a.school_id=? and a.grading_status<>'published'
                    and not exists(select 1 from student_responses r where r.assessment_id=a.id)''',(qid,actor['school_id'])).fetchall():
                    old = loads(snap['answer_json'],{})
                    previous_rule = loads(snap['grading_rule_json'],{})
                    expected = (unit.get('grading_rule') or {}).get('answer')
                    if type_only:
                        if previous_rule.get('answer') != expected:
                            continue
                    elif old not in ({}, '', None) or previous_rule.get('answer') not in ({}, '', None):
                        continue
                    old_content = c.execute('select revision_id from snapshot_content_bindings where snapshot_id=?',(snap['id'],)).fetchone()
                    if old_content and old_content[0] != item['current_revision_id']:
                        continue
                    rule = dict(unit.get('grading_rule') or {}, type=unit['kind'],answer=(unit.get('grading_rule') or {}).get('answer'))
                    c.execute('update question_version_snapshots set answer_json=?,grading_rule_json=?,question_version=(select version from questions where id=?) where id=?',
                        (answer,dumps(rule),qid,snap['id']))
                    c.execute('insert into snapshot_content_bindings(snapshot_id,revision_id,child_key) values(?,?,?) on conflict(snapshot_id) do update set revision_id=excluded.revision_id,child_key=excluded.child_key',
                        (snap['id'],revision,binding['child_key']))
                    result['snapshots_filled'].append(snap['id'])
            _audit(c,actor,'missing_answers_recovered','question_content_group',item['group_id'],
                {'source_task':answer_task_id,'old_revision':item['current_revision_id'],'new_revision':revision,'answer_state':doc['answer_state'],'choice_override':(choice_overrides or {}).get(doc['number']),'type_only':type_only})
        c.commit()
        return result
    except Exception:
        c.rollback()
        raise
