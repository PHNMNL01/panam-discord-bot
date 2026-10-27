"""Transport-independent playback, serialized per guild on one asyncio loop."""

import asyncio
from collections import deque
from dataclasses import dataclass, field
import math
import threading
from typing import Any, Awaitable, Callable, Protocol

from .catalog import DJError, SourceProvider, Track


async def finish_cleanup(task: asyncio.Task):
    """Observe retained cleanup to completion, then propagate caller cancellation.

    Shielding alone would detach cleanup when its caller is cancelled. Every
    caller instead keeps waiting, including through repeated cancellation.
    """
    cancelled = False
    while True:
        try:
            # wait() does not cancel its input. Unlike shield(), it also does
            # not emit an unhandled-error diagnostic for an already-observed
            # task that fails after caller cancellation (Python 3.14).
            await asyncio.wait((task,))
            result = task.result()
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception as error:
            if cancelled:
                raise asyncio.CancelledError() from error
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result


class SourceConstructionError(Exception):
    """Transfer one partial resource from its constructor to cleanup ownership.

    The resource only needs cleanup(); read/is_opus need not exist yet. Holding
    it here prevents finalization between failed construction and registration.
    """

    def __init__(self, resource: Any):
        super().__init__("Audio source construction failed; cleanup is required.")
        self._resource = resource

    def take_resource(self):
        if self._resource is None:
            raise RuntimeError("Construction resource ownership was already transferred")
        resource, self._resource = self._resource, None
        return resource


class OwnedSource:
    """Delegate audio operations and observe one cleanup attempt across threads.

    The adapter subclasses this with discord.AudioSource. Completion contains
    the original cleanup exception (or None), never an unobserved Future error.
    """

    def __init__(self, source: Any, track: Track):
        self.source = source
        self.track = track
        self._loop = asyncio.get_running_loop()
        self.completion = self._loop.create_future()
        self._cleanup_lock = threading.Lock()
        self._cleanup_started = False

    def __getattr__(self, name):
        return getattr(self.source, name)

    def read(self):
        return self.source.read()

    def is_opus(self):
        return self.source.is_opus()

    def cleanup(self):
        with self._cleanup_lock:
            if self._cleanup_started:
                return
            self._cleanup_started = True
        error = None
        try:
            self.source.cleanup()
        except BaseException as exc:
            error = exc
            raise
        finally:
            # Publish only after the delegated attempt has returned or raised.
            # A late callback during forced loop destruction cannot claim success.
            try:
                self._loop.call_soon_threadsafe(self.completion.set_result, error)
            except RuntimeError:
                pass


