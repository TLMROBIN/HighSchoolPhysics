"""Explainable main-question and child-item splitting for normalized documents."""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from .document_models import validate_document_ir, validate_question_document


QUESTION_RE = re.compile(
    r"(?m)(?:(?<=\n)|^|(?<=\s)|(?<=\)))[ \t]*(?:第\s*(?P<dnum>\d{1,3})\s*题|(?P<num>\d{1,3})\s*(?:[．、]|[.](?![ \t]*\d)|[.](?=20\d{2}年)))"
)
CHILD_RE = re.compile(r"(?m)(?:^|(?<=\n)|(?<=\)))[ \t]*[（(]\s*(?P<label>\d{1,2}|[一二三四五六七八九十])\s*[）)]\s*")
INLINE_OPTION_RE = re.compile(r"(?<![A-Za-z])\(?([A-H])[).．、]\s*")
FIGURE_OPTION_LABEL_RE = re.compile(r"^\s*([A-D])\s*$")
OPTION_AFTER_LIST_MARKER_RE = re.compile(r"^\s*\(?[A-H][).．、]\s*")
LIST_NUMBERED_OPTION_PREFIX_RE = re.compile(r"^\s*\d{1,3}\s*[.．、]\s+(?=\(?[A-H][).．、])")
MARKDOWN_IMAGE_PREFIX_RE = re.compile(r"^(?:!\[[^\]]*\]\(asset:[A-Za-z0-9_-]+\)\s*)+$")
ANSWER_HEADING_RE = re.compile(r"^(?:参考答案|答案(?:与解析|及解析)?|答案和解析|解析)\s*[:：]?$", re.IGNORECASE)
ANSWER_SECTION_TITLE_RE = re.compile(r"(?:参考答案|答案与解析|答案及解析|答案和解析)\s*(?:[|｜].*)?$", re.IGNORECASE)
ANSWER_CARD_TITLE_RE = re.compile(r"(?:答题卡|答题纸)\s*$")
SECTION_HEADING_RE = re.compile(
    r"^[一二三四五六七八九十]+[、．.]\s*(?:选择题|单选题|多选题|单项选择题|多项选择题|填空题|实验题|解答题|计算题|综合题)(?:\s*[（(].*)?$"
)
HEADER_TYPES = {"header", "footer"}


def _digest_key(*parts):
    digest = hashlib.sha256("\0".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:20]
    return "part_" + digest


def _spans(block, start=None, end=None):
    locator = dict(block.get("source_locator") or {})
    span = {"block_id": block["id"], "source_locator": locator}
    if start is not None:
        span["start"] = start
    if end is not None:
        span["end"] = end
    return [span]


def _question_starts(text):
    matches = []
    for match in QUESTION_RE.finditer(text):
        number = match.group("dnum") or match.group("num")
        if not number:
            continue
        if OPTION_AFTER_LIST_MARKER_RE.match(text[match.end():]):
            continue
        start = match.start()
        # Inline boundaries are useful for OCR paragraphs but are less certain than line starts.
        prefix = text[:start]
        inline = start > 0 and not prefix.endswith(("\n", "\r"))
        if inline and len(prefix.strip()) < 20:
            continue
        matches.append({"match": match, "number": str(int(number)), "start": start, "body_start": match.end(), "inline": inline})
    return matches


def _is_answer_heading(block):
    text = (block.get("markdown") or "").strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    return bool(ANSWER_HEADING_RE.fullmatch(text))


def _starts_answer_section(block):
    text = (block.get("markdown") or "").strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    return _is_answer_heading(block) or bool(ANSWER_SECTION_TITLE_RE.search(text))


def _starts_answer_card(block):
    text = (block.get("markdown") or "").strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    return bool(ANSWER_CARD_TITLE_RE.search(text))


def _is_section_heading(block):
    text = (block.get("markdown") or "").strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    return bool(SECTION_HEADING_RE.fullmatch(text))


