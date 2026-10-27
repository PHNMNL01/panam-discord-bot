import asyncio
import unittest

from panam_dj.catalog import DJError
from panam_dj.playback import PlaybackManager
from panam_dj.tests.fakes import FakeProvider, FakeSource, FakeVoice


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.voices = {}
        self.sources = []
        self.connects = []
        self.bad_sources = set()

        async def connect(guild_id, channel_id):
            self.connects.append((guild_id, channel_id))
            voice = FakeVoice(guild_id, channel_id)
            self.voices[guild_id] = voice
            return voice

        def source(track):
            if track.key in self.bad_sources:
                raise RuntimeError("synthetic decoding failure")
            value = FakeSource(track)
            self.sources.append(value)
            return value

        self.manager = PlaybackManager(FakeProvider(), source, connect,
                                       guild_ids=(1, 2), queue_limit=2)

    async def asyncTearDown(self):
        for voice in self.voices.values():
            voice.fail_disconnect = False
        await self.manager.close()

    async def view(self, guild=1, channel=10):
        return await self.manager.control(guild, channel, "queue")

    async def test_fifo_and_audio_thread_callback(self):
        for key in ("a", "b", "c"):
            await self.manager.play(1, 10, key)
        self.assertEqual([t.key for t in (await self.view()).pending], ["b", "c"])
        for expected in ("b", "c", None):
            self.voices[1].finish(threaded=True)
            await self.manager.drain_callbacks()
            current = (await self.view()).current
            self.assertEqual(current.key if current else None, expected)
        self.assertEqual(self.voices[1].history, ["a", "b", "c"])
        self.assertEqual([s.cleanups for s in self.sources], [1, 1, 1])

    async def test_capacity_is_atomic_under_concurrent_requests(self):
        results = await asyncio.gather(*(self.manager.play(1, 10, str(i)) for i in range(6)),
                                       return_exceptions=True)
        self.assertEqual(sum(isinstance(r, DJError) for r in results), 3)
        self.assertEqual(self.connects, [(1, 10)])
        view = await self.view()
        self.assertEqual(view.current.key, "0")
        self.assertEqual([t.key for t in view.pending], ["1", "2"])

    async def test_pause_resume_and_invalid_transitions(self):
        with self.assertRaises(DJError):
            await self.manager.control(1, 10, "pause")
        await self.manager.play(1, 10, "a")
        self.assertTrue((await self.manager.control(1, 10, "pause")).paused)
        with self.assertRaises(DJError):
            await self.manager.control(1, 10, "pause")
        self.assertFalse((await self.manager.control(1, 10, "resume")).paused)
        with self.assertRaises(DJError):
            await self.manager.control(1, 10, "resume")

    async def test_skip_while_paused_and_stale_callback(self):
        await self.manager.play(1, 10, "a")
        await self.manager.play(1, 10, "b")
        await self.manager.play(1, 10, "c")
        await self.manager.control(1, 10, "pause")
        await self.manager.control(1, 10, "skip")
        self.voices[1].fire_stopped()
        await self.manager.drain_callbacks()
        view = await self.view()
        self.assertEqual(view.current.key, "b")
        self.assertFalse(view.paused)
        self.assertEqual([t.key for t in view.pending], ["c"])

    async def test_stop_clears_queue_and_keeps_voice(self):
        await self.manager.play(1, 10, "a")
        await self.manager.play(1, 10, "b")
        await self.manager.control(1, 10, "stop")
        await self.manager.play(1, 10, "new")
        self.voices[1].fire_stopped()
        await self.manager.drain_callbacks()
        self.assertEqual((await self.view()).current.key, "new")
        self.assertEqual((await self.view()).pending, ())
        self.assertEqual(len(self.connects), 1)

    async def test_leave_and_rejoin_ignore_old_callbacks(self):
        await self.manager.play(1, 10, "old")
        old = self.voices[1]
        await self.manager.control(1, 10, "leave")
        await self.manager.play(1, 11, "new")
        old.fire_stopped()
        await self.manager.drain_callbacks()
        self.assertEqual((await self.view(channel=11)).current.key, "new")
        self.assertFalse(old.connected)

    async def test_guild_and_channel_isolation(self):
        await self.manager.play(1, 10, "a")
        await self.manager.play(2, 20, "b")
        for action in ("skip", "pause", "resume", "queue", "stop", "leave"):
            with self.assertRaises(DJError):
                await self.manager.control(1, 20, action)
        with self.assertRaises(DJError):
            await self.manager.play(1, 20, "c")
        with self.assertRaises(DJError):
            await self.manager.play(3, 30, "d")
        await self.manager.control(1, 10, "stop")
        self.assertEqual((await self.view(2, 20)).current.key, "b")

    async def test_unknown_track_does_not_connect(self):
        with self.assertRaises(DJError):
            await self.manager.play(1, 10, "missing")
        self.assertEqual(self.connects, [])

    async def test_audio_start_failure_does_not_wedge_session(self):
        self.bad_sources.add("bad")
        with self.assertRaises(DJError):
            await self.manager.play(1, 10, "bad")
        await self.manager.play(1, 10, "good")
        self.assertEqual((await self.view()).current.key, "good")

    async def test_failed_queued_track_is_discarded_and_source_cleaned(self):
        for key in ("a", "bad", "c"):
            await self.manager.play(1, 10, key)
        self.voices[1].fail_play.add("bad")
        self.voices[1].finish()
        await self.manager.drain_callbacks()
        view = await self.view()
        self.assertEqual(view.current.key, "c")
        self.assertIsNotNone(view.last_error)
        bad_source = next(s for s in self.sources if s.track.key == "bad")
        # Rejected play attempts now clean up off-loop. Completion callbacks are
        # not cleanup barriers; observe the retained cleanup attempt explicitly.
        await asyncio.wait_for(asyncio.gather(*tuple(self.manager._cleanup_tasks)), 1)
        self.assertEqual(bad_source.cleanups, 1)

    async def test_runtime_audio_failure_advances_and_duplicate_callback_is_ignored(self):
        for key in ("a", "b", "c"):
            await self.manager.play(1, 10, key)
        callback = self.voices[1].finish(RuntimeError("synthetic"))
        await self.manager.drain_callbacks()
        callback(None)
        await self.manager.drain_callbacks()
        view = await self.view()
        self.assertEqual(view.current.key, "b")
        self.assertIsNotNone(view.last_error)

    async def test_lost_connection_and_external_move_clear_queue(self):
        for moved in (False, True):
            await self.manager.play(1, 10, "a")
            await self.manager.play(1, 10, "b")
            if moved:
                self.voices[1].channel.id = 99
            else:
                self.voices[1].connected = False
            await self.manager.reconcile(1)
            view = await self.view()
            self.assertIsNone(view.current)
            self.assertIsNone(view.channel_id)
            self.assertEqual(view.pending, ())

    async def test_disconnect_failure_preserves_reference_for_retry(self):
        await self.manager.play(1, 10, "a")
        self.voices[1].fail_disconnect = True
        with self.assertRaises(RuntimeError):
            await self.manager.control(1, 10, "leave")
        with self.assertRaises(DJError):
            await self.manager.play(1, 11, "b")
        self.voices[1].fail_disconnect = False
        await self.manager.control(1, 10, "leave")
        self.assertIsNone((await self.view()).channel_id)

    async def test_connection_failure_then_retry(self):
        original = self.manager.connector

        async def fail(*args):
            raise TimeoutError("synthetic timeout")

        self.manager.connector = fail
        with self.assertRaises(TimeoutError):
            await self.manager.play(1, 10, "a")
        self.assertEqual((await self.view()).pending, ())
        self.manager.connector = original
        await self.manager.play(1, 10, "b")
        self.assertEqual((await self.view()).current.key, "b")

    async def test_cancelled_provider_releases_lock_and_leaves_no_track(self):
        entered = asyncio.Event()

        async def resolve(query):
            entered.set()
            await asyncio.Event().wait()

        self.manager.provider.resolve = resolve
        task = asyncio.create_task(self.manager.play(1, 10, "a"))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone((await self.view()).current)
        self.assertEqual(self.connects, [])

    async def test_mismatched_connector_cannot_cross_guilds(self):
        foreign_voice = FakeVoice(2, 20)

        async def connect(*args):
            return foreign_voice

        self.manager.connector = connect
        with self.assertRaises(DJError):
            await self.manager.play(1, 10, "a")
        self.assertEqual(foreign_voice.history, [])
        self.assertEqual(foreign_voice.disconnects, 0)

    async def test_close_cleans_other_guilds_even_when_one_disconnect_fails(self):
        await self.manager.play(1, 10, "a")
        await self.manager.play(2, 20, "b")
        self.voices[1].fail_disconnect = True
        self.assertEqual(await self.manager.close(), (1,))
        self.assertFalse(self.voices[2].connected)
        self.voices[1].fire_stopped()
        await self.manager.drain_callbacks()
        with self.assertRaises(DJError):
            await self.manager.play(2, 20, "new")

    async def test_empty_stop_leave_are_idempotent(self):
        for action in ("stop", "leave", "leave"):
            self.assertIsNone((await self.manager.control(1, 10, action)).current)

    async def test_guild_removal_disconnects_even_if_voice_reports_connected(self):
        await self.manager.play(1, 10, "a")
        await self.manager.play(1, 10, "b")
        await self.manager.play(2, 20, "c")
        await self.manager.disconnect_guild(1)
        self.voices[1].fire_stopped()
        await self.manager.drain_callbacks()
        self.assertFalse(self.voices[1].connected)
        self.assertEqual((await self.view()).pending, ())
        self.assertEqual((await self.view(2, 20)).current.key, "c")

    async def test_cleanup_failure_does_not_block_next_queued_track(self):
        for key in ("a", "bad", "c"):
            await self.manager.play(1, 10, key)
        factory = self.manager.source_factory

        def source(track):
            value = factory(track)
            if track.key == "bad":
                def fail():
                    raise RuntimeError("synthetic cleanup failure")
                value.cleanup = fail
            return value

        self.manager.source_factory = source
        self.voices[1].fail_play.add("bad")
        self.voices[1].finish()
        await self.manager.drain_callbacks()
        self.assertEqual((await self.view()).current.key, "c")

    async def test_close_during_provider_resolution_prevents_new_playback(self):
        entered, release = asyncio.Event(), asyncio.Event()
        provider = FakeProvider()

        async def resolve(query):
            entered.set()
            await release.wait()
            return await provider.resolve(query)

        self.manager.provider.resolve = resolve
        playing = asyncio.create_task(self.manager.play(1, 10, "a"))
        await entered.wait()
        closing = asyncio.create_task(self.manager.close())
        # A loop barrier lets close enter and wait for the per-guild lock.
        await self.manager.drain_callbacks()
        release.set()
        with self.assertRaises(DJError):
            await playing
        self.assertEqual(await closing, ())
        self.assertEqual(self.connects, [])

    async def test_correction_changed_connection_rejects_suspended_resolution_without_starting(self):
        for change in ("move", "disconnect"):
            with self.subTest(change=change):
                await self.manager.play(1, 10, "seed")
                await self.manager.control(1, 10, "stop")
                before = await self.view()
                voice = self.voices[1]
                source_count = len(self.sources)
                entered, release = asyncio.Event(), asyncio.Event()
                provider = FakeProvider()

                async def resolve(query):
                    entered.set()
                    await release.wait()
                    return await provider.resolve(query)

                self.manager.provider.resolve = resolve
                request = asyncio.create_task(self.manager.play(1, 10, "stale"))
                await entered.wait()
                if change == "move":
                    voice.channel.id = 99
                else:
                    voice.connected = False
                release.set()
                with self.assertRaisesRegex(DJError, "Voice connection changed"):
                    await request
                self.assertEqual(self.manager._view(self.manager._sessions[1]), before)
                self.assertEqual(voice.history, ["seed"])
                self.assertEqual(len(self.sources), source_count)
                self.assertEqual(voice.disconnects, 0)
                await self.manager.reconcile(1)
                self.manager.provider = FakeProvider()
        await self.manager.play(1, 10, "fresh")
        self.assertEqual((await self.view()).current.key, "fresh")

    async def test_correction_cancelled_close_finishes_all_sessions_before_propagating(self):
        await self.manager.play(1, 10, "a")
        await self.manager.play(2, 20, "b")
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.voices[1].disconnect

        async def disconnect(**kwargs):
            entered.set()
            await release.wait()
            await original(**kwargs)

        self.voices[1].disconnect = disconnect
        closing = asyncio.create_task(self.manager.close())
        await entered.wait()
        closing.cancel()
        await asyncio.sleep(0)
        self.assertFalse(closing.done())
        self.assertFalse(self.manager.closed)
        with self.assertRaises(DJError):
            await self.manager.play(2, 20, "new")
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertTrue(self.manager.closed)
        self.assertTrue(self.manager._close_task.done())
        for session in self.manager._sessions.values():
            self.assertIsNone(session.voice)
            self.assertIsNone(session.current)
            self.assertEqual(tuple(session.pending), ())
        self.assertEqual(await self.manager.close(), ())
        self.assertEqual([voice.disconnects for voice in self.voices.values()], [1, 1])

    async def test_correction_failed_close_can_retry_without_disconnecting_released_guild_twice(self):
        await self.manager.play(1, 10, "a")
        await self.manager.play(2, 20, "b")
        self.voices[1].fail_disconnect = True
        self.assertEqual(await self.manager.close(), (1,))
        self.assertFalse(self.manager.closed)
        self.assertIs(self.manager._sessions[1].voice, self.voices[1])
        self.assertIsNone(self.manager._sessions[2].voice)
        self.voices[1].fail_disconnect = False
        self.assertEqual(await self.manager.close(), ())
        self.assertTrue(self.manager.closed)
        self.assertEqual(await self.manager.close(), ())
        self.assertEqual([voice.disconnects for voice in self.voices.values()], [2, 1])
