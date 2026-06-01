import discord

import panam_memory
from panam_file_context import (
    clear_last_file_context,
    clear_last_router_decision,
)


async def handle_memory_clear_command(
    interaction: discord.Interaction,
    channel_id: int,
) -> None:
    panam_memory.clear_channel_memory(channel_id)
    clear_last_file_context(channel_id)
    clear_last_router_decision(channel_id)
    await interaction.response.send_message(
        "Krátká paměť pro tento kanál je vymazaná."
    )
