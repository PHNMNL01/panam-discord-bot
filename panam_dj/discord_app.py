"""discord.py adapter. Importing or constructing the client does not log in."""

import asyncio
from io import BytesIO
import logging
import os
import shutil
import threading

import discord
from discord import app_commands

from .catalog import DJError, LocalCatalog, Track
from .config import Settings
from .playback import OwnedSource, PlaybackManager, QueueView, SourceConstructionError, finish_cleanup
from .sources import DJSourceProvider


logger = logging.getLogger("panam_dj")


class DiscordOwnedSource(OwnedSource, discord.AudioSource):
    """Supported AudioSource wrapper with the core's cleanup completion signal."""


class ManagedFFmpegOpusAudio(discord.FFmpegOpusAudio):
    """Keep the process and pipes until termination and explicit closure finish.

    discord.py 2.7.1 can discard an exited process without closing stdout. Its
    cleanup also races its stdin writer by clearing shared attributes. This
    narrowly scoped adapter uses the inspected FFmpegAudio process/thread fields;
    dependency upgrades must rerun the real-process ownership regressions.
    """

    def __init__(self, source: BytesIO, **kwargs):
        self._dj_input = source
        self._dj_cleanup_lock = threading.Lock()
        self._dj_cleanup_done = False
        try:
            super().__init__(source, **kwargs)
        except BaseException as error:
            # Never clean an unregistered partial resource synchronously. Carry
            # it across the factory boundary so the manager owns the first
            # off-loop cleanup attempt, including any failure or timeout.
            raise SourceConstructionError(self) from error

    def cleanup(self):
        with self._dj_cleanup_lock:
            if self._dj_cleanup_done:
                return
            self._stopped = True
            process = getattr(self, "_process", None)
            streams = [self._dj_input]
            if process:
                # Do not use communicate(): discord.py's writer owns stdin.
                # Keep all inherited references valid until that writer exits.
                if process.poll() is None:
                    process.kill()
                process.wait()
                for name in ("_pipe_writer_thread", "_pipe_reader_thread"):
                    thread = getattr(self, name, None)
                    if thread is not None and thread.ident is not None:
                        thread.join()
                streams.extend((process.stdin, process.stdout, process.stderr))
            errors = []
            for stream in streams:
                if stream is not None:
                    try:
                        stream.close()
                        if not stream.closed:
                            raise RuntimeError("FFmpeg stream closure is unresolved")
                    except Exception as error:
                        errors.append(error)
            if errors:
                # OwnedSource records the failure and the manager retains us.
                raise ExceptionGroup("FFmpeg stream cleanup failed", errors)
            self._dj_cleanup_done = True


class FFmpegSourceFactory:
    def __init__(self, catalog: LocalCatalog, executable: str):
        self.catalog = catalog
        self.executable = executable

    def __call__(self, track: Track):
        data = self.catalog.audio_bytes(track)
        # Explicit WAV demuxing + pipe-only protocols prevent URL/playlist fetching.
        # FFmpeg emits Opus; discord.py owns transport encryption and DAVE negotiation.
        # discord.py expects a file object for stderr, not subprocess.DEVNULL.
        # Popen duplicates the handle before this context closes it.
        with open(os.devnull, "wb") as errors:
            return ManagedFFmpegOpusAudio(
                # codec describes the INPUT. Leaving it unset transcodes PCM to Opus;
                # passing "libopus" would copy an existing Opus stream.
                BytesIO(data), executable=self.executable, pipe=True,
                # FFmpeg 8 uses the local fd protocol for '-'; older builds use pipe.
                bitrate=128, before_options="-nostdin -protocol_whitelist pipe,fd -f wav",
                options="-vn", stderr=errors,
            )


