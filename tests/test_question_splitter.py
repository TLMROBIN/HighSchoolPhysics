import unittest

from highschoolphysics.question_splitter import split_document_ir


def fixture_ir():
    return {
        "schema_version": 1,
        "document_id": "doc-split-test",
        "conversion_id": "conversion-split-test",
        "source_sha256": "a" * 64,
        "pages": [{"page": 1, "width": 1000, "height": 1500, "rotation_applied": 0}],
        "assets": [{"id": "asset_figure1", "sha256": "b" * 64, "mime_type": "image/png"}],
        "blocks": [
            {"id": "b1", "type": "heading", "page": 1, "order": 1, "bbox": [0, 0, 1, 0.1], "markdown": "一、选择题", "asset_ids": [], "source_locator": {"kind": "pdf", "page": 1}, "issues": []},
            {"id": "b2", "type": "paragraph", "page": 1, "order": 2, "bbox": [0, 0.1, 1, 0.2], "markdown": "1．观察图示并求速度 $v^2$。", "asset_ids": [], "source_locator": {"kind": "pdf", "page": 1}, "issues": []},
            {"id": "b3", "type": "figure", "page": 1, "order": 3, "bbox": [0.1, 0.2, 0.9, 0.6], "markdown": "![装置](asset:asset_figure1)", "asset_ids": ["asset_figure1"], "source_locator": {"kind": "pdf", "page": 1}, "issues": []},
            {"id": "b4", "type": "paragraph", "page": 1, "order": 4, "bbox": [0, 0.6, 1, 0.8], "markdown": "A．选项甲\nB．选项乙\n（1）完成位移计算。\n（2）说明理由。", "asset_ids": [], "source_locator": {"kind": "pdf", "page": 1}, "issues": []},
            {"id": "b5", "type": "paragraph", "page": 1, "order": 5, "bbox": [0, 0.8, 1, 0.9], "markdown": "2、物体运动的加速度为 ____。", "asset_ids": [], "source_locator": {"kind": "pdf", "page": 1}, "issues": []},
            {"id": "b6", "type": "heading", "page": 1, "order": 6, "bbox": [0, 0.9, 1, 0.93], "markdown": "参考答案", "asset_ids": [], "source_locator": {"kind": "pdf", "page": 1}, "issues": []},
            {"id": "b7", "type": "paragraph", "page": 1, "order": 7, "bbox": [0, 0.93, 1, 1], "markdown": "1. A 2. B", "asset_ids": [], "source_locator": {"kind": "pdf", "page": 1}, "issues": []},
        ],
        "issues": [],
    }


