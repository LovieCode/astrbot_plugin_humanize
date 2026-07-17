"""AstrBot-facing adapter for embedded OpenViking session and memory semantics."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..vendor.openviking_core.core.identifiers import normalize_identifier_part
from ..vendor.openviking_core.message import Message, TextPart
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

    @staticmethod
    def _require_digest(value: Any, field_name: str) -> str:
        digest = str(value or "").strip().lower()
        if not _DIGEST_PATTERN.fullmatch(digest):
            raise ValueError(f"OpenViking {field_name} must be a SHA-256 digest")
        return digest
