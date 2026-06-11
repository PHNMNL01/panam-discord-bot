from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_natural_intents


def ensure_router_helpers_available() -> None:
    if (
        getattr(
            panam_discord_natural_intents.decide_file_response_mode,
            "__name__",
            "",
        )
        != "_missing_router_dependency"
    ):
        return

    def decide_file_response_mode(text: str) -> dict:
        if "excel" in text or "xlsx" in text:
            return {"mode": "structured_data", "output_format": "xlsx"}
        return {"mode": "chat_answer", "output_format": None}

    panam_discord_natural_intents.decide_file_response_mode = decide_file_response_mode
    panam_discord_natural_intents.has_explicit_file_output_request = (
        lambda text: "excel" in text or "xlsx" in text
    )
    panam_discord_natural_intents.has_explicit_file_action_request = lambda text: False


def main() -> None:
    module_source = (PROJECT_ROOT / "panam_discord_natural_intents.py").read_text(
        encoding="utf-8"
    )
    assert "import bot" not in module_source
    assert "from bot" not in module_source
    assert "import discord" not in module_source
    assert "from discord" not in module_source

    for name in (
        "should_skip_recent_text_content",
        "is_file_router_candidate",
        "parse_natural_intent",
        "parse_natural_voice_reply_question",
        "get_natural_action_name",
    ):
        assert hasattr(panam_discord_natural_intents, name), name

    assert panam_discord_natural_intents.parse_natural_intent("help") == (
        "help",
        None,
    )
    assert panam_discord_natural_intents.parse_natural_intent("zapamatuj si to") == (
        "note_add_previous",
        None,
    )
    assert panam_discord_natural_intents.parse_natural_intent("ukaz poznamky") == (
        "note_list",
        None,
    )
    assert panam_discord_natural_intents.parse_natural_intent("ukaz todo") == (
        "todo_list",
        None,
    )

    natural_voice_cases = {
        "hlasem co umíš?": "co umíš?",
        "řekni hlasem co umíš?": "co umíš?",
        "rekni hlasem co umis?": "co umis?",
        "řekni nahlas jak se jmenuješ?": "jak se jmenuješ?",
        "rekni nahlas jak se jmenujes?": "jak se jmenujes?",
        "odpověz hlasem jak se máš?": "jak se máš?",
        "odpovez hlasem jak se mas?": "jak se mas?",
    }
    for text, question in natural_voice_cases.items():
        assert (
            panam_discord_natural_intents.parse_natural_voice_reply_question(text)
            == question
        )

    assert panam_discord_natural_intents.should_skip_recent_text_content("") is True
    assert panam_discord_natural_intents.should_skip_recent_text_content("Panam") is True
    assert (
        panam_discord_natural_intents.should_skip_recent_text_content(
            "Panam hlasem co umis?"
        )
        is True
    )
    assert (
        panam_discord_natural_intents.should_skip_recent_text_content(
            "bezna veta bez prikazu"
        )
        is False
    )

    ensure_router_helpers_available()
    assert (
        panam_discord_natural_intents.get_natural_action_name("ahoj", "Panam ahoj")
        == "ask"
    )
    assert (
        panam_discord_natural_intents.get_natural_action_name(
            "dej mi to do excelu",
            "Panam dej mi to do excelu",
        )
        == "extract_data"
    )

    print("natural intents smoke test ok")


if __name__ == "__main__":
    main()
