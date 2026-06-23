import copy
import hashlib
import json
from pathlib import Path
import uuid


DATA_DIR = Path(__file__).with_name("data")
DEFAULT_TAXONOMY_VERSION = "pep-2019-physics-v1"
DEFAULT_ONTOLOGY_ID = "onto-pep2019-v1"

LEGACY_KNOWLEDGE_REPLACEMENTS = {
    "kn-mechanics": "kn-pep2019-r1",
    "kn-kinematics": "kn-pep2019-r1-c02",
    "kn-newton": "kn-pep2019-r1-c04",
    "kn-newton-2": "kn-pep2019-r1-c04-s03",
    "kn-work": "kn-pep2019-r2-c08",
}

LEGACY_ABILITY_REPLACEMENTS = {
    "ab-modeling": "ab-context-modeling",
    "ab-force": "ab-force-analysis",
    "ab-equation": "ab-equation-building",
    "ab-calc": "ab-calculation",
}

MANIFEST_FILES = {
    "sources": "taxonomy_sources.json",
    "knowledge": "pep2019_knowledge.json",
    "abilities": "physics_abilities.json",
    "literacy": "physics_literacies.json",
}

EXPECTED_COUNTS = {
    "knowledge": 158,
    "abilities": 15,
    "literacy": 18,
}


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_default_taxonomy(data_dir=DATA_DIR):
    bundle = {
        name: _read_json(Path(data_dir) / filename)
        for name, filename in MANIFEST_FILES.items()
    }
    return copy.deepcopy(bundle)


def _validate_no_parent_cycle(records_by_id, start_id):
    seen = set()
    current_id = start_id
    while current_id:
        if current_id in seen:
            raise ValueError("parent cycle detected at %s" % current_id)
        seen.add(current_id)
        current = records_by_id.get(current_id)
        current_id = current.get("parent_id") if current else None


def _validate_page_range(source_ref, page_count):
    start = source_ref.get("page_start")
    end = source_ref.get("page_end")
    if start is None and end is None:
        return
    if not isinstance(start, int) or not isinstance(end, int):
        raise ValueError("source page range must contain integers")
    if start < 1 or end < start:
        raise ValueError("source page range is invalid")
    if page_count is not None and end > page_count:
        raise ValueError("source page range exceeds source page count")


