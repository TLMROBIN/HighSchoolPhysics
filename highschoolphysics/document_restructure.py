"""Safe ordering and reversible restructures for unpublished candidates."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter

from .document_models import canonical_json
from .errors import StateConflict


def _unique_spans(documents):
    spans = {}
    for document in documents:
        for span in document.get("source_spans", []):
            spans.setdefault(canonical_json(span), span)
    return [spans[key] for key in sorted(spans)]


def _document_markdown_fields(document):
    fields = [document.get("stem_md", ""), document.get("answer_md", ""), document.get("analysis_md", "")]
    fields.extend(option.get("markdown", "") for option in document.get("options", []))
    for child in document.get("children", []):
        fields.extend((child.get("stem_md", ""), child.get("answer_md", ""), child.get("analysis_md", "")))
        fields.extend(option.get("markdown", "") for option in child.get("options", []))
    return fields


def _asset_refs(document):
    return sorted(set(re.findall(r"!\[[^\]]*\]\(asset:([A-Za-z0-9_-]+)(?:\s+[^)]*)?\)", "\n".join(_document_markdown_fields(document)))))


def _append_issue(document, issue):
    fingerprint = canonical_json({key: value for key, value in issue.items() if key not in {"severity", "state", "reviewed_by", "reviewed_at", "resolution_note"}})
    existing = {
        canonical_json({key: value for key, value in current.items() if key not in {"severity", "state", "reviewed_by", "reviewed_at", "resolution_note"}})
        for current in document.get("issues", [])
    }
    if fingerprint not in existing:
        document.setdefault("issues", []).append(issue)


def _answer_fields_present(document):
    if document.get("answer_md") or document.get("analysis_md"):
        return True
    return any(child.get("answer_md") or child.get("analysis_md") for child in document.get("children", []))


def _split_documents(document, offset, source_id):
    import copy

    source = document["stem_md"]
    left, right = source[:offset], source[offset:]
    if not left.strip() or not right.strip():
        raise ValueError("Choose a split point with editable question text on both sides")
    outputs = [copy.deepcopy(document), copy.deepcopy(document)]
    outputs[0]["stem_md"] = left
    outputs[1]["stem_md"] = right
    for part, output in enumerate(outputs, start=1):
        if _answer_fields_present(output):
            output["answer_state"] = "needs_review"
            for child in output.get("children", []):
                if child.get("answer_md") or child.get("analysis_md"):
                    child["answer_state"] = "needs_review"
        _append_issue(output, {
            "code": "candidate_split_requires_review",
            "severity": "blocking",
            "field": "stem_md",
            "message": "拆分产生的新候选保留了原答案、选项和小问；请逐项核对正文、题号及答案后再发布。",
            "source_item_id": source_id,
            "part": part,
        })
        output["asset_refs"] = _asset_refs(output)
    return outputs


def _renumber_options(options):
    if len(options) > 8:
        raise ValueError("Merged candidate has more than eight choices; edit the choices before merging")
    return [dict(option, key=chr(ord("A") + index)) for index, option in enumerate(options)]


def _merge_documents(documents, source_ids):
    import copy

    from .document_models import canonical_json

    merged = copy.deepcopy(documents[0])
    merged["stem_md"] = "\n\n".join(document["stem_md"] for document in documents if document.get("stem_md", ""))
    merged["source_spans"] = _unique_spans(documents)
    merged["options"] = _renumber_options([option for document in documents for option in document.get("options", [])])

    children = []
    children_by_key = {}
    for document in documents:
        for child in document.get("children", []):
            existing = children_by_key.get(child["key"])
            if existing is None:
                clone = copy.deepcopy(child)
                children_by_key[child["key"]] = clone
                children.append(clone)
            elif canonical_json(existing) != canonical_json(child):
                raise ValueError("Merged candidates contain conflicting stable subquestion IDs")
    merged["children"] = children

    answer_parts = []
    analysis_parts = []
    for document in documents:
        label = "来源题号 %s" % document.get("number", "")
        if document.get("answer_md"):
            answer_parts.append("**%s 答案**\n\n%s" % (label, document["answer_md"]))
        if document.get("analysis_md"):
            analysis_parts.append("**%s 解析**\n\n%s" % (label, document["analysis_md"]))
    merged["answer_md"] = "\n\n---\n\n".join(answer_parts)
    merged["analysis_md"] = "\n\n---\n\n".join(analysis_parts)
    if merged["answer_md"] or merged["analysis_md"]:
        merged["answer_state"] = "needs_review"
    elif all(document.get("answer_state") == "missing" for document in documents):
        merged["answer_state"] = "missing"
    else:
        merged["answer_state"] = "needs_review"

    kinds = {document.get("kind") for document in documents}
    if merged["options"]:
        merged["kind"] = "multiple_choice" if any(document.get("kind") == "multiple_choice" for document in documents) else "single_choice"
    elif merged["children"]:
        merged["kind"] = "structured"
    elif len(kinds) == 1:
        merged["kind"] = next(iter(kinds))
    else:
        merged["kind"] = "short_answer"

    inherited = []
    seen_issues = set()
    for document in documents:
        for issue in document.get("issues", []):
            fingerprint = canonical_json(issue)
            if fingerprint not in seen_issues:
                seen_issues.add(fingerprint)
                inherited.append(copy.deepcopy(issue))
    merged["issues"] = inherited
    _append_issue(merged, {
        "code": "candidate_merge_requires_review",
        "severity": "blocking",
        "field": "stem_md",
        "message": "合并候选保留了全部来源题干、选项、小问和答案；请核对题目边界、选项、题号及答案后再发布。",
        "source_item_ids": source_ids,
    })
    for child in merged["children"]:
        if child.get("answer_md") or child.get("analysis_md"):
            child["answer_state"] = "needs_review"
    merged["asset_refs"] = _asset_refs(merged)
    return merged


def _new_candidate_values(template, document, item_id, item_index, actor, now):
    spans = document.get("source_spans", [])
    page = next((span.get("source_locator", {}).get("page") for span in spans if span.get("source_locator", {}).get("page")), None)
    options = {option["key"]: option["markdown"] for option in document.get("options", [])}
    values = dict(template)
    values.update({
        "id": item_id,
        "item_index": item_index,
        "page_number": page,
        "question_number": document["number"],
        "stem": document["stem_md"],
        "question_type": document["kind"],
        "options_json": json.dumps(options, ensure_ascii=False),
        "answer_json": json.dumps({"markdown": document["answer_md"], "state": document["answer_state"], "grading_rule": document.get("grading_rule")}, ensure_ascii=False),
        "analysis": document["analysis_md"],
        "media_json": json.dumps(document["asset_refs"], ensure_ascii=False),
        "coordinates_json": json.dumps(spans, ensure_ascii=False, sort_keys=True),
        "saved_question_id": None,
        "review_status": "needs_review",
        "document_json": json.dumps(document, ensure_ascii=False, sort_keys=True),
        "review_revision": 1,
        "issues_json": json.dumps(document.get("issues", []), ensure_ascii=False, sort_keys=True),
        "disposition": "active",
        "updated_by": actor["id"],
        "updated_at": now,
        "created_at": now,
    })
    return values


def _insert_candidate(conn, template, document, item_index, actor, now):
    from .document_models import validate_question_document

    document = validate_question_document(document)
    item_id = "item-" + uuid.uuid4().hex
    values = _new_candidate_values(template, document, item_id, item_index, actor, now)
    columns = list(values)
    quoted = ",".join('"%s"' % name.replace('"', '""') for name in columns)
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        "insert into parsed_question_items(%s) values(%s)" % (quoted, placeholders),
        [values[name] for name in columns],
    )
    return item_id


def _reindex_after_restructure(conn, task_id, all_rows, active_order, new_rows_count):
    old_max = max((row["item_index"] for row in all_rows), default=-1)
    offset = old_max + len(all_rows) + len(active_order) + new_rows_count + 10
    conn.execute("update parsed_question_items set item_index=item_index+? where parse_task_id=?", (offset, task_id))
    # The old rows remain as superseded evidence at their displaced indices.
    # Insert replacement rows in the now-free low range, then compact active rows.
    rows = conn.execute(
        "select id,item_index from parsed_question_items where parse_task_id=? and disposition='active'",
        (task_id,),
    ).fetchall()
    current_ids = {row["id"] for row in rows}
    if current_ids != set(active_order):
        raise ValueError("Active candidate order changed during restructure")
    for index, item_id in enumerate(active_order):
        conn.execute("update parsed_question_items set item_index=? where id=?", (index, item_id))


def reorder_candidates(conn, actor, task_id, payload):
    """Reorder all active candidates in a task using an explicit compare-and-swap."""
    from .document_ingestion import (
        IngestionError,
        _audit,
        _now,
        _operation_result,
        _require_teacher,
        _task_for_actor,
        _validate_request_key,
    )

    _require_teacher(actor)
    task = _task_for_actor(conn, actor, task_id, allow_admin=False)
    if task["status"] not in ("parsed", "partially_parsed"):
        raise StateConflict("Candidates can only be reordered after conversion has completed")
    request_key = _validate_request_key(payload.get("request_key"))
    expected_order = payload.get("expected_order")
    entries = payload.get("items")
    if (
        not isinstance(expected_order, list)
        or any(not isinstance(item_id, str) or not item_id for item_id in expected_order)
        or len(expected_order) != len(set(expected_order))
        or not isinstance(entries, list)
        or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not isinstance(entry.get("expected_revision"), int)
            or isinstance(entry.get("expected_revision"), bool)
            or entry["expected_revision"] < 1
            for entry in entries
        )
    ):
        raise IngestionError("invalid_order", "A complete candidate order and its review revisions are required")
    ordered_ids = [entry["id"] for entry in entries]
    if ordered_ids == [] or len(ordered_ids) != len(set(ordered_ids)):
        raise IngestionError("invalid_order", "The candidate order must contain unique ids")
    if len(expected_order) != len(ordered_ids) or set(expected_order) != set(ordered_ids):
        raise IngestionError("invalid_order", "The old and new orders must contain the same candidates")

    request_hash = hashlib.sha256(
        canonical_json(
            {
                "task_id": task_id,
                "expected_order": expected_order,
                "items": entries,
            }
        ).encode("utf-8")
    ).hexdigest()
    conn.execute("begin immediate")
    try:
        cached = _operation_result(conn, actor, "reorder_candidates", request_key, request_hash)
        if cached is not None:
            conn.commit()
            return cached

        current_task = conn.execute(
            "select status,school_id,created_by from document_parse_tasks where id=?",
            (task_id,),
        ).fetchone()
        if (
            current_task is None
            or current_task["school_id"] != actor["school_id"]
            or current_task["created_by"] != actor["id"]
        ):
            raise IngestionError("task_unavailable", "This document task is no longer available", 404)
        if current_task["status"] not in ("parsed", "partially_parsed"):
            raise StateConflict("Candidates can only be reordered after conversion has completed")

        rows = conn.execute(
            """select item.id,item.item_index,item.review_revision,
                      pub.parsed_item_id as published_item_id
               from parsed_question_items item
               left join import_item_publications pub on pub.parsed_item_id=item.id
               where item.parse_task_id=? and item.disposition='active'
               order by item.item_index,item.id""",
            (task_id,),
        ).fetchall()
        current_order = [row["id"] for row in rows]
        if current_order != expected_order:
            raise IngestionError(
                "order_conflict",
                "The candidate order changed in another session; reload before moving questions",
                409,
                {"current_order": current_order},
            )
        if set(current_order) != set(ordered_ids):
            raise IngestionError("invalid_order", "Every active candidate must appear exactly once")
        if any(row["published_item_id"] for row in rows):
            raise StateConflict("Published candidates cannot be reordered")
        revisions = {row["id"]: row["review_revision"] for row in rows}
        for entry in entries:
            if revisions.get(entry["id"]) != entry["expected_revision"]:
                raise IngestionError(
                    "revision_conflict",
                    "A candidate changed in another session; reload before moving questions",
                    409,
                )

        maximum = max((row["item_index"] for row in conn.execute(
            "select item_index from parsed_question_items where parse_task_id=?", (task_id,)
        )), default=-1)
        offset = maximum + len(rows) + 2
        # Move every row above the current range first so the legacy unique index
        # on (parse_task_id,item_index) cannot observe a transient collision.
        conn.execute(
            "update parsed_question_items set item_index=item_index+? where parse_task_id=?",
            (offset, task_id),
        )
        for new_index, item_id in enumerate(ordered_ids):
            conn.execute(
                "update parsed_question_items set item_index=? where id=? and parse_task_id=? and disposition='active'",
                (new_index, item_id, task_id),
            )

        result = {"task_id": task_id, "ordered_ids": ordered_ids}
        conn.execute(
            "insert into content_operation_keys(school_id,actor_id,operation,request_key,request_hash,result_json) values(?,?,?,?,?,?)",
            (
                actor["school_id"],
                actor["id"],
                "reorder_candidates",
                request_key,
                request_hash,
                json.dumps(result, ensure_ascii=False),
            ),
        )
        _audit(
            conn,
            actor,
            "document_candidates_reordered",
            "document_parse_task",
            task_id,
            {"old_order": expected_order, "new_order": ordered_ids},
        )
        conn.commit()
        return result
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def assign_source_spans(conn, actor, task_id, payload):
    """Atomically reassign source blocks among draft candidates without editing prose."""
    from .document_ingestion import (
        IngestionError,
        _audit,
        _now,
        _operation_result,
        _require_teacher,
        _task_for_actor,
        _validate_request_key,
    )

    _require_teacher(actor)
    _task_for_actor(conn, actor, task_id, allow_admin=False)
    request_key = _validate_request_key(payload.get("request_key"))
    entries = payload.get("items")
    if not isinstance(entries, list) or not entries:
        raise IngestionError("invalid_source_mapping", "At least one changed candidate is required")
    seen_ids = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not entry["id"]
            or entry["id"] in seen_ids
            or not isinstance(entry.get("expected_revision"), int)
            or isinstance(entry.get("expected_revision"), bool)
            or entry["expected_revision"] < 1
            or not isinstance(entry.get("source_spans"), list)
        ):
            raise IngestionError("invalid_source_mapping", "Candidate ids, revisions, and source block lists are required")
        seen_ids.add(entry["id"])

    request_hash = hashlib.sha256(
        canonical_json({"task_id": task_id, "items": entries}).encode("utf-8")
    ).hexdigest()
    conn.execute("begin immediate")
    try:
        cached = _operation_result(conn, actor, "assign_source_spans", request_key, request_hash)
        if cached is not None:
            conn.commit()
            return cached

        task = conn.execute(
            "select status,school_id,created_by from document_parse_tasks where id=?",
            (task_id,),
        ).fetchone()
        if (
            task is None
            or task["school_id"] != actor["school_id"]
            or task["created_by"] != actor["id"]
        ):
            raise IngestionError("task_unavailable", "This document task is no longer available", 404)
        if task["status"] not in ("parsed", "partially_parsed"):
            raise StateConflict("Source blocks can only be assigned after conversion has completed")

        rows = conn.execute(
            """select item.id,item.document_json,item.review_revision,
                      pub.parsed_item_id as published_item_id
               from parsed_question_items item
               left join import_item_publications pub on pub.parsed_item_id=item.id
               where item.parse_task_id=? and item.disposition='active'
               order by item.item_index,item.id""",
            (task_id,),
        ).fetchall()
        rows_by_id = {row["id"]: row for row in rows}
        old_documents = {
            row["id"]: json.loads(row["document_json"] or "{}") for row in rows
        }
        known_spans = {}
        original_counts = Counter()
        for document in old_documents.values():
            for span in document.get("source_spans", []):
                fingerprint = canonical_json(span)
                known_spans.setdefault(fingerprint, span)
                original_counts[fingerprint] += 1

        replacements = {}
        for entry in entries:
            row = rows_by_id.get(entry["id"])
            if row is None:
                raise IngestionError("candidate_unavailable", "A selected candidate is no longer available", 404)
            if row["published_item_id"]:
                raise StateConflict("Source blocks cannot be reassigned from or to a published candidate")
            if row["review_revision"] != entry["expected_revision"]:
                raise IngestionError(
                    "revision_conflict",
                    "A candidate changed in another session; reload before changing source blocks",
                    409,
                )
            spans = entry["source_spans"]
            if any(
                not isinstance(span, dict)
                or not isinstance(span.get("block_id"), str)
                or canonical_json(span) not in known_spans
                for span in spans
            ):
                raise IngestionError(
                    "invalid_source_mapping",
                    "A source block is not part of this conversion; reload the review page",
                    422,
                )
            replacements[entry["id"]] = spans

        new_counts = Counter()
        for item_id, document in old_documents.items():
            spans = replacements.get(item_id, document.get("source_spans", []))
            new_counts.update(canonical_json(span) for span in spans)
        if new_counts != original_counts:
            raise IngestionError(
                "incomplete_source_mapping",
                "Every source block must remain assigned exactly as many times as before",
                422,
            )

        updated = []
        now = _now()
        for entry in entries:
            item_id = entry["id"]
            document = old_documents[item_id]
            spans = replacements[item_id]
            document["source_spans"] = spans
            page = next(
                (
                    span.get("source_locator", {}).get("page")
                    for span in spans
                    if span.get("source_locator", {}).get("page")
                ),
                None,
            )
            changed = conn.execute(
                """update parsed_question_items
                   set document_json=?,coordinates_json=?,page_number=?,review_revision=review_revision+1,
                       review_status='needs_review',updated_by=?,updated_at=?
                   where id=? and review_revision=? and disposition='active'""",
                (
                    json.dumps(document, ensure_ascii=False, sort_keys=True),
                    json.dumps(spans, ensure_ascii=False, sort_keys=True),
                    page,
                    actor["id"],
                    now,
                    item_id,
                    entry["expected_revision"],
                ),
            ).rowcount
            if not changed:
                raise IngestionError("revision_conflict", "A candidate changed in another session; reload and retry", 409)
            updated.append({"id": item_id, "review_revision": entry["expected_revision"] + 1})

        result = {"task_id": task_id, "updated": updated}
        conn.execute(
            "insert into content_operation_keys(school_id,actor_id,operation,request_key,request_hash,result_json) values(?,?,?,?,?,?)",
            (
                actor["school_id"],
                actor["id"],
                "assign_source_spans",
                request_key,
                request_hash,
                json.dumps(result, ensure_ascii=False),
            ),
        )
        _audit(
            conn,
            actor,
            "document_source_spans_reassigned",
            "document_parse_task",
            task_id,
            {"candidate_count": len(updated), "source_span_count": sum(len(value) for value in replacements.values())},
        )
        conn.commit()
        return result
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def restructure_candidates(conn, actor, task_id, payload):
    """Create replacement draft candidates and retain the superseded originals."""
    from .document_ingestion import (
        IngestionError,
        _audit,
        _now,
        _operation_result,
        _require_teacher,
        _task_for_actor,
        _validate_request_key,
    )
    from .document_models import validate_question_document

    _require_teacher(actor)
    _task_for_actor(conn, actor, task_id, allow_admin=False)
    action = payload.get("action")
    ids = payload.get("ids")
    expected = payload.get("expected_revisions")
    request_key = _validate_request_key(payload.get("request_key"))
    if action not in ("split", "merge"):
        raise IngestionError("invalid_restructure", "Action must be split or merge")
    if (
        not isinstance(ids, list)
        or len(ids) < (1 if action == "split" else 2)
        or (action == "split" and len(ids) != 1)
        or any(not isinstance(item_id, str) or not item_id for item_id in ids)
        or len(ids) != len(set(ids))
        or not isinstance(expected, dict)
        or set(expected) != set(ids)
        or any(not isinstance(expected[item_id], int) or isinstance(expected[item_id], bool) or expected[item_id] < 1 for item_id in ids)
    ):
        raise IngestionError("invalid_restructure", "Candidate ids and their current review revisions are required")
    anchor = payload.get("split_anchor")
    if action == "split" and (
        not isinstance(anchor, dict)
        or not isinstance(anchor.get("block_id"), str)
        or not isinstance(anchor.get("offset"), int)
        or isinstance(anchor.get("offset"), bool)
        or anchor["offset"] < 0
    ):
        raise IngestionError("invalid_split_anchor", "A source block and character offset in the saved question stem are required")

    request_hash = hashlib.sha256(canonical_json({
        "task_id": task_id,
        "action": action,
        "ids": ids,
        "expected_revisions": expected,
        "split_anchor": anchor if action == "split" else None,
    }).encode("utf-8")).hexdigest()
    conn.execute("begin immediate")
    try:
        cached = _operation_result(conn, actor, "restructure_candidates", request_key, request_hash)
        if cached is not None:
            conn.commit()
            return cached

        task = conn.execute(
            "select status,school_id,created_by,conversion_id,import_batch_id from document_parse_tasks where id=?",
            (task_id,),
        ).fetchone()
        if task is None or task["school_id"] != actor["school_id"] or task["created_by"] != actor["id"]:
            raise IngestionError("task_unavailable", "This document task is no longer available", 404)
        if task["status"] not in ("parsed", "partially_parsed"):
            raise StateConflict("Candidates can only be restructured after conversion has completed")

        all_rows = conn.execute(
            "select * from parsed_question_items where parse_task_id=? order by item_index,id", (task_id,)
        ).fetchall()
        active_rows = [row for row in all_rows if row["disposition"] == "active"]
        active_publications = conn.execute(
            "select count(*) from import_item_publications publication join parsed_question_items item on item.id=publication.parsed_item_id where item.parse_task_id=? and item.disposition='active'",
            (task_id,),
        ).fetchone()[0]
        if active_publications:
            raise StateConflict("A task with published candidates cannot be split or merged")
        by_id = {row["id"]: row for row in active_rows}
        if any(item_id not in by_id for item_id in ids):
            raise IngestionError("candidate_unavailable", "A selected candidate is no longer active", 404)
        positions = [next(index for index, row in enumerate(active_rows) if row["id"] == item_id) for item_id in ids]
        if action == "merge" and positions != list(range(positions[0], positions[0] + len(positions))):
            raise IngestionError("merge_order_required", "Only adjacent candidates in the requested reading order can be merged", 422)

        source_rows = [by_id[item_id] for item_id in ids]
        for row in source_rows:
            if row["review_revision"] != expected[row["id"]]:
                raise IngestionError("revision_conflict", "A candidate changed in another session; reload before restructuring", 409)
            if conn.execute("select 1 from import_item_publications where parsed_item_id=?", (row["id"],)).fetchone():
                raise StateConflict("Published candidates cannot be split or merged")

        known_assets = {row[0] for row in conn.execute(
            "select asset_id from conversion_asset_refs where conversion_id=?", (task["conversion_id"],)
        )}
        documents = [validate_question_document(json.loads(row["document_json"] or "{}"), known_asset_ids=known_assets) for row in source_rows]
        if action == "split":
            spans = documents[0].get("source_spans", [])
            if not any(span.get("block_id") == anchor["block_id"] for span in spans):
                raise IngestionError("invalid_split_anchor", "The selected source block is not assigned to this candidate", 422)
            try:
                new_documents = _split_documents(documents[0], anchor["offset"], ids[0])
                new_documents = [validate_question_document(document, known_asset_ids=known_assets) for document in new_documents]
            except ValueError as exc:
                raise IngestionError("invalid_split_anchor", str(exc), 422) from exc
        else:
            try:
                merged = _merge_documents(documents, ids)
                new_documents = [validate_question_document(merged, known_asset_ids=known_assets)]
            except ValueError as exc:
                raise IngestionError("merge_conflict", str(exc), 422) from exc

        old_order = [row["id"] for row in active_rows]
        now = _now()
        for row in source_rows:
            changed = conn.execute(
                "update parsed_question_items set disposition='superseded',review_revision=review_revision+1,updated_by=?,updated_at=? where id=? and review_revision=? and disposition='active'",
                (actor["id"], now, row["id"], expected[row["id"]]),
            ).rowcount
            if changed != 1:
                raise IngestionError("revision_conflict", "A candidate changed in another session; reload before restructuring", 409)

        max_index = max((row["item_index"] for row in all_rows), default=-1)
        new_ids = []
        for offset, document in enumerate(new_documents, start=1):
            new_ids.append(_insert_candidate(conn, source_rows[0], document, max_index + offset, actor, now))

        first_position = positions[0]
        old_id_set = set(ids)
        remaining_before = [item_id for item_id in old_order[:first_position] if item_id not in old_id_set]
        remaining_after = [item_id for item_id in old_order[first_position:] if item_id not in old_id_set]
        active_order = remaining_before + new_ids + remaining_after
        _reindex_after_restructure(conn, task_id, all_rows, active_order, len(new_ids))

        conn.execute(
            "update question_import_batches set item_count=(select count(*) from parsed_question_items where import_batch_id=? and disposition='active') where id=?",
            (task["import_batch_id"], task["import_batch_id"]),
        )
        mapping = {old_id: new_ids for old_id in ids}
        old_hashes = {row["id"]: hashlib.sha256(canonical_json(documents[index]).encode("utf-8")).hexdigest() for index, row in enumerate(source_rows)}
        new_hashes = {item_id: hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest() for item_id, document in zip(new_ids, new_documents)}
        result = {"task_id": task_id, "action": action, "old_ids": ids, "new_ids": new_ids, "active_order": active_order}
        conn.execute(
            "insert into content_operation_keys(school_id,actor_id,operation,request_key,request_hash,result_json) values(?,?,?,?,?,?)",
            (actor["school_id"], actor["id"], "restructure_candidates", request_key, request_hash, json.dumps(result, ensure_ascii=False)),
        )
        _audit(
            conn,
            actor,
            "document_candidates_restructured",
            "document_parse_task",
            task_id,
            {"action": action, "mapping": mapping, "old_document_sha256": old_hashes, "new_document_sha256": new_hashes, "request_key": request_key},
        )
        conn.commit()
        return result
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
