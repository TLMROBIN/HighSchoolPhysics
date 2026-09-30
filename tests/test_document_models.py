import copy
import hashlib
import json
import re
import unittest

from highschoolphysics.document_models import (
    DocumentValidationError,
    canonical_sha256,
    question_content_sha256,
    validate_document_ir,
    validate_question_document,
)
from highschoolphysics.question_content import parse_question_md, serialize_question_md
from highschoolphysics.question_rendering import render_markdown, render_question


def sample_question():
    return {
        "schema_version": 1,
        "number": "11",
        "kind": "experiment",
        "stem_md": (
            "测量周期 $T=2\\pi/\\omega$。\n\n"
            "![装置示意图](asset:figure_a)\n\n"
            "| 量 | 单位 |\n| --- | --- |\n| $T$ | s |\n\n## 答案\n此标题属于题干正文。"
        ),
        "options": [
            {"key": "A", "markdown": "速度为 $v=at$。"},
            {"key": "B", "markdown": "见图 ![选项图](asset:option_b)"},
        ],
        "answer_md": "结果为 $T=0.50\\,\\mathrm{s}$。",
        "analysis_md": "由周期定义得到。",
        "answer_state": "needs_review",
        "grading_rule": None,
        "children": [
            {
                "key": "part_1",
                "label": "(1)",
                "kind": "fill",
                "stem_md": "周期为____，保留两位有效数字。",
                "options": [],
                "answer_md": "$T=0.50\\,\\mathrm{s}$",
                "analysis_md": "将数据代入周期公式。",
                "answer_state": "verified",
                "grading_rule": {"type": "numeric", "target": 0.5, "tolerance": 0.01},
                "source_spans": [{"document_id": "doc_a", "block_id": "p2-b04"}],
            },
            {
                "key": "part_2",
                "label": "(2)",
                "kind": "short_answer",
                "stem_md": "说明误差来源。",
                "options": [],
                "answer_md": "仪器读数误差。",
                "analysis_md": "考虑读数与计时反应。",
                "answer_state": "needs_review",
                "grading_rule": None,
                "source_spans": [{"document_id": "doc_a", "block_id": "p2-b05"}],
            },
        ],
        "source_spans": [{"document_id": "doc_a", "block_id": "p2-b03"}],
        "asset_refs": ["figure_a", "option_b"],
        "issues": [{"code": "formula_needs_review", "severity": "review", "field": "stem_md"}],
    }


def sample_ir():
    return {
        "schema_version": 1,
        "document_id": "doc_a",
        "conversion_id": "conv_a",
        "source_sha256": "a" * 64,
        "pages": [{"page": 1, "width": 1000, "height": 1400, "rotation_applied": 0}],
        "blocks": [
            {
                "id": "p1-b001",
                "type": "paragraph",
                "page": 1,
                "column": 0,
                "order": 1,
                "bbox": [0.1, 0.1, 0.9, 0.2],
                "markdown": "1. 物体做匀速运动。",
                "asset_ids": [],
                "source_locator": {"kind": "pdf", "page": 1},
                "issues": [],
            }
        ],
        "assets": [],
        "issues": [],
    }


