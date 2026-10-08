"""Safe receiver/closure diagnostics: fake sockets/devices, synthetic PCM only."""
import asyncio
import base64
import contextlib
import io
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from panam_companion.audio import DISCORD_FRAME, InputBuffer
from panam_companion.config import SafeError
from panam_companion.live import LiveSession, NOT_OBSERVED, SANITIZED, safe_failure_record
from panam_companion.local_runtime import LOCAL_LISTENING, LocalController
from panam_companion.tests.test_audio_boundary import CONFIG, FakeSocket, fake_budget
from panam_companion.tests.test_local_audio import FakeBackend


class RawSocket(FakeSocket):
    async def __anext__(self):
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        return event if type(event) is str else json.dumps(event)


def audio_event(pcm):
    return {"type": "session.output_audio.delta", "delta": base64.b64encode(pcm).decode("ascii")}


class ReceiverDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket = RawSocket()
        self.budget = fake_budget()

        async def connect(_key):
            return self.socket

        self.session = LiveSession(CONFIG, self.budget, input_buffer=InputBuffer(), connect=connect)
        self.addAsyncCleanup(self.session.close)
        await self.session.start()

    async def fail_event(self, event):
        await self.socket.events.put(event)
        await asyncio.wait_for(self.session.failed.wait(), 1)
        self.assertEqual(self.session.fault, "provider_protocol_or_audio_error")
        self.assertFalse(self.session.input.enabled)
        self.assertTrue(self.session.output.blocked)
        return self.session.first_failure

    async def test_malformed_json_records_parsing_without_raw_content(self):
        record = await self.fail_event('{"secret": "synthetic-sensitive-content"')
        self.assertEqual(record["processing_stage"], "incoming_event_parsing")
        self.assertEqual(record["safe_reason_code"], "malformed_provider_event")
        self.assertEqual(record["exception_class"], "JSONDecodeError")
        self.assertEqual(record["provider_event_type"], NOT_OBSERVED)
        self.assertNotIn("synthetic-sensitive-content", json.dumps(record))

    async def test_non_object_event_is_malformed_without_new_validation(self):
        record = await self.fail_event([])
        self.assertEqual(record["safe_reason_code"], "malformed_provider_event")
        self.assertEqual(record["exception_class"], "AttributeError")

    async def test_invalid_base64_has_distinct_payload_reason(self):
        record = await self.fail_event({"type": "session.output_audio.delta", "delta": "!!private!!"})
        self.assertEqual(record["safe_reason_code"], "invalid_output_audio_payload")
        self.assertEqual(record["processing_stage"], "output_audio_validation")
        self.assertEqual(record["output_audio_bytes"], NOT_OBSERVED)
        self.assertNotIn("private", json.dumps(record))

    async def test_odd_pcm_alignment_is_distinct(self):
        record = await self.fail_event(audio_event(b"x"))
        self.assertEqual(record["safe_reason_code"], "invalid_pcm_alignment")
        self.assertEqual(record["output_audio_bytes"], 1)

    async def test_large_even_chunk_is_distinct(self):
        record = await self.fail_event(audio_event(bytes(48002)))
        self.assertEqual(record["safe_reason_code"], "output_audio_chunk_limit")
        self.assertEqual(record["output_audio_bytes"], 48002)

    async def test_observed_overflow_preserves_pre_failure_metrics(self):
        with self.session.output.lock:
            self.session.output.frames.extend([bytes(DISCORD_FRAME)] * 15)
        record = await self.fail_event(audio_event(bytes(1920)))
        self.assertEqual(record["processing_stage"], "output_buffer_append")
        self.assertEqual(record["exception_class"], "BufferError")
        self.assertEqual(record["safe_reason_code"], "playback_buffer_overflow")
        self.assertEqual((record["output_audio_bytes"], record["output_queue_frames_before"],
                          record["output_queue_capacity"]), (1920, 15, 15))
        self.assertEqual(len(self.session.output.frames), 0)  # Existing fail-closed invalidation.
        self.assertEqual(self.session.output.max_frames, 15)

    async def test_conversion_failure_identifies_actual_nested_code_boundary(self):
        # Invalid synthetic resampler state raises inside the existing converter.
        self.session.output.converter.output_state = "synthetic-private-state"
        record = await self.fail_event(audio_event(bytes(1920)))
        self.assertEqual(record["processing_stage"], "output_audio_conversion")
        self.assertEqual(record["safe_reason_code"], "audio_conversion_error")
        self.assertEqual(record["exception_class"], "TypeError")
        self.assertNotIn("synthetic-private-state", json.dumps(record))

    async def test_append_exception_is_not_assumed_to_be_overflow(self):
        with patch.object(self.session.output, "append", side_effect=RuntimeError("private content")):
            record = await self.fail_event(audio_event(bytes(1920)))
        self.assertEqual(record["processing_stage"], "output_buffer_append")
        self.assertEqual(record["safe_reason_code"], "unknown_receive_exception")
        self.assertEqual(record["exception_class"], "RuntimeError")
        self.assertNotIn("private content", json.dumps(record))

    async def test_transcript_callback_failure_never_retains_transcript_or_custom_class_name(self):
        UnsafeException = type("synthetic_secret_class_name", (Exception,), {})
        self.session.caption = Mock(side_effect=UnsafeException("Bearer synthetic-key; Cookie=secret"))
        record = await self.fail_event({"type": "session.output_transcript.delta", "delta": "private conversation"})
        self.assertEqual(record["safe_reason_code"], "transcript_processing_error")
        self.assertEqual(record["exception_class"], SANITIZED)
        encoded = json.dumps(record)
        for unsafe in ["synthetic_secret_class_name", "synthetic-key", "Cookie", "private conversation"]:
            self.assertNotIn(unsafe, encoded)

    async def test_delegation_failure_is_distinct_without_identifier(self):
        record = await self.fail_event({"type": "session.delegation.created",
                                        "delegation": {"target": "unsafe-private-target", "id": "secret-id"}})
        self.assertEqual(record["safe_reason_code"], "delegation_processing_error")
        self.assertEqual(record["processing_stage"], "delegation_processing")
        self.assertNotIn("secret-id", json.dumps(record))

    async def test_startup_validation_failure_is_distinct(self):
        record = await self.fail_event({"type": "session.started", "session": {"model": "secret-model"}})
        self.assertEqual(record["safe_reason_code"], "invalid_session_startup")
        self.assertEqual(record["processing_stage"], "session_startup_validation")
        self.assertNotIn("secret-model", json.dumps(record))

    async def test_usage_failure_keeps_receiver_available_for_valid_finalization(self):
        record = await self.fail_event({"type": "session.closed", "usage": {"seconds": "private"}})
        self.assertEqual(record["safe_reason_code"], "invalid_session_usage")
        self.assertEqual(record["processing_stage"], "session_usage_finalization")
        await self.session.close()
        self.assertTrue(self.session.finalized.is_set())
        self.assertTrue(self.session.closed)
        self.budget.finish_session.assert_called_once()
        self.assertEqual(self.budget.finish_session.call_args.args[1:], (.1, True))
        self.assertIs(self.session.first_failure, record)

    async def test_first_material_fault_survives_secondary_parse_and_cleanup(self):
        record = await self.fail_event(audio_event(b"x"))
        await self.socket.events.put("malformed secondary secret")
        await self.session.close()
        self.assertIs(self.session.first_failure, record)
        self.assertEqual(record["safe_reason_code"], "invalid_pcm_alignment")
        self.assertEqual(self.session.fault, "provider_protocol_or_audio_error")

    async def test_provider_error_precedes_later_generic_exception_without_exposing_body(self):
        await self.socket.events.put({"type": "error", "error": {"message": "private", "code": "secret"},
                                      "event_id": "secret-correlation"})
        await asyncio.wait_for(self.session.failed.wait(), 1)
        record = self.session.first_failure
        await self.socket.events.put("malformed secondary")
        await self.session.close()
        self.assertIs(self.session.first_failure, record)
        self.assertEqual(record["fault_label"], "provider_error")
        self.assertEqual(record["provider_event_type"], "error")
        self.assertNotIn("secret", json.dumps(record))
        self.assertEqual(self.session.fault, "provider_error")

    async def test_unknown_unhashable_provider_type_is_sanitized(self):
        record = await self.fail_event({"type": ["unsafe-event-content"]})
        self.assertEqual(record["provider_event_type"], SANITIZED)
        self.assertEqual(record["safe_reason_code"], "unknown_receive_exception")
        self.assertNotIn("unsafe-event-content", json.dumps(record))

    async def test_diagnostic_collection_failure_cannot_prevent_fail_closed_cleanup(self):
        with patch.object(self.session, "_failure_record", side_effect=RuntimeError("diagnostic private error")):
            record = await self.fail_event(audio_event(b"x"))
            await self.session.close()
        self.assertEqual(record["safe_reason_code"], "diagnostic_collection_failed")
        self.assertTrue(self.session.closed)
        self.assertTrue(self.socket.closed)
        self.assertTrue(all(task.done() for task in self.session.tasks))
        self.assertEqual(self.budget.finish_session.call_args.args[2], True)

    async def test_queue_measurement_failure_does_not_reject_valid_audio(self):
        class BrokenFrames:
            def __len__(self):
                raise RuntimeError("private metric failure")
        # Exercise only the read-only snapshot, then restore the production queue.
        frames = self.session.output.frames
        self.session.output.frames = BrokenFrames()
        try:
            self.assertEqual(self.session.output_queue_metrics(), (NOT_OBSERVED, NOT_OBSERVED))
        finally:
            self.session.output.frames = frames
        await self.socket.events.put(audio_event(bytes(1920)))
        async def queued():
            while not self.session.output.frames:
                await asyncio.sleep(0)
        await asyncio.wait_for(queued(), 1)
        self.assertIsNone(self.session.fault)
        self.assertIsNone(self.session.first_failure)


