import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import discord

import panam_docx
import panam_docx_transform
import panam_excel
import panam_files
import panam_spreadsheet
from panam_ai import (
    extract_structured_data,
    process_document_text,
    transform_docx_text,
)
from panam_discord_context import (
    get_context_int,
    get_safe_context,
    log_action,
    mark_source_error,
)
from panam_discord_responses import send_source_message
from panam_file_context import set_last_file_context
from panam_file_responses import build_file_job_success_message
from panam_text_extraction import (
    TextExtractionUserError as AttachmentAnalysisUserError,
    extract_text_from_attachment,
    get_file_extension,
    trim_document_text,
)


logger = logging.getLogger("panam")


def get_items_for_xlsx(data) -> list[dict]:
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        raw_items = data["items"]
    elif isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        raw_items = [data]
    else:
        raw_items = [{"value": data}]

    items = []
    for item in raw_items:
        if isinstance(item, dict):
            items.append(item)
        else:
            items.append({"value": item})

    return items


async def run_human_document_file_job(
    model: str,
    source,
    file: discord.Attachment,
    instruction: str,
    output_format: str,
    action_type: str,
    action_name: str = "process_file",
) -> None:
    extension = get_file_extension(file.filename)
    normalized_output_format = output_format.lower().strip(".")
    output_extension = f".{normalized_output_format}"
    job = None

    try:
        context = get_safe_context(source)
        job = panam_files.create_file_job(
            user_id=get_context_int(context, "user_id"),
            channel_id=get_context_int(context, "channel_id"),
            action=action_name,
        )
        log_action(
            action_type,
            action_name,
            "started",
            source,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size_bytes=file.size,
            output_format=normalized_output_format,
        )

        input_path = await panam_files.save_attachment_to_job(file, job)
        log_action(
            action_type,
            action_name,
            "attachment_saved",
            source,
            job_id=job.job_id,
            filename=input_path.name,
            extension=input_path.suffix.lower(),
            size_bytes=input_path.stat().st_size,
            output_format=normalized_output_format,
        )

        data = input_path.read_bytes()
        extracted_text = extract_text_from_attachment(input_path.name, data)
        if not extracted_text.strip():
            mark_source_error(source)
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=input_path.name,
                extension=input_path.suffix.lower(),
                size_bytes=input_path.stat().st_size,
                output_format=normalized_output_format,
            )
            message_text = (
                "Z toho Excelu se mi nepodarilo vytahnout zadna data."
                if extension == ".xlsx"
                else "Z toho dokumentu se mi nepodarilo vytahnout zadny text."
            )
            await send_source_message(source, message_text)
            return

        extracted_text = trim_document_text(extracted_text)
        work_path = panam_files.write_work_text(job, "extracted_text.txt", extracted_text)
        log_action(
            action_type,
            action_name,
            "text_extracted",
            source,
            job_id=job.job_id,
            filename=work_path.name,
            extension=work_path.suffix.lower(),
            size_bytes=work_path.stat().st_size,
            output_format=normalized_output_format,
        )

        process_output_format = (
            "md" if normalized_output_format == "docx" else normalized_output_format
        )
        processed_text = await process_document_text(
            model,
            extracted_text,
            instruction,
            input_path.name,
            process_output_format,
        )
        output_filename = panam_files.build_panam_output_filename(
            input_path.name,
            output_extension,
        )
        if normalized_output_format == "docx":
            output_path = panam_files.ensure_within_job(
                job.output_dir / output_filename,
                job,
            )
            panam_docx.create_docx_from_text(processed_text, output_path)
            job.output_files.append(
                {
                    "filename": output_path.name,
                    "extension": output_path.suffix.lower(),
                    "size_bytes": output_path.stat().st_size,
                }
            )
            panam_files.write_job_metadata(job)
        else:
            output_path = panam_files.write_output_text(
                job,
                output_filename,
                processed_text,
            )
        log_action(
            action_type,
            action_name,
            "output_written",
            source,
            job_id=job.job_id,
            filename=output_path.name,
            extension=output_path.suffix.lower(),
            size_bytes=output_path.stat().st_size,
            output_format=normalized_output_format,
        )

        job.status = "success"
        job.finished_at = datetime.now(timezone.utc).isoformat()
        panam_files.write_job_metadata(job)

        await send_source_message(
            source,
            build_file_job_success_message(
                normalized_output_format,
                action_name,
            ),
            output_path,
        )
        set_last_file_context(
            context.get("channel_id"),
            Path(file.filename).name,
            extension,
            "human_document",
            output_path.name,
        )
        log_action(
            action_type,
            action_name,
            "success",
            source,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size_bytes=file.size,
            output_format=normalized_output_format,
        )

    except AttachmentAnalysisUserError as error:
        mark_source_error(source)
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size_bytes=file.size,
                output_format=normalized_output_format,
            )
        logger.exception("Chyba pri zpracovani file-job pipeline")
        await send_source_message(source, str(error))

    except Exception:
        mark_source_error(source)
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size_bytes=file.size,
                output_format=normalized_output_format,
            )
        logger.exception("Chyba pri zpracovani file-job pipeline")
        await send_source_message(source, "Neco se pokazilo pri zpracovani souboru.")

    finally:
        if job is not None:
            panam_files.cleanup_job(job)


