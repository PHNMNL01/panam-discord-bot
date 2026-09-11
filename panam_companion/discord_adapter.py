"""Discord-only transport. Never imports any legacy Panam entrypoint."""
import asyncio
import json
import logging
import struct
import threading
import time

import davey
import discord
from discord import app_commands
from discord.ext import voice_recv

from .audio import DISCORD_FRAME
from .config import SafeError
from .live import LiveSession


def silence_library_logs():
    # The receive extension logs transport keys and packet bodies. Suppress the
    # entire process logging sink before login, including future child loggers.
    # This function is called only by the standalone CLI, never on import.
    logging.disable(logging.CRITICAL)


def allowed_control(config, interaction):
    return (interaction.guild_id == config.guild_id and
            interaction.channel_id == config.control_id and
            interaction.user.id == config.user_id and not interaction.user.bot)


def allowed_members(config, channel, bot_present=True):
    if not channel or channel.id != config.voice_id or channel.guild.id != config.guild_id:
        return False
    expected = {config.user_id, config.bot_id} if bot_present else {config.user_id}
    return {member.id for member in channel.members} == expected


class TestVoiceClient(voice_recv.VoiceRecvClient):
    """Enforce outbound membership and DAVE encryption on every audio packet."""
    companion_config = None
    companion_enabled = False

    def _get_voice_packet(self, data):
        config = self.companion_config
        state = self._connection
        session = state.dave_session
        if (not self.companion_enabled or not config or not allowed_members(config, self.channel) or
                not state.can_encrypt or not session or not session.ready):
            raise RuntimeError("Companion outbound gate closed")
        # Do not use VoiceClient's plaintext fallback during DAVE transitions.
        encrypted = session.encrypt_opus(data)
        header = bytearray(12)
        header[0], header[1] = 0x80, 0x78
        struct.pack_into(">H", header, 2, self.sequence)
        struct.pack_into(">I", header, 4, self.timestamp)
        struct.pack_into(">I", header, 8, self.ssrc)
        if self.mode not in {"aead_xchacha20_poly1305_rtpsize", "aead_aes256_gcm_rtpsize"}:
            raise RuntimeError("Unsupported encrypted transport")
        return getattr(self, "_encrypt_" + self.mode)(header, encrypted)


class ReceiveSink(voice_recv.AudioSink):
    def __init__(self, config, voice, buffer):
        super().__init__()
        self.config = config
        self.voice = voice
        self.buffer = buffer
        self.decoder = None
        self.epoch = None
        self.finished = threading.Event()
        self.accepted = 0
        self.rejected = 0
        self.crypto_failures = 0

    def wants_opus(self):
        return True

    def write(self, user, data):
        # Runs in extension router thread; no event-loop callbacks per packet.
        epoch = self.buffer.epoch
        if (not self.buffer.enabled or not user or user.bot or user.id != self.config.user_id or
                user.id == self.config.bot_id or not allowed_members(self.config, self.voice.channel)):
            self.rejected += 1
            return
        try:
            packet = data.opus
            if not data.packet or not packet or packet == b"\xf8\xff\xfe":
                return  # fake packet or Opus comfort silence: sender supplies clocked silence
            state = self.voice._connection
            session = state.dave_session
            if (not state.can_encrypt or not session or not session.ready or
                    session.can_passthrough(user.id)):
                self.rejected += 1
                return
            # The receive extension decrypted only RTP transport, not DAVE.
            decoded_opus = session.decrypt(user.id, davey.MediaType.audio, packet)
            if epoch != self.epoch:
                self.decoder = discord.opus.Decoder()
                self.epoch = epoch
            pcm = self.decoder.decode(decoded_opus, fec=False)
            if not pcm or len(pcm) % DISCORD_FRAME or len(pcm) > 6 * DISCORD_FRAME:
                self.rejected += 1
                return
            for offset in range(0, len(pcm), DISCORD_FRAME):
                self.buffer.put(pcm[offset:offset + DISCORD_FRAME], epoch)
            self.accepted += 1
        except Exception:
            self.crypto_failures += 1
            # Never log packet, exception, user data or connection state.

    def cleanup(self):
        self.buffer.enable(False)
        self.decoder = None
        self.finished.set()


class StreamSource(discord.AudioSource):
    def __init__(self, output):
        self.output = output

    def read(self):
        return self.output.read()

    def is_opus(self):
        return False

    def cleanup(self):
        self.output.close()


