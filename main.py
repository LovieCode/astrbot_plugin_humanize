from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from collections.abc import Coroutine
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.builtin_stars.builtin_commands.commands.conversation import (
    ConversationCommands,
)
from astrbot.core.agent.message import TextPart
from astrbot.core.provider.provider import Provider as _ProviderBase

from .humanize.config import PluginConfig
from .humanize.container import Container, context_window_token_budget
from .humanize.domain.errors import ProtocolValidationError
from .humanize.domain.models import Action, EventState, MessageContext
from .humanize.image_cache import ImageCacheStore
from .humanize.prompt_cache import PromptCacheTracker
from .humanize.protocol.envelope import EnvelopeBuilder
from .humanize.protocol.parser import ProtocolParser
from .humanize.provider_observability import (
    fingerprint,
    provider_identity,
    usage_dict,
    usage_observed,
)
from .humanize.snapshots import (
    serialize_attachment_reference,
    serialize_llm_response,
    serialize_provider_request,
)

PLUGIN_NAME = "astrbot_plugin_humanize"
_STATE_KEY = "_humanize_state"
_CONTEXT_KEY = "_humanize_context"
_MESSAGES_KEY = "_humanize_messages"
_DISPATCHED_MESSAGES_KEY = "_humanize_dispatched_messages"
_START_KEY = "_humanize_started_at"
_MODEL_KEY = "_humanize_model"
_PROVIDER_ID_KEY = "_humanize_provider_id"
_PROVIDER_IDENTITY_KEY = "_humanize_provider_identity"
_ERROR_KEY = "_humanize_protocol_error"
_MESSAGE_XML_KEY = "_humanize_message_xml"
_ORIGINAL_PROMPT_KEY = "_humanize_original_prompt"
_WRAPPED_PROMPT_KEY = "_humanize_wrapped_prompt"
_RAW_OUTPUT_KEY = "_humanize_raw_output"
_VALIDATED_OUTPUT_KEY = "_humanize_validated_output"
_ASSISTANT_MESSAGE_KEY = "_humanize_assistant_message"
_HISTORY_SYNC_KEY = "_humanize_history_sync_required"
_CONTEXT_WINDOW_ACTIVE_KEY = "_humanize_context_window_active"
_CONTEXT_WINDOW_TOKEN_BUDGET_KEY = "_humanize_context_window_token_budget"
_CONTEXT_WINDOW_IMAGE_COUNT_KEY = "_humanize_context_window_image_count"
_CONTEXT_TURN_REF_KEY = "_humanize_context_turn_ref"
_CONTEXT_WINDOW_PENDING_MESSAGES_KEY = "_humanize_context_window_pending_messages"
_CONTEXT_WINDOW_PENDING_ACTION_KEY = "_humanize_context_window_pending_action"
_CONTEXT_READ_CALLS_KEY = "_humanize_context_read_calls"
_IMAGE_CACHE_KEY = "_humanize_image_cache"
_EVENT_IMAGE_CACHE_PATHS_KEY = "_humanize_event_image_cache_paths"
_EVENT_IMAGE_TRANSCRIPTIONS_KEY = "_humanize_event_image_transcriptions"
_TOOL_IMAGE_TRANSCRIPTIONS_KEY = "_humanize_tool_image_transcriptions"
_TOOL_HISTORY_KEY = "_humanize_tool_history_replacements"
_SEND_GATE_KEY = "_humanize_send_gate_installed"
_SEND_GATE_ERROR_KEY = "_humanize_send_gate_error"
_ORIGINAL_SEND_KEY = "_humanize_original_send"
_SEND_GATE_OWNER_KEY = "_humanize_send_gate_owner"
_REPAIR_PENDING_KEY = "_humanize_protocol_repair_pending"
_REPAIR_BODY_KEY = "_humanize_protocol_repair_body"
_REPAIR_ACTION_KEY = "_humanize_protocol_repair_action"
_REPAIR_ERROR_KEY = "_humanize_protocol_repair_error"
_REPAIR_ATTEMPTED_KEY = "_humanize_protocol_repair_attempted"
_NO_REPLY_REASON_KEY = "_humanize_no_reply_reason"
_FINAL_LOG_PENDING_KEY = "_humanize_final_protocol_log_pending"
_RESPONSE_SNAPSHOTS_KEY = "_humanize_response_snapshots"
_FINAL_RESPONSE_SNAPSHOT_KEY = "_humanize_final_response_snapshot"
_FINAL_DISPATCHED_KEY = "_humanize_final_response_dispatched"
_FINAL_DISPATCH_LOCK_KEY = "_humanize_final_dispatch_lock"
_FINAL_FAILURE_LOGGED_KEY = "_humanize_final_failure_logged"
_TOOL_SENT_MESSAGES_KEY = "_humanize_tool_sent_messages"
_TOOL_SENT_MEDIA_KEY = "_humanize_tool_sent_media"
_TOOL_PROCESSED_KEY = "_humanize_tool_processed_responses"
_TOOL_SEND_LOCK_KEY = "_humanize_tool_send_lock"
_REQUEST_FINGERPRINT_KEY = "_humanize_request_fingerprint"
_REQUEST_SNAPSHOT_KEY = "_humanize_request_snapshot"
_REQUEST_SNAPSHOT_COMPLETE_KEY = "_humanize_request_snapshot_complete"
_PREFIX_FINGERPRINT_KEY = "_humanize_prefix_fingerprint"
_PREFIX_EPOCH_KEY = "_humanize_prefix_epoch"
_PREFIX_FIRST_DIFFERENCE_KEY = "_humanize_prefix_first_difference"
_PREFIX_COMMON_CHARS_KEY = "_humanize_prefix_common_chars"
_PREFIX_EPOCH_REASON_KEY = "_humanize_prefix_epoch_reason"
_FIRST_RESPONSE_AT_KEY = "_humanize_first_response_at"
_PROVIDER_CAPTURE_TTL_SECONDS = 600.0
_PROVIDER_CAPTURE_MAX_ENTRIES = 256
_FIREWALL_PRIORITY = 100_000
_DISPATCH_PRIORITY = -1_000_000
_DECORATION_FINALIZER_PRIORITY = -1_000_001
_FINALIZER_PRIORITY = -100_000
_NO_REPLY_SENTINEL = " "
_CONTROL_TAG_PATTERN = re.compile(
    r"(?:<|&lt;)\s*/?\s*"
    r"(?:Action|UnknownTerms|ImageCache|Messages|Reply|Message)"
    r"(?=\s|/?>|&gt;)",
    re.IGNORECASE,
)

# 事件循环对任务只持弱引用：fire-and-forget 任务必须在此持强引用，
# 否则可能在完成前被 GC 回收（持久化静默丢失、异常无人接收）。
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _finish_background_task(task: asyncio.Task[None]) -> None:
    """Drop the strong reference and surface any unretrieved failure."""
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("[Humanize] background task %s failed: %s", task.get_name(), exc)


def _spawn_background(
    coro: Coroutine[Any, Any, None],
    *,
    name: str,
) -> asyncio.Task[None]:
    """Schedule a fire-and-forget task that survives garbage collection."""
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_finish_background_task)
    return task


