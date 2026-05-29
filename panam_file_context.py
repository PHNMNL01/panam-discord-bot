import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


MAX_FILE_SUMMARY_MEMORY_CHARS = 800

_last_file_context: dict[int, dict[str, str | None]] = {}
_last_router_decision: dict[int, dict[str, str | bool | float | None]] = {}


def _normalize_for_matching(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def sanitize_file_summary_for_memory(text: str) -> str | None:
    if not isinstance(text, str):
        return None

    stripped = text.strip()
    if not stripped:
        return None

    normalized = _normalize_for_matching(stripped)
    sensitive_patterns = (
        r"\bpassword\b",
        r"\bpasswd\b",
        r"\bpwd\b",
        r"\btoken\b",
        r"\bapi\s*key\b",
        r"\bapikey\b",
        r"\bsecret\b",
        r"\bheslo\b",
        r"rodne\s+cislo",
        r"bankovni\s+ucet",
        r"osobni\s+udaje",
    )
    if any(re.search(pattern, normalized) for pattern in sensitive_patterns):
        return None

    compact = re.sub(r"\s+", " ", stripped).strip()
    if not compact:
        return None

    return compact[:MAX_FILE_SUMMARY_MEMORY_CHARS].rstrip()


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
    file_summary: str | None = None,
) -> None:
    normalized_channel_id = normalize_channel_id(channel_id)
    if normalized_channel_id is None:
        return

    safe_source_filename = Path(source_filename).name
    previous_context = _last_file_context.get(normalized_channel_id)
    previous_summary = None
    if (
        previous_context is not None
        and previous_context.get("source_filename") == safe_source_filename
        and previous_context.get("source_extension") == source_extension
    ):
        previous_summary = previous_context.get("file_summary")

    safe_file_summary = (
        sanitize_file_summary_for_memory(file_summary)
        if file_summary is not None
        else previous_summary
    )

    _last_file_context[normalized_channel_id] = {
        "source_filename": safe_source_filename,
        "source_extension": source_extension,
        "last_output_filename": last_output_filename,
        "last_mode": last_mode,
        "file_summary": safe_file_summary,
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