async def run_structured_data_file_job(
    model: str,
    source,
    file: discord.Attachment,
    instruction: str,
    output_format: str,
    action_type: str,
    action_name: str = "extract_data",
) -> None:
    extension = get_file_extension(file.filename)
    normalized_output_format = output_format.lower().strip(".")
    output_extension = f".{normalized_output_format}"
    job = None

    try:
        context = get_safe_context(source)
        job = panam_files.create_file_job(
            user_id=get_context_int(context, "user_id"),
            channel_id=get_context_int(context, "channel_id"),
            action=action_name,
        )
        log_action(
            action_type,
            action_name,
            "started",
            source,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size_bytes=file.size,
            output_format=normalized_output_format,
        )

        input_path = await panam_files.save_attachment_to_job(file, job)
        log_action(
            action_type,
            action_name,
            "attachment_saved",
            source,
            job_id=job.job_id,
            filename=input_path.name,
            extension=input_path.suffix.lower(),
            size_bytes=input_path.stat().st_size,
            output_format=normalized_output_format,
        )

        data = input_path.read_bytes()
        extracted_text = extract_text_from_attachment(input_path.name, data)
        if not extracted_text.strip():
            mark_source_error(source)
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=input_path.name,
                extension=input_path.suffix.lower(),
                size_bytes=input_path.stat().st_size,
                output_format=normalized_output_format,
            )
            message_text = (
                "Z toho Excelu se mi nepodarilo vytahnout zadna data."
                if extension == ".xlsx"
                else "Z toho dokumentu se mi nepodarilo vytahnout zadny text."
            )
            await send_source_message(source, message_text)
            return

        extracted_text = trim_document_text(extracted_text)
        work_path = panam_files.write_work_text(job, "extracted_text.txt", extracted_text)
        log_action(
            action_type,
            action_name,
            "text_extracted",
            source,
            job_id=job.job_id,
            filename=work_path.name,
            extension=work_path.suffix.lower(),
            size_bytes=work_path.stat().st_size,
            output_format=normalized_output_format,
        )

        structured_text = await extract_structured_data(
            model,
            extracted_text,
            instruction,
            input_path.name,
            "json" if normalized_output_format == "xlsx" else normalized_output_format,
        )
        log_action(
            action_type,
            action_name,
            "ai_extraction_completed",
            source,
            job_id=job.job_id,
            filename=input_path.name,
            extension=input_path.suffix.lower(),
            size_bytes=input_path.stat().st_size,
            output_format=normalized_output_format,
        )

        json_data = None
        if normalized_output_format in {"json", "xlsx"}:
            try:
                json_data = json.loads(structured_text)
            except json.JSONDecodeError:
                mark_source_error(source)
                job.status = "error"
                job.finished_at = datetime.now(timezone.utc).isoformat()
                panam_files.write_job_metadata(job)
                log_action(
                    action_type,
                    action_name,
                    "error",
                    source,
                    job_id=job.job_id,
                    filename=input_path.name,
                    extension=input_path.suffix.lower(),
                    size_bytes=input_path.stat().st_size,
                    output_format=normalized_output_format,
                )
                logger.warning(
                    "extract_data JSON validation failed job_id=%s status=error",
                    job.job_id,
                )
                message_text = (
                    "Data pro XLSX se nepodarilo validovat. Zkus presnejsi instrukci nebo output_format=json."
                    if normalized_output_format == "xlsx"
                    else "Vystup JSON se nepodarilo validovat. Zkus presnejsi instrukci nebo output_format=md."
                )
                await send_source_message(source, message_text)
                return
        elif normalized_output_format == "csv":
            if not structured_text.strip() or not structured_text.strip().splitlines():
                raise ValueError("CSV output is empty.")
        elif not structured_text.strip():
            raise ValueError("Markdown output is empty.")

        output_filename = panam_files.build_panam_output_filename(
            input_path.name,
            output_extension,
        )

        if normalized_output_format == "xlsx":
            output_path = panam_files.ensure_within_job(
                job.output_dir / output_filename,
                job,
            )
            panam_excel.create_xlsx_from_items(
                get_items_for_xlsx(json_data),
                output_path,
            )
            job.output_files.append(
                {
                    "filename": output_path.name,
                    "extension": output_path.suffix.lower(),
                    "size_bytes": output_path.stat().st_size,
                }
            )
            panam_files.write_job_metadata(job)
            output_status = "xlsx_output_written"
        else:
            output_path = panam_files.write_output_text(
                job,
                output_filename,
                structured_text,
            )
            output_status = "output_written"

        log_action(
            action_type,
            action_name,
            output_status,
            source,
            job_id=job.job_id,
            filename=output_path.name,
            extension=output_path.suffix.lower(),
            size_bytes=output_path.stat().st_size,
            output_format=normalized_output_format,
        )

        job.status = "success"
        job.finished_at = datetime.now(timezone.utc).isoformat()
        panam_files.write_job_metadata(job)

        await send_source_message(
            source,
            build_file_job_success_message(
                normalized_output_format,
                action_name,
            ),
            output_path,
        )
        set_last_file_context(
            context.get("channel_id"),
            Path(file.filename).name,
            extension,
            "structured_data",
            output_path.name,
        )
        log_action(
            action_type,
            action_name,
            "success",
            source,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size_bytes=file.size,
            output_format=normalized_output_format,
        )

    except AttachmentAnalysisUserError as error:
        mark_source_error(source)
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size_bytes=file.size,
                output_format=normalized_output_format,
            )
        logger.exception("Chyba pri tezeni dat ze souboru")
        await send_source_message(source, str(error))

    except Exception:
        mark_source_error(source)
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size_bytes=file.size,
                output_format=normalized_output_format,
            )
        logger.exception("Chyba pri tezeni dat ze souboru")
        await send_source_message(source, "Neco se pokazilo pri tezeni dat ze souboru.")

    finally:
        if job is not None:
            panam_files.cleanup_job(job)


