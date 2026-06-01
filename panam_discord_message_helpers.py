import re
import unicodedata
from typing import Optional

import discord

from panam_phrases import (
    CONTEXT_REFERENCES,
    PANAM_OPINION_MENTION_PATTERN,
    PANAM_PREFIX_PATTERN,
    PANAM_STRIP_PREFIX_PATTERN,
)


def extract_panam_request(
    message: discord.Message,
    bot_user: discord.ClientUser,
) -> Optional[str]:
    content = (message.content or "").strip()
    mention_patterns = (
        f"<@{bot_user.id}>",
        f"<@!{bot_user.id}>",
    )

    for mention in mention_patterns:
        if content.startswith(mention):
            return content[len(mention):].strip(" \t\n\r,.:;!-")

    match = re.match(PANAM_PREFIX_PATTERN, content, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    if re.match(
        PANAM_OPINION_MENTION_PATTERN,
        content,
        re.IGNORECASE,
    ):
        return content

    return None


def extract_basic_panam_prompt(content: str) -> str | None:
    match = re.match(PANAM_PREFIX_PATTERN, content.strip(), re.IGNORECASE)
    if not match:
        return None

    return match.group(1).strip()


def normalize_natural_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    without_diacritics = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_diacritics).strip(" \t\n\r,.:;!-?")


def normalize_text(text: str) -> str:
    return normalize_natural_text(text)


def is_panam_addressed(content: str) -> bool:
    text = normalize_text(content)
    return (
        text.startswith("panam")
        or text.startswith("hey panam")
        or re.search(r"\bpanam\b", text) is not None
    )


def is_context_reference(text: str) -> bool:
    normalized = normalize_text(text)
    return normalized in CONTEXT_REFERENCES


def extract_inline_content_after_trigger(content: str, trigger_phrases: list[str]) -> str:
    text = content.strip()
    text = re.sub(PANAM_STRIP_PREFIX_PATTERN, "", text, flags=re.IGNORECASE)

    for phrase in trigger_phrases:
        pattern = rf"^{re.escape(phrase)}\b\s*(.*)$"
        match = re.match(pattern, text, re.IGNORECASE)
        if not match:
            continue

        value = match.group(1).strip()
        if is_context_reference(value):
            return ""
        return value

    return ""