def _split_answer_area(blocks):
    question_blocks = []
    answer_blocks = []
    answer_reason = None
    for block in blocks:
        if _starts_answer_section(block):
            answer_reason = "answer_area_requires_mapping"
        elif answer_reason is None and _starts_answer_card(block):
            answer_reason = "answer_card_requires_mapping"
        if answer_reason:
            answer_block = dict(block)
            answer_block["_unassigned_reason"] = answer_reason
            answer_blocks.append(answer_block)
        else:
            question_blocks.append(block)
    return question_blocks, answer_blocks


def _separate_numbered_questions(blocks):
    segments = []
    leading = []
    current = None
    inline_boundaries = []
    section_kind = None
    for block in blocks:
        text = block.get("markdown", "")
        if _is_section_heading(block):
            section_kind = ("multiple_choice" if "多选" in text or "多项选择" in text
                            else "single_choice" if "选择" in text or "单选" in text else None)
        matches = _question_starts(text)
        if not matches:
            if current is None or _is_section_heading(block):
                heading = dict(block)
                if _is_section_heading(block):
                    heading["_unassigned_reason"] = "section_heading"
                leading.append(heading)
            else:
                current["blocks"].append(block)
            continue
        # A heading like "一、选择题" stays in the preamble; numbered start owns its suffix.
        first = matches[0]
        image_prefix = text[:first["start"]]
        move_image_prefix = bool(image_prefix.strip()) and bool(MARKDOWN_IMAGE_PREFIX_RE.fullmatch(image_prefix))
        if first["start"] > 0 and text[:first["start"]].strip():
            if not move_image_prefix:
                prefix = dict(block)
                prefix["markdown"] = text[:first["start"]].strip()
                prefix["source_locator"] = dict(block.get("source_locator") or {}, end=first["start"])
                if current is None:
                    leading.append(prefix)
                else:
                    current["blocks"].append(prefix)
        for index, item in enumerate(matches):
            start = item["start"]
            end = matches[index + 1]["start"] if index + 1 < len(matches) else len(text)
            body_start = item["body_start"]
            body = text[body_start:end].strip()
            if current is not None:
                segments.append(current)
            if item["inline"]:
                inline_boundaries.append({"block_id": block["id"], "number": item["number"], "start": start})
            body_block = dict(block)
            if index == 0 and move_image_prefix:
                body_block["markdown"] = image_prefix.strip() + "\n\n" + body
                body_block["source_locator"] = dict(block.get("source_locator") or {}, start=0, end=end)
            else:
                body_block["markdown"] = body
                body_block["source_locator"] = dict(block.get("source_locator") or {}, start=body_start, end=end)
            current = {"number": item["number"], "blocks": [body_block], "section_kind": section_kind}
    if current is not None:
        segments.append(current)
    return leading, segments, inline_boundaries


def _complete_inline_option_start(keys):
    """Return the last complete A-D run in OCR markers, ignoring earlier prose hits."""
    expected = ["A", "B", "C", "D"]
    for start in range(len(keys) - len(expected), -1, -1):
        if keys[start : start + len(expected)] == expected:
            return start
    return None


