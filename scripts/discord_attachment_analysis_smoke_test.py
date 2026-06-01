import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_attachment_analysis
from panam_discord_attachments import MAX_IMAGE_SIZE_BYTES


@dataclass
class FakeAttachment:
    filename: str
    size: int
    url: str


async def test_analyze_selected_attachment_errors() -> None:
    unsupported = FakeAttachment(
        filename="archive.zip",
        size=10,
        url="http://example.invalid/file.zip",
    )
    try:
        await panam_discord_attachment_analysis.analyze_selected_attachment(
            "test-model",
            unsupported,
            "test",
        )
    except panam_discord_attachment_analysis.AttachmentAnalysisUserError:
        pass
    else:
        raise AssertionError("unsupported attachment should raise user error")

    oversized_image = FakeAttachment(
        filename="image.png",
        size=MAX_IMAGE_SIZE_BYTES + 1,
        url="http://example.invalid/image.png",
    )
    try:
        await panam_discord_attachment_analysis.analyze_selected_attachment(
            "test-model",
            oversized_image,
            "test",
        )
    except panam_discord_attachment_analysis.AttachmentAnalysisUserError:
        pass
    else:
        raise AssertionError("oversized image should raise user error")


def main() -> None:
    module_source = (PROJECT_ROOT / "panam_discord_attachment_analysis.py").read_text(
        encoding="utf-8"
    )
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    for name in (
        "AttachmentAnalysisUserError",
        "analyze_selected_attachment",
        "is_natural_analyze_request",
        "is_natural_attachment_analyze_request",
        "is_generic_natural_attachment_request",
        "get_natural_analyze_question",
        "get_natural_attachment_analyze_question",
        "is_attachment_summary_request",
        "is_attachment_error_request",
        "is_attachment_content_request",
        "is_attachment_look_request",
        "is_general_attachment_context_request",
        "get_attachment_context_question",
        "is_context_attachment_request",
        "get_context_attachment_question",
    ):
        assert hasattr(panam_discord_attachment_analysis, name), name

    assert panam_discord_attachment_analysis.is_attachment_summary_request("shrn to")
    assert panam_discord_attachment_analysis.is_attachment_error_request(
        "co je na tom spatne"
    )
    assert panam_discord_attachment_analysis.is_attachment_content_request("co tam je")
    assert panam_discord_attachment_analysis.is_attachment_look_request("koukni na to")
    assert panam_discord_attachment_analysis.is_general_attachment_context_request(
        "koukni na to"
    )
    assert "Shrň" in panam_discord_attachment_analysis.get_attachment_context_question(
        "shrn to",
        "document",
    )

    asyncio.run(test_analyze_selected_attachment_errors())
    print("discord attachment analysis smoke test ok")


if __name__ == "__main__":
    main()
