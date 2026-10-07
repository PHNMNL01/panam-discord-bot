"""Local audio verification: fake streams, synthetic PCM, fake provider, no files."""
import asyncio
import contextlib
import io
import struct
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from panam_companion.audio import DISCORD_FRAME, InputBuffer, OutputBuffer
from panam_companion.config import SafeError
from panam_companion.live import LiveSession
from panam_companion.local_audio import Microphone, Playback, MAX_CALLBACK_SAMPLES, devices
from panam_companion.local_runtime import LocalController, LISTENING_OFF, LOCAL_LISTENING, ACTIVE
from panam_companion.tests.test_audio_boundary import CONFIG, FakeSocket, fake_budget


class FakeStream:
    def __init__(self, backend, kind, settings):
        self.backend, self.kind, self.settings = backend, kind, settings
        self.active = self.closed = False

    def start(self):
        self.backend.order.append(self.kind + ".start")
        if self.backend.start_hook:
            self.backend.start_hook(self.kind)
        if self.backend.fail_start == self.kind:
            raise RuntimeError("synthetic stream start failure")
        self.active = True

    def abort(self):
        self.backend.order.append(self.kind + ".abort")
        self.active = False

    def close(self):
        self.backend.order.append(self.kind + ".close")
        if self.backend.close_hook:
            self.backend.close_hook(self.kind)
        if self.backend.fail_close == self.kind:
            raise RuntimeError("synthetic stream close failure")
        self.closed = True
        self.active = False


class FakeBackend:
    class CallbackAbort(Exception):
        pass

    def __init__(self):
        self.default = NS(device=(0, 1))
        self.inventory = [
            dict(index=0, name="Fake microphone", hostapi=0, max_input_channels=2,
                 max_output_channels=0, default_samplerate=48000),
            dict(index=1, name="Fake headphones", hostapi=0, max_input_channels=0,
                 max_output_channels=2, default_samplerate=48000),
        ]
        self.inputs, self.outputs, self.order, self.checks = [], [], [], []
        self.fail_settings = self.fail_start = self.fail_close = self.start_hook = self.close_hook = None

    def query_devices(self, device=None):
        return self.inventory if device is None else self.inventory[device]

    def check(self, kind, settings):
        self.checks.append((kind, settings))
        if self.fail_settings == kind:
            raise ValueError("synthetic unsupported sample rate")

    def check_input_settings(self, **settings):
        self.check("input", settings)

    def check_output_settings(self, **settings):
        self.check("output", settings)

    def RawInputStream(self, **settings):
        stream = FakeStream(self, "input", settings)
        self.inputs.append(stream)
        return stream

    def RawOutputStream(self, **settings):
        stream = FakeStream(self, "output", settings)
        self.outputs.append(stream)
        return stream


class InputTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.buffer = InputBuffer()
        self.mic = Microphone(self.buffer, backend=self.backend)
        self.mic.start()
        self.addCleanup(self.mic.close)

    def emit(self, pcm, frames):
        self.mic.callback(pcm, frames, None, False)

    def test_mono_upmix_is_exact_canonical_pcm(self):
        self.mic.set_forwarding(True)
        mono = b"".join(struct.pack("<h", i - 480) for i in range(960))
        self.emit(mono, 960)
        expected = b"".join(struct.pack("<hh", i - 480, i - 480) for i in range(960))
        self.assertEqual(self.buffer.take(), expected)
        self.assertEqual(len(expected), DISCORD_FRAME)
        settings = self.backend.inputs[0].settings
        self.assertEqual((settings["samplerate"], settings["channels"], settings["dtype"]), (48000, 1, "int16"))

    def test_stereo_input_is_unchanged(self):
        self.mic.close()
        self.mic = Microphone(self.buffer, backend=self.backend, channels=2)
        self.mic.start()
        self.addCleanup(self.mic.close)
        self.mic.set_forwarding(True)
        stereo = struct.pack("<hh", -100, 200) * 960
        self.emit(stereo, 960)
        self.assertEqual(self.buffer.take(), stereo)

    def test_small_callbacks_accumulate(self):
        self.mic.set_forwarding(True)
        self.emit(struct.pack("<h", 10) * 400, 400)
        self.assertEqual(len(self.buffer.frames), 0)
        self.emit(struct.pack("<h", 20) * 560, 560)
        self.assertEqual(self.buffer.take(), struct.pack("<hh", 10, 10) * 400 + struct.pack("<hh", 20, 20) * 560)
        self.assertEqual(self.mic.pending, b"")

    def test_large_callbacks_split_and_retain_only_partial_frame(self):
        self.mic.set_forwarding(True)
        self.emit(struct.pack("<h", 12) * 2400, 2400)
        self.assertEqual(len(self.buffer.frames), 2)
        self.assertEqual(len(self.mic.pending), 1920)
        self.assertEqual(self.buffer.take(), struct.pack("<hh", 12, 12) * 960)
        self.assertEqual(self.buffer.take(), struct.pack("<hh", 12, 12) * 960)
        self.emit(struct.pack("<h", 15) * 480, 480)
        self.assertEqual(self.buffer.take(), struct.pack("<hh", 12, 12) * 480 + struct.pack("<hh", 15, 15) * 480)

    def test_inactive_capture_discards_without_buffering(self):
        for _ in range(100):
            self.emit(bytes(4800), 2400)
        self.assertTrue(self.mic.active)
        self.assertEqual(self.mic.pending, b"")
        self.assertEqual(len(self.buffer.frames), 0)

    def test_mute_discards_partial_and_cannot_be_bypassed_by_live_pacing(self):
        self.mic.set_forwarding(True)
        self.emit(struct.pack("<h", 1) * 480, 480)
        self.mic.set_muted(True)
        self.buffer.enable(True)  # Live pacing recovery may independently enable its queue.
        self.emit(struct.pack("<h", 2) * 960, 960)
        self.assertEqual(len(self.buffer.frames), 0)
        self.assertEqual(self.mic.pending, b"")
        self.mic.set_muted(False)
        self.emit(struct.pack("<h", 3) * 960, 960)
        self.assertEqual(self.buffer.take(), struct.pack("<hh", 3, 3) * 960)

    def test_admission_epoch_change_discards_old_partial_frame(self):
        self.mic.set_forwarding(True)
        self.emit(bytes(960), 480)
        self.buffer.enable(True)
        self.emit(struct.pack("<h", 5) * 960, 960)
        self.assertEqual(self.buffer.take(), struct.pack("<hh", 5, 5) * 960)

    def test_invalid_callback_fails_closed_and_bounds_allocation(self):
        self.mic.set_forwarding(True)
        self.mic.callback(b"", MAX_CALLBACK_SAMPLES + 1, None, False)
        self.assertTrue(self.mic.failed.is_set())
        self.assertFalse(self.buffer.enabled)
        self.assertEqual(self.mic.pending, b"")


class OutputDeviceTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.buffer = OutputBuffer()
        self.buffer.permit()
        self.output = Playback(self.buffer, backend=self.backend)
        self.output.start()
        self.addCleanup(self.output.close)

    def emit(self, frames):
        data = bytearray(frames * 4)
        self.output.callback(data, frames, None, False)
        return bytes(data)

    def test_output_preserves_frames_across_callback_sizes(self):
        first, second = bytes([1]) * DISCORD_FRAME, bytes([2]) * DISCORD_FRAME
        self.buffer.frames.extend((first, second))
        self.assertEqual(self.emit(400) + self.emit(1520), first + second)
        self.assertEqual(self.output.pending, b"")
        self.assertEqual(self.emit(960), bytes(DISCORD_FRAME))
        self.assertEqual(self.backend.outputs[0].settings["channels"], 2)

    def test_interruption_discards_adapter_partial_frame(self):
        self.buffer.frames.append(bytes([1]) * DISCORD_FRAME)
        self.emit(480)
        self.buffer.invalidate()
        self.assertEqual(self.emit(480), bytes(1920))
        self.buffer.permit()
        self.buffer.frames.append(bytes([2]) * DISCORD_FRAME)
        self.assertEqual(self.emit(960), bytes([2]) * DISCORD_FRAME)

    def test_output_status_error_produces_silence_and_failure(self):
        data = bytearray([7]) * DISCORD_FRAME
        self.output.callback(data, 960, None, True)
        self.assertEqual(data, bytes(DISCORD_FRAME))
        self.assertTrue(self.output.failed.is_set())
        self.buffer.frames.append(bytes([1]) * DISCORD_FRAME)
        self.assertEqual(self.emit(960), bytes(DISCORD_FRAME))

    def test_oversized_output_request_aborts_without_reading_audio(self):
        with self.assertRaises(self.backend.CallbackAbort):
            self.output.callback(bytearray(), MAX_CALLBACK_SAMPLES + 1, None, False)
        self.assertTrue(self.output.failed.is_set())

    def test_passive_inventory_does_not_open_additional_streams(self):
        inventory = devices(self.backend)
        self.assertEqual((inventory["default_input"], inventory["default_output"]), (0, 1))
        self.assertEqual(len(self.backend.inputs), 0)
        self.assertEqual(len(self.backend.outputs), 1)

    def test_unsupported_input_rate_channels_and_device_fail_before_open(self):
        for device, channels, fail_rate in [(0, 3, False), (-1, 1, False), (1, 1, False), (0, 1, True)]:
            with self.subTest(device=device, channels=channels, fail_rate=fail_rate):
                self.backend.fail_settings = "input" if fail_rate else None
                mic = Microphone(InputBuffer(), backend=self.backend, device=device, channels=channels)
                with self.assertRaises(SafeError):
                    mic.start()
                self.assertIsNone(mic.stream)
        self.assertEqual(len(self.backend.inputs), 0)


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = FakeBackend()
        self.sockets = []
        def factory(config, budget, **kwargs):
            socket = FakeSocket()
            close = socket.close
            async def close_socket():
                self.backend.order.append("live.close")
                await close()
            socket.close = close_socket
            self.sockets.append(socket)
            async def connect(_key):
                return socket
            async def backend(*_args):
                raise AssertionError("No backend request expected")
            return LiveSession(config, budget, connect=connect, backend=backend, **kwargs)
        self.controller = LocalController(CONFIG, fake_budget(), backend=self.backend, session_factory=factory)
        self.addAsyncCleanup(self.controller.close)

    async def test_local_listening_opens_only_microphone(self):
        await self.controller.start_listening()
        self.assertEqual(self.controller.state, LOCAL_LISTENING)
        self.assertTrue(self.controller.microphone.active)
        self.assertEqual(self.sockets, [])
        self.assertEqual(self.backend.outputs, [])
        self.assertFalse(self.controller.input.enabled)

    async def test_start_and_local_mute_retain_session_and_output(self):
        await self.controller.start_call()
        session, output = self.controller.session, self.controller.output
        await self.controller.mute()
        self.assertEqual(self.controller.state, ACTIVE)
        self.assertIs(self.controller.session, session)
        self.assertTrue(output.active)
        self.assertFalse(session.output.blocked)
        self.assertFalse(self.controller.input.enabled)
        self.assertFalse(any(e["type"] == "session.input_audio.mute" for e in self.sockets[0].sent))
        await self.controller.unmute()
        self.assertTrue(self.controller.input.enabled)

    async def test_end_call_keeps_microphone_open(self):
        await self.controller.start_call()
        microphone, output, session = self.controller.microphone, self.controller.output, self.controller.session
        await self.controller.end_call()
        self.assertEqual(self.controller.state, LOCAL_LISTENING)
        self.assertIs(self.controller.microphone, microphone)
        self.assertTrue(microphone.active)
        self.assertIsNone(output.stream)
        self.assertTrue(session.closed)
        self.assertTrue(self.sockets[0].closed)
        self.assertFalse(self.controller.input.enabled)

    async def test_stop_listening_closes_call_before_microphone(self):
        await self.controller.start_call()
        await self.controller.stop_listening()
        self.assertLess(self.backend.order.index("live.close"), self.backend.order.index("input.abort"))
        self.assertEqual(self.controller.state, LISTENING_OFF)
        self.assertIsNone(self.controller.watch)

    async def test_repeated_calls_reuse_input_and_microphone_without_stale_audio(self):
        await self.controller.start_call()
        buffer, mic = self.controller.input, self.controller.microphone
        mic.callback(bytes(960), 480, None, False)
        await self.controller.end_call()
        self.assertEqual(mic.pending, b"")
        await self.controller.start_call()
        self.assertIs(self.controller.input, buffer)
        self.assertIs(self.controller.session.input, buffer)
        self.assertIs(self.controller.microphone, mic)
        self.assertEqual(len(self.backend.inputs), 1)
        self.assertEqual(len(self.backend.outputs), 2)

    async def test_close_is_idempotent(self):
        await self.controller.start_call()
        await self.controller.close()
        await self.controller.close()
        self.assertEqual(self.backend.order.count("input.close"), 1)
        self.assertEqual(self.backend.order.count("output.close"), 1)
        self.assertEqual(self.controller.state, LISTENING_OFF)
        with self.assertRaises(SafeError):
            await self.controller.start_call()

    async def test_duplicate_start_rejected_without_second_live_connection(self):
        await self.controller.start_call()
        with self.assertRaises(SafeError):
            await self.controller.start_call()
        self.assertEqual(len(self.sockets), 1)

    async def test_unsupported_output_fails_before_live_start(self):
        self.backend.fail_settings = "output"
        with self.assertRaises(SafeError):
            await self.controller.start_call()
        self.assertEqual(self.sockets, [])
        self.assertEqual(self.controller.state, LOCAL_LISTENING)
        self.assertTrue(self.controller.microphone.active)

    async def test_output_start_failure_closes_live_but_keeps_microphone(self):
        self.backend.fail_start = "output"
        with self.assertRaises(SafeError):
            await self.controller.start_call()
        self.assertTrue(self.sockets[0].closed)
        self.assertTrue(self.backend.outputs[0].closed)
        self.assertEqual(self.controller.state, LOCAL_LISTENING)

    async def test_provider_failure_returns_to_local_listening(self):
        await self.controller.start_call()
        await self.sockets[0].events.put({"type": "error"})
        async def settled():
            while self.controller.state != LOCAL_LISTENING:
                await asyncio.sleep(.005)
        await asyncio.wait_for(settled(), 1)
        self.assertTrue(self.controller.microphone.active)
        self.assertIsNone(self.controller.output)

    async def test_microphone_failure_stops_all_resources(self):
        await self.controller.start_call()
        self.controller.microphone.callback(b"bad", 960, None, False)
        async def settled():
            while self.controller.state != LISTENING_OFF:
                await asyncio.sleep(.005)
        await asyncio.wait_for(settled(), 1)
        self.assertTrue(self.sockets[0].closed)
        self.assertTrue(self.backend.inputs[0].closed)

    async def test_cleanup_failure_retains_ownership_and_blocks_retry(self):
        await self.controller.start_call()
        self.backend.fail_close = "output"
        with self.assertRaises(SafeError):
            await self.controller.close()
        self.assertTrue(self.controller.cleanup_failed)
        self.assertIsNotNone(self.controller.output.stream)
        self.assertTrue(self.backend.inputs[0].closed)
        with self.assertRaises(SafeError):
            await self.controller.start_call()
        self.assertEqual(self.backend.order.count("output.close"), 1)

    async def test_cancelled_microphone_start_reconciles_native_work_before_cleanup(self):
        began, release = threading.Event(), threading.Event()
        def start_hook(kind):
            if kind == "input":
                began.set()
                release.wait(2)
        self.backend.start_hook = start_hook
        start = asyncio.create_task(self.controller.start_listening())
        try:
            await asyncio.wait_for(asyncio.to_thread(began.wait), 1)
            start.cancel()
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await start
        self.assertEqual(self.controller.state, LISTENING_OFF)
        self.assertTrue(self.backend.inputs[0].closed)

    async def test_repeated_cancellation_during_close_still_closes_all_resources(self):
        await self.controller.start_call()
        began, release = threading.Event(), threading.Event()
        def close_hook(kind):
            if kind == "output":
                began.set()
                release.wait(2)
        self.backend.close_hook = close_hook
        close = asyncio.create_task(self.controller.close())
        try:
            await asyncio.wait_for(asyncio.to_thread(began.wait), 1)
            close.cancel()
            await asyncio.sleep(0)
            close.cancel()
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await close
        self.assertTrue(self.sockets[0].closed)
        self.assertTrue(self.backend.inputs[0].closed)
        self.assertTrue(self.backend.outputs[0].closed)
        self.assertEqual(self.controller.state, LISTENING_OFF)

    async def test_stream_health_inspection_failure_closes_all_resources(self):
        await self.controller.start_call()
        def broken_health(_adapter):
            raise RuntimeError("synthetic native stream property failure")
        with patch.object(Microphone, "active", property(broken_health)):
            async def settled():
                while self.controller.state != LISTENING_OFF:
                    await asyncio.sleep(.005)
            await asyncio.wait_for(settled(), 1)
        self.assertTrue(self.sockets[0].closed)
        self.assertTrue(self.backend.inputs[0].closed)

    async def test_cli_manual_commands_use_controller_and_close_on_quit(self):
        from panam_companion.local_cli import run
        commands = iter(["listen", "start", "mute", "unmute", "end", "stop-listening", "status", "quit"])
        async def reader():
            return next(commands)
        with contextlib.redirect_stdout(io.StringIO()):
            await run(self.controller, reader)
        self.assertTrue(self.controller.closed)
        self.assertEqual(self.controller.state, LISTENING_OFF)


class CLIGateTests(unittest.TestCase):
    def test_passive_devices_and_unapproved_cli_do_not_load_credentials_or_budget(self):
        from panam_companion import local_cli
        with patch.object(local_cli, "load_config", Mock()) as config, \
                patch.object(local_cli, "Budget", Mock()) as budget, \
                patch.object(local_cli, "devices", return_value=devices(FakeBackend())), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with patch("sys.argv", ["local_cli", "--devices"]):
                self.assertEqual(local_cli.main(), 0)
            with patch("sys.argv", ["local_cli"]):
                self.assertEqual(local_cli.main(), 2)
            config.assert_not_called()
            budget.assert_not_called()


if __name__ == "__main__":
    unittest.main()
