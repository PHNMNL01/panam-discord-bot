import asyncio
import base64
import contextlib
import importlib
import io
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from panam_companion.audio import Converter, DISCORD_FRAME, InputBuffer, OutputBuffer, SpeechGate
from panam_companion.budget import Budget, ProcessLock
from panam_companion.config import Config, FIELDS, SafeError, load_config
from panam_companion.discord_adapter import (Controller, ReceiveSink, TestVoiceClient,
                                             allowed_control, allowed_members, make_client)
from panam_companion.live import LiveSession, Transcript, open_socket, session_config

CONFIG = Config("synthetic_key", "synthetic_bot", 100, 200, 300, 400, 500)
ROOT = Path(__file__).resolve().parents[1]


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        # Temporary fixture writes remain inside the isolated experiment.
        folder = ROOT / "runtime" / "offline-tests"
        folder.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=folder)
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)


class ConfigTests(TemporaryTest):
    def fixture(self):
        values = ["synthetic_key", "synthetic_bot", "100", "200", "300", "400", "500"]
        fields = ["PANAM_OPENAI_API_KEY", "PANAM_DISCORD_BOT_TOKEN", "PANAM_TEST_BOT_ID",
                  "PANAM_TEST_GUILD_ID", "PANAM_TEST_VOICE_CHANNEL_ID",
                  "PANAM_TEST_CONTROL_CHANNEL_ID", "PANAM_TEST_USER_ID"]
        path = self.directory / ".env"
        path.write_text("\n".join(f"{key}={value}" for key, value in zip(fields, values)), encoding="utf-8")
        return path

    def test_missing_configuration_fails_closed(self):
        with self.assertRaises(SafeError):
            load_config(self.directory / "absent", {})

    def test_valid_file_and_secret_free_repr(self):
        loaded = load_config(self.fixture(), {})
        self.assertEqual(loaded.user_id, 500)
        self.assertNotIn("synthetic", repr(loaded))

    def test_ambient_credentials_and_endpoints_rejected(self):
        for name in ["OPENAI_API_KEY", "OPENAI_BASE_URL", "DISCORD_TOKEN", *FIELDS]:
            with self.subTest(name=name), self.assertRaises(SafeError) as error:
                load_config(self.fixture(), {name: "secret_should_never_appear"})
            self.assertNotIn("secret_should_never_appear", str(error.exception))

    def test_bad_ids_unknown_fields_and_templates_rejected(self):
        for suffix in ["\nEXTRA=bad", "\nPANAM_TEST_USER_ID=", "\nPANAM_TEST_USER_ID=300",
                       "\nPANAM_OPENAI_API_KEY=REPLACE_LOCALLY"]:
            path = self.fixture()
            path.write_text(path.read_text() + suffix)
            with self.subTest(suffix=suffix), self.assertRaises(SafeError):
                load_config(path, {})


class BudgetTests(TemporaryTest):
    def test_reservation_survives_restart_and_blocks_second_session(self):
        budget = Budget(self.directory)
        self.assertEqual(budget.reserve_session(), 300)
        with self.assertRaises(SafeError):
            budget.reserve_session()
        with self.assertRaises(SafeError):
            Budget(self.directory).reserve_session()
        budget.finish_session(30.1, 29, True)
        restarted = Budget(self.directory)
        self.assertEqual(restarted.data["seconds"], 31)
        self.assertFalse(restarted.data["uncertain"])

    def test_uncertain_close_and_invalid_usage_do_not_refund(self):
        for confirmed, observed in [(False, 2), (True, math.nan), (True, -1)]:
            path = self.directory / str(confirmed) / str(observed)
            budget = Budget(path)
            budget.reserve_session()
            budget.finish_session(2, observed, confirmed)
            self.assertEqual(budget.data["seconds"], 300)
            self.assertTrue(budget.data["uncertain"])

    def test_total_duration_and_backend_reservations(self):
        budget = Budget(self.directory)
        budget.reserve_session()
        for _ in range(40):
            budget.reserve_backend()
        with self.assertRaises(SafeError):
            budget.reserve_backend()
        budget.finish_session(299.2, 299, True)
        budget.reserve_session()
        self.assertLessEqual(budget.reserved_dollars, 0.91)
        budget.finish_session(299.2, 299, True)
        with self.assertRaises(SafeError):
            budget.reserve_session()

    def test_counters_corruption_blocks(self):
        (self.directory / "budget.json").write_text('{"bad":"synthetic"}')
        with self.assertRaises(SafeError):
            Budget(self.directory)

    def test_single_process_lock(self):
        with ProcessLock(self.directory):
            with self.assertRaises(SafeError):
                with ProcessLock(self.directory):
                    pass
        with ProcessLock(self.directory):
            pass


