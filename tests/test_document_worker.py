import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.document_ingestion import (
    complete_upload,
    create_upload,
    get_task_items,
    save_candidate,
    store_upload_part,
    confirm_candidates,
    task_cancel,
)
from highschoolphysics.document_restructure import assign_source_spans, reorder_candidates, restructure_candidates
from highschoolphysics import document_worker
from highschoolphysics.document_worker import claim_next_task, process_task, recover_expired_tasks, run_once
from highschoolphysics.document_maintenance import collect_orphaned_objects
from highschoolphysics.document_store import DocumentStore
from highschoolphysics.learning import outcome_for_snapshot
from highschoolphysics.question_content import canonical_sha256, render_snapshot_content, snapshot_content
from highschoolphysics.repository import PhysicsRepository


class DocumentWorkerFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / "flow.sqlite3"
        conn = connect(self.db_path)
        initialize_database(conn)
        seed_demo_data(conn)
        self.actor = dict(conn.execute("select * from users where id='user-teacher-li'").fetchone())
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _create_task(self, parser_mode="mineru_local"):
        data = b"%PDF-1.7\nworker test fixture\n"
        digest = hashlib.sha256(data).hexdigest()
        conn = connect(self.db_path)
        try:
            upload = create_upload(
                conn,
                self.actor,
                {
                    "name": "worker-fixture.pdf",
                    "size": len(data),
                    "sha256": digest,
                    "title": "Worker fixture",
                    "role": "paper",
                    "parser_mode": parser_mode,
                    "request_key": "worker-upload-001",
                },
                self.db_path,
                self.root / "documents",
            )
            store_upload_part(
                conn,
                self.actor,
                upload["upload_id"],
                {"index": 0, "sha256": digest, "data_base64": base64.b64encode(data).decode("ascii")},
                self.db_path,
                self.root / "documents",
            )
            complete = complete_upload(conn, self.actor, upload["upload_id"], self.db_path, self.root / "documents")
            return complete["task_id"]
        finally:
            conn.close()

    def test_cloud_mode_passes_decrypted_provider_secret_only_to_converter(self):
        conn = connect(self.db_path)
        try:
            repo = PhysicsRepository(conn)
            admin_id = conn.execute("select id from users where role='admin' limit 1").fetchone()[0]
            repo.save_provider_config(
                actor_id=admin_id,
                provider_kind="mineru_api",
                provider_name="MinerU test",
                model_name="vlm",
                api_endpoint="https://mineru.net",
                secret="test-only-mineru-token",
                enabled=True,
                daily_call_limit=5,
            )
        finally:
            conn.close()
        task_id = self._create_task(parser_mode="mineru_api")
        task = claim_next_task(self.db_path)
        captured = {}

        def converter(*args, **kwargs):
            captured.update(kwargs)
            return self._converter(*args, **kwargs)

        result = process_task(task, db_path=self.db_path, document_root=self.root / "documents", converter=converter)
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(captured["api_config"]["api_token"], "test-only-mineru-token")
        self.assertEqual(captured["api_config"]["model_name"], "vlm")
        self.assertEqual(task["id"], task_id)

    def test_cloud_upload_requires_an_enabled_mineru_provider(self):
        data = b"%PDF-1.7\nworker test fixture\n"
        digest = hashlib.sha256(data).hexdigest()
        conn = connect(self.db_path)
        try:
            with self.assertRaisesRegex(Exception, "MinerU API"):
                create_upload(
                    conn,
                    self.actor,
                    {
                        "name": "cloud-fixture.pdf",
                        "size": len(data),
                        "sha256": digest,
                        "title": "Cloud fixture",
                        "role": "paper",
                        "parser_mode": "mineru_api",
                        "request_key": "cloud-upload-001",
                    },
                    self.db_path,
                    self.root / "documents",
                )
        finally:
            conn.close()

    def _converter(self, source_path, original_name, store, school_id, document_id, conversion_id, work_dir, **_kwargs):
        blocks = [
            {
                "id": "block_001",
                "type": "paragraph",
                "page": 1,
                "column": 0,
                "order": 0,
                "bbox": [0.05, 0.05, 0.95, 0.4],
                "markdown": "第1题 物体由静止开始运动。\nA. 加速\nB. 匀速",
                "asset_ids": [],
                "source_locator": {"kind": "pdf", "page": 1, "block": "block_001"},
                "issues": [],
            },
            {
                "id": "block_002",
                "type": "paragraph",
                "page": 1,
                "column": 0,
                "order": 1,
                "bbox": [0.05, 0.5, 0.95, 0.9],
                "markdown": "第2题 请依据图像判断运动状态。",
                "asset_ids": [],
                "source_locator": {"kind": "pdf", "page": 1, "block": "block_002"},
                "issues": [{"code": "formula_ocr_requires_review", "severity": "blocking", "field": "block_002", "message": "公式需对照原卷核对"}],
            },
        ]
        document = {
            "schema_version": 1,
            "document_id": document_id,
            "conversion_id": conversion_id,
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "pages": [{"page": 1, "width": 100, "height": 100, "rotation_applied": 0}],
            "blocks": blocks,
            "assets": [],
            "issues": [],
        }
        return {
            "document": document,
            "markdown": "第1题 物体由静止开始运动。\nA. 加速\nB. 匀速\n\n第2题 请依据图像判断运动状态。",
            "manifest": {"method": "synthetic-test", "issues": [], "formula_count": 0},
            "assets": [],
            "adapter_name": "test-adapter",
            "adapter_version": "1",
            "preview_pdf": b"%PDF-1.7\npreview fixture",
            "preview_converter": "test",
        }

    def test_two_worker_instances_racing_for_one_task_create_only_one_lease(self):
        task_id = self._create_task()
        barrier = threading.Barrier(3)
        claims = []
        errors = []

        def claim_from_worker_instance():
            try:
                barrier.wait(timeout=5)
                claims.append(claim_next_task(self.db_path))
            except BaseException as exc:
                errors.append(exc)

        workers = [threading.Thread(target=claim_from_worker_instance) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=10)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(claims), 2)
        leased = [claim for claim in claims if claim is not None]
        self.assertEqual(len(leased), 1)
        self.assertEqual(leased[0]["id"], task_id)

        conn = connect(self.db_path)
        try:
            task = conn.execute(
                "select status,attempts,generation,lease_token from document_parse_tasks where id=?",
                (task_id,),
            ).fetchone()
            self.assertEqual((task["status"], task["attempts"], task["generation"]), ("running", 1, 1))
            self.assertEqual(task["lease_token"], leased[0]["lease_token"])
        finally:
            conn.close()

    def _assert_worker_failure_is_explicit_and_not_persisted_as_progress(self, code):
        task_id = self._create_task()

        def fail_conversion(*_args, **_kwargs):
            raise document_worker.AdapterError(code, "private conversion detail must not reach the teacher")

        result = run_once(self.db_path, self.root / "documents", converter=fail_conversion)
        self.assertEqual((result["status"], result["error_code"]), ("failed", code))
        conn = connect(self.db_path)
        try:
            task = conn.execute(
                "select status,phase,error_code,failure_reason,conversion_id from document_parse_tasks where id=?",
                (task_id,),
            ).fetchone()
            batch = conn.execute(
                "select status,failure_reason from question_import_batches where id=(select import_batch_id from document_parse_tasks where id=?)",
                (task_id,),
            ).fetchone()
            self.assertEqual((task["status"], task["phase"], task["error_code"]), ("failed", "failed", code))
            self.assertIsNone(task["conversion_id"])
            self.assertEqual(task["failure_reason"], document_worker.SAFE_FAILURES[code])
            self.assertNotIn("private conversion detail", task["failure_reason"])
            self.assertEqual((batch["status"], batch["failure_reason"]), ("failed", task["failure_reason"]))
            self.assertEqual(conn.execute("select count(*) from document_conversions where task_id=?", (task_id,)).fetchone()[0], 0)
        finally:
            conn.close()

    def test_worker_reports_missing_server_model_without_fake_progress(self):
        self._assert_worker_failure_is_explicit_and_not_persisted_as_progress("model_unavailable")

    def test_worker_reports_missing_server_dependency_without_fake_progress(self):
        self._assert_worker_failure_is_explicit_and_not_persisted_as_progress("dependency_missing")

    def test_worker_reports_conversion_failure_without_fake_progress(self):
        self._assert_worker_failure_is_explicit_and_not_persisted_as_progress("conversion_failed")

    def test_worker_reports_conversion_timeout_without_fake_progress(self):
        self._assert_worker_failure_is_explicit_and_not_persisted_as_progress("conversion_timeout")

    def test_worker_cancel_request_reaches_converter_and_leaves_no_partial_conversion(self):
        task_id = self._create_task()
        claim = claim_next_task(self.db_path)
        started = threading.Event()
        result = []

        def wait_for_cancel(*_args, cancel_event=None, **_kwargs):
            started.set()
            if cancel_event is None or not cancel_event.wait(timeout=5):
                raise AssertionError("cancel request did not reach the active converter")
            raise document_worker.AdapterError("cancelled", "internal cancel detail")

        with patch("highschoolphysics.document_worker.HEARTBEAT_SECONDS", 0.05):
            worker = threading.Thread(
                target=lambda: result.append(process_task(claim, self.db_path, self.root / "documents", converter=wait_for_cancel))
            )
            worker.start()
            self.assertTrue(started.wait(timeout=5))
            conn = connect(self.db_path)
            try:
                response = task_cancel(conn, self.actor, task_id)
                conn.commit()
            finally:
                conn.close()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive())
        self.assertEqual(response["status"], "cancelling")
        self.assertEqual(result, [{"task_id": task_id, "status": "cancelled", "error_code": "cancelled"}])
        conn = connect(self.db_path)
        try:
            task = conn.execute(
                "select status,phase,error_code,failure_reason,lease_token,conversion_id from document_parse_tasks where id=?",
                (task_id,),
            ).fetchone()
            batch = conn.execute(
                "select status from question_import_batches where id=(select import_batch_id from document_parse_tasks where id=?)",
                (task_id,),
            ).fetchone()
            self.assertEqual((task["status"], task["phase"], task["error_code"]), ("cancelled", "cancelled", "cancelled"))
            self.assertEqual(task["failure_reason"], document_worker.SAFE_FAILURES["cancelled"])
            self.assertIsNone(task["lease_token"])
            self.assertIsNone(task["conversion_id"])
            self.assertEqual(batch["status"], "cancelled")
            self.assertEqual(conn.execute("select count(*) from document_conversions where task_id=?", (task_id,)).fetchone()[0], 0)
        finally:
            conn.close()

    def test_worker_converts_editable_candidates_and_batch_publication_is_atomic_and_idempotent(self):
        task_id = self._create_task()
        result = run_once(self.db_path, self.root / "documents", converter=self._converter)
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["question_count"], 2)

        conn = connect(self.db_path)
        try:
            items = get_task_items(conn, self.actor, task_id)
            self.assertEqual([item["document"]["number"] for item in items], ["1", "2"])
            self.assertIn("物体由静止", items[0]["document"]["stem_md"])
            self.assertEqual(items[0]["document"]["options"][0]["markdown"], "加速")
            self.assertEqual(items[1]["issues"][0]["severity"], "blocking")
            item_ids = [item["id"] for item in items]
            before_question_count = conn.execute("select count(*) from questions").fetchone()[0]

            with self.assertRaisesRegex(Exception, "Resolve or explicitly review"):
                confirm_candidates(
                    conn,
                    self.actor,
                    task_id,
                    {"request_key": "batch-fail-0001", "items": [{"id": item_ids[0], "expected_revision": 1}, {"id": item_ids[1], "expected_revision": 1}]},
                )
            self.assertEqual(conn.execute("select count(*) from question_content_groups").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from questions").fetchone()[0], before_question_count)
            self.assertEqual(conn.execute("select count(*) from import_item_publications").fetchone()[0], 0)

            document = items[1]["document"]
            issue_id = hashlib.sha256(json.dumps(items[1]["issues"][0], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
            saved = save_candidate(
                conn,
                self.actor,
                item_ids[1],
                {
                    "request_key": "save-candidate-02",
                    "expected_revision": 1,
                    "document": document,
                    "resolved_issue_ids": [issue_id],
                    "resolution_note": "已对照原卷第1页核对公式与上下文",
                },
            )
            self.assertEqual(saved["review_revision"], 2)
            self.assertFalse([issue for issue in saved["document"]["issues"] if issue.get("severity") in ("blocking", "review") and issue.get("state") != "resolved"], saved["document"]["issues"])
            result = confirm_candidates(
                conn,
                self.actor,
                task_id,
                {"request_key": "batch-ok-000001", "items": [{"id": item_ids[0], "expected_revision": 1}, {"id": item_ids[1], "expected_revision": 2}]},
            )
            self.assertEqual(len(result["published"]), 2)
            replay = confirm_candidates(
                conn,
                self.actor,
                task_id,
                {"request_key": "batch-ok-000001", "items": [{"id": item_ids[0], "expected_revision": 1}, {"id": item_ids[1], "expected_revision": 2}]},
            )
            self.assertEqual(replay, result)
            replay_new_key = confirm_candidates(
                conn,
                self.actor,
                task_id,
                {"request_key": "batch-ok-000002", "items": [{"id": item_ids[0], "expected_revision": 1}, {"id": item_ids[1], "expected_revision": 2}]},
            )
            self.assertTrue(all(item["already_published"] for item in replay_new_key["published"]))
            self.assertEqual(conn.execute("select count(*) from question_content_groups").fetchone()[0], 2)
            self.assertEqual(conn.execute("select count(*) from import_item_publications").fetchone()[0], 2)
            batch = conn.execute("select item_count,saved_count,status from question_import_batches where id=(select import_batch_id from document_parse_tasks where id=?)", (task_id,)).fetchone()
            self.assertEqual((batch["item_count"], batch["saved_count"], batch["status"]), (2, 2, "saved"))
            self.assertEqual(conn.execute("select count(*) from questions").fetchone()[0], before_question_count + 2)

            repository = PhysicsRepository(conn)
            question_id = result["published"][0]["question_ids"][0]
            paper = repository.assemble_paper(
                self.actor["id"], "Snapshot test", "test", [{"question_id": question_id, "points": 0}]
            )
            assessment = repository.create_assessment_from_paper(
                self.actor["id"], paper["paper"]["id"], "class-physics-1", "Snapshot test", "", "高二", "2026-10-01"
            )
            snapshot = conn.execute(
                "select * from question_version_snapshots where assessment_id=?", (assessment["id"],)
            ).fetchone()
            binding = conn.execute(
                "select revision_id,child_key from snapshot_content_bindings where snapshot_id=?", (snapshot["id"],)
            ).fetchone()
            self.assertIsNotNone(binding)
            pinned = snapshot_content(conn, snapshot["id"], self.actor["school_id"])
            old_revision = pinned["revision_id"]
            changed_document = dict(pinned["document"])
            changed_document["stem_md"] = "后续题库版本，不应用于历史测评"
            changed_json = json.dumps(changed_document, ensure_ascii=False, sort_keys=True)
            conn.execute(
                "insert into question_content_revisions(id,group_id,revision_no,schema_version,document_json,content_sha256,review_state,answer_state,created_by,change_reason) values(?,?,2,1,?,?, 'verified',?,?,?)",
                ("revision-test-next", result["published"][0]["group_id"], changed_json, canonical_sha256(changed_document), pinned["answer_state"], self.actor["id"], "snapshot pin test"),
            )
            conn.execute("update question_content_groups set current_revision_id='revision-test-next' where id=?", (result["published"][0]["group_id"],))
            still_pinned = snapshot_content(conn, snapshot["id"], self.actor["school_id"])
            rendered = render_snapshot_content(conn, snapshot["id"], self.actor["school_id"])
            self.assertEqual(still_pinned["revision_id"], old_revision)
            self.assertIn("物体由静止", rendered)
            self.assertNotIn("后续题库版本", rendered)
            self.assertEqual(outcome_for_snapshot(conn, snapshot, self.actor["school_id"], "A"), "pending")
            self.assertEqual(outcome_for_snapshot(conn, snapshot, self.actor["school_id"], ""), "blank")
        finally:
            conn.close()

    def test_expired_worker_lease_recovers_with_new_generation_and_discards_stale_result(self):
        task_id = self._create_task()
        stale_attempt = claim_next_task(self.db_path)
        self.assertEqual(stale_attempt["id"], task_id)
        self.assertEqual(stale_attempt["attempt"], 1)

        conn = connect(self.db_path)
        try:
            conn.execute(
                "update document_parse_tasks set lease_until='2000-01-01T00:00:00Z' where id=?",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(recover_expired_tasks(self.db_path), 1)
        conn = connect(self.db_path)
        try:
            recovered = conn.execute(
                "select status,generation,attempts,lease_token,lease_until,available_at from document_parse_tasks where id=?",
                (task_id,),
            ).fetchone()
            self.assertEqual((recovered["status"], recovered["generation"], recovered["attempts"]), ("queued", 2, 1))
            self.assertIsNone(recovered["lease_token"])
            self.assertIsNone(recovered["lease_until"])
            self.assertGreater(recovered["available_at"], "2000-01-01T00:00:00Z")
            conn.execute("update document_parse_tasks set available_at='2000-01-01T00:00:00Z' where id=?", (task_id,))
            conn.commit()
        finally:
            conn.close()

        current_attempt = claim_next_task(self.db_path)
        self.assertEqual(current_attempt["generation"], 2)
        self.assertEqual(current_attempt["attempt"], 2)
        self.assertNotEqual(current_attempt["lease_token"], stale_attempt["lease_token"])

        current_result = process_task(current_attempt, self.db_path, self.root / "documents", converter=self._converter)
        self.assertEqual(current_result["status"], "parsed")
        stale_result = process_task(stale_attempt, self.db_path, self.root / "documents", converter=self._converter)
        self.assertEqual(stale_result["status"], "discarded_stale_output")

        conn = connect(self.db_path)
        try:
            task = conn.execute(
                "select status,generation,conversion_id from document_parse_tasks where id=?",
                (task_id,),
            ).fetchone()
            self.assertEqual((task["status"], task["generation"], task["conversion_id"]), ("parsed", 2, current_result["conversion_id"]))
            self.assertEqual(conn.execute("select count(*) from document_conversions where task_id=?", (task_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from parsed_question_items where parse_task_id=?", (task_id,)).fetchone()[0], 2)
        finally:
            conn.close()

    def test_expired_worker_lease_after_attempt_limit_becomes_terminal_failure(self):
        task_id = self._create_task()
        claim = claim_next_task(self.db_path)
        conn = connect(self.db_path)
        try:
            conn.execute(
                "update document_parse_tasks set attempts=3,lease_until='2000-01-01T00:00:00Z' where id=?",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(recover_expired_tasks(self.db_path), 1)
        conn = connect(self.db_path)
        try:
            task = conn.execute(
                "select status,phase,error_code,lease_token,lease_until from document_parse_tasks where id=?",
                (task_id,),
            ).fetchone()
            self.assertEqual((task["status"], task["phase"], task["error_code"]), ("failed", "failed", "worker_lease_expired"))
            self.assertIsNone(task["lease_token"])
            self.assertIsNone(task["lease_until"])
            self.assertEqual(conn.execute("select count(*) from document_conversions where task_id=?", (task_id,)).fetchone()[0], 0)
            batch = conn.execute(
                "select status,failure_reason from question_import_batches where id=(select import_batch_id from document_parse_tasks where id=?)",
                (task_id,),
            ).fetchone()
            self.assertEqual(batch["status"], "failed")
            self.assertIn("stopped", batch["failure_reason"])
        finally:
            conn.close()

    def test_worker_lease_recovery_respects_concurrent_heartbeat_renewal(self):
        task_id = self._create_task()
        claim = claim_next_task(self.db_path)
        self.assertEqual(claim["id"], task_id)
        lock_conn = connect(self.db_path)
        lock_conn.execute("begin immediate")
        recovery_connection_opened = threading.Event()
        result = []
        original_connect = document_worker.connect

        def observed_connect(path):
            conn = original_connect(path)
            recovery_connection_opened.set()
            return conn

        with patch("highschoolphysics.document_worker.connect", side_effect=observed_connect):
            recovery_thread = threading.Thread(target=lambda: result.append(recover_expired_tasks(self.db_path)))
            recovery_thread.start()
            try:
                self.assertTrue(recovery_connection_opened.wait(timeout=5))
                recovery_thread.join(timeout=0.1)
                self.assertTrue(recovery_thread.is_alive())
                lock_conn.execute(
                    "update document_parse_tasks set lease_until='2999-01-01T00:00:00Z' where id=?",
                    (task_id,),
                )
                lock_conn.commit()
            finally:
                if lock_conn.in_transaction:
                    lock_conn.rollback()
                lock_conn.close()
                recovery_thread.join(timeout=5)
        self.assertFalse(recovery_thread.is_alive())
        self.assertEqual(result, [0])
        conn = connect(self.db_path)
        try:
            task = conn.execute(
                "select status,lease_token,lease_until,generation from document_parse_tasks where id=?",
                (task_id,),
            ).fetchone()
            self.assertEqual(task["status"], "running")
            self.assertEqual(task["lease_token"], claim["lease_token"])
            self.assertEqual(task["lease_until"], "2999-01-01T00:00:00Z")
            self.assertEqual(task["generation"], claim["generation"])
        finally:
            conn.close()

    def test_sigkill_after_conversion_files_are_saved_recovers_and_collects_stale_output(self):
        if os.name != "posix":
            self.skipTest("SIGKILL worker rehearsal requires POSIX")
        task_id = self._create_task()
        child = textwrap.dedent("""
            import os, signal, sys
            import highschoolphysics.document_worker as worker
            from highschoolphysics.document_store import DocumentStore

            database, storage_root = sys.argv[1:]
            def converter(source_path, original_name, store, school_id, document_id, conversion_id, work_dir, **kwargs):
                block = {
                    "id": "crash_block", "type": "paragraph", "page": 1, "column": 0, "order": 0,
                    "bbox": [0.05, 0.05, 0.95, 0.9], "markdown": "第1题 物体由静止开始运动。\\nA. 加速\\nB. 匀速",
                    "asset_ids": [], "source_locator": {"kind": "pdf", "page": 1, "block": "crash_block"}, "issues": []
                }
                document = {
                    "schema_version": 1, "document_id": document_id, "conversion_id": conversion_id,
                    "source_sha256": __import__("hashlib").sha256(source_path.read_bytes()).hexdigest(),
                    "pages": [{"page": 1, "width": 100, "height": 100, "rotation_applied": 0}],
                    "blocks": [block], "assets": [], "issues": []
                }
                return {
                    "document": document,
                    "markdown": block["markdown"],
                    "manifest": {"method": "sigkill-test", "issues": [], "formula_count": 0},
                    "assets": [], "adapter_name": "sigkill-test", "adapter_version": "1",
                    "preview_pdf": b"%PDF-1.7\\nworker preview"
                }
            original = DocumentStore.write_conversion
            def write_conversion_then_kill(self, *args, **kwargs):
                original(self, *args, **kwargs)
                os.kill(os.getpid(), signal.SIGKILL)
            DocumentStore.write_conversion = write_conversion_then_kill
            worker.convert_document = converter
            worker.run_once(database, storage_root)
        """)
        crashed = subprocess.run(
            [sys.executable, "-c", child, str(self.db_path), str(self.root / "documents")],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(crashed.returncode, -signal.SIGKILL, crashed.stderr.decode("utf-8", "replace"))
        conversion_root = self.root / "documents/schools/school-demo/conversions"
        orphan_dirs = [path for path in conversion_root.iterdir() if path.is_dir()]
        self.assertEqual(len(orphan_dirs), 1)

        conn = connect(self.db_path)
        try:
            task = conn.execute(
                "select status,generation,lease_token,lease_until from document_parse_tasks where id=?", (task_id,)
            ).fetchone()
            self.assertEqual(task["status"], "running")
            self.assertIsNotNone(task["lease_token"])
            self.assertEqual(conn.execute("select count(*) from document_conversions where task_id=?", (task_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from parsed_question_items where parse_task_id=?", (task_id,)).fetchone()[0], 0)
            conn.execute("update document_parse_tasks set lease_until='2000-01-01T00:00:00Z' where id=?", (task_id,))
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(recover_expired_tasks(self.db_path), 1)
        conn = connect(self.db_path)
        try:
            conn.execute("update document_parse_tasks set available_at='2000-01-01T00:00:00Z' where id=?", (task_id,))
            conn.commit()
        finally:
            conn.close()

        store = DocumentStore(self.root / "documents")
        cleanup = collect_orphaned_objects(conn := connect(self.db_path), store, retention_seconds=0, dry_run=False)
        conn.close()
        self.assertIn(orphan_dirs[0].resolve().relative_to(store.root).as_posix(), cleanup["removed"])
        result = run_once(self.db_path, self.root / "documents", converter=self._converter)
        self.assertEqual(result["status"], "parsed")
        conn = connect(self.db_path)
        try:
            task = conn.execute("select status,generation,attempts from document_parse_tasks where id=?", (task_id,)).fetchone()
            self.assertEqual((task["status"], task["generation"], task["attempts"]), ("parsed", 2, 2))
            self.assertEqual(conn.execute("select count(*) from document_conversions where task_id=?", (task_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from parsed_question_items where parse_task_id=?", (task_id,)).fetchone()[0], 2)
        finally:
            conn.close()

    def test_unpublished_candidate_reordering_is_atomic_idempotent_and_preserves_content(self):
        task_id = self._create_task()
        run_once(self.db_path, self.root / "documents", converter=self._converter)
        conn = connect(self.db_path)
        try:
            before = get_task_items(conn, self.actor, task_id)
            old_order = [item["id"] for item in before]
            new_order = list(reversed(old_order))
            payload = {
                "request_key": "reorder-candidates-001",
                "expected_order": old_order,
                "items": [
                    {"id": item_id, "expected_revision": next(item["review_revision"] for item in before if item["id"] == item_id)}
                    for item_id in new_order
                ],
            }
            result = reorder_candidates(conn, self.actor, task_id, payload)
            self.assertEqual(result["ordered_ids"], new_order)
            after = get_task_items(conn, self.actor, task_id)
            self.assertEqual([item["id"] for item in after], new_order)
            before_by_id = {item["id"]: item for item in before}
            for item in after:
                old = before_by_id[item["id"]]
                self.assertEqual(item["document"], old["document"])
                self.assertEqual(item["review_revision"], old["review_revision"])
                self.assertEqual(item["issues"], old["issues"])

            replay = reorder_candidates(conn, self.actor, task_id, payload)
            self.assertEqual(replay, result)
            with self.assertRaisesRegex(Exception, "order changed in another session"):
                reorder_candidates(
                    conn,
                    self.actor,
                    task_id,
                    {
                        **payload,
                        "request_key": "reorder-candidates-002",
                        "expected_order": old_order,
                    },
                )
        finally:
            conn.close()

    def test_published_candidates_cannot_be_reordered(self):
        task_id = self._create_task()
        run_once(self.db_path, self.root / "documents", converter=self._converter)
        conn = connect(self.db_path)
        try:
            items = get_task_items(conn, self.actor, task_id)
            confirm_candidates(
                conn,
                self.actor,
                task_id,
                {"request_key": "publish-before-order", "items": [{"id": items[0]["id"], "expected_revision": 1}]},
            )
            with self.assertRaisesRegex(Exception, "Published candidates cannot be reordered"):
                reorder_candidates(
                    conn,
                    self.actor,
                    task_id,
                    {
                        "request_key": "reorder-after-publish",
                        "expected_order": [item["id"] for item in items],
                        "items": [
                            {"id": item["id"], "expected_revision": item["review_revision"]}
                            for item in reversed(items)
                        ],
                    },
                )
            self.assertEqual([item["id"] for item in get_task_items(conn, self.actor, task_id)], [item["id"] for item in items])
        finally:
            conn.close()

    def test_source_span_reassignment_is_atomic_idempotent_and_conserves_the_conversion_map(self):
        task_id = self._create_task()
        run_once(self.db_path, self.root / "documents", converter=self._converter)
        conn = connect(self.db_path)
        try:
            before = get_task_items(conn, self.actor, task_id)
            first, second = before
            first_spans = first["document"]["source_spans"]
            second_spans = second["document"]["source_spans"]
            payload = {
                "request_key": "source-map-operation-001",
                "items": [
                    {"id": first["id"], "expected_revision": first["review_revision"], "source_spans": []},
                    {"id": second["id"], "expected_revision": second["review_revision"], "source_spans": second_spans + first_spans},
                ],
            }
            result = assign_source_spans(conn, self.actor, task_id, payload)
            after = {item["id"]: item for item in get_task_items(conn, self.actor, task_id)}
            self.assertEqual(after[first["id"]]["document"]["source_spans"], [])
            self.assertEqual(after[second["id"]]["document"]["source_spans"], second_spans + first_spans)
            self.assertEqual(after[first["id"]]["document"]["stem_md"], first["document"]["stem_md"])
            self.assertEqual(after[first["id"]]["review_revision"], 2)
            self.assertEqual(after[second["id"]]["review_revision"], 2)
            self.assertEqual(assign_source_spans(conn, self.actor, task_id, payload), result)

            invalid = {
                "request_key": "source-map-drop-block-001",
                "items": [
                    {"id": first["id"], "expected_revision": 2, "source_spans": []},
                    {"id": second["id"], "expected_revision": 2, "source_spans": []},
                ],
            }
            with self.assertRaisesRegex(Exception, "Every source block must remain assigned"):
                assign_source_spans(conn, self.actor, task_id, invalid)
            unchanged = {item["id"]: item for item in get_task_items(conn, self.actor, task_id)}
            self.assertEqual(unchanged[first["id"]]["review_revision"], 2)
            self.assertEqual(unchanged[second["id"]]["review_revision"], 2)
            forged = {
                "request_key": "source-map-forge-block-001",
                "items": [{
                    "id": first["id"],
                    "expected_revision": 2,
                    "source_spans": [{"block_id": "foreign_block", "source_locator": {"page": 99}}],
                }],
            }
            with self.assertRaisesRegex(Exception, "not part of this conversion"):
                assign_source_spans(conn, self.actor, task_id, forged)
        finally:
            conn.close()

    def test_split_and_merge_create_review_blocked_replacements_with_reversible_mapping(self):
        task_id = self._create_task()
        run_once(self.db_path, self.root / "documents", converter=self._converter)
        conn = connect(self.db_path)
        try:
            before = get_task_items(conn, self.actor, task_id)
            source = before[0]
            stem = source["document"]["stem_md"]
            split_at = len(stem) // 2
            split_payload = {
                "action": "split",
                "ids": [source["id"]],
                "expected_revisions": {source["id"]: source["review_revision"]},
                "split_anchor": {
                    "block_id": source["document"]["source_spans"][0]["block_id"],
                    "offset": split_at,
                },
                "request_key": "split-candidate-0001",
            }
            split = restructure_candidates(conn, self.actor, task_id, split_payload)
            self.assertEqual(len(split["new_ids"]), 2)
            self.assertEqual(restructure_candidates(conn, self.actor, task_id, split_payload), split)
            rows = get_task_items(conn, self.actor, task_id)
            self.assertEqual([item["id"] for item in rows], split["new_ids"] + [before[1]["id"]])
            parts = rows[:2]
            self.assertEqual("".join(item["document"]["stem_md"] for item in parts), stem)
            self.assertEqual(parts[0]["document"]["source_spans"], source["document"]["source_spans"])
            self.assertEqual(parts[1]["document"]["source_spans"], source["document"]["source_spans"])
            self.assertTrue(all(any(issue["code"] == "candidate_split_requires_review" and issue["severity"] == "blocking" for issue in item["document"]["issues"]) for item in parts))
            old = conn.execute("select disposition,review_revision from parsed_question_items where id=?", (source["id"],)).fetchone()
            self.assertEqual((old["disposition"], old["review_revision"]), ("superseded", 2))
            self.assertEqual(conn.execute("select item_count from question_import_batches where id=?", (conn.execute("select import_batch_id from parsed_question_items where id=?", (source["id"],)).fetchone()[0],)).fetchone()[0], 3)

            with self.assertRaisesRegex(Exception, "Resolve or explicitly review"):
                confirm_candidates(conn, self.actor, task_id, {"request_key": "publish-split-open-001", "items": [{"id": parts[0]["id"], "expected_revision": 1}]})

            merge_payload = {
                "action": "merge",
                "ids": split["new_ids"],
                "expected_revisions": {item["id"]: item["review_revision"] for item in parts},
                "request_key": "merge-candidate-0001",
            }
            merged_result = restructure_candidates(conn, self.actor, task_id, merge_payload)
            merged_items = get_task_items(conn, self.actor, task_id)
            self.assertEqual([item["id"] for item in merged_items], [merged_result["new_ids"][0], before[1]["id"]])
            merged = merged_items[0]["document"]
            self.assertEqual(merged["stem_md"], stem[:split_at] + "\n\n" + stem[split_at:])
            self.assertEqual([option["key"] for option in merged["options"]], ["A", "B", "C", "D"])
            self.assertEqual(len(merged["source_spans"]), len(source["document"]["source_spans"]))
            self.assertTrue(any(issue["code"] == "candidate_merge_requires_review" and issue["severity"] == "blocking" for issue in merged["issues"]))
            audit_rows = conn.execute("select detail_json from audit_events where action='document_candidates_restructured'").fetchall()
            audit_detail = next(json.loads(row["detail_json"]) for row in audit_rows if json.loads(row["detail_json"]).get("request_key") == "merge-candidate-0001")
            audit_map = audit_detail["mapping"]
            self.assertEqual(audit_map[parts[0]["id"]], [merged_result["new_ids"][0]])

            stale_payload = {**merge_payload, "request_key": "merge-candidate-stale", "expected_revisions": {item["id"]: 1 for item in parts}}
            with self.assertRaisesRegex(Exception, "no longer active"):
                restructure_candidates(conn, self.actor, task_id, stale_payload)
            self.assertEqual([item["id"] for item in get_task_items(conn, self.actor, task_id)], [merged_result["new_ids"][0], before[1]["id"]])
        finally:
            conn.close()

    def test_candidates_cannot_be_restructured_after_any_candidate_in_the_task_is_published(self):
        task_id = self._create_task()
        run_once(self.db_path, self.root / "documents", converter=self._converter)
        conn = connect(self.db_path)
        try:
            items = get_task_items(conn, self.actor, task_id)
            confirm_candidates(conn, self.actor, task_id, {"request_key": "publish-before-restructure", "items": [{"id": items[0]["id"], "expected_revision": 1}]})
            rows_before = conn.execute("select count(*) from parsed_question_items where parse_task_id=?", (task_id,)).fetchone()[0]
            second = items[1]
            with self.assertRaisesRegex(Exception, "task with published candidates"):
                restructure_candidates(conn, self.actor, task_id, {
                    "action": "split",
                    "ids": [second["id"]],
                    "expected_revisions": {second["id"]: second["review_revision"]},
                    "split_anchor": {"block_id": second["document"]["source_spans"][0]["block_id"], "offset": 3},
                    "request_key": "split-after-publication",
                })
            self.assertEqual(conn.execute("select count(*) from parsed_question_items where parse_task_id=?", (task_id,)).fetchone()[0], rows_before)
            self.assertEqual([item["id"] for item in get_task_items(conn, self.actor, task_id)], [item["id"] for item in items])
        finally:
            conn.close()

    def test_teacher_can_correct_a_draft_candidate_question_number(self):
        task_id = self._create_task()
        run_once(self.db_path, self.root / "documents", converter=self._converter)
        conn = connect(self.db_path)
        try:
            item = get_task_items(conn, self.actor, task_id)[0]
            document = dict(item["document"])
            document["number"] = "11"
            result = save_candidate(
                conn,
                self.actor,
                item["id"],
                {"request_key": "candidate-number-correct", "expected_revision": 1, "document": document},
            )
            self.assertEqual(result["document"]["number"], "11")
            saved = get_task_items(conn, self.actor, task_id)[0]
            self.assertEqual(saved["question_number"], "11")
            self.assertEqual(saved["review_revision"], 2)
            self.assertEqual(saved["document"]["stem_md"], item["document"]["stem_md"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
