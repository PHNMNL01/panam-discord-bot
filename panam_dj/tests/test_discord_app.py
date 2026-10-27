import asyncio
import contextlib
from io import StringIO
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

try:
    import discord
    from panam_dj.discord_app import DJClient, DiscordOwnedSource, FFmpegSourceFactory, queue_message
except ImportError:
    discord = None

from panam_dj import __main__ as entrypoint
from panam_dj.catalog import DJError, LocalCatalog, Track
from panam_dj.config import Settings
from panam_dj.playback import PlaybackManager, QueueView
from panam_dj.tests.fakes import FakeProvider, FakeSource, FakeVoice


class NoEnvironmentAccess(dict):
    def get(self, key, default=None):
        if "TOKEN" in key or "SECRET" in key or "KEY" in key:
            raise AssertionError("Offline checks must never access credential environment variables")
        # argparse/gettext may request locale settings; supply synthetic defaults.
        return default


class EntrypointTests(unittest.TestCase):
    def test_offline_check_never_reads_credentials_or_connects(self):
        with patch.object(entrypoint.os, "environ", NoEnvironmentAccess()), \
                patch.object(entrypoint, "prerequisites", return_value=[]), \
                patch.object(entrypoint, "_connect", new_callable=AsyncMock) as connect, \
                contextlib.redirect_stdout(StringIO()):
            self.assertEqual(entrypoint.main(["--check"]), 0)
            self.assertEqual(entrypoint.main([]), 0)
            connect.assert_not_called()

    def test_missing_dependencies_report_failure_before_token_access(self):
        with patch.object(entrypoint.os, "environ", NoEnvironmentAccess()), \
                patch.object(entrypoint, "prerequisites", return_value=["Missing voice dependency: davey."]), \
                contextlib.redirect_stdout(StringIO()) as output:
            self.assertEqual(entrypoint.main(["--connect"]), 2)
            self.assertIn("davey", output.getvalue())

    def test_example_config_cannot_connect(self):
        with patch.object(entrypoint.os, "environ", NoEnvironmentAccess()), \
                patch.object(entrypoint, "prerequisites", return_value=[]), \
                contextlib.redirect_stdout(StringIO()):
            self.assertEqual(entrypoint.main(["--connect"]), 2)