class AudioTests(unittest.TestCase):
    def test_resampler_streaming_matches_continuous(self):
        pcm = b"".join(struct.pack("<hh", i % 20000, -(i % 20000)) for i in range(9600))
        whole = Converter().to_live(pcm)
        converter = Converter()
        chunks = b"".join(converter.to_live(pcm[i:i + 3840]) for i in range(0, len(pcm), 3840))
        self.assertEqual(chunks, whole)
        self.assertEqual(len(chunks), 9600)
        mono = b"".join(struct.pack("<h", i % 2000) for i in range(4800))
        up = Converter()
        self.assertEqual(b"".join(up.to_discord(mono[i:i + 960]) for i in range(0, len(mono), 960)),
                         Converter().to_discord(mono))

    def test_input_bounds_age_and_mute_race(self):
        clock = [0.0]
        buffer = InputBuffer(max_frames=2, clock=lambda: clock[0])
        buffer.enable(True)
        epoch = buffer.epoch
        for value in range(3):
            buffer.put(bytes([value]) * DISCORD_FRAME, epoch)
        self.assertEqual(buffer.dropped, 1)
        buffer.enable(False)
        buffer.put(bytes([3]) * DISCORD_FRAME, epoch)
        self.assertEqual(buffer.take(), bytes(DISCORD_FRAME))
        buffer.enable(True)
        buffer.put(bytes([4]) * DISCORD_FRAME, buffer.epoch)
        clock[0] = 1
        self.assertEqual(buffer.take(), bytes(DISCORD_FRAME))

    def test_output_streaming_generation_invalidation_and_nonblocking_close(self):
        output = OutputBuffer()
        output.permit()
        pcm = struct.pack("<h", 1000) * 960
        old = output.generation
        output.append(pcm, old)
        self.assertNotEqual(output.read(), bytes(DISCORD_FRAME))
        output.invalidate()
        output.append(pcm, old)
        self.assertEqual(output.read(), bytes(DISCORD_FRAME))
        output.permit()
        output.append(pcm, old)
        self.assertEqual(output.read(), bytes(DISCORD_FRAME))
        output.close()
        self.assertEqual(output.read(), b"")

    def test_output_overload_is_explicit_and_bounded(self):
        output = OutputBuffer(max_frames=2)
        output.permit()
        with self.assertRaises(BufferError):
            output.append(bytes(960 * 5), output.generation)
        self.assertLessEqual(len(output.frames), 2)

    def test_startup_markers_survive_later_segments(self):
        clock = [1.0]
        output = OutputBuffer(clock=lambda: clock[0])
        output.permit()
        pcm = struct.pack("<h", 1000) * 960
        output.append(pcm, output.generation)
        output.read()
        output.invalidate(False)
        clock[0] = 2.0
        output.append(pcm, output.generation)
        output.read()
        self.assertEqual(output.first_queued, 1)
        self.assertEqual(output.first_read, 1)
        self.assertEqual(output.markers[-1]["monotonic"], 2)

    def test_vad_onset_hangover_and_no_wakeword_claim(self):
        vad = SpeechGate()
        speech = struct.pack("<h", 2000) * 480
        self.assertFalse(vad.update(speech, 0))
        self.assertTrue(vad.update(speech, .02))
        vad.update(bytes(960), .2)
        self.assertTrue(vad.speaking)
        vad.update(bytes(960), .3)
        self.assertFalse(vad.speaking)

    def test_actual_bundled_opus_roundtrip(self):
        import discord
        encoded = discord.opus.Encoder().encode(bytes(DISCORD_FRAME), 960)
        self.assertEqual(len(discord.opus.Decoder().decode(encoded)), DISCORD_FRAME)


