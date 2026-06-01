from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_natural_file_orchestrator


def main() -> None:
    module_source = (
        PROJECT_ROOT / "panam_discord_natural_file_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    for name in (
        "get_natural_file_action_name",
        "sanitize_classifier_context_text",
        "get_recent_classifier_context",
        "remember_router_decision",
        "handle_natural_file_request",
        "handle_ai_classified_file_request",
        "handle_file_summary_followup",
    ):
        assert hasattr(panam_discord_natural_file_orchestrator, name), name

    expected_actions = {
        "human_document": "process_file",
        "structured_data": "extract_data",
        "docx_transform": "transform_docx",
        "spreadsheet_transform": "transform_excel",
        "chat_answer": "chat_answer",
    }
    for mode, expected_action in expected_actions.items():
        assert (
            panam_discord_natural_file_orchestrator.get_natural_file_action_name(
                {"mode": mode}
            )
            == expected_action
        )

    assert (
        panam_discord_natural_file_orchestrator.sanitize_classifier_context_text(
            "bezny text"
        )
        == "bezny text"
    )
    assert (
        panam_discord_natural_file_orchestrator.sanitize_classifier_context_text(
            "muj token je tajny"
        )
        == "[sensitive content omitted]"
    )
    assert (
        panam_discord_natural_file_orchestrator.sanitize_classifier_context_text(
            "password=secret"
        )
        == "[sensitive content omitted]"
    )

    print("natural file orchestrator smoke test ok")


if __name__ == "__main__":
    main()