def queue_message(view: QueueView) -> str:
    def title(track):
        result = discord.utils.escape_markdown(track.title)
        if track.requested_url:
            result += " [approved local audio; YouTube metadata only]"
            result += " (requested: " + discord.utils.escape_markdown(track.requested_title or "video") + ")"
        return result

    lines = [f"Now: {title(view.current)}{' (paused)' if view.paused else ''}"
             if view.current else "Nothing is playing."]
    lines.append(f"Waiting: {len(view.pending)}")
    if view.resolving:
        lines.append(f"Preparing requests: {view.resolving}")
    for index, track in enumerate(view.pending[:10], 1):
        lines.append(f"{index}. {title(track)}")
    if len(view.pending) > 10:
        lines.append(f"… and {len(view.pending) - 10} more.")
    if view.last_error:
        lines.append(view.last_error)
    return "\n".join(lines)[:1900]


class DJClient(discord.Client):
    def __init__(self, settings: Settings, catalog: LocalCatalog, *, source_factory=None):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents, application_id=settings.application_id,
                         allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.catalog = catalog
        self._dj_close_task: asyncio.Task | None = None
        self.tree = app_commands.CommandTree(self)
        factory = source_factory or FFmpegSourceFactory(catalog, shutil.which("ffmpeg") or "ffmpeg")
        provider = DJSourceProvider.load(catalog, settings.youtube_path)
        self.playback = PlaybackManager(provider, factory, self._connect_voice,
                                        guild_ids=settings.guild_ids, queue_limit=settings.queue_limit,
                                        source_wrapper=DiscordOwnedSource)
        self._register_commands()

    async def setup_hook(self):
        # A wrong token must never synchronize commands into another application.
        identity = await self.application_info()
        if identity.id != self.settings.application_id:
            raise DJError("The token does not belong to the configured DJ application.")
        for guild_id in self.settings.guild_ids:
            await self.tree.sync(guild=discord.Object(id=guild_id))

    def _register_commands(self):
        guilds = [discord.Object(id=value) for value in self.settings.guild_ids]

        @self.tree.command(name="play", description="Play approved audio by catalog ID or mapped YouTube URL", guilds=guilds)
        @app_commands.guild_only()
        @app_commands.describe(track="Catalog ID or YouTube video URL mapped to approved local audio")
        async def play(interaction: discord.Interaction, track: str):
            await self._dispatch(interaction, "play", track)

        @play.autocomplete("track")
        async def autocomplete(interaction: discord.Interaction, current: str):
            if interaction.guild_id not in self.settings.guild_ids:
                return []
            return [app_commands.Choice(name=f"{track.key}: {track.title}"[:100], value=track.key)
                    for track in self.catalog.tracks
                    if current.lower() in f"{track.key} {track.title}".lower()][:25]

        descriptions = {
            "skip": "Skip the current track", "pause": "Pause the current track",
            "resume": "Resume playback", "queue": "Show the current and queued tracks",
            "stop": "Stop playback and clear the queue", "leave": "Clear the queue and disconnect",
        }

        def register(name, description):
            async def callback(interaction: discord.Interaction):
                await self._dispatch(interaction, name)

            self.tree.add_command(app_commands.Command(name=name, description=description,
                                  callback=app_commands.guild_only()(callback)), guilds=guilds)

        for name, description in descriptions.items():
            register(name, description)

        @self.tree.error
        async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            # Do not log raw interactions, tokens, paths or arbitrary exception text.
            logger.warning("Slash command rejected (%s)", type(error).__name__)
            await self._reply(interaction, "Command could not be processed. Try again or contact the owner.")

    async def _reply(self, interaction, message):
        kwargs = {"ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
        if interaction.response.is_done():
            await interaction.followup.send(message, **kwargs)
        else:
            await interaction.response.send_message(message, **kwargs)

    async def _dispatch(self, interaction, action, query=None):
        try:
            if interaction.guild is None or interaction.guild_id not in self.settings.guild_ids:
                raise DJError("This command is available only in an enabled guild.")
            state = getattr(interaction.user, "voice", None)
            channel = getattr(state, "channel", None)
            if not isinstance(channel, discord.VoiceChannel) or channel.guild.id != interaction.guild_id:
                raise DJError("Join a regular voice channel in this guild first.")
            guild_id, channel_id = interaction.guild_id, channel.id

            def authorize():
                # Evaluated by the manager under its guild lock, after waits.
                current = getattr(getattr(interaction.user, "voice", None), "channel", None)
                if (
                    interaction.guild is None or interaction.guild_id != guild_id
                    or guild_id not in self.settings.guild_ids
                    or not isinstance(current, discord.VoiceChannel)
                    or current.guild.id != guild_id or current.id != channel_id
                ):
                    raise DJError("Your voice channel changed; submit a new request from the bot's channel.")

            # Acknowledge before voice negotiation or provider work can exceed Discord's deadline.
            await interaction.response.defer(ephemeral=True, thinking=True)
            if action == "play":
                view = await self.playback.play(guild_id, channel_id, query, authorize=authorize)
                message = "Request accepted.\n" + queue_message(view)
            else:
                view = await self.playback.control(guild_id, channel_id, action, authorize=authorize)
                message = {"stop": "Playback stopped; queue cleared.",
                           "leave": "Disconnected; queue cleared."}.get(action, queue_message(view))
            await self._reply(interaction, message)
        except DJError as exc:
            await self._reply(interaction, str(exc))
        except Exception as exc:
            logger.warning("DJ command failed (%s)", type(exc).__name__)
            await self._reply(interaction, "The operation failed. Check /queue or use /leave before retrying.")

    async def _connect_voice(self, guild_id: int, channel_id: int):
        guild = self.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if not isinstance(channel, discord.VoiceChannel) or guild.me is None:
            raise DJError("The requested voice channel is unavailable.")
        permissions = channel.permissions_for(guild.me)
        if not all((permissions.view_channel, permissions.connect, permissions.speak)):
            raise DJError("The bot needs View Channel, Connect and Speak permissions there.")
        existing = guild.voice_client
        if existing is not None:
            if existing.is_connected():
                if existing.channel.id != channel_id:
                    raise DJError("The bot is already connected to another voice channel.")
                return existing
            await existing.disconnect(force=True)
        # Use the maintained VoiceClient, including its DAVE support. No custom protocol.
        try:
            voice = await channel.connect(timeout=20.0, reconnect=True, self_deaf=True)
            if voice.guild.id != guild_id or voice.channel.id != channel_id:
                raise DJError("The voice channel changed during connection; try again.")
            return voice
        except (Exception, asyncio.CancelledError):
            # discord.py registers the client before awaiting its handshake.
            # Cancellation must not leave that half-connected client behind.
            orphan = guild.voice_client
            if orphan is not None and orphan.guild.id == guild_id:
                try:
                    await orphan.disconnect(force=True)
                except Exception as exc:
                    logger.warning("Connection cleanup failed (%s)", type(exc).__name__)
            raise

    async def on_voice_state_update(self, member, before, after):
        if self.user is not None and member.id == self.user.id:
            try:
                await self.playback.reconcile(member.guild.id)
            except Exception as exc:
                logger.warning("Voice reconciliation failed (%s)", type(exc).__name__)

    async def on_guild_remove(self, guild):
        try:
            await self.playback.disconnect_guild(guild.id)
        except Exception as exc:
            logger.warning("Guild removal cleanup failed (%s)", type(exc).__name__)

    async def close(self):
        task = self._dj_close_task
        if task is None or (
            task.done() and (task.cancelled() or task.exception() is not None or not self.playback.closed)
        ):
            task = self._dj_close_task = asyncio.create_task(self._close_owned())
        await finish_cleanup(task)

    async def _close_owned(self):
        try:
            failures = await self.playback.close()
            if failures:
                logger.warning("Playback cleanup unresolved for %d guild(s); ownership retained", len(failures))
        finally:
            # The same retained task owns both layers of teardown. Public close
            # cancellation cannot bypass this, and callers wait for it to finish.
            await super().close()
