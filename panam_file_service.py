import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import panam_docx
import panam_docx_transform
import panam_excel
import panam_files
import panam_spreadsheet
from panam_ai import (
    analyze_document_text,
    extract_structured_data,
    process_document_text,
    transform_docx_text,
)
from panam_file_responses import build_file_job_success_message
from panam_text_extraction import (
    TextExtractionUserError,
    extract_text_from_attachment,
    get_file_extension,
    trim_document_text,
)


class PanamFileServiceUserError(Exception):
    pass


@dataclass(frozen=True)
class PanamFileRequest:
    input_path: Path
    original_filename: str
    instruction: str
    mode: str
    output_format: str | None
    user_id: int | str | None
    channel_id: int | str | None
    source: str


@dataclass
class PanamFileResult:
    kind: str
    message: str
    output_path: Path | None
    output_filename: str | None
    mode: str
    output_format: str | None
    operation_summary: str | None = None
    row_count_before: int | None = None
    row_count_after: int | None = None
    column_count_before: int | None = None
    column_count_after: int | None = None
    paragraph_count_before: int | None = None
    paragraph_count_after: int | None = None
    _job: panam_files.FileJob | None = field(default=None, repr=False, compare=False)


def get_items_for_xlsx(data: Any) -> list[dict]:
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


def normalize_file_mode(mode: str) -> str:
    normalized = str(mode or "").lower().strip()
    if normalized == "transform_excel":
        return "spreadsheet_transform"
    return normalized


def action_name_for_mode(mode: str) -> str:
    normalized = normalize_file_mode(mode)
    if normalized == "human_document":
        return "process_file"
    if normalized == "structured_data":
        return "extract_data"
    if normalized == "docx_transform":
        return "transform_docx"
    if normalized == "spreadsheet_transform":
        return "transform_excel"
    if normalized == "chat_answer":
        return "chat_answer"
    return normalized or "process_file"


def cleanup_file_result(result: PanamFileResult) -> None:
    if result._job is not None:
        panam_files.cleanup_job(result._job)
        result._job = None


