import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_channel_tools


@dataclass
class FakeAuthor:
    name: str
    display_name: str
    bot: bool = False


@dataclass
class FakeMessage:
    author: FakeAuthor
    content: str
    created_at: datetime


class FakeChannel:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages
        self.requested_limits = []

    async def history(self, limit: int):
        self.requested_limits.append(limit)
        for message in self.messages[:limit]:
            yield message


class NoHistoryChannel:
    pass


def message(content: str, *, author: str = "Alice", bot: bool = False) -> FakeMessage:
    return FakeMessage(
        author=FakeAuthor(name=author.lower(), display_name=author, bot=bot),
        content=content,
        created_at=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
    )


async def test_search_recent_channel_messages() -> None:
    channel = FakeChannel(
        [
            message("target od bota", author="Bot", bot=True),
            message("prvni target zprava"),
            message("bez shody"),
        ]
    )
    result = await panam_discord_channel_tools.search_recent_channel_messages(
        channel,
        "target",
        limit=500,
    )
    assert result is not None
    assert result.startswith("Nalezené zprávy:\n")
    assert "Alice: prvni target zprava" in result
    assert "Bot" not in result
    assert channel.requested_limits == [300]

    no_match = await panam_discord_channel_tools.search_recent_channel_messages(
        FakeChannel([message("bez shody")]),
        "target",
    )
    assert no_match == ""

    no_history = await panam_discord_channel_tools.search_recent_channel_messages(
        NoHistoryChannel(),
        "target",
    )
    assert no_history is None

    many_matches = FakeChannel([message(f"target {index}") for index in range(12)])
    limited = await panam_discord_channel_tools.search_recent_channel_messages(
        many_matches,
        "target",
    )
    assert limited is not None
    assert limited.count(". [") == 10
    assert "10. [" in limited
    assert "11. [" not in limited


async def test_build_channel_summary_text() -> None:
    channel = FakeChannel(
        [
            message("bot text", author="Bot", bot=True),
            message(""),
            message("novejsi text", author="Alice"),
            message("starsi text", author="Bob"),
        ]
    )
    result = await panam_discord_channel_tools.build_channel_summary_text(
        channel,
        limit=500,
    )
    assert result is not None
    assert channel.requested_limits == [200]
    assert "Autor: Bob" in result
    assert "Autor: Alice" in result
    assert result.index("Autor: Bob") < result.index("Autor: Alice")
    assert "Bot" not in result
    assert "Čas: 2026-06-01 12:30 UTC" in result
    assert "Text: starsi text" in result

    empty = await panam_discord_channel_tools.build_channel_summary_text(
        FakeChannel([message("", author="Alice"), message("bot only", bot=True)])
    )
    assert empty == ""

    no_history = await panam_discord_channel_tools.build_channel_summary_text(
        NoHistoryChannel()
    )
    assert no_history is None


async def main_async() -> None:
    module_source = (PROJECT_ROOT / "panam_discord_channel_tools.py").read_text(
        encoding="utf-8"
    )
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    await test_search_recent_channel_messages()
    await test_build_channel_summary_text()


def main() -> None:
    asyncio.run(main_async())
    print("discord channel tools smoke test ok")


if __name__ == "__main__":
    main()
