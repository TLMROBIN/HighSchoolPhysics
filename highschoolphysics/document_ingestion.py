"""Transactional upload, candidate review, and publication services."""

from __future__ import annotations

import base64
import errno
import hashlib
from html.parser import HTMLParser
import io
import json
import math
import os
from pathlib import Path
import re
import uuid
from datetime import datetime, timedelta, timezone

from .document_models import ASSET_URI_RE, canonical_json, validate_question_document
from .document_store import (
    DocumentStore,
    DocumentStoreError,
    MAX_DOCUMENT_BYTES,
    clean_original_name,
    sha256_bytes,
    sniff_document,
)
from .errors import DomainError, PermissionDenied, ResourceNotFound, StateConflict


CHUNK_BYTES = 512 * 1024
MAX_PARTS = math.ceil(MAX_DOCUMENT_BYTES / CHUNK_BYTES)
UPLOAD_TTL_HOURS = 24
REQUEST_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class IngestionError(DomainError):
    def __init__(self, code, message, status=400, details=None):
        self.code = code
        self.status = status
        self.details = details or {}
        super().__init__(message)


def _storage_error(error):
    if error.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return IngestionError("storage_full", "Document storage is full", 507)
    return IngestionError("storage_unavailable", "Document storage is temporarily unavailable", 503)


def loads(value, default=None):
    if value is None or value == "":
        return default
    return json.loads(value)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(conn, actor, action, resource_type, resource_id, detail):
    conn.execute(
        "insert into audit_events(id,school_id,actor_id,action,resource_type,resource_id,detail_json) values(?,?,?,?,?,?,?)",
        ("audit-" + uuid.uuid4().hex, actor["school_id"], actor["id"], action, resource_type, resource_id, json.dumps(detail or {}, ensure_ascii=False)),
    )


def _issue_fingerprint(issue):
    mutable = {"severity", "state", "reviewed_by", "reviewed_at", "resolution_note"}
    return canonical_json({key: value for key, value in issue.items() if key not in mutable})


def _require_teacher(actor):
    if not actor or actor["role"] not in ("teacher", "admin"):
        raise PermissionDenied("Teacher or admin access required")


def _store_for_db(db_path=None, root=None):
    selected = root or os.environ.get("HSP_DOCUMENT_ROOT")
    if not selected and db_path and str(db_path) != ":memory:":
        selected = str(Path(db_path).resolve().parent / "documents")
    return DocumentStore(selected)


def _store_for_connection(conn):
    row = conn.execute("pragma database_list").fetchone()
    return _store_for_db(row["file"] if row and row["file"] else None)


def _validate_request_key(value):
    if not isinstance(value, str) or not REQUEST_KEY_RE.fullmatch(value):
        raise IngestionError("invalid_request_key", "A stable request_key of 8 to 96 characters is required")
    return value


def create_upload(conn, actor, payload, db_path=None, document_root=None):
    _require_teacher(actor)
    try:
        name = clean_original_name(payload.get("name"))
    except DocumentStoreError as exc:
        raise IngestionError(exc.code, str(exc), 415 if exc.code == "unsupported_format" else 400) from exc
    try:
        size = int(payload.get("size"))
    except (TypeError, ValueError) as exc:
        raise IngestionError("invalid_size", "The file size is invalid") from exc
    digest = payload.get("sha256")
    if size < 1 or size > MAX_DOCUMENT_BYTES:
        raise IngestionError("size_limit", "The document must be between 1 byte and 50 MiB", 413)
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise IngestionError("invalid_hash", "The file SHA-256 hash is required")
    request_key = _validate_request_key(payload.get("request_key"))
    role = payload.get("role", "paper")
    if role not in ("paper", "answers", "rubric"):
        raise IngestionError("invalid_role", "The document role is invalid")
    title = str(payload.get("title") or Path(name).stem).strip()[:240]
    if not title:
        raise IngestionError("invalid_title", "A paper title is required")
    original_paper_id = payload.get("original_paper_id")
    if role == "paper" and original_paper_id:
        raise IngestionError("invalid_paper_reference", "A new paper upload cannot reference an existing paper")
    if role != "paper":
        if not isinstance(original_paper_id, str) or not original_paper_id:
            raise IngestionError("paper_required", "An answer or rubric file must be linked to an existing paper")
        paper = conn.execute(
            "select id from original_papers where id=? and school_id=?",
            (original_paper_id, actor["school_id"]),
        ).fetchone()
        if paper is None:
            raise ResourceNotFound("The selected paper is unavailable")
    parts = math.ceil(size / CHUNK_BYTES)
    metadata = {
        "role": role,
        "title": title,
        "original_paper_id": original_paper_id,
    }
    request_hash = hashlib.sha256(canonical_json({
        "name": name,
        "size": size,
        "sha256": digest,
        "metadata": metadata,
    }).encode("utf-8")).hexdigest()
    conn.execute("begin immediate")
    existing = conn.execute(
        "select * from document_uploads where school_id=? and actor_id=? and request_key=?",
        (actor["school_id"], actor["id"], request_key),
    ).fetchone()
    if existing is not None:
        if existing["request_hash"] != request_hash:
            conn.rollback()
            raise IngestionError("idempotency_conflict", "This request_key was already used for different file details", 409)
        conn.rollback()
        return _upload_summary(conn, existing)
    pending = conn.execute(
        "select count(*) from document_uploads where school_id=? and actor_id=? and state in ('uploading','assembling') and expires_at>?",
        (actor["school_id"], actor["id"], _now()),
    ).fetchone()[0]
    if pending >= 2:
        conn.rollback()
        raise IngestionError("upload_limit", "Only two unfinished uploads are allowed per teacher", 429)
    upload_id = "upload-" + uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=UPLOAD_TTL_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """insert into document_uploads(
             id,school_id,actor_id,request_key,request_hash,original_name,declared_size,
             declared_sha256,total_parts,state,metadata_json,expires_at)
           values(?,?,?,?,?,?,?,?,?,'uploading',?,?)""",
        (
            upload_id,
            actor["school_id"],
            actor["id"],
            request_key,
            request_hash,
            name,
            size,
            digest,
            parts,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            expires_at,
        ),
    )
    conn.commit()
    return {
        "upload_id": upload_id,
        "chunk_size": CHUNK_BYTES,
        "total_parts": parts,
        "received_parts": [],
        "expires_at": expires_at,
        "state": "uploading",
    }


def _upload_for_actor(conn, actor, upload_id, mutation=False):
    _require_teacher(actor)
    row = conn.execute("select * from document_uploads where id=?", (upload_id,)).fetchone()
    if row is None or row["school_id"] != actor["school_id"]:
        raise ResourceNotFound("Upload not found")
    if row["actor_id"] != actor["id"] and actor["role"] != "admin":
        raise PermissionDenied("This upload belongs to another teacher")
    return row


def _upload_summary(conn, row):
    received = [item[0] for item in conn.execute(
        "select part_index from document_upload_parts where upload_id=? order by part_index", (row["id"],)
    )]
    return {
        "upload_id": row["id"],
        "chunk_size": CHUNK_BYTES,
        "total_parts": row["total_parts"],
        "received_parts": received,
        "expires_at": row["expires_at"],
        "state": row["state"],
        "task_id": row["task_id"],
        "document_file_id": row["document_file_id"],
    }


