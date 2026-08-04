"""Schema constants and migration data for the Humanize repository."""

from __future__ import annotations

_SCHEMA_VERSION = 23
_CONTEXT_PREVIEW_CHARS = 1_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jargon_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    term TEXT NOT NULL,
    normalized_term TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_type, scope_id, normalized_term)
);

CREATE TABLE IF NOT EXISTS jargon_senses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL UNIQUE REFERENCES jargon_entries(id) ON DELETE CASCADE,
    meaning TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jargon_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES jargon_entries(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    source_text TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid INTEGER NOT NULL DEFAULT 1,
    UNIQUE(entry_id, message_id, content_hash)
);

CREATE TABLE IF NOT EXISTS jargon_inference_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES jargon_entries(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    proposed_meaning TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jargon_injection_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    entry_id INTEGER REFERENCES jargon_entries(id) ON DELETE SET NULL,
    selected INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS protocol_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    success INTEGER NOT NULL,
    action TEXT NOT NULL,
    failure_code TEXT NOT NULL,
    failure_detail TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    raw_output_snapshot TEXT NOT NULL DEFAULT '',
    raw_snapshot_complete INTEGER NOT NULL DEFAULT 0,
    messages_json TEXT NOT NULL DEFAULT '[]',
    response_snapshot_json TEXT NOT NULL DEFAULT '{}',
    response_snapshot_complete INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jargon_entries_scope
    ON jargon_entries(scope_type, scope_id, status, confidence);
CREATE INDEX IF NOT EXISTS idx_jargon_evidence_entry_time
    ON jargon_evidence(entry_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_injection_request
    ON jargon_injection_logs(request_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_protocol_created
    ON protocol_logs(created_at DESC);
"""


_PROMPT_TEMPLATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS humanize_prompt_templates (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    rule_content TEXT NOT NULL,
    protocol_content TEXT NOT NULL,
    repair_content TEXT NOT NULL,
    memory_extraction_content TEXT NOT NULL,
    reply_examples_content TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_prompt_template_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL CHECK (action IN ('update', 'reset')),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_humanize_prompt_template_audit_created
    ON humanize_prompt_template_audit(created_at DESC, id DESC);
"""

_DROP_LEGACY_CONTROL_SCHEMA = """
DROP INDEX IF EXISTS idx_humanize_control_audit_created;
DROP TABLE IF EXISTS humanize_control_audit;
DROP TABLE IF EXISTS humanize_expression;
DROP TABLE IF EXISTS humanize_behavior_policy;
DROP TABLE IF EXISTS humanize_state;
DROP TABLE IF EXISTS humanize_persona;
"""

_CONTEXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS humanize_context_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    protocol_mode TEXT NOT NULL,
    estimated_tokens INTEGER NOT NULL,
    included_sections INTEGER NOT NULL,
    omitted_sections INTEGER NOT NULL,
    request_snapshot_json TEXT NOT NULL DEFAULT '{}',
    request_snapshot_complete INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_context_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES humanize_context_runs(id) ON DELETE CASCADE,
    section_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    targets_json TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    required INTEGER NOT NULL,
    included INTEGER NOT NULL,
    budget_tokens INTEGER,
    estimated_tokens INTEGER NOT NULL,
    applied_tokens INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    reason TEXT NOT NULL,
    content_preview TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_chars INTEGER NOT NULL,
    preview_truncated INTEGER NOT NULL,
    content_snapshot TEXT NOT NULL,
    snapshot_complete INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_humanize_context_runs_created
    ON humanize_context_runs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_context_runs_scope
    ON humanize_context_runs(scope_type, scope_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_context_sections_run
    ON humanize_context_sections(run_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_humanize_context_sections_stats
    ON humanize_context_sections(section_key, included, created_at DESC);
"""

_JARGON_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS jargon_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES jargon_entries(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(entry_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_jargon_aliases_normalized
    ON jargon_aliases(normalized_alias, entry_id);
CREATE INDEX IF NOT EXISTS idx_jargon_senses_entry_status
    ON jargon_senses(entry_id, status, confidence DESC, id);
"""

_PROVIDER_OBSERVABILITY_SCHEMA = """CREATE TABLE IF NOT EXISTS humanize_prompt_prefix_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    provider_id TEXT NOT NULL DEFAULT '',
    provider_type TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    epoch_id TEXT NOT NULL DEFAULT '',
    request_fingerprint TEXT NOT NULL DEFAULT '',
    prefix_fingerprint TEXT NOT NULL DEFAULT '',
    first_difference TEXT NOT NULL DEFAULT '',
    cache_observability TEXT NOT NULL DEFAULT 'unknown',
    input_cached INTEGER NOT NULL DEFAULT 0,
    input_other INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    usage_observed INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_humanize_prefix_samples_created
    ON humanize_prompt_prefix_samples(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_prefix_samples_scope
    ON humanize_prompt_prefix_samples(scope_type, scope_id, created_at DESC);

CREATE TABLE IF NOT EXISTS humanize_llm_usage_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    provider_id TEXT NOT NULL DEFAULT '',
    provider_type TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    request_fingerprint TEXT NOT NULL DEFAULT '',
    input_cached INTEGER NOT NULL DEFAULT 0,
    input_other INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    usage_observed INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_humanize_usage_samples_created
    ON humanize_llm_usage_samples(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_usage_samples_scope
    ON humanize_llm_usage_samples(scope_type, scope_id, created_at DESC);
"""

_PROVIDER_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS humanize_provider_cache_capabilities (
    provider_id TEXT NOT NULL,
    provider_type TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    capability TEXT NOT NULL DEFAULT 'unknown'
        CHECK (capability IN ('implicit', 'explicit', 'unsupported', 'unknown')),
    usage_observability TEXT NOT NULL DEFAULT 'unknown'
        CHECK (usage_observability IN ('observable', 'unsupported', 'unknown')),
    observed_samples INTEGER NOT NULL DEFAULT 0,
    cached_samples INTEGER NOT NULL DEFAULT 0,
    input_cached INTEGER NOT NULL DEFAULT 0,
    input_other INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(provider_id, model)
);

CREATE INDEX IF NOT EXISTS idx_humanize_provider_cache_seen
    ON humanize_provider_cache_capabilities(last_seen_at DESC);
"""


_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS humanize_memory_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL CHECK (
        job_type IN (
            'extract', 'extract_turn', 'embed_example'
        )
    ),
    request_id TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    scope_type TEXT NOT NULL DEFAULT '',
    scope_hash TEXT NOT NULL DEFAULT '',
    subject_hash TEXT NOT NULL DEFAULT '',
    conversation_hash TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT 'default',
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'running', 'retry', 'completed', 'dead')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_run_at TEXT NOT NULL,
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS humanize_memory_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK (
        entity_type IN ('example', 'job')
    ),
    entity_id INTEGER NOT NULL DEFAULT 0,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_reply_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    scope_type TEXT NOT NULL DEFAULT 'global',
    scope_hash TEXT NOT NULL DEFAULT '',
    subject_hash TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT 'default',
    topic TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '',
    style_tags_json TEXT NOT NULL DEFAULT '[]',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    turns_json TEXT NOT NULL,
    ideal_reply TEXT NOT NULL,
    conditions TEXT NOT NULL DEFAULT '',
    exclusions TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'approved', 'rejected', 'tombstoned')
    ),
    enabled INTEGER NOT NULL DEFAULT 0,
    quality_score REAL NOT NULL DEFAULT 0.8 CHECK (
        quality_score >= 0 AND quality_score <= 1
    ),
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_context_run_id TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE(scope_type, scope_hash, agent_id, content_hash)
);

CREATE TABLE IF NOT EXISTS humanize_reply_example_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    example_id INTEGER NOT NULL REFERENCES humanize_reply_examples(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(example_id, revision)
);

CREATE TABLE IF NOT EXISTS humanize_reply_example_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    scope_type TEXT NOT NULL DEFAULT '',
    scope_hash TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT 'default',
    query_hash TEXT NOT NULL DEFAULT '',
    example_id INTEGER REFERENCES humanize_reply_examples(id) ON DELETE SET NULL,
    score REAL NOT NULL DEFAULT 0,
    rank INTEGER NOT NULL DEFAULT 0,
    selected INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_embeddings (
    entity_type TEXT NOT NULL CHECK (entity_type = 'example'),
    entity_id INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    model TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    generation TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(entity_type, entity_id, provider_id, model, generation)
);

"""

_MEMORY_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_humanize_memory_jobs_claim_agent
    ON humanize_memory_jobs(agent_id, status, next_run_at, lease_expires_at, id);
CREATE INDEX IF NOT EXISTS idx_humanize_memory_audit_entity
    ON humanize_memory_audit(entity_type, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_examples_scope_agent
    ON humanize_reply_examples(scope_type, scope_hash, agent_id, status, enabled, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_example_usage_request_agent
    ON humanize_reply_example_usage(agent_id, request_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_embeddings_generation
    ON humanize_embeddings(provider_id, model, generation, entity_type, entity_id);
"""

_MEMORY_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS humanize_reply_example_fts USING fts5(
    example_id UNINDEXED,
    search_text,
    tokenize='unicode61'
);
"""

_DROP_LEGACY_MEMORY_SCHEMA = """
DROP TRIGGER IF EXISTS humanize_memory_fts_ai;
DROP TRIGGER IF EXISTS humanize_memory_fts_ad;
DROP TRIGGER IF EXISTS humanize_memory_fts_au;
DROP TABLE IF EXISTS humanize_memory_fts;
DROP TABLE IF EXISTS humanize_memory_evidence;
DROP TABLE IF EXISTS humanize_memory_aliases;
DROP TABLE IF EXISTS humanize_memory_revisions;
DROP TABLE IF EXISTS humanize_memory_recall_logs;
DROP TABLE IF EXISTS humanize_memory_items;
DROP TABLE IF EXISTS humanize_vector_index_state;
"""