@unittest.skipIf(discord is None, "discord.py unavailable; adapter tests need existing dependency")
class DiscordAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        example = Settings.load()
        self.settings = Settings(123, (1, 2), 2, example.catalog_path)
        self.catalog = LocalCatalog.load(example.catalog_path)
        self.client = DJClient(self.settings, self.catalog, source_factory=FakeSource)
        self.voices = {}

        async def connect(guild_id, channel_id):
            voice = FakeVoice(guild_id, channel_id)
            self.voices[guild_id] = voice
            return voice

        self.client.playback.connector = connect

    async def asyncTearDown(self):
        await self.client.close()

    def interaction(self, guild_id=1, channel_id=10):
        guild = SimpleNamespace(id=guild_id)
        channel = Mock(spec=discord.VoiceChannel)
        channel.id, channel.guild = channel_id, guild
        response = SimpleNamespace(done=False)
        response.is_done = lambda: response.done

        async def defer(**kwargs):
            response.done = True

        response.defer = AsyncMock(side_effect=defer)
        response.send_message = AsyncMock()
        return SimpleNamespace(guild=guild, guild_id=guild_id,
                               user=SimpleNamespace(voice=SimpleNamespace(channel=channel)),
                               response=response, followup=SimpleNamespace(send=AsyncMock()))

    async def test_exact_seven_commands_registered_per_guild_only(self):
        self.assertEqual(self.client.tree.get_commands(), [])
        for guild_id in (1, 2):
            commands = self.client.tree.get_commands(guild=discord.Object(id=guild_id))
            self.assertEqual({c.name for c in commands}, {"play", "skip", "pause", "resume", "queue", "stop", "leave"})
            self.assertTrue(all(c.guild_only for c in commands))
        self.assertFalse(self.client.intents.message_content)
        self.assertFalse(self.client.intents.members)
        self.assertTrue(self.client.intents.voice_states)

    async def test_identity_checked_before_guild_sync(self):
        self.client.application_info = AsyncMock(return_value=SimpleNamespace(id=999))
        self.client.tree.sync = AsyncMock()
        with self.assertRaises(DJError):
            await self.client.setup_hook()
        self.client.tree.sync.assert_not_awaited()
        self.client.application_info.return_value.id = 123
        await self.client.setup_hook()
        self.assertEqual([call.kwargs["guild"].id for call in self.client.tree.sync.await_args_list], [1, 2])

    async def test_all_commands_dispatch_through_mocked_discord(self):
        guild = discord.Object(id=1)
        for name in ("play", "pause", "resume", "queue", "skip", "play", "stop", "leave"):
            interaction = self.interaction()
            command = self.client.tree.get_command(name, guild=guild)
            if name == "play":
                await command.callback(interaction, "demo")
            else:
                await command.callback(interaction)
            interaction.response.defer.assert_awaited_once()
            interaction.followup.send.assert_awaited_once()
            self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        self.assertFalse(self.voices[1].connected)

    async def test_dm_wrong_guild_and_missing_voice_rejected(self):
        for case in ("dm", "guild", "voice"):
            interaction = self.interaction()
            if case == "dm":
                interaction.guild = None
            elif case == "guild":
                interaction.guild_id = 999
            else:
                interaction.user.voice = None
            await self.client._dispatch(interaction, "play", "demo")
            interaction.response.send_message.assert_awaited_once()
            interaction.response.defer.assert_not_awaited()
        self.assertEqual(self.voices, {})

    async def test_cross_channel_command_is_rejected_after_acknowledgment(self):
        await self.client._dispatch(self.interaction(), "play", "demo")
        interaction = self.interaction(channel_id=11)
        await self.client._dispatch(interaction, "stop")
        self.assertIn("Join the bot's voice channel", interaction.followup.send.await_args.args[0])
        self.assertIsNotNone(self.voices[1].current)

    async def test_connector_checks_permissions_and_uses_supported_voice_interface(self):
        channel = Mock(spec=discord.VoiceChannel)
        channel.id = 10
        channel.connect = AsyncMock(return_value=FakeVoice(1, 10))
        permissions = SimpleNamespace(view_channel=True, connect=True, speak=False)
        channel.permissions_for.return_value = permissions
        guild = SimpleNamespace(me=object(), voice_client=None, get_channel=lambda _: channel)
        self.client.get_guild = Mock(return_value=guild)
        with self.assertRaises(DJError):
            await self.client._connect_voice(1, 10)
        channel.connect.assert_not_awaited()
        permissions.speak = True
        await self.client._connect_voice(1, 10)
        channel.connect.assert_awaited_once_with(timeout=20.0, reconnect=True, self_deaf=True)

    async def test_connector_refuses_automatic_move(self):
        channel = Mock(spec=discord.VoiceChannel)
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, connect=True, speak=True)
        guild = SimpleNamespace(me=object(), voice_client=FakeVoice(1, 11), get_channel=lambda _: channel)
        self.client.get_guild = Mock(return_value=guild)
        with self.assertRaises(DJError):
            await self.client._connect_voice(1, 10)
        self.assertEqual(guild.voice_client.disconnects, 0)

    async def test_cancelled_handshake_releases_registered_voice_client(self):
        channel = Mock(spec=discord.VoiceChannel)
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, connect=True, speak=True)
        guild = SimpleNamespace(me=object(), voice_client=None, get_channel=lambda _: channel)
        orphan = FakeVoice(1, 10)

        async def cancel(**kwargs):
            guild.voice_client = orphan
            raise asyncio.CancelledError()

        channel.connect = AsyncMock(side_effect=cancel)
        self.client.get_guild = Mock(return_value=guild)
        with self.assertRaises(asyncio.CancelledError):
            await self.client._connect_voice(1, 10)
        self.assertEqual(orphan.disconnects, 1)
        self.assertFalse(orphan.connected)

    async def test_errors_do_not_echo_raw_exception_text(self):
        self.client.playback.connector = AsyncMock(side_effect=RuntimeError("sensitive-looking synthetic text"))
        interaction = self.interaction()
        with self.assertLogs("panam_dj", level="WARNING") as log:
            await self.client._dispatch(interaction, "play", "demo")
        text = interaction.followup.send.await_args.args[0]
        self.assertNotIn("sensitive-looking", text + " ".join(log.output))

    async def test_ffmpeg_adapter_uses_verified_bytes_and_no_network_protocols(self):
        factory = FFmpegSourceFactory(self.catalog, "ffmpeg")
        with patch("panam_dj.discord_app.ManagedFFmpegOpusAudio") as opus:
            factory(self.catalog.tracks[0])
        args, kwargs = opus.call_args
        self.assertEqual(args[0].getvalue(), self.catalog.audio_bytes(self.catalog.tracks[0]))
        self.assertTrue(kwargs["pipe"])
        self.assertNotIn("codec", kwargs)  # Transcode WAV; do not copy its PCM stream.
        self.assertEqual(kwargs["before_options"], "-nostdin -protocol_whitelist pipe,fd -f wav")

    async def test_queue_output_stays_bounded_and_escapes_markdown(self):
        track = Track("x", "*" * 80)
        message = queue_message(QueueView(track, (track,) * 100, True, 10, None))
        self.assertLessEqual(len(message), 1900)
        self.assertIn("\\*", message)
        self.assertIn("90 more", message)

    async def test_correction_all_commands_reject_caller_move_during_acknowledgment(self):
        for action in ("play", "skip", "pause", "resume", "queue", "stop", "leave"):
            with self.subTest(action=action):
                await self.client.playback.play(1, 10, "demo")
                await self.client.playback.play(1, 10, "demo")
                if action == "resume":
                    await self.client.playback.control(1, 10, "pause")
                before = await self.client.playback.control(1, 10, "queue")
                voice = self.voices[1]
                history = list(voice.history)
                interaction = self.interaction()

                async def defer(**kwargs):
                    interaction.response.done = True
                    interaction.user.voice.channel = self.interaction(channel_id=11).user.voice.channel

                interaction.response.defer.side_effect = defer
                await self.client._dispatch(interaction, action, "demo")
                self.assertIn("Your voice channel changed", interaction.followup.send.await_args.args[0])
                self.assertEqual(await self.client.playback.control(1, 10, "queue"), before)
                self.assertEqual(voice.history, history)
                self.assertEqual(voice.disconnects, 0)
                await self.client.playback.control(1, 10, "leave")

    async def test_correction_all_commands_reject_caller_leave_while_waiting_for_guild_lock(self):
        for action in ("play", "skip", "pause", "resume", "queue", "stop", "leave"):
            with self.subTest(action=action):
                await self.client.playback.play(1, 10, "demo")
                if action == "resume":
                    await self.client.playback.control(1, 10, "pause")
                before = await self.client.playback.control(1, 10, "queue")
                interaction = self.interaction()
                acknowledged = asyncio.Event()

                async def defer(**kwargs):
                    interaction.response.done = True
                    acknowledged.set()

                interaction.response.defer.side_effect = defer
                async with self.client.playback._sessions[1].lock:
                    request = asyncio.create_task(self.client._dispatch(interaction, action, "demo"))
                    await acknowledged.wait()
                    interaction.user.voice = None
                await request
                self.assertIn("Your voice channel changed", interaction.followup.send.await_args.args[0])
                self.assertEqual(await self.client.playback.control(1, 10, "queue"), before)
                self.assertEqual(self.voices[1].disconnects, 0)
                await self.client.playback.control(1, 10, "leave")

    async def test_correction_play_rejects_caller_move_during_source_resolution(self):
        await self.client.playback.play(1, 10, "demo")
        before = await self.client.playback.control(1, 10, "queue")
        entered, release = asyncio.Event(), asyncio.Event()
        interaction = self.interaction()
        original = self.catalog.resolve

        async def resolve(query):
            entered.set()
            await release.wait()
            return await original(query)

        with patch.object(self.catalog, "resolve", side_effect=resolve):
            request = asyncio.create_task(self.client._dispatch(interaction, "play", "demo"))
            await entered.wait()
            interaction.user.voice.channel = self.interaction(channel_id=11).user.voice.channel
            release.set()
            await request
        self.assertIn("Your voice channel changed", interaction.followup.send.await_args.args[0])
        self.assertEqual(await self.client.playback.control(1, 10, "queue"), before)
        self.assertEqual(self.voices[1].history, ["demo"])

    async def test_correction_play_releases_new_connection_if_caller_leaves_during_handshake(self):
        entered, release = asyncio.Event(), asyncio.Event()
        interaction = self.interaction()
        voice = FakeVoice(1, 10)

        async def connect(*args):
            entered.set()
            await release.wait()
            return voice

        self.client.playback.connector = connect
        request = asyncio.create_task(self.client._dispatch(interaction, "play", "demo"))
        await entered.wait()
        interaction.user.voice = None
        release.set()
        await request
        self.assertIn("Your voice channel changed", interaction.followup.send.await_args.args[0])
        self.assertEqual(voice.history, [])
        self.assertEqual(voice.disconnects, 1)
        self.assertFalse(voice.connected)
        session = self.client.playback._sessions[1]
        self.assertIsNone(session.voice)
        self.assertIsNone(session.current)
        self.assertEqual(tuple(session.pending), ())

    async def test_correction_cancelled_concurrent_close_finishes_guilds_and_base_teardown_once(self):
        await self.client.playback.play(1, 10, "demo")
        await self.client.playback.play(2, 20, "demo")
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.voices[1].disconnect

        async def disconnect(**kwargs):
            entered.set()
            await release.wait()
            await original(**kwargs)

        self.voices[1].disconnect = disconnect
        with patch.object(discord.Client, "close", new_callable=AsyncMock) as base_close:
            cancelled = asyncio.create_task(self.client.close())
            await entered.wait()
            cancelled.cancel()
            await asyncio.sleep(0)
            other = asyncio.create_task(self.client.close())
            cancelled.cancel()
            await asyncio.sleep(0)
            self.assertFalse(cancelled.done())
            base_close.assert_not_awaited()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
            await other
            await self.client.close()
            base_close.assert_awaited_once()
        self.assertTrue(self.client.playback.closed)
        self.assertTrue(self.client._dj_close_task.done())
        self.assertTrue(self.client.playback._close_task.done())
        self.assertEqual([voice.disconnects for voice in self.voices.values()], [1, 1])
        for session in self.client.playback._sessions.values():
            self.assertIsNone(session.voice)
            self.assertIsNone(session.current)
            self.assertEqual(tuple(session.pending), ())

    async def test_correction_base_teardown_runs_even_if_manager_close_raises(self):
        with patch.object(self.client.playback, "close", new_callable=AsyncMock,
                          side_effect=RuntimeError("synthetic close failure")), \
                patch.object(discord.Client, "close", new_callable=AsyncMock) as base_close:
            with self.assertRaisesRegex(RuntimeError, "synthetic close failure"):
                await self.client.close()
            base_close.assert_awaited_once()


