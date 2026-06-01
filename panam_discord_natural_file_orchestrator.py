import logging
from pathlib import Path

import discord

import panam_memory
from panam_ai import ask_panam, classify_file_request_intent
from panam_discord_attachment_analysis import (
    AttachmentAnalysisUserError,
    analyze_selected_attachment,
    get_attachment_context_question,
)
from panam_discord_attachments import (
    MAX_DOCUMENT_SIZE_BYTES,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    get_attachment_kind,
    get_safe_attachment_info,
)
from panam_discord_context import log_action
from panam_discord_history import (
    find_recent_docx_attachment,
    find_recent_supported_attachment,
    find_recent_xlsx_attachment,
)
from panam_discord_message_helpers import normalize_natural_text
from panam_file_context import (
    get_last_file_context,
    set_last_file_context,
    set_last_router_decision,
)
from panam_text_extraction import get_file_extension


logger = logging.getLogger("panam")

CREATIVE_SPREADSHEET_FALLBACK_MESSAGE = (
    "Tohle je moc volná úprava. Původní Excel neupravuju a data si nedomýšlím. "
    "Umím bezpečně udělat nový XLSX třeba: odstranit prázdné řádky, filtrovat řádky podle sloupce, "
    "vybrat sloupce, seřadit podle sloupce nebo najít duplicity."
)


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
    model: str,
    message: discord.Message,
    file: discord.Attachment | None,
    decision: dict,
) -> None:
    mode = decision.get("mode")
    action_name = get_natural_file_action_name(decision)

    if mode == "unsupported_creative_docx_edit":
        from panam_docx_transform import DOCX_TRANSFORM_FALLBACK_MESSAGE

        remember_router_decision(
            message,
            decision,
            "current_attachment" if file is not None else "none",
        )
        log_action("natural_message", action_name, "started", message)
        await message.reply(
            DOCX_TRANSFORM_FALLBACK_MESSAGE,
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
            answer = await analyze_selected_attachment(model, file, question)
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
            from panam_discord_file_jobs import run_docx_transform_file_job

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
                model,
                message,
                file,
                decision.get("instruction", message.content or ""),
                "natural_message",
            )
            return

        if mode == "spreadsheet_transform":
            from panam_discord_file_jobs import run_spreadsheet_transform_file_job

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
            from panam_discord_file_jobs import run_human_document_file_job

            output_format = decision.get("output_format", "md")
            if output_format not in {"md", "txt", "docx"}:
                output_format = "md"

            decision["output_format"] = output_format
            remember_router_decision(message, decision)
            await run_human_document_file_job(
                model,
                message,
                file,
                decision.get("instruction", message.content or ""),
                output_format,
                "natural_message",
            )
            return

        if mode == "structured_data":
            from panam_discord_file_jobs import run_structured_data_file_job

            output_format = decision.get("output_format", "json")
            if output_format not in {"json", "csv", "md", "xlsx"}:
                output_format = "json"

            decision["output_format"] = output_format
            remember_router_decision(message, decision)
            await run_structured_data_file_job(
                model,
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
    model: str,
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
        from panam_router import validate_ai_file_intent

        raw_intent = await classify_file_request_intent(
            model,
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
        from panam_docx_transform import DOCX_TRANSFORM_FALLBACK_MESSAGE

        await message.reply(
            DOCX_TRANSFORM_FALLBACK_MESSAGE,
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

    await handle_natural_file_request(model, message, selected_file, decision)
    return True


async def handle_file_summary_followup(
    model: str,
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
        answer = await ask_panam(model, prompt)
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
