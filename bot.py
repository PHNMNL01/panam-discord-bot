import os
import asyncio
import logging
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

from panam_ai import (
    analyze_image,
    ask_panam,
    ask_panam_talk,
    shorten_for_discord,
    summarize_channel_messages,
    summarize_text,
)


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(dotenv_path=BASE_DIR / ".env")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_IDS = [
    guild_id.strip()
    for guild_id in os.getenv("DISCORD_GUILD_IDS", "").split(",")
    if guild_id.strip()
]
ALLOWED_CHANNEL_IDS = [
    channel_id.strip()
    for channel_id in os.getenv("ALLOWED_CHANNEL_IDS", "").split(",")
    if channel_id.strip()
]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
NOTES_FILE = BASE_DIR / "notes.json"
TODOS_FILE = BASE_DIR / "todos.json"
SUPPORTED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


if not DISCORD_BOT_TOKEN:
    raise RuntimeError("Chybí DISCORD_BOT_TOKEN v .env souboru.")


def load_notes() -> list[dict]:
    if not NOTES_FILE.exists():
        return []

    with NOTES_FILE.open("r", encoding="utf-8") as notes_file:
        notes = json.load(notes_file)

    if not isinstance(notes, list):
        return []

    return notes


def save_notes(notes: list[dict]) -> None:
    with NOTES_FILE.open("w", encoding="utf-8") as notes_file:
        json.dump(notes, notes_file, ensure_ascii=False, indent=2)


def load_todos() -> list[dict]:
    if not TODOS_FILE.exists():
        return []

    with TODOS_FILE.open("r", encoding="utf-8") as todos_file:
        todos = json.load(todos_file)

    if not isinstance(todos, list):
        return []

    return todos


def save_todos(todos: list[dict]) -> None:
    with TODOS_FILE.open("w", encoding="utf-8") as todos_file:
        json.dump(todos, todos_file, ensure_ascii=False, indent=2)


def get_author_name(author) -> str:
    return getattr(author, "display_name", author.name)


def split_discord_message(text: str, limit: int = 1900) -> list[str]:
    if not text:
        return [""]

    chunks = []
    remaining = text.strip()

    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks


async def send_followup_chunks(interaction: discord.Interaction, text: str) -> None:
    for chunk in split_discord_message(text):
        await interaction.followup.send(chunk)


async def send_channel_chunks(message: discord.Message, text: str) -> None:
    for chunk in split_discord_message(text):
        await message.channel.send(chunk)


