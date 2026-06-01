from pathlib import Path
from typing import Any

import discord

from panam_text_extraction import get_file_extension


SUPPORTED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
SUPPORTED_DOCUMENT_EXTENSIONS = (".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".xlsx")
MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_SIZE_BYTES = 20 * 1024 * 1024


def is_supported_image_attachment(attachment: discord.Attachment | Any) -> bool:
    return get_file_extension(attachment.filename) in SUPPORTED_IMAGE_EXTENSIONS


def is_supported_document_attachment(attachment: discord.Attachment | Any) -> bool:
    return get_file_extension(attachment.filename) in SUPPORTED_DOCUMENT_EXTENSIONS


def get_attachment_kind(file: discord.Attachment | Any) -> str | None:
    extension = get_file_extension(file.filename)
    if extension in SUPPORTED_IMAGE_EXTENSIONS:
        return "image"
    if extension in SUPPORTED_DOCUMENT_EXTENSIONS:
        return "document"
    return None


def get_safe_attachment_info(
    file: discord.Attachment | Any | None,
) -> dict[str, str | int | None]:
    if file is None:
        return {}

    extension = get_file_extension(file.filename)
    return {
        "filename": Path(file.filename).name,
        "extension": extension,
        "size": file.size,
        "file_type": get_attachment_kind(file),
    }


def find_image_attachment_in_message(
    message: discord.Message | Any,
) -> discord.Attachment | Any | None:
    for attachment in message.attachments:
        if is_supported_image_attachment(attachment):
            return attachment

    return None


def find_supported_attachment_in_message(
    message: discord.Message | Any,
) -> discord.Attachment | Any | None:
    for attachment in message.attachments:
        if get_attachment_kind(attachment) is not None:
            return attachment

    return None


async def read_attachment_bytes(file: discord.Attachment | Any) -> bytes:
    return await file.read()
