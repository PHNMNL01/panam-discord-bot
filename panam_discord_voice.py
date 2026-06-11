import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import discord


BASE_DIR = Path(__file__).resolve().parent
VOICE_RUNTIME_DIR = BASE_DIR / "runtime" / "voice"
MAX_TTS_TEXT_LENGTH = 500
DEFAULT_TTS_PROVIDER = "edge"
DEFAULT_TTS_VOICE = "cs-CZ-VlastaNeural"
DEFAULT_TTS_RATE = "+0%"
DEFAULT_TTS_VOLUME = "+0%"

logger = logging.getLogger("panam")


class VoiceCommandUserError(Exception):
    pass


def _safe_voice_context(interaction: discord.Interaction) -> dict[str, int | None]:
    user = getattr(interaction, "user", None)
    return {
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "user_id": getattr(user, "id", None),
    }


def _log_voice_command(
    command_name: str,
    status: str,
    interaction: discord.Interaction,
    **details,
) -> None:
    context = _safe_voice_context(interaction)
    detail_text = " ".join(
        f"{key}={value}"
        for key, value in details.items()
        if value is not None
    )
    logger.info(
        "voice_command=%s status=%s guild_id=%s channel_id=%s user_id=%s%s",
        command_name,
        status,
        context["guild_id"],
        context["channel_id"],
        context["user_id"],
        f" {detail_text}" if detail_text else "",
    )


def _get_voice_channel(interaction: discord.Interaction) -> discord.abc.Connectable:
    user_voice = getattr(interaction.user, "voice", None)
    channel = getattr(user_voice, "channel", None)

    if channel is None:
        raise VoiceCommandUserError("Nejdřív se připoj do voice kanálu.")

    return channel


def _get_voice_client(interaction: discord.Interaction) -> discord.VoiceClient | None:
    guild = interaction.guild
    if guild is None:
        return None

    voice_client = guild.voice_client
    if isinstance(voice_client, discord.VoiceClient):
        return voice_client

    return None


async def _connect_or_move_to_user_channel(
    interaction: discord.Interaction,
) -> discord.VoiceClient:
    guild = interaction.guild
    if guild is None:
        raise VoiceCommandUserError("Voice command musí běžet na Discord serveru.")

    target_channel = _get_voice_channel(interaction)
    voice_client = _get_voice_client(interaction)

    if voice_client is not None and voice_client.is_connected():
        if voice_client.channel != target_channel:
            await voice_client.move_to(target_channel)
        return voice_client

    connected_client = await target_channel.connect(
        timeout=10.0,
        reconnect=True,
        self_deaf=True,
    )
    if not isinstance(connected_client, discord.VoiceClient):
        raise VoiceCommandUserError("Nepodařilo se vytvořit Discord voice client.")

    return connected_client


def _powershell_executable() -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        raise VoiceCommandUserError(
            "TTS v1 potřebuje PowerShell se System.Speech pro vytvoření audio souboru."
        )
    return executable


