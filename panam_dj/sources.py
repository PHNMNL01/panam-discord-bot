"""YouTube metadata only; playable media always comes from the approved catalog."""

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qsl, urlsplit

from .catalog import DJError, LocalCatalog, Track
from .config import ConfigurationError, read_json
from .playback import finish_cleanup


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}", re.ASCII)


def metadata_worker_environment() -> dict[str, str]:
    """Allow only the Windows OS location; never enumerate/read parent secrets.

    The interpreter path is absolute and the worker needs no PATH, home, temp,
    proxy, Python configuration or credential variables. Windows needs SystemRoot
    for system DLL/side-by-side assembly loading. Other platforms need no entries.
    """
    if os.name == "nt":
        root = os.environ.get("SystemRoot")
        if not root:
            raise DJError("Windows metadata worker environment is unavailable.")
        return {"SystemRoot": root}
    return {}


def youtube_video_id(query: str) -> str:
    """A deliberately small URL grammar; no search, playlist or generic extractor."""
    invalid = DJError("Use a single HTTPS YouTube watch, shorts, or youtu.be video URL without playlist parameters.")
    if (not isinstance(query, str) or not 1 <= len(query) <= 512
            or not query.isascii() or any(c.isspace() or ord(c) < 32 for c in query)
            or any(c in query for c in ("\\", "%", "#"))):
        raise invalid
    try:
        url = urlsplit(query)
        if url.scheme != "https" or url.netloc not in {
            "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
        }:
            raise invalid
        pairs = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
        params = dict(pairs)
        if len(params) != len(pairs) or not set(params) <= {"v", "si", "t"}:
            raise invalid
        if "si" in params and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", params["si"]):
            raise invalid
        if "t" in params and not re.fullmatch(r"[0-9]{1,5}s?", params["t"]):
            raise invalid
        if url.netloc == "youtu.be":
            if "v" in params:
                raise invalid
            video_id = url.path.removeprefix("/")
        elif url.path == "/watch":
            video_id = params.get("v", "")
        elif url.path.startswith("/shorts/") and "v" not in params:
            video_id = url.path.removeprefix("/shorts/")
        else:
            raise invalid
        if not VIDEO_ID.fullmatch(video_id):
            raise invalid
        return video_id
    except ValueError:
        raise invalid from None


@dataclass(frozen=True)
class VideoMetadata:
    video_id: str
    title: str
    duration: float


