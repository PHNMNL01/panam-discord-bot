"""Manual local audio/controller lifetime, independent of Discord.

No device, provider, configuration or budget I/O happens on import/construction.
LOCAL_LISTENING deliberately discards audio; no wake word or pre-roll exists.
"""
import asyncio

from .audio import InputBuffer
from .config import SafeError
from .live import LiveSession, NOT_OBSERVED, safe_failure_record
from .local_audio import Microphone, Playback, get_backend, validate_device

LISTENING_OFF = "LISTENING_OFF"
LOCAL_LISTENING = "LOCAL_LISTENING"
ACTIVE = "ACTIVE"


async def finish_operation(awaitable):
    """Reconcile owned work even under repeated cancellation, then propagate it."""
    task = asyncio.ensure_future(awaitable)
    canceled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            canceled = True
    result = task.result()
    if canceled:
        raise asyncio.CancelledError
    return result


async def audio_operation(operation):
    """Keep synchronous device work off the provider loop; retain native ownership."""
    return await finish_operation(asyncio.to_thread(operation))


class LocalController:
    def __init__(self, config, budget, *, backend=None, input_device=None,
                 output_device=None, input_channels=1, session_factory=LiveSession):
        self.config, self.budget = config, budget
        self.backend = backend
        self.input_device, self.output_device = input_device, output_device
        self.input_channels = input_channels
        self.session_factory = session_factory
        self.input = InputBuffer()
        self.microphone = self.output = self.session = self.watch = None
        self.state = LISTENING_OFF
        self.muted = False
        self.closed = self.cleanup_failed = False
        self.fault = None
        self.last_call = None
        self.lock = asyncio.Lock()

    def status(self):
        return {"state": self.state, "muted": self.muted,
                "microphone_open": self.microphone is not None and self.microphone.stream is not None,
                "cloud_session_owned": self.session is not None and not self.session.closed,
                "playback_open": self.output is not None and self.output.stream is not None,
                "cleanup_failed": self.cleanup_failed, "fault": self.fault,
                "last_call": self.last_call_status()}

    def last_call_status(self):
        """Return independent primitives, never a released session or audio object."""
        if self.last_call is None:
            return None
        try:
            summary = dict(self.last_call)
            summary["first_failure"] = safe_failure_record(summary["first_failure"])
            return summary
        except Exception:
            return {"status": "UNKNOWN", "first_failure": None}

    def retain_call(self, session):
        """Called under the lifecycle lock; stale sessions cannot replace status."""
        if session is not self.session:
            return
        try:
            fault = session.fault
            finalized = session.finalized.is_set()
            confirmed = session.closed and (finalized or not session.start_sent) and not session.backend_uncertain
            uncertain = self.budget.data.get("uncertain", NOT_OBSERVED)
            uncertain = uncertain if type(uncertain) is bool else NOT_OBSERVED
            status = "FAILED" if fault is not None or session.failed.is_set() else (
                "CLOSED" if confirmed and uncertain is False else "UNCERTAIN")
            self.last_call = {
                "status": status,
                "fault": safe_failure_record({"fault_label": fault})["fault_label"] if fault is not None else None,
                "session_closed": bool(session.closed), "provider_finalized": bool(finalized),
                "closure_confirmed": bool(confirmed), "budget_uncertain": uncertain,
                "first_failure": safe_failure_record(session.first_failure),
            }
        except Exception:
            self.last_call = {"status": "UNKNOWN", "fault": NOT_OBSERVED,
                              "session_closed": NOT_OBSERVED, "provider_finalized": NOT_OBSERVED,
                              "closure_confirmed": NOT_OBSERVED, "budget_uncertain": NOT_OBSERVED,
                              "first_failure": None}

    def ensure_ready(self):
        if self.closed or self.cleanup_failed:
            raise SafeError("Controller closed or cleanup unconfirmed; restart blocked.")

    async def _listen(self):
        self.ensure_ready()
        if self.microphone is not None:
            if self.microphone.failed.is_set() or not self.microphone.active:
                raise SafeError("Microphone is not ready.")
            return
        self.microphone = Microphone(self.input, backend=self.backend,
                                     device=self.input_device, channels=self.input_channels)
        try:
            await audio_operation(self.microphone.start)
        except BaseException:
            await finish_operation(self._stop_listening())
            raise
        self.state = LOCAL_LISTENING
        self.watch = asyncio.create_task(self.monitor(), name="companion-local-monitor")

    async def start_listening(self):
        async with self.lock:
            await self._listen()

    async def start_call(self):
        async with self.lock:
            self.ensure_ready()
            if self.session is not None:
                raise SafeError("Local call already active.")
            await self._listen()
            self.microphone.set_muted(self.muted)
            try:
                # Reject unsupported output before reserving/opening a paid session.
                device = await audio_operation(lambda: validate_device(
                    get_backend(self.backend), self.output_device, 2, "output"))
                self.session = self.session_factory(self.config, self.budget, input_buffer=self.input)
                self.last_call = None
                await self.session.start()
                self.output = Playback(self.session.output, backend=self.backend, device=device)
                await audio_operation(self.output.start)
                if self.session.failed.is_set() or self.microphone.failed.is_set() or self.output.failed.is_set():
                    raise SafeError("Local call failed during startup.")
                self.microphone.set_forwarding(True)
                self.state = ACTIVE
                self.fault = None
            except BaseException:
                await finish_operation(self._end_call())
                raise

    async def mute(self):
        await self.set_muted(True)

    async def unmute(self):
        await self.set_muted(False)

    async def set_muted(self, muted):
        async with self.lock:
            self.ensure_ready()
            self.muted = muted
            if self.microphone:
                self.microphone.set_muted(muted)
            # Do not call LiveSession.mute: local output must remain playable.

    async def _end_call(self):
        if self.microphone:
            self.microphone.set_forwarding(False)
        self.input.enable(False)
        failed = False
        if self.output:
            try:
                await audio_operation(self.output.close)
                self.output = None
            except Exception:
                failed = self.cleanup_failed = True
        if self.session:
            try:
                await self.session.close()
                self.retain_call(self.session)
                self.session = None
            except Exception:
                failed = self.cleanup_failed = True
                self.retain_call(self.session)
        self.state = LOCAL_LISTENING if self.microphone else LISTENING_OFF
        if failed:
            raise SafeError("Local call cleanup unconfirmed; restart blocked.")

    async def end_call(self):
        async with self.lock:
            await finish_operation(self._end_call())

    async def _stop_listening(self):
        failed = False
        try:
            await self._end_call()
        except Exception:
            failed = True
        if self.microphone:
            try:
                await audio_operation(self.microphone.close)
                self.microphone = None
            except Exception:
                failed = self.cleanup_failed = True
        self.state = LISTENING_OFF if self.microphone is None else LOCAL_LISTENING
        watch, self.watch = self.watch, None
        if watch and watch is not asyncio.current_task():
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
        if failed:
            raise SafeError("Local resource cleanup unconfirmed; restart blocked.")

    async def stop_listening(self):
        async with self.lock:
            await finish_operation(self._stop_listening())

    async def close(self):
        async with self.lock:
            if self.closed:
                return
            try:
                await finish_operation(self._stop_listening())
            finally:
                self.closed = True

    async def monitor(self):
        try:
            while True:
                await asyncio.sleep(.05)
                async with self.lock:
                    if self.microphone is None or self.closed or self.cleanup_failed:
                        return
                    if self.microphone.failed.is_set() or not self.microphone.active:
                        self.fault = "local_input_failed"
                        await self._stop_listening()
                        return
                    if self.session and (self.session.failed.is_set() or self.session.closing or
                                         self.session.closed or self.output.failed.is_set() or not self.output.active):
                        self.fault = self.session.fault or "local_call_failed"
                        await self._end_call()
        except asyncio.CancelledError:
            pass
        except Exception:
            self.fault = "local_cleanup_failed"
            # A failed native health query still requires closing owned resources.
            async with self.lock:
                if not self.cleanup_failed:
                    try:
                        await self._stop_listening()
                    except Exception:
                        self.cleanup_failed = True
