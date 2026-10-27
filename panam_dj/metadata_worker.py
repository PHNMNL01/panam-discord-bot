"""Isolated metadata-only yt-dlp worker. Never invoked by offline startup checks."""

import json
import importlib.metadata
import re
import sys


def discover(video_id, ydl_factory=None):
    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Invalid video ID")
    if ydl_factory is None:
        if importlib.metadata.version("yt-dlp") != "2026.10.26":
            raise ValueError("Unverified yt-dlp version")
        import yt_dlp
        from yt_dlp.globals import plugin_dirs

        plugin_dirs.value = []  # Disable discovery of arbitrary installed plugins.
        ydl_factory = yt_dlp.YoutubeDL

    class SilentLogger:
        def debug(self, *args, **kwargs): pass
        def info(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass

    options = {
        "skip_download": True, "simulate": True, "noplaylist": True,
        "ignore_no_formats_error": True,  # Metadata does not require playable formats.
        "cachedir": False, "quiet": True, "no_warnings": True,
        "logger": SilentLogger(), "socket_timeout": 3, "retries": 0,
        "extractor_retries": 0, "fragment_retries": 0,
        "cookiefile": None, "cookiesfrombrowser": None, "usenetrc": False,
        "username": None, "password": None, "proxy": "",
        "js_runtimes": {}, "remote_components": [],
        "extractor_args": {"youtube": {"player_skip": ["js"], "skip": ["hls", "dash"]}},
    }
    # Library construction does not load the yt-dlp CLI's user configuration.
    with ydl_factory(options) as ydl:
        data = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}",
                                download=False, process=False, ie_key="Youtube")
    if (not isinstance(data, dict) or data.get("_type", "video") != "video"
            or data.get("id") != video_id or data.get("live_status") != "not_live"
            or data.get("availability") not in (None, "public", "unlisted")
            or type(data.get("age_limit", 0)) not in (int, float) or data.get("age_limit", 0) > 0
            or type(data.get("duration")) not in (int, float) or not 0 < data["duration"] <= 600
            or not isinstance(data.get("title"), str)):
        raise ValueError("Unsupported metadata")
    title = "".join(c for c in data["title"] if c.isprintable()).strip()[:80]
    if not title:
        raise ValueError("Missing title")
    # Media URLs, formats, thumbnails and descriptions never leave this worker.
    return {"id": video_id, "title": title, "duration": data["duration"]}


def main():
    try:
        if len(sys.argv) != 2:
            return 2
        print(json.dumps(discover(sys.argv[1]), ensure_ascii=True))
        return 0
    except Exception:
        return 2  # No raw exceptions, URLs, credentials or provider logs.


if __name__ == "__main__":
    sys.exit(main())
