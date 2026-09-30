import hashlib
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from highschoolphysics.document_maintenance import collect_orphaned_objects
from highschoolphysics.document_store import DocumentStore


SCHEMA = """
create table document_files(storage_key text);
create table document_upload_parts(storage_key text);
create table document_conversions(id text, markdown_key text, layout_key text, manifest_key text);
create table document_assets(storage_key text);
create table document_uploads(id text, school_id text, state text, expires_at text, assembly_lease_until text, assembly_token text);
create table document_parse_tasks(id text, lease_token text, lease_until text, status text);
"""


class DocumentMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "documents"
        self.store = DocumentStore(self.root)
        self.conn = sqlite3.connect(Path(self.tmpdir.name) / "maintenance.sqlite3")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def make_old(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix:
            path.write_bytes(b"orphan")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "payload.bin").write_bytes(b"orphan")
        old = time.time() - 7200
        for current, dirs, files in os.walk(path, topdown=False):
            for name in files:
                os.utime(Path(current) / name, (old, old), follow_symlinks=False)
            for name in dirs:
                os.utime(Path(current) / name, (old, old), follow_symlinks=False)
            os.utime(current, (old, old), follow_symlinks=False)
        if path.is_file():
            os.utime(path, (old, old), follow_symlinks=False)
        return path

    def test_dry_run_and_apply_remove_only_unreferenced_terminal_upload_objects(self):
        upload_id = "upload-active"
        document_id = "doc-" + hashlib.sha256(upload_id.encode()).hexdigest()[:32]
        referenced_key = "schools/school-1/originals/doc-kept/source.pdf"
        active_key = "schools/school-1/originals/%s/source.pdf" % document_id
        orphan_key = "schools/school-1/originals/doc-orphan/source.pdf"
        self.conn.execute("insert into document_files values(?)", (referenced_key,))
        self.conn.execute(
            "insert into document_uploads values(?,?,?,?,?,?)",
            (upload_id, "school-1", "uploading", "2999-01-01T00:00:00Z", None, None),
        )
        self.make_old(self.root / referenced_key)
        self.make_old(self.root / active_key)
        self.make_old(self.root / orphan_key)
        active_staging = self.make_old(self.root / "staging" / upload_id)
        dead_staging = self.make_old(self.root / "staging" / "upload-cancelled")
        self.conn.execute(
            "insert into document_uploads values(?,?,?,?,?,?)",
            ("upload-cancelled", "school-1", "cancelled", "2999-01-01T00:00:00Z", None, None),
        )
        self.conn.commit()

        plan = collect_orphaned_objects(self.conn, self.store, retention_seconds=3600)
        self.assertTrue(plan["dry_run"])
        self.assertIn(orphan_key, plan["candidates"])
        self.assertNotIn(referenced_key, plan["candidates"])
        self.assertNotIn(active_key, plan["candidates"])
        self.assertNotIn("staging/upload-active", plan["candidates"])
        self.assertIn("staging/upload-cancelled", plan["candidates"])
        self.assertTrue((self.root / orphan_key).exists())

        result = collect_orphaned_objects(self.conn, self.store, retention_seconds=3600, dry_run=False)
        self.assertEqual(result["removed"], result["candidates"])
        self.assertFalse((self.root / orphan_key).exists())
        self.assertFalse(dead_staging.exists())
        self.assertTrue((self.root / referenced_key).exists())
        self.assertTrue((self.root / active_key).exists())
        self.assertTrue(active_staging.exists())

    def test_conversion_outputs_and_worker_directories_respect_references_and_running_tasks(self):
        kept_id = "conversion-kept"
        conversion_path = self.root / "schools/school-1/conversions" / kept_id
        self.make_old(conversion_path)
        key_prefix = "schools/school-1/conversions/%s" % kept_id
        self.conn.execute(
            "insert into document_conversions values(?,?,?,?)",
            (kept_id, key_prefix + "/document.md", key_prefix + "/layout.json", key_prefix + "/manifest.json"),
        )
        orphan_conversion = self.make_old(self.root / "schools/school-1/conversions/conversion-orphan")
        active_work = self.make_old(self.root / "work/task-active/token-live")
        stale_work = self.make_old(self.root / "work/task-active/token-old")
        self.conn.execute(
            "insert into document_parse_tasks values(?,?,?,?)",
            ("task-active", "token-live", "2000-01-01T00:00:00Z", "running"),
        )
        self.conn.commit()

        result = collect_orphaned_objects(self.conn, self.store, retention_seconds=3600, dry_run=False)
        self.assertNotIn("schools/school-1/conversions/conversion-orphan", result["removed"])
        self.assertIn("work/task-active/token-old", result["removed"])
        self.assertTrue(orphan_conversion.exists())
        self.assertFalse(stale_work.exists())
        self.assertTrue(conversion_path.exists())
        self.assertTrue(active_work.exists())

        self.conn.execute("update document_parse_tasks set status='parsed',lease_token=null,lease_until=null where id='task-active'")
        self.conn.commit()
        result = collect_orphaned_objects(self.conn, self.store, retention_seconds=3600, dry_run=False)
        self.assertIn("schools/school-1/conversions/conversion-orphan", result["removed"])
        self.assertFalse(orphan_conversion.exists())

    def test_unexpired_upload_expires_atomically_before_its_orphans_are_collected(self):
        upload_id = "upload-expired"
        self.conn.execute(
            "insert into document_uploads values(?,?,?,?,?,?)",
            (upload_id, "school-1", "uploading", "2000-01-01T00:00:00Z", None, None),
        )
        staging = self.make_old(self.root / "staging" / upload_id)
        self.conn.commit()

        result = collect_orphaned_objects(self.conn, self.store, retention_seconds=3600, dry_run=False)
        self.assertIn("staging/upload-expired", result["removed"])
        self.assertEqual(
            self.conn.execute("select state from document_uploads where id=?", (upload_id,)).fetchone()[0],
            "expired",
        )
        self.assertFalse(staging.exists())

    def test_cleanup_failure_keeps_expired_upload_terminal_for_a_later_retry(self):
        upload_id = "upload-cleanup-error"
        self.conn.execute(
            "insert into document_uploads values(?,?,?,?,?,?)",
            (upload_id, "school-1", "uploading", "2000-01-01T00:00:00Z", None, None),
        )
        staging = self.make_old(self.root / "staging" / upload_id)
        self.conn.commit()

        with patch("highschoolphysics.document_maintenance.shutil.rmtree", side_effect=PermissionError("read only")):
            result = collect_orphaned_objects(self.conn, self.store, retention_seconds=3600, dry_run=False)
        self.assertIn({"path": "staging/upload-cleanup-error", "error": "PermissionError"}, result["errors"])
        self.assertNotIn("staging/upload-cleanup-error", result["removed"])
        self.assertTrue(staging.exists())
        self.assertEqual(
            self.conn.execute("select state from document_uploads where id=?", (upload_id,)).fetchone()[0],
            "expired",
        )

    def test_recent_objects_and_symlink_trees_are_never_candidates(self):
        recent = self.root / "schools/school-1/originals/doc-recent/source.pdf"
        recent.parent.mkdir(parents=True, exist_ok=True)
        recent.write_bytes(b"new")
        outside = Path(self.tmpdir.name) / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        link = self.root / "staging/upload-symlink"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks are unavailable")
        self.conn.commit()

        result = collect_orphaned_objects(self.conn, self.store, retention_seconds=3600, dry_run=False)
        self.assertNotIn("schools/school-1/originals/doc-recent/source.pdf", result["removed"])
        self.assertNotIn("staging/upload-symlink", result["removed"])
        self.assertTrue(recent.exists())
        self.assertEqual((outside / "keep.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