class SanitizationTests(unittest.TestCase):
    def test_schema_is_bounded_and_rejects_unsafe_unknown_values(self):
        record = safe_failure_record({
            "fault_label": "private-fault", "component": "private", "provider_event_type": "a" * 100000,
            "exception_class": "ValueError-with-secret", "safe_reason_code": "private-reason",
            "output_audio_bytes": True, "output_queue_capacity": -1,
            "output_queue_frames_before": 10**100, "monotonic_seconds": float("nan"),
            "delta": "audio-or-transcript", "event_id": "secret", "provider_event": {"private": "secret"},
        })
        self.assertEqual(len(record), 10)
        self.assertLess(len(json.dumps(record)), 1000)
        self.assertEqual(record["fault_label"], SANITIZED)
        self.assertEqual(record["provider_event_type"], SANITIZED)
        self.assertEqual(record["output_audio_bytes"], NOT_OBSERVED)
        self.assertEqual(record["monotonic_seconds"], NOT_OBSERVED)
        self.assertNotIn("secret", json.dumps(record))

    def test_strings_with_safe_name_but_unsafe_subclass_are_rejected(self):
        class UnsafeString(str):
            def __hash__(self):
                raise AssertionError("must not invoke untrusted hashing")
        record = safe_failure_record({"fault_label": UnsafeString("provider_error")})
        self.assertEqual(record["fault_label"], SANITIZED)


class BoundaryFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_startup_rejection_preserves_admission_and_final_budget(self):
        class BadStartupSocket(RawSocket):
            async def send(self, raw):
                if json.loads(raw)["type"] == "session.start":
                    await self.events.put({"type": "session.started", "session": {"model": "private-model"}})
                else:
                    await super().send(raw)
        socket = BadStartupSocket()
        async def connect(_key):
            return socket
        budget = fake_budget()
        session = LiveSession(CONFIG, budget, input_buffer=InputBuffer(), connect=connect)
        with self.assertRaises(SafeError):
            await session.start()
        self.assertFalse(session.input.enabled)
        self.assertFalse(session.started)
        self.assertTrue(session.closed)
        self.assertEqual(session.first_failure["processing_stage"], "session_startup_validation")
        self.assertEqual(session.fault, "provider_protocol_or_audio_error")
        self.assertEqual(budget.finish_session.call_args.args[1:], (.1, True))

    async def test_transport_failure_retains_its_existing_separate_fault_label(self):
        class BrokenSocket:
            close = AsyncMock()
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise RuntimeError("private transport failure")
        session = LiveSession(CONFIG, fake_budget(), input_buffer=InputBuffer())
        session.ws = BrokenSocket()
        await session.receive()
        await session.close()
        self.assertEqual(session.fault, "provider_transport_or_protocol_error")
        self.assertEqual(session.first_failure["processing_stage"], "receive_transport")
        self.assertEqual(session.first_failure["safe_reason_code"], "receive_transport_error")
        self.assertNotIn("private", json.dumps(session.first_failure))
        self.assertTrue(session.closed)


class ControllerDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = FakeBackend()
        self.budget = fake_budget()
        self.sockets = []

        def factory(config, budget, **kwargs):
            socket = RawSocket()
            self.sockets.append(socket)
            async def connect(_key):
                return socket
            return LiveSession(config, budget, connect=connect, **kwargs)

        self.controller = LocalController(CONFIG, self.budget, backend=self.backend, session_factory=factory)
        self.addAsyncCleanup(self.controller.close)

    async def failed_call(self):
        await self.controller.start_call()
        session = self.controller.session
        await self.sockets[-1].events.put(audio_event(b"x"))
        async def settled():
            while self.controller.session is not None:
                await asyncio.sleep(.001)
        await asyncio.wait_for(settled(), 1)
        return session

    async def test_record_survives_release_and_status_copies_only_safe_primitives(self):
        session = await self.failed_call()
        status = self.controller.status()
        summary = status["last_call"]
        self.assertEqual(status["state"], LOCAL_LISTENING)
        self.assertEqual(status["fault"], "provider_protocol_or_audio_error")
        self.assertIsNone(self.controller.session)
        self.assertTrue(self.controller.microphone.active)
        self.assertEqual(summary["status"], "FAILED")
        self.assertEqual(summary["first_failure"], session.first_failure)
        self.assertIsNot(summary["first_failure"], session.first_failure)
        self.assertTrue(summary["closure_confirmed"])
        self.assertFalse(summary["budget_uncertain"])
        # A caller cannot alter retained state through the returned dictionary.
        summary["first_failure"]["safe_reason_code"] = "private injected content"
        self.assertEqual(self.controller.status()["last_call"]["first_failure"]["safe_reason_code"],
                         "invalid_pcm_alignment")
        json.dumps(self.controller.last_call)  # No session/worker/audio/event references.

    async def test_existing_cli_status_exposes_retained_diagnostics(self):
        await self.failed_call()
        from panam_companion.local_cli import run
        commands = iter(["status", "quit"])
        async def reader():
            return next(commands)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            await run(self.controller, reader)
        status = json.loads(output.getvalue().splitlines()[1])
        self.assertEqual(status["last_call"]["first_failure"]["safe_reason_code"], "invalid_pcm_alignment")

    async def test_successful_close_has_no_failure_and_keeps_budget_finalization(self):
        await self.controller.start_call()
        session = self.controller.session
        await self.controller.end_call()
        summary = self.controller.status()["last_call"]
        self.assertEqual(summary["status"], "CLOSED")
        self.assertIsNone(summary["fault"])
        self.assertIsNone(summary["first_failure"])
        self.assertIsNone(self.controller.fault)
        self.assertTrue(summary["provider_finalized"])
        self.assertTrue(summary["closure_confirmed"])
        self.assertEqual(self.budget.finish_session.call_args.args[1:], (.1, True))
        self.assertTrue(session.closed)

    async def test_new_session_resets_old_summary_and_rejects_stale_retention(self):
        old = await self.failed_call()
        await self.controller.start_call()
        new = self.controller.session
        self.assertIsNone(self.controller.status()["last_call"])
        self.controller.retain_call(old)
        self.assertIsNone(self.controller.status()["last_call"])
        self.assertIsNone(new.first_failure)
        await self.controller.end_call()
        self.controller.retain_call(old)
        self.assertEqual(self.controller.status()["last_call"]["status"], "CLOSED")
        self.assertIsNone(self.controller.status()["last_call"]["first_failure"])

    async def test_uncertain_backend_close_remains_uncertain_without_changing_budget_arguments(self):
        await self.controller.start_call()
        self.controller.session.backend_uncertain = True
        self.budget.finish_session.side_effect = lambda *_args: self.budget.data.update(uncertain=True)
        await self.controller.end_call()
        summary = self.controller.status()["last_call"]
        self.assertEqual(summary["status"], "UNCERTAIN")
        self.assertFalse(summary["closure_confirmed"])
        self.assertTrue(summary["provider_finalized"])
        self.assertTrue(summary["budget_uncertain"])
        self.assertEqual(self.budget.finish_session.call_args.args[2], False)
        self.assertIsNone(summary["first_failure"])

    async def test_failed_uncertain_closure_keeps_both_facts(self):
        await self.controller.start_call()
        session = self.controller.session
        session.backend_uncertain = True
        self.budget.finish_session.side_effect = lambda *_args: self.budget.data.update(uncertain=True)
        await self.sockets[-1].events.put(audio_event(b"x"))
        await asyncio.wait_for(session.failed.wait(), 1)
        await self.controller.end_call()
        summary = self.controller.status()["last_call"]
        self.assertEqual(summary["status"], "FAILED")
        self.assertTrue(summary["budget_uncertain"])
        self.assertFalse(summary["closure_confirmed"])
        self.assertEqual(summary["first_failure"]["safe_reason_code"], "invalid_pcm_alignment")

    async def test_retention_failure_does_not_prevent_resource_release(self):
        await self.controller.start_call()
        with patch("panam_companion.local_runtime.safe_failure_record", side_effect=RuntimeError("private")):
            await self.controller.end_call()
        self.assertIsNone(self.controller.session)
        self.assertIsNone(self.controller.output)
        self.assertFalse(self.controller.cleanup_failed)
        self.assertTrue(self.sockets[-1].closed)
        self.assertEqual(self.controller.status()["last_call"]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
