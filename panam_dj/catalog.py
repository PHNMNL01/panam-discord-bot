"""Approved, hash-bound PCM WAV catalog. No URLs or arbitrary user paths."""

from dataclasses import dataclass
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import stat
from typing import Protocol
import wave

from .config import ConfigurationError, confined_file, read_json


MAX_AUDIO_BYTES = 64 * 1024 * 1024


class DJError(Exception):
    """A safe, user-facing failure message (no raw exceptions or local paths)."""


@dataclass(frozen=True)
class LocalMedia:
    """Playable identity, independent of a requested video's metadata."""

    catalog_id: str
    sha256: str


@dataclass(frozen=True)
class Track:
    key: str
    title: str
    media: LocalMedia | None = None
    requested_url: str | None = None
    requested_title: str | None = None


class SourceProvider(Protocol):
    async def resolve(self, query: str) -> Track: ...


@dataclass(frozen=True)
class CatalogEntry:
    track: Track
    relative_path: str
    sha256: str
    authorization: str


class LocalCatalog:
    def __init__(self, root: Path, entries: dict[str, CatalogEntry]):
        self.root = root
        self._entries = dict(entries)

    @classmethod
    def load(cls, path: Path):
        data = read_json(path)
        if (
            not isinstance(data, dict) or set(data) != {"version", "tracks"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["tracks"], list) or not 1 <= len(data["tracks"]) <= 100
        ):
            raise ConfigurationError("Catalog version 1 requires 1 to 100 tracks.")
        entries = {}
        keys = {"id", "title", "path", "sha256", "approved", "authorization"}
        for item in data["tracks"]:
            if not isinstance(item, dict) or set(item) != keys:
                raise ConfigurationError("Invalid catalog entry fields.")
            key = item["id"]
            if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", key):
                raise ConfigurationError("Invalid catalog ID.")
            if key in entries:
                raise ConfigurationError("Duplicate catalog ID.")
            title, authority, digest = item["title"], item["authorization"], item["sha256"]
            if not isinstance(title, str) or not 1 <= len(title) <= 80 or not title.isprintable():
                raise ConfigurationError("Invalid catalog title.")
            if item["approved"] is not True or not isinstance(authority, str) or not authority.strip():
                raise ConfigurationError("Each track requires explicit authorization metadata.")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ConfigurationError("Each track requires its SHA-256 digest.")
            entries[key] = CatalogEntry(Track(key, title, LocalMedia(key, digest)),
                                        item["path"], digest, authority)
        catalog = cls(path.parent.resolve(strict=True), entries)
        for entry in entries.values():
            catalog.audio_bytes(entry.track)
        return catalog

    @property
    def tracks(self) -> tuple[Track, ...]:
        return tuple(entry.track for entry in self._entries.values())

    async def resolve(self, query: str) -> Track:
        if not isinstance(query, str) or query not in self._entries:
            raise DJError("Unknown catalog ID. URL playback is not available in DJ-P0.")
        return self._entries[query].track

    def audio_bytes(self, track: Track) -> bytes:
        """Revalidate on use and return the exact verified bytes for FFmpeg's pipe."""
        entry = self._entries.get(track.media.catalog_id) if track.media else None
        if entry is None or entry.track.media != track.media:
            raise DJError("Track is not in the approved catalog.")
        try:
            path = confined_file(self.root, entry.relative_path, ".wav")
            with path.open("rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ConfigurationError("Audio must be a regular file.")
                data = stream.read(MAX_AUDIO_BYTES + 1)
            if len(data) > MAX_AUDIO_BYTES or hashlib.sha256(data).hexdigest() != entry.sha256:
                raise ConfigurationError("Audio size or digest does not match the catalog.")
            with wave.open(BytesIO(data), "rb") as audio:
                if (
                    audio.getcomptype() != "NONE" or audio.getsampwidth() != 2
                    or audio.getnchannels() not in (1, 2)
                    or not 8000 <= audio.getframerate() <= 48000
                    or not 0 < audio.getnframes() <= audio.getframerate() * 600
                ):
                    raise ConfigurationError("Use 16-bit PCM WAV audio up to ten minutes.")
                expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                if len(audio.readframes(audio.getnframes())) != expected:
                    raise ConfigurationError("Truncated WAV audio.")
            return data
        except (OSError, ValueError, EOFError, wave.Error) as exc:
            raise DJError("Catalog audio failed validation; an owner must review it.") from exc
