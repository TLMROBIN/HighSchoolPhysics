"""Single-slot, lease-fenced asynchronous document conversion worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from .db import DEFAULT_DB_PATH, connect, initialize_database
from .document_adapters import AdapterError, convert_document
from .document_ingestion import _store_for_db
from .document_models import canonical_json
from .document_store import DocumentStore, DocumentStoreError
from .providers import ProviderSecretStore
from .question_splitter import split_document_ir


LOGGER = logging.getLogger("highschoolphysics.document_worker")


LEASE_SECONDS = 60
TAG_JOB_LEASE_SECONDS = 180
HEARTBEAT_SECONDS = 10
MAX_ATTEMPTS = 3
TAG_JOB_MAX_ATTEMPTS = 3
MAX_CONVERSION_SECONDS = 20 * 60
SAFE_FAILURES = {
    "unsupported_format": "Only .docx, .doc, and .pdf are supported.",
    "invalid_container": "The uploaded document is damaged or unsafe.",
    "encrypted_document": "Encrypted documents are not supported.",
    "dependency_missing": "A required server-side conversion tool is unavailable.",
    "model_unavailable": "The server-side document recognition model is unavailable.",
    "conversion_timeout": "Document conversion exceeded the time limit.",
    "mineru_api_unavailable": "MinerU cloud parsing could not be reached. Check the provider endpoint and try again.",
    "mineru_api_rejected": "MinerU rejected the cloud parsing request. Check the token and account quota.",
    "mineru_api_upload_failed": "The document could not be uploaded to MinerU.",
    "mineru_api_parse_failed": "MinerU could not parse this document.",
    "provider_not_ready": "MinerU API is not configured or its saved credential cannot be opened.",
    "provider_daily_limit": "The configured daily MinerU file limit has been reached.",
    "invalid_adapter_output": "The conversion tool returned output that did not match the verified schema.",
    "conversion_failed": "The document conversion failed. Retry the task or contact an administrator.",
    "empty_document": "No editable document content was recognized.",
    "unresolved_assets": "One or more referenced images could not be saved.",
    "split_failed": "No question boundaries could be identified.",
    "cancelled": "The conversion was cancelled.",
    "storage_full": "There was not enough space to save conversion output.",
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def recover_expired_tasks(db_path=DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        now = _now()
        conn.execute("begin immediate")
        rows = conn.execute(
            "select id,attempts,generation,lease_token,import_batch_id from document_parse_tasks where status='running' and input_document_id is not null and coalesce(lease_until,'')<?",
            (now,),
        ).fetchall()
        if not rows:
            conn.rollback()
            return 0
        recovered_count = 0
        for row in rows:
            if row["attempts"] >= MAX_ATTEMPTS:
                changed = conn.execute(
                    "update document_parse_tasks set status='failed',phase='failed',error_code='worker_lease_expired',failure_reason=?,lease_token=NULL,lease_until=NULL,updated_at=? where id=? and status='running' and generation=? and lease_token is ? and coalesce(lease_until,'')<?",
                    ("The conversion worker stopped before completing the task.", now, row["id"], row["generation"], row["lease_token"], now),
                )
                if changed.rowcount:
                    conn.execute(
                        "update question_import_batches set status='failed',failure_reason=? where id=?",
                        ("The conversion worker stopped before completing the task.", row["import_batch_id"]),
                    )
            else:
                backoff = 15 * (2 ** max(0, row["attempts"] - 1))
                changed = conn.execute(
                    "update document_parse_tasks set status='queued',phase='queued',generation=generation+1,lease_token=NULL,lease_until=NULL,available_at=?,updated_at=? where id=? and status='running' and generation=? and lease_token is ? and coalesce(lease_until,'')<?",
                    (_later(backoff), now, row["id"], row["generation"], row["lease_token"], now),
                )
            recovered_count += bool(changed.rowcount)
        conn.commit()
        return recovered_count
    finally:
        conn.close()


def claim_next_task(db_path=DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        conn.execute("begin immediate")
        occupied = conn.execute(
            "select 1 from document_parse_tasks where status='running' and input_document_id is not null and coalesce(cancel_requested,0)=0 limit 1"
        ).fetchone()
        if occupied:
            conn.rollback()
            return None
        row = conn.execute(
            """select id,school_id,input_document_id,original_paper_id,import_batch_id,created_by,generation,attempts,parser_mode
               from document_parse_tasks where status='queued' and input_document_id is not null
                 and coalesce(cancel_requested,0)=0 and coalesce(available_at,'')<=?
               order by created_at,id limit 1""",
            (_now(),),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        token = uuid.uuid4().hex
        generation = row["generation"]
        changed = conn.execute(
            """update document_parse_tasks set status='running',phase='inspect',attempts=attempts+1,
                    lease_token=?,lease_until=?,heartbeat_at=?,updated_at=?,failure_reason='',error_code=''
               where id=? and status='queued' and generation=?""",
            (token, _later(LEASE_SECONDS), _now(), _now(), row["id"], generation),
        ).rowcount
        if not changed:
            conn.rollback()
            return None
        conn.commit()
        result = dict(row)
        result.update({"lease_token": token, "attempt": row["attempts"] + 1})
        return result
    finally:
        conn.close()


class _Heartbeat:
    def __init__(self, db_path, task_id, token):
        self.db_path = db_path
        self.task_id = task_id
        self.token = token
        self.cancelled = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="hsp-doc-heartbeat", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=HEARTBEAT_SECONDS + 1)

    def _run(self):
        while not self.stop.wait(HEARTBEAT_SECONDS):
            conn = connect(self.db_path)
            try:
                conn.execute("begin immediate")
                row = conn.execute(
                    "select cancel_requested,status,lease_token from document_parse_tasks where id=?",
                    (self.task_id,),
                ).fetchone()
                if row is None or row["status"] != "running" or row["lease_token"] != self.token:
                    self.cancelled.set()
                    conn.rollback()
                    return
                if row["cancel_requested"]:
                    self.cancelled.set()
                    conn.rollback()
                    return
                conn.execute(
                    "update document_parse_tasks set heartbeat_at=?,lease_until=?,updated_at=? where id=? and lease_token=?",
                    (_now(), _later(LEASE_SECONDS), _now(), self.task_id, self.token),
                )
                conn.commit()
            except Exception:
                conn.rollback()
            finally:
                conn.close()


def _write_failure(db_path, task_id, token, code, message=None):
    conn = connect(db_path)
    try:
        conn.execute("begin immediate")
        row = conn.execute("select lease_token,status from document_parse_tasks where id=?", (task_id,)).fetchone()
        if row is None or row["lease_token"] != token or row["status"] != "running":
            conn.rollback()
            return False
        safe_message = message or SAFE_FAILURES.get(code, "The document could not be converted.")
        conn.execute(
            """update document_parse_tasks set status=?,phase=?,error_code=?,failure_reason=?,lease_token=NULL,lease_until=NULL,updated_at=?
               where id=? and lease_token=?""",
            ("cancelled" if code == "cancelled" else "failed", "cancelled" if code == "cancelled" else "failed", code, safe_message[:400], _now(), task_id, token),
        )
        conn.execute(
            "update question_import_batches set status=?,failure_reason=? where id=(select import_batch_id from document_parse_tasks where id=?)",
            ("cancelled" if code == "cancelled" else "failed", safe_message[:400], task_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _read_task_source(db_path, store, task_id, school_id, document_id, work_dir):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "select * from document_files where id=? and school_id=?",
            (document_id, school_id),
        ).fetchone()
        task = conn.execute("select file_name from document_parse_tasks where id=?", (task_id,)).fetchone()
        if row is None or task is None:
            raise DocumentStoreError("missing_document", "The uploaded source document is unavailable")
        payload = store.read(row["storage_key"], row["sha256"], max_bytes=50 * 1024 * 1024)
        return Path(work_dir) / ("source" + Path(task["file_name"]).suffix.lower()), payload, row["role"], row["original_name"], row["sha256"]
    finally:
        conn.close()


def _mineru_api_config(db_path, school_id, task_id):
    conn = connect(db_path)
    try:
        provider = conn.execute(
            """select * from provider_configs
               where school_id=? and provider_kind='mineru_api' and enabled=1
                 and secret_ciphertext<>'' and api_endpoint<>''
               order by updated_at desc,created_at desc limit 1""",
            (school_id,),
        ).fetchone()
        if provider is None:
            raise AdapterError("provider_not_ready", "MinerU API is not configured")
        limit = int(provider["daily_call_limit"] or 0)
        if limit > 0:
            today_tasks = conn.execute(
                """select count(*) from document_parse_tasks
                   where school_id=? and parser_mode='mineru_api'
                     and date(created_at)=date('now')""",
                (school_id,),
            ).fetchone()[0]
            if int(today_tasks or 0) > limit:
                raise AdapterError("provider_daily_limit", "MinerU daily file limit has been reached")
        try:
            token = ProviderSecretStore.for_connection(conn).decrypt(provider["secret_ciphertext"])
        except Exception as exc:
            raise AdapterError("provider_not_ready", "MinerU API credentials cannot be decrypted") from exc
        if not token:
            raise AdapterError("provider_not_ready", "MinerU API token is not configured")
        return {
            "api_endpoint": provider["api_endpoint"],
            "api_token": token,
            "model_name": provider["model_name"],
            "provider_config_id": provider["id"],
            "provider_name": provider["provider_name"],
            "daily_call_limit": provider["daily_call_limit"],
            "task_id": task_id,
            "created_by": provider["created_by"],
        }
    finally:
        conn.close()


def _document_assets_to_store(conn, school_id, conversion_id, assets):
    for asset in assets:
        conn.execute(
            """insert or ignore into document_assets(id,school_id,sha256,mime_type,byte_size,width_px,height_px,storage_key)
               values(?,?,?,?,?,?,?,?)""",
            (asset["id"], school_id, asset["sha256"], asset["mime_type"], asset["byte_size"], asset["width_px"], asset["height_px"], asset["storage_key"]),
        )
        row = conn.execute("select id,sha256,mime_type from document_assets where id=?", (asset["id"],)).fetchone()
        if row is None or row["sha256"] != asset["sha256"] or row["mime_type"] != asset["mime_type"]:
            raise DocumentStoreError("asset_conflict", "An image asset id conflicts with a different stored image")
        locator = asset.get("source_locator") or {}
        conn.execute(
            """insert or ignore into conversion_asset_refs(conversion_id,asset_id,source_locator_json,asset_role,locator_key)
               values(?,?,?,?,?)""",
            (conversion_id, asset["id"], json.dumps(locator, ensure_ascii=False, sort_keys=True), "figure", hashlib.sha256(canonical_json(locator).encode()).hexdigest()[:24]),
        )


def _insert_candidates(conn, task_id, school_id, conversion_id, adapter_name, adapter_version, batch_id, candidates):
    for candidate in candidates:
        question = candidate["document"]
        options = {item["key"]: item["markdown"] for item in question["options"]}
        spans = question.get("source_spans", [])
        page = next((span.get("source_locator", {}).get("page") for span in spans if span.get("source_locator", {}).get("page")), None)
        item_id = "item-" + uuid.uuid4().hex
        conn.execute(
            """insert into parsed_question_items(
                 id,school_id,parse_task_id,import_batch_id,item_index,page_number,question_number,
                 stem,question_type,options_json,answer_json,analysis,answer_area_json,media_json,
                 coordinates_json,confidence,parser_name,parser_version,review_status,warnings_json,
                 conversion_id,document_json,review_revision,issues_json,disposition,updated_at)
               values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, ?,?,?,1,?,'active',?)""",
            (
                item_id,
                school_id,
                task_id,
                batch_id,
                candidate["item_index"],
                page,
                question["number"],
                question["stem_md"],
                question["kind"],
                json.dumps(options, ensure_ascii=False),
                json.dumps({"markdown": question["answer_md"], "state": question["answer_state"]}, ensure_ascii=False),
                question["analysis_md"],
                "{}",
                json.dumps(question["asset_refs"]),
                json.dumps(spans, ensure_ascii=False),
                0.0,
                adapter_name,
                adapter_version,
                "needs_review",
                json.dumps(candidate["issues"], ensure_ascii=False),
                conversion_id,
                json.dumps(question, ensure_ascii=False, sort_keys=True),
                json.dumps(candidate["issues"], ensure_ascii=False),
                _now(),
            ),
        )


def process_task(task, db_path=DEFAULT_DB_PATH, document_root=None, converter=None):
    task_id = task["id"]
    token = task["lease_token"]
    store = _store_for_db(db_path, document_root)
    heartbeat = _Heartbeat(db_path, task_id, token)
    heartbeat.start()
    work_dir = None
    try:
        work_dir = store.create_work_dir(task_id, token)
        original_path, source_bytes, role, original_name, source_sha = _read_task_source(
            db_path, store, task_id, task["school_id"], task["input_document_id"], work_dir
        )
        original_path.write_bytes(source_bytes)
        os.chmod(original_path, 0o600)
        conversion_id = "conversion-" + uuid.uuid4().hex
        run_converter = converter or convert_document
        converter_options = {
            "timeout_seconds": MAX_CONVERSION_SECONDS,
            "cancel_event": heartbeat.cancelled,
        }
        if task.get("parser_mode") == "mineru_api":
            converter_options["api_config"] = _mineru_api_config(
                db_path, task["school_id"], task_id
            )
        output = run_converter(
            original_path,
            original_name,
            store,
            task["school_id"],
            task["input_document_id"],
            conversion_id,
            work_dir,
            **converter_options,
        )
        if heartbeat.cancelled.is_set():
            raise AdapterError("cancelled", "Document conversion was cancelled")
        document = output["document"]
        split_result = split_document_ir(document) if role == "paper" else {"questions": [], "unassigned_blocks": [], "answer_blocks": [], "issues": []}
        if role == "paper" and not split_result["questions"]:
            raise AdapterError("split_failed", "No main question boundaries were found")
        manifest = dict(output["manifest"])
        manifest["asset_manifest"] = [
            {key: asset[key] for key in ("id", "sha256", "mime_type", "byte_size", "width_px", "height_px", "storage_key", "source_locator") if key in asset}
            for asset in output["assets"]
        ]
        manifest["split"] = {
            "question_count": len(split_result["questions"]),
            "unassigned_block_count": len(split_result["unassigned_blocks"]),
            "answer_block_count": len(split_result["answer_blocks"]),
        }
        manifest["preview_converter"] = output.get("preview_converter")
        layout_bytes = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        markdown_bytes = output["markdown"].encode("utf-8")
        output_hash = hashlib.sha256(markdown_bytes + b"\0" + layout_bytes + b"\0" + manifest_bytes).hexdigest()
        conversion_files = {"document.md": markdown_bytes, "layout.json": layout_bytes, "manifest.json": manifest_bytes}
        if output.get("preview_pdf"):
            conversion_files["preview.pdf"] = output["preview_pdf"]
            manifest["preview_pdf"] = "preview.pdf"
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            conversion_files["manifest.json"] = manifest_bytes
            output_hash = hashlib.sha256(markdown_bytes + b"\0" + layout_bytes + b"\0" + manifest_bytes).hexdigest()
        file_keys = store.write_conversion(
            task["school_id"],
            conversion_id,
            conversion_files,
        )
        if heartbeat.cancelled.is_set():
            raise AdapterError("cancelled", "Document conversion was cancelled")
        conn = connect(db_path)
        try:
            conn.execute("begin immediate")
            current = conn.execute("select status,lease_token,generation,cancel_requested from document_parse_tasks where id=?", (task_id,)).fetchone()
            if current is None or current["status"] != "running" or current["lease_token"] != token or current["generation"] != task["generation"] or current["cancel_requested"]:
                conn.rollback()
                return {"task_id": task_id, "status": "discarded_stale_output"}
            conn.execute(
                """insert into document_conversions(id,school_id,task_id,document_file_id,generation,adapter_name,adapter_version,config_hash,markdown_key,layout_key,manifest_key,output_sha256,warnings_json)
                   values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    conversion_id,
                    task["school_id"],
                    task_id,
                    task["input_document_id"],
                    task["generation"],
                    output["adapter_name"],
                    output["adapter_version"],
                    hashlib.sha256((output["adapter_name"] + "\0" + output["adapter_version"] + "\0" + manifest.get("method", "")).encode()).hexdigest(),
                    file_keys["document.md"],
                    file_keys["layout.json"],
                    file_keys["manifest.json"],
                    output_hash,
                    json.dumps(output["manifest"].get("issues", []), ensure_ascii=False),
                ),
            )
            _document_assets_to_store(conn, task["school_id"], conversion_id, output["assets"])
            _insert_candidates(conn, task_id, task["school_id"], conversion_id, output["adapter_name"], output["adapter_version"], task["import_batch_id"], split_result["questions"])
            final_status = "parsed"
            error_code = ""
            failure_reason = ""
            progress = {
                "pages": len(document.get("pages", [])),
                "blocks": len(document.get("blocks", [])),
                "questions": len(split_result["questions"]),
                "unassigned_blocks": len(split_result["unassigned_blocks"]),
                "images": len(output["assets"]),
                "formula_count": output["manifest"].get("formula_count", 0),
                "needs_review_count": sum(1 for issue in output["manifest"].get("issues", []) if issue.get("severity") in ("blocking", "review")),
            }
            conn.execute(
                """update document_parse_tasks set status=?,phase='complete',progress_json=?,output_json=?,conversion_id=?,lease_token=NULL,lease_until=NULL,heartbeat_at=?,updated_at=?,error_code=?,failure_reason=? where id=? and lease_token=?""",
                (final_status, json.dumps(progress), json.dumps({"conversion_id": conversion_id, "document_sha256": source_sha, "output_sha256": output_hash, "unassigned_blocks": split_result["unassigned_blocks"]}, ensure_ascii=False), conversion_id, _now(), _now(), error_code, failure_reason, task_id, token),
            )
            conn.execute(
                "update question_import_batches set status='needs_review',item_count=? where id=?",
                (len(split_result["questions"]), task["import_batch_id"]),
            )
            conn.execute(
                "insert into audit_events(id,school_id,actor_id,action,resource_type,resource_id,detail_json) values(?,?,?,?,?,?,?)",
                ("audit-" + uuid.uuid4().hex, task["school_id"], task["created_by"], "document_conversion_completed", "document_parse_task", task_id, json.dumps({"conversion_id": conversion_id, "question_count": len(split_result["questions"]), "unassigned_block_count": len(split_result["unassigned_blocks"]), "output_sha256": output_hash})),
            )
            conn.commit()
        finally:
            conn.close()
        return {"task_id": task_id, "status": "parsed", "conversion_id": conversion_id, "question_count": len(split_result["questions"]), "output_sha256": output_hash}
    except AdapterError as exc:
        LOGGER.info("document_task_failed task_id=%s code=%s", task_id, exc.code)
        _write_failure(db_path, task_id, token, exc.code)
        return {"task_id": task_id, "status": "failed" if exc.code != "cancelled" else "cancelled", "error_code": exc.code}
    except DocumentStoreError as exc:
        code = "storage_full" if "space" in str(exc).lower() else exc.code
        LOGGER.info("document_task_failed task_id=%s code=%s", task_id, code)
        _write_failure(db_path, task_id, token, code)
        return {"task_id": task_id, "status": "failed", "error_code": code}
    except Exception as exc:
        LOGGER.error("document_task_failed task_id=%s code=conversion_failed exception=%s", task_id, type(exc).__name__)
        _write_failure(db_path, task_id, token, "conversion_failed")
        return {"task_id": task_id, "status": "failed", "error_code": "conversion_failed"}
    finally:
        heartbeat.close()
        if work_dir is not None:
            try:
                store.remove_work_dir(task_id, token)
            except (OSError, DocumentStoreError):
                pass


