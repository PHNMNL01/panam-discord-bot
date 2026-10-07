"""In-memory extraction checks; no devices, credentials, network or runtime files.

Run with: python -B -m unittest panam_companion.tests.test_audio_boundary -v
Discord/DAVE are fake modules here, not installed-library integration evidence.
"""
import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from panam_companion.audio import Converter, DISCORD_FRAME, InputBuffer
from panam_companion.config import Config
from panam_companion.live import LiveSession


CONFIG = Config("synthetic_key", "synthetic_token", 100, 200, 300, 400, 500)


def fake_budget():
    return NS(reserve_session=Mock(return_value=300), finish_session=Mock(),
              data={"uncertain": False, "seconds": 0, "backend_calls": 0},
              reserved_dollars=0)


class FakeSocket:
    def __init__(self, order=None):
        self.events = asyncio.Queue()
        self.sent = []
        self.audio_sent = asyncio.Event()
        self.closed = False
        self.order = order

    async def send(self, raw):
        event = json.loads(raw)
        self.sent.append(event)
        kind = event["type"]
        if kind == "session.start":
            if self.order is not None:
                self.order.append("live")
            await self.events.put({"type": "session.started", "session": event["session"]})
        elif kind == "session.close":
            await self.events.put({"type": "session.closed", "usage": {"seconds": .1}})
        elif kind in {"session.input_audio.mute", "session.input_audio.unmute"}:
            await self.events.put({"type": kind + "d"})
        elif kind == "session.input_audio.append":
            self.audio_sent.set()

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


def load_fake_discord_adapter():
    discord = ModuleType("discord")
    discord.AudioSource = object
    discord.VoiceChannel = type("FakeChannel", (), {})
    discord.opus = NS(Decoder=Mock())
    discord.app_commands = ModuleType("discord.app_commands")
    extension = ModuleType("discord.ext")
    extension.voice_recv = NS(AudioSink=object, VoiceRecvClient=object)
    davey = ModuleType("davey")
    davey.MediaType = NS(audio="audio")
    path = Path(__file__).resolve().parents[1] / "discord_adapter.py"
    spec = importlib.util.spec_from_file_location("panam_companion._offline_adapter", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"discord": discord, "discord.ext": extension, "davey": davey}):
        spec.loader.exec_module(module)
    return module


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def session(self, buffer, socket=None):
        socket = socket or FakeSocket()

        async def connect(_key):
            return socket

        async def backend(*_args):
            raise AssertionError("No backend request expected")

        session = LiveSession(CONFIG, fake_budget(), input_buffer=buffer,
                              connect=connect, backend=backend)
        self.addAsyncCleanup(session.close)
        await session.start()
        return session, socket

    async def test_input_survives_close_and_can_be_borrowed_by_next_session(self):
        buffer = InputBuffer()
        first, _ = await self.session(buffer)
        buffer.put(bytes([1]) * DISCORD_FRAME, buffer.epoch)
        old_epoch = buffer.epoch
        await first.close()
        self.assertIs(first.input, buffer)
        self.assertFalse(buffer.enabled)
        self.assertEqual(len(buffer.frames), 0)
        self.assertEqual(first.output.read(), b"")
        self.assertTrue(all(task.done() for task in first.tasks))
        second, _ = await self.session(buffer)
        self.assertIs(second.input, buffer)
        self.assertIsNot(first.output, second.output)
        buffer.put(bytes([2]) * DISCORD_FRAME, old_epoch)
        self.assertEqual(buffer.take(), bytes(DISCORD_FRAME))
        fresh = bytes([3]) * DISCORD_FRAME
        buffer.put(fresh, buffer.epoch)
        self.assertEqual(buffer.take(), fresh)

    async def test_live_input_output_and_mute_with_synthetic_audio(self):
        buffer = InputBuffer()
        session, socket = await self.session(buffer)
        pcm = struct.pack("<hh", 100, 100) * 960
        buffer.put(pcm, buffer.epoch)
        await asyncio.wait_for(socket.audio_sent.wait(), 1)
        audio = next(event for event in socket.sent if event["type"] == "session.input_audio.append")
        self.assertEqual(base64.b64decode(audio["audio"]), Converter().to_live(pcm))
        await socket.events.put({"type": "session.output_audio.delta",
                                 "delta": base64.b64encode(struct.pack("<h", 1000) * 960).decode()})
        async def wait_for_output():
            while not session.output.frames:
                await asyncio.sleep(.001)
        await asyncio.wait_for(wait_for_output(), 1)
        self.assertEqual(session.output.read(), struct.pack("<hh", 1000, 1000) * 960)
        await session.mute(True)
        count = len(socket.sent)
        await asyncio.sleep(.045)
        self.assertEqual(len(socket.sent), count)
        self.assertFalse(buffer.enabled)
        self.assertEqual(session.output.read(), bytes(DISCORD_FRAME))
        await session.mute(False)
        self.assertTrue(buffer.enabled)
        await session.close()
        self.assertTrue(session.finalized.is_set())
        self.assertTrue(socket.closed)

    async def test_discord_start_stop_keep_order_and_controller_ownership(self):
        adapter = load_fake_discord_adapter()
        order = []
        channel = adapter.discord.VoiceChannel()
        channel.id, channel.guild = 300, NS(id=200)
        channel.members = [NS(id=500)]
        client = NS(user=NS(id=100), get_channel=lambda _id: channel)
        voice = NS(channel=channel, _connection=NS(can_encrypt=True),
                   listen=Mock(side_effect=lambda *_a, **_k: order.append("receive")),
                   play=Mock(side_effect=lambda *_a, **_k: order.append("play")),
                   stop=Mock(), disconnect=AsyncMock(), is_connected=lambda: True)
        async def connect(*, cls, **_kwargs):
            order.append("discord")
            cls(client, channel)
            channel.members.append(NS(id=100))
            return voice
        channel.connect = connect
        socket = FakeSocket(order)
        async def provider(_key):
            return socket
        def live(config, budget, **kwargs):
            return LiveSession(config, budget, connect=provider, **kwargs)
        controller = adapter.Controller(client, CONFIG, fake_budget())
        self.addAsyncCleanup(controller.stop)
        with patch.object(adapter, "TestVoiceClient", return_value=voice), \
                patch.object(adapter, "LiveSession", side_effect=live), patch("builtins.print"):
            await controller.start()
            self.assertEqual(order, ["discord", "live", "receive", "play"])
            buffer, session = controller.input, controller.session
            self.assertIs(session.input, buffer)
            self.assertIs(controller.sink.buffer, buffer)
            await controller.stop()
        self.assertFalse(buffer.enabled)
        self.assertIsNone(controller.input)
        self.assertIsNone(controller.session)
        self.assertIsNone(controller.voice)
        self.assertTrue(session.closed)
        voice.disconnect.assert_awaited_once_with(force=True)


