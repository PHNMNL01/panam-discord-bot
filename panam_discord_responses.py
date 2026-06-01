from pathlib import Path

import discord


def split_discord_message(text: str, limit: int = 1900) -> list[str]:
    if not text:
        return [""]

    chunks = []
    remaining = text.strip()

    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks


async def send_followup_chunks(interaction: discord.Interaction, text: str) -> None:
    for chunk in split_discord_message(text):
        await interaction.followup.send(chunk)


async def send_channel_chunks(message: discord.Message, text: str) -> None:
    for chunk in split_discord_message(text):
        await message.channel.send(chunk)


async def send_source_message(source, text: str, file_path: Path | None = None) -> None:
    if isinstance(source, discord.Interaction):
        if file_path is None:
            await source.followup.send(text)
        else:
            await source.followup.send(text, file=discord.File(file_path))
        return

    if file_path is None:
        await source.reply(text, mention_author=False)
    else:
        await source.reply(text, file=discord.File(file_path), mention_author=False)
