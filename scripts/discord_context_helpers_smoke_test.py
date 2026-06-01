from dataclasses import dataclass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_context


@dataclass
class FakeUser:
    id: int
    display_name: str


@dataclass
class FakeChannel:
    id: int


@dataclass
class FakeGuild:
    id: int


@dataclass
class FakeSource:
    user: FakeUser
    channel: FakeChannel
    guild: FakeGuild
    channel_id: int | None = None
    guild_id: int | None = None


class FakeInteraction:
    def __init__(self, interaction_id: int) -> None:
        self.id = interaction_id


def main() -> None:
    assert panam_discord_context is not None

    module_source = Path(panam_discord_context.__file__).read_text(encoding="utf-8")
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    source = FakeSource(
        user=FakeUser(123, "Tester"),
        channel=FakeChannel(456),
        guild=FakeGuild(789),
    )
    assert panam_discord_context.get_safe_context(source) == {
        "user_id": 123,
        "user_name": "Tester",
        "channel_id": 456,
        "guild_id": 789,
    }

    assert panam_discord_context.get_context_int({"value": 42}, "value") == 42
    assert panam_discord_context.get_context_int({"value": "42"}, "value") == 42
    assert panam_discord_context.get_context_int({"value": "x"}, "value") == 0

    panam_discord_context.COMMAND_STATUSES.clear()
    interaction = FakeInteraction(1001)
    panam_discord_context.mark_command_status(interaction, "started")
    assert panam_discord_context.COMMAND_STATUSES[1001] == "started"

    original_interaction_class = panam_discord_context.discord.Interaction
    try:
        panam_discord_context.discord.Interaction = FakeInteraction
        panam_discord_context.mark_source_error(interaction)
    finally:
        panam_discord_context.discord.Interaction = original_interaction_class

    assert panam_discord_context.COMMAND_STATUSES[1001] == "error"

    print("discord context helpers smoke test ok")


if __name__ == "__main__":
    main()
