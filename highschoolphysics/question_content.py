"""Canonical content editing and serialization helpers."""

import copy
import json
import re
from urllib.parse import quote

from .document_models import (
    ASSET_URI_RE,
    DocumentValidationError,
    canonical_sha256,
    validate_question_document,
)


SECTION_ORDER = ("题干", "选项", "小问", "答案", "解析")
RESERVED_HEADINGS = set(SECTION_ORDER)
MARKER_RE = re.compile(
    r"(?m)^<!-- hsp:([a-z_]+):([A-Za-z0-9_-]+):start -->\n"
    r"(.*?)\n<!-- hsp:\1:\2:end -->$",
    re.DOTALL,
)
SINGLE_MARKER_RE = re.compile(
    r"(?m)^<!-- hsp:([a-z_]+):start -->\n(.*?)\n<!-- hsp:\1:end -->$",
    re.DOTALL,
)
ESCAPED_HEADING_RE = re.compile(
    r"(?m)^<!-- hsp:escaped-h2:(题干|选项|小问|答案|解析) -->\n### \1$"
)
RESERVED_PREFIX = "<!-- hsp:"


def _protect_reserved_headings(markdown):
    protected = []
    for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = re.fullmatch(r"## (题干|选项|小问|答案|解析)\s*", line)
        if match:
            title = match.group(1)
            protected.extend(("<!-- hsp:escaped-h2:%s -->" % title, "### %s" % title))
        else:
            protected.append(line)
    return "\n".join(protected)


def _unprotect_reserved_headings(markdown):
    return ESCAPED_HEADING_RE.sub(lambda match: "## " + match.group(1), markdown)


def _field_marker(name, value):
    if RESERVED_PREFIX in value:
        raise DocumentValidationError(
            ["Markdown body contains the reserved editor marker prefix"]
        )
    value = _protect_reserved_headings(value)
    return "<!-- hsp:%s:start -->\n%s\n<!-- hsp:%s:end -->" % (
        name,
        value,
        name,
    )


