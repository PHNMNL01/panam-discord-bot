import panam_core
import panam_memory
from panam_command_router import parse_panam_command
from panam_phrases import BASIC_PANAM_EMPTY_RESPONSE


WEB_USER_ID = 1
WEB_USER_NAME = "Matěj"
WEB_CHANNEL_ID = 1001


WEB_HELP_TEXT = """Panam web scaffold commands:
- Panam help
- Panam rekni mi <dotaz>
- Panam talk <tema>
- Panam shrn <text>
- Panam pridej poznamku <text>
- Panam ukaz poznamky
- Panam najdi poznamku <dotaz>
- Panam pridej todo <text>
- Panam ukaz todo"""


async def _handle_ask(model: str, user_text: str, raw_text: str) -> str:
    history = panam_memory.get_messages(WEB_CHANNEL_ID)
    response = await panam_core.handle_chat(model, user_text, history=history)
    panam_memory.add_message(WEB_CHANNEL_ID, "user", raw_text or user_text)
    panam_memory.add_message(WEB_CHANNEL_ID, "assistant", response.text)
    return response.text


async def handle_web_message(model: str, message: str) -> str:
    command = parse_panam_command(message)
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
