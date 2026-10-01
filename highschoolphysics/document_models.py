"""Validated, JSON-only document and question content models."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any


ASSET_URI_RE = re.compile(r"!\[[^\]]*\]\(asset:([A-Za-z0-9_-]+)(?:\s+[^)]*)?\)")
STABLE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ISSUE_SEVERITIES = {"blocking", "review", "info"}
ANSWER_STATES = {"missing", "needs_review", "verified"}
BLOCK_TYPES = {
    "paragraph",
    "heading",
    "list_item",
    "table",
    "formula",
    "figure",
    "caption",
    "header",
    "footer",
}


class DocumentValidationError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _normalize_newlines(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _normalize_newlines(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_newlines(v, key) for v in value]
    if isinstance(value, str) and (key.endswith("_md") or key == "markdown"):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    return value


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            _normalize_newlines(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DocumentValidationError(["content is not finite JSON data: %s" % exc])


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 and math.isfinite(value)


def _validate_issues(issues, path, errors):
    if not isinstance(issues, list):
        errors.append("%s must be an array" % path)
        return
    for index, issue in enumerate(issues):
        ipath = "%s[%d]" % (path, index)
        if not isinstance(issue, dict):
            errors.append("%s must be an object" % ipath)
            continue
        if not isinstance(issue.get("code"), str) or not issue["code"].strip():
            errors.append("%s.code is required" % ipath)
        if issue.get("severity") not in ISSUE_SEVERITIES:
            errors.append("%s.severity is invalid" % ipath)


def validate_document_ir(document, known_asset_ids=None):
    errors = []
    if not isinstance(document, dict):
        raise DocumentValidationError(["document IR must be an object"])
    if document.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ("document_id", "conversion_id", "source_sha256"):
        if not isinstance(document.get(field), str) or not document[field].strip():
            errors.append("%s is required" % field)
    if isinstance(document.get("source_sha256"), str) and not re.fullmatch(
        r"[0-9a-f]{64}", document["source_sha256"]
    ):
        errors.append("source_sha256 must be lowercase SHA-256 hex")

    pages = document.get("pages")
    page_numbers = set()
    if not isinstance(pages, list):
        errors.append("pages must be an array")
        pages = []
    for index, page in enumerate(pages):
        ppath = "pages[%d]" % index
        if not isinstance(page, dict):
            errors.append("%s must be an object" % ppath)
            continue
        number = page.get("page")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            errors.append("%s.page must be a 1-based integer" % ppath)
        elif number in page_numbers:
            errors.append("pages contain a duplicate page number")
        else:
            page_numbers.add(number)
        if not _is_positive_number(page.get("width")) or not _is_positive_number(page.get("height")):
            errors.append("%s dimensions must be positive numbers" % ppath)
        rotation = page.get("rotation_applied", 0)
        if not isinstance(rotation, int) or rotation not in (0, 90, 180, 270):
            errors.append("%s.rotation_applied must be 0, 90, 180, or 270" % ppath)

    assets = document.get("assets")
    asset_ids = set()
    if not isinstance(assets, list):
        errors.append("assets must be an array")
        assets = []
    for index, asset in enumerate(assets):
        apath = "assets[%d]" % index
        if not isinstance(asset, dict):
            errors.append("%s must be an object" % apath)
            continue
        aid = asset.get("id")
        if not isinstance(aid, str) or not STABLE_KEY_RE.fullmatch(aid):
            errors.append("%s.id is invalid" % apath)
        elif aid in asset_ids:
            errors.append("assets contain a duplicate id")
        else:
            asset_ids.add(aid)
        digest = asset.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append("%s.sha256 must be lowercase SHA-256 hex" % apath)
        if not isinstance(asset.get("mime_type"), str) or not asset["mime_type"].startswith("image/"):
            errors.append("%s.mime_type must be an image MIME type" % apath)

    blocks = document.get("blocks")
    if not isinstance(blocks, list):
        errors.append("blocks must be an array")
        blocks = []
    seen_block_ids = set()
    for index, block in enumerate(blocks):
        bpath = "blocks[%d]" % index
        if not isinstance(block, dict):
            errors.append("%s must be an object" % bpath)
            continue
        bid = block.get("id")
        if not isinstance(bid, str) or not STABLE_KEY_RE.fullmatch(bid):
            errors.append("%s.id is invalid" % bpath)
        elif bid in seen_block_ids:
            errors.append("blocks contain a duplicate id")
        else:
            seen_block_ids.add(bid)
        if block.get("type") not in BLOCK_TYPES:
            errors.append("%s.type is invalid" % bpath)
        if not isinstance(block.get("order"), int) or block.get("order", 0) < 0:
            errors.append("%s.order must be a nonnegative integer" % bpath)
        page = block.get("page")
        if page is not None and (not isinstance(page, int) or page not in page_numbers):
            errors.append("%s.page must refer to a known page or be null" % bpath)
        bbox = block.get("bbox")
        if bbox is not None:
            valid_box = (
                isinstance(bbox, list)
                and len(bbox) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1 for v in bbox)
                and bbox[0] <= bbox[2]
                and bbox[1] <= bbox[3]
            )
            if not valid_box:
                errors.append("%s.bbox must be a normalized [x0,y0,x1,y1]" % bpath)
        if not isinstance(block.get("markdown"), str):
            errors.append("%s.markdown must be a string" % bpath)
        refs = block.get("asset_ids", [])
        if not isinstance(refs, list) or any(not isinstance(a, str) for a in refs):
            errors.append("%s.asset_ids must be an array of ids" % bpath)
        elif known_asset_ids is not None and any(a not in known_asset_ids for a in refs):
            errors.append("%s references an unknown asset" % bpath)
        _validate_issues(block.get("issues", []), bpath + ".issues", errors)

    _validate_issues(document.get("issues", []), "issues", errors)
    if known_asset_ids is not None and any(a not in known_asset_ids for a in asset_ids):
        errors.append("IR contains an asset outside the authorized asset set")
    if errors:
        raise DocumentValidationError(errors)
    return _normalize_newlines(copy.deepcopy(document))


def validate_question_document(document, known_asset_ids=None):
    errors = []
    if not isinstance(document, dict):
        raise DocumentValidationError(["question document must be an object"])
    if document.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ("number", "kind", "stem_md", "answer_md", "analysis_md"):
        if not isinstance(document.get(field), str):
            errors.append("%s must be a string" % field)
    if not isinstance(document.get("number"), str) or not document["number"].strip():
        errors.append("number is required")
    if document.get("answer_state") not in ANSWER_STATES:
        errors.append("answer_state is invalid")

    options = document.get("options")
    option_keys = []
    if not isinstance(options, list):
        errors.append("options must be an ordered array")
        options = []
    for index, option in enumerate(options):
        path = "options[%d]" % index
        if not isinstance(option, dict):
            errors.append("%s must be an object" % path)
            continue
        key = option.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-H]", key):
            errors.append("%s.key must be A through H" % path)
        elif key in option_keys:
            errors.append("option keys must be unique")
        else:
            option_keys.append(key)
        if not isinstance(option.get("markdown"), str):
            errors.append("%s.markdown must be a string" % path)

    children = document.get("children")
    child_keys = []
    if not isinstance(children, list):
        errors.append("children must be an ordered array")
        children = []
    for index, child in enumerate(children):
        path = "children[%d]" % index
        if not isinstance(child, dict):
            errors.append("%s must be an object" % path)
            continue
        key = child.get("key")
        if not isinstance(key, str) or not STABLE_KEY_RE.fullmatch(key):
            errors.append("%s.key is invalid" % path)
        elif key in child_keys:
            errors.append("child keys must be unique")
        else:
            child_keys.append(key)
        for field in ("label", "kind", "stem_md", "answer_md", "analysis_md"):
            if not isinstance(child.get(field), str):
                errors.append("%s.%s must be a string" % (path, field))
        if child.get("answer_state") not in ANSWER_STATES:
            errors.append("%s.answer_state is invalid" % path)
        if not isinstance(child.get("options", []), list):
            errors.append("%s.options must be an array" % path)
        else:
            keys = set()
            for option in child.get("options", []):
                if (not isinstance(option, dict) or not isinstance(option.get("key"), str)
                        or not re.fullmatch(r"[A-H]", option["key"])
                        or not isinstance(option.get("markdown"), str)):
                    errors.append("%s.options must contain A-H keys and Markdown text" % path)
                elif option["key"] in keys:
                    errors.append("%s.options contain duplicate keys" % path)
                else:
                    keys.add(option["key"])
        spans = child.get("source_spans", [])
        if not isinstance(spans, list):
            errors.append("%s.source_spans must be an array" % path)

    for field in ("source_spans", "asset_refs", "issues"):
        if not isinstance(document.get(field), list):
            errors.append("%s must be an array" % field)
    refs = document.get("asset_refs", [])
    if isinstance(refs, list):
        if any(not isinstance(ref, str) or not STABLE_KEY_RE.fullmatch(ref) for ref in refs):
            errors.append("asset_refs contain an invalid id")
        if len(set(refs)) != len(refs):
            errors.append("asset_refs must be unique")
        if known_asset_ids is not None and any(ref not in known_asset_ids for ref in refs):
            errors.append("asset_refs contain an unauthorized asset")

    for index, span in enumerate(document.get("source_spans", []) if isinstance(document.get("source_spans"), list) else []):
        if not isinstance(span, dict) or not isinstance(span.get("block_id"), str):
            errors.append("source_spans[%d] must identify a block" % index)
    _validate_issues(document.get("issues", []), "issues", errors)

    markdown_fields = []
    if isinstance(document.get("stem_md"), str):
        markdown_fields.append(document["stem_md"])
    if isinstance(document.get("answer_md"), str):
        markdown_fields.append(document["answer_md"])
    if isinstance(document.get("analysis_md"), str):
        markdown_fields.append(document["analysis_md"])
    for option in options:
        if isinstance(option, dict) and isinstance(option.get("markdown"), str):
            markdown_fields.append(option["markdown"])
    for child in children:
        if isinstance(child, dict):
            markdown_fields.extend(
                child[field]
                for field in ("stem_md", "answer_md", "analysis_md")
                if isinstance(child.get(field), str)
            )
            for option in child.get("options", []) if isinstance(child.get("options", []), list) else []:
                if isinstance(option, dict) and isinstance(option.get("markdown"), str):
                    markdown_fields.append(option["markdown"])
    referenced_assets = set()
    for text in markdown_fields:
        referenced_assets.update(ASSET_URI_RE.findall(text))
    if referenced_assets - set(refs):
        errors.append("markdown references assets missing from asset_refs")

    try:
        json.dumps(document, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        errors.append("content is not JSON serializable: %s" % exc)
    if errors:
        raise DocumentValidationError(errors)
    return _normalize_newlines(copy.deepcopy(document))


def question_content_sha256(document, known_asset_ids=None):
    normalized = validate_question_document(document, known_asset_ids=known_asset_ids)
    return canonical_sha256(normalized)