class DiscordTests(unittest.TestCase):
    def channel(self, ids=(100, 500)):
        return NS(id=300, guild=NS(id=200), members=[NS(id=i) for i in ids])

    def test_control_and_broadcast_allowlists(self):
        valid = NS(guild_id=200, channel_id=400, user=NS(id=500, bot=False))
        self.assertTrue(allowed_control(CONFIG, valid))
        for key, wrong in [("guild_id", 201), ("channel_id", 401)]:
            bad = NS(**vars(valid))
            setattr(bad, key, wrong)
            self.assertFalse(allowed_control(CONFIG, bad))
        for user in [NS(id=501, bot=False), NS(id=100, bot=True), NS(id=500, bot=True)]:
            self.assertFalse(allowed_control(CONFIG, NS(guild_id=200, channel_id=400, user=user)))
        self.assertTrue(allowed_members(CONFIG, self.channel()))
        for ids in [(100, 500, 900), (100,), (), (500,)]:
            self.assertFalse(allowed_members(CONFIG, self.channel(ids)))

    def sink_fixture(self):
        calls = []
        dave = NS(ready=True, can_passthrough=lambda user: False,
                  decrypt=lambda user, kind, data: calls.append((user, data)) or b"opus")
        voice = NS(channel=self.channel(), _connection=NS(can_encrypt=True, dave_session=dave))
        buffer = InputBuffer()
        buffer.enable(True)
        sink = ReceiveSink(CONFIG, voice, buffer)
        return sink, voice, buffer, calls

    def test_receive_filters_before_decryption(self):
        sink, voice, buffer, calls = self.sink_fixture()
        data = NS(opus=b"ciphertext", packet=True)
        for user in [None, NS(id=501, bot=False), NS(id=500, bot=True), NS(id=100, bot=True)]:
            sink.write(user, data)
        self.assertEqual(calls, [])
        voice.channel = self.channel((100, 500, 900))
        sink.write(NS(id=500, bot=False), data)
        self.assertEqual(calls, [])

    def test_dave_before_opus_and_muted_generation(self):
        sink, voice, buffer, calls = self.sink_fixture()
        with patch("discord.opus.Decoder") as decoder:
            decoder.return_value.decode.return_value = bytes([1]) * DISCORD_FRAME
            sink.write(NS(id=500, bot=False), NS(opus=b"ciphertext", packet=True))
            decoder.return_value.decode.assert_called_once_with(b"opus", fec=False)
            self.assertEqual(calls, [(500, b"ciphertext")])
            self.assertEqual(buffer.take(), bytes([1]) * DISCORD_FRAME)
            buffer.enable(False)
            sink.write(NS(id=500, bot=False), NS(opus=b"ciphertext", packet=True))
            self.assertEqual(len(calls), 1)

    def test_dave_failures_never_fall_back_to_plaintext(self):
        sink, voice, buffer, calls = self.sink_fixture()
        user, data = NS(id=500, bot=False), NS(opus=b"encrypted", packet=True)
        voice._connection.dave_session.ready = False
        sink.write(user, data)
        self.assertEqual(calls, [])
        voice._connection.dave_session.ready = True
        voice._connection.dave_session.decrypt = lambda *args: (_ for _ in ()).throw(ValueError("sensitive"))
        with patch("discord.opus.Decoder") as decoder:
            sink.write(user, data)
            decoder.assert_not_called()
        self.assertEqual(sink.crypto_failures, 1)
        self.assertEqual(buffer.take(), bytes(DISCORD_FRAME))

    def test_outbound_encrypts_and_rejects_extra_listener(self):
        voice = NS(companion_config=CONFIG, companion_enabled=True,
                   channel=self.channel(), sequence=3, timestamp=960, ssrc=42,
                   mode="aead_xchacha20_poly1305_rtpsize",
                   _connection=NS(can_encrypt=True, dave_session=NS(ready=True, encrypt_opus=lambda x: b"DAVE" + x)))
        voice._encrypt_aead_xchacha20_poly1305_rtpsize = lambda head, body: bytes(head) + body
        packet = TestVoiceClient._get_voice_packet(voice, b"opus")
        self.assertTrue(packet.endswith(b"DAVEopus"))
        voice.channel = self.channel((100, 500, 999))
        with self.assertRaises(RuntimeError):
            TestVoiceClient._get_voice_packet(voice, b"opus")
        voice.channel = self.channel()
        voice._connection.can_encrypt = False
        with self.assertRaises(RuntimeError):
            TestVoiceClient._get_voice_packet(voice, b"opus")


