"""Real FFmpeg/AudioPlayer resource checks with in-memory Discord transport."""

import asyncio
import gc
from io import BytesIO
import shutil
import threading
import unittest
import warnings
from unittest.mock import AsyncMock, patch

from panam_dj.catalog import DJError, LocalCatalog
from panam_dj.config import Settings
from panam_dj.playback import PlaybackManager, SourceConstructionError
from panam_dj.tests.fakes import FakeVoice
from panam_dj.tests.test_discord_app import ThreadedVoice, discord

if discord is not None:
    from panam_dj.discord_app import DiscordOwnedSource, FFmpegSourceFactory, ManagedFFmpegOpusAudio


class ConstructionProcess:
    """No child is created; only native construction failure semantics are injected."""

    def __init__(self, *, fail_kill=False, fail_close=False):
        class Output(BytesIO):
            def close(stream):
                if self.fail_close:
                    raise OSError("synthetic pipe closure failure")
                super().close()

        self.fail_kill, self.fail_close = fail_kill, fail_close
        self.stdin, self.stdout, self.stderr = BytesIO(), Output(), None
        self.returncode, self.pid = None, 123
        self.kills = self.waits = 0
        self.wait_entered, self.wait_release = threading.Event(), threading.Event()
        self.wait_release.set()

    def poll(self):
        return self.returncode

    def kill(self):
        self.kills += 1
        if self.fail_kill:
            raise PermissionError("synthetic termination failure")
        self.returncode = -9

    def wait(self):
        self.waits += 1
        self.wait_entered.set()
        if not self.wait_release.wait(5):
            raise AssertionError("Test did not release process wait")
        return self.returncode


@unittest.skipIf(discord is None, "discord.py required for partial-construction tests")
class ConstructionOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.catalog = LocalCatalog.load(Settings.load().catalog_path)
        self.allocated = []
        self.construction_errors = []
        self.process = ConstructionProcess()
        self.voice = FakeVoice(1, 10)
        self.manager = PlaybackManager(self.catalog, FFmpegSourceFactory(self.catalog, "unused"),
                                       AsyncMock(return_value=self.voice), guild_ids=(1,),
                                       source_wrapper=DiscordOwnedSource, cleanup_timeout=0.02)

    async def asyncTearDown(self):
        # Release only the synthetic failure after ownership assertions. The
        # manager's recorded failed attempt remains failed; this is fixture repair.
        self.process.fail_kill = self.process.fail_close = False
        self.process.wait_release.set()
        for source in self.allocated:
            await asyncio.to_thread(source.cleanup)
            self.assertTrue(source._dj_input.closed)
        self.assertTrue(self.process.stdin.closed)
        self.assertTrue(self.process.stdout.closed)
        await self.manager.close()

    async def rejected_construction(self):
        allocated = self.allocated
        errors = self.construction_errors

        class CapturedSource(ManagedFFmpegOpusAudio):
            def __new__(cls, *args, **kwargs):
                source = super().__new__(cls)
                allocated.append(source)
                return source

            def __init__(self, *args, **kwargs):
                try:
                    super().__init__(*args, **kwargs)
                except SourceConstructionError as error:
                    errors.append(error)
                    raise

        start = threading.Thread.start

        def fail_writer_start(thread, *args, **kwargs):
            if thread.name.startswith("popen-stdin-writer:"):
                raise RuntimeError("synthetic writer startup failure")
            return start(thread, *args, **kwargs)

        with patch("panam_dj.discord_app.ManagedFFmpegOpusAudio", CapturedSource), \
                patch.object(discord.FFmpegAudio, "_spawn_process", return_value=self.process), \
                patch.object(threading.Thread, "start", fail_writer_start):
            with self.assertRaises(DJError):
                await self.manager.play(1, 10, "demo")
        self.assertEqual(self.voice.history, [])

    async def assert_retained_failure(self):
        await self.rejected_construction()
        self.assertEqual(await self.manager.close(), (1,))
        self.assertFalse(self.manager.closed)
        self.assertEqual(len(self.manager._sources), 1)
        owned = next(iter(self.manager._sources))
        self.assertIs(owned.source, self.allocated[0])
        self.assertIsNotNone(owned.completion.result())
        self.assertIn("ownership retained", self.manager._sessions[1].last_error)
        self.assertEqual(await asyncio.gather(self.manager.close(), self.manager.close()), [(1,), (1,)])
        self.assertEqual(self.process.kills, 1, "Failed cleanup must not run again")

    async def test_revision002_construction_and_kill_failure_retain_source(self):
        self.process.fail_kill = True
        await self.assert_retained_failure()
        self.assertIsNone(self.process.returncode)
        self.assertFalse(self.process.stdout.closed)
        self.assertFalse(self.allocated[0]._dj_input.closed)

    async def test_revision002_construction_and_pipe_failure_retain_source(self):
        self.process.fail_close = True
        await self.assert_retained_failure()
        self.assertEqual(self.process.returncode, -9)
        self.assertFalse(self.process.stdout.closed)
        self.assertTrue(self.allocated[0]._dj_input.closed)

    async def test_revision002_construction_failure_cleanup_succeeds(self):
        await self.rejected_construction()
        self.assertEqual(await self.manager.close(), ())
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.manager._sources, {})
        self.assertEqual((self.process.kills, self.process.waits), (1, 1))
        self.assertTrue(self.process.stdin.closed)
        self.assertTrue(self.process.stdout.closed)
        self.assertTrue(self.allocated[0]._dj_input.closed)

    async def test_revision002_construction_resource_can_only_be_transferred_once(self):
        await self.rejected_construction()
        self.assertEqual(len(self.construction_errors), 1)
        with self.assertRaisesRegex(RuntimeError, "already transferred"):
            self.construction_errors[0].take_resource()
        self.assertEqual(await self.manager.close(), ())
        self.assertEqual((self.process.kills, self.process.waits), (1, 1))

    async def test_revision002_cancelled_concurrent_close_waits_for_partial_cleanup(self):
        self.manager.cleanup_timeout = 2
        self.process.wait_release.clear()
        try:
            await self.rejected_construction()
            self.assertTrue(await asyncio.to_thread(self.process.wait_entered.wait, 1))
            first = asyncio.create_task(self.manager.close())
            second = asyncio.create_task(self.manager.close())
            await self.manager.drain_callbacks()
            first.cancel()
            await self.manager.drain_callbacks()
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            self.assertFalse(self.manager.closed)
            self.assertEqual(len(self.manager._sources), 1)
            self.assertFalse(self.process.stdout.closed)
        finally:
            self.process.wait_release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertEqual(results[1], ())
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.voice.disconnects, 1)
        self.assertEqual((self.process.kills, self.process.waits), (1, 1))
        self.assertTrue(self.process.stdin.closed)
        self.assertTrue(self.process.stdout.closed)
        self.assertTrue(self.allocated[0]._dj_input.closed)


