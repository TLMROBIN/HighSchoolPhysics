import base64
import hashlib
import io
import json
import re
import unittest
import zipfile

from highschoolphysics.question_export import (
    QuestionExportError,
    build_paper_markdown_zip,
    build_question_markdown_zip,
)
from tests.test_document_models import sample_question


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/xkQAAAAASUVORK5CYII="
)


def make_asset_loader():
    assets = {}
    for asset_id in ("figure_a", "option_b"):
        assets[asset_id] = {
            "data": PNG,
            "mime_type": "image/png",
            "sha256": hashlib.sha256(PNG).hexdigest(),
        }

    def load(asset_id):
        return assets[asset_id]

    return load


class QuestionExportTests(unittest.TestCase):
    def test_single_question_zip_is_offline_and_excludes_answers_by_default(self):
        blob = build_question_markdown_zip(
            sample_question(), "revision-1", make_asset_loader(), include_solution=False
        )
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = set(archive.namelist())
            markdown = archive.read("question.md").decode("utf-8")
            source = json.loads(archive.read("source.json"))
            self.assertIn("question.md", names)
            self.assertIn("source.json", names)
            self.assertTrue(any(name.startswith("images/") for name in names))
            self.assertNotIn("0.50", markdown)
            self.assertNotIn("读数与计时反应", markdown)
            self.assertNotIn("grading_rule", archive.read("source.json").decode("utf-8"))
            self.assertNotIn("answer_md", archive.read("source.json").decode("utf-8"))
            self.assertFalse(any(name.startswith("/") or ".." in name.split("/") for name in names))
            refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
            for ref in refs:
                self.assertIn(ref.split()[0], names)
            self.assertFalse(source["includes_solution"])

    def test_solution_export_requires_teacher_and_is_deterministic(self):
        kwargs = dict(
            document=sample_question(),
            revision_id="revision-1",
            asset_loader=make_asset_loader(),
            include_solution=True,
            actor_role="teacher",
        )
        first = build_question_markdown_zip(**kwargs)
        second = build_question_markdown_zip(**kwargs)
        self.assertEqual(first, second)
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            markdown = archive.read("question.md").decode("utf-8")
            self.assertIn("0.50", markdown)
            self.assertIn("读数与计时反应", markdown)
        with self.assertRaises(QuestionExportError):
            build_question_markdown_zip(
                sample_question(),
                "revision-1",
                make_asset_loader(),
                include_solution=True,
                actor_role="student",
            )

    def test_child_export_includes_parent_conditions_but_not_sibling_content(self):
        blob = build_question_markdown_zip(
            sample_question(),
            "revision-1",
            make_asset_loader(),
            child_key="part_1",
            include_solution=False,
        )
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            markdown = archive.read("question.md").decode("utf-8")
        self.assertIn("测量周期", markdown)
        self.assertIn("周期为", markdown)
        self.assertNotIn("说明误差来源", markdown)
        self.assertNotIn("0.50", markdown)

    def test_paper_zip_has_ordered_question_files_and_shared_relative_assets(self):
        second = sample_question()
        second["number"] = "12"
        second["stem_md"] = "第二题 ![图](asset:figure_a)"
        blob = build_paper_markdown_zip(
            [
                {"document": sample_question(), "revision_id": "r11"},
                {"document": second, "revision_id": "r12"},
            ],
            "周测",
            make_asset_loader(),
        )
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = set(archive.namelist())
            paper = archive.read("paper.md").decode("utf-8")
            q1 = archive.read("questions/001.md").decode("utf-8")
            q2 = archive.read("questions/002.md").decode("utf-8")
            self.assertIn("r11", json.dumps(json.loads(archive.read("source.json"))))
            self.assertLess(paper.index("# 11"), paper.index("# 12"))
            self.assertIn("../images/", q1)
            self.assertIn("../images/", q2)
            self.assertIn("images/fig-", paper)
            self.assertEqual(sum(name.startswith("images/") for name in names), 1)
            for file_name, content in (("paper.md", paper), ("questions/001.md", q1), ("questions/002.md", q2)):
                refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content)
                for ref in refs:
                    resolved = file_name.rsplit("/", 1)[0] + "/" + ref if "/" in file_name else ref
                    if file_name != "paper.md":
                        parts = []
                        for part in resolved.split("/"):
                            if part == "..":
                                if parts:
                                    parts.pop()
                            elif part != ".":
                                parts.append(part)
                        resolved = "/".join(parts)
                    self.assertIn(resolved, names)

    def test_export_rejects_bad_asset_hash_type_and_size(self):
        bad_loader = lambda _asset_id: {"data": PNG, "mime_type": "image/png", "sha256": "0" * 64}
        with self.assertRaisesRegex(QuestionExportError, "hash"):
            build_question_markdown_zip(sample_question(), "r1", bad_loader)
        type_loader = lambda _asset_id: {"data": PNG, "mime_type": "image/svg+xml"}
        with self.assertRaisesRegex(QuestionExportError, "MIME"):
            build_question_markdown_zip(sample_question(), "r1", type_loader)
        with self.assertRaisesRegex(QuestionExportError, "size limit"):
            build_question_markdown_zip(sample_question(), "r1", make_asset_loader(), max_bytes=100)


if __name__ == "__main__":
    unittest.main()
