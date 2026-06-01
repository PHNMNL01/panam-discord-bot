from collections.abc import Callable
from typing import Optional

import discord


async def find_previous_message_content(
    message: discord.Message,
    prefer_same_author: bool = True,
) -> Optional[str]:
    history = getattr(message.channel, "history", None)
    if history is None:
        return None

    fallback_content = None

    async for previous_message in history(limit=30, before=message.created_at):
        if previous_message.author.bot:
            continue

        previous_content = (previous_message.content or "").strip()
        if not previous_content:
            continue

        if not prefer_same_author:
            return previous_content

        if previous_message.author.id == message.author.id:
            return previous_content

        if fallback_content is None:
            fallback_content = previous_content

    return fallback_content


async def find_recent_text_message(
    channel,
    should_skip_content: Callable[[str], bool] | None = None,
) -> str | None:
    history = getattr(channel, "history", None)
    if history is None:
        return None

    async for message in history(limit=15):
        if message.author.bot:
            continue

        content = (message.content or "").strip()
        if not content:
            continue

        if content.startswith("/"):
            continue

        if should_skip_content is not None and should_skip_content(content):
            continue

        return content

    return None
