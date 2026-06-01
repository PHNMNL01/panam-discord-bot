import logging

import discord

import panam_core
from panam_ai import shorten_for_discord
from panam_discord_channel_tools import (
    build_channel_summary_text,
    search_recent_channel_messages,
)
from panam_discord_context import mark_command_status


logger = logging.getLogger("panam")


async def handle_search_messages_command(
    interaction: discord.Interaction,
    query: str,
    limit: int = 100,
) -> None:
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


async def handle_channel_summary_command(
    model: str,
    interaction: discord.Interaction,
    limit: int = 50,
) -> None:
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

        response = await panam_core.handle_channel_summary(model, channel_text)
        answer = response.text
        await interaction.followup.send(answer)

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /channel_summary")
        await interaction.followup.send(
            "Něco se pokazilo při shrnování kanálu."
        )