class VoiceClient(Protocol):
    guild: Any
    channel: Any

    def is_connected(self) -> bool: ...
    def play(self, source: Any, *, after: Callable) -> None: ...
    def stop(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    async def disconnect(self, *, force: bool = False) -> None: ...


@dataclass(frozen=True)
class QueueView:
    current: Track | None
    pending: tuple[Track, ...]
    paused: bool
    channel_id: int | None
    last_error: str | None
    resolving: int = 0


@dataclass(eq=False)
class _Request:
    channel_id: int
    task: asyncio.Task | None = None
    valid: bool = True

    def cancel(self):
        if self.valid:
            self.valid = False
            if self.task is not None:
                self.task.cancel()


@dataclass
class _Session:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    resolve_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    requests: list[_Request] = field(default_factory=list)
    voice: VoiceClient | None = None
    channel_id: int | None = None
    current: Track | None = None
    pending: deque[Track] = field(default_factory=deque)
    paused: bool = False
    generation: int = 0
    last_error: str | None = None


class PlaybackManager:
    def __init__(
        self, provider: SourceProvider, source_factory: Callable[[Track], Any],
        connector: Callable[[int, int], Awaitable[VoiceClient]],
        *, guild_ids: tuple[int, ...], queue_limit: int = 20,
        source_wrapper: Callable[[Any, Track], OwnedSource] = OwnedSource,
        cleanup_timeout: float = 5.0,
        resolution_timeout: float = 10.0,
    ):
        if type(queue_limit) is not int or not 1 <= queue_limit <= 100:
            raise ValueError("queue_limit must be from 1 to 100")
        if type(cleanup_timeout) not in (int, float) or not math.isfinite(cleanup_timeout) or cleanup_timeout <= 0:
            raise ValueError("cleanup_timeout must be a positive finite number")
        if type(resolution_timeout) not in (int, float) or not math.isfinite(resolution_timeout) or resolution_timeout <= 0:
            raise ValueError("resolution_timeout must be a positive finite number")
        self.provider = provider
        self.source_factory = source_factory
        self.connector = connector
        self.queue_limit = queue_limit  # Waiting tracks; the current track is separate.
        self.source_wrapper = source_wrapper
        self.cleanup_timeout = cleanup_timeout
        self.resolution_timeout = resolution_timeout
        self._preparations: dict[asyncio.Task, int] = {}
        self._sources: dict[OwnedSource, int] = {}
        self._cleanup_tasks: dict[asyncio.Task, int] = {}
        self._sessions = {guild: _Session() for guild in guild_ids}
        self._callbacks: set[asyncio.Task] = set()
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task | None = None

    def _session(self, guild_id: int) -> _Session:
        if self._closing or self._closed:
            raise DJError("DJ Panam is shutting down.")
        if guild_id not in self._sessions:
            raise DJError("This guild is not enabled for DJ Panam.")
        return self._sessions[guild_id]

    @staticmethod
    def _reset(session: _Session) -> None:
        # Invalidate callbacks before stop(), which may itself invoke after().
        session.generation += 1
        session.current = None
        session.paused = False
        session.pending.clear()
        for request in session.requests:
            request.cancel()
        if session.voice is not None:
            session.voice.stop()

    @staticmethod
    def _channel_check(session: _Session, channel_id: int) -> None:
        if session.voice is not None and session.channel_id != channel_id:
            raise DJError("Join the bot's voice channel before controlling its queue.")
        if any(request.valid and request.channel_id != channel_id for request in session.requests):
            raise DJError("Join the channel preparing this queue before controlling it.")

    @staticmethod
    def _connection_check(voice: VoiceClient, guild_id: int, channel_id: int) -> None:
        if not voice.is_connected() or voice.guild.id != guild_id or voice.channel.id != channel_id:
            raise DJError("Voice connection changed; submit a new request after cleanup.")

    @staticmethod
    def _authorize(authorize: Callable[[], None] | None) -> None:
        # Discord supplies a synchronous live-membership check. Trusted internal
        # callers/tests may omit it; the core does not depend on Discord members.
        if authorize is not None:
            authorize()

    async def _reconcile(self, session: _Session) -> None:
        voice = session.voice
        if voice is not None and (
            not voice.is_connected() or voice.channel.id != session.channel_id
        ):
            self._reset(session)
            session.last_error = "Voice connection changed; the queue was cleared."
            await voice.disconnect(force=True)
            session.voice = None
            session.channel_id = None

    def _schedule_completion(self, guild_id: int, generation: int, error: Exception | None):
        if self._closing or self._closed:
            return
        task = asyncio.create_task(self._completed(guild_id, generation, error))
        self._callbacks.add(task)
        task.add_done_callback(self._callback_done)

    def _callback_done(self, task: asyncio.Task):
        self._callbacks.discard(task)
        if not task.cancelled():
            task.exception()  # _completed records failures; always retrieve the result.

    def _source_finished(self, source: OwnedSource):
        if source not in self._sources or not source.completion.done():
            return
        error = source.completion.result()
        if error is None:
            del self._sources[source]
        else:
            session = self._sessions[self._sources[source]]
            session.last_error = f"Audio cleanup failed ({type(error).__name__}); ownership retained."

    def _cleanup_done(self, task: asyncio.Task):
        if task.done():
            if not task.cancelled():
                task.exception()  # The source completion also preserves the error.
            self._cleanup_tasks.pop(task, None)

    def _own_source(self, resource: Any, track: Track, guild_id: int) -> OwnedSource:
        source = self.source_wrapper(resource, track)
        self._sources[source] = guild_id
        source.completion.add_done_callback(lambda _: self._source_finished(source))
        return source

    def _cleanup_unplayed(self, source: OwnedSource, guild_id: int):
        # Construction or play() failed, so no audio thread owns cleanup. Retain and
        # observe this one off-loop attempt; close includes it in its deadline.
        task = asyncio.create_task(asyncio.to_thread(source.cleanup))
        self._cleanup_tasks[task] = guild_id
        task.add_done_callback(self._cleanup_done)

    def _start_next(self, guild_id: int, session: _Session) -> None:
        loop = asyncio.get_running_loop()
        while session.pending and session.voice is not None:
            track = session.pending.popleft()
            session.generation += 1
            generation = session.generation
            source = None
            try:
                source = self._own_source(self.source_factory(track), track, guild_id)
                session.current = track
                session.paused = False

                def after(error, generation=generation):
                    try:
                        loop.call_soon_threadsafe(self._schedule_completion, guild_id, generation, error)
                    except RuntimeError:
                        pass  # The owning loop has already closed; Discord cleans its source.

                session.voice.play(source, after=after)
                return
            except Exception as error:
                session.generation += 1
                session.current = None
                session.last_error = "Audio could not start; the failed track was discarded."
                if isinstance(error, SourceConstructionError):
                    # Register the incomplete resource before scheduling any
                    # cleanup. A failed attempt remains in the same source
                    # registry as a failed cleanup after successful playback.
                    source = self._own_source(error.take_resource(), track, guild_id)
                if source is not None:
                    self._cleanup_unplayed(source, guild_id)
                if isinstance(error, SourceConstructionError) and isinstance(error.__cause__, BaseException) \
                        and not isinstance(error.__cause__, Exception):
                    # Keep cancellation/interrupt semantics after securing cleanup.
                    raise error.__cause__
        session.current = None
        session.paused = False

    async def _completed(self, guild_id: int, generation: int, error: Exception | None):
        session = self._sessions[guild_id]
        async with session.lock:
            if self._closing or self._closed or generation != session.generation or session.current is None:
                return
            session.current = None
            session.paused = False
            if error is not None:
                session.last_error = "Playback failed; the failed track was discarded."
            try:
                await self._reconcile(session)
                self._start_next(guild_id, session)
            except Exception:
                self._reset(session)
                session.last_error = "Voice cleanup failed; use /leave before retrying."

    async def play(
        self, guild_id: int, channel_id: int, query: str,
        *, authorize: Callable[[], None] | None = None,
    ) -> QueueView:
        session = self._session(guild_id)
        async with session.lock:
            if self._closing or self._closed:
                raise DJError("DJ Panam is shutting down.")
            self._authorize(authorize)
            await self._reconcile(session)
            self._authorize(authorize)
            self._channel_check(session, channel_id)
            # Reservations count against capacity before any asynchronous work.
            # Even cancelled but unfinished requests retain a slot until cleanup.
            if len(session.pending) + len(session.requests) + bool(session.current) >= self.queue_limit + 1:
                raise DJError("The music queue is full.")
            request = _Request(channel_id)
            session.requests.append(request)
            task = request.task = asyncio.create_task(
                self._resolve_request(guild_id, session, request, query, authorize))
            self._preparations[task] = guild_id
            task.add_done_callback(self._preparation_done)
        try:
            await asyncio.wait((task,))
            return task.result()
        except asyncio.CancelledError:
            # Session controls cancel the owned request, not the command caller.
            if task.cancelled() and not asyncio.current_task().cancelling():
                raise DJError("Source request cancelled; nothing was added.") from None
            request.cancel()
            try:
                await finish_cleanup(task)
            except (Exception, asyncio.CancelledError):
                pass
            raise

    def _preparation_done(self, task: asyncio.Task):
        if task.done():
            if not task.cancelled():
                task.exception()
            self._preparations.pop(task, None)
            # A task cancelled before its first instruction never enters finally.
            for session in self._sessions.values():
                session.requests[:] = [r for r in session.requests if r.task is not task]

    async def _resolve_request(self, guild_id, session, request, query, authorize):
        try:
            # Fair lock preserves request order while the separate guild lock
            # remains available to queue/stop/skip/leave and audio callbacks.
            async with session.resolve_lock:
                async with session.lock:
                    if not request.valid or self._closing:
                        raise DJError("Source request is no longer active.")
                    self._authorize(authorize)
                    self._channel_check(session, request.channel_id)
                    if session.voice is not None:
                        self._connection_check(session.voice, guild_id, request.channel_id)
                try:
                    deadline = asyncio.get_running_loop().time() + self.resolution_timeout
                    track = await asyncio.wait_for(self.provider.resolve(query), self.resolution_timeout)
                    if asyncio.get_running_loop().time() > deadline:
                        raise TimeoutError
                except TimeoutError:
                    raise DJError("Source preparation timed out; nothing was added.") from None
                async with session.lock:
                    return await self._accept_track(guild_id, session, request, track, authorize)
        finally:
            # Registry ownership is released only by the task's done callback.
            if request in session.requests:
                session.requests.remove(request)

    async def _accept_track(self, guild_id, session, request, track, authorize):
        channel_id = request.channel_id
        if not request.valid:
            raise DJError("Source request is no longer active.")
        if self._closing or self._closed:
            raise DJError("DJ Panam is shutting down.")
        self._authorize(authorize)
        self._channel_check(session, channel_id)
        # The guild lock does not freeze Discord's external voice state.
        # Reject a result resolved for a stale connection without enqueueing
        # or starting it; lifecycle reconciliation owns connection cleanup.
        if session.voice is not None:
            self._connection_check(session.voice, guild_id, channel_id)
        if session.voice is None:
            voice = await self.connector(guild_id, channel_id)
            if not voice.is_connected() or voice.guild.id != guild_id or voice.channel.id != channel_id:
                # Never adopt or disconnect an unexpected guild's client.
                raise DJError("Voice connection did not match the requested guild and channel.")
            session.voice = voice
            session.channel_id = channel_id
            try:
                if not request.valid:
                    raise DJError("Source request is no longer active.")
                self._authorize(authorize)
            except Exception:
                # Connection creation yielded. Release only the newly owned
                # connection if its caller left; no track has been enqueued.
                await voice.disconnect(force=True)
                session.voice = None
                session.channel_id = None
                raise
        if self._closing or self._closed:
            # close() is waiting on this lock and owns the disconnect.
            raise DJError("DJ Panam is shutting down.")
        session.last_error = None
        session.requests.remove(request)
        session.pending.append(track)
        if session.current is None:
            self._start_next(guild_id, session)
            if session.current is None:
                raise DJError(session.last_error or "Audio could not start.")
        return self._view(session)

    async def control(
        self, guild_id: int, channel_id: int, action: str,
        *, authorize: Callable[[], None] | None = None,
    ) -> QueueView:
        if action not in {"skip", "pause", "resume", "stop", "leave", "queue"}:
            raise DJError("Unknown playback action.")
        session = self._session(guild_id)
        async with session.lock:
            if self._closing or self._closed:
                raise DJError("DJ Panam is shutting down.")
            self._authorize(authorize)
            await self._reconcile(session)
            self._authorize(authorize)
            self._channel_check(session, channel_id)
            voice = session.voice
            if action == "queue":
                return self._view(session)
            if action in {"stop", "leave"}:
                self._reset(session)
                if action == "leave" and voice is not None:
                    await voice.disconnect(force=True)
                    session.voice = None
                    session.channel_id = None
            elif action == "skip" and session.current is None and any(r.valid for r in session.requests):
                next(r for r in session.requests if r.valid).cancel()
            elif voice is None or session.current is None:
                raise DJError("Nothing is playing.")
            elif action == "skip":
                session.generation += 1
                session.current = None
                session.paused = False
                voice.stop()
                self._start_next(guild_id, session)
            elif action == "pause":
                if session.paused:
                    raise DJError("Playback is already paused.")
                voice.pause()
                session.paused = True
            elif action == "resume":
                if not session.paused:
                    raise DJError("Playback is not paused.")
                voice.resume()
                session.paused = False
            return self._view(session)

    @staticmethod
    def _view(session: _Session) -> QueueView:
        return QueueView(session.current, tuple(session.pending), session.paused,
                         session.channel_id, session.last_error,
                         sum(r.valid for r in session.requests))

    async def reconcile(self, guild_id: int) -> None:
        if self._closing or self._closed or guild_id not in self._sessions:
            return
        session = self._sessions[guild_id]
        async with session.lock:
            await self._reconcile(session)

    async def disconnect_guild(self, guild_id: int) -> None:
        """Release a guild removed from Discord even if voice still reports connected."""
        if guild_id not in self._sessions:
            return
        session = self._sessions[guild_id]
        async with session.lock:
            self._reset(session)
            if session.voice is not None:
                await session.voice.disconnect(force=True)
                session.voice = None
                session.channel_id = None

    async def drain_callbacks(self) -> None:
        """Deterministic barrier for callbacks already submitted from audio threads."""
        barrier = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(barrier.set_result, None)
        await barrier
        while self._callbacks:
            pending = tuple(self._callbacks)
            await asyncio.gather(*pending)
            # Python 3.14 may complete gather synchronously for finished tasks,
            # before their scheduled done callbacks have removed them.
            self._callbacks.difference_update(pending)

    async def close(self) -> tuple[int, ...]:
        if self._close_task is None or (self._close_task.done() and not self._closed):
            self._closing = True
            self._close_task = asyncio.create_task(self._close_sessions())
        return await finish_cleanup(self._close_task)

    @property
    def closed(self) -> bool:
        """True only after voice release and successful owned source cleanup."""
        return self._closed

    async def _close_sessions(self) -> tuple[int, ...]:
        failed = set()
        for guild_id, session in self._sessions.items():
            async with session.lock:
                try:
                    self._reset(session)
                except Exception:
                    failed.add(guild_id)
                try:
                    if session.voice is not None:
                        await session.voice.disconnect(force=True)
                        session.voice = None
                        session.channel_id = None
                except Exception:
                    failed.add(guild_id)
        await self.drain_callbacks()
        # after() precedes AudioSource.cleanup() in discord.py. Neither callback
        # draining nor disconnect proves source release. asyncio.wait leaves
        # pending futures/tasks intact on timeout; ownership remains explicit.
        pending = {source.completion for source in self._sources}
        pending.update(self._cleanup_tasks)
        pending.update(self._preparations)
        if pending:
            await asyncio.wait(pending, timeout=self.cleanup_timeout)
        for source in tuple(self._sources):
            self._source_finished(source)
        for task in tuple(self._cleanup_tasks):
            self._cleanup_done(task)
        for task in tuple(self._preparations):
            self._preparation_done(task)
        unresolved = (set(self._sources.values()) | set(self._cleanup_tasks.values())
                      | set(self._preparations.values()))
        for guild_id in unresolved:
            if not any(source.completion.done() and source.completion.result() is not None
                       for source, owner in self._sources.items() if owner == guild_id):
                self._sessions[guild_id].last_error = "Audio cleanup timed out; ownership retained."
        failed.update(unresolved)
        self._closed = (all(session.voice is None for session in self._sessions.values())
                        and not self._sources and not self._cleanup_tasks and not self._preparations)
        # On failure stay in the closing state, reject new playback, and retain
        # ownership. Later close calls recheck pending cleanup and retry remaining
        # voice disconnects. They never duplicate a source cleanup attempt.
        return tuple(guild_id for guild_id in self._sessions if guild_id in failed)