def _labeled_figure_choice_run(blocks, start):
    """Recognize a tightly aligned figure, A label, figure, B label ... sequence."""
    if start + 8 > len(blocks):
        return None
    figures = []
    labels = []
    for offset, expected in enumerate("ABCD"):
        figure = blocks[start + offset * 2]
        label = blocks[start + offset * 2 + 1]
        label_match = FIGURE_OPTION_LABEL_RE.fullmatch(label.get("markdown", ""))
        figure_bbox = figure.get("bbox")
        label_bbox = label.get("bbox")
        if (
            figure.get("type") != "figure"
            or label.get("type") not in {"paragraph", "heading", "list_item"}
            or label_match is None
            or label_match.group(1) != expected
            or not isinstance(figure_bbox, (list, tuple))
            or not isinstance(label_bbox, (list, tuple))
            or len(figure_bbox) != 4
            or len(label_bbox) != 4
        ):
            return None
        try:
            fx0, fy0, fx1, fy1 = (float(value) for value in figure_bbox)
            lx0, ly0, lx1, ly1 = (float(value) for value in label_bbox)
        except (TypeError, ValueError):
            return None
        width = fx1 - fx0
        gap = ly0 - fy1
        label_center_x = (lx0 + lx1) / 2
        if (
            width <= 0
            or fx1 <= fx0
            or fy1 <= fy0
            or lx1 <= lx0
            or ly1 <= ly0
            or gap < -0.01
            or gap > 0.05
            or label_center_x < fx0 - width * 0.1
            or label_center_x > fx1 + width * 0.1
        ):
            return None
        figures.append((fx0, fy0, fx1, fy1))
        labels.append(label)
    if any(figures[index][0] >= figures[index + 1][0] for index in range(3)):
        return None
    row_tolerance = 0.025
    if max(item[1] for item in figures) - min(item[1] for item in figures) > row_tolerance:
        return None
    if max(item[3] for item in figures) - min(item[3] for item in figures) > row_tolerance:
        return None
    return labels


def _promote_labeled_figure_choices(blocks):
    """Turn source-proven A-D image labels into editable image options."""
    promoted = []
    index = 0
    while index < len(blocks):
        labels = _labeled_figure_choice_run(blocks, index)
        if labels is None:
            promoted.append(blocks[index])
            index += 1
            continue
        for offset, label in enumerate(labels):
            figure = dict(blocks[index + offset * 2])
            figure["_figure_option_association"] = True
            figure["_finish_option_figure"] = True
            marker = dict(label)
            marker["markdown"] = "%s." % label["markdown"].strip()
            promoted.extend((marker, figure))
        index += 8
    return promoted


def _option_parts(blocks):
    stem_blocks = []
    options = []
    active_option = None
    option_keys = []
    issues = []
    blocks = _promote_labeled_figure_choices(blocks)
    for block in blocks:
        text = block.get("markdown", "")
        if block.get("type") == "figure":
            if active_option is not None and block.get("_figure_option_association"):
                active_option["markdown"] += ("\n\n" if active_option["markdown"] else "") + text
                active_option["_blocks"].append(block)
                if block.get("_finish_option_figure"):
                    active_option = None
            else:
                if options:
                    issues.append({
                        "code": "figure_after_options_requires_review",
                        "severity": "review",
                        "field": block.get("id", "figure"),
                    })
                active_option = None
                stem_blocks.append(block)
            continue
        lines = text.splitlines() or [text]
        stem_lines = []
        for line in lines:
            option_line = line
            if block.get("type") == "list_item":
                option_line = LIST_NUMBERED_OPTION_PREFIX_RE.sub("", option_line, count=1)
            matches = list(INLINE_OPTION_RE.finditer(option_line))
            detected_keys = [match.group(1) for match in matches]
            sequence_start = _complete_inline_option_start(detected_keys)
            if sequence_start is not None and sequence_start:
                matches = matches[sequence_start:]
                detected_keys = detected_keys[sequence_start:]
            prefix = option_line[:matches[0].start()].strip() if matches else ""
            image_prefix = bool(prefix) and bool(MARKDOWN_IMAGE_PREFIX_RE.fullmatch(prefix))
            inline_option_sequence = bool(prefix) and _complete_inline_option_start(detected_keys) == 0
            at_start = bool(matches) and (not prefix or image_prefix or inline_option_sequence)
            if at_start:
                if prefix and (image_prefix or inline_option_sequence):
                    stem_lines.append(prefix)
                for index, match in enumerate(matches):
                    end = matches[index + 1].start() if index + 1 < len(matches) else len(option_line)
                    key = match.group(1)
                    body = option_line[match.end():end].strip()
                    if not option_keys and key != "A":
                        issues.append({"code": "option_sequence_does_not_start_with_a", "severity": "review", "field": block["id"]})
                    if key in option_keys or (option_keys and ord(key) != ord(option_keys[-1]) + 1):
                        issues.append({"code": "option_order_or_duplicate", "severity": "review", "field": block["id"]})
                    if key not in option_keys:
                        option_keys.append(key)
                        active_option = {"key": key, "markdown": body, "_blocks": [block]}
                        options.append(active_option)
                    else:
                        active_option = next(item for item in options if item["key"] == key)
                        active_option["markdown"] += ("\n" if body else "") + body
                        active_option["_blocks"].append(block)
                continue
            if active_option is not None and any(option.get("_blocks", []) and option["_blocks"][-1] is block for option in options):
                active_option["markdown"] += ("\n" if active_option["markdown"] else "") + option_line.strip()
                continue
            active_option = None
            stem_lines.append(option_line)
        if stem_lines:
            stem_block = dict(block)
            stem_block["markdown"] = "\n".join(stem_lines).strip()
            stem_blocks.append(stem_block)
    return stem_blocks, options, issues


