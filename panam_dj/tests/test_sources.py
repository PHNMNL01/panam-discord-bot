import asyncio
from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

from panam_dj import __main__ as entrypoint
from panam_dj.catalog import DJError, LocalCatalog, LocalMedia
from panam_dj.config import ConfigurationError, Settings
from panam_dj.metadata_worker import discover
from panam_dj.sources import (DJSourceProvider, VideoMetadata, YtDlpMetadata,
                              metadata_worker_environment, youtube_video_id)


VIDEO = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO}"
METADATA = {"id": VIDEO, "title": "Requested recording", "duration": 2}


class URLTests(unittest.TestCase):
    def test_supported_urls_are_canonicalized_to_identity(self):
        for url in (URL, f"https://youtube.com/watch?v={VIDEO}",
                    f"https://m.youtube.com/watch?v={VIDEO}&t=12s",
                    f"https://youtu.be/{VIDEO}?si=abc_123",
                    f"https://www.youtube.com/shorts/{VIDEO}"):
            with self.subTest(url=url):
                self.assertEqual(youtube_video_id(url), VIDEO)

    def test_rejects_playlists_unknown_hosts_and_ambiguous_urls(self):
        for url in (URL + "&list=PL123", URL + "&index=1", URL + "&v=" + VIDEO,
                    URL + "#t=2", URL + "&t=no", URL + "&si=", URL + "&feature=share",
                    "https://www.youtube.com/playlist?list=PL123",
                    "https://www.youtube.com/watch?v=short", "http://youtu.be/" + VIDEO,
                    "https://youtube.com.evil.test/watch?v=" + VIDEO,
                    "https://youtube.com@evil.test/watch?v=" + VIDEO,
                    "https://u:p@youtube.com/watch?v=" + VIDEO,
                    "https://youtube.com:443/watch?v=" + VIDEO,
                    "https://youtube.com./watch?v=" + VIDEO,
                    "https://127.0.0.1/watch?v=" + VIDEO,
                    "https://youtu.be/" + VIDEO + "/other", "https://youtu.be/" + VIDEO + "?v=" + VIDEO,
                    "https://youtube.com/live/" + VIDEO, "https://youtube.com/embed/" + VIDEO,
                    "https://youtube.com/watch?v=%61bcdefghijk", URL + "\n", " " + URL,
                    "https://youtube.com\\@evil.test/" + VIDEO,
                    "https://yоutube.com/watch?v=" + VIDEO, "file:///sample.wav", VIDEO,
                    "", None, "x" * 513):
            with self.subTest(url=url):
                with self.assertRaises(DJError):
                    youtube_video_id(url)


class WorkerTests(unittest.TestCase):
    def test_unverified_yt_dlp_version_is_rejected_before_import_or_network(self):
        with patch("panam_dj.metadata_worker.importlib.metadata.version", return_value="0.0"):
            with self.assertRaisesRegex(ValueError, "Unverified"):
                discover(VIDEO)

    def run_worker(self, **changes):
        info = dict(METADATA, live_status="not_live", availability="public", **changes)
        factory = Mock()
        worker = factory.return_value.__enter__ = Mock(return_value=Mock())
        factory.return_value.__exit__ = Mock(return_value=False)
        worker.return_value.extract_info.return_value = info
        return discover(VIDEO, factory), factory, worker.return_value

    def test_only_metadata_is_returned_and_download_path_is_disabled(self):
        result, factory, worker = self.run_worker(formats=[{"url": "https://media.invalid/secret"}])
        self.assertEqual(result, METADATA)
        options = factory.call_args.args[0]
        for key in ("skip_download", "simulate", "noplaylist", "ignore_no_formats_error"):
            self.assertIs(options[key], True)
        for key in ("cachedir", "usenetrc"):
            self.assertIs(options[key], False)
        self.assertEqual(options["js_runtimes"], {})
        self.assertEqual(options["remote_components"], [])
        self.assertIsNone(options["cookiesfrombrowser"])
        self.assertIsNone(options["cookiefile"])
        self.assertEqual(options["proxy"], "")
        worker.extract_info.assert_called_once_with(URL, download=False, process=False, ie_key="Youtube")
        worker.download.assert_not_called()

    def test_unplayable_and_redirect_results_are_rejected(self):
        valid = dict(METADATA, live_status="not_live", availability="public")
        for change in ({"_type": "playlist"}, {"_type": "url"}, {"id": "wrongwrong1"},
                       {"live_status": "is_live"}, {"live_status": "post_live"},
                       {"availability": "needs_auth"}, {"age_limit": 18},
                       {"duration": float("nan")}, {"duration": 601}, {"duration": True},
                       {"title": "\n\x00"}):
            factory = Mock()
            factory.return_value.__enter__ = Mock(return_value=Mock())
            factory.return_value.__exit__ = Mock(return_value=False)
            factory.return_value.__enter__.return_value.extract_info.return_value = valid | change
            with self.subTest(change=change), self.assertRaises(ValueError):
                discover(VIDEO, factory)

    def test_real_ytdlp_api_with_fake_extractor_never_opens_network_or_downloads(self):
        try:
            import yt_dlp
            from yt_dlp.extractor.youtube import YoutubeIE
        except ImportError:
            self.skipTest("yt-dlp unavailable; metadata API integration requires the declared package")
        data = dict(METADATA, live_status="not_live", availability="public",
                    formats=[{"url": "https://media.invalid/audio", "ext": "mp4"}])
        with patch.object(YoutubeIE, "_real_extract", return_value=data), \
                patch.object(yt_dlp.YoutubeDL, "urlopen", side_effect=AssertionError("Network forbidden")), \
                patch.object(yt_dlp.YoutubeDL, "process_ie_result", side_effect=AssertionError("Processing forbidden")), \
                patch.object(yt_dlp.YoutubeDL, "download", side_effect=AssertionError("Download forbidden")):
            self.assertEqual(discover(VIDEO), METADATA)