class DocumentModelTests(unittest.TestCase):
    def test_question_markdown_round_trip_preserves_text_and_stable_ids(self):
        original = sample_question()
        serialized = serialize_question_md(original)
        parsed = parse_question_md(serialized, original)
        self.assertEqual(parsed, original)
        self.assertIn("hsp:option:A:start", serialized)
        self.assertIn("hsp:child_stem:part_1:start", serialized)

    def test_question_markdown_edits_fields_and_preserves_metadata(self):
        original = sample_question()
        serialized = serialize_question_md(original)
        edited = serialized.replace("速度为 $v=at$。", "新速度 $v=v_0+at$。")
        edited = edited.replace("说明误差来源。", "说明并估算误差来源。")
        parsed = parse_question_md(edited, original)
        self.assertEqual(parsed["options"][0]["markdown"], "新速度 $v=v_0+at$。")
        self.assertEqual(parsed["children"][1]["stem_md"], "说明并估算误差来源。")
        self.assertEqual(parsed["children"][1]["key"], "part_2")
        self.assertEqual(parsed["grading_rule"], original["grading_rule"])
        self.assertEqual(parsed["source_spans"], original["source_spans"])

    def test_question_markdown_can_add_authorized_editable_image_references(self):
        original = sample_question()
        serialized = serialize_question_md(original)
        edited = serialized.replace(
            "![装置示意图]",
            "![新增装置图](asset:figure_new)\n![装置示意图]",
            1,
        )
        parsed = parse_question_md(
            edited,
            original,
            known_asset_ids={"figure_a", "figure_new", "option_b"},
        )
        self.assertIn("asset:figure_new", parsed["stem_md"])
        self.assertEqual(parsed["asset_refs"], ["figure_a", "figure_new", "option_b"])
        with self.assertRaisesRegex(DocumentValidationError, "unauthorized asset"):
            parse_question_md(edited, original, known_asset_ids={"figure_a", "option_b"})

    def test_question_markdown_rejects_missing_sections_and_changed_ids(self):
        original = sample_question()
        serialized = serialize_question_md(original)
        with self.assertRaises(DocumentValidationError):
            parse_question_md(serialized.replace("## 答案", "## 结论"), original)
        with self.assertRaises(DocumentValidationError):
            parse_question_md(serialized.replace("hsp:option:A:start", "hsp:option:C:start"), original)

    def test_canonical_hash_is_stable_and_keeps_meaningful_whitespace(self):
        first = sample_question()
        second = copy.deepcopy(first)
        second["stem_md"] = second["stem_md"].replace("\n", "\r\n")
        self.assertEqual(question_content_sha256(first), question_content_sha256(second))
        second["stem_md"] = second["stem_md"].replace("周期", "周期 ")
        self.assertNotEqual(question_content_sha256(first), question_content_sha256(second))
        self.assertEqual(len(canonical_sha256(first)), 64)

    def test_question_validation_rejects_unknown_image_reference(self):
        doc = sample_question()
        doc["stem_md"] += "\n![未知](asset:unknown)"
        with self.assertRaisesRegex(DocumentValidationError, "asset_refs"):
            validate_question_document(doc)
        with self.assertRaises(DocumentValidationError):
            validate_question_document(sample_question(), known_asset_ids={"figure_a"})

    def test_document_ir_checks_normalized_geometry_and_asset_ids(self):
        self.assertEqual(validate_document_ir(sample_ir())["schema_version"], 1)
        ir = sample_ir()
        ir["blocks"][0]["bbox"] = [0.9, 0.2, 0.1, 0.8]
        with self.assertRaisesRegex(DocumentValidationError, "bbox"):
            validate_document_ir(ir)

    def test_markdown_renderer_escapes_html_and_blocks_unsafe_links(self):
        rendered = render_markdown('<script>alert(1)</script> [open](//evil.invalid/path)')
        self.assertNotIn("<script>", rendered)
        self.assertNotIn('href="//evil.invalid', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("blocked-content-link", rendered)

    def test_markdown_renderer_protects_latex_subscripts_and_delimiters(self):
        formula = r"${F}_{1}\text{、}{F}_{2}$"
        percentage = r"$ 20{ \rm{ \% } } $"
        rendered = render_markdown(
            "当%s的夹角不为0时，大小______。\\(x_1+y_2\\) $$v_0^2$$ %s" % (formula, percentage)
        )
        self.assertIn(formula, rendered)
        self.assertIn(r"\(x_1+y_2\)", rendered)
        self.assertIn("$$v_0^2$$", rendered)
        self.assertIn("大小______", rendered)
        self.assertIn(percentage, rendered)
        self.assertNotIn("<em>", rendered)

    def test_markdown_renderer_resolves_only_authorized_asset_uris(self):
        rendered = render_markdown(
            "![图](asset:figure_a)",
            asset_url=lambda asset_id: "/physics/question-asset?id=" + asset_id,
        )
        self.assertIn('src="/physics/question-asset?id=figure_a"', rendered)
        denied = render_markdown("![外链](https://example.invalid/image.png)")
        self.assertIn("图片引用无效", denied)

    def test_question_renderer_supports_child_view_and_solution_gate(self):
        doc = sample_question()
        student_html = render_question(doc, child_key="part_1", include_solution=False)
        self.assertIn('data-child-key="part_1"', student_html)
        self.assertNotIn("0.50", student_html)
        teacher_html = render_question(doc, child_key="part_1", include_solution=True)
        self.assertIn("0.50", teacher_html)
        self.assertNotIn('data-child-key="part_2"', teacher_html)


if __name__ == "__main__":
    unittest.main()
