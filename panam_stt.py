import logging
import os
from pathlib import Path


DEFAULT_STT_PROVIDER = "elevenlabs"
DEFAULT_ELEVENLABS_STT_MODEL_ID = "scribe_v2"
DEFAULT_ELEVENLABS_STT_LANGUAGE_CODE = "cs"
logger = logging.getLogger("panam")


class SttUserError(Exception):
    pass


def _safe_error_detail(error: Exception) -> str:
    status_code = (
        getattr(error, "status_code", None)
        or getattr(error, "status", None)
        or getattr(getattr(error, "response", None), "status_code", None)
    )
    detail = (
        getattr(error, "body", None)
        or getattr(error, "message", None)
        or getattr(error, "detail", None)
        or str(error)
    )
    detail_text = str(detail or type(error).__name__)
    api_key = os.getenv("ELEVENLABS_API_KEY") or ""
    if api_key:
        detail_text = detail_text.replace(api_key, "[redacted]")
    if len(detail_text) > 500:
        detail_text = detail_text[:500].rstrip() + "..."

    if status_code is None:
        return detail_text
    return f"HTTP {status_code}: {detail_text}"


def _response_shape_hint(response) -> str:
    response_type = type(response).__name__
    if isinstance(response, dict):
        keys = sorted(str(key) for key in response.keys())
        return f"type={response_type} keys={keys}"

    public_names = [
        name
        for name in dir(response)
        if not name.startswith("_") and name in {"text", "language_code", "words"}
    ]
    return f"type={response_type} attrs={sorted(public_names)}"


def get_stt_settings() -> dict[str, str]:
    return {
        "provider": (os.getenv("PANAM_STT_PROVIDER") or DEFAULT_STT_PROVIDER).strip().lower(),
        "elevenlabs_api_key": (os.getenv("ELEVENLABS_API_KEY") or "").strip(),
        "elevenlabs_stt_model_id": (
            os.getenv("ELEVENLABS_STT_MODEL_ID") or DEFAULT_ELEVENLABS_STT_MODEL_ID
        ).strip(),
        "elevenlabs_stt_language_code": (
            os.getenv("ELEVENLABS_STT_LANGUAGE_CODE") or DEFAULT_ELEVENLABS_STT_LANGUAGE_CODE
        ).strip(),
    }


def transcribe_audio_file(audio_path: Path) -> tuple[str, dict[str, str]]:
    settings = get_stt_settings()
    provider = settings["provider"]
    if provider != "elevenlabs":
        raise SttUserError("Neznamy STT provider. Podporovana hodnota je elevenlabs.")

    transcript = _transcribe_with_elevenlabs(audio_path, settings)
    return transcript, settings


def _transcribe_with_elevenlabs(audio_path: Path, settings: dict[str, str]) -> str:
    api_key = settings["elevenlabs_api_key"]
    if not api_key:
        raise SttUserError("ElevenLabs STT potrebuje ELEVENLABS_API_KEY v .env.")

    try:
        from elevenlabs.client import ElevenLabs
    except ImportError as error:
        raise SttUserError(
            "elevenlabs neni nainstalovane. Spust python -m pip install -r requirements.txt."
        ) from error

    try:
        client = ElevenLabs(api_key=api_key)
        with audio_path.open("rb") as audio_file:
            response = client.speech_to_text.convert(
                file=audio_file,
                model_id=settings["elevenlabs_stt_model_id"],
                language_code=settings["elevenlabs_stt_language_code"],
                tag_audio_events=False,
                diarize=False,
            )
    except Exception as error:
        safe_detail = _safe_error_detail(error)
        logger.warning("elevenlabs_stt request_failed %s", safe_detail)
        raise SttUserError(
            "Nepodarilo se vytvorit ElevenLabs STT prepis. "
            f"{safe_detail}"
        ) from error

    text = getattr(response, "text", None)
    if text is None and isinstance(response, dict):
        text = response.get("text")
    if text is None:
        logger.warning(
            "elevenlabs_stt unexpected_response_shape %s",
            _response_shape_hint(response),
        )
        raise SttUserError(
            "ElevenLabs STT odpoved nema rozpoznatelne pole text. "
            f"Response shape: {_response_shape_hint(response)}"
        )

    return str(text or "").strip()
