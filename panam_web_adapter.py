from __future__ import annotations

from pathlib import Path

import panam_core
import panam_memory
from panam_command_router import parse_panam_command
from panam_phrases import BASIC_PANAM_EMPTY_RESPONSE


WEB_USER_ID = 1
WEB_USER_NAME = "Matěj"
WEB_CHANNEL_ID = 1001


WEB_HELP_TEXT = """Panam web scaffold commands:
- help
- rekni mi <dotaz>
- talk <tema>
- shrn <text>
- pridej poznamku <text>
- ukaz poznamky
- najdi poznamku <dotaz>
- pridej todo <text>
- ukaz todo"""


async def handle_web_file_request(
    model: str,
    input_path: Path,
    original_filename: str,
    instruction: str,
    mode: str = "chat_answer",
    output_format: str | None = None,
) -> PanamFileResult:
    from panam_file_service import PanamFileRequest, process_panam_file_request

    request = PanamFileRequest(
        input_path=Path(input_path),
        original_filename=original_filename,
        instruction=instruction,
        mode=mode,
        output_format=output_format,
        user_id=WEB_USER_ID,
        channel_id=WEB_CHANNEL_ID,
        source="web",
    )
    return await process_panam_file_request(model, request)


def clear_web_chat_memory() -> str:
    panam_memory.clear_channel_memory(WEB_CHANNEL_ID)
    return "Chat vyčištěn."


def _is_short_command(text: str) -> bool:
    command = parse_panam_command(text, assume_addressed=True)
    if command.intent in {
        "empty",
        "help",
        "summary_previous",
        "ask_previous",
        "note_list",
        "todo_list",
    }:
        return True

    return command.intent in {"note_add", "note_search", "todo_add"} and len(
        text.split()
    ) <= 5


def get_last_user_message(
    channel_id: int,
    exclude_text: str | None = None,
) -> str | None:
    excluded = (exclude_text or "").strip()

    for message in reversed(panam_memory.get_messages(channel_id)):
        if message.get("role") != "user":
            continue

        content = str(message.get("content") or "").strip()
        if not content:
            continue

        if excluded and content == excluded:
            continue

        if _is_short_command(content):
            continue

        return content

    return None


async def _handle_ask(model: str, user_text: str, raw_text: str) -> str:
    history = panam_memory.get_messages(WEB_CHANNEL_ID)
    response = await panam_core.handle_chat(model, user_text, history=history)
    panam_memory.add_message(WEB_CHANNEL_ID, "user", raw_text or user_text)
    panam_memory.add_message(WEB_CHANNEL_ID, "assistant", response.text)
    return response.text


async def handle_web_message(model: str, message: str) -> str:
    command = parse_panam_command(message, assume_addressed=True)
    command_text = command.text or ""

    if command.intent == "empty":
        return BASIC_PANAM_EMPTY_RESPONSE

    if command.intent == "help":
        return WEB_HELP_TEXT

    if command.intent == "talk":
        response = await panam_core.handle_talk(model, command_text)
        panam_memory.add_message(WEB_CHANNEL_ID, "user", command.raw_text or command_text)
        panam_memory.add_message(WEB_CHANNEL_ID, "assistant", response.text)
        return response.text

    if command.intent == "summary":
        response = await panam_core.handle_summary(model, command_text)
        panam_memory.add_message(WEB_CHANNEL_ID, "user", command.raw_text or command_text)
        panam_memory.add_message(WEB_CHANNEL_ID, "assistant", response.text)
        return response.text

    if command.intent == "summary_previous":
        previous_text = get_last_user_message(WEB_CHANNEL_ID, exclude_text=command.raw_text)
        if previous_text is None:
            answer = "Pošli mi prosím text, který chceš stručně shrnout."
        else:
            response = await panam_core.handle_summary(model, previous_text)
            answer = response.text

        panam_memory.add_message(WEB_CHANNEL_ID, "user", command.raw_text)
        panam_memory.add_message(WEB_CHANNEL_ID, "assistant", answer)
        return answer

    if command.intent == "ask_previous":
        previous_text = get_last_user_message(WEB_CHANNEL_ID, exclude_text=command.raw_text)
        if previous_text is None:
            return await _handle_ask(model, command.raw_text, command.raw_text)

        prompt = (
            "Uživatel navazuje na předchozí text:\n\n"
            f"{previous_text}\n\n"
            f"Dotaz: {command.raw_text}"
        )
        history = panam_memory.get_messages(WEB_CHANNEL_ID)
        response = await panam_core.handle_chat(model, prompt, history=history)
        panam_memory.add_message(WEB_CHANNEL_ID, "user", command.raw_text)
        panam_memory.add_message(WEB_CHANNEL_ID, "assistant", response.text)
        return response.text

    if command.intent == "note_add":
        response = await panam_core.handle_note_add(
            command_text,
            WEB_USER_ID,
            WEB_USER_NAME,
            WEB_CHANNEL_ID,
        )
        return response.text

    if command.intent == "note_list":
        response = await panam_core.handle_note_list()
        return response.text

    if command.intent == "note_search":
        response = await panam_core.handle_note_search(command_text)
        return response.text

    if command.intent == "todo_add":
        response = await panam_core.handle_todo_add(
            command_text,
            WEB_USER_ID,
            WEB_USER_NAME,
            WEB_CHANNEL_ID,
        )
        return response.text

    if command.intent == "todo_list":
        response = await panam_core.handle_todo_list()
        return response.text

    return await _handle_ask(model, command_text or command.raw_text, command.raw_text)