def _powershell_string(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _get_tts_settings() -> dict[str, str]:
    return {
        "provider": (os.getenv("PANAM_TTS_PROVIDER") or DEFAULT_TTS_PROVIDER).strip().lower(),
        "voice": (os.getenv("PANAM_TTS_VOICE") or DEFAULT_TTS_VOICE).strip(),
        "rate": (os.getenv("PANAM_TTS_RATE") or DEFAULT_TTS_RATE).strip(),
        "volume": (os.getenv("PANAM_TTS_VOLUME") or DEFAULT_TTS_VOLUME).strip(),
    }


def _create_tts_wav_file(text: str) -> Path:
    VOICE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    temp_text = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="panam_tts_",
        dir=VOICE_RUNTIME_DIR,
        encoding="utf-8",
        delete=False,
    )
    text_path = Path(temp_text.name)
    temp_text.write(text)
    temp_text.close()

    audio_file = tempfile.NamedTemporaryFile(
        suffix=".wav",
        prefix="panam_tts_",
        dir=VOICE_RUNTIME_DIR,
        delete=False,
    )
    audio_path = Path(audio_file.name)
    audio_file.close()

    script = (
        f"$TextPath = {_powershell_string(text_path)}; "
        f"$OutputPath = {_powershell_string(audio_path)}; "
        "Add-Type -AssemblyName System.Speech; "
        "$text = Get-Content -LiteralPath $TextPath -Raw; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$synth.SetOutputToWaveFile($OutputPath); "
        "$synth.Speak($text); "
        "$synth.Dispose(); "
    )

    try:
        subprocess.run(
            [
                _powershell_executable(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        audio_path.unlink(missing_ok=True)
        raise VoiceCommandUserError("Nepodařilo se vytvořit TTS audio soubor.") from error
    finally:
        text_path.unlink(missing_ok=True)

    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        audio_path.unlink(missing_ok=True)
        raise VoiceCommandUserError("TTS vytvorilo prazdny audio soubor.")

    return audio_path


async def _create_tts_wav_file_async(text: str) -> Path:
    return await asyncio.to_thread(_create_tts_wav_file, text)


async def _create_edge_tts_audio_file(text: str, settings: dict[str, str]) -> Path:
    VOICE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    audio_file = tempfile.NamedTemporaryFile(
        suffix=".mp3",
        prefix="panam_tts_",
        dir=VOICE_RUNTIME_DIR,
        delete=False,
    )
    audio_path = Path(audio_file.name)
    audio_file.close()

    try:
        import edge_tts
    except ImportError as error:
        audio_path.unlink(missing_ok=True)
        raise VoiceCommandUserError(
            "edge-tts neni nainstalovane. Spust python -m pip install -r requirements.txt."
        ) from error

    try:
        communicate = edge_tts.Communicate(
            text,
            voice=settings["voice"],
            rate=settings["rate"],
            volume=settings["volume"],
        )
        await communicate.save(str(audio_path))
    except Exception as error:
        audio_path.unlink(missing_ok=True)
        raise VoiceCommandUserError(
            "Nepodarilo se vytvorit edge TTS audio. Zkontroluj edge-tts, internet a nastaveni hlasu."
        ) from error

    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        audio_path.unlink(missing_ok=True)
        raise VoiceCommandUserError("edge TTS vytvorilo prazdny audio soubor.")

    return audio_path


async def _create_tts_audio_file(text: str, settings: dict[str, str]) -> Path:
    provider = settings["provider"]
    if provider == "edge":
        return await _create_edge_tts_audio_file(text, settings)

    if provider in {"system", "windows", "system_speech"}:
        return await _create_tts_wav_file_async(text)

    raise VoiceCommandUserError(
        "Neznamy TTS provider. Podporovane hodnoty jsou edge nebo system."
    )


def _cleanup_audio_file(audio_path: Path) -> None:
    try:
        audio_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("voice_tts cleanup failed path=%s", audio_path.name)


def truncate_text_for_voice(text: str, limit: int = MAX_TTS_TEXT_LENGTH) -> str:
    clean_text = str(text or "").strip()
    if len(clean_text) <= limit:
        return clean_text

    if limit <= 3:
        return clean_text[:limit]

    return clean_text[: limit - 3].rstrip() + "..."


async def play_tts_text(
    interaction: discord.Interaction,
    text: str,
    command_name: str,
    *,
    original_text_length: int | None = None,
) -> bool:
    clean_text = str(text or "").strip()
    text_length = original_text_length if original_text_length is not None else len(clean_text)
    settings = _get_tts_settings()
    provider = settings["provider"]
    voice = settings["voice"]

    try:
        voice_client = await _connect_or_move_to_user_channel(interaction)
        audio_path = await _create_tts_audio_file(clean_text, settings)
    except VoiceCommandUserError as error:
        _log_voice_command(
            command_name,
            "error",
            interaction,
            provider=provider,
            voice=voice,
            text_length=text_length,
        )
        await interaction.followup.send(str(error), ephemeral=True)
        return False
    except Exception as error:
        _log_voice_command(
            command_name,
            "error",
            interaction,
            provider=provider,
            voice=voice,
            text_length=text_length,
        )
        logger.warning(
            "%s setup failed provider=%s voice=%s error_type=%s",
            command_name,
            provider,
            voice,
            type(error).__name__,
        )
        await interaction.followup.send(
            "Nepodarilo se pripravit voice audio.",
            ephemeral=True,
        )
        return False

    if voice_client.is_playing():
        voice_client.stop()

    def after_playback(error: Exception | None) -> None:
        _cleanup_audio_file(audio_path)
        if error is not None:
            logger.error("%s playback error=%s", command_name, error)

    try:
        voice_client.play(
            discord.FFmpegPCMAudio(str(audio_path)),
            after=after_playback,
        )
    except Exception:
        _cleanup_audio_file(audio_path)
        _log_voice_command(
            command_name,
            "error",
            interaction,
            provider=provider,
            voice=voice,
            text_length=text_length,
        )
        logger.exception("%s playback start failed", command_name)
        await interaction.followup.send(
            "Nepodarilo se prehrat voice audio. Je dostupny FFmpeg?",
            ephemeral=True,
        )
        return False

    _log_voice_command(
        command_name,
        "success",
        interaction,
        provider=provider,
        voice=voice,
        text_length=text_length,
    )
    return True


async def handle_voice_join_command(interaction: discord.Interaction) -> None:
    command_name = "voice_join"
    _log_voice_command(command_name, "started", interaction)

    try:
        voice_client = await _connect_or_move_to_user_channel(interaction)
    except VoiceCommandUserError as error:
        _log_voice_command(command_name, "error", interaction)
        await interaction.response.send_message(str(error), ephemeral=True)
        return
    except Exception:
        _log_voice_command(command_name, "error", interaction)
        logger.exception("voice_join failed")
        await interaction.response.send_message(
            "Nepodařilo se připojit do voice kanálu.",
            ephemeral=True,
        )
        return

    channel_name = getattr(voice_client.channel, "name", "voice kanál")
    _log_voice_command(command_name, "success", interaction)
    await interaction.response.send_message(
        f"Jsem ve voice kanálu: {channel_name}.",
        ephemeral=True,
    )


async def handle_voice_leave_command(interaction: discord.Interaction) -> None:
    command_name = "voice_leave"
    _log_voice_command(command_name, "started", interaction)

    voice_client = _get_voice_client(interaction)
    if voice_client is None or not voice_client.is_connected():
        _log_voice_command(command_name, "success", interaction)
        await interaction.response.send_message(
            "Nejsem připojená ve voice kanálu.",
            ephemeral=True,
        )
        return

    try:
        await voice_client.disconnect(force=False)
    except Exception:
        _log_voice_command(command_name, "error", interaction)
        logger.exception("voice_leave failed")
        await interaction.response.send_message(
            "Nepodařilo se odpojit z voice kanálu.",
            ephemeral=True,
        )
        return

    _log_voice_command(command_name, "success", interaction)
    await interaction.response.send_message("Odpojila jsem se.", ephemeral=True)


async def handle_voice_say_command(interaction: discord.Interaction, text: str) -> None:
    command_name = "voice_say"
    text_length = len(text or "")
    settings = _get_tts_settings()
    _log_voice_command(
        command_name,
        "started",
        interaction,
        provider=settings["provider"],
        voice=settings["voice"],
        text_length=text_length,
    )

    clean_text = str(text or "").strip()
    if not clean_text:
        _log_voice_command(
            command_name,
            "error",
            interaction,
            provider=settings["provider"],
            voice=settings["voice"],
            text_length=0,
        )
        await interaction.response.send_message(
            "Text pro voice_say nesmi byt prazdny.",
            ephemeral=True,
        )
        return

    if len(clean_text) > MAX_TTS_TEXT_LENGTH:
        _log_voice_command(
            command_name,
            "error",
            interaction,
            provider=settings["provider"],
            voice=settings["voice"],
            text_length=text_length,
        )
        await interaction.response.send_message(
            f"Text je moc dlouhy. Limit pro speak v1 je {MAX_TTS_TEXT_LENGTH} znaku.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    played = await play_tts_text(
        interaction,
        clean_text,
        command_name,
        original_text_length=text_length,
    )
    if not played:
        return

    await interaction.followup.send("Prehravam ve voice kanalu.", ephemeral=True)


async def _handle_voice_say_command_legacy(interaction: discord.Interaction, text: str) -> None:
    command_name = "voice_say"
    text_length = len(text or "")
    _log_voice_command(command_name, "started", interaction, text_length=text_length)

    clean_text = str(text or "").strip()
    if not clean_text:
        _log_voice_command(command_name, "error", interaction, text_length=0)
        await interaction.response.send_message(
            "Text pro voice_say nesmí být prázdný.",
            ephemeral=True,
        )
        return

    if len(clean_text) > MAX_TTS_TEXT_LENGTH:
        _log_voice_command(command_name, "error", interaction, text_length=text_length)
        await interaction.response.send_message(
            f"Text je moc dlouhý. Limit pro speak v1 je {MAX_TTS_TEXT_LENGTH} znaků.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        voice_client = await _connect_or_move_to_user_channel(interaction)
        audio_path = await _create_tts_wav_file_async(clean_text)
    except VoiceCommandUserError as error:
        _log_voice_command(command_name, "error", interaction, text_length=text_length)
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        _log_voice_command(command_name, "error", interaction, text_length=text_length)
        logger.exception("voice_say setup failed")
        await interaction.followup.send(
            "Nepodařilo se připravit voice audio.",
            ephemeral=True,
        )
        return

    if voice_client.is_playing():
        voice_client.stop()

    def after_playback(error: Exception | None) -> None:
        _cleanup_audio_file(audio_path)
        if error is not None:
            logger.error("voice_say playback error=%s", error)

    try:
        voice_client.play(
            discord.FFmpegPCMAudio(str(audio_path)),
            after=after_playback,
        )
    except Exception:
        _cleanup_audio_file(audio_path)
        _log_voice_command(command_name, "error", interaction, text_length=text_length)
        logger.exception("voice_say playback start failed")
        await interaction.followup.send(
            "Nepodařilo se přehrát voice audio. Je dostupný FFmpeg?",
            ephemeral=True,
        )
        return

    _log_voice_command(command_name, "success", interaction, text_length=text_length)
    await interaction.followup.send("Přehrávám ve voice kanálu.", ephemeral=True)