class MockSocket:
    def __init__(self, finalize=True):
        self.events = asyncio.Queue()
        self.sent = []
        self.finalize = finalize
        self.closed = False

    async def send(self, raw):
        event = json.loads(raw)
        self.sent.append(event)
        if event["type"] == "session.start":
            await self.events.put({"type": "session.started", "session": event["session"]})
        if event["type"] == "session.close" and self.finalize:
            await self.events.put({"type": "session.closed", "usage": {"seconds": 0.1}})
        if event["type"] in {"session.input_audio.mute", "session.input_audio.unmute"}:
            await self.events.put({"type": event["type"] + "d"})

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        return json.dumps(event)

    async def close(self):
        self.closed = True
        await self.events.put(None)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = ROOT / "runtime" / "offline-tests"
        folder.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=folder)
        self.directory = Path(self.temp.name)
        self.budget = Budget(self.directory)
        self.socket = MockSocket()

        async def connect(key):
            return self.socket

        async def backend(key, context):
            return "Patnáct.", {"input_tokens": 10, "output_tokens": 3}

        self.session = LiveSession(CONFIG, self.budget, connect=connect, backend=backend)

    async def asyncTearDown(self):
        if self.session.began is not None:
            await self.session.close()
        self.temp.cleanup()

    async def test_start_gate_pacing_stop_and_no_owned_tasks(self):
        self.assertEqual(self.socket.sent, [])
        await self.session.start()
        await asyncio.sleep(.065)
        events = self.socket.sent
        self.assertEqual(events[0]["type"], "session.start")
        audio = [e for e in events if e["type"] == "session.input_audio.append"]
        self.assertGreaterEqual(len(audio), 2)
        self.assertLessEqual(len(audio), 6)
        self.assertTrue(all(len(base64.b64decode(e["audio"])) == 960 for e in audio))
        await self.session.close()
        self.assertTrue(self.session.finalized.is_set())
        self.assertTrue(self.socket.closed)
        self.assertTrue(all(t.done() for t in self.session.tasks))
        self.assertFalse(self.session.input.enabled)
        self.assertEqual(self.session.output.read(), b"")

    async def test_mute_stops_input_and_drops_buffer(self):
        await self.session.start()
        await self.session.mute(True)
        before = sum(e["type"] == "session.input_audio.append" for e in self.socket.sent)
        await asyncio.sleep(.045)
        self.assertEqual(before, sum(e["type"] == "session.input_audio.append" for e in self.socket.sent))
        self.assertFalse(self.session.input.enabled)
        await self.session.mute(False)
        self.assertTrue(self.session.input.enabled)

    async def test_provider_error_is_sanitized_and_stops_input(self):
        await self.session.start()
        await self.socket.events.put({"type": "error", "error": {"message": "synthetic_secret"}})
        await asyncio.wait_for(self.session.failed.wait(), .5)
        self.assertEqual(self.session.fault, "provider_error")
        self.assertNotIn("synthetic_secret", str(self.session.metrics))
        self.assertFalse(self.session.input.enabled)

    async def test_timeout_guard(self):
        await self.session.start()
        self.session.began -= 286
        await asyncio.wait_for(self.session.failed.wait(), .5)
        self.assertEqual(self.session.fault, "duration_limit")

    async def test_connection_lost_blocks_next_paid_session(self):
        await self.session.start()
        await self.socket.events.put(None)
        await asyncio.wait_for(self.session.failed.wait(), .5)
        # Final close acknowledgment is absent; don't wait eight real seconds in test.
        self.session.ws = None
        await self.session.close()
        self.assertTrue(self.budget.data["uncertain"])
        with self.assertRaises(SafeError):
            self.budget.reserve_session()

    async def test_backend_uses_current_context_and_discards_interrupted_result(self):
        began, release = asyncio.Event(), asyncio.Event()

        async def backend(key, transcript):
            self.assertIn("sedm", transcript)
            began.set()
            await release.wait()
            return "Patnáct.", {"input_tokens": 10, "output_tokens": 3}

        self.session.backend = backend
        await self.session.start()
        self.session.transcript.append("user", {"delta": "Kolik je sedm plus osm?"})
        await self.session.queue_delegation({"delegation": {"id": "synthetic_1", "target": "client"}})
        await asyncio.wait_for(began.wait(), .5)
        self.session.interrupt()
        release.set()
        await asyncio.sleep(.05)
        self.assertFalse(any(e["type"] == "session.commentary.append" for e in self.socket.sent))
        self.assertEqual(self.budget.data["backend_calls"], 1)
        self.assertTrue(any(e["stage"] == "stale_backend_result_discarded" for e in self.session.metrics))

    async def test_backend_no_parallel_submissions_and_latest_pending(self):
        active, peak = 0, 0

        async def backend(key, transcript):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.04)
            active -= 1
            return "Odpověď.", {}

        self.session.backend = backend
        await self.session.start()
        self.session.transcript.append("user", {"delta": "Otázka"})
        for index in range(4):
            await self.session.queue_delegation({"delegation": {"id": str(index), "target": "client"}})
        await asyncio.sleep(.15)
        self.assertEqual(peak, 1)
        self.assertLessEqual(self.budget.data["backend_calls"], 2)

    async def test_backend_timeout_is_uncertain_and_blocks_new_session(self):
        async def backend(key, transcript):
            raise TimeoutError("synthetic provider timeout")
        self.session.backend = backend
        await self.session.start()
        self.session.transcript.append("user", {"delta": "Otázka"})
        await self.session.queue_delegation({"delegation": {"id": "test_timeout", "target": "client"}})
        await asyncio.wait_for(self.session.failed.wait(), .5)
        await self.session.close()
        self.assertTrue(self.session.backend_uncertain)
        self.assertTrue(self.budget.data["uncertain"])
        self.assertEqual(self.budget.data["backend_reserved_usd"], .01)

    async def test_backend_waits_for_transcript_of_new_speech(self):
        requested = asyncio.Event()
        async def backend(key, transcript):
            self.assertIn("nová otázka", transcript)
            requested.set()
            return "Odpověď.", {}
        self.session.backend = backend
        await self.session.start()
        self.session.last_user_onset = time.monotonic()
        self.session.transcript.append("user", {"delta": "stará otázka"})
        await self.session.queue_delegation({"delegation": {"id": "fresh", "target": "client"}})
        await asyncio.sleep(.04)
        self.assertFalse(requested.is_set())
        await self.socket.events.put({"type": "session.input_transcript.delta", "delta": "nová otázka"})
        await asyncio.wait_for(requested.wait(), .5)

    async def test_unexpected_resolved_model_is_rejected_before_input(self):
        original = self.socket.send
        async def wrong_start(raw):
            event = json.loads(raw)
            if event["type"] == "session.start":
                event["session"]["model"] = "wrong-model"
                await self.socket.events.put({"type": "session.started", "session": event["session"]})
            else:
                await original(raw)
        self.socket.send = wrong_start
        with self.assertRaises(SafeError):
            await self.session.start()
        self.assertFalse(any(e["type"] == "session.input_audio.append" for e in self.socket.sent))
        self.assertFalse(self.session.input.enabled)
        self.assertTrue(self.session.finalized.is_set())
        self.assertFalse(self.budget.data["uncertain"])

    async def test_bad_audio_keeps_receiver_for_finalization(self):
        await self.session.start()
        await self.socket.events.put({"type": "session.output_audio.delta", "delta": "invalid!"})
        await asyncio.wait_for(self.session.failed.wait(), .5)
        await self.session.close()
        self.assertTrue(self.session.finalized.is_set())
        self.assertFalse(self.budget.data["uncertain"])

    async def test_close_clears_session_context(self):
        await self.session.start()
        self.session.transcript.append("user", {"delta": "synthetic private context"})
        await self.session.close()
        self.assertEqual(self.session.transcript.render(), "[]")

    async def test_discord_command_registration_is_guild_only_without_network(self):
        client = make_client(CONFIG, self.budget)
        await client._async_setup_hook()
        await client.setup_hook()
        self.assertEqual(client.tree.get_commands(), [])
        import discord
        self.assertEqual(len(client.tree.get_commands(guild=discord.Object(id=CONFIG.guild_id))), 1)
        self.assertFalse(client.intents.message_content)
        self.assertFalse(client.intents.members)
        await client.close()

    async def test_cancelled_discord_handshake_retains_cleanup_ownership(self):
        import discord
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.id, channel.guild = 300, NS(id=200)
        channel.members = [NS(id=500)]
        registered = []
        began = asyncio.Event()
        owned = NS(companion_enabled=False, stop=lambda: None,
                   disconnect=AsyncMock(), _reader=None, _player=None)
        async def handshake(cls, **kwargs):
            registered.append(cls(None, channel))
            began.set()
            await asyncio.Event().wait()
        channel.connect = handshake
        controller = Controller(NS(user=NS(id=100), get_channel=lambda identity: channel), CONFIG, self.budget)
        with patch("panam_companion.discord_adapter.TestVoiceClient", return_value=owned):
            start = asyncio.create_task(controller.start())
            await asyncio.wait_for(began.wait(), .5)
            await asyncio.wait_for(controller.stop(), 1)
        self.assertEqual(registered, [owned])
        owned.disconnect.assert_awaited_once_with(force=True)
        self.assertIsNone(controller.voice)
        self.assertTrue(start.done())
        self.assertEqual(self.budget.data["seconds"], 0)

    async def test_repeated_start_is_rejected_without_connection(self):
        controller = Controller(NS(), CONFIG, self.budget)
        controller.session = object()
        with self.assertRaises(SafeError):
            await controller.start()

    async def test_redirect_is_not_followed(self):
        from websockets.asyncio.client import connect
        from websockets.datastructures import Headers
        from websockets.exceptions import InvalidStatus
        from websockets.http11 import Response
        redirect = InvalidStatus(Response(307, "Temporary Redirect", Headers({"Location": "wss://example.invalid/"})))
        connection = NS(handshake=AsyncMock(side_effect=redirect), transport=NS(abort=lambda: None))
        with patch.object(connect, "open_tcp_connection", AsyncMock(return_value=connection)) as attempt:
            with self.assertRaises(InvalidStatus):
                await open_socket("synthetic_key")
        self.assertEqual(attempt.await_count, 1)


