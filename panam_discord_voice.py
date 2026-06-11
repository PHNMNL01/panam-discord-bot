import asyncio
from dataclasses import dataclass
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import discord

from panam_stt import SttUserError, get_stt_settings, transcribe_audio_file


BASE_DIR = Path(__file__).resolve().parent
VOICE_RUNTIME_DIR = BASE_DIR / "runtime" / "voice"
MAX_TTS_TEXT_LENGTH = 500
LISTEN_TEST_MIN_SECONDS = 1
LISTEN_TEST_MAX_SECONDS = 10
LISTEN_TRANSCRIBE_MIN_SECONDS = 1
LISTEN_TRANSCRIBE_MAX_SECONDS = 10
DISCORD_PCM_SAMPLE_RATE = 48000
DISCORD_PCM_CHANNELS = 2
DISCORD_PCM_SAMPLE_WIDTH = 2
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


@dataclass
class VoiceCaptureResult:
    audio_path: Path | None
    packet_count: int
    pcm_frame_count: int
    opus_frame_count: int
    received_audio: bool
    pcm_bytes: bytes
    pcm_bytes_total: int
    opus_bytes_total: int
    first_frame_byte_lengths: list[int]
    first_pcm_frame_byte_lengths: list[int]
    first_opus_frame_byte_lengths: list[int]
    wants_opus: bool
    has_pcm_attr: bool
    has_opus_attr: bool
    voice_data_type: str | None
    voice_data_safe_attrs: list[str]
    min_frame_bytes: int | None
    max_frame_bytes: int | None
    avg_frame_bytes: float | None
    capture_requested_seconds: int
    capture_actual_seconds: float
    first_frame_at_ms: float | None
    last_frame_at_ms: float | None
    sink_cleanup_done: bool
    output_file_size_bytes: int
    duration_seconds: float | None
    sample_rate: int | None
    channels: int | None


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
    packet_count: int = 0,
    output_file_size_bytes: int = 0,
    duration_seconds: float | None = None,
) -> None:
    context = _safe_voice_context(interaction)
    logger.info(
        (
            "command=listen_test action=listen_test status=%s seconds=%s "
            "packet_count=%s received_audio=%s output_file_size_bytes=%s "
            "duration_seconds=%s guild_id=%s channel_id=%s user_id=%s"
        ),
        status,
        seconds,
        packet_count,
        str(received_audio).lower(),
        output_file_size_bytes,
        duration_seconds,
        context["guild_id"],
        context["channel_id"],
        context["user_id"],
    )


def log_listen_transcribe_status(
    status: str,
    interaction: discord.Interaction,
    *,
    seconds: int,
    received_audio: bool,
    packet_count: int,
    transcript_length: int | None,
    provider: str,
    model_id: str,
) -> None:
    context = _safe_voice_context(interaction)
    logger.info(
        (
            "action=listen_transcribe status=%s seconds=%s received_audio=%s "
            "packet_count=%s transcript_length=%s provider=%s model_id=%s "
            "guild_id=%s channel_id=%s user_id=%s"
        ),
        status,
        seconds,
        str(received_audio).lower(),
        packet_count,
        transcript_length,
        provider,
        model_id,
        context["guild_id"],
        context["channel_id"],
        context["user_id"],
    )


