import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import discord


BASE_DIR = Path(__file__).resolve().parent
VOICE_RUNTIME_DIR = BASE_DIR / "runtime" / "voice"
MAX_TTS_TEXT_LENGTH = 500
LISTEN_TEST_MIN_SECONDS = 1
LISTEN_TEST_MAX_SECONDS = 10
DEFAULT_TTS_PROVIDER = "edge"
DEFAULT_TTS_VOICE = "cs-CZ-VlastaNeural"
DEFAULT_TTS_RATE = "+0%"
DEFAULT_TTS_VOLUME = "+0%"
DEFAULT_ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_ELEVENLABS_OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_ELEVENLABS_STABILITY = 0.45
DEFAULT_ELEVENLABS_SIMILARITY_BOOST = 0.75
DEFAULT_ELEVENLABS_STYLE = 0.25
DEFAULT_ELEVENLABS_USE_SPEAKER_BOOST = True

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


def log_listen_test_status(
    status: str,
    interaction: discord.Interaction,
    *,
    seconds: int,
    received_audio: bool,
) -> None:
    context = _safe_voice_context(interaction)
    logger.info(
        "action=listen_test status=%s seconds=%s guild_id=%s channel_id=%s user_id=%s received_audio=%s",
        status,
        seconds,
        context["guild_id"],
        context["channel_id"],
        context["user_id"],
        str(received_audio).lower(),
    )


def _get_voice_channel(interaction: discord.Interaction) -> discord.abc.Connectable:
    return _get_voice_channel_for_user(interaction.user)


def _get_voice_channel_for_user(user) -> discord.abc.Connectable:
    user_voice = getattr(user, "voice", None)
    channel = getattr(user_voice, "channel", None)

    if channel is None:
        raise VoiceCommandUserError("Nejdřív se připoj do voice kanálu.")

    return channel


def _get_voice_client(interaction: discord.Interaction) -> discord.VoiceClient | None:
    return _get_voice_client_for_guild(interaction.guild)


def _get_voice_client_for_guild(guild) -> discord.VoiceClient | None:
    if guild is None:
        return None

    voice_client = guild.voice_client
    if isinstance(voice_client, discord.VoiceClient):
        return voice_client

    return None


async def _connect_or_move_to_user_channel(
    interaction: discord.Interaction,
) -> discord.VoiceClient:
    return await _connect_or_move_to_user_voice(interaction.guild, interaction.user)


async def _connect_or_move_to_user_voice(
    guild,
    user,
) -> discord.VoiceClient:
    if guild is None:
        raise VoiceCommandUserError("Voice command musí běžet na Discord serveru.")

    target_channel = _get_voice_channel_for_user(user)
    voice_client = _get_voice_client_for_guild(guild)

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


async def _connect_or_move_to_user_voice_recv(interaction: discord.Interaction):
    try:
        from discord.ext import voice_recv
    except ImportError as error:
        raise VoiceCommandUserError(
            "listen_test potrebuje discord-ext-voice-recv. Spust python -m pip install -r requirements.txt."
        ) from error

    guild = interaction.guild
    if guild is None:
        raise VoiceCommandUserError("Voice command musi bezet na Discord serveru.")

    target_channel = _get_voice_channel(interaction)
    voice_client = _get_voice_client(interaction)
    if voice_client is not None and voice_client.is_connected():
        if voice_client.is_playing():
            raise VoiceCommandUserError(
                "Nejdriv nech Panam domluvit. listen_test se nespusti, kdyz bot prehrava audio."
            )

        if hasattr(voice_client, "is_listening") and voice_client.is_listening():
            raise VoiceCommandUserError("listen_test uz prave bezi.")

        if not isinstance(voice_client, voice_recv.VoiceRecvClient):
            await voice_client.disconnect(force=False)
            return await target_channel.connect(
                cls=voice_recv.VoiceRecvClient,
                timeout=10.0,
                reconnect=True,
                self_deaf=False,
            )

        if voice_client.channel != target_channel:
            await voice_client.move_to(target_channel)
        return voice_client

    connected_client = await target_channel.connect(
        cls=voice_recv.VoiceRecvClient,
        timeout=10.0,
        reconnect=True,
        self_deaf=False,
    )
    if not isinstance(connected_client, voice_recv.VoiceRecvClient):
        raise VoiceCommandUserError("Nepodarilo se vytvorit Discord voice receive client.")

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


