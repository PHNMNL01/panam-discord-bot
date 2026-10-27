"""Non-secret configuration. Tokens are never stored in this configuration."""

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re


PACKAGE_ROOT = Path(__file__).resolve().parent


class ConfigurationError(ValueError):
    pass


def confined_file(root: Path, relative: str, suffix: str) -> Path:
    """Accept portable relative paths, rejecting devices, links and traversal."""
    if not isinstance(relative, str) or not relative or len(relative) > 240:
        raise ConfigurationError("A bounded relative file path is required.")
    parts = relative.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10))
    if any(
        not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", part)
        or part.split(".")[0].upper() in reserved
        for part in parts
    ) or PurePosixPath(relative).suffix.lower() != suffix:
        raise ConfigurationError("Invalid catalog/configuration path.")
    root = root.resolve(strict=True)
    candidate = root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink() or candidate.is_junction():
            raise ConfigurationError("Links and junctions are not permitted.")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ConfigurationError("File must be inside the approved directory.")
    return resolved


def read_json(path: Path, max_bytes: int = 1_048_576):
    # Bounded reads also protect against an accidentally enormous manifest.
    with path.open("rb") as stream:
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ConfigurationError("Configuration or catalog is too large.")
    try:
        return json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise ConfigurationError("Invalid JSON configuration or catalog.") from exc


@dataclass(frozen=True)
class Settings:
    application_id: int
    guild_ids: tuple[int, ...]
    queue_limit: int
    catalog_path: Path

    @classmethod
    def load(cls, relative: str = "config.example.json", *, root: Path = PACKAGE_ROOT):
        data = read_json(confined_file(root, relative, ".json"))
        keys = {"application_id", "guild_ids", "queue_limit", "catalog"}
        if not isinstance(data, dict) or set(data) != keys:
            raise ConfigurationError("Configuration must contain only the documented keys.")

        def snowflake(value):
            return type(value) is int and 0 < value < 2**64

        guilds = data["guild_ids"]
        if not snowflake(data["application_id"]):
            raise ConfigurationError("A dedicated Discord application ID is required.")
        if (
            not isinstance(guilds, list) or not 1 <= len(guilds) <= 100
            or not all(snowflake(guild) for guild in guilds)
            or len(set(guilds)) != len(guilds)
        ):
            raise ConfigurationError("Provide 1 to 100 distinct allowed guild IDs.")
        limit = data["queue_limit"]
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConfigurationError("queue_limit must be an integer from 1 to 100.")
        catalog = confined_file(root, data["catalog"], ".json")
        return cls(data["application_id"], tuple(guilds), limit, catalog)