def store_upload_part(conn, actor, upload_id, payload, db_path=None, document_root=None):
    upload = _upload_for_actor(conn, actor, upload_id, mutation=True)
    if upload["state"] != "uploading" or upload["expires_at"] <= _now():
        raise StateConflict("This upload is no longer accepting parts")
    try:
        index = int(payload.get("index"))
    except (TypeError, ValueError) as exc:
        raise IngestionError("invalid_part_index", "A valid part index is required") from exc
    if index < 0 or index >= upload["total_parts"]:
        raise IngestionError("invalid_part_index", "The part index is outside this upload")
    supplied_hash = payload.get("sha256")
    encoded = payload.get("data_base64")
    if not isinstance(supplied_hash, str) or not SHA256_RE.fullmatch(supplied_hash) or not isinstance(encoded, str):
        raise IngestionError("invalid_part", "Part data and SHA-256 are required")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise IngestionError("invalid_base64", "The part data is not valid base64") from exc
    if not data or len(data) > CHUNK_BYTES:
        raise IngestionError("part_size_limit", "Each upload part must be between 1 byte and 512 KiB", 413)
    expected = min(CHUNK_BYTES, upload["declared_size"] - index * CHUNK_BYTES)
    if len(data) != expected:
        raise IngestionError("part_size_mismatch", "The part size does not match its position")
    actual_hash = sha256_bytes(data)
    if actual_hash != supplied_hash:
        raise IngestionError("part_hash_mismatch", "The part hash does not match its bytes")
    store = _store_for_db(db_path, document_root)
    relative = ["staging", upload_id, "part-%06d" % index]
    try:
        path, digest, size = store.atomic_write(relative, [data])
    except DocumentStoreError as exc:
        raise IngestionError(exc.code, str(exc), 409) from exc
    except OSError as exc:
        raise _storage_error(exc) from exc
    storage_key = "/".join(relative)
    try:
        conn.execute("begin immediate")
        fresh = conn.execute(
            "select state,expires_at from document_uploads where id=?", (upload_id,)
        ).fetchone()
        if fresh is None or fresh["state"] != "uploading" or fresh["expires_at"] <= _now():
            conn.rollback()
            raise StateConflict("This upload is no longer accepting parts")
        existing = conn.execute(
            "select sha256,byte_size from document_upload_parts where upload_id=? and part_index=?",
            (upload_id, index),
        ).fetchone()
        if existing is not None:
            conn.rollback()
            if existing["sha256"] != actual_hash or existing["byte_size"] != size:
                raise IngestionError("part_conflict", "This part index already contains different bytes", 409)
            return {"upload_id": upload_id, "index": index, "sha256": actual_hash, "state": "received"}
        conn.execute(
            "insert into document_upload_parts(upload_id,part_index,sha256,byte_size,storage_key) values(?,?,?,?,?)",
            (upload_id, index, digest, size, storage_key),
        )
        conn.commit()
    except (IngestionError, StateConflict):
        if conn.in_transaction:
            conn.rollback()
        raise
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        winner = conn.execute(
            "select sha256,byte_size from document_upload_parts where upload_id=? and part_index=?",
            (upload_id, index),
        ).fetchone()
        if winner is None or winner["sha256"] != actual_hash or winner["byte_size"] != size:
            raise IngestionError("part_conflict", "This part index already contains different bytes", 409)
    return {"upload_id": upload_id, "index": index, "sha256": actual_hash, "state": "received"}


def get_upload(conn, actor, upload_id):
    row = _upload_for_actor(conn, actor, upload_id)
    return _upload_summary(conn, row)


def _recover_expired_assembly(conn, upload_id):
    conn.execute("begin immediate")
    now = _now()
    row = conn.execute(
        "select state,assembly_token,assembly_lease_until from document_uploads where id=?",
        (upload_id,),
    ).fetchone()
    recovered = False
    if row is not None and row["state"] == "assembling" and (row["assembly_lease_until"] or "") < now:
        changed = conn.execute(
            "update document_uploads set state='uploading',assembly_token=NULL,assembly_lease_until=NULL where id=? and state='assembling' and assembly_token is ? and coalesce(assembly_lease_until,'')<?",
            (upload_id, row["assembly_token"], now),
        )
        recovered = bool(changed.rowcount)
    conn.commit()
    return recovered


def _completed_upload_result(upload_id, row):
    return {"upload_id": upload_id, "document_file_id": row["document_file_id"], "task_id": row["task_id"], "status": "queued"}


