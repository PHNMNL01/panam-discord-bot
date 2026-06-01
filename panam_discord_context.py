import logging
from functools import wraps

import discord

from panam_discord_attachments import get_safe_attachment_info


COMMAND_STATUSES: dict[int, str] = {}

logger = logging.getLogger("panam")


def get_safe_user_name(user) -> str:
    return str(getattr(user, "display_name", getattr(user, "name", "unknown")))


def get_safe_context(source) -> dict[str, str | int | None]:
    user = getattr(source, "user", None) or getattr(source, "author", None)
    channel_id = getattr(source, "channel_id", None)
    if channel_id is None:
        channel = getattr(source, "channel", None)
        channel_id = getattr(channel, "id", None)

    guild_id = getattr(source, "guild_id", None)
    if guild_id is None:
        guild = getattr(source, "guild", None)
        guild_id = getattr(guild, "id", None)

    return {
        "user_id": getattr(user, "id", None),
        "user_name": get_safe_user_name(user) if user is not None else "unknown",
        "channel_id": channel_id,
        "guild_id": guild_id,
    }


def get_context_int(context: dict[str, str | int | None], key: str) -> int:
    value = context.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return 0


def log_attachment_info(action_type: str, action_name: str, source, file: discord.Attachment) -> None:
    context = get_safe_context(source)
    attachment = get_safe_attachment_info(file)
    logger.info(
        "action_type=%s action=%s user_id=%s user_name=%s channel_id=%s guild_id=%s filename=%s extension=%s size=%s file_type=%s",
        action_type,
        action_name,
        context["user_id"],
        context["user_name"],
        context["channel_id"],
        context["guild_id"],
        attachment.get("filename"),
        attachment.get("extension"),
        attachment.get("size"),
        attachment.get("file_type"),
    )


def log_action(
    action_type: str,
    action_name: str,
    status: str,
    source,
    **details,
) -> None:
    context = get_safe_context(source)
    detail_text = " ".join(
        f"{key}={value}"
        for key, value in details.items()
        if value is not None
    )
    logger.info(
        "action_type=%s action=%s status=%s user_id=%s user_name=%s channel_id=%s guild_id=%s%s",
        action_type,
        action_name,
        status,
        context["user_id"],
        context["user_name"],
        context["channel_id"],
        context["guild_id"],
        f" {detail_text}" if detail_text else "",
    )


def mark_command_status(interaction: discord.Interaction, status: str) -> None:
    COMMAND_STATUSES[interaction.id] = status


def log_slash_command(command_name: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(interaction: discord.Interaction, *args, **kwargs):
            log_action("slash_command", command_name, "started", interaction)
            try:
                result = await func(interaction, *args, **kwargs)
            except Exception:
                COMMAND_STATUSES.pop(interaction.id, None)
                log_action("slash_command", command_name, "error", interaction)
                logger.exception("Neosetrena chyba pri zpracovani /%s", command_name)
                raise

            status = COMMAND_STATUSES.pop(interaction.id, None)
            if status is None:
                log_action("slash_command", command_name, "success", interaction)
            elif status == "error":
                log_action("slash_command", command_name, "error", interaction)

            return result

        return wrapper

    return decorator


def mark_source_error(source) -> None:
    if isinstance(source, discord.Interaction):
        mark_command_status(source, "error")
