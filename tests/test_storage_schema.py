from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from astrbot_plugin_humanize.humanize.repositories.sqlite import (
    _SCHEMA_VERSION,
    SQLiteRepository,
)


def test_fresh_database_keeps_only_plugin_owned_memory_support_tables(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()

        with sqlite3.connect(db_path) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])

        assert version == _SCHEMA_VERSION
        assert {
            "humanize_context_runs",
            "humanize_prompt_templates",
            "humanize_prompt_template_audit",
            "humanize_provider_cache_capabilities",
            "humanize_memory_jobs",
            "humanize_memory_audit",
            "humanize_reply_examples",
            "humanize_reply_example_revisions",
            "humanize_reply_example_usage",
            "humanize_embeddings",
        } <= tables
        assert (
            not {
                "humanize_openviking_outbox",
                "humanize_openviking_commits",
            }
            & tables
        )
        assert (
            not {
                "humanize_persona",
                "humanize_state",
                "humanize_behavior_policy",
                "humanize_expression",
                "humanize_control_audit",
                "humanize_llm_cache_entries",
                "humanize_llm_cache_events",
                "humanize_embedding_cache",
                "humanize_embedding_cache_leases",
                "humanize_session_archives",
                "humanize_session_turns",
                "humanize_archive_turn_refs",
                "humanize_archive_layers",
                "humanize_archive_citations",
                "humanize_archive_events",
                "humanize_background_jobs",
                "humanize_embedding_generations",
                "humanize_vector_entries",
                "humanize_memory_items",
                "humanize_memory_evidence",
                "humanize_memory_aliases",
                "humanize_memory_revisions",
                "humanize_memory_recall_logs",
                "humanize_memory_fts",
                "humanize_vector_index_state",
            }
            & tables
        )

    asyncio.run(scenario())


