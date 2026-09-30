from copy import deepcopy
import json
import unittest

from highschoolphysics import db
from highschoolphysics.content_corrections import (
    CorrectionError,
    _old_asset_hashes,
    _snapshot_old_content_hash,
    apply_preview,
    build_preview,
    rollback_migration,
    verify_baseline,
)
from highschoolphysics.document_models import canonical_sha256, validate_question_document
from highschoolphysics.question_content import snapshot_content


class HistoricalContentCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.initialize_database(self.conn)
        db.seed_demo_data(self.conn)
        snapshot = self.conn.execute("select * from question_version_snapshots where id='snap-q1'").fetchone()
        self.snapshot = snapshot
        document = validate_question_document({
            "schema_version": 1,
            "number": "1",
            "kind": "single_choice",
            "stem_md": "已核对的可编辑题干。",
            "options": [{"key": "A", "markdown": "完整选项甲。"}],
            "answer_md": "",
            "analysis_md": "",
            "answer_state": "missing",
            "grading_rule": None,
            "children": [],
            "source_spans": [],
            "asset_refs": [],
            "issues": [],
        })
        self.conn.execute(
            "insert into question_content_groups(id,school_id,original_paper_id,source_item_id,current_revision_id,created_by) values(?,?,?,?,NULL,?)",
            ("group-correction-test", "school-demo", None, None, "user-teacher-li"),
        )
        self.conn.execute(
            """insert into question_content_revisions
               (id,group_id,revision_no,schema_version,document_json,content_sha256,review_state,answer_state,created_by,change_reason)
               values(?,?,?,?,?,?,?,?,?,?)""",
            ("revision-correction-test", "group-correction-test", 1, 1, json.dumps(document, ensure_ascii=False, sort_keys=True), canonical_sha256(document), "verified", "missing", "user-teacher-li", "synthetic migration test"),
        )
        self.conn.execute("update question_content_groups set current_revision_id=? where id=?", ("revision-correction-test", "group-correction-test"))
        self.conn.commit()
        self.mapping = {
            "schema_version": 1,
            "migration_key": "synthetic-correction-001",
            "school_id": "school-demo",
            "baseline_manifest_sha256": "",
            "entries": [{
                "old_question_id": snapshot["question_id"],
                "snapshot_ids": [snapshot["id"]],
                "expected_old_content_hash": _snapshot_old_content_hash(self.conn, snapshot),
                "expected_old_asset_hashes": _old_asset_hashes(self.conn, snapshot["question_id"], "school-demo"),
                "new_revision_id": "revision-correction-test",
                "child_key": "",
                "verified_original_number": "1",
                "reason": "合成迁移工具验收",
                "reviewed_by": "user-teacher-li",
                "reviewed_at": "2026-09-29T10:00:00+08:00",
            }],
        }

    def tearDown(self):
        self.conn.close()

    def test_preview_rejects_target_revision_that_is_not_verified(self):
        self.conn.execute(
            "update question_content_revisions set review_state='draft' where id=?",
            ("revision-correction-test",),
        )
        preview = build_preview(self.conn, self.mapping)
        self.assertIn("target revision must be verified", preview["errors"])
        self.assertEqual(
            self.conn.execute("select count(*) from historical_content_corrections").fetchone()[0],
            0,
        )

    def test_preview_pins_mapping_and_baseline_apply_is_idempotent_and_reversible(self):
        baseline = build_preview(self.conn, self.mapping)
        self.assertEqual(baseline["errors"], [])
        self.mapping["baseline_manifest_sha256"] = baseline["baseline_manifest_sha256"]
        preview = build_preview(self.conn, self.mapping)
        self.assertEqual(preview["errors"], [])
        self.assertIn("student_responses", preview["protected_records"])

        with self.assertRaisesRegex(CorrectionError, "stale"):
            apply_preview(self.conn, self.mapping, "0" * 64, "user-admin")
        self.assertEqual(self.conn.execute("select count(*) from historical_content_corrections").fetchone()[0], 0)

        before_responses = [tuple(row) for row in self.conn.execute("select * from student_responses order by id")]
        applied = apply_preview(self.conn, self.mapping, preview["preview_sha256"], "user-admin")
        self.assertFalse(applied["idempotent"])
        self.assertIsNotNone(snapshot_content(self.conn, "snap-q1", "school-demo"))
        repeated = apply_preview(self.conn, self.mapping, preview["preview_sha256"], "user-admin")
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["correction_ids"], applied["correction_ids"])

        changed_mapping = deepcopy(self.mapping)
        changed_mapping["entries"][0]["reason"] += "；不同映射内容"
        changed_preview = build_preview(self.conn, changed_mapping)
        self.assertEqual(changed_preview["errors"], [])
        with self.assertRaisesRegex(CorrectionError, "migration_key reused with different mapping"):
            apply_preview(self.conn, changed_mapping, changed_preview["preview_sha256"], "user-admin")
        corrections = self.conn.execute(
            "select id,mapping_sha256,state from historical_content_corrections where migration_key=?",
            (self.mapping["migration_key"],),
        ).fetchall()
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["mapping_sha256"], applied["mapping_sha256"])
        self.assertEqual(corrections[0]["state"], "active")

        verified = verify_baseline(self.conn, self.mapping, preview)
        self.assertTrue(verified["ok"], verified["errors"])
        self.assertEqual(before_responses, [tuple(row) for row in self.conn.execute("select * from student_responses order by id")])

        rollback = rollback_migration(self.conn, self.mapping["migration_key"], "user-admin", "撤销合成验收")
        self.assertEqual(rollback["revoked_count"], 1)
        self.assertIsNone(snapshot_content(self.conn, "snap-q1", "school-demo"))
        self.assertEqual(
            self.conn.execute("select state from historical_content_corrections where migration_key=?", (self.mapping["migration_key"],)).fetchone()[0],
            "revoked",
        )


if __name__ == "__main__":
    unittest.main()
