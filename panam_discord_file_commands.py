import discord

from panam_discord_attachments import (
    MAX_DOCUMENT_SIZE_BYTES,
    SUPPORTED_DOCUMENT_EXTENSIONS,
)
from panam_text_extraction import get_file_extension


def normalize_output_format(output_format: str) -> str:
    return output_format.lower().strip(".")


async def handle_transform_docx_command(
    model: str,
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
) -> None:
    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velky. Zatim beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension != ".docx":
        await interaction.response.send_message(
            "Tenhle command podporuje jen DOCX soubory.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    from panam_discord_file_jobs import run_docx_transform_file_job

    await run_docx_transform_file_job(
        model,
        interaction,
        file,
        instruction,
        "slash_command",
    )


async def handle_transform_excel_command(
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
) -> None:
    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velky. Zatim beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension != ".xlsx":
        await interaction.response.send_message(
            "Tenhle command podporuje jen XLSX soubory.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    from panam_discord_file_jobs import run_spreadsheet_transform_file_job

    await run_spreadsheet_transform_file_job(
        interaction,
        file,
        instruction,
        "slash_command",
    )


async def handle_process_file_command(
    model: str,
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
    output_format: str = "md",
) -> None:
    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velky. Zatim beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        await interaction.response.send_message(
            "Tenhle command podporuje dokumenty TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            ephemeral=True,
        )
        return

    normalized_output_format = normalize_output_format(output_format)
    if normalized_output_format not in {"md", "txt", "docx"}:
        await interaction.response.send_message(
            "Podporovane output_format jsou jen md, txt nebo docx.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    from panam_discord_file_jobs import run_human_document_file_job

    await run_human_document_file_job(
        model,
        interaction,
        file,
        instruction,
        normalized_output_format,
        "slash_command",
        "process_file",
    )


async def handle_extract_data_command(
    model: str,
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
    output_format: str = "json",
) -> None:
    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        await interaction.response.send_message(
            "Ten soubor je moc velky. Zatim beru max 20 MB.",
            ephemeral=True,
        )
        return

    extension = get_file_extension(file.filename)
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        await interaction.response.send_message(
            "Tenhle command podporuje dokumenty TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            ephemeral=True,
        )
        return

    normalized_output_format = normalize_output_format(output_format)
    if normalized_output_format not in {"json", "csv", "md", "xlsx"}:
        await interaction.response.send_message(
            "Podporovane output_format jsou jen json, csv, md nebo xlsx.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    from panam_discord_file_jobs import run_structured_data_file_job

    await run_structured_data_file_job(
        model,
        interaction,
        file,
        instruction,
        normalized_output_format,
        "slash_command",
        "extract_data",
    )