def test_schema_upgrade_drops_legacy_memory_and_control_tables(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE humanize_prompt_templates (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    rule_content TEXT NOT NULL,
                    protocol_content TEXT NOT NULL,
                    repair_content TEXT NOT NULL,
                    archive_l0_system_content TEXT NOT NULL,
                    archive_l0_user_content TEXT NOT NULL,
                    archive_l1_system_content TEXT NOT NULL,
                    archive_l1_user_content TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO humanize_prompt_templates VALUES (
                    1, 'rule', 'protocol', '<Action>{{required_action}}</Action>', '', '', '', '',
                    '2026-07-16T00:00:00+00:00'
                );
                CREATE TABLE humanize_llm_cache_entries (
                    cache_key TEXT PRIMARY KEY
                );
                CREATE TABLE humanize_openviking_outbox (
                    id INTEGER PRIMARY KEY
                );
                CREATE TABLE humanize_memory_items (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_memory_evidence (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_memory_aliases (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_memory_revisions (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_memory_recall_logs (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_memory_fts (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_vector_index_state (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_persona (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_state (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_behavior_policy (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_expression (id INTEGER PRIMARY KEY);
                CREATE TABLE humanize_control_audit (id INTEGER PRIMARY KEY);
                PRAGMA user_version = 16;
                """
            )

        repository = SQLiteRepository(db_path)
        await repository.initialize()
        stored = await repository.get_prompt_templates()

        with sqlite3.connect(db_path) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])

        assert version == _SCHEMA_VERSION
        assert stored["templates"]["rule"] == "rule"
        assert stored["templates"]["protocol"] == "protocol"
        assert stored["templates"]["repair"] == ("<Action>{{required_action}}</Action>")
        assert set(stored["templates"]) == {
            "rule",
            "protocol",
            "repair",
            "memory_extraction",
            "reply_examples",
        }
        assert "humanize_llm_cache_entries" in tables
        assert "humanize_openviking_outbox" in tables
        assert "humanize_openviking_commits" not in tables
        assert (
            not {
                "humanize_memory_items",
                "humanize_memory_evidence",
                "humanize_memory_aliases",
                "humanize_memory_revisions",
                "humanize_memory_recall_logs",
                "humanize_memory_fts",
                "humanize_vector_index_state",
                "humanize_persona",
                "humanize_state",
                "humanize_behavior_policy",
                "humanize_expression",
                "humanize_control_audit",
            }
            & tables
        )
        assert "humanize_session_archives" not in tables

    asyncio.run(scenario())


def test_memory_job_lease_can_be_renewed_and_released_by_owner_only(
    tmp_path: Path,
) -> None:
    """Lease maintenance is atomic and legacy extract jobs remain discoverable."""

    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO humanize_memory_jobs (
                    job_key, job_type, request_id, provider_id, scope_type,
                    scope_hash, subject_hash, conversation_hash, payload_json,
                    status, attempts, next_run_at, created_at, updated_at
                ) VALUES (
                    'extract:legacy-request', 'extract', 'legacy-request', '',
                    'private_user', 'scope-a', 'subject-a', 'conversation-a',
                    '{}', 'pending', 0, '2000-01-01T00:00:00+00:00',
                    '2000-01-01T00:00:00+00:00',
                    '2000-01-01T00:00:00+00:00'
                )
                """
            )
            conn.commit()

        claimed = await repository.claim_memory_job("worker-a", lease_seconds=30)
        assert claimed is not None
        assert claimed["job_type"] == "extract_turn"
        job_id = int(claimed["id"])
        first_expiry = str(claimed["lease_expires_at"])

        assert await repository.renew_memory_job(job_id, "worker-b") is False
        assert (
            await repository.renew_memory_job(job_id, "worker-a", lease_seconds=180)
            is True
        )
        with sqlite3.connect(db_path) as conn:
            renewed = conn.execute(
                "SELECT status, lease_owner, lease_expires_at FROM humanize_memory_jobs "
                "WHERE id = ?",
                (job_id,),
            ).fetchone()
        assert renewed is not None
        assert renewed[0] == "running"
        assert renewed[1] == "worker-a"
        assert str(renewed[2]) > first_expiry

        assert await repository.release_memory_job(job_id, "worker-b") is False
        assert (
            await repository.release_memory_job(
                job_id, "worker-a", reason="worker_cancelled"
            )
            is True
        )
        assert await repository.renew_memory_job(job_id, "worker-a") is False

        listing = await repository.list_memory_jobs(
            job_type="extract_turn", page=1, page_size=20
        )
        assert listing["total"] == 1
        assert listing["items"][0]["job_type"] == "extract_turn"
        assert listing["items"][0]["status"] == "retry"
        assert listing["items"][0]["lease_owner"] == ""
        assert listing["items"][0]["lease_expires_at"] is None
        assert listing["items"][0]["error"] == "worker_cancelled"

    asyncio.run(scenario())


def test_memory_job_complete_persists_sanitized_result(tmp_path: Path) -> None:
    """Completing a job stores the sanitized execution summary and the list
    API returns it as ``result`` without raw payload text."""

    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO humanize_memory_jobs (
                    job_key, job_type, request_id, provider_id, scope_type,
                    scope_hash, subject_hash, conversation_hash, payload_json,
                    status, attempts, next_run_at, created_at, updated_at
                ) VALUES (
                    'extract:result-request', 'extract_turn', 'result-request', 'p1',
                    'private_user', 'scope-a', 'subject-a', 'conversation-a',
                    '{"user_text":"机密原文"}', 'pending', 0,
                    '2000-01-01T00:00:00+00:00',
                    '2000-01-01T00:00:00+00:00',
                    '2000-01-01T00:00:00+00:00'
                )
                """
            )
            conn.commit()
        claimed = await repository.claim_memory_job("worker-a", lease_seconds=30)
        assert claimed is not None
        job_id = int(claimed["id"])
        assert "user_text" in claimed["payload"]

        summary = {
            "extracted": [
                {
                    "memory_key": "preference:tea",
                    "memory_type": "preference",
                    "status": "candidate",
                    "operation": "create",
                    "version": 1,
                    "memory_uri": "viking://agent/default/memories/private_user/scope-a/subject-a/preference/preference:tea",
                }
            ],
            "candidate_count": 1,
            "source_turn_count": 1,
        }
        completed = await repository.complete_memory_job(
            job_id, "worker-a", result=summary
        )
        assert completed["status"] == "completed"

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT payload_json, result_json FROM humanize_memory_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == "{}"  # raw payload cleared
        stored = json.loads(row[1])
        assert stored["candidate_count"] == 1
        assert stored["extracted"][0]["memory_key"] == "preference:tea"

        listing = await repository.list_memory_jobs(
            job_type="extract_turn", page=1, page_size=20
        )
        item = listing["items"][0]
        assert item["result"]["candidate_count"] == 1
        assert "user_text" not in json.dumps(item.get("result", {}))

    asyncio.run(scenario())


def test_reply_example_recall_filters_conditions_exclusions_and_scores_ngrams(
    tmp_path: Path,
) -> None:
    """Reviewed examples apply deterministic positive and negative keyword gates."""

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        scope = {
            "scope_type": "global",
            "scope_hash": "scope-a",
            "subject_hash": "",
        }
        conditioned = await repository.apply_reply_example_action(
            {
                "action": "create",
                "title": "情绪支持",
                **scope,
                "agent_id": "agent-a",
                "turns": [{"role": "user", "content": "我有点撑不住"}],
                "ideal_reply": "先缓一下，我在。",
                "conditions": "难过，低落\n委屈",
                "exclusions": "考试;工作",
                "status": "approved",
                "enabled": True,
                "quality_score": 0.95,
            }
        )
        ngram = await repository.apply_reply_example_action(
            {
                "action": "create",
                "title": "alphabeta tone",
                **scope,
                "agent_id": "agent-a",
                "turns": [{"role": "user", "content": "release package"}],
                "ideal_reply": "Keep it concise.",
                "status": "approved",
                "enabled": True,
                "quality_score": 0.9,
            }
        )
        draft = await repository.apply_reply_example_action(
            {
                "action": "create",
                "title": "低落草稿",
                **scope,
                "agent_id": "agent-a",
                "turns": [{"role": "user", "content": "低落"}],
                "ideal_reply": "不能召回",
                "conditions": "低落",
                "status": "draft",
                "enabled": False,
                "quality_score": 1.0,
            }
        )
        wrong_scope = await repository.apply_reply_example_action(
            {
                "action": "create",
                "title": "其他作用域低落样例",
                **scope,
                "scope_hash": "scope-b",
                "agent_id": "agent-a",
                "turns": [{"role": "user", "content": "低落"}],
                "ideal_reply": "不能跨作用域召回",
                "conditions": "低落",
                "status": "approved",
                "enabled": True,
                "quality_score": 1.0,
            }
        )

        matched = await repository.search_reply_examples(
            [scope], "今天真的很低落", 10, 0.7, agent_id="agent-a"
        )
        conditioned_result = next(
            item for item in matched if int(item["id"]) == int(conditioned["id"])
        )
        assert conditioned_result["score"] > 0
        assert conditioned_result["filter_reason"] == "conditions_matched:低落"
        assert conditioned_result["score_components"]["condition_match"] == 1.0
        assert int(draft["id"]) not in {int(item["id"]) for item in matched}
        assert int(wrong_scope["id"]) not in {int(item["id"]) for item in matched}

        excluded = await repository.search_reply_examples(
            [scope], "考试后很低落", 10, 0.7, agent_id="agent-a"
        )
        assert int(conditioned["id"]) not in {int(item["id"]) for item in excluded}
        unmet = await repository.search_reply_examples(
            [scope], "今天需要鼓励", 10, 0.7, agent_id="agent-a"
        )
        assert int(conditioned["id"]) not in {int(item["id"]) for item in unmet}

        latin = await repository.search_reply_examples(
            [scope], "alpha build", 10, 0.7, agent_id="agent-a"
        )
        ngram_result = next(
            item for item in latin if int(item["id"]) == int(ngram["id"])
        )
        assert ngram_result["score"] > 0
        assert ngram_result["filter_reason"] == "no_conditions"
        assert ngram_result["score_components"]["bigram_overlap"] > 0

        vector_eligible = await repository.list_recallable_reply_examples(
            [scope], min_quality=0.7, agent_id="agent-a", limit=20
        )
        assert int(conditioned["id"]) not in {
            int(item["id"]) for item in vector_eligible
        }
        assert int(ngram["id"]) in {int(item["id"]) for item in vector_eligible}

    asyncio.run(scenario())


def test_memory_job_batch_never_mixes_agents(tmp_path: Path) -> None:
    """Batch extraction groups identical conversations by logical agent as well."""

    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()
        with sqlite3.connect(db_path) as conn:
            for position, agent_id in enumerate(
                ("agent-a", "agent-b", "agent-a", "agent-b"), start=1
            ):
                conn.execute(
                    """
                    INSERT INTO humanize_memory_jobs (
                        job_key, job_type, request_id, scope_type, scope_hash,
                        subject_hash, conversation_hash, agent_id, payload_json,
                        status, next_run_at, created_at, updated_at
                    ) VALUES (?, 'extract_turn', ?, 'private_user', 'scope-a',
                              'subject-a', 'conversation-a', ?, ?, 'pending',
                              '2000-01-01T00:00:00+00:00',
                              '2000-01-01T00:00:00+00:00',
                              '2000-01-01T00:00:00+00:00')
                    """,
                    (
                        f"extract_turn:request-{position}",
                        f"request-{position}",
                        agent_id,
                        f'{{"agent_id":"{agent_id}"}}',
                    ),
                )
            conn.commit()

        claimed = await repository.claim_memory_job_batch(
            "worker-a", batch_turns=2, idle_seconds=0
        )
        assert len(claimed) == 2
        assert {str(item["agent_id"]) for item in claimed} in (
            {"agent-a"},
            {"agent-b"},
        )
        assert {str(item["payload"].get("agent_id")) for item in claimed} == {
            str(claimed[0]["agent_id"])
        }

    asyncio.run(scenario())