class BenchmarkTests(unittest.TestCase):
    def rows(self):
        return [{"id": f"Q{i:02}", "attempt": "1", "status": "PASS", "audible_seconds": "2",
                 "uncertainty_seconds": "0.3", "method": "human_stopwatch"} for i in range(1, 21)]

    def test_actual_human_timings_percentiles_and_failure_count(self):
        from panam_companion.benchmark import summarize
        rows = self.rows()
        self.assertTrue(summarize(rows)["status"].startswith("PASS"))
        rows[-1]["status"] = "FAIL"
        result = summarize(rows)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["successes"], 19)
        self.assertEqual(result["metrics_seconds"]["p95"], 2)
        self.assertEqual(result["metrics_seconds"]["max"], "infinity (failed trial)")

    def test_proxy_missing_or_borderline_cannot_pass(self):
        from panam_companion.benchmark import summarize
        for field, value in [("method", "server_enqueue"), ("status", "NOT_TESTED"), ("audible_seconds", "2.9")]:
            rows = self.rows()
            for row in rows:
                row[field] = value
            self.assertEqual(summarize(rows)["status"], "NOT VERIFIED")

    def test_missing_ids_or_retake_substitution_rejected(self):
        from panam_companion.benchmark import summarize
        rows = self.rows()
        rows[-1]["attempt"] = "2"
        with self.assertRaises(ValueError):
            summarize(rows)


