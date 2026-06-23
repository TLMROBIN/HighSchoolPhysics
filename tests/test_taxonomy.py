import copy
import unittest

from highschoolphysics.db import (
    bootstrap_admin,
    connect,
    initialize_database,
    seed_demo_data,
)
from highschoolphysics.security import hash_password
from highschoolphysics.taxonomy import (
    DEFAULT_ONTOLOGY_ID,
    install_default_taxonomy,
    load_default_taxonomy,
    validate_taxonomy_bundle,
)


class TaxonomyManifestTests(unittest.TestCase):
    def test_default_bundle_has_expected_counts_and_valid_hierarchy(self):
        bundle = load_default_taxonomy()

        validate_taxonomy_bundle(bundle)

        knowledge = bundle["knowledge"]["records"]
        literacy = bundle["literacy"]["records"]
        self.assertEqual(len(knowledge), 158)
        self.assertEqual(
            {
                level: sum(item["level"] == level for item in knowledge)
                for level in (1, 2, 3)
            },
            {1: 6, 2: 27, 3: 125},
        )
        self.assertEqual(len(bundle["abilities"]["records"]), 15)
        self.assertEqual(len(literacy), 18)
        self.assertEqual(sum(item["level"] == 1 for item in literacy), 4)
        self.assertEqual(sum(item["level"] == 2 for item in literacy), 14)

    def test_every_non_root_item_has_an_existing_parent(self):
        bundle = load_default_taxonomy()

        for collection in ("knowledge", "literacy"):
            records = bundle[collection]["records"]
            ids = {item["id"] for item in records}
            for item in records:
                if item["level"] > 1:
                    self.assertIn(item["parent_id"], ids)

    def test_validator_rejects_duplicate_default_keys(self):
        bundle = load_default_taxonomy()
        bundle["abilities"]["records"].append(
            dict(bundle["abilities"]["records"][0])
        )

        with self.assertRaisesRegex(ValueError, "duplicate default_key"):
            validate_taxonomy_bundle(bundle)

    def test_validator_rejects_parent_level_gaps(self):
        bundle = load_default_taxonomy()
        child = next(
            item
            for item in bundle["knowledge"]["records"]
            if item["level"] == 3
        )
        child["level"] = 4

        with self.assertRaisesRegex(ValueError, "parent level mismatch"):
            validate_taxonomy_bundle(bundle)

    def test_validator_rejects_parent_cycles(self):
        bundle = load_default_taxonomy()
        root = bundle["knowledge"]["records"][0]
        child = next(
            item
            for item in bundle["knowledge"]["records"]
            if item.get("parent_id") == root["id"]
        )
        root["parent_id"] = child["id"]

        with self.assertRaisesRegex(ValueError, "parent cycle"):
            validate_taxonomy_bundle(bundle)

    def test_validator_rejects_source_pages_outside_document(self):
        bundle = load_default_taxonomy()
        source = bundle["sources"]["records"][0]
        record = next(
            item
            for item in bundle["knowledge"]["records"]
            if item["source_refs"][0]["source_key"] == source["source_key"]
        )
        record["source_refs"][0]["page_end"] = source["page_count"] + 1

        with self.assertRaisesRegex(ValueError, "exceeds source page count"):
            validate_taxonomy_bundle(bundle)

    def test_validator_rejects_broken_curriculum_mapping(self):
        bundle = load_default_taxonomy()
        broken = copy.deepcopy(bundle["knowledge"]["curriculum_mappings"][0])
        broken["curriculum_topic_id"] = "curriculum-missing"
        bundle["knowledge"]["curriculum_mappings"].append(broken)

        with self.assertRaisesRegex(ValueError, "unknown topic"):
            validate_taxonomy_bundle(bundle)

    def test_validator_requires_evidence_for_curriculum_mappings(self):
        bundle = load_default_taxonomy()
        bundle["knowledge"]["curriculum_mappings"][0]["source_refs"] = []

        with self.assertRaisesRegex(
            ValueError,
            "curriculum mapping requires source_refs",
        ):
            validate_taxonomy_bundle(bundle)


class TaxonomyInstallerTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        initialize_database(self.conn)

    def tearDown(self):
        self.conn.close()

    def _bootstrap_school(self):
        actor_id = bootstrap_admin(
            self.conn,
            username="school_admin",
            display_name="学校管理员",
            password_hash=hash_password("AdminPhysics123"),
            school_name="本地学校",
        )
        school_id = self.conn.execute(
            "select school_id from users where id = ?",
            (actor_id,),
        ).fetchone()["school_id"]
        return school_id, actor_id

    def test_install_creates_expected_default_records(self):
        school_id, actor_id = self._bootstrap_school()

        summary = install_default_taxonomy(
            self.conn,
            school_id=school_id,
            actor_id=actor_id,
            publish=True,
        )

        self.assertEqual(summary["knowledge"]["created"], 158)
        self.assertEqual(summary["abilities"]["created"], 15)
        self.assertEqual(summary["literacy"]["created"], 18)
        self.assertEqual(
            self.conn.execute(
                "select count(*) from taxonomy_sources"
            ).fetchone()[0],
            9,
        )
        self.assertEqual(
            self.conn.execute(
                "select count(*) from curriculum_topics"
            ).fetchone()[0],
            20,
        )
        self.assertEqual(
            self.conn.execute(
                "select count(*) from knowledge_curriculum_mappings"
            ).fetchone()[0],
            29,
        )
        ontology = self.conn.execute(
            "select status from knowledge_ontology_versions where id = ?",
            (DEFAULT_ONTOLOGY_ID,),
        ).fetchone()
        self.assertEqual(ontology["status"], "active")

    def test_reinstall_is_idempotent_and_preserves_school_edits(self):
        school_id, actor_id = self._bootstrap_school()
        install_default_taxonomy(
            self.conn,
            school_id,
            actor_id,
            publish=True,
        )
        self.conn.execute(
            """
            update knowledge_nodes
            set name = ?, enabled = 0, change_note = ?
            where default_key = ?
            """,
            (
                "校本修订名称",
                "备课组暂时停用",
                "pep2019.r1.c01.s01",
            ),
        )

        second = install_default_taxonomy(
            self.conn,
            school_id,
            actor_id,
            publish=True,
        )

        row = self.conn.execute(
            """
            select name, enabled, change_note
            from knowledge_nodes
            where default_key = ?
            """,
            ("pep2019.r1.c01.s01",),
        ).fetchone()
        self.assertEqual(
            dict(row),
            {
                "name": "校本修订名称",
                "enabled": 0,
                "change_note": "备课组暂时停用",
            },
        )
        self.assertEqual(second["knowledge"]["created"], 0)
        self.assertEqual(
            self.conn.execute(
                "select count(*) from knowledge_nodes where is_default = 1"
            ).fetchone()[0],
            158,
        )

    def test_existing_school_install_creates_draft_without_replacing_active(self):
        school_id, actor_id = self._bootstrap_school()
        self.conn.execute(
            """
            insert into knowledge_ontology_versions(
                id, school_id, version_label, status, source_summary
            ) values(?,?,?,?,?)
            """,
            (
                "onto-school-current",
                school_id,
                "校本当前版本",
                "active",
                "校本体系",
            ),
        )

        install_default_taxonomy(
            self.conn,
            school_id,
            actor_id,
            publish=False,
        )

        self.assertEqual(
            self.conn.execute(
                """
                select id from knowledge_ontology_versions
                where school_id = ? and status = 'active'
                """,
                (school_id,),
            ).fetchone()["id"],
            "onto-school-current",
        )
        self.assertEqual(
            self.conn.execute(
                """
                select status from knowledge_ontology_versions where id = ?
                """,
                (DEFAULT_ONTOLOGY_ID,),
            ).fetchone()["status"],
            "draft",
        )

    def test_invalid_bundle_rolls_back_without_partial_install(self):
        school_id, actor_id = self._bootstrap_school()
        bundle = load_default_taxonomy()
        bundle["knowledge"]["records"][0]["source_refs"][0][
            "source_key"
        ] = "missing-source"

        with self.assertRaisesRegex(ValueError, "unknown source key"):
            install_default_taxonomy(
                self.conn,
                school_id,
                actor_id,
                publish=True,
                bundle=bundle,
            )

        self.assertEqual(
            self.conn.execute(
                "select count(*) from taxonomy_sources"
            ).fetchone()[0],
            0,
        )
        self.assertIsNone(
            self.conn.execute(
                "select id from knowledge_ontology_versions where id = ?",
                (DEFAULT_ONTOLOGY_ID,),
            ).fetchone()
        )

    def test_legacy_live_tags_move_but_historical_snapshot_is_unchanged(self):
        seed_demo_data(self.conn)
        ontology_id = self.conn.execute(
            """
            select id from knowledge_ontology_versions
            where school_id = 'school-demo' and status = 'active'
            """
        ).fetchone()["id"]
        legacy_nodes = [
            (
                "kn-mechanics",
                None,
                "M",
                "力学",
                1,
            ),
            (
                "kn-newton",
                "kn-mechanics",
                "M.N",
                "相互作用与运动定律",
                2,
            ),
            (
                "kn-newton-2",
                "kn-newton",
                "M.N.2",
                "牛顿第二定律",
                3,
            ),
            (
                "kn-work",
                "kn-mechanics",
                "M.W",
                "功和能",
                2,
            ),
            (
                "kn-kinematics",
                "kn-mechanics",
                "M.K",
                "匀变速直线运动",
                2,
            ),
        ]
        for node_id, parent_id, code, name, level in legacy_nodes:
            self.conn.execute(
                """
                insert or ignore into knowledge_nodes(
                    id, school_id, ontology_version_id, parent_id,
                    stable_code, name, node_type, level, aliases,
                    description, textbook_scope, source, enabled,
                    version, change_note
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    node_id,
                    "school-demo",
                    ontology_id,
                    parent_id,
                    code,
                    name,
                    "knowledge",
                    level,
                    "",
                    "",
                    "",
                    "旧演示",
                    1,
                    1,
                    "",
                ),
            )
        legacy_abilities = [
            ("ab-modeling", "A.OLD.MODEL", "情境建模"),
            ("ab-force", "A.OLD.FORCE", "受力分析"),
            ("ab-equation", "A.OLD.EQUATION", "方程建立"),
            ("ab-calc", "A.OLD.CALC", "数学运算"),
        ]
        for ability_id, code, name in legacy_abilities:
            self.conn.execute(
                """
                insert or ignore into ability_tags(
                    id, school_id, ontology_version_id, stable_code,
                    name, description, source, enabled, version
                ) values(?,?,?,?,?,?,?,?,?)
                """,
                (
                    ability_id,
                    "school-demo",
                    ontology_id,
                    code,
                    name,
                    "",
                    "旧演示",
                    1,
                    1,
                ),
            )
        self.conn.executemany(
            """
            insert or ignore into question_tags(
                id, school_id, question_id, tag_type, tag_id,
                ontology_version_id, source, confirmed_by,
                confidence, rationale
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    "legacy-q1-kn",
                    "school-demo",
                    "q-newton-1",
                    "knowledge",
                    "kn-newton-2",
                    ontology_id,
                    "teacher",
                    "user-teacher-li",
                    1.0,
                    "旧正式标签",
                ),
                (
                    "legacy-q1-ab",
                    "school-demo",
                    "q-newton-1",
                    "ability",
                    "ab-force",
                    ontology_id,
                    "teacher",
                    "user-teacher-li",
                    1.0,
                    "旧正式标签",
                ),
            ],
        )
        snapshot_before = self.conn.execute(
            """
            select tag_snapshot_json
            from question_version_snapshots
            where id = 'snap-q2'
            """
        ).fetchone()["tag_snapshot_json"]

        install_default_taxonomy(
            self.conn,
            "school-demo",
            "user-admin",
            publish=True,
        )

        migrated = {
            row["tag_type"]: row["tag_id"]
            for row in self.conn.execute(
                """
                select tag_type, tag_id from question_tags
                where id in ('legacy-q1-kn', 'legacy-q1-ab')
                """
            ).fetchall()
        }
        snapshot_after = self.conn.execute(
            """
            select tag_snapshot_json
            from question_version_snapshots
            where id = 'snap-q2'
            """
        ).fetchone()["tag_snapshot_json"]
        self.assertEqual(
            migrated,
            {
                "knowledge": "kn-pep2019-r1-c04-s03",
                "ability": "ab-force-analysis",
            },
        )
        self.assertEqual(snapshot_after, snapshot_before)
        self.assertEqual(
            self.conn.execute(
                """
                select count(*) from taxonomy_replacements
                where school_id = 'school-demo'
                """
            ).fetchone()[0],
            9,
        )
        self.assertEqual(
            self.conn.execute(
                """
                select count(*) from knowledge_nodes
                where id in (
                    'kn-mechanics', 'kn-newton', 'kn-newton-2',
                    'kn-work', 'kn-kinematics'
                ) and enabled = 0
                """
            ).fetchone()[0],
            5,
        )


if __name__ == "__main__":
    unittest.main()