def log_listen_transcribe_debug_status(
    status: str,
    interaction: discord.Interaction,
    *,
    seconds: int,
    received_audio: bool,
    packet_count: int,
    pcm_frame_count: int = 0,
    pcm_bytes_total: int = 0,
    min_frame_bytes: int | None = None,
    max_frame_bytes: int | None = None,
    avg_frame_bytes: float | None = None,
    capture_requested_seconds: int | None = None,
    capture_actual_seconds: float | None = None,
    first_frame_at_ms: float | None = None,
    last_frame_at_ms: float | None = None,
    output_file_size_bytes: int = 0,
    duration_seconds: float | None = None,
) -> None:
    context = _safe_voice_context(interaction)
    logger.info(
        (
            "command=listen_transcribe_debug action=listen_transcribe_debug status=%s seconds=%s "
            "packet_count=%s pcm_frame_count=%s received_audio=%s pcm_bytes_total=%s "
            "min_frame_bytes=%s max_frame_bytes=%s avg_frame_bytes=%s "
            "capture_requested_seconds=%s capture_actual_seconds=%s first_frame_at_ms=%s "
            "last_frame_at_ms=%s output_file_size_bytes=%s duration_seconds=%s "
            "guild_id=%s channel_id=%s user_id=%s"
        ),
        status,
        seconds,
        packet_count,
        pcm_frame_count,
        str(received_audio).lower(),
        pcm_bytes_total,
        min_frame_bytes,
        max_frame_bytes,
        avg_frame_bytes,
        capture_requested_seconds,
        capture_actual_seconds,
        first_frame_at_ms,
        last_frame_at_ms,
        output_file_size_bytes,
        duration_seconds,
        context["guild_id"],
        context["channel_id"],
        context["user_id"],
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


def _get_wav_metadata(audio_path: Path) -> tuple[float | None, int | None, int | None]:
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            frame_count = wav_file.getnframes()
            duration = frame_count / sample_rate if sample_rate else None
            return duration, sample_rate, channels
    except (OSError, wave.Error):
        return None, None, None


def _infer_pcm_channels(frame_sizes: list[int]) -> int:
    if not frame_sizes:
        return DISCORD_PCM_CHANNELS

    mono_frame_bytes = int(DISCORD_PCM_SAMPLE_RATE * 0.02 * DISCORD_PCM_SAMPLE_WIDTH)
    stereo_frame_bytes = mono_frame_bytes * DISCORD_PCM_CHANNELS
    stereo_matches = sum(1 for size in frame_sizes if size and size % stereo_frame_bytes == 0)
    mono_matches = sum(1 for size in frame_sizes if size and size % mono_frame_bytes == 0)

    if mono_matches > stereo_matches:
        return 1
    return DISCORD_PCM_CHANNELS


def _log_capture_state(
    interaction: discord.Interaction,
    *,
    file_prefix: str,
    state: str,
    seconds: int,
    packet_count: int = 0,
    pcm_frame_count: int = 0,
    capture_actual_seconds: float | None = None,
) -> None:
    context = _safe_voice_context(interaction)
    logger.info(
        (
            "voice_capture_state=%s command=%s seconds=%s packet_count=%s "
            "pcm_frame_count=%s capture_actual_seconds=%s guild_id=%s channel_id=%s user_id=%s"
        ),
        state,
        file_prefix,
        seconds,
        packet_count,
        pcm_frame_count,
        capture_actual_seconds,
        context["guild_id"],
        context["channel_id"],
        context["user_id"],
    )


def _safe_voice_data_attrs(data) -> list[str]:
    attrs: list[str] = []
    for name in dir(data):
        if name.startswith("_"):
            continue
        try:
            value = getattr(data, name)
        except Exception:
            continue
        if callable(value):
            continue
        attrs.append(name)
    return sorted(attrs)[:30]


def _create_voice_capture_sink(voice_recv):
    class VoiceCaptureSink(voice_recv.AudioSink):
        def __init__(self) -> None:
            super().__init__()
            self.packet_count = 0
            self.pcm_frame_count = 0
            self.opus_frame_count = 0
            self._lock = threading.Lock()
            self._started_at: float | None = None
            self._first_frame_at: float | None = None
            self._last_frame_at: float | None = None
            self._pcm_chunks: list[bytes] = []
            self._pcm_frame_sizes: list[int] = []
            self._opus_frame_sizes: list[int] = []
            self._has_pcm_attr = False
            self._has_opus_attr = False
            self._voice_data_type: str | None = None
            self._voice_data_safe_attrs: list[str] = []
            self._cleanup_done = threading.Event()

        def mark_started(self) -> None:
            with self._lock:
                self._started_at = time.perf_counter()

        def wants_opus(self) -> bool:
            return True

        def write(self, user, data) -> None:
            try:
                with self._lock:
                    self.packet_count += 1
                    if self._voice_data_type is None:
                        self._voice_data_type = type(data).__name__
                        self._voice_data_safe_attrs = _safe_voice_data_attrs(data)

                    self._has_pcm_attr = self._has_pcm_attr or hasattr(data, "pcm")
                    self._has_opus_attr = self._has_opus_attr or hasattr(data, "opus")
                    pcm = getattr(data, "pcm", None)
                    opus = getattr(data, "opus", None)

                    pcm_frame = bytes(pcm) if pcm else b""
                    opus_frame = bytes(opus) if opus else b""

                    if opus_frame:
                        self.opus_frame_count += 1
                        self._opus_frame_sizes.append(len(opus_frame))

                    if not pcm_frame:
                        return

                    now = time.perf_counter()
                    if self._started_at is not None:
                        if self._first_frame_at is None:
                            self._first_frame_at = now
                        self._last_frame_at = now

                    self.pcm_frame_count += 1
                    self._pcm_chunks.append(pcm_frame)
                    self._pcm_frame_sizes.append(len(pcm_frame))
            except Exception:
                logger.exception("voice_capture_sink_write_failed")

        def cleanup(self) -> None:
            self._cleanup_done.set()

        @property
        def received_audio(self) -> bool:
            with self._lock:
                return self.packet_count > 0

        def snapshot_packet_count(self) -> int:
            with self._lock:
                return self.packet_count

        def snapshot_pcm_frame_count(self) -> int:
            with self._lock:
                return self.pcm_frame_count

        def snapshot_opus_frame_count(self) -> int:
            with self._lock:
                return self.opus_frame_count

        def snapshot_pcm_bytes(self) -> bytes:
            with self._lock:
                return b"".join(self._pcm_chunks)

        def snapshot_pcm_frame_sizes(self) -> list[int]:
            with self._lock:
                return list(self._pcm_frame_sizes)

        def snapshot_opus_frame_sizes(self) -> list[int]:
            with self._lock:
                return list(self._opus_frame_sizes)

        def snapshot_voice_data_metadata(self) -> tuple[bool, bool, str | None, list[str]]:
            with self._lock:
                return (
                    self._has_pcm_attr,
                    self._has_opus_attr,
                    self._voice_data_type,
                    list(self._voice_data_safe_attrs),
                )

        def snapshot_frame_timing_ms(self) -> tuple[float | None, float | None]:
            with self._lock:
                if self._started_at is None:
                    return None, None
                first_frame_at_ms = (
                    (self._first_frame_at - self._started_at) * 1000
                    if self._first_frame_at is not None
                    else None
                )
                last_frame_at_ms = (
                    (self._last_frame_at - self._started_at) * 1000
                    if self._last_frame_at is not None
                    else None
                )
                return first_frame_at_ms, last_frame_at_ms

        def wait_for_cleanup(self, timeout: float = 2.0) -> bool:
            return self._cleanup_done.wait(timeout)

        def write_wav(self, audio_path: Path) -> None:
            pcm_bytes = self.snapshot_pcm_bytes()
            channels = _infer_pcm_channels(self.snapshot_pcm_frame_sizes())

            with wave.open(str(audio_path), "wb") as wav_file:
                wav_file.setnchannels(channels)
                wav_file.setsampwidth(DISCORD_PCM_SAMPLE_WIDTH)
                wav_file.setframerate(DISCORD_PCM_SAMPLE_RATE)
                wav_file.writeframes(pcm_bytes)

    return VoiceCaptureSink()


async def capture_voice_audio(
    interaction: discord.Interaction,
    seconds: int,
    *,
    file_prefix: str = "capture",
    output_wav: bool = False,
) -> VoiceCaptureResult:
    try:
        from discord.ext import voice_recv
    except ImportError as error:
        raise VoiceCommandUserError(
            f"{file_prefix} potrebuje discord-ext-voice-recv. Spust python -m pip install -r requirements.txt."
        ) from error

    sink = _create_voice_capture_sink(voice_recv)
    voice_client = None
    capture_started_at: float | None = None
    capture_actual_seconds = 0.0
    sink_cleanup_done = False

    def current_capture_actual_seconds() -> float:
        if capture_actual_seconds:
            return capture_actual_seconds
        if capture_started_at is None:
            return 0.0
        return time.perf_counter() - capture_started_at

    try:
        _log_capture_state(
            interaction,
            file_prefix=file_prefix,
            state="capture_started",
            seconds=seconds,
        )
        voice_client = await _connect_or_move_to_user_voice_recv(interaction)
        capture_started_at = time.perf_counter()
        sink.mark_started()
        voice_client.listen(sink)
        _log_capture_state(
            interaction,
            file_prefix=file_prefix,
            state="sink_listen_started",
            seconds=seconds,
            packet_count=sink.snapshot_packet_count(),
            pcm_frame_count=sink.snapshot_pcm_frame_count(),
        )
        _log_capture_state(
            interaction,
            file_prefix=file_prefix,
            state="sleep_started",
            seconds=seconds,
            packet_count=sink.snapshot_packet_count(),
            pcm_frame_count=sink.snapshot_pcm_frame_count(),
        )
        await asyncio.sleep(seconds)
        capture_actual_seconds = current_capture_actual_seconds()
        _log_capture_state(
            interaction,
            file_prefix=file_prefix,
            state="sleep_finished",
            seconds=seconds,
            packet_count=sink.snapshot_packet_count(),
            pcm_frame_count=sink.snapshot_pcm_frame_count(),
            capture_actual_seconds=capture_actual_seconds,
        )
    finally:
        if voice_client is not None and hasattr(voice_client, "is_listening"):
            try:
                if voice_client.is_listening():
                    voice_client.stop_listening()
                _log_capture_state(
                    interaction,
                    file_prefix=file_prefix,
                    state="stop_listening_called",
                    seconds=seconds,
                    packet_count=sink.snapshot_packet_count(),
                    pcm_frame_count=sink.snapshot_pcm_frame_count(),
                    capture_actual_seconds=current_capture_actual_seconds(),
                )
            except Exception:
                logger.warning("%s stop_listening failed", file_prefix)
            sink_cleanup_done = await asyncio.to_thread(sink.wait_for_cleanup, 2.0)
            _log_capture_state(
                interaction,
                file_prefix=file_prefix,
                state="sink_cleanup_done",
                seconds=seconds,
                packet_count=sink.snapshot_packet_count(),
                pcm_frame_count=sink.snapshot_pcm_frame_count(),
                capture_actual_seconds=current_capture_actual_seconds(),
            )

    packet_count = sink.snapshot_packet_count()
    pcm_frame_count = sink.snapshot_pcm_frame_count()
    opus_frame_count = sink.snapshot_opus_frame_count()
    received_audio = sink.received_audio
    pcm_bytes = sink.snapshot_pcm_bytes()
    pcm_frame_sizes = sink.snapshot_pcm_frame_sizes()
    opus_frame_sizes = sink.snapshot_opus_frame_sizes()
    has_pcm_attr, has_opus_attr, voice_data_type, voice_data_safe_attrs = (
        sink.snapshot_voice_data_metadata()
    )
    first_frame_at_ms, last_frame_at_ms = sink.snapshot_frame_timing_ms()
    pcm_bytes_total = len(pcm_bytes)
    opus_bytes_total = sum(opus_frame_sizes)
    first_pcm_frame_byte_lengths = pcm_frame_sizes[:5]
    first_opus_frame_byte_lengths = opus_frame_sizes[:5]
    first_frame_byte_lengths = (
        first_pcm_frame_byte_lengths
        if first_pcm_frame_byte_lengths
        else first_opus_frame_byte_lengths
    )
    min_frame_bytes = min(pcm_frame_sizes) if pcm_frame_sizes else None
    max_frame_bytes = max(pcm_frame_sizes) if pcm_frame_sizes else None
    avg_frame_bytes = (
        sum(pcm_frame_sizes) / len(pcm_frame_sizes)
        if pcm_frame_sizes
        else None
    )
    base_result = {
        "packet_count": packet_count,
        "pcm_frame_count": pcm_frame_count,
        "opus_frame_count": opus_frame_count,
        "received_audio": received_audio,
        "pcm_bytes": pcm_bytes,
        "pcm_bytes_total": pcm_bytes_total,
        "opus_bytes_total": opus_bytes_total,
        "first_frame_byte_lengths": first_frame_byte_lengths,
        "first_pcm_frame_byte_lengths": first_pcm_frame_byte_lengths,
        "first_opus_frame_byte_lengths": first_opus_frame_byte_lengths,
        "wants_opus": sink.wants_opus(),
        "has_pcm_attr": has_pcm_attr,
        "has_opus_attr": has_opus_attr,
        "voice_data_type": voice_data_type,
        "voice_data_safe_attrs": voice_data_safe_attrs,
        "min_frame_bytes": min_frame_bytes,
        "max_frame_bytes": max_frame_bytes,
        "avg_frame_bytes": avg_frame_bytes,
        "capture_requested_seconds": seconds,
        "capture_actual_seconds": current_capture_actual_seconds(),
        "first_frame_at_ms": first_frame_at_ms,
        "last_frame_at_ms": last_frame_at_ms,
        "sink_cleanup_done": sink_cleanup_done,
    }
    if not received_audio:
        return VoiceCaptureResult(
            audio_path=None,
            **base_result,
            output_file_size_bytes=0,
            duration_seconds=None,
            sample_rate=None,
            channels=None,
        )

    if not output_wav:
        return VoiceCaptureResult(
            audio_path=None,
            **base_result,
            output_file_size_bytes=0,
            duration_seconds=None,
            sample_rate=None,
            channels=None,
        )

    if pcm_frame_count <= 0:
        return VoiceCaptureResult(
            audio_path=None,
            **base_result,
            output_file_size_bytes=0,
            duration_seconds=None,
            sample_rate=None,
            channels=None,
        )

    VOICE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    audio_file = tempfile.NamedTemporaryFile(
        suffix=".wav",
        prefix=f"panam_{file_prefix}_",
        dir=VOICE_RUNTIME_DIR,
        delete=False,
    )
    audio_path = Path(audio_file.name)
    audio_file.close()
    sink.write_wav(audio_path)
    duration, sample_rate, channels = _get_wav_metadata(audio_path)

    return VoiceCaptureResult(
        audio_path=audio_path,
        **base_result,
        output_file_size_bytes=audio_path.stat().st_size if audio_path.exists() else 0,
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
    )


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
        capture = await capture_voice_audio(
            interaction,
            seconds,
            file_prefix="listen_test",
            output_wav=False,
        )
    except VoiceCommandUserError as error:
        log_listen_test_status(
            "error",
            interaction,
            seconds=seconds,
            received_audio=False,
        )
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        log_listen_test_status(
            "error",
            interaction,
            seconds=seconds,
            received_audio=False,
        )
        logger.exception("listen_test failed")
        await interaction.followup.send(
            "Nepodarilo se dokoncit listen_test.",
            ephemeral=True,
        )
        return

    log_listen_test_status(
        "success",
        interaction,
        seconds=seconds,
        received_audio=capture.received_audio,
        packet_count=capture.packet_count,
        output_file_size_bytes=capture.output_file_size_bytes,
        duration_seconds=capture.duration_seconds,
    )
    await interaction.followup.send(
        (
            "listen_test dokoncen. "
            f"Prijate audio: {'ano' if capture.received_audio else 'ne'}. "
            f"Packety: {capture.packet_count}."
        ),
        ephemeral=True,
    )


async def handle_listen_transcribe_command(
    interaction: discord.Interaction,
    seconds: int = 5,
) -> None:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 5
    seconds = max(
        LISTEN_TRANSCRIBE_MIN_SECONDS,
        min(LISTEN_TRANSCRIBE_MAX_SECONDS, seconds),
    )

    stt_settings = get_stt_settings()
    provider = stt_settings["provider"]
    model_id = stt_settings["elevenlabs_stt_model_id"]

    def log_status(
        status: str,
        *,
        received_audio: bool,
        packet_count: int = 0,
        transcript_length: int | None = None,
    ) -> None:
        log_listen_transcribe_status(
            status,
            interaction,
            seconds=seconds,
            received_audio=received_audio,
            packet_count=packet_count,
            transcript_length=transcript_length,
            provider=provider,
            model_id=model_id,
        )

    log_status("started", received_audio=False)

    voice_client = _get_voice_client(interaction)
    if voice_client is not None and voice_client.is_connected() and voice_client.is_playing():
        log_status("error", received_audio=False)
        await interaction.response.send_message(
            "Nejdriv nech Panam domluvit. listen_transcribe se nespusti, kdyz bot prehrava audio.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        capture = await capture_voice_audio(
            interaction,
            seconds,
            file_prefix="stt",
            output_wav=True,
        )
    except VoiceCommandUserError as error:
        log_status("error", received_audio=False)
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        log_status("error", received_audio=False)
        logger.exception("listen_transcribe receive failed")
        await interaction.followup.send(
            "Nepodarilo se dokoncit listen_transcribe.",
            ephemeral=True,
        )
        return

    if not capture.received_audio or capture.audio_path is None:
        log_status(
            "success",
            received_audio=False,
            packet_count=capture.packet_count,
            transcript_length=0,
        )
        await interaction.followup.send(
            "Nic jsem neslysela. Zkus /listen_transcribe znovu a promluv behem testu.",
            ephemeral=True,
        )
        return

    try:
        transcript, stt_settings = await asyncio.to_thread(
            transcribe_audio_file,
            capture.audio_path,
        )
        provider = stt_settings["provider"]
        model_id = stt_settings["elevenlabs_stt_model_id"]
        transcript_length = len(transcript)
    except SttUserError as error:
        log_status(
            "error",
            received_audio=True,
            packet_count=capture.packet_count,
            transcript_length=0,
        )
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        log_status(
            "error",
            received_audio=True,
            packet_count=capture.packet_count,
            transcript_length=0,
        )
        logger.exception("listen_transcribe stt failed")
        await interaction.followup.send(
            "Nepodarilo se prepsat audio.",
            ephemeral=True,
        )
        return
    finally:
        if capture.audio_path is not None:
            _cleanup_audio_file(capture.audio_path)

    log_status(
        "success",
        received_audio=True,
        packet_count=capture.packet_count,
        transcript_length=transcript_length,
    )
    if not transcript:
        await interaction.followup.send(
            "Audio jsem slysela, ale nepodarilo se ho prepsat.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"Přepis:\n{transcript}",
    )


async def handle_listen_transcribe_debug_command(
    interaction: discord.Interaction,
    seconds: int = 5,
) -> None:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 5
    seconds = max(
        LISTEN_TRANSCRIBE_MIN_SECONDS,
        min(LISTEN_TRANSCRIBE_MAX_SECONDS, seconds),
    )

    def log_status(
        status: str,
        *,
        received_audio: bool,
        packet_count: int = 0,
        pcm_frame_count: int = 0,
        pcm_bytes_total: int = 0,
        min_frame_bytes: int | None = None,
        max_frame_bytes: int | None = None,
        avg_frame_bytes: float | None = None,
        capture_requested_seconds: int | None = None,
        capture_actual_seconds: float | None = None,
        first_frame_at_ms: float | None = None,
        last_frame_at_ms: float | None = None,
        output_file_size_bytes: int = 0,
        duration_seconds: float | None = None,
    ) -> None:
        log_listen_transcribe_debug_status(
            status,
            interaction,
            seconds=seconds,
            received_audio=received_audio,
            packet_count=packet_count,
            pcm_frame_count=pcm_frame_count,
            pcm_bytes_total=pcm_bytes_total,
            min_frame_bytes=min_frame_bytes,
            max_frame_bytes=max_frame_bytes,
            avg_frame_bytes=avg_frame_bytes,
            capture_requested_seconds=capture_requested_seconds,
            capture_actual_seconds=capture_actual_seconds,
            first_frame_at_ms=first_frame_at_ms,
            last_frame_at_ms=last_frame_at_ms,
            output_file_size_bytes=output_file_size_bytes,
            duration_seconds=duration_seconds,
        )

    log_status("started", received_audio=False)

    voice_client = _get_voice_client(interaction)
    if voice_client is not None and voice_client.is_connected() and voice_client.is_playing():
        log_status("error", received_audio=False)
        await interaction.response.send_message(
            "Nejdriv nech Panam domluvit. listen_transcribe_debug se nespusti, kdyz bot prehrava audio.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        capture = await capture_voice_audio(
            interaction,
            seconds,
            file_prefix="stt_debug",
            output_wav=True,
        )
    except VoiceCommandUserError as error:
        log_status("error", received_audio=False)
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        log_status("error", received_audio=False)
        logger.exception("listen_transcribe_debug receive failed")
        await interaction.followup.send(
            "Nepodarilo se dokoncit listen_transcribe_debug.",
            ephemeral=True,
        )
        return

    log_status(
        "success",
        received_audio=capture.received_audio,
        packet_count=capture.packet_count,
        pcm_frame_count=capture.pcm_frame_count,
        pcm_bytes_total=capture.pcm_bytes_total,
        min_frame_bytes=capture.min_frame_bytes,
        max_frame_bytes=capture.max_frame_bytes,
        avg_frame_bytes=capture.avg_frame_bytes,
        capture_requested_seconds=capture.capture_requested_seconds,
        capture_actual_seconds=capture.capture_actual_seconds,
        first_frame_at_ms=capture.first_frame_at_ms,
        last_frame_at_ms=capture.last_frame_at_ms,
        output_file_size_bytes=capture.output_file_size_bytes,
        duration_seconds=capture.duration_seconds,
    )
    if capture.pcm_frame_count > 0:
        diagnosis = "data.pcm prislo, WAV je vytvoren z PCM."
    elif capture.opus_frame_count > 0:
        diagnosis = "data.pcm nechodi; chodi Opus/raw packet data, WAV z PCM nelze vytvorit."
    else:
        diagnosis = "neprisly PCM ani Opus framy."

    metadata_text = (
        "listen_transcribe_debug dokoncen.\n"
        f"packet_count={capture.packet_count}\n"
        f"pcm_frame_count={capture.pcm_frame_count}\n"
        f"opus_frame_count={capture.opus_frame_count}\n"
        f"received_audio={str(capture.received_audio).lower()}\n"
        f"wants_opus={str(capture.wants_opus).lower()}\n"
        f"has_pcm_attr={str(capture.has_pcm_attr).lower()}\n"
        f"has_opus_attr={str(capture.has_opus_attr).lower()}\n"
        f"voice_data_type={capture.voice_data_type}\n"
        f"voice_data_safe_attrs={capture.voice_data_safe_attrs}\n"
        f"first_5_frame_byte_lengths={capture.first_frame_byte_lengths}\n"
        f"first_5_pcm_frame_byte_lengths={capture.first_pcm_frame_byte_lengths}\n"
        f"first_5_opus_frame_byte_lengths={capture.first_opus_frame_byte_lengths}\n"
        f"opus_bytes_total={capture.opus_bytes_total}\n"
        f"capture_requested_seconds={capture.capture_requested_seconds}\n"
        f"capture_actual_seconds={capture.capture_actual_seconds}\n"
        f"first_frame_at_ms={capture.first_frame_at_ms}\n"
        f"last_frame_at_ms={capture.last_frame_at_ms}\n"
        f"sink_cleanup_done={str(capture.sink_cleanup_done).lower()}\n"
        f"pcm_bytes_total={capture.pcm_bytes_total}\n"
        f"min_frame_bytes={capture.min_frame_bytes}\n"
        f"max_frame_bytes={capture.max_frame_bytes}\n"
        f"avg_frame_bytes={capture.avg_frame_bytes}\n"
        f"output_file_size_bytes={capture.output_file_size_bytes}\n"
        f"duration_seconds={capture.duration_seconds}\n"
        f"sample_rate={capture.sample_rate}\n"
        f"channels={capture.channels}\n"
        f"diagnosis={diagnosis}"
    )

    try:
        if capture.audio_path is None:
            await interaction.followup.send(metadata_text, ephemeral=True)
            return

        await interaction.followup.send(
            metadata_text,
            file=discord.File(
                str(capture.audio_path),
                filename="panam_listen_transcribe_debug.wav",
            ),
            ephemeral=True,
        )
    finally:
        if capture.audio_path is not None:
            _cleanup_audio_file(capture.audio_path)


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
