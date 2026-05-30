from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from panam_router import decide_file_response_mode


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


TEST_CASES = [
    ("Panam dej mi to do Wordu", "human_document", "docx"),
    ("Panam udělej z toho DOCX", "human_document", "docx"),
    ("Panam připrav z toho Word dokument", "human_document", "docx"),
    ("Panam dej mi to do Excelu", "structured_data", "xlsx"),
    ("Panam udělej z toho tabulku", "structured_data", "xlsx"),
    ("Panam udělej z toho soubor csv", "structured_data", "csv"),
    ("Panam vrať JSON", "structured_data", "json"),
    ("Panam dej mi to do souboru", "human_document", "md"),
    ("Panam přepiš to do čistého textu", "human_document", "txt"),
    ("Panam uprav ten Excel", "unsupported_direct_edit", None),
]


def strip_panam_prefix(text: str) -> str:
    return re.sub(r"^(?:hey\s+)?panam\b[\s,.:;!-]*", "", text, flags=re.IGNORECASE)


def format_result(mode: str | None, output_format: str | None) -> str:
    return f"{mode}/{output_format}"


def main() -> int:
    failures = 0

    for input_text, expected_mode, expected_output_format in TEST_CASES:
        request_text = strip_panam_prefix(input_text)
        result = decide_file_response_mode(request_text)
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

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
