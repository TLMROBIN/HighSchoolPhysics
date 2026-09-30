import sqlite3
import unittest
import json
import tempfile
from pathlib import Path

from highschoolphysics import db


def _quote_identifier(value):
    return '"%s"' % value.replace('"', '""')


def _snapshot_legacy_tables(conn):
    snapshot = {}
    tables = conn.execute(
        "select name,sql from sqlite_master where type='table' "
        "and name not like 'sqlite_%' order by name"
    ).fetchall()
    for table_row in tables:
        table, create_sql = table_row
        columns = [row[1] for row in conn.execute(
            "pragma table_info(%s)" % _quote_identifier(table)
        )]
        quoted_columns = ",".join(_quote_identifier(column) for column in columns)
        if "WITHOUT ROWID" in (create_sql or "").upper():
            key_columns = [
                row[1]
                for row in sorted(
                    (r for r in conn.execute(
                        "pragma table_info(%s)" % _quote_identifier(table)
                    ) if r[5]),
                    key=lambda r: r[5],
                )
            ]
            order_by = ",".join(_quote_identifier(column) for column in key_columns)
        else:
            order_by = "rowid"
        rows = [
            tuple(row)
            for row in conn.execute(
                "select %s from %s order by %s"
                % (quoted_columns, _quote_identifier(table), order_by)
            )
        ]
        snapshot[table] = (columns, rows)
    return snapshot


def _insert_history_fixture(conn):
    conn.execute(
        "insert into identity_accounts(id,user_id,provider,issuer,subject,external_id,status) values(?,?,?,?,?,?,?)",
        ("identity-kept", "stu-1001", "oidc", "https://id.example", "subject-1001", "1001", "active"),
    )
    conn.execute(
        "insert into wrong_questions(id,school_id,assessment_id,student_id,question_id,response_id,wrong_answer,correct_answer_json,score,max_score,error_reason,redo_status) values(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("wrong-kept", "school-demo", "assess-week-1", "stu-1001", "q-newton-2", "resp-1001-q2", "D", json.dumps({"answer": "B"}), 0, 4, "fixture", "pending"),
    )
    conn.execute(
        "insert into redo_attempts(id,school_id,wrong_question_id,student_id,answer,score,max_score,status,feedback) values(?,?,?,?,?,?,?,?,?)",
        ("redo-kept", "school-demo", "wrong-kept", "stu-1001", "B", 4, 4, "reviewed", "fixture"),
    )
    conn.execute(
        "insert into exam_assets(id,school_id,question_id,assessment_id,student_id,png) values(?,?,?,?,?,?)",
        (
            "answer-card-scan-kept",
            "school-demo",
            None,
            "assess-week-1",
            "stu-1001",
            bytes.fromhex("89504e470d0a1a0a") + b"fixture",
        ),
    )
    conn.commit()


def _assert_legacy_tables_unchanged(test_case, conn, before):
    for table, (columns, rows) in before.items():
        projection = ",".join(_quote_identifier(column) for column in columns)
        create_sql = conn.execute(
            "select sql from sqlite_master where type='table' and name=?", (table,)
        ).fetchone()[0]
        if "WITHOUT ROWID" in (create_sql or "").upper():
            info = conn.execute("pragma table_info(%s)" % _quote_identifier(table)).fetchall()
            key_columns = [row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5])]
            order_by = ",".join(_quote_identifier(column) for column in key_columns)
        else:
            order_by = "rowid"
        migrated_rows = [
            tuple(row)
            for row in conn.execute(
                "select %s from %s order by %s"
                % (projection, _quote_identifier(table), order_by)
            )
        ]
        test_case.assertEqual(migrated_rows, rows, table)


class DocumentIngestionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_empty_database_migrates_document_feature_v12_without_bumping_core_schema(self):
        db.initialize_database(self.conn)
        self.assertEqual(self.conn.execute("pragma user_version").fetchone()[0], 11)
        self.assertEqual(
            self.conn.execute(
                "select version from app_schema_migrations where feature='document_ingestion'"
            ).fetchone()[0],
            12,
        )
        self.assertEqual(self.conn.execute("pragma integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("pragma foreign_key_check").fetchall(), [])

        expected_tables = {
            "app_schema_migrations",
            "document_files",
            "document_uploads",
            "document_upload_parts",
            "document_conversions",
            "document_assets",
            "conversion_asset_refs",
            "question_content_groups",
            "question_content_revisions",
            "content_asset_refs",
            "question_content_bindings",
            "snapshot_content_bindings",
            "historical_content_corrections",
            "import_item_publications",
            "content_operation_keys",
        }
        table_names = {
            row[0]
            for row in self.conn.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        self.assertTrue(expected_tables.issubset(table_names))

        db.initialize_database(self.conn)
        self.assertEqual(self.conn.execute("pragma user_version").fetchone()[0], 11)
        self.assertEqual(db._document_ingestion_schema_version(self.conn), 12)
        self.assertEqual(
            self.conn.execute(
                "select count(*) from sqlite_master "
                "where type='table' and name='document_files'"
            ).fetchone()[0],
            1,
        )

    def test_v11_data_and_schema_are_unchanged_when_migration_statement_fails(self):
        db._initialize_legacy_schema(self.conn)
        self.assertEqual(self.conn.execute("pragma user_version").fetchone()[0], 11)
        self.conn.execute(
            "insert into schools(id,name,org_scope) values(?,?,?)",
            ("school-kept", "Migration fixture", "fixture-scope"),
        )
        self.conn.commit()
        before_columns = {
            table: tuple(row[1] for row in self.conn.execute("pragma table_info(%s)" % table))
            for table in ("document_parse_tasks", "parsed_question_items")
        }

        def deny_v12_index(action, _arg1, _arg2, _database, _source):
            if action == sqlite3.SQLITE_CREATE_INDEX:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.conn.set_authorizer(deny_v12_index)
        with self.assertRaises(sqlite3.DatabaseError):
            db._migrate_document_ingestion_v12(self.conn)
        self.conn.set_authorizer(None)

        self.assertEqual(self.conn.execute("pragma user_version").fetchone()[0], 11)
        self.assertEqual(
            tuple(
                self.conn.execute(
                    "select id,name,org_scope from schools where id='school-kept'"
                ).fetchone()
            ),
            ("school-kept", "Migration fixture", "fixture-scope"),
        )
        self.assertEqual(
            self.conn.execute(
                "select count(*) from sqlite_master "
                "where type='table' and name='document_files'"
            ).fetchone()[0],
            0,
        )
        for table, expected in before_columns.items():
            self.assertEqual(
                tuple(row[1] for row in self.conn.execute("pragma table_info(%s)" % table)),
                expected,
            )

        db.initialize_database(self.conn)
        self.assertEqual(self.conn.execute("pragma user_version").fetchone()[0], 11)
        self.assertEqual(db._document_ingestion_schema_version(self.conn), 12)
        self.assertEqual(
            tuple(
                self.conn.execute(
                    "select id,name,org_scope from schools where id='school-kept'"
                ).fetchone()
            ),
            ("school-kept", "Migration fixture", "fixture-scope"),
        )

    def test_v11_migration_preserves_every_existing_table_and_history_record(self):
        db._initialize_legacy_schema(self.conn)
        db.seed_demo_data(self.conn)
        _insert_history_fixture(self.conn)
        before = _snapshot_legacy_tables(self.conn)

        db._migrate_document_ingestion_v12(self.conn)

        self.assertEqual(self.conn.execute("pragma user_version").fetchone()[0], 11)
        self.assertEqual(db._document_ingestion_schema_version(self.conn), 12)
        _assert_legacy_tables_unchanged(self, self.conn, before)
        self.assertEqual(tuple(self.conn.execute("select raw_answer,final_answer from student_responses where id='resp-1001-q2'").fetchone()), ("D", "D"))
        self.assertEqual(self.conn.execute("select count(*) from wrong_questions where id='wrong-kept'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("select count(*) from redo_attempts where id='redo-kept'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("select count(*) from exam_assets where id='answer-card-scan-kept'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("pragma integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("pragma foreign_key_check").fetchall(), [])

    def test_v11_file_copy_migration_preserves_history_after_close_and_reopen(self):
        with tempfile.TemporaryDirectory(prefix="hsp-v11-copy-") as directory:
            source_path = Path(directory) / "source-v11.sqlite3"
            copy_path = Path(directory) / "copy-v11.sqlite3"
            source = db.connect(source_path)
            copy_conn = None
            migrated = None
            reopened = None
            try:
                db._initialize_legacy_schema(source)
                db.seed_demo_data(source)
                _insert_history_fixture(source)
                before = _snapshot_legacy_tables(source)

                copy_conn = db.connect(copy_path)
                source.backup(copy_conn)
                copy_conn.close()
                copy_conn = None
                source.close()
                source = None

                migrated = db.connect(copy_path)
                self.assertEqual(migrated.execute("pragma user_version").fetchone()[0], 11)
                db._migrate_document_ingestion_v12(migrated)
                migrated.close()
                migrated = None

                reopened = db.connect(copy_path)
                self.assertEqual(reopened.execute("pragma user_version").fetchone()[0], 11)
                self.assertEqual(db._document_ingestion_schema_version(reopened), 12)
                _assert_legacy_tables_unchanged(self, reopened, before)
                self.assertEqual(
                    tuple(reopened.execute(
                        "select raw_answer,final_answer from student_responses where id='resp-1001-q2'"
                    ).fetchone()),
                    ("D", "D"),
                )
                self.assertEqual(reopened.execute(
                    "select count(*) from answer_card_templates where id='card-template-1'"
                ).fetchone()[0], 1)
                self.assertGreater(reopened.execute(
                    "select count(*) from question_tags where question_id='q-newton-2'"
                ).fetchone()[0], 0)
                self.assertEqual(reopened.execute(
                    "select count(*) from exam_assets where id='answer-card-scan-kept'"
                ).fetchone()[0], 1)
                self.assertEqual(reopened.execute("pragma integrity_check").fetchone()[0], "ok")
                self.assertEqual(reopened.execute("pragma foreign_key_check").fetchall(), [])
            finally:
                for conn in (reopened, migrated, copy_conn, source):
                    if conn is not None:
                        conn.close()

    def test_legacy_v11_initializer_keeps_document_feature_marker_and_schema(self):
        db.initialize_database(self.conn)
        self.assertEqual(self.conn.execute("pragma user_version").fetchone()[0], 11)
        self.assertEqual(db._document_ingestion_schema_version(self.conn), 12)

        db._initialize_legacy_schema(self.conn)

        self.assertEqual(self.conn.execute("pragma user_version").fetchone()[0], 11)
        self.assertEqual(db._document_ingestion_schema_version(self.conn), 12)
        self.assertEqual(
            self.conn.execute(
                "select count(*) from sqlite_master where type='table' and name='document_files'"
            ).fetchone()[0],
            1,
        )

    def test_newer_database_is_rejected_before_schema_changes(self):
        self.conn.execute("pragma user_version = 13")
        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            db.initialize_database(self.conn)
        self.assertEqual(self.conn.execute("pragma user_version").fetchone()[0], 13)
        self.assertEqual(
            self.conn.execute(
                "select count(*) from sqlite_master where type='table'"
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
