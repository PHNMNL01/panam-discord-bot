import re

import discord

from panam_ai import analyze_document_text, analyze_image, shorten_for_discord
from panam_discord_attachments import (
    MAX_IMAGE_SIZE_BYTES,
    get_attachment_kind,
    read_attachment_bytes,
)
from panam_discord_message_helpers import normalize_natural_text
from panam_phrases import (
    ATTACHMENT_ANALYZE_PATTERNS,
    ATTACHMENT_SUBJECTS,
    GENERIC_ATTACHMENT_PATTERNS,
    GENERIC_IMAGE_PHRASES,
    IMAGE_ANALYZE_TRIGGERS,
)
from panam_text_extraction import (
    TextExtractionUserError as AttachmentAnalysisUserError,
    extract_text_from_attachment,
    get_file_extension,
    trim_document_text,
)


async def analyze_selected_attachment(
    model: str,
    file: discord.Attachment,
    question: str,
) -> str:
    attachment_kind = get_attachment_kind(file)
    if attachment_kind is None:
        raise AttachmentAnalysisUserError(
            "Tenhle typ souboru zatím neumím přečíst. Podporuju obrázky PNG, JPG, JPEG, WEBP, GIF a dokumenty TXT, MD, CSV, JSON, PDF, DOCX, XLSX."
        )

    if file.size > MAX_IMAGE_SIZE_BYTES:
        raise AttachmentAnalysisUserError(
            "Ten soubor je moc velký. Zatím beru max 20 MB."
        )

    if attachment_kind == "image":
        answer = await analyze_image(model, file.url, question)
        return shorten_for_discord(answer)

    data = await read_attachment_bytes(file)
    document_text = extract_text_from_attachment(file.filename, data)

    if not document_text.strip():
        if get_file_extension(file.filename) == ".xlsx":
            raise AttachmentAnalysisUserError(
                "Z toho Excelu se mi nepodarilo vytahnout zadna data."
            )

        if get_file_extension(file.filename) == ".pdf":
            raise AttachmentAnalysisUserError(
                "Z toho PDF se mi nepodařilo vytáhnout žádný text. Možná je to sken nebo obrázkové PDF."
            )

        raise AttachmentAnalysisUserError(
            "Z toho dokumentu se mi nepodařilo vytáhnout žádný text."
        )

    document_text = trim_document_text(document_text)
    answer = await analyze_document_text(
        model,
        document_text,
        question,
        file.filename,
    )
    return shorten_for_discord(answer)


def is_natural_analyze_request(content: str) -> bool:
    text = normalize_natural_text(content)
    return any(phrase in text for phrase in IMAGE_ANALYZE_TRIGGERS)


def is_natural_attachment_analyze_request(content: str) -> bool:
    if is_natural_analyze_request(content):
        return True

    text = normalize_natural_text(content)
    subject_pattern = "|".join(re.escape(subject) for subject in ATTACHMENT_SUBJECTS)

    return any(
        re.match(
            pattern_template.format(subject_pattern=subject_pattern),
            text,
            re.IGNORECASE,
        )
        for pattern_template in ATTACHMENT_ANALYZE_PATTERNS
    )


def is_generic_natural_attachment_request(content: str) -> bool:
    if is_natural_analyze_request(content):
        text = normalize_natural_text(content)
        return text in GENERIC_IMAGE_PHRASES

    text = normalize_natural_text(content)
    subject_pattern = "|".join(re.escape(subject) for subject in ATTACHMENT_SUBJECTS)
    return any(
        re.match(
            pattern_template.format(subject_pattern=subject_pattern),
            text,
            re.IGNORECASE,
        )
        for pattern_template in GENERIC_ATTACHMENT_PATTERNS
    )


def get_natural_analyze_question(content: str) -> str:
    text = normalize_natural_text(content)
    default_question = "Popiš, co je na obrázku."
    if text in GENERIC_IMAGE_PHRASES:
        return default_question

    return content.strip() or default_question


def get_natural_attachment_analyze_question(content: str) -> str:
    default_question = "Analyzuj tuto přílohu a stručně popiš, co obsahuje."
    if is_generic_natural_attachment_request(content):
        return default_question

    return content.strip() or default_question


def is_attachment_summary_request(text: str) -> bool:
    normalized = normalize_natural_text(text)
    return re.search(r"\b(?:shrn|precti)\b", normalized) is not None


def is_attachment_error_request(text: str) -> bool:
    normalized = normalize_natural_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "chyba",
            "spatne",
            "problem",
            "najdi chybu",
            "co je tam spatne",
            "co je na tom spatne",
        )
    )


def is_attachment_content_request(text: str) -> bool:
    normalized = normalize_natural_text(text)
    return any(
        re.match(pattern, normalized) is not None
        for pattern in (
            r"^co\s+je\s+(?:v|ve|na)\s+(?:tom|toto|tohle).*$",
            r"^co\s+je\s+(?:v|ve)\s+(?:teto|te|ta)\s+priloze.*$",
            r"^co\s+tam\s+je.*$",
            r"^co\s+vidis.*$",
            r"^co\s+obsahuje(?:\s+(?:to|toto|tohle|tahle\s+priloha|ta\s+priloha))?.*$",
        )
    )


def is_attachment_look_request(text: str) -> bool:
    normalized = normalize_natural_text(text)
    return any(
        re.match(pattern, normalized) is not None
        for pattern in (
            r"^(?:koukni|mrkni)\s+na\s+(?:to|toto|tohle).*$",
            r"^podivej\s+se\s+na\s+(?:to|toto|tohle).*$",
            r"^analyzuj\s+(?:to|toto|tohle).*$",
        )
    )


def is_general_attachment_context_request(text: str) -> bool:
    return (
        is_attachment_summary_request(text)
        or is_attachment_error_request(text)
        or is_attachment_content_request(text)
        or is_attachment_look_request(text)
    )


def get_attachment_context_question(text: str, attachment_kind: str) -> str:
    if attachment_kind == "image" and is_attachment_error_request(text):
        return "Podívej se na obrázek a řekni, jaká chyba je tam vidět. Navrhni krátce další postup."

    if attachment_kind == "document" and is_attachment_summary_request(text):
        return "Shrň tuto přílohu."

    if attachment_kind == "document" and is_attachment_error_request(text):
        return "Najdi v dokumentu možné chyby nebo problémové části a stručně je vysvětli."

    return "Analyzuj tuto přílohu a stručně popiš, co obsahuje."


def is_context_attachment_request(text: str) -> bool:
    return is_general_attachment_context_request(text)


def get_context_attachment_question(text: str) -> str:
    return get_attachment_context_question(text, "document")
