from __future__ import annotations

from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from ..config import PluginConfig
from ..domain.models import KnownTerm, MessageContext
from ..domain.prompts import PromptTemplates


class EnvelopeBuilder:
    def __init__(
        self,
        config: PluginConfig,
        templates: PromptTemplates | None = None,
    ) -> None:
        self._config = config
        self._templates = templates or PromptTemplates()

    def set_templates(self, templates: dict[str, str] | PromptTemplates) -> None:
        """Replace all runtime prompt templates atomically.

        Args:
            templates: Validated templates or a complete raw template mapping.

        Raises:
            ValueError: If a raw template is invalid.
        """
        self._templates = (
            templates
            if isinstance(templates, PromptTemplates)
            else PromptTemplates.from_mapping(templates)
        )

    def build_message_xml(self, user_text: str) -> str:
        root = ET.Element("Msg")
        root.text = user_text
        return ET.tostring(root, encoding="unicode", short_empty_elements=True)

    def build_known_terms_xml(self, terms: tuple[KnownTerm, ...]) -> str:
        """Build the compact known-terms block injected before each turn.

        One term per line: ``词[ /别名]：释义``. No nested metadata (confidence,
        status, scope) — those are for humans in the WebUI, not for the model;
        injection already filtered by scope and confidence.

        Terms and meanings are untrusted text (LLM-reported guesses land in
        senses verbatim), so each field is XML-escaped — same entity set as
        the ``<Msg>`` wrapper — to keep the block well-formed and stop a
        meaning from forging ``</Term>`` or protocol tags inside the prompt.

        Args:
            terms: Scoped terms that passed the injection filters.

        Returns:
            ``<KnownTerms>`` block with one ``<Term>`` line per term.
        """
        lines = []
        for term in terms:
            aliases = " / ".join(escape(alias) for alias in term.aliases)
            label = f"{escape(term.term)} / {aliases}" if aliases else escape(term.term)
            meanings = [
                sense.meaning
                for sense in term.senses
                if sense and getattr(sense, "meaning", "")
            ] or ([term.meaning] if getattr(term, "meaning", "") else [])
            for meaning in meanings:
                lines.append(f"<Term>{label}：{escape(meaning)}</Term>")
        body = "\n".join(lines)
        if not body:
            return "<KnownTerms />"
        return f"<KnownTerms>\n{body}\n</KnownTerms>"

    def build_protocol_prompt(self, context: MessageContext) -> str:
        parts: list[str] = []
        if self._config.default_rule_enabled:
            parts.append(self._build_rule(context))
        protocol = self._templates.render(
            "protocol",
            {
                "max_chars": self._config.max_message_chars,
            },
        )
        parts.append(protocol)
        # 图片转述由系统在 <Msg> 内联注入（含缓存路径）；模型不再输出 ImageCache 标签
        return "\n\n".join(parts)

    def build_protocol_repair_request(
        self,
        context: MessageContext,
        *,
        error_code: str,
        invalid_header_preview: str,
        required_action: str,
    ) -> tuple[str, str]:
        """Build an isolated header-only repair request.

        Args:
            context: Trusted metadata and the original user message.
            error_code: Deterministic validation failure from the first attempt.
            invalid_header_preview: Bounded preview of the malformed control lines.
            required_action: Trusted action that the repair must preserve.

        Returns:
            System prompt and XML-escaped user payload for the repair call.
        """
        root = ET.Element("RepairInput")
        ET.SubElement(root, "ErrorCode").text = error_code
        ET.SubElement(root, "RequiredAction").text = required_action
        ET.SubElement(root, "UserMessage").text = context.user_text
        ET.SubElement(root, "InvalidHeaderPreview").text = invalid_header_preview
        return (
            self._templates.render(
                "repair",
                {
                    "required_action": required_action,
                },
            ),
            ET.tostring(root, encoding="unicode", short_empty_elements=False),
        )

    def _build_rule(self, context: MessageContext) -> str:
        admin_ids = "、".join(context.admin_ids) if context.admin_ids else "未配置"
        if context.scope_type == "group" or context.chat_scene == "QQ群":
            scene = "QQ群聊天"
        elif context.chat_scene.startswith("QQ 上和"):
            scene = f"和{context.chat_scene.removeprefix('QQ 上和')} QQ私聊"
        else:
            scene = context.chat_scene
        return self._templates.render(
            "rule",
            {
                "scene": scene,
                "admin_name": context.admin_name,
                "admin_ids": admin_ids,
            },
        )
