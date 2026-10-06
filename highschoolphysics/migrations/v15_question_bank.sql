-- Reusable imported papers, optimistic tag editing, and scoped Agent credentials.
CREATE TABLE IF NOT EXISTS question_bank_imports (
    batch_id TEXT PRIMARY KEY REFERENCES question_import_batches(id) ON DELETE CASCADE,
    import_mode TEXT NOT NULL CHECK(import_mode IN ('paper','questions')),
    paper_id TEXT REFERENCES papers(id),
    title TEXT NOT NULL,
    tagging_policy TEXT NOT NULL DEFAULT 'automatic' CHECK(tagging_policy IN ('automatic','question_bank'))
);
CREATE TABLE IF NOT EXISTS question_tag_revisions (
    question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS question_bank_agent_tokens (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    actor_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