def _split_children(blocks, document_id, question_number, conversion_id):
    parent = []
    children = []
    for block in blocks:
        text = block.get("markdown", "")
        matches = list(CHILD_RE.finditer(text))
        if not matches:
            (children[-1]["blocks"] if children else parent).append(block)
            continue
        if matches[0].start() > 0 and text[:matches[0].start()].strip():
            prefix = dict(block)
            prefix["markdown"] = text[:matches[0].start()].strip()
            prefix["source_locator"] = dict(block.get("source_locator") or {}, end=matches[0].start())
            parent.append(prefix)
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[match.end():end].strip()
            label_value = match.group("label")
            label = "(%s)" % label_value
            child_index = len(children) + 1
            key = _digest_key(document_id, conversion_id, question_number, child_index, label, body[:100])
            child_block = dict(block)
            child_block["markdown"] = body
            child_block["source_locator"] = dict(block.get("source_locator") or {}, start=match.end(), end=end)
            children.append({"key": key, "label": label, "blocks": [child_block]})
    return parent, children


def _markdown(blocks):
    return "\n\n".join(block.get("markdown", "").strip() for block in blocks if block.get("markdown", "").strip())


def _assets_in(markdown):
    return sorted(set(re.findall(r"!\[[^\]]*\]\(asset:([A-Za-z0-9_-]+)(?:\s+[^)]*)?\)", markdown or "")))


def _kind_for(stem, options, children, section_kind=None):
    text = stem
    if options:
        return "multiple_choice" if section_kind == "multiple_choice" or "多选" in text or "不定项" in text else "single_choice"
    if "实验" in text or "探究" in text:
        return "experiment"
    if re.search(r"_{2,}|（\s*）|\(\s*\)", text):
        return "fill"
    return "structured" if children else "short_answer"


