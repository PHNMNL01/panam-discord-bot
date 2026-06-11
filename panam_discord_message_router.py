import logging
from pathlib import Path

import discord

import panam_memory
import panam_core
from panam_ai import ask_panam
from panam_discord_attachments import (
    get_attachment_kind,
    get_safe_attachment_info,
    find_supported_attachment_in_message,
)
from panam_discord_attachment_analysis import (
    AttachmentAnalysisUserError,
    analyze_selected_attachment,
    get_attachment_context_question,
    get_natural_attachment_analyze_question,
    is_general_attachment_context_request,
    is_natural_attachment_analyze_request,
)
from panam_discord_context import log_action
from panam_discord_help import get_help_text
from panam_discord_history import (
    find_recent_docx_attachment,
    find_recent_supported_attachment,
    find_recent_xlsx_attachment,
)
from panam_discord_message_helpers import (
    extract_basic_panam_prompt,
    extract_inline_content_after_trigger,
    extract_panam_request,
    is_context_reference,
)
from panam_discord_natural_file_orchestrator import (
    handle_ai_classified_file_request,
    handle_file_summary_followup,
    handle_natural_file_request,
    remember_router_decision,
)
from panam_discord_natural_intents import (
    get_natural_action_name,
    parse_natural_voice_reply_question,
    parse_natural_intent,
    should_skip_recent_text_content,
)
from panam_discord_responses import send_channel_chunks
from panam_discord_text_history import find_recent_text_message
from panam_discord_voice import play_tts_text_for_message, truncate_text_for_voice
from panam_file_context import (
    get_last_file_context,
    set_last_file_context,
)
from panam_phrases import (
    BASIC_PANAM_EMPTY_RESPONSE,
    NOTE_ADD_TRIGGERS,
    OPINION_TRIGGERS,
    SUMMARY_TRIGGERS,
)
from panam_text_extraction import get_file_extension

try:
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
    )
except ModuleNotFoundError as router_import_error:
    if router_import_error.name != "docx":
        raise

    _ROUTER_IMPORT_ERROR = router_import_error

    def _missing_router_dependency(*args, **kwargs):
        raise _ROUTER_IMPORT_ERROR

    decide_file_response_mode = _missing_router_dependency
    has_direct_file_edit_request = _missing_router_dependency
    has_docx_transform_request = _missing_router_dependency
    has_explicit_file_action_request = _missing_router_dependency
    has_explicit_file_output_request = _missing_router_dependency
    has_spreadsheet_transform_request = _missing_router_dependency
    has_unsafe_creative_docx_request = _missing_router_dependency
    has_unsafe_creative_spreadsheet_request = _missing_router_dependency
    is_ambiguous_context_request = _missing_router_dependency
    is_followup_to_file_summary = _missing_router_dependency
    is_meta_router_or_behavior_discussion = _missing_router_dependency
    matches_last_file_reference = _missing_router_dependency


logger = logging.getLogger("panam")


def get_author_name(author) -> str:
    return getattr(author, "display_name", author.name)


def log_natural_voice_reply_status(
    status: str,
    message: discord.Message,
    *,
    question_length: int | None = None,
    answer_length: int | None = None,
) -> None:
    logger.info(
        "action=natural_voice_reply status=%s user_id=%s channel_id=%s guild_id=%s question_length=%s answer_length=%s",
        status,
        getattr(message.author, "id", None),
        getattr(message.channel, "id", None),
        getattr(message.guild, "id", None),
        question_length,
        answer_length,
    )


def _message_author_voice_channel(message: discord.Message):
    author_voice = getattr(message.author, "voice", None)
    return getattr(author_voice, "channel", None)


async def handle_natural_voice_reply(
    model: str,
    message: discord.Message,
    question: str,
) -> None:
    question_length = len(question or "")
    answer_length: int | None = None
    log_natural_voice_reply_status(
        "started",
        message,
        question_length=question_length,
    )

    try:
        answer = (await ask_panam(model, question)).strip() or "Nemam odpoved."
        answer_length = len(answer)
        await send_channel_chunks(message, answer)
    except Exception:
        log_natural_voice_reply_status(
            "error",
            message,
            question_length=question_length,
            answer_length=answer_length,
        )
        logger.exception("Chyba pri zpracovani natural voice reply")
        await message.channel.send("Neco se pokazilo pri zpracovani hlasove odpovedi.")
        return

    if _message_author_voice_channel(message) is None:
        await message.channel.send(
            "Textove jsem odpovedela, ale hlas nemuzu prehrat, protoze nejsi ve voice kanalu."
        )
        log_natural_voice_reply_status(
            "voice_unavailable",
            message,
            question_length=question_length,
            answer_length=answer_length,
        )
        return

    voice_text = truncate_text_for_voice(answer)
    played = await play_tts_text_for_message(
        message,
        voice_text,
        "natural_voice_reply",
        original_text_length=len(voice_text),
        log_playback=False,
    )
    if not played:
        log_natural_voice_reply_status(
            "voice_error",
            message,
            question_length=question_length,
            answer_length=answer_length,
        )
        return

    log_natural_voice_reply_status(
        "success",
        message,
        question_length=question_length,
        answer_length=answer_length,
    )


