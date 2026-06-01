import os
import logging
import logging.handlers
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

import panam_memory
import panam_core
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
from panam_discord_context import (
    log_action,
    log_attachment_info,
    log_slash_command,
    mark_command_status,
)
from panam_discord_channel_tools import (
    build_channel_summary_text,
    search_recent_channel_messages,
)
from panam_discord_file_commands import (
    handle_extract_data_command,
    handle_process_file_command,
    handle_transform_docx_command,
    handle_transform_excel_command,
)
from panam_discord_file_job_test import handle_file_job_test_command
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
    is_file_router_candidate,
    parse_natural_intent,
    should_skip_recent_text_content,
)
from panam_discord_responses import (
    send_channel_chunks,
    send_followup_chunks,
    split_discord_message,
)
from panam_discord_read_file import handle_read_file_command
from panam_discord_text_history import (
    find_recent_text_message,
)
from panam_file_context import (
    clear_last_file_context,
    clear_last_router_decision,
    get_last_file_context,
    set_last_file_context,
)
from panam_ai import (
    ask_panam,
    shorten_for_discord,
)
from panam_phrases import (
    BASIC_PANAM_EMPTY_RESPONSE,
    NOTE_ADD_TRIGGERS,
    OPINION_TRIGGERS,
    SUMMARY_TRIGGERS,
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
)
from panam_text_extraction import get_file_extension


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
                OPENAI_MODEL,
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
                OPENAI_MODEL,
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
                    OPENAI_MODEL,
                    message,
                    request_text,
                    last_file_context,
                )
                if handled_by_summary:
                    return

        if is_ambiguous_context_request(request_text) and not meta_router_discussion:
            handled_by_classifier = await handle_ai_classified_file_request(
                OPENAI_MODEL,
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
                    OPENAI_MODEL,
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
                    OPENAI_MODEL,
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
                            OPENAI_MODEL,
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
                                OPENAI_MODEL,
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
                        OPENAI_MODEL,
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
                            OPENAI_MODEL,
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
                        OPENAI_MODEL,
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
                        OPENAI_MODEL,
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
        answer = await search_recent_channel_messages(channel, query, limit)
        if answer is None:
            await interaction.followup.send(
                "Něco se pokazilo při vyhledávání zpráv."
            )
            return

        if answer == "":
            await interaction.followup.send("Nic jsem nenašla.")
            return

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
        channel_text = await build_channel_summary_text(channel, limit)
        if channel_text is None:
            await interaction.followup.send(
                "Něco se pokazilo při načítání zpráv."
            )
            return

        if channel_text == "":
            await interaction.followup.send("Nemám tu co shrnout.")
            return

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
        answer = await analyze_selected_attachment(OPENAI_MODEL, selected_file, question)
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

    await handle_read_file_command(
        OPENAI_MODEL,
        interaction,
        file,
        question,
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

    await handle_file_job_test_command(
        interaction,
        file,
        output_format,
    )


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

    await handle_transform_docx_command(
        OPENAI_MODEL,
        interaction,
        file,
        instruction,
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

    await handle_transform_excel_command(
        interaction,
        file,
        instruction,
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

    await handle_process_file_command(
        OPENAI_MODEL,
        interaction,
        file,
        instruction,
        output_format,
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

    await handle_extract_data_command(
        OPENAI_MODEL,
        interaction,
        file,
        instruction,
        output_format,
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
