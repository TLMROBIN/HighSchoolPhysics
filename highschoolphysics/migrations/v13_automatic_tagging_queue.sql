-- Durable, lease-fenced jobs for model-based question tagging.
CREATE TABLE IF NOT EXISTS question_tag_jobs (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    question_id TEXT NOT NULL REFERENCES questions(id),
    requested_by TEXT NOT NULL REFERENCES users(id),
    question_version INTEGER NOT NULL CHECK(question_version > 0),
    source TEXT NOT NULL DEFAULT 'document_import',
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_token TEXT,
    lease_until TEXT,
    candidate_id TEXT REFERENCES question_tag_candidates(id),
    result_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(question_id,question_version,source)
);
CREATE INDEX IF NOT EXISTS idx_question_tag_jobs_queue
ON question_tag_jobs(status,available_at,lease_until);
