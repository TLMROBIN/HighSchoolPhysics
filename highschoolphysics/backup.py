"""Backup export, restore, and consistency checks."""

import json

from .security import hash_password


BACKUP_TABLES = [
    "schools",
    "class_groups",
    "users",
    "teacher_classes",
    "role_assignments",
    "access_policies",
    "identity_accounts",
    "auth_provider_configs",
    "llm_provider_configs",
    "knowledge_ontology_versions",
    "mastery_inference_versions",
    "knowledge_nodes",
    "knowledge_edges",
    "ability_tags",
    "literacy_tags",
    "taxonomy_sources",
    "taxonomy_source_links",
    "curriculum_topics",
    "knowledge_curriculum_mappings",
    "taxonomy_replacements",
    "original_papers",
    "question_import_batches",
    "document_parser_configs",
    "questions",
    "question_tag_candidates",
    "question_tags",
    "papers",
    "paper_questions",
    "answer_card_templates",
    "assessment_sessions",
    "assessment_participants",
    "question_version_snapshots",
    "scan_batches",
    "student_responses",
    "wrong_questions",
    "mastery_marks",
    "knowledge_mastery_marks",
    "student_mastery_metrics",
    "document_parse_tasks",
    "parsed_question_items",
    "export_tasks",
    "privacy_consent_records",
    "grading_revisions",
    "grading_revision_items",
    "redo_attempts",
    "error_reason_tags",
    "wrong_question_error_tags",
    "export_profiles",
    "audit_events",
    "identity_audit_logs",
]


def _table_exists(conn, table):
    return (
        conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _ordered_rows(table, rows):
    if table in ("knowledge_nodes", "literacy_tags"):
        return sorted(
            rows,
            key=lambda row: (
                int(row.get("level") or 0),
                row.get("parent_id") or "",
                row.get("stable_code") or "",
                row.get("id") or "",
            ),
        )
    return rows


def export_tables(conn):
    backup = {}
    for table in BACKUP_TABLES:
        if not _table_exists(conn, table):
            backup[table] = []
            continue
        rows = [
            dict(row)
            for row in conn.execute("select * from %s" % table).fetchall()
        ]
        rows = _ordered_rows(table, rows)
        if table == "users":
            for row in rows:
                row["password_hash"] = "<redacted>"
        backup[table] = rows
    return backup


def _insert_row(conn, table, row):
    columns = list(row.keys())
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        "insert into %s(%s) values(%s)"
        % (table, ",".join(columns), placeholders),
        [row[column] for column in columns],
    )


def restore_backup(conn, backup):
    placeholder_hash = hash_password("RestoredPlaceholder123")
    source = backup.get("tables", backup)
    restored_rows = 0
    conn.execute("pragma foreign_keys = off")
    try:
        with conn:
            for table in reversed(BACKUP_TABLES):
                if _table_exists(conn, table):
                    conn.execute("delete from %s" % table)
            for table in BACKUP_TABLES:
                if not _table_exists(conn, table):
                    continue
                rows = _ordered_rows(table, [dict(row) for row in source.get(table, [])])
                for row in rows:
                    if table == "users" and row.get("password_hash") == "<redacted>":
                        row["password_hash"] = placeholder_hash
                        row["must_change_password"] = 1
                    _insert_row(conn, table, row)
                    restored_rows += 1
    finally:
        conn.execute("pragma foreign_keys = on")
    return {"restored_rows": restored_rows, "tables": len(BACKUP_TABLES)}


def consistency_check(conn):
    issues = []
    fk_issues = conn.execute("pragma foreign_key_check").fetchall()
    for issue in fk_issues:
        issues.append(
            "foreign_key:%s:%s:%s"
            % (issue["table"], issue["rowid"], issue["parent"])
        )

    required_tables = {
        "schools": "school",
        "users": "user",
        "knowledge_nodes": "knowledge_node",
        "knowledge_ontology_versions": "ontology",
    }
    for table, label in required_tables.items():
        if _table_exists(conn, table):
            count = conn.execute("select count(*) from %s" % table).fetchone()[0]
            if count <= 0:
                issues.append("missing:%s" % label)

    dangling = conn.execute(
        """
        select count(*)
        from student_responses r
        left join question_version_snapshots s on s.id = r.snapshot_id
        left join questions q on q.id = r.question_id
        left join users u on u.id = r.student_id
        where s.id is null or q.id is null or u.id is null
        """
    ).fetchone()[0]
    if dangling:
        issues.append("dangling:student_responses:%s" % dangling)

    mastery_dangling = conn.execute(
        """
        select count(*)
        from student_mastery_metrics m
        left join users u on u.id = m.student_id
        where u.id is null
        """
    ).fetchone()[0]
    if mastery_dangling:
        issues.append("dangling:student_mastery_metrics:%s" % mastery_dangling)

    snapshot_tag_errors = 0
    for row in conn.execute(
        "select id, tag_snapshot_json from question_version_snapshots"
    ).fetchall():
        try:
            json.loads(row["tag_snapshot_json"])
        except json.JSONDecodeError:
            snapshot_tag_errors += 1
    if snapshot_tag_errors:
        issues.append("invalid:snapshot_tags:%s" % snapshot_tag_errors)

    return {
        "status": "ok" if not issues else "failed",
        "issues": issues,
    }