def validate_taxonomy_bundle(bundle):
    source_manifest = bundle["sources"]
    if source_manifest["manifest_version"] != 1:
        raise ValueError("sources manifest_version must be 1")
    if source_manifest["ontology_label"] != DEFAULT_TAXONOMY_VERSION:
        raise ValueError("sources ontology_label mismatch")

    for name, expected in EXPECTED_COUNTS.items():
        manifest = bundle[name]
        if manifest["manifest_version"] != 1:
            raise ValueError("%s manifest_version must be 1" % name)
        if manifest["ontology_label"] != DEFAULT_TAXONOMY_VERSION:
            raise ValueError("%s ontology_label mismatch" % name)

    source_records = source_manifest["records"]
    source_keys = [item["source_key"] for item in source_records]
    if len(source_keys) != len(set(source_keys)):
        raise ValueError("sources contains duplicate source_key")
    source_page_counts = {
        item["source_key"]: item.get("page_count")
        for item in source_records
    }

    global_ids = []
    global_codes = []
    for name in ("knowledge", "abilities", "literacy"):
        records = bundle[name]["records"]
        keys = [item["default_key"] for item in records]
        ids = [item["id"] for item in records]
        codes = [item["stable_code"] for item in records]
        global_ids.extend(ids)
        global_codes.extend(codes)
        if len(keys) != len(set(keys)):
            raise ValueError("%s contains duplicate default_key" % name)
        if len(ids) != len(set(ids)):
            raise ValueError("%s contains duplicate id" % name)
        if len(codes) != len(set(codes)):
            raise ValueError("%s contains duplicate stable_code" % name)

        records_by_id = {item["id"]: item for item in records}
        for item in records:
            _validate_no_parent_cycle(records_by_id, item["id"])
            parent_id = item.get("parent_id")
            if parent_id:
                parent = records_by_id.get(parent_id)
                if parent is None:
                    raise ValueError(
                        "%s parent does not exist: %s" % (name, parent_id)
                    )
                if parent["level"] + 1 != item["level"]:
                    raise ValueError(
                        "%s parent level mismatch: %s" % (name, item["id"])
                    )
            for source_ref in item.get("source_refs", []):
                source_key = source_ref["source_key"]
                if source_key not in source_page_counts:
                    raise ValueError(
                        "%s has unknown source key: %s" % (name, source_key)
                    )
                _validate_page_range(
                    source_ref,
                    source_page_counts[source_key],
                )
        expected = EXPECTED_COUNTS[name]
        if len(records) != expected:
            raise ValueError("%s item count must be %s" % (name, expected))

    if len(global_ids) != len(set(global_ids)):
        raise ValueError("taxonomy ids must be globally unique")
    if len(global_codes) != len(set(global_codes)):
        raise ValueError("stable_code values must be globally unique")

    knowledge_manifest = bundle["knowledge"]
    topics = knowledge_manifest.get("curriculum_topics", [])
    topic_ids = {item["id"] for item in topics}
    knowledge_ids = {
        item["id"] for item in knowledge_manifest["records"]
    }
    for topic in topics:
        for source_ref in topic.get("source_refs", []):
            source_key = source_ref["source_key"]
            if source_key not in source_page_counts:
                raise ValueError("curriculum topic has unknown source key")
            _validate_page_range(
                source_ref,
                source_page_counts[source_key],
            )
    for mapping in knowledge_manifest.get("curriculum_mappings", []):
        if mapping["knowledge_node_id"] not in knowledge_ids:
            raise ValueError("curriculum mapping has unknown knowledge node")
        if mapping["curriculum_topic_id"] not in topic_ids:
            raise ValueError("curriculum mapping has unknown topic")
        if not mapping.get("source_refs"):
            raise ValueError("curriculum mapping requires source_refs")
        for source_ref in mapping["source_refs"]:
            source_key = source_ref["source_key"]
            if source_key not in source_page_counts:
                raise ValueError("curriculum mapping has unknown source key")
            _validate_page_range(
                source_ref,
                source_page_counts[source_key],
            )

    knowledge = knowledge_manifest["records"]
    level_counts = {
        level: sum(item["level"] == level for item in knowledge)
        for level in (1, 2, 3, 4)
    }
    if level_counts != {1: 6, 2: 27, 3: 125, 4: 0}:
        raise ValueError(
            "knowledge hierarchy counts mismatch: %r" % level_counts
        )

    literacy = bundle["literacy"]["records"]
    literacy_counts = {
        level: sum(item["level"] == level for item in literacy)
        for level in (1, 2)
    }
    if literacy_counts != {1: 4, 2: 14}:
        raise ValueError(
            "literacy hierarchy counts mismatch: %r" % literacy_counts
        )


def _source_id(source_key):
    return "taxonomy-source-" + source_key


def _source_link_id(
    school_id,
    entity_type,
    entity_id,
    source_id,
    source_ref,
):
    payload = "|".join(
        [
            school_id,
            entity_type,
            entity_id,
            source_id,
            str(source_ref.get("page_start") or ""),
            str(source_ref.get("page_end") or ""),
            source_ref.get("locator", ""),
        ]
    )
    return "taxonomy-link-" + hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:24]


def _record_source_links(
    conn,
    school_id,
    entity_type,
    entity_id,
    source_refs,
    source_ids,
):
    for source_ref in source_refs:
        source_id = source_ids[source_ref["source_key"]]
        conn.execute(
            """
            insert or ignore into taxonomy_source_links(
                id, school_id, entity_type, entity_id, source_id,
                page_start, page_end, locator, evidence_summary
            ) values(?,?,?,?,?,?,?,?,?)
            """,
            (
                _source_link_id(
                    school_id,
                    entity_type,
                    entity_id,
                    source_id,
                    source_ref,
                ),
                school_id,
                entity_type,
                entity_id,
                source_id,
                source_ref.get("page_start"),
                source_ref.get("page_end"),
                source_ref.get("locator", ""),
                source_ref.get("evidence_summary", ""),
            ),
        )


def _verified_local_path(source, local_source_root):
    if not local_source_root or not source.get("file_name"):
        return ""
    candidate = Path(local_source_root) / source["file_name"]
    if not candidate.is_file():
        return ""
    expected = source.get("sha256")
    if expected:
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(
                "Source hash mismatch for %s" % source["source_key"]
            )
    return str(candidate)


