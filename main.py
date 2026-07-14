from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart

from .humanize.config import PluginConfig
from .humanize.container import Container
from .humanize.domain.errors import ProtocolValidationError
from .humanize.domain.models import Action, EventState, MessageContext
from .humanize.protocol.splitter import enforce_message_limits

PLUGIN_NAME = "astrbot_plugin_humanize"
_STATE_KEY = "_humanize_state"
_CONTEXT_KEY = "_humanize_context"
_MESSAGES_KEY = "_humanize_messages"
_START_KEY = "_humanize_started_at"
_MODEL_KEY = "_humanize_model"
_ERROR_KEY = "_humanize_protocol_error"
_MESSAGE_XML_KEY = "_humanize_message_xml"
_RAW_OUTPUT_KEY = "_humanize_raw_output"
_ASSISTANT_MESSAGE_KEY = "_humanize_assistant_message"
_HISTORY_SYNC_KEY = "_humanize_history_sync_required"
_TOOL_HISTORY_KEY = "_humanize_tool_history_replacements"
_SEND_GATE_KEY = "_humanize_send_gate_installed"
_SEND_GATE_ERROR_KEY = "_humanize_send_gate_error"
_ORIGINAL_SEND_KEY = "_humanize_original_send"
_FIREWALL_PRIORITY = 100_000
_DISPATCH_PRIORITY = 10_000
_FINALIZER_PRIORITY = -100_000
_NO_REPLY_SENTINEL = " "


class HumanizePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.context = context
        self._plugin_config = PluginConfig.from_mapping(config)
        self._container: Container | None = None

    async def initialize(self) -> None:
        self._container = Container.build(self._plugin_config)
        await self._container.repository.initialize()
        self.context.register_web_api(
            f"{PLUGIN_NAME}/<path:subpath>",
            self._container.web_api.dispatch,
            ["GET", "POST"],
            "Humanize management API",
        )
        logger.info("[Humanize] plugin initialized")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=_FIREWALL_PRIORITY)
    async def prepare_message_event(self, event: AstrMessageEvent) -> None:
        if self._is_active:
            event.set_extra("enable_streaming", False)
            self._install_send_gate(event)

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

        user_text = (
            req.prompt if req.prompt is not None else event.get_message_str()
        ) or ""
        message_context = self._build_message_context(event, user_text)
        try:
            prepared = await self._container.service.prepare_request(message_context)
        except Exception as exc:
            logger.error(
                "[Humanize] request preparation failed: %s", exc, exc_info=True
            )
            event.set_extra(_STATE_KEY, EventState.FINAL_BLOCKED.value)
            event.set_extra(_ERROR_KEY, "request_preparation_failed")
            event.clear_result()
            event.stop_event()
            return

        req.prompt = prepared.message_xml
        req.system_prompt = self._append_prompt(
            req.system_prompt, prepared.protocol_prompt
        )
        req.extra_user_content_parts.append(
            TextPart(text=prepared.known_terms_xml).mark_as_temp()
        )

        event.set_extra(_STATE_KEY, EventState.REQUESTED.value)
        event.set_extra(_CONTEXT_KEY, message_context)
        event.set_extra(_MESSAGES_KEY, ())
        event.set_extra(_START_KEY, time.perf_counter())
        event.set_extra(_MODEL_KEY, req.model or "")
        event.set_extra(_MESSAGE_XML_KEY, prepared.message_xml)
        event.set_extra(_RAW_OUTPUT_KEY, "")
        event.set_extra(_ASSISTANT_MESSAGE_KEY, None)
        event.set_extra(_HISTORY_SYNC_KEY, req.conversation is not None)
        event.set_extra(_TOOL_HISTORY_KEY, {})

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

        try:
            outcome = await self._container.service.process_final_response(
                context,
                raw_output,
                model=model,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.error(
                "[Humanize] final response handling failed: %s", exc, exc_info=True
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
            self._block_response(event, response, outcome.error_code)
            return

        if outcome.action is Action.NO_REPLY:
            event.set_extra(_STATE_KEY, EventState.NO_REPLY.value)
            event.set_extra(_MESSAGES_KEY, ())
            self._set_response_text(response, _NO_REPLY_SENTINEL)
            return

        history_text = "\n".join(outcome.messages)
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
        if state in {EventState.REQUESTED.value, EventState.TOOL_RUNNING.value}:
            self._block_response(event, response, "response_firewall_not_applied")
            state = EventState.FINAL_BLOCKED.value
        if state not in {
            EventState.FINAL_VALID.value,
            EventState.FINAL_BLOCKED.value,
            EventState.NO_REPLY.value,
        }:
            return

        if not event.get_extra(_HISTORY_SYNC_KEY, False):
            return

        context = event.get_extra(_CONTEXT_KEY)
        message_xml = str(event.get_extra(_MESSAGE_XML_KEY, ""))
        if not isinstance(context, MessageContext) or not message_xml:
            self._block_response(event, response, "missing_history_context")
            return

        user_index = self._restore_current_user_message(
            run_context, message_xml, context.user_text
        )
        if user_index is None:
            logger.warning("[Humanize] current user history message was not found")
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

    @filter.on_decorating_result(priority=_DISPATCH_PRIORITY)
    async def dispatch_response(self, event: AstrMessageEvent) -> None:
        state = event.get_extra(_STATE_KEY, EventState.INACTIVE.value)
        if state == EventState.FINAL_VALID.value:
            result = event.get_result()
            if event.is_stopped() or not result or not result.chain:
                return
            if not all(isinstance(component, Plain) for component in result.chain):
                event.set_extra(_STATE_KEY, EventState.DISPATCHED.value)
                return

            original_messages = tuple(event.get_extra(_MESSAGES_KEY, ()))
            rendered_text = result.get_plain_text()
            if rendered_text == "\n".join(original_messages):
                outbound = original_messages
            else:
                try:
                    outbound = enforce_message_limits(
                        [
                            component.text
                            for component in result.chain
                            if component.text.strip()
                        ],
                        max_chars=self._plugin_config.max_message_chars,
                        max_messages=self._plugin_config.max_reply_messages,
                        split_long_messages=self._plugin_config.split_long_messages,
                    )
                except ProtocolValidationError as exc:
                    logger.warning(
                        "[Humanize] blocked decorated response: %s (%s)",
                        exc.code,
                        exc.detail,
                    )
                    event.set_extra(_STATE_KEY, EventState.FINAL_BLOCKED.value)
                    event.set_extra(_ERROR_KEY, exc.code)
                    event.clear_result()
                    event.stop_event()
                    return
            if not outbound:
                event.clear_result()
                return

            event.clear_result()
            try:
                await self._send_messages(event, outbound)
            finally:
                event.clear_result()
            event.set_extra(_STATE_KEY, EventState.DISPATCHED.value)
            return
        if state in {
            EventState.REQUESTED.value,
            EventState.TOOL_RUNNING.value,
        }:
            result = event.get_result()
            if (
                result
                and result.chain
                and all(isinstance(component, Plain) for component in result.chain)
            ):
                await self._process_tool_stage_payload(
                    event,
                    result.get_plain_text(),
                )
            event.clear_result()
        if state == EventState.FINAL_BLOCKED.value:
            event.clear_result()
        if state == EventState.NO_REPLY.value:
            event.clear_result()

    @filter.on_decorating_result(priority=_FINALIZER_PRIORITY)
    async def finalize_decoration(self, event: AstrMessageEvent) -> None:
        state = event.get_extra(_STATE_KEY, EventState.INACTIVE.value)
        if state in {
            EventState.NO_REPLY.value,
            EventState.DISPATCHED.value,
            EventState.FINAL_BLOCKED.value,
        }:
            event.clear_result()

    async def terminate(self) -> None:
        self._container = None
        logger.info("[Humanize] plugin terminated")

    @property
    def _is_active(self) -> bool:
        return (
            self._container is not None
            and self._plugin_config.enabled
            and self._plugin_config.protocol_enabled
        )

    def _build_message_context(
        self, event: AstrMessageEvent, user_text: str
    ) -> MessageContext:
        scope_type = "private" if event.is_private_chat() else "group"
        sender_name = event.get_sender_name() or event.get_sender_id() or "当前用户"
        chat_scene = "QQ 上和当前用户" if scope_type == "private" else "QQ群"
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

    def _install_send_gate(self, event: AstrMessageEvent) -> None:
        """Install a per-event outbound protocol gate.

        Args:
            event: The message event whose direct sends must be guarded.
        """
        if event.get_extra(_SEND_GATE_KEY, False):
            return
        original_send = event.send

        async def guarded_send(message: MessageChain | None) -> None:
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

            if state in {
                EventState.REQUESTED.value,
                EventState.TOOL_RUNNING.value,
            } and all(isinstance(component, Plain) for component in message.chain):
                await self._process_tool_stage_payload(
                    event,
                    message.get_plain_text(),
                )

        try:
            event.set_extra(_ORIGINAL_SEND_KEY, original_send)
            setattr(event, "send", guarded_send)
        except (AttributeError, TypeError):
            logger.exception("[Humanize] failed to install outbound send gate")
            event.set_extra(_SEND_GATE_ERROR_KEY, True)
            return
        event.set_extra(_SEND_GATE_KEY, True)

    async def _process_tool_stage_payload(
        self,
        event: AstrMessageEvent,
        raw_output: str,
    ) -> None:
        raw = raw_output or ""
        if not raw.strip() or not self._is_active:
            return
        context = event.get_extra(_CONTEXT_KEY)
        if not isinstance(context, MessageContext):
            return

        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
        replacements = event.get_extra(_TOOL_HISTORY_KEY, {})
        if not isinstance(replacements, dict):
            replacements = {}
            event.set_extra(_TOOL_HISTORY_KEY, replacements)
        if digest in replacements:
            return

        assert self._container is not None
        started_at = event.get_extra(_START_KEY, time.perf_counter())
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1_000))
        try:
            outcome = await self._container.service.process_final_response(
                context,
                raw,
                model=str(event.get_extra(_MODEL_KEY, "")),
                duration_ms=duration_ms,
            )
        except Exception:
            logger.exception("[Humanize] tool-stage response handling failed")
            replacements[digest] = ""
            return

        if not outcome.valid:
            replacements[digest] = ""
            logger.warning(
                "[Humanize] suppressed tool-stage text without a valid Action: %s",
                outcome.error_code,
            )
            return

        clean_text = "\n".join(outcome.messages)
        replacements[digest] = clean_text
        if outcome.action is not Action.REPLY:
            return
        await self._send_messages(event, outcome.messages)

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
        for message in messages:
            await sender(MessageChain([Plain(message)]))

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
    def _restore_current_user_message(
        run_context: Any, message_xml: str, user_text: str
    ) -> int | None:
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list):
            return None
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if getattr(message, "role", None) != "user":
                continue
            content = getattr(message, "content", None)
            if content == message_xml:
                message.content = user_text
                return index
            if isinstance(content, list):
                for part in content:
                    if (
                        getattr(part, "type", "") == "text"
                        and getattr(part, "text", None) == message_xml
                    ):
                        part.text = user_text
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