def _item_marker(name, key, value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
        raise DocumentValidationError(["invalid stable key in editor source"])
    if RESERVED_PREFIX in value:
        raise DocumentValidationError(
            ["Markdown body contains the reserved editor marker prefix"]
        )
    value = _protect_reserved_headings(value)
    return "<!-- hsp:%s:%s:start -->\n%s\n<!-- hsp:%s:%s:end -->" % (
        name,
        key,
        value,
        name,
        key,
    )


def serialize_question_md(document):
    """Render a lossless, stable-ID editing form for a question document."""
    doc = validate_question_document(document)
    sections = []
    sections.append(
        "## 题干\n\n" + _field_marker("stem", doc["stem_md"])
    )

    option_lines = ["## 选项"]
    for option in doc["options"]:
        option_lines.extend(
            (
                "",
                "### 选项 %s" % option["key"],
                _item_marker("option", option["key"], option["markdown"]),
            )
        )
    sections.append("\n".join(option_lines))

    child_lines = ["## 小问"]
    for child in doc["children"]:
        child_lines.extend(
            (
                "",
                "### %s · %s" % (child["label"], child["key"]),
                _item_marker("child_stem", child["key"], child["stem_md"]),
            )
        )
        option_body = "\n\n".join(
            "### 选项 %s\n%s" % (option["key"], _item_marker("child_option", option["key"], option["markdown"]))
            for option in child.get("options", [])
        )
        child_lines.extend(("", "<!-- hsp:child_options:%s:start -->\n%s\n<!-- hsp:child_options:%s:end -->" %
                            (child["key"], option_body, child["key"])))
    sections.append("\n".join(child_lines))

    answer_lines = [
        "## 答案",
        "",
        _field_marker("answer", doc["answer_md"]),
    ]
    for child in doc["children"]:
        answer_lines.extend(
            (
                "",
                "### %s · %s" % (child["label"], child["key"]),
                _item_marker("child_answer", child["key"], child["answer_md"]),
            )
        )
    sections.append("\n".join(answer_lines))

    analysis_lines = [
        "## 解析",
        "",
        _field_marker("analysis", doc["analysis_md"]),
    ]
    for child in doc["children"]:
        analysis_lines.extend(
            (
                "",
                "### %s · %s" % (child["label"], child["key"]),
                _item_marker("child_analysis", child["key"], child["analysis_md"]),
            )
        )
    sections.append("\n".join(analysis_lines))
    return "\n\n".join(sections)


def _split_sections(text):
    if not isinstance(text, str):
        raise DocumentValidationError(["Markdown editor input must be text"])
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(re.finditer(r"(?m)^## (题干|选项|小问|答案|解析)\s*$", normalized))
    titles = [match.group(1) for match in matches]
    if titles != list(SECTION_ORDER):
        raise DocumentValidationError(
            ["required sections must appear once, in order: " + " / ".join(SECTION_ORDER)]
        )
    sections = {}
    for index, match in enumerate(matches):
        start = match.end()
        if start < len(normalized) and normalized[start] == "\n":
            start += 1
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        body = normalized[start:end]
        sections[match.group(1)] = body.strip("\n")
    return sections


def _read_single_marker(section, name):
    matches = [match for match in SINGLE_MARKER_RE.finditer(section) if match.group(1) == name]
    if len(matches) != 1:
        raise DocumentValidationError(["section marker %s must occur exactly once" % name])
    return _unprotect_reserved_headings(matches[0].group(2))


def _read_item_markers(section, name, expected_keys):
    matches = [match for match in MARKER_RE.finditer(section) if match.group(1) == name]
    keys = [match.group(2) for match in matches]
    if len(keys) != len(set(keys)):
        raise DocumentValidationError(["%s markers contain duplicate stable IDs" % name])
    if set(keys) != set(expected_keys):
        raise DocumentValidationError(
            ["%s markers do not match the original stable IDs" % name]
        )
    return {
        match.group(2): _unprotect_reserved_headings(match.group(3))
        for match in matches
    }, keys


def parse_question_md(text, original_document, known_asset_ids=None):
    """Apply edits to Markdown fields while retaining non-body metadata."""
    original = validate_question_document(original_document)
    sections = _split_sections(text)
    option_values, option_order = _read_item_markers(
        sections["选项"], "option", [item["key"] for item in original["options"]]
    )
    child_stems, child_order = _read_item_markers(
        sections["小问"], "child_stem", [item["key"] for item in original["children"]]
    )
    child_answers, _ = _read_item_markers(
        sections["答案"], "child_answer", [item["key"] for item in original["children"]]
    )
    child_analyses, _ = _read_item_markers(
        sections["解析"], "child_analysis", [item["key"] for item in original["children"]]
    )
    has_child_options = "<!-- hsp:child_options:" in sections["小问"]
    child_options, _ = _read_item_markers(
        sections["小问"], "child_options", [item["key"] for item in original["children"]]
    ) if has_child_options else ({}, [])

    result = copy.deepcopy(original)
    result["stem_md"] = _read_single_marker(sections["题干"], "stem")
    result["answer_md"] = _read_single_marker(sections["答案"], "answer")
    result["analysis_md"] = _read_single_marker(sections["解析"], "analysis")

    old_options = {item["key"]: item for item in original["options"]}
    result["options"] = []
    for key in option_order:
        item = copy.deepcopy(old_options[key])
        item["markdown"] = option_values[key]
        result["options"].append(item)

    old_children = {item["key"]: item for item in original["children"]}
    result["children"] = []
    for key in child_order:
        item = copy.deepcopy(old_children[key])
        item["stem_md"] = child_stems[key]
        item["answer_md"] = child_answers[key]
        item["analysis_md"] = child_analyses[key]
        if has_child_options:
            values, order = _read_item_markers(
                child_options[key], "child_option", [option["key"] for option in item.get("options", [])]
            )
            item["options"] = [{"key": option_key, "markdown": values[option_key]} for option_key in order]
        result["children"].append(item)
    markdown_fields = [result["stem_md"], result["answer_md"], result["analysis_md"]]
    markdown_fields.extend(option["markdown"] for option in result["options"])
    for child in result["children"]:
        markdown_fields.extend((child["stem_md"], child["answer_md"], child["analysis_md"]))
        markdown_fields.extend(option["markdown"] for option in child.get("options", []))
    result["asset_refs"] = sorted(
        {asset_id for field in markdown_fields for asset_id in ASSET_URI_RE.findall(field or "")}
    )
    return validate_question_document(result, known_asset_ids=known_asset_ids)


def apply_editor_operations(document, operations):
    """Apply explicit draft structure edits while retaining surviving stable IDs."""
    if not isinstance(operations, list) or len(operations) > 100:
        raise DocumentValidationError(["结构操作必须是最多 100 项的列表；请先保存草稿"])
    result = copy.deepcopy(document)
    kinds = {"single_choice", "multiple_choice", "fill", "short_answer", "structured", "experiment"}
    for operation in operations:
        if not isinstance(operation, dict):
            raise DocumentValidationError(["结构操作无效"])
        action = operation.get("action")
        child_key = operation.get("child_key", "")
        child = next((item for item in result["children"] if item["key"] == child_key), None)
        if child_key and child is None:
            raise DocumentValidationError(["所选小问不存在"])
        target = child if child_key else result
        if action == "add_child":
            key = operation.get("key")
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or any(item["key"] == key for item in result["children"]):
                raise DocumentValidationError(["新增小问 ID 无效或重复"])
            if len(result["children"]) >= 100:
                raise DocumentValidationError(["每题最多 100 个小问"])
            labels = [int(match.group(1)) for child in result["children"]
                      for match in [re.fullmatch(r"[（(](\d+)[）)]", child["label"])] if match]
            result["children"].append({
                "key": key, "label": "(%s)" % (max(labels, default=0) + 1), "kind": "short_answer",
                "stem_md": "请填写小问正文。", "options": [], "answer_md": "", "analysis_md": "",
                "answer_state": "missing", "grading_rule": None,
                "source_spans": copy.deepcopy(result.get("source_spans", [])),
            })
        elif action == "remove_child":
            if child is None:
                raise DocumentValidationError(["请选择要删除的小问"])
            result["children"].remove(child)
        elif action in ("add_option", "remove_option", "move_option", "move_child"):
            options = target["options"]
            if action == "add_option":
                key = next((key for key in "ABCDEFGH" if not any(item["key"] == key for item in options)), None)
                if key is None:
                    raise DocumentValidationError(["最多 8 个选项"])
                options.append({"key": key, "markdown": "请填写选项正文。"})
            else:
                items = result["children"] if action == "move_child" else options
                selected = child if action == "move_child" else next((item for item in options if item["key"] == operation.get("option_key")), None)
                if selected is None:
                    raise DocumentValidationError(["请选择要调整的选项或小问"])
                if action == "remove_option":
                    items.remove(selected)
                else:
                    if operation.get("direction") not in ("up", "down"):
                        raise DocumentValidationError(["移动方向无效"])
                    index = items.index(selected)
                    neighbor = index + (-1 if operation["direction"] == "up" else 1)
                    if not 0 <= neighbor < len(items):
                        raise DocumentValidationError(["已经位于边界"])
                    items[index], items[neighbor] = items[neighbor], items[index]
        elif action == "set_kind":
            if operation.get("kind") not in kinds:
                raise DocumentValidationError(["题型无效"])
            target["kind"] = operation["kind"]
            target["grading_rule"] = None
        else:
            raise DocumentValidationError(["不支持的结构操作"])
    fields = [result["stem_md"], result["answer_md"], result["analysis_md"]]
    fields.extend(item["markdown"] for item in result["options"])
    for child in result["children"]:
        fields.extend((child["stem_md"], child["answer_md"], child["analysis_md"]))
        fields.extend(item["markdown"] for item in child.get("options", []))
    result["asset_refs"] = sorted({asset for field in fields for asset in ASSET_URI_RE.findall(field)})
    if operations:
        issue = {"code": "question_structure_changed", "severity": "review", "field": "children",
                 "structure_sha256": canonical_sha256({"kind": result["kind"], "options": result["options"],
                                                       "children": result["children"]}),
                 "message": "题目结构或题型已调整；请核对选项、小问顺序、来源与答案后再入库。"}
        if not any(item.get("structure_sha256") == issue["structure_sha256"] for item in result.get("issues", [])):
            result.setdefault("issues", []).append(issue)
        for target in [result] + result["children"]:
            if target.get("answer_md") or target.get("analysis_md"):
                target["answer_state"] = "needs_review"
    return validate_question_document(result)


def content_revision_sha256(document):
    return canonical_sha256(validate_question_document(document))


def snapshot_content(conn, snapshot_id, school_id=None):
    """Return the immutable content version fixed when an assessment was created."""
    row = conn.execute(
        """select revision.document_json,revision.answer_state,revision.review_state,
                  revision.id as revision_id,group_row.school_id,
                  coalesce(correction.child_key,bind.child_key,'') as child_key
           from question_version_snapshots snapshot
           left join snapshot_content_bindings bind on bind.snapshot_id=snapshot.id
           left join historical_content_corrections correction
             on correction.snapshot_id=snapshot.id and correction.state='active'
           join question_content_revisions revision
             on revision.id=coalesce(correction.revision_id,bind.revision_id)
           join question_content_groups group_row on group_row.id=revision.group_id
           where snapshot.id=?""",
        (snapshot_id,),
    ).fetchone()
    if row is None or (school_id is not None and row["school_id"] != school_id):
        return None
    document = json.loads(row["document_json"])
    child_key = row["child_key"]
    answer_state = row["answer_state"]
    if child_key:
        child = next((item for item in document.get("children", []) if item.get("key") == child_key), None)
        if child is None:
            return None
        answer_state = child.get("answer_state", "missing")
        child_label = child.get("label", "")
    else:
        child_label = ""
    return {
        "revision_id": row["revision_id"],
        "school_id": row["school_id"],
        "child_key": child_key,
        "child_label": child_label,
        "answer_state": answer_state,
        "review_state": row["review_state"],
        "document": document,
    }


def visible_question_asset_ids(document, child_key=None, include_solution=False):
    """Assets rendered in this snapshot view, excluding answers unless requested."""
    children = document.get("children", [])
    selected = next((item for item in children if item.get("key") == child_key), None) if child_key else None
    if child_key and selected is None:
        return set()
    fields = [document.get("stem_md", "")]
    fields.extend(item.get("markdown", "") for item in document.get("options", []))
    visible_children = [selected] if selected is not None else children
    for child in visible_children:
        fields.append(child.get("stem_md", ""))
        fields.extend(item.get("markdown", "") for item in child.get("options", []))
    if include_solution:
        fields.extend((document.get("answer_md", ""), document.get("analysis_md", "")))
        for child in visible_children:
            fields.extend((child.get("answer_md", ""), child.get("analysis_md", "")))
    return {asset_id for field in fields for asset_id in ASSET_URI_RE.findall(field or "")}


def _snapshot_asset_url(snapshot_id, base_path="", include_solution=False, whole_group=False):
    prefix = base_path.rstrip("/")
    return lambda asset_id: "%s/api/question-assets/%s?snapshot_id=%s" % (
        prefix,
        quote(asset_id, safe=""),
        quote(snapshot_id, safe=""),
    ) + ("&solution=1" if include_solution else "") + ("&group=1" if whole_group else "")


def render_snapshot_options(conn, snapshot_id, school_id, base_path=""):
    """Render the authorized option bodies for interactive answer controls."""
    content = snapshot_content(conn, snapshot_id, school_id)
    if content is None:
        return None
    document = content["document"]
    if content["child_key"]:
        child = next((item for item in document["children"] if item["key"] == content["child_key"]), None)
        if child is None:
            return None
        options = child.get("options", [])
    else:
        options = document.get("options", [])
    from .question_rendering import render_markdown

    asset_url = _snapshot_asset_url(snapshot_id, base_path)
    return [
        {"key": option["key"], "html": render_markdown(option["markdown"], asset_url)}
        for option in options
    ]


def render_snapshot_content(
    conn,
    snapshot_id,
    school_id,
    base_path="",
    include_solution=False,
    include_options=True,
    whole_group=False,
):
    content = snapshot_content(conn, snapshot_id, school_id)
    if content is None:
        return None
    from .question_rendering import render_question

    return render_question(
        content["document"],
        asset_url=_snapshot_asset_url(snapshot_id, base_path, include_solution, whole_group),
        include_solution=include_solution,
        child_key=None if whole_group else content["child_key"] or None,
        include_options=include_options,
    )


def render_snapshot_solution(conn, snapshot_id, school_id, base_path=""):
    """Render only this scoring unit's answer and analysis, without repeating its stem."""
    content = snapshot_content(conn, snapshot_id, school_id)
    if content is None:
        return None
    from .question_rendering import render_markdown

    document = content["document"]
    asset_url = _snapshot_asset_url(snapshot_id, base_path, include_solution=True)
    if content["child_key"]:
        selected = next(
            (child for child in document.get("children", []) if child.get("key") == content["child_key"]),
            None,
        )
        if selected is None:
            return None
        fields = (document.get("answer_md", ""), document.get("analysis_md", ""),
                  selected.get("answer_md", ""), selected.get("analysis_md", ""))
    else:
        fields = (document.get("answer_md", ""), document.get("analysis_md", ""))
    rendered = "".join(render_markdown(field, asset_url) for field in fields if field)
    return '<section class="question-solution"><h3>参考答案与解析</h3>%s</section>' % rendered if rendered else ""
