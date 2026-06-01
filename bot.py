import os
import asyncio
import logging
import logging.handlers
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

import panam_docx_transform
import panam_files
import panam_memory
import panam_core
from panam_discord_attachments import (
    MAX_DOCUMENT_SIZE_BYTES,
    MAX_IMAGE_SIZE_BYTES,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    get_attachment_kind,
    get_safe_attachment_info,
    find_image_attachment_in_message,
    find_supported_attachment_in_message,
    read_attachment_bytes,
)
from panam_discord_context import (
    log_action,
    log_attachment_info,
    log_slash_command,
    mark_command_status,
)
from panam_discord_file_jobs import (
    run_docx_transform_file_job,
    run_human_document_file_job,
    run_spreadsheet_transform_file_job,
    run_structured_data_file_job,
)
from panam_discord_help import get_help_text
from panam_discord_history import (
    find_recent_docx_attachment,
    find_recent_image_attachment,
    find_recent_supported_attachment,
    find_recent_xlsx_attachment,
)
from panam_discord_message_helpers import (
    extract_basic_panam_prompt,
    extract_inline_content_after_trigger,
    extract_panam_request,
    is_context_reference,
    is_panam_addressed,
    normalize_natural_text,
    normalize_text,
)
from panam_discord_responses import (
    send_channel_chunks,
    send_followup_chunks,
    split_discord_message,
)
from panam_file_responses import build_file_job_success_message
from panam_file_context import (
    clear_last_file_context,
    clear_last_router_decision,
    get_last_file_context,
    set_last_file_context,
    set_last_router_decision,
)
from panam_ai import (
    analyze_image,
    analyze_document_text,
    ask_panam,
    classify_file_request_intent,
    shorten_for_discord,
)
from panam_phrases import (
    ATTACHMENT_ANALYZE_PATTERNS,
    ATTACHMENT_SUBJECTS,
    BASIC_PANAM_EMPTY_RESPONSE,
    GENERIC_ATTACHMENT_PATTERNS,
    GENERIC_IMAGE_PHRASES,
    HELP_PATTERNS,
    IMAGE_ANALYZE_TRIGGERS,
    NATURAL_INTENT_PATTERNS,
    NOTE_ADD_PREVIOUS_PATTERN,
    NOTE_ADD_TRIGGERS,
    NOTE_LIST_PATTERN,
    OPINION_CONTEXT_PATTERNS,
    OPINION_TRIGGERS,
    PANAM_STRIP_PREFIX_PATTERN,
    SUMMARY_CONTEXT_PATTERN,
    SUMMARY_TRIGGERS,
    TODO_LIST_PATTERN,
)
from panam_router import (
    decide_file_response_mode,
    has_direct_file_edit_request,
    has_docx_transform_request,
    has_explicit_file_action_request,
    has_explicit_file_output_request,
    has_spreadsheet_transform_request,
    has_unsafe_creative_docx_request,
    has_unsafe_creative_spreadsheet_request,
    is_ambiguous_context_request,
    is_followup_to_file_summary,
    is_meta_router_or_behavior_discussion,
    matches_last_file_reference,
    validate_ai_file_intent,
)
from panam_text_extraction import (
    TextExtractionUserError as AttachmentAnalysisUserError,
    extract_text_from_attachment,
    extract_text_from_docx,
    extract_text_from_pdf,
    extract_text_from_plain_file,
    extract_text_from_xlsx,
    get_file_extension,
    trim_document_text,
)


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "panam.log"

load_dotenv(dotenv_path=BASE_DIR / ".env")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_IDS = [
    guild_id.strip()
    for guild_id in os.getenv("DISCORD_GUILD_IDS", "").split(",")
    if guild_id.strip()
]
ALLOWED_CHANNEL_IDS = [
    channel_id.strip()
    for channel_id in os.getenv("ALLOWED_CHANNEL_IDS", "").split(",")
    if channel_id.strip()
]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
CREATIVE_SPREADSHEET_FALLBACK_MESSAGE = (
    "Tohle je moc volná úprava. Původní Excel neupravuju a data si nedomýšlím. "
    "Umím bezpečně udělat nový XLSX třeba: odstranit prázdné řádky, filtrovat řádky podle sloupce, "
    "vybrat sloupce, seřadit podle sloupce nebo najít duplicity."
)


logger = logging.getLogger("panam")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if not any(
        isinstance(handler, logging.handlers.RotatingFileHandler)
        and Path(handler.baseFilename) == LOG_FILE
        for handler in root_logger.handlers
    ):
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def is_interaction_allowed(interaction: discord.Interaction, command_name: str) -> bool:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        log_action("slash_command", command_name, "denied", interaction)
        mark_command_status(interaction, "denied")
        return False

    return True


setup_logging()
logger.info("Panam bot startuje.")


if not DISCORD_BOT_TOKEN:
    raise RuntimeError("Chybí DISCORD_BOT_TOKEN v .env souboru.")


def get_author_name(author) -> str:
    return getattr(author, "display_name", author.name)


async def analyze_selected_attachment(file: discord.Attachment, question: str) -> str:
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
        answer = await analyze_image(OPENAI_MODEL, file.url, question)
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
        OPENAI_MODEL,
        document_text,
        question,
        file.filename,
    )
    return shorten_for_discord(answer)


async def find_previous_message_content(
    message: discord.Message,
    prefer_same_author: bool = True,
) -> Optional[str]:
    history = getattr(message.channel, "history", None)
    if history is None:
        return None

    fallback_content = None

    async for previous_message in history(limit=30, before=message.created_at):
        if previous_message.author.bot:
            continue

        previous_content = (previous_message.content or "").strip()
        if not previous_content:
            continue

        if not prefer_same_author:
            return previous_content

        if previous_message.author.id == message.author.id:
            return previous_content

        if fallback_content is None:
            fallback_content = previous_content

    return fallback_content


async def handle_basic_panam_message(
    message: discord.Message,
    basic_prompt: str,
) -> None:
    if not basic_prompt.strip():
        answer = BASIC_PANAM_EMPTY_RESPONSE
        await message.reply(answer, mention_author=False)
        panam_memory.add_message(message.channel.id, "user", message.content or "Panam")
        panam_memory.add_message(message.channel.id, "assistant", answer)
        return

    history = panam_memory.get_messages(message.channel.id)
    response = await panam_core.handle_chat(OPENAI_MODEL, basic_prompt, history=history)
    answer = response.text
    await message.reply(answer, mention_author=False)
    panam_memory.add_message(message.channel.id, "user", message.content or basic_prompt)
    panam_memory.add_message(message.channel.id, "assistant", answer)


async def find_recent_text_message(channel) -> str | None:
    history = getattr(channel, "history", None)
    if history is None:
        return None

    async for message in history(limit=15):
        if message.author.bot:
            continue

        content = (message.content or "").strip()
        if not content:
            continue

        if content.startswith("/"):
            continue

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
            continue

        if (
            is_natural_attachment_analyze_request(normalized)
            or parse_natural_intent(normalized) is not None
        ):
            continue

        return content

    return None


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


def is_file_router_candidate(text: str) -> bool:
    return has_explicit_file_output_request(text) or has_explicit_file_action_request(text)


