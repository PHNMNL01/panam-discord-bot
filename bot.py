import os
import logging
import logging.handlers
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from panam_discord_analyze_command import handle_analyze_command
from panam_discord_basic_commands import (
    handle_help_command,
    handle_ping_command,
)
from panam_discord_channel_commands import (
    handle_channel_summary_command,
    handle_search_messages_command,
)
from panam_discord_context import (
    log_action,
    log_slash_command,
    mark_command_status,
)
from panam_discord_core_commands import (
    handle_ask_command,
    handle_note_add_command,
    handle_note_list_command,
    handle_note_search_command,
    handle_panam_talk_command,
    handle_summary_command,
    handle_todo_add_command,
    handle_todo_done_command,
    handle_todo_list_command,
)
from panam_discord_file_commands import (
    handle_extract_data_command,
    handle_process_file_command,
    handle_transform_docx_command,
    handle_transform_excel_command,
)
from panam_discord_file_job_test import handle_file_job_test_command
from panam_discord_memory_command import handle_memory_clear_command
from panam_discord_message_router import handle_discord_message
from panam_discord_read_file import handle_read_file_command


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "panam.log"

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
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
logger = logging.getLogger("panam")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if not any(
        isinstance(handler, logging.handlers.RotatingFileHandler)
        and Path(handler.baseFilename) == LOG_FILE
        for handler in root_logger.handlers
    ):
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def is_interaction_allowed(interaction: discord.Interaction, command_name: str) -> bool:
    if ALLOWED_CHANNEL_IDS and str(interaction.channel_id) not in ALLOWED_CHANNEL_IDS:
        log_action("slash_command", command_name, "denied", interaction)
        mark_command_status(interaction, "denied")
        return False

    return True


setup_logging()
logger.info("Panam bot startuje.")


