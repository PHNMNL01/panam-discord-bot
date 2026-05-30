from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from panam_docx import create_docx_from_text


SAMPLE_TEXT = """# Souhrn zakazky

Panam umi vytvorit jednoduchy DOCX vystup z textu.

## Checklist
- nacist text
- rozpoznat jednoduche nadpisy
- ulozit vystupni soubor

1. Prvni krok
2. Druhy krok
"""


def main() -> None:
    output_path = PROJECT_ROOT / "runtime" / "manual_tests" / "docx_smoke_test.docx"
    created_path = create_docx_from_text(
        SAMPLE_TEXT,
        output_path,
        title="Panam DOCX smoke test",
    )
    print(created_path)


if __name__ == "__main__":
    main()