class PCMTrustTests(unittest.TestCase):
    def test_stateful_conversion_matches_continuous_in_both_directions(self):
        for method, pcm, size in [
            ("to_live", b"".join(struct.pack("<hh", i, i // 2) for i in range(9600)), 3840),
            ("to_discord", b"".join(struct.pack("<h", i) for i in range(4800)), 960),
        ]:
            converter = Converter()
            chunks = b"".join(getattr(converter, method)(pcm[i:i + size]) for i in range(0, len(pcm), size))
            self.assertEqual(chunks, getattr(Converter(), method)(pcm))

    def test_discord_trust_checks_precede_shared_pcm_admission(self):
        adapter = load_fake_discord_adapter()
        order = []
        pcm = bytes([1]) * DISCORD_FRAME
        decoder = NS(decode=Mock(side_effect=lambda *_a, **_k: order.append("decode") or pcm))
        adapter.discord.opus.Decoder.return_value = decoder
        dave = NS(ready=True, can_passthrough=lambda _id: False,
                  decrypt=Mock(side_effect=lambda *_a: order.append("dave") or b"opus"))
        channel = NS(id=300, guild=NS(id=200), members=[NS(id=100), NS(id=500)])
        voice = NS(channel=channel, _connection=NS(can_encrypt=True, dave_session=dave))
        buffer = InputBuffer()
        buffer.enable(True)
        sink = adapter.ReceiveSink(CONFIG, voice, buffer)
        packet = NS(opus=b"encrypted", packet=True)
        for user in [None, NS(id=501, bot=False), NS(id=500, bot=True)]:
            sink.write(user, packet)
        self.assertEqual(order, [])
        dave.ready = False
        sink.write(NS(id=500, bot=False), packet)
        self.assertEqual(order, [])
        dave.ready = True
        sink.write(NS(id=500, bot=False), packet)
        self.assertEqual(order, ["dave", "decode"])
        self.assertEqual(buffer.take(), pcm)
        buffer.enable(False)
        sink.write(NS(id=500, bot=False), packet)
        self.assertEqual(order, ["dave", "decode"])


if __name__ == "__main__":
    unittest.main()
