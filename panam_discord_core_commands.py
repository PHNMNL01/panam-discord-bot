import logging

import discord

import panam_memory
import panam_core
from panam_discord_context import mark_command_status
from panam_discord_responses import send_followup_chunks
from panam_discord_voice import play_tts_text, truncate_text_for_voice


logger = logging.getLogger("panam")


def get_author_name(author) -> str:
    return getattr(author, "display_name", author.name)


def log_ask_voice_status(
    status: str,
    interaction: discord.Interaction,
    **details,
) -> None:
    user = getattr(interaction, "user", None)
    detail_text = " ".join(
        f"{key}={value}"
        for key, value in details.items()
        if value is not None
    )
    logger.info(
        "ask_voice status=%s guild_id=%s channel_id=%s user_id=%s%s",
        status,
        interaction.guild_id,
        interaction.channel_id,
        getattr(user, "id", None),
        f" {detail_text}" if detail_text else "",
    )


def log_panam_talk_voice_status(
    status: str,
    interaction: discord.Interaction,
    **details,
) -> None:
    user = getattr(interaction, "user", None)
    detail_text = " ".join(
        f"{key}={value}"
        for key, value in details.items()
        if value is not None
    )
    logger.info(
        "panam_talk_voice status=%s guild_id=%s channel_id=%s user_id=%s%s",
        status,
        interaction.guild_id,
        interaction.channel_id,
        getattr(user, "id", None),
        f" {detail_text}" if detail_text else "",
    )


async def handle_note_add_command(
    interaction: discord.Interaction,
    text: str,
) -> None:
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


async def handle_note_list_command(
    interaction: discord.Interaction,
) -> None:
    try:
        response = await panam_core.handle_note_list()
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /note_list")
        await interaction.response.send_message(
            "Něco se pokazilo při načítání poznámek."
        )


async def handle_note_search_command(
    interaction: discord.Interaction,
    query: str,
) -> None:
    try:
        response = await panam_core.handle_note_search(query)
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /note_search")
        await interaction.response.send_message(
            "Něco se pokazilo při vyhledávání poznámek."
        )


async def handle_todo_add_command(
    interaction: discord.Interaction,
    text: str,
) -> None:
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


async def handle_todo_list_command(
    interaction: discord.Interaction,
) -> None:
    try:
        response = await panam_core.handle_todo_list()
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /todo_list")
        await interaction.response.send_message(
            "Něco se pokazilo při načítání úkolů."
        )


async def handle_todo_done_command(
    interaction: discord.Interaction,
    todo_id: int,
) -> None:
    try:
        response = await panam_core.handle_todo_done(todo_id)
        await interaction.response.send_message(response.text)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /todo_done")
        await interaction.response.send_message(
            "Něco se pokazilo při dokončování úkolu."
        )


async def handle_summary_command(
    model: str,
    interaction: discord.Interaction,
    text: str,
) -> None:
    await interaction.response.defer(thinking=True)

    try:
        response = await panam_core.handle_summary(model, text)
        answer = response.text
        await interaction.followup.send(answer)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /summary")
        await interaction.followup.send(
            "Něco se pokazilo při shrnování textu. Mrkni do konzole na chybu."
        )


async def handle_ask_command(
    model: str,
    interaction: discord.Interaction,
    question: str,
) -> None:
    await interaction.response.defer(thinking=True)

    try:
        response = await panam_core.handle_chat(model, question)
        answer = response.text
        await interaction.followup.send(answer)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /ask")
        await interaction.followup.send(
            "Něco se pokazilo při volání AI. Mrkni do konzole na chybu."
        )


async def handle_ask_voice_command(
    model: str,
    interaction: discord.Interaction,
    question: str,
) -> None:
    log_ask_voice_status(
        "started",
        interaction,
    )
    await interaction.response.defer(thinking=True)

    channel_id = interaction.channel_id
    history = panam_memory.get_messages(channel_id) if channel_id is not None else None

    try:
        response = await panam_core.handle_chat(model, question, history=history)
    except Exception:
        mark_command_status(interaction, "error")
        log_ask_voice_status(
            "error",
            interaction,
        )
        logger.exception("Chyba pri zpracovani /ask_voice")
        await interaction.followup.send(
            "Neco se pokazilo pri volani AI. Mrkni do konzole na chybu."
        )
        return

    answer = str(response.text or "").strip() or "Nemam odpoved."
    voice_text = truncate_text_for_voice(answer)
    voice_text_length = len(voice_text)

    try:
        await send_followup_chunks(interaction, answer)
    except Exception:
        mark_command_status(interaction, "error")
        log_ask_voice_status(
            "error",
            interaction,
        )
        logger.exception("Chyba pri odesilani /ask_voice odpovedi")
        return

    if channel_id is not None:
        panam_memory.add_message(channel_id, "user", question)
        panam_memory.add_message(channel_id, "assistant", answer)

    log_ask_voice_status(
        "answered",
        interaction,
    )

    played = await play_tts_text(
        interaction,
        voice_text,
        "ask_voice",
        original_text_length=voice_text_length,
    )
    if not played:
        mark_command_status(interaction, "error")
        log_ask_voice_status(
            "voice_error",
            interaction,
        )
        return

    log_ask_voice_status(
        "success",
        interaction,
    )


async def handle_panam_talk_command(
    model: str,
    interaction: discord.Interaction,
    message: str,
) -> None:
    await interaction.response.defer(thinking=True)

    try:
        response = await panam_core.handle_talk(model, message)
        answer = response.text
        await send_followup_chunks(interaction, answer)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /panam_talk")
        await interaction.followup.send(
            "Něco se pokazilo při talk režimu. Mrkni do konzole na chybu."
        )


async def handle_panam_talk_voice_command(
    model: str,
    interaction: discord.Interaction,
    message: str,
) -> None:
    message_length = len(message or "")
    log_panam_talk_voice_status(
        "started",
        interaction,
        message_length=message_length,
    )
    await interaction.response.defer(thinking=True)

    try:
        response = await panam_core.handle_talk(model, message)
    except Exception:
        mark_command_status(interaction, "error")
        log_panam_talk_voice_status(
            "error",
            interaction,
            message_length=message_length,
        )
        logger.exception("Chyba pri zpracovani /panam_talk_voice")
        await interaction.followup.send(
            "Neco se pokazilo pri talk rezimu. Mrkni do konzole na chybu."
        )
        return

    answer = str(response.text or "").strip() or "Nemam odpoved."
    voice_text = truncate_text_for_voice(answer)
    answer_length = len(answer)
    voice_text_length = len(voice_text)

    try:
        await send_followup_chunks(interaction, answer)
    except Exception:
        mark_command_status(interaction, "error")
        log_panam_talk_voice_status(
            "error",
            interaction,
            message_length=message_length,
            answer_length=answer_length,
            voice_text_length=voice_text_length,
        )
        logger.exception("Chyba pri odesilani /panam_talk_voice odpovedi")
        return

    log_panam_talk_voice_status(
        "answered",
        interaction,
        message_length=message_length,
        answer_length=answer_length,
        voice_text_length=voice_text_length,
    )

    played = await play_tts_text(
        interaction,
        voice_text,
        "panam_talk_voice",
        original_text_length=voice_text_length,
    )
    if not played:
        mark_command_status(interaction, "error")
        log_panam_talk_voice_status(
            "voice_error",
            interaction,
            message_length=message_length,
            answer_length=answer_length,
            voice_text_length=voice_text_length,
        )
        return

    log_panam_talk_voice_status(
        "success",
        interaction,
        message_length=message_length,
        answer_length=answer_length,
        voice_text_length=voice_text_length,
    )
