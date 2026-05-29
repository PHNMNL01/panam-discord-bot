from datetime import datetime, timezone
from pathlib import Path


_last_file_context: dict[int, dict[str, str | None]] = {}
_last_router_decision: dict[int, dict[str, str | bool | float | None]] = {}


def normalize_channel_id(channel_id: int | str | None) -> int | None:
    if isinstance(channel_id, int):
        return channel_id
    if isinstance(channel_id, str) and channel_id.isdecimal():
        return int(channel_id)
    return None


def set_last_file_context(
    channel_id: int | str | None,
    source_filename: str,
    source_extension: str,
    last_mode: str,
    last_output_filename: str | None = None,
) -> None:
    normalized_channel_id = normalize_channel_id(channel_id)
    if normalized_channel_id is None:
        return

    _last_file_context[normalized_channel_id] = {
        "source_filename": Path(source_filename).name,
        "source_extension": source_extension,
        "last_output_filename": last_output_filename,
        "last_mode": last_mode,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_last_file_context(channel_id: int | str | None) -> dict[str, str | None] | None:
    normalized_channel_id = normalize_channel_id(channel_id)
    if normalized_channel_id is None:
        return None

    context = _last_file_context.get(normalized_channel_id)
    if context is None:
        return None
    return dict(context)


def clear_last_file_context(channel_id: int | str | None) -> None:
    normalized_channel_id = normalize_channel_id(channel_id)
    if normalized_channel_id is None:
        return

    _last_file_context.pop(normalized_channel_id, None)


def set_last_router_decision(
    channel_id: int | str | None,
    target: str,
    mode: str,
    output_format: str | None = None,
    classifier_used: bool = False,
    confidence: float | None = None,
) -> None:
    normalized_channel_id = normalize_channel_id(channel_id)
    if normalized_channel_id is None:
        return

    normalized_confidence = None
    if isinstance(confidence, (int, float)):
        normalized_confidence = max(0.0, min(1.0, float(confidence)))

    _last_router_decision[normalized_channel_id] = {
        "target": target,
        "mode": mode,
        "output_format": output_format,
        "classifier_used": bool(classifier_used),
        "confidence": normalized_confidence,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_last_router_decision(
    channel_id: int | str | None,
) -> dict[str, str | bool | float | None] | None:
    normalized_channel_id = normalize_channel_id(channel_id)
    if normalized_channel_id is None:
        return None

    decision = _last_router_decision.get(normalized_channel_id)
    if decision is None:
        return None
    return dict(decision)


def clear_last_router_decision(channel_id: int | str | None) -> None:
    normalized_channel_id = normalize_channel_id(channel_id)
    if normalized_channel_id is None:
        return

    _last_router_decision.pop(normalized_channel_id, None)
