from dataclasses import dataclass
from pathlib import Path

from panam_ai import (
    ask_panam,
    ask_panam_talk,
    summarize_channel_messages,
    summarize_text,
)


@dataclass
class PanamResponse:
    text: str
    output_file_path: Path | None = None


async def handle_chat(
    model: str,
    message: str,
    history: list[dict] | None = None,
) -> PanamResponse:
    return PanamResponse(text=await ask_panam(model, message, history=history))


async def handle_talk(model: str, message: str) -> PanamResponse:
    return PanamResponse(text=await ask_panam_talk(model, message))


async def handle_summary(model: str, text: str) -> PanamResponse:
    return PanamResponse(text=await summarize_text(model, text))


async def handle_channel_summary(model: str, channel_text: str) -> PanamResponse:
    return PanamResponse(text=await summarize_channel_messages(model, channel_text))
