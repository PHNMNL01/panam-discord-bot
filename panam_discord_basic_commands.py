import discord

from panam_discord_help import get_help_text
from panam_discord_responses import split_discord_message


async def handle_ping_command(
    interaction: discord.Interaction,
) -> None:
    await interaction.response.send_message("Panam je online.")


async def handle_help_command(
    interaction: discord.Interaction,
) -> None:
    help_chunks = split_discord_message(get_help_text())
    await interaction.response.send_message(help_chunks[0])
    for chunk in help_chunks[1:]:
        await interaction.followup.send(chunk)
