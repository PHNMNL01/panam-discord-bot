import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_text_history


@dataclass
class FakeAuthor:
    id: int
    bot: bool = False


@dataclass
class FakeMessage:
    author: FakeAuthor
    content: str
    created_at: int = 0
    channel: object | None = None


class FakeChannel:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages
        for message in self.messages:
            message.channel = self

    async def history(self, limit: int = 100, before=None):
        yielded = 0
        for message in self.messages:
            if before is not None and message.created_at >= before:
                continue
            if yielded >= limit:
                return
            yielded += 1
            yield message


class FakeChannelWithoutHistory:
    pass


async def main() -> None:
    module_source = Path(panam_discord_text_history.__file__).read_text(
        encoding="utf-8"
    )
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    bot_author = FakeAuthor(1, bot=True)
    user_author = FakeAuthor(2)
    channel = FakeChannel(
        [
            FakeMessage(bot_author, "bot text"),
            FakeMessage(user_author, ""),
            FakeMessage(user_author, "   "),
            FakeMessage(user_author, "/help"),
            FakeMessage(user_author, "skip me"),
            FakeMessage(user_author, "normal text"),
        ]
    )
    assert (
        await panam_discord_text_history.find_recent_text_message(
            channel,
            should_skip_content=lambda content: content == "skip me",
        )
        == "normal text"
    )
    assert (
        await panam_discord_text_history.find_recent_text_message(
            FakeChannelWithoutHistory()
        )
        is None
    )

    current_author = FakeAuthor(10)
    other_author = FakeAuthor(20)
    previous_channel = FakeChannel(
        [
            FakeMessage(FakeAuthor(99, bot=True), "bot previous", created_at=1),
            FakeMessage(other_author, "other previous", created_at=2),
            FakeMessage(current_author, "same author previous", created_at=3),
        ]
    )
    current_message = FakeMessage(
        current_author,
        "current",
        created_at=4,
        channel=previous_channel,
    )

    assert (
        await panam_discord_text_history.find_previous_message_content(
            current_message
        )
        == "same author previous"
    )
    assert (
        await panam_discord_text_history.find_previous_message_content(
            current_message,
            prefer_same_author=False,
        )
        == "other previous"
    )

    print("discord text history smoke test ok")


if __name__ == "__main__":
    asyncio.run(main())
