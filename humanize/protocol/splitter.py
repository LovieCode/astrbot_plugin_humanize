from __future__ import annotations

from ..domain.errors import ProtocolValidationError


def enforce_message_limits(
    messages: list[str],
    *,
    max_messages: int,
) -> tuple[str, ...]:
    output: list[str] = []
    for message in messages:
        clean = message.strip()
        if not clean:
            raise ProtocolValidationError(
                "empty_message", "Reply contains an empty Message"
            )
        output.append(clean)
        if len(output) > max_messages:
            raise ProtocolValidationError(
                "too_many_messages",
                f"Reply exceeds the {max_messages} message limit",
            )
    return tuple(output)