async def run_spreadsheet_transform_file_job(
    source,
    file: discord.Attachment,
    instruction: str,
    action_type: str,
    action_name: str = "transform_excel",
) -> None:
    extension = get_file_extension(file.filename)
    job = None

    try:
        context = get_safe_context(source)
        job = panam_files.create_file_job(
            user_id=get_context_int(context, "user_id"),
            channel_id=get_context_int(context, "channel_id"),
            action="spreadsheet_transform",
        )
        log_action(
            action_type,
            action_name,
            "started",
            source,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size_bytes=file.size,
            output_format="xlsx",
        )

        input_path = await panam_files.save_attachment_to_job(file, job)
        log_action(
            action_type,
            action_name,
            "attachment_saved",
            source,
            job_id=job.job_id,
            filename=input_path.name,
            extension=input_path.suffix.lower(),
            size_bytes=input_path.stat().st_size,
            output_format="xlsx",
        )

        output_filename = panam_files.build_panam_output_filename(
            input_path.name,
            ".xlsx",
        )
        output_path = panam_files.ensure_within_job(
            job.output_dir / output_filename,
            job,
        )
        result = panam_spreadsheet.transform_xlsx(
            input_path,
            output_path,
            instruction,
        )
        job.output_files.append(
            {
                "filename": output_path.name,
                "extension": output_path.suffix.lower(),
                "size_bytes": output_path.stat().st_size,
            }
        )
        panam_files.write_job_metadata(job)
        log_action(
            action_type,
            action_name,
            "xlsx_output_written",
            source,
            job_id=job.job_id,
            filename=output_path.name,
            extension=output_path.suffix.lower(),
            size_bytes=output_path.stat().st_size,
            output_format="xlsx",
            rows_read=result.rows_read,
            rows_written=result.rows_written,
            columns_written=result.columns_written,
            operations=",".join(result.operations),
        )

        job.status = "success"
        job.finished_at = datetime.now(timezone.utc).isoformat()
        panam_files.write_job_metadata(job)

        await send_source_message(
            source,
            build_file_job_success_message(
                "xlsx",
                action_name,
                result.operation_summary,
                row_count_before=result.row_count_before,
                row_count_after=result.row_count_after,
                column_count_before=result.column_count_before,
                column_count_after=result.column_count_after,
            ),
            result.output_path,
        )
        set_last_file_context(
            context.get("channel_id"),
            Path(file.filename).name,
            extension,
            "spreadsheet_transform",
            output_path.name,
        )
        log_action(
            action_type,
            action_name,
            "success",
            source,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size_bytes=file.size,
            output_format="xlsx",
            rows_read=result.rows_read,
            rows_written=result.rows_written,
            columns_written=result.columns_written,
            operations=",".join(result.operations),
        )

    except panam_spreadsheet.SpreadsheetTransformUserError as error:
        mark_source_error(source)
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size_bytes=file.size,
                output_format="xlsx",
            )
        logger.warning(
            "spreadsheet_transform user_error job_id=%s",
            job.job_id if job else None,
        )
        await send_source_message(source, str(error))

    except Exception:
        mark_source_error(source)
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size_bytes=file.size,
                output_format="xlsx",
            )
        logger.exception("Chyba pri spreadsheet_transform file-job pipeline")
        await send_source_message(source, "Neco se pokazilo pri uprave Excelu.")

    finally:
        if job is not None:
            panam_files.cleanup_job(job)


