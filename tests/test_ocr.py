import unittest

from highschoolphysics.ocr import normalize_paddleocr_result, run_paddleocr


class PaddleOCRAdapterTests(unittest.TestCase):
    def test_normalize_paddleocr_result_preserves_text_confidence_and_bbox(self):
        normalized = normalize_paddleocr_result(
            [
                (
                    [[10, 20], [80, 20], [80, 44], [10, 44]],
                    ("C", 0.93),
                ),
                (
                    [[12, 80], [140, 80], [140, 120], [12, 120]],
                    ("动量守恒", 0.52),
                ),
            ],
            source_path="scan-1.png",
            confidence_threshold=0.75,
        )

        self.assertEqual(normalized[0]["text"], "C")
        self.assertEqual(normalized[0]["bbox"], [10, 20, 80, 44])
        self.assertEqual(normalized[0]["review_status"], "not_required")
        self.assertEqual(normalized[1]["review_status"], "required")
        self.assertEqual(normalized[1]["review_reason"], "low_confidence")

    def test_run_paddleocr_uses_injected_runner(self):
        result = run_paddleocr(
            ["scan-1.png"],
            runner=lambda paths: {
                "scan-1.png": [
                    {
                        "text": "B",
                        "confidence": 0.88,
                        "bbox": [1, 2, 3, 4],
                        "student_id": "stu-1001",
                        "question_id": "q-newton-1",
                    }
                ]
            },
        )

        self.assertEqual(result[0]["text"], "B")
        self.assertEqual(result[0]["student_id"], "stu-1001")
        self.assertEqual(result[0]["question_id"], "q-newton-1")


if __name__ == "__main__":
    unittest.main()
