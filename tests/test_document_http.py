import base64
import errno
import hashlib
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import textwrap
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tests.http_support import LivePhysicsServer, seed_other_class
from highschoolphysics.document_worker import run_once
from highschoolphysics.document_ingestion import confirm_candidates
from highschoolphysics.document_models import canonical_json
from highschoolphysics.document_store import DocumentStore, DocumentStoreError
from highschoolphysics.question_export import build_question_markdown_zip
from highschoolphysics import document_ingestion
from highschoolphysics.db import connect
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.security import hash_password
from highschoolphysics import learning


class DocumentHTTPTests(unittest.TestCase):
    def setUp(self):
        self._ingestion_flag = patch.dict(os.environ, {"HSP_DOCUMENT_INGESTION_ENABLED": "1"})
        self._ingestion_flag.start()
        self.addCleanup(self._ingestion_flag.stop)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.context = LivePhysicsServer(Path(self.tmpdir.name) / "documents.sqlite3")
        self.server = self.context.__enter__()
        conn = connect(self.server.db_path)
        try:
            learning.migrate(conn)
        finally:
            conn.close()
        _, self.cookie, _ = self.server.login("teacher_li", "teacher123")
        self.origin = "http://%s:%s" % self.server.address

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tmpdir.cleanup()

    def post_document(self, path, payload):
        return self.server.request(
            "POST",
            path,
            json.dumps(payload).encode("utf-8"),
            {
                "Content-Type": "application/json",
                "Cookie": self.cookie,
                "Origin": self.origin,
            },
        )

    def test_teacher_entry_and_documents_page_support_proxy_prefix(self):
        status, _, payload = self.server.request(
            "GET", "/teacher", headers={"Cookie": self.cookie, "X-Forwarded-Prefix": "/physics"}
        )
        self.assertEqual(status, 200)
        page = payload.decode("utf-8")
        self.assertIn('href="/documents"', page)
        self.assertIn('data-base-path="/physics"', page)

        status, _, payload = self.server.request(
            "GET", "/documents", headers={"Cookie": self.cookie, "X-Forwarded-Prefix": "/physics"}
        )
        self.assertEqual(status, 200)
        page = payload.decode("utf-8")
        self.assertIn('src="/assets/document-import.js', page)
        self.assertIn('data-actor-id="', page)
        self.assertIn('href="/assets/document-import.css', page)
        self.assertIn("href='/exams'", page)
        self.assertIn("href='/logout'", page)
        self.assertIn("扫描版 PDF 在服务器上识别", page)
        self.assertIn("服务器模型不可用", page)
        self.assertNotIn("本机模型", page)

        source = b"%PDF-1.7\nprefix logout link fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        _, _, payload = self.post_document("/api/documents/uploads", {
            "name": "prefix-review.pdf", "size": len(source), "sha256": digest,
            "title": "Prefix review", "role": "paper", "request_key": "prefix-review-upload",
        })
        upload = json.loads(payload)["result"]
        self.post_document("/api/documents/uploads/%s/parts" % upload["upload_id"], {
            "index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii"),
        })
        _, _, payload = self.post_document("/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "prefix-review-complete"})
        task_id = json.loads(payload)["result"]["task_id"]
        status, _, payload = self.server.request(
            "GET", "/documents/review?task_id=%s" % task_id, headers={"Cookie": self.cookie, "X-Forwarded-Prefix": "/physics"}
        )
        self.assertEqual(status, 200)
        self.assertIn(b"href='/exams'", payload)
        self.assertIn(b"href='/logout'", payload)

    def test_unauthenticated_documents_redirect_is_compatible_with_proxy_prefix(self):
        status, headers, _ = self.server.request(
            "GET", "/documents", headers={"X-Forwarded-Prefix": "/physics"}
        )
        self.assertEqual(status, 303)
        # Production nginx's proxy_redirect turns this into /physics/login.
        self.assertEqual(headers["Location"], "/login")

    def test_public_asset_bundle_serves_through_prefixed_reverse_proxy(self):
        assets = (
            ("/assets/app.css", "text/css"),
            ("/assets/app.js", "javascript"),
            ("/assets/document-import.css", "text/css"),
            ("/assets/document-import.js", "javascript"),
            ("/assets/question-rendering.css", "text/css"),
            ("/assets/question-rendering.js", "javascript"),
            ("/assets/vendor/katex/katex.min.css", "text/css"),
            ("/assets/vendor/katex/katex.min.js", "javascript"),
        )
        for path, expected_type in assets:
            with self.subTest(path=path):
                status, headers, payload = self.server.request(
                    "GET", path, headers={"X-Forwarded-Prefix": "/physics"}
                )
                self.assertEqual(status, 200)
                self.assertIn(expected_type, headers["Content-Type"])
                self.assertGreater(len(payload), 0)

    def test_sso_login_redirect_keeps_proxy_prefix_in_callback_uri(self):
        conn = connect(self.server.db_path)
        try:
            PhysicsRepository(conn).save_oidc_provider_config(
                actor_id="user-admin",
                provider_name="Test OIDC",
                issuer="https://idp.example.test",
                client_id="physics-test-client",
                client_secret="test-only-secret",
                authorization_endpoint="https://idp.example.test/authorize",
                token_endpoint="https://idp.example.test/token",
                userinfo_endpoint="https://idp.example.test/userinfo",
                enabled=True,
            )
        finally:
            conn.close()

        status, headers, _ = self.server.request(
            "GET",
            "/sso/login",
            headers={
                "X-Forwarded-Prefix": "/physics",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "physics.example.test",
            },
        )
        self.assertEqual(status, 303)
        location = headers["Location"]
        self.assertTrue(location.startswith("https://idp.example.test/authorize?"))
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(location).query)
        self.assertEqual(
            query["redirect_uri"][0],
            "https://physics.example.test/physics/sso/callback",
        )
        self.assertTrue(query["state"][0])

    def test_upload_is_chunked_idempotent_and_source_is_private(self):
        source = b"%PDF-1.7\nminimal parser test fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        status, _, payload = self.post_document(
            "/api/documents/uploads",
            {
                "name": "fixture.pdf",
                "size": len(source),
                "sha256": digest,
                "title": "HTTP fixture",
                "role": "paper",
                "request_key": "http-upload-0001",
            },
        )
        self.assertEqual(status, 202)
        upload = json.loads(payload)["result"]
        part = {
            "index": 0,
            "sha256": digest,
            "data_base64": base64.b64encode(source).decode("ascii"),
        }
        status, _, _ = self.post_document(
            "/api/documents/uploads/%s/parts" % upload["upload_id"], part
        )
        self.assertEqual(status, 200)

        # A browser refresh replays the stable create key and resumes received parts.
        status, _, payload = self.post_document(
            "/api/documents/uploads",
            {
                "name": "fixture.pdf",
                "size": len(source),
                "sha256": digest,
                "title": "HTTP fixture",
                "role": "paper",
                "request_key": "http-upload-0001",
            },
        )
        self.assertEqual(status, 202)
        resumed = json.loads(payload)["result"]
        self.assertEqual(resumed["upload_id"], upload["upload_id"])
        self.assertEqual(resumed["received_parts"], [0])

        # Replaying the same chunk request must not duplicate rows or alter bytes.
        status, _, _ = self.post_document(
            "/api/documents/uploads/%s/parts" % upload["upload_id"], part
        )
        self.assertEqual(status, 200)
        changed_part = bytearray(source)
        changed_part[-1] ^= 1
        changed_part = bytes(changed_part)
        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/parts" % upload["upload_id"],
            {
                "index": 0,
                "sha256": hashlib.sha256(changed_part).hexdigest(),
                "data_base64": base64.b64encode(changed_part).decode("ascii"),
            },
        )
        self.assertEqual(status, 409)
        self.assertIn(json.loads(payload)["error"], {"part_conflict", "storage_conflict"})

        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % upload["upload_id"],
            {"request_key": "http-complete-001"},
        )
        self.assertEqual(status, 202)
        completed = json.loads(payload)["result"]
        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % upload["upload_id"],
            {"request_key": "http-complete-002"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(payload)["result"]["task_id"], completed["task_id"])

        status, headers, payload = self.server.request(
            "GET",
            "/api/documents/tasks/%s/source" % completed["task_id"],
            headers={"Cookie": self.cookie},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, source)
        self.assertIn("private, no-store", headers["Cache-Control"])
        self.assertIn("attachment", headers["Content-Disposition"])

        status, headers, payload = self.server.request(
            "GET",
            "/api/documents/tasks/%s/preview" % completed["task_id"],
            headers={"Cookie": self.cookie},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, source)
        self.assertIn("application/pdf", headers["Content-Type"])
        self.assertIn("inline", headers["Content-Disposition"])

        status, _, payload = self.server.request(
            "GET", "/documents/review?task_id=%s" % completed["task_id"], headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn(b'data-task-status="queued"', payload)

    def test_concurrent_http_upload_parts_are_idempotent_and_conflicts_do_not_overwrite(self):
        def create_upload(name, body, request_key):
            digest = hashlib.sha256(body).hexdigest()
            status, _, response = self.post_document(
                "/api/documents/uploads",
                {
                    "name": name,
                    "size": len(body),
                    "sha256": digest,
                    "title": "并发上传竞态验证",
                    "role": "paper",
                    "request_key": request_key,
                },
            )
            self.assertEqual(status, 202, response)
            return json.loads(response)["result"]["upload_id"]

        def race(upload_id, payloads):
            barrier = threading.Barrier(len(payloads) + 1)

            def post_part(payload):
                barrier.wait(timeout=5)
                return self.post_document(
                    "/api/documents/uploads/%s/parts" % upload_id,
                    payload,
                )

            with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
                futures = [executor.submit(post_part, payload) for payload in payloads]
                barrier.wait(timeout=5)
                return [future.result(timeout=10) for future in futures]

        same_bytes = b"%PDF-1.7\nconcurrent identical part\n"
        same_digest = hashlib.sha256(same_bytes).hexdigest()
        same_upload = create_upload("same-part.pdf", same_bytes, "http-race-same-upload")
        same_payload = {
            "index": 0,
            "sha256": same_digest,
            "data_base64": base64.b64encode(same_bytes).decode("ascii"),
        }
        identical_results = race(same_upload, [same_payload, same_payload])
        self.assertEqual([result[0] for result in identical_results], [200, 200])

        conn = connect(self.server.db_path)
        try:
            rows = conn.execute(
                "select sha256,byte_size,storage_key from document_upload_parts where upload_id=? and part_index=0",
                (same_upload,),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sha256"], same_digest)
            self.assertEqual(rows[0]["byte_size"], len(same_bytes))
            store = document_ingestion._store_for_db(self.server.db_path)
            self.assertEqual(store.read(rows[0]["storage_key"], same_digest), same_bytes)
        finally:
            conn.close()

        conflicting_a = b"%PDF-1.7\nconcurrent choice A\n"
        conflicting_b = b"%PDF-1.7\nconcurrent choice B\n"
        self.assertEqual(len(conflicting_a), len(conflicting_b))
        conflict_upload = create_upload(
            "conflict-part.pdf", conflicting_a, "http-race-conflict-upload"
        )
        conflict_payloads = [
            {
                "index": 0,
                "sha256": hashlib.sha256(value).hexdigest(),
                "data_base64": base64.b64encode(value).decode("ascii"),
            }
            for value in (conflicting_a, conflicting_b)
        ]
        conflict_results = race(conflict_upload, conflict_payloads)
        self.assertEqual(sorted(result[0] for result in conflict_results), [200, 409])
        conflict_error = next(
            json.loads(result[2])["error"] for result in conflict_results if result[0] == 409
        )
        self.assertIn(conflict_error, {"storage_conflict", "part_conflict"})

        conn = connect(self.server.db_path)
        try:
            rows = conn.execute(
                "select sha256,byte_size,storage_key from document_upload_parts where upload_id=? and part_index=0",
                (conflict_upload,),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            winner_hashes = {
                hashlib.sha256(conflicting_a).hexdigest(),
                hashlib.sha256(conflicting_b).hexdigest(),
            }
            self.assertIn(rows[0]["sha256"], winner_hashes)
            self.assertEqual(rows[0]["byte_size"], len(conflicting_a))
            store = document_ingestion._store_for_db(self.server.db_path)
            stored_bytes = store.read(rows[0]["storage_key"], rows[0]["sha256"])
            self.assertIn(stored_bytes, (conflicting_a, conflicting_b))
            self.assertEqual(hashlib.sha256(stored_bytes).hexdigest(), rows[0]["sha256"])
        finally:
            conn.close()

    def test_two_http_tasks_are_single_slot_and_reuse_one_worker_asset_with_complete_refs(self):
        sources = (
            ("shared-image-1.pdf", b"%PDF-1.7\nshared image worker task one\n", "shared-image-task-01"),
            ("shared-image-2.pdf", b"%PDF-1.7\nshared image worker task two\n", "shared-image-task-02"),
        )
        task_ids = []
        for name, source, request_key in sources:
            digest = hashlib.sha256(source).hexdigest()
            status, _, payload = self.post_document("/api/documents/uploads", {
                "name": name,
                "size": len(source),
                "sha256": digest,
                "title": name,
                "role": "paper",
                "request_key": request_key,
            })
            self.assertEqual(status, 202, payload)
            upload = json.loads(payload)["result"]
            status, _, payload = self.post_document(
                "/api/documents/uploads/%s/parts" % upload["upload_id"],
                {"index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii")},
            )
            self.assertEqual(status, 200, payload)
            status, _, payload = self.post_document(
                "/api/documents/uploads/%s/complete" % upload["upload_id"],
                {"request_key": "complete-" + request_key},
            )
            self.assertEqual(status, 202, payload)
            task_ids.append(json.loads(payload)["result"]["task_id"])

        conn = connect(self.server.db_path)
        try:
            task_order = [row["id"] for row in conn.execute(
                "select id from document_parse_tasks where id in (?,?) order by created_at,id",
                tuple(task_ids),
            )]
            source_name_by_task = {
                row["id"]: row["file_name"]
                for row in conn.execute(
                    "select id,file_name from document_parse_tasks where id in (?,?)",
                    tuple(task_ids),
                )
            }
        finally:
            conn.close()
        self.assertEqual(set(task_order), set(task_ids))
        first_task_id = task_order[0]
        first_name = source_name_by_task[first_task_id]

        shared_image = io.BytesIO()
        Image.new("RGB", (12, 10), (20, 90, 160)).save(shared_image, format="PNG")
        converter_assets = []
        first_converter_entered = threading.Event()
        release_first_converter = threading.Event()

        def converter(source_path, original_name, store, school_id, document_id, conversion_id, work_dir, **_kwargs):
            asset = store.store_asset(school_id, shared_image.getvalue(), {"page": 1, "block_id": "shared-photo"})
            converter_assets.append({key: asset[key] for key in ("id", "sha256", "storage_key")})
            if original_name == first_name:
                first_converter_entered.set()
                if not release_first_converter.wait(timeout=10):
                    raise RuntimeError("test did not release the first converter")
            block = {
                "id": "block_shared",
                "type": "paragraph",
                "page": 1,
                "column": 0,
                "order": 0,
                "bbox": [0.05, 0.05, 0.95, 0.95],
                "markdown": "1. 小车的运动如图所示。\n\nA. 加速运动\n\n![小车图](asset:%s)" % asset["id"],
                "asset_ids": [asset["id"]],
                "source_locator": {"kind": "pdf", "page": 1, "block": "block_shared"},
                "issues": [],
            }
            document = {
                "schema_version": 1,
                "document_id": document_id,
                "conversion_id": conversion_id,
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "pages": [{"page": 1, "width": 100, "height": 100, "rotation_applied": 0}],
                "blocks": [block],
                "assets": [{key: asset[key] for key in ("id", "sha256", "mime_type", "byte_size", "width_px", "height_px")}],
                "issues": [],
            }
            return {
                "document": document,
                "markdown": block["markdown"],
                "manifest": {"method": "shared-image-http-worker-test", "issues": [], "formula_count": 0},
                "assets": [asset],
                "adapter_name": "http-test",
                "adapter_version": "1",
            }

        with ThreadPoolExecutor(max_workers=1) as executor:
            first_future = executor.submit(run_once, self.server.db_path, converter=converter)
            try:
                self.assertTrue(first_converter_entered.wait(timeout=10))
                # The production worker intentionally allows one active conversion per school DB.
                self.assertIsNone(run_once(self.server.db_path, converter=converter))
            finally:
                release_first_converter.set()
            first_result = first_future.result(timeout=20)

        self.assertEqual(first_result["task_id"], first_task_id)
        self.assertEqual(first_result["status"], "parsed")
        second_result = run_once(self.server.db_path, converter=converter)
        self.assertIn(second_result["task_id"], set(task_ids) - {first_task_id})
        self.assertEqual(second_result["status"], "parsed")
        self.assertEqual(len(converter_assets), 2)
        self.assertEqual(len({asset["id"] for asset in converter_assets}), 1)
        self.assertEqual(len({asset["storage_key"] for asset in converter_assets}), 1)
        self.assertEqual(len({asset["sha256"] for asset in converter_assets}), 1)

        conn = connect(self.server.db_path)
        try:
            asset_id = converter_assets[0]["id"]
            self.assertEqual(
                conn.execute("select count(*) from document_assets where id=?", (asset_id,)).fetchone()[0],
                1,
            )
            refs = conn.execute(
                "select conversion_id,asset_id,source_locator_json from conversion_asset_refs where asset_id=? order by conversion_id",
                (asset_id,),
            ).fetchall()
            self.assertEqual(len(refs), 2)
            self.assertEqual({row["conversion_id"] for row in refs}, {first_result["conversion_id"], second_result["conversion_id"]})
            self.assertTrue(all(json.loads(row["source_locator_json"]) == {"page": 1, "block_id": "shared-photo"} for row in refs))
            store = document_ingestion._store_for_db(self.server.db_path)
            stored = store.read(converter_assets[0]["storage_key"], converter_assets[0]["sha256"])
            self.assertEqual(hashlib.sha256(stored).hexdigest(), converter_assets[0]["sha256"])
            candidate_rows = conn.execute(
                "select parse_task_id,document_json from parsed_question_items where parse_task_id in (?,?)",
                tuple(task_ids),
            ).fetchall()
            self.assertEqual(len(candidate_rows), 2)
            self.assertEqual(
                {row["parse_task_id"] for row in candidate_rows},
                set(task_ids),
            )
            self.assertTrue(all(asset_id in json.loads(row["document_json"])["asset_refs"] for row in candidate_rows))
            self.assertEqual(
                conn.execute("select count(*) from import_item_publications where parsed_item_id in (select id from parsed_question_items where parse_task_id in (?,?))", tuple(task_ids)).fetchone()[0],
                0,
            )
        finally:
            conn.close()

    def test_upload_part_permission_error_is_explicit_and_retryable(self):
        source = b"%PDF-1.7\npermission retry fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        status, _, payload = self.post_document(
            "/api/documents/uploads",
            {"name": "permission.pdf", "size": len(source), "sha256": digest, "title": "Permission", "role": "paper", "request_key": "permission-part-01"},
        )
        self.assertEqual(status, 202)
        upload = json.loads(payload)["result"]
        part = {"index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii")}
        with patch("highschoolphysics.document_store.DocumentStore.atomic_write", side_effect=PermissionError(errno.EACCES, "read only")):
            status, _, payload = self.post_document("/api/documents/uploads/%s/parts" % upload["upload_id"], part)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(payload)["error"], "storage_unavailable")
        conn = connect(self.server.db_path)
        try:
            self.assertEqual(conn.execute("select count(*) from document_upload_parts where upload_id=?", (upload["upload_id"],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("select state from document_uploads where id=?", (upload["upload_id"],)).fetchone()[0], "uploading")
        finally:
            conn.close()
        status, _, _ = self.post_document("/api/documents/uploads/%s/parts" % upload["upload_id"], part)
        self.assertEqual(status, 200)

    def test_expired_upload_assembly_lease_is_reclaimed_without_duplicate_rows(self):
        source = b"%PDF-1.7\nassembly lease recovery fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        status, _, payload = self.post_document(
            "/api/documents/uploads",
            {"name": "assembly-recovery.pdf", "size": len(source), "sha256": digest, "title": "Assembly recovery", "role": "paper", "request_key": "assembly-recovery-01"},
        )
        self.assertEqual(status, 202)
        upload = json.loads(payload)["result"]
        part = {"index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii")}
        status, _, _ = self.post_document("/api/documents/uploads/%s/parts" % upload["upload_id"], part)
        self.assertEqual(status, 200)

        original_atomic_write = DocumentStore.atomic_write
        assembly_file_written = threading.Event()
        release_first = threading.Event()
        gate_lock = threading.Lock()
        pause_first_assembly = [True]

        def write_then_pause_first_assembly(store, relative_parts, chunks):
            result = original_atomic_write(store, relative_parts, chunks)
            if relative_parts[0] == "staging" and relative_parts[-1].startswith("assembled"):
                with gate_lock:
                    should_pause = pause_first_assembly[0]
                    pause_first_assembly[0] = False
                if should_pause:
                    assembly_file_written.set()
                    if not release_first.wait(timeout=10):
                        raise TimeoutError("test did not release the stale assembly")
            return result

        stale_response = []

        def finish_stale_request():
            stale_response.append(
                self.post_document(
                    "/api/documents/uploads/%s/complete" % upload["upload_id"],
                    {"request_key": "assembly-complete-stale"},
                )
            )

        with patch.object(DocumentStore, "atomic_write", autospec=True, side_effect=write_then_pause_first_assembly):
            worker = threading.Thread(target=finish_stale_request)
            worker.start()
            try:
                self.assertTrue(assembly_file_written.wait(timeout=5))
                conn = connect(self.server.db_path)
                try:
                    conn.execute(
                        "update document_uploads set assembly_lease_until='2000-01-01T00:00:00Z' where id=? and state='assembling'",
                        (upload["upload_id"],),
                    )
                    conn.commit()
                finally:
                    conn.close()

                status, _, payload = self.post_document(
                    "/api/documents/uploads/%s/complete" % upload["upload_id"],
                    {"request_key": "assembly-complete-retry"},
                )
                self.assertEqual(status, 202)
                winning_result = json.loads(payload)["result"]
            finally:
                release_first.set()
                worker.join(timeout=10)
            self.assertFalse(worker.is_alive())

        self.assertEqual(len(stale_response), 1)
        self.assertEqual(stale_response[0][0], 202)
        stale_result = json.loads(stale_response[0][2])["result"]
        self.assertEqual(stale_result["task_id"], winning_result["task_id"])
        self.assertEqual(stale_result["document_file_id"], winning_result["document_file_id"])
        conn = connect(self.server.db_path)
        try:
            completed = conn.execute(
                "select state,assembly_token,document_file_id,task_id from document_uploads where id=?",
                (upload["upload_id"],),
            ).fetchone()
            self.assertEqual(completed["state"], "completed")
            self.assertIsNone(completed["assembly_token"])
            self.assertEqual(conn.execute("select count(*) from document_files where id=?", (completed["document_file_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from document_parse_tasks where id=?", (completed["task_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from question_import_batches where source_file_name='assembly-recovery.pdf'").fetchone()[0], 1)
        finally:
            conn.close()

    def test_upload_complete_disk_full_returns_retryable_507_without_partial_rows(self):
        source = b"%PDF-1.7\ndisk full retry fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        status, _, payload = self.post_document(
            "/api/documents/uploads",
            {"name": "disk-full.pdf", "size": len(source), "sha256": digest, "title": "Disk full", "role": "paper", "request_key": "disk-full-upload-01"},
        )
        self.assertEqual(status, 202)
        upload = json.loads(payload)["result"]
        part = {"index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii")}
        status, _, _ = self.post_document("/api/documents/uploads/%s/parts" % upload["upload_id"], part)
        self.assertEqual(status, 200)

        atomic_write = DocumentStore.atomic_write
        fail_once = [True]

        def fail_assembly_once(store, relative_parts, chunks):
            if fail_once[0]:
                fail_once[0] = False
                raise OSError(errno.ENOSPC, "disk full")
            return atomic_write(store, relative_parts, chunks)

        with patch.object(DocumentStore, "atomic_write", autospec=True, side_effect=fail_assembly_once):
            status, _, payload = self.post_document(
                "/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "disk-full-complete-01"}
            )
        self.assertEqual(status, 507)
        self.assertEqual(json.loads(payload)["error"], "storage_full")
        conn = connect(self.server.db_path)
        try:
            self.assertEqual(conn.execute("select state from document_uploads where id=?", (upload["upload_id"],)).fetchone()[0], "uploading")
            self.assertEqual(conn.execute("select count(*) from document_files where original_name=?", ("disk-full.pdf",)).fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from document_parse_tasks where file_name=?", ("disk-full.pdf",)).fetchone()[0], 0)
        finally:
            conn.close()
        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "disk-full-complete-02"}
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(payload)["result"]["status"], "queued")

    def test_original_persisted_before_database_commit_is_retryable_and_unpublished(self):
        source = b"%PDF-1.7\ncrash-consistency fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        status, _, payload = self.post_document(
            "/api/documents/uploads",
            {"name": "crash-window.pdf", "size": len(source), "sha256": digest, "title": "Crash window", "role": "paper", "request_key": "crash-window-upload"},
        )
        self.assertEqual(status, 202)
        upload = json.loads(payload)["result"]
        part = {"index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii")}
        self.assertEqual(self.post_document("/api/documents/uploads/%s/parts" % upload["upload_id"], part)[0], 200)

        with patch("highschoolphysics.document_ingestion._audit", side_effect=RuntimeError("simulated crash before commit")):
            status, _, _ = self.post_document(
                "/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "crash-window-complete-1"}
            )
        self.assertEqual(status, 500)
        document_id = "doc-" + hashlib.sha256(upload["upload_id"].encode("utf-8")).hexdigest()[:32]
        root = Path(self.server.db_path).parent / "documents"
        original = root / "schools/school-demo/originals" / document_id / "source.pdf"
        self.assertTrue(original.exists())

        conn = connect(self.server.db_path)
        try:
            upload_row = conn.execute("select state from document_uploads where id=?", (upload["upload_id"],)).fetchone()
            self.assertEqual(upload_row["state"], "uploading")
            self.assertEqual(conn.execute("select count(*) from document_files where id=?", (document_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from document_parse_tasks where file_name='crash-window.pdf'").fetchone()[0], 0)
        finally:
            conn.close()

        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "crash-window-complete-2"}
        )
        self.assertEqual(status, 202)
        winner = json.loads(payload)["result"]
        self.assertEqual(winner["document_file_id"], document_id)
        conn = connect(self.server.db_path)
        try:
            self.assertEqual(conn.execute("select count(*) from document_files where id=?", (document_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from document_parse_tasks where id=?", (winner["task_id"],)).fetchone()[0], 1)
        finally:
            conn.close()

    def test_sigkill_after_original_rename_leaves_no_published_reference_and_recovers(self):
        if os.name != "posix":
            self.skipTest("SIGKILL crash-window rehearsal requires POSIX")
        source = b"%PDF-1.7\nSIGKILL crash-window fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        status, _, payload = self.post_document(
            "/api/documents/uploads",
            {"name": "sigkill-window.pdf", "size": len(source), "sha256": digest, "title": "SIGKILL window", "role": "paper", "request_key": "sigkill-window-upload"},
        )
        self.assertEqual(status, 202)
        upload = json.loads(payload)["result"]
        part = {"index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii")}
        self.assertEqual(self.post_document("/api/documents/uploads/%s/parts" % upload["upload_id"], part)[0], 200)

        child = textwrap.dedent("""
            import os, signal, sys
            from highschoolphysics.db import connect
            from highschoolphysics.document_ingestion import complete_upload
            from highschoolphysics.document_store import DocumentStore

            database, upload_id = sys.argv[1:]
            conn = connect(database)
            actor_row = conn.execute("select id,school_id,role from users where username='teacher_li'").fetchone()
            actor = dict(actor_row)
            original = DocumentStore.store_original
            def write_original_then_kill(self, *args, **kwargs):
                original(self, *args, **kwargs)
                os.kill(os.getpid(), signal.SIGKILL)
            DocumentStore.store_original = write_original_then_kill
            complete_upload(conn, actor, upload_id, db_path=database)
        """)
        crashed = subprocess.run(
            [sys.executable, "-c", child, str(self.server.db_path), upload["upload_id"]],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
        self.assertEqual(crashed.returncode, -signal.SIGKILL, crashed.stderr.decode("utf-8", "replace"))
        document_id = "doc-" + hashlib.sha256(upload["upload_id"].encode("utf-8")).hexdigest()[:32]
        root = Path(self.server.db_path).parent / "documents"
        original_path = root / "schools/school-demo/originals" / document_id / "source.pdf"
        self.assertTrue(original_path.is_file())

        conn = connect(self.server.db_path)
        try:
            state = conn.execute(
                "select state,assembly_token from document_uploads where id=?", (upload["upload_id"],)
            ).fetchone()
            self.assertEqual(state["state"], "assembling")
            self.assertIsNotNone(state["assembly_token"])
            self.assertEqual(conn.execute("select count(*) from document_files where id=?", (document_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from document_parse_tasks where file_name='sigkill-window.pdf'").fetchone()[0], 0)
            conn.execute(
                "update document_uploads set assembly_lease_until='2000-01-01T00:00:00Z' where id=?",
                (upload["upload_id"],),
            )
            conn.commit()
        finally:
            conn.close()

        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "sigkill-window-retry"}
        )
        self.assertEqual(status, 202)
        result = json.loads(payload)["result"]
        self.assertEqual(result["document_file_id"], document_id)
        conn = connect(self.server.db_path)
        try:
            self.assertEqual(conn.execute("select count(*) from document_files where id=?", (document_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from document_parse_tasks where id=?", (result["task_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from question_import_batches where source_file_name='sigkill-window.pdf'").fetchone()[0], 1)
        finally:
            conn.close()

    def test_upload_incomplete_hash_mismatch_and_wrong_container_fail_closed(self):
        source = b"%PDF-1.7\nfail-closed upload fixture\n"
        baseline = connect(self.server.db_path)
        try:
            initial_tasks = baseline.execute("select count(*) from document_parse_tasks").fetchone()[0]
            initial_files = baseline.execute("select count(*) from document_files").fetchone()[0]
        finally:
            baseline.close()

        large = b"%PDF-1.7\n" + b"x" * (512 * 1024)
        _, _, payload = self.post_document("/api/documents/uploads", {
            "name": "missing-part.pdf", "size": len(large), "sha256": hashlib.sha256(large).hexdigest(),
            "title": "Missing part", "role": "paper", "request_key": "missing-part-001",
        })
        missing = json.loads(payload)["result"]
        first_part = large[:512 * 1024]
        self.assertEqual(self.post_document("/api/documents/uploads/%s/parts" % missing["upload_id"], {
            "index": 0,
            "sha256": hashlib.sha256(first_part).hexdigest(),
            "data_base64": base64.b64encode(first_part).decode("ascii"),
        })[0], 200)
        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % missing["upload_id"],
            {"request_key": "missing-part-complete-001"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(payload)["error"], "parts_missing")
        self.assertEqual(self.post_document(
            "/api/documents/uploads/%s/cancel" % missing["upload_id"], {},
        )[0], 200)

        wrong_declared_hash = hashlib.sha256(b"y" * len(source)).hexdigest()
        _, _, payload = self.post_document("/api/documents/uploads", {
            "name": "wrong-hash.pdf", "size": len(source), "sha256": wrong_declared_hash,
            "title": "Wrong hash", "role": "paper", "request_key": "wrong-hash-001",
        })
        wrong_hash = json.loads(payload)["result"]
        digest = hashlib.sha256(source).hexdigest()
        self.assertEqual(self.post_document("/api/documents/uploads/%s/parts" % wrong_hash["upload_id"], {
            "index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii"),
        })[0], 200)
        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % wrong_hash["upload_id"],
            {"request_key": "wrong-hash-complete-001"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"], "file_hash_mismatch")
        self.assertEqual(self.post_document(
            "/api/documents/uploads/%s/cancel" % wrong_hash["upload_id"], {},
        )[0], 200)

        _, _, payload = self.post_document("/api/documents/uploads", {
            "name": "mislabelled.docx", "size": len(source), "sha256": digest,
            "title": "Wrong container", "role": "paper", "request_key": "wrong-container-001",
        })
        wrong_container = json.loads(payload)["result"]
        self.assertEqual(self.post_document("/api/documents/uploads/%s/parts" % wrong_container["upload_id"], {
            "index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii"),
        })[0], 200)
        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % wrong_container["upload_id"],
            {"request_key": "wrong-container-complete-001"},
        )
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(payload)["error"], "invalid_container")

        final = connect(self.server.db_path)
        try:
            self.assertEqual(final.execute("select count(*) from document_parse_tasks").fetchone()[0], initial_tasks)
            self.assertEqual(final.execute("select count(*) from document_files").fetchone()[0], initial_files)
        finally:
            final.close()

    def test_upload_size_limit_accepts_exact_50_mib_and_rejects_above(self):
        digest = "a" * 64
        status, _, payload = self.post_document("/api/documents/uploads", {
            "name": "at-limit.pdf", "size": 50 * 1024 * 1024, "sha256": digest,
            "title": "At limit", "role": "paper", "request_key": "upload-limit-exact-001",
        })
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(payload)["result"]["total_parts"], 100)
        status, _, payload = self.post_document("/api/documents/uploads", {
            "name": "over-limit.pdf", "size": 50 * 1024 * 1024 + 1, "sha256": digest,
            "title": "Over limit", "role": "paper", "request_key": "upload-limit-over-001",
        })
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(payload)["error"], "size_limit")

    def test_document_json_requests_require_bounded_valid_utf8_body(self):
        connection = socket.create_connection(self.server.address, timeout=5)
        try:
            raw = (
                b"POST /api/documents/uploads HTTP/1.1\r\n"
                + ("Host: %s:%s\r\n" % self.server.address).encode("ascii")
                + b"Content-Type: application/json\r\n"
                + ("Cookie: %s\r\n" % self.cookie).encode("ascii")
                + ("Origin: %s\r\n" % self.origin).encode("ascii")
                + b"Connection: close\r\n\r\n"
                + b'{"name":"ignored.pdf","size":1,"sha256":"'
                + b"0" * 64
                + b'","title":"ignored","role":"paper","request_key":"missing-length-001"}'
            )
            connection.sendall(raw)
            response = b""
            while True:
                block = connection.recv(4096)
                if not block:
                    break
                response += block
        finally:
            connection.close()
        headers, body = response.split(b"\r\n\r\n", 1)
        self.assertIn(b"400", headers.split(b"\r\n", 1)[0])
        self.assertEqual(json.loads(body)["message"], "Content-Length is required")

        connection = socket.create_connection(self.server.address, timeout=5)
        try:
            oversized_length = 800 * 1024 + 1
            raw = (
                b"POST /api/documents/uploads HTTP/1.1\r\n"
                + ("Host: %s:%s\r\n" % self.server.address).encode("ascii")
                + b"Content-Type: application/json\r\n"
                + ("Content-Length: %s\r\n" % oversized_length).encode("ascii")
                + ("Cookie: %s\r\n" % self.cookie).encode("ascii")
                + ("Origin: %s\r\n" % self.origin).encode("ascii")
                + b"Connection: close\r\n\r\n"
            )
            connection.sendall(raw)
            response = b""
            while True:
                block = connection.recv(4096)
                if not block:
                    break
                response += block
        finally:
            connection.close()
        headers, body = response.split(b"\r\n\r\n", 1)
        self.assertIn(b"413", headers.split(b"\r\n", 1)[0])
        self.assertEqual(json.loads(body)["error"], "request_too_large")

        body = b'{"name":"\xff.pdf"}'
        status, _, payload = self.server.request(
            "POST",
            "/api/documents/uploads",
            body,
            {
                "Content-Type": "application/json",
                "Cookie": self.cookie,
                "Origin": self.origin,
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"], "invalid_request")

    def test_document_mutations_reject_cross_origin_and_students(self):
        request = json.dumps({}).encode("utf-8")
        status, _, payload = self.server.request(
            "POST",
            "/api/documents/uploads",
            request,
            {"Content-Type": "application/json", "Cookie": self.cookie, "Origin": "https://attacker.invalid"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload)["error"], "forbidden")

        status, _, payload = self.server.request(
            "POST",
            "/api/documents/uploads",
            request,
            {
                "Content-Type": "application/json",
                "Cookie": "hsp_session=expired-or-revoked-token",
                "Origin": self.origin,
            },
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload)["error"], "forbidden")

        _, student_cookie, _ = self.server.login("stu_1001", "student123")
        status, _, _ = self.server.request("GET", "/documents", headers={"Cookie": student_cookie})
        self.assertEqual(status, 403)

        conn = connect(self.server.db_path)
        try:
            conn.execute("update users set must_change_password=1 where username='teacher_li'")
            conn.commit()
        finally:
            conn.close()
        status, _, payload = self.server.request(
            "POST",
            "/api/documents/uploads",
            request,
            {"Content-Type": "application/json", "Cookie": self.cookie, "Origin": self.origin},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(payload)["error"], "password_change_required")

    def test_document_task_reads_and_writes_are_limited_to_owner_and_school(self):
        conn = connect(self.server.db_path)
        try:
            conn.execute("insert into schools(id,name,org_scope) values(?,?,?)", ("school-other", "Other school", "single-school"))
            conn.executemany(
                """insert into users(id,school_id,username,display_name,role,class_id,student_no,enrollment_year,status,password_hash,must_change_password)
                   values(?,?,?,?,?,NULL,NULL,NULL,'active',?,0)""",
                [
                    ("user-teacher-peer", "school-demo", "teacher_peer", "同校其他教师", "teacher", hash_password("teacher123")),
                    ("user-teacher-foreign", "school-other", "teacher_foreign", "外校教师", "teacher", hash_password("teacher123")),
                ],
            )
            conn.commit()
        finally:
            conn.close()
        _, peer_cookie, _ = self.server.login("teacher_peer", "teacher123")
        _, foreign_cookie, _ = self.server.login("teacher_foreign", "teacher123")

        source = b"%PDF-1.7\nprivate document ownership fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        status, _, response = self.post_document("/api/documents/uploads", {
            "name": "private-owner.pdf", "size": len(source), "sha256": digest,
            "title": "私有草稿权限验证", "role": "paper", "request_key": "acl-owner-upload-001",
        })
        self.assertEqual(status, 202)
        upload = json.loads(response)["result"]
        status, _, _ = self.post_document("/api/documents/uploads/%s/parts" % upload["upload_id"], {
            "index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii"),
        })
        self.assertEqual(status, 200)
        status, _, response = self.post_document(
            "/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "acl-owner-complete-001"}
        )
        self.assertEqual(status, 202)
        task_id = json.loads(response)["result"]["task_id"]

        def converter(source_path, original_name, store, school_id, document_id, conversion_id, work_dir, **_kwargs):
            image = Image.new("RGB", (12, 10), (20, 90, 150))
            output = io.BytesIO()
            image.save(output, format="PNG")
            asset = store.store_asset(school_id, output.getvalue(), {"page": 1, "block_id": "acl-block"})
            block = {
                "id": "acl-block", "type": "paragraph", "page": 1, "column": 0, "order": 1,
                "bbox": [0.1, 0.1, 0.9, 0.5],
                "markdown": "1. 私有题目正文。\n\n![题目图](asset:%s)" % asset["id"],
                "asset_ids": [asset["id"]], "source_locator": {"kind": "pdf", "page": 1, "block": "acl-block"}, "issues": [],
            }
            document = {
                "schema_version": 1, "document_id": document_id, "conversion_id": conversion_id,
                "source_sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
                "pages": [{"page": 1, "width": 100, "height": 100, "rotation_applied": 0}],
                "blocks": [block],
                "assets": [{key: asset[key] for key in ("id", "sha256", "mime_type", "byte_size", "width_px", "height_px")}],
                "issues": [],
            }
            return {
                "document": document, "markdown": block["markdown"],
                "manifest": {"method": "http-acl-fixture", "issues": [], "formula_count": 0},
                "assets": [asset], "adapter_name": "http-test", "adapter_version": "1",
                "preview_pdf": b"%PDF-1.7\npreview fixture", "preview_converter": "test",
            }

        self.assertEqual(run_once(self.server.db_path, converter=converter)["status"], "parsed")
        status, _, review_html = self.server.request(
            "GET", "/documents/review?task_id=%s" % task_id, headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn(b'name="document-source-preview"', review_html)
        self.assertIn(
            ('href="/api/documents/tasks/%s/preview?jump_page=1#page=1" target="document-source-preview"' % task_id).encode(),
            review_html,
        )
        owner_headers = {"Cookie": self.cookie}
        status, _, owner_items = self.server.request("GET", "/api/documents/tasks/%s/items" % task_id, headers=owner_headers)
        self.assertEqual(status, 200)
        item = json.loads(owner_items)["items"][0]
        asset_id = item["document"]["asset_refs"][0]
        status, _, owner_asset = self.server.request(
            "GET", "/api/documents/assets/%s?task_id=%s" % (asset_id, task_id), headers=owner_headers
        )
        self.assertEqual(status, 200)
        self.assertTrue(owner_asset.startswith(b"\x89PNG\r\n\x1a\n"))

        read_paths = [
            "/api/documents/tasks/%s" % task_id,
            "/api/documents/tasks/%s/items" % task_id,
            "/api/documents/tasks/%s/source" % task_id,
            "/api/documents/tasks/%s/preview" % task_id,
            "/api/documents/tasks/%s/export.zip" % task_id,
            "/api/documents/assets/%s?task_id=%s" % (asset_id, task_id),
            "/documents/review?task_id=%s" % task_id,
        ]
        write_requests = [
            ("/api/documents/tasks/%s/cancel" % task_id, {"request_key": "acl-denied-cancel"}),
            ("/api/documents/tasks/%s/attach-answers" % task_id, {"answer_task_id": "not-owned"}),
            ("/api/documents/items/%s/preview" % item["id"], {"task_id": task_id, "markdown": "## stem"}),
            ("/api/documents/items/%s/save" % item["id"], {
                "task_id": task_id, "request_key": "acl-denied-save", "expected_revision": item["review_revision"],
            }),
        ]
        for label, cookie, expected_status in (("same-school peer", peer_cookie, 403), ("other school", foreign_cookie, 404)):
            with self.subTest(actor=label):
                status, _, task_list = self.server.request("GET", "/api/documents/tasks?limit=50", headers={"Cookie": cookie})
                self.assertEqual(status, 200)
                self.assertNotIn(task_id, [row["id"] for row in json.loads(task_list)["tasks"]])
                for path in read_paths:
                    status, _, _ = self.server.request("GET", path, headers={"Cookie": cookie})
                    self.assertEqual(status, expected_status, path)
                for path, payload in write_requests:
                    status, _, _ = self.server.request(
                        "POST", path, json.dumps(payload).encode("utf-8"),
                        {"Content-Type": "application/json", "Cookie": cookie, "Origin": self.origin},
                    )
                    self.assertEqual(status, expected_status, path)

        conn = connect(self.server.db_path)
        try:
            row = conn.execute("select review_revision,document_json from parsed_question_items where id=?", (item["id"],)).fetchone()
            self.assertEqual(row["review_revision"], item["review_revision"])
            self.assertFalse(json.loads(row["document_json"]).get("answer_md"))
            self.assertEqual(conn.execute("select count(*) from import_item_publications where parsed_item_id=?", (item["id"],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("select status from document_parse_tasks where id=?", (task_id,)).fetchone()[0], "parsed")
        finally:
            conn.close()

    def test_teacher_can_preview_and_attach_answers_without_publishing_or_overwriting(self):
        def create_task(name, role, body, key, original_paper_id=None):
            digest = hashlib.sha256(body).hexdigest()
            payload = {
                "name": name,
                "size": len(body),
                "sha256": digest,
                "title": name,
                "role": role,
                "request_key": key + "-upload",
            }
            if original_paper_id:
                payload["original_paper_id"] = original_paper_id
            status, _, response = self.post_document("/api/documents/uploads", payload)
            self.assertEqual(status, 202)
            upload = json.loads(response)["result"]
            status, _, _ = self.post_document(
                "/api/documents/uploads/%s/parts" % upload["upload_id"],
                {"index": 0, "sha256": digest, "data_base64": base64.b64encode(body).decode("ascii")},
            )
            self.assertEqual(status, 200)
            status, _, response = self.post_document(
                "/api/documents/uploads/%s/complete" % upload["upload_id"],
                {"request_key": key + "-complete"},
            )
            self.assertEqual(status, 202)
            return json.loads(response)["result"]

        paper = create_task("answer-paper.pdf", "paper", b"%PDF-1.7\npaper", "answer-paper")

        def converter(source_path, original_name, store, school_id, document_id, conversion_id, work_dir, **_kwargs):
            if original_name == "answer-sheet.pdf":
                texts = ("参考答案与解析", "1. 答案：B", "解析：第一题的理由。", "2. 答案：A", "解析：第二题的理由。", "3. 答案：C", "解析：第三题的理由。")
            else:
                texts = (
                    "1. 甲物体由静止开始运动。\nA. 选项甲\nB. 选项乙",
                    "1. 另一道重复编号的题。\nA. 选项甲\nB. 选项乙",
                    "2. 乙物体做匀速直线运动。\nA. 选项甲\nB. 选项乙",
                    "3. 丙物体做匀加速直线运动。\nA. 选项甲\nB. 选项乙",
                )
            blocks = [{
                "id": "block_%03d" % index, "type": "paragraph", "page": 1, "column": 0,
                "order": index, "bbox": [0.05, index * 0.1, 0.95, index * 0.1 + 0.08],
                "markdown": text, "asset_ids": [],
                "source_locator": {"kind": "pdf", "page": 1, "block": index}, "issues": [],
            } for index, text in enumerate(texts, 1)]
            document = {
                "schema_version": 1, "document_id": document_id, "conversion_id": conversion_id,
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "pages": [{"page": 1, "width": 100, "height": 100, "rotation_applied": 0}],
                "blocks": blocks, "assets": [], "issues": [],
            }
            return {
                "document": document, "markdown": "\n\n".join(texts),
                "manifest": {"method": "answer-attachment-test", "issues": [], "formula_count": 0},
                "assets": [], "adapter_name": "test-adapter", "adapter_version": "1",
                "preview_pdf": b"%PDF-1.7\npreview fixture", "preview_converter": "test",
            }

        self.assertEqual(run_once(self.server.db_path, converter=converter)["status"], "parsed")
        answers = create_task("answer-sheet.pdf", "answers", b"%PDF-1.7\nanswers", "answer-sheet", paper["original_paper_id"])
        self.assertEqual(run_once(self.server.db_path, converter=converter)["status"], "parsed")

        status, _, page = self.server.request("GET", "/documents/review?task_id=%s" % paper["task_id"], headers={"Cookie": self.cookie})
        self.assertEqual(status, 200)
        self.assertIn(b"data-answer-upload-form", page)
        status, _, task_payload = self.server.request("GET", "/api/documents/tasks?limit=50", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200)
        answer_task_row = next(task for task in json.loads(task_payload)["tasks"] if task["id"] == answers["task_id"])
        self.assertEqual(answer_task_row["document_role"], "answers")
        self.assertEqual(answer_task_row["original_paper_id"], paper["original_paper_id"])
        status, _, response = self.post_document(
            "/api/documents/tasks/%s/attach-answers" % paper["task_id"],
            {"answer_task_id": answers["task_id"]},
        )
        self.assertEqual(status, 200)
        preview = json.loads(response)["result"]
        self.assertTrue(preview["preview"])
        self.assertEqual([match["answer_number"] for match in preview["matches"]], ["2", "3"])
        self.assertIn("答案：A", preview["matches"][0]["answer_markdown"])
        self.assertEqual(preview["issues"], [{"answer_number": "1", "code": "question_match_not_unique", "count": 2}])

        request = {
            "answer_task_id": answers["task_id"],
            "mappings": [{
                "item_id": preview["matches"][0]["item_id"],
                "answer_number": "2",
                "expected_revision": preview["matches"][0]["expected_revision"],
            }],
            "request_key": "attach-answer-q1-0001",
        }
        rollback_request = {
            "answer_task_id": answers["task_id"],
            "mappings": [
                dict(request["mappings"][0]),
                {"item_id": preview["matches"][1]["item_id"], "answer_number": "3", "expected_revision": 99},
            ],
            "request_key": "attach-answer-rollback",
        }
        failed_status, _, failed_body = self.post_document("/api/documents/tasks/%s/attach-answers" % paper["task_id"], rollback_request)
        self.assertEqual(failed_status, 409, failed_body)
        conn = connect(self.server.db_path)
        try:
            unchanged = conn.execute("select review_revision,document_json from parsed_question_items where id=?", (request["mappings"][0]["item_id"],)).fetchone()
            self.assertEqual(unchanged["review_revision"], 1)
            self.assertEqual(json.loads(unchanged["document_json"])["answer_state"], "missing")
        finally:
            conn.close()
        status, _, response = self.post_document("/api/documents/tasks/%s/attach-answers" % paper["task_id"], request)
        self.assertEqual(status, 200)
        result = json.loads(response)["result"]
        self.assertEqual(len(result["attached"]), 1)
        replay_status, _, replay = self.post_document("/api/documents/tasks/%s/attach-answers" % paper["task_id"], request)
        self.assertEqual(replay_status, 200)
        self.assertEqual(json.loads(replay)["result"], result)
        conflict_request = dict(request)
        conflict_request["mappings"] = [{
            "item_id": preview["matches"][1]["item_id"],
            "answer_number": "3",
            "expected_revision": preview["matches"][1]["expected_revision"],
        }]
        conflict_status, _, conflict_body = self.post_document("/api/documents/tasks/%s/attach-answers" % paper["task_id"], conflict_request)
        self.assertEqual(conflict_status, 409, conflict_body)

        conn = connect(self.server.db_path)
        try:
            attached = conn.execute("select document_json,review_revision from parsed_question_items where id=?", (request["mappings"][0]["item_id"],)).fetchone()
            untouched = conn.execute("select document_json,review_revision from parsed_question_items where id=?", (preview["matches"][1]["item_id"],)).fetchone()
            ambiguous = conn.execute("select document_json,review_revision from parsed_question_items where parse_task_id=? and question_number='1' order by item_index", (paper["task_id"],)).fetchall()
            published = conn.execute("select count(*) from import_item_publications where parsed_item_id=?", (request["mappings"][0]["item_id"],)).fetchone()[0]
            document = json.loads(attached["document_json"])
            self.assertEqual(attached["review_revision"], 2)
            self.assertIn("答案：A", document["answer_md"])
            self.assertEqual(document["answer_state"], "needs_review")
            self.assertIn("attached_answer_requires_review", [issue["code"] for issue in document["issues"]])
            self.assertEqual(untouched["review_revision"], 1)
            self.assertEqual(json.loads(untouched["document_json"])["answer_state"], "missing")
            self.assertEqual(len(ambiguous), 2)
            self.assertTrue(all(json.loads(item["document_json"])["answer_state"] == "missing" for item in ambiguous))
            self.assertTrue(all(item["review_revision"] == 1 for item in ambiguous))
            self.assertEqual(published, 0)
        finally:
            conn.close()

    def test_teacher_can_reorder_unpublished_candidates_through_the_document_api(self):
        source = b"%PDF-1.7\nreorder integration fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        status, _, payload = self.post_document(
            "/api/documents/uploads",
            {"name": "reorder-fixture.pdf", "size": len(source), "sha256": digest, "title": "调序验证", "role": "paper", "request_key": "http-reorder-upload"},
        )
        upload = json.loads(payload)["result"]
        status, _, _ = self.post_document(
            "/api/documents/uploads/%s/parts" % upload["upload_id"],
            {"index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii")},
        )
        self.assertEqual(status, 200)
        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "http-reorder-complete"}
        )
        task_id = json.loads(payload)["result"]["task_id"]

        def converter(source_path, original_name, store, school_id, document_id, conversion_id, work_dir, **_kwargs):
            blocks = []
            for index, text in enumerate(("第1题 甲物体由静止开始运动。", "第2题 乙物体做匀速直线运动。"), 1):
                blocks.append({
                    "id": "block_%03d" % index, "type": "paragraph", "page": 1, "column": 0,
                    "order": index, "bbox": [0.05, index * 0.2, 0.95, index * 0.2 + 0.1],
                    "markdown": text, "asset_ids": [],
                    "source_locator": {"kind": "pdf", "page": 1, "block": index}, "issues": [],
                })
            document = {
                "schema_version": 1, "document_id": document_id, "conversion_id": conversion_id,
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "pages": [{"page": 1, "width": 100, "height": 100, "rotation_applied": 0}],
                "blocks": blocks, "assets": [], "issues": [],
            }
            return {
                "document": document, "markdown": "\n\n".join(block["markdown"] for block in blocks),
                "manifest": {"method": "http-reorder-fixture", "issues": [], "formula_count": 0},
                "assets": [], "adapter_name": "http-test", "adapter_version": "1",
                "preview_pdf": b"%PDF-1.7\npreview fixture", "preview_converter": "test",
            }

        self.assertEqual(run_once(self.server.db_path, converter=converter)["status"], "parsed")
        status, _, payload = self.server.request(
            "GET", "/api/documents/tasks/%s/items" % task_id, headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        before = json.loads(payload)["items"]
        status, _, review = self.server.request(
            "GET", "/documents/review?task_id=%s" % task_id, headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn(b'data-question-number-edit value="1"', review)
        self.assertIn(b'data-question-number-edit value="2"', review)
        self.assertIn(b'data-save-source-mapping', review)
        self.assertIn(b'data-source-owner', review)
        self.assertIn(b'data-restructure-split', review)
        self.assertIn(b'data-restructure-merge', review)
        old_order = [item["id"] for item in before]
        new_order = list(reversed(old_order))
        status, _, payload = self.post_document(
            "/api/documents/tasks/%s/reorder" % task_id,
            {
                "request_key": "http-reorder-operation",
                "expected_order": old_order,
                "items": [
                    {"id": item_id, "expected_revision": next(item["review_revision"] for item in before if item["id"] == item_id)}
                    for item_id in new_order
                ],
            },
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(json.loads(payload)["result"]["ordered_ids"], new_order)
        status, _, payload = self.server.request(
            "GET", "/api/documents/tasks/%s/items" % task_id, headers={"Cookie": self.cookie}
        )
        reordered = json.loads(payload)["items"]
        self.assertEqual([item["id"] for item in reordered], new_order)
        current_by_id = {item["id"]: item for item in reordered}
        source_id, target_id = old_order
        source_spans = current_by_id[source_id]["document"]["source_spans"]
        target_spans = current_by_id[target_id]["document"]["source_spans"]
        status, _, payload = self.post_document(
            "/api/documents/tasks/%s/sources" % task_id,
            {
                "request_key": "http-source-map-operation",
                "items": [
                    {"id": source_id, "expected_revision": 1, "source_spans": []},
                    {"id": target_id, "expected_revision": 1, "source_spans": target_spans + source_spans},
                ],
            },
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(json.loads(payload)["result"]["updated"]), 2)
        status, _, payload = self.server.request(
            "GET", "/api/documents/tasks/%s/items" % task_id, headers={"Cookie": self.cookie}
        )
        remapped = {item["id"]: item for item in json.loads(payload)["items"]}
        self.assertEqual(remapped[source_id]["document"]["source_spans"], [])
        self.assertEqual(remapped[target_id]["document"]["source_spans"], target_spans + source_spans)

        target = remapped[target_id]
        stem = target["document"]["stem_md"]
        status, _, payload = self.post_document(
            "/api/documents/tasks/%s/restructure" % task_id,
            {
                "action": "split",
                "ids": [target_id],
                "expected_revisions": {target_id: target["review_revision"]},
                "split_anchor": {"block_id": target["document"]["source_spans"][0]["block_id"], "offset": len(stem) // 2},
                "request_key": "http-split-operation",
            },
        )
        self.assertEqual(status, 200, payload)
        split = json.loads(payload)["result"]
        self.assertEqual(len(split["new_ids"]), 2)
        status, _, payload = self.server.request(
            "GET", "/api/documents/tasks/%s/items" % task_id, headers={"Cookie": self.cookie}
        )
        split_items = {item["id"]: item for item in json.loads(payload)["items"]}
        self.assertEqual(split_items[split["new_ids"][0]]["disposition"], "active")
        status, _, payload = self.post_document(
            "/api/documents/tasks/%s/restructure" % task_id,
            {
                "action": "merge",
                "ids": split["new_ids"],
                "expected_revisions": {item_id: split_items[item_id]["review_revision"] for item_id in split["new_ids"]},
                "request_key": "http-merge-operation",
            },
        )
        self.assertEqual(status, 200, payload)
        merged = json.loads(payload)["result"]
        self.assertEqual(len(merged["new_ids"]), 1)
        status, _, payload = self.server.request(
            "GET", "/api/documents/tasks/%s/items" % task_id, headers={"Cookie": self.cookie}
        )
        final_items = json.loads(payload)["items"]
        self.assertEqual(len(final_items), 2)
        self.assertEqual(final_items[0]["id"], merged["new_ids"][0])

    def test_document_entry_and_mutations_are_disabled_by_default_flag(self):
        with patch.dict(os.environ, {"HSP_DOCUMENT_INGESTION_ENABLED": "0"}):
            status, _, _ = self.server.request("GET", "/documents", headers={"Cookie": self.cookie})
            self.assertEqual(status, 404)
            status, _, page = self.server.request("GET", "/teacher", headers={"Cookie": self.cookie})
            self.assertEqual(status, 200)
            self.assertNotIn(b'href="/documents"', page)
            status, _, _ = self.post_document("/api/documents/uploads", {})
            self.assertEqual(status, 404)

    def test_teacher_can_publish_editable_question_and_download_offline_image_zip(self):
        source = b"%PDF-1.7\nexport test fixture\n"
        digest = hashlib.sha256(source).hexdigest()
        status, _, payload = self.post_document(
            "/api/documents/uploads",
            {"name": "export-fixture.pdf", "size": len(source), "sha256": digest, "title": "导出验证", "role": "paper", "request_key": "export-upload-001"},
        )
        upload = json.loads(payload)["result"]
        status, _, _ = self.post_document(
            "/api/documents/uploads/%s/parts" % upload["upload_id"],
            {"index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode("ascii")},
        )
        self.assertEqual(status, 200)
        status, _, payload = self.post_document(
            "/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "export-complete-001"}
        )
        task_id = json.loads(payload)["result"]["task_id"]

        def converter(source_path, original_name, store, school_id, document_id, conversion_id, work_dir, **_kwargs):
            image = Image.new("RGB", (12, 10), (25, 100, 170))
            output = io.BytesIO()
            image.save(output, format="PNG")
            asset = store.store_asset(school_id, output.getvalue(), {"page": 1, "block_id": "block_001"})
            block = {
                "id": "block_001", "type": "paragraph", "page": 1, "column": 0, "order": 0,
                "bbox": [0.1, 0.1, 0.9, 0.8], "markdown": "第1题 小车通过斜面。\n\nA. ![斜面图](asset:%s)\n\nB. $v^2=2gh$" % asset["id"],
                "asset_ids": [asset["id"]], "source_locator": {"kind": "pdf", "page": 1, "block": "block_001"}, "issues": [],
            }
            document = {
                "schema_version": 1, "document_id": document_id, "conversion_id": conversion_id,
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "pages": [{"page": 1, "width": 100, "height": 100, "rotation_applied": 0}],
                "blocks": [block],
                "assets": [{key: asset[key] for key in ("id", "sha256", "mime_type", "byte_size", "width_px", "height_px")}],
                "issues": [],
            }
            return {
                "document": document,
                "markdown": block["markdown"],
                "manifest": {"method": "http-export-fixture", "issues": [], "formula_count": 0},
                "assets": [asset], "adapter_name": "http-test", "adapter_version": "1",
                "preview_pdf": b"%PDF-1.7\npreview fixture", "preview_converter": "test",
            }

        converted = run_once(self.server.db_path, converter=converter)
        self.assertEqual(converted["status"], "parsed")
        conn = connect(self.server.db_path)
        try:
            refs = conn.execute(
                """select ref.asset_id,ref.source_locator_json,asset.storage_key,asset.sha256
                   from conversion_asset_refs ref join document_assets asset on asset.id=ref.asset_id
                   where ref.conversion_id=?""",
                (converted["conversion_id"],),
            ).fetchall()
            self.assertEqual(len(refs), 1)
            self.assertEqual(json.loads(refs[0]["source_locator_json"]), {"page": 1, "block_id": "block_001"})
            self.assertTrue(refs[0]["storage_key"])
            self.assertEqual(len(refs[0]["sha256"]), 64)
        finally:
            conn.close()
        status, _, payload = self.server.request(
            "GET", "/api/documents/tasks/%s/items" % task_id, headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        item = json.loads(payload)["items"][0]
        self.assertIn("小车通过斜面", item["document"]["stem_md"])
        status, _, review_page = self.server.request(
            "GET", "/documents/review?task_id=%s" % task_id, headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn(b'data-review-draft-export', review_page)

        edited_document = item["document"]
        edited_document["stem_md"] = "小车通过斜面运动，倾斜轨道如图所示。"
        open_issue = {
            "code": "figure_placement_requires_review",
            "severity": "review",
            "state": "open",
            "field": "stem_md",
            "message": "请对照原卷核对图位",
        }
        edited_document["issues"].append(open_issue)
        status, _, payload = self.post_document(
            "/api/documents/items/%s/save" % item["id"],
            {
                "request_key": "draft-export-save-001",
                "expected_revision": item["review_revision"],
                "document": edited_document,
            },
        )
        self.assertEqual(status, 200, payload)
        saved = json.loads(payload)["result"]
        self.assertEqual(saved["review_revision"], item["review_revision"] + 1)

        status, headers, payload = self.server.request(
            "GET", "/api/documents/tasks/%s/review-export.zip" % task_id, headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn("application/zip", headers["Content-Type"])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            self.assertIn("REVIEW-STATUS.txt", names)
            self.assertIn("paper.md", names)
            self.assertIn("questions/001.md", names)
            self.assertIn("images/fig-" + refs[0]["sha256"][:16] + ".png", names)
            paper_markdown = archive.read("paper.md").decode("utf-8")
            question_markdown = archive.read("questions/001.md").decode("utf-8")
            manifest = json.loads(archive.read("source.json"))
            status_note = archive.read("REVIEW-STATUS.txt").decode("utf-8")
            self.assertIn("校对稿：尚未完成教师审核", paper_markdown)
            self.assertIn("小车通过斜面运动，倾斜轨道如图所示。", question_markdown)
            self.assertIn("../images/fig-", question_markdown)
            self.assertIn("尚未完成教师审核", status_note)
            self.assertEqual(manifest["export_state"], "teacher_review_draft")
            self.assertEqual(manifest["review_metadata"]["candidate_count"], 1)
            self.assertEqual(manifest["review_metadata"]["open_issue_count"], 1)
            self.assertEqual(manifest["questions"][0]["review"]["issues"][0]["code"], open_issue["code"])
            exported_image = next(name for name in names if name.startswith("images/fig-"))
            exported_bytes = archive.read(exported_image)
            asset_manifest = next(entry for entry in manifest["assets"] if entry["path"] == exported_image)
            self.assertEqual(hashlib.sha256(exported_bytes).hexdigest(), asset_manifest["sha256"])

        # Mark the synthetic issue resolved before continuing through the existing publication test.
        issue_id = hashlib.sha256(canonical_json(open_issue).encode("utf-8")).hexdigest()[:20]
        resolved_document = saved["document"]
        status, _, payload = self.post_document(
            "/api/documents/items/%s/save" % item["id"],
            {
                "request_key": "draft-export-resolve-001",
                "expected_revision": saved["review_revision"],
                "document": resolved_document,
                "resolved_issue_ids": [issue_id],
                "resolution_note": "已对照原卷第1页核对该图位。",
            },
        )
        self.assertEqual(status, 200, payload)
        item["review_revision"] = json.loads(payload)["result"]["review_revision"]
        status, _, payload = self.server.request(
            "GET", "/api/documents/tasks/%s/review-export.zip" % task_id, headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            resolved_manifest = json.loads(archive.read("source.json"))
            resolved_issue = resolved_manifest["review_metadata"]["items"][0]["issues"][0]
            self.assertEqual(resolved_issue["state"], "resolved")
            self.assertEqual(resolved_issue["reviewed_by"], "user-teacher-li")
            self.assertTrue(resolved_issue["reviewed_at"])
            self.assertEqual(resolved_issue["resolution_note"], "已对照原卷第1页核对该图位。")
            self.assertEqual(resolved_manifest["review_metadata"]["open_issue_count"], 0)
        confirmation = {
            "request_key": "export-confirm-001",
            "items": [{"id": item["id"], "expected_revision": item["review_revision"]}],
        }
        conn = connect(self.server.db_path)
        try:
            actor = dict(conn.execute("select * from users where username='teacher_li'").fetchone())
            before = {
                "questions": conn.execute("select count(*) from questions").fetchone()[0],
                "groups": conn.execute("select count(*) from question_content_groups").fetchone()[0],
                "revisions": conn.execute("select count(*) from question_content_revisions").fetchone()[0],
                "publications": conn.execute("select count(*) from import_item_publications").fetchone()[0],
                "assets": conn.execute("select count(*) from exam_assets").fetchone()[0],
                "operation_keys": conn.execute("select count(*) from content_operation_keys where operation='confirm_candidates'").fetchone()[0],
            }
            with patch("highschoolphysics.document_ingestion._legacy_png", side_effect=RuntimeError("injected legacy asset failure")):
                with self.assertRaisesRegex(RuntimeError, "injected legacy asset failure"):
                    confirm_candidates(conn, actor, task_id, confirmation)
            self.assertFalse(conn.in_transaction)
            after = {
                "questions": conn.execute("select count(*) from questions").fetchone()[0],
                "groups": conn.execute("select count(*) from question_content_groups").fetchone()[0],
                "revisions": conn.execute("select count(*) from question_content_revisions").fetchone()[0],
                "publications": conn.execute("select count(*) from import_item_publications").fetchone()[0],
                "assets": conn.execute("select count(*) from exam_assets").fetchone()[0],
                "operation_keys": conn.execute("select count(*) from content_operation_keys where operation='confirm_candidates'").fetchone()[0],
            }
            self.assertEqual(after, before)
        finally:
            conn.close()

        status, _, payload = self.post_document(
            "/api/documents/tasks/%s/confirm" % task_id,
            confirmation,
        )
        self.assertEqual(status, 200)
        publication = json.loads(payload)["result"]["published"][0]

        conn = connect(self.server.db_path)
        try:
            published_question = conn.execute(
                "select id,stem,options_json from questions where id=?",
                (publication["question_ids"][0],),
            ).fetchone()
            legacy_options = json.loads(published_question["options_json"])
            self.assertIn("图：斜面图", legacy_options["A"])
            self.assertNotIn("![", legacy_options["A"])
            self.assertIn("$v^2=2gh$", legacy_options["B"])
            legacy_image = conn.execute(
                "select png from exam_assets where question_id=?",
                (published_question["id"],),
            ).fetchone()
            self.assertIsNotNone(legacy_image)
            self.assertTrue(bytes(legacy_image["png"]).startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(legacy_image["png"])) as decoded_legacy_image:
                self.assertEqual(decoded_legacy_image.size, (12, 10))

            seed_other_class(conn)
            repository = PhysicsRepository(conn)
            paper = repository.assemble_paper(
                "user-teacher-li", "Imported content", "test", [{"question_id": publication["question_ids"][0], "points": 0}]
            )
            assessment = repository.create_assessment_from_paper(
                "user-teacher-li", paper["paper"]["id"], "class-physics-1", "Imported content", "", "高二", "2026-10-01"
            )
            snapshot = conn.execute(
                "select * from question_version_snapshots where assessment_id=?", (assessment["id"],)
            ).fetchone()
            asset = conn.execute(
                "select asset.id from document_assets asset join content_asset_refs ref on ref.asset_id=asset.id join snapshot_content_bindings bind on bind.revision_id=ref.revision_id where bind.snapshot_id=?",
                (snapshot["id"],),
            ).fetchone()
            conn.commit()
            from highschoolphysics.learning_views import answer_controls
            from highschoolphysics.question_content import render_snapshot_content
            student_row = {**dict(snapshot), "question_type": "single_choice"}
            practice_question = render_snapshot_content(conn, snapshot["id"], "school-demo", include_options=False)
            practice_controls = answer_controls(conn, student_row, {"school_id": "school-demo"})
            self.assertNotIn("data-option-key", practice_question)
            self.assertIn('input type="radio"', practice_controls)
            self.assertIn("question-content-image", practice_controls)
        finally:
            conn.close()
        status, _, payload = self.server.request(
            "GET", "/exams?id=%s" % assessment["id"], headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn("question-content-image", payload.decode("utf-8"))
        self.assertIn('data-option-key="A"', payload.decode("utf-8"))
        asset_path = "/api/question-assets/%s?snapshot_id=%s" % (asset["id"], snapshot["id"])
        _, student_cookie, _ = self.server.login("stu_1001", "student123")
        status, _, _ = self.server.request("GET", asset_path, headers={"Cookie": student_cookie})
        self.assertEqual(status, 403)
        conn = connect(self.server.db_path)
        try:
            conn.execute("update assessment_sessions set grading_status='published',status='published' where id=?", (assessment["id"],))
            conn.commit()
        finally:
            conn.close()
        status, headers, content_image = self.server.request("GET", asset_path, headers={"Cookie": student_cookie})
        self.assertEqual(status, 200)
        self.assertIn("image/png", headers["Content-Type"])
        self.assertTrue(content_image.startswith(b"\x89PNG"))
        status, _, body = self.server.request("GET", asset_path + "&solution=1", headers={"Cookie": student_cookie})
        self.assertEqual(status, 403)
        self.assertNotIn(b"image/png", body)

        _, other_student_cookie, _ = self.server.login("stu_2001", "student123")
        status, _, body = self.server.request("GET", asset_path, headers={"Cookie": other_student_cookie})
        self.assertEqual(status, 403)
        self.assertNotIn(b"image/png", body)

        student_only_teacher_routes = [
            "/api/documents/tasks/%s/source" % task_id,
            "/api/documents/tasks/%s/preview" % task_id,
            "/api/documents/tasks/%s/export.zip" % task_id,
            "/api/documents/tasks/%s/export.zip?include_solution=1" % task_id,
            "/api/documents/tasks/%s/review-export.zip" % task_id,
            "/api/documents/assets/%s?task_id=%s" % (asset["id"], task_id),
            "/api/documents/items/%s/export.zip" % item["id"],
            "/api/documents/items/%s/export.zip?include_solution=1" % item["id"],
            "/documents/review?task_id=%s" % task_id,
        ]
        for path in student_only_teacher_routes:
            status, _, body = self.server.request("GET", path, headers={"Cookie": student_cookie})
            self.assertEqual(status, 403, path)
            self.assertNotIn(b"export test fixture", body)
            self.assertNotIn(b"0.50", body)

        status, headers, payload = self.server.request(
            "GET", "/api/documents/items/%s/export.zip" % item["id"], headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn("application/zip", headers["Content-Type"])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            markdown = archive.read("question.md").decode("utf-8")
            manifest = json.loads(archive.read("source.json"))
            self.assertIn("![斜面图](images/", markdown)
            self.assertNotIn("/api/", markdown)
            asset_path = manifest["assets"][0]["path"]
            self.assertEqual(hashlib.sha256(archive.read(asset_path)).hexdigest(), manifest["assets"][0]["sha256"])

        status, _, payload = self.server.request(
            "GET", "/api/documents/tasks/%s/export.zip" % task_id, headers={"Cookie": self.cookie}
        )
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertIn("questions/001.md", archive.namelist())
            self.assertIn("# 导出验证", archive.read("paper.md").decode("utf-8"))

        with patch(
            "highschoolphysics.document_store.DocumentStore.read",
            side_effect=DocumentStoreError("missing_asset", "The stored file is missing"),
        ):
            status, headers, payload = self.server.request(
                "GET", "/api/documents/items/%s/export.zip" % item["id"], headers={"Cookie": self.cookie}
            )
        self.assertEqual(status, 422)
        self.assertEqual(headers["Content-Type"].split(";", 1)[0], "application/json")
        self.assertEqual(json.loads(payload)["error"], "export_failed")
        self.assertIn("image", json.loads(payload)["message"])

        with patch(
            "highschoolphysics.document_store.DocumentStore.read",
            side_effect=PermissionError("asset permissions denied"),
        ):
            status, headers, payload = self.server.request(
                "GET", "/api/documents/items/%s/export.zip" % item["id"], headers={"Cookie": self.cookie}
            )
        self.assertEqual(status, 422)
        self.assertEqual(headers["Content-Type"].split(";", 1)[0], "application/json")
        self.assertEqual(json.loads(payload)["error"], "export_failed")
        self.assertIn("unreadable", json.loads(payload)["message"])

        def build_tiny_export(*args, **kwargs):
            kwargs["max_bytes"] = 1
            return build_question_markdown_zip(*args, **kwargs)

        with patch("highschoolphysics.server.build_question_markdown_zip", side_effect=build_tiny_export):
            status, headers, payload = self.server.request(
                "GET", "/api/documents/items/%s/export.zip" % item["id"], headers={"Cookie": self.cookie}
            )
        self.assertEqual(status, 422)
        self.assertEqual(headers["Content-Type"].split(";", 1)[0], "application/json")
        self.assertEqual(json.loads(payload)["error"], "export_failed")

        _, student_cookie, _ = self.server.login("stu_1001", "student123")
        status, _, _ = self.server.request(
            "GET", "/api/documents/items/%s/export.zip?include_solution=1" % item["id"], headers={"Cookie": student_cookie}
        )
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
