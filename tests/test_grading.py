import unittest

from highschoolphysics.grading import grade_answer, grade_response_set


class GradingTests(unittest.TestCase):
    def test_multiple_choice_requires_same_option_set(self):
        rule = {
            "type": "multiple_choice",
            "answer": ["A", "C"],
            "points": 4,
        }

        self.assertEqual(grade_answer(rule, "C,A")["score"], 4)
        self.assertEqual(grade_answer(rule, "A")["score"], 0)
        self.assertEqual(grade_answer(rule, "A,B,C")["score"], 0)

    def test_fill_answer_supports_exact_and_numeric_tolerance(self):
        exact_rule = {
            "type": "fill",
            "answer": ["9.8", "9.80"],
            "points": 2,
            "match": "exact",
        }
        tolerance_rule = {
            "type": "fill",
            "answer": "12.5",
            "points": 3,
            "match": "numeric_tolerance",
            "tolerance": 0.2,
        }

        self.assertEqual(grade_answer(exact_rule, "9.80")["score"], 2)
        self.assertEqual(grade_answer(tolerance_rule, "12.68")["score"], 3)
        self.assertEqual(grade_answer(tolerance_rule, "12.9")["score"], 0)

    def test_response_set_flags_low_confidence_and_conflicts_for_review(self):
        rules = {
            "Q1": {"type": "single_choice", "answer": "B", "points": 2},
            "Q2": {"type": "fill", "answer": "4.0", "points": 2, "match": "exact"},
        }
        responses = {
            "Q1": {"answer": "B", "confidence": 0.92},
            "Q2": {"answer": "5.0", "confidence": 0.42},
        }

        result = grade_response_set(rules, responses, confidence_threshold=0.75)

        self.assertEqual(result["total_score"], 2)
        self.assertEqual(result["items"]["Q1"]["status"], "correct")
        self.assertEqual(result["items"]["Q2"]["status"], "needs_review")
        self.assertIn("low_confidence", result["items"]["Q2"]["review_reasons"])


if __name__ == "__main__":
    unittest.main()
