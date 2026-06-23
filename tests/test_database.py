import tempfile
import threading
import unittest
from pathlib import Path

from highschoolphysics.db import (
    SCHEMA_VERSION,
    bootstrap_admin,
    connect,
    initialize_database,
    seed_demo_data,
)
from highschoolphysics.security import hash_password


def table_columns(conn, table):
    return {
        row["name"]
        for row in conn.execute("pragma table_info(%s)" % table).fetchall()
    }


class DatabaseConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "database.sqlite3"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_phase_2b_schema_adds_taxonomy_tables_and_columns(self):
        conn = connect(self.db_path)
        initialize_database(conn)

        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertTrue(
            {
                "taxonomy_sources",
                "taxonomy_source_links",
                "curriculum_topics",
                "knowledge_curriculum_mappings",
                "taxonomy_replacements",
                "literacy_tags",
            }.issubset(tables)
        )
        knowledge_columns = table_columns(conn, "knowledge_nodes")
        ability_columns = table_columns(conn, "ability_tags")
        self.assertTrue(
            {"default_key", "is_default"}.issubset(knowledge_columns)
        )
        self.assertTrue(
            {"default_key", "is_default", "change_note"}.issubset(
                ability_columns
            )
        )
        self.assertEqual(
            conn.execute("pragma user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        conn.close()

    def test_phase_2c_schema_adds_question_bank_tables_and_columns(self):
        conn = connect(self.db_path)
        initialize_database(conn)

        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertTrue(
            {
                "original_papers",
                "question_import_batches",
                "parsed_question_items",
                "document_parser_configs",
            }.issubset(tables)
        )
        question_columns = table_columns(conn, "questions")
        self.assertTrue(
            {
                "original_paper_id",
                "import_batch_id",
                "parser_task_id",
                "original_page",
                "original_question_number",
                "source_school",
                "source_publisher",
                "exam_type",
                "source_confidence",
                "review_status",
            }.issubset(question_columns)
        )
        candidate_columns = table_columns(conn, "question_tag_candidates")
        self.assertIn("literacy_tags_json", candidate_columns)
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_2d_schema_adds_revision_redo_reason_and_export_tables(self):
        conn = connect(self.db_path)
        initialize_database(conn)

        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertTrue(
            {
                "grading_revisions",
                "grading_revision_items",
                "redo_attempts",
                "error_reason_tags",
                "wrong_question_error_tags",
                "export_profiles",
            }.issubset(tables)
        )
        response_columns = table_columns(conn, "student_responses")
        self.assertIn("ocr_payload_json", response_columns)
        self.assertIn("reviewed_by", response_columns)
        wrong_columns = table_columns(conn, "wrong_questions")
        self.assertIn("latest_redo_status", wrong_columns)
        self.assertIn("error_reason_tag_ids_json", wrong_columns)
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_2e_schema_adds_mastery_metric_table(self):
        conn = connect(self.db_path)
        initialize_database(conn)

        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertIn("student_mastery_metrics", tables)
        columns = table_columns(conn, "student_mastery_metrics")
        self.assertTrue(
            {
                "tag_type",
                "tag_id",
                "assessment_attempts",
                "assessment_correct",
                "assessment_wrong",
                "assessment_blank",
                "redo_attempts",
                "redo_correct",
                "redo_wrong",
                "eligible_attempts",
                "correct_rate",
                "mastery_state",
            }.issubset(columns)
        )
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_2e_schema_upgrades_existing_phase_2d_database(self):
        conn = connect(self.db_path)
        initialize_database(conn)
        seed_demo_data(conn)
        conn.execute(
            """
            insert into wrong_questions(
                id, school_id, assessment_id, student_id, question_id,
                response_id, wrong_answer, correct_answer_json, score,
                max_score, error_reason, redo_status
            ) values(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "legacy-wq-phase2e",
                "school-demo",
                "assess-week-1",
                "stu-1001",
                "q-newton-1",
                "resp-1001-q1",
                "B",
                '{"type":"single_choice","answer":"A"}',
                0,
                4,
                "旧错因",
                "pending",
            ),
        )
        conn.execute(
            """
            insert into knowledge_mastery_marks(
                id, school_id, student_id, knowledge_node_id, level, note, source
            ) values(?,?,?,?,?,?,?)
            """,
            (
                "legacy-kmm-phase2e",
                "school-demo",
                "stu-1001",
                "kn-pep2019-r1-c04-s03",
                "基本掌握",
                "旧手动标记",
                "student",
            ),
        )
        conn.execute("pragma user_version = 4")

        initialize_database(conn)

        self.assertEqual(
            conn.execute(
                "select error_reason from wrong_questions where id = ?",
                ("legacy-wq-phase2e",),
            ).fetchone()["error_reason"],
            "旧错因",
        )
        self.assertEqual(
            conn.execute(
                "select level from knowledge_mastery_marks where id = ?",
                ("legacy-kmm-phase2e",),
            ).fetchone()["level"],
            "基本掌握",
        )
        self.assertIn(
            "student_mastery_metrics",
            {
                row["name"]
                for row in conn.execute(
                    "select name from sqlite_master where type = 'table'"
                ).fetchall()
            },
        )
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_3_schema_migrates_phase_2g_database_without_history_loss(self):
        conn = connect(self.db_path)
        initialize_database(conn)
        seed_demo_data(conn)
        conn.execute("pragma user_version = 5")
        before = {
            "assessments": conn.execute(
                "select count(*) from assessment_sessions"
            ).fetchone()[0],
            "responses": conn.execute(
                "select count(*) from student_responses"
            ).fetchone()[0],
            "ontology": conn.execute(
                "select count(*) from knowledge_nodes"
            ).fetchone()[0],
        }

        initialize_database(conn)

        after = {
            "assessments": conn.execute(
                "select count(*) from assessment_sessions"
            ).fetchone()[0],
            "responses": conn.execute(
                "select count(*) from student_responses"
            ).fetchone()[0],
            "ontology": conn.execute(
                "select count(*) from knowledge_nodes"
            ).fetchone()[0],
        }
        self.assertEqual(before, after)
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(conn.execute("pragma foreign_key_check").fetchall(), [])
        conn.close()

    def test_phase_2d_schema_upgrades_existing_phase_2c_database(self):
        conn = connect(self.db_path)
        initialize_database(conn)
        seed_demo_data(conn)
        conn.execute(
            """
            insert into wrong_questions(
                id, school_id, assessment_id, student_id, question_id,
                response_id, wrong_answer, correct_answer_json, score,
                max_score, error_reason, redo_status
            ) values(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "legacy-wq",
                "school-demo",
                "assess-week-1",
                "stu-1001",
                "q-newton-1",
                "resp-1001-q1",
                "B",
                '{"type":"single_choice","answer":"A"}',
                0,
                4,
                "旧错因",
                "pending",
            ),
        )
        conn.execute("pragma user_version = 3")

        initialize_database(conn)

        wrong = conn.execute(
            """
            select error_reason, latest_redo_status, error_reason_tag_ids_json
            from wrong_questions
            where id = ?
            """,
            ("legacy-wq",),
        ).fetchone()
        self.assertEqual(wrong["error_reason"], "旧错因")
        self.assertEqual(wrong["latest_redo_status"], "pending")
        self.assertEqual(wrong["error_reason_tag_ids_json"], "[]")
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_production_schema_adds_runtime_capability_checks(self):
        conn = connect(self.db_path)
        initialize_database(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertIn("runtime_capability_checks", tables)
        columns = table_columns(conn, "runtime_capability_checks")
        self.assertTrue(
            {
                "capability_id",
                "status",
                "label",
                "detail",
                "version",
                "checked_by",
                "checked_at",
            }.issubset(columns)
        )
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_production_schema_adds_provider_operations_tables(self):
        conn = connect(self.db_path)
        initialize_database(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertTrue(
            {
                "provider_configs",
                "provider_usage_events",
                "provider_budget_windows",
            }.issubset(tables)
        )
        config_columns = table_columns(conn, "provider_configs")
        self.assertTrue(
            {
                "provider_kind",
                "provider_name",
                "model_name",
                "api_endpoint",
                "secret_ciphertext",
                "secret_masked",
                "daily_call_limit",
                "monthly_budget_cents",
                "per_call_max_cents",
                "input_cost_per_1k_cents",
                "output_cost_per_1k_cents",
                "last_test_status",
                "last_test_detail",
            }.issubset(config_columns)
        )
        usage_columns = table_columns(conn, "provider_usage_events")
        self.assertTrue(
            {
                "provider_config_id",
                "request_type",
                "prompt_version",
                "input_units",
                "output_units",
                "page_count",
                "estimated_cost_cents",
                "outcome",
                "error_category",
            }.issubset(usage_columns)
        )
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_production_schema_adds_pdf_artifact_metadata(self):
        conn = connect(self.db_path)
        initialize_database(conn)
        export_columns = table_columns(conn, "export_tasks")
        self.assertTrue(
            {
                "file_name",
                "content_type",
                "byte_size",
                "engine_version",
                "created_by",
                "completed_at",
            }.issubset(export_columns)
        )
        generated_columns = table_columns(conn, "generated_export_files")
        self.assertTrue(
            {
                "export_task_id",
                "file_name",
                "content_type",
                "byte_size",
                "engine_version",
                "storage_path",
                "created_by",
            }.issubset(generated_columns)
        )
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_production_schema_adds_sso_state_and_binding_tables(self):
        conn = connect(self.db_path)
        initialize_database(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertTrue(
            {"sso_login_states", "external_identity_bindings"}.issubset(tables)
        )
        self.assertTrue(
            {
                "provider_config_id",
                "state",
                "nonce",
                "code_verifier",
                "redirect_uri",
                "status",
                "consumed_at",
            }.issubset(table_columns(conn, "sso_login_states"))
        )
        self.assertTrue(
            {
                "provider",
                "issuer",
                "subject",
                "external_id",
                "email",
                "display_name",
                "status",
                "local_user_id",
            }.issubset(table_columns(conn, "external_identity_bindings"))
        )
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_2c_schema_upgrades_legacy_question_tables(self):
        conn = connect(self.db_path)
        conn.executescript(
            """
            create table schools (
                id text primary key,
                name text not null,
                org_scope text not null,
                created_at text default current_timestamp
            );
            create table questions (
                id text primary key,
                school_id text not null references schools(id),
                stem text not null,
                options_json text not null,
                answer_json text not null,
                analysis text not null default '',
                question_type text not null,
                source text not null,
                grade text not null,
                chapter text not null,
                difficulty text not null,
                media_json text not null default '[]',
                scenario text not null default '',
                quality_status text not null default 'draft',
                notes text not null default '',
                version integer not null default 1,
                created_at text default current_timestamp
            );
            create table question_tag_candidates (
                id text primary key,
                school_id text not null references schools(id),
                question_id text not null references questions(id),
                cache_key text not null,
                knowledge_tags_json text not null,
                ability_tags_json text not null,
                prompt_version text not null,
                model_version text not null,
                status text not null default 'pending_review',
                created_by text,
                reviewed_by text,
                review_note text not null default '',
                created_at text default current_timestamp,
                reviewed_at text,
                unique(question_id, cache_key)
            );
            insert into schools(id, name, org_scope)
            values('school-legacy', '旧学校', 'single-school');
            insert into questions(
                id, school_id, stem, options_json, answer_json, analysis,
                question_type, source, grade, chapter, difficulty, media_json,
                scenario, quality_status, notes, version
            ) values(
                'q-legacy', 'school-legacy', '旧题干', '{}', '{}', '旧解析',
                'short_answer', '旧来源', '高二', '旧章节', 'medium', '[]',
                '', 'reviewed', '旧备注', 4
            );
            insert into question_tag_candidates(
                id, school_id, question_id, cache_key, knowledge_tags_json,
                ability_tags_json, prompt_version, model_version, status
            ) values(
                'cand-legacy', 'school-legacy', 'q-legacy', 'legacy-key',
                '[{"id":"kn-legacy"}]', '[{"id":"ab-legacy"}]',
                'legacy-prompt', 'legacy-model', 'pending_review'
            );
            pragma user_version = 2;
            """
        )

        initialize_database(conn)

        question = conn.execute(
            "select * from questions where id = 'q-legacy'"
        ).fetchone()
        candidate = conn.execute(
            "select * from question_tag_candidates where id = 'cand-legacy'"
        ).fetchone()
        self.assertEqual(question["stem"], "旧题干")
        self.assertEqual(question["version"], 4)
        self.assertIsNone(question["original_paper_id"])
        self.assertEqual(question["source_confidence"], 1.0)
        self.assertEqual(question["review_status"], "confirmed")
        self.assertEqual(candidate["knowledge_tags_json"], '[{"id":"kn-legacy"}]')
        self.assertEqual(candidate["ability_tags_json"], '[{"id":"ab-legacy"}]')
        self.assertEqual(candidate["literacy_tags_json"], "[]")
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_phase_2b_schema_upgrades_legacy_tables_without_losing_rows(self):
        conn = connect(self.db_path)
        conn.executescript(
            """
            create table schools (
                id text primary key,
                name text not null,
                org_scope text not null,
                created_at text default current_timestamp
            );
            create table knowledge_ontology_versions (
                id text primary key,
                school_id text not null references schools(id),
                version_label text not null,
                status text not null,
                source_summary text not null,
                created_at text default current_timestamp
            );
            create table knowledge_nodes (
                id text primary key,
                school_id text not null references schools(id),
                ontology_version_id text not null
                    references knowledge_ontology_versions(id),
                parent_id text references knowledge_nodes(id),
                stable_code text not null,
                name text not null,
                node_type text not null,
                level integer not null,
                aliases text not null default '',
                description text not null default '',
                textbook_scope text not null default '',
                source text not null default '',
                enabled integer not null default 1,
                deleted_at text,
                version integer not null default 1,
                change_note text not null default ''
            );
            create table ability_tags (
                id text primary key,
                school_id text not null references schools(id),
                ontology_version_id text not null
                    references knowledge_ontology_versions(id),
                stable_code text not null,
                name text not null,
                description text not null default '',
                source text not null default '',
                enabled integer not null default 1,
                deleted_at text,
                version integer not null default 1
            );
            insert into schools(id, name, org_scope)
            values('school-legacy', '旧学校', 'single-school');
            insert into knowledge_ontology_versions(
                id, school_id, version_label, status, source_summary
            ) values(
                'onto-legacy', 'school-legacy', '旧本体', 'active', '旧来源'
            );
            insert into knowledge_nodes(
                id, school_id, ontology_version_id, parent_id, stable_code,
                name, node_type, level, aliases, description,
                textbook_scope, source, enabled, version, change_note
            ) values(
                'kn-legacy', 'school-legacy', 'onto-legacy', null, 'OLD.K',
                '旧知识点', 'knowledge', 1, '旧别名', '旧说明',
                '旧教材', '旧来源', 0, 7, '保留修改'
            );
            insert into ability_tags(
                id, school_id, ontology_version_id, stable_code, name,
                description, source, enabled, version
            ) values(
                'ab-legacy', 'school-legacy', 'onto-legacy', 'OLD.A',
                '旧能力', '旧说明', '旧来源', 0, 5
            );
            """
        )

        initialize_database(conn)

        knowledge = conn.execute(
            "select * from knowledge_nodes where id = 'kn-legacy'"
        ).fetchone()
        ability = conn.execute(
            "select * from ability_tags where id = 'ab-legacy'"
        ).fetchone()
        self.assertEqual(knowledge["name"], "旧知识点")
        self.assertEqual(knowledge["enabled"], 0)
        self.assertEqual(knowledge["version"], 7)
        self.assertEqual(knowledge["change_note"], "保留修改")
        self.assertIsNone(knowledge["default_key"])
        self.assertEqual(knowledge["is_default"], 0)
        self.assertEqual(ability["name"], "旧能力")
        self.assertEqual(ability["enabled"], 0)
        self.assertEqual(ability["version"], 5)
        self.assertEqual(ability["change_note"], "")
        self.assertIsNone(ability["default_key"])
        self.assertEqual(ability["is_default"], 0)
        conn.close()

    def test_demo_seed_installs_full_default_taxonomy(self):
        conn = connect(self.db_path)
        initialize_database(conn)

        seed_demo_data(conn)

        self.assertEqual(
            conn.execute(
                "select count(*) from knowledge_nodes where is_default = 1"
            ).fetchone()[0],
            158,
        )
        self.assertEqual(
            conn.execute(
                "select count(*) from ability_tags where is_default = 1"
            ).fetchone()[0],
            15,
        )
        self.assertEqual(
            conn.execute(
                "select count(*) from literacy_tags where is_default = 1"
            ).fetchone()[0],
            18,
        )
        conn.close()

    def test_file_database_uses_wal_and_five_second_busy_timeout(self):
        conn = connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute("pragma journal_mode").fetchone()[0].lower(),
                "wal",
            )
            self.assertGreaterEqual(
                conn.execute("pragma busy_timeout").fetchone()[0],
                5000,
            )
        finally:
            conn.close()

    def test_two_connections_can_complete_concurrent_writes(self):
        conn = connect(self.db_path)
        initialize_database(conn)
        seed_demo_data(conn)
        baseline = conn.execute(
            "select count(*) from audit_events"
        ).fetchone()[0]
        conn.close()

        barrier = threading.Barrier(2)
        errors = []

        def write_events(worker):
            worker_conn = None
            try:
                worker_conn = connect(self.db_path)
                barrier.wait(timeout=5)
                for index in range(20):
                    worker_conn.execute(
                        """
                        insert into audit_events(
                            id, school_id, actor_id, action, resource_type,
                            resource_id, detail_json
                        ) values(?,?,?,?,?,?,?)
                        """,
                        (
                            "audit-concurrent-%s-%s" % (worker, index),
                            "school-demo",
                            "user-admin",
                            "concurrent_test",
                            "test",
                            str(index),
                            "{}",
                        ),
                    )
                    worker_conn.commit()
            except Exception as error:
                errors.append(error)
            finally:
                if worker_conn is not None:
                    worker_conn.close()

        threads = [
            threading.Thread(target=write_events, args=(worker,))
            for worker in ("a", "b")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        conn = connect(self.db_path)
        try:
            final = conn.execute(
                "select count(*) from audit_events"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(final - baseline, 40)

    def test_bootstrap_admin_creates_non_demo_login_without_seed_accounts(self):
        conn = connect(self.db_path)
        initialize_database(conn)

        user_id = bootstrap_admin(
            conn,
            username="school_admin",
            display_name="学校管理员",
            password_hash=hash_password("AdminPhysics123"),
            school_name="本地学校",
        )

        self.assertTrue(user_id.startswith("user-admin-"))
        self.assertIsNone(
            conn.execute(
                "select id from users where username = 'teacher_li'"
            ).fetchone()
        )
        self.assertIsNotNone(
            conn.execute(
                "select id from users where username = 'school_admin'"
            ).fetchone()
        )
        audit = conn.execute(
            """
            select actor_id, action, user_id, detail_json
            from identity_audit_logs
            where action = 'admin_bootstrapped'
            """
        ).fetchone()
        self.assertEqual(audit["actor_id"], user_id)
        self.assertEqual(audit["user_id"], user_id)
        self.assertNotIn("AdminPhysics123", audit["detail_json"])
        conn.close()

    def test_bootstrap_admin_refuses_database_with_existing_users(self):
        conn = connect(self.db_path)
        initialize_database(conn)
        seed_demo_data(conn)

        with self.assertRaisesRegex(
            ValueError,
            "users already exist",
        ):
            bootstrap_admin(
                conn,
                username="replacement_admin",
                display_name="替代管理员",
                password_hash=hash_password("AdminPhysics123"),
                school_name="本地学校",
            )

        conn.close()


if __name__ == "__main__":
    unittest.main()
