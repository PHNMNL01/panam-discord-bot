import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

from panam_dj.catalog import DJError, LocalCatalog
from panam_dj.config import ConfigurationError, PACKAGE_ROOT, Settings, confined_file


class RuntimeVersionTests(unittest.TestCase):
    def test_correction_python_311_is_rejected_at_package_import(self):
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.version_info = (3, 11, 9, 'final', 0); import panam_dj"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("requires Python 3.12 or newer", result.stderr)

    def test_correction_python_312_floor_allows_package_import(self):
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.version_info = (3, 12, 0, 'final', 0); import panam_dj"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class CatalogConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f"fixture_{uuid.uuid4().hex}"
        # Normal inherited Windows ACLs; Python 3.14's tempfile mode 0700 can
        # make a directory inaccessible to a restricted sandbox token.
        self.root.mkdir()
        self.addCleanup(self.cleanup_fixture)
        self.audio = (PACKAGE_ROOT / "catalog/audio/dj_panam_demo.wav").read_bytes()
        (self.root / "sample.wav").write_bytes(self.audio)
        self.manifest = {
            "version": 1,
            "tracks": [{"id": "sample", "title": "Synthetic test sample", "path": "sample.wav",
                        "sha256": hashlib.sha256(self.audio).hexdigest(), "approved": True,
                        "authorization": "Original DJ-P0 synthetic test fixture."}],
        }
        self.settings = {"application_id": 1, "guild_ids": [2], "queue_limit": 2,
                         "catalog": "catalog.json"}
        self.write_manifest()
        self.write_settings()

    def cleanup_fixture(self):
        resolved = self.root.resolve(strict=True)
        assert resolved.parent == Path(__file__).resolve().parent
        assert resolved.name.startswith("fixture_") and not self.root.is_symlink()
        shutil.rmtree(resolved)

    def write_manifest(self):
        (self.root / "catalog.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def write_settings(self):
        (self.root / "config.json").write_text(json.dumps(self.settings), encoding="utf-8")

    def load(self):
        return LocalCatalog.load(self.root / "catalog.json")

    def test_bundled_catalog_and_template_are_valid(self):
        settings = Settings.load()
        catalog = LocalCatalog.load(settings.catalog_path)
        self.assertEqual(catalog.tracks[0].key, "demo")
        self.assertGreater(len(catalog.audio_bytes(catalog.tracks[0])), 44)

    def test_exact_id_lookup_rejects_urls_and_paths(self):
        catalog = self.load()
        self.assertEqual(asyncio.run(catalog.resolve("sample")).key, "sample")
        for query in ("https://example.invalid/audio.wav", "../sample.wav", "sample.wav", "SAMPLE", ""):
            with self.subTest(query=query), self.assertRaises(DJError):
                asyncio.run(catalog.resolve(query))

    def test_paths_reject_traversal_windows_devices_and_network_paths(self):
        for path in ("../sample.wav", "/sample.wav", "C:/sample.wav", "C:sample.wav",
                     "audio\\sample.wav", "//server/sample.wav", "audio//sample.wav",
                     "./sample.wav", "file:sample.wav", "sample.wav:ads", "CON.wav", "aux.wav",
                     "COM1.wav", "sample.mp3", "sample.wav."):
            with self.subTest(path=path), self.assertRaises((DJError, ConfigurationError, OSError)):
                self.manifest["tracks"][0]["path"] = path
                self.write_manifest()
                self.load()

    def test_symlink_and_junction_rejected_before_read(self):
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(ConfigurationError):
                confined_file(self.root, "sample.wav", ".wav")
        with patch.object(Path, "is_junction", return_value=True):
            with self.assertRaises(ConfigurationError):
                confined_file(self.root, "sample.wav", ".wav")

    def test_hash_mismatch_rejected_on_load_and_again_before_playback(self):
        catalog = self.load()
        (self.root / "sample.wav").write_bytes(self.audio + b"changed")
        with self.assertRaises(DJError):
            catalog.audio_bytes(catalog.tracks[0])
        with self.assertRaises(DJError):
            self.load()

    def test_missing_file_and_directory_rejected(self):
        for relative in ("missing.wav", "directory.wav"):
            (self.root / "directory.wav").mkdir(exist_ok=True)
            self.manifest["tracks"][0]["path"] = relative
            self.write_manifest()
            with self.assertRaises(DJError):
                self.load()

    def test_non_audio_and_truncated_audio_rejected_even_with_matching_hash(self):
        for payload in (b"https://example.invalid/playlist", self.audio[:60]):
            (self.root / "sample.wav").write_bytes(payload)
            self.manifest["tracks"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
            self.write_manifest()
            with self.assertRaises(DJError):
                self.load()

    def test_audio_size_limit(self):
        with patch("panam_dj.catalog.MAX_AUDIO_BYTES", 64):
            with self.assertRaises(DJError):
                self.load()

    def test_approval_metadata_and_unique_ids_required(self):
        original = deepcopy(self.manifest)
        for key, value in (("approved", False), ("approved", 1), ("authorization", ""),
                           ("id", "../bad"), ("title", "line\nbreak"), ("sha256", "bad")):
            with self.subTest(key=key, value=value):
                self.manifest = deepcopy(original)
                self.manifest["tracks"][0][key] = value
                self.write_manifest()
                with self.assertRaises(ConfigurationError):
                    self.load()
        self.manifest = deepcopy(original)
        self.manifest["tracks"].append(deepcopy(original["tracks"][0]))
        self.write_manifest()
        with self.assertRaises(ConfigurationError):
            self.load()

    def test_configuration_rejects_unbounded_or_ambiguous_settings(self):
        original = deepcopy(self.settings)
        for key, value in (("queue_limit", 0), ("queue_limit", 101), ("queue_limit", True),
                           ("application_id", 0), ("application_id", True),
                           ("guild_ids", []), ("guild_ids", [2, 2]), ("guild_ids", [False]),
                           ("catalog", "../catalog.json"), ("token", "synthetic-forbidden-field")):
            with self.subTest(key=key, value=value):
                self.settings = deepcopy(original)
                self.settings[key] = value
                self.write_settings()
                with self.assertRaises(ConfigurationError):
                    Settings.load("config.json", root=self.root)

    def test_malformed_and_oversized_json(self):
        for payload in (b"{", b" " * 1_048_577):
            (self.root / "config.json").write_bytes(payload)
            with self.assertRaises(ConfigurationError):
                Settings.load("config.json", root=self.root)
