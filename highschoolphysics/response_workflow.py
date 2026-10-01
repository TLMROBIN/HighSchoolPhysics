"""Auditable outcome imports, review, publication and correction.

student_responses.outcome/final_answer are the effective projection. Evidence
and decisions are append-only; published initial_answer and snapshots stay fixed.
"""
import csv
import hashlib
import io
import json
from pathlib import Path
import uuid
from datetime import datetime, timezone

from .db import _execute_sqlite_script, _ensure_column
from .errors import InvalidRequest, PermissionDenied, StateConflict
from .outcomes import VERSION, decide
from .question_content import snapshot_content
from .repository import dumps, loads


OUTCOMES = {"correct", "wrong", "blank", "pending"}
CSV_OUTCOMES = {"正确": "correct", "错误": "wrong", "空白": "blank", "待确认": "pending", "": None}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def identifier(prefix):
    return prefix + "-" + uuid.uuid4().hex


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def migrate(conn):
    current = conn.execute("select version from app_schema_migrations where feature='response_evidence'").fetchone()
    if current:
        if current[0] != 14:
            raise StateConflict("不支持的作答证据版本")
        return
    conn.commit()
    conn.execute("begin immediate")
    try:
        # Recheck under the migration lock.
        if conn.execute("select 1 from app_schema_migrations where feature='response_evidence'").fetchone():
            conn.rollback()
            return
        _execute_sqlite_script(conn, (Path(__file__).parent / "migrations/v14_response_evidence.sql").read_text())
        for definition in ("effective_decision_id text references response_decisions(id)", "effective_from text"):
            _ensure_column(conn, "student_responses", definition)
        _ensure_column(conn, "wrong_questions", "is_active integer not null default 1")
        for r in conn.execute("select * from student_responses").fetchall():
            eid, did = identifier("evidence"), identifier("decision")
            original = r["initial_answer"] if r["initial_answer"] is not None else r["final_answer"] or ""
            conn.execute("""insert into response_evidence(id,response_id,raw_answer,normalized_answer,
                         extraction_method,created_at) values(?,?,?,?,?,?)""",
                         (eid,r["id"],r["raw_answer"],original,"legacy",r["created_at"]))
            conn.execute("""insert into response_decisions(id,response_id,evidence_id,outcome,answer,
                         proposed_outcome,method,rule_version,answer_version,reason_code,reason,created_at)
                         values(?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (did,r["id"],eid,r["outcome"],r["final_answer"] or original,r["outcome"],
                          "legacy","legacy",r["snapshot_id"],"legacy","历史已确认记录；来源元数据未补造",r["created_at"]))
            conn.execute("update student_responses set effective_decision_id=? where id=?",(did,r["id"]))
        conn.execute("insert into app_schema_migrations(feature,version) values('response_evidence',14)")
        if conn.execute("pragma foreign_key_check").fetchall():
            raise StateConflict("作答证据迁移外键核验失败")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def assessment(repo, user, aid):
    if user["role"] not in ("teacher", "admin"):
        raise PermissionDenied("需要教师身份")
    result = repo.assessment_detail(user["id"], aid, operation="grade")
    if result["school_id"] != user["school_id"]:
        raise PermissionDenied("不可跨学校操作")
    return result


def context_hash(conn, aid):
    return digest({
        "assessment": [dict(r) for r in conn.execute("select id,grading_status from assessment_sessions where id=?",(aid,))],
        "participants": [dict(r) for r in conn.execute("""select p.student_id,p.status,u.username,u.display_name,u.student_no
                            from assessment_participants p join users u on u.id=p.student_id
                            where p.assessment_id=? order by p.student_id""",(aid,))],
        "snapshots": [dict(r) for r in conn.execute("select * from question_version_snapshots where assessment_id=? order by id",(aid,))],
        "bindings": [dict(r) for r in conn.execute("""select b.* from snapshot_content_bindings b join
                            question_version_snapshots s on s.id=b.snapshot_id where s.assessment_id=? order by b.snapshot_id""",(aid,))],
        "content": [snapshot_content(conn,r["id"]) for r in conn.execute("select id from question_version_snapshots where assessment_id=? order by id",(aid,))],
        "responses": [dict(r) for r in conn.execute("select id,effective_decision_id,outcome,final_answer from student_responses where assessment_id=? order by id",(aid,))],
    })


def snapshot_decision(conn, s, school_id, answer):
    content = snapshot_content(conn, s["id"], school_id)
    result=decide(loads(s["grading_rule_json"],{}), answer, loads(s["options_json"],{}),
                  verified=content is None or content["answer_state"] == "verified")
    result["answer_version"]=digest([s["id"],s["grading_rule_json"],s["options_json"],content])
    return result


def _prepare(repo, user, p):
    a = assessment(repo,user,p["assessment_id"])
    if a["grading_status"] == "published":
        raise StateConflict("已发布的首次作答不可覆盖；请使用结果更正")
    source_type = p.get("source_type", "answers")
    if source_type not in ("answers", "external"):
        raise InvalidRequest("请选择导入学生答案或已核对结果")
    source_name = str(p.get("source_name") or "教师 CSV 录入").strip()[:240]
    source_reason = str(p.get("source_reason") or "").strip()[:2000]
    if source_type == "external" and (not source_reason or not p.get("source_name")):
        raise InvalidRequest("外部结果需要填写来源名称和核对依据")
    if "records" in p:
        raw_rows = p["records"]
        if not isinstance(raw_rows,list):
            raise InvalidRequest("records 必须是作答列表")
    else:
        reader = csv.DictReader(io.StringIO(str(p.get("csv", "")).lstrip("\ufeff")))
        if not {"学生","题号","作答","结果"}.issubset(reader.fieldnames or []):
            raise InvalidRequest("表格列名必须包含：学生,题号,作答,结果")
        raw_rows = []
        for line,row in enumerate(reader,2):
            if None in row or any(row.get(k) is None for k in ("学生","题号","作答","结果")):
                raise InvalidRequest("第 %s 行有缺失/多余单元格；缺失不能当空白" % line)
            if row["结果"] not in CSV_OUTCOMES:
                raise InvalidRequest("第 %s 行结果必须为正确、错误、空白或待确认" % line)
            raw_rows.append(dict(student=row["学生"],number=row["题号"],answer=row["作答"],
                                 supplied_outcome=CSV_OUTCOMES[row["结果"]],source_row=line))
    if not raw_rows or len(raw_rows)>30000:
        raise InvalidRequest("请提供 1—30000 条作答记录")
    conn=repo.conn
    students=[dict(r) for r in conn.execute("""select u.* from users u join assessment_participants p
                    on p.student_id=u.id where p.assessment_id=? and p.status='present' and u.school_id=?""",
                    (a["id"],user["school_id"]))]
    snapshots=[dict(r) for r in conn.execute("select * from question_version_snapshots where assessment_id=?",(a["id"],))]
    prepared=[]
    seen=set()
    for index,row in enumerate(raw_rows,1):
        if not isinstance(row,dict) or not isinstance(row.get("answer"),str):
            raise InvalidRequest("第 %s 条缺少原始答案，不能只导入结果" % index)
        label = str(row.get("student_id") or row.get("student") or "").strip()
        matches=[u for u in students if label in (u["id"],u["username"],u["display_name"],u["student_no"])]
        if len(matches)!=1:
            raise InvalidRequest("第 %s 条学生身份不唯一或不在纳入名单：%s" % (index,label))
        u=matches[0]
        ss=[s for s in snapshots if (row.get("snapshot_id") and s["id"]==row["snapshot_id"]) or
            (not row.get("snapshot_id") and str(s["position"])==str(row.get("number","")))]
        if len(ss)!=1:
            raise InvalidRequest("第 %s 条作答序号不存在或不唯一" % index)
        s=ss[0]
        key=(u["id"],s["id"])
        if key in seen:
            raise InvalidRequest("第 %s 条学生题号重复" % index)
        seen.add(key)
        supplied=row.get("supplied_outcome")
        if supplied is not None and supplied not in OUTCOMES:
            raise InvalidRequest("无效的外部判定结果")
        if supplied and source_type != "external":
            raise InvalidRequest("提供结果时请选择“导入已核对结果”，并填写来源和依据")
        if any(k in row for k in ("score","max_score","confirmed_score","points")):
            raise InvalidRequest("请提供无分数结果，不接收或换算外部分数")
        answer=row["answer"]
        if supplied=="blank" and answer.strip():
            raise InvalidRequest("非空答案不可标为空白")
        if not answer.strip() and supplied in ("correct","wrong"):
            raise InvalidRequest("空答案不能标为正确或错误；请补录原始答案")
        decision=snapshot_decision(conn,s,user["school_id"],answer)
        proposed=decision["outcome"]
        effective=proposed
        category=""
        method="rule"
        if supplied=="pending":
            effective="pending";category="external_pending"
        elif supplied and proposed in ("correct","wrong","blank") and supplied!=proposed:
            effective="pending";category="external_conflict"
        elif supplied and proposed=="pending":
            effective="pending";category="external_confirmation"
        elif supplied:
            method="external_rule_agreement"
        elif proposed=="pending":
            category=decision["reason_code"]
        asset=row.get("source_asset_id")
        if asset:
            asset_row=conn.execute("select * from exam_assets where id=?",(asset,)).fetchone()
            if not asset_row or asset_row["school_id"]!=user["school_id"] or asset_row["student_id"]!=u["id"] or asset_row["assessment_id"]!=a["id"]:
                raise PermissionDenied("原图必须属于当前学生和测评")
        prepared.append(dict(student_id=u["id"],student_name=u["display_name"],snapshot_id=s["id"],
                             question_id=s["question_id"],number=s["position"],raw_answer=answer,
                             normalized_answer=decision["normalized_answer"],proposed_outcome=proposed,
                             supplied_outcome=supplied,outcome=effective,category=category,method=method,
                             reason_code=decision["reason_code"],rule_version=VERSION,answer_version=decision["answer_version"],source_asset_id=asset,
                             source_row=row.get("source_row",index)))
    return a,source_type,source_name,source_reason,prepared


def import_answers(repo,user,p):
    conn=repo.conn
    conn.execute("begin immediate")
    try:
        # Replay acknowledged saves even after publication; never overwrite.
        key=str(p.get("request_key") or "").strip()
        if not key or len(key)>100:
            raise InvalidRequest("缺少稳定的导入标识，请刷新后重试")
        payload_hash=digest({k:p.get(k) for k in ("assessment_id","csv","records","source_type","source_name","source_reason")})
        a=assessment(repo,user,p["assessment_id"])
        old=conn.execute("select * from response_import_batches where assessment_id=? and created_by=? and batch_key=?",
                         (a["id"],user["id"],key)).fetchone()
        if old:
            if old["payload_hash"]!=payload_hash:
                raise StateConflict("同一导入标识内容不同；请重新预览")
            if old["status"] in ("saved","published"):
                conn.rollback()
                return dict(message="该批次已保存，未重复写入",batch_id=old["id"],already_saved=True)
        a,source_type,source_name,source_reason,records=_prepare(repo,user,p)
        ch=context_hash(conn,a["id"])
        if p.get("confirm") is True:
            if not old or p.get("preview_token")!=old["id"] or old["context_hash"]!=ch:
                raise StateConflict("预览已失效或尚未预览；请重新核对后保存")
            stored=loads(old["records_json"],[])
            if stored!=records:
                raise StateConflict("判定依据发生变化，请重新预览")
            for record in records:
                _save_record(conn,user,a,old["id"],record,source_reason)
            conn.execute("update response_import_batches set status='saved' where id=?",(old["id"],))
            _audit(conn,user,"response_import_saved",old["id"],{"count":len(records),"source_type":source_type})
            conn.commit()
            return dict(message="作答与证据已保存；待确认项复核后可发布",batch_id=old["id"])
        batch_id=old["id"] if old else identifier("batch")
        if old:
            conn.execute("update response_import_batches set context_hash=?,records_json=? where id=?",(ch,dumps(records),batch_id))
        else:
            conn.execute("""insert into response_import_batches(id,school_id,assessment_id,created_by,batch_key,
                          payload_hash,context_hash,source_type,source_name,source_reason,status,records_json,created_at)
                          values(?,?,?,?,?,?,?,?,?,?,'preview',?,?)""",
                          (batch_id,user["school_id"],a["id"],user["id"],key,payload_hash,ch,source_type,source_name,source_reason,dumps(records),timestamp()))
        conn.commit()
        return dict(message="预览 %s 条，%s 条需复核。请核对下方逐项差异。" % (len(records),sum(r["outcome"]=="pending" for r in records)),
                    preview=True,preview_token=batch_id,records=records)
    except Exception:
        conn.rollback()
        raise


def _audit(conn,user,action,resource,detail):
    conn.execute("insert into audit_events(id,school_id,actor_id,action,resource_type,resource_id,detail_json) values(?,?,?,?,?,?,?)",
                 (identifier("audit"),user["school_id"],user["id"],action,"response",resource,dumps(detail)))


def _save_record(conn,user,a,batch_id,r,reason):
    old=conn.execute("select * from student_responses where assessment_id=? and student_id=? and snapshot_id=?",
                     (a["id"],r["student_id"],r["snapshot_id"])).fetchone()
    rid=old["id"] if old else identifier("response")
    if not old:
        conn.execute("""insert into student_responses(id,school_id,assessment_id,student_id,question_id,snapshot_id,
                       raw_answer,final_answer,initial_answer,outcome,review_status,grading_status)
                       values(?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (rid,user["school_id"],a["id"],r["student_id"],r["question_id"],r["snapshot_id"],r["raw_answer"],
                        r["raw_answer"],r["raw_answer"],r["outcome"],"pending" if r["outcome"]=="pending" else "reviewed","reviewed"))
    else:
        conn.execute("update student_responses set final_answer=?,initial_answer=?,outcome=?,review_status=? where id=?",
                     (r["raw_answer"],r["raw_answer"],r["outcome"],"pending" if r["outcome"]=="pending" else "reviewed",rid))
    eid=identifier("evidence")
    conn.execute("""insert into response_evidence(id,response_id,batch_id,raw_answer,normalized_answer,
                   source_row,source_asset_id,extraction_method,created_at) values(?,?,?,?,?,?,?,'manual_import',?)""",
                   (eid,rid,batch_id,r["raw_answer"],r["normalized_answer"],r["source_row"],r["source_asset_id"],timestamp()))
    did=_decision(conn,user,rid,eid,r["outcome"],r["raw_answer"],r["proposed_outcome"],r["supplied_outcome"],
                  r["method"],r["reason_code"],reason,r["answer_version"],old["effective_decision_id"] if old else None)
    conn.execute("update response_review_items set status='superseded' where response_id=? and status='open'",(rid,))
    if r["outcome"]=="pending":
        conn.execute("insert into response_review_items(id,response_id,decision_id,category,status) values(?,?,?,?,'open')",
                     (identifier("review"),rid,did,r["category"]))


def _decision(conn,user,rid,eid,outcome,answer,proposed,supplied,method,code,reason,answer_version,previous=None,key=None):
    did=identifier("decision")
    conn.execute("""insert into response_decisions(id,response_id,evidence_id,outcome,answer,proposed_outcome,
                   supplied_outcome,method,rule_version,answer_version,reason_code,reason,created_by,created_at,
                   supersedes_id,request_key) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (did,rid,eid,outcome,answer,proposed,supplied,method,VERSION,answer_version,code,reason,user["id"],timestamp(),previous,key))
    conn.execute("update student_responses set effective_decision_id=?,final_answer=?,outcome=?,review_status=?,updated_at=? where id=?",
                 (did,answer,outcome,"pending" if outcome=="pending" else "reviewed",timestamp(),rid))
    return did


def _response(repo,user,rid):
    r=repo.conn.execute("select * from student_responses where id=?",(rid,)).fetchone()
    if not r:
        raise InvalidRequest("作答不存在")
    a=assessment(repo,user,r["assessment_id"])
    return r,a


def history(repo,user,p):
    r,a=_response(repo,user,p["response_id"])
    return dict(initial_answer=r["initial_answer"],effective_answer=r["final_answer"],outcome=r["outcome"],
                decisions=[dict(d) for d in repo.conn.execute("select * from response_decisions where response_id=? order by rowid",(r["id"],))],
                evidence=[dict(e) for e in repo.conn.execute("""select e.*,b.source_name,b.source_reason from response_evidence e
                         left join response_import_batches b on b.id=e.batch_id where e.response_id=? order by e.rowid""",(r["id"],))])


def _publication(conn,user,aid,kind):
    version=conn.execute("select coalesce(max(version),0)+1 from response_publications where assessment_id=?",(aid,)).fetchone()[0]
    records=[dict(r) for r in conn.execute("select r.id,r.effective_decision_id from student_responses r join assessment_participants p on p.assessment_id=r.assessment_id and p.student_id=r.student_id where r.assessment_id=? and p.status='present' order by r.id",(aid,))]
    conn.execute("insert into response_publications(id,assessment_id,version,kind,decisions_json,created_by,created_at) values(?,?,?,?,?,?,?)",
                 (identifier("publication"),aid,version,kind,dumps(records),user["id"],timestamp()))


def _sync_wrong(conn,r):
    w=conn.execute("select * from wrong_questions where response_id=?",(r["id"],)).fetchone()
    active=r["outcome"] in ("wrong","blank")
    if w:
        conn.execute("update wrong_questions set is_active=? where id=?",(int(active),w["id"]))
    elif active:
        s=conn.execute("select answer_json from question_version_snapshots where id=?",(r["snapshot_id"],)).fetchone()
        conn.execute("""insert into wrong_questions(id,school_id,assessment_id,student_id,question_id,response_id,
                       wrong_answer,correct_answer_json,score,max_score,is_active) values(?,?,?,?,?,?,?,?,null,null,1)""",
                       (identifier("wrong"),r["school_id"],r["assessment_id"],r["student_id"],r["question_id"],r["id"],r["initial_answer"],s[0]))


def publish(repo,user,p):
    conn=repo.conn
    conn.execute("begin immediate")
    try:
        a=assessment(repo,user,p["assessment_id"])
        if a["grading_status"]=="published":
            raise StateConflict("测评已发布")
        participants=conn.execute("select student_id from assessment_participants where assessment_id=? and status='present'",(a["id"],)).fetchall()
        snapshots=conn.execute("select id from question_version_snapshots where assessment_id=?",(a["id"],)).fetchall()
        if not participants or not snapshots:
            raise InvalidRequest("没有纳入学生或题目")
        rows=[]
        for u in participants:
            for s in snapshots:
                r=conn.execute("select * from student_responses where assessment_id=? and student_id=? and snapshot_id=?",(a["id"],u[0],s[0])).fetchone()
                if not r or r["outcome"]=="pending" or conn.execute("select 1 from response_review_items where response_id=? and status='open'",(r["id"],)).fetchone():
                    raise StateConflict("仍有缺失或待确认作答，不能发布")
                if not r["effective_decision_id"]:
                    raise StateConflict("作答缺少判定依据")
                d=conn.execute('select * from response_decisions where id=?',(r['effective_decision_id'],)).fetchone()
                snapshot=conn.execute('select * from question_version_snapshots where id=?',(r['snapshot_id'],)).fetchone()
                if d['method']!='legacy' and d['answer_version']!=snapshot_decision(conn,snapshot,user['school_id'],r['final_answer'])['answer_version']:
                    raise StateConflict('判定依据已变化，请重新导入或复核后发布')
                rows.append(r)
        for r in rows:
            _sync_wrong(conn,r)
        conn.execute("update assessment_sessions set grading_status='published',status='published' where id=?",(a["id"],))
        conn.execute("update response_import_batches set status='published' where assessment_id=? and status='saved'",(a["id"],))
        _publication(conn,user,a["id"],"initial")
        _audit(conn,user,"response_published",a["id"],{"count":len(rows)})
        conn.commit()
        return {"message":"已发布，学生可查看自己的结果和错题"}
    except Exception:
        conn.rollback();raise


def review_or_correct(repo,user,p,correction=False):
    conn=repo.conn
    conn.execute("begin immediate")
    try:
        r,a=_response(repo,user,p["response_id"])
        if (a["grading_status"]=="published") != correction:
            raise StateConflict("请在发布前使用复核，发布后使用结果更正")
        reason=str(p.get("reason") or "").strip()
        key=str(p.get("request_key") or "").strip()
        answer=p.get("answer",r["final_answer"] or "")
        outcome=p.get("outcome")
        code=p.get("reason_code","teacher_review")
        if code not in ("teacher_review","extraction_error","external_error","judgment_error"):
            raise InvalidRequest("此入口仅支持作答或结果更正；身份和标准答案修订需另行核对")
        if not reason or len(reason)>2000 or not key or len(key)>100 or not isinstance(answer,str) or outcome not in ("correct","wrong","blank"):
            raise InvalidRequest("请填写答案、已确认结果、更正/复核依据及提交标识")
        if (outcome=="blank") != (not answer.strip()):
            raise InvalidRequest("空白结果与原始答案不一致")
        prior=conn.execute("select * from response_decisions where response_id=? and request_key=?",(r["id"],key)).fetchone()
        if prior:
            if (prior["answer"],prior["outcome"],prior["reason"],prior["reason_code"])!=(answer,outcome,reason,code):
                raise StateConflict("重复请求内容不一致")
            conn.rollback();return {"message":"已处理，未重复写入"}
        expected=p.get("expected_decision_id")
        if not expected or expected!=r["effective_decision_id"]:
            raise StateConflict("这条作答已经变化，请刷新后重新核对")
        if correction and not p.get("confirm"):
            token=digest([r["id"],expected,answer,outcome,reason,code])
            active_count=conn.execute("""select count(*) from wrong_questions where student_id=? and question_id=?
                            and is_active=1 and response_id<>?""",(r["student_id"],r["question_id"],r["id"])).fetchone()[0]
            conn.rollback()
            return dict(preview=True,preview_token=token,message="请核对更正影响后确认保存。",
                        impact=dict(initial_answer=r["initial_answer"],old_answer=r["final_answer"],new_answer=answer,
                                    old_outcome=r["outcome"],new_outcome=outcome,other_wrong_sources=active_count,
                                    practice="本来源停止到期练习；历史尝试保留" if outcome=="correct" else "保留错误来源；新增错误从生效次日安排",statistics="本次测评及标签统计同步更新"))
        if correction and p.get("preview_token")!=digest([r["id"],expected,answer,outcome,reason,code]):
            raise StateConflict("更正预览无效，请重新核对")
        s=conn.execute("select * from question_version_snapshots where id=?",(r["snapshot_id"],)).fetchone()
        decision=snapshot_decision(conn,s,user["school_id"],answer)
        previous=conn.execute("select evidence_id from response_decisions where id=?",(expected,)).fetchone()
        eid=previous[0] if previous else None
        if answer!=(r["final_answer"] or ""):
            eid=identifier("evidence")
            conn.execute("""insert into response_evidence(id,response_id,raw_answer,normalized_answer,extraction_method,created_at)
                           values(?,?,?,?,?,?)""",(eid,r["id"],answer,decision["normalized_answer"],"teacher_correction",timestamp()))
        did=_decision(conn,user,r["id"],eid,outcome,answer,decision["outcome"],None,
                      "teacher_correction" if correction else "teacher_review",code,reason,decision["answer_version"],expected,key)
        if not correction:
            conn.execute("update student_responses set initial_answer=? where id=?",(answer,r["id"]))
        elif r["outcome"] not in ("wrong","blank") and outcome in ("wrong","blank"):
            conn.execute("update student_responses set effective_from=? where id=?",(timestamp(),r["id"]))
        conn.execute("update response_review_items set status='resolved',resolved_by=?,resolved_at=?,resolution_note=? where response_id=? and status='open'",
                     (user["id"],timestamp(),reason,r["id"]))
        if correction:
            _sync_wrong(conn,conn.execute("select * from student_responses where id=?",(r["id"],)).fetchone())
            _publication(conn,user,a["id"],"correction")
        _audit(conn,user,"response_corrected" if correction else "response_reviewed",r["id"],{"decision_id":did,"reason":reason})
        conn.commit()
        return dict(message="更正已生效；首次记录和历史练习保留" if correction else "作答已复核",decision_id=did)
    except Exception:
        conn.rollback();raise
