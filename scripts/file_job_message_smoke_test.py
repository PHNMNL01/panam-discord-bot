from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from panam_file_responses import build_file_job_success_message


def assert_common_parts(message: str, output_type: str) -> None:
    assert output_type in message, message
    assert "Provedená změna" in message, message
    assert "Původní příloha zůstala beze změn" in message, message


def main() -> None:
    source = (PROJECT_ROOT / "panam_file_responses.py").read_text(encoding="utf-8")
    assert "import discord" not in source
    assert "from discord" not in source

    spreadsheet_message = build_file_job_success_message(
        "xlsx",
        "spreadsheet_transform",
        "filtr řádků, kde Oddělení = IT",
        row_count_before=11,
        row_count_after=2,
    )
    assert_common_parts(spreadsheet_message, "Excel")
    assert "Řádky: 11 → 2" in spreadsheet_message, spreadsheet_message

    markdown_message = build_file_job_success_message(
        "md",
        "process_file",
        "shrnutí dokumentu do přehledu",
    )
    assert_common_parts(markdown_message, "Markdown")
    assert "shrnutí dokumentu do přehledu" in markdown_message, markdown_message

    fallback_message = build_file_job_success_message("xlsx", "extract_data")
    assert_common_parts(fallback_message, "XLSX")
    assert "extrakce strukturovaných dat" in fallback_message, fallback_message

    docx_message = build_file_job_success_message(
        "docx",
        "docx_transform",
        "oprava překlepů a stylistiky",
        paragraph_count_before=4,
        paragraph_count_after=3,
    )
    assert_common_parts(docx_message, "DOCX")
    assert "Odstavce: 4 → 3" in docx_message, docx_message

    print("file job message smoke test ok")


if __name__ == "__main__":
    main()
