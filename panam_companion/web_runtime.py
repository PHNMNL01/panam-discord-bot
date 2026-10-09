"""One loop-owned command worker; HTTP clients never own controller operations."""
import asyncio
from collections import OrderedDict
import math
from uuid import UUID, uuid4

from .budget import CLOSE_MARGIN, SESSION_SECONDS, TOTAL_SECONDS, TOTAL_DOLLARS, VOICE_PER_SECOND
from .config import SafeError
from .live import FAULT_LABELS, NOT_OBSERVED, SANITIZED, safe_failure_record
from .local_runtime import ACTIVE, LISTENING_OFF, LOCAL_LISTENING, finish_operation

ACTIONS = {"listen": "start_listening", "start": "start_call", "mute": "mute",
           "unmute": "unmute", "end": "end_call", "stop-listening": "stop_listening"}
LOCAL_FAULTS = {"local_input_failed", "local_call_failed", "local_cleanup_failed"}
MAX_IDS = 64


class CommandRejected(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


def valid_id(value):
    if type(value) is not str or len(value) != 36:
        return False
    try:
        parsed = UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except ValueError:
        return False


def safe_fault(value):
    if value is None:
        return None
    return value if type(value) is str and value in FAULT_LABELS | LOCAL_FAULTS else SANITIZED


def safe_last_call(value):
    if value is None:
        return None
    source = value if type(value) is dict else {}
    status = source.get("status")
    result = {"status": status if type(status) is str and status in {"CLOSED", "FAILED", "UNCERTAIN", "UNKNOWN"} else "UNKNOWN",
              "fault": safe_fault(source.get("fault")),
              "first_failure": safe_failure_record(source.get("first_failure"))}
    for field in ("session_closed", "provider_finalized", "closure_confirmed", "budget_uncertain"):
        observed = source.get(field)
        result[field] = observed if type(observed) is bool else NOT_OBSERVED
    return result


class WebRuntime:
    def __init__(self, controller):
        self.controller = controller
        self.loop = asyncio.get_running_loop()
        self.runtime_id = str(uuid4())
        self.revision = 0
        self.signature = None
        self.current_operation = None
        self.last_outcome = None
        self.recent = OrderedDict()
        self.queue = asyncio.Queue(maxsize=1)
        self.accepting = True
        self.closed = False
        self.close_failed = False
        self.close_lock = asyncio.Lock()
        self.worker = asyncio.create_task(self._work(), name="companion-web-command-worker")

    def _own_loop(self):
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("Controller must stay on its owning event loop")

    def _start_allowed(self, fault):
        budget = self.controller.budget
        if fault is not None or getattr(budget, "active", 0) or budget.data.get("uncertain") is not False:
            return False
        seconds = min(SESSION_SECONDS, int(TOTAL_SECONDS - budget.data["seconds"]))
        return seconds > CLOSE_MARGIN + 5 and budget.reserved_dollars + seconds * VOICE_PER_SECOND <= TOTAL_DOLLARS

    def snapshot(self):
        self._own_loop()
        observed = self.controller.status()
        state = observed.get("state")
        state = state if type(state) is str and state in {LISTENING_OFF, LOCAL_LISTENING, ACTIVE} else "UNKNOWN"
        session = self.controller.session
        fault = safe_fault(observed.get("fault") or (session.fault if session else None))
        flags = {key: observed.get(source) is True for key, source in
                 (("microphone_owned", "microphone_open"), ("cloud_session_owned", "cloud_session_owned"),
                  ("output_owned", "playback_open"), ("muted", "muted"), ("cleanup_failed", "cleanup_failed"))}
        closing = bool(session and session.closing)
        ready = bool(session and session.started and not session.failed.is_set() and not closing and state == ACTIVE)
        elapsed = None
        if session and type(session.began) in (float, int):
            duration = session.clock() - session.began
            if type(duration) in (float, int) and math.isfinite(duration) and 0 <= duration <= 1e12:
                elapsed = round(duration, 1)
        allowed = []
        last_call = safe_last_call(observed.get("last_call"))
        if self.accepting and self.current_operation is None and not self.controller.closed and not closing:
            if flags["cloud_session_owned"]:
                allowed.extend(("end", "stop-listening"))
            elif flags["microphone_owned"]:
                allowed.append("stop-listening")
            if not flags["cleanup_failed"] and fault is None and not closing:
                if state == LISTENING_OFF:
                    allowed.append("listen")
                elif state == LOCAL_LISTENING and self._start_allowed(fault) and (last_call is None or last_call["closure_confirmed"] is True):
                    allowed.append("start")
                elif ready:
                    allowed.append("unmute" if flags["muted"] else "mute")
        result = {"runtime_instance_id": self.runtime_id, "lifecycle_state": state,
                  "current_operation": self.current_operation, **flags, "cloud_ready": ready,
                  "session_closing": closing, "fault": fault, "elapsed_session_seconds": elapsed,
                  "allowed_commands": allowed, "last_command_outcome": dict(self.last_outcome) if self.last_outcome else None,
                  "last_call": last_call, "shutting_down": not self.accepting}
        # A monotonic control revision changes with observations, never with the timer.
        signature = repr({key: value for key, value in result.items() if key != "elapsed_session_seconds"})
        if self.signature is not None and self.signature != signature:
            self.revision += 1
        self.signature = signature
        result["control_revision"] = self.revision
        return result

    def admit(self, payload):
        self._own_loop()
        if type(payload) is not dict:
            raise CommandRejected("INVALID_COMMAND", 400)
        action = payload.get("action")
        if type(action) is not str or action not in ACTIONS:
            raise CommandRejected("INVALID_COMMAND", 400)
        fields = {"command_id", "runtime_instance_id", "expected_control_revision", "action"}
        if action == "start":
            fields.add("paid_call_consent")
        if set(payload) != fields or not valid_id(payload.get("command_id")) or not valid_id(payload.get("runtime_instance_id")):
            raise CommandRejected("INVALID_COMMAND", 400)
        revision = payload["expected_control_revision"]
        if type(revision) is not int or not 0 <= revision <= 2**53 - 1:
            raise CommandRejected("INVALID_COMMAND", 400)
        if action == "start" and payload["paid_call_consent"] is not True:
            raise CommandRejected("PAID_CONSENT_REQUIRED", 400)
        if payload["runtime_instance_id"] != self.runtime_id:
            raise CommandRejected("STALE_RUNTIME")
        command_id = payload["command_id"]
        identity = (action, revision, payload.get("paid_call_consent"))
        if command_id in self.recent:
            original, outcome = self.recent[command_id]
            if original != identity:
                raise CommandRejected("COMMAND_ID_CONFLICT")
            return dict(outcome)
        status = self.snapshot()
        if not self.accepting:
            raise CommandRejected("SHUTTING_DOWN")
        if revision != status["control_revision"]:
            raise CommandRejected("STALE_REVISION")
        if self.current_operation is not None:
            raise CommandRejected("OPERATION_IN_PROGRESS")
        if action not in status["allowed_commands"]:
            raise CommandRejected("COMMAND_NOT_ALLOWED")
        outcome = {"command_id": command_id, "action": action, "status": "ACCEPTED", "error_code": None}
        self.recent[command_id] = (identity, outcome)
        while len(self.recent) > MAX_IDS:
            self.recent.popitem(last=False)
        self.current_operation = action
        self.last_outcome = outcome
        self.queue.put_nowait((action, outcome))
        self.snapshot()
        return dict(outcome)

    async def _work(self):
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                action, outcome = item
                try:
                    await finish_operation(getattr(self.controller, ACTIONS[action])())
                    status = self.snapshot()
                    last_call = status["last_call"]
                    if status["cleanup_failed"]:
                        raise SafeError("Cleanup unconfirmed")
                    if action in {"end", "stop-listening"} and last_call and last_call["closure_confirmed"] is not True:
                        outcome.update(status="FAILED", error_code="UNCERTAIN_CLOSURE")
                    else:
                        outcome.update(status="SUCCEEDED")
                except Exception:
                    outcome.update(status="FAILED", error_code="COMMAND_FAILED")
                finally:
                    self.current_operation = None
                    try:
                        self.snapshot()
                    except Exception:
                        # Observation failure must not strand queue.join() or
                        # permit new work against an unknown controller state.
                        self.accepting = False
                        outcome.update(status="FAILED", error_code="STATUS_UNAVAILABLE")
            finally:
                self.queue.task_done()

    async def close(self):
        self._own_loop()
        await finish_operation(self._close())

    async def _close(self):
        async with self.close_lock:
            if self.closed:
                if self.close_failed:
                    raise SafeError("Local runtime cleanup previously unconfirmed.")
                return
            self.accepting = False
            await self.queue.join()
            self.queue.put_nowait(None)
            await self.worker
            try:
                await self.controller.close()
                status = self.snapshot()
                if status["cleanup_failed"] or any(status[key] for key in ("microphone_owned", "cloud_session_owned", "output_owned")):
                    raise SafeError("Local runtime cleanup unconfirmed.")
                if status["last_call"] and status["last_call"]["closure_confirmed"] is not True:
                    raise SafeError("Cloud finalization unconfirmed; inspect existing budget before another call.")
            except BaseException:
                self.close_failed = True
                raise
            finally:
                self.closed = True