class QuestionSplitterTests(unittest.TestCase):
    def test_mixed_number_ranges_in_choice_heading(self):
        ir=fixture_ir();base=ir['blocks'][0]
        ir['blocks']=[dict(base,id='head',markdown='**一、单选题（1-7为单选，8-10为多选）**')]
        for n in (1,7,8,10):
            ir['blocks'].append(dict(base,id='q'+str(n),type='paragraph',order=n+1,markdown=str(n)+'．下列说法正确的是。\nA．甲\nB．乙'))
        questions=split_document_ir(ir)['questions']
        self.assertEqual([q['document']['kind'] for q in questions],['single_choice','single_choice','multiple_choice','multiple_choice'])
    def test_multiple_choice_section_is_inherited_and_resets_for_next_section(self):
        ir = fixture_ir()
        base = ir["blocks"][0]
        ir["blocks"] = [dict(base, id="s1", order=1, markdown="## 二、多项选择题"),
                        dict(base, id="q1", type="paragraph", order=2, markdown="8．下列说法正确的是。\nA．甲\nB．乙\nC．丙\nD．丁"),
                        dict(base, id="s2", order=3, markdown="三、单选题"),
                        dict(base, id="q2", type="paragraph", order=4, markdown="9．请选择正确选项。\nA．甲\nB．乙")]
        questions = split_document_ir(ir)["questions"]
        self.assertEqual([item["document"]["kind"] for item in questions], ["multiple_choice", "single_choice"])

    def test_experiment_child_with_options_is_a_choice_question(self):
        ir = fixture_ir()
        ir["blocks"] = [dict(ir["blocks"][1], markdown="13．某实验装置如下。\n（1）请选择正确操作。\nA．甲\nB．乙\n（2）多选：请选择正确操作。\nA．丙\nB．丁")]
        children = split_document_ir(ir)["questions"][0]["document"]["children"]
        self.assertEqual([child["kind"] for child in children], ["single_choice", "multiple_choice"])
    def test_numbers_options_children_and_source_assets_are_preserved(self):
        result = split_document_ir(fixture_ir())
        self.assertEqual([item["document"]["number"] for item in result["questions"]], ["1", "2"])
        first, second = [item["document"] for item in result["questions"]]
        self.assertEqual([(item["key"], item["markdown"]) for item in first["options"]], [("A", "选项甲"), ("B", "选项乙")])
        self.assertEqual([item["label"] for item in first["children"]], ["(1)", "(2)"])
        self.assertEqual(first["asset_refs"], ["asset_figure1"])
        self.assertIn("$v^2$", first["stem_md"])
        self.assertEqual(second["kind"], "fill")
        self.assertEqual(result["answer_blocks"], ["b6", "b7"])
        self.assertIn("section_heading", [item["reason"] for item in result["unassigned_blocks"]])
        self.assertTrue(all(span["block_id"] in {"b2", "b3", "b4", "b5"} for item in result["questions"] for span in item["source_spans"]))

    def test_no_question_number_is_not_reported_as_success(self):
        document = fixture_ir()
        document["blocks"] = [dict(document["blocks"][0], markdown="选择题说明")]
        result = split_document_ir(document)
        self.assertEqual(result["questions"], [])
        self.assertEqual(result["issues"][0]["code"], "no_question_boundaries_found")

    def test_inline_question_boundary_is_flagged_for_teacher_review(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(document["blocks"][1], markdown="1．" + "甲题条件描述足够长。" * 3 + " 2．乙题条件。")
        ]
        result = split_document_ir(document)
        self.assertEqual(len(result["questions"]), 2)
        self.assertTrue(any(issue["code"] == "inline_question_boundary_requires_review" for issue in result["questions"][1]["issues"]))

    def test_duplicate_question_numbers_are_flagged_for_teacher_review(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(document["blocks"][1], markdown="1．第一道题，题干足够长。"),
            dict(document["blocks"][2], id="duplicate-question", order=3, markdown="1．第二道题，题干也足够长。"),
        ]
        result = split_document_ir(document)
        self.assertEqual(len(result["questions"]), 2)
        self.assertTrue(
            all(
                any(issue["code"] == "duplicate_question_number" for issue in item["issues"])
                for item in result["questions"]
            )
        )

    def test_answer_heading_in_document_title_stops_question_splitting(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(document["blocks"][1], markdown="1．保留为可编辑正文的真实题目。"),
            dict(document["blocks"][5], id="b-answer-title", order=2, markdown="《一套试卷》参考答案"),
            dict(document["blocks"][6], id="b-answer-body", order=3, markdown="1．【答案】A\n【详解】答案解析正文。"),
        ]

        result = split_document_ir(document)

        self.assertEqual([item["document"]["number"] for item in result["questions"]], ["1"])
        self.assertEqual(result["answer_blocks"], ["b-answer-title", "b-answer-body"])
        self.assertEqual(
            {item["block_id"]: item["reason"] for item in result["unassigned_blocks"]},
            {"b-answer-title": "answer_area_requires_mapping", "b-answer-body": "answer_area_requires_mapping"},
        )
        self.assertIn("真实题目", result["questions"][0]["document"]["stem_md"])

    def test_answer_card_is_preserved_as_mapping_material_not_a_question(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(document["blocks"][1], markdown="1．真实题目正文。"),
            dict(document["blocks"][0], id="b-card", order=2, markdown="2027届物理答题卡"),
            dict(document["blocks"][1], id="b-card-table", order=3, markdown="| 题号 | 1 | 答案 | |"),
            dict(document["blocks"][5], id="b-answer-title", order=4, markdown="参考答案"),
            dict(document["blocks"][6], id="b-answer-body", order=5, markdown="1．【答案】A"),
        ]

        result = split_document_ir(document)

        self.assertEqual([item["document"]["number"] for item in result["questions"]], ["1"])
        self.assertEqual(result["answer_blocks"], ["b-card", "b-card-table", "b-answer-title", "b-answer-body"])
        self.assertEqual(
            {item["block_id"]: item["reason"] for item in result["unassigned_blocks"]},
            {
                "b-card": "answer_card_requires_mapping",
                "b-card-table": "answer_card_requires_mapping",
                "b-answer-title": "answer_area_requires_mapping",
                "b-answer-body": "answer_area_requires_mapping",
            },
        )

    def test_word_list_number_before_option_is_not_a_question_and_fullwidth_number_keeps_year(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(document["blocks"][1], id="b-q5", order=1, markdown="5．题目条件。"),
            dict(document["blocks"][3], id="b-q5-option-a", order=2, markdown="![装置](asset:asset_figure1)A．选项甲", asset_ids=["asset_figure1"]),
            dict(document["blocks"][3], id="b-q5-option-b", type="list_item", order=3, markdown="1. B．选项乙"),
            dict(document["blocks"][1], id="b-q6", order=4, markdown="6．2026年探测器的速度为1.25m/s，判断其运动。"),
            dict(document["blocks"][2], id="b-q7", order=5, markdown="![装置](asset:asset_figure1)7．图后紧接的新题。", asset_ids=["asset_figure1"]),
        ]

        result = split_document_ir(document)

        self.assertEqual([item["document"]["number"] for item in result["questions"]], ["5", "6", "7"])
        self.assertEqual(
            [(item["key"], item["markdown"]) for item in result["questions"][0]["document"]["options"]],
            [("A", "选项甲"), ("B", "选项乙")],
        )
        self.assertIn("asset_figure1", result["questions"][0]["document"]["asset_refs"])
        self.assertIn("2026年", result["questions"][1]["document"]["stem_md"])
        self.assertIn("1.25m/s", result["questions"][1]["document"]["stem_md"])
        self.assertIn("inline_question_boundary_requires_review", [issue["code"] for issue in result["questions"][2]["issues"]])

    def test_ocr_question_number_tight_to_year_and_spaced_decimal_are_unambiguous(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(document["blocks"][1], id="b-q1-tight-year", order=1, markdown="1.2026年发布的新装置如图，判断其运动。"),
            dict(document["blocks"][1], id="b-q2", order=2, markdown="2. 神舟飞船绕行，求速度。"),
            dict(document["blocks"][1], id="b-q15-decimals", order=3, markdown="15. 轨道最大摩擦因数 cos 53° = 0 . 8，sin 53° = 0 . 6。"),
        ]

        result = split_document_ir(document)

        self.assertEqual([item["document"]["number"] for item in result["questions"]], ["1", "2", "15"])
        self.assertIn("2026年", result["questions"][0]["document"]["stem_md"])
        self.assertIn("0 . 8", result["questions"][2]["document"]["stem_md"])

    def test_ocr_concatenated_sequential_options_are_separated_from_stem(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(
                document["blocks"][1],
                markdown="2. 神舟飞船绕行，求速度。A. 线速度较大B. 周期较大C. 角速度较小D. 加速度较小",
            )
        ]

        question = split_document_ir(document)["questions"][0]["document"]

        self.assertEqual(question["number"], "2")
        self.assertEqual(question["stem_md"], "神舟飞船绕行，求速度。")
        self.assertEqual([item["key"] for item in question["options"]], ["A", "B", "C", "D"])

        document["blocks"] = [
            dict(document["blocks"][0], markdown="8. 识别不完整时 A.甲B.乙C.丙，未见D选项。")
        ]
        incomplete = split_document_ir(document)["questions"][0]["document"]
        self.assertEqual(incomplete["options"], [])
        self.assertIn("A.甲B.乙C.丙", incomplete["stem_md"])

    def test_ocr_option_suffix_ignores_prose_letters_and_digit_before_d(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(
                document["blocks"][1],
                markdown=(
                    "5. 同一截面上有A、B两处射入孔，其中A与圆心等高，"
                    "以下说法正确的是A.时间相同B.速度相同C.夹角正切值等于2D.加速度不同"
                ),
            ),
            dict(document["blocks"][2], id="b-q5-stem-figure", order=2),
        ]

        question = split_document_ir(document)["questions"][0]["document"]

        self.assertEqual([item["key"] for item in question["options"]], ["A", "B", "C", "D"])
        self.assertIn("A、B两处", question["stem_md"])
        self.assertIn("其中A与圆心", question["stem_md"])
        self.assertIn("加速度不同", question["options"][3]["markdown"])
        self.assertNotIn("asset_figure1", question["options"][3]["markdown"])
        self.assertIn("asset_figure1", question["stem_md"])
        self.assertTrue(any(issue["code"] == "figure_after_options_requires_review" for issue in question["issues"]))

    def test_labeled_figure_row_becomes_real_image_options(self):
        document = fixture_ir()
        document["assets"] = [
            {"id": "asset_graph_%s" % key, "sha256": (key.lower() * 64)[:64], "mime_type": "image/png"}
            for key in "ABCD"
        ] + [{"id": "asset_context", "sha256": "e" * 64, "mime_type": "image/png"}]
        blocks = [dict(document["blocks"][1], id="b-q3", order=1, markdown="3. 图形选项题正文。")]
        for index, key in enumerate("ABCD"):
            x0 = 0.05 + index * 0.22
            x1 = x0 + 0.15
            asset_id = "asset_graph_%s" % key
            blocks.extend([
                {
                    "id": "b-graph-%s" % key,
                    "type": "figure",
                    "page": 1,
                    "order": index * 2 + 2,
                    "bbox": [x0, 0.4, x1, 0.55],
                    "markdown": "![图形](asset:%s)" % asset_id,
                    "asset_ids": [asset_id],
                    "source_locator": {"kind": "pdf", "page": 1},
                    "issues": [],
                },
                {
                    "id": "b-label-%s" % key,
                    "type": "paragraph",
                    "page": 1,
                    "order": index * 2 + 3,
                    "bbox": [x0 + 0.06, 0.552, x0 + 0.09, 0.57],
                    "markdown": key,
                    "asset_ids": [],
                    "source_locator": {"kind": "pdf", "page": 1},
                    "issues": [],
                },
            ])
        blocks.append({
            "id": "b-context-figure-after-options",
            "type": "figure",
            "page": 1,
            "order": 10,
            "bbox": [0.85, 0.4, 0.95, 0.6],
            "markdown": "![题干示意图](asset:asset_context)",
            "asset_ids": ["asset_context"],
            "source_locator": {"kind": "pdf", "page": 1},
            "issues": [],
        })
        document["blocks"] = blocks

        question = split_document_ir(document)["questions"][0]["document"]

        self.assertIn("图形选项题正文。", question["stem_md"])
        self.assertIn("![题干示意图](asset:asset_context)", question["stem_md"])
        self.assertEqual([item["key"] for item in question["options"]], list("ABCD"))
        self.assertEqual(
            [item["markdown"] for item in question["options"]],
            ["![图形](asset:asset_graph_%s)" % key for key in "ABCD"],
        )
        self.assertTrue(any(issue["code"] == "figure_after_options_requires_review" for issue in question["issues"]))
        self.assertEqual(question["asset_refs"], ["asset_context", "asset_graph_A", "asset_graph_B", "asset_graph_C", "asset_graph_D"])
        self.assertEqual({span["block_id"] for span in question["source_spans"]}, {block["id"] for block in blocks})

    def test_child_question_after_inline_image_starts_a_new_editable_child(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(document["blocks"][1], id="b-q13", order=1, markdown="13．实验题总述。"),
            dict(document["blocks"][2], id="b-children", order=2, type="paragraph", markdown="（1）第一小问正文。![装置](asset:asset_figure1)(2)第二小问正文。", asset_ids=["asset_figure1"]),
        ]

        result = split_document_ir(document)
        question = result["questions"][0]["document"]

        self.assertEqual([child["label"] for child in question["children"]], ["(1)", "(2)"])
        self.assertEqual(question["children"][0]["stem_md"], "第一小问正文。![装置](asset:asset_figure1)")
        self.assertEqual(question["children"][1]["stem_md"], "第二小问正文。")
        self.assertEqual(question["children"][0]["source_spans"][0]["source_locator"]["start"], len("（1）"))

    def test_image_before_next_question_number_belongs_to_following_question(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(document["blocks"][1], id="b-q1", order=1, markdown="1．第一题正文。"),
            dict(
                document["blocks"][2],
                id="b-q2-with-leading-image",
                order=2,
                type="paragraph",
                markdown="![装置](asset:asset_figure1)2．第二题如图所示，求速度。",
                asset_ids=["asset_figure1"],
            ),
        ]

        result = split_document_ir(document)
        first, second = [item["document"] for item in result["questions"]]

        self.assertEqual(first["asset_refs"], [])
        self.assertEqual(second["asset_refs"], ["asset_figure1"])
        self.assertIn("![装置](asset:asset_figure1)", second["stem_md"])
        self.assertIn("inline_question_boundary_requires_review", [issue["code"] for issue in second["issues"]])

    def test_section_heading_between_numbered_questions_is_not_attached_to_previous_child(self):
        document = fixture_ir()
        document["blocks"] = [
            dict(document["blocks"][1], id="b-q12", order=1, markdown="12．实验总述。"),
            dict(document["blocks"][3], id="b-q12-child", order=2, markdown="（4）最后一问。"),
            dict(document["blocks"][0], id="b-section-4", order=3, type="heading", markdown="四、解答题"),
            dict(document["blocks"][1], id="b-q13", order=4, markdown="13．下一道完整题目。"),
        ]

        result = split_document_ir(document)
        q12, q13 = [item["document"] for item in result["questions"]]

        self.assertEqual(q12["number"], "12")
        self.assertEqual(q12["children"][0]["label"], "(4)")
        self.assertEqual(q12["children"][0]["stem_md"], "最后一问。")
        self.assertNotIn("四、解答题", q12["children"][0]["stem_md"])
        self.assertEqual(q13["number"], "13")
        self.assertEqual(
            {item["block_id"]: item["reason"] for item in result["unassigned_blocks"]},
            {"b-section-4": "section_heading"},
        )


if __name__ == "__main__":
    unittest.main()