def run_once(db_path=DEFAULT_DB_PATH, document_root=None, converter=None):
    recover_expired_tasks(db_path)
    recover_expired_tag_jobs(db_path)
    task = claim_next_task(db_path)
    if task is not None:
        return process_task(task, db_path=db_path, document_root=document_root, converter=converter)
    job = claim_next_tag_job(db_path)
    if job is None:
        return None
    return process_tag_job(job, db_path=db_path)


def recover_expired_tag_jobs(db_path=DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        now = _now()
        conn.execute("begin immediate")
        rows = conn.execute(
            "select id,attempts,lease_token from question_tag_jobs where status='running' and coalesce(lease_until,'')<?",
            (now,),
        ).fetchall()
        recovered = 0
        for row in rows:
            if row["attempts"] >= TAG_JOB_MAX_ATTEMPTS:
                changed = conn.execute(
                    """update question_tag_jobs set status='failed',error_code='worker_lease_expired',
                         lease_token=null,lease_until=null,updated_at=?
                       where id=? and status='running' and lease_token=? and coalesce(lease_until,'')<?""",
                    (now, row["id"], row["lease_token"], now),
                )
            else:
                delay = 15 * (2 ** max(0, row["attempts"] - 1))
                changed = conn.execute(
                    """update question_tag_jobs set status='queued',available_at=?,lease_token=null,
                         lease_until=null,error_code='worker_lease_expired',updated_at=?
                       where id=? and status='running' and lease_token=? and coalesce(lease_until,'')<?""",
                    (_later(delay), now, row["id"], row["lease_token"], now),
                )
            recovered += bool(changed.rowcount)
        conn.commit()
        return recovered
    finally:
        conn.close()


def claim_next_tag_job(db_path=DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        conn.execute("begin immediate")
        row = conn.execute(
            """select id,school_id,question_id,requested_by,question_version,attempts
               from question_tag_jobs where status='queued' and available_at<=?
               order by created_at,id limit 1""",
            (_now(),),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        token = uuid.uuid4().hex
        changed = conn.execute(
            """update question_tag_jobs set status='running',attempts=attempts+1,
                 lease_token=?,lease_until=?,updated_at=? where id=? and status='queued'""",
            (token, _later(TAG_JOB_LEASE_SECONDS), _now(), row["id"]),
        ).rowcount
        if not changed:
            conn.rollback()
            return None
        conn.commit()
        result = dict(row)
        result.update({"lease_token": token, "attempt": row["attempts"] + 1})
        return result
    finally:
        conn.close()


def process_tag_job(job, db_path=DEFAULT_DB_PATH):
    from .llm import LLMProviderError
    from .repository import PhysicsRepository

    conn = connect(db_path)
    try:
        result = PhysicsRepository(conn).auto_tag_question(job["requested_by"], job["question_id"])
        state = "completed"
        error_code = ""
        result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
    except LLMProviderError as exc:
        result = {"question_id": job["question_id"], "status": "failed", "reason": exc.code}
        error_code = exc.code
        if job["attempt"] < TAG_JOB_MAX_ATTEMPTS:
            state = "queued"
            result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
            delay = 15 * (2 ** max(0, job["attempt"] - 1))
            conn.execute(
                """update question_tag_jobs set status=?,available_at=?,lease_token=null,lease_until=null,
                     error_code=?,result_json=?,updated_at=? where id=? and status='running' and lease_token=?""",
                (state, _later(delay), error_code, result_json, _now(), job["id"], job["lease_token"]),
            )
            conn.commit()
            conn.close()
            LOGGER.info("question_tag_job_retrying job_id=%s code=%s attempt=%s", job["id"], error_code, job["attempt"])
            return {"tag_job_id": job["id"], "status": state, "error_code": error_code}
        state = "failed"
        result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
    except Exception as exc:
        error_code = "automatic_tag_failed"
        result = {"question_id": job["question_id"], "status": "failed", "reason": error_code}
        if job["attempt"] < TAG_JOB_MAX_ATTEMPTS:
            delay = 15 * (2 ** max(0, job["attempt"] - 1))
            conn.execute(
                """update question_tag_jobs set status='queued',available_at=?,lease_token=null,lease_until=null,
                     error_code=?,result_json=?,updated_at=? where id=? and status='running' and lease_token=?""",
                (_later(delay), error_code, json.dumps(result, ensure_ascii=False), _now(), job["id"], job["lease_token"]),
            )
            conn.commit()
            conn.close()
            LOGGER.error("question_tag_job_retrying job_id=%s exception=%s attempt=%s", job["id"], type(exc).__name__, job["attempt"])
            return {"tag_job_id": job["id"], "status": "queued", "error_code": error_code}
        state = "failed"
        result_json = json.dumps(result, ensure_ascii=False)
        LOGGER.error("question_tag_job_failed job_id=%s exception=%s", job["id"], type(exc).__name__)
    try:
        candidate_id = (result.get("candidate_id") or None) if isinstance(result, dict) else None
        conn.execute(
            """update question_tag_jobs set status=?,lease_token=null,lease_until=null,
                 candidate_id=?,result_json=?,error_code=?,updated_at=?
               where id=? and status='running' and lease_token=?""",
            (state, candidate_id, result_json, error_code, _now(), job["id"], job["lease_token"]),
        )
        conn.commit()
    finally:
        conn.close()
    return {"tag_job_id": job["id"], "question_id": job["question_id"], "status": state, "result": result, "error_code": error_code}


def run_forever(db_path=DEFAULT_DB_PATH, document_root=None, poll_seconds=5):
    stopping = threading.Event()

    def stop(_signum, _frame):
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    conn = connect(db_path)
    try:
        initialize_database(conn)
    finally:
        conn.close()
    while not stopping.is_set():
        result = run_once(db_path, document_root)
        if result is None:
            stopping.wait(max(1, poll_seconds))


def main():
    parser = argparse.ArgumentParser(description="HighSchoolPhysics document conversion worker")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--document-root", default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    conn = connect(args.db)
    try:
        initialize_database(conn)
    finally:
        conn.close()
    if args.once:
        print(json.dumps(run_once(args.db, args.document_root), ensure_ascii=False))
    else:
        if os.environ.get("HSP_DOCUMENT_WORKER_ENABLED", "").lower() not in ("1", "true", "yes", "on"):
            LOGGER.info("document_worker_disabled")
            return
        run_forever(args.db, args.document_root)


if __name__ == "__main__":
    main()
