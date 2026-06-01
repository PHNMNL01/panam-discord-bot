import logging
from pathlib import Path

import discord

from panam_ai import analyze_document_text
from panam_discord_attachment_analysis import AttachmentAnalysisUserError
from panam_discord_attachments import (
    MAX_DOCUMENT_SIZE_BYTES,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    read_attachment_bytes,
)
from panam_discord_context import log_attachment_info, mark_command_status
from panam_file_context import set_last_file_context
from panam_text_extraction import (
    extract_text_from_docx,
    extract_text_from_pdf,
    extract_text_from_plain_file,
    extract_text_from_xlsx,
    get_file_extension,
    trim_document_text,
)


logger = logging.getLogger("panam")


def extract_read_file_document_text(filename: str, data: bytes) -> str:
    extension = get_file_extension(filename)
    if extension == ".pdf":
        return extract_text_from_pdf(data)
    if extension == ".docx":
        return extract_text_from_docx(data)
    if extension == ".xlsx":
        return extract_text_from_xlsx(data)
    return extract_text_from_plain_file(data)


async def handle_read_file_command(
    model: str,
    interaction: discord.Interaction,
    file: discord.Attachment,
    question: str,
) -> None:
    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velký. Zatím beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        await interaction.response.send_message(
            "Tenhle typ dokumentu zatím neumím přečíst. Pošli mi prosím TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            ephemeral=True,
        )
        return

    log_attachment_info("slash_command", "read_file", interaction, file)

    await interaction.response.defer(thinking=True)

    try:
        data = await read_attachment_bytes(file)
        document_text = extract_read_file_document_text(file.filename, data)

        if extension == ".pdf" and not document_text:
            await interaction.followup.send(
                "Z toho PDF se mi nepodařilo vytáhnout žádný text. Možná je to sken nebo obrázkové PDF."
            )
            return

        if not document_text.strip():
            if extension == ".xlsx":
                await interaction.followup.send(
                    "Z toho Excelu se mi nepodarilo vytahnout zadna data."
                )
                return

            await interaction.followup.send(
                "Z toho dokumentu se mi nepodařilo vytáhnout žádný text."
            )
            return

        document_text = trim_document_text(document_text)
        answer = await analyze_document_text(
            model,
            document_text,
            question,
            file.filename,
        )
        await interaction.followup.send(answer)
        set_last_file_context(
            interaction.channel_id,
            Path(file.filename).name,
            extension,
            "chat_answer",
            file_summary=answer,
        )

    except AttachmentAnalysisUserError as error:
        mark_command_status(interaction, "error")
        await interaction.followup.send(str(error))

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /read_file")
        await interaction.followup.send(
            "Něco se pokazilo při čtení dokumentu. Mrkni do konzole na chybu."
        )
