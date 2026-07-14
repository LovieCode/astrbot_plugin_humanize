from __future__ import annotations

from ..domain.errors import ProtocolValidationError

_BREAK_CHARS = frozenset("。！？!?；;，,、…~～\n\t ")


def split_message(text: str, max_chars: int) -> list[str]:
    value = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return []
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    result: list[str] = []
    remaining = value
    while remaining:
        if len(remaining) <= max_chars:
            result.append(remaining.strip())
            break

        window = remaining[:max_chars]
        candidates = [
            index + 1 for index, char in enumerate(window) if char in _BREAK_CHARS
        ]
        minimum_natural_break = max(1, max_chars // 2)
        split_at = max_chars
        if candidates and candidates[-1] >= minimum_natural_break:
            split_at = candidates[-1]

        chunk = remaining[:split_at].strip()
        if not chunk:
            chunk = remaining[:max_chars]
            split_at = max_chars
        result.append(chunk)
        remaining = remaining[split_at:].lstrip()

    return [part for part in result if part]


def enforce_message_limits(
    messages: list[str],
    *,
    max_chars: int,
    max_messages: int,
    split_long_messages: bool,
) -> tuple[str, ...]:
    output: list[str] = []
    for message in messages:
        clean = message.strip()
        if not clean:
            raise ProtocolValidationError(
                "empty_message", "Reply contains an empty Message"
            )
        if len(clean) <= max_chars:
            output.append(clean)
        elif split_long_messages:
            output.extend(split_message(clean, max_chars))
        else:
            raise ProtocolValidationError(
                "message_too_long",
                f"Message exceeds the {max_chars} character limit",
            )
        if len(output) > max_messages:
            raise ProtocolValidationError(
                "too_many_messages",
                f"Reply expands beyond the {max_messages} message limit",
            )
    return tuple(output)
