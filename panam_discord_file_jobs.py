import logging
from pathlib import Path

import discord

import panam_docx_transform
import panam_files
import panam_spreadsheet
from panam_discord_context import (
    get_context_int,
    get_safe_context,
    log_action,
    mark_source_error,
)
from panam_discord_responses import send_source_message
from panam_file_context import set_last_file_context
from panam_file_service import (
    PanamFileRequest,
    PanamFileResult,
    PanamFileServiceUserError,
    cleanup_file_result,
    get_items_for_xlsx,
    process_panam_file_request,
)
from panam_text_extraction import (
    TextExtractionUserError as AttachmentAnalysisUserError,
    get_file_extension,
)


logger = logging.getLogger("panam")

USER_VISIBLE_FILE_ERRORS = (
    PanamFileServiceUserError,
    AttachmentAnalysisUserError,
    panam_spreadsheet.SpreadsheetTransformUserError,
    panam_docx_transform.DocxTransformUserError,
)


def _normalized_output_format(output_format: str | None, default: str) -> str:
    return (output_format or default).lower().strip(".")


def _build_request(
    source,
    file: discord.Attachment,
    input_path: Path,
    instruction: str,
    mode: str,
    output_format: str | None,
) -> PanamFileRequest:
    context = get_safe_context(source)
    return PanamFileRequest(
        input_path=input_path,
        original_filename=Path(file.filename).name,
        instruction=instruction,
        mode=mode,
        output_format=output_format,
        user_id=context.get("user_id"),
        channel_id=context.get("channel_id"),
        source="discord",
    )


def _create_job(source, action_name: str) -> panam_files.FileJob:
    context = get_safe_context(source)
    return panam_files.create_file_job(
        user_id=get_context_int(context, "user_id"),
        channel_id=get_context_int(context, "channel_id"),
        action=action_name,
    )


def _log_file_job_status(
    status: str,
    source,
    file: discord.Attachment,
    job: panam_files.FileJob | None,
    action_type: str,
    action_name: str,
    output_format: str | None,
    **details,
) -> None:
    log_action(
        action_type,
        action_name,
        status,
        source,
        job_id=job.job_id if job is not None else None,
        filename=Path(file.filename).name,
        extension=get_file_extension(file.filename),
        size_bytes=file.size,
        output_format=output_format,
        **details,
    )


def _log_saved_attachment(
    source,
    input_path: Path,
    file: discord.Attachment,
    job: panam_files.FileJob,
    action_type: str,
    action_name: str,
    output_format: str | None,
) -> None:
    log_action(
        action_type,
        action_name,
        "attachment_saved",
        source,
        job_id=job.job_id,
        filename=input_path.name,
        extension=input_path.suffix.lower(),
        size_bytes=input_path.stat().st_size,
        output_format=output_format,
    )


def _log_result(
    source,
    file: discord.Attachment,
    job: panam_files.FileJob,
    action_type: str,
    action_name: str,
    result: PanamFileResult,
) -> None:
    if result.output_path is not None:
        log_action(
            action_type,
            action_name,
            "output_written",
            source,
            job_id=job.job_id,
            filename=result.output_path.name,
            extension=result.output_path.suffix.lower(),
            size_bytes=result.output_path.stat().st_size,
            output_format=result.output_format,
            operation_summary=result.operation_summary,
            row_count_before=result.row_count_before,
            row_count_after=result.row_count_after,
            column_count_before=result.column_count_before,
            column_count_after=result.column_count_after,
            paragraph_count_before=result.paragraph_count_before,
            paragraph_count_after=result.paragraph_count_after,
        )

    _log_file_job_status(
        "success",
        source,
        file,
        job,
        action_type,
        action_name,
        result.output_format,
        operation_summary=result.operation_summary,
        row_count_before=result.row_count_before,
        row_count_after=result.row_count_after,
        column_count_before=result.column_count_before,
        column_count_after=result.column_count_after,
        paragraph_count_before=result.paragraph_count_before,
        paragraph_count_after=result.paragraph_count_after,
    )


