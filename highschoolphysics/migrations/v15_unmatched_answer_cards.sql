create table unmatched_answer_cards (
    id text primary key,
    school_id text not null references schools(id),
    assessment_id text not null references assessment_sessions(id),
    created_by text not null references users(id),
    request_key text not null,
    payload_hash text not null,
    class_name text not null,
    source_file text not null,
    front_page integer not null,
    back_page integer not null,
    detected_name text not null default '',
    identity_note text not null default '',
    records_json text not null,
    front_image blob not null,
    front_mime text not null default 'image/jpeg',
    back_image blob not null,
    back_mime text not null default 'image/jpeg',
    status text not null default 'awaiting_student'
        check(status in ('awaiting_student','assigned')),
    assigned_student_id text references users(id),
    import_batch_id text references response_import_batches(id),
    assigned_by text references users(id),
    created_at text not null,
    assigned_at text,
    unique(school_id,assessment_id,request_key)
);
create index unmatched_answer_cards_queue
    on unmatched_answer_cards(assessment_id,status,created_at);
