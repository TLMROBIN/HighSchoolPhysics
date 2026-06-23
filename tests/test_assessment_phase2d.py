import unittest

from highschoolphysics.assessment import (
    default_export_options,
    generate_answer_card_template,
    normalize_ocr_items,
    score_redo_attempt,
)


class AssessmentPhase2DHelperTests(unittest.TestCase):
    def test_generate_answer_card_template_uses_snapshot_positions(self):
        snapshots = [
            {
                "question_id": "q1",
                "position": 1,
                "points": 4,
                "question_type": "single_choice",
            },
            {
                "question_id": "q2",
                "position": 2,
                "points": 6,
                "question_type": "fill",
            },
        ]

        template = generate_answer_card_template(
            "card-new",
            "高二力学周测",
            snapshots,
        )

        self.assertEqual(template["id"], "card-new")
        self.assertEqual(template["name"], "高二力学周测答题卡")
        self.assertEqual(template["regions"][0]["question_id"], "q1")
        self.assertEqual(template["regions"][0]["kind"], "choice")
        self.assertEqual(template["regions"][1]["kind"], "text")

    def test_normalize_ocr_items_flags_low_confidence_and_conflicts(self):
        items = normalize_ocr_items(
            [
                {
                    "student_id": "stu-1001",
                    "question_id": "q1",
                    "answer": "A",
                    "confidence": 0.91,
                },
                {
                    "student_id": "stu-1001",
                    "question_id": "q2",
                    "answer": "C",
                    "confidence": 0.42,
                },
                {
                    "student_id": "stu-1002",
                    "question_id": "q1",
                    "answer": "B",
                    "confidence": 0.88,
                    "conflict": True,
                },
            ],
            confidence_threshold=0.75,
        )

        self.assertEqual(items[0]["review_status"], "not_required")
        self.assertEqual(items[1]["review_status"], "required")
        self.assertEqual(items[1]["review_reason"], "low_confidence")
        self.assertEqual(items[2]["review_status"], "required")
        self.assertEqual(items[2]["review_reason"], "conflict")

    def test_score_redo_attempt_keeps_redo_separate_from_original_wrong(self):
        rule = {"type": "single_choice", "answer": "C", "points": 4}

        scored = score_redo_attempt(rule, "C")

        self.assertEqual(scored["score"], 4)
        self.assertEqual(scored["max_score"], 4)
        self.assertEqual(scored["status"], "done")

    def test_default_export_options_hide_answers_and_analysis(self):
        options = default_export_options({})

        self.assertFalse(options["include_answers"])
        self.assertFalse(options["include_analysis"])
        self.assertTrue(options["include_error_reasons"])
        self.assertEqual(options["page_break"], "student")


if __name__ == "__main__":
    unittest.main()
