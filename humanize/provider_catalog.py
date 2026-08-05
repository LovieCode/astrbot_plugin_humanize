from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any

from .provider_observability import provider_identity


class ProviderCatalog:
    """Discover live chat Providers without performing paid health checks."""

    def __init__(self, context: Any | None) -> None:
        self._context = context

    async def list_chat_providers(self) -> dict[str, Any]:
        """Return Chat Provider cache metadata without paid capability probes.

        Returns:
            Provider discovery state and declared prompt-cache capability.
        """
        context = self._context
        getter = getattr(context, "get_all_providers", None)
        if not callable(getter):
            return {"state": "not_initialized", "providers": []}
        try:
            raw = getter()
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:
            return {
                "state": "error",
                "providers": [],
                "error": type(exc).__name__,
            }
        if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
            return {"state": "ready", "providers": []}
        providers: list[dict[str, Any]] = []
        for provider in raw:
            try:
                identity = provider_identity(provider)
                providers.append(
                    {
                        "id": identity.get("provider_id", ""),
                        "adapter": identity.get("provider_type", ""),
                        "model": identity.get("model", "") or None,
                        "model_revision": identity.get("model_revision", "") or None,
                        "capability": identity.get(
                            "prompt_cache_capability", "unknown"
                        ),
                    }
                )
            except Exception:
                continue
        providers.sort(key=lambda item: (str(item["id"]), str(item["adapter"])))
        return {"state": "ready", "providers": providers}

    async def list_memory_providers(self) -> dict[str, Any]:
        """Return local Provider choices without making inference requests.

        Returns:
            Chat, Embedding, and Rerank Provider metadata safe for settings UI.
        """
        context = self._context
        if context is None:
            return {
                "state": "not_initialized",
                "chat": [],
                "embedding": [],
                "rerank": [],
            }
        groups: dict[str, list[dict[str, Any]]] = {
            "chat": [],
            "embedding": [],
            "rerank": [],
        }
        getters = {
            "chat": getattr(context, "get_all_providers", None),
            "embedding": getattr(context, "get_all_embedding_providers", None),
        }
        try:
            for key, getter in getters.items():
                if not callable(getter):
                    continue
                raw = getter()
                if inspect.isawaitable(raw):
                    raw = await raw
                if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
                    continue
                groups[key] = self._provider_options(raw)
            manager = getattr(context, "provider_manager", None)
            raw_rerank = getattr(manager, "rerank_provider_insts", ())
            if isinstance(raw_rerank, Iterable) and not isinstance(
                raw_rerank, (str, bytes)
            ):
                groups["rerank"] = self._provider_options(raw_rerank)
        except Exception as exc:
            return {
                "state": "error",
                **groups,
                "error": type(exc).__name__,
            }
        return {"state": "ready", **groups}

    async def list_memory_personas(self) -> dict[str, Any]:
        """Return AstrBot personas that can own memory and reply examples.

        Returns:
            Persona discovery state, the configured default persona ID, and safe
            persona labels without exposing prompt content.
        """
        context = self._context
        manager = getattr(context, "persona_manager", None)
        if manager is None:
            return {
                "state": "not_initialized",
                "default_id": "default",
                "items": [
                    {
                        "id": "default",
                        "label": "默认人格",
                        "source": "fallback",
                    }
                ],
            }

        selected_default = getattr(manager, "selected_default_persona_v3", None)
        default_id = ""
        if isinstance(selected_default, dict):
            default_id = str(selected_default.get("name") or "").strip()
        if not default_id:
            default_id = str(getattr(manager, "default_persona", "") or "").strip()
        default_id = default_id or "default"

        personas: dict[str, dict[str, str]] = {}
        raw_v3 = getattr(manager, "personas_v3", ())
        if isinstance(raw_v3, Iterable) and not isinstance(raw_v3, (str, bytes)):
            for persona in raw_v3:
                if not isinstance(persona, dict):
                    continue
                persona_id = str(persona.get("name") or "").strip()
                if persona_id:
                    personas[persona_id] = {
                        "id": persona_id,
                        "label": "默认人格" if persona_id == "default" else persona_id,
                        "source": "astrbot_persona",
                    }

        getter = getattr(manager, "get_all_personas", None)
        discovery_error = ""
        if callable(getter):
            try:
                raw = getter()
                if inspect.isawaitable(raw):
                    raw = await raw
                if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
                    for persona in raw:
                        persona_id = str(
                            getattr(persona, "persona_id", "") or ""
                        ).strip()
                        if persona_id:
                            personas.setdefault(
                                persona_id,
                                {
                                    "id": persona_id,
                                    "label": (
                                        "默认人格"
                                        if persona_id == "default"
                                        else persona_id
                                    ),
                                    "source": "astrbot_persona",
                                },
                            )
            except Exception as exc:
                discovery_error = type(exc).__name__

        personas.setdefault(
            default_id,
            {
                "id": default_id,
                "label": "默认人格" if default_id == "default" else default_id,
                "source": "astrbot_default",
            },
        )
        items = sorted(
            personas.values(),
            key=lambda item: (
                0 if item["id"] == default_id else 1,
                str(item["label"]).casefold(),
            ),
        )
        result: dict[str, Any] = {
            "state": "error" if discovery_error else "ready",
            "default_id": default_id,
            "items": items,
        }
        if discovery_error:
            result["error"] = discovery_error
        return result

    @staticmethod
    def _provider_options(raw: Iterable[Any]) -> list[dict[str, Any]]:
        """Normalize Provider metadata while excluding keys and credentials."""
        items: list[dict[str, Any]] = []
        for provider in raw:
            try:
                meta = provider.meta()
                model = str(getattr(meta, "model", "") or "") or None
                if not model:
                    provider_config = getattr(provider, "provider_config", {}) or {}
                    model = str(provider_config.get("embedding_model") or "") or None
                items.append(
                    {
                        "id": str(getattr(meta, "id", "") or ""),
                        "adapter": str(getattr(meta, "type", "") or ""),
                        "model": model,
                        "provider_type": str(
                            getattr(getattr(meta, "provider_type", None), "value", "")
                            or ""
                        ),
                    }
                )
            except Exception:
                continue
        items.sort(key=lambda item: (str(item["id"]), str(item["adapter"])))
        return items
