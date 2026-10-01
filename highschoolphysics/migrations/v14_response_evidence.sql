create table response_import_batches (
    id text primary key, school_id text not null references schools(id),
    assessment_id text not null references assessment_sessions(id),
    created_by text not null references users(id), batch_key text not null,
    payload_hash text not null, context_hash text not null,
    source_type text not null, source_name text not null, source_reason text not null,
    status text not null, records_json text not null, created_at text not null,
    unique(school_id, assessment_id, created_by, batch_key)
);
create table response_evidence (
    id text primary key, response_id text not null references student_responses(id),
    batch_id text references response_import_batches(id), raw_answer text,
    normalized_answer text, source_row integer, source_asset_id text references exam_assets(id),
    page_number integer, bbox_json text, extraction_method text not null,
    extractor_version text, extraction_confidence real, created_at text not null
);
create table response_decisions (
    id text primary key, response_id text not null references student_responses(id),
    evidence_id text references response_evidence(id), outcome text not null,
    answer text not null, proposed_outcome text not null, supplied_outcome text,
    method text not null, rule_version text not null, answer_version text not null,
    reason_code text not null, reason text not null, created_by text references users(id),
    created_at text not null, supersedes_id text references response_decisions(id),
    request_key text, unique(response_id, request_key)
);
create table response_review_items (
    id text primary key, response_id text not null references student_responses(id),
    decision_id text not null references response_decisions(id), category text not null,
    status text not null, resolved_by text references users(id), resolved_at text,
    resolution_note text not null default ''
);
create table response_publications (
    id text primary key, assessment_id text not null references assessment_sessions(id),
    version integer not null, kind text not null, decisions_json text not null,
    created_by text references users(id), created_at text not null,
    unique(assessment_id, version)
);
create index response_decisions_history on response_decisions(response_id, created_at);
create index response_review_queue on response_review_items(response_id, status);
create trigger evidence_no_update before update on response_evidence
begin select raise(abort,'Response evidence is immutable'); end;
create trigger evidence_no_delete before delete on response_evidence
begin select raise(abort,'Response evidence is immutable'); end;
create trigger decisions_no_update before update on response_decisions
begin select raise(abort,'Response decisions are immutable'); end;
create trigger decisions_no_delete before delete on response_decisions
begin select raise(abort,'Response decisions are immutable'); end;
