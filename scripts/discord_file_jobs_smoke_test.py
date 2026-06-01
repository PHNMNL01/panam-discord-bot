from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_file_jobs


def main() -> None:
    assert panam_discord_file_jobs is not None

    module_source = Path(panam_discord_file_jobs.__file__).read_text(encoding="utf-8")
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    for name in (
        "get_items_for_xlsx",
        "run_human_document_file_job",
        "run_structured_data_file_job",
        "run_spreadsheet_transform_file_job",
        "run_docx_transform_file_job",
    ):
        assert hasattr(panam_discord_file_jobs, name), name

    assert panam_discord_file_jobs.get_items_for_xlsx(
        {"items": [{"a": 1}, "x"]}
    ) == [{"a": 1}, {"value": "x"}]
    assert panam_discord_file_jobs.get_items_for_xlsx(
        [{"a": 1}, {"b": 2}]
    ) == [{"a": 1}, {"b": 2}]
    assert panam_discord_file_jobs.get_items_for_xlsx({"a": 1}) == [{"a": 1}]
    assert panam_discord_file_jobs.get_items_for_xlsx("x") == [{"value": "x"}]

    print("discord file jobs smoke test ok")


if __name__ == "__main__":
    main()