class Controller:
    def __init__(self, client, config, budget, captions=False, metadata=False):
        self.client = client
        self.config = config
        self.budget = budget
        self.captions = captions
        self.metadata = metadata
        self.lock = asyncio.Lock()
        self.session = None
        self.voice = None
        self.sink = None
        self.watch = None
        self.start_task = None
        self.stop_requested = False
        self.cleanup_failed = False
        self.state = "odpojeno"
        self.last_summary = None

    def caption(self, role, delta):
        if self.captions:
            # Only printable content, never terminal control sequences.
            text = "".join(c for c in delta if c.isprintable() or c == " ")[:2000]
            print(("Vy: " if role == "user" else "Panam: ") + text, flush=True)

    def status(self):
        elapsed = 0 if not self.session else int(time.monotonic() - self.session.began)
        return (f"Stav: {self.state}. Relace: {elapsed} s. "
                f"Dávka (rezervováno): {self.budget.data['seconds']} / 600 s, "
                f"${self.budget.reserved_dollars:.3f} / $1.00. "
                f"Backend: {self.budget.data['backend_calls']} / 40. "
                f"Uzavření nejisté: {'ano' if self.budget.data['uncertain'] and not self.session else 'ne'}.")

    async def start(self):
        async with self.lock:
            if self.session or self.voice or self.cleanup_failed:
                raise SafeError("Relace už běží nebo čeká na bezpečné dokončení úklidu.")
            if self.budget.data["uncertain"]:
                raise SafeError("Předchozí relace nemá potvrzené ukončení. Nutná ruční kontrola.")
            channel = self.client.get_channel(self.config.voice_id)
            if not isinstance(channel, discord.VoiceChannel) or not allowed_members(self.config, channel, False):
                raise SafeError("V testovacím hlasovém kanálu musí být pouze povolený uživatel.")
            if self.client.user.id != self.config.bot_id:
                raise SafeError("Nesouhlasí identita testovacího bota.")
            self.stop_requested = False
            self.start_task = asyncio.current_task()
            self.state = "připojování"
            try:
                def own_voice(client, target):
                    # Discord registers the instance before awaiting its handshake.
                    # Keep ownership even if Stop cancels that await.
                    self.voice = TestVoiceClient(client, target)
                    self.voice.companion_config = self.config
                    return self.voice

                await channel.connect(cls=own_voice, timeout=10, reconnect=False,
                                      self_deaf=False, self_mute=False)
                # Wait for negotiated DAVE readiness before any paid request.
                for _ in range(50):
                    if self.voice._connection.can_encrypt:
                        break
                    await asyncio.sleep(0.1)
                if not self.voice._connection.can_encrypt or not allowed_members(self.config, channel):
                    raise SafeError("Discord DAVE nebo členství kanálu není připraveno.")
                if self.stop_requested:
                    raise SafeError("Start byl zastaven.")
                self.session = LiveSession(self.config, self.budget, caption=self.caption)
                await self.session.start()
                if self.stop_requested or not allowed_members(self.config, channel):
                    raise SafeError("Start byl zastaven.")
                self.sink = ReceiveSink(self.config, self.voice, self.session.input)
                loop = asyncio.get_running_loop()

                def transport_done(error):
                    if error and self.session and not self.session.closing:
                        loop.call_soon_threadsafe(self.session.fail, "discord_audio_failed")

                self.voice.companion_enabled = True
                self.voice.listen(self.sink, after=transport_done)
                self.voice.play(StreamSource(self.session.output), after=transport_done)
                self.state = "poslouchá"
                self.watch = asyncio.create_task(self.monitor(), name="companion-discord-monitor")
                print(self.status(), flush=True)
            except BaseException:
                await self.teardown()
                raise
            finally:
                self.start_task = None

    async def monitor(self):
        try:
            while self.session and not self.session.closing:
                if (self.session.failed.is_set() or not self.voice.is_connected() or
                        not allowed_members(self.config, self.voice.channel)):
                    await self.stop()
                    return
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass

    async def mute(self, muted):
        async with self.lock:
            if not self.session:
                raise SafeError("Žádná relace neběží.")
            await self.session.mute(muted)
            self.state = "ztlumeno" if muted else "poslouchá"

    async def stop(self):
        self.stop_requested = True
        if self.session:
            self.session.input.enable(False)
            self.session.output.close()
        start = self.start_task
        if start and start is not asyncio.current_task() and not start.done():
            start.cancel()
            await asyncio.gather(start, return_exceptions=True)
        async with self.lock:
            await self.teardown()

    async def teardown(self):
        self.state = "zastavování"
        watch, self.watch = self.watch, None
        if watch and watch is not asyncio.current_task():
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
        voice, session, sink = self.voice, self.session, self.sink
        reader = getattr(voice, "_reader", None) if voice else None
        player = getattr(voice, "_player", None) if voice else None
        threads = [player]
        if reader:
            threads.extend(getattr(reader, attr, None) for attr in
                           ("packet_router", "event_router", "speaking_timer", "keepalive"))
        if session:
            session.input.enable(False)
            session.output.close()
        if voice:
            voice.companion_enabled = False
            voice.stop()
        if session:
            await session.close()
            self.last_summary = {"finalized": session.finalized.is_set(), "fault": session.fault,
                                 "provider_seconds": session.provider_seconds,
                                 "first_provider_audio": session.first_provider_audio,
                                 "first_queued": session.output.first_queued,
                                 "first_source_read": session.output.first_read,
                                 "input_dropped": session.input.dropped,
                                 "receive_accepted": sink.accepted if sink else 0,
                                 "crypto_failures": sink.crypto_failures if sink else 0,
                                 "events": list(session.metrics) + list(session.output.markers)}
            if self.metadata:
                print(json.dumps(self.last_summary, ensure_ascii=True), flush=True)
            print("Relace ukončena; " + (session.fault or "stop") + "; finalizace=" +
                  str(session.finalized.is_set()) + "; audio rámců přijato=" +
                  str(sink.accepted if sink else 0), flush=True)
        if voice:
            try:
                await asyncio.wait_for(voice.disconnect(force=True), 4)
            except Exception:
                self.cleanup_failed = True
        if sink and reader:
            if not await asyncio.to_thread(sink.finished.wait, 2):
                self.cleanup_failed = True
        for thread in threads:
            if isinstance(thread, threading.Thread) and thread.is_alive():
                await asyncio.to_thread(thread.join, 1)
                if thread.is_alive():
                    self.cleanup_failed = True
        self.session, self.voice, self.sink = None, None, None
        self.state = "chyba úklidu" if self.cleanup_failed else "odpojeno"