def _json_safe(value: Any) -> Any:
    """Convert a runtime value into a JSON-serializable structure.

    Args:
        value: Arbitrary runtime value captured from a Provider payload.

    Returns:
        A JSON-compatible copy with dataclasses, pydantic models, and other
        non-serializable objects reduced to plain dict/list/scalar values.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _json_safe(dump(mode="json"))
        except TypeError:
            try:
                return _json_safe(dump())
            except Exception:
                return str(value)
        except Exception:
            return str(value)
    if hasattr(value, "__dict__"):
        return {str(key): _json_safe(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


class HumanizePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.context = context
        self._plugin_config = PluginConfig.from_mapping(config)
        self._container: Container | None = None
        self._protocol_parser = ProtocolParser(self._plugin_config)
        self._envelope_builder = EnvelopeBuilder(self._plugin_config)
        self._prompt_cache_tracker = PromptCacheTracker()
        # Provider 调用拦截：在真实请求发生处捕获完整上下文（含 persona/KB/
        # 文件/工具注入），这是最终快照的权威来源，不依赖 agent 钩子时序。
        self._provider_hooks_installed = False
        self._provider_originals: list[tuple] = []
        self._provider_capture: dict[str, dict[str, Any]] = {}
        # 协议修复频率监控：近端时间窗口内 repair 触发次数，用于向管理员告警
        self._repair_timestamps: list[float] = []
        self._repair_warned_at: float = 0.0

    async def initialize(self) -> None:
        self._container = Container.build(self._plugin_config, self.context)
        await self._container.repository.initialize()
        self._image_store = ImageCacheStore(
            self._plugin_config, self._container.repository
        )
        memory_initialized = False
        memory_config = getattr(self._container.memory, "_config", None)
        restore_memory_config = False
        try:
            if (
                isinstance(memory_config, PluginConfig)
                and not memory_config.memory_enabled
            ):
                # Identity hashes must remain stable while recall and the worker are
                # disabled. Temporarily bypass only the service's early return.
                setattr(
                    self._container.memory,
                    "_config",
                    replace(memory_config, memory_enabled=True),
                )
                restore_memory_config = True
            await self._container.memory.initialize()
            memory_initialized = True
        except Exception:
            logger.exception(
                "[Humanize] memory identity initialization failed; memory is disabled"
            )
        finally:
            if restore_memory_config:
                setattr(self._container.memory, "_config", memory_config)
                setattr(self._container.memory, "_state", "disabled")
                setattr(self._container.memory, "_reason", "disabled")
        try:
            self._container.context_window.initialize()
        except Exception:
            logger.exception("[Humanize] context window initialization failed")
        self._prompt_cache_tracker = PromptCacheTracker(self._container.repository)
        stored_templates = await self._container.repository.get_prompt_templates()
        self._container.envelope.set_templates(stored_templates["templates"])
        self._envelope_builder = self._container.envelope
        self.context.register_web_api(
            f"{PLUGIN_NAME}/<path:subpath>",
            self._container.web_api.dispatch,
            ["GET", "POST"],
            "Humanize management API",
        )
        if (
            memory_initialized
            and self._plugin_config.enabled
            and self._plugin_config.memory_enabled
        ):
            self._container.memory.start_worker()
        try:
            memory_status = await self._container.memory.get_status()
        except Exception:
            logger.exception("[Humanize] failed to read memory status")
            memory_status = {
                "state": "error",
                "reason": "status_unavailable",
            }
        logger.info(
            "[Humanize] plugin initialized; memory state=%s reason=%s",
            memory_status.get("state", "unknown"),
            memory_status.get("reason", "unknown"),
        )
        try:
            self._install_provider_hooks()
        except Exception:
            logger.exception(
                "[Humanize] provider capture hooks installation failed; "
                "final snapshots will be unavailable"
            )

    @filter.event_message_type(filter.EventMessageType.ALL, priority=_FIREWALL_PRIORITY)
    async def prepare_message_event(self, event: AstrMessageEvent) -> None:
        if not self._is_active:
            return
        event.set_extra("enable_streaming", False)
        self._install_send_gate(event)
        # 图片链路：把收到的图片统一落进插件缓存（LRU），并把组件路径改写为
        # 缓存路径——core 后续 convert_to_file_path 直接复用，不再依赖临时目录。
        # 全程 fail-open：缓存失败时保留原路径。
        cache_paths: list[str] = []
        components: list[Any] = []
        message_obj = getattr(getattr(event, "message_obj", None), "message", []) or []
        components.extend(message_obj)
        for component in message_obj:
            if type(component).__name__ == "Reply":
                components.extend(getattr(component, "chain", []) or [])
        try:
            for component in components:
                if type(component).__name__ != "Image":
                    continue
                convert = getattr(component, "convert_to_file_path", None)
                if not callable(convert):
                    continue
                try:
                    source_path = str(await convert() or "")
                except Exception:
                    logger.debug(
                        "[Humanize] image convert_to_file_path failed", exc_info=True
                    )
                    continue
                if not source_path:
                    continue
                cached = await self._image_store.store(
                    source_path,
                    message_id=str(getattr(event, "message_id", "") or ""),
                    scope_type="",
                    scope_id=str(getattr(event, "unified_msg_origin", "") or ""),
                )
                if cached.cached:
                    try:
                        component.file = Path(cached.file_path).as_uri()
                        component.path = cached.file_path
                        component.url = ""
                    except Exception:
                        logger.debug(
                            "[Humanize] failed to rewrite image component path",
                            exc_info=True,
                        )
                cache_paths.append(cached.file_path)
        except Exception:
            logger.exception("[Humanize] image cache interception failed")
        event.set_extra(_EVENT_IMAGE_CACHE_PATHS_KEY, tuple(cache_paths))
        # 转述：配置了转述模型即对本次图片转述（供 Msg 内联），与常驻工具互补。
        if cache_paths and self._plugin_config.image_transcription_provider_id:
            try:
                transcriptions = await self._transcribe_images(
                    event,
                    cache_paths,
                    str(
                        event.get_message_str()
                        if hasattr(event, "get_message_str")
                        else ""
                    ),
                )
                if transcriptions:
                    event.set_extra(
                        _EVENT_IMAGE_TRANSCRIPTIONS_KEY, tuple(transcriptions)
                    )
            except Exception:
                logger.exception("[Humanize] auto image transcription failed")
        await self._maybe_record_ambient(event)
        await self._maybe_schedule_proactive(event)

    @filter.command("clear", alias={"reset"}, priority=_FIREWALL_PRIORITY)
    async def clear_managed_context(self, event: AstrMessageEvent) -> None:
        """Reset native and Humanize short-term context for this conversation.

        Args:
            event: The command event for ``/clear`` or ``/reset``.
        """
        if not self._is_active or self._container is None:
            return
        try:
            managed_context = await self._managed_context_for_reset(event)
            await ConversationCommands(self.context).reset(event)
        except Exception:
            logger.exception("[Humanize] native conversation reset failed")
            event.set_result(event.plain_result("😕 Conversation reset failed."))
            event.stop_event()
            return

        result = event.get_result()
        succeeded = bool(
            result
            and any(
                getattr(component, "text", "") == "✅ Conversation reset successfully."
                for component in result.chain
            )
        )
        if not succeeded:
            event.stop_event()
            return

        try:
            await self._container.context_window.clear(managed_context)
        except Exception:
            logger.exception("[Humanize] managed context reset failed")
            event.set_result(
                event.plain_result(
                    "✅ Conversation reset successfully.\n"
                    "⚠️ Humanize context reset failed; it will retry on the next reset."
                )
            )
            event.stop_event()
            return

        event.set_result(
            event.plain_result(
                "✅ Conversation reset successfully.\n✅ Humanize context cleared."
            )
        )
        event.stop_event()

    @filter.on_llm_request(priority=_FINALIZER_PRIORITY)
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        if not self._is_active:
            return
        assert self._container is not None

        event.set_extra("enable_streaming", False)
        self._install_send_gate(event)

        if event.get_extra(_SEND_GATE_ERROR_KEY, False):
            event.set_extra(_STATE_KEY, EventState.FINAL_BLOCKED.value)
            event.set_extra(_ERROR_KEY, "send_gate_installation_failed")
            event.clear_result()
            event.stop_event()
            return

        message_getter = getattr(event, "get_message_str", None)
        raw_user = (
            str(message_getter() or "")
            if callable(message_getter)
            else str(req.prompt or "")
        )
        original_prompt = str(req.prompt if req.prompt is not None else raw_user)
        message_context = await self._build_message_context(event, raw_user)
        conversation_id = str(getattr(req.conversation, "cid", "") or "")
        if conversation_id and message_context.conversation_id != conversation_id:
            message_context = replace(
                message_context,
                conversation_id=conversation_id,
            )
        provider_settings: dict[str, Any] = {}
        try:
            candidate_settings = self.context.get_config(
                umo=event.unified_msg_origin
            ).get("provider_settings", {})
            if isinstance(candidate_settings, dict):
                provider_settings = candidate_settings
            (
                persona_id,
                persona_obj,
                _,
                use_webchat_default,
            ) = await self.context.persona_manager.resolve_selected_persona(
                umo=event.unified_msg_origin,
                conversation_persona_id=(
                    getattr(req.conversation, "persona_id", None)
                    if req.conversation is not None
                    else None
                ),
                platform_name=event.get_platform_name(),
                provider_settings=provider_settings,
            )
            agent_id = (
                "_chatui_default_"
                if use_webchat_default
                else str(persona_id or "default")
            )
            bot_name = str(
                (persona_obj and persona_obj.get("name")) or persona_id or ""
            ).strip()
            message_context = replace(
                message_context, agent_id=agent_id, bot_name=bot_name
            )
        except Exception:
            logger.exception("[Humanize] failed to resolve the effective persona")

        # 图片链路：缓存路径 + 转述 → Msg 内联标注；按 Provider modalities 分流。
        # 多模态：保留原图（image_urls 已由 core 填充，指向压缩派生文件）；
        # 非多模态：剥离原图与附件占位，只留路径+转述文本，模型可经常驻工具重读。
        # 注意：必须在 prepare_request 之前完成，标注才能进入 Msg 与历史。
        image_paths = list(event.get_extra(_EVENT_IMAGE_CACHE_PATHS_KEY, ()) or ())
        if not image_paths:
            image_paths = [
                path
                for path in list(req.image_urls or [])
                if self._image_store.is_cache_path(str(path))
            ]
        event_transcriptions = event.get_extra(_EVENT_IMAGE_TRANSCRIPTIONS_KEY, ())
        transcriptions = [str(item) for item in event_transcriptions]
        if (
            image_paths
            and len(transcriptions) < len(image_paths)
            and self._plugin_config.image_transcription_provider_id
        ):
            try:
                transcriptions.extend(
                    str(item)
                    for item in await self._transcribe_images(
                        event,
                        image_paths[len(transcriptions) :],
                        raw_user,
                    )
                )
            except Exception:
                logger.exception("[Humanize] image transcription failed")
        event.set_extra(_IMAGE_CACHE_KEY, tuple(transcriptions))
        if image_paths:
            image_lines = [
                "[图片：{}（图片路径 {}）]".format(
                    transcriptions[index].strip()
                    if index < len(transcriptions)
                    else "未转述",
                    path,
                )
                for index, path in enumerate(image_paths)
            ]
            message_context = replace(
                message_context,
                user_text=message_context.user_text + "\n" + "\n".join(image_lines),
            )
        try:
            provider_multimodal = self._provider_supports_image(
                self.context.get_using_provider(event.unified_msg_origin)
            )
        except Exception:
            # 判定失败按多模态处理：保留原图路径（与旧行为一致），不阻断请求。
            provider_multimodal = True
        if image_paths and not provider_multimodal:
            req.image_urls = []
            parts = [
                part
                for part in (getattr(req, "extra_user_content_parts", None) or [])
                if "[Image Attachment" not in str(getattr(part, "text", "") or "")
            ]
            try:
                req.extra_user_content_parts = parts
            except Exception:
                logger.exception(
                    "[Humanize] failed to strip image attachments for "
                    "non-multimodal provider"
                )

        context_window_active = False
        context_window_entry_count = 0
        context_window_estimated_tokens = 0
        context_window_error_type = ""
        context_window_token_budget = self._context_window_token_budget(
            provider_settings
        )
        try:
            context_window = await self._container.context_window.load(
                message_context,
                token_budget=context_window_token_budget,
            )
            req.contexts = list(context_window.contexts)
            req.conversation = None
            context_window_active = True
            context_window_entry_count = context_window.entry_count
            context_window_estimated_tokens = context_window.estimated_tokens
        except Exception as exc:
            # The managed window is authoritative. An unavailable workspace must
            # omit short-term history rather than silently exposing AstrBot's
            # separate conversation history to the Provider.
            req.contexts = []
            req.conversation = None
            context_window_error_type = type(exc).__name__
            logger.warning(
                "[Humanize] context window unavailable; cleared AstrBot native "
                "history for this request (error_type=%s)",
                context_window_error_type,
                exc_info=True,
            )
        try:
            prepared = await self._container.service.prepare_request(
                message_context,
                include_session_fallback=False,
            )
        except Exception as exc:
            logger.error(
                "[Humanize] request preparation failed: %s", exc, exc_info=True
            )
            event.set_extra(_STATE_KEY, EventState.FINAL_BLOCKED.value)
            event.set_extra(_ERROR_KEY, "request_preparation_failed")
            event.clear_result()
            event.stop_event()
            return

        active_provider_identity: dict[str, Any] = {}
        try:
            active_provider = self.context.get_using_provider(event.unified_msg_origin)
            if active_provider is not None:
                active_provider_identity = provider_identity(active_provider)
        except Exception:
            logger.exception("[Humanize] failed to capture active Provider identity")

        try:
            request_snapshot: dict[str, Any] = {}
            request_snapshot_complete = False
            prompt_replacement = prepared.message_xml
            logger.info(
                "[Humanize] debug sections=%s protocol_len=%s",
                len(prepared.sections or ()),
                len(prepared.protocol_prompt or ""),
            )
            if prepared.sections:
                prompt_target_count = 0
                for section in sorted(prepared.sections, key=lambda item: item.ordinal):
                    if not section.included:
                        continue
                    for target in section.targets:
                        if target == "prompt":
                            if section.key != "current_message":
                                raise ValueError(
                                    "only current_message may target the request prompt"
                                )
                            prompt_target_count += 1
                            if prompt_target_count > 1:
                                raise ValueError(
                                    "context sections must define exactly one prompt target"
                                )
                            prompt_replacement = section.content
                        elif target == "system":
                            req.system_prompt = self._append_prompt(
                                req.system_prompt, section.content
                            )
                        elif target == "temp_user":
                            req.extra_user_content_parts.append(
                                TextPart(text=section.content).mark_as_temp()
                            )
                        else:
                            raise ValueError(f"unsupported context target: {target}")
                if prompt_target_count != 1:
                    raise ValueError(
                        "context sections must define exactly one prompt target"
                    )
            else:
                if self._plugin_config.protocol_injection_mode == "both":
                    req.system_prompt = self._append_prompt(
                        req.system_prompt, prepared.protocol_prompt
                    )
                req.extra_user_content_parts.append(
                    TextPart(text=prepared.known_terms_xml).mark_as_temp()
                )
                req.extra_user_content_parts.append(
                    TextPart(text=prepared.protocol_prompt).mark_as_temp()
                )

            wrapped_prompt = original_prompt
            if raw_user:
                replacement_spans: list[tuple[int, int]] = []
                search_from = 0
                while True:
                    replacement_index = original_prompt.find(
                        prompt_replacement, search_from
                    )
                    if replacement_index < 0:
                        break
                    replacement_spans.append(
                        (
                            replacement_index,
                            replacement_index + len(prompt_replacement),
                        )
                    )
                    search_from = replacement_index + 1

                user_indexes: list[int] = []
                search_from = 0
                while True:
                    user_index = original_prompt.find(raw_user, search_from)
                    if user_index < 0:
                        break
                    user_end = user_index + len(raw_user)
                    if not any(
                        span_start <= user_index and user_end <= span_end
                        for span_start, span_end in replacement_spans
                    ):
                        user_indexes.append(user_index)
                    search_from = user_index + 1

                if len(user_indexes) == 1:
                    user_index = user_indexes[0]
                    wrapped_prompt = (
                        original_prompt[:user_index]
                        + prompt_replacement
                        + original_prompt[user_index + len(raw_user) :]
                    )
                elif not user_indexes and len(replacement_spans) == 1:
                    # A prior request hook may already have wrapped the current
                    # message. Accept exactly one complete envelope, never a loose
                    # raw-text match inside that envelope.
                    wrapped_prompt = original_prompt
                elif user_indexes:
                    raise ValueError(
                        "current user message occurs ambiguously in the provider prompt"
                    )
                else:
                    raise ValueError(
                        "current user message is absent or ambiguously wrapped in "
                        "the provider prompt"
                    )
            req.prompt = wrapped_prompt

            request_snapshot, request_snapshot_complete = serialize_provider_request(
                req
            )
            request_snapshot["capture_stage"] = "on_llm_request_finalizer"
            event.set_extra(_REQUEST_SNAPSHOT_KEY, request_snapshot)
            event.set_extra(_REQUEST_SNAPSHOT_COMPLETE_KEY, request_snapshot_complete)
            request_snapshot["humanize_context_window"] = {
                "status": "active" if context_window_active else "unavailable",
                "entry_count": context_window_entry_count,
                "estimated_tokens": context_window_estimated_tokens,
                "error_type": context_window_error_type or None,
            }
            request_fields = request_snapshot.get("fields", {})
            if not isinstance(request_fields, dict):
                request_fields = {}
            prefix_fields = {
                key: request_fields[key]
                for key in (
                    "system_prompt",
                    "contexts",
                    "func_tool",
                    "model",
                )
                if key in request_fields
            }
            prefix_fields.update(
                {
                    "provider_id": str(
                        active_provider_identity.get("provider_id") or ""
                    ),
                    "provider_type": str(
                        active_provider_identity.get("provider_type") or ""
                    ),
                    "model_revision": str(
                        active_provider_identity.get("model_revision") or ""
                    ),
                }
            )
            stable_fields = {
                key: request_fields[key]
                for key in ("system_prompt", "func_tool", "model")
                if key in request_fields
            }
            stable_fields.update(
                {
                    "provider_id": prefix_fields["provider_id"],
                    "provider_type": prefix_fields["provider_type"],
                    "model_revision": prefix_fields["model_revision"],
                }
            )
            observation = await self._prompt_cache_tracker.observe(
                scope_type=message_context.scope_type,
                scope_id=message_context.scope_id,
                conversation_id=message_context.conversation_id,
                request_fields=request_fields,
                prefix_fields=prefix_fields,
                stable_fields=stable_fields,
            )
            request_fingerprint = observation.request_fingerprint
            prefix_fingerprint = observation.prefix_fingerprint
            if prepared.sections:
                await self._container.service.record_context_trace(
                    message_context,
                    prepared.sections,
                    request_snapshot=request_snapshot,
                    request_snapshot_complete=request_snapshot_complete,
                )
        except Exception as exc:
            logger.error(
                "[Humanize] context application failed: %s", exc, exc_info=True
            )
            event.set_extra(_STATE_KEY, EventState.FINAL_BLOCKED.value)
            event.set_extra(_ERROR_KEY, "context_application_failed")
            event.clear_result()
            event.stop_event()
            return

        # Volatile group chatter is a trailing system fragment. Append it
        # after prefix observation so persona / protocol / history stay a
        # stable prefix-cache key; a changing ambient suffix must not
        # invalidate that prefix fingerprint.
        try:
            loader = getattr(
                getattr(self._container, "context_window", None),
                "load_ambient",
                None,
            )
            if callable(loader):
                ambient = await loader(message_context)
                if ambient:
                    req.contexts = list(req.contexts or [])
                    req.contexts.append({"role": "system", "content": ambient})
        except Exception:
            logger.debug("[Humanize] ambient context injection skipped", exc_info=True)

        event.set_extra(_STATE_KEY, EventState.REQUESTED.value)
        event.set_extra(_CONTEXT_KEY, message_context)
        event.set_extra(_MESSAGES_KEY, ())
        event.set_extra(_DISPATCHED_MESSAGES_KEY, [])
        event.set_extra(_START_KEY, time.perf_counter())
        event.set_extra(_MODEL_KEY, req.model or "")
        event.set_extra(
            _PROVIDER_ID_KEY,
            str(active_provider_identity.get("provider_id") or ""),
        )
        event.set_extra(_PROVIDER_IDENTITY_KEY, active_provider_identity)
        event.set_extra(_MESSAGE_XML_KEY, prepared.message_xml)
        event.set_extra(_ORIGINAL_PROMPT_KEY, original_prompt)
        event.set_extra(_WRAPPED_PROMPT_KEY, req.prompt or "")
        event.set_extra(_RAW_OUTPUT_KEY, "")
        event.set_extra(_VALIDATED_OUTPUT_KEY, "")
        event.set_extra(_ASSISTANT_MESSAGE_KEY, None)
        # The managed window is authoritative. If it is unavailable, this request
        # proceeds without short-term history instead of synchronizing AstrBot's
        # native conversation as a hidden fallback.
        event.set_extra(_HISTORY_SYNC_KEY, False)
        event.set_extra(_CONTEXT_WINDOW_ACTIVE_KEY, context_window_active)
        event.set_extra(
            _CONTEXT_WINDOW_TOKEN_BUDGET_KEY,
            context_window_token_budget,
        )
        event.set_extra(
            _CONTEXT_WINDOW_IMAGE_COUNT_KEY,
            len(req.image_urls or [])
            or len(
                [
                    part
                    for part in (getattr(req, "extra_user_content_parts", None) or [])
                    if re.search(
                        r"\[Image Attachment: path ",
                        str(getattr(part, "text", "") or ""),
                    )
                ]
            ),
        )
        event.set_extra(_CONTEXT_TURN_REF_KEY, "")
        event.set_extra(_CONTEXT_WINDOW_PENDING_MESSAGES_KEY, ())
        event.set_extra(_CONTEXT_WINDOW_PENDING_ACTION_KEY, "")
        event.set_extra(_CONTEXT_READ_CALLS_KEY, 0)
        event.set_extra(_IMAGE_CACHE_KEY, ())
        event.set_extra(_TOOL_HISTORY_KEY, {})
        event.set_extra(_REPAIR_PENDING_KEY, False)
        event.set_extra(_REPAIR_BODY_KEY, "")
        event.set_extra(_REPAIR_ACTION_KEY, "")
        event.set_extra(_REPAIR_ERROR_KEY, "")
        event.set_extra(_REPAIR_ATTEMPTED_KEY, False)
        event.set_extra(_FINAL_LOG_PENDING_KEY, False)
        event.set_extra(_RESPONSE_SNAPSHOTS_KEY, [])
        event.set_extra(_FINAL_RESPONSE_SNAPSHOT_KEY, None)
        event.set_extra(_FINAL_DISPATCHED_KEY, False)
        event.set_extra(_FINAL_DISPATCH_LOCK_KEY, asyncio.Lock())
        event.set_extra(_FINAL_FAILURE_LOGGED_KEY, False)
        event.set_extra(_TOOL_SENT_MESSAGES_KEY, [])
        event.set_extra(_TOOL_SENT_MEDIA_KEY, [])
        event.set_extra(_TOOL_PROCESSED_KEY, set())
        event.set_extra(_TOOL_SEND_LOCK_KEY, asyncio.Lock())
        event.set_extra(_REQUEST_FINGERPRINT_KEY, request_fingerprint)
        event.set_extra(_PREFIX_FINGERPRINT_KEY, prefix_fingerprint)
        event.set_extra(_PREFIX_EPOCH_KEY, observation.epoch_id)
        event.set_extra(
            _PREFIX_FIRST_DIFFERENCE_KEY,
            observation.first_difference,
        )
        event.set_extra(
            _PREFIX_COMMON_CHARS_KEY,
            observation.longest_common_prefix_chars,
        )
        event.set_extra(_PREFIX_EPOCH_REASON_KEY, observation.epoch_reason)
        event.set_extra(_FIRST_RESPONSE_AT_KEY, None)

        # 常驻读图工具：按路径重读缓存图片（转述模型转述，或提示多模态直读）。
        if self._plugin_config.image_cache_enabled:
            self._install_image_tool(event, req, image_paths)

    @staticmethod
    def _provider_supports_image(provider: Any) -> bool:
        """Judge image support from the Provider's user-configured modalities.

        Mirrors AstrBot core's ``_provider_supports_modality`` semantics: an
        empty ``modalities`` list is treated as unconfigured (supported).

        Args:
            provider: Active chat provider (or None).

        Returns:
            True when the provider declares (or does not restrict) image input.
        """
        config = getattr(provider, "provider_config", None)
        if not isinstance(config, dict):
            return True
        modalities = config.get("modalities", None)
        if modalities == []:
            return True
        return isinstance(modalities, list) and "image" in modalities

    def _install_image_tool(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        image_paths: list[str],
    ) -> None:
        """常驻工具：按路径重读缓存图片。

        非多模态模型收到 [图片：…（图片路径 …）] 标注后，可用该工具结合上下文
        重新转述图片；多模态模型本请求已带原图，工具用于历史图片。读图经由
        图片转述 Provider；未配置时返回明确提示。

        Args:
            event: Active message event.
            req: Provider request whose tool set receives the tool.
            image_paths: Known cache paths for the current request.
        """
        tool_set = getattr(req, "func_tool", None)
        add_tool = getattr(tool_set, "add_tool", None)
        if not callable(add_tool):
            return
        from astrbot.core.agent.tool import FunctionTool

        async def read_image(event: AstrMessageEvent, path: str) -> str:
            """按路径读取一张缓存图片并结合当前聊天上下文转述。

            Args:
                event: Active message event.
                path(str): 图片路径，来自消息中的 [图片：…（图片路径 …）] 标注。

            Returns:
                结合上下文的图片转述文本；失败或路径无效时返回说明。
            """
            clean_path = str(path or "").strip()
            if not self._plugin_config.image_transcription_provider_id:
                return (
                    "无法读取图片内容：未配置图片转述模型。"
                    "请基于上下文中的图片转述回复，避免直接谈论图片细节。"
                )
            data = await self._image_store.read(clean_path)
            if data is None:
                if image_paths and clean_path in image_paths:
                    return "图片读取失败。"
                return "图片不存在或已被缓存清理。"
            user_text = str(
                event.get_message_str() if hasattr(event, "get_message_str") else ""
            )
            try:
                transcriptions = await self._transcribe_images(
                    event,
                    [clean_path],
                    user_text,
                )
            except Exception:
                logger.exception("[Humanize] image read tool failed")
                return "图片转述失败。"
            if not transcriptions:
                return "未生成转述文本。"
            # 记录工具转述，保证持久化回合仍带图片标注（见上下文持久化报告）。
            try:
                existing = [
                    str(item)
                    for item in (
                        event.get_extra(_TOOL_IMAGE_TRANSCRIPTIONS_KEY, ()) or ()
                    )
                ]
                existing.extend(
                    str(getattr(item, "text", item) or "") for item in transcriptions
                )
                event.set_extra(
                    _TOOL_IMAGE_TRANSCRIPTIONS_KEY,
                    tuple(item for item in existing if item.strip()),
                )
            except Exception:
                logger.exception("[Humanize] failed to record tool transcription")
            return str(
                getattr(transcriptions[0], "text", transcriptions[0])
                or "未生成转述文本。"
            )

        tool = FunctionTool(
            name="humanize_read_image",
            description=(
                "按路径读取一张图片并结合当前聊天上下文转述其含义和简单内容。"
                "当消息中的图片标注不够用、或需要理解历史消息中的图片时调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "图片路径，来自消息中的 [图片：…（图片路径 …）] 标注",
                    }
                },
                "required": ["path"],
            },
            handler=read_image,
            handler_module_path=__name__,
            active=True,
            is_background_task=False,
        )
        add_tool(tool)
        logger.debug(
            "[Humanize] injected resident image read tool (%s known paths)",
            len(image_paths),
        )

    async def _transcribe_images(
        self,
        event: AstrMessageEvent,
        image_urls: list[str],
        user_text: str,
    ) -> list[object]:
        """Transcribe images with a multimodal provider, in context.

        The transcription carries the conversational context (user message) and
        captures both the image meaning and simple content, so later turns can
        understand it without seeing the image again.

        Args:
            event: Active event for provider resolution.
            image_urls: Current request image URLs.
            user_text: Current user message text.

        Returns:
            List of plain-text ImageCache entries, or an empty list on failure.
        """
        provider = await self.context.provider_manager.get_provider_by_id(
            self._plugin_config.image_transcription_provider_id
        )
        if provider is None:
            logger.warning(
                "[Humanize] image transcription provider not found: %s",
                self._plugin_config.image_transcription_provider_id,
            )
            return []
        prompt = (
            "你是图片转述助手。结合当前聊天上下文，用简洁中文转述图片的含义和简单内容"
            "（不是干巴巴描述画面）。只输出转述文本，不要解释。"
        )
        if user_text.strip():
            prompt += f"\n用户当前消息：{user_text}"
        try:
            response = await provider.text_chat(
                prompt=prompt,
                session_id="",
                image_urls=image_urls,
                audio_urls=[],
                func_tool=None,
                contexts=[],
                system_prompt="",
                tool_calls_result=None,
                model=None,
                extra_user_content_parts=[],
                request_max_retries=1,
            )
        except Exception as exc:
            logger.error(
                "[Humanize] image transcription request failed: %s",
                exc,
                exc_info=True,
            )
            return []
        text = str(getattr(response, "completion_text", "") or "").strip()
        if not text:
            return []
        from .humanize.domain.models import ImageCache

        return [ImageCache(text=text[:600])]

    @filter.on_using_llm_tool(priority=_FINALIZER_PRIORITY)
    async def on_tool_start(self, event: AstrMessageEvent, tool, tool_args) -> None:
        if event.get_extra(_STATE_KEY) == EventState.REQUESTED.value:
            event.set_extra(_STATE_KEY, EventState.TOOL_RUNNING.value)

    @filter.on_llm_tool_respond(priority=_FINALIZER_PRIORITY)
    async def on_tool_end(
        self, event: AstrMessageEvent, tool, tool_args, tool_result
    ) -> None:
        if event.get_extra(_STATE_KEY) == EventState.TOOL_RUNNING.value:
            event.set_extra(_STATE_KEY, EventState.REQUESTED.value)

    @filter.llm_tool(name="humanize_read_context")
    async def humanize_read_context(self, event: AstrMessageEvent, ref: str) -> str:
        """Read one folded Humanize context record when the current chat needs details.

        Args:
            ref(string): Short context reference from the current conversation, such
                as ctx-7F3K9M2Q.

        Returns:
            Bounded untrusted historical data, or a safe unavailable notice.
        """
        if (
            not self._is_active
            or self._container is None
            or not event.get_extra(_CONTEXT_WINDOW_ACTIVE_KEY, False)
        ):
            return "Humanize context history is unavailable for this request."
        context = event.get_extra(_CONTEXT_KEY)
        if not isinstance(context, MessageContext):
            return "Humanize context history is unavailable for this request."
        calls = int(event.get_extra(_CONTEXT_READ_CALLS_KEY, 0) or 0)
        if calls >= 3:
            return "Context detail limit reached for this request."
        event.set_extra(_CONTEXT_READ_CALLS_KEY, calls + 1)
        try:
            content = await self._container.context_window.read_context(context, ref)
        except ValueError:
            return "The context reference is invalid or outside this conversation."
        except Exception:
            logger.exception("[Humanize] context detail read failed")
            return "Context detail is temporarily unavailable."
        if not content:
            return "The context reference is unavailable in this conversation."
        return content

    @filter.on_llm_response(priority=_FIREWALL_PRIORITY)
    async def enforce_response_protocol(
        self, event: AstrMessageEvent, response: LLMResponse | None
    ) -> None:
        if not self._is_active:
            return
        state = event.get_extra(_STATE_KEY)
        if state not in {
            EventState.REQUESTED.value,
            EventState.TOOL_RUNNING.value,
        }:
            return
        if event.get_extra(_FIRST_RESPONSE_AT_KEY) is None:
            event.set_extra(_FIRST_RESPONSE_AT_KEY, time.perf_counter())
        self._capture_llm_response_snapshot(event, response)
        assert self._container is not None
        context = event.get_extra(_CONTEXT_KEY)
        if not isinstance(context, MessageContext):
            self._block_response(event, response, "missing_request_context")
            return

        raw_output = response.completion_text if response else ""
        event.set_extra(_RAW_OUTPUT_KEY, raw_output)
        started_at = event.get_extra(_START_KEY, time.perf_counter())
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
        model = str(event.get_extra(_MODEL_KEY, ""))
        # stage 判定：以响应是否携带工具调用为准（状态机可能因工具未结束
        # 而把 final 轮误判为 tool；finish_reason=stop 无 tool_calls 就是 final）
        has_tool_calls = bool(
            response
            and (
                response.tools_call_args
                or response.tools_call_name
                or response.tools_call_ids
                or response.tools_call_extra_content
            )
        )
        stage = (
            "tool"
            if has_tool_calls or state == EventState.TOOL_RUNNING.value
            else "final"
        )
        if stage == "final":
            # Persist the provider-visible final context when the final response
            # arrives; the provider payload was captured at the real Provider call.
            try:
                self._flush_provider_capture(event, response)
            except Exception:
                logger.exception("[Humanize] failed to flush final provider snapshot")
        await self._record_llm_usage_sample(
            event,
            response,
            stage=stage,
            duration_ms=duration_ms,
        )
        response_snapshot, response_snapshot_complete = (
            self._response_snapshot_for_record(event)
        )

        try:
            outcome = await self._container.service.process_final_response(
                context,
                raw_output,
                model=model,
                provider_id=str(event.get_extra(_PROVIDER_ID_KEY, "")),
                duration_ms=duration_ms,
                record_success=False,
                response_snapshot=response_snapshot,
                response_snapshot_complete=response_snapshot_complete,
            )
        except Exception as exc:
            logger.error(
                "[Humanize] final response handling failed: %s", exc, exc_info=True
            )
            await self._record_final_protocol_failure(
                event,
                "response_handling_failed",
                "Final response handling raised an exception",
            )
            self._block_response(event, response, "response_handling_failed")
            return

        if not outcome.valid:
            logger.warning(
                "[Humanize] blocked malformed response: %s (%s)",
                outcome.error_code,
                outcome.error_detail,
            )
            event.set_extra(_ERROR_KEY, outcome.error_code)
            if (
                self._plugin_config.protocol_repair_retry_enabled
                and response is not None
                and response.role == "assistant"
                and not event.get_extra(_REPAIR_ATTEMPTED_KEY, False)
            ):
                repair_candidate = self._protocol_parser.extract_repair_candidate(
                    raw_output
                )
                if repair_candidate is not None and (
                    repair_candidate[0].strip() or self._plugin_config.no_reply_enabled
                ):
                    repair_body, repair_action = repair_candidate
                    event.set_extra(_REPAIR_PENDING_KEY, True)
                    event.set_extra(_REPAIR_BODY_KEY, repair_body)
                    event.set_extra(_REPAIR_ACTION_KEY, repair_action)
                    event.set_extra(_REPAIR_ERROR_KEY, outcome.error_code)
                    event.set_extra(_REPAIR_ATTEMPTED_KEY, True)
                    self._set_response_text(response, "")
                    return
            self._block_response(event, response, outcome.error_code)
            return

        if outcome.action is Action.NO_REPLY:
            event.set_extra(_VALIDATED_OUTPUT_KEY, raw_output)
            event.set_extra(_IMAGE_CACHE_KEY, outcome.image_cache)
            event.set_extra(_NO_REPLY_REASON_KEY, outcome.no_reply_reason)
            event.set_extra(_FINAL_LOG_PENDING_KEY, True)
            event.set_extra(_STATE_KEY, EventState.NO_REPLY.value)
            event.set_extra(_MESSAGES_KEY, ())
            self._set_response_text(response, _NO_REPLY_SENTINEL)
            return

        if outcome.messages_over_limit:
            logger.warning(
                "[Humanize] reply exceeded max_messages_per_reply; "
                "kept the first %s messages",
                self._plugin_config.max_messages_per_reply,
            )
        history_text = "\n".join(outcome.messages)
        event.set_extra(_VALIDATED_OUTPUT_KEY, raw_output)
        event.set_extra(_IMAGE_CACHE_KEY, outcome.image_cache)
        event.set_extra(_FINAL_LOG_PENDING_KEY, True)
        self._set_response_text(response, history_text)
        event.set_extra(_MESSAGES_KEY, outcome.messages)
        event.set_extra(_STATE_KEY, EventState.FINAL_VALID.value)

    @filter.on_agent_done(priority=_FIREWALL_PRIORITY)
    async def synchronize_agent_history(
        self, event: AstrMessageEvent, run_context, response: LLMResponse | None
    ) -> None:
        if not self._is_active:
            return
        state = event.get_extra(_STATE_KEY, EventState.INACTIVE.value)
        if (
            not isinstance(event.get_extra(_FINAL_RESPONSE_SNAPSHOT_KEY), dict)
            and response is not None
        ):
            # Runners are allowed to reach OnAgentDone without firing the normal
            # response hook. Preserve that untouched terminal response before any
            # firewall failure mutates it.
            self._capture_llm_response_snapshot(event, response)
            if not event.get_extra(_RAW_OUTPUT_KEY, ""):
                event.set_extra(_RAW_OUTPUT_KEY, response.completion_text or "")
        if event.get_extra(_REPAIR_PENDING_KEY, False):
            await self._attempt_protocol_repair(event, response)
            state = event.get_extra(_STATE_KEY, EventState.INACTIVE.value)
        if state in {EventState.REQUESTED.value, EventState.TOOL_RUNNING.value}:
            await self._record_final_protocol_failure(
                event,
                "response_firewall_not_applied",
                "No validated final response reached the agent completion hook",
            )
            self._block_response(event, response, "response_firewall_not_applied")
            state = EventState.FINAL_BLOCKED.value
        if state not in {
            EventState.FINAL_VALID.value,
            EventState.FINAL_BLOCKED.value,
            EventState.NO_REPLY.value,
        }:
            return

        if event.get_extra(_CONTEXT_WINDOW_ACTIVE_KEY, False):
            if state in {
                EventState.FINAL_VALID.value,
                EventState.NO_REPLY.value,
                EventState.FINAL_BLOCKED.value,
            }:
                messages = getattr(run_context, "messages", ())
                if isinstance(messages, list):
                    # Blocked turns keep the user side of the turn (message,
                    # tool calls, tool results) with No Reply semantics; the
                    # rejected assistant reply is stripped by the canonical
                    # form. Locate the current user message by content so the
                    # synthetic user messages AstrBot appends mid-turn cannot
                    # shift the slice and drop the tool history.
                    user_index = self._locate_current_user_message(
                        run_context,
                        str(
                            event.get_extra(
                                _WRAPPED_PROMPT_KEY,
                                event.get_extra(_MESSAGE_XML_KEY, ""),
                            )
                        ),
                    )
                    if user_index is not None:
                        self._sanitize_tool_assistant_messages(
                            run_context,
                            user_index=user_index,
                            replacements=event.get_extra(_TOOL_HISTORY_KEY, {}),
                        )
                event.set_extra(
                    _CONTEXT_WINDOW_PENDING_MESSAGES_KEY,
                    tuple(messages) if isinstance(messages, list) else (),
                )
                event.set_extra(
                    _CONTEXT_WINDOW_PENDING_ACTION_KEY,
                    (
                        Action.REPLY.value
                        if state == EventState.FINAL_VALID.value
                        else Action.NO_REPLY.value
                    ),
                )
            return

        if not event.get_extra(_HISTORY_SYNC_KEY, False):
            return

        context = event.get_extra(_CONTEXT_KEY)
        wrapped_prompt = str(
            event.get_extra(
                _WRAPPED_PROMPT_KEY,
                event.get_extra(_MESSAGE_XML_KEY, ""),
            )
        )
        original_prompt = str(
            event.get_extra(_ORIGINAL_PROMPT_KEY, getattr(context, "user_text", ""))
        )
        if not isinstance(context, MessageContext) or not wrapped_prompt:
            await self._record_final_protocol_failure(
                event,
                "missing_history_context",
                "Validated response could not be synchronized to history",
            )
            self._block_response(event, response, "missing_history_context")
            return

        user_index = self._restore_current_user_message(
            run_context, wrapped_prompt, original_prompt
        )
        if user_index is None:
            logger.warning("[Humanize] current user history message was not found")
            await self._record_final_protocol_failure(
                event,
                "current_user_message_not_found",
                "Current user message was not found in agent history",
            )
            self._block_response(event, response, "current_user_message_not_found")
            return

        self._sanitize_tool_assistant_messages(
            run_context,
            user_index=user_index,
            replacements=event.get_extra(_TOOL_HISTORY_KEY, {}),
        )

        raw_output = str(event.get_extra(_RAW_OUTPUT_KEY, ""))
        clean_text = response.completion_text if response else ""
        if state in {EventState.FINAL_BLOCKED.value, EventState.NO_REPLY.value}:
            clean_text = ""
        assistant = self._replace_current_assistant_message(
            run_context,
            user_index=user_index,
            raw_output=raw_output,
            clean_text=clean_text,
        )
        event.set_extra(_ASSISTANT_MESSAGE_KEY, assistant)

    @filter.on_agent_done(priority=_FINALIZER_PRIORITY)
    async def finalize_agent_history(
        self, event: AstrMessageEvent, run_context, response: LLMResponse | None
    ) -> None:
        state = event.get_extra(_STATE_KEY, EventState.INACTIVE.value)

        # Capture the provider-visible final context after the agent run assembled
        # the real request (persona, KB, file extraction, tool prompts) and the
        # model produced its reasoning and response. This is the true complete
        # snapshot for the context trace; it is recorded for every terminal run
        # state (including No Reply), not only validated replies.
        if state in {
            EventState.REQUESTED.value,
            EventState.TOOL_RUNNING.value,
            EventState.FINAL_VALID.value,
            EventState.NO_REPLY.value,
        }:
            try:
                self._flush_provider_capture(event, response)
            except Exception:
                logger.exception("[Humanize] failed to flush final provider snapshot")

        if state == EventState.NO_REPLY.value:
            self._set_response_text(response, _NO_REPLY_SENTINEL)
            return
        if state != EventState.FINAL_VALID.value or response is None:
            return

        assistant = event.get_extra(_ASSISTANT_MESSAGE_KEY)
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list) or not any(
            message is assistant for message in messages
        ):
            return
        self._set_assistant_message_text(assistant, response.completion_text)

    def _install_provider_hooks(self) -> None:
        """Patch concrete Provider subclasses to capture real payloads.

        AstrBot's OpenAI/Anthropic/Gemini providers override ``text_chat`` and
        ``text_chat_stream``, so patching only the base ``Provider`` class is
        insufficient. This walks every concrete ``Provider`` subclass (including
        derived ones such as Groq or KimiCode) and wraps the two methods that
        actually exist on each class.

        Raises:
            RuntimeError: If no Provider method could be patched.
        """
        if self._provider_hooks_installed:
            return
        # Ensure the concrete provider classes are registered before walking the
        # subclass tree; AstrBot may import them lazily after plugin init.
        try:
            from astrbot.core.provider.sources import (  # noqa: F401
                anthropic_source,
                gemini_source,
                openai_source,
            )
        except Exception:
            logger.warning(
                "[Humanize] provider source modules unavailable; "
                "falling back to base-class hook",
                exc_info=True,
            )
        plugin = self
        patched = 0
        seen: set[type] = set()

        def patch_class(cls: type) -> None:
            nonlocal patched
            if cls in seen or cls.__dict__.get("_humanize_provider_patched", False):
                return
            seen.add(cls)
            for method_name in ("text_chat", "text_chat_stream"):
                original = cls.__dict__.get(method_name)
                if original is None:
                    continue
                if method_name == "text_chat":

                    async def hooked_text_chat(self, *args, _orig=original, **kwargs):
                        plugin._capture_provider_payload(self, kwargs)
                        return await _orig(self, *args, **kwargs)

                    setattr(cls, method_name, hooked_text_chat)
                else:

                    async def hooked_text_chat_stream(
                        self, *args, _orig=original, **kwargs
                    ):
                        plugin._capture_provider_payload(self, kwargs)
                        async for item in _orig(self, *args, **kwargs):
                            yield item

                    setattr(cls, method_name, hooked_text_chat_stream)
                self._provider_originals.append((cls, method_name, original))
                patched += 1
            setattr(cls, "_humanize_provider_patched", True)

        patch_class(_ProviderBase)
        pending = list(_ProviderBase.__subclasses__())
        while pending:
            cls = pending.pop()
            patch_class(cls)
            pending.extend(cls.__subclasses__())

        if patched == 0:
            raise RuntimeError("no Provider method could be patched")
        self._provider_hooks_installed = True
        logger.info(
            "[Humanize] provider capture hooks installed on %d method(s) "
            "across %d Provider class(es)",
            patched,
            len(seen),
        )

    def _uninstall_provider_hooks(self) -> None:
        """Restore the original Provider methods when the plugin terminates."""
        if not self._provider_hooks_installed:
            return
        for target, name, original in self._provider_originals:
            try:
                setattr(target, name, original)
            except Exception:
                logger.exception("[Humanize] failed to restore %s.%s", target, name)
            try:
                delattr(target, "_humanize_provider_patched")
            except AttributeError:
                pass
        self._provider_originals = []
        self._provider_hooks_installed = False
        self._provider_capture.clear()
        logger.info("[Humanize] provider capture hooks uninstalled")

    def _capture_provider_payload(self, provider: Any, kwargs: dict[str, Any]) -> None:
        """Store the provider-visible payload keyed by the session identifier.

        Args:
            provider: Provider instance that is about to be called.
            kwargs: Arguments passed to ``text_chat`` or ``text_chat_stream``.
        """
        if not self._is_active:
            return
        try:
            session_id = str(kwargs.get("session_id") or "default")
            model = kwargs.get("model") or ""
            provider_id = ""
            try:
                meta = provider.meta()
                provider_id = str(getattr(meta, "id", "") or "")
            except Exception:
                provider_id = ""
            contexts = kwargs.get("contexts") or []
            contexts = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in contexts
            ]
            extra_parts = kwargs.get("extra_user_content_parts") or []
            extra_parts = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in extra_parts
            ]
            prompt = kwargs.get("prompt")
            if hasattr(prompt, "model_dump"):
                prompt = prompt.model_dump(mode="json")
            func_tool = kwargs.get("func_tool")
            tools_info = None
            if func_tool is not None:
                try:
                    schema = getattr(func_tool, "openai_schema", None)
                    if callable(schema):
                        tools_info = schema()
                except Exception:
                    logger.warning(
                        "[Humanize] tool schema capture failed", exc_info=True
                    )
            now = time.monotonic()
            self._evict_stale_provider_capture(now)
            self._provider_capture[session_id] = {
                "provider_id": provider_id,
                "model": str(model or ""),
                "contexts": _json_safe(contexts),
                "tools": tools_info,
                "system_prompt": str(kwargs.get("system_prompt") or ""),
                "prompt": _json_safe(prompt),
                "image_urls": list(kwargs.get("image_urls") or []),
                "audio_urls": list(kwargs.get("audio_urls") or []),
                "extra_user_content_parts": _json_safe(extra_parts),
                "captured_at": now,
            }
        except Exception:
            logger.exception("[Humanize] provider payload capture failed")

    def _evict_stale_provider_capture(self, now: float) -> None:
        """Drop captures that no final response ever claimed.

        A capture is only popped when a final response reaches
        ``_flush_provider_capture``. Requests that error out, get cancelled, or
        never reach the final stage would otherwise retain their full provider
        payload (contexts plus tool schemas) for the lifetime of the process.

        Args:
            now: Current ``time.monotonic()`` reading.
        """
        capture = self._provider_capture
        for session_id, entry in list(capture.items()):
            captured_at = entry.get("captured_at") if isinstance(entry, dict) else None
            if not isinstance(captured_at, (int, float)):
                capture.pop(session_id, None)
            elif now - captured_at > _PROVIDER_CAPTURE_TTL_SECONDS:
                capture.pop(session_id, None)
        overflow = len(capture) - _PROVIDER_CAPTURE_MAX_ENTRIES
        if overflow <= 0:
            return
        oldest = sorted(
            capture.items(),
            key=lambda item: (
                item[1].get("captured_at", 0.0) if isinstance(item[1], dict) else 0.0
            ),
        )
        for session_id, _ in oldest[:overflow]:
            capture.pop(session_id, None)
        logger.warning(
            "[Humanize] provider capture overflow, dropped %s stale entries", overflow
        )

    def _flush_provider_capture(
        self,
        event: AstrMessageEvent,
        response: LLMResponse | None,
    ) -> bool:
        """Assemble and persist the final complete snapshot for one request.

        Args:
            event: Active event carrying scoped request metadata.
            response: Untouched final LLM response.

        Returns:
            ``True`` when a provider payload was found and the final snapshot
            was persisted.
        """
        if self._container is None:
            return False
        context = event.get_extra(_CONTEXT_KEY)
        if not isinstance(context, MessageContext):
            return False
        session_id = str(event.unified_msg_origin or "default")
        captured = self._provider_capture.pop(session_id, None)
        if not isinstance(captured, dict):
            return False
        response_snapshot, response_complete = serialize_llm_response(response)
        reasoning = ""
        if isinstance(response_snapshot, dict):
            fields = response_snapshot.get("fields", {})
            if isinstance(fields, dict):
                reasoning = str(fields.get("reasoning_content") or "")
        # 图片转述（ImageCache）保存在 event 上，必须在快照中保留，供后续轮次
        # 注入到 <Msg> 上下文。
        image_cache = event.get_extra(_IMAGE_CACHE_KEY, ())
        image_cache_texts: list[str] = []
        for item in image_cache or ():
            text = getattr(item, "text", None)
            if isinstance(item, dict):
                text = item.get("text", "")
            if text:
                image_cache_texts.append(str(text))
        final_snapshot = {
            "capture_stage": "on_provider_call",
            "type": "provider_request_final",
            "fields": {
                "contexts": captured.get("contexts", []),
                "image_urls": captured.get("image_urls", []),
                "audio_urls": captured.get("audio_urls", []),
                "extra_user_content_parts": captured.get(
                    "extra_user_content_parts", []
                ),
                "func_tool": {"tools": captured.get("tools") or []}
                if captured.get("tools")
                else None,
                "system_prompt": captured.get("system_prompt", ""),
                "prompt": captured.get("prompt"),
                "model": captured.get("model", ""),
            },
            "image_cache": image_cache_texts,
            "provider_id": captured.get("provider_id", ""),
            "reasoning": reasoning,
            "response": response_snapshot or {},
        }
        final_snapshot = _json_safe(final_snapshot)
        final_complete = bool(response_complete)
        try:
            _spawn_background(
                self._container.service.update_context_trace_final_snapshot(
                    context,
                    request_snapshot_final=final_snapshot,
                    request_snapshot_final_complete=final_complete,
                ),
                name="humanize-context-final-snapshot",
            )
        except Exception:
            logger.exception("[Humanize] final snapshot persistence scheduling failed")
        return True

    async def _persist_context_window(self, event: AstrMessageEvent) -> None:
        """Persist the validated run only after terminal dispatch succeeded.

        Args:
            event: Active event carrying the scoped request metadata.
        """
        if self._container is None:
            return
        if event.get_extra(_CONTEXT_TURN_REF_KEY, ""):
            return
        context = event.get_extra(_CONTEXT_KEY)
        if not isinstance(context, MessageContext):
            return
        action = str(event.get_extra(_CONTEXT_WINDOW_PENDING_ACTION_KEY, ""))
        messages = event.get_extra(_CONTEXT_WINDOW_PENDING_MESSAGES_KEY, ())
        if action not in {Action.REPLY.value, Action.NO_REPLY.value}:
            return
        if not isinstance(messages, tuple):
            messages = ()
        try:
            image_cache = tuple(event.get_extra(_IMAGE_CACHE_KEY, ()))
            # Temporary ImageCache fallback: in tool transcription mode the
            # model may never echo <ImageCache>; keep transcriptions produced
            # by the injected tool so the saved turn still carries image
            # markers. Deduplicated, order-preserving, bounded by image_count
            # when rendered into markers.
            tool_transcriptions = tuple(
                str(item)
                for item in (event.get_extra(_TOOL_IMAGE_TRANSCRIPTIONS_KEY, ()) or ())
                if str(item).strip()
            )
            if tool_transcriptions:
                known = {str(getattr(item, "text", item) or "") for item in image_cache}
                merged = list(image_cache)
                merged.extend(item for item in tool_transcriptions if item not in known)
                image_cache = tuple(merged)
            result = await self._container.context_window.append(
                context,
                action=action,
                run_messages=messages,
                final_messages=tuple(event.get_extra(_MESSAGES_KEY, ())),
                image_cache=image_cache,
                image_count=int(
                    event.get_extra(_CONTEXT_WINDOW_IMAGE_COUNT_KEY, 0) or 0
                ),
                token_budget=int(
                    event.get_extra(_CONTEXT_WINDOW_TOKEN_BUDGET_KEY, 6_000) or 6_000
                ),
                current_user_prompt=str(
                    event.get_extra(
                        _WRAPPED_PROMPT_KEY,
                        event.get_extra(_MESSAGE_XML_KEY, ""),
                    )
                ),
            )
            event.set_extra(_CONTEXT_TURN_REF_KEY, result.context_ref)
            try:
                dropper = getattr(
                    getattr(self._container, "context_window", None),
                    "drop_ambient",
                    None,
                )
                if callable(dropper):
                    await dropper(context)
            except Exception:
                logger.debug("[Humanize] ambient drop skipped", exc_info=True)
            committer = getattr(
                getattr(self._container, "memory", None),
                "commit_context_turn",
                None,
            )
            if callable(committer):
                await committer(
                    context,
                    action=action,
                    messages=tuple(event.get_extra(_MESSAGES_KEY, ())),
                    context_ref=result.context_ref,
                )
        except Exception:
            logger.exception("[Humanize] context window persistence failed")

    @filter.on_decorating_result(priority=_DISPATCH_PRIORITY)
    async def dispatch_response(self, event: AstrMessageEvent) -> None:
        state = event.get_extra(_STATE_KEY, EventState.INACTIVE.value)
        if state in {EventState.FINAL_VALID.value, EventState.NO_REPLY.value}:
            await self._dispatch_terminal_result(event, event.get_result())
            return
        if state in {
            EventState.REQUESTED.value,
            EventState.TOOL_RUNNING.value,
        }:
            result = event.get_result()
            if result and result.chain:
                await self._process_tool_stage_chain(event, result)
            event.clear_result()
        if state == EventState.FINAL_BLOCKED.value:
            event.clear_result()

    async def _dispatch_terminal_result(
        self,
        event: AstrMessageEvent,
        result: Any,
    ) -> None:
        """Atomically dispatch one final result after all normal decorators ran.

        Args:
            event: Active event carrying validated protocol state.
            result: Decorated result or a direct-send fallback message chain.
        """
        lock = event.get_extra(_FINAL_DISPATCH_LOCK_KEY)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            event.set_extra(_FINAL_DISPATCH_LOCK_KEY, lock)

        tool_lock = event.get_extra(_TOOL_SEND_LOCK_KEY)
        if not isinstance(tool_lock, asyncio.Lock):
            tool_lock = asyncio.Lock()
            event.set_extra(_TOOL_SEND_LOCK_KEY, tool_lock)

        # A tool-stage direct send may still be in flight when the provider's
        # terminal response arrives. Waiting for that reservation makes the
        # duplicate filter observe the exact text and media already delivered.
        async with lock, tool_lock:
            if event.get_extra(_FINAL_DISPATCHED_KEY, False):
                event.clear_result()
                return
            state = event.get_extra(_STATE_KEY, EventState.INACTIVE.value)
            if state == EventState.NO_REPLY.value:
                await self._record_final_protocol_success(event)
                event.set_extra(_FINAL_DISPATCHED_KEY, True)
                event.clear_result()
                return
            if state != EventState.FINAL_VALID.value:
                return
            if event.is_stopped() or not result or not result.chain:
                await self._record_final_protocol_failure(
                    event,
                    "response_not_dispatched",
                    "Validated response was stopped or had no outbound message",
                )
                event.clear_result()
                event.set_extra(_STATE_KEY, EventState.FINAL_BLOCKED.value)
                return

            rendered_text = result.get_plain_text()
            if _CONTROL_TAG_PATTERN.search(rendered_text):
                logger.warning(
                    "[Humanize] suppressed decorated response containing control tags"
                )
                await self._record_final_protocol_failure(
                    event,
                    "decorated_response_control_tag_leak",
                    "A result decorator exposed protocol control tags",
                )
                self._block_response(
                    event,
                    None,
                    "decorated_response_control_tag_leak",
                )
                return

            if not all(isinstance(component, Plain) for component in result.chain):
                original_messages = tuple(event.get_extra(_MESSAGES_KEY, ()))
                joined = "\n".join(original_messages)
                text_ok = rendered_text == joined or (
                    original_messages
                    and rendered_text.strip()
                    and all(
                        part in rendered_text
                        for part in original_messages
                        if part.strip()
                    )
                )
                if not text_ok:
                    logger.warning(
                        "[Humanize] suppressed decorated media result with changed text"
                    )
                    await self._record_final_protocol_failure(
                        event,
                        "decorated_response_text_changed",
                        "A result decorator changed validated response text",
                    )
                    self._block_response(event, None, "decorated_response_text_changed")
                    return
                outbound = self._without_tool_duplicates(event, original_messages)
                sent_media = event.get_extra(_TOOL_SENT_MEDIA_KEY, [])
                if not isinstance(sent_media, list):
                    sent_media = []
                # 文本逐条发送（保留分段），媒体组件单独一条链发送
                remaining_sent_media = [str(item) for item in sent_media]
                media_components = []
                for component in result.chain:
                    if isinstance(component, Plain):
                        continue
                    media_key = repr(component)
                    try:
                        remaining_sent_media.remove(media_key)
                    except ValueError:
                        media_components.append(component)
                dispatch_result = (
                    result.derive(media_components) if media_components else None
                )
                event.clear_result()
                try:
                    if outbound:
                        await self._send_messages(event, outbound)
                    if dispatch_result is not None:
                        await self._send_chain(event, dispatch_result)
                except Exception:
                    logger.exception("[Humanize] final media response dispatch failed")
                    await self._record_final_protocol_failure(
                        event,
                        "response_dispatch_failed",
                        "Validated media response could not be sent",
                    )
                    self._block_response(event, None, "response_dispatch_failed")
                    return
                finally:
                    event.clear_result()
                await self._record_final_protocol_success(event)
                event.set_extra(_FINAL_DISPATCHED_KEY, True)
                event.set_extra(_STATE_KEY, EventState.DISPATCHED.value)
                return

            original_messages = tuple(event.get_extra(_MESSAGES_KEY, ()))
            # 分段发送应以协议解析的原始消息为准。result decorator 可能
            # 微调文本（换行/分段），但内容一致时仍按原始多条逐条发送；
            # 仅当装饰结果删改内容时才回退到装饰后的单条文本。
            joined = "\n".join(original_messages)
            if rendered_text == joined:
                outbound = original_messages
            elif (
                original_messages
                and rendered_text.strip()
                and all(
                    part in rendered_text for part in original_messages if part.strip()
                )
            ):
                outbound = original_messages
            else:
                outbound = (rendered_text,) if rendered_text.strip() else ()
            candidate_outbound = outbound
            outbound = self._without_tool_duplicates(event, outbound)
            if not outbound:
                if not candidate_outbound:
                    await self._record_final_protocol_failure(
                        event,
                        "decorated_response_empty",
                        "A result decorator removed validated response text",
                    )
                    self._block_response(event, None, "decorated_response_empty")
                    return
                event.clear_result()
                await self._record_final_protocol_success(event)
                event.set_extra(_FINAL_DISPATCHED_KEY, True)
                event.set_extra(_STATE_KEY, EventState.DISPATCHED.value)
                return

            event.clear_result()
            try:
                await self._send_messages(event, outbound)
            except Exception:
                logger.exception("[Humanize] final response dispatch failed")
                await self._record_final_protocol_failure(
                    event,
                    "response_dispatch_failed",
                    "Validated response could not be sent",
                )
                self._block_response(event, None, "response_dispatch_failed")
                return
            finally:
                event.clear_result()
            await self._record_final_protocol_success(event)
            event.set_extra(_FINAL_DISPATCHED_KEY, True)
            event.set_extra(_STATE_KEY, EventState.DISPATCHED.value)

    @filter.on_decorating_result(priority=_DECORATION_FINALIZER_PRIORITY)
    async def finalize_decoration(self, event: AstrMessageEvent) -> None:
        state = event.get_extra(_STATE_KEY, EventState.INACTIVE.value)
        if state in {
            EventState.NO_REPLY.value,
            EventState.DISPATCHED.value,
            EventState.FINAL_BLOCKED.value,
        }:
            # A blocked turn still must persist its user side (message, tool
            # calls, tool results) with No Reply semantics; only the rejected
            # assistant reply is dropped. Idempotent via the turn reference,
            # so a later repair-driven success write is unaffected.
            if state == EventState.FINAL_BLOCKED.value and event.get_extra(
                _CONTEXT_WINDOW_ACTIVE_KEY, False
            ):
                try:
                    await self._persist_context_window(event)
                except Exception:
                    logger.exception(
                        "[Humanize] blocked-turn context window persistence failed"
                    )
            event.clear_result()

    async def _warn_if_repair_frequent(self, event: AstrMessageEvent) -> None:
        """私聊提醒管理员：协议修复在短时间窗口内频繁触发。

        Args:
            event: Active event whose platform/admins identify the warning target.
        """
        now = time.monotonic()
        window_seconds = 600.0  # 10 分钟窗口
        threshold = 3  # 窗口内触发 3 次视为频繁
        self._repair_timestamps = [
            stamp for stamp in self._repair_timestamps if now - stamp < window_seconds
        ]
        self._repair_timestamps.append(now)
        if len(self._repair_timestamps) < threshold:
            return
        # 同窗口只告警一次，避免刷屏
        if now - self._repair_warned_at < window_seconds:
            return
        self._repair_warned_at = now
        admin_ids = self._plugin_config.admin_qq_ids
        if not admin_ids:
            logger.warning(
                "[Humanize] protocol repair fired %d times in the last %ds "
                "(no admin configured to notify)",
                len(self._repair_timestamps),
                window_seconds,
            )
            return
        platform_name = event.get_platform_name()

        message = (
            f"[Humanize 警告] 协议修复在最近 {window_seconds // 60} 分钟内触发 "
            f"{len(self._repair_timestamps)} 次，回复控制协议频繁校验失败。\n"
            "可能原因：其他插件修改了回复文本、模型输出格式不稳定、或配置不当。\n"
            "请检查 插件管理 → 协议日志 或上下文追踪页定位原因。"
        )
        for admin_id in admin_ids:
            session = f"{platform_name}:FriendMessage:{admin_id}"
            try:
                await self.context.send_message(session, MessageChain([Plain(message)]))
            except Exception:
                logger.exception(
                    "[Humanize] failed to notify admin %s about frequent repairs",
                    admin_id,
                )
        logger.warning(
            "[Humanize] protocol repair frequent (%d in %ds); admins notified",
            len(self._repair_timestamps),
            window_seconds,
        )

    async def _attempt_protocol_repair(
        self, event: AstrMessageEvent, response: LLMResponse | None
    ) -> None:
        """Run one isolated model call that may replace only the control header.

        Args:
            event: Active message event containing the failed response metadata.
            response: Mutable final LLM response from the original agent run.
        """
        await self._warn_if_repair_frequent(event)
        event.set_extra(_REPAIR_PENDING_KEY, False)
        if response is None or self._container is None:
            await self._record_protocol_repair_failure(
                event,
                "protocol_repair_unavailable",
                "Final response or plugin service is unavailable",
            )
            self._block_response(event, response, "protocol_repair_unavailable")
            return

        context = event.get_extra(_CONTEXT_KEY)
        if not isinstance(context, MessageContext):
            self._block_response(event, response, "missing_request_context")
            return

        original_body = event.get_extra(_REPAIR_BODY_KEY, "")
        if not isinstance(original_body, str):
            await self._record_protocol_repair_failure(
                event,
                "invalid_protocol_repair_body",
                "Preserved response body is not text",
            )
            self._block_response(event, response, "invalid_protocol_repair_body")
            return
        required_action = str(event.get_extra(_REPAIR_ACTION_KEY, ""))
        if required_action not in {Action.REPLY.value, Action.NO_REPLY.value}:
            await self._record_protocol_repair_failure(
                event,
                "invalid_protocol_repair_action",
                "No trustworthy Action is available for header repair",
            )
            self._block_response(event, response, "invalid_protocol_repair_action")
            return
        raw_output = str(event.get_extra(_RAW_OUTPUT_KEY, ""))
        invalid_header_preview = "\n".join(raw_output.splitlines()[:2])[:2_000]
        system_prompt, repair_prompt = (
            self._envelope_builder.build_protocol_repair_request(
                context,
                error_code=str(event.get_extra(_REPAIR_ERROR_KEY, "")),
                invalid_header_preview=invalid_header_preview,
                required_action=required_action,
            )
        )

        repair_started_at = time.perf_counter()
        try:
            provider = self.context.get_using_provider(event.unified_msg_origin)
            if provider is None:
                raise RuntimeError("no active chat provider")
            repair_response = await provider.text_chat(
                prompt=repair_prompt,
                session_id="",
                image_urls=[],
                audio_urls=[],
                func_tool=None,
                contexts=[],
                system_prompt=system_prompt,
                tool_calls_result=None,
                model=str(event.get_extra(_MODEL_KEY, "")) or None,
                extra_user_content_parts=[],
                request_max_retries=1,
            )
        except Exception as exc:
            logger.error(
                "[Humanize] protocol header repair request failed: %s",
                exc,
                exc_info=True,
            )
            await self._record_protocol_repair_failure(
                event,
                "protocol_repair_request_failed",
                "Header repair provider request failed",
            )
            self._block_response(event, response, "protocol_repair_request_failed")
            return

        self._capture_llm_response_snapshot(event, repair_response, phase="repair")
        repair_duration_ms = max(
            0,
            int((time.perf_counter() - repair_started_at) * 1_000),
        )
        await self._record_llm_usage_sample(
            event,
            repair_response,
            stage="repair",
            duration_ms=repair_duration_ms,
            request_fingerprint=fingerprint(
                {
                    "system_prompt": system_prompt,
                    "prompt": repair_prompt,
                    "model": str(event.get_extra(_MODEL_KEY, "")),
                },
                namespace="humanize-provider-request-v1",
            ),
            prefix_fingerprint=fingerprint(
                {
                    "system_prompt": system_prompt,
                    "model": str(event.get_extra(_MODEL_KEY, "")),
                },
                namespace="humanize-provider-prefix-v1",
            ),
        )
        if (
            not isinstance(repair_response, LLMResponse)
            or repair_response.role != "assistant"
            or repair_response.tools_call_name
            or repair_response.tools_call_args
            or repair_response.tools_call_ids
        ):
            await self._record_protocol_repair_failure(
                event,
                "invalid_protocol_repair_response",
                "Header repair returned a non-assistant or tool response",
            )
            self._block_response(event, response, "invalid_protocol_repair_response")
            return

        try:
            repaired_raw = self._protocol_parser.compose_repaired_response(
                repair_response.completion_text,
                original_body,
            )
        except ProtocolValidationError as exc:
            logger.warning(
                "[Humanize] rejected protocol header repair: %s (%s)",
                exc.code,
                exc.detail,
            )
            await self._record_protocol_repair_failure(event, exc.code, exc.detail)
            self._block_response(event, response, exc.code)
            return

        started_at = event.get_extra(_START_KEY, time.perf_counter())
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
        response_snapshot, response_snapshot_complete = (
            self._response_snapshot_for_record(event)
        )
        try:
            outcome = await self._container.service.process_final_response(
                context,
                repaired_raw,
                model=str(event.get_extra(_MODEL_KEY, "")),
                provider_id=str(event.get_extra(_PROVIDER_ID_KEY, "")),
                duration_ms=duration_ms,
                record_success=False,
                response_snapshot=response_snapshot,
                response_snapshot_complete=response_snapshot_complete,
            )
        except Exception as exc:
            logger.error(
                "[Humanize] repaired response handling failed: %s", exc, exc_info=True
            )
            await self._record_protocol_repair_failure(
                event,
                "response_handling_failed",
                "Repaired response handling failed",
            )
            self._block_response(event, response, "response_handling_failed")
            return

        if not outcome.valid:
            self._block_response(event, response, outcome.error_code)
            return
        event.set_extra(_ERROR_KEY, "")
        event.set_extra(_VALIDATED_OUTPUT_KEY, repaired_raw)
        event.set_extra(_IMAGE_CACHE_KEY, outcome.image_cache)
        event.set_extra(_FINAL_LOG_PENDING_KEY, True)
        # Persist the repaired success immediately: the repair runs inside the
        # agent-done hook, where a subsequent stop_event may prevent the normal
        # decorating-result dispatch from recording the success.
        try:
            await self._record_final_protocol_success(event)
        except Exception:
            logger.exception("[Humanize] failed to persist repaired protocol success")
        if outcome.action is Action.NO_REPLY:
            event.set_extra(_STATE_KEY, EventState.NO_REPLY.value)
            event.set_extra(_MESSAGES_KEY, ())
            event.set_extra(_NO_REPLY_REASON_KEY, outcome.no_reply_reason)
            self._set_response_text(response, _NO_REPLY_SENTINEL)
            return

        clean_text = "\n".join(outcome.messages)
        self._set_response_text(response, clean_text)
        event.set_extra(_MESSAGES_KEY, outcome.messages)
        event.set_extra(_STATE_KEY, EventState.FINAL_VALID.value)

    async def _record_protocol_repair_failure(
        self,
        event: AstrMessageEvent,
        error_code: str,
        error_detail: str,
    ) -> None:
        """Persist a terminal repair failure without affecting response blocking.

        Args:
            event: Active event containing the request context and timing metadata.
            error_code: Stable terminal failure code.
            error_detail: Human-readable failure detail for the dashboard.
        """
        if self._container is None:
            return
        context = event.get_extra(_CONTEXT_KEY)
        recorder = getattr(self._container.service, "record_protocol_failure", None)
        if not isinstance(context, MessageContext) or not callable(recorder):
            return
        started_at = event.get_extra(_START_KEY, time.perf_counter())
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
        response_snapshot, response_snapshot_complete = (
            self._response_snapshot_for_record(event)
        )
        try:
            await recorder(
                context,
                error_code=error_code,
                error_detail=error_detail,
                raw_output=str(event.get_extra(_RAW_OUTPUT_KEY, "")),
                response_snapshot=response_snapshot,
                response_snapshot_complete=response_snapshot_complete,
                model=str(event.get_extra(_MODEL_KEY, "")),
                duration_ms=duration_ms,
                stage="final",
            )
        except Exception:
            logger.exception("[Humanize] failed to persist protocol repair failure")

    async def _record_llm_usage_sample(
        self,
        event: AstrMessageEvent,
        response: LLMResponse | None,
        *,
        stage: str,
        duration_ms: int,
        request_fingerprint: str = "",
        prefix_fingerprint: str = "",
        ttft_ms: int | None = None,
    ) -> None:
        """Persist provider-reported prompt-cache usage without blocking chat.

        Args:
            event: Active message event carrying request and scope metadata.
            response: Untouched provider response, when available.
            stage: Provider call stage such as ``tool``, ``final`` or ``repair``.
            duration_ms: Measured provider-call duration.
            request_fingerprint: Optional override for an isolated repair call.
            prefix_fingerprint: Optional prefix override for an isolated call.
            ttft_ms: Optional isolated-call time to first response.
        """
        if self._container is None:
            return
        context = event.get_extra(_CONTEXT_KEY)
        if not isinstance(context, MessageContext):
            return
        raw_usage = None
        raw_completion = getattr(response, "raw_completion", None)
        if raw_completion is not None:
            raw_usage = getattr(raw_completion, "usage", None)
        response_usage = getattr(response, "usage", None)
        usage = usage_dict(response_usage)
        # Some adapters construct an empty TokenUsage even when the upstream
        # response omitted usage. Treat that as unknown instead of a measured
        # zero-cache sample; raw usage or positive normalized tokens is evidence.
        observed_usage = usage_observed(response_usage, raw_usage=raw_usage)
        captured_identity = event.get_extra(_PROVIDER_IDENTITY_KEY, {})
        identity = (
            dict(captured_identity) if isinstance(captured_identity, dict) else {}
        )
        if not identity:
            try:
                provider = self.context.get_using_provider(event.unified_msg_origin)
                if provider is not None:
                    identity = provider_identity(provider)
            except Exception:
                identity = {}
        if not identity.get("provider_id"):
            identity["provider_id"] = str(event.get_extra(_PROVIDER_ID_KEY, "") or "")
        capability = str(identity.get("prompt_cache_capability") or "unknown")
        if (
            capability == "unknown"
            and observed_usage
            and (usage or {}).get("input_cached", 0) > 0
        ):
            capability = "implicit"
        if ttft_ms is None:
            started_at = event.get_extra(_START_KEY)
            first_response_at = event.get_extra(_FIRST_RESPONSE_AT_KEY)
            if isinstance(started_at, (int, float)) and isinstance(
                first_response_at, (int, float)
            ):
                ttft_ms = max(0, int((first_response_at - started_at) * 1_000))
        try:
            await self._container.repository.record_llm_usage_sample(
                request_id=context.request_id,
                stage=stage,
                scope_type=context.scope_type,
                scope_id=context.scope_id,
                conversation_id=context.conversation_id,
                provider_id=str(identity.get("provider_id") or ""),
                provider_type=str(identity.get("provider_type") or ""),
                model=str(identity.get("model") or event.get_extra(_MODEL_KEY, "")),
                provider_cache_capability=capability,
                epoch_id=str(event.get_extra(_PREFIX_EPOCH_KEY, "")),
                request_fingerprint=request_fingerprint
                or str(event.get_extra(_REQUEST_FINGERPRINT_KEY, "")),
                prefix_fingerprint=prefix_fingerprint
                or str(event.get_extra(_PREFIX_FINGERPRINT_KEY, "")),
                first_difference=str(event.get_extra(_PREFIX_FIRST_DIFFERENCE_KEY, "")),
                longest_common_prefix_chars=int(
                    event.get_extra(_PREFIX_COMMON_CHARS_KEY, 0) or 0
                ),
                epoch_reason=str(event.get_extra(_PREFIX_EPOCH_REASON_KEY, "")),
                cache_observability=(
                    "unsupported"
                    if capability == "unsupported"
                    else "observable"
                    if observed_usage
                    else "unknown"
                ),
                input_cached=(usage or {}).get("input_cached", 0),
                input_other=(usage or {}).get("input_other", 0),
                output_tokens=(usage or {}).get("output", 0),
                usage_observed=observed_usage,
                duration_ms=duration_ms,
                ttft_ms=ttft_ms,
            )
        except Exception:
            logger.exception("[Humanize] failed to persist LLM cache usage sample")

    async def _record_final_protocol_success(self, event: AstrMessageEvent) -> bool:
        """Persist a final success exactly once after dispatch reaches a terminal state.

        Args:
            event: Active event carrying the validated response and request metadata.

        Returns:
            ``True`` when no write is pending or persistence succeeds.
        """
        if not event.get_extra(_FINAL_LOG_PENDING_KEY, False):
            return True
        if self._container is None:
            return False
        if event.get_extra(_CONTEXT_WINDOW_ACTIVE_KEY, False):
            await self._persist_context_window(event)
        context = event.get_extra(_CONTEXT_KEY)
        recorder = getattr(self._container.service, "record_protocol_success", None)
        if not isinstance(context, MessageContext) or not callable(recorder):
            return False
        started_at = event.get_extra(_START_KEY, time.perf_counter())
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
        action = (
            Action.NO_REPLY.value
            if event.get_extra(_STATE_KEY) == EventState.NO_REPLY.value
            else Action.REPLY.value
        )
        response_snapshot, response_snapshot_complete = (
            self._response_snapshot_for_record(event)
        )
        try:
            record_kwargs: dict[str, Any] = {
                "action": action,
                "raw_output": str(event.get_extra(_VALIDATED_OUTPUT_KEY, "")),
                "messages": tuple(
                    event.get_extra(_DISPATCHED_MESSAGES_KEY, ())
                    or event.get_extra(_MESSAGES_KEY, ())
                ),
                "no_reply_reason": str(event.get_extra(_NO_REPLY_REASON_KEY, "")),
                "response_snapshot": response_snapshot,
                "response_snapshot_complete": response_snapshot_complete,
                "model": str(event.get_extra(_MODEL_KEY, "")),
                "provider_id": str(event.get_extra(_PROVIDER_ID_KEY, "")),
                "duration_ms": duration_ms,
                "stage": "final",
            }
            if event.get_extra(_CONTEXT_WINDOW_ACTIVE_KEY, False):
                record_kwargs["context_ref"] = str(
                    event.get_extra(_CONTEXT_TURN_REF_KEY, "")
                )
            persisted = await recorder(context, **record_kwargs)
        except Exception:
            logger.exception("[Humanize] failed to persist final protocol success")
            return False
        if persisted is not True:
            logger.error("[Humanize] final protocol success was not persisted")
            return False
        event.set_extra(_FINAL_LOG_PENDING_KEY, False)
        return True

    async def _record_final_protocol_failure(
        self,
        event: AstrMessageEvent,
        error_code: str,
        error_detail: str,
    ) -> bool:
        """Persist a post-parse terminal failure instead of leaving a false success.

        Args:
            event: Active event carrying the validated response and request metadata.
            error_code: Stable terminal failure code.
            error_detail: Human-readable failure detail for the dashboard.

        Returns:
            ``True`` when this terminal failure was already logged or persistence
            succeeds.
        """
        if event.get_extra(_FINAL_FAILURE_LOGGED_KEY, False):
            return True
        if self._container is None:
            return False
        context = event.get_extra(_CONTEXT_KEY)
        recorder = getattr(self._container.service, "record_protocol_failure", None)
        if not isinstance(context, MessageContext) or not callable(recorder):
            return False
        started_at = event.get_extra(_START_KEY, time.perf_counter())
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
        response_snapshot, response_snapshot_complete = (
            self._response_snapshot_for_record(event)
        )
        raw_output = str(event.get_extra(_VALIDATED_OUTPUT_KEY, "")) or str(
            event.get_extra(_RAW_OUTPUT_KEY, "")
        )
        try:
            persisted = await recorder(
                context,
                error_code=error_code,
                error_detail=error_detail,
                raw_output=raw_output,
                messages=tuple(event.get_extra(_DISPATCHED_MESSAGES_KEY, ())),
                response_snapshot=response_snapshot,
                response_snapshot_complete=response_snapshot_complete,
                model=str(event.get_extra(_MODEL_KEY, "")),
                duration_ms=duration_ms,
                stage="final",
            )
        except Exception:
            logger.exception("[Humanize] failed to persist post-parse protocol failure")
            return False
        if persisted is not True:
            logger.error("[Humanize] final protocol failure was not persisted")
            return False
        event.set_extra(_FINAL_LOG_PENDING_KEY, False)
        event.set_extra(_FINAL_FAILURE_LOGGED_KEY, True)
        return True

    async def terminate(self) -> None:
        container = self._container
        self._container = None
        self._uninstall_provider_hooks()
        if container is not None:
            proactive = getattr(container, "proactive", None)
            if proactive is not None:
                await proactive.shutdown()
            await container.memory.stop()
        logger.info("[Humanize] plugin terminated")

    @property
    def _is_active(self) -> bool:
        return (
            self._container is not None
            and self._plugin_config.enabled
            and self._plugin_config.protocol_enabled
        )

    @staticmethod
    def _context_window_token_budget(provider_settings: dict[str, Any]) -> int:
        """Reserve a bounded portion of the active Provider context for history.

        Args:
            provider_settings: Current AstrBot provider settings for this event.

        Returns:
            Approximate token budget used by the managed context window.
        """
        return context_window_token_budget(provider_settings)

    async def _resolve_display_name(self, event: AstrMessageEvent) -> str:
        """Resolve the best user-visible name for the message sender.

        Priority: group card (群名片) -> friend remark (好友备注) -> QQ nickname.
        OneBot message events often omit ``card``, so the group card is looked up
        through the platform adapter when the sender is in a group.

        Args:
            event: Active event carrying sender and platform identity.

        Returns:
            The resolved display name, or a fallback identifier.
        """
        fallback = event.get_sender_name() or event.get_sender_id() or "当前用户"
        is_group = not event.is_private_chat()
        try:
            platform = self.context.get_platform_inst(event.get_platform_id())
            bot = getattr(platform, "bot", None)
            if bot is None or not callable(getattr(bot, "call_action", None)):
                return fallback
            sender_id = event.get_sender_id()
            if not sender_id:
                return fallback
            if is_group:
                group_id = getattr(event.message_obj, "group_id", "") or ""
                if not group_id:
                    return fallback
                info = await bot.call_action(
                    action="get_group_member_info",
                    group_id=group_id,
                    user_id=int(sender_id),
                    no_cache=False,
                )
                card = (
                    str(info.get("card") or "").strip()
                    if isinstance(info, dict)
                    else ""
                )
                if card:
                    return card
            # 好友备注（get_stranger_info 的 nick 为备注优先）
            info = await bot.call_action(
                action="get_stranger_info",
                user_id=int(sender_id),
                no_cache=False,
            )
            remark = (
                str(info.get("nick") or info.get("nickname") or "").strip()
                if isinstance(info, dict)
                else ""
            )
            return remark or fallback
        except Exception:
            logger.debug("[Humanize] display name resolution failed", exc_info=True)
            return fallback

    async def _build_message_context(
        self,
        event: AstrMessageEvent,
        user_text: str,
        *,
        conversation_id: str = "",
    ) -> MessageContext:
        scope_type = "private" if event.is_private_chat() else "group"
        sender_name = await self._resolve_display_name(event)
        chat_scene = f"QQ 上和{sender_name}" if scope_type == "private" else "QQ群"
        raw_timestamp = getattr(event.message_obj, "timestamp", None)
        try:
            occurred_at = datetime.fromtimestamp(float(raw_timestamp), UTC).isoformat(
                timespec="seconds"
            )
        except (TypeError, ValueError, OSError, OverflowError):
            occurred_at = datetime.fromtimestamp(event.created_at, UTC).isoformat(
                timespec="seconds"
            )
        components = getattr(event.message_obj, "message", ()) or ()
        attachment_refs = []
        source_complete = True
        for component in components:
            if isinstance(component, Plain):
                continue
            reference, _ = serialize_attachment_reference(component)
            attachment_refs.append(reference)
            # The reference is auditable, but it is not the original binary
            # content. Keep this explicit so summaries never treat it as L2 text.
            source_complete = False
        return MessageContext(
            request_id=uuid.uuid4().hex,
            scope_type=scope_type,
            scope_id=event.unified_msg_origin,
            message_id=self._message_id(event, user_text),
            sender_id=event.get_sender_id(),
            sender_name=sender_name,
            user_text=user_text,
            chat_scene=chat_scene,
            admin_name=self._plugin_config.admin_name,
            admin_ids=self._plugin_config.admin_qq_ids,
            conversation_id=conversation_id or event.unified_msg_origin,
            occurred_at=occurred_at,
            attachment_refs=tuple(attachment_refs),
            source_complete=source_complete,
        )

    async def _managed_context_for_reset(
        self, event: AstrMessageEvent
    ) -> MessageContext:
        """Resolve the same scoped identity that the managed window uses.

        Args:
            event: Current command event.

        Returns:
            Trusted current conversation metadata with the effective Agent ID.
        """
        raw_user = str(event.get_message_str() or "")
        message_context = await self._build_message_context(event, raw_user)
        conversation_persona_id: str | None = None
        try:
            conversation_id = (
                await self.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
            )
            if conversation_id:
                conversation = await self.context.conversation_manager.get_conversation(
                    event.unified_msg_origin,
                    conversation_id,
                )
                conversation_persona_id = getattr(conversation, "persona_id", None)
                message_context = replace(
                    message_context,
                    conversation_id=str(conversation_id),
                )
        except Exception:
            logger.exception("[Humanize] failed to resolve reset conversation")

        try:
            provider_settings = self.context.get_config(
                umo=event.unified_msg_origin
            ).get("provider_settings", {})
            if not isinstance(provider_settings, dict):
                provider_settings = {}
            (
                persona_id,
                _,
                _,
                use_webchat_default,
            ) = await self.context.persona_manager.resolve_selected_persona(
                umo=event.unified_msg_origin,
                conversation_persona_id=conversation_persona_id,
                platform_name=event.get_platform_name(),
                provider_settings=provider_settings,
            )
            message_context = replace(
                message_context,
                agent_id=(
                    "_chatui_default_"
                    if use_webchat_default
                    else str(persona_id or "default")
                ),
            )
        except Exception:
            logger.exception("[Humanize] failed to resolve reset persona")
        return message_context

    async def _maybe_record_ambient(self, event: AstrMessageEvent) -> None:
        """Record unaddressed group chatter into a rolling ledger.

        Plugin EventMessageType filters wake the event (``is_wake=True``) so
        un-@ group messages still reach this hook, but AstrBot only runs the
        LLM when ``is_at_or_wake_command`` is true. Those unaddressed lines
        must not enter the managed window (that would compact and spend
        tokens). Fail-open on test fakes that omit group/message metadata.

        Args:
            event: Incoming message event, possibly without a full AstrBot
                surface.
        """
        if self._container is None:
            return
        appender = getattr(
            getattr(self._container, "context_window", None),
            "append_ambient",
            None,
        )
        if not callable(appender):
            return
        is_private = getattr(event, "is_private_chat", None)
        if not callable(is_private):
            return
        try:
            if is_private():
                return
        except Exception:
            return
        if getattr(event, "is_at_or_wake_command", False):
            return
        message_obj = getattr(event, "message_obj", None)
        if message_obj is None:
            return
        self_id_getter = getattr(event, "get_self_id", None)
        sender_id_getter = getattr(event, "get_sender_id", None)
        if callable(self_id_getter) and callable(sender_id_getter):
            try:
                self_id = str(self_id_getter() or "")
                sender_id = str(sender_id_getter() or "")
            except Exception:
                self_id = ""
                sender_id = ""
            if self_id and sender_id and self_id == sender_id:
                return
        try:
            ambient_context, has_image = self._ambient_record_from_event(event)
        except Exception:
            logger.debug("[Humanize] ambient context build skipped", exc_info=True)
            return
        if ambient_context is None:
            return
        try:
            await appender(ambient_context, has_image=has_image)
        except Exception:
            logger.debug("[Humanize] ambient record skipped", exc_info=True)

    async def _maybe_schedule_proactive(self, event: AstrMessageEvent) -> None:
        """Feed one unaddressed group message into the proactive path.

        Addressed messages (``is_at_or_wake_command``) are answered by the
        normal pipeline; everything else either hits a high-precision direct
        trigger (keyword mention or quote-reply to the bot) or starts the
        group's adaptive evaluation window. Fail-open on test fakes that
        omit group/message metadata.

        Args:
            event: Incoming message event, possibly without a full AstrBot
                surface.
        """
        container = self._container
        proactive = getattr(container, "proactive", None) if container else None
        if proactive is None:
            return
        is_private = getattr(event, "is_private_chat", None)
        if not callable(is_private):
            return
        try:
            if is_private():
                return
        except Exception:
            return
        if getattr(event, "is_at_or_wake_command", False):
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if not umo:
            return
        self_id_getter = getattr(event, "get_self_id", None)
        sender_id_getter = getattr(event, "get_sender_id", None)
        if callable(self_id_getter) and callable(sender_id_getter):
            try:
                self_id = str(self_id_getter() or "")
                sender_id = str(sender_id_getter() or "")
            except Exception:
                self_id = ""
                sender_id = ""
            if self_id and sender_id and self_id == sender_id:
                return
        if self._is_proactive_direct_trigger(event, self_id):
            await proactive.on_direct_trigger(umo)
        else:
            await proactive.on_group_chatter(umo)

    def _is_proactive_direct_trigger(
        self, event: AstrMessageEvent, self_id: str
    ) -> bool:
        """Detect high-precision proactive triggers in one group message.

        Args:
            event: Incoming group message event.
            self_id: The bot's own platform id (empty when unavailable).

        Returns:
            ``True`` when the message quotes one of the bot's messages or
            contains a configured keyword (case-insensitive).
        """
        message_obj = getattr(event, "message_obj", None)
        for component in getattr(message_obj, "message", []) or []:
            if type(component).__name__ == "Reply":
                quoted_sender = str(getattr(component, "sender_id", "") or "")
                if self_id and quoted_sender and quoted_sender == self_id:
                    return True
        getter = getattr(event, "get_message_str", None)
        text = ""
        if callable(getter):
            try:
                text = str(getter() or "")
            except Exception:
                return False
        if not text:
            return False
        lowered = text.lower()
        for keyword in self._plugin_config.proactive_keywords:
            token = str(keyword or "").strip().lower()
            if token and token in lowered:
                return True
        return False

    def _ambient_record_from_event(
        self, event: AstrMessageEvent
    ) -> tuple[MessageContext | None, bool]:
        """Build a cheap group-scope context for the ambient ledger.

        Avoids display-name network lookups so unaddressed chatter stays a
        local, zero-LLM append.

        Args:
            event: Incoming group message with a message object.

        Returns:
            Message metadata and whether the chain carried an image, or
            ``(None, False)`` when there is nothing to record.
        """
        message_getter = getattr(event, "get_message_str", None)
        user_text = str(message_getter() or "") if callable(message_getter) else ""
        components = getattr(event.message_obj, "message", ()) or ()
        has_image = any(type(component).__name__ == "Image" for component in components)
        if not user_text.strip() and not has_image:
            return None, False
        raw_timestamp = getattr(event.message_obj, "timestamp", None)
        try:
            occurred_at = datetime.fromtimestamp(float(raw_timestamp), UTC).isoformat(
                timespec="seconds"
            )
        except (TypeError, ValueError, OSError, OverflowError):
            created_at = getattr(event, "created_at", None)
            try:
                occurred_at = datetime.fromtimestamp(float(created_at), UTC).isoformat(
                    timespec="seconds"
                )
            except (TypeError, ValueError, OSError, OverflowError):
                occurred_at = datetime.now(UTC).isoformat(timespec="seconds")
        sender_getter = getattr(event, "get_sender_name", None)
        sender_id_getter = getattr(event, "get_sender_id", None)
        sender_name = (
            str(sender_getter() or "").strip() if callable(sender_getter) else ""
        )
        sender_id = str(sender_id_getter() or "") if callable(sender_id_getter) else ""
        return (
            MessageContext(
                request_id=uuid.uuid4().hex,
                scope_type="group",
                scope_id=event.unified_msg_origin,
                message_id=self._message_id(event, user_text),
                sender_id=sender_id,
                sender_name=sender_name or sender_id or "someone",
                user_text=user_text,
                chat_scene="QQ群",
                admin_name=self._plugin_config.admin_name,
                admin_ids=self._plugin_config.admin_qq_ids,
                conversation_id=event.unified_msg_origin,
                occurred_at=occurred_at,
            ),
            has_image,
        )

    @staticmethod
    def _message_id(event: AstrMessageEvent, user_text: str) -> str:
        raw_id = getattr(event.message_obj, "message_id", "")
        if raw_id not in (None, ""):
            return str(raw_id)
        timestamp = getattr(event.message_obj, "timestamp", "")
        payload = f"{event.unified_msg_origin}|{event.get_sender_id()}|{timestamp}|{user_text}"
        return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _append_prompt(existing: str, addition: str) -> str:
        if not existing.strip():
            return addition
        return f"{existing.rstrip()}\n\n{addition}"

    @staticmethod
    def _capture_llm_response_snapshot(
        event: AstrMessageEvent,
        response: LLMResponse | None,
        *,
        phase: str = "",
    ) -> None:
        """Capture an LLM response before the protocol firewall mutates it.

        Args:
            event: Active event used to retain snapshots until terminal logging.
            response: Untouched provider response received by the hook.
            phase: Optional explicit phase such as ``repair``.
        """
        snapshot, complete = serialize_llm_response(response)
        has_tool_calls = bool(
            response
            and (
                response.tools_call_args
                or response.tools_call_name
                or response.tools_call_ids
                or response.tools_call_extra_content
            )
        )
        is_final = bool(
            not phase and response and not response.is_chunk and not has_tool_calls
        )
        normalized_phase = phase or (
            "final"
            if is_final
            else "chunk"
            if response and response.is_chunk
            else "tool"
        )
        entries = event.get_extra(_RESPONSE_SNAPSHOTS_KEY, [])
        if not isinstance(entries, list):
            entries = []
        entry = {
            "sequence": len(entries) + 1,
            "phase": normalized_phase,
            "is_final": is_final,
            "snapshot_complete": complete,
            "response": snapshot,
        }
        entries = [*entries, entry]
        event.set_extra(_RESPONSE_SNAPSHOTS_KEY, entries)
        if normalized_phase == "final":
            event.set_extra(_FINAL_RESPONSE_SNAPSHOT_KEY, entry)

    @staticmethod
    def _response_snapshot_for_record(
        event: AstrMessageEvent,
    ) -> tuple[dict[str, Any], bool]:
        """Build the full response sequence stored with a terminal protocol log.

        Args:
            event: Active event containing untouched response snapshots.

        Returns:
            JSON-compatible response sequence and its completeness flag.
        """
        entries = event.get_extra(_RESPONSE_SNAPSHOTS_KEY, [])
        if not isinstance(entries, list):
            entries = []
        final_entry = event.get_extra(_FINAL_RESPONSE_SNAPSHOT_KEY)
        complete = bool(
            isinstance(final_entry, dict)
            and final_entry.get("response") is not None
            and all(
                isinstance(entry, dict) and entry.get("snapshot_complete") is True
                for entry in entries
            )
        )
        return {
            "capture_stage": "on_llm_response_firewall",
            "responses": entries,
            "final_response": final_entry,
        }, complete

    def _install_send_gate(self, event: AstrMessageEvent) -> None:
        """Install a per-event outbound protocol gate.

        Args:
            event: The message event whose direct sends must be guarded.
        """
        existing_owner = event.get_extra(_SEND_GATE_OWNER_KEY)
        if event.get_extra(_SEND_GATE_KEY, False):
            if existing_owner is self:
                return
            original_send = event.get_extra(_ORIGINAL_SEND_KEY)
            if not callable(original_send):
                logger.error(
                    "[Humanize] cannot replace a stale outbound gate without its "
                    "original sender"
                )
                event.set_extra(_SEND_GATE_ERROR_KEY, True)
                return
        else:
            original_send = event.send

        async def guarded_send(message: MessageChain | None) -> None:
            if self._container is None:
                # Hot reload detaches the old plugin instance while already-created
                # events may still retain this closure. Hand the send to a newly
                # installed owner when possible; otherwise pass ordinary text
                # through while keeping protocol tags fail-closed.
                current_owner = event.get_extra(_SEND_GATE_OWNER_KEY)
                current_sender = getattr(event, "send", None)
                if (
                    current_owner is not self
                    and callable(current_sender)
                    and current_sender is not guarded_send
                ):
                    await current_sender(message)
                    return
                if message is not None and _CONTROL_TAG_PATTERN.search(
                    message.get_plain_text()
                ):
                    return
                await original_send(message)
                return
            if message is None:
                await original_send(message)
                return

            state = event.get_extra(_STATE_KEY, EventState.INACTIVE.value)
            if state == EventState.INACTIVE.value:
                await original_send(message)
                return

            if not message.chain:
                await original_send(message)
                return

            if message.type == "agent_stats":
                await original_send(message)
                return
            if message.type == "tool_call":
                return

            if state in {EventState.FINAL_VALID.value, EventState.NO_REPLY.value}:
                await self._dispatch_terminal_result(event, message)
                return
            if state in {
                EventState.FINAL_BLOCKED.value,
                EventState.DISPATCHED.value,
            }:
                return
            if state in {
                EventState.REQUESTED.value,
                EventState.TOOL_RUNNING.value,
            }:
                await self._process_tool_stage_chain(event, message)

        try:
            event.set_extra(_ORIGINAL_SEND_KEY, original_send)
            event.set_extra(_SEND_GATE_OWNER_KEY, self)
            setattr(event, "send", guarded_send)
        except (AttributeError, TypeError):
            logger.exception("[Humanize] failed to install outbound send gate")
            event.set_extra(_SEND_GATE_ERROR_KEY, True)
            return
        event.set_extra(_SEND_GATE_KEY, True)

    async def _record_tool_protocol_failure(
        self,
        event: AstrMessageEvent,
        context: MessageContext,
        service: Any,
        *,
        error_code: str,
        error_detail: str,
        raw_output: str,
        messages: tuple[str, ...] = (),
    ) -> bool:
        """Persist one audited tool-stage failure with its response sequence.

        Args:
            event: Active event carrying timing and response snapshots.
            context: Trusted request context for the protocol log.
            service: Humanize service instance that owns persistence retries.
            error_code: Stable machine-readable failure code.
            error_detail: Human-readable diagnostic detail.
            raw_output: Raw tool-stage response associated with the failure.
            messages: Text delivered before the failure, if any.

        Returns:
            ``True`` only when the failure record was persisted.
        """
        recorder = getattr(service, "record_protocol_failure", None)
        if not callable(recorder):
            return False
        response_snapshot, response_snapshot_complete = (
            self._response_snapshot_for_record(event)
        )
        try:
            persisted = await recorder(
                context,
                error_code=error_code,
                error_detail=error_detail,
                raw_output=raw_output,
                messages=messages,
                response_snapshot=response_snapshot,
                response_snapshot_complete=response_snapshot_complete,
                model=str(event.get_extra(_MODEL_KEY, "")),
                duration_ms=max(
                    0,
                    int(
                        (
                            time.perf_counter()
                            - event.get_extra(_START_KEY, time.perf_counter())
                        )
                        * 1_000
                    ),
                ),
                stage="tool",
            )
        except Exception:
            logger.exception("[Humanize] failed to persist tool-stage failure")
            return False
        if persisted is not True:
            logger.error("[Humanize] tool-stage failure was not persisted")
            return False
        return True

    async def _process_tool_stage_payload(
        self,
        event: AstrMessageEvent,
        raw_output: str,
        source_chain: MessageChain | None = None,
    ) -> None:
        """Serialize tool-stage validation and dispatch for one event.

        Args:
            event: Active event carrying the tool-stage state.
            raw_output: Raw text proposed for direct delivery.
            source_chain: Original mixed-media chain, when available.
        """
        raw = raw_output or ""
        if not raw.strip():
            return
        lock = event.get_extra(_TOOL_SEND_LOCK_KEY)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            event.set_extra(_TOOL_SEND_LOCK_KEY, lock)
        handoff: MessageChain | None = None
        async with lock:
            if (
                self._container is None
                and event.get_extra(_SEND_GATE_OWNER_KEY) is not self
                and source_chain is not None
            ):
                handoff = source_chain
            else:
                await self._process_tool_stage_payload_locked(
                    event,
                    raw,
                    source_chain=source_chain,
                )
        if handoff is not None:
            await event.send(handoff)

    async def _process_tool_stage_payload_locked(
        self,
        event: AstrMessageEvent,
        raw_output: str,
        source_chain: MessageChain | None = None,
    ) -> None:
        """Validate and dispatch one tool payload while holding its event lock.

        Args:
            event: Active event carrying atomic tool delivery state.
            raw_output: Non-empty raw response text.
            source_chain: Original mixed-media chain, when available.
        """
        raw = raw_output or ""
        if not self._is_active:
            if self._container is None and source_chain is not None:
                if not _CONTROL_TAG_PATTERN.search(raw):
                    await self._send_chain(event, source_chain)
            return
        if event.get_extra(_STATE_KEY) not in {
            EventState.REQUESTED.value,
            EventState.TOOL_RUNNING.value,
        }:
            # The state can advance while this payload waits for another tool
            # delivery. Once terminal dispatch owns the event, queued tool text
            # must not appear after the final response.
            return
        context = event.get_extra(_CONTEXT_KEY)
        if not isinstance(context, MessageContext):
            return

        container = self._container
        if container is None:
            if source_chain is not None:
                await self._send_chain(event, source_chain)
            return

        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
        replacements = event.get_extra(_TOOL_HISTORY_KEY, {})
        if not isinstance(replacements, dict):
            replacements = {}
            event.set_extra(_TOOL_HISTORY_KEY, replacements)
        processed = event.get_extra(_TOOL_PROCESSED_KEY)
        if not isinstance(processed, set):
            processed = set()
            event.set_extra(_TOOL_PROCESSED_KEY, processed)

        media_components = (
            tuple(
                component
                for component in source_chain.chain
                if not isinstance(component, Plain)
            )
            if source_chain is not None
            else ()
        )
        sent_media = event.get_extra(_TOOL_SENT_MEDIA_KEY, [])
        if not isinstance(sent_media, list):
            sent_media = []
        remaining_sent_media = [str(item) for item in sent_media]
        outbound_media = []
        outbound_media_keys: list[str] = []
        for component in media_components:
            media_key = repr(component)
            try:
                remaining_sent_media.remove(media_key)
            except ValueError:
                outbound_media.append(component)
                outbound_media_keys.append(media_key)

        if digest in processed:
            if replacements.get(digest) and outbound_media and source_chain is not None:
                try:
                    await self._send_chain(event, source_chain.derive(outbound_media))
                except Exception:
                    logger.exception(
                        "[Humanize] repeated tool-stage media dispatch failed"
                    )
                    await self._record_tool_protocol_failure(
                        event,
                        context,
                        container.service,
                        error_code="response_dispatch_failed",
                        error_detail="Repeated tool-stage media could not be sent",
                        raw_output=raw,
                    )
                    return
                sent_media.extend(outbound_media_keys)
                event.set_extra(_TOOL_SENT_MEDIA_KEY, sent_media)
            return

        started_at = event.get_extra(_START_KEY, time.perf_counter())
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
        try:
            outcome = await container.service.process_final_response(
                context,
                raw,
                model=str(event.get_extra(_MODEL_KEY, "")),
                provider_id=str(event.get_extra(_PROVIDER_ID_KEY, "")),
                duration_ms=duration_ms,
                stage="tool",
                record_success=False,
            )
        except Exception:
            logger.exception("[Humanize] tool-stage response handling failed")
            replacements[digest] = ""
            await self._record_tool_protocol_failure(
                event,
                context,
                container.service,
                error_code="response_handling_failed",
                error_detail="Tool-stage response handling raised an exception",
                raw_output=raw,
            )
            return

        if not outcome.valid:
            replacements[digest] = ""
            processed.add(digest)
            logger.warning(
                "[Humanize] suppressed tool-stage text without a valid Action: %s",
                outcome.error_code,
            )
            return

        clean_text = "\n".join(outcome.messages)
        if _CONTROL_TAG_PATTERN.search(clean_text):
            logger.warning(
                "[Humanize] suppressed tool-stage response containing control tags"
            )
            replacements[digest] = ""
            processed.add(digest)
            await self._record_tool_protocol_failure(
                event,
                context,
                container.service,
                error_code="tool_response_control_tag_leak",
                error_detail="Validated tool-stage text exposed protocol control tags",
                raw_output=raw,
            )
            return
        replacements[digest] = clean_text
        outbound_messages = (
            self._without_tool_duplicates(
                event, tuple(str(message) for message in outcome.messages)
            )
            if outcome.action is Action.REPLY
            else ()
        )
        dispatched_before = event.get_extra(_DISPATCHED_MESSAGES_KEY, [])
        dispatched_count = (
            len(dispatched_before) if isinstance(dispatched_before, list) else 0
        )
        sent_chain = False
        try:
            if outcome.action is Action.REPLY and source_chain is None:
                if outbound_messages:
                    await self._send_messages(event, outbound_messages)
                    sent_chain = True
            elif outcome.action is Action.REPLY and source_chain is not None:
                # 媒体混合时也保留分段：文本逐条发送（_send_messages），
                # 媒体组件单独一条链发送
                if outbound_messages:
                    await self._send_messages(event, outbound_messages)
                    sent_chain = True
                remaining_outbound_media = list(outbound_media_keys)
                media_components = []
                for component in source_chain.chain:
                    if isinstance(component, Plain):
                        continue
                    media_key = repr(component)
                    try:
                        remaining_outbound_media.remove(media_key)
                    except ValueError:
                        continue
                    else:
                        media_components.append(component)
                if media_components:
                    await self._send_chain(event, source_chain.derive(media_components))
                    sent_chain = True
        except Exception:
            logger.exception("[Humanize] tool-stage response dispatch failed")
            dispatched = event.get_extra(_DISPATCHED_MESSAGES_KEY, [])
            delivered = (
                tuple(str(item) for item in dispatched[dispatched_count:])
                if isinstance(dispatched, list)
                else ()
            )
            if delivered:
                sent_messages = event.get_extra(_TOOL_SENT_MESSAGES_KEY, [])
                if not isinstance(sent_messages, list):
                    sent_messages = []
                sent_messages.extend(delivered)
                event.set_extra(_TOOL_SENT_MESSAGES_KEY, sent_messages)
            await self._record_tool_protocol_failure(
                event,
                context,
                container.service,
                error_code="response_dispatch_failed",
                error_detail="Tool-stage response could not be sent",
                raw_output=raw,
                messages=delivered,
            )
            return

        dispatched = event.get_extra(_DISPATCHED_MESSAGES_KEY, [])
        delivered = (
            tuple(str(item) for item in dispatched[dispatched_count:])
            if isinstance(dispatched, list)
            else ()
        )
        if sent_chain and outbound_messages:
            sent_messages = event.get_extra(_TOOL_SENT_MESSAGES_KEY, [])
            if not isinstance(sent_messages, list):
                sent_messages = []
            sent_messages.extend(outbound_messages)
            event.set_extra(_TOOL_SENT_MESSAGES_KEY, sent_messages)
        if sent_chain and source_chain is not None:
            sent_media.extend(outbound_media_keys)
            event.set_extra(_TOOL_SENT_MEDIA_KEY, sent_media)
        if outcome.action is Action.REPLY and not sent_chain:
            processed.add(digest)
            return

        processed.add(digest)
        recorder = getattr(container.service, "record_protocol_success", None)
        if callable(recorder):
            response_snapshot, response_snapshot_complete = (
                self._response_snapshot_for_record(event)
            )
            try:
                persisted = await recorder(
                    context,
                    action=outcome.action.value,
                    raw_output=raw,
                    messages=delivered,
                    response_snapshot=response_snapshot,
                    response_snapshot_complete=response_snapshot_complete,
                    model=str(event.get_extra(_MODEL_KEY, "")),
                    provider_id=str(event.get_extra(_PROVIDER_ID_KEY, "")),
                    duration_ms=max(
                        0,
                        int(
                            (
                                time.perf_counter()
                                - event.get_extra(_START_KEY, time.perf_counter())
                            )
                            * 1_000
                        ),
                    ),
                    stage="tool",
                )
                if persisted is not True:
                    logger.error("[Humanize] tool-stage success was not persisted")
            except Exception:
                logger.exception("[Humanize] failed to persist tool-stage success")

    async def _process_tool_stage_chain(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
    ) -> None:
        """Validate text in a tool-stage chain while preserving media components.

        Args:
            event: Active message event.
            message: Tool-stage message chain to inspect and possibly send.
        """
        if not any(isinstance(component, Plain) for component in message.chain):
            await self._send_chain(event, message)
            return
        await self._process_tool_stage_payload(
            event,
            message.get_plain_text(),
            source_chain=message,
        )

    async def _send_messages(
        self,
        event: AstrMessageEvent,
        messages: tuple[str, ...],
    ) -> None:
        """Send validated messages without reopening the public send gate.

        Args:
            event: The active message event.
            messages: Validated plain-text messages to send in order.

        Raises:
            RuntimeError: If a gate is installed without its original sender.
        """
        sender = event.get_extra(_ORIGINAL_SEND_KEY)
        if not callable(sender):
            if event.get_extra(_SEND_GATE_KEY, False):
                raise RuntimeError("outbound send gate is missing its original sender")
            sender = event.send
        for index, message in enumerate(messages):
            if index and self._plugin_config.message_interval_seconds:
                await asyncio.sleep(self._plugin_config.message_interval_seconds)
            await sender(MessageChain([Plain(message)]))
            await self._track_bot_reply(event, message)
            dispatched = event.get_extra(_DISPATCHED_MESSAGES_KEY, [])
            if not isinstance(dispatched, list):
                dispatched = []
            dispatched.append(message)
            event.set_extra(_DISPATCHED_MESSAGES_KEY, dispatched)

    async def _track_bot_reply(self, event: AstrMessageEvent, message: str) -> None:
        """Record one dispatched reply for proactive follow-up bookkeeping.

        Args:
            event: The event whose reply was just delivered.
            message: The message text that was actually sent.
        """
        container = self._container
        proactive = getattr(container, "proactive", None) if container else None
        if proactive is None:
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if not umo:
            return
        try:
            await proactive.record_bot_reply(umo, message, interactive=True)
        except Exception:
            logger.debug("[Humanize] proactive reply tracking failed", exc_info=True)

    @staticmethod
    def _without_tool_duplicates(
        event: AstrMessageEvent,
        messages: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Remove messages already delivered earlier in the same tool turn.

        Args:
            event: Active event carrying the tool-stage delivery history.
            messages: Validated messages in their original order.

        Returns:
            Messages that still need to be sent.
        """
        sent = event.get_extra(_TOOL_SENT_MESSAGES_KEY, [])
        if not isinstance(sent, list) or not sent:
            return messages
        remaining_sent = [str(item) for item in sent]
        remaining: list[str] = []
        for message in messages:
            try:
                remaining_sent.remove(message)
            except ValueError:
                remaining.append(message)
        return tuple(remaining)

    async def _send_chain(
        self,
        event: AstrMessageEvent,
        message: MessageChain,
    ) -> None:
        """Send one validated or media-only chain through the original sender.

        Args:
            event: Active message event.
            message: Message chain that already passed the applicable gate.

        Raises:
            RuntimeError: If a gate is installed without its original sender.
        """
        sender = event.get_extra(_ORIGINAL_SEND_KEY)
        if not callable(sender):
            if event.get_extra(_SEND_GATE_KEY, False):
                raise RuntimeError("outbound send gate is missing its original sender")
            sender = event.send
        await sender(message)
        plain_text = message.get_plain_text()
        if plain_text:
            dispatched = event.get_extra(_DISPATCHED_MESSAGES_KEY, [])
            if not isinstance(dispatched, list):
                dispatched = []
            dispatched.append(plain_text)
            event.set_extra(_DISPATCHED_MESSAGES_KEY, dispatched)

    def _block_response(
        self,
        event: AstrMessageEvent,
        response: LLMResponse | None,
        error_code: str,
    ) -> None:
        event.set_extra(_STATE_KEY, EventState.FINAL_BLOCKED.value)
        event.set_extra(_ERROR_KEY, error_code)
        self._set_response_text(response, "")
        event.clear_result()
        event.stop_event()

    @staticmethod
    def _set_response_text(response: LLMResponse | None, text: str) -> None:
        if response is not None:
            response.result_chain = MessageChain([Plain(text)]) if text else None
            response.completion_text = text

    @staticmethod
    def _locate_current_user_message(
        run_context: Any, wrapped_prompt: str
    ) -> int | None:
        """Find the current user message by content, falling back to the last one.

        Args:
            run_context: Agent run context holding the message sequence.
            wrapped_prompt: Wrapped provider prompt of the current user message.

        Returns:
            Message index, or ``None`` when no user message exists.
        """
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list):
            return None
        prompt = str(wrapped_prompt or "").strip()
        if prompt:
            for index in range(len(messages) - 1, -1, -1):
                message = messages[index]
                if getattr(message, "role", None) != "user":
                    continue
                content = getattr(message, "content", None)
                if content == prompt:
                    return index
                if isinstance(content, list):
                    for part in content:
                        if (
                            getattr(part, "type", "") == "text"
                            and getattr(part, "text", None) == prompt
                        ):
                            return index
        for index in range(len(messages) - 1, -1, -1):
            if getattr(messages[index], "role", None) == "user":
                return index
        return None

    @staticmethod
    def _restore_current_user_message(
        run_context: Any, wrapped_prompt: str, original_prompt: str
    ) -> int | None:
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list):
            return None
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if getattr(message, "role", None) != "user":
                continue
            content = getattr(message, "content", None)
            if content == wrapped_prompt:
                message.content = original_prompt
                return index
            if isinstance(content, list):
                for part in content:
                    if (
                        getattr(part, "type", "") == "text"
                        and getattr(part, "text", None) == wrapped_prompt
                    ):
                        part.text = original_prompt
                        return index
        return None

    @classmethod
    def _sanitize_tool_assistant_messages(
        cls,
        run_context: Any,
        *,
        user_index: int,
        replacements: Any,
    ) -> None:
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list) or not isinstance(replacements, dict):
            return
        for message in messages[user_index + 1 :]:
            if (
                getattr(message, "role", None) != "assistant"
                or getattr(message, "tool_calls", None) is None
            ):
                continue
            raw_text = cls._assistant_message_text(message)
            digest = hashlib.sha256(
                raw_text.encode("utf-8", errors="replace")
            ).hexdigest()
            if digest in replacements:
                cls._set_assistant_message_text(message, str(replacements[digest]))

    @classmethod
    def _replace_current_assistant_message(
        cls,
        run_context: Any,
        *,
        user_index: int,
        raw_output: str,
        clean_text: str,
    ) -> Any | None:
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list):
            return None
        for index in range(len(messages) - 1, user_index, -1):
            message = messages[index]
            if getattr(message, "role", None) != "assistant":
                continue
            if raw_output:
                if cls._assistant_message_text(message) != raw_output:
                    continue
            elif index != len(messages) - 1:
                continue
            if not clean_text:
                messages.pop(index)
                return None
            cls._set_assistant_message_text(message, clean_text)
            return message
        return None

    @staticmethod
    def _assistant_message_text(message: Any) -> str:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(getattr(part, "text", ""))
                for part in content
                if getattr(part, "type", "") == "text"
            )
        return ""

    @staticmethod
    def _set_assistant_message_text(message: Any, text: str) -> None:
        content = getattr(message, "content", None)
        if isinstance(content, list):
            preserved = [
                part for part in content if getattr(part, "type", "") == "think"
            ]
            if text:
                preserved.append(TextPart(text=text))
            message.content = preserved or (
                None if getattr(message, "tool_calls", None) is not None else ""
            )
        else:
            message.content = (
                text if text or getattr(message, "tool_calls", None) is None else None
            )
