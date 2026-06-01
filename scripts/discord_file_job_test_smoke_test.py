from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_file_job_test


def main() -> None:
    module_source = (PROJECT_ROOT / "panam_discord_file_job_test.py").read_text(
        encoding="utf-8"
    )
    assert "import bot" not in module_source
    assert "from bot" not in module_source
    assert hasattr(panam_discord_file_job_test, "handle_file_job_test_command")
    assert hasattr(panam_discord_file_job_test, "normalize_file_job_test_output_format")

    normalize = panam_discord_file_job_test.normalize_file_job_test_output_format
    assert normalize("md") == "md"
    assert normalize(".md") == "md"
    assert normalize("txt") == "txt"
    assert normalize("csv") == "txt"

    print("discord file job test smoke test ok")


if __name__ == "__main__":
    main()