def _install_sources(
    conn,
    school_id,
    manifest,
    local_source_root,
):
    source_ids = {}
    for source in manifest["records"]:
        source_id = _source_id(source["source_key"])
        local_path = _verified_local_path(source, local_source_root)
        conn.execute(
            """
            insert or ignore into taxonomy_sources(
                id, school_id, source_key, source_type, title, edition,
                volume_code, file_name, local_path, sha256, page_count,
                parser_name, parser_version, verified_at, metadata_json
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                source_id,
                school_id,
                source["source_key"],
                source["source_type"],
                source["title"],
                source.get("edition", ""),
                source.get("volume_code", ""),
                source.get("file_name", ""),
                local_path or source.get("local_path", ""),
                source.get("sha256", ""),
                source.get("page_count"),
                source.get("parser_name", ""),
                source.get("parser_version", ""),
                source.get("verified_at", ""),
                json.dumps(
                    source.get("metadata", {}),
                    ensure_ascii=False,
                ),
            ),
        )
        existing = conn.execute(
            """
            select id, local_path from taxonomy_sources
            where school_id = ? and source_key = ?
            """,
            (school_id, source["source_key"]),
        ).fetchone()
        if local_path and not existing["local_path"]:
            conn.execute(
                "update taxonomy_sources set local_path = ? where id = ?",
                (local_path, existing["id"]),
            )
        source_ids[source["source_key"]] = existing["id"]
    return source_ids


def _ensure_default_ontology(conn, school_id, publish):
    existing = conn.execute(
        "select * from knowledge_ontology_versions where id = ?",
        (DEFAULT_ONTOLOGY_ID,),
    ).fetchone()
    if existing is not None and existing["school_id"] != school_id:
        raise ValueError(
            "Default ontology id belongs to another school"
        )
    if existing is None:
        conn.execute(
            """
            insert into knowledge_ontology_versions(
                id, school_id, version_label, status, source_summary
            ) values(?,?,?,?,?)
            """,
            (
                DEFAULT_ONTOLOGY_ID,
                school_id,
                "PEP 2019 高中物理默认体系 v1",
                "active" if publish else "draft",
                "人教版 2019 六册教材目录 + 2017 版 2020 修订课程标准",
            ),
        )
    if publish:
        conn.execute(
            """
            update knowledge_ontology_versions
            set status = 'archived'
            where school_id = ? and status = 'active' and id <> ?
            """,
            (school_id, DEFAULT_ONTOLOGY_ID),
        )
        conn.execute(
            """
            update knowledge_ontology_versions
            set status = 'active'
            where id = ?
            """,
            (DEFAULT_ONTOLOGY_ID,),
        )
    return DEFAULT_ONTOLOGY_ID


def _source_label(record):
    keys = []
    for source_ref in record.get("source_refs", []):
        source_key = source_ref["source_key"]
        if source_key not in keys:
            keys.append(source_key)
    return " / ".join(keys)


def _install_knowledge(
    conn,
    school_id,
    ontology_id,
    manifest,
    source_ids,
    summary,
):
    actual_ids = {}
    for record in sorted(
        manifest["records"],
        key=lambda item: (item["level"], item["stable_code"]),
    ):
        existing = conn.execute(
            """
            select * from knowledge_nodes
            where school_id = ? and default_key = ?
            """,
            (school_id, record["default_key"]),
        ).fetchone()
        if existing is None:
            parent_id = (
                actual_ids[record["parent_id"]]
                if record.get("parent_id")
                else None
            )
            conn.execute(
                """
                insert into knowledge_nodes(
                    id, school_id, ontology_version_id, parent_id,
                    stable_code, name, node_type, level, aliases,
                    description, textbook_scope, source, enabled,
                    version, change_note, default_key, is_default
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["id"],
                    school_id,
                    ontology_id,
                    parent_id,
                    record["stable_code"],
                    record["name"],
                    record["node_type"],
                    record["level"],
                    ",".join(record.get("aliases", [])),
                    record.get("description", ""),
                    record.get("textbook_scope", ""),
                    _source_label(record),
                    1,
                    1,
                    "默认体系安装",
                    record["default_key"],
                    1,
                ),
            )
            actual_id = record["id"]
            summary["knowledge"]["created"] += 1
        else:
            actual_id = existing["id"]
            summary["knowledge"]["existing"] += 1
        actual_ids[record["id"]] = actual_id
        _record_source_links(
            conn,
            school_id,
            "knowledge_node",
            actual_id,
            record.get("source_refs", []),
            source_ids,
        )
    return actual_ids


def _install_abilities(
    conn,
    school_id,
    ontology_id,
    manifest,
    source_ids,
    summary,
):
    actual_ids = {}
    for record in manifest["records"]:
        existing = conn.execute(
            """
            select * from ability_tags
            where school_id = ? and default_key = ?
            """,
            (school_id, record["default_key"]),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                insert into ability_tags(
                    id, school_id, ontology_version_id, stable_code,
                    name, description, source, enabled, version,
                    default_key, is_default, change_note
                ) values(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["id"],
                    school_id,
                    ontology_id,
                    record["stable_code"],
                    record["name"],
                    record.get("description", ""),
                    _source_label(record),
                    1,
                    1,
                    record["default_key"],
                    1,
                    "默认体系安装",
                ),
            )
            actual_id = record["id"]
            summary["abilities"]["created"] += 1
        else:
            actual_id = existing["id"]
            summary["abilities"]["existing"] += 1
        actual_ids[record["id"]] = actual_id
        _record_source_links(
            conn,
            school_id,
            "ability_tag",
            actual_id,
            record.get("source_refs", []),
            source_ids,
        )
    return actual_ids


