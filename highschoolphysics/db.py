import json
import sqlite3
from pathlib import Path
import uuid

from .security import hash_password
from .taxonomy import DEFAULT_ONTOLOGY_ID, install_default_taxonomy


DEFAULT_DB_PATH = Path("data/highschoolphysics.sqlite3")
SCHEMA_VERSION = 10


def connect(path=DEFAULT_DB_PATH):
    path = Path(path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma busy_timeout = 5000")
    if str(path) != ":memory:":
        conn.execute("pragma journal_mode = WAL")
    return conn


def _column_names(conn, table):
    return {
        row["name"]
        for row in conn.execute("pragma table_info(%s)" % table).fetchall()
    }


def _ensure_column(conn, table, definition):
    column = definition.split()[0]
    if column not in _column_names(conn, table):
        conn.execute("alter table %s add column %s" % (table, definition))


def initialize_database(conn):
    conn.executescript(
        """
        create table if not exists schools (
            id text primary key,
            name text not null,
            org_scope text not null,
            created_at text default current_timestamp
        );

        create table if not exists class_groups (
            id text primary key,
            school_id text not null references schools(id),
            name text not null,
            grade text not null,
            school_year text not null,
            status text not null default 'active'
        );

        create table if not exists users (
            id text primary key,
            school_id text not null references schools(id),
            username text not null unique,
            display_name text not null,
            role text not null,
            class_id text references class_groups(id),
            student_no text,
            enrollment_year text,
            status text not null default 'active',
            password_hash text not null,
            must_change_password integer not null default 0,
            created_at text default current_timestamp
        );

        create table if not exists teacher_classes (
            teacher_id text not null references users(id),
            class_id text not null references class_groups(id),
            subject text not null default 'physics',
            primary key(teacher_id, class_id, subject)
        );

        create table if not exists role_assignments (
            id text primary key,
            user_id text not null references users(id),
            role text not null,
            scope_type text not null,
            scope_id text not null,
            created_at text default current_timestamp
        );

        create table if not exists access_policies (
            id text primary key,
            subject_role text not null,
            resource text not null,
            operation text not null,
            scope_rule text not null,
            enabled integer not null default 1
        );

        create table if not exists auth_sessions (
            token_hash text primary key,
            user_id text not null references users(id),
            user_agent text,
            created_at text default current_timestamp,
            expires_at text,
            revoked_at text
        );

        create table if not exists identity_accounts (
            id text primary key,
            user_id text not null references users(id),
            provider text not null,
            issuer text,
            subject text,
            external_id text,
            status text not null default 'reserved',
            created_at text default current_timestamp
        );

        create table if not exists auth_provider_configs (
            id text primary key,
            school_id text not null references schools(id),
            provider_name text not null,
            issuer text,
            client_config_json text not null,
            secret_ciphertext text,
            enabled integer not null default 0,
            created_at text default current_timestamp
        );

        create table if not exists llm_provider_configs (
            id text primary key,
            school_id text not null references schools(id),
            provider_name text not null,
            model_name text not null,
            key_ciphertext text,
            key_masked text,
            enabled integer not null default 0,
            daily_call_limit integer not null default 1000,
            monthly_budget_cents integer not null default 0,
            last_test_status text,
            created_at text default current_timestamp
        );

        create table if not exists knowledge_ontology_versions (
            id text primary key,
            school_id text not null references schools(id),
            version_label text not null,
            status text not null,
            source_summary text not null,
            created_at text default current_timestamp
        );

        create table if not exists mastery_inference_versions (
            id text primary key,
            school_id text not null references schools(id),
            version_label text not null,
            method text not null,
            status text not null,
            created_at text default current_timestamp
        );

        create table if not exists knowledge_nodes (
            id text primary key,
            school_id text not null references schools(id),
            ontology_version_id text not null references knowledge_ontology_versions(id),
            parent_id text references knowledge_nodes(id),
            stable_code text not null,
            name text not null,
            node_type text not null,
            level integer not null,
            aliases text not null default '',
            description text not null default '',
            textbook_scope text not null default '',
            source text not null default '',
            enabled integer not null default 1,
            deleted_at text,
            version integer not null default 1,
            change_note text not null default ''
        );

        create table if not exists knowledge_edges (
            id text primary key,
            school_id text not null references schools(id),
            ontology_version_id text not null references knowledge_ontology_versions(id),
            source_node_id text not null references knowledge_nodes(id),
            target_node_id text not null references knowledge_nodes(id),
            relation_type text not null,
            bidirectional integer not null default 1,
            rationale text not null default '',
            enabled integer not null default 1,
            version integer not null default 1,
            deleted_at text
        );

        create table if not exists ability_tags (
            id text primary key,
            school_id text not null references schools(id),
            ontology_version_id text not null references knowledge_ontology_versions(id),
            stable_code text not null,
            name text not null,
            description text not null default '',
            source text not null default '',
            enabled integer not null default 1,
            deleted_at text,
            version integer not null default 1
        );

        create table if not exists questions (
            id text primary key,
            school_id text not null references schools(id),
            stem text not null,
            options_json text not null,
            answer_json text not null,
            analysis text not null default '',
            question_type text not null,
            source text not null,
            grade text not null,
            chapter text not null,
            difficulty text not null,
            media_json text not null default '[]',
            scenario text not null default '',
            quality_status text not null default 'draft',
            notes text not null default '',
            version integer not null default 1,
            created_at text default current_timestamp
        );

        create table if not exists question_tag_candidates (
            id text primary key,
            school_id text not null references schools(id),
            question_id text not null references questions(id),
            cache_key text not null,
            knowledge_tags_json text not null,
            ability_tags_json text not null,
            prompt_version text not null,
            model_version text not null,
            status text not null default 'pending_review',
            created_by text references users(id),
            reviewed_by text references users(id),
            review_note text not null default '',
            created_at text default current_timestamp,
            reviewed_at text,
            unique(question_id, cache_key)
        );

        create table if not exists question_tags (
            id text primary key,
            school_id text not null references schools(id),
            question_id text not null references questions(id),
            tag_type text not null,
            tag_id text not null,
            ontology_version_id text not null references knowledge_ontology_versions(id),
            source text not null,
            confirmed_by text references users(id),
            candidate_id text references question_tag_candidates(id),
            confidence real,
            rationale text not null default '',
            version integer not null default 1,
            enabled integer not null default 1,
            created_at text default current_timestamp,
            unique(question_id, tag_type, tag_id)
        );

        create table if not exists papers (
            id text primary key,
            school_id text not null references schools(id),
            title text not null,
            source text not null,
            status text not null default 'draft',
            created_at text default current_timestamp
        );

        create table if not exists paper_questions (
            paper_id text not null references papers(id),
            question_id text not null references questions(id),
            position integer not null,
            points integer not null,
            primary key(paper_id, question_id)
        );

        create table if not exists answer_card_templates (
            id text primary key,
            school_id text not null references schools(id),
            name text not null,
            template_json text not null,
            created_at text default current_timestamp
        );

        create table if not exists assessment_sessions (
            id text primary key,
            school_id text not null references schools(id),
            title text not null,
            term text not null,
            grade text not null,
            class_id text not null references class_groups(id),
            scheduled_at text not null,
            source text not null,
            full_score integer not null,
            paper_id text references papers(id),
            answer_card_template_id text references answer_card_templates(id),
            ontology_version_id text not null references knowledge_ontology_versions(id),
            mastery_inference_version_id text not null references mastery_inference_versions(id),
            status text not null default 'draft',
            grading_status text not null default 'not_started',
            statistics_status text not null default 'not_started',
            published_at text,
            archived_at text,
            created_at text default current_timestamp
        );

        create table if not exists assessment_participants (
            assessment_id text not null references assessment_sessions(id),
            student_id text not null references users(id),
            status text not null default 'present',
            primary key(assessment_id, student_id)
        );

        create table if not exists question_version_snapshots (
            id text primary key,
            assessment_id text not null references assessment_sessions(id),
            question_id text not null references questions(id),
            position integer not null,
            points integer not null,
            stem text not null,
            options_json text not null,
            answer_json text not null,
            grading_rule_json text not null,
            tag_snapshot_json text not null,
            question_version integer not null,
            ontology_version_id text not null references knowledge_ontology_versions(id)
        );

        create table if not exists scan_batches (
            id text primary key,
            school_id text not null references schools(id),
            assessment_id text not null references assessment_sessions(id),
            source_name text not null,
            recognizer text not null,
            recognizer_version text not null,
            status text not null,
            low_confidence_count integer not null default 0,
            created_at text default current_timestamp
        );

        create table if not exists student_responses (
            id text primary key,
            school_id text not null references schools(id),
            assessment_id text not null references assessment_sessions(id),
            scan_batch_id text references scan_batches(id),
            student_id text not null references users(id),
            question_id text not null references questions(id),
            snapshot_id text not null references question_version_snapshots(id),
            raw_answer text,
            final_answer text,
            original_confidence real not null default 1.0,
            review_status text not null default 'not_required',
            review_reason text not null default '',
            review_note text not null default '',
            score integer,
            max_score integer,
            grading_status text not null default 'pending',
            overridden_by text references users(id),
            override_reason text not null default '',
            created_at text default current_timestamp,
            updated_at text default current_timestamp,
            unique(assessment_id, student_id, question_id)
        );

        create table if not exists wrong_questions (
            id text primary key,
            school_id text not null references schools(id),
            assessment_id text not null references assessment_sessions(id),
            student_id text not null references users(id),
            question_id text not null references questions(id),
            response_id text not null references student_responses(id),
            wrong_answer text,
            correct_answer_json text not null,
            score integer not null,
            max_score integer not null,
            error_reason text not null default '',
            redo_status text not null default 'pending',
            created_at text default current_timestamp,
            unique(assessment_id, student_id, question_id)
        );

        create table if not exists mastery_marks (
            id text primary key,
            school_id text not null references schools(id),
            student_id text not null references users(id),
            wrong_question_id text not null references wrong_questions(id),
            level text not null,
            note text not null default '',
            source text not null,
            created_at text default current_timestamp,
            updated_at text default current_timestamp,
            unique(student_id, wrong_question_id)
        );

        create table if not exists knowledge_mastery_marks (
            id text primary key,
            school_id text not null references schools(id),
            student_id text not null references users(id),
            knowledge_node_id text not null references knowledge_nodes(id),
            level text not null,
            note text not null default '',
            source text not null,
            created_at text default current_timestamp,
            updated_at text default current_timestamp,
            unique(student_id, knowledge_node_id)
        );

        create table if not exists student_mastery_metrics (
            id text primary key,
            school_id text not null references schools(id),
            student_id text not null references users(id),
            mastery_inference_version_id text not null references mastery_inference_versions(id),
            tag_type text not null,
            tag_id text not null,
            tag_name text not null default '',
            assessment_attempts integer not null default 0,
            assessment_correct integer not null default 0,
            assessment_wrong integer not null default 0,
            assessment_blank integer not null default 0,
            redo_attempts integer not null default 0,
            redo_correct integer not null default 0,
            redo_wrong integer not null default 0,
            eligible_attempts integer not null default 0,
            correct_count integer not null default 0,
            wrong_count integer not null default 0,
            blank_count integer not null default 0,
            correct_rate real,
            mastery_state text not null,
            calculated_at text default current_timestamp,
            unique(student_id, tag_type, tag_id)
        );

        create table if not exists document_parse_tasks (
            id text primary key,
            school_id text not null references schools(id),
            file_name text not null,
            parser text not null,
            parser_version text not null,
            status text not null,
            output_json text not null default '{}',
            failure_reason text not null default '',
            created_at text default current_timestamp
        );

        create table if not exists export_tasks (
            id text primary key,
            school_id text not null references schools(id),
            assessment_id text references assessment_sessions(id),
            export_type text not null,
            status text not null,
            output_path text not null default '',
            failure_reason text not null default '',
            file_name text not null default '',
            content_type text not null default '',
            byte_size integer not null default 0,
            engine_version text not null default '',
            created_by text references users(id),
            completed_at text,
            created_at text default current_timestamp
        );

        create table if not exists generated_export_files (
            id text primary key,
            school_id text not null references schools(id),
            export_task_id text not null references export_tasks(id),
            assessment_id text references assessment_sessions(id),
            export_type text not null,
            file_name text not null,
            content_type text not null,
            byte_size integer not null default 0,
            engine_version text not null default '',
            storage_path text not null,
            created_by text references users(id),
            created_at text default current_timestamp
        );

        create table if not exists privacy_consent_records (
            id text primary key,
            school_id text not null references schools(id),
            subject_type text not null,
            subject_id text not null,
            basis text not null,
            retention_policy text not null,
            status text not null,
            created_at text default current_timestamp
        );

        create table if not exists audit_events (
            id text primary key,
            school_id text not null,
            actor_id text,
            action text not null,
            resource_type text not null,
            resource_id text,
            detail_json text not null default '{}',
            created_at text default current_timestamp
        );

        create table if not exists runtime_capability_checks (
            id text primary key,
            school_id text not null references schools(id),
            capability_id text not null,
            status text not null,
            label text not null default '',
            detail text not null default '',
            version text not null default '',
            checked_by text references users(id),
            checked_at text default current_timestamp
        );

        create table if not exists provider_configs (
            id text primary key,
            school_id text not null references schools(id),
            provider_kind text not null,
            provider_name text not null,
            model_name text not null default '',
            api_endpoint text not null default '',
            secret_ciphertext text not null default '',
            secret_masked text not null default '',
            enabled integer not null default 0,
            daily_call_limit integer not null default 1000,
            monthly_budget_cents real not null default 0,
            per_call_max_cents real not null default 0,
            input_cost_per_1k_cents real not null default 0,
            output_cost_per_1k_cents real not null default 0,
            last_test_status text not null default '',
            last_test_detail text not null default '',
            created_by text references users(id),
            created_at text default current_timestamp,
            updated_at text default current_timestamp,
            unique(school_id, provider_kind, provider_name, model_name)
        );

        create table if not exists provider_usage_events (
            id text primary key,
            school_id text not null references schools(id),
            provider_config_id text not null references provider_configs(id),
            provider_kind text not null,
            provider_name text not null,
            model_name text not null default '',
            request_type text not null,
            prompt_version text not null default '',
            input_units integer not null default 0,
            output_units integer not null default 0,
            page_count integer not null default 0,
            estimated_cost_cents real not null default 0,
            outcome text not null,
            error_category text not null default '',
            detail_json text not null default '{}',
            created_by text references users(id),
            created_at text default current_timestamp
        );

        create table if not exists provider_budget_windows (
            id text primary key,
            school_id text not null references schools(id),
            provider_config_id text not null references provider_configs(id),
            window_type text not null,
            window_start text not null,
            call_count integer not null default 0,
            cost_cents real not null default 0,
            updated_at text default current_timestamp,
            unique(provider_config_id, window_type, window_start)
        );

        create table if not exists sso_login_states (
            id text primary key,
            school_id text not null references schools(id),
            provider_config_id text not null references auth_provider_configs(id),
            state text not null unique,
            nonce text not null,
            code_verifier text not null,
            redirect_uri text not null,
            status text not null default 'pending',
            created_at text default current_timestamp,
            consumed_at text
        );

        create table if not exists external_identity_bindings (
            id text primary key,
            school_id text not null references schools(id),
            provider text not null,
            provider_config_id text references auth_provider_configs(id),
            issuer text not null default '',
            subject text not null default '',
            external_id text not null default '',
            email text not null default '',
            display_name text not null default '',
            local_user_id text references users(id),
            status text not null default 'pending',
            detail_json text not null default '{}',
            created_at text default current_timestamp,
            reviewed_at text
        );

        create table if not exists identity_audit_logs (
            id text primary key,
            school_id text not null,
            actor_id text,
            action text not null,
            user_id text,
            detail_json text not null default '{}',
            created_at text default current_timestamp
        );
        """
    )
    _ensure_column(conn, "knowledge_nodes", "default_key text")
    _ensure_column(
        conn,
        "knowledge_nodes",
        "is_default integer not null default 0",
    )
    _ensure_column(conn, "ability_tags", "default_key text")
    _ensure_column(
        conn,
        "ability_tags",
        "is_default integer not null default 0",
    )
    _ensure_column(
        conn,
        "ability_tags",
        "change_note text not null default ''",
    )
    conn.executescript(
        """
        create table if not exists taxonomy_sources (
            id text primary key,
            school_id text not null references schools(id),
            source_key text not null,
            source_type text not null,
            title text not null,
            edition text not null default '',
            volume_code text not null default '',
            file_name text not null default '',
            local_path text not null default '',
            sha256 text not null default '',
            page_count integer,
            parser_name text not null default '',
            parser_version text not null default '',
            verified_at text not null default '',
            metadata_json text not null default '{}',
            created_at text default current_timestamp,
            unique(school_id, source_key)
        );

        create table if not exists taxonomy_source_links (
            id text primary key,
            school_id text not null references schools(id),
            entity_type text not null,
            entity_id text not null,
            source_id text not null references taxonomy_sources(id),
            page_start integer,
            page_end integer,
            locator text not null default '',
            evidence_summary text not null default '',
            created_at text default current_timestamp,
            unique(
                school_id, entity_type, entity_id, source_id,
                page_start, page_end, locator
            )
        );

        create table if not exists curriculum_topics (
            id text primary key,
            school_id text not null references schools(id),
            ontology_version_id text not null
                references knowledge_ontology_versions(id),
            stable_code text not null,
            name text not null,
            course_module text not null,
            enabled integer not null default 1,
            version integer not null default 1,
            deleted_at text
        );

        create table if not exists knowledge_curriculum_mappings (
            knowledge_node_id text not null references knowledge_nodes(id),
            curriculum_topic_id text not null references curriculum_topics(id),
            mapping_type text not null,
            rationale text not null default '',
            primary key(knowledge_node_id, curriculum_topic_id)
        );

        create table if not exists taxonomy_replacements (
            id text primary key,
            school_id text not null references schools(id),
            entity_type text not null,
            old_entity_id text not null,
            replacement_entity_id text not null,
            reason text not null,
            created_at text default current_timestamp,
            unique(school_id, entity_type, old_entity_id)
        );

        create table if not exists literacy_tags (
            id text primary key,
            school_id text not null references schools(id),
            ontology_version_id text not null
                references knowledge_ontology_versions(id),
            parent_id text references literacy_tags(id),
            default_key text,
            stable_code text not null,
            name text not null,
            level integer not null,
            description text not null default '',
            source text not null default '',
            enabled integer not null default 1,
            is_default integer not null default 0,
            deleted_at text,
            version integer not null default 1,
            change_note text not null default ''
        );

        create unique index if not exists
            ux_knowledge_nodes_school_default_key
        on knowledge_nodes(school_id, default_key)
        where default_key is not null;

        create unique index if not exists
            ux_ability_tags_school_default_key
        on ability_tags(school_id, default_key)
        where default_key is not null;

        create unique index if not exists
            ux_literacy_tags_school_default_key
        on literacy_tags(school_id, default_key)
        where default_key is not null;

        create unique index if not exists
            ux_curriculum_topics_school_stable_code
        on curriculum_topics(school_id, stable_code);
        """
    )
    conn.executescript(
        """
        create table if not exists original_papers (
            id text primary key,
            school_id text not null references schools(id),
            title text not null,
            document_name text not null,
            source_school text not null default '',
            source_publisher text not null default '',
            exam_type text not null default '',
            grade text not null default '',
            term text not null default '',
            status text not null default 'active',
            created_by text references users(id),
            created_at text default current_timestamp
        );

        create table if not exists question_import_batches (
            id text primary key,
            school_id text not null references schools(id),
            original_paper_id text not null references original_papers(id),
            source_file_name text not null,
            parser_mode text not null,
            status text not null default 'queued',
            item_count integer not null default 0,
            saved_count integer not null default 0,
            failure_reason text not null default '',
            created_by text references users(id),
            created_at text default current_timestamp
        );

        create table if not exists parsed_question_items (
            id text primary key,
            school_id text not null references schools(id),
            parse_task_id text not null references document_parse_tasks(id),
            import_batch_id text not null references question_import_batches(id),
            item_index integer not null,
            page_number integer,
            question_number text not null default '',
            stem text not null,
            question_type text not null,
            options_json text not null default '{}',
            answer_json text not null default '{}',
            analysis text not null default '',
            answer_area_json text not null default '{}',
            media_json text not null default '[]',
            coordinates_json text not null default '{}',
            confidence real not null default 0.0,
            parser_name text not null,
            parser_version text not null,
            review_status text not null default 'needs_review',
            warnings_json text not null default '[]',
            saved_question_id text references questions(id),
            created_at text default current_timestamp,
            unique(parse_task_id, item_index)
        );

        create table if not exists document_parser_configs (
            id text primary key,
            school_id text not null references schools(id),
            parser_mode text not null,
            enabled integer not null default 1,
            command_path text not null default '',
            api_endpoint text not null default '',
            fallback_policy text not null default 'fail_closed',
            config_json text not null default '{}',
            last_test_status text not null default '',
            created_at text default current_timestamp,
            unique(school_id, parser_mode)
        );
        """
    )
    _ensure_column(
        conn,
        "questions",
        "original_paper_id text references original_papers(id)",
    )
    _ensure_column(
        conn,
        "questions",
        "import_batch_id text references question_import_batches(id)",
    )
    _ensure_column(
        conn,
        "questions",
        "parser_task_id text references document_parse_tasks(id)",
    )
    _ensure_column(conn, "questions", "original_page integer")
    _ensure_column(
        conn,
        "questions",
        "original_question_number text not null default ''",
    )
    _ensure_column(
        conn,
        "questions",
        "source_school text not null default ''",
    )
    _ensure_column(
        conn,
        "questions",
        "source_publisher text not null default ''",
    )
    _ensure_column(conn, "questions", "exam_type text not null default ''")
    _ensure_column(
        conn,
        "questions",
        "source_confidence real not null default 1.0",
    )
    _ensure_column(
        conn,
        "questions",
        "review_status text not null default 'confirmed'",
    )
    _ensure_column(
        conn,
        "question_tag_candidates",
        "literacy_tags_json text not null default '[]'",
    )
    _ensure_column(conn, "export_tasks", "file_name text not null default ''")
    _ensure_column(conn, "export_tasks", "content_type text not null default ''")
    _ensure_column(conn, "export_tasks", "byte_size integer not null default 0")
    _ensure_column(conn, "export_tasks", "engine_version text not null default ''")
    _ensure_column(conn, "export_tasks", "created_by text references users(id)")
    _ensure_column(conn, "export_tasks", "completed_at text")
    _ensure_column(
        conn,
        "document_parse_tasks",
        "original_paper_id text references original_papers(id)",
    )
    _ensure_column(
        conn,
        "document_parse_tasks",
        "import_batch_id text references question_import_batches(id)",
    )
    _ensure_column(
        conn,
        "document_parse_tasks",
        "parser_mode text not null default 'deterministic_text'",
    )
    _ensure_column(
        conn,
        "document_parse_tasks",
        "fallback_policy text not null default 'fail_closed'",
    )
    _ensure_column(
        conn,
        "document_parse_tasks",
        "source_text text not null default ''",
    )
    conn.executescript(
        """
        create table if not exists grading_revisions (
            id text primary key,
            school_id text not null references schools(id),
            assessment_id text not null references assessment_sessions(id),
            status text not null default 'draft',
            reason text not null,
            created_by text not null references users(id),
            applied_at text,
            created_at text default current_timestamp
        );

        create table if not exists grading_revision_items (
            id text primary key,
            school_id text not null references schools(id),
            revision_id text not null references grading_revisions(id),
            response_id text not null references student_responses(id),
            previous_answer text,
            revised_answer text,
            previous_score integer,
            revised_score integer not null,
            max_score integer not null,
            reason text not null,
            created_at text default current_timestamp
        );

        create table if not exists redo_attempts (
            id text primary key,
            school_id text not null references schools(id),
            wrong_question_id text not null references wrong_questions(id),
            student_id text not null references users(id),
            answer text not null default '',
            score integer,
            max_score integer,
            status text not null default 'submitted',
            feedback text not null default '',
            submitted_at text default current_timestamp,
            reviewed_by text references users(id),
            reviewed_at text
        );

        create table if not exists error_reason_tags (
            id text primary key,
            school_id text not null references schools(id),
            code text not null,
            name text not null,
            description text not null default '',
            enabled integer not null default 1,
            created_at text default current_timestamp,
            unique(school_id, code)
        );

        create table if not exists wrong_question_error_tags (
            wrong_question_id text not null references wrong_questions(id),
            error_reason_tag_id text not null references error_reason_tags(id),
            tagged_by text not null references users(id),
            note text not null default '',
            created_at text default current_timestamp,
            primary key(wrong_question_id, error_reason_tag_id)
        );

        create table if not exists export_profiles (
            id text primary key,
            school_id text not null references schools(id),
            name text not null,
            options_json text not null,
            created_by text references users(id),
            created_at text default current_timestamp
        );

        create index if not exists idx_grading_revisions_assessment
        on grading_revisions(assessment_id, status);

        create index if not exists idx_redo_attempts_wrong
        on redo_attempts(wrong_question_id, status);

        create index if not exists idx_wrong_question_error_tags_tag
        on wrong_question_error_tags(error_reason_tag_id);

        create index if not exists idx_questions_source_filters
        on questions(
            school_id, grade, chapter, difficulty, quality_status,
            review_status
        );

        create index if not exists idx_questions_original_paper
        on questions(original_paper_id, import_batch_id);

        create index if not exists idx_parsed_items_batch_status
        on parsed_question_items(import_batch_id, review_status, confidence);

        create index if not exists idx_student_mastery_metrics_student_type
        on student_mastery_metrics(student_id, tag_type);

        create index if not exists idx_student_mastery_metrics_school_tag
        on student_mastery_metrics(school_id, tag_type, tag_id);

        create index if not exists idx_runtime_capability_checks_latest
        on runtime_capability_checks(school_id, capability_id, checked_at);

        create index if not exists idx_provider_configs_kind
        on provider_configs(school_id, provider_kind, enabled);

        create index if not exists idx_provider_usage_config_time
        on provider_usage_events(provider_config_id, created_at);

        create index if not exists idx_provider_budget_windows_config
        on provider_budget_windows(provider_config_id, window_type, window_start);

        create index if not exists idx_generated_export_files_task
        on generated_export_files(export_task_id, created_at);

        create index if not exists idx_sso_login_states_state
        on sso_login_states(state, status);

        create index if not exists idx_external_identity_bindings_subject
        on external_identity_bindings(school_id, provider, issuer, subject);
        """
    )
    _ensure_column(
        conn,
        "student_responses",
        "ocr_payload_json text not null default '{}'",
    )
    _ensure_column(
        conn,
        "student_responses",
        "reviewed_by text references users(id)",
    )
    _ensure_column(conn, "student_responses", "reviewed_at text")
    _ensure_column(
        conn,
        "wrong_questions",
        "latest_redo_status text not null default 'pending'",
    )
    _ensure_column(
        conn,
        "wrong_questions",
        "error_reason_tag_ids_json text not null default '[]'",
    )
    conn.execute(
        """
        update wrong_questions
        set latest_redo_status = redo_status
        where latest_redo_status = 'pending'
          and redo_status <> 'pending'
        """
    )
    conn.execute(
        """
        insert or ignore into mastery_inference_versions(
            id, school_id, version_label, method, status
        )
        select
            'mastery-deterministic-v1',
            id,
            'deterministic-tag-metrics-v1',
            'Published assessment responses plus reviewed redo attempts; blanks are separate eligible attempts',
            'active'
        from schools
        """
    )
    conn.execute("pragma user_version = %d" % SCHEMA_VERSION)
    conn.commit()


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _exists(conn, table_name):
    return conn.execute("select 1 from %s limit 1" % table_name).fetchone() is not None


def bootstrap_admin(
    conn,
    username,
    display_name,
    password_hash,
    school_name,
):
    if _exists(conn, "users"):
        raise ValueError("Cannot bootstrap admin when users already exist")

    school = conn.execute(
        "select id from schools order by created_at, id limit 1"
    ).fetchone()
    if school is None:
        school_id = "school-" + uuid.uuid4().hex[:12]
        conn.execute(
            "insert into schools(id, name, org_scope) values(?,?,?)",
            (school_id, school_name, "single-school"),
        )
    else:
        school_id = school["id"]

    user_id = "user-admin-" + uuid.uuid4().hex[:12]
    conn.execute(
        """
        insert into users(
            id, school_id, username, display_name, role, class_id, student_no,
            enrollment_year, status, password_hash, must_change_password
        ) values(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            user_id,
            school_id,
            username,
            display_name,
            "admin",
            None,
            None,
            None,
            "active",
            password_hash,
            0,
        ),
    )
    conn.execute(
        """
        insert into role_assignments(
            id, user_id, role, scope_type, scope_id
        ) values(?,?,?,?,?)
        """,
        (
            "role-" + uuid.uuid4().hex,
            user_id,
            "admin",
            "school",
            school_id,
        ),
    )
    conn.execute(
        """
        insert into identity_audit_logs(
            id, school_id, actor_id, action, user_id, detail_json
        ) values(?,?,?,?,?,?)
        """,
        (
            "identity-audit-" + uuid.uuid4().hex,
            school_id,
            user_id,
            "admin_bootstrapped",
            user_id,
            _json({"username": username}),
        ),
    )
    conn.commit()
    return user_id


def seed_demo_data(conn):
    if _exists(conn, "schools"):
        return

    school_id = "school-demo"
    ontology_id = DEFAULT_ONTOLOGY_ID
    mastery_version_id = "mastery-manual-v1"
    teacher_hash = hash_password("teacher123")
    student_hash = hash_password("student123")
    admin_hash = hash_password("admin123")

    conn.execute(
        "insert into schools(id, name, org_scope) values(?,?,?)",
        (school_id, "示范高中物理组", "single-school"),
    )
    conn.execute(
        "insert into class_groups(id, school_id, name, grade, school_year, status) values(?,?,?,?,?,?)",
        ("class-physics-1", school_id, "高二(1)班", "高二", "2025-2026", "active"),
    )
    users = [
        ("user-admin", school_id, "admin", "系统管理员", "admin", None, None, None, "active", admin_hash, 0),
        ("user-teacher-li", school_id, "teacher_li", "李老师", "teacher", None, None, None, "active", teacher_hash, 0),
        ("stu-1001", school_id, "stu_1001", "张明", "student", "class-physics-1", "1001", "2024", "active", student_hash, 0),
        ("stu-1002", school_id, "stu_1002", "李华", "student", "class-physics-1", "1002", "2024", "active", student_hash, 0),
        ("stu-1003", school_id, "stu_1003", "王然", "student", "class-physics-1", "1003", "2024", "active", student_hash, 0),
    ]
    conn.executemany(
        """
        insert into users(
            id, school_id, username, display_name, role, class_id, student_no,
            enrollment_year, status, password_hash, must_change_password
        ) values(?,?,?,?,?,?,?,?,?,?,?)
        """,
        users,
    )
    conn.execute(
        "insert into teacher_classes(teacher_id, class_id, subject) values(?,?,?)",
        ("user-teacher-li", "class-physics-1", "physics"),
    )
    conn.executemany(
        "insert into role_assignments(id, user_id, role, scope_type, scope_id) values(?,?,?,?,?)",
        [
            ("role-admin", "user-admin", "admin", "school", school_id),
            ("role-teacher-li", "user-teacher-li", "teacher", "class", "class-physics-1"),
            ("role-stu-1001", "stu-1001", "student", "self", "stu-1001"),
            ("role-stu-1002", "stu-1002", "student", "self", "stu-1002"),
            ("role-stu-1003", "stu-1003", "student", "self", "stu-1003"),
        ],
    )
    conn.executemany(
        "insert into access_policies(id, subject_role, resource, operation, scope_rule) values(?,?,?,?,?)",
        [
            ("policy-admin-all", "admin", "*", "*", "school"),
            ("policy-teacher-class-wrong", "teacher", "class_wrong_questions", "view", "assigned_class"),
            ("policy-teacher-assessment", "teacher", "assessment", "*", "assigned_class"),
            ("policy-student-self", "student", "wrong_questions", "view", "self"),
            ("policy-student-mastery", "student", "mastery_mark", "modify", "self"),
        ],
    )
    conn.execute(
        "insert into mastery_inference_versions(id, school_id, version_label, method, status) values(?,?,?,?,?)",
        (
            mastery_version_id,
            school_id,
            "manual-mark-v1",
            "学生手动标记 + 原题重做结果",
            "active",
        ),
    )
    conn.execute(
        "insert into mastery_inference_versions(id, school_id, version_label, method, status) values(?,?,?,?,?)",
        (
            "mastery-deterministic-v1",
            school_id,
            "deterministic-tag-metrics-v1",
            "已发布评测作答 + 已复核错题重做；空白单独计为 eligible attempt",
            "active",
        ),
    )
    install_default_taxonomy(
        conn,
        school_id=school_id,
        actor_id="user-admin",
        publish=True,
    )
    conn.executemany(
        """
        insert into knowledge_edges(
            id, school_id, ontology_version_id, source_node_id, target_node_id,
            relation_type, bidirectional, rationale
        ) values(?,?,?,?,?,?,?,?)
        """,
        [
            ("edge-kin-newton", school_id, ontology_id, "kn-pep2019-r1-c02", "kn-pep2019-r1-c04-s03", "前置", 1, "运动学变量是应用牛顿第二定律的前提"),
            ("edge-newton-work", school_id, ontology_id, "kn-pep2019-r1-c04-s03", "kn-pep2019-r2-c08", "迁移", 1, "受力过程可迁移到功和能量分析"),
            ("edge-newton-confuse", school_id, ontology_id, "kn-pep2019-r1-c04", "kn-pep2019-r1-c04-s03", "易混", 1, "概念陈述和定量应用常被混淆"),
        ],
    )
    conn.execute(
        """
        insert into original_papers(
            id, school_id, title, document_name, source_school,
            source_publisher, exam_type, grade, term, status, created_by
        ) values(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "paper-origin-week-1",
            school_id,
            "牛顿运动定律周测一原卷",
            "牛顿运动定律周测一.docx",
            "校内命题",
            "高二物理备课组",
            "weekly_quiz",
            "高二",
            "2025-2026下",
            "active",
            "user-teacher-li",
        ),
    )
    conn.execute(
        """
        insert into question_import_batches(
            id, school_id, original_paper_id, source_file_name, parser_mode,
            status, item_count, saved_count, created_by
        ) values(?,?,?,?,?,?,?,?,?)
        """,
        (
            "import-week-1",
            school_id,
            "paper-origin-week-1",
            "牛顿运动定律周测一.docx",
            "deterministic_text",
            "parsed",
            3,
            3,
            "user-teacher-li",
        ),
    )
    questions = [
        (
            "q-newton-1",
            school_id,
            "一个物体在水平面上受恒定合外力作用，加速度方向与下列哪一项一致？",
            _json({"A": "速度方向", "B": "合外力方向", "C": "位移方向", "D": "摩擦力方向"}),
            _json("B"),
            "由牛顿第二定律可知，加速度方向与合外力方向一致。",
            "single_choice",
            "校本周测",
            "高二",
            "牛顿运动定律",
            "基础",
            _json([]),
            "课堂巩固",
            "draft",
            "",
            1,
            "paper-origin-week-1",
            "import-week-1",
            None,
            1,
            "1",
            "校内命题",
            "高二物理备课组",
            "weekly_quiz",
            0.95,
            "confirmed",
        ),
        (
            "q-newton-2",
            school_id,
            "质量为2kg的物体受到6N合外力，若初速度为0，则2s末速度大小为多少？",
            _json({"A": "3m/s", "B": "4m/s", "C": "6m/s", "D": "12m/s"}),
            _json("C"),
            "a=F/m=3m/s²，v=at=6m/s。",
            "single_choice",
            "校本周测",
            "高二",
            "牛顿第二定律",
            "中等",
            _json([]),
            "课堂巩固",
            "reviewed",
            "",
            1,
            "paper-origin-week-1",
            "import-week-1",
            None,
            1,
            "2",
            "校内命题",
            "高二物理备课组",
            "weekly_quiz",
            0.95,
            "confirmed",
        ),
        (
            "q-fill-1",
            school_id,
            "近地面重力加速度通常取多少 m/s²？",
            _json({}),
            _json(["9.8", "9.80"]),
            "高中阶段常取 g=9.8m/s²。",
            "fill",
            "校本周测",
            "高二",
            "基础常量",
            "基础",
            _json([]),
            "课堂巩固",
            "reviewed",
            "",
            1,
            "paper-origin-week-1",
            "import-week-1",
            None,
            1,
            "3",
            "校内命题",
            "高二物理备课组",
            "weekly_quiz",
            0.95,
            "confirmed",
        ),
    ]
    conn.executemany(
        """
        insert into questions(
            id, school_id, stem, options_json, answer_json, analysis, question_type,
            source, grade, chapter, difficulty, media_json, scenario, quality_status,
            notes, version, original_paper_id, import_batch_id, parser_task_id,
            original_page, original_question_number, source_school,
            source_publisher, exam_type, source_confidence, review_status
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        questions,
    )
    conn.executemany(
        """
        insert into question_tags(
            id, school_id, question_id, tag_type, tag_id, ontology_version_id,
            source, confirmed_by, confidence, rationale
        ) values(?,?,?,?,?,?,?,?,?,?)
        """,
        [
            ("tag-q2-kn", school_id, "q-newton-2", "knowledge", "kn-pep2019-r1-c04-s03", ontology_id, "teacher", "user-teacher-li", 1.0, "示例正式标签"),
            ("tag-q2-ab-force", school_id, "q-newton-2", "ability", "ab-force-analysis", ontology_id, "teacher", "user-teacher-li", 1.0, "示例正式标签"),
            ("tag-q2-ab-eq", school_id, "q-newton-2", "ability", "ab-equation-building", ontology_id, "teacher", "user-teacher-li", 1.0, "示例正式标签"),
            ("tag-q3-kn", school_id, "q-fill-1", "knowledge", "kn-pep2019-r1-c02", ontology_id, "teacher", "user-teacher-li", 1.0, "示例正式标签"),
            ("tag-q3-ab-calc", school_id, "q-fill-1", "ability", "ab-calculation", ontology_id, "teacher", "user-teacher-li", 1.0, "示例正式标签"),
        ],
    )
    conn.execute(
        "insert into papers(id, school_id, title, source, status) values(?,?,?,?,?)",
        ("paper-week-1", school_id, "牛顿运动定律周测", "教师录入", "reviewed"),
    )
    conn.executemany(
        "insert into paper_questions(paper_id, question_id, position, points) values(?,?,?,?)",
        [
            ("paper-week-1", "q-newton-1", 1, 4),
            ("paper-week-1", "q-newton-2", 2, 4),
            ("paper-week-1", "q-fill-1", 3, 2),
        ],
    )
    conn.execute(
        "insert into answer_card_templates(id, school_id, name, template_json) values(?,?,?,?)",
        (
            "card-template-1",
            school_id,
            "三题客观题答题卡",
            _json({"identity": "student_no", "questions": ["q-newton-1", "q-newton-2", "q-fill-1"]}),
        ),
    )
    conn.execute(
        """
        insert into assessment_sessions(
            id, school_id, title, term, grade, class_id, scheduled_at, source,
            full_score, paper_id, answer_card_template_id, ontology_version_id,
            mastery_inference_version_id, status, grading_status, statistics_status
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "assess-week-1",
            school_id,
            "牛顿运动定律周测一",
            "2025-2026下",
            "高二",
            "class-physics-1",
            "2026-06-05 08:00:00",
            "周测",
            10,
            "paper-week-1",
            "card-template-1",
            ontology_id,
            mastery_version_id,
            "待复核",
            "待复核",
            "not_started",
        ),
    )
    conn.executemany(
        "insert into assessment_participants(assessment_id, student_id, status) values(?,?,?)",
        [
            ("assess-week-1", "stu-1001", "present"),
            ("assess-week-1", "stu-1002", "present"),
            ("assess-week-1", "stu-1003", "present"),
        ],
    )
    snapshots = [
        (
            "snap-q1",
            "assess-week-1",
            "q-newton-1",
            1,
            4,
            questions[0][2],
            questions[0][3],
            questions[0][4],
            _json({"type": "single_choice", "answer": "B", "points": 4}),
            _json([]),
            1,
            ontology_id,
        ),
        (
            "snap-q2",
            "assess-week-1",
            "q-newton-2",
            2,
            4,
            questions[1][2],
            questions[1][3],
            questions[1][4],
            _json({"type": "single_choice", "answer": "C", "points": 4}),
            _json([
                {"tag_type": "knowledge", "tag_id": "kn-pep2019-r1-c04-s03", "name": "牛顿第二定律"},
                {"tag_type": "ability", "tag_id": "ab-force-analysis", "name": "受力分析"},
                {"tag_type": "ability", "tag_id": "ab-equation-building", "name": "方程建立"},
            ]),
            1,
            ontology_id,
        ),
        (
            "snap-q3",
            "assess-week-1",
            "q-fill-1",
            3,
            2,
            questions[2][2],
            questions[2][3],
            questions[2][4],
            _json({"type": "fill", "answer": ["9.8", "9.80"], "points": 2, "match": "exact"}),
            _json([
                {"tag_type": "knowledge", "tag_id": "kn-pep2019-r1-c02", "name": "匀变速直线运动的研究"},
                {"tag_type": "ability", "tag_id": "ab-calculation", "name": "数学运算"},
            ]),
            1,
            ontology_id,
        ),
    ]
    conn.executemany(
        """
        insert into question_version_snapshots(
            id, assessment_id, question_id, position, points, stem, options_json,
            answer_json, grading_rule_json, tag_snapshot_json, question_version,
            ontology_version_id
        ) values(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        snapshots,
    )
    conn.execute(
        """
        insert into scan_batches(
            id, school_id, assessment_id, source_name, recognizer,
            recognizer_version, status, low_confidence_count
        ) values(?,?,?,?,?,?,?,?)
        """,
        ("scan-week-1", school_id, "assess-week-1", "答题卡扫描样例", "PaddleOCR", "reserved-local-v1", "待复核", 1),
    )
    responses = [
        ("resp-1001-q1", school_id, "assess-week-1", "scan-week-1", "stu-1001", "q-newton-1", "snap-q1", "A", "A", 0.95, "not_required", ""),
        ("resp-1001-q2", school_id, "assess-week-1", "scan-week-1", "stu-1001", "q-newton-2", "snap-q2", "D", "D", 0.42, "required", "low_confidence"),
        ("resp-1001-q3", school_id, "assess-week-1", "scan-week-1", "stu-1001", "q-fill-1", "snap-q3", "9.8", "9.8", 0.88, "not_required", ""),
        ("resp-1002-q1", school_id, "assess-week-1", "scan-week-1", "stu-1002", "q-newton-1", "snap-q1", "B", "B", 0.91, "not_required", ""),
        ("resp-1002-q2", school_id, "assess-week-1", "scan-week-1", "stu-1002", "q-newton-2", "snap-q2", "C", "C", 0.93, "not_required", ""),
        ("resp-1002-q3", school_id, "assess-week-1", "scan-week-1", "stu-1002", "q-fill-1", "snap-q3", "9.5", "9.5", 0.86, "not_required", ""),
        ("resp-1003-q1", school_id, "assess-week-1", "scan-week-1", "stu-1003", "q-newton-1", "snap-q1", "C", "C", 0.84, "not_required", ""),
        ("resp-1003-q2", school_id, "assess-week-1", "scan-week-1", "stu-1003", "q-newton-2", "snap-q2", "B", "B", 0.87, "not_required", ""),
        ("resp-1003-q3", school_id, "assess-week-1", "scan-week-1", "stu-1003", "q-fill-1", "snap-q3", "", "", 0.81, "not_required", ""),
    ]
    conn.executemany(
        """
        insert into student_responses(
            id, school_id, assessment_id, scan_batch_id, student_id, question_id,
            snapshot_id, raw_answer, final_answer, original_confidence,
            review_status, review_reason
        ) values(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        responses,
    )
    conn.executemany(
        """
        insert into document_parse_tasks(
            id, school_id, file_name, parser, parser_version, status, output_json, failure_reason
        ) values(?,?,?,?,?,?,?,?)
        """,
        [
            ("parse-word-sample", school_id, "牛顿运动定律周测.docx", "Microsoft MarkItDown", "reserved", "completed", _json({"questions_detected": 3}), ""),
            ("parse-pdf-sample", school_id, "牛顿运动定律周测.pdf", "MinerU API", "reserved", "queued_for_local_fallback", _json({}), "API额度不足，进入本地队列"),
        ],
    )
    conn.execute(
        """
        insert into privacy_consent_records(
            id, school_id, subject_type, subject_id, basis, retention_policy, status
        ) values(?,?,?,?,?,?,?)
        """,
        (
            "privacy-school-2026",
            school_id,
            "school",
            school_id,
            "学校教学管理授权；最小必要采集学生姓名、学号、班级、作答和错题数据",
            "毕业或转出后按学校要求归档、脱敏或删除",
            "active",
        ),
    )
    conn.execute(
        """
        insert into llm_provider_configs(
            id, school_id, provider_name, model_name, key_ciphertext, key_masked,
            enabled, daily_call_limit, monthly_budget_cents, last_test_status
        ) values(?,?,?,?,?,?,?,?,?,?)
        """,
        ("llm-local-placeholder", school_id, "本地演示候选器", "local-deterministic-candidate-v1", "", "", 1, 500, 0, "ok"),
    )
    conn.execute(
        """
        insert into audit_events(id, school_id, actor_id, action, resource_type, resource_id, detail_json)
        values(?,?,?,?,?,?,?)
        """,
        (
            "audit-seed",
            school_id,
            "user-admin",
            "seed_demo_data",
            "school",
            school_id,
            _json({"summary": "初始化示范账号、班级、题目、测评和答题卡识别样例"}),
        ),
    )
    conn.commit()
