import asyncio
import unittest
from unittest.mock import AsyncMock

from panam_dj.catalog import DJError, Track
from panam_dj.playback import PlaybackManager
from panam_dj.tests.fakes import FakeProvider, FakeSource, FakeVoice


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop_errors = []
        self.loop = asyncio.get_running_loop()
        self.previous_handler = self.loop.get_exception_handler()
        self.loop.set_exception_handler(lambda loop, context: self.loop_errors.append(context))
        self.voices = {}
        self.started = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def connect(guild, channel):
            voice = self.voices[guild] = FakeVoice(guild, channel)
            return voice

        self.provider = FakeProvider()
        self.manager = PlaybackManager(self.provider, FakeSource, connect,
                                       guild_ids=(1, 2), queue_limit=2, cleanup_timeout=0.02)

    async def asyncTearDown(self):
        self.release.set()
        try:
            await self.manager.close()
            await self.barrier()
        finally:
            self.loop.set_exception_handler(self.previous_handler)
        self.assertEqual(self.loop_errors, [], "No unowned task errors may escape preparation cleanup")

    async def barrier(self):
        await self.manager.drain_callbacks()

    def slow_provider(self, *, suppress_cancel=False):
        async def resolve(query):
            self.started.append(query)
            if query == "slow":
                self.entered.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    if not suppress_cancel:
                        raise
                    await self.release.wait()
            if query == "missing":
                raise DJError("Unavailable")
            return Track(query, query)
        self.provider.resolve = resolve

    async def start_slow(self, **kwargs):
        self.slow_provider(**kwargs)
        task = asyncio.create_task(self.manager.play(1, 10, "slow"))
        await self.entered.wait()
        return task

    async def test_controls_remain_responsive_during_discovery(self):
        await self.manager.play(1, 10, "seed")
        request = await self.start_slow()
        for action in ("pause", "queue", "resume", "skip"):
            view = await asyncio.wait_for(self.manager.control(1, 10, action), 1)
            self.assertEqual(view.resolving, 1)
        self.assertIsNone(view.current)
        await asyncio.wait_for(self.manager.control(1, 10, "stop"), 1)
        with self.assertRaises(DJError):
            await request
        self.assertEqual(self.voices[1].history, ["seed"])

    async def test_fifo_reservations_include_slow_requests_in_capacity(self):
        first = await self.start_slow()
        second = asyncio.create_task(self.manager.play(1, 10, "second"))
        third = asyncio.create_task(self.manager.play(1, 10, "third"))
        await self.barrier()
        with self.assertRaisesRegex(DJError, "full"):
            await self.manager.play(1, 10, "overflow")
        self.assertEqual(self.started, ["slow"])
        self.release.set()
        await asyncio.gather(first, second, third)
        voice = self.voices[1]
        voice.finish()
        await self.barrier()
        voice.finish()
        await self.barrier()
        self.assertEqual(voice.history, ["slow", "second", "third"])

    async def test_skip_can_cancel_preparation_before_any_voice_connection(self):
        request = await self.start_slow()
        await self.manager.control(1, 10, "skip")
        with self.assertRaises(DJError):
            await request
        self.assertEqual(self.voices, {})
        await self.manager.play(1, 10, "next")
        self.assertEqual(self.voices[1].history, ["next"])

    async def test_leave_rejects_late_result_and_allows_new_channel(self):
        request = await self.start_slow(suppress_cancel=True)
        await self.manager.control(1, 10, "leave")
        self.release.set()
        with self.assertRaises(DJError):
            await request
        self.assertEqual(self.voices, {})
        await self.manager.play(1, 11, "new")
        self.assertEqual(self.voices[1].channel.id, 11)

    async def test_pending_request_binds_channel_and_does_not_block_other_guild(self):
        request = await self.start_slow()
        for action in ("queue", "skip", "pause", "resume", "stop", "leave"):
            with self.assertRaises(DJError):
                await self.manager.control(1, 11, action)
        with self.assertRaises(DJError):
            await self.manager.play(1, 11, "wrong")
        await self.manager.play(2, 20, "other")
        self.assertEqual(self.voices[2].history, ["other"])
        await self.manager.control(1, 10, "stop")
        with self.assertRaises(DJError):
            await request

    async def test_timeout_releases_reservation_and_next_request_succeeds(self):
        self.manager.resolution_timeout = 0.01
        request = await self.start_slow()
        with self.assertRaisesRegex(DJError, "timed out"):
            await request
        self.assertEqual((await self.manager.control(1, 10, "queue")).resolving, 0)
        await self.manager.play(1, 10, "next")
        self.assertEqual(self.voices[1].history, ["next"])

    async def test_provider_suppressing_timeout_cannot_commit_late_success(self):
        timed_out = asyncio.Event()

        async def resolve(query):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                timed_out.set()
                await self.release.wait()
                return Track(query, query)

        self.provider.resolve = resolve
        self.manager.resolution_timeout = 0.01
        request = asyncio.create_task(self.manager.play(1, 10, "late"))
        await timed_out.wait()
        self.release.set()
        with self.assertRaisesRegex(DJError, "timed out"):
            await request
        self.assertEqual(self.voices, {})

    async def test_waiting_caller_is_reauthorized_before_metadata_effects(self):
        first = await self.start_slow()
        authorize = unittest.mock.Mock()
        second = asyncio.create_task(self.manager.play(1, 10, "unauthorized", authorize=authorize))
        await self.barrier()
        authorize.side_effect = DJError("Caller left")
        self.release.set()
        await first
        with self.assertRaisesRegex(DJError, "Caller left"):
            await second
        self.assertEqual(self.started, ["slow"])

    async def test_natural_completion_during_resolution_does_not_invalidate_request(self):
        await self.manager.play(1, 10, "seed")
        request = await self.start_slow()
        self.voices[1].finish()
        await self.barrier()
        self.release.set()
        await request
        self.assertEqual(self.voices[1].history, ["seed", "slow"])

    async def test_failure_does_not_poison_following_fifo_request(self):
        request = await self.start_slow()
        missing = asyncio.create_task(self.manager.play(1, 10, "missing"))
        third = asyncio.create_task(self.manager.play(1, 10, "third"))
        self.release.set()
        await request
        with self.assertRaises(DJError):
            await missing
        await third
        self.voices[1].finish()
        await self.barrier()
        self.assertEqual(self.voices[1].history, ["slow", "third"])

    async def test_cancelled_caller_waits_for_owned_preparation_cleanup(self):
        cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event()

        async def resolve(query):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_entered.set()
                await cleanup_release.wait()

        self.provider.resolve = resolve
        request = asyncio.create_task(self.manager.play(1, 10, "slow"))
        await self.entered.wait()
        request.cancel()
        await cleanup_entered.wait()
        request.cancel()
        await self.barrier()
        self.assertFalse(request.done())
        self.assertTrue(self.manager._preparations)
        cleanup_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertEqual(self.voices, {})

    async def test_failed_close_retains_preparation_and_reconciles_late_cleanup(self):
        request = await self.start_slow(suppress_cancel=True)
        await self.manager.play(2, 20, "other")
        self.assertEqual(await self.manager.close(), (1,))
        self.assertFalse(self.manager.closed)
        self.assertTrue(self.manager._preparations)
        self.assertFalse(self.voices[2].connected)
        self.release.set()
        with self.assertRaises(DJError):
            await request
        self.assertEqual(await self.manager.close(), ())
        self.assertTrue(self.manager.closed)
        self.assertFalse(self.manager._preparations)
        self.assertEqual(self.voices[2].disconnects, 1)

    async def test_concurrent_cancelled_close_still_cleans_every_guild(self):
        request = await self.start_slow()
        await self.manager.play(2, 20, "other")
        first = asyncio.create_task(self.manager.close())
        second = asyncio.create_task(self.manager.close())
        await self.barrier()
        first.cancel()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertEqual(results[1], ())
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.voices[2].disconnects, 1)
        with self.assertRaises(DJError):
            await request

    async def test_user_authorization_rechecked_before_admitting_prepared_track(self):
        self.slow_provider()
        authorize = unittest.mock.Mock()
        request = asyncio.create_task(self.manager.play(1, 10, "slow", authorize=authorize))
        await self.entered.wait()
        authorize.side_effect = DJError("Caller left")
        self.release.set()
        with self.assertRaisesRegex(DJError, "Caller left"):
            await request
        self.assertEqual(self.voices, {})

    async def test_cancelled_reservation_before_worker_starts_is_removed(self):
        request = await self.start_slow()
        second = asyncio.create_task(self.manager.play(1, 10, "second"))
        await self.barrier()
        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second
        self.release.set()
        await request
        await self.barrier()
        self.assertEqual(self.voices[1].history, ["slow"])
        self.assertEqual(self.manager._sessions[1].requests, [])

    async def test_cancelled_request_cannot_play_after_connector_suppresses_cancellation(self):
        cancelled = asyncio.Event()
        voice = FakeVoice(1, 10)

        async def connect(*args):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await self.release.wait()
                return voice

        self.manager.connector = connect
        request = asyncio.create_task(self.manager.play(1, 10, "local"))
        await self.entered.wait()
        request.cancel()
        await cancelled.wait()
        self.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertEqual(voice.history, [])
        self.assertEqual(voice.disconnects, 1)
        self.assertIsNone(self.manager._sessions[1].voice)