class ControlledAudioSource(FakeSource):
    """In-memory source; gates have safety deadlines so failed tests cannot hang."""

    def __init__(self):
        super().__init__(Track("controlled", "Controlled synthetic source"))
        self.read_entered = threading.Event()
        self.read_release = threading.Event()
        self.cleanup_entered = threading.Event()
        self.cleanup_release = threading.Event()
        self.fail_cleanup = False

    def read(self):
        self.read_entered.set()
        if not self.read_release.wait(5):
            raise AssertionError("Test did not release source.read")
        return b""

    def is_opus(self):
        return True

    def cleanup(self):
        self.cleanups += 1
        self.cleanup_entered.set()
        if not self.cleanup_release.wait(5):
            raise AssertionError("Test did not release source.cleanup")
        if self.fail_cleanup:
            raise RuntimeError("synthetic source cleanup failure")


class ThreadedVoice(FakeVoice):
    """Actual installed AudioPlayer; every transport operation stays in memory."""

    def __init__(self, guild_id, channel_id):
        super().__init__(guild_id, channel_id)
        self.players = []
        self.after_called = threading.Event()
        self.disconnected = asyncio.Event()

    def play(self, source, *, after):
        class OfflinePlayer(discord.player.AudioPlayer):
            def _speak(self, speaking):
                pass

        def completed(error):
            self.after_called.set()
            after(error)

        self.current = source
        self.history.append(source.track.key)
        player = OfflinePlayer(source, self, after=completed)
        self.players.append(player)
        player.start()

    def stop(self):
        for player in self.players:
            player.stop()
        self.current = None

    def send_audio_packet(self, *args, **kwargs):
        pass  # No socket, encoding or Discord connection exists in this fake.

    async def disconnect(self, *, force=False):
        await super().disconnect(force=force)
        self.disconnected.set()