def create_note(text: str, author, channel_id: int) -> None:
    notes = load_notes()
    notes.append(
        {
            "text": text,
            "author_id": author.id,
            "author_name": get_author_name(author),
            "channel_id": channel_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_notes(notes)


def format_note_list_response() -> str:
    notes = load_notes()
    if not notes:
        return "Zatím nemám žádné poznámky."

    lines = ["Poslední poznámky:"]
    for index, note in enumerate(reversed(notes[-10:]), start=1):
        author_name = note.get("author_name", "neznámý autor")
        created_at = note.get("created_at", "neznámý čas")
        note_text = note.get("text", "")
        lines.append(f"{index}. [{created_at}] {author_name}: {note_text}")

    return shorten_for_discord("\n".join(lines))


def format_note_search_response(query: str) -> str:
    notes = load_notes()
    if not notes:
        return "Zatím nemám žádné poznámky."

    query_lower = query.lower()
    matches = [
        note
        for note in notes
        if query_lower in str(note.get("text", "")).lower()
    ][-10:]

    if not matches:
        return "Nic jsem nenašla."

    lines = ["Nalezené poznámky:"]
    for index, note in enumerate(reversed(matches), start=1):
        author_name = note.get("author_name", "neznámý autor")
        created_at = note.get("created_at", "neznámý čas")
        note_text = note.get("text", "")
        lines.append(f"{index}. [{created_at}] {author_name}: {note_text}")

    return shorten_for_discord("\n".join(lines))


def create_todo(text: str, author, channel_id: int) -> int:
    todos = load_todos()
    next_id = max(
        (todo.get("id", 0) for todo in todos if isinstance(todo.get("id"), int)),
        default=0,
    ) + 1

    todos.append(
        {
            "id": next_id,
            "text": text,
            "done": False,
            "author_id": author.id,
            "author_name": get_author_name(author),
            "channel_id": channel_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }
    )
    save_todos(todos)

    return next_id


def format_todo_list_response() -> str:
    todos = load_todos()
    active_todos = [todo for todo in todos if not todo.get("done")]
    if not active_todos:
        return "Nemáš žádné aktivní úkoly."

    lines = ["Aktivní úkoly:"]
    for todo in active_todos[:15]:
        todo_id = todo.get("id", "?")
        text = todo.get("text", "")
        author_name = todo.get("author_name", "neznámý autor")
        lines.append(f"#{todo_id} - {text} ({author_name})")

    return shorten_for_discord("\n".join(lines))


def get_help_text() -> str:
    return (
        "Panam nápověda\n\n"
        "1. Slash commandy\n"
        "/ask, /summary, /channel_summary, /search_messages, /note_add, /note_list, "
        "/note_search, /todo_add, /todo_list, /todo_done, /analyze, /ping, /help, /panam_talk\n\n"
        "2. Panam asistentka\n"
        "Poznámky:\n"
        "`Panam přidej poznámku <text>`, `Panam ulož poznámku <text>`, "
        "`Panam zapamatuj si <text>`, `Panam zapamatuj si to`, "
        "`Panam ukaž poznámky`, `Panam najdi poznámku <text>`\n"
        "Todo:\n"
        "`Panam přidej todo <text>`, `Panam přidej úkol <text>`, "
        "`Panam ukaž todo`, `Panam ukaž úkoly`\n"
        "AI:\n"
        "`Panam řekni mi <dotaz>`, `Panam řekni <dotaz>`, `Panam odpověz <dotaz>`, "
        "`Panam co si myslíš o <text>`, `Co si myslí Panam o <text>`, "
        "`Panam co si o tom myslíš?`\n"
        "Shrnutí:\n"
        "`Panam shrň <text>`, `Panam shrň mi <text>`, `Panam udělej summary <text>`, "
        "`Panam shrň toto`, `Panam shrň to`\n"
        "Obrázky:\n"
        "`/analyze` s přílohou .png, .jpg, .jpeg, .webp nebo .gif do 20 MB\n"
        "Help:\n"
        "`Panam help`, `Panam pomoc`, `Panam nápověda`, `Panam co umíš?`, "
        "`Panam ukaž příkazy`\n\n"
        "3. Panam nomad / talk mód\n"
        "Volnější rozhovor s výraznější osobností Panam:\n"
        "`/panam_talk <text>`, `Panam talk <text>`, `Panam pokec <text>`, "
        "`Panam pokecej o <text>`, `Panam co si fakt myslíš o <text>`\n\n"
        "4. Bezpečnostní pravidla\n"
        "Nezadávej hesla, tokeny, API klíče, HR data, zákaznická data ani jiné citlivé údaje. "
        "Panam neukládá historii kanálu automaticky.\n\n"
        "5. Allowed channels\n"
        "Panam funguje jen v kanálech uvedených v `ALLOWED_CHANNEL_IDS`. "
        "Historii kanálu čte jen při explicitním commandu."
    )


async def find_previous_message_content(
    message: discord.Message,
    prefer_same_author: bool = True,
) -> Optional[str]:
    history = getattr(message.channel, "history", None)
    if history is None:
        return None

    fallback_content = None

    async for previous_message in history(limit=30, before=message.created_at):
        if previous_message.author.bot:
            continue

        previous_content = (previous_message.content or "").strip()
        if not previous_content:
            continue

        if not prefer_same_author:
            return previous_content

        if previous_message.author.id == message.author.id:
            return previous_content

        if fallback_content is None:
            fallback_content = previous_content

    return fallback_content


def extract_panam_request(
    message: discord.Message,
    bot_user: discord.ClientUser,
) -> Optional[str]:
    content = (message.content or "").strip()
    mention_patterns = (
        f"<@{bot_user.id}>",
        f"<@!{bot_user.id}>",
    )

    for mention in mention_patterns:
        if content.startswith(mention):
            return content[len(mention):].strip(" \t\n\r,.:;!-")

    match = re.match(r"^(?:hey\s+)?panam\b[\s,.:;!-]*(.*)$", content, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    if re.match(
        r"^co\s+si\s+(?:o\s+tom\s+)?(?:myslí|mysli)\s+panam\b",
        content,
        re.IGNORECASE,
    ):
        return content

    return None


def parse_natural_intent(text: str) -> Optional[tuple[str, Optional[str]]]:
    if re.match(r"^(?:help|pomoc|nápověda|prikazy|příkazy)\s*$", text, re.IGNORECASE):
        return "help", None

    if re.match(r"^co\s+(?:umíš|umis|dokážeš|dokazes)\s*\??$", text, re.IGNORECASE):
        return "help", None

    if re.match(r"^(?:ukaž|ukaz)\s+(?:příkazy|prikazy)\s*$", text, re.IGNORECASE):
        return "help", None

    if re.match(r"^zapamatuj\s+si\s+to\s*$", text, re.IGNORECASE):
        return "note_add_previous", None

    if re.match(r"^co\s+si\s+o\s+tom\s+(?:myslíš|myslis)\s*\??$", text, re.IGNORECASE):
        return "ask_previous", None

    if re.match(r"^co\s+si\s+o\s+tom\s+(?:myslí|mysli)\s+panam\s*\??$", text, re.IGNORECASE):
        return "ask_previous", None

    if re.match(r"^(?:shrň|shrn)\s+(?:to|toto)\s*$", text, re.IGNORECASE):
        return "summary_previous", None

    intent_patterns = (
        ("note_add", r"^(?:přidej|pridej)\s+(?:poznámku|poznamku)\s+(.+)$"),
        ("note_add", r"^(?:ulož|uloz)\s+(?:poznámku|poznamku)\s+(.+)$"),
        ("note_add", r"^zapamatuj\s+si\s+(.+)$"),
        ("note_add", r"^pamatuj\s+si\s+(.+)$"),
        ("note_add", r"^(?:ulož|uloz)\s+si\s+(.+)$"),
        ("todo_add", r"^(?:přidej|pridej)\s+todo\s+(.+)$"),
        ("todo_add", r"^(?:přidej|pridej)\s+(?:úkol|ukol)\s+(.+)$"),
        ("note_search", r"^najdi\s+(?:poznámku|poznamku)\s+(.+)$"),
        ("ask", r"^(?:řekni|rekni)\s+mi\s+(.+)$"),
        ("ask", r"^(?:řekni|rekni)\s+(.+)$"),
        ("ask", r"^(?:odpověz|odpovez)\s+(.+)$"),
        ("ask", r"^co\s+si\s+(?:myslíš|myslis)\s+o\s+(.+)$"),
        ("ask", r"^co\s+si\s+(?:myslí|mysli)\s+panam\s+o\s+(.+)$"),
        ("talk", r"^talk\s+(.+)$"),
        ("talk", r"^pokec\s+(.+)$"),
        ("talk", r"^(?:pokecáme|pokecame)\s+o\s+(.+)$"),
        ("talk", r"^pokecej\s+o\s+(.+)$"),
        ("talk", r"^co\s+si\s+fakt\s+(?:myslíš|myslis)\s+o\s+(.+)$"),
        ("summary", r"^(?:shrň|shrn)\s+mi\s+(.+)$"),
        ("summary", r"^(?:shrň|shrn)\s+(.+)$"),
        ("summary", r"^(?:udělej|udelej)\s+summary\s+(.+)$"),
    )

    for intent, pattern in intent_patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return intent, match.group(1).strip()

    if re.match(r"^(?:ukaž|ukaz)\s+(?:poznámky|poznamky)\s*$", text, re.IGNORECASE):
        return "note_list", None

    if re.match(r"^(?:ukaž|ukaz)\s+(?:todo|úkoly|ukoly)\s*$", text, re.IGNORECASE):
        return "todo_list", None

    return None


class DiscordAIBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True

        # Message content intent je potřeba pro práci s obsahem zpráv.
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """
        Registrace slash commandů.

        Pro PoC doporučuji použít DISCORD_GUILD_IDS.
        Guild sync je rychlý, často prakticky hned.
        Globální sync může trvat déle.
        """
        if DISCORD_GUILD_IDS:
            for guild_id in DISCORD_GUILD_IDS:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)

            logging.info(
                "Slash commandy synchronizovány pro %s serverů.",
                len(DISCORD_GUILD_IDS),
            )
        else:
            await self.tree.sync()
            logging.info("Slash commandy synchronizovány globálně.")

    async def on_ready(self) -> None:
        if self.user is None:
            logging.info("Bot je online, ale user zatím není dostupný.")
            return

        logging.info("Bot je online jako %s | ID: %s", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if ALLOWED_CHANNEL_IDS and str(message.channel.id) not in ALLOWED_CHANNEL_IDS:
            return

        if self.user is None:
            return

        request_text = extract_panam_request(message, self.user)
        if request_text is None:
            return

        intent = parse_natural_intent(request_text)
        if intent is None:
            await message.channel.send(
                "Tohle zatím neumím převést na akci. Zkus /help."
            )
            return

        intent_name, value = intent

        try:
            if intent_name == "help":
                await message.channel.send(get_help_text())
                return

            if intent_name == "note_add_previous":
                previous_content = await find_previous_message_content(
                    message,
                    prefer_same_author=False,
                )
                if previous_content is None:
                    await message.channel.send(
                        "Nenašla jsem předchozí zprávu k uložení."
                    )
                    return

                create_note(previous_content, message.author, message.channel.id)
                await message.channel.send("Poznámka uložená.")
                return

            if intent_name == "note_add" and value:
                create_note(value, message.author, message.channel.id)
                await message.channel.send("Poznámka uložená.")
                return

            if intent_name == "todo_add" and value:
                todo_id = create_todo(value, message.author, message.channel.id)
                await message.channel.send(f"Úkol #{todo_id} uložený.")
                return

            if intent_name == "note_search" and value:
                await message.channel.send(format_note_search_response(value))
                return

            if intent_name == "note_list":
                await message.channel.send(format_note_list_response())
                return

            if intent_name == "todo_list":
                await message.channel.send(format_todo_list_response())
                return

            if intent_name == "ask" and value:
                await send_channel_chunks(message, await ask_panam(OPENAI_MODEL, value))
                return

            if intent_name == "ask_previous":
                previous_content = await find_previous_message_content(message)
                if previous_content is None:
                    await message.channel.send(
                        "Nenašla jsem předchozí zprávu, ke které se mám vyjádřit."
                    )
                    return

                await send_channel_chunks(
                    message,
                    await ask_panam(
                        OPENAI_MODEL,
                        "Co si o tom myslíš?\n\n" + previous_content,
                    ),
                )
                return

            if intent_name == "summary" and value:
                await send_channel_chunks(message, await summarize_text(OPENAI_MODEL, value))
                return

            if intent_name == "summary_previous":
                previous_content = await find_previous_message_content(message)
                if previous_content is None:
                    await message.channel.send(
                        "Nenašla jsem předchozí zprávu ke shrnutí."
                    )
                    return

                await send_channel_chunks(
                    message,
                    await summarize_text(OPENAI_MODEL, previous_content),
                )
                return

            if intent_name == "talk" and value:
                await send_channel_chunks(
                    message,
                    await ask_panam_talk(OPENAI_MODEL, value),
                )
                return

        except Exception:
            logging.exception("Chyba při zpracování přirozené zprávy")
            await message.channel.send("Něco se pokazilo při zpracování akce.")


bot = DiscordAIBot()


@bot.tree.command(
    name="ping",
    description="Ověř, že je bot online."
)
async def ping(interaction: discord.Interaction) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message("Panam je online.")


@bot.tree.command(
    name="help",
    description="Zobraz dostupné commandy."
)
async def help_command(interaction: discord.Interaction) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(get_help_text())


@bot.tree.command(
    name="note_add",
    description="Ulož krátkou poznámku."
)
@app_commands.describe(text="Text poznámky")
async def note_add(interaction: discord.Interaction, text: str) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        notes = load_notes()
        notes.append(
            {
                "text": text,
                "author_id": interaction.user.id,
                "author_name": getattr(
                    interaction.user,
                    "display_name",
                    interaction.user.name,
                ),
                "channel_id": interaction.channel_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        save_notes(notes)

        await interaction.response.send_message("Poznámka uložená.")

    except Exception:
        logging.exception("Chyba při zpracování /note_add")
        await interaction.response.send_message(
            "Něco se pokazilo při ukládání poznámky."
        )


@bot.tree.command(
    name="note_list",
    description="Vypiš poslední poznámky."
)
async def note_list(interaction: discord.Interaction) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        notes = load_notes()

        if not notes:
            await interaction.response.send_message("Zatím nemám žádné poznámky.")
            return

        lines = ["Poslední poznámky:"]
        for index, note in enumerate(reversed(notes[-10:]), start=1):
            author_name = note.get("author_name", "neznámý autor")
            created_at = note.get("created_at", "neznámý čas")
            note_text = note.get("text", "")
            lines.append(f"{index}. [{created_at}] {author_name}: {note_text}")

        answer = "\n".join(lines)
        answer = shorten_for_discord(answer)

        await interaction.response.send_message(answer)

    except Exception:
        logging.exception("Chyba při zpracování /note_list")
        await interaction.response.send_message(
            "Něco se pokazilo při načítání poznámek."
        )


@bot.tree.command(
    name="note_search",
    description="Vyhledej uložené poznámky."
)
@app_commands.describe(query="Text k vyhledání")
async def note_search(interaction: discord.Interaction, query: str) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        notes = load_notes()

        if not notes:
            await interaction.response.send_message("Zatím nemám žádné poznámky.")
            return

        query_lower = query.lower()
        matches = [
            note
            for note in notes
            if query_lower in str(note.get("text", "")).lower()
        ][-10:]

        if not matches:
            await interaction.response.send_message("Nic jsem nenašla.")
            return

        lines = ["Nalezené poznámky:"]
        for index, note in enumerate(reversed(matches), start=1):
            author_name = note.get("author_name", "neznámý autor")
            created_at = note.get("created_at", "neznámý čas")
            note_text = note.get("text", "")
            lines.append(f"{index}. [{created_at}] {author_name}: {note_text}")

        answer = "\n".join(lines)
        answer = shorten_for_discord(answer)

        await interaction.response.send_message(answer)

    except Exception:
        logging.exception("Chyba při zpracování /note_search")
        await interaction.response.send_message(
            "Něco se pokazilo při vyhledávání poznámek."
        )


@bot.tree.command(
    name="todo_add",
    description="Přidej úkol do todo listu."
)
@app_commands.describe(text="Text úkolu")
async def todo_add(interaction: discord.Interaction, text: str) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        todos = load_todos()
        next_id = max(
            (todo.get("id", 0) for todo in todos if isinstance(todo.get("id"), int)),
            default=0,
        ) + 1

        todos.append(
            {
                "id": next_id,
                "text": text,
                "done": False,
                "author_id": interaction.user.id,
                "author_name": getattr(
                    interaction.user,
                    "display_name",
                    interaction.user.name,
                ),
                "channel_id": interaction.channel_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
            }
        )
        save_todos(todos)

        await interaction.response.send_message(f"Úkol #{next_id} uložený.")

    except Exception:
        logging.exception("Chyba při zpracování /todo_add")
        await interaction.response.send_message(
            "Něco se pokazilo při ukládání úkolu."
        )


@bot.tree.command(
    name="todo_list",
    description="Vypiš aktivní úkoly."
)
async def todo_list(interaction: discord.Interaction) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        todos = load_todos()
        active_todos = [todo for todo in todos if not todo.get("done")]

        if not active_todos:
            await interaction.response.send_message("Nemáš žádné aktivní úkoly.")
            return

        lines = ["Aktivní úkoly:"]
        for todo in active_todos[:15]:
            todo_id = todo.get("id", "?")
            text = todo.get("text", "")
            author_name = todo.get("author_name", "neznámý autor")
            lines.append(f"#{todo_id} - {text} ({author_name})")

        answer = "\n".join(lines)
        answer = shorten_for_discord(answer)

        await interaction.response.send_message(answer)

    except Exception:
        logging.exception("Chyba při zpracování /todo_list")
        await interaction.response.send_message(
            "Něco se pokazilo při načítání úkolů."
        )


@bot.tree.command(
    name="todo_done",
    description="Označ úkol jako hotový."
)
@app_commands.describe(todo_id="ID úkolu")
async def todo_done(interaction: discord.Interaction, todo_id: int) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    try:
        todos = load_todos()
        todo = next((item for item in todos if item.get("id") == todo_id), None)

        if todo is None:
            await interaction.response.send_message("Takový úkol jsem nenašla.")
            return

        todo["done"] = True
        todo["completed_at"] = datetime.now(timezone.utc).isoformat()
        save_todos(todos)

        await interaction.response.send_message(f"Úkol #{todo_id} je hotový.")

    except Exception:
        logging.exception("Chyba při zpracování /todo_done")
        await interaction.response.send_message(
            "Něco se pokazilo při dokončování úkolu."
        )


@bot.tree.command(
    name="search_messages",
    description="Vyhledej text v posledních zprávách kanálu."
)
@app_commands.describe(
    query="Text k vyhledání",
    limit="Kolik posledních zpráv prohledat, maximálně 300",
)
async def search_messages(
    interaction: discord.Interaction,
    query: str,
    limit: int = 100,
) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        channel = interaction.channel
        history = getattr(channel, "history", None)
        if history is None:
            await interaction.followup.send(
                "Něco se pokazilo při vyhledávání zpráv."
            )
            return

        query_lower = query.lower()
        search_limit = min(max(limit, 1), 300)
        matches = []

        async for message in history(limit=search_limit):
            if message.author.bot:
                continue

            content = message.content or ""
            content_lower = content.lower()
            if query_lower not in content_lower:
                continue

            match_index = content_lower.find(query_lower)
            start = max(match_index - 45, 0)
            end = min(match_index + len(query) + 90, len(content))
            excerpt = content[start:end].replace("\n", " ").strip()

            if start > 0:
                excerpt = "…" + excerpt
            if end < len(content):
                excerpt = excerpt + "…"

            created_at = message.created_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            author_name = getattr(message.author, "display_name", message.author.name)
            matches.append(
                f"{len(matches) + 1}. [{created_at}] {author_name}: {excerpt}"
            )

            if len(matches) >= 10:
                break

        if not matches:
            await interaction.followup.send("Nic jsem nenašla.")
            return

        answer = "Nalezené zprávy:\n" + "\n".join(matches)
        answer = shorten_for_discord(answer)

        await interaction.followup.send(answer)

    except Exception:
        logging.exception("Chyba při zpracování /search_messages")
        await interaction.followup.send(
            "Něco se pokazilo při vyhledávání zpráv."
        )


@bot.tree.command(
    name="channel_summary",
    description="Shrň poslední zprávy z aktuálního kanálu."
)
@app_commands.describe(limit="Kolik posledních zpráv shrnout, maximálně 200")
async def channel_summary(
    interaction: discord.Interaction,
    limit: int = 50,
) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        channel = interaction.channel
        history = getattr(channel, "history", None)
        if history is None:
            await interaction.followup.send(
                "Něco se pokazilo při načítání zpráv."
            )
            return

        summary_limit = min(max(limit, 1), 200)
        messages = []

        async for message in history(limit=summary_limit):
            if message.author.bot:
                continue

            content = (message.content or "").strip()
            if not content:
                continue

            created_at = message.created_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            author_name = getattr(message.author, "display_name", message.author.name)
            messages.append(
                {
                    "author": author_name,
                    "created_at": created_at,
                    "content": content,
                }
            )

        if not messages:
            await interaction.followup.send("Nemám tu co shrnout.")
            return

        messages.reverse()
        channel_text = "\n".join(
            (
                f"Autor: {message['author']}\n"
                f"Čas: {message['created_at']}\n"
                f"Text: {message['content']}"
            )
            for message in messages
        )

        answer = await summarize_channel_messages(OPENAI_MODEL, channel_text)
        await interaction.followup.send(answer)

    except Exception:
        logging.exception("Chyba při zpracování /channel_summary")
        await interaction.followup.send(
            "Něco se pokazilo při shrnování kanálu."
        )


@bot.tree.command(
    name="summary",
    description="Stručně shrň delší text."
)
@app_commands.describe(text="Text ke shrnutí")
async def summary(interaction: discord.Interaction, text: str) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        answer = await summarize_text(OPENAI_MODEL, text)
        await interaction.followup.send(answer)

    except Exception:
        logging.exception("Chyba při zpracování /summary")
        await interaction.followup.send(
            "Něco se pokazilo při shrnování textu. Mrkni do konzole na chybu."
        )


@bot.tree.command(
    name="analyze",
    description="Analyzuj přiložený obrázek pomocí AI."
)
@app_commands.describe(
    image="Obrázek k analýze",
    question="Co chceš k obrázku zjistit",
)
async def analyze(
    interaction: discord.Interaction,
    image: discord.Attachment,
    question: str = "Co je na obrázku?",
) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    filename = image.filename.lower()
    if not filename.endswith(SUPPORTED_IMAGE_EXTENSIONS):
        await interaction.response.send_message(
            "Podporuju jen obrázky .png, .jpg, .jpeg, .webp a .gif.",
            ephemeral=True,
        )
        return

    if image.size > MAX_IMAGE_SIZE_BYTES:
        await interaction.response.send_message(
            "Obrázek je moc velký. Maximum je 20 MB.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        answer = await analyze_image(OPENAI_MODEL, image.url, question)
        await interaction.followup.send(answer)

    except Exception:
        logging.exception("Chyba při zpracování /analyze")
        await interaction.followup.send(
            "Něco se pokazilo při analýze obrázku. Mrkni do konzole na chybu."
        )


@bot.tree.command(
    name="ask",
    description="Pošli otázku do OpenAI API a vrať odpověď do Discordu."
)
@app_commands.describe(question="Tvoje otázka pro AI")
async def ask(interaction: discord.Interaction, question: str) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        answer = await ask_panam(OPENAI_MODEL, question)
        await interaction.followup.send(answer)

    except Exception:
        logging.exception("Chyba při zpracování /ask")
        await interaction.followup.send(
            "Něco se pokazilo při volání AI. Mrkni do konzole na chybu."
        )


@bot.tree.command(
    name="panam_talk",
    description="Promluv si s Panam v osobnějším talk režimu."
)
@app_commands.describe(message="Zpráva pro Panam talk režim")
async def panam_talk(interaction: discord.Interaction, message: str) -> None:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        answer = await ask_panam_talk(OPENAI_MODEL, message)
        await send_followup_chunks(interaction, answer)

    except Exception:
        logging.exception("Chyba při zpracování /panam_talk")
        await interaction.followup.send(
            "Něco se pokazilo při talk režimu. Mrkni do konzole na chybu."
        )


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
