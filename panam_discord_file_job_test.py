import logging
from datetime import datetime, timezone
from pathlib import Path

import discord

import panam_files
from panam_discord_attachments import (
    MAX_DOCUMENT_SIZE_BYTES,
    SUPPORTED_DOCUMENT_EXTENSIONS,
)
from panam_discord_context import log_action, mark_command_status
from panam_file_responses import build_file_job_success_message
from panam_text_extraction import (
    extract_text_from_attachment,
    get_file_extension,
    trim_document_text,
)


logger = logging.getLogger("panam")


def normalize_file_job_test_output_format(output_format: str) -> str:
    return "md" if output_format.lower().strip(".") == "md" else "txt"


async def handle_file_job_test_command(
    interaction: discord.Interaction,
    file: discord.Attachment,
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
            "File job test zatim podporuje dokumenty TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX.",
            ephemeral=True,
        )
        return

    normalized_output_format = normalize_file_job_test_output_format(output_format)
    output_extension = f".{normalized_output_format}"
    job = None
    await interaction.response.defer(thinking=True)

    try:
        job = panam_files.create_file_job(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            action="file_job_test",
        )
        log_action(
            "slash_command",
            "file_job_test",
            "started",
            interaction,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size=file.size,
        )

        input_path = await panam_files.save_attachment_to_job(file, job)
        data = input_path.read_bytes()
        extracted_text = extract_text_from_attachment(input_path.name, data)
        if not extracted_text.strip():
            extracted_text = "Z dokumentu se nepodarilo vytahnout zadny text."
        else:
            extracted_text = trim_document_text(extracted_text)
        panam_files.write_work_text(job, "extracted_text.txt", extracted_text)

        output_content = (
            "# Panam file job test\n\n"
            f"- job_id: `{job.job_id}`\n"
            f"- input: `{input_path.name}`\n\n"
            "## Extracted text\n\n"
            f"{extracted_text}\n"
        )
        if output_extension == ".txt":
            output_content = (
                "Panam file job test\n\n"
                f"job_id: {job.job_id}\n"
                f"input: {input_path.name}\n\n"
                "Extracted text\n\n"
                f"{extracted_text}\n"
            )

        output_path = panam_files.write_output_text(
            job,
            f"file_job_test_output{output_extension}",
            output_content,
        )
        job.status = "success"
        job.finished_at = datetime.now(timezone.utc).isoformat()
        panam_files.write_job_metadata(job)

        await interaction.followup.send(
            build_file_job_success_message(
                normalized_output_format,
                "file_job_test",
            ),
            file=discord.File(output_path),
        )
        log_action(
            "slash_command",
            "file_job_test",
            "success",
            interaction,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size=file.size,
        )

    except Exception:
        mark_command_status(interaction, "error")
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                "slash_command",
                "file_job_test",
                "error",
                interaction,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size=file.size,
            )
        logger.exception("Chyba pri zpracovani /file_job_test")
        await interaction.followup.send(
            "Neco se pokazilo pri testu file-job pipeline."
        )

    finally:
        if job is not None:
            panam_files.cleanup_job(job)