def sanitize_classifier_context_text(text: str) -> str:
    normalized = normalize_natural_text(text)
    sensitive_markers = (
        "token",
        "api key",
        "apikey",
        "heslo",
        "password",
        "secret",
        "klic",
        "key",
    )
    if any(marker in normalized for marker in sensitive_markers):
        return "[sensitive content omitted]"

    return text[:500]


def get_recent_classifier_context(channel_id: int, limit: int = 4) -> list[dict]:
    recent_messages = panam_memory.get_messages(channel_id)[-limit:]
    context = []
    for message in recent_messages:
        role = message.get("role")
        content = str(message.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue

        context.append(
            {
                "role": role,
                "content": sanitize_classifier_context_text(content),
            }
        )

    return context


def get_natural_file_action_name(decision: dict) -> str:
    mode = decision.get("mode")
    if mode == "human_document":
        return "process_file"
    if mode == "structured_data":
        return "extract_data"
    if mode == "docx_transform":
        return "transform_docx"
    if mode == "spreadsheet_transform":
        return "transform_excel"
    if mode == "unsupported_creative_docx_edit":
        return "unsupported_creative_docx_edit"
    if mode == "unsupported_creative_spreadsheet_edit":
        return "unsupported_creative_spreadsheet_edit"
    if mode == "unsupported_direct_edit":
        return "unsupported_direct_edit"
    return "chat_answer"


def remember_router_decision(
    message: discord.Message,
    decision: dict,
    default_target: str = "current_attachment",
) -> None:
    mode = str(decision.get("mode") or "chat_answer")
    target = str(decision.get("target") or default_target)
    output_format = decision.get("output_format")
    if not isinstance(output_format, str):
        output_format = None

    confidence = decision.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = None

    set_last_router_decision(
        message.channel.id,
        target,
        mode,
        output_format=output_format,
        classifier_used=bool(decision.get("classifier_used", False)),
        confidence=confidence,
    )


async def handle_natural_file_request(
    message: discord.Message,
    file: discord.Attachment | None,
    decision: dict,
) -> None:
    mode = decision.get("mode")
    action_name = get_natural_file_action_name(decision)

    if mode == "unsupported_creative_docx_edit":
        remember_router_decision(
            message,
            decision,
            "current_attachment" if file is not None else "none",
        )
        log_action("natural_message", action_name, "started", message)
        await message.reply(
            panam_docx_transform.DOCX_TRANSFORM_FALLBACK_MESSAGE,
            mention_author=False,
        )
        log_action("natural_message", action_name, "success", message)
        return

    if mode == "unsupported_creative_spreadsheet_edit":
        remember_router_decision(
            message,
            decision,
            "current_attachment" if file is not None else "none",
        )
        log_action("natural_message", action_name, "started", message)
        await message.reply(
            CREATIVE_SPREADSHEET_FALLBACK_MESSAGE,
            mention_author=False,
        )
        log_action("natural_message", action_name, "success", message)
        return

    if mode == "unsupported_direct_edit":
        remember_router_decision(
            message,
            decision,
            "current_attachment" if file is not None else "none",
        )
        log_action("natural_message", action_name, "started", message)
        await message.reply(
            "Puvodni prilohu primo neupravuju. Umim ale vytvorit novy soubor pres podporovanou DOCX nebo XLSX transformaci.",
            mention_author=False,
        )
        log_action("natural_message", action_name, "success", message)
        return

    if file is None:
        remember_router_decision(message, decision, "none")
        await message.reply(
            "Nevidim zadnou podporovanou prilohu ani v teto zprave, ani v predchozich zpravach.",
            mention_author=False,
        )
        return

    attachment_details = get_safe_attachment_info(file)
    attachment_details["kind"] = attachment_details.pop("file_type", None)

    if mode == "chat_answer":
        log_action(
            "natural_message",
            action_name,
            "started",
            message,
            **attachment_details,
        )
        try:
            question = decision.get("question") or get_attachment_context_question(
                message.content or "",
                get_attachment_kind(file) or "document",
            )
            answer = await analyze_selected_attachment(file, question)
            await message.reply(answer, mention_author=False)
            set_last_file_context(
                message.channel.id,
                Path(file.filename).name,
                get_file_extension(file.filename),
                "chat_answer",
                file_summary=answer,
            )
            remember_router_decision(message, decision)
            log_action(
                "natural_message",
                action_name,
                "success",
                message,
                **attachment_details,
            )
            return

        except AttachmentAnalysisUserError as error:
            await message.reply(str(error), mention_author=False)
            log_action(
                "natural_message",
                action_name,
                "error",
                message,
                **attachment_details,
            )
            return

        except Exception:
            log_action(
                "natural_message",
                action_name,
                "error",
                message,
                **attachment_details,
            )
            logger.exception("Chyba pri zpracovani natural file chat odpovedi")
            await message.reply(
                "Neco se pokazilo pri analyze prilohy. Mrkni do konzole na chybu.",
                mention_author=False,
            )
            return

    if get_attachment_kind(file) != "document":
        await message.reply(
            "Vystupni soubory zatim umim tvorit jen z dokumentu TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            mention_author=False,
        )
        return

    extension = get_file_extension(file.filename)
    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await message.reply("Ten soubor je moc velky. Zatim beru max 20 MB.", mention_author=False)
        return

    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        await message.reply(
            "Tenhle rezim podporuje dokumenty TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            mention_author=False,
        )
        return

    async with message.channel.typing():
        if mode == "docx_transform":
            if extension != ".docx":
                remember_router_decision(message, decision)
                await message.reply(
                    "DOCX transform v1 podporuje jen DOCX soubory. Puvodni prilohu neupravuju.",
                    mention_author=False,
                )
                return

            decision["output_format"] = "docx"
            remember_router_decision(message, decision)
            await run_docx_transform_file_job(
                OPENAI_MODEL,
                message,
                file,
                decision.get("instruction", message.content or ""),
                "natural_message",
            )
            return

        if mode == "spreadsheet_transform":
            if extension != ".xlsx":
                remember_router_decision(message, decision)
                await message.reply(
                    "Excel transform v1 podporuje jen XLSX soubory. Puvodni prilohu neupravuju.",
                    mention_author=False,
                )
                return

            decision["output_format"] = "xlsx"
            remember_router_decision(message, decision)
            await run_spreadsheet_transform_file_job(
                message,
                file,
                decision.get("instruction", message.content or ""),
                "natural_message",
            )
            return

        if mode == "human_document":
            output_format = decision.get("output_format", "md")
            if output_format not in {"md", "txt", "docx"}:
                output_format = "md"

            decision["output_format"] = output_format
            remember_router_decision(message, decision)
            await run_human_document_file_job(
                OPENAI_MODEL,
                message,
                file,
                decision.get("instruction", message.content or ""),
                output_format,
                "natural_message",
            )
            return

        if mode == "structured_data":
            output_format = decision.get("output_format", "json")
            if output_format not in {"json", "csv", "md", "xlsx"}:
                output_format = "json"

            decision["output_format"] = output_format
            remember_router_decision(message, decision)
            await run_structured_data_file_job(
                OPENAI_MODEL,
                message,
                file,
                decision.get("instruction", message.content or ""),
                output_format,
                "natural_message",
            )
            return

    await message.reply(
        "Tohle jsem u prilohy nepoznala, tak volim bezpecnou odpoved do chatu.",
        mention_author=False,
    )


async def handle_ai_classified_file_request(
    message: discord.Message,
    request_text: str,
    current_file: discord.Attachment | None,
) -> bool:
    last_file_context = get_last_file_context(message.channel.id)
    recent_context = get_recent_classifier_context(message.channel.id)

    if not (current_file is not None or last_file_context is not None or recent_context):
        log_action(
            "natural_message",
            "ai_file_intent_classifier",
            "skipped",
            message,
            classifier_used=False,
            target=None,
            mode=None,
            output_format=None,
            confidence=None,
            classifier_status="no_context",
        )
        return False

    current_attachment_context = {}
    if current_file is not None:
        current_attachment_context = {
            "exists": True,
            "filename": Path(current_file.filename).name,
            "extension": get_file_extension(current_file.filename),
        }
    else:
        current_attachment_context = {"exists": False}

    file_context = {
        "current_attachment": current_attachment_context,
        "last_file_context": last_file_context,
    }

    try:
        raw_intent = await classify_file_request_intent(
            OPENAI_MODEL,
            request_text,
            file_context=file_context,
            recent_context=recent_context,
        )
        intent = validate_ai_file_intent(
            raw_intent,
            has_current_attachment=current_file is not None,
            has_last_file_context=last_file_context is not None,
        )
    except Exception:
        log_action(
            "natural_message",
            "ai_file_intent_classifier",
            "error",
            message,
            classifier_used=True,
            target=None,
            mode=None,
            output_format=None,
            confidence=None,
            classifier_status="error",
        )
        logger.exception("Chyba pri AI klasifikaci nejasneho file/context dotazu")
        return False

    classifier_status = (
        "fallback"
        if intent.get("target") in {"conversation", "none"} or float(intent.get("confidence", 0.0)) < 0.65
        else "selected"
    )
    log_action(
        "natural_message",
        "ai_file_intent_classifier",
        "success",
        message,
        classifier_used=True,
        target=intent.get("target"),
        mode=intent.get("mode"),
        output_format=intent.get("output_format"),
        confidence=round(float(intent.get("confidence", 0.0)), 2),
        classifier_status=classifier_status,
    )
    set_last_router_decision(
        message.channel.id,
        str(intent.get("target") or "none"),
        str(intent.get("mode") or "chat_answer"),
        output_format=intent.get("output_format") if isinstance(intent.get("output_format"), str) else None,
        classifier_used=True,
        confidence=float(intent.get("confidence", 0.0)),
    )

    target = intent.get("target")
    mode = intent.get("mode")
    if target in {"conversation", "none"}:
        return False

    if mode == "unsupported_direct_edit":
        await message.reply(
            "Puvodni prilohu primo neupravuju. Umim ale vytvorit novy soubor pres podporovanou DOCX nebo XLSX transformaci.",
            mention_author=False,
        )
        return True

    if mode == "unsupported_creative_docx_edit":
        await message.reply(
            panam_docx_transform.DOCX_TRANSFORM_FALLBACK_MESSAGE,
            mention_author=False,
        )
        return True

    if mode == "unsupported_creative_spreadsheet_edit":
        await message.reply(
            CREATIVE_SPREADSHEET_FALLBACK_MESSAGE,
            mention_author=False,
        )
        return True

    selected_file = current_file
    if target == "last_file_context":
        if mode == "docx_transform":
            selected_file = await find_recent_docx_attachment(message.channel)
        elif mode == "spreadsheet_transform":
            selected_file = await find_recent_xlsx_attachment(message.channel)
        else:
            selected_file = await find_recent_supported_attachment(message.channel)

    if selected_file is None:
        return False

    if mode == "human_document":
        decision = {
            "target": target,
            "mode": "human_document",
            "instruction": intent.get("instruction") or request_text,
            "output_format": intent.get("output_format") or "md",
            "classifier_used": True,
            "confidence": intent.get("confidence"),
        }
    elif mode == "structured_data":
        decision = {
            "target": target,
            "mode": "structured_data",
            "instruction": intent.get("instruction") or request_text,
            "output_format": intent.get("output_format") or "json",
            "classifier_used": True,
            "confidence": intent.get("confidence"),
        }
    elif mode == "docx_transform":
        decision = {
            "target": target,
            "mode": "docx_transform",
            "instruction": intent.get("instruction") or request_text,
            "output_format": "docx",
            "classifier_used": True,
            "confidence": intent.get("confidence"),
        }
    elif mode == "spreadsheet_transform":
        decision = {
            "target": target,
            "mode": "spreadsheet_transform",
            "instruction": intent.get("instruction") or request_text,
            "output_format": "xlsx",
            "classifier_used": True,
            "confidence": intent.get("confidence"),
        }
    else:
        decision = {
            "target": target,
            "mode": "chat_answer",
            "question": intent.get("question") or get_attachment_context_question(
                request_text,
                get_attachment_kind(selected_file) or "document",
            ),
            "classifier_used": True,
            "confidence": intent.get("confidence"),
        }

    await handle_natural_file_request(message, selected_file, decision)
    return True


async def handle_file_summary_followup(
    message: discord.Message,
    request_text: str,
    file_context: dict,
) -> bool:
    file_summary = file_context.get("file_summary")
    if not isinstance(file_summary, str) or not file_summary.strip():
        return False

    log_action("natural_message", "file_summary_followup", "started", message)
    try:
        prompt = (
            "Uzivatel navazuje na posledni zpracovany soubor. "
            "Bezpecne shrnuti souboru: "
            f"{file_summary.strip()}\n\n"
            "Odpovez podle tohoto shrnuti. Nepredstirej, ze znas cely obsah souboru. "
            "Pokud z tohoto shrnuti nejde odpovedet, rekni to kratce a pozadej o soubor nebo upresneni.\n\n"
            f"Dotaz uzivatele: {request_text.strip()}"
        )
        answer = await ask_panam(OPENAI_MODEL, prompt)
        await message.reply(answer, mention_author=False)
        panam_memory.add_message(message.channel.id, "user", message.content or request_text)
        panam_memory.add_message(message.channel.id, "assistant", answer)
        log_action("natural_message", "file_summary_followup", "success", message)
        return True

    except Exception:
        log_action("natural_message", "file_summary_followup", "error", message)
        logger.exception("Chyba pri odpovedi podle bezpecneho file summary")
        await message.reply(
            "Mam ulozene jen kratke shrnuti posledniho souboru, ale ted se mi z nej nepodarilo odpovedet.",
            mention_author=False,
        )
        return True


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


class DiscordAIBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True

        # Message content intent je potřeba pro práci s obsahem zpráv.
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """
        Registrace slash commandů.

        Pro PoC doporučuji použít DISCORD_GUILD_IDS.
        Guild sync je rychlý, často prakticky hned.
        Globální sync může trvat déle.
        """
        if DISCORD_GUILD_IDS:
            for guild_id in DISCORD_GUILD_IDS:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)

            logger.info(
                "Slash commandy synchronizovány pro %s serverů.",
                len(DISCORD_GUILD_IDS),
            )
        else:
            await self.tree.sync()
            logger.info("Slash commandy synchronizovány globálně.")

    async def on_ready(self) -> None:
        if self.user is None:
            logger.info("Bot je online, ale user zatím není dostupný.")
            return

        logger.info("Bot je online jako %s | ID: %s", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if self.user is None:
            return

        request_text = extract_panam_request(message, self.user)
        if request_text is None:
            return

        current_file = find_supported_attachment_in_message(message)
        selected_file = current_file
        natural_action = get_natural_action_name(request_text, message.content or "")
        if ALLOWED_CHANNEL_IDS and str(message.channel.id) not in ALLOWED_CHANNEL_IDS:
            if natural_action is not None:
                log_action("natural_message", natural_action, "denied", message)
            return

        explicit_file_output_request = has_explicit_file_output_request(request_text)
        explicit_file_action_request = has_explicit_file_action_request(request_text)
        meta_router_discussion = is_meta_router_or_behavior_discussion(request_text)
        direct_file_edit_request = has_direct_file_edit_request(request_text)
        last_file_context = get_last_file_context(message.channel.id)
        last_context_extension = (
            str(last_file_context.get("source_extension") or "").lower()
            if last_file_context is not None
            else ""
        )
        current_extension = (
            get_file_extension(current_file.filename) if current_file is not None else None
        )
        has_xlsx_file_context = (
            current_extension == ".xlsx"
            if current_file is not None
            else last_context_extension == ".xlsx"
        )
        has_docx_file_context = (
            current_extension == ".docx"
            if current_file is not None
            else last_context_extension == ".docx"
        )
        docx_transform_candidate = has_docx_transform_request(
            request_text,
            current_extension if current_file is not None else last_context_extension,
            has_docx_context=has_docx_file_context,
        )
        spreadsheet_transform_candidate = has_spreadsheet_transform_request(
            request_text,
            current_extension if current_file is not None else last_context_extension,
            has_xlsx_context=has_xlsx_file_context,
        )
        unsafe_creative_docx_candidate = has_unsafe_creative_docx_request(
            request_text,
            current_extension if current_file is not None else last_context_extension,
            has_docx_context=has_docx_file_context,
        )
        unsafe_creative_spreadsheet_candidate = has_unsafe_creative_spreadsheet_request(
            request_text,
            current_extension if current_file is not None else last_context_extension,
            has_xlsx_context=has_xlsx_file_context,
        )
        last_file_reference = (
            current_file is None
            and not meta_router_discussion
            and matches_last_file_reference(request_text, last_file_context)
        )
        file_router_candidate = (
            explicit_file_output_request
            or explicit_file_action_request
            or docx_transform_candidate
            or spreadsheet_transform_candidate
            or unsafe_creative_docx_candidate
            or unsafe_creative_spreadsheet_candidate
            or direct_file_edit_request
            or last_file_reference
        )
        if file_router_candidate and selected_file is None:
            if docx_transform_candidate or unsafe_creative_docx_candidate:
                selected_file = await find_recent_docx_attachment(message.channel)
            elif spreadsheet_transform_candidate or unsafe_creative_spreadsheet_candidate:
                selected_file = await find_recent_xlsx_attachment(message.channel)
            else:
                selected_file = await find_recent_supported_attachment(message.channel)

        file_decision = decide_file_response_mode(
            request_text,
            get_file_extension(selected_file.filename) if selected_file is not None else None,
            has_xlsx_context=has_xlsx_file_context,
            has_docx_context=has_docx_file_context,
        )

        if (
            current_file is None
            and not meta_router_discussion
            and last_file_context is not None
            and last_file_context.get("file_summary")
            and last_file_reference
            and is_followup_to_file_summary(request_text)
            and file_decision.get("mode") == "chat_answer"
        ):
            handled_by_summary = await handle_file_summary_followup(
                message,
                request_text,
                last_file_context,
            )
            if handled_by_summary:
                return

        if file_router_candidate and (
            selected_file is not None
            or file_decision.get("mode") in {"human_document", "structured_data", "docx_transform", "spreadsheet_transform", "unsupported_creative_docx_edit", "unsupported_creative_spreadsheet_edit", "unsupported_direct_edit"}
            or explicit_file_output_request
            or explicit_file_action_request
            or docx_transform_candidate
            or spreadsheet_transform_candidate
            or unsafe_creative_docx_candidate
            or unsafe_creative_spreadsheet_candidate
            or last_file_reference
        ):
            file_decision["target"] = (
                "current_attachment"
                if current_file is not None
                else "last_file_context"
                if selected_file is not None
                else "none"
            )
            await handle_natural_file_request(message, selected_file, file_decision)
            return

        if (
            current_file is None
            and not meta_router_discussion
            and is_followup_to_file_summary(request_text)
        ):
            if last_file_context is not None and last_file_context.get("file_summary"):
                handled_by_summary = await handle_file_summary_followup(
                    message,
                    request_text,
                    last_file_context,
                )
                if handled_by_summary:
                    return

        if is_ambiguous_context_request(request_text) and not meta_router_discussion:
            handled_by_classifier = await handle_ai_classified_file_request(
                message,
                request_text,
                current_file,
            )
            if handled_by_classifier:
                return

        context_attachment_request = (
            selected_file is not None
            and not is_ambiguous_context_request(request_text)
            and not meta_router_discussion
            and is_general_attachment_context_request(request_text)
        )
        if context_attachment_request and selected_file is None:
            selected_file = await find_recent_supported_attachment(message.channel)

        if context_attachment_request and selected_file is not None:
            attachment_details = {}
            if selected_file is not None:
                attachment_details = get_safe_attachment_info(selected_file)
                attachment_details["kind"] = attachment_details.pop("file_type", None)

            log_action(
                "natural_message",
                "natural_attachment_context",
                "started",
                message,
                **attachment_details,
            )
            try:
                attachment_kind = get_attachment_kind(selected_file) or "document"
                question = get_attachment_context_question(request_text, attachment_kind)
                answer = await analyze_selected_attachment(selected_file, question)
                await message.reply(answer, mention_author=False)
                set_last_file_context(
                    message.channel.id,
                    Path(selected_file.filename).name,
                    get_file_extension(selected_file.filename),
                    "chat_answer",
                    file_summary=answer,
                )
                remember_router_decision(
                    message,
                    {
                        "target": "current_attachment" if current_file is not None else "last_file_context",
                        "mode": "chat_answer",
                    },
                )
                log_action(
                    "natural_message",
                    "natural_attachment_context",
                    "success",
                    message,
                    **attachment_details,
                )
                return

            except AttachmentAnalysisUserError as error:
                await message.reply(
                    str(error),
                    mention_author=False,
                )
                log_action(
                    "natural_message",
                    "natural_attachment_context",
                    "error",
                    message,
                    **attachment_details,
                )
                return

            except Exception:
                log_action(
                    "natural_message",
                    "natural_attachment_context",
                    "error",
                    message,
                    **attachment_details,
                )
                logger.exception("Chyba při zpracování přirozené kontextové přílohy")
                await message.reply(
                    "Něco se pokazilo při analýze přílohy. Mrkni do konzole na chybu.",
                    mention_author=False,
                )
                return

        if context_attachment_request and selected_file is None:
            fallback_intent = parse_natural_intent(request_text)
            if fallback_intent is None:
                basic_prompt = extract_basic_panam_prompt(message.content or "")
                natural_action = "ask" if basic_prompt is not None else None
            else:
                fallback_intent_name, _ = fallback_intent
                if fallback_intent_name in ("ask_previous", "ask"):
                    natural_action = "ask"
                elif fallback_intent_name in ("summary_previous", "summary"):
                    natural_action = "summary"
                elif fallback_intent_name in ("note_add_previous", "note_add"):
                    natural_action = "note_add"
                else:
                    natural_action = fallback_intent_name

        if is_natural_attachment_analyze_request(request_text) and (
            selected_file is not None or explicit_file_action_request
        ):
            log_action("natural_message", "analyze_attachment", "started", message)
            try:
                if selected_file is None:
                    selected_file = await find_recent_supported_attachment(message.channel)

                if selected_file is None:
                    await message.reply(
                        "Nevidím žádnou podporovanou přílohu ani v této zprávě, ani v předchozích zprávách.",
                        mention_author=False,
                    )
                    log_action("natural_message", "analyze_attachment", "success", message)
                    return

                question = get_natural_attachment_analyze_question(request_text)
                answer = await analyze_selected_attachment(selected_file, question)
                await message.reply(answer, mention_author=False)
                log_action(
                    "natural_message",
                    "analyze_attachment",
                    "success",
                    message,
                    **get_safe_attachment_info(selected_file),
                )
                return

            except AttachmentAnalysisUserError as error:
                await message.reply(
                    str(error),
                    mention_author=False,
                )
                log_action("natural_message", "analyze_attachment", "error", message)
                return

            except Exception:
                log_action("natural_message", "analyze_attachment", "error", message)
                logger.exception("Chyba při zpracování přirozené analýzy přílohy")
                await message.reply(
                    "Něco se pokazilo při analýze přílohy. Mrkni do konzole na chybu.",
                    mention_author=False,
                )
                return

        intent = parse_natural_intent(request_text)
        if intent is None:
            basic_prompt = extract_basic_panam_prompt(message.content or "")
            if basic_prompt is not None:
                log_action("natural_message", "ask", "started", message)
                try:
                    await handle_basic_panam_message(message, basic_prompt)
                except Exception:
                    log_action("natural_message", "ask", "error", message)
                    logger.exception("Chyba při zpracování přirozené zprávy")
                    await message.channel.send("Něco se pokazilo při zpracování akce.")
                else:
                    log_action("natural_message", "ask", "success", message)
                return

            await message.channel.send("Tohle zatím neumím převést na akci. Zkus /help.")
            return

        intent_name, value = intent
        natural_action = natural_action or intent_name
        natural_status = "success"
        log_action("natural_message", natural_action, "started", message)

        try:
            if intent_name == "help":
                await send_channel_chunks(message, get_help_text())
                return

            if intent_name == "note_add_previous":
                previous_content = await find_recent_text_message(message.channel)
                if previous_content is None:
                    await message.channel.send(
                        "Nemám co uložit. Napiš text poznámky nebo to pošli pod zprávu, kterou si mám zapamatovat."
                    )
                    return

                response = await panam_core.handle_note_add(
                    previous_content,
                    message.author.id,
                    get_author_name(message.author),
                    message.channel.id,
                )
                await message.channel.send(response.text)
                return

            if intent_name == "note_add" and value:
                inline_note = extract_inline_content_after_trigger(
                    request_text,
                    list(NOTE_ADD_TRIGGERS),
                )
                if inline_note:
                    value = inline_note

                if is_context_reference(value):
                    previous_content = await find_recent_text_message(message.channel)
                    if previous_content is None:
                        await message.channel.send(
                            "Nemám co uložit. Napiš text poznámky nebo to pošli pod zprávu, kterou si mám zapamatovat."
                        )
                        return

                    response = await panam_core.handle_note_add(
                        previous_content,
                        message.author.id,
                        get_author_name(message.author),
                        message.channel.id,
                    )
                    await message.channel.send(response.text)
                    return

                response = await panam_core.handle_note_add(
                    value,
                    message.author.id,
                    get_author_name(message.author),
                    message.channel.id,
                )
                await message.channel.send(response.text)
                return

            if intent_name == "todo_add" and value:
                response = await panam_core.handle_todo_add(
                    value,
                    message.author.id,
                    get_author_name(message.author),
                    message.channel.id,
                )
                await message.channel.send(response.text)
                return

            if intent_name == "note_search" and value:
                response = await panam_core.handle_note_search(value)
                await message.channel.send(response.text)
                return

            if intent_name == "note_list":
                response = await panam_core.handle_note_list()
                await message.channel.send(response.text)
                return

            if intent_name == "todo_list":
                response = await panam_core.handle_todo_list()
                await message.channel.send(response.text)
                return

            if intent_name == "ask" and value:
                inline_question = extract_inline_content_after_trigger(
                    request_text,
                    list(OPINION_TRIGGERS),
                )
                if inline_question:
                    value = inline_question

                if is_context_reference(value):
                    selected_file = find_supported_attachment_in_message(message)
                    if selected_file is not None:
                        answer = await analyze_selected_attachment(
                            selected_file,
                            "Co si o této příloze myslíš?",
                        )
                        await message.reply(answer, mention_author=False)
                        return

                    previous_content = await find_recent_text_message(message.channel)
                    if previous_content is None:
                        selected_file = await find_recent_supported_attachment(message.channel)
                        if selected_file is not None:
                            answer = await analyze_selected_attachment(
                                selected_file,
                                "Co si o této příloze myslíš?",
                            )
                            await message.reply(answer, mention_author=False)
                            return

                    if previous_content is None:
                        await message.channel.send(
                            "Nevím, k čemu se mám vyjádřit. Pošli text, přílohu, nebo to napiš pod zprávu."
                        )
                        return

                    await send_channel_chunks(
                        message,
                        await ask_panam(
                            OPENAI_MODEL,
                            "Co si o tom myslíš?\n\n" + previous_content,
                        ),
                    )
                    return

                await send_channel_chunks(message, await ask_panam(OPENAI_MODEL, value))
                return

            if intent_name == "ask_previous":
                selected_file = find_supported_attachment_in_message(message)
                if selected_file is not None:
                    answer = await analyze_selected_attachment(
                        selected_file,
                        "Co si o této příloze myslíš?",
                    )
                    await message.reply(answer, mention_author=False)
                    return

                previous_content = await find_recent_text_message(message.channel)
                if previous_content is None:
                    selected_file = await find_recent_supported_attachment(message.channel)
                    if selected_file is not None:
                        answer = await analyze_selected_attachment(
                            selected_file,
                            "Co si o této příloze myslíš?",
                        )
                        await message.reply(answer, mention_author=False)
                        return

                if previous_content is None:
                    await message.channel.send(
                        "Nevím, k čemu se mám vyjádřit. Pošli text, přílohu, nebo to napiš pod zprávu."
                    )
                    return

                await send_channel_chunks(
                    message,
                    await ask_panam(
                        OPENAI_MODEL,
                        "Co si o tom myslíš?\n\n" + previous_content,
                    ),
                )
                return

            if intent_name == "summary" and value:
                inline_summary = extract_inline_content_after_trigger(
                    request_text,
                    list(SUMMARY_TRIGGERS),
                )
                if inline_summary:
                    value = inline_summary

                selected_file = find_supported_attachment_in_message(message)
                if selected_file is not None:
                    answer = await analyze_selected_attachment(
                        selected_file,
                        "Shrň tuto přílohu.",
                    )
                    await message.reply(answer, mention_author=False)
                    return

                if is_context_reference(value):
                    previous_content = await find_recent_text_message(message.channel)
                    if previous_content is None:
                        await message.channel.send(
                            "Nemám co shrnout. Pošli text, přílohu, nebo to napiš hned pod zprávu, kterou chceš shrnout."
                        )
                        return

                    response = await panam_core.handle_summary(
                        OPENAI_MODEL,
                        previous_content,
                    )
                    await send_channel_chunks(message, response.text)
                    return

                response = await panam_core.handle_summary(OPENAI_MODEL, value)
                await send_channel_chunks(message, response.text)
                return

            if intent_name == "summary_previous":
                selected_file = find_supported_attachment_in_message(message)
                if selected_file is not None:
                    answer = await analyze_selected_attachment(
                        selected_file,
                        "Shrň tuto přílohu.",
                    )
                    await message.reply(answer, mention_author=False)
                    return

                previous_content = await find_recent_text_message(message.channel)
                if previous_content is None:
                    await message.channel.send(
                        "Nemám co shrnout. Pošli text, přílohu, nebo to napiš hned pod zprávu, kterou chceš shrnout."
                    )
                    return

                response = await panam_core.handle_summary(
                    OPENAI_MODEL,
                    previous_content,
                )
                await send_channel_chunks(message, response.text)
                return

            if intent_name == "talk" and value:
                response = await panam_core.handle_talk(OPENAI_MODEL, value)
                await send_channel_chunks(message, response.text)
                return

            basic_prompt = extract_basic_panam_prompt(message.content or "")
            if basic_prompt is not None:
                await handle_basic_panam_message(message, basic_prompt)
                return

        except Exception:
            natural_status = "error"
            logger.exception("Chyba při zpracování přirozené zprávy")
            await message.channel.send("Něco se pokazilo při zpracování akce.")
        finally:
            log_action("natural_message", natural_action, natural_status, message)


bot = DiscordAIBot()


@bot.tree.command(
    name="ping",
    description="Ověř, že je bot online."
)
@log_slash_command("ping")
async def ping(interaction: discord.Interaction) -> None:
    if not is_interaction_allowed(interaction, "ping"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message("Panam je online.")


@bot.tree.command(
    name="help",
    description="Zobraz dostupné commandy."
)
@log_slash_command("help")
async def help_command(interaction: discord.Interaction) -> None:
    if not is_interaction_allowed(interaction, "help"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    help_chunks = split_discord_message(get_help_text())
    await interaction.response.send_message(help_chunks[0])
    for chunk in help_chunks[1:]:
        await interaction.followup.send(chunk)


@bot.tree.command(
    name="memory_clear",
    description="Vymaž krátkou konverzační paměť Panam pro tento kanál."
)
@log_slash_command("memory_clear")
async def memory_clear(interaction: discord.Interaction) -> None:
    channel_id = interaction.channel_id
    if channel_id is None:
        await interaction.response.send_message(
            "Tenhle command potřebuje běžet v kanálu.",
            ephemeral=True,
        )
        return

    if not is_interaction_allowed(interaction, "memory_clear"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    panam_memory.clear_channel_memory(channel_id)
    clear_last_file_context(channel_id)
    clear_last_router_decision(channel_id)
    await interaction.response.send_message(
        "Krátká paměť pro tento kanál je vymazaná."
    )


@bot.tree.command(
    name="note_add",
    description="Ulož krátkou poznámku."
)
@app_commands.describe(text="Text poznámky")
@log_slash_command("note_add")
async def note_add(interaction: discord.Interaction, text: str) -> None:
    if not is_interaction_allowed(interaction, "note_add"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        channel_id = interaction.channel_id or 0
        response = await panam_core.handle_note_add(
            text,
            interaction.user.id,
            get_author_name(interaction.user),
            channel_id,
        )
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /note_add")
        await interaction.response.send_message(
            "Něco se pokazilo při ukládání poznámky."
        )


@bot.tree.command(
    name="note_list",
    description="Vypiš poslední poznámky."
)
@log_slash_command("note_list")
async def note_list(interaction: discord.Interaction) -> None:
    if not is_interaction_allowed(interaction, "note_list"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        response = await panam_core.handle_note_list()
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /note_list")
        await interaction.response.send_message(
            "Něco se pokazilo při načítání poznámek."
        )


@bot.tree.command(
    name="note_search",
    description="Vyhledej uložené poznámky."
)
@app_commands.describe(query="Text k vyhledání")
@log_slash_command("note_search")
async def note_search(interaction: discord.Interaction, query: str) -> None:
    if not is_interaction_allowed(interaction, "note_search"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        response = await panam_core.handle_note_search(query)
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /note_search")
        await interaction.response.send_message(
            "Něco se pokazilo při vyhledávání poznámek."
        )


@bot.tree.command(
    name="todo_add",
    description="Přidej úkol do todo listu."
)
@app_commands.describe(text="Text úkolu")
@log_slash_command("todo_add")
async def todo_add(interaction: discord.Interaction, text: str) -> None:
    if not is_interaction_allowed(interaction, "todo_add"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        channel_id = interaction.channel_id or 0
        response = await panam_core.handle_todo_add(
            text,
            interaction.user.id,
            get_author_name(interaction.user),
            channel_id,
        )
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /todo_add")
        await interaction.response.send_message(
            "Něco se pokazilo při ukládání úkolu."
        )


@bot.tree.command(
    name="todo_list",
    description="Vypiš aktivní úkoly."
)
@log_slash_command("todo_list")
async def todo_list(interaction: discord.Interaction) -> None:
    if not is_interaction_allowed(interaction, "todo_list"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        response = await panam_core.handle_todo_list()
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /todo_list")
        await interaction.response.send_message(
            "Něco se pokazilo při načítání úkolů."
        )


@bot.tree.command(
    name="todo_done",
    description="Označ úkol jako hotový."
)
@app_commands.describe(todo_id="ID úkolu")
@log_slash_command("todo_done")
async def todo_done(interaction: discord.Interaction, todo_id: int) -> None:
    if not is_interaction_allowed(interaction, "todo_done"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        response = await panam_core.handle_todo_done(todo_id)
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /todo_done")
        await interaction.response.send_message(
            "Něco se pokazilo při dokončování úkolu."
        )


@bot.tree.command(
    name="search_messages",
    description="Vyhledej text v posledních zprávách kanálu."
)
@app_commands.describe(
    query="Text k vyhledání",
    limit="Kolik posledních zpráv prohledat, maximálně 300",
)
@log_slash_command("search_messages")
async def search_messages(
    interaction: discord.Interaction,
    query: str,
    limit: int = 100,
) -> None:
    if not is_interaction_allowed(interaction, "search_messages"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        channel = interaction.channel
        history = getattr(channel, "history", None)
        if history is None:
            await interaction.followup.send(
                "Něco se pokazilo při vyhledávání zpráv."
            )
            return

        query_lower = query.lower()
        search_limit = min(max(limit, 1), 300)
        matches = []

        async for message in history(limit=search_limit):
            if message.author.bot:
                continue

            content = message.content or ""
            content_lower = content.lower()
            if query_lower not in content_lower:
                continue

            match_index = content_lower.find(query_lower)
            start = max(match_index - 45, 0)
            end = min(match_index + len(query) + 90, len(content))
            excerpt = content[start:end].replace("\n", " ").strip()

            if start > 0:
                excerpt = "…" + excerpt
            if end < len(content):
                excerpt = excerpt + "…"

            created_at = message.created_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            author_name = getattr(message.author, "display_name", message.author.name)
            matches.append(
                f"{len(matches) + 1}. [{created_at}] {author_name}: {excerpt}"
            )

            if len(matches) >= 10:
                break

        if not matches:
            await interaction.followup.send("Nic jsem nenašla.")
            return

        answer = "Nalezené zprávy:\n" + "\n".join(matches)
        answer = shorten_for_discord(answer)

        await interaction.followup.send(answer)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /search_messages")
        await interaction.followup.send(
            "Něco se pokazilo při vyhledávání zpráv."
        )


@bot.tree.command(
    name="channel_summary",
    description="Shrň poslední zprávy z aktuálního kanálu."
)
@app_commands.describe(limit="Kolik posledních zpráv shrnout, maximálně 200")
@log_slash_command("channel_summary")
async def channel_summary(
    interaction: discord.Interaction,
    limit: int = 50,
) -> None:
    if not is_interaction_allowed(interaction, "channel_summary"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        channel = interaction.channel
        history = getattr(channel, "history", None)
        if history is None:
            await interaction.followup.send(
                "Něco se pokazilo při načítání zpráv."
            )
            return

        summary_limit = min(max(limit, 1), 200)
        messages = []

        async for message in history(limit=summary_limit):
            if message.author.bot:
                continue

            content = (message.content or "").strip()
            if not content:
                continue

            created_at = message.created_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            author_name = getattr(message.author, "display_name", message.author.name)
            messages.append(
                {
                    "author": author_name,
                    "created_at": created_at,
                    "content": content,
                }
            )

        if not messages:
            await interaction.followup.send("Nemám tu co shrnout.")
            return

        messages.reverse()
        channel_text = "\n".join(
            (
                f"Autor: {message['author']}\n"
                f"Čas: {message['created_at']}\n"
                f"Text: {message['content']}"
            )
            for message in messages
        )

        response = await panam_core.handle_channel_summary(OPENAI_MODEL, channel_text)
        answer = response.text
        await interaction.followup.send(answer)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /channel_summary")
        await interaction.followup.send(
            "Něco se pokazilo při shrnování kanálu."
        )


@bot.tree.command(
    name="summary",
    description="Stručně shrň delší text."
)
@app_commands.describe(text="Text ke shrnutí")
@log_slash_command("summary")
async def summary(interaction: discord.Interaction, text: str) -> None:
    if not is_interaction_allowed(interaction, "summary"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        response = await panam_core.handle_summary(OPENAI_MODEL, text)
        answer = response.text
        await interaction.followup.send(answer)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /summary")
        await interaction.followup.send(
            "Něco se pokazilo při shrnování textu. Mrkni do konzole na chybu."
        )


@bot.tree.command(
    name="analyze",
    description="Analyzuj přiložený obrázek nebo dokument pomocí AI."
)
@app_commands.describe(
    file="Soubor k analýze",
    question="Co chceš k souboru zjistit",
)
@log_slash_command("analyze")
async def analyze(
    interaction: discord.Interaction,
    file: discord.Attachment | None = None,
    question: str = "Popiš, co je v příloze.",
) -> None:
    if not is_interaction_allowed(interaction, "analyze"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    selected_file = file
    if selected_file is None:
        selected_file = await find_recent_supported_attachment(interaction.channel)

    if selected_file is None:
        await interaction.followup.send(
            "Nevidím žádnou podporovanou přílohu ani v commandu, ani v předchozí zprávě."
        )
        return

    log_attachment_info("slash_command", "analyze", interaction, selected_file)

    try:
        answer = await analyze_selected_attachment(selected_file, question)
        await interaction.followup.send(answer)
        set_last_file_context(
            interaction.channel_id,
            Path(selected_file.filename).name,
            get_file_extension(selected_file.filename),
            "chat_answer",
            file_summary=answer,
        )

    except AttachmentAnalysisUserError as error:
        await interaction.followup.send(str(error))

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /analyze")
        await interaction.followup.send(
            "Něco se pokazilo při analýze přílohy. Mrkni do konzole na chybu."
        )


@bot.tree.command(
    name="read_file",
    description="Přečti TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX přílohu pomocí AI."
)
@app_commands.describe(
    file="Dokument k přečtení",
    question="Co chceš k dokumentu zjistit",
)
@log_slash_command("read_file")
async def read_file(
    interaction: discord.Interaction,
    file: discord.Attachment,
    question: str = "Shrň mi tento dokument.",
) -> None:
    if not is_interaction_allowed(interaction, "read_file"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velký. Zatím beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        await interaction.response.send_message(
            "Tenhle typ dokumentu zatím neumím přečíst. Pošli mi prosím TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            ephemeral=True,
        )
        return

    log_attachment_info("slash_command", "read_file", interaction, file)

    await interaction.response.defer(thinking=True)

    try:
        data = await read_attachment_bytes(file)

        if extension == ".pdf":
            document_text = extract_text_from_pdf(data)
            if not document_text:
                await interaction.followup.send(
                    "Z toho PDF se mi nepodařilo vytáhnout žádný text. Možná je to sken nebo obrázkové PDF."
                )
                return
        elif extension == ".docx":
            document_text = extract_text_from_docx(data)
        elif extension == ".xlsx":
            document_text = extract_text_from_xlsx(data)
        else:
            document_text = extract_text_from_plain_file(data)

        if not document_text.strip():
            if extension == ".xlsx":
                await interaction.followup.send(
                    "Z toho Excelu se mi nepodarilo vytahnout zadna data."
                )
                return

            await interaction.followup.send(
                "Z toho dokumentu se mi nepodařilo vytáhnout žádný text."
            )
            return

        document_text = trim_document_text(document_text)
        answer = await analyze_document_text(
            OPENAI_MODEL,
            document_text,
            question,
            file.filename,
        )
        await interaction.followup.send(answer)
        set_last_file_context(
            interaction.channel_id,
            Path(file.filename).name,
            extension,
            "chat_answer",
            file_summary=answer,
        )

    except AttachmentAnalysisUserError as error:
        mark_command_status(interaction, "error")
        await interaction.followup.send(str(error))

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /read_file")
        await interaction.followup.send(
            "Něco se pokazilo při čtení dokumentu. Mrkni do konzole na chybu."
        )


@bot.tree.command(
    name="file_job_test",
    description="Otestuj docasnou file-job pipeline na dokumentove priloze."
)
@app_commands.describe(
    file="Dokument k otestovani pipeline",
    output_format="md nebo txt",
)
@log_slash_command("file_job_test")
async def file_job_test(
    interaction: discord.Interaction,
    file: discord.Attachment,
    output_format: str = "md",
) -> None:
    if not is_interaction_allowed(interaction, "file_job_test"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velky. Zatim beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        await interaction.response.send_message(
            "File job test zatim podporuje dokumenty TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            ephemeral=True,
        )
        return

    normalized_output_format = "md" if output_format.lower().strip(".") == "md" else "txt"
    output_extension = f".{normalized_output_format}"
    job = None
    await interaction.response.defer(thinking=True)

    try:
        job = panam_files.create_file_job(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            action="file_job_test",
        )
        log_action(
            "slash_command",
            "file_job_test",
            "started",
            interaction,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size=file.size,
        )

        input_path = await panam_files.save_attachment_to_job(file, job)
        data = input_path.read_bytes()
        extracted_text = extract_text_from_attachment(input_path.name, data)
        if not extracted_text.strip():
            extracted_text = "Z dokumentu se nepodarilo vytahnout zadny text."
        else:
            extracted_text = trim_document_text(extracted_text)
        panam_files.write_work_text(job, "extracted_text.txt", extracted_text)

        output_content = (
            "# Panam file job test\n\n"
            f"- job_id: `{job.job_id}`\n"
            f"- input: `{input_path.name}`\n\n"
            "## Extracted text\n\n"
            f"{extracted_text}\n"
        )
        if output_extension == ".txt":
            output_content = (
                "Panam file job test\n\n"
                f"job_id: {job.job_id}\n"
                f"input: {input_path.name}\n\n"
                "Extracted text\n\n"
                f"{extracted_text}\n"
            )

        output_path = panam_files.write_output_text(
            job,
            f"file_job_test_output{output_extension}",
            output_content,
        )
        job.status = "success"
        job.finished_at = datetime.now(timezone.utc).isoformat()
        panam_files.write_job_metadata(job)

        await interaction.followup.send(
            build_file_job_success_message(
                normalized_output_format,
                "file_job_test",
            ),
            file=discord.File(output_path),
        )
        log_action(
            "slash_command",
            "file_job_test",
            "success",
            interaction,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size=file.size,
        )

    except Exception:
        mark_command_status(interaction, "error")
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                "slash_command",
                "file_job_test",
                "error",
                interaction,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size=file.size,
            )
        logger.exception("Chyba pri zpracovani /file_job_test")
        await interaction.followup.send(
            "Neco se pokazilo pri testu file-job pipeline."
        )

    finally:
        if job is not None:
            panam_files.cleanup_job(job)


@bot.tree.command(
    name="transform_docx",
    description="Bezpecne vytvor novy cisty DOCX podle podporovane upravy."
)
@app_commands.describe(
    file="DOCX soubor k uprave",
    instruction="Jednoducha podporovana uprava dokumentu",
)
@log_slash_command("transform_docx")
async def transform_docx(
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
) -> None:
    if not is_interaction_allowed(interaction, "transform_docx"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velky. Zatim beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension != ".docx":
        await interaction.response.send_message(
            "Tenhle command podporuje jen DOCX soubory.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    await run_docx_transform_file_job(
        OPENAI_MODEL,
        interaction,
        file,
        instruction,
        "slash_command",
    )


@bot.tree.command(
    name="transform_excel",
    description="Bezpecne uprav XLSX podle jednoduche instrukce a vrat novy soubor."
)
@app_commands.describe(
    file="XLSX soubor k uprave",
    instruction="Jednoducha deterministicka uprava Excelu",
)
@log_slash_command("transform_excel")
async def transform_excel(
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
) -> None:
    if not is_interaction_allowed(interaction, "transform_excel"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velky. Zatim beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension != ".xlsx":
        await interaction.response.send_message(
            "Tenhle command podporuje jen XLSX soubory.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    await run_spreadsheet_transform_file_job(
        interaction,
        file,
        instruction,
        "slash_command",
    )


@bot.tree.command(
    name="process_file",
    description="Zpracuj dokument podle instrukce a vrat vystup jako soubor."
)
@app_commands.describe(
    file="Dokument ke zpracovani",
    instruction="Co ma Panam s dokumentem udelat",
    output_format="md, txt nebo docx",
)
@log_slash_command("process_file")
async def process_file(
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
    output_format: str = "md",
) -> None:
    if not is_interaction_allowed(interaction, "process_file"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velky. Zatim beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        await interaction.response.send_message(
            "Tenhle command podporuje dokumenty TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            ephemeral=True,
        )
        return

    normalized_output_format = output_format.lower().strip(".")
    if normalized_output_format not in {"md", "txt", "docx"}:
        await interaction.response.send_message(
            "Podporovane output_format jsou jen md, txt nebo docx.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    await run_human_document_file_job(
        OPENAI_MODEL,
        interaction,
        file,
        instruction,
        normalized_output_format,
        "slash_command",
        "process_file",
    )


@bot.tree.command(
    name="extract_data",
    description="Vytez ze souboru strukturovana data jako JSON, CSV, Markdown nebo XLSX."
)
@app_commands.describe(
    file="Dokument pro tezeni dat",
    instruction="Jaka data ma Panam vytahnout",
    output_format="json, csv, md nebo xlsx",
)
@log_slash_command("extract_data")
async def extract_data(
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
    output_format: str = "json",
) -> None:
    if not is_interaction_allowed(interaction, "extract_data"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velky. Zatim beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        await interaction.response.send_message(
            "Tenhle command podporuje dokumenty TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            ephemeral=True,
        )
        return

    normalized_output_format = output_format.lower().strip(".")
    if normalized_output_format not in {"json", "csv", "md", "xlsx"}:
        await interaction.response.send_message(
            "Podporovane output_format jsou jen json, csv, md nebo xlsx.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    await run_structured_data_file_job(
        OPENAI_MODEL,
        interaction,
        file,
        instruction,
        normalized_output_format,
        "slash_command",
        "extract_data",
    )


@bot.tree.command(
    name="ask",
    description="Pošli otázku do OpenAI API a vrať odpověď do Discordu."
)
@app_commands.describe(question="Tvoje otázka pro AI")
@log_slash_command("ask")
async def ask(interaction: discord.Interaction, question: str) -> None:
    if not is_interaction_allowed(interaction, "ask"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        response = await panam_core.handle_chat(OPENAI_MODEL, question)
        answer = response.text
        await interaction.followup.send(answer)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /ask")
        await interaction.followup.send(
            "Něco se pokazilo při volání AI. Mrkni do konzole na chybu."
        )


@bot.tree.command(
    name="panam_talk",
    description="Promluv si s Panam v osobnějším talk režimu."
)
@app_commands.describe(message="Zpráva pro Panam talk režim")
@log_slash_command("panam_talk")
async def panam_talk(interaction: discord.Interaction, message: str) -> None:
    if not is_interaction_allowed(interaction, "panam_talk"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        response = await panam_core.handle_talk(OPENAI_MODEL, message)
        answer = response.text
        await send_followup_chunks(interaction, answer)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /panam_talk")
        await interaction.followup.send(
            "Něco se pokazilo při talk režimu. Mrkni do konzole na chybu."
        )


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