if not DISCORD_BOT_TOKEN:
    raise RuntimeError("Chybí DISCORD_BOT_TOKEN v .env souboru.")


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

            logger.info(
                "Slash commandy synchronizovány pro %s serverů.",
                len(DISCORD_GUILD_IDS),
            )
        else:
            await self.tree.sync()
            logger.info("Slash commandy synchronizovány globálně.")

    async def on_ready(self) -> None:
        if self.user is None:
            logger.info("Bot je online, ale user zatím není dostupný.")
            return

        logger.info("Bot je online jako %s | ID: %s", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if self.user is None:
            return

        await handle_discord_message(
            OPENAI_MODEL,
            message,
            self.user,
            ALLOWED_CHANNEL_IDS,
        )

bot = DiscordAIBot()


@bot.tree.command(
    name="ping",
    description="Ověř, že je bot online."
)
@log_slash_command("ping")
async def ping(interaction: discord.Interaction) -> None:
    if not is_interaction_allowed(interaction, "ping"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_ping_command(interaction)


@bot.tree.command(
    name="help",
    description="Zobraz dostupné commandy."
)
@log_slash_command("help")
async def help_command(interaction: discord.Interaction) -> None:
    if not is_interaction_allowed(interaction, "help"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_help_command(interaction)


@bot.tree.command(
    name="memory_clear",
    description="Vymaž krátkou konverzační paměť Panam pro tento kanál."
)
@log_slash_command("memory_clear")
async def memory_clear(interaction: discord.Interaction) -> None:
    channel_id = interaction.channel_id
    if channel_id is None:
        await interaction.response.send_message(
            "Tenhle command potřebuje běžet v kanálu.",
            ephemeral=True,
        )
        return

    if not is_interaction_allowed(interaction, "memory_clear"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_memory_clear_command(
        interaction,
        channel_id,
    )


@bot.tree.command(
    name="note_add",
    description="Ulož krátkou poznámku."
)
@app_commands.describe(text="Text poznámky")
@log_slash_command("note_add")
async def note_add(interaction: discord.Interaction, text: str) -> None:
    if not is_interaction_allowed(interaction, "note_add"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_note_add_command(interaction, text)


@bot.tree.command(
    name="note_list",
    description="Vypiš poslední poznámky."
)
@log_slash_command("note_list")
async def note_list(interaction: discord.Interaction) -> None:
    if not is_interaction_allowed(interaction, "note_list"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_note_list_command(interaction)


@bot.tree.command(
    name="note_search",
    description="Vyhledej uložené poznámky."
)
@app_commands.describe(query="Text k vyhledání")
@log_slash_command("note_search")
async def note_search(interaction: discord.Interaction, query: str) -> None:
    if not is_interaction_allowed(interaction, "note_search"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_note_search_command(interaction, query)


@bot.tree.command(
    name="todo_add",
    description="Přidej úkol do todo listu."
)
@app_commands.describe(text="Text úkolu")
@log_slash_command("todo_add")
async def todo_add(interaction: discord.Interaction, text: str) -> None:
    if not is_interaction_allowed(interaction, "todo_add"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_todo_add_command(interaction, text)


@bot.tree.command(
    name="todo_list",
    description="Vypiš aktivní úkoly."
)
@log_slash_command("todo_list")
async def todo_list(interaction: discord.Interaction) -> None:
    if not is_interaction_allowed(interaction, "todo_list"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_todo_list_command(interaction)


@bot.tree.command(
    name="todo_done",
    description="Označ úkol jako hotový."
)
@app_commands.describe(todo_id="ID úkolu")
@log_slash_command("todo_done")
async def todo_done(interaction: discord.Interaction, todo_id: int) -> None:
    if not is_interaction_allowed(interaction, "todo_done"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_todo_done_command(interaction, todo_id)


@bot.tree.command(
    name="search_messages",
    description="Vyhledej text v posledních zprávách kanálu."
)
@app_commands.describe(
    query="Text k vyhledání",
    limit="Kolik posledních zpráv prohledat, maximálně 300",
)
@log_slash_command("search_messages")
async def search_messages(
    interaction: discord.Interaction,
    query: str,
    limit: int = 100,
) -> None:
    if not is_interaction_allowed(interaction, "search_messages"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_search_messages_command(
        interaction,
        query,
        limit,
    )


@bot.tree.command(
    name="channel_summary",
    description="Shrň poslední zprávy z aktuálního kanálu."
)
@app_commands.describe(limit="Kolik posledních zpráv shrnout, maximálně 200")
@log_slash_command("channel_summary")
async def channel_summary(
    interaction: discord.Interaction,
    limit: int = 50,
) -> None:
    if not is_interaction_allowed(interaction, "channel_summary"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_channel_summary_command(
        OPENAI_MODEL,
        interaction,
        limit,
    )


@bot.tree.command(
    name="summary",
    description="Stručně shrň delší text."
)
@app_commands.describe(text="Text ke shrnutí")
@log_slash_command("summary")
async def summary(interaction: discord.Interaction, text: str) -> None:
    if not is_interaction_allowed(interaction, "summary"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_summary_command(OPENAI_MODEL, interaction, text)


@bot.tree.command(
    name="analyze",
    description="Analyzuj přiložený obrázek nebo dokument pomocí AI."
)
@app_commands.describe(
    file="Soubor k analýze",
    question="Co chceš k souboru zjistit",
)
@log_slash_command("analyze")
async def analyze(
    interaction: discord.Interaction,
    file: discord.Attachment | None = None,
    question: str = "Popiš, co je v příloze.",
) -> None:
    if not is_interaction_allowed(interaction, "analyze"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_analyze_command(
        OPENAI_MODEL,
        interaction,
        file,
        question,
    )


@bot.tree.command(
    name="read_file",
    description="Přečti TXT, MD, CSV, JSON, PDF, DOCX nebo XLSX přílohu pomocí AI."
)
@app_commands.describe(
    file="Dokument k přečtení",
    question="Co chceš k dokumentu zjistit",
)
@log_slash_command("read_file")
async def read_file(
    interaction: discord.Interaction,
    file: discord.Attachment,
    question: str = "Shrň mi tento dokument.",
) -> None:
    if not is_interaction_allowed(interaction, "read_file"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_read_file_command(
        OPENAI_MODEL,
        interaction,
        file,
        question,
    )


@bot.tree.command(
    name="file_job_test",
    description="Otestuj docasnou file-job pipeline na dokumentove priloze."
)
@app_commands.describe(
    file="Dokument k otestovani pipeline",
    output_format="md nebo txt",
)
@log_slash_command("file_job_test")
async def file_job_test(
    interaction: discord.Interaction,
    file: discord.Attachment,
    output_format: str = "md",
) -> None:
    if not is_interaction_allowed(interaction, "file_job_test"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    await handle_file_job_test_command(
        interaction,
        file,
        output_format,
    )


@bot.tree.command(
    name="transform_docx",
    description="Bezpecne vytvor novy cisty DOCX podle podporovane upravy."
)
@app_commands.describe(
    file="DOCX soubor k uprave",
    instruction="Jednoducha podporovana uprava dokumentu",
)
@log_slash_command("transform_docx")
async def transform_docx(
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
) -> None:
    if not is_interaction_allowed(interaction, "transform_docx"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    await handle_transform_docx_command(
        OPENAI_MODEL,
        interaction,
        file,
        instruction,
    )


@bot.tree.command(
    name="transform_excel",
    description="Bezpecne uprav XLSX podle jednoduche instrukce a vrat novy soubor."
)
@app_commands.describe(
    file="XLSX soubor k uprave",
    instruction="Jednoducha deterministicka uprava Excelu",
)
@log_slash_command("transform_excel")
async def transform_excel(
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
) -> None:
    if not is_interaction_allowed(interaction, "transform_excel"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    await handle_transform_excel_command(
        interaction,
        file,
        instruction,
    )


@bot.tree.command(
    name="process_file",
    description="Zpracuj dokument podle instrukce a vrat vystup jako soubor."
)
@app_commands.describe(
    file="Dokument ke zpracovani",
    instruction="Co ma Panam s dokumentem udelat",
    output_format="md, txt nebo docx",
)
@log_slash_command("process_file")
async def process_file(
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
    output_format: str = "md",
) -> None:
    if not is_interaction_allowed(interaction, "process_file"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    await handle_process_file_command(
        OPENAI_MODEL,
        interaction,
        file,
        instruction,
        output_format,
    )


@bot.tree.command(
    name="extract_data",
    description="Vytez ze souboru strukturovana data jako JSON, CSV, Markdown nebo XLSX."
)
@app_commands.describe(
    file="Dokument pro tezeni dat",
    instruction="Jaka data ma Panam vytahnout",
    output_format="json, csv, md nebo xlsx",
)
@log_slash_command("extract_data")
async def extract_data(
    interaction: discord.Interaction,
    file: discord.Attachment,
    instruction: str,
    output_format: str = "json",
) -> None:
    if not is_interaction_allowed(interaction, "extract_data"):
        await interaction.response.send_message(
            "Tady nemam povolene odpovidat.",
            ephemeral=True,
        )
        return

    await handle_extract_data_command(
        OPENAI_MODEL,
        interaction,
        file,
        instruction,
        output_format,
    )


@bot.tree.command(
    name="ask",
    description="Pošli otázku do OpenAI API a vrať odpověď do Discordu."
)
@app_commands.describe(question="Tvoje otázka pro AI")
@log_slash_command("ask")
async def ask(interaction: discord.Interaction, question: str) -> None:
    if not is_interaction_allowed(interaction, "ask"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_ask_command(OPENAI_MODEL, interaction, question)


@bot.tree.command(
    name="panam_talk",
    description="Promluv si s Panam v osobnějším talk režimu."
)
@app_commands.describe(message="Zpráva pro Panam talk režim")
@log_slash_command("panam_talk")
async def panam_talk(interaction: discord.Interaction, message: str) -> None:
    if not is_interaction_allowed(interaction, "panam_talk"):
        await interaction.response.send_message(
            "Tady nemám povolené odpovídat.",
            ephemeral=True,
        )
        return

    await handle_panam_talk_command(OPENAI_MODEL, interaction, message)


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
