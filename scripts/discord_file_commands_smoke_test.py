from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_file_commands


def main() -> None:
    module_source = (PROJECT_ROOT / "panam_discord_file_commands.py").read_text(
        encoding="utf-8"
    )
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    for name in (
        "handle_transform_docx_command",
        "handle_transform_excel_command",
        "handle_process_file_command",
        "handle_extract_data_command",
    ):
        assert hasattr(panam_discord_file_commands, name), name

    assert panam_discord_file_commands.normalize_output_format("md") == "md"
    assert panam_discord_file_commands.normalize_output_format(".md") == "md"
    assert panam_discord_file_commands.normalize_output_format("XLSX") == "xlsx"

    print("discord file commands smoke test ok")


if __name__ == "__main__":
    main()
