"""Administrative views and mutations for embedded OpenViking memories."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..vendor.openviking_core.session.memory.utils.memory_file_utils import (
    MemoryFileUtils,
)
from .adapter import OpenVikingMemoryAdapter, normalize_openviking_agent_id
from .workspace import OpenVikingWorkspace, WorkspaceTransaction

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SCOPE_TYPES = {"global", "private_user", "group", "group_member"}
_STATUSES = {"active", "candidate", "rejected", "superseded"}


class OpenVikingManagementAdapter:
    """Expose bounded OpenViking memory administration without legacy tables."""

    def __init__(
        self,
        memory_adapter: OpenVikingMemoryAdapter,
        workspace: OpenVikingWorkspace,
    ) -> None:
        """Bind management operations to the runtime memory workspace.

        Args:
            memory_adapter: Initialized OpenViking write adapter.
            workspace: Controlled workspace shared by recall and management.
        """
        self._memory = memory_adapter
        self._workspace = workspace

    def get_overview(self) -> dict[str, Any]:
        """Return memory counts and anonymized scope options.

        Returns:
            WebUI-compatible local memory overview.
        """
        items = self._read_items()
        counts = dict.fromkeys(sorted(_STATUSES), 0)
        scopes: dict[tuple[str, str, str], dict[str, str]] = {}
        for item in items:
            status = str(item["status"])
            counts[status] = counts.get(status, 0) + 1
            key = (
                str(item["scope_type"]),
                str(item["scope_hash"]),
                str(item["subject_hash"]),
            )
            scopes[key] = {
                "scope_type": key[0],
                "scope_hash": key[1],
                "subject_hash": key[2],
            }
        return {
            "memories": {"by_status": counts, "total": len(items)},
            "retrieval": {
                "engine": "openviking",
                "fts5_available": False,
                "index_generation": "workspace",
            },
            "scope_options": list(scopes.values()),
        }

    def list_memories(self, **filters: Any) -> dict[str, Any]:
        """Return a filtered, paginated OpenViking memory list.

        Args:
            **filters: Search, identity, status, type, and pagination values.

        Returns:
            WebUI-compatible page containing stable string memory IDs.
        """
        page = max(1, int(filters.get("page", 1)))
        page_size = max(1, min(int(filters.get("page_size", 50)), 200))
        search = str(filters.get("search") or filters.get("query") or "").strip()
        expected = {
            key: str(filters.get(key) or "").strip()
            for key in (
                "agent_id",
                "memory_type",
                "scope_type",
                "scope_hash",
                "subject_hash",
                "status",
            )
        }
        if not expected["memory_type"]:
            expected["memory_type"] = str(filters.get("type") or "").strip()
        if expected["agent_id"]:
            expected["agent_id"] = normalize_openviking_agent_id(
                expected["agent_id"]
            )
        items = []
        for item in self._read_items():
            if any(
                expected[key] and str(item.get(key) or "") != expected[key]
                for key in expected
            ):
                continue
            if (
                search
                and search.casefold()
                not in "\n".join(
                    (
                        str(item.get("memory_key") or ""),
                        str(item.get("content") or ""),
                        json.dumps(
                            item.get("structured_value") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                ).casefold()
            ):
                continue
            public_item = dict(item)
            public_item.pop("_path", None)
            public_item.pop("evidence", None)
            items.append(public_item)
        items.sort(
            key=lambda item: (
                str(item.get("updated_at") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        offset = (page - 1) * page_size
        return {
            "items": items[offset : offset + page_size],
            "page": page,
            "page_size": page_size,
            "total": len(items),
        }

    def get_memory_detail(self, memory_id: str) -> dict[str, Any] | None:
        """Return one OpenViking memory with evidence, revisions, and audit.

        Args:
            memory_id: Stable SHA-256 memory identifier.

        Returns:
            Complete administrative detail, or ``None`` when absent.

        Raises:
            ValueError: If the identifier is malformed.
        """
        clean_id = self._require_digest(memory_id, "memory id")
        item = next(
            (value for value in self._read_items() if value["id"] == clean_id),
            None,
        )
        if item is None:
            return None
        evidence = list(item.pop("evidence", []))
        revisions: list[dict[str, Any]] = []
        audit: list[dict[str, Any]] = []
        memory_uri = str(item["uri"])
        memory_path = Path(str(item.pop("_path")))
        with self._workspace.transaction() as transaction:
            history_manifest_path = (
                memory_path.parent / f"{clean_id}.history" / "manifest.json"
            )
            if transaction.is_file(history_manifest_path):
                manifest = self._read_json(transaction, history_manifest_path)
                history_evidence: list[dict[str, Any]] = []
                for raw_path in manifest.get("evidence_pages", []):
                    if isinstance(raw_path, str) and raw_path:
                        value = self._read_json(transaction, Path(raw_path))
                        if value:
                            history_evidence.append(value)
                if history_evidence:
                    evidence = history_evidence
                for raw_path in manifest.get("revision_pages", []):
                    if isinstance(raw_path, str) and raw_path:
                        value = self._read_json(transaction, Path(raw_path))
                        if value:
                            revisions.append(value)
            for path in transaction.list_files_recursive(
                "memory_diffs", suffix=".json", limit=10_000
            ):
                value = self._read_json(transaction, path)
                if value.get("memory_uri") == memory_uri:
                    revisions.append(value)
            for path in transaction.list_files_recursive(
                "memory_admin", suffix=".json", limit=10_000
            ):
                value = self._read_json(transaction, path)
                if value.get("memory_uri") == memory_uri:
                    audit.append(value)
        revisions.sort(
            key=lambda value: (
                int(value.get("version") or value.get("revision") or 0),
                str(value.get("created_at") or ""),
            ),
            reverse=True,
        )
        audit.sort(key=lambda value: str(value.get("created_at") or ""), reverse=True)
        return {
            **item,
            "audit": audit,
            "evidence": evidence,
            "revisions": revisions,
        }

    def apply_memory_action(
        self,
        payload: dict[str, Any],
        *,
        actor: str = "web_admin",
    ) -> dict[str, Any]:
        """Create or mutate one OpenViking memory with anonymous audit metadata.

        Args:
            payload: Validated WebUI mutation payload with HMAC scope identity.
            actor: Bounded administrative actor label.

        Returns:
            Updated OpenViking memory detail.

        Raises:
            KeyError: If the target memory does not exist.
            ValueError: If identity, revision, action, or content is invalid.
        """
        action = str(payload.get("action") or "update").strip().lower()
        if action not in {
            "activate",
            "approve",
            "confirm",
            "create",
            "delete",
            "reject",
            "save",
            "update",
        }:
            raise ValueError("unsupported OpenViking memory action")
        existing: dict[str, Any] | None = None
        raw_id = str(payload.get("id") or "").strip().lower()
        if action != "create":
            existing = self.get_memory_detail(raw_id)
            if existing is None:
                raise KeyError("OpenViking memory does not exist")
            expected_revision = int(payload.get("revision") or 0)
            if expected_revision and expected_revision != int(existing["version"]):
                raise ValueError("OpenViking memory revision conflict")

        identity: dict[str, str] = {}
        for key in (
            "agent_id",
            "memory_key",
            "memory_type",
            "scope_hash",
            "scope_type",
            "subject_hash",
        ):
            incoming = payload.get("type") if key == "memory_type" else payload.get(key)
            identity[key] = str(
                incoming
                if incoming not in (None, "")
                else (existing or {}).get(key, "")
            ).strip()
        identity["agent_id"] = identity["agent_id"] or "default"
        if identity["scope_type"] not in _SCOPE_TYPES:
            raise ValueError("unsupported OpenViking memory scope type")
        identity["scope_hash"] = self._require_digest(
            identity["scope_hash"], "scope hash"
        )
        if identity["subject_hash"]:
            identity["subject_hash"] = self._require_digest(
                identity["subject_hash"], "subject hash"
            )
        if existing is not None and any(
            identity[key] != str(existing.get(key) or "") for key in identity
        ):
            raise ValueError("OpenViking memory identity is immutable")

        content = str(
            payload.get("content")
            if payload.get("content") is not None
            else (existing or {}).get("content", "")
        ).strip()[:20_000]
        if not identity["memory_key"] or not content:
            raise ValueError("OpenViking memory key and content are required")
        status = str(
            payload.get("status") or (existing or {}).get("status") or "candidate"
        )
        if action in {"activate", "approve", "confirm"}:
            status = "active"
        elif action in {"delete", "reject"}:
            status = "rejected"
        if status not in _STATUSES:
            raise ValueError("unsupported OpenViking memory status")
        try:
            confidence = float(
                payload.get("confidence", (existing or {}).get("confidence", 0.8))
            )
            importance = float(
                payload.get("importance", (existing or {}).get("importance", 0.5))
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("OpenViking memory scores must be numeric") from exc
        if not all(
            math.isfinite(value) and 0.0 <= value <= 1.0
            for value in (confidence, importance)
        ):
            raise ValueError("OpenViking memory scores must be between zero and one")
        structured_value = payload.get(
            "structured_value", (existing or {}).get("structured_value", {})
        )
        if not isinstance(structured_value, dict):
            structured_value = {}
        if action == "create":
            duplicate = next(
                (
                    item
                    for item in self._read_items()
                    if all(
                        str(item.get(key) or "") == value
                        for key, value in identity.items()
                    )
                ),
                None,
            )
            if duplicate is not None:
                if (
                    str(duplicate.get("content") or "") == content
                    and str(duplicate.get("status") or "") == status
                    and float(duplicate.get("confidence") or 0.0) == confidence
                    and float(duplicate.get("importance") or 0.0) == importance
                    and duplicate.get("structured_value") == structured_value
                ):
                    detail = self.get_memory_detail(str(duplicate["id"]))
                    if detail is not None:
                        return detail
                raise ValueError("OpenViking memory identity already exists")
        now = datetime.now(UTC).isoformat()
        operation_material = json.dumps(
            {
                "action": action,
                "content": content,
                "identity": identity,
                "revision": int((existing or {}).get("version") or 0),
                "status": status,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        commit_id = hashlib.sha256(operation_material.encode()).hexdigest()
        conversation_hash = hashlib.sha256(
            (
                "openviking-admin-v1\0"
                f"{identity['agent_id']}\0{identity['scope_type']}\0"
                f"{identity['scope_hash']}\0{identity['subject_hash']}"
            ).encode()
        ).hexdigest()
        commit = self._memory.commit_turn(
            {
                "action": "No Reply",
                "agent_id": identity["agent_id"],
                "assistant_messages": [],
                "conversation_hash": conversation_hash,
                "idempotency_key": commit_id,
                "occurred_at": now,
                "scope_hash": identity["scope_hash"],
                "scope_type": identity["scope_type"],
                "source_complete": True,
                "subject_hash": identity["subject_hash"],
                "user_text": content,
            }
        )
        result = self._memory.upsert_memory(
            {
                **identity,
                "confidence": confidence,
                "content": content,
                "conversation_hash": conversation_hash,
                "importance": importance,
                "occurred_at": now,
                "status": status,
                "structured_value": structured_value,
                "valid_from": str(
                    payload.get("valid_from", (existing or {}).get("valid_from", ""))
                    or ""
                ),
                "valid_until": str(
                    payload.get("valid_until", (existing or {}).get("valid_until", ""))
                    or ""
                ),
            },
            evidence=[
                {
                    "occurred_at": now,
                    "quote": content,
                    "source_complete": True,
                }
            ],
            source_commit_ids=(commit.commit_id,),
            force_replace=True,
        )
        audit = {
            "action": action,
            "actor": str(actor or "web_admin")[:120],
            "after_hash": hashlib.sha256(content.encode()).hexdigest(),
            "before_hash": hashlib.sha256(
                str((existing or {}).get("content") or "").encode()
            ).hexdigest(),
            "created_at": now,
            "memory_uri": result.memory_uri,
            "operation_id": result.operation_id,
            "reason": str(payload.get("reason") or "")[:500],
            "status": status,
            "version": result.version,
        }
        with self._workspace.transaction() as transaction:
            transaction.atomic_write(
                Path("memory_admin") / f"{result.operation_id}.json",
                f"{json.dumps(audit, ensure_ascii=True, indent=2, sort_keys=True)}\n",
            )
        memory_id = result.memory_uri.rsplit("/", 1)[-1]
        detail = self.get_memory_detail(memory_id)
        if detail is None:
            raise RuntimeError("OpenViking memory mutation could not be read back")
        return detail

    def _read_items(self) -> list[dict[str, Any]]:
        """Read validated memory files into bounded administrative rows.

        Returns:
            Valid memory rows; corrupt or path-inconsistent files are omitted.
        """
        items: list[dict[str, Any]] = []
        with self._workspace.transaction() as transaction:
            paths = transaction.list_files_recursive(
                "memories", suffix=".md", limit=10_000
            )
            for path in paths:
                if len(path.parts) != 7 or path.parts[0] != "memories":
                    continue
                _, agent_id, scope_type, scope_hash, subject_segment, memory_type, _ = (
                    path.parts
                )
                memory_id = path.stem
                subject_hash = "" if subject_segment == "global" else subject_segment
                if (
                    scope_type not in _SCOPE_TYPES
                    or not _DIGEST_PATTERN.fullmatch(scope_hash)
                    or subject_hash
                    and not _DIGEST_PATTERN.fullmatch(subject_hash)
                    or not _DIGEST_PATTERN.fullmatch(memory_id)
                ):
                    continue
                uri = (
                    f"viking://agent/{agent_id}/memories/{scope_type}/{scope_hash}/"
                    f"{subject_segment}/{memory_type}/{memory_id}"
                )
                try:
                    memory = MemoryFileUtils.read(
                        transaction.read_bytes(path).decode("utf-8"), uri=uri
                    )
                except (OSError, UnicodeDecodeError, ValueError):
                    continue
                fields = memory.extra_fields
                if any(
                    str(fields.get(key) or "") != expected
                    for key, expected in (
                        ("agent_id", agent_id),
                        ("memory_id", memory_id),
                        ("scope_hash", scope_hash),
                        ("scope_type", scope_type),
                        ("subject_hash", subject_hash),
                    )
                ):
                    continue
                status = str(fields.get("status") or "")
                if status not in _STATUSES:
                    continue
                items.append(
                    {
                        "_path": path.as_posix(),
                        "agent_id": agent_id,
                        "confidence": float(fields.get("confidence") or 0.0),
                        "content": str(memory.content or ""),
                        "created_at": str(fields.get("created_at") or ""),
                        "evidence": (
                            fields.get("evidence")
                            if isinstance(fields.get("evidence"), list)
                            else []
                        ),
                        "evidence_count": len(fields.get("evidence") or []),
                        "id": memory_id,
                        "importance": float(fields.get("importance") or 0.0),
                        "memory_id": memory_id,
                        "memory_key": str(fields.get("memory_key") or ""),
                        "memory_type": memory_type,
                        "scope_hash": scope_hash,
                        "scope_type": scope_type,
                        "status": status,
                        "structured_value": (
                            fields.get("structured_value")
                            if isinstance(fields.get("structured_value"), dict)
                            else {}
                        ),
                        "subject_hash": subject_hash,
                        "updated_at": str(fields.get("updated_at") or ""),
                        "uri": uri,
                        "valid_from": str(fields.get("valid_from") or ""),
                        "valid_until": str(fields.get("valid_until") or ""),
                        "version": max(1, int(fields.get("version") or 1)),
                    }
                )
        return items

    @staticmethod
    def _read_json(transaction: WorkspaceTransaction, path: Path) -> dict[str, Any]:
        """Read one workspace JSON object without leaking parse failures.

        Args:
            transaction: Active locked workspace transaction.
            path: Validated workspace-relative path.

        Returns:
            Parsed object, or an empty mapping for invalid content.
        """
        try:
            value = json.loads(transaction.read_bytes(path).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _require_digest(value: Any, field_name: str) -> str:
        """Validate a lowercase SHA-256 identifier.

        Args:
            value: Candidate identifier.
            field_name: Safe field label for errors.

        Returns:
            Validated digest.

        Raises:
            ValueError: If the identifier is malformed.
        """
        digest = str(value or "").strip().lower()
        if not _DIGEST_PATTERN.fullmatch(digest):
            raise ValueError(f"OpenViking {field_name} must be a SHA-256 digest")
        return digest