@unittest.skipIf(discord is None, "discord.py unavailable; real AudioPlayer test needs existing dependency")
class SourceCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.source = ControlledAudioSource()
        self.voice = ThreadedVoice(1, 10)
        self.sources = [self.source]
        self.voices = [self.voice]
        self.thread_errors = []
        self.expected_thread_errors = 0
        hook = patch.object(threading, "excepthook", side_effect=self.thread_errors.append)
        hook.start()
        self.addCleanup(hook.stop)

        async def connect(*args):
            return self.voice

        self.manager = PlaybackManager(FakeProvider(), lambda track: self.source,
                                       connect, guild_ids=(1,), source_wrapper=DiscordOwnedSource)

    async def asyncTearDown(self):
        for source in self.sources:
            source.read_release.set()
            source.cleanup_release.set()
        for voice in self.voices:
            for player in voice.players:
                await asyncio.to_thread(player.join, 2)
                self.assertFalse(player.is_alive())
        await self.manager.close()
        self.assertEqual(len(self.thread_errors), self.expected_thread_errors)

    async def entered(self, event):
        self.assertTrue(await asyncio.to_thread(event.wait, 2))

    async def test_cleanup_barrier_waits_beyond_after_callback(self):
        await self.manager.play(1, 10, "controlled")
        await self.entered(self.source.read_entered)
        self.source.read_release.set()
        await self.entered(self.source.cleanup_entered)
        self.assertTrue(self.voice.after_called.is_set())
        self.assertTrue(self.voice.players[0].is_alive())
        closing = asyncio.create_task(self.manager.close())
        await self.voice.disconnected.wait()
        await self.manager.drain_callbacks()
        self.assertFalse(self.manager.closed, "close falsely succeeded before source cleanup finished")
        self.assertFalse(closing.done())
        self.source.cleanup_release.set()
        self.assertEqual(await closing, ())
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.source.cleanups, 1)

    async def test_cleanup_barrier_waits_for_blocked_read_and_cleans_once(self):
        await self.manager.play(1, 10, "controlled")
        await self.entered(self.source.read_entered)
        closing = asyncio.create_task(self.manager.close())
        await self.voice.disconnected.wait()
        await self.manager.drain_callbacks()
        self.assertFalse(self.manager.closed)
        self.assertFalse(closing.done())
        self.assertFalse(self.voice.after_called.is_set())
        self.assertEqual(self.source.cleanups, 0)
        self.source.cleanup_release.set()
        self.source.read_release.set()
        self.assertEqual(await closing, ())
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.source.cleanups, 1)

    async def test_cleanup_barrier_error_retains_ownership_and_original_error(self):
        self.source.fail_cleanup = True
        self.expected_thread_errors = 1
        self.source.cleanup_release.set()
        await self.manager.play(1, 10, "controlled")
        await self.entered(self.source.read_entered)
        self.source.read_release.set()
        await asyncio.to_thread(self.voice.players[0].join, 2)
        self.assertEqual(await self.manager.close(), (1,))
        self.assertFalse(self.manager.closed)
        owned = next(iter(self.manager._sources))
        error = owned.completion.result()
        self.assertIsInstance(error, RuntimeError)
        self.assertIs(error, self.thread_errors[0].exc_value)
        self.assertIn("Audio cleanup failed (RuntimeError)", self.manager._sessions[1].last_error)
        self.assertEqual(await self.manager.close(), (1,))
        self.assertEqual(self.source.cleanups, 1)
        self.assertEqual(self.voice.disconnects, 1)

    async def test_cleanup_barrier_timeout_is_bounded_and_later_close_reconciles(self):
        self.manager.cleanup_timeout = 0.02
        await self.manager.play(1, 10, "controlled")
        await self.entered(self.source.read_entered)
        self.source.read_release.set()
        await self.entered(self.source.cleanup_entered)
        self.assertEqual(await asyncio.wait_for(self.manager.close(), 1), (1,))
        self.assertFalse(self.manager.closed)
        self.assertEqual(len(self.manager._sources), 1)
        owned = next(iter(self.manager._sources))
        self.assertFalse(owned.completion.done())
        self.assertIn("timed out", self.manager._sessions[1].last_error)
        self.assertEqual(await self.manager.close(), (1,))
        self.assertEqual(self.source.cleanups, 1)
        self.assertEqual(self.voice.disconnects, 1)
        self.source.cleanup_release.set()
        await asyncio.wait_for(asyncio.shield(owned.completion), 1)
        self.assertEqual(await self.manager.close(), ())
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.manager._sources, {})
        self.assertEqual(self.voice.disconnects, 1)

    async def test_cleanup_barrier_cancellation_concurrent_close_cleans_other_guild_and_base(self):
        second = FakeVoice(2, 20)
        second_source = FakeSource(Track("second", "Second"))

        async def connect(guild_id, channel_id):
            return self.voice if guild_id == 1 else second

        self.manager = PlaybackManager(FakeProvider(),
                                       lambda track: self.source if track.key == "controlled" else second_source,
                                       connect, guild_ids=(1, 2), source_wrapper=DiscordOwnedSource)
        settings = Settings.load()
        client = DJClient(settings, LocalCatalog.load(settings.catalog_path), source_factory=FakeSource)
        client.playback = self.manager
        await self.manager.play(1, 10, "controlled")
        await self.manager.play(2, 20, "second")
        await self.entered(self.source.read_entered)
        self.source.read_release.set()
        await self.entered(self.source.cleanup_entered)
        with patch.object(discord.Client, "close", new_callable=AsyncMock) as base_close:
            closing = asyncio.create_task(client.close())
            await self.voice.disconnected.wait()
            await self.manager.drain_callbacks()
            closing.cancel()
            other = asyncio.create_task(client.close())
            await self.manager.drain_callbacks()
            closing.cancel()
            await self.manager.drain_callbacks()
            self.assertFalse(closing.done())
            self.assertFalse(other.done())
            self.assertFalse(second.connected)
            self.assertEqual(second_source.cleanups, 1)
            base_close.assert_not_awaited()
            self.source.cleanup_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            await other
            await client.close()
            base_close.assert_awaited_once()
        self.assertTrue(self.manager.closed)
        self.assertTrue(client._dj_close_task.done())
        self.assertTrue(self.manager._close_task.done())
        self.assertEqual((self.voice.disconnects, second.disconnects), (1, 1))
        self.assertEqual(self.source.cleanups, 1)
        before = self.manager._view(self.manager._sessions[1])
        self.voice.players[0].after(None)
        await self.manager.drain_callbacks()
        self.assertEqual(self.manager._view(self.manager._sessions[1]), before)
        self.assertEqual(self.manager._sources, {})
        self.assertEqual(self.manager._callbacks, set())

    async def test_cleanup_barrier_unplayed_source_cleanup_is_owned_and_does_not_block_loop(self):
        self.manager.cleanup_timeout = 0.02
        self.voice.play = Mock(side_effect=RuntimeError("synthetic play failure"))
        with self.assertRaises(DJError):
            await self.manager.play(1, 10, "controlled")
        await self.entered(self.source.cleanup_entered)
        self.assertFalse(self.source.cleanup_release.is_set())
        self.assertEqual(await self.manager.close(), (1,))
        self.assertFalse(self.manager.closed)
        self.assertEqual(len(self.manager._sources), 1)
        self.assertEqual(len(self.manager._cleanup_tasks), 1)
        self.source.cleanup_release.set()
        self.assertEqual(await self.manager.close(), ())
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.manager._cleanup_tasks, {})
        self.assertEqual(self.source.cleanups, 1)

    async def test_cleanup_barrier_cancelled_timeout_keeps_unresolved_ownership(self):
        self.manager.cleanup_timeout = 0.02
        await self.manager.play(1, 10, "controlled")
        await self.entered(self.source.read_entered)
        self.source.read_release.set()
        await self.entered(self.source.cleanup_entered)
        closing = asyncio.create_task(self.manager.close())
        await self.voice.disconnected.wait()
        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertTrue(self.manager._close_task.done())
        self.assertEqual(self.manager._close_task.result(), (1,))
        self.assertFalse(self.manager.closed)
        self.assertEqual(len(self.manager._sources), 1)
        self.assertIn("timed out", self.manager._sessions[1].last_error)
        self.source.cleanup_release.set()
        self.assertEqual(await self.manager.close(), ())
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.source.cleanups, 1)

    async def test_cleanup_barrier_preserves_wrapper_interface_and_exactly_once_cleanup(self):
        original = Mock(spec=discord.AudioSource)
        original.read.return_value = b"synthetic opus"
        original.is_opus.return_value = True
        original.cleanup.return_value = None
        source = DiscordOwnedSource(original, Track("sample", "Sample"))
        self.assertIsInstance(source, discord.AudioSource)
        self.assertEqual(source.read(), b"synthetic opus")
        self.assertTrue(source.is_opus())
        await asyncio.gather(asyncio.to_thread(source.cleanup), asyncio.to_thread(source.cleanup))
        self.assertIsNone(await source.completion)
        original.read.assert_called_once_with()
        original.is_opus.assert_called_once_with()
        original.cleanup.assert_called_once_with()

    async def test_cleanup_barrier_retains_sources_across_stop_skip_leave_and_natural_completion(self):
        for action in ("stop", "skip", "leave", "natural"):
            with self.subTest(action=action):
                first, second = ControlledAudioSource(), ControlledAudioSource()
                self.sources.extend((first, second))
                voice = ThreadedVoice(1, 10)
                self.voices.append(voice)

                async def connect(*args):
                    return voice

                manager = PlaybackManager(FakeProvider(), lambda track: first if track.key == "first" else second,
                                          connect, guild_ids=(1,), source_wrapper=DiscordOwnedSource)
                try:
                    await manager.play(1, 10, "first")
                    await self.entered(first.read_entered)
                    await manager.play(1, 10, "second")
                    if action != "natural":
                        await manager.control(1, 10, action)
                    first.read_release.set()
                    await self.entered(first.cleanup_entered)
                    await manager.drain_callbacks()
                    if action in ("skip", "natural"):
                        await self.entered(second.read_entered)
                        self.assertEqual(voice.history, ["first", "second"])
                    else:
                        self.assertEqual(voice.history, ["first"])
                    closing = asyncio.create_task(manager.close())
                    await voice.disconnected.wait()
                    await manager.drain_callbacks()
                    self.assertFalse(manager.closed)
                    second.read_release.set()
                    second.cleanup_release.set()
                    first.cleanup_release.set()
                    self.assertEqual(await closing, ())
                    self.assertTrue(manager.closed)
                    self.assertEqual(first.cleanups, 1)
                    self.assertEqual(second.cleanups, int(action in ("skip", "natural")))
                    self.assertEqual(voice.disconnects, 1)
                finally:
                    first.read_release.set()
                    first.cleanup_release.set()
                    second.read_release.set()
                    second.cleanup_release.set()
                    await manager.close()
