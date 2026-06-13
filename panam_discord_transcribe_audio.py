import asyncio
import logging
import tempfile
from pathlib import Path

import discord

from panam_stt import SttUserError, get_stt_settings, transcribe_audio_file


BASE_DIR = Path(__file__).resolve().parent
TRANSCRIBE_RUNTIME_DIR = BASE_DIR / "runtime" / "transcribe_audio"
MAX_TRANSCRIBE_AUDIO_BYTES = 20 * 1024 * 1024
SUPPORTED_TRANSCRIBE_AUDIO_EXTENSIONS = {
    ".m4a",
    ".mp3",
    ".wav",
    ".webm",
    ".ogg",
    ".flac",
}

logger = logging.getLogger("panam")


def _safe_context(interaction: discord.Interaction) -> dict[str, int | None]:
    user = getattr(interaction, "user", None)
    return {
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "user_id": getattr(user, "id", None),
    }


def _audio_extension(file: discord.Attachment) -> str:
    return Path(file.filename or "").suffix.lower()


def log_transcribe_audio_status(
    status: str,
    interaction: discord.Interaction,
    *,
    provider: str,
    model_id: str,
    language_code: str,
    extension: str,
    file_size_bytes: int | None,
    transcript_length: int | None,
) -> None:
    context = _safe_context(interaction)
    logger.info(
        (
            "action=transcribe_audio status=%s provider=%s model_id=%s language_code=%s "
            "extension=%s file_size_bytes=%s transcript_length=%s "
            "guild_id=%s channel_id=%s user_id=%s"
        ),
        status,
        provider,
        model_id,
        language_code,
        extension,
        file_size_bytes,
        transcript_length,
        context["guild_id"],
        context["channel_id"],
        context["user_id"],
    )


async def _send_transcript(interaction: discord.Interaction, transcript: str) -> None:
    prefix = "Prepis:\n"
    max_chunk = 1900
    if len(prefix) + len(transcript) <= max_chunk:
        await interaction.followup.send(prefix + transcript)
        return

    first_chunk_size = max_chunk - len(prefix)
    await interaction.followup.send(prefix + transcript[:first_chunk_size])
    offset = first_chunk_size
    while offset < len(transcript):
        await interaction.followup.send(transcript[offset:offset + max_chunk])
        offset += max_chunk


async def handle_transcribe_audio_command(
    interaction: discord.Interaction,
    file: discord.Attachment,
) -> None:
    settings = get_stt_settings()
    provider = settings["provider"]
    model_id = settings["elevenlabs_stt_model_id"]
    language_code = settings["elevenlabs_stt_language_code"]
    extension = _audio_extension(file)
    file_size_bytes = getattr(file, "size", None)

    def log_status(status: str, *, transcript_length: int | None = None) -> None:
        log_transcribe_audio_status(
            status,
            interaction,
            provider=provider,
            model_id=model_id,
            language_code=language_code,
            extension=extension,
            file_size_bytes=file_size_bytes,
            transcript_length=transcript_length,
        )

    log_status("started")

    if extension not in SUPPORTED_TRANSCRIBE_AUDIO_EXTENSIONS:
        log_status("error")
        await interaction.response.send_message(
            "Podporovane audio prilohy jsou: .m4a, .mp3, .wav, .webm, .ogg, .flac.",
            ephemeral=True,
        )
        return

    if file_size_bytes is not None and file_size_bytes > MAX_TRANSCRIBE_AUDIO_BYTES:
        log_status("error")
        await interaction.response.send_message(
            "Audio priloha je moc velka. Maximum je 20 MB.",
            ephemeral=True,
        )
        return

    if not settings["elevenlabs_api_key"]:
        log_status("error")
        await interaction.response.send_message(
            "ElevenLabs STT potrebuje ELEVENLABS_API_KEY v .env.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    TRANSCRIBE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        suffix=extension,
        prefix="panam_transcribe_audio_",
        dir=TRANSCRIBE_RUNTIME_DIR,
        delete=False,
    )
    audio_path = Path(temp_file.name)
    temp_file.close()

    try:
        await file.save(str(audio_path))
        file_size_bytes = audio_path.stat().st_size
        if file_size_bytes > MAX_TRANSCRIBE_AUDIO_BYTES:
            log_status("error")
            await interaction.followup.send(
                "Audio priloha je moc velka. Maximum je 20 MB.",
                ephemeral=True,
            )
            return
        transcript, settings = await asyncio.to_thread(transcribe_audio_file, audio_path)
        provider = settings["provider"]
        model_id = settings["elevenlabs_stt_model_id"]
        language_code = settings["elevenlabs_stt_language_code"]
    except SttUserError as error:
        log_status("error", transcript_length=0)
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        log_status("error", transcript_length=0)
        logger.exception("transcribe_audio failed")
        await interaction.followup.send(
            "Nepodarilo se prepsat audio prilohu.",
            ephemeral=True,
        )
        return
    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("transcribe_audio cleanup_failed")

    transcript_length = len(transcript)
    if not transcript:
        log_status("success", transcript_length=0)
        await interaction.followup.send(
            "Audio se nepodarilo prepsat.",
            ephemeral=True,
        )
        return

    log_status("success", transcript_length=transcript_length)
    await _send_transcript(interaction, transcript)