def complete_upload(conn, actor, upload_id, db_path=None, document_root=None):
    row = _upload_for_actor(conn, actor, upload_id, mutation=True)
    if row["state"] == "assembling":
        _recover_expired_assembly(conn, upload_id)
        row = conn.execute("select * from document_uploads where id=?", (upload_id,)).fetchone()
    if row["state"] == "completed":
        return _completed_upload_result(upload_id, row)
    if row["state"] != "uploading":
        raise StateConflict("This upload cannot be completed in its current state")
    parts = conn.execute(
        "select * from document_upload_parts where upload_id=? order by part_index", (upload_id,)
    ).fetchall()
    if len(parts) != row["total_parts"] or [item["part_index"] for item in parts] != list(range(row["total_parts"])):
        received = {item["part_index"] for item in parts}
        missing = [index for index in range(row["total_parts"]) if index not in received]
        raise IngestionError("parts_missing", "Upload is missing one or more parts", 409, {"missing_parts": missing[:100]})
    token = uuid.uuid4().hex
    conn.execute("begin immediate")
    fresh_upload = conn.execute(
        "select state,expires_at from document_uploads where id=?", (upload_id,)
    ).fetchone()
    if fresh_upload is None or fresh_upload["state"] != "uploading" or fresh_upload["expires_at"] <= _now():
        conn.rollback()
        raise StateConflict("This upload is no longer accepting completion requests")
    updated = conn.execute(
        "update document_uploads set state='assembling',assembly_token=?,assembly_lease_until=? where id=? and state='uploading'",
        (token, (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), upload_id),
    ).rowcount
    if not updated:
        winner = conn.execute("select state,document_file_id,task_id from document_uploads where id=?", (upload_id,)).fetchone()
        conn.rollback()
        if winner is not None and winner["state"] == "completed":
            return {"upload_id": upload_id, "document_file_id": winner["document_file_id"], "task_id": winner["task_id"], "status": "queued"}
        raise StateConflict("Another request is completing this upload")
    conn.commit()
    store = _store_for_db(db_path, document_root)
    assembled_key = ["staging", upload_id, "assembled" + Path(row["original_name"]).suffix.lower()]

    def data_chunks():
        for part in parts:
            part_path = store._path(*part["storage_key"].split("/"))
            with part_path.open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk

    try:
        assembled_path, assembled_hash, assembled_size = store.atomic_write(assembled_key, data_chunks())
        fresh = conn.execute(
            "select state,assembly_token,document_file_id,task_id from document_uploads where id=?",
            (upload_id,),
        ).fetchone()
        if fresh is not None and fresh["state"] == "completed":
            return _completed_upload_result(upload_id, fresh)
        if fresh is None or fresh["state"] != "assembling" or fresh["assembly_token"] != token:
            raise StateConflict("The upload lease changed while assembling the document")
        if assembled_size != row["declared_size"] or assembled_hash != row["declared_sha256"]:
            raise IngestionError("file_hash_mismatch", "The completed file size or SHA-256 does not match")
        try:
            file_info = sniff_document(assembled_path, row["original_name"])
        except DocumentStoreError as exc:
            raise IngestionError(exc.code, str(exc), 415 if exc.code == "unsupported_format" else 422) from exc
        metadata = loads(row["metadata_json"], {})
        document_id = "doc-" + hashlib.sha256(upload_id.encode("utf-8")).hexdigest()[:32]
        document = store.store_original(
            row["school_id"],
            document_id,
            row["original_name"],
            assembled_path.read_bytes(),
            row["declared_sha256"],
        )
        original_paper_id = metadata.get("original_paper_id")
        batch_id = "batch-" + uuid.uuid4().hex
        task_id = "task-" + uuid.uuid4().hex
        if metadata.get("role") == "paper":
            original_paper_id = "paper-" + uuid.uuid4().hex
        title = metadata.get("title") or Path(row["original_name"]).stem
        conn.execute("begin immediate")
        fresh = conn.execute(
            "select state,assembly_token,document_file_id,task_id from document_uploads where id=?", (upload_id,)
        ).fetchone()
        if fresh is None or fresh["state"] != "assembling" or fresh["assembly_token"] != token:
            conn.rollback()
            if fresh is not None and fresh["state"] == "completed":
                return _completed_upload_result(upload_id, fresh)
            raise StateConflict("The upload lease changed before completion")
        if metadata.get("role") == "paper":
            conn.execute(
                """insert into original_papers(id,school_id,title,document_name,source_school,source_publisher,exam_type,grade,term,status,created_by)
                   values(?,?,?,?,?,?,?,?,?,'active',?)""",
                (original_paper_id, row["school_id"], title, row["original_name"], "", "", "", "", "", actor["id"]),
            )
        batch_mode = "document-answer" if metadata.get("role") in ("answers", "rubric") else "document-ingestion"
        conn.execute(
            """insert into question_import_batches(id,school_id,original_paper_id,source_file_name,parser_mode,status,created_by)
               values(?,?,?,?,?,'queued',?)""",
            (batch_id, row["school_id"], original_paper_id, row["original_name"], batch_mode, actor["id"]),
        )
        conn.execute(
            """insert into document_files(id,school_id,original_paper_id,role,parent_file_id,original_name,mime_type,byte_size,sha256,storage_key,created_by)
               values(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                row["school_id"],
                original_paper_id,
                metadata["role"],
                None,
                row["original_name"],
                file_info["mime_type"],
                file_info["byte_size"],
                document["sha256"],
                document["storage_key"],
                actor["id"],
            ),
        )
        conn.execute(
            """insert into document_parse_tasks(
                 id,school_id,file_name,parser,parser_version,status,output_json,failure_reason,
                 original_paper_id,import_batch_id,parser_mode,fallback_policy,source_text,
                 input_document_id,phase,progress_json,generation,attempts,cancel_requested,
                 error_code,available_at,updated_at,created_by)
               values(?,?,?,?,?,'queued','{}','',?,?,?,'fail_closed','',?,'queued','{}',1,0,0,'',?,?,?)""",
            (
                task_id,
                row["school_id"],
                row["original_name"],
                "document-adapter",
                "1.0.0",
                original_paper_id,
                batch_id,
                batch_mode,
                document_id,
                _now(),
                _now(),
                actor["id"],
            ),
        )
        conn.execute(
            "update document_uploads set state='completed',document_file_id=?,task_id=?,assembly_token=NULL,assembly_lease_until=NULL where id=? and assembly_token=?",
            (document_id, task_id, upload_id, token),
        )
        _audit(conn, actor, "document_upload_completed", "document_file", document_id, {"task_id": task_id, "role": metadata["role"], "byte_size": file_info["byte_size"], "sha256": document["sha256"]})
        conn.commit()
        _remove_upload_parts(store, upload_id, parts)
        return {"upload_id": upload_id, "document_file_id": document_id, "original_paper_id": original_paper_id, "task_id": task_id, "status": "queued"}
    except Exception as exc:
        if conn.in_transaction:
            conn.rollback()
        winner = conn.execute(
            "select state,document_file_id,task_id from document_uploads where id=?",
            (upload_id,),
        ).fetchone()
        if winner is not None and winner["state"] == "completed":
            return _completed_upload_result(upload_id, winner)
        conn.execute(
            "update document_uploads set state='uploading',assembly_token=NULL,assembly_lease_until=NULL where id=? and assembly_token=?",
            (upload_id, token),
        )
        conn.commit()
        if isinstance(exc, OSError):
            raise _storage_error(exc) from exc
        raise


def _remove_upload_parts(store, upload_id, parts):
    for part in parts:
        try:
            path = store._path(*part["storage_key"].split("/"))
            path.unlink(missing_ok=True)
        except (OSError, DocumentStoreError):
            pass
    try:
        assembled = store._path("staging", upload_id)
        # Upload metadata remains for idempotent complete; remove only payloads and the directory when empty.
        for path in assembled.iterdir():
            if path.is_file() and path.name.startswith("assembled"):
                path.unlink()
        assembled.rmdir()
    except (OSError, FileNotFoundError, DocumentStoreError):
        pass


def cancel_upload(conn, actor, upload_id, db_path=None, document_root=None):
    row = _upload_for_actor(conn, actor, upload_id, mutation=True)
    if row["state"] in ("completed", "cancelled", "expired"):
        return {"upload_id": upload_id, "state": row["state"], "task_id": row["task_id"]}
    if row["state"] == "assembling":
        raise StateConflict("The upload is being assembled; retry cancellation in a moment")
    parts = conn.execute("select storage_key from document_upload_parts where upload_id=?", (upload_id,)).fetchall()
    conn.execute("begin immediate")
    conn.execute("update document_uploads set state='cancelled' where id=? and state='uploading'", (upload_id,))
    conn.commit()
    _remove_upload_parts(_store_for_db(db_path, document_root), upload_id, parts)
    return {"upload_id": upload_id, "state": "cancelled"}


def _task_for_actor(conn, actor, task_id, allow_admin=True):
    _require_teacher(actor)
    row = conn.execute("select * from document_parse_tasks where id=?", (task_id,)).fetchone()
    if row is None or row["school_id"] != actor["school_id"]:
        raise ResourceNotFound("Document task not found")
    if row["created_by"] != actor["id"] and not (allow_admin and actor["role"] == "admin"):
        raise PermissionDenied("This document task belongs to another teacher")
    return row


def list_tasks(conn, actor, limit=25, cursor=None):
    _require_teacher(actor)
    limit = max(1, min(int(limit), 50))
    clauses = ["task.school_id=?"]
    params = [actor["school_id"]]
    if actor["role"] != "admin":
        clauses.append("task.created_by=?")
        params.append(actor["id"])
    if cursor:
        clauses.append("task.created_at < ?")
        params.append(cursor)
    params.append(limit)
    rows = conn.execute(
        """select task.id,task.file_name,task.status,task.phase,task.progress_json,task.error_code,
                  task.created_at,task.updated_at,task.original_paper_id,file.role as document_role
           from document_parse_tasks task
           join document_files file on file.id=task.input_document_id
           where """ + " and ".join(clauses) + " order by task.created_at desc,task.id desc limit ?",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def get_task(conn, actor, task_id):
    row = _task_for_actor(conn, actor, task_id)
    file_row = conn.execute(
        "select role from document_files where id=? and school_id=?",
        (row["input_document_id"], actor["school_id"]),
    ).fetchone()
    result = {
        "id": row["id"],
        "file_name": row["file_name"],
        "original_paper_id": row["original_paper_id"],
        "document_role": file_row["role"] if file_row else "paper",
        "status": row["status"],
        "phase": row["phase"],
        "progress": loads(row["progress_json"], {}),
        "error_code": row["error_code"],
        "failure_reason": row["failure_reason"],
        "conversion_id": row["conversion_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    result["item_count"] = conn.execute("select count(*) from parsed_question_items where parse_task_id=? and disposition='active'", (task_id,)).fetchone()[0]
    result["published_count"] = conn.execute("select count(*) from import_item_publications pub join parsed_question_items item on item.id=pub.parsed_item_id where item.parse_task_id=?", (task_id,)).fetchone()[0]
    return result


def _paper_asset_ids(conn, school_id, original_paper_id, created_by=None):
    if not original_paper_id:
        return set()
    params = [school_id, original_paper_id]
    creator_filter = ""
    if created_by:
        creator_filter = " and task.created_by=?"
        params.append(created_by)
    return {
        row[0]
        for row in conn.execute(
            """select distinct ref.asset_id
               from document_parse_tasks task
               join document_conversions conversion on conversion.id=task.conversion_id
               join document_files file on file.id=task.input_document_id
               join conversion_asset_refs ref on ref.conversion_id=conversion.id
               where task.school_id=? and task.original_paper_id=?
                 and file.role in ('paper','answers','rubric')""" + creator_filter,
            params,
        )
    }


def _answer_groups(conn, actor, answer_task_id, db_path=None, document_root=None):
    from .question_splitter import _question_starts, split_document_ir

    answer_task = _task_for_actor(conn, actor, answer_task_id)
    if answer_task["status"] not in ("parsed", "partially_parsed") or not answer_task["conversion_id"]:
        raise IngestionError("answer_task_not_ready", "答案文件尚未转换完成", 409)
    file_row = conn.execute(
        "select role from document_files where id=? and school_id=?",
        (answer_task["input_document_id"], actor["school_id"]),
    ).fetchone()
    if file_row is None or file_row["role"] not in ("answers", "rubric"):
        raise IngestionError("invalid_answer_task", "请选择已关联到本试卷的答案或评分文件", 422)
    conversion = conn.execute(
        "select layout_key from document_conversions where id=? and task_id=? and school_id=?",
        (answer_task["conversion_id"], answer_task_id, actor["school_id"]),
    ).fetchone()
    if conversion is None:
        raise IngestionError("answer_conversion_unavailable", "答案文件转换结果不可用", 424)
    try:
        layout = json.loads(
            _store_for_db(db_path, document_root).read(
                conversion["layout_key"], max_bytes=100 * 1024 * 1024
            ).decode("utf-8")
        )
    except (DocumentStoreError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IngestionError("answer_conversion_unavailable", "答案文件转换结果无法读取", 424) from exc
    blocks = layout.get("blocks") if isinstance(layout, dict) else None
    if not isinstance(blocks, list):
        raise IngestionError("invalid_answer_conversion", "答案文件没有可编辑的转换正文", 422)
    split = split_document_ir(layout)
    answer_ids = set(split.get("answer_blocks", []))
    answer_blocks = [block for block in blocks if block.get("id") in answer_ids]
    if not answer_blocks:
        answer_blocks = blocks

    groups = {}
    current_number = None
    current_parts = []

    def flush():
        nonlocal current_number, current_parts
        if current_number is not None and current_parts:
            entry = groups.setdefault(current_number, [])
            markdown = "\n\n".join(part["markdown"] for part in current_parts if part["markdown"]).strip()
            if markdown:
                asset_ids = sorted({asset_id for part in current_parts for asset_id in re.findall(r"\(asset:([A-Za-z0-9_-]+)\)", part["markdown"])})
                entry.append({"markdown": markdown, "source_spans": [part["span"] for part in current_parts], "asset_refs": asset_ids})
        current_number = None
        current_parts = []

    for block in answer_blocks:
        text = block.get("markdown", "")
        matches = _question_starts(text)
        if not matches:
            if current_number is not None and text.strip():
                current_parts.append({
                    "markdown": text.strip(),
                    "span": {
                        "block_id": "answer_%s_%s" % (answer_task_id[-24:], block["id"]),
                        "source_locator": dict(block.get("source_locator") or {}, task_id=answer_task_id, role="answers"),
                    },
                })
            continue
        for index, marker in enumerate(matches):
            flush()
            end = matches[index + 1]["start"] if index + 1 < len(matches) else len(text)
            body_start = marker["body_start"]
            body = text[body_start:end].strip()
            current_number = marker["number"]
            if body:
                current_parts.append({
                    "markdown": body,
                    "span": {
                        "block_id": "answer_%s_%s" % (answer_task_id[-24:], block["id"]),
                        "source_locator": dict(block.get("source_locator") or {}, task_id=answer_task_id, role="answers"),
                        "start": body_start,
                        "end": end,
                    },
                })
        if matches and current_number is not None and matches[-1]["start"] < len(text):
            # The final anchor owns the remainder of this block.
            pass
    flush()
    return groups


def attach_answers(conn, actor, paper_task_id, payload, db_path=None, document_root=None):
    """Preview exact question-number matches, then attach selected answers for review."""
    _require_teacher(actor)
    paper_task = _task_for_actor(conn, actor, paper_task_id)
    if paper_task["status"] not in ("parsed", "partially_parsed"):
        raise IngestionError("paper_task_not_ready", "题卷尚未转换完成", 409)
    paper_file = conn.execute(
        "select role from document_files where id=? and school_id=?",
        (paper_task["input_document_id"], actor["school_id"]),
    ).fetchone()
    if paper_file is None or paper_file["role"] != "paper":
        raise IngestionError("invalid_paper_task", "答案必须关联到原试卷任务", 422)
    answer_task_id = payload.get("answer_task_id")
    if not isinstance(answer_task_id, str) or not answer_task_id:
        raise IngestionError("answer_task_required", "请选择答案文件任务", 400)
    answer_task = _task_for_actor(conn, actor, answer_task_id)
    if answer_task["original_paper_id"] != paper_task["original_paper_id"]:
        raise ResourceNotFound("答案文件与当前原卷不匹配")
    groups = _answer_groups(conn, actor, answer_task_id, db_path, document_root)
    items = conn.execute(
        """select item.id,item.question_number,item.document_json,item.review_revision,item.disposition,
                  publication.revision_id as published_revision_id
           from parsed_question_items item
           left join import_item_publications publication on publication.parsed_item_id=item.id
           where item.parse_task_id=? and item.school_id=? and item.disposition='active'
           order by item.item_index,item.id""",
        (paper_task_id, actor["school_id"]),
    ).fetchall()
    by_number = {}
    for item in items:
        document = loads(item["document_json"], {})
        number = str(document.get("number") or item["question_number"] or "").strip()
        by_number.setdefault(number, []).append(item)
    proposals = []
    issues = []
    for number, answer_entries in sorted(groups.items(), key=lambda pair: int(pair[0])):
        targets = by_number.get(number, [])
        if len(answer_entries) != 1:
            issues.append({"answer_number": number, "code": "duplicate_answer_number", "count": len(answer_entries)})
            continue
        if len(targets) != 1:
            issues.append({"answer_number": number, "code": "question_match_not_unique", "count": len(targets)})
            continue
        target = targets[0]
        document = loads(target["document_json"], {})
        if target["published_revision_id"]:
            issues.append({"answer_number": number, "item_id": target["id"], "code": "published_question_immutable"})
            continue
        if document.get("answer_state") != "missing" or document.get("answer_md") or any(child.get("answer_md") for child in document.get("children", [])):
            issues.append({"answer_number": number, "item_id": target["id"], "code": "existing_answer_preserved"})
            continue
        entry = answer_entries[0]
        proposals.append({
            "item_id": target["id"],
            "question_number": number,
            "answer_number": number,
            "expected_revision": target["review_revision"],
            "answer_markdown": entry["markdown"],
            "source_spans": entry["source_spans"],
            "asset_refs": entry["asset_refs"],
        })
    preview = {"paper_task_id": paper_task_id, "answer_task_id": answer_task_id, "matches": proposals, "issues": issues}
    mappings = payload.get("mappings")
    if mappings is None:
        return {"preview": True, **preview}
    if not isinstance(mappings, list) or not mappings:
        raise IngestionError("empty_answer_mapping", "至少选择一条待核验的答案匹配", 422)
    request_key = _validate_request_key(payload.get("request_key"))
    clean_mappings = []
    proposal_by_item = {entry["item_id"]: entry for entry in proposals}
    seen = set()
    for mapping in mappings:
        if not isinstance(mapping, dict) or not isinstance(mapping.get("item_id"), str) or not isinstance(mapping.get("answer_number"), str) or not isinstance(mapping.get("expected_revision"), int) or isinstance(mapping.get("expected_revision"), bool):
            raise IngestionError("invalid_answer_mapping", "答案匹配需要候选、题号和复核版本", 422)
        if mapping["item_id"] in seen:
            raise IngestionError("invalid_answer_mapping", "同一题目不能重复关联答案", 422)
        clean_mappings.append({"item_id": mapping["item_id"], "answer_number": mapping["answer_number"], "expected_revision": mapping["expected_revision"]})
        seen.add(mapping["item_id"])
    request_hash = hashlib.sha256(canonical_json({"paper_task_id": paper_task_id, "answer_task_id": answer_task_id, "mappings": clean_mappings}).encode()).hexdigest()
    cached = _operation_result(conn, actor, "attach_answers", request_key, request_hash)
    if cached is not None:
        return cached
    for mapping in clean_mappings:
        proposal = proposal_by_item.get(mapping["item_id"])
        if proposal is None or proposal["answer_number"] != mapping["answer_number"] or proposal["expected_revision"] != mapping["expected_revision"]:
            raise IngestionError("answer_mapping_changed", "所选映射与当前预览不一致，请重新预览", 409)
    conn.execute("begin immediate")
    cached = _operation_result(conn, actor, "attach_answers", request_key, request_hash)
    if cached is not None:
        conn.rollback()
        return cached
    applied = []
    known_assets = _paper_asset_ids(conn, actor["school_id"], paper_task["original_paper_id"], paper_task["created_by"])
    for mapping in clean_mappings:
        proposal = proposal_by_item[mapping["item_id"]]
        row = conn.execute(
            "select document_json,review_revision,conversion_id from parsed_question_items where id=? and parse_task_id=? and school_id=? and disposition='active'",
            (mapping["item_id"], paper_task_id, actor["school_id"]),
        ).fetchone()
        if row is None or row["review_revision"] != mapping["expected_revision"]:
            conn.rollback()
            raise IngestionError("revision_conflict", "题目在答案匹配期间发生变化，请重新预览", 409)
        publication = conn.execute("select 1 from import_item_publications where parsed_item_id=?", (mapping["item_id"],)).fetchone()
        document = loads(row["document_json"], {})
        if publication or document.get("answer_state") != "missing" or document.get("answer_md"):
            conn.rollback()
            raise StateConflict("已有答案或已入库题目不能由附件覆盖")
        for asset_id in proposal["asset_refs"]:
            if asset_id not in known_assets:
                conn.rollback()
                raise IngestionError("answer_asset_unavailable", "答案资源不属于当前原卷", 422)
        document["answer_md"] = proposal["answer_markdown"]
        document["answer_state"] = "needs_review"
        document["asset_refs"] = sorted(set(document.get("asset_refs", [])) | set(proposal["asset_refs"]))
        document["source_spans"] = list(document.get("source_spans", [])) + proposal["source_spans"]
        answer_issue = {"code": "attached_answer_requires_review", "severity": "review", "field": "answer_md", "message": "独立答案文件已匹配；请对照原文件核验答案与解析"}
        issues = list(document.get("issues", []))
        if answer_issue not in issues:
            issues.append(answer_issue)
        document["issues"] = issues
        try:
            document = validate_question_document(document, known_asset_ids=known_assets)
        except Exception as exc:
            conn.rollback()
            raise IngestionError("invalid_answer_content", "附件答案无法合并到可编辑题目内容", 422) from exc
        answer_json = json.dumps({"markdown": document["answer_md"], "state": document["answer_state"], "grading_rule": document.get("grading_rule")}, ensure_ascii=False)
        conn.execute(
            """update parsed_question_items set document_json=?,answer_json=?,issues_json=?,review_revision=review_revision+1,
                      review_status='needs_review',updated_by=?,updated_at=? where id=? and review_revision=?""",
            (json.dumps(document, ensure_ascii=False, sort_keys=True), answer_json, json.dumps(issues, ensure_ascii=False, sort_keys=True), actor["id"], _now(), mapping["item_id"], mapping["expected_revision"]),
        )
        applied.append({"item_id": mapping["item_id"], "question_number": mapping["answer_number"], "review_revision": mapping["expected_revision"] + 1, "answer_state": "needs_review"})
    result = {"paper_task_id": paper_task_id, "answer_task_id": answer_task_id, "attached": applied, "issues": issues}
    conn.execute(
        "insert into content_operation_keys(school_id,actor_id,operation,request_key,request_hash,result_json) values(?,?,?,?,?,?)",
        (actor["school_id"], actor["id"], "attach_answers", request_key, request_hash, json.dumps(result, ensure_ascii=False)),
    )
    _audit(conn, actor, "document_answers_attached", "document_parse_task", paper_task_id, {"answer_task_id": answer_task_id, "item_ids": [entry["item_id"] for entry in applied], "count": len(applied)})
    conn.commit()
    return result


def get_task_items(conn, actor, task_id, cursor=0, limit=50):
    _task_for_actor(conn, actor, task_id)
    try:
        cursor = max(0, int(cursor))
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError) as exc:
        raise IngestionError("invalid_pagination", "Item pagination is invalid") from exc
    rows = conn.execute(
        """select pqi.*,
                  pub.revision_id as published_revision_id,
                  pub.question_ids_json as published_question_ids
           from parsed_question_items pqi
           left join import_item_publications pub on pub.parsed_item_id=pqi.id
           where pqi.parse_task_id=? and pqi.disposition='active'
           order by pqi.item_index,pqi.id limit ? offset ?""",
        (task_id, limit, cursor),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "item_index": row["item_index"],
            "question_number": row["question_number"],
            "page_number": row["page_number"],
            "document": loads(row["document_json"], {}),
            "review_revision": row["review_revision"],
            "issues": loads(row["issues_json"], []),
            "review_status": row["review_status"],
            "disposition": row["disposition"],
            "published_revision_id": row["published_revision_id"],
            "published_question_ids": loads(row["published_question_ids"], []),
        }
        for row in rows
    ]


def get_task_source_assets(conn, actor, task_id):
    """List reusable figure assets for an authorized teacher review page."""
    _task_for_actor(conn, actor, task_id)
    rows = conn.execute(
        """select ref.asset_id,asset.mime_type,asset.sha256,ref.source_locator_json,ref.asset_role
           from document_parse_tasks task
           join document_conversions conversion on conversion.id=task.conversion_id
           join conversion_asset_refs ref on ref.conversion_id=conversion.id
           join document_assets asset on asset.id=ref.asset_id
           where task.id=? and task.school_id=? and asset.school_id=?
             and ref.asset_role in ('figure','option_figure')
           order by ref.asset_id,ref.locator_key""",
        (task_id, actor["school_id"], actor["school_id"]),
    ).fetchall()
    assets = {}
    for row in rows:
        asset = assets.setdefault(
            row["asset_id"],
            {
                "id": row["asset_id"],
                "mime_type": row["mime_type"],
                "sha256": row["sha256"],
                "source_locators": [],
            },
        )
        locator = loads(row["source_locator_json"], {})
        if locator not in asset["source_locators"]:
            asset["source_locators"].append(locator)
    return list(assets.values())


def preview_candidate(conn, actor, item_id, task_id, markdown, base_path=""):
    from .question_content import parse_question_md
    from .question_rendering import render_question

    _task_for_actor(conn, actor, task_id)
    item = conn.execute(
        """select item.document_json,item.conversion_id,task.original_paper_id,task.created_by
           from parsed_question_items item join document_parse_tasks task on task.id=item.parse_task_id
           where item.id=? and item.parse_task_id=? and item.school_id=? and item.disposition='active'""",
        (item_id, task_id, actor["school_id"]),
    ).fetchone()
    if item is None:
        raise ResourceNotFound("Document question not found")
    original = loads(item["document_json"], None)
    if not original:
        raise IngestionError("invalid_candidate", "This candidate has no editable Markdown source", 422)
    try:
        known_assets = _paper_asset_ids(conn, actor["school_id"], item["original_paper_id"], item["created_by"])
        document = parse_question_md(markdown, original, known_asset_ids=known_assets)
    except Exception as exc:
        raise IngestionError("invalid_markdown", str(exc), 422) from exc
    asset_prefix = base_path.rstrip("/")
    html_fragment = render_question(
        document,
        asset_url=lambda asset_id: "%s/api/documents/assets/%s?task_id=%s" % (asset_prefix, asset_id, task_id),
        include_solution=True,
    )
    return {"document": document, "html": html_fragment}


def _operation_result(conn, actor, operation, request_key, request_hash):
    row = conn.execute(
        "select request_hash,result_json from content_operation_keys where school_id=? and actor_id=? and operation=? and request_key=?",
        (actor["school_id"], actor["id"], operation, request_key),
    ).fetchone()
    if row is None:
        return None
    if row["request_hash"] != request_hash:
        raise IngestionError("idempotency_conflict", "The request_key was already used for different content", 409)
    return loads(row["result_json"], {})


def save_candidate(conn, actor, item_id, payload):
    _require_teacher(actor)
    request_key = _validate_request_key(payload.get("request_key"))
    expected_revision = payload.get("expected_revision")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise IngestionError("invalid_revision", "A positive expected review revision is required")
    request_hash = hashlib.sha256(canonical_json({"item_id": item_id, "expected_revision": expected_revision, "document": payload.get("document"), "resolved_issue_ids": payload.get("resolved_issue_ids", []), "resolution_note": payload.get("resolution_note", "")}).encode("utf-8")).hexdigest()
    cached = _operation_result(conn, actor, "save_candidate", request_key, request_hash)
    if cached is not None:
        return cached
    item = conn.execute(
        """select pqi.*,task.school_id as task_school_id,task.created_by as task_created_by,
                  task.conversion_id as task_conversion_id,task.original_paper_id as task_original_paper_id
           from parsed_question_items pqi join document_parse_tasks task on task.id=pqi.parse_task_id where pqi.id=?""",
        (item_id,),
    ).fetchone()
    if item is None or item["task_school_id"] != actor["school_id"]:
        raise ResourceNotFound("Document question not found")
    if item["task_created_by"] != actor["id"] and actor["role"] != "admin":
        raise PermissionDenied("This question belongs to another teacher")
    if item["review_revision"] != expected_revision:
        raise IngestionError("revision_conflict", "This question changed in another session; reload before saving", 409, {"current_revision": item["review_revision"]})
    publication = conn.execute("select 1 from import_item_publications where parsed_item_id=?", (item_id,)).fetchone()
    if publication:
        raise StateConflict("A published question is immutable; create a new revision instead")
    if not isinstance(payload.get("document"), dict):
        raise IngestionError("invalid_document", "An editable question document is required", 422)
    known_assets = _paper_asset_ids(conn, actor["school_id"], item["task_original_paper_id"], item["task_created_by"])
    try:
        document = validate_question_document(payload["document"], known_asset_ids=known_assets)
    except Exception as exc:
        raise IngestionError("invalid_document", str(exc), 422) from exc
    old_issues = loads(item["issues_json"], [])
    resolved_ids = payload.get("resolved_issue_ids", [])
    resolution_note = str(payload.get("resolution_note") or "").strip()
    if not isinstance(resolved_ids, list) or any(not isinstance(value, str) for value in resolved_ids):
        raise IngestionError("invalid_issue_resolution", "Issue resolution ids must be an array of strings")
    if resolved_ids and (len(resolution_note) < 8 or len(resolution_note) > 1000):
        raise IngestionError("resolution_note_required", "Record what was checked in at least eight characters")
    if set(resolved_ids) - {hashlib.sha256(canonical_json(issue).encode()).hexdigest()[:20] for issue in old_issues}:
        raise IngestionError("invalid_issue_resolution", "An issue selected for review no longer exists")
    resolved = []
    for issue in old_issues:
        issue_id = hashlib.sha256(canonical_json(issue).encode()).hexdigest()[:20]
        current = dict(issue)
        if issue_id in resolved_ids:
            current.update({"severity": "info", "state": "resolved", "reviewed_by": actor["id"], "reviewed_at": _now(), "resolution_note": resolution_note})
        resolved.append(current)
    provided = document.get("issues", [])
    merged = {_issue_fingerprint(issue): issue for issue in resolved}
    resolved_fingerprints = {_issue_fingerprint(issue) for issue in resolved if issue.get("state") == "resolved"}
    for issue in provided:
        fingerprint = _issue_fingerprint(issue)
        if fingerprint in resolved_fingerprints:
            continue
        merged.setdefault(fingerprint, issue)
    document["issues"] = list(merged.values())
    try:
        document = validate_question_document(document, known_asset_ids=known_assets)
    except Exception as exc:
        raise IngestionError("invalid_document", str(exc), 422) from exc
    options = {option["key"]: option["markdown"] for option in document["options"]}
    stem = document["stem_md"]
    answer = document["answer_md"]
    issues_json = json.dumps(document["issues"], ensure_ascii=False, sort_keys=True)
    item_index = item["item_index"]
    source_spans = document["source_spans"]
    page = next((span.get("source_locator", {}).get("page") for span in source_spans if span.get("source_locator", {}).get("page")), None)
    conn.execute("begin immediate")
    changed = conn.execute(
        """update parsed_question_items set document_json=?,question_number=?,page_number=?,stem=?,question_type=?,options_json=?,answer_json=?,analysis=?,issues_json=?,review_revision=review_revision+1,review_status='needs_review',updated_by=?,updated_at=?
           where id=? and review_revision=? and disposition='active'""",
        (
            json.dumps(document, ensure_ascii=False, sort_keys=True),
            document["number"],
            page,
            stem,
            document["kind"],
            json.dumps(options, ensure_ascii=False),
            json.dumps({"markdown": answer, "state": document["answer_state"], "grading_rule": document.get("grading_rule")}, ensure_ascii=False),
            document["analysis_md"],
            issues_json,
            actor["id"],
            _now(),
            item_id,
            expected_revision,
        ),
    ).rowcount
    if not changed:
        conn.rollback()
        raise IngestionError("revision_conflict", "This question changed in another session; reload before saving", 409)
    result = {"id": item_id, "review_revision": expected_revision + 1, "document": document}
    conn.execute(
        "insert into content_operation_keys(school_id,actor_id,operation,request_key,request_hash,result_json) values(?,?,?,?,?,?)",
        (actor["school_id"], actor["id"], "save_candidate", request_key, request_hash, json.dumps(result, ensure_ascii=False)),
    )
    _audit(conn, actor, "document_candidate_saved", "parsed_question_item", item_id, {"old_revision": expected_revision, "new_revision": expected_revision + 1, "resolved_issue_count": len(resolved_ids), "issue_count": len(document["issues"])})
    conn.commit()
    return result


def _question_rows(document, parent_stem):
    if not document["children"]:
        return [{"child_key": "", "label": "", "kind": document["kind"], "stem": document["stem_md"], "options": document["options"], "answer_md": document["answer_md"], "analysis_md": document["analysis_md"], "answer_state": document["answer_state"], "grading_rule": document.get("grading_rule")}]
    rows = []
    for child in document["children"]:
        combined_stem = "\n\n".join(part for part in (parent_stem, child["stem_md"]) if part)
        rows.append({"child_key": child["key"], "label": child["label"], "kind": child["kind"], "stem": combined_stem, "options": child.get("options", []), "answer_md": child.get("answer_md", ""), "analysis_md": child.get("analysis_md", ""), "answer_state": child["answer_state"], "grading_rule": child.get("grading_rule")})
    return rows


class _LegacyTextParser(HTMLParser):
    """Flatten safe Markdown HTML into readable text for the v11 reader."""

    BLOCK_TAGS = {"br", "div", "li", "ol", "p", "ul"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            alt = (attrs.get("alt") or "题图").strip()
            self.parts.append(" [图：%s] " % alt)
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def _legacy_question_text(markdown):
    """Keep published Markdown readable to the original escaped-text UI."""
    from .question_rendering import render_markdown

    parser = _LegacyTextParser()
    parser.feed(render_markdown(markdown, lambda asset_id: "/exam-media?id=" + asset_id))
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for index, line in enumerate(lines) if line or (index and lines[index - 1]))


def _legacy_png(payload):
    """The v11 media route labels every exam asset as PNG, so store real PNG bytes."""
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(payload)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            if image.mode not in ("RGB", "L", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except Exception as exc:
        raise IngestionError("asset_unavailable", "A referenced image cannot be decoded for legacy question views", 422) from exc


def _legacy_visible_asset_ids(content_row):
    fields = [content_row["stem"]]
    fields.extend(option["markdown"] for option in content_row["options"])
    ordered = []
    for field in fields:
        for asset_id in ASSET_URI_RE.findall(field or ""):
            if asset_id not in ordered:
                ordered.append(asset_id)
    return ordered


def confirm_candidates(conn, actor, task_id, payload):
    _require_teacher(actor)
    task = _task_for_actor(conn, actor, task_id, allow_admin=False)
    request_key = _validate_request_key(payload.get("request_key"))
    selected = payload.get("items")
    if not isinstance(selected, list) or not selected:
        raise IngestionError("empty_publication", "Select at least one question to publish")
    seen = set()
    clean_items = []
    for entry in selected:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("expected_revision"), int) or isinstance(entry.get("expected_revision"), bool) or entry.get("expected_revision") < 1:
            raise IngestionError("invalid_publication", "Every selected question needs an id and review revision")
        if entry["id"] in seen:
            raise IngestionError("invalid_publication", "A question was selected more than once")
        seen.add(entry["id"])
        clean_items.append({"id": entry["id"], "expected_revision": entry["expected_revision"]})
    request_hash = hashlib.sha256(canonical_json({"task_id": task_id, "items": clean_items}).encode()).hexdigest()
    cached = _operation_result(conn, actor, "confirm_candidates", request_key, request_hash)
    if cached is not None:
        return cached
    conn.execute("begin immediate")
    try:
        results = []
        for entry in clean_items:
            item = conn.execute(
                "select * from parsed_question_items where id=? and parse_task_id=? and school_id=? and disposition='active'",
                (entry["id"], task_id, actor["school_id"]),
            ).fetchone()
            if item is None:
                conn.rollback()
                raise ResourceNotFound("A selected question is unavailable")
            publication = conn.execute("select * from import_item_publications where parsed_item_id=?", (item["id"],)).fetchone()
            if publication is not None:
                if publication["published_review_revision"] != entry["expected_revision"]:
                    conn.rollback()
                    raise IngestionError("revision_conflict", "A published question changed; reload the current revision", 409)
                results.append({"item_id": item["id"], "group_id": publication["group_id"], "revision_id": publication["revision_id"], "question_ids": loads(publication["question_ids_json"], []), "already_published": True})
                continue
            if item["review_revision"] != entry["expected_revision"]:
                conn.rollback()
                raise IngestionError("revision_conflict", "A selected question changed; reload before publishing", 409)
            document = loads(item["document_json"], None)
            if not document:
                conn.rollback()
                raise IngestionError("invalid_candidate", "This candidate does not contain an editable question document", 422)
            try:
                document = validate_question_document(document)
            except Exception as exc:
                conn.rollback()
                raise IngestionError("invalid_candidate", str(exc), 422) from exc
            unresolved = [issue for issue in document.get("issues", []) if issue.get("severity") in ("blocking", "review") and issue.get("state") != "resolved"]
            if unresolved:
                conn.rollback()
                raise IngestionError("review_required", "Resolve or explicitly review all open items before publishing", 422, {"issue_count": len(unresolved)})
            # Inserting a new content group and all compatible answer rows happens in the same transaction.
            group_id = "content-" + uuid.uuid4().hex
            revision_id = "revision-" + uuid.uuid4().hex
            created_questions = []
            conn.execute(
                "insert into question_content_groups(id,school_id,original_paper_id,source_item_id,current_revision_id,created_by) values(?,?,?,?,NULL,?)",
                (group_id, actor["school_id"], task["original_paper_id"], item["id"], actor["id"]),
            )
            revision_no = 1
            conn.execute(
                """insert into question_content_revisions(id,group_id,revision_no,schema_version,document_json,content_sha256,review_state,answer_state,created_by,change_reason)
                   values(?,?,?,1,?,?,?, ?,?,?)""",
                (
                    revision_id,
                    group_id,
                    revision_no,
                    json.dumps(document, ensure_ascii=False, sort_keys=True),
                    hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest(),
                    "verified",
                    document["answer_state"],
                    actor["id"],
                    "教师复核整卷导入候选",
                ),
            )
            conn.execute("update question_content_groups set current_revision_id=? where id=?", (revision_id, group_id))
            for asset_id in document["asset_refs"]:
                asset = conn.execute("select id from document_assets where id=? and school_id=?", (asset_id, actor["school_id"])).fetchone()
                if asset is None:
                    conn.rollback()
                    raise IngestionError("asset_unavailable", "A referenced image is no longer available", 422)
                conn.execute("insert or ignore into content_asset_refs(revision_id,asset_id,field_path) values(?,?,?)", (revision_id, asset_id, "document"))
            original_paper = conn.execute("select source_school,source_publisher,exam_type from original_papers where id=? and school_id=?", (task["original_paper_id"], actor["school_id"])).fetchone()
            paper_metadata = dict(original_paper) if original_paper else {"source_school": "", "source_publisher": "", "exam_type": ""}
            for content_row in _question_rows(document, document["stem_md"]):
                question_id = "q-" + uuid.uuid4().hex[:16]
                options_json = {
                    option["key"]: _legacy_question_text(option["markdown"])
                    for option in content_row["options"]
                }
                media_ids = _legacy_visible_asset_ids(content_row)
                question_type = content_row["kind"] if content_row["kind"] in ("single_choice", "multiple_choice", "fill", "short_answer", "structured", "experiment") else "short_answer"
                conn.execute(
                    """insert into questions(
                         id,school_id,stem,options_json,answer_json,analysis,question_type,source,grade,chapter,difficulty,media_json,scenario,quality_status,notes,version,original_paper_id,import_batch_id,parser_task_id,original_page,original_question_number,source_school,source_publisher,exam_type,source_confidence,review_status)
                       values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        question_id,
                        actor["school_id"],
                        _legacy_question_text(content_row["stem"]),
                        json.dumps(options_json, ensure_ascii=False),
                        "{}",
                        _legacy_question_text(content_row["analysis_md"]),
                        question_type,
                        task["file_name"],
                        "待分类",
                        "待分类",
                        "待标注",
                        json.dumps(media_ids),
                        "",
                        "draft",
                        "Document ingestion; answer/grading needs separate verification",
                        1,
                        task["original_paper_id"],
                        task["import_batch_id"],
                        task_id,
                        item["page_number"],
                        document["number"],
                        paper_metadata["source_school"],
                        paper_metadata["source_publisher"],
                        paper_metadata["exam_type"],
                        0.0,
                        "confirmed",
                    ),
                )
                legacy_store = _store_for_connection(conn)
                for asset_id in media_ids:
                    asset = conn.execute(
                        "select storage_key,sha256 from document_assets where id=? and school_id=?",
                        (asset_id, actor["school_id"]),
                    ).fetchone()
                    if asset is None:
                        conn.rollback()
                        raise IngestionError("asset_unavailable", "A visible image is no longer available", 422)
                    try:
                        image_bytes = legacy_store.read(asset["storage_key"], asset["sha256"], max_bytes=25 * 1024 * 1024)
                        png_bytes = _legacy_png(image_bytes)
                    except (DocumentStoreError, IngestionError):
                        conn.rollback()
                        raise
                    legacy_asset_id = "docmedia-" + hashlib.sha256(
                        (question_id + ":" + asset_id).encode("utf-8")
                    ).hexdigest()[:32]
                    conn.execute(
                        "insert into exam_assets(id,school_id,question_id,png) values(?,?,?,?)",
                        (legacy_asset_id, actor["school_id"], question_id, png_bytes),
                    )
                conn.execute("insert into question_content_bindings(question_id,group_id,child_key) values(?,?,?)", (question_id, group_id, content_row["child_key"]))
                created_questions.append(question_id)
            if not document["children"] and not created_questions:
                conn.rollback()
                raise IngestionError("publication_failed", "The question content could not be linked to a result-only question row", 422)
            conn.execute(
                "insert into import_item_publications(parsed_item_id,group_id,revision_id,published_review_revision,question_ids_json,published_by) values(?,?,?,?,?,?)",
                (item["id"], group_id, revision_id, item["review_revision"], json.dumps(created_questions), actor["id"]),
            )
            conn.execute(
                "update parsed_question_items set review_status='saved',saved_question_id=?,updated_by=?,updated_at=? where id=?",
                (created_questions[0] if created_questions else None, actor["id"], _now(), item["id"]),
            )
            _audit(conn, actor, "document_question_published", "question_content_group", group_id, {"candidate_id": item["id"], "revision_id": revision_id, "question_ids": created_questions, "answer_state": document["answer_state"]})
            results.append({"item_id": item["id"], "group_id": group_id, "revision_id": revision_id, "question_ids": created_questions, "already_published": False})
        conn.execute(
            """update question_import_batches set
                 saved_count=(select count(*) from import_item_publications p join parsed_question_items i on i.id=p.parsed_item_id where i.import_batch_id=question_import_batches.id),
                 status=case when (select count(*) from import_item_publications p join parsed_question_items i on i.id=p.parsed_item_id where i.import_batch_id=question_import_batches.id)
                                  >= item_count and item_count>0 then 'saved' else 'partially_saved' end
               where id=?""",
            (task["import_batch_id"],),
        )
        result = {"task_id": task_id, "published": results}
        conn.execute(
            "insert into content_operation_keys(school_id,actor_id,operation,request_key,request_hash,result_json) values(?,?,?,?,?,?)",
            (actor["school_id"], actor["id"], "confirm_candidates", request_key, request_hash, json.dumps(result, ensure_ascii=False)),
        )
        _audit(conn, actor, "document_batch_confirmed", "document_parse_task", task_id, {"candidate_ids": [item["id"] for item in clean_items], "published_count": len(results)})
        conn.commit()
        return result
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise


