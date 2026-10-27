"""Offline checks by default; an explicit --connect is needed for future live use."""

import argparse
import asyncio
import importlib.metadata
import os
import shutil

from .catalog import DJError, LocalCatalog
from .config import ConfigurationError, Settings
from .sources import DJSourceProvider


def prerequisites() -> list[str]:
    problems = []
    try:
        version = importlib.metadata.version("discord.py")
        parts = tuple(int(part) for part in version.split(".")[:3])
        if not (2, 7, 1) <= parts < (3, 0, 0):
            problems.append("DJ-P0 requires discord.py >=2.7.1,<3 with its voice extra.")
    except (importlib.metadata.PackageNotFoundError, ValueError):
        problems.append("A supported discord.py release is unavailable.")
    for name in ("PyNaCl", "davey"):
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"Missing voice dependency: {name}.")
    if not shutil.which("ffmpeg"):
        problems.append("FFmpeg is not on PATH.")
    return problems


async def _connect(settings, catalog, token):
    from .discord_app import DJClient

    async with DJClient(settings, catalog) as client:
        await client.start(token)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Independent DJ Panam bot (offline checks by default)")
    parser.add_argument("--config", default="config.example.json", help="JSON path relative to panam_dj/")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Validate locally without reading a token")
    mode.add_argument("--connect", action="store_true", help="Log in and sync commands; needs separate live approval")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load(args.config)
        catalog = LocalCatalog.load(settings.catalog_path)
        provider = DJSourceProvider.load(catalog, settings.youtube_path)
        if provider.enabled:
            try:
                if importlib.metadata.version("yt-dlp") != "2026.10.26":
                    raise ConfigurationError("Enabled metadata lookup requires the declared yt-dlp version.")
            except importlib.metadata.PackageNotFoundError:
                raise ConfigurationError("yt-dlp is required for enabled metadata lookup.") from None
    except (ConfigurationError, DJError, OSError):
        print("Configuration/catalog validation failed; inspect the non-secret JSON and approved audio.")
        return 2
    print(f"Configuration and catalog valid: {len(catalog.tracks)} track(s), {len(settings.guild_ids)} guild(s).")
    problems = prerequisites()
    for problem in problems:
        print(problem)
    if not args.connect:
        print("Offline check only. No token read, login, command sync or voice connection.")
        return 2 if problems else 0
    if problems:
        return 2
    # The example IDs are deliberately invalid for an actual authorized deployment.
    if args.config == "config.example.json":
        print("Use an owner-prepared dedicated configuration before connecting.")
        return 2
    token = os.environ.get("DJ_PANAM_BOT_TOKEN")
    if not token:
        print("DJ_PANAM_BOT_TOKEN must be provisioned independently for the dedicated bot.")
        return 2
    try:
        asyncio.run(_connect(settings, catalog, token))
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("DJ Panam could not run; check the dedicated application and voice prerequisites.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
