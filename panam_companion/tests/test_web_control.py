"""Offline web control: fake lifecycle operations, synthetic diagnostics only."""
import asyncio
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from aiohttp import ClientSession, web
from aiohttp.test_utils import make_mocked_request

from panam_companion.config import RUNTIME, SafeError
from panam_companion.local_owner import LocalOwnership, OwnerLock, OWNER_PATH
from panam_companion.web_app import create_app, HOST, PORT, ORIGIN, MAX_REQUEST
from panam_companion.web_runtime import WebRuntime, CommandRejected, MAX_IDS
from panam_companion import local_cli, web_cli


class FakeController:
    def __init__(self, *_args, **_kwargs):
        self.budget = SimpleNamespace(active=0, data={"uncertain": False, "seconds": 0}, reserved_dollars=0)
        self.state = "LISTENING_OFF"
        self.microphone = self.output = self.muted = self.closed = self.cleanup_failed = False
        self.session = self.fault = self.last_call = None
        self.calls = []
        self.loops = []
        self.gate = None
        self.entered = asyncio.Event()
        self.failure = False
        self.confirmed = True
        self.now = 100.0

    def status(self):
        return {"state": self.state, "microphone_open": self.microphone, "playback_open": self.output,
                "cloud_session_owned": self.session is not None, "muted": self.muted,
                "cleanup_failed": self.cleanup_failed, "fault": self.fault, "last_call": self.last_call}

    async def operation(self, name):
        self.calls.append(name)
        self.loops.append(asyncio.get_running_loop())
        self.entered.set()
        if self.gate:
            await self.gate.wait()
        if self.failure:
            raise ValueError("synthetic-secret-exception")

    async def start_listening(self):
        await self.operation("listen")
        self.microphone = True
        self.state = "LOCAL_LISTENING"

    async def start_call(self):
        await self.operation("start")
        self.session = SimpleNamespace(fault=None, closing=False, started=True, failed=asyncio.Event(),
                                       began=100.0, clock=lambda: self.now)
        self.output = True
        self.state = "ACTIVE"
        self.last_call = None
        self.budget.active = 300
        self.budget.data["uncertain"] = True

    async def mute(self):
        await self.operation("mute")
        self.muted = True

    async def unmute(self):
        await self.operation("unmute")
        self.muted = False

    def end_resources(self):
        if self.session:
            self.last_call = {"status": "CLOSED" if self.confirmed else "UNCERTAIN", "fault": None,
                              "session_closed": self.confirmed, "provider_finalized": self.confirmed,
                              "closure_confirmed": self.confirmed, "budget_uncertain": not self.confirmed,
                              "first_failure": None}
        self.session = None
        self.output = False
        self.budget.active = 0
        self.budget.data["uncertain"] = not self.confirmed
        self.state = "LOCAL_LISTENING" if self.microphone else "LISTENING_OFF"

    async def end_call(self):
        await self.operation("end")
        self.end_resources()

    async def stop_listening(self):
        await self.operation("stop-listening")
        self.end_resources()
        self.microphone = False
        self.state = "LISTENING_OFF"

    async def close(self):
        self.calls.append("close")
        self.end_resources()
        self.microphone = False
        self.state = "LISTENING_OFF"
        self.closed = True


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = FakeController()
        self.runtime = WebRuntime(self.controller)
        self.runtime.snapshot()

    async def asyncTearDown(self):
        if self.controller.gate:
            self.controller.gate.set()
        with contextlib.suppress(SafeError):
            await self.runtime.close()

    def payload(self, action, **extra):
        status = self.runtime.snapshot()
        result = {"command_id": str(uuid4()), "runtime_instance_id": status["runtime_instance_id"],
                  "expected_control_revision": status["control_revision"], "action": action}
        if action == "start":
            result["paid_call_consent"] = True
        result.update(extra)
        return result

    async def execute(self, action):
        response = self.runtime.admit(self.payload(action))
        await asyncio.wait_for(self.runtime.queue.join(), 1)
        self.assertEqual(self.runtime.snapshot()["last_command_outcome"]["status"], "SUCCEEDED")
        return response

    async def active(self):
        await self.execute("listen")
        await self.execute("start")

    async def test_status_has_no_lifecycle_effect(self):
        for _ in range(5):
            self.assertEqual(self.runtime.snapshot()["allowed_commands"], ["listen"])
        self.assertEqual(self.controller.calls, [])
        self.assertFalse(self.controller.budget.data["uncertain"])

    async def test_all_actions_mapping_same_loop_and_end_preserves_microphone(self):
        await self.active()
        await self.execute("mute")
        self.assertTrue(self.runtime.snapshot()["muted"])
        await self.execute("unmute")
        await self.execute("end")
        status = self.runtime.snapshot()
        self.assertTrue(status["microphone_owned"])
        self.assertFalse(status["cloud_session_owned"])
        self.assertFalse(status["output_owned"])
        await self.execute("stop-listening")
        self.assertFalse(self.runtime.snapshot()["microphone_owned"])
        self.assertEqual(self.controller.calls, ["listen", "start", "mute", "unmute", "end", "stop-listening"])
        self.assertTrue(all(loop is asyncio.get_running_loop() for loop in self.controller.loops))

    async def test_stop_active_closes_all(self):
        await self.active()
        await self.execute("stop-listening")
        status = self.runtime.snapshot()
        self.assertFalse(any(status[key] for key in ("microphone_owned", "cloud_session_owned", "output_owned")))

    async def test_active_reservation_is_not_failed_closure(self):
        await self.active()
        self.assertTrue(self.controller.budget.data["uncertain"])
        status = self.runtime.snapshot()
        self.assertIsNone(status["last_call"])
        self.assertIsNone(status["fault"])
        self.assertTrue(status["cloud_ready"])
        self.assertIn("mute", status["allowed_commands"])

    async def test_elapsed_does_not_change_control_revision(self):
        await self.active()
        before = self.runtime.snapshot()
        self.controller.now += 15
        after = self.runtime.snapshot()
        self.assertEqual(before["control_revision"], after["control_revision"])
        self.assertEqual(after["elapsed_session_seconds"], 15)
        self.controller.muted = True
        self.assertGreater(self.runtime.snapshot()["control_revision"], after["control_revision"])

    async def test_cloud_ownership_is_not_readiness(self):
        await self.active()
        self.controller.session.started = False
        status = self.runtime.snapshot()
        self.assertTrue(status["cloud_session_owned"])
        self.assertFalse(status["cloud_ready"])
        self.assertNotIn("mute", status["allowed_commands"])

    async def test_closing_disables_commands(self):
        await self.active()
        self.controller.session.closing = True
        self.assertEqual(self.runtime.snapshot()["allowed_commands"], [])

    async def test_stale_runtime(self):
        with self.assertRaisesRegex(CommandRejected, "") as caught:
            self.runtime.admit(self.payload("listen", runtime_instance_id=str(uuid4())))
        self.assertEqual(caught.exception.code, "STALE_RUNTIME")
        self.assertEqual(self.controller.calls, [])

    async def test_stale_revision(self):
        payload = self.payload("listen")
        self.controller.microphone = True
        self.controller.state = "LOCAL_LISTENING"
        with self.assertRaises(CommandRejected) as caught:
            self.runtime.admit(payload)
        self.assertEqual(caught.exception.code, "STALE_REVISION")

    async def test_invalid_payloads(self):
        for mutation in ({"action": "admin"}, {"command_id": "bad"}, {"extra": "x"},
                         {"expected_control_revision": True}, {"expected_control_revision": -1},
                         {"expected_control_revision": 2**53}, {"runtime_instance_id": []}):
            with self.subTest(mutation=mutation), self.assertRaises(CommandRejected):
                self.runtime.admit(dict(self.payload("listen"), **mutation))
        for payload in (None, [], "listen"):
            with self.assertRaises(CommandRejected):
                self.runtime.admit(payload)
        self.assertEqual(self.controller.calls, [])

    async def test_paid_consent_required(self):
        await self.execute("listen")
        for consent in (False, 1, "true", None):
            with self.subTest(consent=consent), self.assertRaises(CommandRejected):
                self.runtime.admit(self.payload("start", paid_call_consent=consent))
        missing = self.payload("start")
        del missing["paid_call_consent"]
        with self.assertRaises(CommandRejected):
            self.runtime.admit(missing)

    async def test_invalid_state_does_not_start_cloud(self):
        with self.assertRaises(CommandRejected) as caught:
            self.runtime.admit(self.payload("start"))
        self.assertEqual(caught.exception.code, "COMMAND_NOT_ALLOWED")

    async def test_pending_duplicate_conflict_and_busy_prevent_second_paid_action(self):
        await self.execute("listen")
        self.controller.gate = asyncio.Event()
        payload = self.payload("start")
        self.assertEqual(self.runtime.admit(payload)["status"], "ACCEPTED")
        await self.controller.entered.wait()
        self.assertEqual(self.runtime.snapshot()["current_operation"], "start")
        self.assertEqual(self.runtime.snapshot()["allowed_commands"], [])
        self.assertEqual(self.runtime.admit(payload)["status"], "ACCEPTED")
        with self.assertRaises(CommandRejected) as caught:
            self.runtime.admit(self.payload("start"))
        self.assertEqual(caught.exception.code, "OPERATION_IN_PROGRESS")
        with self.assertRaises(CommandRejected) as caught:
            self.runtime.admit(dict(payload, action="end", paid_call_consent=True))
        self.assertEqual(caught.exception.code, "INVALID_COMMAND")
        with self.assertRaises(CommandRejected) as caught:
            self.runtime.admit(dict(payload, expected_control_revision=999))
        self.assertEqual(caught.exception.code, "COMMAND_ID_CONFLICT")
        self.controller.gate.set()
        await self.runtime.queue.join()
        self.assertEqual(self.runtime.admit(payload)["status"], "SUCCEEDED")
        self.assertEqual(self.controller.calls.count("start"), 1)

    async def test_command_history_bounded(self):
        for _ in range(MAX_IDS + 2):
            await self.execute("listen")
            await self.execute("stop-listening")
        self.assertEqual(len(self.runtime.recent), MAX_IDS)
        self.assertEqual(self.runtime.queue.maxsize, 1)

    async def test_fault_cleanup_and_uncertain_closure_block_start(self):
        await self.execute("listen")
        for field, value in (("fault", "local_call_failed"), ("cleanup_failed", True),
                             ("last_call", {"status": "UNCERTAIN", "closure_confirmed": False})):
            previous = getattr(self.controller, field)
            setattr(self.controller, field, value)
            self.assertNotIn("start", self.runtime.snapshot()["allowed_commands"])
            setattr(self.controller, field, previous)
        self.controller.budget.data["uncertain"] = True
        self.assertNotIn("start", self.runtime.snapshot()["allowed_commands"])

    async def test_budget_limits_guard_start_without_mutating_budget(self):
        await self.execute("listen")
        self.controller.budget.data["seconds"] = 581
        before = dict(self.controller.budget.data)
        self.assertNotIn("start", self.runtime.snapshot()["allowed_commands"])
        self.assertEqual(before, self.controller.budget.data)
        self.controller.budget.data["seconds"] = 0
        self.controller.budget.reserved_dollars = 1
        self.assertNotIn("start", self.runtime.snapshot()["allowed_commands"])

    async def test_status_omits_sensitive_diagnostics(self):
        self.controller.fault = "synthetic-secret-fault"
        self.controller.last_call = {"status": "synthetic-secret-status", "exception": "synthetic-secret-body",
                                    "first_failure": {"provider_event_type": "synthetic-secret-event",
                                                      "exception_class": "synthetic-secret-class"}}
        body = json.dumps(self.runtime.snapshot())
        self.assertNotIn("synthetic-secret", body)
        self.assertIn("SANITIZED", body)
        self.controller.last_call = None

    async def test_error_outcome_has_no_exception_text(self):
        self.controller.failure = True
        self.runtime.admit(self.payload("listen"))
        await self.runtime.queue.join()
        result = self.runtime.snapshot()["last_command_outcome"]
        self.assertEqual(result["error_code"], "COMMAND_FAILED")
        self.assertNotIn("synthetic-secret", json.dumps(result))

    async def test_uncertain_end_is_not_success(self):
        await self.active()
        self.controller.confirmed = False
        self.runtime.admit(self.payload("end"))
        await self.runtime.queue.join()
        status = self.runtime.snapshot()
        self.assertEqual(status["last_command_outcome"]["error_code"], "UNCERTAIN_CLOSURE")
        self.assertNotIn("start", status["allowed_commands"])
        with self.assertRaises(SafeError):
            await self.runtime.close()
        with self.assertRaises(SafeError):
            await self.runtime.close()

    async def test_shutdown_waits_for_admitted_work_then_cleans_once(self):
        self.controller.gate = asyncio.Event()
        self.runtime.admit(self.payload("listen"))
        await self.controller.entered.wait()
        closing = asyncio.create_task(self.runtime.close())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(self.runtime.snapshot()["allowed_commands"], [])
        self.assertFalse(closing.done())
        self.controller.gate.set()
        await closing
        await self.runtime.close()
        self.assertEqual(self.controller.calls, ["listen", "close"])

    async def test_shutdown_cancellation_reconciles_resources(self):
        self.controller.gate = asyncio.Event()
        self.runtime.admit(self.payload("listen"))
        await self.controller.entered.wait()
        closing = asyncio.create_task(self.runtime.close())
        await asyncio.sleep(0)
        closing.cancel()
        self.controller.gate.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertTrue(self.controller.closed)
        self.assertTrue(self.runtime.worker.done())

    async def test_observation_failure_does_not_strand_worker(self):
        self.controller.gate = asyncio.Event()
        self.runtime.admit(self.payload("listen"))
        await self.controller.entered.wait()
        original = self.controller.status
        self.controller.status = Mock(side_effect=ValueError("synthetic-secret"))
        self.controller.gate.set()
        await asyncio.wait_for(self.runtime.queue.join(), 1)
        self.assertFalse(self.runtime.accepting)
        self.assertEqual(self.runtime.last_outcome["error_code"], "STATUS_UNAVAILABLE")
        self.controller.status = original


class HttpBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = FakeController()
        self.runtime = WebRuntime(self.controller)
        self.app = create_app(self.runtime)
        self.app.freeze()

    async def asyncTearDown(self):
        await self.runtime.close()

    async def request(self, method="GET", path="/api/status", *, headers=None, body=b""):
        supplied = {"Host": f"{HOST}:{PORT}"}
        supplied.update(headers or {})
        if method == "POST":
            defaults = {"Origin": ORIGIN, "X-Panam-Command": "1", "Content-Type": "application/json"}
            for key, value in defaults.items():
                supplied.setdefault(key, value)
        supplied = {key: value for key, value in supplied.items() if value is not None}
        payload = Mock(readany=AsyncMock(side_effect=[body, b""]))
        request = make_mocked_request(method, path, headers=supplied, app=self.app, payload=payload,
                                      client_max_size=MAX_REQUEST)
        return await self.app._handle(request)

    async def test_page_refresh_static_and_status_have_no_effect(self):
        for path in ("/", "/", "/admin", "/api/status", "/voice-deck.css", "/voice-deck.js", "/static/panam-selfie.png"):
            response = await self.request(path=path)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("microphone=()", response.headers["Permissions-Policy"])
            self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(self.controller.calls, [])

    async def test_bad_host_including_missing_port_rejected(self):
        for value in (None, "localhost:8767", "127.0.0.1", "evil.test:8767", "127.0.0.1:8768"):
            with self.subTest(value=value):
                self.assertEqual((await self.request(headers={"Host": value})).status, 403)

    async def test_origin_header_and_content_type_rejected(self):
        cases = (({"Origin": None}, 403), ({"Origin": "http://evil.test"}, 403),
                 ({"X-Panam-Command": None}, 403), ({"X-Panam-Command": "0"}, 403),
                 ({"Content-Type": "text/plain"}, 415))
        for headers, expected in cases:
            with self.subTest(headers=headers):
                self.assertEqual((await self.request("POST", "/api/commands", headers=headers, body=b"{}")).status, expected)
        self.assertEqual(self.controller.calls, [])

    async def test_no_cors_or_preflight_permission(self):
        response = await self.request("OPTIONS", "/api/commands", headers={"Origin": "http://evil.test"})
        self.assertEqual(response.status, 405)
        self.assertFalse(any(key.lower().startswith("access-control-") for key in response.headers))

    async def test_traversal_admin_and_secret_paths_not_served(self):
        for path in ("/../config.py", "/%2e%2e/config.py", "/web/../../.env", "/.env", "/arbitrary", "/static/../config.py", "/static/other.png"):
            with self.subTest(path=path):
                self.assertEqual((await self.request(path=path)).status, 404)

    async def test_main_references_approved_portrait_without_diagnostic_blocks(self):
        response = await self.request(path="/")
        html = response.body.decode("utf-8")
        self.assertIn('src="/static/panam-selfie.png"', html)
        self.assertIn('alt="Portrét Panam"', html)
        self.assertIn('href="/admin"', html)
        for technical in ('<pre', 'id="last-call"', 'id="runtime-details"', 'portrait-mark'):
            self.assertNotIn(technical, html)

    async def test_admin_technical_surface_is_read_only(self):
        response = await self.request(path="/admin")
        html = response.body.decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn('data-surface="admin"', html)
        self.assertIn('id="last-call"', html)
        self.assertIn('id="runtime-details"', html)
        self.assertNotIn('<button', html)
        self.assertNotIn('data-action', html)
        self.assertEqual((await self.request("POST", "/admin", body=b"{}")).status, 405)
        self.assertEqual(self.controller.calls, [])

    async def test_portrait_binary_mime_and_approved_fingerprint(self):
        response = await self.request(path="/static/panam-selfie.png")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Content-Type'], 'image/png')
        self.assertEqual(response.body[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(hashlib.sha256(response.body).hexdigest(), '6a0ab4dec8423c486e9ac08faef2aedb1f8ec269767f356e912a785d08d8832f')

    async def test_new_assets_and_admin_keep_host_origin_and_cors_boundaries(self):
        for path in ('/admin', '/static/panam-selfie.png'):
            self.assertEqual((await self.request(path=path, headers={'Host': 'evil.test:8767'})).status, 403)
            self.assertEqual((await self.request(path=path, headers={'Origin': 'http://evil.test'})).status, 403)
            response = await self.request('OPTIONS', path)
            self.assertEqual(response.status, 405)
            self.assertFalse(any(key.lower().startswith('access-control-') for key in response.headers))
        self.assertEqual(self.controller.calls, [])

    async def test_oversized_declared_and_streamed_requests(self):
        for headers in ({}, {"Content-Length": "2048"}):
            self.assertEqual((await self.request("POST", "/api/commands", headers=headers, body=b"x" * 2048)).status, 413)

    async def test_invalid_json_duplicate_fields_and_command_schema(self):
        for body in (b"bad", b'{"action":"listen","action":"start"}', b"[]", b"{}"):
            self.assertEqual((await self.request("POST", "/api/commands", body=body)).status, 400)
        self.assertEqual(self.controller.calls, [])

    async def test_fixed_error_suppresses_exception_text(self):
        with patch.object(self.runtime, "snapshot", side_effect=ValueError("synthetic-secret")):
            response = await self.request()
        self.assertEqual(response.status, 500)
        self.assertEqual(json.loads(response.body), {"error_code": "REQUEST_FAILED"})

    async def test_http_202_admission_precedes_operation_completion(self):
        self.controller.gate = asyncio.Event()
        status = self.runtime.snapshot()
        body = json.dumps({"command_id": str(uuid4()), "runtime_instance_id": status["runtime_instance_id"],
                           "expected_control_revision": status["control_revision"], "action": "listen"}).encode()
        response = await self.request("POST", "/api/commands", body=body)
        self.assertEqual(response.status, 202)
        self.assertEqual(json.loads(response.body)["status"], "ACCEPTED")
        self.assertFalse(self.controller.microphone)
        self.controller.gate.set()
        await self.runtime.queue.join()


class LoopbackSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_http_disconnect_and_refresh_do_not_cancel_owned_work(self):
        # The only real HTTP smoke uses the required loopback address/port and a
        # fake controller. Conflict fails; no alternate port is selected.
        web_cli.check_port()
        controller = FakeController()
        runtime = WebRuntime(controller)
        runner = web.AppRunner(create_app(runtime), access_log=None)
        try:
            await runner.setup()
            await web.TCPSite(runner, HOST, PORT, reuse_address=False).start()
            async with ClientSession(trust_env=False) as client:
                async with client.get(ORIGIN + "/") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("Panam Companion / Voice Deck", await response.text())
                async with client.get(ORIGIN + "/api/status") as response:
                    status = await response.json()
                async with client.post(ORIGIN + "/api/commands", data=b"x" * 2048,
                                       headers={"Origin": ORIGIN, "X-Panam-Command": "1", "Content-Type": "application/json"}) as response:
                    self.assertEqual(response.status, 413)
                self.assertEqual(controller.calls, [])
                controller.gate = asyncio.Event()
                payload = {"command_id": str(uuid4()), "runtime_instance_id": status["runtime_instance_id"],
                           "expected_control_revision": status["control_revision"], "action": "listen"}
                async with client.post(ORIGIN + "/api/commands", json=payload,
                                       headers={"Origin": ORIGIN, "X-Panam-Command": "1"}) as response:
                    self.assertEqual(response.status, 202)
            # Originating HTTP client is now gone, while work remains pending.
            await controller.entered.wait()
            self.assertFalse(controller.microphone)
            async with ClientSession(trust_env=False) as refreshed:
                async with refreshed.get(ORIGIN + "/api/status") as response:
                    self.assertEqual((await response.json())["current_operation"], "listen")
            controller.gate.set()
            await asyncio.wait_for(runtime.queue.join(), 1)
            self.assertTrue(controller.microphone)
            self.assertFalse(controller.closed)
            self.assertEqual(controller.calls, ["listen"])
        finally:
            if controller.gate:
                controller.gate.set()
            await runtime.close()
            await runner.cleanup()


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        directory = RUNTIME / "offline-tests"
        directory.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=directory, prefix="web-owner-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.owner = self.directory / "owner.lock"

    def test_authoritative_path_independent_of_cwd(self):
        self.assertEqual(OWNER_PATH.resolve(), Path("C:/Panam_APP_astra/panam_companion/runtime/companion-owner.lock"))
        self.assertEqual(OwnerLock().path, OWNER_PATH.resolve())

    def test_exclusive_across_processes_and_different_budget_directories(self):
        first = self.directory / "a"
        second = self.directory / "b"
        script = """import sys
from pathlib import Path
from panam_companion.local_owner import LocalOwnership
from panam_companion.config import SafeError
try:
    with LocalOwnership(Path(sys.argv[1]), owner_path=Path(sys.argv[2])):
        pass
except SafeError:
    raise SystemExit(7)
"""
        with LocalOwnership(first, owner_path=self.owner):
            result = subprocess.run([sys.executable, "-B", "-c", script, str(second), str(self.owner)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse(second.exists())
        with LocalOwnership(second, owner_path=self.owner):
            self.assertTrue(self.owner.exists())

    def test_double_acquisition_rejected_then_released(self):
        lock = OwnerLock(self.owner)
        with lock:
            with self.assertRaises(SafeError):
                lock.__enter__()
        with lock:
            pass
        self.assertTrue(self.owner.exists())

    def test_distinct_owner_and_budget_locks_required(self):
        with self.assertRaises(SafeError):
            with LocalOwnership(self.directory, owner_path=self.directory / "instance.lock"):
                pass
        self.assertFalse((self.directory / "instance.lock").exists())

    def test_acquisition_order_and_reverse_release(self):
        events = []
        class Guard:
            def __init__(self, name): self.name = name
            def __enter__(self): events.append(self.name + "+"); return self
            def __exit__(self, *_args): events.append(self.name + "-")
        ownership = LocalOwnership(self.directory, owner_path=self.owner)
        ownership.owner = Guard("owner")
        ownership.owner.path = self.owner
        with patch("panam_companion.local_owner.ProcessLock", return_value=Guard("budget")):
            with ownership:
                pass
        self.assertEqual(events, ["owner+", "budget+", "budget-", "owner-"])

    def test_budget_lock_failure_releases_owner(self):
        with patch("panam_companion.local_owner.ProcessLock", side_effect=SafeError("synthetic")):
            with self.assertRaises(SafeError):
                with LocalOwnership(self.directory, owner_path=self.owner):
                    pass
        with OwnerLock(self.owner):
            pass

    def test_both_launchers_use_shared_ownership_without_audio_or_config_access(self):
        (self.directory / "budget.json").write_text("{}", encoding="utf-8")
        for module, execution in ((local_cli, "run"), (web_cli, "serve")):
            with self.subTest(module=module.__name__), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(sys, "argv", ["launcher", "--live-approved", "--budget-dir", str(self.directory)]))
                stack.enter_context(patch.object(sys.stdin, "isatty", return_value=True))
                stack.enter_context(patch.object(module, "load_config", return_value=object()))
                stack.enter_context(patch.object(module, "Budget", return_value=SimpleNamespace(data={"uncertain": False})))
                guard = stack.enter_context(patch.object(module, "LocalOwnership"))
                stack.enter_context(patch.object(module, execution, new=AsyncMock()))
                if module is local_cli:
                    stack.enter_context(patch.object(module, "LocalController"))
                else:
                    stack.enter_context(patch.object(module, "check_port"))
                self.assertEqual(module.main(), 0)
                guard.assert_called_once_with(self.directory.resolve())
                guard.return_value.__enter__.assert_called_once()
                guard.return_value.__exit__.assert_called_once()

    def test_port_conflict_fails_closed(self):
        with patch.object(web_cli.socket, "socket") as factory:
            factory.return_value.__enter__.return_value.bind.side_effect = OSError("synthetic-private-detail")
            with self.assertRaises(SafeError) as caught:
                web_cli.check_port()
        self.assertIn("8767", str(caught.exception))
        self.assertNotIn("synthetic-private", str(caught.exception))


class ServerLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_app_setup_still_closes_controller(self):
        controller = FakeController()
        with patch.object(web_cli, "create_app", side_effect=ValueError("synthetic")):
            with self.assertRaises(ValueError):
                await web_cli.serve(None, None, controller_factory=lambda *_args, **_kwargs: controller)
        self.assertTrue(controller.closed)

    async def test_shutdown_on_same_loop_after_server_stop(self):
        controller = FakeController()
        stop = asyncio.Event()
        stop.set()
        runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock())
        site = SimpleNamespace(start=AsyncMock())
        with patch.object(web_cli.web, "AppRunner", return_value=runner), patch.object(web_cli.web, "TCPSite", return_value=site) as factory, contextlib.redirect_stdout(io.StringIO()):
            await web_cli.serve(None, None, controller_factory=lambda *_args, **_kwargs: controller, stop_event=stop)
        factory.assert_called_once_with(runner, "127.0.0.1", 8767, reuse_address=False, reuse_port=False)
        self.assertTrue(controller.closed)
        runner.cleanup.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