@unittest.skipUnless(discord is not None and shutil.which("ffmpeg"), "discord.py and FFmpeg required")
class FFmpegOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.catalog = LocalCatalog.load(Settings.load().catalog_path)
        self.factory = FFmpegSourceFactory(self.catalog, shutil.which("ffmpeg"))
        self.sources, self.voices, self.managers, self.thread_errors = [], [], [], []
        self.warning_context = warnings.catch_warnings(record=True)
        self.warnings = self.warning_context.__enter__()
        self.addCleanup(self.warning_context.__exit__, None, None, None)
        warnings.simplefilter("always", ResourceWarning)
        hook = patch.object(threading, "excepthook", side_effect=self.thread_errors.append)
        hook.start()
        self.addCleanup(hook.stop)

    def source(self, track):
        source = self.factory(track)
        self.sources.append(source)
        return source

    def manager(self, voice=None, *, source_factory=None, cleanup_timeout=5):
        voice = voice or ThreadedVoice(1, 10)
        self.voices.append(voice)
        manager = PlaybackManager(self.catalog, source_factory or self.source,
                                  AsyncMock(return_value=voice), guild_ids=(1,),
                                  source_wrapper=DiscordOwnedSource, cleanup_timeout=cleanup_timeout)
        self.managers.append(manager)
        return manager, voice

    async def asyncTearDown(self):
        for voice in self.voices:
            voice.stop()
        for manager in self.managers:
            await manager.close()
        for voice in self.voices:
            for player in getattr(voice, "players", ()):
                await asyncio.to_thread(player.join, 3)
                self.assertFalse(player.is_alive(), "AudioPlayer survived cleanup")
        for source in self.sources:
            await asyncio.to_thread(source.cleanup)
            self.assert_released(source)
        self.assertEqual(self.thread_errors, [])
        gc.collect()
        self.assertEqual([str(w.message) for w in self.warnings
                          if issubclass(w.category, ResourceWarning)], [])

    def assert_released(self, source):
        self.assertIsNotNone(source._process.poll(), "FFmpeg process still running")
        self.assertTrue(source._process.stdout.closed, "stdout is still open")
        self.assertTrue(source._process.stdin.closed, "stdin is still open")
        self.assertTrue(source._dj_input.closed, "owned input is still open")
        if source._process.stderr is not None:
            self.assertTrue(source._process.stderr.closed)
        for name in ("_pipe_writer_thread", "_pipe_reader_thread"):
            thread = getattr(source, name)
            self.assertTrue(thread is None or not thread.is_alive(), name + " survived cleanup")

    async def entered(self, event):
        self.assertTrue(await asyncio.to_thread(event.wait, 5), "Event deadline exceeded")

    async def exercise(self, action):
        manager, voice = self.manager()
        packet_sent = threading.Event()
        voice.send_audio_packet = lambda *args, **kwargs: packet_sent.set()
        await manager.play(1, 10, "demo")
        first = voice.current
        await self.entered(packet_sent)
        if action == "skip":
            await manager.play(1, 10, "demo")
        if action == "natural":
            self.assertIsNone(await asyncio.wait_for(asyncio.shield(first.completion), 5))
            self.assertEqual(first.source._process.returncode, 0)
        elif action == "shutdown":
            self.assertEqual(await manager.close(), ())
        else:
            await manager.control(1, 10, action)
        self.assertEqual(await manager.close(), ())
        self.assertTrue(manager.closed)
        self.assert_released(first.source)
        self.assertEqual(manager._sources, {})

    async def test_natural_completion_exits_process_and_closes_pipes(self):
        await self.exercise("natural")

    async def test_skip_exits_process_and_closes_pipes(self):
        await self.exercise("skip")

    async def test_stop_exits_process_and_closes_pipes(self):
        await self.exercise("stop")

    async def test_leave_exits_process_and_closes_pipes(self):
        await self.exercise("leave")

    async def test_shutdown_exits_process_and_closes_pipes(self):
        await self.exercise("shutdown")

    async def test_rejected_play_start_releases_real_source(self):
        voice = FakeVoice(1, 10)
        voice.fail_play.add("demo")
        manager, _ = self.manager(voice)
        with self.assertRaises(DJError):
            await manager.play(1, 10, "demo")
        self.assertEqual(await manager.close(), ())
        self.assertTrue(manager.closed)
        self.assert_released(self.sources[0])

    async def test_blocked_pipe_close_retains_ownership_and_late_completion_reconciles(self):
        source = self.source(self.catalog.tracks[0])
        entered, release = threading.Event(), threading.Event()
        close = source._process.stdout.close

        def blocked_close():
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test did not release pipe closure")
            close()

        manager, voice = self.manager(source_factory=lambda track: source, cleanup_timeout=0.02)
        with patch.object(source._process.stdout, "close", side_effect=blocked_close):
            try:
                await manager.play(1, 10, "demo")
                await self.entered(entered)
                self.assertTrue(voice.after_called.is_set())
                self.assertEqual(source._process.poll(), 0)
                self.assertFalse(source._process.stdout.closed)
                self.assertEqual(await manager.close(), (1,))
                self.assertFalse(manager.closed)
                self.assertEqual(len(manager._sources), 1)
            finally:
                release.set()
            # Completion is a real barrier, not a sleep or another timeout race.
            owned = next(iter(manager._sources))
            self.assertIsNone(await asyncio.wait_for(asyncio.shield(owned.completion), 3))
            self.assertEqual(await manager.close(), ())
            self.assertTrue(manager.closed)
            self.assert_released(source)

    async def test_pipe_close_error_is_retained_and_never_reports_closed(self):
        source = self.source(self.catalog.tracks[0])
        voice = FakeVoice(1, 10)
        voice.fail_play.add("demo")
        manager, _ = self.manager(voice, source_factory=lambda track: source)
        with patch.object(source._process.stdout, "close", side_effect=OSError("synthetic close failure")):
            with self.assertRaises(DJError):
                await manager.play(1, 10, "demo")
            self.assertEqual(await manager.close(), (1,))
            self.assertFalse(manager.closed)
            owned = next(iter(manager._sources))
            self.assertIsInstance(owned.completion.result(), ExceptionGroup)
            self.assertIsNotNone(source._process.poll())
            self.assertFalse(source._process.stdout.closed)
            self.assertTrue(source._process.stdin.closed)
            self.assertTrue(source._dj_input.closed)
        # Test-only repair releases the injected failure, not production retry.
        await asyncio.to_thread(source.cleanup)
        self.assert_released(source)
        self.assertEqual(await manager.close(), (1,))
        self.assertFalse(manager.closed, "Failed cleanup must remain reported")

    async def test_cancelled_concurrent_close_waits_for_real_pipe_cleanup(self):
        source = self.source(self.catalog.tracks[0])
        entered, release, packet_sent = threading.Event(), threading.Event(), threading.Event()
        close = source._process.stdout.close

        def blocked_close():
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test did not release pipe closure")
            close()

        manager, voice = self.manager(source_factory=lambda track: source)
        voice.send_audio_packet = lambda *args, **kwargs: packet_sent.set()
        with patch.object(source._process.stdout, "close", side_effect=blocked_close):
            try:
                await manager.play(1, 10, "demo")
                await self.entered(packet_sent)
                first = asyncio.create_task(manager.close())
                await self.entered(entered)
                second = asyncio.create_task(manager.close())
                first.cancel()
                barrier = asyncio.get_running_loop().create_future()
                asyncio.get_running_loop().call_soon(barrier.set_result, None)
                await barrier
                self.assertFalse(first.done())
                self.assertFalse(second.done())
                self.assertFalse(manager.closed)
            finally:
                release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
            self.assertIsInstance(results[0], asyncio.CancelledError)
            self.assertEqual(results[1], ())
            self.assertTrue(manager.closed)
            self.assertEqual(voice.disconnects, 1)
            self.assert_released(source)

    async def test_concurrent_cleanup_is_idempotent(self):
        source = self.source(self.catalog.tracks[0])
        with patch.object(source._process.stdout, "close", wraps=source._process.stdout.close) as close:
            await asyncio.gather(asyncio.to_thread(source.cleanup), asyncio.to_thread(source.cleanup))
            close.assert_called_once_with()
        self.assert_released(source)
