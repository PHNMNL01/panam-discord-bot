"""Optional real FFmpeg encoding smoke test. Never creates a Discord connection."""

import shutil
import sys

from .catalog import LocalCatalog
from .config import Settings


def main() -> int:
    executable = shutil.which("ffmpeg")
    if executable is None:
        print("UNAVAILABLE: FFmpeg is not on PATH.")
        return 2
    try:
        from .discord_app import FFmpegSourceFactory
    except ImportError:
        print("UNAVAILABLE: discord.py cannot be imported.")
        return 2
    settings = Settings.load()
    catalog = LocalCatalog.load(settings.catalog_path)
    source = FFmpegSourceFactory(catalog, executable)(catalog.tracks[0])
    packets = 0
    try:
        if not source.is_opus():
            raise AssertionError("The Discord audio source must emit Opus.")
        while packet := source.read():
            if not 0 < len(packet) <= 4000:
                raise AssertionError("Invalid Opus packet size.")
            packets += 1
            if packets > 200:
                raise AssertionError("Demo encoding exceeded its bounded duration.")
        if not 95 <= packets <= 105:
            raise AssertionError(f"Expected roughly 100 demo packets; got {packets}.")
    finally:
        source.cleanup()
    print(f"PASS: bundled WAV encoded into {packets} Opus packets; source cleaned up. No voice connection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
