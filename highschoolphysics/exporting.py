import html

from .assessment import default_export_options


def _answer_text(value):
    if isinstance(value, dict):
        if "answer" in value:
            return _answer_text(value["answer"])
        return "；".join(
            "%s：%s" % (key, value[key]) for key in sorted(value.keys())
        )
    if isinstance(value, list):
        return " / ".join(str(item) for item in value)
    return str(value)


def build_wrong_book_html(
    repo,
    actor_id,
    assessment_id,
    class_id=None,
    student_id=None,
    options=None,
):
    export_options = default_export_options(options)
    assessment = repo.assessment_detail(
        actor_id,
        assessment_id,
        operation="export",
    )
    wrongs = repo.list_wrong_questions_for_assessment(
        actor_id,
        assessment_id,
        class_id=class_id,
        student_id=student_id,
        operation="export",
    )
    grouped = {}
    for wrong in wrongs:
        grouped.setdefault(
            wrong["student_id"],
            {
                "student_name": wrong["student_name"],
                "student_no": wrong["student_no"],
                "items": [],
            },
        )["items"].append(wrong)

    parts = [
        "<!doctype html>",
        "<html lang='zh-CN'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>%s 错题本</title>" % html.escape(assessment["title"]),
        "<style>",
        "@page { size: A4; margin: 16mm 14mm; }",
        "body { font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Noto Sans CJK SC', sans-serif; color: #1f2937; }",
        ".student-page { page-break-after: always; }",
        ".student-page:last-child { page-break-after: auto; }",
        "h1 { font-size: 22px; margin: 0 0 4px; }",
        "h2 { font-size: 18px; margin: 18px 0 8px; }",
        ".meta { color: #64748b; margin-bottom: 14px; }",
        ".question { border-top: 1px solid #d7dee8; padding: 12px 0; }",
        ".tag-block { background: #f6f8fb; border: 1px solid #d7dee8; padding: 8px 10px; margin-top: 10px; }",
        ".tag-block p { margin: 4px 0; }",
        ".tag-block span { display: inline-block; border: 1px solid #b8c2d2; border-radius: 4px; padding: 2px 6px; margin: 3px 4px 0 0; font-size: 12px; }",
        "</style>",
        "</head>",
        "<body>",
    ]
    for group in grouped.values():
        parts.append("<section class='student-page'>")
        parts.append("<h1>%s - 个人错题本</h1>" % html.escape(assessment["title"]))
        parts.append(
            "<div class='meta'>班级：%s　学生：%s（%s）　错题数：%s</div>"
            % (
                html.escape(assessment["class_name"]),
                html.escape(group["student_name"]),
                html.escape(group["student_no"] or ""),
                len(group["items"]),
            )
        )
        for index, wrong in enumerate(group["items"], start=1):
            parts.append("<article class='question'>")
            parts.append("<h2>%s. %s</h2>" % (index, html.escape(wrong["stem"])))
            if wrong["options"]:
                option_text = "　".join(
                    "%s. %s" % (html.escape(key), html.escape(str(value)))
                    for key, value in sorted(wrong["options"].items())
                )
                parts.append("<p>%s</p>" % option_text)
            parts.append(
                "<p>原作答：%s　得分：%s/%s　掌握标记：%s</p>"
                % (
                    html.escape(wrong.get("wrong_answer") or "空白"),
                    wrong["score"],
                    wrong["max_score"],
                    html.escape(wrong.get("mastery_level") or "未标记"),
                )
            )
            if export_options["include_answers"]:
                parts.append(
                    "<p>正确答案：%s</p>"
                    % html.escape(_answer_text(wrong["correct_answer"]))
                )
            if export_options["include_analysis"] and wrong.get("analysis"):
                parts.append("<p>解析：%s</p>" % html.escape(wrong["analysis"]))
            knowledge_paths = [
                tag.get("path_text") or tag["name"]
                for tag in wrong["knowledge_tags"]
            ]
            ability_tags = [tag["name"] for tag in wrong["ability_tags"]]
            parts.append("<div class='tag-block'>")
            parts.append(
                "<p><strong>知识点路径：</strong>%s</p>"
                % html.escape("；".join(knowledge_paths) or "未标注")
            )
            parts.append("<p><strong>学科能力：</strong>")
            if ability_tags:
                for tag in ability_tags:
                    parts.append("<span>%s</span>" % html.escape(tag))
            else:
                parts.append("未标注")
            parts.append("</p></div>")
            if export_options["include_error_reasons"]:
                reasons = [
                    tag["name"] for tag in wrong.get("error_reason_tags", [])
                ]
                parts.append(
                    "<p><strong>错因：</strong>%s</p>"
                    % html.escape(
                        "；".join(reasons)
                        or wrong.get("error_reason")
                        or "未标注"
                    )
                )
            if (
                export_options["include_redo_history"]
                and wrong.get("redo_attempts")
            ):
                parts.append("<div class='tag-block'><strong>重做记录</strong>")
                for attempt in wrong["redo_attempts"]:
                    parts.append(
                        "<p>%s：%s/%s %s</p>"
                        % (
                            html.escape(attempt["status"]),
                            attempt.get("score", ""),
                            attempt.get("max_score", ""),
                            html.escape(attempt.get("feedback", "")),
                        )
                    )
                parts.append("</div>")
            parts.append("</article>")
        parts.append("</section>")
    if not grouped:
        parts.append("<h1>%s - 暂无错题</h1>" % html.escape(assessment["title"]))
    parts.append("</body></html>")
    repo.audit(
        actor_id,
        "wrong_book_exported",
        "assessment",
        assessment_id,
        {"student_pages": len(grouped), "class_id": class_id, "student_id": student_id},
    )
    repo.conn.commit()
    return "\n".join(parts)
