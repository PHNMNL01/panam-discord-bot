import discord

from panam_discord_attachments import (
    get_attachment_kind,
    is_supported_image_attachment,
)
from panam_text_extraction import get_file_extension


async def find_recent_image_attachment(channel) -> discord.Attachment | None:
    history = getattr(channel, "history", None)
    if history is None:
        return None

    async for message in history(limit=15):
        if message.author.bot:
            continue

        for attachment in message.attachments:
            if is_supported_image_attachment(attachment):
                return attachment

    return None


async def find_recent_supported_attachment(channel) -> discord.Attachment | None:
    history = getattr(channel, "history", None)
    if history is None:
        return None

    async for message in history(limit=15):
        if message.author.bot:
            continue

        for attachment in message.attachments:
            if get_attachment_kind(attachment) is not None:
                return attachment

    return None


async def find_recent_xlsx_attachment(channel) -> discord.Attachment | None:
    history = getattr(channel, "history", None)
    if history is None:
        return None

    async for message in history(limit=15):
        if message.author.bot:
            continue

        for attachment in message.attachments:
            if get_file_extension(attachment.filename) == ".xlsx":
                return attachment

    return None


async def find_recent_docx_attachment(channel) -> discord.Attachment | None:
    history = getattr(channel, "history", None)
    if history is None:
        return None

    async for message in history(limit=15):
        if message.author.bot:
            continue

        for attachment in message.attachments:
            if get_file_extension(attachment.filename) == ".docx":
                return attachment

    return None
