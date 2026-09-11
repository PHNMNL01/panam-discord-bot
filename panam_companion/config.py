"""Explicit experiment configuration; never inherit credentials or endpoint overrides."""
from dataclasses import dataclass, field
import os
from pathlib import Path

MODULE = Path(__file__).resolve().parent
CONFIG = MODULE / ".env"
RUNTIME = MODULE / "runtime"
LIVE_MODEL = "gpt-live-1"
VOICE = "marin"
BACKEND_MODEL = "gpt-5.6-luna"
LIVE_URL = "wss://api.openai.com/v1/live/sessions"
RESPONSES_URL = "https://api.openai.com/v1/responses"
AMBIENT = {
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID",
    "OPENAI_WEBSOCKET_BASE_URL", "DISCORD_TOKEN", "DISCORD_BOT_TOKEN",
}
FIELDS = {
    "PANAM_OPENAI_API_KEY", "PANAM_DISCORD_BOT_TOKEN", "PANAM_TEST_BOT_ID",
    "PANAM_TEST_GUILD_ID", "PANAM_TEST_VOICE_CHANNEL_ID",
    "PANAM_TEST_CONTROL_CHANNEL_ID", "PANAM_TEST_USER_ID",
}


class SafeError(Exception):
    """Only fixed application-authored messages may cross the console boundary."""


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    bot_token: str = field(repr=False)
    bot_id: int
    guild_id: int
    voice_id: int
    control_id: int
    user_id: int


def load_config(path=CONFIG, environ=None):
    from dotenv import dotenv_values
    env = os.environ if environ is None else environ
    if any(env.get(k) for k in AMBIENT | FIELDS):
        raise SafeError("Konflikt prostředí: spusťte z prostředí bez OpenAI/Discord/PANAM proměnných.")
    if not path.is_file():
        raise SafeError("Chybí panam_companion/.env. Vyplňte místní kopii .env.example.")
    try:
        if path.stat().st_size > 8192:
            raise ValueError
        # No load_dotenv, interpolation, discovery or environment mutation.
        data = dotenv_values(path, interpolate=False, encoding="utf-8-sig")
        if set(data) != FIELDS:
            raise ValueError
        values = [data[k] for k in ("PANAM_OPENAI_API_KEY", "PANAM_DISCORD_BOT_TOKEN")]
        if any(not v or v.startswith("REPLACE_") or any(c.isspace() for c in v) for v in values):
            raise ValueError
        ids = [data[k] for k in (
            "PANAM_TEST_BOT_ID", "PANAM_TEST_GUILD_ID", "PANAM_TEST_VOICE_CHANNEL_ID",
            "PANAM_TEST_CONTROL_CHANNEL_ID", "PANAM_TEST_USER_ID")]
        if any(not v or not v.isascii() or not v.isdigit() or not 0 < int(v) < 2**64 for v in ids):
            raise ValueError
        numbers = list(map(int, ids))
        if len(set(numbers)) != 5:
            raise ValueError
        return Config(*values, *numbers)
    except Exception:
        raise SafeError("Neúplná nebo neplatná konfigurace. Hodnoty nebyly vypsány.") from None


def safe_identity(config):
    return (f"bot={config.bot_id} guild={config.guild_id} voice={config.voice_id} "
            f"control={config.control_id} user={config.user_id}")
