import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_history


@dataclass
class FakeAttachment:
    filename: str
    size: int = 123


@dataclass
class FakeAuthor:
    bot: bool = False


@dataclass
class FakeMessage:
    author: FakeAuthor
    attachments: list[FakeAttachment]


class FakeChannel:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages
        self.last_limit: int | None = None

    async def history(self, limit: int = 100):
        self.last_limit = limit
        for message in self.messages[:limit]:
            yield message


class FakeChannelWithoutHistory:
    pass


async def main() -> None:
    bot_image = FakeAttachment("bot-image.png")
    unsupported = FakeAttachment("archive.zip")
    png = FakeAttachment("photo.png")
    txt = FakeAttachment("notes.txt")
    xlsx = FakeAttachment("data.xlsx")
    docx = FakeAttachment("brief.docx")

    channel = FakeChannel(
        [
            FakeMessage(FakeAuthor(bot=True), [bot_image]),
            FakeMessage(FakeAuthor(), [unsupported]),
            FakeMessage(FakeAuthor(), [png]),
            FakeMessage(FakeAuthor(), [txt, xlsx, docx]),
        ]
    )

    assert await panam_discord_history.find_recent_image_attachment(channel) is png
    assert channel.last_limit == 15
    assert await panam_discord_history.find_recent_supported_attachment(channel) is png
    assert await panam_discord_history.find_recent_xlsx_attachment(channel) is xlsx
    assert await panam_discord_history.find_recent_docx_attachment(channel) is docx

    bot_only_channel = FakeChannel(
        [
            FakeMessage(FakeAuthor(bot=True), [bot_image, xlsx, docx]),
            FakeMessage(FakeAuthor(), [unsupported]),
        ]
    )

    assert (
        await panam_discord_history.find_recent_image_attachment(bot_only_channel)
        is None
    )
    assert (
        await panam_discord_history.find_recent_xlsx_attachment(bot_only_channel)
        is None
    )
    assert (
        await panam_discord_history.find_recent_docx_attachment(bot_only_channel)
        is None
    )
    assert (
        await panam_discord_history.find_recent_supported_attachment(
            FakeChannelWithoutHistory()
        )
        is None
    )

    print("discord history helpers smoke test ok")


if __name__ == "__main__":
    asyncio.run(main())
