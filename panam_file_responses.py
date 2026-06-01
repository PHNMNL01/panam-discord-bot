FILE_JOB_OPERATION_FALLBACKS = {
    "process_file": "zpracování dokumentu podle instrukce",
    "extract_data": "extrakce strukturovaných dat",
    "transform_excel": "podporovaná Excel transformace",
    "spreadsheet_transform": "podporovaná Excel transformace",
    "transform_docx": "podporovaná DOCX transformace",
    "docx_transform": "podporovaná DOCX transformace",
    "file_job_test": "technický test file pipeline",
}

OUTPUT_FORMAT_LABELS = {
    "csv": "CSV",
    "docx": "DOCX",
    "excel": "Excel",
    "json": "JSON",
    "markdown": "Markdown",
    "md": "Markdown",
    "txt": "TXT",
    "xlsx": "XLSX",
}


def build_file_job_success_message(
    output_format: str,
    action_name: str,
    operation_summary: str | None = None,
    row_count_before: int | None = None,
    row_count_after: int | None = None,
    column_count_before: int | None = None,
    column_count_after: int | None = None,
    paragraph_count_before: int | None = None,
    paragraph_count_after: int | None = None,
) -> str:
    normalized_format = (output_format or "").lower().strip().strip(".")
    normalized_action = (action_name or "").lower().strip()
    output_label = OUTPUT_FORMAT_LABELS.get(
        normalized_format,
        normalized_format.upper() if normalized_format else "Soubor",
    )
    summary = (operation_summary or "").strip() or FILE_JOB_OPERATION_FALLBACKS.get(
        normalized_action,
        "vytvoření výstupního souboru",
    )

    if normalized_action in {"transform_excel", "spreadsheet_transform"}:
        intro = "Excel jsem upravila jako nový soubor."
    elif normalized_action in {"transform_docx", "docx_transform"}:
        intro = "DOCX jsem upravila jako nový soubor."
    else:
        intro = f"{output_label} jsem vytvořila jako nový soubor."

    message_parts = [
        intro,
        f"Provedená změna: {summary}.",
    ]
    if row_count_before is not None and row_count_after is not None:
        message_parts.append(f"Řádky: {row_count_before} → {row_count_after}.")
    if (
        column_count_before is not None
        and column_count_after is not None
        and column_count_before != column_count_after
    ):
        message_parts.append(f"Sloupce: {column_count_before} → {column_count_after}.")
    if paragraph_count_before is not None and paragraph_count_after is not None:
        message_parts.append(
            f"Odstavce: {paragraph_count_before} → {paragraph_count_after}."
        )
    message_parts.append("Původní příloha zůstala beze změn.")
    return " ".join(message_parts)
