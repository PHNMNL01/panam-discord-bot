from dataclasses import dataclass
from pathlib import Path

import panam_notes
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


async def handle_note_add(
    text: str,
    author_id: int,
    author_name: str,
    channel_id: int,
) -> PanamResponse:
    panam_notes.create_note(text, author_id, author_name, channel_id)
    return PanamResponse(text="Poznámka uložená.")


async def handle_note_list() -> PanamResponse:
    return PanamResponse(text=panam_notes.format_note_list_response())


async def handle_note_search(query: str) -> PanamResponse:
    return PanamResponse(text=panam_notes.format_note_search_response(query))
