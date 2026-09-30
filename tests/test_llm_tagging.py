import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from highschoolphysics.auth import AuthService
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.document_worker import run_once
from highschoolphysics.llm import generate_model_candidate_tags
from highschoolphysics.repository import PhysicsRepository


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self.body


class LLMTaggingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmpdir.name) / "llm.sqlite3")
        initialize_database(self.conn)
        seed_demo_data(self.conn)
        self.repo = PhysicsRepository(self.conn)
        self.teacher = AuthService(self.conn).login("teacher_li", "teacher123", "llm-test").user

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_openai_compatible_response_is_limited_to_active_taxonomy_ids(self):
        question = self.repo.get_question("q-newton-1")
        knowledge = self.repo.knowledge_nodes()
        ability = self.repo.ability_tags()
        literacy = self.repo.literacy_tags()
        response = {
            "model": "unit-model",
            "choices": [{"message": {"content": json.dumps({
                "knowledge_tags": [{"id": knowledge[0]["id"], "confidence": 0.91, "rationale": "题目明确使用该力学规律。"}],
                "ability_tags": [{"id": ability[0]["id"], "confidence": 0.84, "rationale": "需要分析研究对象的受力。"}],
                "literacy_tags": [{"id": literacy[0]["id"], "confidence": 0.78, "rationale": "解题中需要建立物理模型。"}],
            }, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 220, "completion_tokens": 90},
        }
        request_capture = {}

        def fake_open(request, timeout):
            request_capture["url"] = request.full_url
            request_capture["authorization"] = request.get_header("Authorization")
            request_capture["timeout"] = timeout
            request_capture["body"] = json.loads(request.data.decode("utf-8"))
            return _Response(json.dumps(response).encode("utf-8"))

        with patch("highschoolphysics.llm.url_request.urlopen", side_effect=fake_open):
            result = generate_model_candidate_tags(
                question,
                knowledge,
                ability,
                literacy,
                self.repo.first_active_ontology_id(),
                {"api_endpoint": "https://llm.example.test/v1", "model_name": "unit-model"},
                "private-test-key",
            )

        self.assertEqual(request_capture["url"], "https://llm.example.test/v1/chat/completions")
        self.assertEqual(request_capture["authorization"], "Bearer private-test-key")
        self.assertEqual(request_capture["body"]["model"], "unit-model")
        self.assertIn("指令式语句", json.dumps(request_capture["body"]["messages"][0], ensure_ascii=False))
        self.assertEqual(result["knowledge_tags"][0]["id"], knowledge[0]["id"])
        self.assertEqual(result["ability_tags"][0]["id"], ability[0]["id"])
        self.assertEqual(result["literacy_tags"][0]["id"], literacy[0]["id"])
        self.assertEqual((result["input_units"], result["output_units"]), (220, 90))

    def test_openai_compatible_output_cannot_invent_tag_ids(self):
        question = self.repo.get_question("q-newton-1")
        output = {"knowledge_tags": [{"id": "made-up", "confidence": 1, "rationale": "不存在。"}]}
        response = {"choices": [{"message": {"content": json.dumps(output)}}]}
        with patch(
            "highschoolphysics.llm.url_request.urlopen",
            return_value=_Response(json.dumps(response).encode("utf-8")),
        ):
            with self.assertRaisesRegex(Exception, "未启用或重复"):
                generate_model_candidate_tags(
                    question,
                    self.repo.knowledge_nodes(),
                    self.repo.ability_tags(),
                    self.repo.literacy_tags(),
                    self.repo.first_active_ontology_id(),
                    {"api_endpoint": "https://llm.example.test/v1", "model_name": "unit-model"},
                    "private-test-key",
                )

    def test_new_imports_do_not_auto_apply_rule_candidates_without_an_enabled_provider(self):
        before = self.conn.execute(
            "select count(*) from question_tags where question_id='q-newton-1'"
        ).fetchone()[0]
        result = self.repo.auto_tag_question(self.teacher["id"], "q-newton-1")
        after = self.conn.execute(
            "select count(*) from question_tags where question_id='q-newton-1'"
        ).fetchone()[0]
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "llm_provider_not_configured")
        self.assertEqual(after, before)

    def test_enabled_provider_auto_confirms_validated_knowledge_ability_and_literacy_tags(self):
        config = self.repo.save_provider_config(
            actor_id="user-admin",
            provider_kind="llm",
            provider_name="Test OpenAI Compatible",
            model_name="unit-model",
            secret="private-test-key",
            api_endpoint="https://llm.example.test/v1",
            enabled=True,
            daily_call_limit=10,
        )

        def model_result(question, knowledge, ability, literacy, ontology, provider, api_key):
            return {
                "knowledge_tags": [{"id": knowledge[0]["id"], "name": knowledge[0]["name"], "stable_code": knowledge[0].get("stable_code", ""), "confidence": 0.91, "rationale": "核心规律直接决定受力与运动关系。"}],
                "ability_tags": [{"id": ability[0]["id"], "name": ability[0]["name"], "stable_code": ability[0].get("stable_code", ""), "confidence": 0.84, "rationale": "需要从情境抽取对象并分析受力。"}],
                "literacy_tags": [{"id": literacy[0]["id"], "name": literacy[0]["name"], "stable_code": literacy[0].get("stable_code", ""), "confidence": 0.78, "rationale": "题目要求用模型解释物理过程。"}],
                "prompt_version": "physics-tri-family-tags-v1",
                "model_version": "unit-model",
                "cache_key": "unit-model-q-newton-1",
                "input_units": 220,
                "output_units": 90,
                "request_input_units": 400,
                "request_output_units": 1200,
            }

        with patch("highschoolphysics.repository.generate_model_candidate_tags", side_effect=model_result) as model_call:
            result = self.repo.auto_tag_question(self.teacher["id"], "q-newton-1")

        self.assertEqual(result["status"], "tagged")
        self.assertEqual(result["tag_counts"], {"knowledge_tags": 1, "ability_tags": 1, "literacy_tags": 1})
        model_call.assert_called_once()
        rows = self.conn.execute(
            "select tag_type,source,confidence,rationale from question_tags where question_id='q-newton-1' order by tag_type"
        ).fetchall()
        self.assertEqual({row["tag_type"] for row in rows}, {"knowledge", "ability", "literacy"})
        self.assertTrue(all(row["source"] == "llm_auto" for row in rows))
        candidate_status = self.conn.execute(
            "select status from question_tag_candidates where id=?", (result["candidate_id"],)
        ).fetchone()[0]
        self.assertEqual(candidate_status, "auto_approved")
        usage = self.conn.execute(
            "select request_type,outcome,input_units,output_units from provider_usage_events where provider_config_id=?",
            (config["id"],),
        ).fetchone()
        self.assertEqual(tuple(usage), ("question_tagging", "success", 220, 90))

    def test_background_worker_processes_durable_tag_job_without_blocking_import(self):
        db_path = Path(self.tmpdir.name) / "llm.sqlite3"
        question = self.repo.get_question("q-newton-1")
        self.conn.execute(
            """insert into question_tag_jobs(
                 id,school_id,question_id,requested_by,question_version,source,status
               ) values(?,?,?,?,?,'document_import','queued')""",
            ("tagjob-test-1", question["school_id"], question["id"], self.teacher["id"], question["version"]),
        )
        self.conn.commit()
        with patch.object(
            PhysicsRepository,
            "auto_tag_question",
            return_value={"question_id": "q-newton-1", "status": "tagged"},
        ) as model_tag:
            result = run_once(db_path)
        model_tag.assert_called_once_with(self.teacher["id"], "q-newton-1")
        self.assertEqual(result["status"], "completed")
        job = self.conn.execute(
            "select status,candidate_id,result_json from question_tag_jobs where id='tagjob-test-1'"
        ).fetchone()
        self.assertEqual((job["status"], job["candidate_id"]), ("completed", None))
        self.assertEqual(json.loads(job["result_json"])["status"], "tagged")


if __name__ == "__main__":
    unittest.main()