def _install_literacy(
    conn,
    school_id,
    ontology_id,
    manifest,
    source_ids,
    summary,
):
    actual_ids = {}
    for record in sorted(
        manifest["records"],
        key=lambda item: (item["level"], item["stable_code"]),
    ):
        existing = conn.execute(
            """
            select * from literacy_tags
            where school_id = ? and default_key = ?
            """,
            (school_id, record["default_key"]),
        ).fetchone()
        if existing is None:
            parent_id = (
                actual_ids[record["parent_id"]]
                if record.get("parent_id")
                else None
            )
            conn.execute(
                """
                insert into literacy_tags(
                    id, school_id, ontology_version_id, parent_id,
                    default_key, stable_code, name, level, description,
                    source, enabled, is_default, version, change_note
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["id"],
                    school_id,
                    ontology_id,
                    parent_id,
                    record["default_key"],
                    record["stable_code"],
                    record["name"],
                    record["level"],
                    record.get("description", ""),
                    _source_label(record),
                    1,
                    1,
                    1,
                    "默认体系安装",
                ),
            )
            actual_id = record["id"]
            summary["literacy"]["created"] += 1
        else:
            actual_id = existing["id"]
            summary["literacy"]["existing"] += 1
        actual_ids[record["id"]] = actual_id
        _record_source_links(
            conn,
            school_id,
            "literacy_tag",
            actual_id,
            record.get("source_refs", []),
            source_ids,
        )
    return actual_ids


def _install_curriculum(
    conn,
    school_id,
    ontology_id,
    manifest,
    knowledge_ids,
    source_ids,
    summary,
):
    topic_ids = {}
    for topic in manifest.get("curriculum_topics", []):
        existing = conn.execute(
            """
            select * from curriculum_topics
            where school_id = ? and stable_code = ?
            """,
            (school_id, topic["stable_code"]),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                insert into curriculum_topics(
                    id, school_id, ontology_version_id, stable_code,
                    name, course_module, enabled, version
                ) values(?,?,?,?,?,?,?,?)
                """,
                (
                    topic["id"],
                    school_id,
                    ontology_id,
                    topic["stable_code"],
                    topic["name"],
                    topic["course_module"],
                    1,
                    1,
                ),
            )
            actual_id = topic["id"]
            summary["curriculum"]["topics_created"] += 1
        else:
            actual_id = existing["id"]
        topic_ids[topic["id"]] = actual_id
        _record_source_links(
            conn,
            school_id,
            "curriculum_topic",
            actual_id,
            topic.get("source_refs", []),
            source_ids,
        )

    for mapping in manifest.get("curriculum_mappings", []):
        knowledge_id = knowledge_ids[mapping["knowledge_node_id"]]
        topic_id = topic_ids[mapping["curriculum_topic_id"]]
        cursor = conn.execute(
            """
            insert or ignore into knowledge_curriculum_mappings(
                knowledge_node_id, curriculum_topic_id,
                mapping_type, rationale
            ) values(?,?,?,?)
            """,
            (
                knowledge_id,
                topic_id,
                mapping["mapping_type"],
                mapping.get("rationale", ""),
            ),
        )
        if cursor.rowcount:
            summary["curriculum"]["mappings_created"] += 1
        _record_source_links(
            conn,
            school_id,
            "knowledge_curriculum_mapping",
            mapping["id"],
            mapping.get("source_refs", []),
            source_ids,
        )


def _migrate_question_tag(
    conn,
    old_id,
    replacement_id,
    tag_type,
    ontology_id,
):
    rows = conn.execute(
        """
        select * from question_tags
        where tag_type = ? and tag_id = ? and enabled = 1
        """,
        (tag_type, old_id),
    ).fetchall()
    for row in rows:
        duplicate = conn.execute(
            """
            select id from question_tags
            where question_id = ? and tag_type = ? and tag_id = ?
              and enabled = 1 and id <> ?
            """,
            (
                row["question_id"],
                tag_type,
                replacement_id,
                row["id"],
            ),
        ).fetchone()
        if duplicate is None:
            conn.execute(
                """
                update question_tags
                set tag_id = ?, ontology_version_id = ?,
                    version = version + 1
                where id = ?
                """,
                (replacement_id, ontology_id, row["id"]),
            )
        else:
            conn.execute(
                """
                update question_tags
                set enabled = 0, version = version + 1
                where id = ?
                """,
                (row["id"],),
            )


