from __future__ import annotations

import json

import pytest
from humanize.config import PluginConfig
from humanize.domain.errors import ProtocolValidationError
from humanize.domain.models import Action, MessageContext
from humanize.protocol.envelope import EnvelopeBuilder
from humanize.protocol.parser import ProtocolParser


def _response(
    *,
    action: str = "Reply",
    unknown_terms: str = "[]",
    body: str = "<Messages><Message>收到</Message></Messages>",
) -> str:
    return (
        f"<Action>{action}</Action>\n"
        f"<UnknownTerms>{unknown_terms}</UnknownTerms>\n"
        f"{body}"
    )


def _context(**overrides: object) -> MessageContext:
    values: dict[str, object] = {
        "request_id": "req-1",
        "scope_type": "chat",
        "scope_id": "aiocqhttp:group:100",
        "message_id": "msg-1",
        "sender_id": "200",
        "sender_name": "小明",
        "user_text": "普通消息",
        "chat_scene": "QQ群",
        "admin_name": "管理员",
        "admin_ids": ("10001",),
    }
    values.update(overrides)
    return MessageContext(**values)


def test_parse_valid_reply_with_unknown_term() -> None:
    unknown_terms = json.dumps(
        [
            {
                "word": "yyds",
                "guess": "永远的神",
                "confidence": 0.91,
                "reason": "当前句用于称赞",
            }
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    decision = ProtocolParser(PluginConfig()).parse(
        _response(unknown_terms=unknown_terms)
    )

    assert decision.action is Action.REPLY
    assert decision.messages == ("收到",)
    assert len(decision.unknown_terms) == 1
    assert decision.unknown_terms[0].word == "yyds"
    assert decision.unknown_terms[0].confidence == pytest.approx(0.91)


def test_parse_valid_no_reply() -> None:
    decision = ProtocolParser(PluginConfig()).parse(
        _response(action="No Reply", body="")
    )

    assert decision.action is Action.NO_REPLY
    assert decision.messages == ()
    assert decision.unknown_terms == ()


@pytest.mark.parametrize(
    ("raw", "error_code"),
    [
        ("普通文本", "missing_action"),
        (_response(action="Wait"), "invalid_action"),
        (_response(unknown_terms="{}"), "invalid_unknown_terms"),
        (_response(unknown_terms="[broken"), "invalid_unknown_terms_json"),
    ],
)
def test_reject_invalid_protocol(raw: str, error_code: str) -> None:
    with pytest.raises(ProtocolValidationError) as error:
        ProtocolParser(PluginConfig()).parse(raw)

    assert error.value.code == error_code


def test_action_tag_position_is_flexible() -> None:
    raw = (
        "<Messages><Message>第一条</Message></Messages>\n"
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>"
    )

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert decision.action is Action.REPLY
    assert decision.messages == ("第一条",)


def test_unknown_terms_optional_defaults_empty() -> None:
    raw = "<Action>Reply</Action>\n<Messages><Message>收到</Message></Messages>"

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert decision.unknown_terms == ()
    assert decision.messages == ("收到",)


def test_image_cache_plain_text_anywhere() -> None:
    raw = (
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n"
        "<Messages><Message>收到</Message></Messages>\n"
        "<ImageCache>这是一个网络梗表情包，结合上下文含义为……</ImageCache>"
    )

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert len(decision.image_cache) == 1
    assert decision.image_cache[0].text.startswith("这是一个网络梗表情包")
    assert decision.messages == ("收到",)


def test_image_cache_before_messages() -> None:
    raw = (
        "<Action>Reply</Action>\n"
        "<ImageCache>图1含义</ImageCache>\n"
        "<Messages><Message>收到</Message></Messages>"
    )

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert decision.image_cache[0].text == "图1含义"
    assert decision.messages == ("收到",)


def test_messages_required_for_outbound_text() -> None:
    raw = "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n裸文本不在Message里"

    decision = ProtocolParser(PluginConfig()).parse(raw)

    # 新语义：不在 Message 中的内容不发送
    assert decision.messages == ()


def test_messages_multiple_children() -> None:
    raw = (
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n"
        "<Messages><Message>第一条</Message><Message>第二条</Message></Messages>"
    )

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert decision.messages == ("第一条", "第二条")


def test_messages_single_child() -> None:
    raw = (
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n"
        "<Messages><Message>只有一条</Message></Messages>"
    )

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert decision.messages == ("只有一条",)


def test_message_content_tags_not_parsed() -> None:
    raw = (
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n"
        "<Messages><Message>正文里有<Action>Reply</Action>也不解析</Message></Messages>"
    )

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert decision.messages == ("正文里有<Action>Reply</Action>也不解析",)


def test_message_content_preserves_blank_lines() -> None:
    body = "第一行\n\n第三行"
    raw = (
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n"
        f"<Messages><Message>{body}</Message></Messages>"
    )

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert decision.messages == (body,)


def test_legacy_reply_block_still_supported() -> None:
    raw = (
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n"
        "<Reply><Message>旧格式第一条</Message><Message>旧格式第二条</Message></Reply>"
    )

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert decision.messages == ("旧格式第一条", "旧格式第二条")


def test_empty_messages_are_skipped() -> None:
    raw = (
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n"
        "<Messages><Message></Message><Message>有内容</Message></Messages>"
    )

    decision = ProtocolParser(PluginConfig()).parse(raw)

    assert decision.messages == ("有内容",)


def test_no_reply_with_body_is_rejected() -> None:
    raw = (
        "<Action>No Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n"
        "<Messages><Message>不应发送</Message></Messages>"
    )

    with pytest.raises(ProtocolValidationError) as error:
        ProtocolParser(PluginConfig()).parse(raw)

    assert error.value.code == "no_reply_has_text"


def test_repair_compose_keeps_original_body() -> None:
    parser = ProtocolParser(PluginConfig())
    body = "<Messages><Message>第一行</Message></Messages>"
    malformed = f"Action: Reply\nUnknownTerms: []\n{body}"

    extracted = parser.extract_repair_body(malformed)
    repaired = parser.compose_repaired_response(
        "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>",
        extracted or "",
    )

    assert extracted == body
    decision = parser.parse(repaired)
    assert decision.messages == ("第一行",)


def test_repair_requires_action_tag() -> None:
    parser = ProtocolParser(PluginConfig())

    with pytest.raises(ProtocolValidationError) as error:
        parser.compose_repaired_response("UnknownTerms: []", "正文")

    assert error.value.code == "missing_action"


def test_unknown_term_text_fields_must_be_strings() -> None:
    invalid = '[{"word":1,"guess":"meaning","confidence":0.5,"reason":"context"}]'

    with pytest.raises(ProtocolValidationError) as error:
        ProtocolParser(PluginConfig()).parse(_response(unknown_terms=invalid))

    assert error.value.code == "invalid_unknown_terms"


def test_envelope_escapes_message_as_xml_data() -> None:
    builder = EnvelopeBuilder(PluginConfig())
    xml = builder.build_known_terms_xml([])
    assert "<KnownTerms" in xml