def _safe_int(value: int | str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _normalize_output_format(value: str | None, default: str | None) -> str | None:
    if value is None:
        return default

    normalized = str(value).lower().strip().strip(".")
    return normalized or default


def _create_job_for_request(request: PanamFileRequest) -> panam_files.FileJob:
    return panam_files.create_file_job(
        user_id=_safe_int(request.user_id),
        channel_id=_safe_int(request.channel_id),
        action=action_name_for_mode(request.mode),
    )


def _is_inside(path: Path, directory: Path) -> bool:
    resolved_path = path.resolve()
    resolved_directory = directory.resolve()
    return resolved_path == resolved_directory or resolved_directory in resolved_path.parents


def _prepare_input_file(
    request: PanamFileRequest,
    job: panam_files.FileJob,
) -> Path:
    source_path = Path(request.input_path)
    if not source_path.exists():
        raise PanamFileServiceUserError("Vstupni soubor neexistuje.")

    if _is_inside(source_path, job.input_dir):
        input_path = panam_files.ensure_within_job(source_path, job)
    else:
        filename = panam_files.safe_filename(
            request.original_filename or source_path.name
        )
        input_path = panam_files.ensure_within_job(job.input_dir / filename, job)
        if source_path.resolve() != input_path.resolve():
            shutil.copy2(source_path, input_path)

    if not any(item.get("filename") == input_path.name for item in job.input_files):
        job.input_files.append(
            {
                "filename": input_path.name,
                "extension": input_path.suffix.lower(),
                "size_bytes": input_path.stat().st_size,
            }
        )
        panam_files.write_job_metadata(job)

    return input_path


def _mark_output_file(job: panam_files.FileJob, output_path: Path) -> None:
    job.output_files.append(
        {
            "filename": output_path.name,
            "extension": output_path.suffix.lower(),
            "size_bytes": output_path.stat().st_size,
        }
    )
    panam_files.write_job_metadata(job)


def _mark_job_finished(job: panam_files.FileJob, status: str) -> None:
    job.status = status
    job.finished_at = datetime.now(timezone.utc).isoformat()
    panam_files.write_job_metadata(job)


def _extract_document_text(input_path: Path) -> str:
    data = input_path.read_bytes()
    extracted_text = extract_text_from_attachment(input_path.name, data)
    if extracted_text.strip():
        return trim_document_text(extracted_text)

    extension = get_file_extension(input_path.name)
    if extension == ".xlsx":
        raise PanamFileServiceUserError(
            "Z toho Excelu se mi nepodarilo vytahnout zadna data."
        )
    if extension == ".pdf":
        raise PanamFileServiceUserError(
            "Z toho PDF se mi nepodarilo vytahnout zadny text. Mozna je to sken nebo obrazkove PDF."
        )
    if extension == ".docx":
        raise PanamFileServiceUserError(
            "Z toho DOCX se mi nepodarilo vytahnout zadny text."
        )

    raise PanamFileServiceUserError(
        "Z toho dokumentu se mi nepodarilo vytahnout zadny text."
    )


async def _process_chat_answer(
    model: str,
    request: PanamFileRequest,
    job: panam_files.FileJob,
    input_path: Path,
) -> PanamFileResult:
    extracted_text = _extract_document_text(input_path)
    panam_files.write_work_text(job, "extracted_text.txt", extracted_text)
    answer = await analyze_document_text(
        model,
        extracted_text,
        request.instruction,
        input_path.name,
    )
    return PanamFileResult(
        kind="chat_answer",
        message=answer,
        output_path=None,
        output_filename=None,
        mode="chat_answer",
        output_format=None,
    )


async def _process_human_document(
    model: str,
    request: PanamFileRequest,
    job: panam_files.FileJob,
    input_path: Path,
) -> PanamFileResult:
    output_format = _normalize_output_format(request.output_format, "md") or "md"
    output_extension = f".{output_format}"
    extracted_text = _extract_document_text(input_path)
    panam_files.write_work_text(job, "extracted_text.txt", extracted_text)

    process_output_format = "md" if output_format == "docx" else output_format
    processed_text = await process_document_text(
        model,
        extracted_text,
        request.instruction,
        input_path.name,
        process_output_format,
    )
    output_filename = panam_files.build_panam_output_filename(
        input_path.name,
        output_extension,
    )

    if output_format == "docx":
        output_path = panam_files.ensure_within_job(job.output_dir / output_filename, job)
        panam_docx.create_docx_from_text(processed_text, output_path)
        _mark_output_file(job, output_path)
    else:
        output_path = panam_files.write_output_text(
            job,
            output_filename,
            processed_text,
        )

    return PanamFileResult(
        kind="file",
        message=build_file_job_success_message(output_format, action_name_for_mode(request.mode)),
        output_path=output_path,
        output_filename=output_path.name,
        mode="human_document",
        output_format=output_format,
    )


async def _process_structured_data(
    model: str,
    request: PanamFileRequest,
    job: panam_files.FileJob,
    input_path: Path,
) -> PanamFileResult:
    output_format = _normalize_output_format(request.output_format, "json") or "json"
    output_extension = f".{output_format}"
    extracted_text = _extract_document_text(input_path)
    panam_files.write_work_text(job, "extracted_text.txt", extracted_text)

    structured_text = await extract_structured_data(
        model,
        extracted_text,
        request.instruction,
        input_path.name,
        "json" if output_format == "xlsx" else output_format,
    )

    json_data = None
    if output_format in {"json", "xlsx"}:
        try:
            json_data = json.loads(structured_text)
        except json.JSONDecodeError as error:
            if output_format == "xlsx":
                raise PanamFileServiceUserError(
                    "Data pro XLSX se nepodarilo validovat. Zkus presnejsi instrukci nebo output_format=json."
                ) from error

            raise PanamFileServiceUserError(
                "Vystup JSON se nepodarilo validovat. Zkus presnejsi instrukci nebo output_format=md."
            ) from error
    elif output_format == "csv":
        if not structured_text.strip() or not structured_text.strip().splitlines():
            raise PanamFileServiceUserError("CSV vystup je prazdny.")
    elif not structured_text.strip():
        raise PanamFileServiceUserError("Markdown vystup je prazdny.")

    output_filename = panam_files.build_panam_output_filename(
        input_path.name,
        output_extension,
    )

    if output_format == "xlsx":
        output_path = panam_files.ensure_within_job(job.output_dir / output_filename, job)
        panam_excel.create_xlsx_from_items(
            get_items_for_xlsx(json_data),
            output_path,
        )
        _mark_output_file(job, output_path)
    else:
        output_path = panam_files.write_output_text(
            job,
            output_filename,
            structured_text,
        )

    return PanamFileResult(
        kind="file",
        message=build_file_job_success_message(output_format, action_name_for_mode(request.mode)),
        output_path=output_path,
        output_filename=output_path.name,
        mode="structured_data",
        output_format=output_format,
    )


async def _process_docx_transform(
    model: str,
    request: PanamFileRequest,
    job: panam_files.FileJob,
    input_path: Path,
) -> PanamFileResult:
    operation = panam_docx_transform.detect_docx_transform_operation(
        request.instruction
    )
    operation_name = str(operation.get("operation") or "unknown")
    operation_summary = str(
        operation.get("operation_summary")
        or "provedena podporovana DOCX transformace"
    )

    if panam_docx_transform.contains_sensitive_docx_transform_signal(
        request.instruction
    ):
        raise panam_docx_transform.DocxTransformUserError(
            "Tenhle DOCX transform nepouzivam pro hesla, tokeny, HR data, zakaznicka data ani citliva data."
        )

    extracted_text = _extract_document_text(input_path)
    if panam_docx_transform.contains_sensitive_docx_transform_signal(extracted_text):
        raise panam_docx_transform.DocxTransformUserError(
            "Tenhle DOCX transform nepouzivam pro hesla, tokeny, HR data, zakaznicka data ani citliva data."
        )

    panam_files.write_work_text(job, "extracted_text.txt", extracted_text)
    transformed_text = await transform_docx_text(
        model,
        extracted_text,
        operation_name,
        request.instruction,
        input_path.name,
    )
    output_filename = panam_files.build_panam_output_filename(input_path.name, ".docx")
    output_path = panam_files.ensure_within_job(job.output_dir / output_filename, job)
    result = panam_docx_transform.create_docx_transform_from_text(
        extracted_text,
        transformed_text,
        output_path,
        operation_summary,
    )
    _mark_output_file(job, output_path)

    return PanamFileResult(
        kind="file",
        message=build_file_job_success_message(
            "docx",
            action_name_for_mode(request.mode),
            result.operation_summary,
            paragraph_count_before=result.paragraph_count_before,
            paragraph_count_after=result.paragraph_count_after,
        ),
        output_path=result.output_path,
        output_filename=result.output_path.name,
        mode="docx_transform",
        output_format="docx",
        operation_summary=result.operation_summary,
        paragraph_count_before=result.paragraph_count_before,
        paragraph_count_after=result.paragraph_count_after,
    )


async def _process_spreadsheet_transform(
    request: PanamFileRequest,
    job: panam_files.FileJob,
    input_path: Path,
) -> PanamFileResult:
    output_filename = panam_files.build_panam_output_filename(input_path.name, ".xlsx")
    output_path = panam_files.ensure_within_job(job.output_dir / output_filename, job)
    result = panam_spreadsheet.transform_xlsx(
        input_path,
        output_path,
        request.instruction,
    )
    _mark_output_file(job, output_path)

    return PanamFileResult(
        kind="file",
        message=build_file_job_success_message(
            "xlsx",
            action_name_for_mode(request.mode),
            result.operation_summary,
            row_count_before=result.row_count_before,
            row_count_after=result.row_count_after,
            column_count_before=result.column_count_before,
            column_count_after=result.column_count_after,
        ),
        output_path=result.output_path,
        output_filename=result.output_path.name,
        mode="spreadsheet_transform",
        output_format="xlsx",
        operation_summary=result.operation_summary,
        row_count_before=result.row_count_before,
        row_count_after=result.row_count_after,
        column_count_before=result.column_count_before,
        column_count_after=result.column_count_after,
    )


async def process_panam_file_request(
    model: str,
    request: PanamFileRequest,
    job: panam_files.FileJob | None = None,
) -> PanamFileResult:
    owned_job = job is None
    active_job = job or _create_job_for_request(request)

    try:
        input_path = _prepare_input_file(request, active_job)
        mode = normalize_file_mode(request.mode)

        if mode == "chat_answer":
            result = await _process_chat_answer(model, request, active_job, input_path)
        elif mode == "human_document":
            result = await _process_human_document(model, request, active_job, input_path)
        elif mode == "structured_data":
            result = await _process_structured_data(model, request, active_job, input_path)
        elif mode == "docx_transform":
            result = await _process_docx_transform(model, request, active_job, input_path)
        elif mode == "spreadsheet_transform":
            result = await _process_spreadsheet_transform(request, active_job, input_path)
        else:
            raise PanamFileServiceUserError(f"Nepodporovany file mode: {request.mode}")

        _mark_job_finished(active_job, "success")
        result._job = active_job
        return result

    except (PanamFileServiceUserError, TextExtractionUserError):
        _mark_job_finished(active_job, "error")
        if owned_job:
            panam_files.cleanup_job(active_job)
        raise
    except Exception:
        _mark_job_finished(active_job, "error")
        if owned_job:
            panam_files.cleanup_job(active_job)
        raise
