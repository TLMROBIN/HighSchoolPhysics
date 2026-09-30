-- HighSchoolPhysics document-ingestion feature schema v12 on core schema v11.
-- Keep PRAGMA user_version at 11 so the existing v11 application can start after
-- an application rollback without changing the core schema marker. The feature
-- version is recorded in app_schema_migrations after all checks pass.

CREATE TABLE IF NOT EXISTS app_schema_migrations (
    feature TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK(version > 0),
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_files (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    original_paper_id TEXT NOT NULL REFERENCES original_papers(id),
    role TEXT NOT NULL CHECK(role IN ('paper','answers','rubric','rendered_source')),
    parent_file_id TEXT REFERENCES document_files(id),
    original_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size > 0),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    storage_key TEXT NOT NULL,
    page_count INTEGER CHECK(page_count > 0),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_document_files_school_hash ON document_files(school_id,sha256);

CREATE TABLE IF NOT EXISTS document_uploads (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    actor_id TEXT NOT NULL REFERENCES users(id),
    request_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    original_name TEXT NOT NULL,
    declared_size INTEGER NOT NULL CHECK(declared_size > 0),
    declared_sha256 TEXT NOT NULL CHECK(length(declared_sha256) = 64),
    total_parts INTEGER NOT NULL CHECK(total_parts > 0),
    state TEXT NOT NULL CHECK(state IN ('uploading','assembling','completed','cancelled','expired')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    document_file_id TEXT REFERENCES document_files(id),
    task_id TEXT REFERENCES document_parse_tasks(id),
    assembly_token TEXT,
    assembly_lease_until TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(school_id,actor_id,request_key)
);
CREATE TABLE IF NOT EXISTS document_upload_parts (
    upload_id TEXT NOT NULL REFERENCES document_uploads(id),
    part_index INTEGER NOT NULL CHECK(part_index >= 0),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK(byte_size > 0),
    storage_key TEXT NOT NULL,
    PRIMARY KEY(upload_id,part_index)
);

CREATE TABLE IF NOT EXISTS document_conversions (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    task_id TEXT NOT NULL REFERENCES document_parse_tasks(id),
    document_file_id TEXT NOT NULL REFERENCES document_files(id),
    generation INTEGER NOT NULL CHECK(generation > 0),
    adapter_name TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    markdown_key TEXT NOT NULL,
    layout_key TEXT NOT NULL,
    manifest_key TEXT NOT NULL,
    output_sha256 TEXT NOT NULL CHECK(length(output_sha256) = 64),
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id,generation)
);
CREATE TABLE IF NOT EXISTS document_assets (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size > 0),
    width_px INTEGER NOT NULL CHECK(width_px > 0),
    height_px INTEGER NOT NULL CHECK(height_px > 0),
    storage_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(school_id,sha256,mime_type)
);
CREATE TABLE IF NOT EXISTS conversion_asset_refs (
    conversion_id TEXT NOT NULL REFERENCES document_conversions(id),
    asset_id TEXT NOT NULL REFERENCES document_assets(id),
    source_locator_json TEXT NOT NULL,
    asset_role TEXT NOT NULL CHECK(asset_role IN ('figure','formula_evidence','source_page','option_figure')),
    locator_key TEXT NOT NULL,
    PRIMARY KEY(conversion_id,asset_id,locator_key)
);

-- content_groups 一行代表完整大题，不代表每一个填空作答项。
CREATE TABLE IF NOT EXISTS question_content_groups (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    original_paper_id TEXT REFERENCES original_papers(id),
    source_item_id TEXT UNIQUE REFERENCES parsed_question_items(id),
    current_revision_id TEXT REFERENCES question_content_revisions(id),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS question_content_revisions (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES question_content_groups(id),
    revision_no INTEGER NOT NULL CHECK(revision_no > 0),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    document_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    review_state TEXT NOT NULL CHECK(review_state IN ('draft','verified')),
    answer_state TEXT NOT NULL CHECK(answer_state IN ('missing','needs_review','verified')),
    created_by TEXT NOT NULL REFERENCES users(id),
    change_reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_id,revision_no)
);
CREATE TABLE IF NOT EXISTS content_asset_refs (
    revision_id TEXT NOT NULL REFERENCES question_content_revisions(id),
    asset_id TEXT NOT NULL REFERENCES document_assets(id),
    field_path TEXT NOT NULL,
    PRIMARY KEY(revision_id,asset_id,field_path)
);
CREATE TABLE IF NOT EXISTS question_content_bindings (
    question_id TEXT PRIMARY KEY REFERENCES questions(id),
    group_id TEXT NOT NULL REFERENCES question_content_groups(id),
    child_key TEXT NOT NULL DEFAULT '',
    UNIQUE(group_id,child_key)
);
CREATE TABLE IF NOT EXISTS snapshot_content_bindings (
    snapshot_id TEXT PRIMARY KEY REFERENCES question_version_snapshots(id),
    revision_id TEXT NOT NULL REFERENCES question_content_revisions(id),
    child_key TEXT NOT NULL DEFAULT ''
);

-- 同一个原题/作答项的旧 questions ID 可能不止一个；历史关联不强制唯一。
CREATE TABLE IF NOT EXISTS historical_content_corrections (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    snapshot_id TEXT NOT NULL REFERENCES question_version_snapshots(id),
    revision_id TEXT NOT NULL REFERENCES question_content_revisions(id),
    child_key TEXT NOT NULL DEFAULT '',
    migration_key TEXT NOT NULL,
    mapping_sha256 TEXT NOT NULL CHECK(length(mapping_sha256) = 64),
    reason TEXT NOT NULL,
    reviewed_by TEXT NOT NULL REFERENCES users(id),
    state TEXT NOT NULL CHECK(state IN ('active','revoked')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT,
    UNIQUE(migration_key,snapshot_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_snapshot_correction
    ON historical_content_corrections(snapshot_id) WHERE state = 'active';

CREATE TABLE IF NOT EXISTS import_item_publications (
    parsed_item_id TEXT PRIMARY KEY REFERENCES parsed_question_items(id),
    group_id TEXT NOT NULL REFERENCES question_content_groups(id),
    revision_id TEXT NOT NULL REFERENCES question_content_revisions(id),
    published_review_revision INTEGER NOT NULL,
    question_ids_json TEXT NOT NULL,
    published_by TEXT NOT NULL REFERENCES users(id),
    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS content_operation_keys (
    school_id TEXT NOT NULL REFERENCES schools(id),
    actor_id TEXT NOT NULL REFERENCES users(id),
    operation TEXT NOT NULL,
    request_key TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(school_id,actor_id,operation,request_key)
);