class FakeProcess:
    def __init__(self, payload=None, code=0):
        self.stdout = asyncio.StreamReader()
        self.returncode = None
        self.exited = asyncio.Event()
        self.kills = 0
        if payload is not None:
            self.stdout.feed_data(payload)
            self.stdout.feed_eof()
            self.returncode = code
            self.exited.set()

    def kill(self):
        self.kills += 1
        self.returncode = -9
        self.stdout.feed_eof()
        self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode


class MetadataProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_revision002_failed_kill_and_drain_retain_ownership_until_exit(self):
        from panam_dj.playback import PlaybackManager
        from panam_dj.tests.fakes import FakeSource

        process = FakeProcess()
        process.stdout.set_exception(OSError("synthetic broken stdout"))
        kill_attempt = asyncio.Event()
        wait = AsyncMock(wraps=process.wait)
        process.wait = wait

        def fail_kill():
            process.kills += 1
            kill_attempt.set()
            raise PermissionError("synthetic failed termination")

        process.kill = fail_kill
        catalog = LocalCatalog.load(Settings.load().catalog_path)
        metadata = YtDlpMetadata(create_process=AsyncMock(return_value=process))
        provider = DJSourceProvider(catalog, enabled=True, mappings={VIDEO: catalog.tracks[0]}, metadata=metadata)
        connect = AsyncMock(side_effect=AssertionError("No voice connection expected"))
        manager = PlaybackManager(provider, FakeSource, connect, guild_ids=(1,), cleanup_timeout=0.01)
        request = asyncio.create_task(manager.play(1, 10, URL))
        try:
            await asyncio.wait_for(kill_attempt.wait(), 1)
            self.assertEqual(await manager.close(), (1,))
            self.assertFalse(manager.closed)
            self.assertIsNone(process.returncode)
            self.assertEqual(len(manager._preparations), 1)
            self.assertFalse(request.done())
            wait.assert_awaited_once()
            self.assertEqual(await manager.close(), (1,))
            self.assertEqual(process.kills, 1)
            wait.assert_awaited_once()
            process.returncode = 2
            process.exited.set()
            result = (await asyncio.gather(request, return_exceptions=True))[0]
            self.assertIsInstance(result, (DJError, OSError))
            self.assertEqual(await manager.close(), ())
            self.assertTrue(manager.closed)
            self.assertEqual(manager._preparations, {})
            wait.assert_awaited_once()
            self.assertEqual(process.kills, 1)
            connect.assert_not_called()
        finally:
            process.returncode = 2
            process.exited.set()
            await asyncio.gather(request, return_exceptions=True)
            await manager.close()

    async def test_revision002_cancelled_cleanup_observes_exit_after_drain_error(self):
        process = FakeProcess()
        process.stdout.set_exception(OSError("synthetic broken stdout"))
        entered = asyncio.Event()
        original_wait = process.wait

        async def wait():
            entered.set()
            return await original_wait()

        process.wait = AsyncMock(side_effect=wait)
        process.kill = Mock(side_effect=PermissionError("synthetic failed termination"))
        request = asyncio.create_task(YtDlpMetadata(create_process=AsyncMock(return_value=process)).lookup(VIDEO))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            request.cancel()
            barrier = asyncio.get_running_loop().create_future()
            asyncio.get_running_loop().call_soon(barrier.set_result, None)
            await barrier
            self.assertFalse(request.done())
            process.returncode = 2
            process.exited.set()
            with self.assertRaises(asyncio.CancelledError):
                await request
            process.wait.assert_awaited_once()
            process.kill.assert_called_once_with()
        finally:
            process.returncode = 2
            process.exited.set()
            await asyncio.gather(request, return_exceptions=True)

    async def test_bounded_worker_response_and_command(self):
        process = FakeProcess(json.dumps(METADATA).encode())
        spawn = AsyncMock(return_value=process)
        self.assertEqual((await YtDlpMetadata(create_process=spawn).lookup(VIDEO)).title, METADATA["title"])
        args, kwargs = spawn.call_args
        self.assertEqual(args[0:3], (sys.executable, "-I", "-B"))
        self.assertEqual(args[-1], VIDEO)
        self.assertNotIn(URL, args)
        self.assertEqual(kwargs["stdin"], asyncio.subprocess.DEVNULL)
        self.assertEqual(process.kills, 0)

    async def test_nonzero_oversized_invalid_and_mismatched_results(self):
        for payload, code in ((b"{}", 1), (b"x" * 8193, 0), (b"broken", 0),
                              (json.dumps(METADATA | {"id": "other"}).encode(), 0),
                              (json.dumps(METADATA | {"url": "https://bad.invalid"}).encode(), 0)):
            with self.subTest(code=code, size=len(payload)):
                process = FakeProcess(payload, code)
                with self.assertRaises(DJError):
                    await YtDlpMetadata(create_process=AsyncMock(return_value=process)).lookup(VIDEO)

    async def test_timeout_kills_and_reaps_worker(self):
        process = FakeProcess()
        with self.assertRaisesRegex(DJError, "timed out"):
            await YtDlpMetadata(timeout=0.01, create_process=AsyncMock(return_value=process)).lookup(VIDEO)
        self.assertEqual(process.kills, 1)
        self.assertIsNotNone(process.returncode)

    async def test_cancellation_during_process_creation_cannot_orphan_worker(self):
        entered, release = asyncio.Event(), asyncio.Event()
        process = FakeProcess()

        async def spawn(*args, **kwargs):
            entered.set()
            await release.wait()
            return process

        task = asyncio.create_task(YtDlpMetadata(create_process=spawn).lookup(VIDEO))
        await entered.wait()
        task.cancel()
        # Loop barrier, not a timed sleep: cancellation has entered owned cleanup.
        barrier = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(barrier.set_result, None)
        await barrier
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(process.kills, 1)
        self.assertTrue(process.exited.is_set())

    async def test_spawn_failure_is_safe(self):
        with self.assertRaisesRegex(DJError, "unavailable"):
            await YtDlpMetadata(create_process=AsyncMock(side_effect=OSError("private path"))).lookup(VIDEO)

    async def test_failed_kill_does_not_release_ownership_before_exit(self):
        process = FakeProcess()
        kill_attempt = asyncio.Event()

        def fail_kill():
            kill_attempt.set()
            raise OSError("Cannot kill yet")

        process.kill = fail_kill
        task = asyncio.create_task(YtDlpMetadata(timeout=0.01,
                                   create_process=AsyncMock(return_value=process)).lookup(VIDEO))
        await kill_attempt.wait()
        self.assertFalse(task.done())
        process.returncode = 2
        process.stdout.feed_eof()
        process.exited.set()
        with self.assertRaisesRegex(DJError, "timed out"):
            await task

    async def test_real_offline_subprocess_is_reaped(self):
        children = []

        async def spawn(*args, **kwargs):
            code = "import sys; sys.stdout.write(" + repr(json.dumps(METADATA)) + ")"
            process = await asyncio.create_subprocess_exec(sys.executable, "-I", "-B", "-c", code, **kwargs)
            children.append(process)
            return process

        self.assertEqual((await YtDlpMetadata(create_process=spawn).lookup(VIDEO)).video_id, VIDEO)
        self.assertEqual([p.returncode for p in children], [0])

    async def test_real_worker_subprocess_excludes_synthetic_parent_secrets(self):
        allowed = metadata_worker_environment()
        parent = allowed | {"DJ_PANAM_BOT_TOKEN": "synthetic-test-only",
                            "UNRELATED_SECRET": "synthetic-test-only",
                            "AWS_SECRET_ACCESS_KEY": "synthetic-test-only",
                            "HTTPS_PROXY": "https://invalid.test",
                            "PYTHONPATH": "untrusted-test-path"}
        children = []

        async def spawn(*args, **kwargs):
            self.assertEqual(kwargs["env"], allowed)
            code = (
                "import os,json,yt_dlp; "
                "assert not any(k in os.environ for k in "
                "('DJ_PANAM_BOT_TOKEN','UNRELATED_SECRET','AWS_SECRET_ACCESS_KEY','HTTPS_PROXY','PYTHONPATH')); "
                # Some Unix Python builds add LC_CTYPE during locale coercion.
                "assert set(os.environ) <= {'SystemRoot','SYSTEMROOT','LC_CTYPE'}; "
                "print(" + repr(json.dumps(METADATA)) + ")"
            )
            child = await asyncio.create_subprocess_exec(sys.executable, "-I", "-B", "-c", code, **kwargs)
            children.append(child)
            return child

        # Replace the mapping with synthetic values; never copy real secrets.
        with patch("panam_dj.sources.os.environ", parent):
            result = await YtDlpMetadata(create_process=spawn).lookup(VIDEO)
        self.assertEqual(result.video_id, VIDEO)
        self.assertEqual([p.returncode for p in children], [0])

    def test_environment_allowlist_never_enumerates_or_reads_secrets(self):
        class GuardedEnvironment:
            def get(self, key):
                if key != "SystemRoot":
                    raise AssertionError("Only the OS directory may be read")
                return "C:\\Windows"

        with patch("panam_dj.sources.os.environ", GuardedEnvironment()):
            self.assertEqual(metadata_worker_environment(),
                             {"SystemRoot": "C:\\Windows"} if os.name == "nt" else {})

    @unittest.skipUnless(os.name == "nt", "Windows environment prerequisite")
    async def test_missing_windows_systemroot_fails_before_worker_launch(self):
        spawn = AsyncMock()
        with patch("panam_dj.sources.os.environ", {}), self.assertRaises(DJError):
            await YtDlpMetadata(create_process=spawn).lookup(VIDEO)
        spawn.assert_not_called()


class SourceProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings.load()
        self.catalog = LocalCatalog.load(self.settings.catalog_path)
        self.asset = self.catalog.tracks[0]
        self.metadata = Mock(lookup=AsyncMock(return_value=VideoMetadata(VIDEO, "A different title", 2)))

    async def test_default_disabled_and_local_playback_does_not_lookup(self):
        provider = DJSourceProvider(self.catalog, metadata=self.metadata)
        self.assertEqual(await provider.resolve("demo"), self.asset)
        with self.assertRaisesRegex(DJError, "disabled"):
            await provider.resolve(URL)
        self.metadata.lookup.assert_not_called()

    async def test_approved_asset_and_video_identity_are_separate(self):
        provider = DJSourceProvider(self.catalog, enabled=True, mappings={VIDEO: self.asset}, metadata=self.metadata)
        track = await provider.resolve(URL)
        self.assertEqual(track.key, f"youtube:{VIDEO}")
        self.assertEqual(track.title, self.asset.title)
        self.assertEqual(track.requested_title, "A different title")
        self.assertEqual(track.media, self.asset.media)
        self.assertEqual(self.catalog.audio_bytes(track), self.catalog.audio_bytes(self.asset))
        with self.assertRaises(DJError):
            self.catalog.audio_bytes(replace(track, media=LocalMedia("demo", "0" * 64)))

    async def test_missing_mapping_and_metadata_failure_never_fall_back(self):
        provider = DJSourceProvider(self.catalog, enabled=True, metadata=self.metadata)
        with self.assertRaisesRegex(DJError, "no approved local audio"):
            await provider.resolve(URL)
        self.metadata.lookup.assert_not_called()
        self.metadata.lookup.side_effect = DJError("Metadata unavailable")
        provider.mappings[VIDEO] = self.asset
        with self.assertRaisesRegex(DJError, "Metadata unavailable"):
            await provider.resolve(URL)

    async def test_bad_url_never_reaches_metadata(self):
        provider = DJSourceProvider(self.catalog, enabled=True, metadata=self.metadata)
        with self.assertRaises(DJError):
            await provider.resolve("https://evil.invalid/music")
        self.metadata.lookup.assert_not_called()

    async def test_unmapped_and_invalid_urls_cannot_launch_worker(self):
        spawn = AsyncMock(side_effect=AssertionError("Worker launch forbidden"))
        provider = DJSourceProvider(self.catalog, enabled=True, mappings={VIDEO: self.asset},
                                    metadata=YtDlpMetadata(create_process=spawn))
        other = "lmnopqrstuv"
        for query in (f"https://youtu.be/{other}?si=abc_123", f"https://youtube.com/shorts/{other}",
                      f"https://www.youtube.com/watch?v={other}&t=2s",
                      URL + "&list=playlist", URL + "&v=" + VIDEO, URL + "#fragment",
                      "http://youtu.be/" + VIDEO, "https://youtube.com@evil.test/" + VIDEO,
                      "https://youtube.com:443/watch?v=" + VIDEO, "https://127.0.0.1/audio",
                      "https://youtu.be/short", "file:///audio.wav"):
            with self.subTest(query=query), self.assertRaises(DJError):
                await provider.resolve(query)
        self.assertEqual(await provider.resolve("demo"), self.asset)
        spawn.assert_not_called()

    async def test_mapped_short_url_normalizes_before_metadata_lookup(self):
        provider = DJSourceProvider(self.catalog, enabled=True, mappings={VIDEO: self.asset}, metadata=self.metadata)
        track = await provider.resolve(f"https://youtu.be/{VIDEO}?si=test&t=12s")
        self.metadata.lookup.assert_awaited_once_with(VIDEO)
        self.assertEqual(track.requested_url, URL)
        self.assertEqual(track.media, self.asset.media)

    def test_mapping_configuration_requires_explicit_approval(self):
        entry = {"video_id": VIDEO, "catalog_id": "demo", "approved": True, "authorization": "Test mapping only"}
        valid = {"version": 1, "metadata_enabled": True, "mappings": [entry]}
        for data in (valid, valid | {"mappings": [entry | {"approved": False}]},
                     valid | {"mappings": [entry | {"authorization": " "}]},
                     valid | {"mappings": [entry | {"catalog_id": "unknown"}]},
                     valid | {"mappings": [entry, entry]}, valid | {"metadata_enabled": 1}):
            with patch("panam_dj.sources.read_json", return_value=data):
                if data is valid:
                    self.assertEqual(DJSourceProvider.load(self.catalog, Path("unused.json")).mappings[VIDEO], self.asset)
                else:
                    with self.assertRaises(ConfigurationError):
                        DJSourceProvider.load(self.catalog, Path("unused.json"))

    def test_enabled_offline_check_never_launches_metadata(self):
        provider = DJSourceProvider(self.catalog, enabled=True, metadata=self.metadata)
        with patch.object(entrypoint.DJSourceProvider, "load", return_value=provider), \
                patch.object(entrypoint, "prerequisites", return_value=[]), \
                patch.object(entrypoint.importlib.metadata, "version", return_value="2026.10.26"), \
                patch("sys.stdout", new_callable=StringIO):
            self.assertEqual(entrypoint.main(["--check"]), 0)
        self.metadata.lookup.assert_not_called()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg unavailable; approved-asset integration cannot run")
    async def test_mapped_asset_uses_real_ffmpeg_without_media_url(self):
        try:
            from panam_dj.discord_app import FFmpegSourceFactory, queue_message
        except ImportError:
            self.skipTest("discord.py unavailable")
        from panam_dj.playback import PlaybackManager
        from panam_dj.tests.fakes import FakeVoice

        voice = FakeVoice(1, 10)
        provider = DJSourceProvider(self.catalog, enabled=True, mappings={VIDEO: self.asset}, metadata=self.metadata)
        manager = PlaybackManager(provider, FFmpegSourceFactory(self.catalog, shutil.which("ffmpeg")),
                                  AsyncMock(return_value=voice), guild_ids=(1,))
        try:
            view = await manager.play(1, 10, URL)
            self.assertIn("approved local audio; YouTube metadata only", queue_message(view))
            # Test-only inspection, not an implementation dependency on internals.
            process = voice.current.source._process
            packets = 0
            while voice.current.read():
                packets += 1
                self.assertLessEqual(packets, 105)
            self.assertGreaterEqual(packets, 95)
        finally:
            self.assertEqual(await manager.close(), ())
        self.assertIsNotNone(process.poll(), "FFmpeg must actually exit, not just return from cleanup")
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stdin.closed)
