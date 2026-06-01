import re
from typing import Optional

from panam_discord_attachment_analysis import (
    is_general_attachment_context_request,
    is_natural_attachment_analyze_request,
)
from panam_discord_message_helpers import (
    extract_basic_panam_prompt,
    is_panam_addressed,
    normalize_text,
)
from panam_discord_natural_file_orchestrator import get_natural_file_action_name
from panam_phrases import (
    HELP_PATTERNS,
    NATURAL_INTENT_PATTERNS,
    NOTE_ADD_PREVIOUS_PATTERN,
    NOTE_LIST_PATTERN,
    OPINION_CONTEXT_PATTERNS,
    PANAM_STRIP_PREFIX_PATTERN,
    SUMMARY_CONTEXT_PATTERN,
    TODO_LIST_PATTERN,
)

try:
    from panam_router import (
        decide_file_response_mode,
        has_explicit_file_action_request,
        has_explicit_file_output_request,
    )
except ModuleNotFoundError as router_import_error:
    if router_import_error.name != "docx":
        raise

    _ROUTER_IMPORT_ERROR = router_import_error

    def _missing_router_dependency(*args, **kwargs):
        raise _ROUTER_IMPORT_ERROR

    decide_file_response_mode = _missing_router_dependency
    has_explicit_file_action_request = _missing_router_dependency
    has_explicit_file_output_request = _missing_router_dependency


def should_skip_recent_text_content(content: str) -> bool:
    request_text = None
    if is_panam_addressed(content):
        request_text = re.sub(
            PANAM_STRIP_PREFIX_PATTERN,
            "",
            content,
            flags=re.IGNORECASE,
        ).strip()

    normalized = normalize_text(request_text or content)
    if not normalized or normalized == "panam":
        return True

    return (
        is_natural_attachment_analyze_request(normalized)
        or parse_natural_intent(normalized) is not None
    )


def is_file_router_candidate(text: str) -> bool:
    return has_explicit_file_output_request(text) or has_explicit_file_action_request(text)


def parse_natural_intent(text: str) -> Optional[tuple[str, Optional[str]]]:
    for pattern in HELP_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return "help", None

    if re.match(NOTE_ADD_PREVIOUS_PATTERN, text, re.IGNORECASE):
        return "note_add_previous", None

    for pattern in OPINION_CONTEXT_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return "ask_previous", None

    if re.match(SUMMARY_CONTEXT_PATTERN, text, re.IGNORECASE):
        return "summary_previous", None

    for intent, pattern in NATURAL_INTENT_PATTERNS:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return intent, match.group(1).strip()

    if re.match(NOTE_LIST_PATTERN, text, re.IGNORECASE):
        return "note_list", None

    if re.match(TODO_LIST_PATTERN, text, re.IGNORECASE):
        return "todo_list", None

    return None


def get_natural_action_name(request_text: str, original_content: str) -> str | None:
    file_decision = decide_file_response_mode(request_text)
    if is_file_router_candidate(request_text):
        return get_natural_file_action_name(file_decision)

    if is_general_attachment_context_request(request_text) and has_explicit_file_action_request(request_text):
        return "natural_attachment_context"

    if is_natural_attachment_analyze_request(request_text) and has_explicit_file_action_request(request_text):
        return "analyze_attachment"

    intent = parse_natural_intent(request_text)
    if intent is not None:
        intent_name, _ = intent
        if intent_name in ("ask_previous", "ask"):
            return "ask"
        if intent_name in ("summary_previous", "summary"):
            return "summary"
        if intent_name in ("note_add_previous", "note_add"):
            return "note_add"
        return intent_name

    if extract_basic_panam_prompt(original_content) is not None:
        return "ask"

    return None