class YtDlpMetadata:
    """One short-lived worker per lookup, at most two at once; never an audio worker.

    A process gives cancellation a real termination boundary. A cancelled Python
    thread would otherwise continue yt-dlp requests after its caller returned.
    No JS runtimes or plugins are enabled, so the worker has no child workers.
    """

    def __init__(self, *, timeout: float = 8.0, create_process=None):
        self.timeout = timeout
        self._slots = asyncio.Semaphore(2)
        self._create_process = create_process or asyncio.create_subprocess_exec

    @staticmethod
    async def _reap(launch):
        try:
            process = await launch
        except OSError:
            return  # Spawn failed; there is no process to own.
        if process.returncode is None:
            try:
                process.kill()
            except OSError:
                # Still own the process. A failed kill is not proof of exit;
                # wait/reap must finish before this lookup releases ownership.
                pass
        # Drain concurrently with termination; wait-before-drain can deadlock
        # when a child filled its pipe. Discard chunks, never accumulate output.
        try:
            if process.stdout is not None:
                while await process.stdout.read(8192):
                    pass
        finally:
            # A broken pipe does not prove exit, especially after a failed kill.
            # This retained cleanup task must observe exit even when draining
            # raises. Caller cancellation cannot cancel it (finish_cleanup).
            # asyncio.Process.wait observes the transport's exit notification;
            # it performs no additional termination attempt.
            await process.wait()

    async def lookup(self, video_id: str) -> VideoMetadata:
        if not VIDEO_ID.fullmatch(video_id):
            raise DJError("Invalid video identity.")
        async with self._slots:
            environment = metadata_worker_environment()
            launch = asyncio.create_task(self._create_process(
                sys.executable, "-I", "-B", str(Path(__file__).with_name("metadata_worker.py")), video_id,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=8192,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=environment,
            ))
            try:
                async with asyncio.timeout(self.timeout):
                    await asyncio.wait((launch,))
                    process = launch.result()
                    payload = await process.stdout.read(8193)
                    if len(payload) > 8192:
                        raise DJError("YouTube metadata exceeded its limit.")
                    # read(n) may return early. Collect the remaining small response.
                    while chunk := await process.stdout.read(8193 - len(payload)):
                        payload += chunk
                        if len(payload) > 8192:
                            raise DJError("YouTube metadata exceeded its limit.")
                    if await process.wait() != 0:
                        raise DJError("YouTube metadata is unavailable; no media was downloaded.")
                data = json.loads(payload)
                if (not isinstance(data, dict) or set(data) != {"id", "title", "duration"}
                        or data["id"] != video_id or not isinstance(data["title"], str)
                        or not 1 <= len(data["title"]) <= 80 or not data["title"].isprintable()
                        or type(data["duration"]) not in (int, float)
                        or not 0 < data["duration"] <= 600):
                    raise ValueError("invalid metadata")
                return VideoMetadata(video_id, data["title"], data["duration"])
            except TimeoutError:
                raise DJError("YouTube metadata timed out; no media was downloaded.") from None
            except (ValueError, OSError):
                raise DJError("YouTube metadata is unavailable; no media was downloaded.") from None
            finally:
                await finish_cleanup(asyncio.create_task(self._reap(launch)))


class DJSourceProvider:
    def __init__(self, catalog: LocalCatalog, *, enabled=False, mappings=None, metadata=None):
        self.catalog = catalog
        self.enabled = enabled
        self.mappings = dict(mappings or {})
        self.metadata = metadata or YtDlpMetadata()

    @classmethod
    def load(cls, catalog: LocalCatalog, path: Path | None):
        if path is None:
            return cls(catalog)
        data = read_json(path)
        if (not isinstance(data, dict) or set(data) != {"version", "metadata_enabled", "mappings"}
                or type(data["version"]) is not int or data["version"] != 1
                or type(data["metadata_enabled"]) is not bool or not isinstance(data["mappings"], list)
                or len(data["mappings"]) > 100):
            raise ConfigurationError("Invalid YouTube metadata configuration.")
        tracks = {track.key: track for track in catalog.tracks}
        mappings = {}
        for item in data["mappings"]:
            if (not isinstance(item, dict)
                    or set(item) != {"video_id", "catalog_id", "approved", "authorization"}
                    or not isinstance(item["video_id"], str) or not VIDEO_ID.fullmatch(item["video_id"])
                    or item["video_id"] in mappings or not isinstance(item["catalog_id"], str)
                    or item["catalog_id"] not in tracks or item["approved"] is not True
                    or not isinstance(item["authorization"], str)
                    or not item["authorization"].strip()):
                raise ConfigurationError("Each video mapping requires a unique ID and an approved catalog asset.")
            mappings[item["video_id"]] = tracks[item["catalog_id"]]
        return cls(catalog, enabled=data["metadata_enabled"], mappings=mappings)

    async def resolve(self, query: str) -> Track:
        if isinstance(query, str) and "://" not in query:
            return await self.catalog.resolve(query)
        video_id = youtube_video_id(query)
        if not self.enabled:
            raise DJError("YouTube metadata lookup is disabled; owner authorization is required.")
        asset = self.mappings.get(video_id)
        if asset is None:
            raise DJError("There is no approved local audio mapped to this video. Metadata was not requested; YouTube audio is not retrieved.")
        metadata = await self.metadata.lookup(video_id)
        return Track(f"youtube:{video_id}", asset.title, asset.media,
                     f"https://www.youtube.com/watch?v={video_id}", metadata.title)
