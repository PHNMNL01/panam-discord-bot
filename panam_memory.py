from datetime import datetime, timezone


MAX_MESSAGES_PER_CHANNEL = 20

_channel_memory: dict[int, list[dict]] = {}


def add_message(channel_id: int, role: str, content: str) -> None:
    if role not in {"user", "assistant"}:
        return

    message = {
        "role": role,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    messages = _channel_memory.setdefault(channel_id, [])
    messages.append(message)
    del messages[:-MAX_MESSAGES_PER_CHANNEL]


def get_messages(channel_id: int) -> list[dict]:
    return [message.copy() for message in _channel_memory.get(channel_id, [])]


def clear_channel_memory(channel_id: int) -> None:
    _channel_memory.pop(channel_id, None)
