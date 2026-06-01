from dataclasses import dataclass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_message_helpers


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeBotUser:
    id: int


def main() -> None:
    assert panam_discord_message_helpers is not None

    module_source = Path(panam_discord_message_helpers.__file__).read_text(
        encoding="utf-8"
    )
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    assert (
        panam_discord_message_helpers.normalize_natural_text("Pánam, ŘEKNI   mi!")
        == "panam, rekni mi"
    )
    assert panam_discord_message_helpers.normalize_text("  AHOJ   Pánam!  ") == "ahoj panam"

    assert panam_discord_message_helpers.is_panam_addressed("Panam ahoj") is True
    assert panam_discord_message_helpers.is_panam_addressed("hey panam") is True
    assert panam_discord_message_helpers.is_panam_addressed("co myslíš panam") is True

    assert panam_discord_message_helpers.is_context_reference("to") is True
    assert panam_discord_message_helpers.is_context_reference("toto") is True

    assert (
        panam_discord_message_helpers.extract_basic_panam_prompt(
            "Panam řekni mi ahoj"
        )
        == "řekni mi ahoj"
    )

    bot_user = FakeBotUser(123)
    assert (
        panam_discord_message_helpers.extract_panam_request(
            FakeMessage("Panam ahoj"),
            bot_user,
        )
        == "ahoj"
    )
    assert (
        panam_discord_message_helpers.extract_panam_request(
            FakeMessage("<@123> ahoj"),
            bot_user,
        )
        == "ahoj"
    )

    assert (
        panam_discord_message_helpers.extract_inline_content_after_trigger(
            "Panam přidej poznámku koupit mléko",
            ["přidej poznámku"],
        )
        == "koupit mléko"
    )

    print("discord message helpers smoke test ok")


if __name__ == "__main__":
    main()
