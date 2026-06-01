import logging
from pathlib import Path

import discord

from panam_discord_attachment_analysis import (
    AttachmentAnalysisUserError,
    analyze_selected_attachment,
)
from panam_discord_context import log_attachment_info, mark_command_status
from panam_discord_history import find_recent_supported_attachment
from panam_file_context import set_last_file_context
from panam_text_extraction import get_file_extension


logger = logging.getLogger("panam")


async def handle_analyze_command(
    model: str,
    interaction: discord.Interaction,
    file: discord.Attachment | None = None,
    question: str = "Popiš, co je v příloze.",
) -> None:
    await interaction.response.defer(thinking=True)

    selected_file = file
    if selected_file is None:
        selected_file = await find_recent_supported_attachment(interaction.channel)

    if selected_file is None:
        await interaction.followup.send(
            "Nevidím žádnou podporovanou přílohu ani v commandu, ani v předchozí zprávě."
        )
        return

    log_attachment_info("slash_command", "analyze", interaction, selected_file)

    try:
        answer = await analyze_selected_attachment(model, selected_file, question)
        await interaction.followup.send(answer)
        set_last_file_context(
            interaction.channel_id,
            Path(selected_file.filename).name,
            get_file_extension(selected_file.filename),
            "chat_answer",
            file_summary=answer,
        )

    except AttachmentAnalysisUserError as error:
        await interaction.followup.send(str(error))

    except Exception:
        mark_command_status(interaction, "error")
        logger.exception("Chyba při zpracování /analyze")
        await interaction.followup.send(
            "Něco se pokazilo při analýze přílohy. Mrkni do konzole na chybu."
        )
