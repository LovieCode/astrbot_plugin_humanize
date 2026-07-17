"""AstrBot-facing adapter for embedded OpenViking session and memory semantics."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..vendor.openviking_core.core.identifiers import normalize_identifier_part
from ..vendor.openviking_core.message import Message, TextPart
from ..vendor.openviking_core.session.memory import MemoryFile, StoredLink
from ..vendor.openviking_core.session.memory.merge_op import ReplaceOp, merge_links
from ..vendor.openviking_core.session.memory.utils.memory_file_utils import (
    MemoryFileUtils,
    next_memory_version,
)
from .workspace import OpenVikingWorkspace

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SCOPE_TYPES = frozenset({"global", "private_user", "group", "group_member"})


@dataclass(frozen=True, slots=True)
class SessionCommitResult:
    """Result of one idempotent OpenViking session commit."""

    commit_id: str
    session_uri: str
    message_ids: tuple[str, ...]
    duplicate: bool
    message_count: int
    commit_count: int


@dataclass(frozen=True, slots=True)
class MemoryUpsertResult:
    """Result of one idempotent OpenViking memory operation."""

    operation_id: str
    memory_uri: str
    operation: str
    version: int
    duplicate: bool


class OpenVikingMemoryAdapter:
    """Translate anonymized Humanize jobs into embedded OpenViking files."""

    def __init__(self, workspace: OpenVikingWorkspace) -> None:
        """Create an adapter without touching disk.

        Args:
            workspace: Controlled OpenViking workspace.
        """
        self._workspace = workspace
        self._initialized = False

    def initialize(self) -> None:
        """Initialize and validate the controlled workspace."""
        self._workspace.initialize()
        self._initialized = True

    def commit_turn(self, payload: dict[str, Any]) -> SessionCommitResult:
        """Append one complete chat turn and create an idempotent commit.

        Args:
            payload: Anonymized durable job produced by ``ChatMemoryService``.

        Returns:
            Session commit identity and current aggregate counts.

        Raises:
            RuntimeError: If the adapter is not initialized or stored JSON is corrupt.
            ValueError: If identity, action, or message fields are invalid.
        """
        if not self._initialized:
            raise RuntimeError("OpenViking adapter is not initialized")

        commit_id = self._require_digest(payload.get("idempotency_key"), "commit id")
        scope_hash = self._require_digest(payload.get("scope_hash"), "scope hash")
        conversation_hash = self._require_digest(
            payload.get("conversation_hash"), "conversation hash"
        )
        raw_subject_hash = str(payload.get("subject_hash") or "").strip()
        subject_hash = (
            self._require_digest(raw_subject_hash, "subject hash")
            if raw_subject_hash
            else ""
        )
        scope_type = str(payload.get("scope_type") or "").strip()
        if scope_type not in _SCOPE_TYPES:
            raise ValueError("unsupported OpenViking scope type")
        agent_id = normalize_identifier_part(
            str(payload.get("agent_id") or "default").strip() or "default",
            "agent_id",
        )
        if agent_id is None:
            raise ValueError("OpenViking agent id is required")

        action = str(payload.get("action") or "").strip()
        if action not in {"Reply", "No Reply"}:
            raise ValueError("unsupported OpenViking turn action")
        user_text = str(payload.get("user_text") or "")[:8_000].strip()
        if not user_text:
            raise ValueError("OpenViking turn requires user text")
        raw_assistant_messages = payload.get("assistant_messages") or []
        if not isinstance(raw_assistant_messages, list) or not all(
            isinstance(item, str) for item in raw_assistant_messages
        ):
            raise ValueError("assistant messages must be a list of strings")
        assistant_messages = [item[:8_000] for item in raw_assistant_messages if item]
        if action == "Reply" and not assistant_messages:
            raise ValueError("Reply turn requires an assistant message")
        if action == "No Reply" and assistant_messages:
            raise ValueError("No Reply turn cannot contain assistant messages")

        occurred_at = str(payload.get("occurred_at") or "").strip()
        try:
            if occurred_at:
                datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            else:
                raise ValueError
        except ValueError:
            occurred_at = datetime.now(UTC).isoformat()

        session_uri = (
            f"viking://agent/{agent_id}/sessions/{scope_type}/"
            f"{scope_hash}/{conversation_hash}"
        )
        session_directory = (
            Path("sessions") / agent_id / scope_type / scope_hash / conversation_hash
        )
        messages_path = session_directory / "messages.jsonl"
        meta_path = session_directory / ".meta.json"
        commits_directory = session_directory / "commits"
        commit_path = commits_directory / f"{commit_id}.json"

        messages = [
            Message(
                id=f"{commit_id}-user",
                role="user",
                parts=[TextPart(text=user_text)],
                peer_id=subject_hash or None,
                created_at=occurred_at,
            )
        ]
        messages.extend(
            Message(
                id=f"{commit_id}-assistant-{index}",
                role="assistant",
                parts=[TextPart(text=text)],
                created_at=occurred_at,
            )
            for index, text in enumerate(assistant_messages, start=1)
        )
        desired_message_ids = tuple(message.id for message in messages)

        with self._workspace.transaction() as transaction:
            duplicate = transaction.is_file(commit_path)
            existing_messages: list[dict[str, Any]] = []
            existing_ids: set[str] = set()
            if transaction.is_file(messages_path):
                raw_messages = transaction.read_bytes(messages_path).decode("utf-8")
                for line in raw_messages.splitlines():
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            "OpenViking session JSONL is corrupt"
                        ) from exc
                    if not isinstance(record, dict) or not isinstance(
                        record.get("id"), str
                    ):
                        raise RuntimeError("OpenViking session message is invalid")
                    existing_messages.append(record)
                    existing_ids.add(record["id"])

            for message in messages:
                if message.id not in existing_ids:
                    existing_messages.append(message.to_dict())
                    existing_ids.add(message.id)
            serialized_messages = "".join(
                f"{json.dumps(record, ensure_ascii=False, sort_keys=True)}\n"
                for record in existing_messages
            )
            transaction.atomic_write(messages_path, serialized_messages)

            l0 = " ".join(user_text.split())[:160]
            l1_parts = [f"User: {user_text}"]
            l1_parts.extend(f"Assistant: {text}" for text in assistant_messages)
            l1 = "\n".join(l1_parts)[:1_000]
            commit_payload = {
                "action": action,
                "commit_id": commit_id,
                "created_at": occurred_at,
                "l0": l0,
                "l1": l1,
                "l2_uri": f"{session_uri}/messages.jsonl",
                "message_ids": list(desired_message_ids),
                "session_uri": session_uri,
                "source_complete": bool(payload.get("source_complete", True)),
                "summary_source": "deterministic_fallback",
            }
            if duplicate:
                try:
                    existing_commit = json.loads(
                        transaction.read_bytes(commit_path).decode("utf-8")
                    )
                except json.JSONDecodeError as exc:
                    raise RuntimeError("OpenViking session commit is corrupt") from exc
                if existing_commit != commit_payload:
                    raise RuntimeError("OpenViking commit id has conflicting content")
            else:
                transaction.atomic_write(
                    commit_path,
                    f"{json.dumps(commit_payload, ensure_ascii=True, indent=2, sort_keys=True)}\n",
                )

            commit_count = len(
                transaction.list_files(commits_directory, suffix=".json")
            )
            created_at = occurred_at
            if transaction.is_file(meta_path):
                try:
                    old_meta = json.loads(
                        transaction.read_bytes(meta_path).decode("utf-8")
                    )
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "OpenViking session metadata is corrupt"
                    ) from exc
                if not isinstance(old_meta, dict):
                    raise RuntimeError("OpenViking session metadata is invalid")
                created_at = str(old_meta.get("created_at") or occurred_at)
            meta_payload = {
                "agent_id": agent_id,
                "commit_count": commit_count,
                "conversation_hash": conversation_hash,
                "created_at": created_at,
                "last_commit_id": commit_id,
                "message_count": len(existing_messages),
                "scope_hash": scope_hash,
                "scope_type": scope_type,
                "session_uri": session_uri,
                "subject_hash": subject_hash,
                "updated_at": occurred_at,
            }
            transaction.atomic_write(
                meta_path,
                f"{json.dumps(meta_payload, ensure_ascii=True, indent=2, sort_keys=True)}\n",
            )

        return SessionCommitResult(
            commit_id=commit_id,
            session_uri=session_uri,
            message_ids=desired_message_ids,
            duplicate=duplicate,
            message_count=len(existing_messages),
            commit_count=commit_count,
        )

    def upsert_memory(
        self,
        candidate: dict[str, Any],
        *,
        evidence: list[dict[str, Any]],
        source_commit_ids: tuple[str, ...],
        force_replace: bool = False,
    ) -> MemoryUpsertResult:
        """Create or update one scoped OpenViking memory with diff and links.

        Args:
            candidate: Normalized memory candidate from the extraction stage.
            evidence: Trusted evidence records supporting the candidate.
            source_commit_ids: Session commits used by this extraction operation.
            force_replace: Whether a trusted caller overrides confidence ordering.

        Returns:
            Stable operation identity, URI, version, and duplicate state.

        Raises:
            RuntimeError: If the adapter is uninitialized or stored data is corrupt.
            ValueError: If candidate identity or content is invalid.
        """
        if not self._initialized:
            raise RuntimeError("OpenViking adapter is not initialized")
        normalized = self._normalize_memory_candidate(candidate)
        commit_ids = tuple(
            sorted(
                dict.fromkeys(
                    self._require_digest(value, "source commit id")
                    for value in source_commit_ids
                )
            )
        )
        if not commit_ids:
            raise ValueError("OpenViking memory requires a source commit")

        identity = "\0".join(
            str(normalized[key])
            for key in (
                "agent_id",
                "scope_type",
                "scope_hash",
                "subject_hash",
                "memory_type",
                "memory_key",
            )
        )
        memory_id = hashlib.sha256(identity.encode()).hexdigest()
        operation_material = "\0".join(
            (*commit_ids, memory_id, str(normalized["content"]))
        )
        operation_id = hashlib.sha256(operation_material.encode()).hexdigest()
        subject_segment = str(normalized["subject_hash"] or "global")
        memory_directory = (
            Path("memories")
            / str(normalized["agent_id"])
            / str(normalized["scope_type"])
            / str(normalized["scope_hash"])
            / subject_segment
            / str(normalized["memory_type"])
        )
        memory_path = memory_directory / f"{memory_id}.md"
        diff_path = Path("memory_diffs") / f"{operation_id}.json"
        memory_uri = (
            f"viking://agent/{normalized['agent_id']}/memories/"
            f"{normalized['scope_type']}/{normalized['scope_hash']}/"
            f"{subject_segment}/{normalized['memory_type']}/{memory_id}"
        )
        session_uri = (
            f"viking://agent/{normalized['agent_id']}/sessions/"
            f"{normalized['scope_type']}/{normalized['scope_hash']}/"
            f"{normalized['conversation_hash']}"
        )
        session_commits_directory = (
            Path("sessions")
            / str(normalized["agent_id"])
            / str(normalized["scope_type"])
            / str(normalized["scope_hash"])
            / str(normalized["conversation_hash"])
            / "commits"
        )

        with self._workspace.transaction() as transaction:
            for commit_id in commit_ids:
                if not transaction.is_file(
                    session_commits_directory / f"{commit_id}.json"
                ):
                    raise ValueError("OpenViking memory source commit does not exist")
            if transaction.is_file(diff_path):
                try:
                    stored_diff = json.loads(
                        transaction.read_bytes(diff_path).decode("utf-8")
                    )
                except json.JSONDecodeError as exc:
                    raise RuntimeError("OpenViking memory diff is corrupt") from exc
                if not isinstance(stored_diff, dict):
                    raise RuntimeError("OpenViking memory diff is invalid")
                return MemoryUpsertResult(
                    operation_id=operation_id,
                    memory_uri=memory_uri,
                    operation=str(stored_diff.get("operation") or "keep"),
                    version=int(stored_diff.get("version") or 1),
                    duplicate=True,
                )

            old_file: MemoryFile | None = None
            if transaction.is_file(memory_path):
                old_file = MemoryFileUtils.read(
                    transaction.read_bytes(memory_path).decode("utf-8"),
                    uri=memory_uri,
                )
                old_identity = {
                    key: str(old_file.extra_fields.get(key) or "")
                    for key in ("agent_id", "scope_type", "scope_hash", "subject_hash")
                }
                expected_identity = {
                    key: str(normalized[key])
                    for key in ("agent_id", "scope_type", "scope_hash", "subject_hash")
                }
                if old_identity != expected_identity:
                    raise RuntimeError("OpenViking memory identity conflicts with path")
                if old_file.extra_fields.get("last_operation_id") == operation_id:
                    recovered_diff = old_file.extra_fields.get("last_diff")
                    if not isinstance(recovered_diff, dict):
                        raise RuntimeError("OpenViking memory recovery diff is missing")
                    transaction.atomic_write(
                        diff_path,
                        f"{json.dumps(recovered_diff, ensure_ascii=True, indent=2, sort_keys=True)}\n",
                    )
                    return MemoryUpsertResult(
                        operation_id=operation_id,
                        memory_uri=memory_uri,
                        operation=str(recovered_diff.get("operation") or "keep"),
                        version=int(recovered_diff.get("version") or 1),
                        duplicate=True,
                    )

            old_content = old_file.content if old_file is not None else ""
            old_confidence = (
                float(old_file.extra_fields.get("confidence") or 0.0)
                if old_file is not None
                else 0.0
            )
            should_replace = (
                force_replace
                or old_file is None
                or (float(normalized["confidence"]) >= old_confidence)
            )
            content = (
                str(normalized["content"])
                if old_file is None
                else ReplaceOp().apply(
                    old_content,
                    str(normalized["content"]) if should_replace else "",
                )
            )
            operation = (
                "create"
                if old_file is None
                else "replace"
                if content != old_content
                else "keep"
            )
            version = next_memory_version(old_file)
            occurred_at = str(normalized["occurred_at"])
            created_at = (
                str(old_file.extra_fields.get("created_at") or occurred_at)
                if old_file is not None
                else occurred_at
            )
            keep_existing = old_file is not None and not should_replace
            effective_confidence = (
                old_confidence if keep_existing else float(normalized["confidence"])
            )
            effective_importance = (
                old_file.extra_fields.get("importance", normalized["importance"])
                if keep_existing
                else normalized["importance"]
            )
            effective_status = (
                old_file.extra_fields.get("status", normalized["status"])
                if keep_existing
                else normalized["status"]
            )
            effective_structured_value = (
                old_file.extra_fields.get(
                    "structured_value", normalized["structured_value"]
                )
                if keep_existing
                else normalized["structured_value"]
            )
            effective_valid_until = (
                old_file.extra_fields.get("valid_until", normalized["valid_until"])
                if keep_existing
                else normalized["valid_until"]
            )
            effective_valid_from = (
                old_file.extra_fields.get("valid_from", normalized["valid_from"])
                if keep_existing
                else normalized["valid_from"]
            )
            old_evidence = (
                old_file.extra_fields.get("evidence", [])
                if old_file is not None
                else []
            )
            evidence_by_key: dict[tuple[str, str], dict[str, Any]] = {}
            for item in [
                *(old_evidence if isinstance(old_evidence, list) else []),
                *evidence,
            ]:
                if not isinstance(item, dict):
                    continue
                quote = str(item.get("quote") or "")[:8_000]
                evidence_at = str(item.get("occurred_at") or occurred_at)
                if quote:
                    evidence_by_key[(quote, evidence_at)] = {
                        "occurred_at": evidence_at,
                        "quote": quote,
                        "source_complete": bool(item.get("source_complete", True)),
                    }
            merged_evidence = list(evidence_by_key.values())[-100:]
            existing_links = old_file.links if old_file is not None else []
            source_links = [
                StoredLink(
                    from_uri=memory_uri,
                    to_uri=f"{session_uri}/commits/{commit_id}",
                    link_type="derived_from",
                    weight=float(normalized["confidence"]),
                    description="Extracted from an anonymized session commit",
                    created_at=occurred_at,
                ).model_dump()
                for commit_id in commit_ids
            ]
            links = merge_links(existing_links, source_links)
            abstract_source = (
                old_file.extra_fields.get("abstract", "")
                if keep_existing
                else normalized.get("abstract", "")
            )
            abstract = (
                str(abstract_source or "").strip() or " ".join(content.split())[:160]
            )
            overview_source = (
                old_file.extra_fields.get("overview", "")
                if keep_existing
                else normalized.get("overview", "")
            )
            overview = str(overview_source or "").strip()
            if not overview:
                prefix = (
                    json.dumps(
                        effective_structured_value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if isinstance(effective_structured_value, dict)
                    and effective_structured_value
                    else ""
                )
                overview = "\n".join(part for part in (prefix, content) if part)[:600]
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            diff_payload = {
                "after": content,
                "before": old_content,
                "changed_fields": ["content"] if content != old_content else [],
                "created_at": occurred_at,
                "memory_uri": memory_uri,
                "operation": operation,
                "operation_id": operation_id,
                "source_commit_ids": list(commit_ids),
                "version": version,
            }
            extra_fields = {
                "abstract": abstract,
                "agent_id": normalized["agent_id"],
                "confidence": effective_confidence,
                "content_hash": content_hash,
                "conversation_hash": normalized["conversation_hash"],
                "created_at": created_at,
                "evidence": merged_evidence,
                "importance": effective_importance,
                "last_diff": diff_payload,
                "last_operation_id": operation_id,
                "memory_id": memory_id,
                "memory_key": normalized["memory_key"],
                "overview": overview,
                "scope_hash": normalized["scope_hash"],
                "scope_type": normalized["scope_type"],
                "source_commit_ids": list(commit_ids),
                "status": effective_status,
                "structured_value": effective_structured_value,
                "subject_hash": normalized["subject_hash"],
                "updated_at": occurred_at,
                "valid_from": effective_valid_from,
                "valid_until": effective_valid_until,
                "version": version,
            }
            memory_file = MemoryFile(
                uri=memory_uri,
                content=content,
                links=links,
                backlinks=old_file.backlinks if old_file is not None else [],
                memory_type=str(normalized["memory_type"]),
                extra_fields=extra_fields,
            )
            transaction.atomic_write(memory_path, MemoryFileUtils.write(memory_file))
            transaction.atomic_write(
                diff_path,
                f"{json.dumps(diff_payload, ensure_ascii=True, indent=2, sort_keys=True)}\n",
            )

        return MemoryUpsertResult(
            operation_id=operation_id,
            memory_uri=memory_uri,
            operation=operation,
            version=version,
            duplicate=False,
        )

    def _normalize_memory_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        agent_id = normalize_identifier_part(
            str(candidate.get("agent_id") or "default").strip() or "default",
            "agent_id",
        )
        memory_type = normalize_identifier_part(
            str(candidate.get("memory_type") or "").strip(), "memory_type"
        )
        if agent_id is None or memory_type is None:
            raise ValueError("OpenViking memory agent and type are required")
        scope_type = str(candidate.get("scope_type") or "").strip()
        if scope_type not in _SCOPE_TYPES:
            raise ValueError("unsupported OpenViking scope type")
        scope_hash = self._require_digest(candidate.get("scope_hash"), "scope hash")
        conversation_hash = self._require_digest(
            candidate.get("conversation_hash"), "conversation hash"
        )
        raw_subject_hash = str(candidate.get("subject_hash") or "").strip()
        subject_hash = (
            self._require_digest(raw_subject_hash, "subject hash")
            if raw_subject_hash
            else ""
        )
        memory_key = str(candidate.get("memory_key") or "").strip()[:256]
        content = str(candidate.get("content") or "").strip()[:20_000]
        if not memory_key or not content:
            raise ValueError("OpenViking memory key and content are required")
        try:
            confidence = min(1.0, max(0.0, float(candidate.get("confidence") or 0.0)))
            importance = min(1.0, max(0.0, float(candidate.get("importance") or 0.0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("OpenViking memory scores must be numeric") from exc
        status = str(candidate.get("status") or "candidate").strip().lower()
        if status not in {"active", "candidate", "rejected", "superseded"}:
            raise ValueError("unsupported OpenViking memory status")
        occurred_at = str(candidate.get("occurred_at") or "").strip()
        try:
            if occurred_at:
                datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            else:
                raise ValueError
        except ValueError:
            occurred_at = datetime.now(UTC).isoformat()
        structured_value = candidate.get("structured_value")
        if not isinstance(structured_value, dict):
            structured_value = {}
        return {
            "abstract": str(candidate.get("abstract") or "")[:500],
            "agent_id": agent_id,
            "confidence": confidence,
            "content": content,
            "conversation_hash": conversation_hash,
            "importance": importance,
            "memory_key": memory_key,
            "memory_type": memory_type,
            "occurred_at": occurred_at,
            "overview": str(candidate.get("overview") or "")[:2_000],
            "scope_hash": scope_hash,
            "scope_type": scope_type,
            "status": status,
            "structured_value": structured_value,
            "subject_hash": subject_hash,
            "valid_from": str(candidate.get("valid_from") or "")[:64],
            "valid_until": str(candidate.get("valid_until") or "")[:64],
        }

    @staticmethod
    def _require_digest(value: Any, field_name: str) -> str:
        digest = str(value or "").strip().lower()
        if not _DIGEST_PATTERN.fullmatch(digest):
            raise ValueError(f"OpenViking {field_name} must be a SHA-256 digest")
        return digest