def _parse_float_env(name: str, default: float) -> float:
    value = (os.getenv(name) or "").strip()
    if not value:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def _parse_bool_env(name: str, default: bool) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default

    return value in {"1", "true", "yes", "on"}


def _mask_secretish_value(value: str | None) -> str | None:
    clean_value = str(value or "").strip()
    if not clean_value:
        return None
    if len(clean_value) <= 8:
        return "*" * len(clean_value)
    return f"{clean_value[:4]}...{clean_value[-4:]}"


def _get_tts_settings() -> dict[str, str | float | bool]:
    return {
        "provider": (os.getenv("PANAM_TTS_PROVIDER") or DEFAULT_TTS_PROVIDER).strip().lower(),
        "voice": (os.getenv("PANAM_TTS_VOICE") or DEFAULT_TTS_VOICE).strip(),
        "rate": (os.getenv("PANAM_TTS_RATE") or DEFAULT_TTS_RATE).strip(),
        "volume": (os.getenv("PANAM_TTS_VOLUME") or DEFAULT_TTS_VOLUME).strip(),
        "elevenlabs_api_key": (os.getenv("ELEVENLABS_API_KEY") or "").strip(),
        "elevenlabs_voice_id": (os.getenv("ELEVENLABS_VOICE_ID") or "").strip(),
        "elevenlabs_model_id": (
            os.getenv("ELEVENLABS_MODEL_ID") or DEFAULT_ELEVENLABS_MODEL_ID
        ).strip(),
        "elevenlabs_output_format": (
            os.getenv("ELEVENLABS_OUTPUT_FORMAT") or DEFAULT_ELEVENLABS_OUTPUT_FORMAT
        ).strip(),
        "elevenlabs_stability": _parse_float_env(
            "ELEVENLABS_STABILITY",
            DEFAULT_ELEVENLABS_STABILITY,
        ),
        "elevenlabs_similarity_boost": _parse_float_env(
            "ELEVENLABS_SIMILARITY_BOOST",
            DEFAULT_ELEVENLABS_SIMILARITY_BOOST,
        ),
        "elevenlabs_style": _parse_float_env("ELEVENLABS_STYLE", DEFAULT_ELEVENLABS_STYLE),
        "elevenlabs_use_speaker_boost": _parse_bool_env(
            "ELEVENLABS_USE_SPEAKER_BOOST",
            DEFAULT_ELEVENLABS_USE_SPEAKER_BOOST,
        ),
    }