async def handle_basic_panam_message(
    model: str,
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
    response = await panam_core.handle_chat(model, basic_prompt, history=history)
    answer = response.text
    await message.reply(answer, mention_author=False)
    panam_memory.add_message(message.channel.id, "user", message.content or basic_prompt)
    panam_memory.add_message(message.channel.id, "assistant", answer)



async def handle_discord_message(
    model: str,
    message: discord.Message,
    bot_user: discord.ClientUser,
    allowed_channel_ids: list[str],
) -> None:
    request_text = extract_panam_request(message, bot_user)
    if request_text is None:
        return

    natural_voice_question = parse_natural_voice_reply_question(request_text)
    if natural_voice_question is not None:
        question_length = len(natural_voice_question)
        if allowed_channel_ids and str(message.channel.id) not in allowed_channel_ids:
            log_natural_voice_reply_status(
                "denied",
                message,
                question_length=question_length,
            )
            return

        await handle_natural_voice_reply(model, message, natural_voice_question)
        return

    current_file = find_supported_attachment_in_message(message)
    selected_file = current_file
    natural_action = get_natural_action_name(request_text, message.content or "")
    if allowed_channel_ids and str(message.channel.id) not in allowed_channel_ids:
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
            model,
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
        await handle_natural_file_request(
            model,
            message,
            selected_file,
            file_decision,
        )
        return

    if (
        current_file is None
        and not meta_router_discussion
        and is_followup_to_file_summary(request_text)
    ):
        if last_file_context is not None and last_file_context.get("file_summary"):
            handled_by_summary = await handle_file_summary_followup(
                model,
                message,
                request_text,
                last_file_context,
            )
            if handled_by_summary:
                return

    if is_ambiguous_context_request(request_text) and not meta_router_discussion:
        handled_by_classifier = await handle_ai_classified_file_request(
            model,
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
            answer = await analyze_selected_attachment(
                model,
                selected_file,
                question,
            )
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
            answer = await analyze_selected_attachment(
                model,
                selected_file,
                question,
            )
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
                await handle_basic_panam_message(model, message, basic_prompt)
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
            previous_content = await find_recent_text_message(
                message.channel,
                should_skip_content=should_skip_recent_text_content,
            )
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
                previous_content = await find_recent_text_message(
                    message.channel,
                    should_skip_content=should_skip_recent_text_content,
                )
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
                        model,
                        selected_file,
                        "Co si o této příloze myslíš?",
                    )
                    await message.reply(answer, mention_author=False)
                    return

                previous_content = await find_recent_text_message(
                    message.channel,
                    should_skip_content=should_skip_recent_text_content,
                )
                if previous_content is None:
                    selected_file = await find_recent_supported_attachment(message.channel)
                    if selected_file is not None:
                        answer = await analyze_selected_attachment(
                            model,
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
                        model,
                        "Co si o tom myslíš?\n\n" + previous_content,
                    ),
                )
                return

            await send_channel_chunks(message, await ask_panam(model, value))
            return

        if intent_name == "ask_previous":
            selected_file = find_supported_attachment_in_message(message)
            if selected_file is not None:
                answer = await analyze_selected_attachment(
                    model,
                    selected_file,
                    "Co si o této příloze myslíš?",
                )
                await message.reply(answer, mention_author=False)
                return

            previous_content = await find_recent_text_message(
                message.channel,
                should_skip_content=should_skip_recent_text_content,
            )
            if previous_content is None:
                selected_file = await find_recent_supported_attachment(message.channel)
                if selected_file is not None:
                    answer = await analyze_selected_attachment(
                        model,
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
                    model,
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
                    model,
                    selected_file,
                    "Shrň tuto přílohu.",
                )
                await message.reply(answer, mention_author=False)
                return

            if is_context_reference(value):
                previous_content = await find_recent_text_message(
                    message.channel,
                    should_skip_content=should_skip_recent_text_content,
                )
                if previous_content is None:
                    await message.channel.send(
                        "Nemám co shrnout. Pošli text, přílohu, nebo to napiš hned pod zprávu, kterou chceš shrnout."
                    )
                    return

                response = await panam_core.handle_summary(
                    model,
                    previous_content,
                )
                await send_channel_chunks(message, response.text)
                return

            response = await panam_core.handle_summary(model, value)
            await send_channel_chunks(message, response.text)
            return

        if intent_name == "summary_previous":
            selected_file = find_supported_attachment_in_message(message)
            if selected_file is not None:
                answer = await analyze_selected_attachment(
                    model,
                    selected_file,
                    "Shrň tuto přílohu.",
                )
                await message.reply(answer, mention_author=False)
                return

            previous_content = await find_recent_text_message(
                message.channel,
                should_skip_content=should_skip_recent_text_content,
            )
            if previous_content is None:
                await message.channel.send(
                    "Nemám co shrnout. Pošli text, přílohu, nebo to napiš hned pod zprávu, kterou chceš shrnout."
                )
                return

            response = await panam_core.handle_summary(
                model,
                previous_content,
            )
            await send_channel_chunks(message, response.text)
            return

        if intent_name == "talk" and value:
            response = await panam_core.handle_talk(model, value)
            await send_channel_chunks(message, response.text)
            return

        basic_prompt = extract_basic_panam_prompt(message.content or "")
        if basic_prompt is not None:
            await handle_basic_panam_message(model, message, basic_prompt)
            return

    except Exception:
        natural_status = "error"
        logger.exception("Chyba při zpracování přirozené zprávy")
        await message.channel.send("Něco se pokazilo při zpracování akce.")
    finally:
        log_action("natural_message", natural_action, natural_status, message)
