"""Offline Markdown ZIP export for immutable question content versions."""

import hashlib
import io
import json
import re
import zipfile

from .document_models import ASSET_URI_RE, validate_question_document


MAX_EXPORT_BYTES = 100 * 1024 * 1024
MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
RESERVED_H2_RE = re.compile(r"(?m)^## (题干|选项|小问|答案|解析)\s*$")


class QuestionExportError(ValueError):
    pass


def _export_body(markdown):
    return RESERVED_H2_RE.sub(lambda match: "### " + match.group(1), markdown or "")


def _markdown_assets(markdown):
    return set(ASSET_URI_RE.findall(markdown or ""))


def _question_markdown(document, child_key=None, include_solution=False):
    doc = document
    if child_key is not None:
        child = next((item for item in doc.get("children", []) if item.get("key") == child_key), None)
        if child is None:
            raise QuestionExportError("The requested child does not exist")
        lines = ["# %s %s" % (doc["number"], child["label"]), "", _export_body(doc["stem_md"]), "", _export_body(child["stem_md"])]
        if child.get("options"):
            lines.extend(("", "## 选项"))
            lines.extend("**%s.** %s" % (item["key"], _export_body(item["markdown"])) for item in child["options"])
        if include_solution:
            if doc.get("answer_md") or child.get("answer_md"):
                lines.extend(("", "## 答案"))
                if doc.get("answer_md"):
                    lines.append(_export_body(doc["answer_md"]))
                if child.get("answer_md"):
                    lines.append(_export_body(child["answer_md"]))
            if doc.get("analysis_md") or child.get("analysis_md"):
                lines.extend(("", "## 解析"))
                if doc.get("analysis_md"):
                    lines.append(_export_body(doc["analysis_md"]))
                if child.get("analysis_md"):
                    lines.append(_export_body(child["analysis_md"]))
        markdown = "\n".join(lines)
    else:
        lines = ["# %s" % doc["number"], "", _export_body(doc["stem_md"])]
        if doc["options"]:
            lines.extend(("", "## 选项"))
            lines.extend("**%s.** %s" % (item["key"], _export_body(item["markdown"])) for item in doc["options"])
        if doc["children"]:
            lines.extend(("", "## 小问"))
            for child in doc["children"]:
                lines.extend(("", "### %s" % child["label"], _export_body(child["stem_md"])))
                if child.get("options"):
                    lines.extend("**%s.** %s" % (item["key"], _export_body(item["markdown"])) for item in child["options"])
        if include_solution:
            answer_fields = [doc.get("answer_md", "")] + [child.get("answer_md", "") for child in doc["children"]]
            if any(answer_fields):
                lines.extend(("", "## 答案"))
                if doc.get("answer_md"):
                    lines.append(_export_body(doc["answer_md"]))
                for child in doc["children"]:
                    if child.get("answer_md"):
                        lines.extend(("", "### %s" % child["label"], _export_body(child["answer_md"])))
            analysis_fields = [doc.get("analysis_md", "")] + [child.get("analysis_md", "") for child in doc["children"]]
            if any(analysis_fields):
                lines.extend(("", "## 解析"))
                if doc.get("analysis_md"):
                    lines.append(_export_body(doc["analysis_md"]))
                for child in doc["children"]:
                    if child.get("analysis_md"):
                        lines.extend(("", "### %s" % child["label"], _export_body(child["analysis_md"])))
        markdown = "\n".join(lines)

    return markdown


def _replace_asset_uris(markdown, replacements):
    pattern = re.compile(
        r"!\[([^\]]*)\]\(asset:([A-Za-z0-9_-]{1,64})"
        r"(?:\s+(?:\"([^\"]*)\"|'([^']*)'|\(([^)]*)\)))?\)"
    )

    def replace(match):
        asset_id = match.group(2)
        path = replacements.get(asset_id)
        if path is None:
            raise QuestionExportError("An image reference was not loaded")
        alt = match.group(1)
        title = next((value for value in match.groups()[2:] if value is not None), None)
        suffix = ' "%s"' % title.replace('"', "&quot;") if title is not None else ""
        return "![%s](%s%s)" % (alt, path, suffix)

    replaced = pattern.sub(replace, markdown)
    if re.search(r"!\[[^\]]*\]\(asset:", replaced):
        raise QuestionExportError("An image reference could not be converted to a local path")
    return replaced


def _load_assets(asset_ids, asset_loader):
    context = {}
    files = {}
    manifest = []
    total = 0
    for asset_id in sorted(asset_ids):
        payload = asset_loader(asset_id)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), bytes):
            raise QuestionExportError("The asset could not be read")
        mime = payload.get("mime_type")
        extension = MIME_EXTENSIONS.get(mime)
        if extension is None:
            raise QuestionExportError("The asset MIME type is not exportable")
        data = payload["data"]
        digest = hashlib.sha256(data).hexdigest()
        expected = payload.get("sha256")
        if expected and expected != digest:
            raise QuestionExportError("The asset hash does not match its bytes")
        filename = "fig-%s%s" % (digest[:16], extension)
        context[asset_id] = {"filename": filename, "sha256": digest, "mime_type": mime}
        if filename not in files:
            files[filename] = data
            total += len(data)
        manifest.append(
            {
                "asset_id": asset_id,
                "path": "images/" + filename,
                "sha256": digest,
                "mime_type": mime,
            }
        )
    return files, manifest, total, context