def _apply_replacement_group(
    conn,
    school_id,
    table,
    entity_type,
    tag_type,
    replacements,
    summary,
):
    for old_id, replacement_id in replacements.items():
        old = conn.execute(
            "select * from %s where id = ? and school_id = ?" % table,
            (old_id, school_id),
        ).fetchone()
        replacement = conn.execute(
            "select * from %s where id = ? and school_id = ?" % table,
            (replacement_id, school_id),
        ).fetchone()
        if old is None or replacement is None:
            continue
        cursor = conn.execute(
            """
            insert or ignore into taxonomy_replacements(
                id, school_id, entity_type, old_entity_id,
                replacement_entity_id, reason
            ) values(?,?,?,?,?,?)
            """,
            (
                "replacement-%s-%s" % (entity_type, old_id),
                school_id,
                entity_type,
                old_id,
                replacement_id,
                "Phase 2B 默认体系替代旧演示实体",
            ),
        )
        if cursor.rowcount:
            summary["replacements"] += 1
        _migrate_question_tag(
            conn,
            old_id,
            replacement_id,
            tag_type,
            replacement["ontology_version_id"],
        )
        conn.execute(
            """
            update %s
            set enabled = 0,
                change_note = case
                    when change_note = '' then '已由 Phase 2B 默认体系替代'
                    else change_note
                end,
                version = version + case when enabled = 1 then 1 else 0 end
            where id = ?
            """
            % table,
            (old_id,),
        )


def _record_and_apply_replacements(
    conn,
    school_id,
    summary,
):
    _apply_replacement_group(
        conn,
        school_id,
        "knowledge_nodes",
        "knowledge",
        "knowledge",
        LEGACY_KNOWLEDGE_REPLACEMENTS,
        summary,
    )
    _apply_replacement_group(
        conn,
        school_id,
        "ability_tags",
        "ability",
        "ability",
        LEGACY_ABILITY_REPLACEMENTS,
        summary,
    )


def _record_install_audit(
    conn,
    school_id,
    actor_id,
    summary,
):
    conn.execute(
        """
        insert into audit_events(
            id, school_id, actor_id, action, resource_type,
            resource_id, detail_json
        ) values(?,?,?,?,?,?,?)
        """,
        (
            "audit-" + uuid.uuid4().hex,
            school_id,
            actor_id,
            "default_taxonomy_installed",
            "knowledge_ontology_version",
            DEFAULT_ONTOLOGY_ID,
            json.dumps(summary, ensure_ascii=False),
        ),
    )


def install_default_taxonomy(
    conn,
    school_id,
    actor_id,
    publish=False,
    local_source_root=None,
    bundle=None,
):
    bundle = bundle or load_default_taxonomy()
    validate_taxonomy_bundle(bundle)
    summary = {
        "version": DEFAULT_TAXONOMY_VERSION,
        "knowledge": {"created": 0, "existing": 0},
        "abilities": {"created": 0, "existing": 0},
        "literacy": {"created": 0, "existing": 0},
        "curriculum": {
            "topics_created": 0,
            "mappings_created": 0,
        },
        "replacements": 0,
    }

    conn.execute("savepoint install_default_taxonomy")
    try:
        ontology_id = _ensure_default_ontology(
            conn,
            school_id,
            publish,
        )
        source_ids = _install_sources(
            conn,
            school_id,
            bundle["sources"],
            local_source_root,
        )
        knowledge_ids = _install_knowledge(
            conn,
            school_id,
            ontology_id,
            bundle["knowledge"],
            source_ids,
            summary,
        )
        _install_abilities(
            conn,
            school_id,
            ontology_id,
            bundle["abilities"],
            source_ids,
            summary,
        )
        _install_literacy(
            conn,
            school_id,
            ontology_id,
            bundle["literacy"],
            source_ids,
            summary,
        )
        _install_curriculum(
            conn,
            school_id,
            ontology_id,
            bundle["knowledge"],
            knowledge_ids,
            source_ids,
            summary,
        )
        _record_and_apply_replacements(
            conn,
            school_id,
            summary,
        )
        _record_install_audit(
            conn,
            school_id,
            actor_id,
            summary,
        )
        conn.execute("release savepoint install_default_taxonomy")
    except Exception:
        conn.execute("rollback to savepoint install_default_taxonomy")
        conn.execute("release savepoint install_default_taxonomy")
        raise
    return summary
