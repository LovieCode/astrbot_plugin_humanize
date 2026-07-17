from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .provider_observability import canonical_json, fingerprint, first_difference


@dataclass(frozen=True, slots=True)
class PromptCacheObservation:
    """Describe one final ProviderRequest relative to its prior request."""

    epoch_id: str
    request_fingerprint: str
    prefix_fingerprint: str
    first_difference: str
    longest_common_prefix_chars: int
    epoch_reason: str


@dataclass(slots=True)
class _PromptState:
    epoch_id: str
    request_fields: Any
    request_prefix: str
    stable_fields: Any
    prefix_fingerprint: str


class PromptCacheTracker:
    """Track prompt-cache epochs without retaining persistent message bodies."""

    def __init__(self, repository: Any | None = None, *, max_states: int = 512) -> None:
        self._repository = repository
        self._max_states = max(16, int(max_states))
        self._states: OrderedDict[tuple[str, str, str], _PromptState] = OrderedDict()
        self._guard = asyncio.Lock()

    async def observe(
        self,
        *,
        scope_type: str,
        scope_id: str,
        conversation_id: str,
        request_fields: Any,
        prefix_fields: Any,
        stable_fields: Any,
    ) -> PromptCacheObservation:
        """Record one request and return bounded cache diagnostics.

        Args:
            scope_type: Private scope type.
            scope_id: Private scope identifier.
            conversation_id: AstrBot conversation identifier, if available.
            request_fields: Final provider-visible request fields.
            prefix_fields: Fields used for the provider prefix fingerprint.
            stable_fields: Static fields used to decide epoch rollover.

        Returns:
            Epoch, fingerprints and structural-diff diagnostics.  Only a compact
            bounded projection of request data is retained in process memory.
        """
        key = (
            str(scope_type or "")[:120],
            str(scope_id or "")[:300],
            str(conversation_id or scope_id or "")[:300],
        )
        request_projection = self._compact(request_fields)
        stable_projection = self._compact(stable_fields)
        try:
            request_prefix = canonical_json(request_fields)[:65_536]
        except (TypeError, ValueError):
            request_prefix = ""
        request_fp = fingerprint(
            request_fields, namespace="humanize-provider-request-v1"
        )
        prefix_fp = fingerprint(prefix_fields, namespace="humanize-provider-prefix-v1")
        async with self._guard:
            state = self._states.get(key)
            if state is None:
                persisted = await self._latest_persisted(key)
                if (
                    persisted is not None
                    and persisted.get("prefix_fingerprint") == prefix_fp
                ):
                    epoch_id = str(persisted.get("epoch_id") or uuid.uuid4().hex)
                    epoch_reason = "reload_same_prefix"
                else:
                    epoch_id = uuid.uuid4().hex
                    epoch_reason = (
                        "initial" if persisted is None else "reload_prefix_changed"
                    )
                difference = ""
                common_chars = 0
            else:
                difference = (
                    first_difference(state.request_fields, request_projection) or ""
                )
                common_chars = self._common_prefix_chars(
                    state.request_prefix, request_prefix
                )
                if state.stable_fields != stable_projection:
                    epoch_id = uuid.uuid4().hex
                    epoch_reason = "stable_prefix_changed"
                else:
                    epoch_id = state.epoch_id
                    epoch_reason = "same_epoch"
            self._states[key] = _PromptState(
                epoch_id=epoch_id,
                request_fields=request_projection,
                request_prefix=request_prefix,
                stable_fields=stable_projection,
                prefix_fingerprint=prefix_fp,
            )
            self._states.move_to_end(key)
            while len(self._states) > self._max_states:
                self._states.popitem(last=False)
        return PromptCacheObservation(
            epoch_id=epoch_id,
            request_fingerprint=request_fp,
            prefix_fingerprint=prefix_fp,
            first_difference=difference,
            longest_common_prefix_chars=common_chars,
            epoch_reason=epoch_reason,
        )

    async def _latest_persisted(
        self, key: tuple[str, str, str]
    ) -> dict[str, Any] | None:
        getter = getattr(self._repository, "get_latest_prompt_prefix_sample", None)
        if not callable(getter):
            return None
        try:
            row = await getter(
                scope_type=key[0], scope_id=key[1], conversation_id=key[2]
            )
        except Exception:
            return None
        return row if isinstance(row, dict) else None

    @staticmethod
    def _compact(value: Any, *, depth: int = 0) -> Any:
        """Keep only enough structure to identify a changed field path."""
        if depth >= 8:
            return {"__truncated__": type(value).__name__}
        if isinstance(value, dict):
            return {
                str(key): PromptCacheTracker._compact(item, depth=depth + 1)
                for key, item in sorted(value.items(), key=lambda item: str(item[0]))
            }
        if isinstance(value, (list, tuple)):
            return [
                PromptCacheTracker._compact(item, depth=depth + 1)
                for item in list(value)[:64]
            ]
        if isinstance(value, str):
            return value[:512] if len(value) > 512 else value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return {"__type__": type(value).__name__}

    @staticmethod
    def _common_prefix_chars(left_text: str, right_text: str) -> int:
        count = 0
        for left_char, right_char in zip(left_text, right_text):
            if left_char != right_char:
                break
            count += 1
        return count