def _zip_bytes(entries, max_bytes):
    uncompressed_size = sum(len(data) for data in entries.values())
    if uncompressed_size > max_bytes:
        raise QuestionExportError("Export exceeds the configured size limit")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    result = output.getvalue()
    if len(result) > max_bytes:
        raise QuestionExportError("Compressed export exceeds the configured size limit")
    return result


def _require_solution_permission(include_solution, actor_role):
    if include_solution and actor_role not in ("teacher", "admin"):
        raise QuestionExportError("Only teachers can export answers and analysis")


def build_question_markdown_zip(
    document,
    revision_id,
    asset_loader,
    include_solution=False,
    actor_role="teacher",
    child_key=None,
    max_bytes=MAX_EXPORT_BYTES,
):
    _require_solution_permission(include_solution, actor_role)
    doc = validate_question_document(document)
    raw_markdown = _question_markdown(doc, child_key, include_solution)
    asset_ids = _markdown_assets(raw_markdown)
    assets, manifest, _size, context = _load_assets(asset_ids, asset_loader)
    markdown = _replace_asset_uris(
        raw_markdown,
        {asset_id: "images/" + context[asset_id]["filename"] for asset_id in asset_ids},
    )
    public_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    source = {
        "schema_version": 1,
        "revision_id": revision_id,
        "question_number": doc["number"],
        "kind": doc["kind"],
        "child_key": child_key,
        "includes_solution": bool(include_solution),
        "export_sha256": public_hash,
        "assets": manifest,
    }
    entries = {
        "question.md": markdown.encode("utf-8"),
        "source.json": json.dumps(source, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
    }
    entries.update({"images/" + filename: data for filename, data in assets.items()})
    return _zip_bytes(entries, max_bytes)


def build_paper_markdown_zip(
    questions,
    paper_title,
    asset_loader,
    include_solution=False,
    actor_role="teacher",
    max_bytes=MAX_EXPORT_BYTES,
    review_metadata=None,
):
    _require_solution_permission(include_solution, actor_role)
    if not isinstance(paper_title, str) or not paper_title.strip():
        raise QuestionExportError("A paper title is required")
    if not isinstance(questions, (list, tuple)):
        raise QuestionExportError("Questions must be provided in paper order")
    normalized = []
    all_assets = set()
    for index, item in enumerate(questions, 1):
        if not isinstance(item, dict) or not isinstance(item.get("document"), dict):
            raise QuestionExportError("Each paper item must contain a question document")
        doc = validate_question_document(item["document"])
        content = _question_markdown(doc, item.get("child_key"), include_solution)
        asset_ids = _markdown_assets(content)
        all_assets.update(asset_ids)
        normalized.append((index, item.get("revision_id"), doc, item.get("child_key"), content))

    assets, asset_manifest, _size, context = _load_assets(all_assets, asset_loader)
    paper_lines = ["# " + paper_title]
    entries = {}
    source_items = []
    for index, (number, revision_id, doc, child_key, raw_content) in enumerate(normalized, 1):
        filename = "questions/%03d.md" % number
        child_assets = _markdown_assets(raw_content)
        question_replacements = {
            asset_id: "../images/" + context[asset_id]["filename"]
            for asset_id in child_assets
        }
        entries[filename] = _replace_asset_uris(raw_content, question_replacements).encode("utf-8")
        paper_replacements = {
            asset_id: "images/" + context[asset_id]["filename"]
            for asset_id in child_assets
        }
        paper_lines.extend(
            ("", "---", "", _replace_asset_uris(raw_content, paper_replacements))
        )
        source_item = {
            "order": index,
            "question_number": doc["number"],
            "revision_id": revision_id,
            "child_key": child_key,
        }
        if isinstance(item.get("review"), dict):
            source_item["review"] = item["review"]
        source_items.append(source_item)
    if review_metadata is not None:
        if not isinstance(review_metadata, dict):
            raise QuestionExportError("Review metadata must be an object")
        warning = "校对稿：尚未完成教师审核，不可直接用于考试或学生练习。"
        paper_lines[1:1] = ["", "> **%s**" % warning]
    manifest = {
        "schema_version": 1,
        "title": paper_title,
        "includes_solution": bool(include_solution),
        "questions": source_items,
        "assets": asset_manifest,
    }
    if review_metadata is not None:
        manifest["export_state"] = "teacher_review_draft"
        manifest["review_metadata"] = review_metadata
        entries["REVIEW-STATUS.txt"] = (
            "教师校对稿\n"
            "尚未完成教师审核；不可直接用于考试或学生练习。\n"
            "请通过原导入任务页面对照原卷完成校对，再批量入库。\n"
        ).encode("utf-8")
    entries["paper.md"] = "\n".join(paper_lines).encode("utf-8")
    entries["source.json"] = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")
    entries.update({"images/" + filename: data for filename, data in assets.items()})
    return _zip_bytes(entries, max_bytes)
