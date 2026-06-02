import re
import unicodedata
from dataclasses import dataclass

from panam_phrases import (
    BASIC_PANAM_EMPTY_RESPONSE,
    HELP_PATTERNS,
    NATURAL_INTENT_PATTERNS,
    NOTE_LIST_PATTERN,
    PANAM_PREFIX_PATTERN,
    PANAM_STRIP_PREFIX_PATTERN,
    TODO_LIST_PATTERN,
)


@dataclass
class PanamCommandIntent:
    intent: str
    text: str | None = None
    raw_text: str = ""


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    without_diacritics = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_diacritics).strip(" \t\n\r,.:;!-?")


def strip_panam_prefix(text: str) -> str:
    value = (text or "").strip()
    match = re.match(PANAM_PREFIX_PATTERN, value, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return re.sub(PANAM_STRIP_PREFIX_PATTERN, "", value, flags=re.IGNORECASE).strip()


def _has_panam_prefix(text: str) -> bool:
    return re.match(PANAM_PREFIX_PATTERN, text.strip(), re.IGNORECASE) is not None


def _match_existing_patterns(text: str) -> PanamCommandIntent | None:
    for pattern in HELP_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return PanamCommandIntent("help")

    for intent, pattern in NATURAL_INTENT_PATTERNS:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return PanamCommandIntent(intent, match.group(1).strip())

    if re.match(NOTE_LIST_PATTERN, text, re.IGNORECASE):
        return PanamCommandIntent("note_list")

    if re.match(TODO_LIST_PATTERN, text, re.IGNORECASE):
        return PanamCommandIntent("todo_list")

    return None


def _match_normalized_patterns(text: str) -> PanamCommandIntent | None:
    normalized = _normalize_text(text)
    original_words = text.split()

    if normalized in {"help", "pomoc", "napoveda", "prikazy"}:
        return PanamCommandIntent("help")

    if re.match(r"^co\s+(?:umis|dokazes)\??$", normalized):
        return PanamCommandIntent("help")

    normalized_prefixes = (
        ("note_add", "pridej poznamku", 2),
        ("note_add", "uloz poznamku", 2),
        ("note_add", "zapamatuj si", 2),
        ("note_add", "pamatuj si", 2),
        ("note_add", "uloz si", 2),
        ("todo_add", "pridej todo", 2),
        ("todo_add", "pridej ukol", 2),
        ("note_search", "najdi poznamku", 2),
        ("ask", "rekni mi", 2),
        ("ask", "rekni", 1),
        ("ask", "odpovez", 1),
        ("ask", "co si myslis o", 4),
        ("talk", "talk", 1),
        ("talk", "pokec", 1),
        ("talk", "pokecame o", 2),
        ("talk", "pokecej o", 2),
        ("summary", "shrn mi", 2),
        ("summary", "shrn", 1),
        ("summary", "udelej summary", 2),
    )

    for intent, prefix, words_to_drop in normalized_prefixes:
        if normalized == prefix or normalized.startswith(f"{prefix} "):
            value = " ".join(original_words[words_to_drop:]).strip()
            if value:
                return PanamCommandIntent(intent, value)

    if re.match(r"^ukaz\s+poznamky$", normalized):
        return PanamCommandIntent("note_list")

    if re.match(r"^ukaz\s+(?:todo|ukoly)$", normalized):
        return PanamCommandIntent("todo_list")

    return None


def parse_panam_command(text: str) -> PanamCommandIntent:
    raw_text = text or ""
    value = raw_text.strip()
    addressed = _has_panam_prefix(value)
    request_text = strip_panam_prefix(value) if addressed else value

    if not request_text:
        intent = PanamCommandIntent("empty")
    else:
        intent = _match_existing_patterns(request_text) or _match_normalized_patterns(
            request_text
        )

    if intent is None:
        intent = PanamCommandIntent("ask", request_text)
    elif intent.intent == "empty":
        intent.text = None

    intent.raw_text = raw_text
    return intent


__all__ = (
    "BASIC_PANAM_EMPTY_RESPONSE",
    "PanamCommandIntent",
    "parse_panam_command",
    "strip_panam_prefix",
)