def task_cancel(conn, actor, task_id):
    row = _task_for_actor(conn, actor, task_id, allow_admin=False)
    if row["status"] in ("parsed", "partially_parsed", "failed", "cancelled"):
        return {"task_id": task_id, "status": row["status"]}
    if row["status"] == "queued":
        conn.execute("update document_parse_tasks set status='cancelled',phase='cancelled',cancel_requested=1,updated_at=? where id=?", (_now(), task_id))
    else:
        conn.execute("update document_parse_tasks set cancel_requested=1,updated_at=? where id=?", (_now(), task_id))
    conn.commit()
    return {"task_id": task_id, "status": "cancelled" if row["status"] == "queued" else "cancelling"}


def retry_task(conn, actor, task_id, request_key):
    _task_for_actor(conn, actor, task_id, allow_admin=False)
    request_key = _validate_request_key(request_key)
    request_hash = hashlib.sha256(canonical_json({"task_id": task_id}).encode("utf-8")).hexdigest()
    conn.execute("begin immediate")
    cached = _operation_result(conn, actor, "retry_task", request_key, request_hash)
    if cached is not None:
        conn.rollback()
        return cached
    fresh = conn.execute("select status,conversion_id from document_parse_tasks where id=?", (task_id,)).fetchone()
    if fresh is None or fresh["status"] not in ("failed", "cancelled") or fresh["conversion_id"]:
        conn.rollback()
        if fresh is not None and fresh["conversion_id"]:
            raise StateConflict("This task already produced a conversion; create a new task to reconvert it")
        raise StateConflict("This task changed before it could be retried")
    conn.execute(
        "update document_parse_tasks set status='queued',phase='queued',generation=generation+1,attempts=0,cancel_requested=0,error_code='',failure_reason='',available_at=?,updated_at=? where id=?",
        (_now(), _now(), task_id),
    )
    result = {"task_id": task_id, "status": "queued"}
    conn.execute(
        "insert into content_operation_keys(school_id,actor_id,operation,request_key,request_hash,result_json) values(?,?,?,?,?,?)",
        (actor["school_id"], actor["id"], "retry_task", request_key, request_hash, json.dumps(result)),
    )
    _audit(conn, actor, "document_task_retried", "document_parse_task", task_id, {})
    conn.commit()
    return result
