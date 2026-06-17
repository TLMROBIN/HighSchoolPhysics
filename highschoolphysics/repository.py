import json
from pathlib import Path
import uuid

from . import backup
from . import taxonomy
from .assessment import (
    default_export_options,
    generate_answer_card_template,
    normalize_ocr_items,
)
from .auth import AuthService
from .errors import InvalidRequest, PermissionDenied, ResourceNotFound, StateConflict
from .exporting import build_wrong_book_html
from .grading import grade_answer
from .llm import generate_candidate_tags
from .mastery import (
    blank_answer,
    classify_mastery,
    mastery_css_class,
    normalize_snapshot_tags,
)
from .ocr import run_paddleocr
from .parsing import PARSER_VERSION, ParseAdapterError, run_parser
from .pdf_export import write_pdf_artifact
from .providers import (
    ProviderSecretStore,
    budget_status,
    estimate_cost_cents,
    mask_secret,
)
from .runtime import check_runtime_capabilities
from .sso import (
    build_oidc_authorization_url,
    create_oidc_login_state,
    normalize_oidc_claims,
)


def row_to_dict(row):
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows):
    return [row_to_dict(row) for row in rows]


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def loads(value, default=None):
    if value is None or value == "":
        return default
    return json.loads(value)


class PhysicsRepository:
    def __init__(self, conn):
        self.conn = conn

    def _actor(self, actor_id):
        user = AuthService(self.conn).user_by_id(actor_id)
        if user is None:
            raise PermissionDenied("Authentication required")
        return user

    def _require(self, actor_id, operation, resource, scope_id):
        user = self._actor(actor_id)
        if not AuthService(self.conn).can(
            user,
            operation,
            resource,
            scope_id,
        ):
            raise PermissionDenied("You do not have access to this resource")
        return user

    def _require_question_bank_actor(self, actor_id):
        user = self._actor(actor_id)
        if user["role"] not in ("teacher", "admin"):
            raise PermissionDenied("Teacher or admin access required")
        return user

    def _require_admin_actor(self, actor_id):
        user = self._actor(actor_id)
        if user["role"] != "admin":
            raise PermissionDenied("Admin role required")
        return user

    def audit(self, actor_id, action, resource_type, resource_id=None, detail=None):
        detail = detail or {}
        school_id = self.school_id_for_actor(actor_id)
        self.conn.execute(
            """
            insert into audit_events(id, school_id, actor_id, action, resource_type, resource_id, detail_json)
            values(?,?,?,?,?,?,?)
            """,
            (
                "audit-" + uuid.uuid4().hex,
                school_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                dumps(detail),
            ),
        )

    def school_id_for_actor(self, actor_id):
        if actor_id:
            row = self.conn.execute("select school_id from users where id = ?", (actor_id,)).fetchone()
            if row:
                return row["school_id"]
        row = self.conn.execute("select id from schools limit 1").fetchone()
        return row["id"] if row else "school-demo"

    def first_active_ontology_id(self):
        row = self.conn.execute(
            "select id from knowledge_ontology_versions where status = 'active' order by created_at desc limit 1"
        ).fetchone()
        return row["id"]

    def active_ontology_version(self):
        return row_to_dict(
            self.conn.execute(
                """
                select *
                from knowledge_ontology_versions
                where status = 'active'
                order by created_at desc
                limit 1
                """
            ).fetchone()
        )

    def first_mastery_inference_version_id(self):
        row = self.conn.execute(
            """
            select id
            from mastery_inference_versions
            where status = 'active'
            order by created_at desc
            limit 1
            """
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                """
                select id
                from mastery_inference_versions
                order by created_at desc, id
                limit 1
                """
            ).fetchone()
        return row["id"]

    def deterministic_mastery_inference_version_id(self):
        row = self.conn.execute(
            """
            select id
            from mastery_inference_versions
            where id = 'mastery-deterministic-v1'
            limit 1
            """
        ).fetchone()
        if row is not None:
            return row["id"]
        return self.first_mastery_inference_version_id()

    def ontology_versions(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select *
                from knowledge_ontology_versions
                order by created_at desc, version_label desc
                """
            ).fetchall()
        )

    def _question_payload(self, row):
        question = row_to_dict(row)
        if question:
            question["options"] = loads(question.pop("options_json"), {})
            question["answer"] = loads(question.pop("answer_json"), None)
            question["media"] = loads(question.pop("media_json"), [])
        return question

    def get_question(self, question_id):
        row = self.conn.execute(
            """
            select q.*, op.title as original_paper_title
            from questions q
            left join original_papers op on op.id = q.original_paper_id
            where q.id = ?
            """,
            (question_id,),
        ).fetchone()
        return self._question_payload(row)

    def create_question(
        self,
        actor_id,
        stem,
        options,
        answer,
        analysis,
        question_type,
        source,
        grade,
        chapter,
        difficulty,
        media=None,
        scenario="",
        quality_status="draft",
        notes="",
        original_paper_id=None,
        import_batch_id=None,
        parser_task_id=None,
        original_page=None,
        original_question_number="",
        source_school="",
        source_publisher="",
        exam_type="",
        source_confidence=1.0,
        review_status="confirmed",
    ):
        actor = self._require_question_bank_actor(actor_id)
        question_id = "q-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into questions(
                id, school_id, stem, options_json, answer_json, analysis,
                question_type, source, grade, chapter, difficulty, media_json,
                scenario, quality_status, notes, version, original_paper_id,
                import_batch_id, parser_task_id, original_page,
                original_question_number, source_school, source_publisher,
                exam_type, source_confidence, review_status
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                question_id,
                actor["school_id"],
                stem,
                dumps(options or {}),
                dumps(answer),
                analysis or "",
                question_type,
                source,
                grade,
                chapter,
                difficulty,
                dumps(media or []),
                scenario or "",
                quality_status,
                notes or "",
                1,
                original_paper_id,
                import_batch_id,
                parser_task_id,
                original_page,
                original_question_number or "",
                source_school or "",
                source_publisher or "",
                exam_type or "",
                float(source_confidence if source_confidence is not None else 1.0),
                review_status,
            ),
        )
        self.audit(
            actor_id,
            "question_created",
            "question",
            question_id,
            {
                "source": source,
                "original_paper_id": original_paper_id,
                "import_batch_id": import_batch_id,
            },
        )
        self.conn.commit()
        return self.get_question(question_id)

    def update_question(
        self,
        actor_id,
        question_id,
        stem,
        options,
        answer,
        analysis,
        question_type,
        source,
        grade,
        chapter,
        difficulty,
        media=None,
        scenario="",
        quality_status="draft",
        notes="",
        review_status=None,
        source_confidence=None,
    ):
        self._require_question_bank_actor(actor_id)
        existing = self.conn.execute(
            "select * from questions where id = ?",
            (question_id,),
        ).fetchone()
        if existing is None:
            raise ResourceNotFound("Question not found: %s" % question_id)
        self.conn.execute(
            """
            update questions
            set stem = ?, options_json = ?, answer_json = ?, analysis = ?,
                question_type = ?, source = ?, grade = ?, chapter = ?,
                difficulty = ?, media_json = ?, scenario = ?,
                quality_status = ?, notes = ?,
                review_status = coalesce(?, review_status),
                source_confidence = coalesce(?, source_confidence),
                version = version + 1
            where id = ?
            """,
            (
                stem,
                dumps(options or {}),
                dumps(answer),
                analysis or "",
                question_type,
                source,
                grade,
                chapter,
                difficulty,
                dumps(media or []),
                scenario or "",
                quality_status,
                notes or "",
                review_status,
                source_confidence,
                question_id,
            ),
        )
        self.audit(
            actor_id,
            "question_updated",
            "question",
            question_id,
            {"previous_version": existing["version"]},
        )
        self.conn.commit()
        return self.get_question(question_id)

    def _require_assessment_class_actor(self, actor_id, class_id):
        actor = self._require_question_bank_actor(actor_id)
        if actor["role"] == "admin":
            return actor
        row = self.conn.execute(
            """
            select 1
            from teacher_classes
            where teacher_id = ? and class_id = ? and subject = 'physics'
            """,
            (actor["id"], class_id),
        ).fetchone()
        if row is None:
            raise PermissionDenied("You do not have access to this class")
        return actor

    def students_for_class(self, class_id):
        return rows_to_dicts(
            self.conn.execute(
                """
                select *
                from users
                where class_id = ?
                  and role = 'student'
                  and status = 'active'
                order by student_no, id
                """,
                (class_id,),
            ).fetchall()
        )

    def assemble_paper(self, actor_id, title, source, question_items):
        actor = self._require_question_bank_actor(actor_id)
        if not question_items:
            raise ValueError("Paper requires at least one question")
        paper_id = "paper-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into papers(id, school_id, title, source, status)
            values(?,?,?,?,?)
            """,
            (paper_id, actor["school_id"], title, source, "reviewed"),
        )
        assembled = []
        for position, item in enumerate(question_items, start=1):
            question = self.get_question(item["question_id"])
            if question is None:
                raise ResourceNotFound(
                    "Question not found: %s" % item["question_id"]
                )
            points = int(item["points"])
            self.conn.execute(
                """
                insert into paper_questions(
                    paper_id, question_id, position, points
                ) values(?,?,?,?)
                """,
                (paper_id, item["question_id"], position, points),
            )
            assembled.append(
                {
                    "question_id": item["question_id"],
                    "position": position,
                    "points": points,
                }
            )
        self.audit(
            actor_id,
            "paper_assembled",
            "paper",
            paper_id,
            {"question_count": len(assembled)},
        )
        self.conn.commit()
        return {
            "paper": row_to_dict(
                self.conn.execute(
                    "select * from papers where id = ?",
                    (paper_id,),
                ).fetchone()
            ),
            "questions": assembled,
        }

    def create_assessment_from_paper(
        self,
        actor_id,
        paper_id,
        class_id,
        title,
        term,
        grade,
        scheduled_at,
    ):
        actor = self._require_assessment_class_actor(actor_id, class_id)
        paper = self.conn.execute(
            "select * from papers where id = ?",
            (paper_id,),
        ).fetchone()
        if paper is None:
            raise ResourceNotFound("Paper not found: %s" % paper_id)
        rows = self.conn.execute(
            """
            select pq.*, q.stem, q.options_json, q.answer_json,
                   q.question_type, q.version
            from paper_questions pq
            join questions q on q.id = pq.question_id
            where pq.paper_id = ?
            order by pq.position
            """,
            (paper_id,),
        ).fetchall()
        if not rows:
            raise ValueError("Paper has no questions")
        assessment_id = "assess-" + uuid.uuid4().hex[:12]
        ontology_id = self.first_active_ontology_id()
        mastery_version_id = self.first_mastery_inference_version_id()
        snapshots = []
        full_score = 0
        for row in rows:
            answer = loads(row["answer_json"], {})
            if isinstance(answer, dict):
                expected_answer = answer.get("answer", answer)
                match = answer.get("match", "exact")
                tolerance = answer.get("tolerance", 0)
            else:
                expected_answer = answer
                match = "exact"
                tolerance = 0
            rule = {
                "type": row["question_type"],
                "answer": expected_answer,
                "points": row["points"],
                "match": match,
                "tolerance": tolerance,
            }
            snapshot_id = "snap-" + uuid.uuid4().hex[:12]
            snapshots.append(
                {
                    "id": snapshot_id,
                    "question_id": row["question_id"],
                    "position": row["position"],
                    "points": row["points"],
                    "question_type": row["question_type"],
                    "stem": row["stem"],
                    "options_json": row["options_json"],
                    "answer_json": row["answer_json"],
                    "grading_rule_json": dumps(rule),
                    "tag_snapshot_json": dumps(
                        self.tags_for_question(row["question_id"])
                    ),
                    "question_version": row["version"],
                }
            )
            full_score += row["points"]
        template_id = "card-" + uuid.uuid4().hex[:12]
        template = generate_answer_card_template(template_id, title, snapshots)
        self.conn.execute(
            """
            insert into answer_card_templates(
                id, school_id, name, template_json
            ) values(?,?,?,?)
            """,
            (
                template_id,
                actor["school_id"],
                template["name"],
                dumps(template),
            ),
        )
        self.conn.execute(
            """
            insert into assessment_sessions(
                id, school_id, title, term, grade, class_id, scheduled_at,
                source, full_score, paper_id, answer_card_template_id,
                ontology_version_id, mastery_inference_version_id, status,
                grading_status, statistics_status
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                assessment_id,
                actor["school_id"],
                title,
                term,
                grade,
                class_id,
                scheduled_at,
                paper["source"],
                full_score,
                paper_id,
                template_id,
                ontology_id,
                mastery_version_id,
                "待扫描",
                "not_started",
                "not_started",
            ),
        )
        for student in self.students_for_class(class_id):
            self.conn.execute(
                """
                insert into assessment_participants(
                    assessment_id, student_id, status
                ) values(?,?,?)
                """,
                (assessment_id, student["id"], "present"),
            )
        for snapshot in snapshots:
            self.conn.execute(
                """
                insert into question_version_snapshots(
                    id, assessment_id, question_id, position, points, stem,
                    options_json, answer_json, grading_rule_json,
                    tag_snapshot_json, question_version, ontology_version_id
                ) values(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot["id"],
                    assessment_id,
                    snapshot["question_id"],
                    snapshot["position"],
                    snapshot["points"],
                    snapshot["stem"],
                    snapshot["options_json"],
                    snapshot["answer_json"],
                    snapshot["grading_rule_json"],
                    snapshot["tag_snapshot_json"],
                    snapshot["question_version"],
                    ontology_id,
                ),
            )
        self.audit(
            actor_id,
            "assessment_created_from_paper",
            "assessment",
            assessment_id,
            {"paper_id": paper_id},
        )
        self.conn.commit()
        return self.assessment_detail(actor_id, assessment_id)

    def import_ocr_responses(
        self,
        actor_id,
        assessment_id,
        source_name,
        recognizer,
        recognizer_version,
        items,
    ):
        assessment = self.assessment_detail(
            actor_id,
            assessment_id,
            operation="grade",
        )
        batch_id = "scan-" + uuid.uuid4().hex[:12]
        normalized = normalize_ocr_items(items)
        low_count = sum(
            1 for item in normalized if item["review_status"] == "required"
        )
        self.conn.execute(
            """
            insert into scan_batches(
                id, school_id, assessment_id, source_name, recognizer,
                recognizer_version, status, low_confidence_count
            ) values(?,?,?,?,?,?,?,?)
            """,
            (
                batch_id,
                assessment["school_id"],
                assessment_id,
                source_name,
                recognizer,
                recognizer_version,
                "needs_review" if low_count else "imported",
                low_count,
            ),
        )
        snapshot_rows = self.conn.execute(
            """
            select id, question_id
            from question_version_snapshots
            where assessment_id = ?
            """,
            (assessment_id,),
        ).fetchall()
        snapshots = {row["question_id"]: row["id"] for row in snapshot_rows}
        responses = []
        for item in normalized:
            if item["question_id"] not in snapshots:
                raise ResourceNotFound(
                    "Snapshot not found for question: %s" % item["question_id"]
                )
            response_id = "resp-" + uuid.uuid4().hex[:12]
            self.conn.execute(
                """
                insert into student_responses(
                    id, school_id, assessment_id, scan_batch_id, student_id,
                    question_id, snapshot_id, raw_answer, final_answer,
                    original_confidence, review_status, review_reason,
                    ocr_payload_json
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
                on conflict(assessment_id, student_id, question_id)
                do update set scan_batch_id = excluded.scan_batch_id,
                              raw_answer = excluded.raw_answer,
                              final_answer = excluded.final_answer,
                              original_confidence = excluded.original_confidence,
                              review_status = excluded.review_status,
                              review_reason = excluded.review_reason,
                              ocr_payload_json = excluded.ocr_payload_json,
                              updated_at = current_timestamp
                """,
                (
                    response_id,
                    assessment["school_id"],
                    assessment_id,
                    batch_id,
                    item["student_id"],
                    item["question_id"],
                    snapshots[item["question_id"]],
                    item["answer"],
                    item["answer"],
                    item["confidence"],
                    item["review_status"],
                    item["review_reason"],
                    dumps(item["raw"]),
                ),
            )
            stored = self.conn.execute(
                """
                select *
                from student_responses
                where assessment_id = ?
                  and student_id = ?
                  and question_id = ?
                """,
                (assessment_id, item["student_id"], item["question_id"]),
            ).fetchone()
            responses.append(row_to_dict(stored))
        self.audit(
            actor_id,
            "ocr_responses_imported",
            "assessment",
            assessment_id,
            {
                "scan_batch_id": batch_id,
                "low_confidence_count": low_count,
            },
        )
        self.conn.commit()
        batch = row_to_dict(
            self.conn.execute(
                "select * from scan_batches where id = ?",
                (batch_id,),
            ).fetchone()
        )
        batch["responses"] = responses
        return batch

    def import_paddleocr_scan(
        self,
        actor_id,
        assessment_id,
        source_name,
        image_paths,
        ocr_runner=None,
    ):
        recognitions = run_paddleocr(image_paths, runner=ocr_runner)
        items = []
        for recognition in recognitions:
            if not recognition.get("student_id") or not recognition.get("question_id"):
                raise ValueError(
                    "PaddleOCR recognition requires student_id and question_id"
                )
            items.append(
                {
                    "student_id": recognition["student_id"],
                    "question_id": recognition["question_id"],
                    "answer": recognition["text"],
                    "confidence": recognition["confidence"],
                    "bbox": recognition.get("bbox") or [],
                    "source_path": recognition.get("source_path", ""),
                    "raw_paddleocr": recognition,
                }
            )
        return self.import_ocr_responses(
            actor_id=actor_id,
            assessment_id=assessment_id,
            source_name=source_name,
            recognizer="PaddleOCR",
            recognizer_version="local-adapter-v1",
            items=items,
        )

    def search_questions(self, actor_id, filters=None):
        actor = self._require_question_bank_actor(actor_id)
        filters = filters or {}
        clauses = ["q.school_id = ?"]
        params = [actor["school_id"]]
        simple_filters = {
            "grade": "q.grade",
            "chapter": "q.chapter",
            "difficulty": "q.difficulty",
            "quality_status": "q.quality_status",
            "review_status": "q.review_status",
            "original_paper_id": "q.original_paper_id",
            "import_batch_id": "q.import_batch_id",
        }
        for key, column in simple_filters.items():
            value = filters.get(key)
            if value:
                clauses.append("%s = ?" % column)
                params.append(value)
        if filters.get("source_confidence_max") is not None:
            clauses.append("q.source_confidence <= ?")
            params.append(float(filters["source_confidence_max"]))
        if filters.get("tag_type") and filters.get("tag_id"):
            clauses.append(
                """
                exists (
                    select 1
                    from question_tags qt
                    where qt.question_id = q.id
                      and qt.enabled = 1
                      and qt.tag_type = ?
                      and qt.tag_id = ?
                )
                """
            )
            params.extend([filters["tag_type"], filters["tag_id"]])
        rows = self.conn.execute(
            """
            select q.*, op.title as original_paper_title
            from questions q
            left join original_papers op on op.id = q.original_paper_id
            where %s
            order by q.created_at desc, q.id
            """
            % " and ".join(clauses),
            params,
        ).fetchall()
        questions = [self._question_payload(row) for row in rows]
        for question in questions:
            tags = self.tags_for_question(question["id"])
            question["tags"] = tags
            question["knowledge_tag_ids"] = [
                tag["tag_id"] for tag in tags if tag["tag_type"] == "knowledge"
            ]
            question["ability_tag_ids"] = [
                tag["tag_id"] for tag in tags if tag["tag_type"] == "ability"
            ]
            question["literacy_tag_ids"] = [
                tag["tag_id"] for tag in tags if tag["tag_type"] == "literacy"
            ]
        return questions

    def create_parse_task(
        self,
        actor_id,
        paper_title,
        document_name,
        source_text,
        parser_mode="deterministic_text",
        fallback_policy="fail_closed",
        source_school="",
        source_publisher="",
        exam_type="",
        grade="",
        term="",
    ):
        actor = self._require_question_bank_actor(actor_id)
        original_paper_id = "paper-origin-" + uuid.uuid4().hex[:12]
        batch_id = "import-" + uuid.uuid4().hex[:12]
        task_id = "parse-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into original_papers(
                id, school_id, title, document_name, source_school,
                source_publisher, exam_type, grade, term, status, created_by
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                original_paper_id,
                actor["school_id"],
                paper_title,
                document_name,
                source_school or "",
                source_publisher or "",
                exam_type or "",
                grade or "",
                term or "",
                "active",
                actor_id,
            ),
        )
        self.conn.execute(
            """
            insert into question_import_batches(
                id, school_id, original_paper_id, source_file_name,
                parser_mode, status, created_by
            ) values(?,?,?,?,?,?,?)
            """,
            (
                batch_id,
                actor["school_id"],
                original_paper_id,
                document_name,
                parser_mode,
                "queued",
                actor_id,
            ),
        )
        self.conn.execute(
            """
            insert into document_parse_tasks(
                id, school_id, file_name, parser, parser_version, status,
                output_json, failure_reason, original_paper_id,
                import_batch_id, parser_mode, fallback_policy, source_text
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                task_id,
                actor["school_id"],
                document_name,
                parser_mode,
                PARSER_VERSION,
                "queued",
                "{}",
                "",
                original_paper_id,
                batch_id,
                parser_mode,
                fallback_policy,
                source_text or "",
            ),
        )
        self.audit(
            actor_id,
            "parse_task_created",
            "document_parse_task",
            task_id,
            {"original_paper_id": original_paper_id, "import_batch_id": batch_id},
        )
        self.conn.commit()
        return {
            "id": task_id,
            "original_paper_id": original_paper_id,
            "import_batch_id": batch_id,
            "status": "queued",
        }

    def _parser_config(self, school_id, parser_mode):
        row = self.conn.execute(
            """
            select *
            from document_parser_configs
            where school_id = ? and parser_mode = ? and enabled = 1
            """,
            (school_id, parser_mode),
        ).fetchone()
        config = {}
        if row is not None:
            config = loads(row["config_json"], {})
            if row["command_path"]:
                config["command_path"] = row["command_path"]
            if row["api_endpoint"]:
                config["api_endpoint"] = row["api_endpoint"]
        if parser_mode == "mineru_api":
            provider = self.conn.execute(
                """
                select *
                from provider_configs
                where school_id = ?
                  and provider_kind = 'mineru_api'
                  and enabled = 1
                order by updated_at desc, created_at desc
                limit 1
                """,
                (school_id,),
            ).fetchone()
            if provider is not None:
                config["api_endpoint"] = provider["api_endpoint"]
                config["provider_config_id"] = provider["id"]
                if provider["secret_ciphertext"]:
                    config["api_token"] = self._provider_secret_store().decrypt(
                        provider["secret_ciphertext"]
                    )
        return config

    def _parsed_item_payload(self, row):
        item = row_to_dict(row)
        if item:
            item["options"] = loads(item.pop("options_json"), {})
            item["answer"] = loads(item.pop("answer_json"), {})
            item["answer_area"] = loads(item.pop("answer_area_json"), {})
            item["media"] = loads(item.pop("media_json"), [])
            item["coordinates"] = loads(item.pop("coordinates_json"), {})
            item["warnings"] = loads(item.pop("warnings_json"), [])
        return item

    def run_parse_task(self, actor_id, task_id):
        self._require_question_bank_actor(actor_id)
        task = self.conn.execute(
            "select * from document_parse_tasks where id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ResourceNotFound("Parse task not found: %s" % task_id)
        self.conn.execute(
            """
            update document_parse_tasks
            set status = 'running', failure_reason = ''
            where id = ?
            """,
            (task_id,),
        )
        self.conn.execute(
            """
            update question_import_batches
            set status = 'running', failure_reason = ''
            where id = ?
            """,
            (task["import_batch_id"],),
        )
        self.conn.commit()
        try:
            parser_config = self._parser_config(
                task["school_id"],
                task["parser_mode"],
            )
            provider_config_id = parser_config.get("provider_config_id")
            if provider_config_id:
                budget = self.provider_budget_status(
                    actor_id,
                    provider_config_id,
                    input_units=len(task["source_text"] or ""),
                    output_units=0,
                )
                if not budget["allowed"]:
                    self.record_provider_usage(
                        actor_id=actor_id,
                        provider_config_id=provider_config_id,
                        request_type="document_parse",
                        prompt_version=task["parser_version"] or PARSER_VERSION,
                        input_units=len(task["source_text"] or ""),
                        output_units=0,
                        outcome="blocked",
                        error_category=budget["reason"],
                        estimated_cost_cents=budget["estimated_cost_cents"],
                        detail={"parser_task_id": task_id},
                    )
                    raise ParseAdapterError(
                        "Provider budget blocked: %s" % budget["reason"]
                    )
            parsed = run_parser(
                task["parser_mode"],
                task["source_text"],
                parser_version=task["parser_version"] or PARSER_VERSION,
                config=parser_config,
                fallback_policy=task["fallback_policy"],
            )
        except ParseAdapterError as error:
            self.conn.execute(
                """
                update document_parse_tasks
                set status = 'failed', failure_reason = ?
                where id = ?
                """,
                (str(error), task_id),
            )
            self.conn.execute(
                """
                update question_import_batches
                set status = 'failed', failure_reason = ?
                where id = ?
                """,
                (str(error), task["import_batch_id"]),
            )
            self.audit(
                actor_id,
                "parse_task_failed",
                "document_parse_task",
                task_id,
                {"failure_reason": str(error)},
            )
            self.conn.commit()
            raise
        self.conn.execute(
            "delete from parsed_question_items where parse_task_id = ?",
            (task_id,),
        )
        inserted_items = []
        for item in parsed["items"]:
            item_id = "parsed-" + uuid.uuid4().hex[:12]
            self.conn.execute(
                """
                insert into parsed_question_items(
                    id, school_id, parse_task_id, import_batch_id,
                    item_index, page_number, question_number, stem,
                    question_type, options_json, answer_json, analysis,
                    answer_area_json, media_json, coordinates_json, confidence,
                    parser_name, parser_version, review_status, warnings_json
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item_id,
                    task["school_id"],
                    task_id,
                    task["import_batch_id"],
                    item["item_index"],
                    item.get("page_number"),
                    item.get("question_number", ""),
                    item.get("stem", ""),
                    item.get("question_type", "short_answer"),
                    dumps(item.get("options") or {}),
                    dumps(item.get("answer") or {}),
                    item.get("analysis") or "",
                    dumps(item.get("answer_area") or {}),
                    dumps(item.get("media") or []),
                    dumps(item.get("coordinates") or {}),
                    float(item.get("confidence") or 0.0),
                    item.get("parser_name") or parsed["parser_name"],
                    item.get("parser_version") or parsed["parser_version"],
                    item.get("review_status") or "needs_review",
                    dumps(item.get("warnings") or []),
                ),
            )
            inserted_items.append(
                self._parsed_item_payload(
                    self.conn.execute(
                        "select * from parsed_question_items where id = ?",
                        (item_id,),
                    ).fetchone()
                )
            )
        status = "parsed" if inserted_items else "failed"
        failure_reason = "" if inserted_items else "No questions parsed"
        self.conn.execute(
            """
            update document_parse_tasks
            set status = ?, output_json = ?, failure_reason = ?
            where id = ?
            """,
            (status, dumps(parsed), failure_reason, task_id),
        )
        self.conn.execute(
            """
            update question_import_batches
            set status = ?, item_count = ?, failure_reason = ?
            where id = ?
            """,
            (status, len(inserted_items), failure_reason, task["import_batch_id"]),
        )
        self.audit(
            actor_id,
            "parse_task_completed",
            "document_parse_task",
            task_id,
            {"item_count": len(inserted_items), "status": status},
        )
        if provider_config_id:
            self.record_provider_usage(
                actor_id=actor_id,
                provider_config_id=provider_config_id,
                request_type="document_parse",
                prompt_version=parsed.get("parser_version") or PARSER_VERSION,
                input_units=len(task["source_text"] or ""),
                output_units=len(dumps(parsed)),
                page_count=len(
                    {
                        item.get("page_number")
                        for item in parsed["items"]
                        if item.get("page_number") is not None
                    }
                ),
                outcome="success" if inserted_items else "failed",
                error_category="" if inserted_items else "empty_output",
                detail={"parser_task_id": task_id},
            )
        self.conn.commit()
        return {"id": task_id, "status": status, "items": inserted_items}

    def parsed_question_items(self, actor_id, import_batch_id=None, status=None):
        actor = self._require_question_bank_actor(actor_id)
        clauses = ["school_id = ?"]
        params = [actor["school_id"]]
        if import_batch_id:
            clauses.append("import_batch_id = ?")
            params.append(import_batch_id)
        if status:
            clauses.append("review_status = ?")
            params.append(status)
        rows = self.conn.execute(
            """
            select *
            from parsed_question_items
            where %s
            order by created_at desc, item_index
            """
            % " and ".join(clauses),
            params,
        ).fetchall()
        return [self._parsed_item_payload(row) for row in rows]

    def list_parse_tasks(self, actor_id):
        actor = self._require_question_bank_actor(actor_id)
        rows = self.conn.execute(
            """
            select
                dpt.*,
                op.title as original_paper_title,
                qib.item_count,
                qib.saved_count
            from document_parse_tasks dpt
            left join original_papers op on op.id = dpt.original_paper_id
            left join question_import_batches qib on qib.id = dpt.import_batch_id
            where dpt.school_id = ?
            order by dpt.created_at desc, dpt.id
            """,
            (actor["school_id"],),
        ).fetchall()
        rows = rows_to_dicts(rows)
        for row in rows:
            row["output"] = loads(row.pop("output_json"), {})
        return rows

    def save_parsed_question(self, actor_id, parsed_item_id, overrides=None):
        self._require_question_bank_actor(actor_id)
        overrides = overrides or {}
        row = self.conn.execute(
            """
            select
                pqi.*,
                qib.original_paper_id,
                qib.source_file_name,
                dpt.id as parser_task_id,
                op.title as paper_title,
                op.source_school,
                op.source_publisher,
                op.exam_type,
                op.grade,
                op.term
            from parsed_question_items pqi
            join question_import_batches qib on qib.id = pqi.import_batch_id
            join document_parse_tasks dpt on dpt.id = pqi.parse_task_id
            join original_papers op on op.id = qib.original_paper_id
            where pqi.id = ?
            """,
            (parsed_item_id,),
        ).fetchone()
        if row is None:
            raise ResourceNotFound(
                "Parsed question item not found: %s" % parsed_item_id
            )
        item = self._parsed_item_payload(row)
        question = self.create_question(
            actor_id=actor_id,
            stem=overrides.get("stem", item["stem"]),
            options=overrides.get("options", item["options"]),
            answer=overrides.get("answer", item["answer"]),
            analysis=overrides.get("analysis", item["analysis"]),
            question_type=overrides.get("question_type", item["question_type"]),
            source=overrides.get("source", row["source_file_name"]),
            grade=overrides.get("grade", row["grade"] or ""),
            chapter=overrides.get("chapter", "未归类"),
            difficulty=overrides.get("difficulty", "medium"),
            media=overrides.get("media", item["media"]),
            scenario=overrides.get("scenario", ""),
            quality_status=overrides.get("quality_status", "draft"),
            notes=overrides.get("notes", ""),
            original_paper_id=row["original_paper_id"],
            import_batch_id=row["import_batch_id"],
            parser_task_id=row["parser_task_id"],
            original_page=item["page_number"],
            original_question_number=item["question_number"],
            source_school=row["source_school"],
            source_publisher=row["source_publisher"],
            exam_type=row["exam_type"],
            source_confidence=item["confidence"],
            review_status=overrides.get("review_status", "needs_review"),
        )
        self.conn.execute(
            """
            update parsed_question_items
            set review_status = 'saved', saved_question_id = ?
            where id = ?
            """,
            (question["id"], parsed_item_id),
        )
        self.conn.execute(
            """
            update question_import_batches
            set saved_count = (
                select count(*)
                from parsed_question_items
                where import_batch_id = ? and review_status = 'saved'
            )
            where id = ?
            """,
            (row["import_batch_id"], row["import_batch_id"]),
        )
        self.audit(
            actor_id,
            "parsed_question_saved",
            "parsed_question_item",
            parsed_item_id,
            {"question_id": question["id"]},
        )
        self.conn.commit()
        return self.get_question(question["id"])

    def knowledge_nodes(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select * from knowledge_nodes
                where enabled = 1 and deleted_at is null
                order by level, stable_code
                """
            ).fetchall()
        )

    def all_knowledge_nodes(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select kn.*, parent.name as parent_name
                from knowledge_nodes kn
                left join knowledge_nodes parent on parent.id = kn.parent_id
                where kn.deleted_at is null
                order by kn.level, kn.stable_code
                """
            ).fetchall()
        )

    def knowledge_edges(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select e.*, s.name as source_name, t.name as target_name
                from knowledge_edges e
                join knowledge_nodes s on s.id = e.source_node_id
                join knowledge_nodes t on t.id = e.target_node_id
                where e.enabled = 1 and e.deleted_at is null
                  and s.enabled = 1 and s.deleted_at is null
                  and t.enabled = 1 and t.deleted_at is null
                order by e.relation_type, s.name, t.name
                """
            ).fetchall()
        )

    def all_knowledge_edges(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select e.*, s.name as source_name, t.name as target_name
                from knowledge_edges e
                join knowledge_nodes s on s.id = e.source_node_id
                join knowledge_nodes t on t.id = e.target_node_id
                where e.deleted_at is null
                order by e.relation_type, s.name, t.name
                """
            ).fetchall()
        )

    def knowledge_node_paths(self):
        nodes = {node["id"]: node for node in self.knowledge_nodes()}
        paths = {}

        def build_path(node_id):
            if node_id in paths:
                return paths[node_id]
            node = nodes.get(node_id)
            if not node:
                return []
            prefix = build_path(node["parent_id"]) if node["parent_id"] else []
            paths[node_id] = prefix + [node["name"]]
            return paths[node_id]

        for node_id in nodes:
            build_path(node_id)
        return paths

    def descendant_knowledge_ids(self, node_id):
        nodes = self.knowledge_nodes()
        children = {}
        for node in nodes:
            children.setdefault(node["parent_id"], []).append(node["id"])
        stack = [node_id]
        result = []
        while stack:
            current = stack.pop()
            result.append(current)
            stack.extend(children.get(current, []))
        return result

    def related_questions_for_knowledge(self, node_id):
        node_ids = self.descendant_knowledge_ids(node_id)
        placeholders = ",".join("?" for _ in node_ids)
        if not placeholders:
            return []
        rows = self.conn.execute(
            """
            select distinct q.id, q.stem, q.question_type, q.difficulty, q.chapter
            from questions q
            join question_tags qt on qt.question_id = q.id
            where qt.tag_type = 'knowledge'
              and qt.enabled = 1
              and qt.tag_id in (%s)
            order by q.chapter, q.id
            """
            % placeholders,
            node_ids,
        ).fetchall()
        return rows_to_dicts(rows)

    def _related_questions_for_tag(self, tag_type, tag_id):
        rows = self.conn.execute(
            """
            select distinct
                q.id,
                q.stem,
                q.question_type,
                q.difficulty,
                q.chapter,
                q.grade,
                q.quality_status
            from questions q
            join question_tags qt on qt.question_id = q.id
            where qt.tag_type = ?
              and qt.tag_id = ?
              and qt.enabled = 1
            order by q.grade, q.chapter, q.id
            """,
            (tag_type, tag_id),
        ).fetchall()
        return rows_to_dicts(rows)

    def related_questions_for_ability(self, ability_tag_id):
        return self._related_questions_for_tag("ability", ability_tag_id)

    def related_questions_for_literacy(self, literacy_tag_id):
        return self._related_questions_for_tag("literacy", literacy_tag_id)

    def ability_tags(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select * from ability_tags
                where enabled = 1 and deleted_at is null
                order by stable_code
                """
            ).fetchall()
        )

    def all_ability_tags(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select *
                from ability_tags
                where deleted_at is null
                order by stable_code
                """
            ).fetchall()
        )

    def literacy_tags(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select * from literacy_tags
                where enabled = 1 and deleted_at is null
                order by level, stable_code
                """
            ).fetchall()
        )

    def all_literacy_tags(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select lt.*, parent.name as parent_name
                from literacy_tags lt
                left join literacy_tags parent on parent.id = lt.parent_id
                where lt.deleted_at is null
                order by lt.level, lt.stable_code
                """
            ).fetchall()
        )

    def create_knowledge_node(
        self,
        actor_id,
        stable_code,
        name,
        parent_id=None,
        aliases="",
        source="教师校本",
        node_type="knowledge",
        description="",
        textbook_scope="",
        change_note="",
        enabled=True,
    ):
        ontology_id = self.first_active_ontology_id()
        school_id = self.school_id_for_actor(actor_id)
        level = 1
        if parent_id:
            parent = self.conn.execute("select * from knowledge_nodes where id = ?", (parent_id,)).fetchone()
            if parent is None:
                raise ValueError("Parent knowledge node not found: %s" % parent_id)
            level = parent["level"] + 1
        node_id = "kn-" + uuid.uuid4().hex[:10]
        self.conn.execute(
            """
            insert into knowledge_nodes(
                id, school_id, ontology_version_id, parent_id, stable_code, name,
                node_type, level, aliases, description, textbook_scope, source,
                enabled, version, change_note
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                node_id,
                school_id,
                ontology_id,
                parent_id or None,
                stable_code,
                name,
                node_type,
                level,
                aliases,
                description,
                textbook_scope,
                source,
                1 if enabled else 0,
                1,
                change_note,
            ),
        )
        self.audit(
            actor_id,
            "knowledge_node_created",
            "knowledge_node",
            node_id,
            {"stable_code": stable_code, "parent_id": parent_id, "change_note": change_note},
        )
        self.conn.commit()
        return row_to_dict(self.conn.execute("select * from knowledge_nodes where id = ?", (node_id,)).fetchone())

    def update_knowledge_node(
        self,
        actor_id,
        node_id,
        name,
        aliases="",
        source="",
        description="",
        textbook_scope="",
        change_note="",
    ):
        existing = self.conn.execute("select * from knowledge_nodes where id = ?", (node_id,)).fetchone()
        if existing is None:
            raise ValueError("Knowledge node not found: %s" % node_id)
        self.conn.execute(
            """
            update knowledge_nodes
            set name = ?, aliases = ?, source = ?, description = ?, textbook_scope = ?,
                change_note = ?, version = version + 1
            where id = ?
            """,
            (
                name,
                aliases,
                source or existing["source"],
                description or existing["description"],
                textbook_scope or existing["textbook_scope"],
                change_note,
                node_id,
            ),
        )
        self.audit(
            actor_id,
            "knowledge_node_updated",
            "knowledge_node",
            node_id,
            {
                "old_name": existing["name"],
                "new_name": name,
                "change_note": change_note,
            },
        )
        self.conn.commit()
        return row_to_dict(self.conn.execute("select * from knowledge_nodes where id = ?", (node_id,)).fetchone())

    def set_knowledge_node_enabled(self, actor_id, node_id, enabled, change_note=""):
        existing = self.conn.execute("select * from knowledge_nodes where id = ?", (node_id,)).fetchone()
        if existing is None:
            raise ValueError("Knowledge node not found: %s" % node_id)
        self.conn.execute(
            """
            update knowledge_nodes
            set enabled = ?, change_note = ?, version = version + 1
            where id = ?
            """,
            (1 if enabled else 0, change_note, node_id),
        )
        action = "knowledge_node_restored" if enabled else "knowledge_node_disabled"
        self.audit(actor_id, action, "knowledge_node", node_id, {"change_note": change_note})
        self.conn.commit()
        return row_to_dict(self.conn.execute("select * from knowledge_nodes where id = ?", (node_id,)).fetchone())

    def create_knowledge_edge(
        self,
        actor_id,
        source_node_id,
        target_node_id,
        relation_type,
        bidirectional=True,
        rationale="",
    ):
        for node_id in (source_node_id, target_node_id):
            if self.conn.execute("select 1 from knowledge_nodes where id = ?", (node_id,)).fetchone() is None:
                raise ValueError("Knowledge node not found: %s" % node_id)
        edge_id = "edge-" + uuid.uuid4().hex[:10]
        self.conn.execute(
            """
            insert into knowledge_edges(
                id, school_id, ontology_version_id, source_node_id, target_node_id,
                relation_type, bidirectional, rationale, enabled, version
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                edge_id,
                self.school_id_for_actor(actor_id),
                self.first_active_ontology_id(),
                source_node_id,
                target_node_id,
                relation_type,
                1 if bidirectional else 0,
                rationale,
                1,
                1,
            ),
        )
        self.audit(
            actor_id,
            "knowledge_edge_created",
            "knowledge_edge",
            edge_id,
            {
                "source_node_id": source_node_id,
                "target_node_id": target_node_id,
                "relation_type": relation_type,
            },
        )
        self.conn.commit()
        return row_to_dict(self.conn.execute("select * from knowledge_edges where id = ?", (edge_id,)).fetchone())

    def create_ability_tag(self, actor_id, stable_code, name, description="", source="", enabled=True):
        ability_id = "ab-" + uuid.uuid4().hex[:10]
        self.conn.execute(
            """
            insert into ability_tags(
                id, school_id, ontology_version_id, stable_code, name, description,
                source, enabled, version
            ) values(?,?,?,?,?,?,?,?,?)
            """,
            (
                ability_id,
                self.school_id_for_actor(actor_id),
                self.first_active_ontology_id(),
                stable_code,
                name,
                description,
                source,
                1 if enabled else 0,
                1,
            ),
        )
        self.audit(
            actor_id,
            "ability_tag_created",
            "ability_tag",
            ability_id,
            {"stable_code": stable_code, "source": source},
        )
        self.conn.commit()
        return row_to_dict(self.conn.execute("select * from ability_tags where id = ?", (ability_id,)).fetchone())

    def update_ability_tag(self, actor_id, ability_tag_id, name, description="", source="", change_note=""):
        existing = self.conn.execute("select * from ability_tags where id = ?", (ability_tag_id,)).fetchone()
        if existing is None:
            raise ValueError("Ability tag not found: %s" % ability_tag_id)
        self.conn.execute(
            """
            update ability_tags
            set name = ?, description = ?, source = ?, change_note = ?,
                version = version + 1
            where id = ?
            """,
            (
                name,
                description or existing["description"],
                source or existing["source"],
                change_note,
                ability_tag_id,
            ),
        )
        self.audit(
            actor_id,
            "ability_tag_updated",
            "ability_tag",
            ability_tag_id,
            {"old_name": existing["name"], "new_name": name, "change_note": change_note},
        )
        self.conn.commit()
        return row_to_dict(self.conn.execute("select * from ability_tags where id = ?", (ability_tag_id,)).fetchone())

    def set_ability_tag_enabled(self, actor_id, ability_tag_id, enabled, change_note=""):
        existing = self.conn.execute("select * from ability_tags where id = ?", (ability_tag_id,)).fetchone()
        if existing is None:
            raise ValueError("Ability tag not found: %s" % ability_tag_id)
        self.conn.execute(
            """
            update ability_tags
            set enabled = ?, change_note = ?, version = version + 1
            where id = ?
            """,
            (1 if enabled else 0, change_note, ability_tag_id),
        )
        action = "ability_tag_restored" if enabled else "ability_tag_disabled"
        self.audit(actor_id, action, "ability_tag", ability_tag_id, {"change_note": change_note})
        self.conn.commit()
        return row_to_dict(self.conn.execute("select * from ability_tags where id = ?", (ability_tag_id,)).fetchone())

    def create_literacy_tag(
        self,
        actor_id,
        stable_code,
        name,
        parent_id=None,
        description="",
        source="教师校本",
        change_note="",
        enabled=True,
    ):
        school_id = self.school_id_for_actor(actor_id)
        level = 1
        if parent_id:
            parent = self.conn.execute(
                "select * from literacy_tags where id = ?",
                (parent_id,),
            ).fetchone()
            if (
                parent is None
                or parent["school_id"] != school_id
                or parent["deleted_at"] is not None
            ):
                raise ValueError(
                    "Parent literacy tag not found: %s" % parent_id
                )
            if parent["level"] != 1:
                raise ValueError(
                    "Literacy elements can only have a dimension parent"
                )
            level = 2
        literacy_id = "lit-" + uuid.uuid4().hex[:10]
        self.conn.execute(
            """
            insert into literacy_tags(
                id, school_id, ontology_version_id, parent_id,
                stable_code, name, level, description, source,
                enabled, version, change_note, is_default
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                literacy_id,
                school_id,
                self.first_active_ontology_id(),
                parent_id or None,
                stable_code,
                name,
                level,
                description,
                source,
                1 if enabled else 0,
                1,
                change_note,
                0,
            ),
        )
        self.audit(
            actor_id,
            "literacy_tag_created",
            "literacy_tag",
            literacy_id,
            {
                "stable_code": stable_code,
                "parent_id": parent_id,
                "change_note": change_note,
            },
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute(
                "select * from literacy_tags where id = ?",
                (literacy_id,),
            ).fetchone()
        )

    def update_literacy_tag(
        self,
        actor_id,
        literacy_id,
        name,
        description="",
        source="",
        change_note="",
    ):
        existing = self.conn.execute(
            "select * from literacy_tags where id = ?",
            (literacy_id,),
        ).fetchone()
        if existing is None:
            raise ValueError(
                "Literacy tag not found: %s" % literacy_id
            )
        self.conn.execute(
            """
            update literacy_tags
            set name = ?, description = ?, source = ?, change_note = ?,
                version = version + 1
            where id = ?
            """,
            (
                name,
                description or existing["description"],
                source or existing["source"],
                change_note,
                literacy_id,
            ),
        )
        self.audit(
            actor_id,
            "literacy_tag_updated",
            "literacy_tag",
            literacy_id,
            {
                "old_name": existing["name"],
                "new_name": name,
                "change_note": change_note,
            },
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute(
                "select * from literacy_tags where id = ?",
                (literacy_id,),
            ).fetchone()
        )

    def set_literacy_tag_enabled(
        self,
        actor_id,
        literacy_id,
        enabled,
        change_note="",
    ):
        existing = self.conn.execute(
            "select * from literacy_tags where id = ?",
            (literacy_id,),
        ).fetchone()
        if existing is None:
            raise ValueError(
                "Literacy tag not found: %s" % literacy_id
            )
        self.conn.execute(
            """
            update literacy_tags
            set enabled = ?, change_note = ?, version = version + 1
            where id = ?
            """,
            (
                1 if enabled else 0,
                change_note,
                literacy_id,
            ),
        )
        action = (
            "literacy_tag_restored"
            if enabled
            else "literacy_tag_disabled"
        )
        self.audit(
            actor_id,
            action,
            "literacy_tag",
            literacy_id,
            {"change_note": change_note},
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute(
                "select * from literacy_tags where id = ?",
                (literacy_id,),
            ).fetchone()
        )

    def create_ontology_draft(self, actor_id, version_label, source_summary):
        version_id = "onto-" + uuid.uuid4().hex[:10]
        self.conn.execute(
            """
            insert into knowledge_ontology_versions(id, school_id, version_label, status, source_summary)
            values(?,?,?,?,?)
            """,
            (
                version_id,
                self.school_id_for_actor(actor_id),
                version_label,
                "draft",
                source_summary,
            ),
        )
        self.audit(
            actor_id,
            "ontology_version_drafted",
            "knowledge_ontology_version",
            version_id,
            {"version_label": version_label},
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute("select * from knowledge_ontology_versions where id = ?", (version_id,)).fetchone()
        )

    def submit_ontology_for_review(self, actor_id, ontology_version_id):
        version = self.conn.execute(
            "select * from knowledge_ontology_versions where id = ?",
            (ontology_version_id,),
        ).fetchone()
        if version is None:
            raise ValueError("Ontology version not found: %s" % ontology_version_id)
        self.conn.execute(
            "update knowledge_ontology_versions set status = 'review' where id = ?",
            (ontology_version_id,),
        )
        self.audit(
            actor_id,
            "ontology_version_submitted",
            "knowledge_ontology_version",
            ontology_version_id,
            {"previous_status": version["status"]},
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute("select * from knowledge_ontology_versions where id = ?", (ontology_version_id,)).fetchone()
        )

    def publish_ontology_version(self, actor_id, ontology_version_id):
        version = self.conn.execute(
            "select * from knowledge_ontology_versions where id = ?",
            (ontology_version_id,),
        ).fetchone()
        if version is None:
            raise ValueError("Ontology version not found: %s" % ontology_version_id)
        self.conn.execute(
            """
            update knowledge_ontology_versions
            set status = 'archived'
            where status = 'active' and id <> ?
            """,
            (ontology_version_id,),
        )
        self.conn.execute(
            "update knowledge_ontology_versions set status = 'active' where id = ?",
            (ontology_version_id,),
        )
        self.conn.execute(
            "update knowledge_nodes set ontology_version_id = ? where deleted_at is null",
            (ontology_version_id,),
        )
        self.conn.execute(
            "update knowledge_edges set ontology_version_id = ? where deleted_at is null",
            (ontology_version_id,),
        )
        self.conn.execute(
            "update ability_tags set ontology_version_id = ? where deleted_at is null",
            (ontology_version_id,),
        )
        self.conn.execute(
            "update literacy_tags set ontology_version_id = ? where deleted_at is null",
            (ontology_version_id,),
        )
        self.conn.execute(
            """
            update curriculum_topics
            set ontology_version_id = ?
            where deleted_at is null
            """,
            (ontology_version_id,),
        )
        self.audit(
            actor_id,
            "ontology_version_published",
            "knowledge_ontology_version",
            ontology_version_id,
            {"version_label": version["version_label"]},
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute("select * from knowledge_ontology_versions where id = ?", (ontology_version_id,)).fetchone()
        )

    def get_question_tags(self, question_id):
        rows = self.conn.execute(
            """
            select qt.*, coalesce(kn.name, ab.name, lt.name, qt.tag_id) as name
            from question_tags qt
            left join knowledge_nodes kn on qt.tag_type = 'knowledge' and kn.id = qt.tag_id
            left join ability_tags ab on qt.tag_type = 'ability' and ab.id = qt.tag_id
            left join literacy_tags lt on qt.tag_type = 'literacy' and lt.id = qt.tag_id
            where qt.question_id = ? and qt.enabled = 1
              and (
                    (
                        qt.tag_type = 'knowledge'
                        and kn.enabled = 1
                        and kn.deleted_at is null
                    )
                    or (
                        qt.tag_type = 'ability'
                        and ab.enabled = 1
                        and ab.deleted_at is null
                    )
                    or (
                        qt.tag_type = 'literacy'
                        and lt.enabled = 1
                        and lt.deleted_at is null
                    )
                  )
            order by qt.tag_type, name
            """,
            (question_id,),
        ).fetchall()
        tags = rows_to_dicts(rows)
        paths = self.knowledge_node_paths()
        for tag in tags:
            if tag["tag_type"] == "knowledge":
                tag["path"] = paths.get(tag["tag_id"], [tag["name"]])
                tag["path_text"] = " > ".join(tag["path"])
        return tags

    def generate_llm_candidates(self, actor_id, question_id):
        question = self.get_question(question_id)
        if question is None:
            raise ValueError("Question not found: %s" % question_id)
        ontology_id = self.first_active_ontology_id()
        candidate = generate_candidate_tags(
            question,
            self.knowledge_nodes(),
            self.ability_tags(),
            self.literacy_tags(),
            ontology_id,
        )
        existing = self.conn.execute(
            """
            select * from question_tag_candidates
            where question_id = ? and cache_key = ?
            """,
            (question_id, candidate["cache_key"]),
        ).fetchone()
        if existing:
            return self._candidate_payload(existing)

        candidate_id = "cand-" + uuid.uuid4().hex
        self.conn.execute(
            """
            insert into question_tag_candidates(
                id, school_id, question_id, cache_key, knowledge_tags_json,
                ability_tags_json, literacy_tags_json, prompt_version,
                model_version, status, created_by
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                candidate_id,
                question["school_id"],
                question_id,
                candidate["cache_key"],
                dumps(candidate["knowledge_tags"]),
                dumps(candidate["ability_tags"]),
                dumps(candidate["literacy_tags"]),
                candidate["prompt_version"],
                candidate["model_version"],
                "pending_review",
                actor_id,
            ),
        )
        self.audit(
            actor_id,
            "llm_candidate_generated",
            "question",
            question_id,
            {
                "candidate_id": candidate_id,
                "prompt_version": candidate["prompt_version"],
                "model_version": candidate["model_version"],
            },
        )
        self.conn.commit()
        return self._candidate_payload(
            self.conn.execute("select * from question_tag_candidates where id = ?", (candidate_id,)).fetchone()
        )

    def list_pending_candidates(self):
        rows = self.conn.execute(
            """
            select c.*, q.stem
            from question_tag_candidates c
            join questions q on q.id = c.question_id
            where c.status = 'pending_review'
            order by c.created_at desc
            """
        ).fetchall()
        return [self._candidate_payload(row) for row in rows]

    def get_candidate(self, candidate_id):
        row = self.conn.execute(
            "select * from question_tag_candidates where id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ResourceNotFound("Candidate not found: %s" % candidate_id)
        return self._candidate_payload(row)

    def _candidate_payload(self, row):
        payload = row_to_dict(row)
        payload["knowledge_tags"] = loads(payload.pop("knowledge_tags_json"), [])
        payload["ability_tags"] = loads(payload.pop("ability_tags_json"), [])
        payload["literacy_tags"] = loads(
            payload.pop("literacy_tags_json", "[]"),
            [],
        )
        return payload

    def _validate_tag_limit(self, label, ids):
        if len(ids) > 3:
            raise ValueError(
                "At most 3 %s tags may be confirmed" % label
            )
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate %s tags are not allowed" % label)

    def _assert_active_tags(self, school_id, tag_type, tag_ids):
        table_by_type = {
            "knowledge": ("knowledge_nodes", "Knowledge node"),
            "ability": ("ability_tags", "Ability tag"),
            "literacy": ("literacy_tags", "Literacy tag"),
        }
        table, label = table_by_type[tag_type]
        for tag_id in tag_ids:
            active = self.conn.execute(
                """
                select 1 from %s
                where id = ? and school_id = ? and enabled = 1
                  and deleted_at is null
                """
                % table,
                (tag_id, school_id),
            ).fetchone()
            if active is None:
                raise ValueError("%s is not active: %s" % (label, tag_id))

    def _insert_confirmed_question_tag(
        self,
        actor_id,
        school_id,
        question_id,
        tag_type,
        tag_id,
        ontology_id,
        candidate_id,
    ):
        self.conn.execute(
            """
            insert or replace into question_tags(
                id, school_id, question_id, tag_type, tag_id,
                ontology_version_id, source, confirmed_by, candidate_id,
                confidence, rationale
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "tag-" + uuid.uuid4().hex,
                school_id,
                question_id,
                tag_type,
                tag_id,
                ontology_id,
                "teacher_review",
                actor_id,
                candidate_id,
                1.0,
                "教师审核候选标签后确认",
            ),
        )

    def confirm_question_tags(
        self,
        actor_id,
        question_id,
        candidate_id=None,
        knowledge_node_ids=None,
        ability_tag_ids=None,
        literacy_tag_ids=None,
    ):
        knowledge_node_ids = knowledge_node_ids or []
        ability_tag_ids = ability_tag_ids or []
        literacy_tag_ids = literacy_tag_ids or []
        self._validate_tag_limit("knowledge", knowledge_node_ids)
        self._validate_tag_limit("ability", ability_tag_ids)
        self._validate_tag_limit("literacy", literacy_tag_ids)

        question = self.get_question(question_id)
        if question is None:
            raise ResourceNotFound("Question not found: %s" % question_id)
        self._require_question_bank_actor(actor_id)
        school_id = question["school_id"]
        candidate = None
        if candidate_id:
            candidate = self.conn.execute(
                "select * from question_tag_candidates where id = ?",
                (candidate_id,),
            ).fetchone()
            if candidate is None:
                raise ResourceNotFound("Candidate not found: %s" % candidate_id)
            if (
                candidate["question_id"] != question_id
                or candidate["school_id"] != school_id
            ):
                raise ValueError("Candidate does not belong to this question")

        self._assert_active_tags(school_id, "knowledge", knowledge_node_ids)
        self._assert_active_tags(school_id, "ability", ability_tag_ids)
        self._assert_active_tags(school_id, "literacy", literacy_tag_ids)

        ontology_id = self.first_active_ontology_id()
        self.conn.execute(
            "delete from question_tags where question_id = ? and source = 'teacher_review'",
            (question_id,),
        )
        for tag_type, tag_ids in (
            ("knowledge", knowledge_node_ids),
            ("ability", ability_tag_ids),
            ("literacy", literacy_tag_ids),
        ):
            for tag_id in tag_ids:
                self._insert_confirmed_question_tag(
                    actor_id,
                    school_id,
                    question_id,
                    tag_type,
                    tag_id,
                    ontology_id,
                    candidate_id,
                )
        if candidate is not None:
            self.conn.execute(
                """
                update question_tag_candidates
                set status = 'approved', reviewed_by = ?,
                    reviewed_at = current_timestamp
                where id = ?
                """,
                (actor_id, candidate_id),
            )
        self.audit(
            actor_id,
            "question_tags_confirmed",
            "question",
            question_id,
            {
                "candidate_id": candidate_id,
                "knowledge_node_ids": knowledge_node_ids,
                "ability_tag_ids": ability_tag_ids,
                "literacy_tag_ids": literacy_tag_ids,
            },
        )
        self.conn.commit()
        return self.get_question_tags(question_id)

    def approve_candidate_tags(self, actor_id, candidate_id, knowledge_node_ids, ability_tag_ids):
        candidate = self.get_candidate(candidate_id)
        confirmed = self.confirm_question_tags(
            actor_id=actor_id,
            question_id=candidate["question_id"],
            candidate_id=candidate_id,
            knowledge_node_ids=knowledge_node_ids,
            ability_tag_ids=ability_tag_ids,
            literacy_tag_ids=[],
        )
        self.audit(
            actor_id,
            "question_tag_approved",
            "question",
            candidate["question_id"],
            {
                "candidate_id": candidate_id,
                "knowledge_node_ids": knowledge_node_ids,
                "ability_tag_ids": ability_tag_ids,
            },
        )
        self.conn.commit()
        return confirmed

    def resolve_review_item(self, actor_id, response_id, corrected_answer, reason):
        row = self.conn.execute(
            "select * from student_responses where id = ?",
            (response_id,),
        ).fetchone()
        if row is None:
            raise ResourceNotFound("Response not found: %s" % response_id)
        self._require(actor_id, "review", "assessment", row["assessment_id"])
        self.conn.execute(
            """
            update student_responses
            set final_answer = ?, review_status = 'resolved', review_note = ?,
                reviewed_by = ?, reviewed_at = current_timestamp,
                updated_at = current_timestamp
            where id = ?
            """,
            (corrected_answer, reason, actor_id, response_id),
        )
        self.audit(
            actor_id,
            "answer_card_review_resolved",
            "student_response",
            response_id,
            {
                "old_answer": row["final_answer"],
                "corrected_answer": corrected_answer,
                "reason": reason,
            },
        )
        self.conn.commit()

    def grade_assessment(self, actor_id, assessment_id, publish=False):
        assessment = self.conn.execute(
            "select * from assessment_sessions where id = ?",
            (assessment_id,),
        ).fetchone()
        if assessment is None:
            raise ResourceNotFound("Assessment not found: %s" % assessment_id)
        self._require(actor_id, "grade", "assessment", assessment_id)
        if (
            assessment["grading_status"] == "published"
            or assessment["status"] in ("已发布", "已归档")
        ):
            raise StateConflict(
                "Published assessments require an explicit revision"
            )

        unresolved = self.conn.execute(
            """
            select count(*) as count
            from student_responses
            where assessment_id = ?
              and review_status = 'required'
            """,
            (assessment_id,),
        ).fetchone()["count"]
        if unresolved:
            self.conn.execute(
                "update assessment_sessions set status = '待复核', grading_status = 'blocked_for_review' where id = ?",
                (assessment_id,),
            )
            self.audit(
                actor_id,
                "grading_blocked_for_review",
                "assessment",
                assessment_id,
                {"review_required": unresolved},
            )
            self.conn.commit()
            return {"status": "blocked_for_review", "review_required": unresolved}

        responses = self.conn.execute(
            """
            select r.*, s.grading_rule_json, s.answer_json
            from student_responses r
            join question_version_snapshots s on s.id = r.snapshot_id
            where r.assessment_id = ?
            order by r.student_id, s.position
            """,
            (assessment_id,),
        ).fetchall()
        self.conn.execute(
            """
            delete from mastery_marks
            where wrong_question_id in (
                select id from wrong_questions where assessment_id = ?
            )
            """,
            (assessment_id,),
        )
        self.conn.execute("delete from wrong_questions where assessment_id = ?", (assessment_id,))
        total_wrong = 0
        student_ids = set()
        for row in responses:
            student_ids.add(row["student_id"])
            rule = loads(row["grading_rule_json"], {})
            graded = grade_answer(rule, row["final_answer"])
            status = "correct" if graded["correct"] else "wrong"
            self.conn.execute(
                """
                update student_responses
                set score = ?, max_score = ?, grading_status = ?, updated_at = current_timestamp
                where id = ?
                """,
                (graded["score"], graded["max_score"], status, row["id"]),
            )
            if not graded["correct"]:
                total_wrong += 1
                self.conn.execute(
                    """
                    insert or replace into wrong_questions(
                        id, school_id, assessment_id, student_id, question_id, response_id,
                        wrong_answer, correct_answer_json, score, max_score, error_reason
                    ) values(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "wq-%s-%s-%s" % (assessment_id, row["student_id"], row["question_id"]),
                        row["school_id"],
                        assessment_id,
                        row["student_id"],
                        row["question_id"],
                        row["id"],
                        row["final_answer"],
                        row["answer_json"],
                        graded["score"],
                        graded["max_score"],
                        "客观题自动批改未得分",
                    ),
                )

        next_status = "已发布" if publish else "已批改"
        grading_status = "published" if publish else "graded"
        self.conn.execute(
            """
            update assessment_sessions
            set status = ?, grading_status = ?, statistics_status = 'ready',
                published_at = case when ? then current_timestamp else published_at end
            where id = ?
            """,
            (next_status, grading_status, 1 if publish else 0, assessment_id),
        )
        self.conn.execute(
            """
            update scan_batches
            set status = '已复核'
            where assessment_id = ?
            """,
            (assessment_id,),
        )
        if publish:
            self.refresh_assessment_mastery_metrics(assessment_id)
        self.audit(
            actor_id,
            "assessment_graded",
            "assessment",
            assessment_id,
            {"publish": publish, "wrong_question_count": total_wrong},
        )
        if publish:
            self.audit(actor_id, "assessment_published", "assessment", assessment_id, {})
        self.conn.commit()
        return {
            "status": "published" if publish else "graded",
            "student_count": len(student_ids),
            "wrong_question_count": total_wrong,
        }

    def apply_grading_revision(self, actor_id, assessment_id, reason, items):
        assessment = self.assessment_detail(
            actor_id,
            assessment_id,
            operation="grade",
        )
        if not items:
            raise ValueError("Grading revision requires at least one item")
        revision_id = "grev-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into grading_revisions(
                id, school_id, assessment_id, status, reason, created_by,
                applied_at
            ) values(?,?,?,?,?,?,current_timestamp)
            """,
            (
                revision_id,
                assessment["school_id"],
                assessment_id,
                "applied",
                reason,
                actor_id,
            ),
        )
        for item in items:
            response = self.conn.execute(
                """
                select *
                from student_responses
                where id = ? and assessment_id = ?
                """,
                (item["response_id"], assessment_id),
            ).fetchone()
            if response is None:
                raise ResourceNotFound(
                    "Response not found: %s" % item["response_id"]
                )
            revised_answer = item.get(
                "revised_answer",
                response["final_answer"],
            )
            revised_score = int(item["revised_score"])
            max_score = int(item["max_score"])
            item_id = "grevi-" + uuid.uuid4().hex[:12]
            self.conn.execute(
                """
                insert into grading_revision_items(
                    id, school_id, revision_id, response_id,
                    previous_answer, revised_answer, previous_score,
                    revised_score, max_score, reason
                ) values(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item_id,
                    assessment["school_id"],
                    revision_id,
                    response["id"],
                    response["final_answer"],
                    revised_answer,
                    response["score"],
                    revised_score,
                    max_score,
                    item["reason"],
                ),
            )
            self.conn.execute(
                """
                update student_responses
                set final_answer = ?, score = ?, max_score = ?,
                    grading_status = ?,
                    overridden_by = ?, override_reason = ?,
                    updated_at = current_timestamp
                where id = ?
                """,
                (
                    revised_answer,
                    revised_score,
                    max_score,
                    "correct" if revised_score >= max_score else "wrong",
                    actor_id,
                    item["reason"],
                    response["id"],
                ),
            )
            if revised_score < max_score:
                snapshot = self.conn.execute(
                    """
                    select answer_json
                    from question_version_snapshots
                    where id = ?
                    """,
                    (response["snapshot_id"],),
                ).fetchone()
                self.conn.execute(
                    """
                    insert into wrong_questions(
                        id, school_id, assessment_id, student_id, question_id,
                        response_id, wrong_answer, correct_answer_json, score,
                        max_score, error_reason, latest_redo_status
                    ) values(?,?,?,?,?,?,?,?,?,?,?,?)
                    on conflict(assessment_id, student_id, question_id)
                    do update set wrong_answer = excluded.wrong_answer,
                                  score = excluded.score,
                                  max_score = excluded.max_score,
                                  error_reason = excluded.error_reason
                    """,
                    (
                        "wq-%s-%s-%s"
                        % (
                            assessment_id,
                            response["student_id"],
                            response["question_id"],
                        ),
                        assessment["school_id"],
                        assessment_id,
                        response["student_id"],
                        response["question_id"],
                        response["id"],
                        revised_answer,
                        snapshot["answer_json"],
                        revised_score,
                        max_score,
                        item["reason"],
                        "pending",
                    ),
                )
            else:
                self.conn.execute(
                    """
                    delete from mastery_marks
                    where wrong_question_id in (
                        select id from wrong_questions where response_id = ?
                    )
                    """,
                    (response["id"],),
                )
                self.conn.execute(
                    "delete from wrong_questions where response_id = ?",
                    (response["id"],),
                )
        self.refresh_assessment_mastery_metrics(assessment_id)
        self.audit(
            actor_id,
            "grading_revision_applied",
            "assessment",
            assessment_id,
            {"revision_id": revision_id, "item_count": len(items)},
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute(
                "select * from grading_revisions where id = ?",
                (revision_id,),
            ).fetchone()
        )

    def _wrong_question_row(self, wrong_question_id):
        row = self.conn.execute(
            """
            select wq.*, a.class_id
            from wrong_questions wq
            join assessment_sessions a on a.id = wq.assessment_id
            where wq.id = ?
            """,
            (wrong_question_id,),
        ).fetchone()
        if row is None:
            raise ResourceNotFound(
                "Wrong question not found: %s" % wrong_question_id
            )
        return row_to_dict(row)

    def _require_wrong_question_student(self, actor_id, wrong_question_id):
        wrong = self._wrong_question_row(wrong_question_id)
        self._require(actor_id, "modify", "wrong_questions", wrong["student_id"])
        if actor_id != wrong["student_id"]:
            raise PermissionDenied("You do not have access to this wrong question")
        return wrong

    def _require_wrong_question_reviewer(self, actor_id, wrong_question_id):
        wrong = self._wrong_question_row(wrong_question_id)
        self.assessment_detail(actor_id, wrong["assessment_id"], operation="grade")
        return wrong

    def create_error_reason_tag(self, actor_id, code, name, description=""):
        actor = self._require_question_bank_actor(actor_id)
        tag_id = "ert-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into error_reason_tags(
                id, school_id, code, name, description, enabled
            ) values(?,?,?,?,?,1)
            on conflict(school_id, code)
            do update set name = excluded.name,
                          description = excluded.description,
                          enabled = 1
            """,
            (tag_id, actor["school_id"], code, name, description),
        )
        row = self.conn.execute(
            """
            select *
            from error_reason_tags
            where school_id = ? and code = ?
            """,
            (actor["school_id"], code),
        ).fetchone()
        self.audit(
            actor_id,
            "error_reason_tag_created",
            "error_reason_tag",
            row["id"],
            {"code": code},
        )
        self.conn.commit()
        return row_to_dict(row)

    def tag_wrong_question_error(
        self,
        actor_id,
        wrong_question_id,
        tag_ids,
        note="",
    ):
        wrong = self._require_wrong_question_reviewer(actor_id, wrong_question_id)
        valid_rows = self.conn.execute(
            """
            select id
            from error_reason_tags
            where school_id = ?
              and enabled = 1
              and id in (%s)
            """
            % ",".join("?" for _ in tag_ids),
            [wrong["school_id"]] + list(tag_ids),
        ).fetchall() if tag_ids else []
        valid_tag_ids = [row["id"] for row in valid_rows]
        if len(valid_tag_ids) != len(tag_ids):
            raise ResourceNotFound("One or more error reason tags were not found")
        self.conn.execute(
            "delete from wrong_question_error_tags where wrong_question_id = ?",
            (wrong_question_id,),
        )
        for tag_id in valid_tag_ids:
            self.conn.execute(
                """
                insert into wrong_question_error_tags(
                    wrong_question_id, error_reason_tag_id, tagged_by, note
                ) values(?,?,?,?)
                """,
                (wrong_question_id, tag_id, actor_id, note),
            )
        self.conn.execute(
            """
            update wrong_questions
            set error_reason_tag_ids_json = ?
            where id = ?
            """,
            (dumps(valid_tag_ids), wrong_question_id),
        )
        self.audit(
            actor_id,
            "wrong_question_error_tagged",
            "wrong_question",
            wrong_question_id,
            {"tag_ids": valid_tag_ids},
        )
        self.conn.commit()
        return self.wrong_question_detail(actor_id, wrong_question_id)

    def submit_redo_attempt(self, actor_id, wrong_question_id, answer):
        wrong = self._require_wrong_question_student(actor_id, wrong_question_id)
        attempt_id = "redo-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into redo_attempts(
                id, school_id, wrong_question_id, student_id, answer, status
            ) values(?,?,?,?,?,?)
            """,
            (
                attempt_id,
                wrong["school_id"],
                wrong_question_id,
                wrong["student_id"],
                answer,
                "submitted",
            ),
        )
        self.conn.execute(
            """
            update wrong_questions
            set latest_redo_status = 'submitted',
                redo_status = 'submitted'
            where id = ?
            """,
            (wrong_question_id,),
        )
        self.audit(
            actor_id,
            "redo_attempt_submitted",
            "wrong_question",
            wrong_question_id,
            {"attempt_id": attempt_id},
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute(
                "select * from redo_attempts where id = ?",
                (attempt_id,),
            ).fetchone()
        )

    def review_redo_attempt(self, actor_id, attempt_id, score, feedback=""):
        attempt = self.conn.execute(
            "select * from redo_attempts where id = ?",
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise ResourceNotFound("Redo attempt not found: %s" % attempt_id)
        wrong = self._require_wrong_question_reviewer(
            actor_id,
            attempt["wrong_question_id"],
        )
        score = int(score)
        max_score = int(wrong["max_score"])
        status = "done" if score >= max_score else "reviewed"
        self.conn.execute(
            """
            update redo_attempts
            set score = ?, max_score = ?, status = ?, feedback = ?,
                reviewed_by = ?, reviewed_at = current_timestamp
            where id = ?
            """,
            (score, max_score, status, feedback, actor_id, attempt_id),
        )
        self.conn.execute(
            """
            update wrong_questions
            set latest_redo_status = ?,
                redo_status = ?
            where id = ?
            """,
            (status, status, wrong["id"]),
        )
        self.recalculate_student_mastery_metrics(wrong["student_id"])
        self.audit(
            actor_id,
            "redo_attempt_reviewed",
            "wrong_question",
            wrong["id"],
            {"attempt_id": attempt_id, "status": status},
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute(
                "select * from redo_attempts where id = ?",
                (attempt_id,),
            ).fetchone()
        )

    def _new_mastery_metric(self, student, mastery_version_id, tag):
        return {
            "id": "smm-" + uuid.uuid4().hex[:16],
            "school_id": student["school_id"],
            "student_id": student["id"],
            "mastery_inference_version_id": mastery_version_id,
            "tag_type": tag["tag_type"],
            "tag_id": tag["tag_id"],
            "tag_name": tag["name"],
            "assessment_attempts": 0,
            "assessment_correct": 0,
            "assessment_wrong": 0,
            "assessment_blank": 0,
            "redo_attempts": 0,
            "redo_correct": 0,
            "redo_wrong": 0,
        }

    def _metric_for_tag(self, metrics, student, mastery_version_id, tag):
        key = (tag["tag_type"], tag["tag_id"])
        if key not in metrics:
            metrics[key] = self._new_mastery_metric(
                student,
                mastery_version_id,
                tag,
            )
        elif not metrics[key]["tag_name"] and tag.get("name"):
            metrics[key]["tag_name"] = tag["name"]
        return metrics[key]

    def recalculate_student_mastery_metrics(self, student_id):
        student = self.conn.execute(
            "select id, school_id from users where id = ? and role = 'student'",
            (student_id,),
        ).fetchone()
        if student is None:
            raise ResourceNotFound("Student not found: %s" % student_id)
        mastery_version_id = self.deterministic_mastery_inference_version_id()
        metrics = {}

        response_rows = self.conn.execute(
            """
            select r.*, s.tag_snapshot_json
            from student_responses r
            join assessment_sessions a on a.id = r.assessment_id
            join question_version_snapshots s on s.id = r.snapshot_id
            where r.student_id = ?
              and a.grading_status = 'published'
              and r.score is not null
              and r.max_score is not null
            order by a.published_at, r.assessment_id, r.question_id
            """,
            (student_id,),
        ).fetchall()
        for row in response_rows:
            tags = normalize_snapshot_tags(loads(row["tag_snapshot_json"], []))
            if not tags:
                continue
            max_score = int(row["max_score"] or 0)
            score = int(row["score"] or 0)
            is_correct = max_score > 0 and score >= max_score
            is_blank = blank_answer(row["final_answer"])
            for tag in tags:
                metric = self._metric_for_tag(
                    metrics,
                    student,
                    mastery_version_id,
                    tag,
                )
                metric["assessment_attempts"] += 1
                if is_correct:
                    metric["assessment_correct"] += 1
                elif is_blank:
                    metric["assessment_blank"] += 1
                else:
                    metric["assessment_wrong"] += 1

        redo_rows = self.conn.execute(
            """
            select ra.*, wq.response_id, coalesce(ra.max_score, wq.max_score) as effective_max_score,
                   s.tag_snapshot_json
            from redo_attempts ra
            join wrong_questions wq on wq.id = ra.wrong_question_id
            join student_responses r on r.id = wq.response_id
            join assessment_sessions a on a.id = wq.assessment_id
            join question_version_snapshots s on s.id = r.snapshot_id
            where ra.student_id = ?
              and a.grading_status = 'published'
              and ra.status in ('reviewed', 'done')
              and ra.score is not null
            order by ra.reviewed_at, ra.id
            """,
            (student_id,),
        ).fetchall()
        for row in redo_rows:
            tags = normalize_snapshot_tags(loads(row["tag_snapshot_json"], []))
            if not tags:
                continue
            max_score = int(row["effective_max_score"] or 0)
            score = int(row["score"] or 0)
            is_correct = max_score > 0 and score >= max_score
            for tag in tags:
                metric = self._metric_for_tag(
                    metrics,
                    student,
                    mastery_version_id,
                    tag,
                )
                metric["redo_attempts"] += 1
                if is_correct:
                    metric["redo_correct"] += 1
                else:
                    metric["redo_wrong"] += 1

        self.conn.execute(
            "delete from student_mastery_metrics where student_id = ?",
            (student_id,),
        )
        for metric in metrics.values():
            eligible_attempts = (
                metric["assessment_attempts"] + metric["redo_attempts"]
            )
            correct_count = metric["assessment_correct"] + metric["redo_correct"]
            wrong_count = metric["assessment_wrong"] + metric["redo_wrong"]
            blank_count = metric["assessment_blank"]
            correct_rate = (
                correct_count / eligible_attempts
                if eligible_attempts
                else None
            )
            metric["eligible_attempts"] = eligible_attempts
            metric["correct_count"] = correct_count
            metric["wrong_count"] = wrong_count
            metric["blank_count"] = blank_count
            metric["correct_rate"] = correct_rate
            metric["mastery_state"] = classify_mastery(
                eligible_attempts,
                correct_rate,
            )
            self.conn.execute(
                """
                insert into student_mastery_metrics(
                    id, school_id, student_id, mastery_inference_version_id,
                    tag_type, tag_id, tag_name, assessment_attempts,
                    assessment_correct, assessment_wrong, assessment_blank,
                    redo_attempts, redo_correct, redo_wrong, eligible_attempts,
                    correct_count, wrong_count, blank_count, correct_rate,
                    mastery_state, calculated_at
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)
                """,
                (
                    metric["id"],
                    metric["school_id"],
                    metric["student_id"],
                    metric["mastery_inference_version_id"],
                    metric["tag_type"],
                    metric["tag_id"],
                    metric["tag_name"],
                    metric["assessment_attempts"],
                    metric["assessment_correct"],
                    metric["assessment_wrong"],
                    metric["assessment_blank"],
                    metric["redo_attempts"],
                    metric["redo_correct"],
                    metric["redo_wrong"],
                    metric["eligible_attempts"],
                    metric["correct_count"],
                    metric["wrong_count"],
                    metric["blank_count"],
                    metric["correct_rate"],
                    metric["mastery_state"],
                ),
            )
        return self.student_mastery_metrics(student_id, student_id)

    def refresh_assessment_mastery_metrics(self, assessment_id):
        rows = self.conn.execute(
            """
            select student_id
            from assessment_participants
            where assessment_id = ?
            order by student_id
            """,
            (assessment_id,),
        ).fetchall()
        for row in rows:
            self.recalculate_student_mastery_metrics(row["student_id"])

    def student_mastery_metrics(self, actor_id, student_id=None, tag_type=None):
        student_id = student_id or actor_id
        self._require(actor_id, "view", "wrong_questions", student_id)
        params = [student_id]
        tag_filter = ""
        if tag_type:
            tag_filter = " and tag_type = ?"
            params.append(tag_type)
        return rows_to_dicts(
            self.conn.execute(
                """
                select *
                from student_mastery_metrics
                where student_id = ?
                %s
                order by tag_type, tag_name, tag_id
                """ % tag_filter,
                params,
            ).fetchall()
        )

    def error_reason_tags_for_wrong(self, wrong_question_id):
        return rows_to_dicts(
            self.conn.execute(
                """
                select t.*, wt.note, wt.tagged_by, wt.created_at as tagged_at
                from wrong_question_error_tags wt
                join error_reason_tags t on t.id = wt.error_reason_tag_id
                where wt.wrong_question_id = ?
                  and t.enabled = 1
                order by t.name, t.id
                """,
                (wrong_question_id,),
            ).fetchall()
        )

    def redo_attempts_for_wrong(self, wrong_question_id):
        return rows_to_dicts(
            self.conn.execute(
                """
                select *
                from redo_attempts
                where wrong_question_id = ?
                order by submitted_at desc, id
                """,
                (wrong_question_id,),
            ).fetchall()
        )

    def save_export_profile(self, actor_id, name, options):
        actor = self._actor(actor_id)
        if actor["role"] != "admin":
            raise PermissionDenied("Admin role required")
        normalized = default_export_options(options)
        profile_id = "export-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into export_profiles(
                id, school_id, name, options_json, created_by
            ) values(?,?,?,?,?)
            """,
            (
                profile_id,
                actor["school_id"],
                name,
                dumps(normalized),
                actor_id,
            ),
        )
        self.audit(
            actor_id,
            "export_profile_saved",
            "export_profile",
            profile_id,
            {"name": name},
        )
        self.conn.commit()
        profile = row_to_dict(
            self.conn.execute(
                "select * from export_profiles where id = ?",
                (profile_id,),
            ).fetchone()
        )
        profile["options"] = loads(profile["options_json"], {})
        return profile

    def _default_export_dir(self):
        rows = self.conn.execute("pragma database_list").fetchall()
        for row in rows:
            name = row["name"] if hasattr(row, "keys") else row[1]
            file_name = row["file"] if hasattr(row, "keys") else row[2]
            if name == "main" and file_name and file_name != ":memory:":
                return Path(file_name).parent / "exports"
        return Path("data/exports")

    def generate_wrong_book_pdf(
        self,
        actor_id,
        assessment_id,
        class_id=None,
        student_id=None,
        options=None,
        output_dir=None,
        engine=None,
    ):
        actor = self._actor(actor_id)
        html = build_wrong_book_html(
            self,
            actor_id,
            assessment_id,
            class_id=class_id,
            student_id=student_id,
            options=options,
        )
        output_dir = Path(output_dir) if output_dir else self._default_export_dir()
        task_id = "pdf-export-" + uuid.uuid4().hex[:12]
        file_name = "%s-wrong-book-%s.pdf" % (assessment_id, task_id[-6:])
        output_path = output_dir / file_name
        self.conn.execute(
            """
            insert into export_tasks(
                id, school_id, assessment_id, export_type, status,
                output_path, file_name, content_type, created_by
            ) values(?,?,?,?,?,?,?,?,?)
            """,
            (
                task_id,
                actor["school_id"],
                assessment_id,
                "wrong_book_pdf",
                "running",
                str(output_path),
                file_name,
                "application/pdf",
                actor_id,
            ),
        )
        try:
            artifact = write_pdf_artifact(
                html,
                output_path,
                engine=engine,
                options={"format": "A4", "print_background": True},
            )
        except Exception as error:
            self.conn.execute(
                """
                update export_tasks
                set status = 'failed', failure_reason = ?
                where id = ?
                """,
                (str(error), task_id),
            )
            self.audit(
                actor_id,
                "pdf_export_failed",
                "export_task",
                task_id,
                {"assessment_id": assessment_id, "failure_reason": str(error)},
            )
            self.conn.commit()
            raise
        self.conn.execute(
            """
            update export_tasks
            set status = 'completed',
                output_path = ?,
                file_name = ?,
                content_type = ?,
                byte_size = ?,
                engine_version = ?,
                completed_at = current_timestamp
            where id = ?
            """,
            (
                artifact["output_path"],
                artifact["file_name"],
                artifact["content_type"],
                artifact["byte_size"],
                artifact["engine_version"],
                task_id,
            ),
        )
        file_id = "generated-export-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into generated_export_files(
                id, school_id, export_task_id, assessment_id, export_type,
                file_name, content_type, byte_size, engine_version,
                storage_path, created_by
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                file_id,
                actor["school_id"],
                task_id,
                assessment_id,
                "wrong_book_pdf",
                artifact["file_name"],
                artifact["content_type"],
                artifact["byte_size"],
                artifact["engine_version"],
                artifact["output_path"],
                actor_id,
            ),
        )
        self.audit(
            actor_id,
            "pdf_export_completed",
            "export_task",
            task_id,
            {
                "assessment_id": assessment_id,
                "file_id": file_id,
                "byte_size": artifact["byte_size"],
            },
        )
        self.conn.commit()
        task = row_to_dict(
            self.conn.execute(
                "select * from export_tasks where id = ?",
                (task_id,),
            ).fetchone()
        )
        task["generated_file_id"] = file_id
        return task

    def _enrich_wrong_question_item(self, item):
        item["options"] = loads(item.pop("options_json"), {})
        item["correct_answer"] = loads(item.pop("correct_answer_json"), None)
        item["error_reason_tag_ids"] = loads(
            item.get("error_reason_tag_ids_json"),
            [],
        )
        tags = self.tags_for_question(item["question_id"])
        item["knowledge_tags"] = [
            tag for tag in tags if tag["tag_type"] == "knowledge"
        ]
        item["ability_tags"] = [
            tag for tag in tags if tag["tag_type"] == "ability"
        ]
        item["literacy_tags"] = [
            tag for tag in tags if tag["tag_type"] == "literacy"
        ]
        item["error_reason_tags"] = self.error_reason_tags_for_wrong(item["id"])
        item["redo_attempts"] = self.redo_attempts_for_wrong(item["id"])
        return item

    def wrong_question_detail(self, actor_id, wrong_question_id):
        wrong = self._wrong_question_row(wrong_question_id)
        actor = self._actor(actor_id)
        if actor["role"] == "student":
            if actor_id != wrong["student_id"]:
                raise PermissionDenied("You do not have access to this wrong question")
            self._require(actor_id, "view", "wrong_questions", wrong["student_id"])
        else:
            self.assessment_detail(actor_id, wrong["assessment_id"], operation="view")
        row = self.conn.execute(
            """
            select wq.*, q.stem, q.options_json, q.analysis, q.question_type,
                   u.display_name as student_name, u.student_no,
                   a.title as assessment_title, c.name as class_name,
                   mm.level as mastery_level, mm.note as mastery_note
            from wrong_questions wq
            join questions q on q.id = wq.question_id
            join users u on u.id = wq.student_id
            join assessment_sessions a on a.id = wq.assessment_id
            join class_groups c on c.id = a.class_id
            left join mastery_marks mm on mm.wrong_question_id = wq.id and mm.student_id = wq.student_id
            where wq.id = ?
            """,
            (wrong_question_id,),
        ).fetchone()
        return self._enrich_wrong_question_item(row_to_dict(row))

    def list_wrong_questions_for_student(
        self,
        actor_id,
        student_id=None,
        knowledge_node_id=None,
    ):
        student_id = student_id or actor_id
        self._require(actor_id, "view", "wrong_questions", student_id)
        params = [student_id]
        knowledge_filter = ""
        if knowledge_node_id:
            node_ids = self.descendant_knowledge_ids(knowledge_node_id)
            placeholders = ",".join("?" for _ in node_ids)
            knowledge_filter = """
              and exists (
                select 1 from question_tags qt
                where qt.question_id = wq.question_id
                  and qt.tag_type = 'knowledge'
                  and qt.enabled = 1
                  and qt.tag_id in (%s)
              )
            """ % placeholders
            params.extend(node_ids)
        rows = self.conn.execute(
            """
            select wq.*, q.stem, q.options_json, q.analysis, q.question_type,
                   a.title as assessment_title, c.name as class_name,
                   mm.level as mastery_level, mm.note as mastery_note
            from wrong_questions wq
            join questions q on q.id = wq.question_id
            join assessment_sessions a on a.id = wq.assessment_id
            join class_groups c on c.id = a.class_id
            left join mastery_marks mm on mm.wrong_question_id = wq.id and mm.student_id = wq.student_id
            where wq.student_id = ?
              and a.grading_status = 'published'
            %s
            order by wq.created_at desc, q.id
            """ % knowledge_filter,
            params,
        ).fetchall()
        wrongs = []
        for row in rows:
            item = row_to_dict(row)
            wrongs.append(self._enrich_wrong_question_item(item))
        return wrongs

    def assessment_detail(self, actor_id, assessment_id, operation="view"):
        self._require(actor_id, operation, "assessment", assessment_id)
        row = self.conn.execute(
            """
            select a.*, c.name as class_name
            from assessment_sessions a
            join class_groups c on c.id = a.class_id
            where a.id = ?
            """,
            (assessment_id,),
        ).fetchone()
        if row is None:
            raise ResourceNotFound("Assessment not found: %s" % assessment_id)
        return row_to_dict(row)

    def list_wrong_questions_for_assessment(
        self,
        actor_id,
        assessment_id,
        class_id=None,
        student_id=None,
        operation="view",
    ):
        self._require(actor_id, operation, "assessment", assessment_id)
        params = [assessment_id]
        filters = []
        if class_id:
            filters.append("a.class_id = ?")
            params.append(class_id)
        if student_id:
            filters.append("wq.student_id = ?")
            params.append(student_id)
        extra_where = ""
        if filters:
            extra_where = " and " + " and ".join(filters)
        rows = self.conn.execute(
            """
            select wq.*, q.stem, q.options_json, q.analysis, q.question_type,
                   u.display_name as student_name, u.student_no,
                   a.title as assessment_title, c.name as class_name,
                   mm.level as mastery_level, mm.note as mastery_note
            from wrong_questions wq
            join questions q on q.id = wq.question_id
            join users u on u.id = wq.student_id
            join assessment_sessions a on a.id = wq.assessment_id
            join class_groups c on c.id = a.class_id
            left join mastery_marks mm on mm.wrong_question_id = wq.id and mm.student_id = wq.student_id
            where wq.assessment_id = ?
            %s
            order by u.student_no, q.id
            """ % extra_where,
            params,
        ).fetchall()
        wrongs = []
        for row in rows:
            item = row_to_dict(row)
            wrongs.append(self._enrich_wrong_question_item(item))
        return wrongs

    def tags_for_question(self, question_id):
        rows = self.conn.execute(
            """
            select
                qt.tag_type,
                qt.tag_id,
                coalesce(kn.name, ab.name, lt.name, qt.tag_id) as name
            from question_tags qt
            left join knowledge_nodes kn on qt.tag_type = 'knowledge' and kn.id = qt.tag_id
            left join ability_tags ab on qt.tag_type = 'ability' and ab.id = qt.tag_id
            left join literacy_tags lt on qt.tag_type = 'literacy' and lt.id = qt.tag_id
            where qt.question_id = ? and qt.enabled = 1
            order by qt.tag_type, name
            """,
            (question_id,),
        ).fetchall()
        return rows_to_dicts(rows)

    def set_knowledge_mastery_mark(self, actor_id, student_id, knowledge_node_id, level, note=""):
        self._require(actor_id, "modify", "mastery_mark", student_id)
        node = self.conn.execute(
            "select * from knowledge_nodes where id = ?",
            (knowledge_node_id,),
        ).fetchone()
        if node is None:
            raise ValueError("Knowledge node not found: %s" % knowledge_node_id)
        mark_id = "kmm-%s-%s" % (student_id, knowledge_node_id)
        self.conn.execute(
            """
            insert into knowledge_mastery_marks(
                id, school_id, student_id, knowledge_node_id, level, note, source
            ) values(?,?,?,?,?,?,?)
            on conflict(student_id, knowledge_node_id)
            do update set level = excluded.level, note = excluded.note,
                          updated_at = current_timestamp
            """,
            (
                mark_id,
                node["school_id"],
                student_id,
                knowledge_node_id,
                level,
                note,
                "student_graph_manual",
            ),
        )
        self.audit(
            actor_id,
            "knowledge_mastery_mark_updated",
            "knowledge_node",
            knowledge_node_id,
            {"student_id": student_id, "level": level, "note": note},
        )
        self.conn.commit()
        return {"id": mark_id, "level": level, "note": note}

    def set_mastery_mark(self, actor_id, wrong_question_id, level, note=""):
        wrong = self.conn.execute(
            "select * from wrong_questions where id = ?",
            (wrong_question_id,),
        ).fetchone()
        if wrong is None:
            raise ResourceNotFound("Wrong question not found: %s" % wrong_question_id)
        self._require(
            actor_id,
            "modify",
            "mastery_mark",
            wrong["student_id"],
        )
        mark_id = "mm-%s-%s" % (wrong["student_id"], wrong_question_id)
        self.conn.execute(
            """
            insert into mastery_marks(
                id, school_id, student_id, wrong_question_id, level, note, source
            ) values(?,?,?,?,?,?,?)
            on conflict(student_id, wrong_question_id)
            do update set level = excluded.level, note = excluded.note,
                          updated_at = current_timestamp
            """,
            (
                mark_id,
                wrong["school_id"],
                wrong["student_id"],
                wrong_question_id,
                level,
                note,
                "student_manual",
            ),
        )
        self.audit(
            actor_id,
            "mastery_mark_updated",
            "wrong_question",
            wrong_question_id,
            {"level": level, "note": note},
        )
        self.conn.commit()
        return {"id": mark_id, "level": level, "note": note}

    def class_diagnostics(self, actor_id, assessment_id):
        self._require(actor_id, "view", "diagnostics", assessment_id)
        wrongs = self.list_wrong_questions_for_assessment(
            actor_id,
            assessment_id,
            operation="view",
        )
        assessment = self.assessment_detail(actor_id, assessment_id, operation="view")
        participants = self.conn.execute(
            "select count(*) as count from assessment_participants where assessment_id = ?",
            (assessment_id,),
        ).fetchone()["count"]
        question_count = self.conn.execute(
            "select count(*) as count from question_version_snapshots where assessment_id = ?",
            (assessment_id,),
        ).fetchone()["count"]
        denominator = max(1, participants)
        by_knowledge = {}
        by_ability = {}
        by_question = {}
        for wrong in wrongs:
            by_question.setdefault(
                wrong["question_id"],
                {
                    "question_id": wrong["question_id"],
                    "stem": wrong["stem"],
                    "wrong_count": 0,
                    "max_score": wrong["max_score"],
                },
            )
            by_question[wrong["question_id"]]["wrong_count"] += 1
            knowledge_tags = wrong["knowledge_tags"] or [{"name": "未标注知识点", "tag_id": "untagged"}]
            ability_tags = wrong["ability_tags"] or [{"name": "未标注能力", "tag_id": "untagged"}]
            for tag in knowledge_tags:
                bucket = by_knowledge.setdefault(tag["name"], {"name": tag["name"], "wrong_count": 0})
                bucket["wrong_count"] += 1
            for tag in ability_tags:
                bucket = by_ability.setdefault(tag["name"], {"name": tag["name"], "wrong_count": 0})
                bucket["wrong_count"] += 1

        def with_rate(items):
            values = []
            for item in items:
                item = dict(item)
                item["error_rate"] = round(item["wrong_count"] / denominator, 3)
                values.append(item)
            return sorted(values, key=lambda item: (-item["wrong_count"], item["name"]))

        high_frequency = []
        for item in by_question.values():
            item["error_rate"] = round(item["wrong_count"] / denominator, 3)
            high_frequency.append(item)
        high_frequency.sort(key=lambda item: (-item["wrong_count"], item["question_id"]))
        self.audit(
            actor_id,
            "class_diagnostics_viewed",
            "assessment",
            assessment_id,
            {"wrong_question_count": len(wrongs)},
        )
        self.conn.commit()
        return {
            "assessment": assessment,
            "participant_count": participants,
            "question_count": question_count,
            "wrong_question_count": len(wrongs),
            "knowledge_error_rates": with_rate(by_knowledge.values()),
            "ability_error_rates": with_rate(by_ability.values()),
            "high_frequency_wrong_questions": high_frequency,
            "grade_average": self.grade_average_for_assessment(assessment["grade"]),
        }

    def grade_average_for_assessment(self, grade):
        rows = self.conn.execute(
            """
            select a.id, a.full_score, r.student_id, sum(r.score) as score
            from assessment_sessions a
            join student_responses r on r.assessment_id = a.id
            where a.grade = ? and a.grading_status in ('published', 'graded')
            group by a.id, r.student_id
            """,
            (grade,),
        ).fetchall()
        if not rows:
            return {"grade": grade, "student_count": 0, "average_score_rate": 0.0}
        total_rate = 0.0
        for row in rows:
            total_rate += (row["score"] or 0) / max(1, row["full_score"])
        return {
            "grade": grade,
            "student_count": len(rows),
            "average_score_rate": round(total_rate / len(rows), 3),
        }

    def _mastery_state_counts(self):
        return {
            "未练习": 0,
            "未掌握": 0,
            "有困难": 0,
            "不熟练": 0,
            "已掌握": 0,
        }

    def _tag_catalog(self, tag_type):
        if tag_type == "knowledge":
            paths = self.knowledge_node_paths()
            return {
                node["id"]: {
                    "tag_type": "knowledge",
                    "tag_id": node["id"],
                    "tag_name": node["name"],
                    "stable_code": node.get("stable_code", ""),
                    "path_text": " > ".join(paths.get(node["id"], [node["name"]])),
                }
                for node in self.all_knowledge_nodes()
            }
        if tag_type == "ability":
            return {
                tag["id"]: {
                    "tag_type": "ability",
                    "tag_id": tag["id"],
                    "tag_name": tag["name"],
                    "stable_code": tag.get("stable_code", ""),
                    "path_text": tag.get("stable_code", ""),
                }
                for tag in self.all_ability_tags()
            }
        return {
            tag["id"]: {
                "tag_type": "literacy",
                "tag_id": tag["id"],
                "tag_name": tag["name"],
                "stable_code": tag.get("stable_code", ""),
                "path_text": tag.get("stable_code", ""),
            }
            for tag in self.all_literacy_tags()
        }

    def _analytics_bucket(self, row, catalog):
        tag_type = row["tag_type"]
        tag_id = row["tag_id"]
        tag = catalog.get(tag_type, {}).get(
            tag_id,
            {
                "tag_type": tag_type,
                "tag_id": tag_id,
                "tag_name": row.get("tag_name") or tag_id,
                "stable_code": "",
                "path_text": "",
            },
        )
        return {
            **tag,
            "assessment_attempts": 0,
            "redo_attempts": 0,
            "eligible_attempts": 0,
            "correct_count": 0,
            "wrong_count": 0,
            "blank_count": 0,
            "correct_rate": 0.0,
            "error_rate": 0.0,
            "blank_rate": 0.0,
            "mastery_state": "未练习",
            "mastery_css_class": mastery_css_class("未练习"),
            "state_counts": self._mastery_state_counts(),
            "students": [],
            "_student_ids": set(),
        }

    def _aggregate_mastery_rows(self, rows, include_students=False):
        catalog = {
            "knowledge": self._tag_catalog("knowledge"),
            "ability": self._tag_catalog("ability"),
            "literacy": self._tag_catalog("literacy"),
        }
        buckets = {"knowledge": {}, "ability": {}, "literacy": {}}
        for source_row in rows:
            row = row_to_dict(source_row)
            tag_type = row["tag_type"]
            tag_id = row["tag_id"]
            bucket = buckets[tag_type].setdefault(
                tag_id,
                self._analytics_bucket(row, catalog),
            )
            bucket["assessment_attempts"] += row["assessment_attempts"]
            bucket["redo_attempts"] += row["redo_attempts"]
            bucket["eligible_attempts"] += row["eligible_attempts"]
            bucket["correct_count"] += row["correct_count"]
            bucket["wrong_count"] += row["wrong_count"]
            bucket["blank_count"] += row["blank_count"]
            bucket["state_counts"][row["mastery_state"]] = (
                bucket["state_counts"].get(row["mastery_state"], 0) + 1
            )
            bucket["_student_ids"].add(row["student_id"])
            if include_students:
                eligible = max(1, row["eligible_attempts"])
                bucket["students"].append(
                    {
                        "student_id": row["student_id"],
                        "student_no": row.get("student_no") or "",
                        "student_name": row.get("student_name") or "",
                        "class_id": row.get("class_id") or "",
                        "class_name": row.get("class_name") or "",
                        "grade": row.get("grade") or "",
                        "mastery_state": row["mastery_state"],
                        "mastery_css_class": mastery_css_class(row["mastery_state"]),
                        "eligible_attempts": row["eligible_attempts"],
                        "correct_count": row["correct_count"],
                        "wrong_count": row["wrong_count"],
                        "blank_count": row["blank_count"],
                        "correct_rate": round(
                            row["correct_count"] / eligible,
                            3,
                        ),
                        "error_rate": round(row["wrong_count"] / eligible, 3),
                        "blank_rate": round(row["blank_count"] / eligible, 3),
                    }
                )

        result = {}
        for tag_type, items in buckets.items():
            values = []
            for item in items.values():
                eligible = item["eligible_attempts"]
                if eligible:
                    item["correct_rate"] = round(item["correct_count"] / eligible, 3)
                    item["error_rate"] = round(item["wrong_count"] / eligible, 3)
                    item["blank_rate"] = round(item["blank_count"] / eligible, 3)
                item["mastery_state"] = classify_mastery(
                    item["eligible_attempts"],
                    item["correct_rate"],
                )
                item["mastery_css_class"] = mastery_css_class(item["mastery_state"])
                item["student_count"] = len(item["_student_ids"])
                del item["_student_ids"]
                if not include_students:
                    item.pop("students", None)
                else:
                    item["students"].sort(
                        key=lambda student: (
                            student["student_no"],
                            student["student_name"],
                            student["student_id"],
                        )
                    )
                values.append(item)
            values.sort(
                key=lambda item: (
                    -item["eligible_attempts"],
                    item["tag_name"],
                    item["tag_id"],
                )
            )
            result[tag_type] = values
        return result

    def _class_mastery_rows(self, class_id):
        return self.conn.execute(
            """
            select
                m.*,
                u.display_name as student_name,
                u.student_no,
                u.class_id,
                c.name as class_name,
                c.grade
            from student_mastery_metrics m
            join users u on u.id = m.student_id
            join class_groups c on c.id = u.class_id
            where u.class_id = ?
            order by m.tag_type, m.tag_name, u.student_no, u.display_name
            """,
            (class_id,),
        ).fetchall()

    def _grade_mastery_rows(self, grade):
        return self.conn.execute(
            """
            select
                m.*,
                u.display_name as student_name,
                u.student_no,
                u.class_id,
                c.name as class_name,
                c.grade
            from student_mastery_metrics m
            join users u on u.id = m.student_id
            join class_groups c on c.id = u.class_id
            where c.grade = ?
            order by m.tag_type, m.tag_name, c.name, u.student_no
            """,
            (grade,),
        ).fetchall()

    def _grade_mastery_trends(self):
        rows = self.conn.execute(
            """
            select
                a.grade,
                a.id as assessment_id,
                a.title,
                a.scheduled_at,
                count(distinct a.class_id) as class_count,
                count(distinct r.student_id) as student_count,
                a.full_score,
                sum(r.score) as score
            from assessment_sessions a
            join student_responses r on r.assessment_id = a.id
            where a.grading_status in ('published', 'graded')
            group by a.grade, a.id
            order by a.grade, a.scheduled_at
            """
        ).fetchall()
        trends = []
        for row in rows:
            student_count = row["student_count"] or 0
            denominator = max(1, (row["full_score"] or 0) * max(1, student_count))
            trends.append(
                {
                    "grade": row["grade"],
                    "assessment_id": row["assessment_id"],
                    "title": row["title"],
                    "scheduled_at": row["scheduled_at"],
                    "class_count": row["class_count"] or 0,
                    "student_count": student_count,
                    "average_score_rate": round((row["score"] or 0) / denominator, 3),
                }
            )
        return trends

    def class_mastery_analytics(self, actor_id, assessment_id):
        self._require(actor_id, "view", "diagnostics", assessment_id)
        assessment = self.assessment_detail(actor_id, assessment_id, operation="view")
        class_row = self.conn.execute(
            "select * from class_groups where id = ?",
            (assessment["class_id"],),
        ).fetchone()
        class_aggregates = self._aggregate_mastery_rows(
            self._class_mastery_rows(assessment["class_id"]),
            include_students=True,
        )
        grade_aggregates = self._aggregate_mastery_rows(
            self._grade_mastery_rows(assessment["grade"]),
            include_students=False,
        )
        return {
            "assessment": assessment,
            "class": row_to_dict(class_row),
            "knowledge": class_aggregates["knowledge"],
            "ability": class_aggregates["ability"],
            "literacy": class_aggregates["literacy"],
            "grade_comparison": {
                "grade": assessment["grade"],
                "knowledge": grade_aggregates["knowledge"],
                "ability": grade_aggregates["ability"],
                "literacy": grade_aggregates["literacy"],
            },
        }

    def admin_mastery_analytics(self, actor_id):
        actor = self._actor(actor_id)
        if actor["role"] != "admin":
            raise PermissionDenied("Admin role required")
        grade_rows = self.conn.execute(
            """
            select
                c.grade,
                count(distinct c.id) as class_count,
                count(distinct u.id) as student_count
            from class_groups c
            left join users u
              on u.class_id = c.id
             and u.role = 'student'
             and u.status = 'active'
            group by c.grade
            order by c.grade
            """
        ).fetchall()
        grades = []
        for row in grade_rows:
            aggregates = self._aggregate_mastery_rows(
                self._grade_mastery_rows(row["grade"]),
                include_students=False,
            )
            grades.append(
                {
                    "grade": row["grade"],
                    "class_count": row["class_count"] or 0,
                    "student_count": row["student_count"] or 0,
                    "knowledge": aggregates["knowledge"],
                    "ability": aggregates["ability"],
                    "literacy": aggregates["literacy"],
                }
            )
        return {"grades": grades, "trends": self._grade_mastery_trends()}

    def assessment_overview(self, actor_id):
        user = self._actor(actor_id)
        params = []
        scope = ""
        if user["role"] != "admin":
            scope = """
              where exists (
                select 1 from teacher_classes tc
                where tc.teacher_id = ?
                  and tc.class_id = a.class_id
                  and tc.subject = 'physics'
              )
            """
            params.append(user["id"])
        rows = self.conn.execute(
            """
            select a.*, c.name as class_name,
                   (select count(*) from student_responses r where r.assessment_id = a.id and r.review_status = 'required') as review_required,
                   (select count(*) from wrong_questions w where w.assessment_id = a.id) as wrong_count
            from assessment_sessions a
            join class_groups c on c.id = a.class_id
            %s
            order by a.scheduled_at desc
            """
            % scope,
            params,
        ).fetchall()
        return rows_to_dicts(rows)

    def scan_review_items(self, assessment_id):
        rows = self.conn.execute(
            """
            select r.*, u.display_name as student_name, u.student_no, q.stem
            from student_responses r
            join users u on u.id = r.student_id
            join questions q on q.id = r.question_id
            where r.assessment_id = ? and r.review_status = 'required'
            order by u.student_no, q.id
            """,
            (assessment_id,),
        ).fetchall()
        return rows_to_dicts(rows)

    def teacher_dashboard(self, actor_id):
        assessments = self.assessment_overview(actor_id)
        assessment_id = assessments[0]["id"] if assessments else None
        review_items = self.scan_review_items(assessment_id) if assessment_id else []
        empty_mastery_analytics = {
            "assessment": None,
            "class": None,
            "knowledge": [],
            "ability": [],
            "literacy": [],
            "grade_comparison": {
                "grade": "",
                "knowledge": [],
                "ability": [],
                "literacy": [],
            },
        }
        diagnostics = (
            self.class_diagnostics(actor_id, assessment_id)
            if assessment_id
            else {
                "assessment": None,
                "participant_count": 0,
                "question_count": 0,
                "wrong_question_count": 0,
                "knowledge_error_rates": [],
                "ability_error_rates": [],
                "high_frequency_wrong_questions": [],
                "grade_average": {
                    "grade": "",
                    "student_count": 0,
                    "average_score_rate": 0.0,
                },
            }
        )
        mastery_analytics = (
            self.class_mastery_analytics(actor_id, assessment_id)
            if assessment_id
            else empty_mastery_analytics
        )
        return {
            "assessments": assessments,
            "pending_candidates": self.list_pending_candidates(),
            "review_items": review_items,
            "knowledge_nodes": self.knowledge_nodes(),
            "knowledge_edges": self.knowledge_edges(),
            "ability_tags": self.ability_tags(),
            "literacy_tags": self.literacy_tags(),
            "question_bank": self.search_questions(actor_id, {}),
            "parse_tasks": self.list_parse_tasks(actor_id),
            "parsed_items": self.parsed_question_items(
                actor_id,
                status="needs_review",
            ),
            "diagnostics": diagnostics,
            "mastery_analytics": mastery_analytics,
            "classes": self.class_groups(actor_id),
            "students": (
                self.students_for_assessment(actor_id, assessment_id)
                if assessment_id
                else []
            ),
        }

    def student_dashboard(self, actor_id, student_id=None):
        student_id = student_id or actor_id
        self._require(actor_id, "view", "wrong_questions", student_id)
        rows = self.conn.execute(
            """
            select a.id, a.title, a.status, a.published_at,
                   sum(r.score) as score, sum(r.max_score) as max_score
            from assessment_participants p
            join assessment_sessions a on a.id = p.assessment_id
            left join student_responses r on r.assessment_id = a.id and r.student_id = p.student_id
            where p.student_id = ?
              and a.grading_status = 'published'
            group by a.id
            order by a.scheduled_at desc
            """,
            (student_id,),
        ).fetchall()
        wrongs = self.list_wrong_questions_for_student(actor_id, student_id)
        mastery_metrics = self.student_mastery_metrics(actor_id, student_id)
        published_question_ids = self.student_published_question_ids(student_id)
        return {
            "assessments": rows_to_dicts(rows),
            "wrong_questions": wrongs,
            "redo_queue": [item for item in wrongs if item["redo_status"] == "pending"],
            "mastery_counts": self.mastery_counts(actor_id, student_id),
            "mastery_metrics": mastery_metrics,
            "knowledge_tree": self.student_knowledge_tree(
                student_id,
                mastery_metrics,
            ),
            "ability_mastery": self.student_tag_mastery_summary(
                "ability",
                mastery_metrics,
            ),
            "literacy_mastery": self.student_tag_mastery_summary(
                "literacy",
                mastery_metrics,
            ),
            "knowledge_navigation": self.student_navigation_modules(
                student_id,
                "knowledge",
                mastery_metrics,
                wrongs,
                published_question_ids,
            ),
            "ability_navigation": self.student_navigation_modules(
                student_id,
                "ability",
                mastery_metrics,
                wrongs,
                published_question_ids,
            ),
            "literacy_navigation": self.student_navigation_modules(
                student_id,
                "literacy",
                mastery_metrics,
                wrongs,
                published_question_ids,
            ),
            "knowledge_edges": self.knowledge_edges(),
        }

    def student_published_question_ids(self, student_id):
        rows = self.conn.execute(
            """
            select distinct r.question_id
            from student_responses r
            join assessment_sessions a on a.id = r.assessment_id
            where r.student_id = ?
              and a.grading_status = 'published'
            """,
            (student_id,),
        ).fetchall()
        return {row["question_id"] for row in rows}

    def _metric_by_tag(self, metrics):
        return {
            (metric["tag_type"], metric["tag_id"]): metric
            for metric in metrics
        }

    def _rate_text(self, metric):
        if not metric or metric.get("correct_rate") is None:
            return "正确率 --"
        return "正确率 %d%%" % round(float(metric["correct_rate"]) * 100)

    def _mastery_evidence_text(self, metric):
        if not metric:
            return "未练习｜评测 0 次｜重做 0 次"
        return (
            "%s｜评测 %s 次｜重做 %s 次｜正确 %s｜错误 %s｜空白 %s"
            % (
                self._rate_text(metric),
                metric["assessment_attempts"],
                metric["redo_attempts"],
                metric["correct_count"],
                metric["wrong_count"],
                metric["blank_count"],
            )
        )

    def _mastery_view_model(self, metric, tag_name, manual_mark=None):
        calculated_state = metric["mastery_state"] if metric else "未练习"
        manual_level = manual_mark["level"] if manual_mark else ""
        display_state = manual_level or calculated_state
        return {
            "tag_name": tag_name,
            "calculated_mastery_state": calculated_state,
            "display_mastery_state": display_state,
            "manual_mastery_level": manual_level,
            "manual_mastery_note": manual_mark["note"] if manual_mark else "",
            "mastery_css_class": mastery_css_class(calculated_state),
            "mastery_evidence_text": self._mastery_evidence_text(metric),
            "mastery_rate_text": self._rate_text(metric),
            "mastery_metric": metric,
        }

    def student_tag_mastery_summary(self, tag_type, metrics):
        by_tag = self._metric_by_tag(metrics)
        tags = self.ability_tags() if tag_type == "ability" else self.literacy_tags()
        summary = []
        for tag in tags:
            metric = by_tag.get((tag_type, tag["id"]))
            view = self._mastery_view_model(metric, tag["name"])
            view.update(
                {
                    "tag_type": tag_type,
                    "tag_id": tag["id"],
                    "tag_name": tag["name"],
                    "stable_code": tag.get("stable_code", ""),
                    "related_questions": (
                        self.related_questions_for_ability(tag["id"])
                        if tag_type == "ability"
                        else self.related_questions_for_literacy(tag["id"])
                    ),
                }
            )
            summary.append(view)
        return summary

    def _student_navigation_tags(self, tag_type):
        if tag_type == "knowledge":
            paths = self.knowledge_node_paths()
            tags = []
            for node in self.knowledge_nodes():
                tag = dict(node)
                tag["tag_id"] = node["id"]
                tag["tag_name"] = node["name"]
                tag["path"] = paths.get(node["id"], [node["name"]])
                tag["path_text"] = " > ".join(tag["path"])
                tags.append(tag)
            return tags
        if tag_type == "ability":
            return [
                {
                    **tag,
                    "tag_id": tag["id"],
                    "tag_name": tag["name"],
                    "path_text": tag.get("stable_code", ""),
                }
                for tag in self.ability_tags()
            ]
        return [
            {
                **tag,
                "tag_id": tag["id"],
                "tag_name": tag["name"],
                "path_text": tag.get("stable_code", ""),
            }
            for tag in self.literacy_tags()
        ]

    def _published_related_questions(self, tag_type, tag_id, published_question_ids):
        related = self._related_questions_for_tag(tag_type, tag_id)
        return [
            question
            for question in related
            if question["id"] in published_question_ids
        ]

    def _wrong_has_tag(self, wrong, tag_type, tag_id):
        key = "%s_tags" % tag_type
        return any(tag["tag_id"] == tag_id for tag in wrong.get(key, []))

    def _wrong_needs_redo(self, wrong):
        status = (
            wrong.get("latest_redo_status")
            or wrong.get("redo_status")
            or "pending"
        )
        return status != "done"

    def student_navigation_modules(
        self,
        student_id,
        tag_type,
        mastery_metrics,
        wrongs,
        published_question_ids,
    ):
        by_tag = self._metric_by_tag(mastery_metrics)
        marks = {}
        if tag_type == "knowledge":
            marks = {
                row["knowledge_node_id"]: row_to_dict(row)
                for row in self.conn.execute(
                    "select * from knowledge_mastery_marks where student_id = ?",
                    (student_id,),
                ).fetchall()
            }
        modules = []
        for tag in self._student_navigation_tags(tag_type):
            tag_id = tag["tag_id"]
            metric = by_tag.get((tag_type, tag_id))
            view = self._mastery_view_model(
                metric,
                tag["tag_name"],
                marks.get(tag_id),
            )
            tagged_wrongs = [
                wrong for wrong in wrongs
                if self._wrong_has_tag(wrong, tag_type, tag_id)
            ]
            related_questions = self._published_related_questions(
                tag_type,
                tag_id,
                published_question_ids,
            )
            if (
                not related_questions
                and not tagged_wrongs
                and not metric
                and tag_type != "knowledge"
            ):
                continue
            module = {
                **view,
                "tag_type": tag_type,
                "tag_id": tag_id,
                "tag_name": tag["tag_name"],
                "path_text": tag.get("path_text", ""),
                "stable_code": tag.get("stable_code", ""),
                "related_questions": related_questions,
                "wrong_questions": tagged_wrongs,
                "redo_tasks": [
                    wrong for wrong in tagged_wrongs
                    if self._wrong_needs_redo(wrong)
                ],
            }
            modules.append(module)
        return modules

    def student_knowledge_tree(self, student_id, mastery_metrics=None):
        paths = self.knowledge_node_paths()
        by_tag = self._metric_by_tag(mastery_metrics or [])
        marks = {
            row["knowledge_node_id"]: row_to_dict(row)
            for row in self.conn.execute(
                "select * from knowledge_mastery_marks where student_id = ?",
                (student_id,),
            ).fetchall()
        }
        nodes = []
        for node in self.knowledge_nodes():
            related = self.related_questions_for_knowledge(node["id"])
            mark = marks.get(node["id"])
            node = dict(node)
            node["path"] = paths.get(node["id"], [node["name"]])
            node["path_text"] = " > ".join(node["path"])
            metric = by_tag.get(("knowledge", node["id"]))
            mastery_view = self._mastery_view_model(
                metric,
                node["name"],
                mark,
            )
            node.update(mastery_view)
            node["mastery_level"] = mastery_view["display_mastery_state"]
            node["related_questions"] = related
            node["related_question_count"] = len(related)
            nodes.append(node)
        return nodes

    def class_groups(self, actor_id):
        user = self._actor(actor_id)
        if user["role"] == "admin":
            rows = self.conn.execute(
                "select * from class_groups order by grade, name"
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                select c.*
                from class_groups c
                join teacher_classes tc on tc.class_id = c.id
                where tc.teacher_id = ? and tc.subject = 'physics'
                order by c.grade, c.name
                """,
                (user["id"],),
            ).fetchall()
        return rows_to_dicts(
            rows
        )

    def students_for_assessment(self, actor_id, assessment_id):
        self._require(actor_id, "view", "assessment", assessment_id)
        return rows_to_dicts(
            self.conn.execute(
                """
                select u.id, u.display_name, u.student_no, u.class_id, c.name as class_name, c.grade
                from assessment_participants p
                join users u on u.id = p.student_id
                join class_groups c on c.id = u.class_id
                where p.assessment_id = ?
                order by c.grade, c.name, u.student_no
                """,
                (assessment_id,),
            ).fetchall()
        )

    def mastery_counts(self, actor_id, student_id=None):
        student_id = student_id or actor_id
        counts = {"未掌握": 0, "基本掌握": 0, "已掌握": 0, "需教师讲解": 0, "未标记": 0}
        wrongs = self.list_wrong_questions_for_student(actor_id, student_id)
        for wrong in wrongs:
            level = wrong.get("mastery_level") or "未标记"
            counts[level] = counts.get(level, 0) + 1
        return counts

    def record_runtime_capability_checks(self, actor_id, checks=None):
        actor = self._actor(actor_id)
        if actor["role"] != "admin":
            raise PermissionDenied("Admin role required")
        if checks is None:
            checks = []
            for check in check_runtime_capabilities():
                if check["capability_id"] == "mineru-api":
                    checks.append(self._mineru_api_runtime_capability(actor) or check)
                else:
                    checks.append(check)
        stored = []
        for check in checks:
            check_id = "runtime-check-" + uuid.uuid4().hex[:12]
            self.conn.execute(
                """
                insert into runtime_capability_checks(
                    id, school_id, capability_id, status, label, detail,
                    version, checked_by
                ) values(?,?,?,?,?,?,?,?)
                """,
                (
                    check_id,
                    actor["school_id"],
                    check["capability_id"],
                    check["status"],
                    check.get("label", ""),
                    check.get("detail", ""),
                    check.get("version", ""),
                    actor_id,
                ),
            )
            stored.append({**check, "id": check_id})
        self.audit(
            actor_id,
            "runtime_capability_checked",
            "runtime",
            "production-readiness",
            {"count": len(stored)},
        )
        self.conn.commit()
        return stored

    def latest_runtime_capability_checks(self, actor_id):
        actor = self._actor(actor_id)
        if actor["role"] != "admin":
            raise PermissionDenied("Admin role required")
        rows = self.conn.execute(
            """
            select r.*
            from runtime_capability_checks r
            join (
                select capability_id, max(checked_at) as checked_at
                from runtime_capability_checks
                where school_id = ?
                group by capability_id
            ) latest
              on latest.capability_id = r.capability_id
             and latest.checked_at = r.checked_at
            where r.school_id = ?
            order by r.capability_id
            """,
            (actor["school_id"], actor["school_id"]),
        ).fetchall()
        return rows_to_dicts(rows)

    def production_readiness_dashboard(self, actor_id):
        actor = self._actor(actor_id)
        if actor["role"] != "admin":
            raise PermissionDenied("Admin role required")
        latest = {
            row["capability_id"]: row
            for row in self.latest_runtime_capability_checks(actor_id)
        }
        runtime_checks = []
        for check in check_runtime_capabilities():
            persisted = latest.get(check["capability_id"])
            resolved = persisted or check
            if check["capability_id"] == "mineru-api":
                resolved = self._mineru_api_runtime_capability(actor) or resolved
            runtime_checks.append(resolved)
        return {"runtime_checks": runtime_checks}

    def _mineru_api_runtime_capability(self, actor):
        row = self.conn.execute(
            """
            select *
            from provider_configs
            where school_id = ?
              and provider_kind = 'mineru_api'
              and enabled = 1
            order by updated_at desc, created_at desc
            limit 1
            """,
            (actor["school_id"],),
        ).fetchone()
        if row is None:
            return None
        if not row["api_endpoint"]:
            return {
                "capability_id": "mineru-api",
                "label": "MinerU API",
                "status": "missing_credential",
                "detail": "MinerU API provider is missing endpoint",
                "version": row["model_name"] or "",
            }
        if not row["secret_ciphertext"]:
            return {
                "capability_id": "mineru-api",
                "label": "MinerU API",
                "status": "missing_credential",
                "detail": "MinerU API provider is missing encrypted secret",
                "version": row["model_name"] or "",
            }
        try:
            self._provider_secret_store().decrypt(row["secret_ciphertext"])
        except Exception:
            return {
                "capability_id": "mineru-api",
                "label": "MinerU API",
                "status": "failed",
                "detail": "MinerU API provider secret cannot be decrypted",
                "version": row["model_name"] or "",
            }
        return {
            "capability_id": "mineru-api",
            "label": "MinerU API",
            "status": "ready",
            "detail": "MinerU API provider configured for %s"
            % row["provider_name"],
            "version": row["model_name"] or "",
        }

    def _provider_secret_store(self):
        return ProviderSecretStore.for_connection(self.conn)

    def _provider_config_payload(self, row):
        config = row_to_dict(row)
        if not config:
            return None
        config.pop("secret_ciphertext", None)
        config["enabled"] = bool(config.get("enabled"))
        return config

    def _provider_config_row(self, provider_config_id, school_id):
        row = self.conn.execute(
            """
            select *
            from provider_configs
            where id = ? and school_id = ?
            """,
            (provider_config_id, school_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFound("Provider config not found: %s" % provider_config_id)
        return row

    def provider_configs(self, actor_id):
        actor = self._require_admin_actor(actor_id)
        rows = self.conn.execute(
            """
            select *
            from provider_configs
            where school_id = ?
            order by provider_kind, provider_name, model_name
            """,
            (actor["school_id"],),
        ).fetchall()
        return [self._provider_config_payload(row) for row in rows]

    def save_provider_config(
        self,
        actor_id,
        provider_kind,
        provider_name,
        model_name="",
        secret="",
        api_endpoint="",
        enabled=False,
        daily_call_limit=1000,
        monthly_budget_cents=0,
        per_call_max_cents=0,
        input_cost_per_1k_cents=0,
        output_cost_per_1k_cents=0,
    ):
        actor = self._require_admin_actor(actor_id)
        provider_kind = (provider_kind or "").strip()
        provider_name = (provider_name or "").strip()
        model_name = (model_name or "").strip()
        if provider_kind not in ("llm", "mineru_api"):
            raise ValueError("Unsupported provider_kind: %s" % provider_kind)
        if not provider_name:
            raise ValueError("provider_name is required")
        existing = self.conn.execute(
            """
            select *
            from provider_configs
            where school_id = ? and provider_kind = ?
              and provider_name = ? and model_name = ?
            """,
            (actor["school_id"], provider_kind, provider_name, model_name),
        ).fetchone()
        config_id = existing["id"] if existing else "provider-" + uuid.uuid4().hex[:12]
        secret_ciphertext = existing["secret_ciphertext"] if existing else ""
        secret_masked = existing["secret_masked"] if existing else ""
        if secret:
            secret_ciphertext = self._provider_secret_store().encrypt(secret)
            secret_masked = mask_secret(secret)
        self.conn.execute(
            """
            insert into provider_configs(
                id, school_id, provider_kind, provider_name, model_name,
                api_endpoint, secret_ciphertext, secret_masked, enabled,
                daily_call_limit, monthly_budget_cents, per_call_max_cents,
                input_cost_per_1k_cents, output_cost_per_1k_cents,
                created_by, updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)
            on conflict(school_id, provider_kind, provider_name, model_name)
            do update set api_endpoint = excluded.api_endpoint,
                          secret_ciphertext = excluded.secret_ciphertext,
                          secret_masked = excluded.secret_masked,
                          enabled = excluded.enabled,
                          daily_call_limit = excluded.daily_call_limit,
                          monthly_budget_cents = excluded.monthly_budget_cents,
                          per_call_max_cents = excluded.per_call_max_cents,
                          input_cost_per_1k_cents = excluded.input_cost_per_1k_cents,
                          output_cost_per_1k_cents = excluded.output_cost_per_1k_cents,
                          updated_at = current_timestamp
            """,
            (
                config_id,
                actor["school_id"],
                provider_kind,
                provider_name,
                model_name,
                api_endpoint or "",
                secret_ciphertext,
                secret_masked,
                1 if enabled else 0,
                int(daily_call_limit or 0),
                float(monthly_budget_cents or 0),
                float(per_call_max_cents or 0),
                float(input_cost_per_1k_cents or 0),
                float(output_cost_per_1k_cents or 0),
                actor_id,
            ),
        )
        self.audit(
            actor_id,
            "provider_config_saved",
            "provider_config",
            config_id,
            {
                "provider_kind": provider_kind,
                "provider_name": provider_name,
                "model_name": model_name,
                "enabled": bool(enabled),
            },
        )
        self.conn.commit()
        row = self._provider_config_row(config_id, actor["school_id"])
        return self._provider_config_payload(row)

    def _provider_usage_totals(self, provider_config_id):
        daily = self.conn.execute(
            """
            select count(*) as calls
            from provider_usage_events
            where provider_config_id = ?
              and outcome <> 'blocked'
              and date(created_at) = date('now')
            """,
            (provider_config_id,),
        ).fetchone()
        monthly = self.conn.execute(
            """
            select coalesce(sum(estimated_cost_cents), 0) as cost
            from provider_usage_events
            where provider_config_id = ?
              and outcome <> 'blocked'
              and strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
            """,
            (provider_config_id,),
        ).fetchone()
        return int(daily["calls"] or 0), float(monthly["cost"] or 0)

    def provider_budget_status(
        self,
        actor_id,
        provider_config_id,
        input_units=0,
        output_units=0,
    ):
        actor = self._actor(actor_id)
        row = self._provider_config_row(provider_config_id, actor["school_id"])
        estimate = estimate_cost_cents(
            input_units,
            output_units,
            row["input_cost_per_1k_cents"],
            row["output_cost_per_1k_cents"],
        )
        current_daily_calls, current_monthly_cost = self._provider_usage_totals(
            provider_config_id
        )
        if not row["enabled"]:
            status = {"allowed": False, "reason": "provider_disabled"}
        else:
            status = budget_status(
                row["daily_call_limit"],
                row["monthly_budget_cents"],
                current_daily_calls,
                current_monthly_cost,
                estimate,
                row["per_call_max_cents"],
            )
        return {
            **status,
            "estimated_cost_cents": round(estimate, 4),
            "current_daily_calls": current_daily_calls,
            "current_monthly_cost_cents": round(current_monthly_cost, 4),
        }

    def record_provider_usage(
        self,
        actor_id,
        provider_config_id,
        request_type,
        prompt_version="",
        input_units=0,
        output_units=0,
        page_count=0,
        estimated_cost_cents=None,
        outcome="success",
        error_category="",
        detail=None,
    ):
        actor = self._actor(actor_id)
        config = self._provider_config_row(provider_config_id, actor["school_id"])
        if estimated_cost_cents is None:
            estimated_cost_cents = estimate_cost_cents(
                input_units,
                output_units,
                config["input_cost_per_1k_cents"],
                config["output_cost_per_1k_cents"],
            )
        event_id = "provider-usage-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into provider_usage_events(
                id, school_id, provider_config_id, provider_kind,
                provider_name, model_name, request_type, prompt_version,
                input_units, output_units, page_count, estimated_cost_cents,
                outcome, error_category, detail_json, created_by
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                actor["school_id"],
                provider_config_id,
                config["provider_kind"],
                config["provider_name"],
                config["model_name"],
                request_type,
                prompt_version or "",
                int(input_units or 0),
                int(output_units or 0),
                int(page_count or 0),
                float(estimated_cost_cents or 0),
                outcome,
                error_category or "",
                dumps(detail or {}),
                actor_id,
            ),
        )
        for window_type, window_start in (
            ("daily", self.conn.execute("select date('now')").fetchone()[0]),
            (
                "monthly",
                self.conn.execute("select strftime('%Y-%m-01', 'now')").fetchone()[0],
            ),
        ):
            window_id = "provider-window-" + uuid.uuid4().hex[:12]
            self.conn.execute(
                """
                insert into provider_budget_windows(
                    id, school_id, provider_config_id, window_type,
                    window_start, call_count, cost_cents
                ) values(?,?,?,?,?,?,?)
                on conflict(provider_config_id, window_type, window_start)
                do update set call_count = call_count + excluded.call_count,
                              cost_cents = cost_cents + excluded.cost_cents,
                              updated_at = current_timestamp
                """,
                (
                    window_id,
                    actor["school_id"],
                    provider_config_id,
                    window_type,
                    window_start,
                    0 if outcome == "blocked" else 1,
                    0 if outcome == "blocked" else float(estimated_cost_cents or 0),
                ),
            )
        self.audit(
            actor_id,
            "provider_usage_recorded",
            "provider_config",
            provider_config_id,
            {
                "request_type": request_type,
                "outcome": outcome,
                "estimated_cost_cents": float(estimated_cost_cents or 0),
            },
        )
        self.conn.commit()
        return row_to_dict(
            self.conn.execute(
                "select * from provider_usage_events where id = ?",
                (event_id,),
            ).fetchone()
        )

    def test_provider_connection(self, actor_id, provider_config_id):
        actor = self._require_admin_actor(actor_id)
        row = self._provider_config_row(provider_config_id, actor["school_id"])
        status = "ready"
        detail = "密钥可解密，预算策略可执行；远程连通性需在显式烟测中执行。"
        if not row["enabled"]:
            status = "disabled"
            detail = "Provider 已停用。"
        elif not row["secret_ciphertext"]:
            status = "missing_secret"
            detail = "缺少加密保存的 provider secret。"
        elif row["provider_kind"] == "mineru_api" and not row["api_endpoint"]:
            status = "missing_endpoint"
            detail = "MinerU API 需要配置 endpoint。"
        else:
            try:
                self._provider_secret_store().decrypt(row["secret_ciphertext"])
            except Exception:
                status = "failed"
                detail = "Provider secret 无法解密，请轮换密钥。"
        self.conn.execute(
            """
            update provider_configs
            set last_test_status = ?, last_test_detail = ?,
                updated_at = current_timestamp
            where id = ?
            """,
            (status, detail, provider_config_id),
        )
        self.audit(
            actor_id,
            "provider_connection_tested",
            "provider_config",
            provider_config_id,
            {"status": status},
        )
        self.conn.commit()
        return self._provider_config_payload(
            self._provider_config_row(provider_config_id, actor["school_id"])
        )

    def _auth_provider_payload(self, row):
        config = row_to_dict(row)
        config.pop("secret_ciphertext", None)
        config["client_config"] = loads(config.pop("client_config_json"), {})
        config["enabled"] = bool(config["enabled"])
        return config

    def save_oidc_provider_config(
        self,
        actor_id,
        provider_name,
        issuer,
        client_id,
        client_secret,
        authorization_endpoint,
        token_endpoint="",
        userinfo_endpoint="",
        scope="openid profile email",
        enabled=False,
        binding_policy="existing_user_only",
    ):
        actor = self._require_admin_actor(actor_id)
        config_id = "oidc-provider-" + uuid.uuid4().hex[:12]
        client_config = {
            "client_id": client_id,
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": token_endpoint,
            "userinfo_endpoint": userinfo_endpoint,
            "scope": scope,
            "binding_policy": binding_policy,
        }
        secret_ciphertext = (
            self._provider_secret_store().encrypt(client_secret)
            if client_secret
            else ""
        )
        self.conn.execute(
            """
            insert into auth_provider_configs(
                id, school_id, provider_name, issuer, client_config_json,
                secret_ciphertext, enabled
            ) values(?,?,?,?,?,?,?)
            """,
            (
                config_id,
                actor["school_id"],
                provider_name,
                issuer,
                dumps(client_config),
                secret_ciphertext,
                1 if enabled else 0,
            ),
        )
        self.audit(
            actor_id,
            "oidc_provider_config_saved",
            "auth_provider_config",
            config_id,
            {
                "provider_name": provider_name,
                "issuer": issuer,
                "enabled": bool(enabled),
                "binding_policy": binding_policy,
            },
        )
        self.conn.commit()
        return self._auth_provider_payload(
            self.conn.execute(
                "select * from auth_provider_configs where id = ?",
                (config_id,),
            ).fetchone()
        )

    def start_sso_login(self, provider_config_id, redirect_uri):
        provider = self.conn.execute(
            """
            select *
            from auth_provider_configs
            where id = ? and enabled = 1
            """,
            (provider_config_id,),
        ).fetchone()
        if provider is None:
            raise ResourceNotFound("Enabled SSO provider not found")
        client_config = loads(provider["client_config_json"], {})
        login_state = create_oidc_login_state()
        state_id = "sso-state-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into sso_login_states(
                id, school_id, provider_config_id, state, nonce,
                code_verifier, redirect_uri, status
            ) values(?,?,?,?,?,?,?,?)
            """,
            (
                state_id,
                provider["school_id"],
                provider_config_id,
                login_state["state"],
                login_state["nonce"],
                login_state["code_verifier"],
                redirect_uri,
                "pending",
            ),
        )
        self.conn.commit()
        authorization_url = build_oidc_authorization_url(
            client_config,
            redirect_uri,
            login_state,
        )
        return {
            "state": login_state["state"],
            "nonce": login_state["nonce"],
            "authorization_url": authorization_url,
        }

    def enabled_oidc_provider(self):
        row = self.conn.execute(
            """
            select *
            from auth_provider_configs
            where enabled = 1
            order by created_at desc
            limit 1
            """
        ).fetchone()
        if row is None:
            return None
        payload = row_to_dict(row)
        payload["client_config"] = loads(payload["client_config_json"], {})
        payload["client_secret"] = self._provider_secret_store().decrypt(
            payload.get("secret_ciphertext", "")
        )
        return payload

    def _record_external_binding(
        self,
        school_id,
        provider_config_id,
        claims,
        status,
        local_user_id=None,
        detail=None,
    ):
        binding_id = "external-binding-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into external_identity_bindings(
                id, school_id, provider, provider_config_id, issuer, subject,
                external_id, email, display_name, local_user_id, status,
                detail_json
            ) values(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                binding_id,
                school_id,
                "oidc",
                provider_config_id,
                claims["issuer"],
                claims["subject"],
                claims["external_id"],
                claims["email"],
                claims["display_name"],
                local_user_id,
                status,
                dumps(detail or {}),
            ),
        )
        return binding_id

    def complete_sso_callback(self, state, claims):
        state_row = self.conn.execute(
            """
            select *
            from sso_login_states
            where state = ?
            """,
            (state,),
        ).fetchone()
        if state_row is None or state_row["status"] != "pending":
            raise StateConflict("SSO state is invalid or already consumed")
        provider = self.conn.execute(
            "select * from auth_provider_configs where id = ?",
            (state_row["provider_config_id"],),
        ).fetchone()
        if provider is None or not provider["enabled"]:
            raise PermissionDenied("SSO provider is disabled")
        normalized = normalize_oidc_claims(claims)
        if normalized["issuer"] and normalized["issuer"] != provider["issuer"]:
            raise PermissionDenied("SSO issuer mismatch")
        client_config = loads(provider["client_config_json"], {})
        user = self.conn.execute(
            """
            select *
            from users
            where school_id = ? and username = ? and status = 'active'
            """,
            (provider["school_id"], normalized["local_username"]),
        ).fetchone()
        if user is None:
            self._record_external_binding(
                provider["school_id"],
                provider["id"],
                normalized,
                "blocked",
                detail={
                    "reason": "local_user_not_found",
                    "binding_policy": client_config.get("binding_policy"),
                },
            )
            self.conn.execute(
                """
                update sso_login_states
                set status = 'consumed', consumed_at = current_timestamp
                where id = ?
                """,
                (state_row["id"],),
            )
            self.conn.commit()
            raise PermissionDenied("SSO user is not bound to a local account")
        existing = self.conn.execute(
            """
            select *
            from identity_accounts
            where provider = 'oidc'
              and issuer = ?
              and subject = ?
            """,
            (normalized["issuer"], normalized["subject"]),
        ).fetchone()
        if existing is None:
            identity_id = "identity-" + uuid.uuid4().hex[:12]
            self.conn.execute(
                """
                insert into identity_accounts(
                    id, user_id, provider, issuer, subject, external_id, status
                ) values(?,?,?,?,?,?,?)
                """,
                (
                    identity_id,
                    user["id"],
                    "oidc",
                    normalized["issuer"],
                    normalized["subject"],
                    normalized["external_id"],
                    "active",
                ),
            )
        else:
            identity_id = existing["id"]
        binding_id = self._record_external_binding(
            provider["school_id"],
            provider["id"],
            normalized,
            "active",
            local_user_id=user["id"],
        )
        self.conn.execute(
            """
            update sso_login_states
            set status = 'consumed', consumed_at = current_timestamp
            where id = ?
            """,
            (state_row["id"],),
        )
        self.conn.execute(
            """
            insert into identity_audit_logs(
                id, school_id, actor_id, action, user_id, detail_json
            ) values(?,?,?,?,?,?)
            """,
            (
                "identity-audit-" + uuid.uuid4().hex[:12],
                provider["school_id"],
                None,
                "sso_identity_bound",
                user["id"],
                dumps(
                    {
                        "provider_config_id": provider["id"],
                        "binding_id": binding_id,
                    }
                ),
            ),
        )
        self.conn.commit()
        return {
            "user": row_to_dict(user),
            "identity_account_id": identity_id,
            "binding_id": binding_id,
        }

    def admin_dashboard(self, actor_id=None):
        actor_id = actor_id or "user-admin"
        tables = [
            "users",
            "class_groups",
            "llm_provider_configs",
            "document_parse_tasks",
            "export_tasks",
            "provider_usage_events",
            "privacy_consent_records",
            "audit_events",
            "identity_accounts",
            "auth_provider_configs",
        ]
        dashboard = {table: rows_to_dicts(self.conn.execute("select * from %s" % table).fetchall()) for table in tables}
        dashboard["user_management"] = self.admin_user_management_rows()
        dashboard["knowledge_nodes"] = self.all_knowledge_nodes()
        dashboard["knowledge_edges"] = self.all_knowledge_edges()
        dashboard["ability_tags"] = self.all_ability_tags()
        dashboard["literacy_tags"] = self.all_literacy_tags()
        dashboard["taxonomy_summary"] = self.taxonomy_summary()
        dashboard["taxonomy_sources"] = self.taxonomy_sources()
        dashboard["ontology_versions"] = self.ontology_versions()
        dashboard["active_ontology_version"] = self.active_ontology_version()
        dashboard["production_readiness"] = self.production_readiness_dashboard(actor_id)
        dashboard["provider_configs"] = self.provider_configs(actor_id)
        dashboard["mastery_analytics"] = (
            self.admin_mastery_analytics(actor_id)
            if actor_id
            else {"grades": [], "trends": []}
        )
        return dashboard

    def taxonomy_sources(self):
        return rows_to_dicts(
            self.conn.execute(
                """
                select * from taxonomy_sources
                order by source_type, volume_code, title
                """
            ).fetchall()
        )

    def taxonomy_summary(self):
        installed = self.conn.execute(
            """
            select 1 from knowledge_ontology_versions
            where id = ?
            """,
            (taxonomy.DEFAULT_ONTOLOGY_ID,),
        ).fetchone() is not None

        def counts(table):
            row = self.conn.execute(
                """
                select count(*) as total,
                       sum(
                           case
                               when enabled = 1 and deleted_at is null
                               then 1 else 0
                           end
                       ) as active
                from %s
                where is_default = 1
                """
                % table
            ).fetchone()
            return {
                "total": row["total"],
                "active": row["active"] or 0,
            }

        return {
            "version": taxonomy.DEFAULT_TAXONOMY_VERSION,
            "installed": installed,
            "knowledge": counts("knowledge_nodes"),
            "abilities": counts("ability_tags"),
            "literacy": counts("literacy_tags"),
            "sources": self.taxonomy_sources(),
        }

    def install_default_taxonomy(self, actor_id, publish=False):
        actor = self._actor(actor_id)
        if actor["role"] != "admin":
            raise PermissionDenied("Admin role required")
        return taxonomy.install_default_taxonomy(
            self.conn,
            school_id=actor["school_id"],
            actor_id=actor_id,
            publish=publish,
        )

    def admin_user_management_rows(self):
        rows = self.conn.execute(
            """
            select u.id, u.username, u.display_name, u.role, u.status,
                   case
                     when u.role = 'student' then c.grade
                     when u.role = 'teacher' then group_concat(distinct tcg.grade)
                     else ''
                   end as grade,
                   case
                     when u.role = 'student' then c.name
                     when u.role = 'teacher' then group_concat(distinct tcg.name)
                     else '全校'
                   end as class_name,
                   case
                     when u.role = 'teacher' then group_concat(distinct tcg.id)
                     else ''
                   end as class_ids
            from users u
            left join class_groups c on c.id = u.class_id
            left join teacher_classes tc on tc.teacher_id = u.id
            left join class_groups tcg on tcg.id = tc.class_id
            group by u.id
            order by u.role, u.username
            """
        ).fetchall()
        return rows_to_dicts(rows)

    def import_student(self, actor_id, username, display_name, student_no, class_id, temp_password_hash):
        user_id = "stu-" + uuid.uuid4().hex[:8]
        self.conn.execute(
            """
            insert into users(
                id, school_id, username, display_name, role, class_id, student_no,
                enrollment_year, status, password_hash, must_change_password
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                self.school_id_for_actor(actor_id),
                username,
                display_name,
                "student",
                class_id,
                student_no,
                "2024",
                "active",
                temp_password_hash,
                1,
            ),
        )
        self.audit(actor_id, "student_imported", "user", user_id, {"username": username})
        self.conn.commit()
        return user_id

    def create_teacher(self, actor_id, username, display_name, temp_password_hash):
        existing = self.conn.execute(
            "select id from users where username = ?",
            (username,),
        ).fetchone()
        if existing is not None:
            raise StateConflict("Username already exists")
        user_id = "tea-" + uuid.uuid4().hex[:8]
        self.conn.execute(
            """
            insert into users(
                id, school_id, username, display_name, role, class_id, student_no,
                enrollment_year, status, password_hash, must_change_password
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                self.school_id_for_actor(actor_id),
                username,
                display_name,
                "teacher",
                None,
                None,
                None,
                "active",
                temp_password_hash,
                1,
            ),
        )
        self.audit(actor_id, "teacher_imported", "user", user_id, {"username": username})
        self.conn.commit()
        return user_id

    def set_teacher_classes(self, actor_id, teacher_id, class_ids):
        target = self.conn.execute(
            "select id, role, school_id from users where id = ? and status = 'active'",
            (teacher_id,),
        ).fetchone()
        if target is None:
            raise ResourceNotFound("Teacher not found")
        if target["role"] != "teacher":
            raise InvalidRequest("Target user is not a teacher")

        normalized = []
        seen = set()
        for raw in class_ids or []:
            if not raw:
                continue
            value = str(raw)
            if value in seen:
                continue
            seen.add(value)
            normalized.append(value)

        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            rows = self.conn.execute(
                f"select id from class_groups where id in ({placeholders})",
                normalized,
            ).fetchall()
            existing_class_ids = {row["id"] for row in rows}
            missing = [value for value in normalized if value not in existing_class_ids]
            if missing:
                raise InvalidRequest(
                    "Unknown class_id: %s" % ", ".join(sorted(missing))
                )

        existing_rows = self.conn.execute(
            """
            select class_id from teacher_classes
            where teacher_id = ? and subject = 'physics'
            """,
            (teacher_id,),
        ).fetchall()
        existing_set = {row["class_id"] for row in existing_rows}
        desired_set = set(normalized)

        to_assign = sorted(desired_set - existing_set)
        to_remove = sorted(existing_set - desired_set)

        if to_assign:
            self.conn.executemany(
                """
                insert into teacher_classes(teacher_id, class_id, subject)
                values(?,?, 'physics')
                """,
                [(teacher_id, class_id) for class_id in to_assign],
            )
        if to_remove:
            placeholders = ",".join("?" for _ in to_remove)
            self.conn.execute(
                f"""
                delete from teacher_classes
                where teacher_id = ? and subject = 'physics'
                  and class_id in ({placeholders})
                """,
                (teacher_id, *to_remove),
            )

        self.audit(
            actor_id,
            "teacher_classes_assigned",
            "user",
            teacher_id,
            {
                "assigned": to_assign,
                "removed": to_remove,
            },
        )
        self.conn.commit()
        return {"assigned": to_assign, "removed": to_remove}

    def export_backup(self, actor_id):
        tables = backup.export_tables(self.conn)
        self.audit(
            actor_id,
            "backup_exported",
            "school",
            self.school_id_for_actor(actor_id),
            {"tables": backup.BACKUP_TABLES},
        )
        self.conn.commit()
        tables["audit_events"] = rows_to_dicts(
            self.conn.execute("select * from audit_events").fetchall()
        )
        tables["_metadata"] = {
            "format": "highschoolphysics-backup-v2",
            "tables": backup.BACKUP_TABLES,
        }
        return tables

    def restore_backup(self, payload):
        return backup.restore_backup(self.conn, payload)

    def consistency_check(self):
        return backup.consistency_check(self.conn)