def _tts_log_details(
    settings: dict[str, str | float | bool],
    text_length: int,
) -> dict[str, str | int | None]:
    provider = str(settings["provider"])
    if provider == "elevenlabs":
        return {
            "provider": provider,
            "model_id": str(settings["elevenlabs_model_id"]),
            "voice_id": _mask_secretish_value(str(settings["elevenlabs_voice_id"])),
            "text_length": text_length,
        }

    return {
        "provider": provider,
        "voice_id": _mask_secretish_value(str(settings["voice"])),
        "text_length": text_length,
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


async def _create_edge_tts_audio_file(text: str, settings: dict[str, str | float | bool]) -> Path:
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
            voice=str(settings["voice"]),
            rate=str(settings["rate"]),
            volume=str(settings["volume"]),
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


def _create_elevenlabs_audio_file_sync(
    text: str,
    settings: dict[str, str | float | bool],
) -> Path:
    api_key = str(settings["elevenlabs_api_key"])
    voice_id = str(settings["elevenlabs_voice_id"])
    if not api_key or not voice_id:
        raise VoiceCommandUserError(
            "ElevenLabs TTS potrebuje ELEVENLABS_API_KEY a ELEVENLABS_VOICE_ID v .env."
        )

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
        from elevenlabs import VoiceSettings
        from elevenlabs.client import ElevenLabs
    except ImportError as error:
        audio_path.unlink(missing_ok=True)
        raise VoiceCommandUserError(
            "elevenlabs neni nainstalovane. Spust python -m pip install -r requirements.txt."
        ) from error

    try:
        elevenlabs_client = ElevenLabs(api_key=api_key)
        response = elevenlabs_client.text_to_speech.convert(
            voice_id=voice_id,
            output_format=str(settings["elevenlabs_output_format"]),
            text=text,
            model_id=str(settings["elevenlabs_model_id"]),
            voice_settings=VoiceSettings(
                stability=float(settings["elevenlabs_stability"]),
                similarity_boost=float(settings["elevenlabs_similarity_boost"]),
                style=float(settings["elevenlabs_style"]),
                use_speaker_boost=bool(settings["elevenlabs_use_speaker_boost"]),
            ),
        )
        with audio_path.open("wb") as output_file:
            for chunk in response:
                if chunk:
                    output_file.write(chunk)
    except VoiceCommandUserError:
        audio_path.unlink(missing_ok=True)
        raise
    except Exception as error:
        audio_path.unlink(missing_ok=True)
        raise VoiceCommandUserError(
            "Nepodarilo se vytvorit ElevenLabs TTS audio. Zkontroluj internet a ElevenLabs nastaveni."
        ) from error

    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        audio_path.unlink(missing_ok=True)
        raise VoiceCommandUserError("ElevenLabs TTS vytvorilo prazdny audio soubor.")

    return audio_path


async def _create_elevenlabs_audio_file(
    text: str,
    settings: dict[str, str | float | bool],
) -> Path:
    return await asyncio.to_thread(_create_elevenlabs_audio_file_sync, text, settings)


async def _create_tts_audio_file(text: str, settings: dict[str, str | float | bool]) -> Path:
    provider = str(settings["provider"])
    if provider == "edge":
        return await _create_edge_tts_audio_file(text, settings)

    if provider == "elevenlabs":
        return await _create_elevenlabs_audio_file(text, settings)

    if provider in {"system", "windows", "system_speech"}:
        return await _create_tts_wav_file_async(text)

    raise VoiceCommandUserError(
        "Neznamy TTS provider. Podporovane hodnoty jsou elevenlabs, edge nebo system."
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
    log_details = _tts_log_details(settings, text_length)

    try:
        voice_client = await _connect_or_move_to_user_channel(interaction)
        audio_path = await _create_tts_audio_file(clean_text, settings)
    except VoiceCommandUserError as error:
        _log_voice_command(
            command_name,
            "error",
            interaction,
            **log_details,
        )
        await interaction.followup.send(str(error), ephemeral=True)
        return False
    except Exception as error:
        _log_voice_command(
            command_name,
            "error",
            interaction,
            **log_details,
        )
        logger.warning(
            "%s setup failed provider=%s model_id=%s voice_id=%s error_type=%s",
            command_name,
            log_details.get("provider"),
            log_details.get("model_id"),
            log_details.get("voice_id"),
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
            **log_details,
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
        **log_details,
    )
    return True


async def play_tts_text_for_message(
    message: discord.Message,
    text: str,
    command_name: str,
    *,
    original_text_length: int | None = None,
    log_playback: bool = True,
) -> bool:
    clean_text = str(text or "").strip()
    text_length = original_text_length if original_text_length is not None else len(clean_text)
    settings = _get_tts_settings()
    log_details = _tts_log_details(settings, text_length)

    async def send_voice_error(error_text: str) -> None:
        await message.channel.send(error_text)

    try:
        voice_client = await _connect_or_move_to_user_voice(message.guild, message.author)
        audio_path = await _create_tts_audio_file(clean_text, settings)
    except VoiceCommandUserError as error:
        if log_playback:
            logger.info(
                "voice_command=%s status=error guild_id=%s channel_id=%s user_id=%s %s",
                command_name,
                getattr(message.guild, "id", None),
                getattr(message.channel, "id", None),
                getattr(message.author, "id", None),
                " ".join(f"{key}={value}" for key, value in log_details.items()),
            )
        await send_voice_error(str(error))
        return False
    except Exception as error:
        if log_playback:
            logger.warning(
                "%s setup failed provider=%s model_id=%s voice_id=%s error_type=%s",
                command_name,
                log_details.get("provider"),
                log_details.get("model_id"),
                log_details.get("voice_id"),
                type(error).__name__,
            )
        await send_voice_error("Nepodarilo se pripravit voice audio.")
        return False

    if voice_client.is_playing():
        voice_client.stop()

    def after_playback(error: Exception | None) -> None:
        _cleanup_audio_file(audio_path)
        if error is not None and log_playback:
            logger.error("%s playback error=%s", command_name, error)

    try:
        voice_client.play(
            discord.FFmpegPCMAudio(str(audio_path)),
            after=after_playback,
        )
    except Exception:
        _cleanup_audio_file(audio_path)
        if log_playback:
            logger.exception("%s playback start failed", command_name)
        await send_voice_error("Nepodarilo se prehrat voice audio. Je dostupny FFmpeg?")
        return False

    if log_playback:
        logger.info(
            "voice_command=%s status=success guild_id=%s channel_id=%s user_id=%s %s",
            command_name,
            getattr(message.guild, "id", None),
            getattr(message.channel, "id", None),
            getattr(message.author, "id", None),
            " ".join(f"{key}={value}" for key, value in log_details.items()),
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


async def handle_listen_test_command(
    interaction: discord.Interaction,
    seconds: int = 5,
) -> None:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 5
    seconds = max(LISTEN_TEST_MIN_SECONDS, min(LISTEN_TEST_MAX_SECONDS, seconds))

    log_listen_test_status(
        "started",
        interaction,
        seconds=seconds,
        received_audio=False,
    )

    voice_client = _get_voice_client(interaction)
    if voice_client is not None and voice_client.is_connected() and voice_client.is_playing():
        log_listen_test_status(
            "error",
            interaction,
            seconds=seconds,
            received_audio=False,
        )
        await interaction.response.send_message(
            "Nejdriv nech Panam domluvit. listen_test se nespusti, kdyz bot prehrava audio.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        from discord.ext import voice_recv
    except ImportError:
        log_listen_test_status(
            "error",
            interaction,
            seconds=seconds,
            received_audio=False,
        )
        await interaction.followup.send(
            "listen_test potrebuje discord-ext-voice-recv. Spust python -m pip install -r requirements.txt.",
            ephemeral=True,
        )
        return

    class PacketCountingSink(voice_recv.AudioSink):
        def __init__(self) -> None:
            super().__init__()
            self.packet_count = 0
            self._lock = threading.Lock()

        def wants_opus(self) -> bool:
            return True

        def write(self, user, data) -> None:
            with self._lock:
                self.packet_count += 1

        def cleanup(self) -> None:
            pass

        @property
        def received_audio(self) -> bool:
            with self._lock:
                return self.packet_count > 0

        def snapshot_packet_count(self) -> int:
            with self._lock:
                return self.packet_count

    sink = PacketCountingSink()

    try:
        voice_client = await _connect_or_move_to_user_voice_recv(interaction)
        voice_client.listen(sink)
        await asyncio.sleep(seconds)
    except VoiceCommandUserError as error:
        log_listen_test_status(
            "error",
            interaction,
            seconds=seconds,
            received_audio=sink.received_audio,
        )
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        log_listen_test_status(
            "error",
            interaction,
            seconds=seconds,
            received_audio=sink.received_audio,
        )
        logger.exception("listen_test failed")
        await interaction.followup.send(
            "Nepodarilo se dokoncit listen_test.",
            ephemeral=True,
        )
        return
    finally:
        if "voice_client" in locals() and hasattr(voice_client, "is_listening"):
            try:
                if voice_client.is_listening():
                    voice_client.stop_listening()
            except Exception:
                logger.warning("listen_test stop_listening failed")

    received_audio = sink.received_audio
    packet_count = sink.snapshot_packet_count()
    log_listen_test_status(
        "success",
        interaction,
        seconds=seconds,
        received_audio=received_audio,
    )
    await interaction.followup.send(
        (
            "listen_test dokoncen. "
            f"Prijate audio: {'ano' if received_audio else 'ne'}. "
            f"Packety: {packet_count}."
        ),
        ephemeral=True,
    )


async def handle_voice_say_command(interaction: discord.Interaction, text: str) -> None:
    command_name = "voice_say"
    text_length = len(text or "")
    settings = _get_tts_settings()
    log_details = _tts_log_details(settings, text_length)
    _log_voice_command(
        command_name,
        "started",
        interaction,
        **log_details,
    )

    clean_text = str(text or "").strip()
    if not clean_text:
        _log_voice_command(
            command_name,
            "error",
            interaction,
            **_tts_log_details(settings, 0),
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
            **log_details,
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
