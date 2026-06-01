from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_read_file


def main() -> None:
    module_source = (PROJECT_ROOT / "panam_discord_read_file.py").read_text(
        encoding="utf-8"
    )
    assert "import bot" not in module_source
    assert "from bot" not in module_source
    assert hasattr(panam_discord_read_file, "handle_read_file_command")
    assert hasattr(panam_discord_read_file, "extract_read_file_document_text")

    assert (
        panam_discord_read_file.extract_read_file_document_text(
            "test.txt",
            b"hello",
        )
        == "hello"
    )

    print("discord read file smoke test ok")


if __name__ == "__main__":
    main()