def _remember_file_context(
    source,
    file: discord.Attachment,
    mode: str,
    output_filename: str | None,
    file_summary: str | None = None,
) -> None:
    context = get_safe_context(source)
    set_last_file_context(
        context.get("channel_id"),
        Path(file.filename).name,
        get_file_extension(file.filename),
        mode,
        output_filename,
        file_summary=file_summary,
    )


async def _run_discord_file_service_job(
    model: str,
    source,
    file: discord.Attachment,
    instruction: str,
    mode: str,
    output_format: str | None,
    action_type: str,
    action_name: str,
    generic_error_message: str,
) -> None:
    job = None
    result = None

    try:
        job = _create_job(source, action_name)
        _log_file_job_status(
            "started",
            source,
            file,
            job,
            action_type,
            action_name,
            output_format,
        )

        input_path = await panam_files.save_attachment_to_job(file, job)
        _log_saved_attachment(
            source,
            input_path,
            file,
            job,
            action_type,
            action_name,
            output_format,
        )

        request = _build_request(
            source,
            file,
            input_path,
            instruction,
            mode,
            output_format,
        )
        result = await process_panam_file_request(model, request, job=job)

        await send_source_message(
            source,
            result.message,
            result.output_path if result.kind == "file" else None,
        )
        _remember_file_context(
            source,
            file,
            result.mode,
            result.output_filename,
            file_summary=result.message if result.kind == "chat_answer" else None,
        )
        _log_result(source, file, job, action_type, action_name, result)

    except USER_VISIBLE_FILE_ERRORS as error:
        mark_source_error(source)
        _log_file_job_status(
            "error",
            source,
            file,
            job,
            action_type,
            action_name,
            output_format,
        )
        await send_source_message(source, str(error))

    except Exception:
        mark_source_error(source)
        _log_file_job_status(
            "error",
            source,
            file,
            job,
            action_type,
            action_name,
            output_format,
        )
        logger.exception("Chyba pri file-job pipeline")
        await send_source_message(source, generic_error_message)

    finally:
        if result is not None:
            cleanup_file_result(result)
        elif job is not None:
            panam_files.cleanup_job(job)


async def run_human_document_file_job(
    model: str,
    source,
    file: discord.Attachment,
    instruction: str,
    output_format: str,
    action_type: str,
    action_name: str = "process_file",
) -> None:
    await _run_discord_file_service_job(
        model,
        source,
        file,
        instruction,
        "human_document",
        _normalized_output_format(output_format, "md"),
        action_type,
        action_name,
        "Neco se pokazilo pri zpracovani souboru.",
    )


async def run_structured_data_file_job(
    model: str,
    source,
    file: discord.Attachment,
    instruction: str,
    output_format: str,
    action_type: str,
    action_name: str = "extract_data",
) -> None:
    await _run_discord_file_service_job(
        model,
        source,
        file,
        instruction,
        "structured_data",
        _normalized_output_format(output_format, "json"),
        action_type,
        action_name,
        "Neco se pokazilo pri tezeni dat ze souboru.",
    )


async def run_spreadsheet_transform_file_job(
    source,
    file: discord.Attachment,
    instruction: str,
    action_type: str,
    action_name: str = "transform_excel",
) -> None:
    await _run_discord_file_service_job(
        "",
        source,
        file,
        instruction,
        "spreadsheet_transform",
        "xlsx",
        action_type,
        action_name,
        "Neco se pokazilo pri uprave Excelu.",
    )


async def run_docx_transform_file_job(
    model: str,
    source,
    file: discord.Attachment,
    instruction: str,
    action_type: str,
    action_name: str = "transform_docx",
) -> None:
    await _run_discord_file_service_job(
        model,
        source,
        file,
        instruction,
        "docx_transform",
        "docx",
        action_type,
        action_name,
        "Neco se pokazilo pri uprave DOCX.",
    )
