"""Idempotent migration from legacy Humanize memory rows to OpenViking files."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..vendor.openviking_core.core.identifiers import normalize_identifier_part
from ..vendor.openviking_core.session.memory import StoredLink
from ..vendor.openviking_core.session.memory.merge_op import merge_links
from ..vendor.openviking_core.session.memory.utils.memory_file_utils import (
    MemoryFileUtils,
)
from .adapter import OpenVikingMemoryAdapter
from .workspace import OpenVikingWorkspace, WorkspaceTransaction

_DIGEST_LENGTH = 64
_HISTORY_LINK_DESCRIPTION = "Migrated legacy memory history"
_CUTOVER_PATH = Path("migration/cutover.json")
_MAX_EVIDENCE = 1_000
_MAX_REVISIONS = 1_000
_SCOPE_TYPES = {"global", "private_user", "group", "group_member"}
_STATUS_MAP = {
    "active": "active",
    "candidate": "candidate",
    "rejected": "rejected",
    "superseded": "superseded",
    "tombstoned": "rejected",
}


@dataclass(frozen=True, slots=True)
class MigrationItemResult:
    """Outcome for one legacy memory row without exposing memory content."""

    legacy_id: int
    status: str
    memory_uri: str
    source_hash: str
    version: int
    verified: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class MigrationBatchResult:
    """Aggregate outcome for one bounded migration batch."""

    total: int
    migrated: int
    duplicates: int
    validated: int
    failed: int
    verified: int
    items: tuple[MigrationItemResult, ...]


@dataclass(frozen=True, slots=True)
class _LegacyRecord:
    legacy_id: int
    source_revision: int
    source_hash: str
    candidate: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    revisions: tuple[dict[str, Any], ...]
    aliases: tuple[str, ...]
    session_payload: dict[str, Any]
    memory_path: Path
    memory_uri: str
    history_root: Path
    history_uri: str


class OpenVikingMigrationAdapter:
    """Migrate trusted legacy rows while retaining the old database as rollback."""

    def __init__(
        self,
        memory_adapter: OpenVikingMemoryAdapter,
        workspace: OpenVikingWorkspace,
    ) -> None:
        """Bind migration to the same adapter and workspace used at runtime.

        Args:
            memory_adapter: Initialized OpenViking write adapter.
            workspace: Initialized controlled workspace.
        """
        self._memory = memory_adapter
        self._workspace = workspace

    def read_cutover_state(self) -> dict[str, Any]:
        """Read a validated anonymous authoritative-mode marker.

        Returns:
            Cutover metadata, or an empty mapping when absent or invalid.
        """
        with self._workspace.transaction() as transaction:
            if not transaction.is_file(_CUTOVER_PATH):
                return {}
            payload = self._read_json(transaction, _CUTOVER_PATH)
        source_digest = str(payload.get("source_digest") or "")
        try:
            total = int(payload.get("total"))
            verified = int(payload.get("verified"))
        except (TypeError, ValueError):
            return {}
        if (
            payload.get("format_version") != 1
            or payload.get("state") != "authoritative"
            or len(source_digest) != _DIGEST_LENGTH
            or any(character not in "0123456789abcdef" for character in source_digest)
            or total < 0
            or verified < 0
            or verified != total
        ):
            return {}
        return payload

    def activate_cutover(
        self,
        *,
        total: int,
        verified: int,
        source_digest: str,
        activated_at: str,
    ) -> dict[str, Any]:
        """Persist the verified switch from legacy memory rows to OpenViking.

        Args:
            total: Number of legacy snapshots inspected.
            verified: Number of snapshots verified in the workspace.
            source_digest: Aggregate digest of normalized source hashes.
            activated_at: UTC activation timestamp.

        Returns:
            Persisted anonymous cutover marker.

        Raises:
            ValueError: If counts or the aggregate digest are invalid.
        """
        clean_total = max(0, int(total))
        clean_verified = max(0, int(verified))
        clean_digest = str(source_digest or "").strip().lower()
        if clean_verified != clean_total:
            raise ValueError("OpenViking cutover requires every legacy row verified")
        if len(clean_digest) != _DIGEST_LENGTH or any(
            character not in "0123456789abcdef" for character in clean_digest
        ):
            raise ValueError("OpenViking cutover source digest is invalid")
        payload = {
            "activated_at": self._timestamp(activated_at),
            "format_version": 1,
            "source_digest": clean_digest,
            "state": "authoritative",
            "total": clean_total,
            "verified": clean_verified,
        }
        with self._workspace.transaction() as transaction:
            transaction.atomic_write(_CUTOVER_PATH, self._json_text(payload))
        return payload

    def migrate(
        self,
        records: Iterable[dict[str, Any]],
        *,
        dry_run: bool = False,
    ) -> MigrationBatchResult:
        """Validate or migrate a batch of complete legacy memory details.

        Args:
            records: Legacy rows including evidence and revision collections.
            dry_run: Validate mappings without writing Session or Memory files.

        Returns:
            Per-row outcomes and aggregate verified counts.
        """
        source_records = tuple(records)
        items: list[MigrationItemResult] = []
        for raw in source_records:
            legacy_id = 0
            try:
                if isinstance(raw, dict):
                    legacy_id = int(raw.get("id") or 0)
                record = self._normalize(raw)
                if dry_run:
                    items.append(
                        MigrationItemResult(
                            legacy_id=record.legacy_id,
                            status="validated",
                            memory_uri=record.memory_uri,
                            source_hash=record.source_hash,
                            version=record.source_revision,
                            verified=True,
                        )
                    )
                else:
                    items.append(self._migrate_one(record))
            except Exception as exc:
                items.append(
                    MigrationItemResult(
                        legacy_id=legacy_id,
                        status="failed",
                        memory_uri="",
                        source_hash="",
                        version=0,
                        verified=False,
                        error=type(exc).__name__,
                    )
                )
        return MigrationBatchResult(
            total=len(items),
            migrated=sum(item.status == "migrated" for item in items),
            duplicates=sum(item.status == "duplicate" for item in items),
            validated=sum(item.status == "validated" for item in items),
            failed=sum(item.status == "failed" for item in items),
            verified=sum(item.verified for item in items),
            items=tuple(items),
        )

    def _migrate_one(self, record: _LegacyRecord) -> MigrationItemResult:
        """Write and verify one normalized legacy record idempotently.

        Args:
            record: Fully normalized trusted source record.

        Returns:
            Verified migration or duplicate outcome.

        Raises:
            RuntimeError: If the written files cannot be verified.
        """
        manifest_path = record.history_root / "manifest.json"
        with self._workspace.transaction() as transaction:
            if transaction.is_file(manifest_path):
                manifest = self._read_json(transaction, manifest_path)
                if manifest.get(
                    "source_hash"
                ) == record.source_hash and self._verify_manifest(
                    transaction, record, manifest
                ):
                    return MigrationItemResult(
                        legacy_id=record.legacy_id,
                        status="duplicate",
                        memory_uri=record.memory_uri,
                        source_hash=record.source_hash,
                        version=int(manifest.get("memory_version") or 1),
                        verified=True,
                    )

        commit = self._memory.commit_turn(record.session_payload)
        upsert = self._memory.upsert_memory(
            record.candidate,
            evidence=list(record.evidence),
            source_commit_ids=(commit.commit_id,),
            force_replace=True,
        )
        evidence_pages: list[str] = []
        revision_pages: list[str] = []
        with self._workspace.transaction() as transaction:
            for evidence in record.evidence:
                page_hash = hashlib.sha256(
                    json.dumps(
                        evidence,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                page_path = record.history_root / "evidence" / f"{page_hash}.json"
                page_uri = f"{record.memory_uri}/evidence/{page_hash}"
                transaction.atomic_write(
                    page_path,
                    self._json_text(
                        {
                            "content_hash": page_hash,
                            "occurred_at": evidence["occurred_at"],
                            "quote": evidence["quote"],
                            "source_complete": evidence["source_complete"],
                            "uri": page_uri,
                        }
                    ),
                )
                evidence_pages.append(page_path.as_posix())
            for revision in record.revisions:
                revision_number = int(revision["revision"])
                page_path = (
                    record.history_root / "revisions" / f"{revision_number:06d}.json"
                )
                page_uri = f"{record.memory_uri}/revisions/{revision_number}"
                transaction.atomic_write(
                    page_path,
                    self._json_text({**revision, "uri": page_uri}),
                )
                revision_pages.append(page_path.as_posix())

            memory = MemoryFileUtils.read(
                transaction.read_bytes(record.memory_path).decode("utf-8"),
                uri=record.memory_uri,
            )
            expected_content_hash = hashlib.sha256(
                str(record.candidate["content"]).encode("utf-8")
            ).hexdigest()
            if (
                memory.content != record.candidate["content"]
                or memory.extra_fields.get("content_hash") != expected_content_hash
            ):
                raise RuntimeError("migrated OpenViking memory content mismatch")
            memory.extra_fields["migration_source"] = {
                "legacy_id": record.legacy_id,
                "source_hash": record.source_hash,
                "source_revision": record.source_revision,
            }
            existing_links = [
                link
                for link in memory.links
                if str(link.get("description") or "") != _HISTORY_LINK_DESCRIPTION
            ]
            history_link = StoredLink(
                from_uri=record.memory_uri,
                to_uri=record.history_uri,
                link_type="evolved_from",
                weight=1.0,
                description=_HISTORY_LINK_DESCRIPTION,
                created_at=str(record.candidate["occurred_at"]),
            ).model_dump()
            memory.links = merge_links(existing_links, [history_link])
            transaction.atomic_write(
                record.memory_path,
                MemoryFileUtils.write(memory),
            )
            manifest = {
                "aliases": list(record.aliases),
                "evidence_count": len(evidence_pages),
                "evidence_pages": evidence_pages,
                "format_version": 1,
                "history_uri": record.history_uri,
                "legacy_id": record.legacy_id,
                "memory_uri": record.memory_uri,
                "memory_version": upsert.version,
                "operation_id": upsert.operation_id,
                "revision_count": len(revision_pages),
                "revision_pages": revision_pages,
                "source_hash": record.source_hash,
                "source_revision": record.source_revision,
            }
            transaction.atomic_write(manifest_path, self._json_text(manifest))
            if not self._verify_manifest(transaction, record, manifest):
                raise RuntimeError("OpenViking migration verification failed")

        return MigrationItemResult(
            legacy_id=record.legacy_id,
            status="migrated",
            memory_uri=record.memory_uri,
            source_hash=record.source_hash,
            version=upsert.version,
            verified=True,
        )

    def _normalize(self, raw: dict[str, Any]) -> _LegacyRecord:
        """Normalize one complete legacy detail without preserving raw identifiers.

        Args:
            raw: Legacy memory detail from ``humanize.db``.

        Returns:
            Deterministic migration model and destination coordinates.

        Raises:
            TypeError: If the source is not an object.
            ValueError: If identity, scores, or required content are invalid.
        """
        if not isinstance(raw, dict):
            raise TypeError("legacy memory record must be an object")
        legacy_id = int(raw.get("id") or 0)
        if legacy_id <= 0:
            raise ValueError("legacy memory id must be positive")
        agent_id = normalize_identifier_part(
            str(raw.get("agent_id") or "default").strip() or "default",
            "agent_id",
        )
        memory_type = normalize_identifier_part(
            str(raw.get("memory_type") or raw.get("type") or "").strip(),
            "memory_type",
        )
        if agent_id is None or memory_type is None:
            raise ValueError("legacy memory Agent and type are required")
        scope_type = str(raw.get("scope_type") or "").strip()
        if scope_type not in _SCOPE_TYPES:
            raise ValueError("legacy memory scope type is unsupported")
        scope_hash = self._digest(raw.get("scope_hash"), "scope hash")
        raw_subject = str(raw.get("subject_hash") or "").strip()
        subject_hash = self._digest(raw_subject, "subject hash") if raw_subject else ""
        memory_key = str(raw.get("memory_key") or "").strip()[:256]
        content = str(raw.get("canonical_text") or raw.get("content") or "").strip()[
            :20_000
        ]
        if not memory_key or not content:
            raise ValueError("legacy memory key and content are required")
        confidence = self._score(raw.get("confidence"), "confidence")
        importance = self._score(raw.get("importance", 0.5), "importance")
        status = _STATUS_MAP.get(str(raw.get("status") or "candidate").lower())
        if status is None:
            raise ValueError("legacy memory status is unsupported")
        structured_value = raw.get("structured_value", raw.get("structured_value_json"))
        if isinstance(structured_value, str):
            try:
                structured_value = json.loads(structured_value)
            except json.JSONDecodeError:
                structured_value = {}
        if not isinstance(structured_value, dict):
            structured_value = {}
        occurred_at = self._timestamp(raw.get("updated_at") or raw.get("created_at"))
        valid_from = str(raw.get("valid_from") or "").strip()[:64]
        valid_until = str(raw.get("valid_until") or "").strip()[:64]
        for field_name, timestamp in (
            ("valid_from", valid_from),
            ("valid_until", valid_until),
        ):
            if not timestamp:
                continue
            try:
                datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"legacy memory {field_name} is invalid") from exc
        source_revision = max(1, int(raw.get("revision") or 1))

        raw_evidence = raw.get("evidence", [])
        if not isinstance(raw_evidence, list) or len(raw_evidence) > _MAX_EVIDENCE:
            raise ValueError("legacy memory evidence collection is invalid")
        evidence: list[dict[str, Any]] = []
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("excerpt") or item.get("quote") or "").strip()[:2_000]
            if not quote:
                continue
            evidence.append(
                {
                    "occurred_at": self._timestamp(
                        item.get("observed_at")
                        or item.get("occurred_at")
                        or item.get("created_at")
                        or occurred_at
                    ),
                    "quote": quote,
                    "source_complete": bool(item.get("source_complete", True)),
                }
            )

        raw_revisions = raw.get("revisions", [])
        if not isinstance(raw_revisions, list) or len(raw_revisions) > _MAX_REVISIONS:
            raise ValueError("legacy memory revision collection is invalid")
        revisions: list[dict[str, Any]] = []
        seen_revisions: set[int] = set()
        for item in raw_revisions:
            if not isinstance(item, dict):
                continue
            revision_number = int(item.get("revision") or 0)
            if revision_number <= 0 or revision_number in seen_revisions:
                raise ValueError("legacy memory revisions are not unique")
            seen_revisions.add(revision_number)
            snapshot = item.get("snapshot", {})
            if not isinstance(snapshot, dict):
                snapshot = {}
            revisions.append(
                {
                    "action": str(item.get("action") or "update")[:80],
                    "actor": str(item.get("actor") or "legacy")[:120],
                    "created_at": self._timestamp(
                        item.get("created_at") or occurred_at
                    ),
                    "reason": str(item.get("reason") or "")[:500],
                    "revision": revision_number,
                    "snapshot": self._snapshot(snapshot),
                }
            )
        revisions.sort(key=lambda item: int(item["revision"]))
        raw_aliases = raw.get("aliases", [])
        if not isinstance(raw_aliases, (list, tuple)):
            raise ValueError("legacy memory aliases collection is invalid")
        aliases = tuple(
            dict.fromkeys(
                str(value).strip()[:256] for value in raw_aliases if str(value).strip()
            )
        )
        candidate = {
            "abstract": " ".join(content.split())[:160],
            "agent_id": agent_id,
            "confidence": confidence,
            "content": content,
            "importance": importance,
            "memory_key": memory_key,
            "memory_type": memory_type,
            "occurred_at": occurred_at,
            "overview": content[:600],
            "scope_hash": scope_hash,
            "scope_type": scope_type,
            "status": status,
            "structured_value": structured_value,
            "subject_hash": subject_hash,
            "valid_from": valid_from,
            "valid_until": valid_until,
        }
        source_material = {
            "aliases": aliases,
            "candidate": candidate,
            "evidence": evidence,
            "legacy_id": legacy_id,
            "revisions": revisions,
            "source_revision": source_revision,
        }
        source_hash = hashlib.sha256(
            json.dumps(
                source_material,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        conversation_hash = hashlib.sha256(
            (
                f"openviking-migration-v1\0{agent_id}\0{scope_type}\0"
                f"{scope_hash}\0{subject_hash}\0{legacy_id}"
            ).encode()
        ).hexdigest()
        commit_id = hashlib.sha256(
            f"openviking-migration-v1\0{legacy_id}\0{source_hash}".encode()
        ).hexdigest()
        candidate["conversation_hash"] = conversation_hash
        source_text = "\n".join(item["quote"] for item in evidence)[:8_000]
        session_payload = {
            "action": "No Reply",
            "agent_id": agent_id,
            "assistant_messages": [],
            "conversation_hash": conversation_hash,
            "idempotency_key": commit_id,
            "occurred_at": occurred_at,
            "scope_hash": scope_hash,
            "scope_type": scope_type,
            "source_complete": bool(evidence)
            and all(item["source_complete"] for item in evidence),
            "subject_hash": subject_hash,
            "user_text": source_text or content[:8_000],
        }
        identity = "\0".join(
            (
                agent_id,
                scope_type,
                scope_hash,
                subject_hash,
                memory_type,
                memory_key,
            )
        )
        memory_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        subject_segment = subject_hash or "global"
        memory_path = (
            Path("memories")
            / agent_id
            / scope_type
            / scope_hash
            / subject_segment
            / memory_type
            / f"{memory_id}.md"
        )
        memory_uri = (
            f"viking://agent/{agent_id}/memories/{scope_type}/{scope_hash}/"
            f"{subject_segment}/{memory_type}/{memory_id}"
        )
        history_root = memory_path.parent / f"{memory_id}.history"
        return _LegacyRecord(
            legacy_id=legacy_id,
            source_revision=source_revision,
            source_hash=source_hash,
            candidate=candidate,
            evidence=tuple(evidence),
            revisions=tuple(revisions),
            aliases=aliases,
            session_payload=session_payload,
            memory_path=memory_path,
            memory_uri=memory_uri,
            history_root=history_root,
            history_uri=f"{memory_uri}/history",
        )

    def _verify_manifest(
        self,
        transaction: WorkspaceTransaction,
        record: _LegacyRecord,
        manifest: dict[str, Any],
    ) -> bool:
        """Verify current memory content and every page named by a manifest.

        Args:
            transaction: Locked workspace transaction.
            record: Expected normalized source record.
            manifest: Parsed migration manifest.

        Returns:
            Whether the migration is complete and matches the source hash.
        """
        if (
            manifest.get("format_version") != 1
            or manifest.get("source_hash") != record.source_hash
            or manifest.get("memory_uri") != record.memory_uri
            or manifest.get("history_uri") != record.history_uri
            or manifest.get("legacy_id") != record.legacy_id
            or manifest.get("source_revision") != record.source_revision
            or manifest.get("aliases") != list(record.aliases)
            or not transaction.is_file(record.memory_path)
        ):
            return False
        try:
            memory = MemoryFileUtils.read(
                transaction.read_bytes(record.memory_path).decode("utf-8"),
                uri=record.memory_uri,
            )
        except (OSError, UnicodeDecodeError, ValueError):
            return False
        expected_content_hash = hashlib.sha256(
            str(record.candidate["content"]).encode("utf-8")
        ).hexdigest()
        source = memory.extra_fields.get("migration_source")
        if (
            memory.content != record.candidate["content"]
            or memory.extra_fields.get("content_hash") != expected_content_hash
            or not isinstance(source, dict)
            or source.get("source_hash") != record.source_hash
            or source.get("legacy_id") != record.legacy_id
            or source.get("source_revision") != record.source_revision
        ):
            return False
        if not any(
            str(link.get("to_uri") or "") == record.history_uri
            and str(link.get("description") or "") == _HISTORY_LINK_DESCRIPTION
            for link in memory.links
        ):
            return False
        evidence_pages = manifest.get("evidence_pages", [])
        revision_pages = manifest.get("revision_pages", [])
        if (
            not isinstance(evidence_pages, list)
            or not isinstance(revision_pages, list)
            or manifest.get("evidence_count") != len(record.evidence)
            or manifest.get("revision_count") != len(record.revisions)
            or len(evidence_pages) != len(record.evidence)
            or len(revision_pages) != len(record.revisions)
        ):
            return False
        try:
            for raw_path, evidence in zip(evidence_pages, record.evidence, strict=True):
                if not isinstance(raw_path, str) or not raw_path:
                    return False
                path = Path(raw_path)
                if not transaction.is_file(path):
                    return False
                page = self._read_json(transaction, path)
                page_hash = hashlib.sha256(
                    json.dumps(
                        evidence,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                expected_path = record.history_root / "evidence" / f"{page_hash}.json"
                expected_page = {
                    "content_hash": page_hash,
                    "occurred_at": evidence["occurred_at"],
                    "quote": evidence["quote"],
                    "source_complete": evidence["source_complete"],
                    "uri": f"{record.memory_uri}/evidence/{page_hash}",
                }
                if path != expected_path or page != expected_page:
                    return False
            for raw_path, revision in zip(
                revision_pages, record.revisions, strict=True
            ):
                if not isinstance(raw_path, str) or not raw_path:
                    return False
                path = Path(raw_path)
                if not transaction.is_file(path):
                    return False
                page = self._read_json(transaction, path)
                revision_number = int(revision["revision"])
                expected_path = (
                    record.history_root / "revisions" / f"{revision_number:06d}.json"
                )
                expected_page = {
                    **revision,
                    "uri": f"{record.memory_uri}/revisions/{revision_number}",
                }
                if path != expected_path or page != expected_page:
                    return False
        except (OSError, ValueError):
            return False
        return True

    @staticmethod
    def _read_json(transaction: WorkspaceTransaction, path: Path) -> dict[str, Any]:
        """Read one JSON object from a locked workspace transaction.

        Args:
            transaction: Locked workspace transaction.
            path: Validated relative JSON path.

        Returns:
            Parsed object, or an empty object for invalid data.
        """
        try:
            value = json.loads(transaction.read_bytes(path).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        """Keep bounded revision fields while excluding unrelated database metadata.

        Args:
            snapshot: Legacy revision snapshot.

        Returns:
            JSON-compatible memory fields needed for rollback inspection.
        """
        result: dict[str, Any] = {}
        for key in (
            "agent_id",
            "canonical_text",
            "confidence",
            "content_hash",
            "created_at",
            "importance",
            "memory_key",
            "memory_type",
            "revision",
            "scope_hash",
            "scope_type",
            "status",
            "subject_hash",
            "updated_at",
            "valid_from",
            "valid_until",
        ):
            value = snapshot.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                result[key] = value
        structured = snapshot.get(
            "structured_value", snapshot.get("structured_value_json")
        )
        if isinstance(structured, str):
            try:
                structured = json.loads(structured)
            except json.JSONDecodeError:
                structured = {}
        result["structured_value"] = structured if isinstance(structured, dict) else {}
        return result

    @staticmethod
    def _digest(value: Any, field_name: str) -> str:
        """Validate one lowercase SHA-256 identity digest.

        Args:
            value: Candidate digest.
            field_name: Safe field label for errors.

        Returns:
            Normalized digest.

        Raises:
            ValueError: If the value is not a SHA-256 digest.
        """
        digest = str(value or "").strip().lower()
        if len(digest) != _DIGEST_LENGTH or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError(f"legacy memory {field_name} must be a SHA-256 digest")
        return digest

    @staticmethod
    def _score(value: Any, field_name: str) -> float:
        """Validate a finite score from zero to one.

        Args:
            value: Candidate numeric value.
            field_name: Safe field label for errors.

        Returns:
            Validated score.

        Raises:
            ValueError: If the score is invalid or out of range.
        """
        try:
            score = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"legacy memory {field_name} is invalid") from exc
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError(f"legacy memory {field_name} is invalid")
        return score

    @staticmethod
    def _timestamp(value: Any) -> str:
        """Normalize one timestamp with a deterministic migration fallback.

        Args:
            value: Candidate ISO-8601 timestamp.

        Returns:
            Valid ISO-8601 text.
        """
        timestamp = str(value or "").strip()
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return "1970-01-01T00:00:00+00:00"
        return timestamp

    @staticmethod
    def _json_text(value: dict[str, Any]) -> str:
        """Serialize deterministic UTF-8 JSON for atomic workspace writes.

        Args:
            value: JSON-compatible page payload.

        Returns:
            Stable indented JSON ending with a newline.
        """
        return f"{json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)}\n"
