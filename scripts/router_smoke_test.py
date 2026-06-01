from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from panam_router import decide_file_response_mode, has_explicit_file_output_request


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


TEST_CASES = [
    ("Panam dej mi to do Wordu", "human_document", "docx", None),
    ("Panam udelej z toho DOCX", "human_document", "docx", None),
    ("Panam priprav z toho Word dokument", "human_document", "docx", None),
    ("Panam dej mi to do Excelu", "structured_data", "xlsx", None),
    ("Panam dej mi to excelu pls", "structured_data", "xlsx", None),
    ("Panam dej mi to do excelu pls", "structured_data", "xlsx", None),
    ("Panam udelej mi z toho excel pls", "structured_data", "xlsx", None),
    ("Panam udelej z toho excel", "structured_data", "xlsx", None),
    ("Panam udelej mi z toho excel", "structured_data", "xlsx", None),
    ("Panam preved to do excelu", "structured_data", "xlsx", None),
    ("Panam dej mi z toho xlsx", "structured_data", "xlsx", None),
    ("Panam dej mi to do xlsx", "structured_data", "xlsx", None),
    ("Panam udelej z toho tabulku", "structured_data", "xlsx", None),
    ("Panam udelej markdown tabulku", "structured_data", "md", None),
    ("Panam udelej z toho soubor csv", "structured_data", "csv", None),
    ("Panam vrat JSON", "structured_data", "json", None),
    ("Panam dej mi to do souboru", "human_document", "md", None),
    ("Panam prepis to do cisteho textu", "human_document", "txt", None),
    (
        "Panam udelej z toho hezci tabulku a neco tam dopln podle sebe",
        "unsupported_creative_spreadsheet_edit",
        None,
        None,
    ),
    (
        "Panam udelej z toho hezci tabulku",
        "unsupported_creative_spreadsheet_edit",
        None,
        None,
    ),
    (
        "Panam neco tam dopln podle sebe",
        "unsupported_creative_spreadsheet_edit",
        None,
        ".xlsx",
    ),
    (
        "Panam oprav v tom Wordu preklepy a stylistiku",
        "docx_transform",
        "docx",
        ".docx",
    ),
    (
        "Panam oprav ten DOCX",
        "docx_transform",
        "docx",
        ".docx",
    ),
    (
        "Panam zestrucni ten Word dokument",
        "docx_transform",
        "docx",
        ".docx",
    ),
    (
        "Panam preved ten dokument do formalniho tonu",
        "docx_transform",
        "docx",
        ".docx",
    ),
    (
        "Panam udelej z toho DOCX checklist",
        "docx_transform",
        "docx",
        ".docx",
    ),
    (
        "Panam udelej z toho strukturovany dokument s nadpisy",
        "docx_transform",
        "docx",
        ".docx",
    ),
    (
        "Panam vytvor cistou verzi toho Wordu",
        "docx_transform",
        "docx",
        ".docx",
    ),
    (
        "Panam ten dokument uces a vrat jako DOCX",
        "docx_transform",
        "docx",
        ".docx",
    ),
    (
        "Panam neco tam dopln podle sebe",
        "unsupported_creative_docx_edit",
        None,
        ".docx",
    ),
    ("Panam uprav ten Word", "unsupported_direct_edit", None, ".docx"),
    ("Panam uprav ten Excel", "unsupported_direct_edit", None, ".xlsx"),
    (
        "Panam uprav ten Excel a nech jen radky kde Oddeleni = IT",
        "spreadsheet_transform",
        "xlsx",
        ".xlsx",
    ),
    (
        "Panam odstran prazdne radky z te tabulky",
        "spreadsheet_transform",
        "xlsx",
        ".xlsx",
    ),
    (
        "Panam vyber sloupce Jmeno a Prijmeni, Soukromy email",
        "spreadsheet_transform",
        "xlsx",
        ".xlsx",
    ),
    (
        "Panam serad ten Excel podle Datum nastupu",
        "spreadsheet_transform",
        "xlsx",
        ".xlsx",
    ),
    (
        "Panam najdi duplicity podle Soukromy email",
        "spreadsheet_transform",
        "xlsx",
        ".xlsx",
    ),
]

EXPLICIT_FILE_OUTPUT_CASES = [
    "Panam dej mi to excelu pls",
    "Panam dej mi to do excelu pls",
    "Panam udelej z toho excel",
    "Panam udelej mi z toho excel",
    "Panam preved to do excelu",
    "Panam dej mi z toho xlsx",
    "Panam dej mi to do xlsx",
    "Panam udelej z toho tabulku",
]


def strip_panam_prefix(text: str) -> str:
    return re.sub(r"^(?:hey\s+)?panam\b[\s,.:;!-]*", "", text, flags=re.IGNORECASE)


def format_result(mode: str | None, output_format: str | None) -> str:
    return f"{mode}/{output_format}"


def main() -> int:
    failures = 0

    for input_text, expected_mode, expected_output_format, extension in TEST_CASES:
        request_text = strip_panam_prefix(input_text)
        result = decide_file_response_mode(
            request_text,
            extension=extension,
            has_xlsx_context=extension == ".xlsx",
            has_docx_context=extension == ".docx",
        )
        actual_mode = result.get("mode")
        actual_output_format = result.get("output_format")

        if (
            actual_mode == expected_mode
            and actual_output_format == expected_output_format
        ):
            print(
                f"PASS: {input_text} -> "
                f"{format_result(actual_mode, actual_output_format)}"
            )
            continue

        failures += 1
        print(f"FAIL: {input_text}")
        print(f"  expected: {format_result(expected_mode, expected_output_format)}")
        print(f"  actual: {format_result(actual_mode, actual_output_format)}")
        print(f"  raw: {result}")

    for input_text in EXPLICIT_FILE_OUTPUT_CASES:
        request_text = strip_panam_prefix(input_text)
        if has_explicit_file_output_request(request_text):
            print(f"PASS: {input_text} -> explicit_file_output_request")
            continue

        failures += 1
        print(f"FAIL: {input_text}")
        print("  expected: explicit_file_output_request")
        print("  actual: no explicit file output request")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