class IsolationTests(unittest.TestCase):
    def test_imports_do_not_create_threads_or_network(self):
        script = '''
import importlib, socket, threading
from unittest.mock import patch
before = set(threading.enumerate())
with patch.object(socket.socket, 'connect', side_effect=AssertionError('network on import')):
    for name in ['config', 'budget', 'audio', 'live', 'discord_adapter', '__main__']:
        importlib.import_module('panam_companion.' + name)
assert set(threading.enumerate()) == before
'''
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT.parent,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_legacy_or_development_loop_runtime_imports(self):
        import ast
        for file in ROOT.glob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [n.name for n in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(any(n.startswith(("panam_ai", "panam_discord", "panam_core", "panam_memory"))
                                     or "development_loop" in n for n in names), file.name)

    def test_transcript_memory_is_bounded_and_preserves_fragments(self):
        transcript = Transcript()
        transcript.append("user", {"delta": "Ahoj "})
        transcript.append("user", {"delta": "Panam"})
        self.assertEqual([f["text"] for f in transcript.fragments], ["Ahoj ", "Panam"])
        for _ in range(200):
            transcript.append("assistant", {"delta": "Ž" * 1000})
        self.assertLessEqual(len(transcript.render().encode("utf-8")), 6000)


if __name__ == "__main__":
    unittest.main()