def _candidate(segment, document, conversion_id, parent_spans, inline_boundary=False):
    number = segment["number"]
    blocks = segment["blocks"]
    parent_blocks, children_raw = _split_children(blocks, document["document_id"], number, conversion_id)
    stem_blocks, options_raw, option_issues = _option_parts(parent_blocks)
    stem_md = _markdown(stem_blocks)
    options = [{"key": item["key"], "markdown": item["markdown"]} for item in options_raw]
    children = []
    issues = list(option_issues)
    for block in blocks:
        issues.extend(block.get("issues", []))
    for child in children_raw:
        child_stem_blocks, child_options_raw, child_option_issues = _option_parts(child["blocks"])
        child_stem = _markdown(child_stem_blocks)
        child_options = [{"key": item["key"], "markdown": item["markdown"]} for item in child_options_raw]
        child_issues = list(child_option_issues)
        child_spans = [span for block in child["blocks"] for span in _spans(block)]
        children.append({
            "key": child["key"],
            "label": child["label"],
            "kind": (_kind_for(child_stem, child_options, [], segment.get("section_kind")) if child_options
                     else "fill" if re.search(r"_{2,}|（\s*）|\(\s*\)", child_stem) else "short_answer"),
            "stem_md": child_stem,
            "options": child_options,
            "answer_md": "",
            "analysis_md": "",
            "answer_state": "missing",
            "grading_rule": None,
            "source_spans": child_spans,
        })
        issues.extend(child_issues)
    source_spans = parent_spans + [span for block in blocks for span in _spans(block)]
    if not stem_md.strip() and not children:
        issues.append({"code": "question_has_no_editable_text", "severity": "blocking", "field": "stem_md"})
    elif not re.search(r"[\u3400-\u9fffA-Za-z0-9]", stem_md) and not children:
        issues.append({"code": "question_contains_only_figures", "severity": "blocking", "field": "stem_md"})
    if inline_boundary:
        issues.append({"code": "inline_question_boundary_requires_review", "severity": "review", "field": "number"})
    for block in blocks:
        for issue in block.get("issues", []):
            if issue not in issues:
                issues.append(issue)
    all_markdown = [stem_md, *(item["markdown"] for item in options)]
    for child in children:
        all_markdown.append(child["stem_md"])
        all_markdown.extend(option["markdown"] for option in child["options"])
    asset_refs = sorted({asset_id for text in all_markdown for asset_id in _assets_in(text)})
    kind = _kind_for(stem_md, options, children, segment.get("section_kind"))
    question = {
        "schema_version": 1,
        "number": number,
        "kind": kind,
        "stem_md": stem_md,
        "options": options,
        "answer_md": "",
        "analysis_md": "",
        "answer_state": "missing",
        "grading_rule": None,
        "children": children,
        "source_spans": source_spans,
        "asset_refs": asset_refs,
        "issues": issues,
    }
    return validate_question_document(question, known_asset_ids={asset["id"] for asset in document.get("assets", [])})


def split_document_ir(document):
    """Return editable question candidates and every block left for review."""
    ir = validate_document_ir(document, known_asset_ids={asset["id"] for asset in document.get("assets", [])})
    question_blocks, answer_blocks = _split_answer_area(ir["blocks"])
    answer_reasons = {block["id"]: block["_unassigned_reason"] for block in answer_blocks}
    leading, segments, inline = _separate_numbered_questions(question_blocks)
    inline_numbers = {item["number"] for item in inline}
    number_counts = Counter(segment["number"] for segment in segments)
    candidates = []
    for index, segment in enumerate(segments):
        question = _candidate(segment, ir, ir["conversion_id"], [], segment["number"] in inline_numbers)
        if number_counts[segment["number"]] > 1:
            question["issues"].append({
                "code": "duplicate_question_number",
                "severity": "review",
                "field": "number",
                "message": "原卷题号 %s 在本份文档中出现 %d 次；请对照原卷核对或更正题号。"
                % (segment["number"], number_counts[segment["number"]]),
            })
        candidates.append({
            "item_index": index + 1,
            "document": question,
            "issues": question["issues"],
            "source_spans": question["source_spans"],
        })
    assigned = {span["block_id"] for item in candidates for span in item["source_spans"]}
    unassigned = [
        {
            "block_id": block["id"],
            "reason": block.get("_unassigned_reason", "document_preamble"),
            "markdown": block.get("markdown", ""),
            "source_locator": block.get("source_locator", {}),
        }
        for block in leading
    ]
    leading_ids = {block["id"] for block in leading}
    unassigned.extend(
        {"block_id": block["id"], "reason": answer_reasons.get(block["id"], "not_assigned_to_question"), "markdown": block.get("markdown", ""), "source_locator": block.get("source_locator", {})}
        for block in ir["blocks"]
        if block["id"] not in assigned and block["id"] not in leading_ids
    )
    return {
        "questions": candidates,
        "unassigned_blocks": unassigned,
        "answer_blocks": [block["id"] for block in answer_blocks],
        "issues": [
            {"code": "no_question_boundaries_found", "severity": "blocking", "field": "blocks"}
        ] if not candidates else [],
    }
