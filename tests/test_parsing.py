import unittest

from highschoolphysics.parsing import (
    ParseAdapterError,
    normalize_parser_output,
    parse_deterministic_text,
    run_parser,
)


class ParsingTests(unittest.TestCase):
    def test_deterministic_text_parser_splits_numbered_questions(self):
        text = """
        1. 质量为2kg的物体受到6N合外力，2s末速度是多少？
        A. 1m/s
        B. 2m/s
        C. 6m/s
        D. 12m/s
        答案：C
        2. 简述牛顿第二定律的适用条件。
        答案：宏观低速惯性参考系
        """
        result = parse_deterministic_text(text, parser_version="test-v1")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["question_number"], "1")
        self.assertEqual(result["items"][0]["question_type"], "single_choice")
        self.assertEqual(result["items"][0]["answer"]["answer"], "C")
        self.assertEqual(result["items"][1]["question_type"], "short_answer")

    def test_normalizer_marks_low_confidence_item_needs_review(self):
        raw = {
            "items": [
                {
                    "item_index": 1,
                    "page_number": 1,
                    "question_number": "1",
                    "stem": "题干",
                    "question_type": "single_choice",
                    "options": {"A": "1", "B": "2"},
                    "answer": {"type": "single_choice", "answer": "B"},
                    "confidence": 0.61,
                }
            ],
            "parser_name": "deterministic_text",
            "parser_version": "test-v1",
        }
        normalized = normalize_parser_output(raw)
        self.assertEqual(
            normalized["items"][0]["review_status"],
            "needs_review",
        )
        self.assertIn("low_confidence", normalized["items"][0]["warnings"])

    def test_run_parser_fail_closed_reports_missing_external_adapter(self):
        with self.assertRaises(ParseAdapterError):
            run_parser(
                parser_mode="markitdown",
                source_text="1. 测试\n答案：A",
                parser_version="test-v1",
                config={"command_path": "/path/not-present"},
                fallback_policy="fail_closed",
            )

    def test_markitdown_adapter_normalizes_injected_runner_text(self):
        result = run_parser(
            parser_mode="markitdown",
            source_text="placeholder",
            parser_version="test-v2",
            config={"file_name": "sample.docx"},
            adapter_runner=lambda mode, source_text, config: (
                "1. MarkItDown 解析出的题干\nA. 1\nB. 2\n答案：B"
            ),
            fallback_policy="fail_closed",
        )

        self.assertEqual(result["parser_name"], "markitdown")
        self.assertEqual(result["items"][0]["stem"], "MarkItDown 解析出的题干")
        self.assertEqual(result["items"][0]["answer"]["answer"], "B")

    def test_mineru_local_adapter_normalizes_injected_json_items(self):
        result = run_parser(
            parser_mode="mineru_local",
            source_text="placeholder",
            parser_version="test-v2",
            config={"file_name": "sample.pdf"},
            adapter_runner=lambda mode, source_text, config: {
                "parser_name": "mineru_local",
                "parser_version": "mineru-test",
                "items": [
                    {
                        "item_index": 1,
                        "page_number": 3,
                        "question_number": "12",
                        "stem": "MinerU 结构化题干",
                        "answer": {"type": "short_answer", "answer": "动量守恒"},
                        "confidence": 0.86,
                        "coordinates": {"page": 3, "bbox": [10, 20, 80, 120]},
                    }
                ],
            },
            fallback_policy="fail_closed",
        )

        self.assertEqual(result["parser_name"], "mineru_local")
        self.assertEqual(result["items"][0]["page_number"], 3)
        self.assertEqual(result["items"][0]["coordinates"]["bbox"], [10, 20, 80, 120])
        self.assertEqual(result["items"][0]["review_status"], "ready")

    def test_mineru_api_requires_endpoint_without_injected_client(self):
        with self.assertRaisesRegex(ParseAdapterError, "MinerU API endpoint"):
            run_parser(
                parser_mode="mineru_api",
                source_text="1. 测试\n答案：A",
                parser_version="test-v2",
                config={},
                fallback_policy="fail_closed",
            )


if __name__ == "__main__":
    unittest.main()
