from dataclasses import dataclass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_attachments


@dataclass
class FakeAttachment:
    filename: str
    size: int = 123


@dataclass
class FakeMessage:
    attachments: list[FakeAttachment]


def main() -> None:
    png = FakeAttachment("image.png")
    txt = FakeAttachment("notes.txt")
    xlsx = FakeAttachment("data.xlsx", size=456)
    unsupported = FakeAttachment("archive.zip")

    assert panam_discord_attachments.get_attachment_kind(png) == "image"
    assert panam_discord_attachments.get_attachment_kind(txt) == "document"
    assert panam_discord_attachments.get_attachment_kind(xlsx) == "document"
    assert panam_discord_attachments.get_attachment_kind(unsupported) is None

    assert panam_discord_attachments.is_supported_image_attachment(png) is True
    assert panam_discord_attachments.is_supported_image_attachment(txt) is False

    assert panam_discord_attachments.is_supported_document_attachment(txt) is True
    assert panam_discord_attachments.is_supported_document_attachment(xlsx) is True
    assert panam_discord_attachments.is_supported_document_attachment(png) is False

    image_message = FakeMessage([unsupported, png, txt])
    assert panam_discord_attachments.find_image_attachment_in_message(image_message) is png

    supported_message = FakeMessage([unsupported, xlsx])
    assert (
        panam_discord_attachments.find_supported_attachment_in_message(supported_message)
        is xlsx
    )

    info = panam_discord_attachments.get_safe_attachment_info(xlsx)
    assert info == {
        "filename": "data.xlsx",
        "extension": ".xlsx",
        "size": 456,
        "file_type": "document",
    }, info

    assert panam_discord_attachments.get_safe_attachment_info(None) == {}

    print("discord attachment helpers smoke test ok")


if __name__ == "__main__":
    main()