def make_client(config, budget, captions=False, metadata=False):
    intents = discord.Intents.none()
    intents.guilds = True
    intents.voice_states = True

    class CompanionClient(discord.Client):
        async def setup_hook(self):
            self.tree = app_commands.CommandTree(self)
            self.controller = Controller(self, config, budget, captions, metadata)
            group = app_commands.Group(name="panam", description="Izolovaný hlasový test Panam")

            @group.command(name="ovladani", description="Start, ztlumení, stop, reset nebo stav")
            @app_commands.choices(akce=[app_commands.Choice(name=name, value=value) for name, value in
                                       [("Start", "start"), ("Ztlumit", "mute"), ("Zapnout mikrofon", "unmute"),
                                        ("Stop", "stop"), ("Reset (ukončí kontext)", "reset"),
                                        ("Stav", "status"), ("Vypnout bota", "quit")]])
            async def control(interaction: discord.Interaction, akce: app_commands.Choice[str]):
                if not allowed_control(config, interaction):
                    await interaction.response.send_message("Tento test zde není povolen.", ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    action = akce.value
                    if action == "start":
                        await self.controller.start()
                    elif action in {"stop", "reset", "quit"}:
                        await self.controller.stop()
                    elif action in {"mute", "unmute"}:
                        await self.controller.mute(action == "mute")
                    await interaction.followup.send(self.controller.status(), ephemeral=True)
                    if action == "quit":
                        await self.close()
                except SafeError as error:
                    await interaction.followup.send(str(error), ephemeral=True)
                except Exception:
                    await self.controller.stop()
                    await interaction.followup.send("Test selhal; bezpečně zastaven. Podrobnosti nebyly zveřejněny.",
                                                    ephemeral=True)

            self.tree.add_command(group, guild=discord.Object(id=config.guild_id))
            self.synced = False

        async def on_ready(self):
            if self.user.id != config.bot_id or {g.id for g in self.guilds} != {config.guild_id}:
                print("Identita nebo seznam serverů neodpovídá samostatnému testovacímu botovi.", flush=True)
                await self.close()
                return
            if not self.synced:
                await self.tree.sync(guild=discord.Object(id=config.guild_id))
                self.synced = True
                print("Bot připraven. V povoleném kanálu použijte /panam ovladani → Start.", flush=True)

        async def on_voice_state_update(self, member, before, after):
            c = self.controller
            if c.voice and c.session and not allowed_members(config, c.voice.channel):
                await c.stop()

        async def on_disconnect(self):
            if hasattr(self, "controller"):
                await self.controller.stop()

        async def on_error(self, event_method, *args, **kwargs):
            print("Discord událost selhala; ukončuji test.", flush=True)
            await self.controller.stop()
            await self.close()

        async def close(self):
            if hasattr(self, "controller"):
                await self.controller.stop()
            await super().close()

    return CompanionClient(intents=intents, max_messages=None, allowed_mentions=discord.AllowedMentions.none())
