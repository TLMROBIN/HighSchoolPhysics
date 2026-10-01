"""HTTP regressions for draft structure edits and review gates."""

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.http_support import LivePhysicsServer
from highschoolphysics.db import connect
from highschoolphysics import learning
from highschoolphysics.document_worker import run_once
from highschoolphysics.document_models import canonical_json
from highschoolphysics.question_content import serialize_question_md


class DocumentReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.flag = patch.dict("os.environ", {"HSP_DOCUMENT_INGESTION_ENABLED": "1"})
        self.flag.start()
        self.addCleanup(self.flag.stop)
        self.directory = tempfile.TemporaryDirectory(prefix="hsp-review-fixes-")
        self.addCleanup(self.directory.cleanup)
        self.context = LivePhysicsServer(Path(self.directory.name) / "school.sqlite3")
        self.server = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        conn = connect(self.server.db_path)
        learning.migrate(conn)
        conn.close()
        _, self.cookie, _ = self.server.login("teacher_li", "teacher123")
        source = b"%PDF-1.7\nreview fixture"
        digest = hashlib.sha256(source).hexdigest()
        upload = self.post("/api/documents/uploads", {
            "name": "review.pdf", "size": len(source), "sha256": digest,
            "role": "paper", "request_key": "review-fixture-upload",
        }, expected=202)
        self.post("/api/documents/uploads/%s/parts" % upload["upload_id"], {
            "index": 0, "sha256": digest, "data_base64": base64.b64encode(source).decode(),
        })
        task = self.post("/api/documents/uploads/%s/complete" % upload["upload_id"], {"request_key": "review-fixture-complete"}, expected=202)
        self.task_id = task["task_id"]

        def converter(source_path, name, store, school_id, document_id, conversion_id, work_dir, **kwargs):
            texts = ["1．某实验如下。\n（1）请选择操作。\nA．甲\nB．乙\n（2）说明理由。", "2．计算速度 $v^2$。"]
            blocks = [{"id": "b%s" % index, "type": "paragraph", "page": 1, "order": index,
                       "bbox": [0, 0, 1, 1], "markdown": text, "asset_ids": [],
                       "source_locator": {"kind": "pdf", "page": 1}, "issues": []}
                      for index, text in enumerate(texts, 1)]
            document = {"schema_version": 1, "document_id": document_id, "conversion_id": conversion_id,
                        "source_sha256": digest, "pages": [{"page": 1, "width": 100, "height": 100}],
                        "blocks": blocks, "assets": [], "issues": []}
            return {"document": document, "markdown": "\n\n".join(texts), "assets": [],
                    "manifest": {"issues": []}, "adapter_name": "fixture", "adapter_version": "1"}

        self.assertEqual(run_once(self.server.db_path, converter=converter)["status"], "parsed")
        status, _, payload = self.server.request("GET", "/api/documents/tasks/%s/items" % self.task_id,
                                                 headers={"Cookie": self.cookie})
        self.assertEqual(status, 200)
        self.items = json.loads(payload)["items"]

    def post(self, path, payload, expected=200):
        status, _, body = self.server.request("POST", path, json.dumps(payload).encode(), {
            "Content-Type": "application/json", "Cookie": self.cookie,
            "Origin": "http://%s:%s" % self.server.address,
        })
        self.assertEqual(status, expected, body)
        parsed = json.loads(body)
        return parsed.get("result", parsed)

    def test_structure_preview_replay_save_review_and_publication(self):
        item = self.items[0]
        child_key = item["document"]["children"][0]["key"]
        endpoint = "/api/documents/items/%s/preview" % item["id"]
        action = {"action": "set_kind", "child_key": child_key, "kind": "multiple_choice"}
        preview = self.post(endpoint, {"task_id": self.task_id, "markdown": serialize_question_md(item["document"]),
                                       "structure_action": action})
        edited = preview["markdown"].replace("\n甲\n", "\n修改甲\n")
        replay = self.post(endpoint, {"task_id": self.task_id, "markdown": edited, "structure_operations": [action]})
        self.assertEqual(replay["document"]["children"][0]["kind"], "multiple_choice")
        self.assertEqual(replay["document"]["children"][0]["options"][0]["markdown"], "修改甲")
        save_path = "/api/documents/items/%s/save" % item["id"]
        saved = self.post(save_path, {"task_id": self.task_id, "document": replay["document"],
                                     "expected_revision": 1, "request_key": "review-structure-save"})
        self.assertTrue(saved["issues"])
        confirm_path = "/api/documents/tasks/%s/confirm" % self.task_id
        self.post(confirm_path, {"request_key": "review-publish-blocked", "items": [{"id": item["id"], "expected_revision": 2}]}, expected=422)
        reviewed = self.post(save_path, {"task_id": self.task_id, "document": saved["document"],
            "expected_revision": 2, "request_key": "review-structure-resolved",
            "resolved_issue_ids": [entry["id"] for entry in saved["issues"]], "resolution_note": "已对照原卷核对多选题型及小问选项"})
        self.post(confirm_path, {"request_key": "review-publish-confirmed", "items": [{"id": item["id"], "expected_revision": 3}]})
        self.post(endpoint, {"task_id": self.task_id, "markdown": serialize_question_md(reviewed["document"]),
                            "structure_action": {"action": "remove_child", "child_key": child_key}}, expected=409)

    def test_structure_controls_and_preview_are_read_only_until_save(self):
        status, _, page = self.server.request("GET", "/documents/review?task_id=%s" % self.task_id,
                                              headers={"Cookie": self.cookie})
        self.assertEqual(status, 200)
        self.assertIn(b"data-structure-kind", page)
        self.assertIn(b"data-structure-action=\"add_child\"", page)
        item = self.items[0]
        self.post("/api/documents/items/%s/preview" % item["id"], {
            "task_id": self.task_id, "markdown": serialize_question_md(item["document"]),
            "structure_action": {"action": "remove_child", "child_key": item["document"]["children"][1]["key"]},
        })
        conn = connect(self.server.db_path)
        row = conn.execute("select document_json,review_revision from parsed_question_items where id=?", (item["id"],)).fetchone()
        self.assertEqual(canonical_json(json.loads(row[0])), canonical_json(item["document"]))
        self.assertEqual(row[1], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