async def run_docx_transform_file_job(
    model: str,
    source,
    file: discord.Attachment,
    instruction: str,
    action_type: str,
    action_name: str = "transform_docx",
) -> None:
    extension = get_file_extension(file.filename)
    job = None

    try:
        operation = panam_docx_transform.detect_docx_transform_operation(instruction)
        operation_name = str(operation.get("operation") or "unknown")
        operation_summary = str(
            operation.get("operation_summary") or "provedena podporovaná DOCX transformace"
        )

        if panam_docx_transform.contains_sensitive_docx_transform_signal(instruction):
            raise panam_docx_transform.DocxTransformUserError(
                "Tenhle DOCX transform nepouzivam pro hesla, tokeny, HR data, zakaznicka data ani citliva data."
            )

        context = get_safe_context(source)
        job = panam_files.create_file_job(
            user_id=get_context_int(context, "user_id"),
            channel_id=get_context_int(context, "channel_id"),
            action="docx_transform",
        )
        log_action(
            action_type,
            action_name,
            "started",
            source,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size_bytes=file.size,
            output_format="docx",
            operation=operation_name,
        )

        input_path = await panam_files.save_attachment_to_job(file, job)
        log_action(
            action_type,
            action_name,
            "attachment_saved",
            source,
            job_id=job.job_id,
            filename=input_path.name,
            extension=input_path.suffix.lower(),
            size_bytes=input_path.stat().st_size,
            output_format="docx",
            operation=operation_name,
        )

        data = input_path.read_bytes()
        extracted_text = extract_text_from_attachment(input_path.name, data)
        if not extracted_text.strip():
            mark_source_error(source)
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=input_path.name,
                extension=input_path.suffix.lower(),
                size_bytes=input_path.stat().st_size,
                output_format="docx",
                operation=operation_name,
            )
            await send_source_message(
                source,
                "Z toho DOCX se mi nepodarilo vytahnout zadny text.",
            )
            return

        if panam_docx_transform.contains_sensitive_docx_transform_signal(extracted_text):
            raise panam_docx_transform.DocxTransformUserError(
                "Tenhle DOCX transform nepouzivam pro hesla, tokeny, HR data, zakaznicka data ani citliva data."
            )

        extracted_text = trim_document_text(extracted_text)
        work_path = panam_files.write_work_text(job, "extracted_text.txt", extracted_text)
        log_action(
            action_type,
            action_name,
            "text_extracted",
            source,
            job_id=job.job_id,
            filename=work_path.name,
            extension=work_path.suffix.lower(),
            size_bytes=work_path.stat().st_size,
            output_format="docx",
            operation=operation_name,
        )

        transformed_text = await transform_docx_text(
            model,
            extracted_text,
            operation_name,
            instruction,
            input_path.name,
        )
        output_filename = panam_files.build_panam_output_filename(
            input_path.name,
            ".docx",
        )
        output_path = panam_files.ensure_within_job(
            job.output_dir / output_filename,
            job,
        )
        result = panam_docx_transform.create_docx_transform_from_text(
            extracted_text,
            transformed_text,
            output_path,
            operation_summary,
        )
        job.output_files.append(
            {
                "filename": output_path.name,
                "extension": output_path.suffix.lower(),
                "size_bytes": output_path.stat().st_size,
            }
        )
        panam_files.write_job_metadata(job)
        log_action(
            action_type,
            action_name,
            "docx_output_written",
            source,
            job_id=job.job_id,
            filename=output_path.name,
            extension=output_path.suffix.lower(),
            size_bytes=output_path.stat().st_size,
            output_format="docx",
            operation=operation_name,
            paragraph_count_before=result.paragraph_count_before,
            paragraph_count_after=result.paragraph_count_after,
        )

        job.status = "success"
        job.finished_at = datetime.now(timezone.utc).isoformat()
        panam_files.write_job_metadata(job)

        await send_source_message(
            source,
            build_file_job_success_message(
                "docx",
                action_name,
                result.operation_summary,
                paragraph_count_before=result.paragraph_count_before,
                paragraph_count_after=result.paragraph_count_after,
            ),
            result.output_path,
        )
        set_last_file_context(
            context.get("channel_id"),
            Path(file.filename).name,
            extension,
            "docx_transform",
            output_path.name,
        )
        log_action(
            action_type,
            action_name,
            "success",
            source,
            job_id=job.job_id,
            filename=Path(file.filename).name,
            extension=extension,
            size_bytes=file.size,
            output_format="docx",
            operation=operation_name,
            paragraph_count_before=result.paragraph_count_before,
            paragraph_count_after=result.paragraph_count_after,
        )

    except panam_docx_transform.DocxTransformUserError as error:
        mark_source_error(source)
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size_bytes=file.size,
                output_format="docx",
            )
        await send_source_message(source, str(error))

    except Exception:
        mark_source_error(source)
        if job is not None:
            job.status = "error"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            panam_files.write_job_metadata(job)
            log_action(
                action_type,
                action_name,
                "error",
                source,
                job_id=job.job_id,
                filename=Path(file.filename).name,
                extension=extension,
                size_bytes=file.size,
                output_format="docx",
            )
        logger.exception("Chyba pri docx_transform file-job pipeline")
        await send_source_message(source, "Neco se pokazilo pri uprave DOCX.")

    finally:
        if job is not None:
            panam_files.cleanup_job(job)
