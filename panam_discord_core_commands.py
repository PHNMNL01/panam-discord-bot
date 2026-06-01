import logging

import discord

import panam_core
from panam_discord_context import mark_command_status
from panam_discord_responses import send_followup_chunks


logger = logging.getLogger("panam")


def get_author_name(author) -> str:
    return getattr(author, "display_name", author.name)


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
