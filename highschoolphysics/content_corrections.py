"""Previewable, idempotent and reversible display-only snapshot corrections."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime

from .document_models import canonical_json, canonical_sha256


class CorrectionError(ValueError):
    pass


_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def _plain(value):
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "byte_size": len(value)}
    if isinstance(value, sqlite3.Row):
        return {key: _plain(value[key]) for key in value.keys()}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _hash(value):
    return canonical_sha256(_plain(value))


def _key_hash(key):
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()


def _parse_mapping(mapping):
    if isinstance(mapping, str):
        mapping = json.loads(mapping)
    if not isinstance(mapping, dict) or mapping.get("schema_version") != 1:
        raise CorrectionError("mapping schema_version must be 1")
    for name in ("migration_key", "school_id"):
        if not isinstance(mapping.get(name), str) or not mapping[name].strip():
            raise CorrectionError("mapping %s is required" % name)
    entries = mapping.get("entries")
    if not isinstance(entries, list) or not entries:
        raise CorrectionError("mapping entries must be a non-empty list")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise CorrectionError("mapping entry must be an object")
        required = (
            "old_question_id", "snapshot_ids", "expected_old_content_hash",
            "expected_old_asset_hashes", "new_revision_id", "child_key",
            "verified_original_number", "reason", "reviewed_by", "reviewed_at",
        )
        missing = [name for name in required if name not in entry]
        if missing:
            raise CorrectionError("mapping entry missing: " + ", ".join(missing))
        if not isinstance(entry["snapshot_ids"], list) or not entry["snapshot_ids"]:
            raise CorrectionError("snapshot_ids must be a non-empty list")
        if not _HASH_RE.fullmatch(str(entry["expected_old_content_hash"])):
            raise CorrectionError("expected_old_content_hash must be a SHA-256")
        if not isinstance(entry["expected_old_asset_hashes"], list) or any(
            not _HASH_RE.fullmatch(str(item)) for item in entry["expected_old_asset_hashes"]
        ):
            raise CorrectionError("expected_old_asset_hashes must contain SHA-256 values")
        for snapshot_id in entry["snapshot_ids"]:
            if not isinstance(snapshot_id, str) or not snapshot_id:
                raise CorrectionError("snapshot id must be non-empty text")
            if snapshot_id in seen:
                raise CorrectionError("snapshot appears more than once: " + snapshot_id)
            seen.add(snapshot_id)
        if not all(isinstance(entry.get(name), str) and entry[name].strip() for name in ("old_question_id", "new_revision_id", "verified_original_number", "reason", "reviewed_by", "reviewed_at")):
            raise CorrectionError("mapping entry has empty review or identity fields")
        try:
            datetime.fromisoformat(entry["reviewed_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise CorrectionError("reviewed_at must be an ISO timestamp") from exc
    return mapping


def _scope(conn, mapping):
    snapshots = {}
    assessment_ids = set()
    question_ids = set()
    for entry in mapping["entries"]:
        for snapshot_id in entry["snapshot_ids"]:
            row = conn.execute(
                """select snapshot.*,assessment.school_id,assessment.answer_card_template_id
                   from question_version_snapshots snapshot
                   join assessment_sessions assessment on assessment.id=snapshot.assessment_id
                   where snapshot.id=?""", (snapshot_id,),
            ).fetchone()
            if row is None:
                raise CorrectionError("snapshot not found: " + snapshot_id)
            if row["school_id"] != mapping["school_id"]:
                raise CorrectionError("snapshot belongs to a different school: " + snapshot_id)
            if row["question_id"] != entry["old_question_id"]:
                raise CorrectionError("snapshot question mapping mismatch: " + snapshot_id)
            snapshots[snapshot_id] = row
            assessment_ids.add(row["assessment_id"])
            question_ids.add(row["question_id"])
    return snapshots, sorted(assessment_ids), sorted(question_ids)


def _snapshot_old_content_hash(conn, snapshot):
    tags = conn.execute(
        "select tag_snapshot_json from question_version_snapshots where id=?",
        (snapshot["id"],),
    ).fetchone()[0]
    return _hash({
        "question_id": snapshot["question_id"], "stem": snapshot["stem"],
        "options_json": snapshot["options_json"], "answer_json": snapshot["answer_json"],
        "grading_rule_json": snapshot["grading_rule_json"], "tag_snapshot_json": tags,
        "question_version": snapshot["question_version"],
    })


def _old_asset_hashes(conn, question_id, school_id):
    rows = conn.execute(
        "select png from exam_assets where question_id=? and school_id=? order by id",
        (question_id, school_id),
    ).fetchall()
    return sorted(hashlib.sha256(row["png"]).hexdigest() for row in rows)


def _revision_info(conn, revision_id, school_id, child_key):
    row = conn.execute(
        """select revision.*,group_row.school_id
           from question_content_revisions revision
           join question_content_groups group_row on group_row.id=revision.group_id
           where revision.id=?""", (revision_id,),
    ).fetchone()
    if row is None or row["school_id"] != school_id:
        raise CorrectionError("target revision is missing or belongs to another school")
    if row["review_state"] != "verified":
        raise CorrectionError("target revision must be verified")
    document = json.loads(row["document_json"])
    if _hash(document) != row["content_sha256"]:
        raise CorrectionError("target revision content hash does not match its document")
    if child_key and not any(child.get("key") == child_key for child in document.get("children", [])):
        raise CorrectionError("target revision does not contain child_key " + child_key)
    assets = conn.execute(
        """select asset.id,asset.sha256 from content_asset_refs ref
           join document_assets asset on asset.id=ref.asset_id
           where ref.revision_id=? and asset.school_id=? order by asset.sha256""",
        (revision_id, school_id),
    ).fetchall()
    declared_assets = set(document.get("asset_refs", []))
    stored_assets = {asset["id"] for asset in assets}
    if declared_assets != stored_assets:
        raise CorrectionError("target revision assets are missing or cross-school")
    return {
        "revision_id": revision_id, "group_id": row["group_id"],
        "content_sha256": row["content_sha256"],
        "asset_sha256": [asset["sha256"] for asset in assets],
    }


def _rows_by_scope(conn, table, where, params, keys):
    rows = conn.execute("select * from %s where %s" % (table, where), params).fetchall()
    result = {}
    for row in rows:
        key = "|".join(str(row[column]) for column in keys)
        result[_key_hash(key)] = _hash(row)
    return result


def _protected_manifest(conn, snapshots, assessment_ids, question_ids):
    assessments = [row["id"] for row in conn.execute(
        "select id from assessment_sessions where id in (%s)" % ",".join("?" for _ in assessment_ids), assessment_ids
    )] if assessment_ids else []
    students = [row["student_id"] for row in conn.execute(
        "select distinct student_id from assessment_participants where assessment_id in (%s)" % (",".join("?" for _ in assessments) or "NULL"), assessments
    )] if assessments else []
    wrong_ids = [row["id"] for row in conn.execute(
        "select id from wrong_questions where assessment_id in (%s)" % (",".join("?" for _ in assessments) or "NULL"), assessments
    )] if assessments else []
    templates = [row["answer_card_template_id"] for row in conn.execute(
        "select distinct answer_card_template_id from assessment_sessions where id in (%s) and answer_card_template_id is not null" % (",".join("?" for _ in assessments) or "NULL"), assessments
    )] if assessments else []
    manifest = {
        "snapshots": { _key_hash(key): _hash(row) for key, row in sorted(snapshots.items()) },
        "questions": _rows_by_scope(conn, "questions", "id in (%s)" % (",".join("?" for _ in question_ids) or "NULL"), question_ids, ("id",)),
        "paper_questions": _rows_by_scope(conn, "paper_questions", "question_id in (%s)" % (",".join("?" for _ in question_ids) or "NULL"), question_ids, ("paper_id", "question_id")),
        "exam_assets": _rows_by_scope(conn, "exam_assets", "question_id in (%s)" % (",".join("?" for _ in question_ids) or "NULL"), question_ids, ("id",)),
        "snapshot_content_bindings": _rows_by_scope(conn, "snapshot_content_bindings", "snapshot_id in (%s)" % (",".join("?" for _ in snapshots) or "NULL"), list(snapshots), ("snapshot_id",)),
        "assessment_sessions": _rows_by_scope(conn, "assessment_sessions", "id in (%s)" % (",".join("?" for _ in assessments) or "NULL"), assessments, ("id",)),
        "assessment_participants": _rows_by_scope(conn, "assessment_participants", "assessment_id in (%s)" % (",".join("?" for _ in assessments) or "NULL"), assessments, ("assessment_id", "student_id")),
        "answer_card_templates": _rows_by_scope(conn, "answer_card_templates", "id in (%s)" % (",".join("?" for _ in templates) or "NULL"), templates, ("id",)),
        "student_responses": _rows_by_scope(conn, "student_responses", "assessment_id in (%s)" % (",".join("?" for _ in assessments) or "NULL"), assessments, ("id",)),
        "wrong_questions": _rows_by_scope(conn, "wrong_questions", "assessment_id in (%s)" % (",".join("?" for _ in assessments) or "NULL"), assessments, ("id",)),
        "redo_attempts": _rows_by_scope(conn, "redo_attempts", "wrong_question_id in (%s)" % (",".join("?" for _ in wrong_ids) or "NULL"), wrong_ids, ("id",)),
        "wrong_question_error_tags": _rows_by_scope(conn, "wrong_question_error_tags", "wrong_question_id in (%s)" % (",".join("?" for _ in wrong_ids) or "NULL"), wrong_ids, ("wrong_question_id", "error_reason_tag_id")),
        "question_tags": _rows_by_scope(conn, "question_tags", "question_id in (%s)" % (",".join("?" for _ in question_ids) or "NULL"), question_ids, ("id",)),
        "question_tag_candidates": _rows_by_scope(conn, "question_tag_candidates", "question_id in (%s)" % (",".join("?" for _ in question_ids) or "NULL"), question_ids, ("id",)),
        "users": _rows_by_scope(conn, "users", "id in (%s)" % (",".join("?" for _ in students) or "NULL"), students, ("id",)),
        "identity_accounts": _rows_by_scope(conn, "identity_accounts", "user_id in (%s)" % (",".join("?" for _ in students) or "NULL"), students, ("id",)),
        "external_identity_bindings": _rows_by_scope(conn, "external_identity_bindings", "local_user_id in (%s)" % (",".join("?" for _ in students) or "NULL"), students, ("id",)),
        "identity_audit_logs": _rows_by_scope(conn, "identity_audit_logs", "user_id in (%s)" % (",".join("?" for _ in students) or "NULL"), students, ("id",)),
    }
    return manifest


def build_preview(conn, raw_mapping):
    mapping = _parse_mapping(raw_mapping)
    snapshots, assessment_ids, question_ids = _scope(conn, mapping)
    errors = []
    items = []
    for entry in mapping["entries"]:
        actual_assets = _old_asset_hashes(conn, entry["old_question_id"], mapping["school_id"])
        try:
            revision = _revision_info(conn, entry["new_revision_id"], mapping["school_id"], entry["child_key"])
        except CorrectionError as exc:
            errors.append(str(exc))
            revision = {"revision_id": entry["new_revision_id"], "content_sha256": None, "asset_sha256": []}
        if sorted(entry["expected_old_asset_hashes"]) != actual_assets:
            errors.append("old asset hash mismatch for " + entry["old_question_id"])
        for snapshot_id in entry["snapshot_ids"]:
            old_hash = _snapshot_old_content_hash(conn, snapshots[snapshot_id])
            if old_hash != entry["expected_old_content_hash"]:
                errors.append("old content hash mismatch for " + snapshot_id)
            reviewer = conn.execute("select school_id,status from users where id=?", (entry["reviewed_by"],)).fetchone()
            if reviewer is None or reviewer["school_id"] != mapping["school_id"] or reviewer["status"] != "active":
                errors.append("reviewer is not an active user in the mapped school: " + entry["reviewed_by"])
            items.append({
                "snapshot_id": snapshot_id, "old_question_id": entry["old_question_id"],
                "verified_original_number": entry["verified_original_number"],
                "old_content_sha256": old_hash, "old_asset_sha256": actual_assets,
                "new_revision_id": revision["revision_id"], "new_content_sha256": revision["content_sha256"],
                "new_asset_sha256": revision["asset_sha256"], "child_key": entry["child_key"],
                "reason": entry["reason"], "reviewed_by": entry["reviewed_by"], "reviewed_at": entry["reviewed_at"],
            })
    protected = _protected_manifest(conn, snapshots, assessment_ids, question_ids)
    baseline_hash = _hash(protected)
    expected_baseline = mapping.get("baseline_manifest_sha256")
    if expected_baseline and expected_baseline != baseline_hash:
        errors.append("baseline manifest hash mismatch")
    payload = {
        "schema_version": 1, "migration_key": mapping["migration_key"],
        "school_id": mapping["school_id"], "mapping_sha256": _hash(mapping),
        "baseline_manifest_sha256": baseline_hash, "protected_records": protected,
        "items": items, "errors": sorted(set(errors)),
    }
    payload["preview_sha256"] = _hash(payload)
    return payload


def apply_preview(conn, raw_mapping, expected_preview_sha256, actor_id):
    mapping = _parse_mapping(raw_mapping)
    conn.execute("begin immediate")
    try:
        preview = build_preview(conn, mapping)
        if preview["errors"]:
            raise CorrectionError("preview has mapping errors: " + "; ".join(preview["errors"]))
        if not mapping.get("baseline_manifest_sha256") or mapping["baseline_manifest_sha256"] != preview["baseline_manifest_sha256"]:
            raise CorrectionError("mapping must pin the reviewed baseline_manifest_sha256")
        if preview["preview_sha256"] != expected_preview_sha256:
            raise CorrectionError("preview is stale or expected preview hash does not match")
        actor = conn.execute("select school_id,role,status from users where id=?", (actor_id,)).fetchone()
        if actor is None or actor["school_id"] != mapping["school_id"] or actor["role"] != "admin" or actor["status"] != "active":
            raise CorrectionError("apply actor must be an active administrator in the mapped school")
        mapping_hash = preview["mapping_sha256"]
        results = []
        created_any = False
        for entry in mapping["entries"]:
            revision_id = entry["new_revision_id"]
            for snapshot_id in entry["snapshot_ids"]:
                existing = conn.execute(
                    "select id,mapping_sha256,state from historical_content_corrections where migration_key=? and snapshot_id=?",
                    (mapping["migration_key"], snapshot_id),
                ).fetchone()
                if existing:
                    if existing["mapping_sha256"] != mapping_hash:
                        raise CorrectionError("migration_key reused with different mapping for " + snapshot_id)
                    results.append(existing["id"])
                    continue
                active = conn.execute("select id from historical_content_corrections where snapshot_id=? and state='active'", (snapshot_id,)).fetchone()
                if active:
                    raise CorrectionError("snapshot already has an active correction: " + snapshot_id)
                correction_id = "correction-" + hashlib.sha256((mapping["migration_key"] + "\0" + snapshot_id).encode()).hexdigest()[:32]
                conn.execute(
                    """insert into historical_content_corrections
                       (id,school_id,snapshot_id,revision_id,child_key,migration_key,mapping_sha256,reason,reviewed_by,state)
                       values(?,?,?,?,?,?,?,?,?,'active')""",
                    (correction_id, mapping["school_id"], snapshot_id, revision_id, entry["child_key"], mapping["migration_key"], mapping_hash, entry["reason"], entry["reviewed_by"]),
                )
                results.append(correction_id)
                created_any = True
        if created_any:
            conn.execute(
                "insert into audit_events(id,school_id,actor_id,action,resource_type,resource_id,detail_json) values(?,?,?,?,?,?,?)",
                ("audit-" + hashlib.sha256((mapping["migration_key"] + mapping_hash).encode()).hexdigest()[:32], mapping["school_id"], actor_id, "historical_content_correction_applied", "historical_content_migration", mapping["migration_key"], json.dumps({"mapping_sha256": mapping_hash, "correction_ids": results}, sort_keys=True)),
            )
        conn.commit()
        return {"migration_key": mapping["migration_key"], "mapping_sha256": mapping_hash, "correction_ids": results, "idempotent": not created_any}
    except Exception:
        conn.rollback()
        raise


def verify_baseline(conn, raw_mapping, baseline):
    mapping = _parse_mapping(raw_mapping)
    preview = build_preview(conn, mapping)
    expected = baseline.get("protected_records")
    if not isinstance(expected, dict):
        raise CorrectionError("baseline does not contain protected_records")
    errors = []
    for table, rows in expected.items():
        current = preview["protected_records"].get(table, {})
        for key_hash, row_hash in rows.items():
            if current.get(key_hash) != row_hash:
                errors.append("protected %s record changed or disappeared: %s" % (table, key_hash))
    mapping_hash = _hash(mapping)
    for entry in mapping["entries"]:
        for snapshot_id in entry["snapshot_ids"]:
            correction = conn.execute(
                "select mapping_sha256,state,revision_id from historical_content_corrections where migration_key=? and snapshot_id=?",
                (mapping["migration_key"], snapshot_id),
            ).fetchone()
            if correction is None or correction["state"] != "active":
                errors.append("active correction missing for " + snapshot_id)
            elif correction["revision_id"] != entry["new_revision_id"]:
                errors.append("correction revision differs for " + snapshot_id)
            elif correction["mapping_sha256"] != preview["mapping_sha256"]:
                errors.append("correction mapping hash differs for " + snapshot_id)
    return {"ok": not errors, "errors": errors, "baseline_manifest_sha256": baseline.get("baseline_manifest_sha256"), "current_protected_records": preview["protected_records"]}


def rollback_migration(conn, migration_key, actor_id, reason):
    if not isinstance(reason, str) or not reason.strip():
        raise CorrectionError("rollback reason is required")
    conn.execute("begin immediate")
    try:
        actor = conn.execute("select school_id,role,status from users where id=?", (actor_id,)).fetchone()
        if actor is None or actor["role"] != "admin" or actor["status"] != "active":
            raise CorrectionError("rollback actor must be an active administrator")
        rows = conn.execute(
            "select id,school_id from historical_content_corrections where migration_key=? and state='active'",
            (migration_key,),
        ).fetchall()
        if not rows:
            raise CorrectionError("no active corrections for migration_key")
        if any(row["school_id"] != actor["school_id"] for row in rows):
            raise CorrectionError("rollback actor belongs to a different school")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute("update historical_content_corrections set state='revoked',revoked_at=? where migration_key=? and state='active'", (now, migration_key))
        conn.execute(
            "insert into audit_events(id,school_id,actor_id,action,resource_type,resource_id,detail_json) values(?,?,?,?,?,?,?)",
            ("audit-" + hashlib.sha256((migration_key + actor_id + now).encode()).hexdigest()[:32], actor["school_id"], actor_id, "historical_content_correction_revoked", "historical_content_migration", migration_key, json.dumps({"reason": reason.strip(), "correction_ids": [row["id"] for row in rows]}, sort_keys=True)),
        )
        conn.commit()
        return {"migration_key": migration_key, "revoked_count": len(rows), "reason": reason.strip()}
    except Exception:
        conn.rollback()
        raise
