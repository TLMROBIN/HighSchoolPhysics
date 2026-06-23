import http.client
import json
from pathlib import Path
import threading
from urllib.parse import urlencode

from http.server import ThreadingHTTPServer

from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.security import hash_password
from highschoolphysics.taxonomy import DEFAULT_ONTOLOGY_ID
from highschoolphysics.server import PhysicsHandler


class LivePhysicsServer:
    def __init__(self, db_path, demo_mode=True, seed=True):
        self.db_path = Path(db_path)
        conn = connect(self.db_path)
        initialize_database(conn)
        if seed:
            seed_demo_data(conn)
        conn.close()
        handler = type(
            "TestPhysicsHandler",
            (PhysicsHandler,),
            {"db_path": self.db_path, "demo_mode": demo_mode},
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def address(self):
        return self.server.server_address

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(*self.address, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            conn.close()

    def login(self, username, password):
        body = urlencode({"username": username, "password": password})
        status, headers, payload = self.request(
            "POST",
            "/login",
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        return status, cookie, payload

    def post_json(self, path, payload, cookie):
        return self.request(
            "POST",
            path,
            json.dumps(payload).encode("utf-8"),
            {"Content-Type": "application/json", "Cookie": cookie},
        )


def seed_other_class(conn):
    conn.execute(
        """
        insert into class_groups(
            id, school_id, name, grade, school_year, status
        ) values(?,?,?,?,?,?)
        """,
        (
            "class-physics-2",
            "school-demo",
            "高二(2)班",
            "高二",
            "2025-2026",
            "active",
        ),
    )
    conn.executemany(
        """
        insert into users(
            id, school_id, username, display_name, role, class_id, student_no,
            enrollment_year, status, password_hash, must_change_password
        ) values(?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                "user-teacher-wang",
                "school-demo",
                "teacher_wang",
                "王老师",
                "teacher",
                None,
                None,
                None,
                "active",
                hash_password("teacher123"),
                0,
            ),
            (
                "stu-2001",
                "school-demo",
                "stu_2001",
                "赵同学",
                "student",
                "class-physics-2",
                "2001",
                "2024",
                "active",
                hash_password("student123"),
                0,
            ),
        ],
    )
    conn.execute(
        """
        insert into teacher_classes(teacher_id, class_id, subject)
        values(?,?,?)
        """,
        ("user-teacher-wang", "class-physics-2", "physics"),
    )
    conn.execute(
        """
        insert into assessment_sessions(
            id, school_id, title, term, grade, class_id, scheduled_at, source,
            full_score, paper_id, answer_card_template_id, ontology_version_id,
            mastery_inference_version_id, status, grading_status,
            statistics_status
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "assess-week-2",
            "school-demo",
            "高二二班权限测试",
            "2025-2026下",
            "高二",
            "class-physics-2",
            "2026-06-06 08:00:00",
            "周测",
            4,
            "paper-week-1",
            "card-template-1",
            DEFAULT_ONTOLOGY_ID,
            "mastery-manual-v1",
            "待复核",
            "待复核",
            "not_started",
        ),
    )
    conn.execute(
        """
        insert into assessment_participants(assessment_id, student_id, status)
        values(?,?,?)
        """,
        ("assess-week-2", "stu-2001", "present"),
    )
    question = conn.execute(
        "select * from questions where id = 'q-newton-1'"
    ).fetchone()
    conn.execute(
        """
        insert into question_version_snapshots(
            id, assessment_id, question_id, position, points, stem,
            options_json, answer_json, grading_rule_json, tag_snapshot_json,
            question_version, ontology_version_id
        ) values(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "snap-2-q1",
            "assess-week-2",
            "q-newton-1",
            1,
            4,
            question["stem"],
            question["options_json"],
            question["answer_json"],
            json.dumps({"type": "single_choice", "answer": "B", "points": 4}),
            "[]",
            question["version"],
            DEFAULT_ONTOLOGY_ID,
        ),
    )
    conn.execute(
        """
        insert into scan_batches(
            id, school_id, assessment_id, source_name, recognizer,
            recognizer_version, status, low_confidence_count
        ) values(?,?,?,?,?,?,?,?)
        """,
        (
            "scan-week-2",
            "school-demo",
            "assess-week-2",
            "二班答题卡",
            "PaddleOCR",
            "reserved-local-v1",
            "待复核",
            1,
        ),
    )
    conn.execute(
        """
        insert into student_responses(
            id, school_id, assessment_id, scan_batch_id, student_id,
            question_id, snapshot_id, raw_answer, final_answer,
            original_confidence, review_status, review_reason
        ) values(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "resp-2001-q1",
            "school-demo",
            "assess-week-2",
            "scan-week-2",
            "stu-2001",
            "q-newton-1",
            "snap-2-q1",
            "A",
            "A",
            0.42,
            "required",
            "low_confidence",
        ),
    )
    conn.commit()
