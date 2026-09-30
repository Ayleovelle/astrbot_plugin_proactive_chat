"""Permanent regression tests at the production flow/API boundaries.

Framework and transport fixtures are synthetic. No model or platform is called.
"""

import asyncio
import json
import logging
import sqlite3
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock
from unittest.mock import Mock

import test_log_center as fixtures

from test_log_center import (
    FakePlugin,
    Llm,
    LogCenter,
    Segment,
    Sender,
    facade,
    flush,
    log,
    logmod,
)


class IntegratedPlugin(FakePlugin, Sender):
    def __init__(self, center, results=(True, True)):
        super().__init__(center)
        del self._send_proactive_message
        self.config_data.update(
            tts_settings={"enable_tts": False},
            segmented_reply_settings={"enable": True, "interval": "0,0"},
        )
        self._extract_response_text = lambda response: "FIRST_BODY。SECOND_BODY。"
        self._persist_proactive_message_to_platform_history = AsyncMock()
        self.event = types.SimpleNamespace(send=AsyncMock(side_effect=results))
        self._build_proactive_event = lambda session: self.event
        self._run_decorating_hooks = AsyncMock(
            side_effect=lambda event, components: (components, True)
        )
        self._dispatch_after_message_sent_hooks = AsyncMock()


class FlowV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.center = LogCenter(self.temp.name)

    async def asyncTearDown(self):
        self.center.close()
        self.temp.cleanup()

    def rows(self):
        flush(self.center)
        return self.center.collect(cap=10000, min_level="DEBUG")["items"]

    def terminal(self):
        rows = [r for r in self.rows() if r["event"] == "run.completed"]
        self.assertEqual(len(rows), 1)
        return rows[0]["details"]

    async def test_real_two_segment_success_has_unique_terminal_and_attempts(self):
        plugin = IntegratedPlugin(self.center)
        await plugin.check_and_chat("RAW_SESSION_SECRET")
        terminal = self.terminal()
        self.assertEqual(terminal["execution_outcome"], "completed")
        self.assertEqual(terminal["delivery_outcome"], "accepted")
        self.assertEqual(terminal["accepted_segments"], 2)
        self.assertEqual(terminal["planned_segments"], 2)
        self.assertEqual(terminal["not_attempted_segments"], 0)
        prepared = next(r for r in self.rows() if r["event"] == "send.prepared")
        self.assertEqual(prepared["details"]["segment_count"], 2)
        self.assertEqual(terminal["history_outcome"], "verified")
        self.assertEqual(terminal["counter_outcome"], "changed")
        self.assertEqual(terminal["schedule_outcome"], "created")
        attempts = [
            r["details"] for r in self.rows() if r["event"] == "send.attempt.finished"
        ]
        self.assertEqual({d["segment_index"] for d in attempts}, {1, 2})
        self.assertEqual({d["attempt_no"] for d in attempts}, {1, 2})
        self.assertEqual(len({d["operation_id"] for d in attempts}), 1)
        raw = json.dumps(self.rows())
        for secret in ("RAW_SESSION_SECRET", "FIRST_BODY", "SECOND_BODY"):
            self.assertNotIn(secret, raw)

    async def test_partial_success_is_separate_from_flow_true(self):
        plugin = IntegratedPlugin(self.center, (True, False))
        await plugin.check_and_chat("session")
        terminal = self.terminal()
        self.assertEqual(terminal["delivery_outcome"], "partial_success")
        self.assertEqual(terminal["accepted_segments"], 1)
        self.assertEqual(terminal["failed_segments"], 1)
        self.assertFalse(terminal["delivery_unknown"])
        self.assertEqual(plugin.session_data["session"]["unanswered_count"], 1)

    async def test_partial_unknown_preserves_uncertainty_without_replay(self):
        plugin = IntegratedPlugin(self.center, (True, TimeoutError("TOKEN_SECRET")))
        plugin._send_chain_direct = AsyncMock()
        await plugin.check_and_chat("session")
        terminal = self.terminal()
        self.assertEqual(terminal["delivery_outcome"], "partial_success")
        self.assertTrue(terminal["delivery_unknown"])
        self.assertEqual(terminal["unknown_segments"], 1)
        self.assertEqual(plugin.event.send.await_count, 2)
        plugin._send_chain_direct.assert_not_called()
        self.assertNotIn("TOKEN_SECRET", json.dumps(self.rows()))

    async def test_all_explicit_failures_do_not_archive_or_increment(self):
        plugin = IntegratedPlugin(self.center, (False, False))
        await plugin.check_and_chat("session")
        terminal = self.terminal()
        self.assertEqual(terminal["execution_outcome"], "failed")
        self.assertEqual(terminal["delivery_outcome"], "explicit_failure")
        self.assertEqual(terminal["counter_outcome"], "not_changed")
        plugin.context.conversation_manager.add_message_pair.assert_not_called()

    async def test_unknown_none_is_not_terminal_delivery_success(self):
        plugin = IntegratedPlugin(self.center, (None, None))
        await plugin.check_and_chat("session")
        self.assertEqual(self.terminal()["delivery_outcome"], "delivery_unknown")
        self.assertEqual(plugin.event.send.await_count, 2)

    async def test_cancel_in_second_send_propagates_and_retains_first_acceptance(self):
        entered = asyncio.Event()

        async def second_send(chain):
            entered.set()
            await asyncio.Event().wait()

        plugin = IntegratedPlugin(self.center)
        calls = 0

        async def send(chain):
            nonlocal calls
            calls += 1
            return True if calls == 1 else await second_send(chain)

        plugin.event.send.side_effect = send
        task = asyncio.create_task(plugin.check_and_chat("session"))
        await asyncio.wait_for(entered.wait(), 2)
        logmod.cancel_requested(plugin, "session", "manual_cancel")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        terminal = self.terminal()
        self.assertEqual(terminal["execution_outcome"], "cancelled")
        self.assertEqual(terminal["delivery_outcome"], "partial_success")
        self.assertTrue(terminal["delivery_unknown"])
        self.assertEqual(calls, 2)
        observed = next(r for r in self.rows() if r["event"] == "cancel.observed")
        self.assertEqual(observed["details"]["cancel_source"], "manual_cancel")

    async def test_cancel_before_model_has_no_send_or_model_attempt(self):
        plugin = FakePlugin(self.center)
        plugin._prepare_llm_request.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await plugin.check_and_chat("session")
        self.assertEqual(self.terminal()["delivery_outcome"], "not_attempted")
        plugin._generate_llm_response.assert_not_called()
        plugin._send_proactive_message.assert_not_called()

    async def test_cancel_between_segments_marks_unsent_segment_without_unknown_receipt(
        self,
    ):
        entered = asyncio.Event()
        plugin = IntegratedPlugin(self.center)

        async def interval(*args):
            entered.set()
            await asyncio.Event().wait()

        plugin._calc_interval = interval
        task = asyncio.create_task(plugin.check_and_chat("session"))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        terminal = self.terminal()
        self.assertEqual(terminal["execution_outcome"], "cancelled")
        self.assertEqual(terminal["delivery_outcome"], "partial_success")
        self.assertFalse(terminal["delivery_unknown"])
        self.assertEqual(terminal["not_attempted_segments"], 1)
        self.assertEqual(plugin.event.send.await_count, 1)

    async def test_policy_skips_have_no_attempts_and_precise_reasons(self):
        for configuration, expected in (
            (None, "session_config_missing"),
            ({"enable": False}, "session_disabled"),
            (
                {"enable": True, "schedule_settings": {"quiet_hours": "0-24"}},
                "quiet_hours",
            ),
        ):
            with self.subTest(expected=expected):
                plugin = FakePlugin(self.center)
                plugin.config_data = configuration
                await plugin.check_and_chat(expected)
                run = next(
                    r
                    for r in self.rows()
                    if r["event"] == "run.completed"
                    and r["session_id"] == self.center.alias("session", expected)
                )
                self.assertEqual(run["details"]["execution_outcome"], "skipped")
                self.assertEqual(run["details"]["reason"], expected)
                plugin._generate_llm_response.assert_not_called()
                plugin._send_proactive_message.assert_not_called()

    async def test_compensation_does_not_turn_original_error_into_success(self):
        plugin = FakePlugin(self.center)
        plugin._prepare_llm_request.side_effect = ValueError("PROMPT_SECRET")
        await plugin.check_and_chat("session")
        terminal = self.terminal()
        self.assertEqual(terminal["execution_outcome"], "failed")
        self.assertEqual(terminal["schedule_outcome"], "created")
        self.assertEqual(terminal["delivery_outcome"], "not_attempted")
        errors = [r for r in self.rows() if r["event"] == "task_error"]
        self.assertEqual(terminal["error_id"], errors[0]["details"]["error_id"])
        self.assertIn("retry.scheduled", [r["event"] for r in self.rows()])

    async def test_child_context_stage_does_not_mutate_parent(self):
        token = logmod._trace.set(logmod.RunContext(self.center, "session", "a" * 32))
        try:
            logmod.run_update(stage="generation_started")

            async def child():
                logmod.emit(
                    types.SimpleNamespace(log_center=self.center), "send_started"
                )
                self.assertEqual(logmod._trace.get().stage, "send_started")

            await asyncio.create_task(child())
            self.assertEqual(logmod._trace.get().stage, "generation_started")
        finally:
            logmod._trace.reset(token)

    async def test_sqlite_write_failure_does_not_interrupt_chat_and_reports_loss(self):
        class FullConnection(sqlite3.Connection):
            def executemany(self, *args, **kwargs):
                error = sqlite3.OperationalError("database or disk is full")
                error.sqlite_errorcode = 13
                raise error

        class FullCenter(LogCenter):
            def _connect(self):
                return sqlite3.connect(self.path, factory=FullConnection)

        directory = self.temp.name + "/full"
        center = FullCenter(directory)
        try:
            plugin = IntegratedPlugin(center)
            await plugin.check_and_chat("session")
            self.assertTrue(center._ready.wait(3))
            center._queue.join()
            self.assertEqual(plugin.session_data["session"]["unanswered_count"], 1)
            self.assertGreater(center.status()["dropped"], 0)
            self.assertEqual(center.storage_error, "storage_full")
        finally:
            center.close()


class ModelV2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_real_generation_hook_stop_error_and_missing_provider(self):
        for phase, behavior in (
            ("request", True),
            ("response", True),
            ("request", ValueError("HOOK_SECRET")),
            ("response", ValueError("HOOK_SECRET")),
            ("provider", None),
        ):
            with (
                self.subTest(phase=phase, behavior=type(behavior).__name__),
                tempfile.TemporaryDirectory() as directory,
            ):
                center = LogCenter(directory)
                llm = Llm()
                llm.log_center = center
                llm.timezone = None
                llm.telemetry = None
                llm._build_extra_content_parts = lambda **kwargs: []
                llm._build_provider_request = lambda **kwargs: Segment(
                    **kwargs, extra_user_content_parts=[]
                )
                provider = Segment(
                    text_chat=AsyncMock(
                        return_value=Segment(completion_text="BODY_SECRET")
                    )
                )
                llm._resolve_chat_provider = AsyncMock(
                    return_value=None if phase == "provider" else provider
                )
                llm._supported_provider_kwargs = lambda provider: None
                llm._dispatch_llm_request_hooks = AsyncMock(return_value=False)
                llm._dispatch_llm_response_hooks = AsyncMock(return_value=False)
                hook = (
                    llm._dispatch_llm_request_hooks
                    if phase == "request"
                    else llm._dispatch_llm_response_hooks
                )
                if isinstance(behavior, Exception):
                    hook.side_effect = behavior
                elif phase != "provider":
                    hook.return_value = behavior
                result, prompt = await llm._generate_llm_response(
                    "session",
                    {"proactive_prompt": "PROMPT_SECRET"},
                    [],
                    "SYSTEM_SECRET",
                    0,
                )
                flush(center)
                rows = center.query()["items"]
                self.assertNotIn("SECRET", json.dumps(rows))
                if phase == "provider" or phase == "request" and behavior is True:
                    provider.text_chat.assert_not_called()
                    self.assertIsNone(result)
                else:
                    provider.text_chat.assert_awaited_once()
                if isinstance(behavior, Exception):
                    events = [
                        r["details"]
                        for r in rows
                        if r["event"] == "hook_result"
                        and r["details"]["route"] == phase + "_hook"
                    ]
                    self.assertEqual(len(events), 1)
                    self.assertEqual(events[0]["outcome"], "error")
                center.close()

    async def test_actual_boundary_duration_return_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            llm = Llm()
            llm.log_center = center
            llm._supported_provider_kwargs = lambda provider: None

            async def call(**kwargs):
                await asyncio.sleep(0.015)
                return types.SimpleNamespace(completion_text="RESPONSE_SECRET")

            provider = types.SimpleNamespace(text_chat=call)
            req = types.SimpleNamespace(prompt="PROMPT_SECRET", model="MODEL_SECRET")
            await asyncio.sleep(0.150)  # Deliberately outside the observed call.
            await llm._invoke_provider(provider, req)
            flush(center)
            rows = center.query()["items"]
            result = next(
                r["details"] for r in rows if r["event"] == "model.call.finished"
            )
            self.assertGreaterEqual(result["duration_ms"], 10)
            self.assertLess(result["duration_ms"], 100)
            self.assertIsNone(result["timeout_ms"])
            self.assertRegex(result["provider_ref"], r"^P-[a-f0-9]{12}$")
            self.assertRegex(result["model_ref"], r"^M-[a-f0-9]{12}$")
            self.assertEqual(len({r["details"]["operation_id"] for r in rows}), 1)
            self.assertNotIn("SECRET", json.dumps(rows))
            center.close()

    async def test_model_empty_timeout_401_429_and_cancellation(self):
        for result in (
            None,
            TimeoutError("SECRET"),
            type("RemoteError", (Exception,), {"status_code": 401})("SECRET"),
            type("RemoteError", (Exception,), {"status_code": 429})("SECRET"),
            asyncio.CancelledError(),
        ):
            with (
                self.subTest(result=type(result).__name__),
                tempfile.TemporaryDirectory() as directory,
            ):
                center = LogCenter(directory)
                llm = Llm()
                llm.log_center = center
                llm._supported_provider_kwargs = lambda provider: None
                provider = types.SimpleNamespace(
                    text_chat=AsyncMock(return_value=None, side_effect=result)
                )
                if result is None:
                    await llm._invoke_provider(provider, Segment())
                else:
                    with self.assertRaises(type(result)):
                        await llm._invoke_provider(provider, Segment())
                flush(center)
                rows = center.query()["items"]
                self.assertEqual(provider.text_chat.await_count, 1)
                self.assertNotIn("SECRET", json.dumps(rows))
                if result is None:
                    self.assertIn("model.result.empty", [r["event"] for r in rows])
                else:
                    failure = next(
                        r["details"] for r in rows if r["event"] == "model.call.failed"
                    )
                    expected = (
                        "cancelled"
                        if isinstance(result, asyncio.CancelledError)
                        else "timeout"
                        if isinstance(result, TimeoutError)
                        else "authentication_failed"
                        if result.status_code == 401
                        else "rate_limited"
                    )
                    self.assertEqual(failure["error_code"], expected)
                center.close()


class TransportV2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_true_false_none_exception_matrix_on_actual_routes(self):
        for route in ("event", "platform", "core"):
            for value in (True, False, None, TimeoutError("RESPONSE_SECRET")):
                with (
                    self.subTest(route=route, value=type(value).__name__),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    center = LogCenter(directory)
                    sender = Sender()
                    sender.log_center = center
                    sender.telemetry = None
                    sender._parse_session_id = lambda session: (
                        "demo",
                        "FriendMessage",
                        "target",
                    )
                    sender._persist_proactive_message_to_platform_history = AsyncMock()
                    call = AsyncMock(
                        return_value=value,
                        side_effect=value if isinstance(value, Exception) else None,
                    )
                    platform = Segment(
                        status="running",
                        meta=lambda: Segment(id="demo"),
                        send_by_session=call,
                    )
                    sender.context = Segment(
                        platform_manager=Segment(get_insts=lambda: [platform]),
                        send_message=call if route == "core" else AsyncMock(),
                    )
                    if route == "event":
                        await sender._send_chain(
                            "session", Segment(send=call), [Segment()]
                        )
                    elif route == "platform":
                        await sender._send_chain_direct("session", [Segment()])
                    else:
                        await sender._send_chain_via_core_api(
                            "session", fixtures.Chain([])
                        )
                    flush(center)
                    rows = center.query()["items"]
                    attempts = [
                        r["details"]
                        for r in rows
                        if r["event"]
                        in {"send.attempt.finished", "send.attempt.failed"}
                    ]
                    self.assertEqual(len(attempts), 1)
                    expected = (
                        "accepted"
                        if value is True
                        else "explicit_failure"
                        if value is False
                        else "delivery_unknown"
                    )
                    self.assertEqual(attempts[0]["receipt_state"], expected)
                    self.assertEqual(call.await_count, 1)
                    if route != "core":
                        sender.context.send_message.assert_not_called()
                    self.assertNotIn("RESPONSE_SECRET", json.dumps(rows))
                    center.close()

    async def test_local_session_construction_failure_has_only_core_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            sender = Sender()
            sender.log_center = center
            sender._parse_session_id = lambda session: (
                "demo",
                "FriendMessage",
                "target",
            )
            sender._persist_proactive_message_to_platform_history = AsyncMock()
            platform = Segment(
                status="running",
                meta=lambda: Segment(id="demo"),
                send_by_session=AsyncMock(),
            )
            sender.context = Segment(
                platform_manager=Segment(get_insts=lambda: [platform]),
                send_message=AsyncMock(return_value=True),
            )
            with patch(
                "proactive_test.core.message_sender.MS",
                side_effect=ValueError("LOCAL_SECRET"),
            ):
                self.assertTrue(await sender._send_chain_direct("session", [Segment()]))
            platform.send_by_session.assert_not_called()
            sender.context.send_message.assert_awaited_once()
            flush(center)
            rows = center.query()["items"]
            self.assertIn("fallback.scheduled", [r["event"] for r in rows])
            self.assertEqual(
                [
                    r["details"]["route"]
                    for r in rows
                    if r["event"] == "send.attempt.started"
                ],
                ["core"],
            )
            center.close()

    async def test_direct_false_does_not_become_accepted_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            sender = Sender()
            sender.log_center = center
            sender._parse_session_id = lambda session: (
                "demo",
                "FriendMessage",
                "target",
            )
            sender._persist_proactive_message_to_platform_history = AsyncMock()
            platform = types.SimpleNamespace(
                meta=lambda: Segment(id="demo"),
                status="running",
                send_by_session=AsyncMock(return_value=False),
            )
            sender.context = Segment(
                platform_manager=Segment(get_insts=lambda: [platform]),
                send_message=AsyncMock(),
            )
            self.assertTrue(
                await sender._send_chain_direct("session", [Segment()])
            )  # Preserve upstream orchestration result.
            flush(center)
            result = next(
                r
                for r in center.query()["items"]
                if r["event"] == "send.attempt.finished"
            )
            self.assertEqual(result["details"]["receipt_state"], "explicit_failure")
            sender.context.send_message.assert_not_called()
            center.close()

    async def test_failure_before_actual_send_preserves_core_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            sender = Sender()
            sender.log_center = center
            sender._parse_session_id = lambda session: None
            sender._persist_proactive_message_to_platform_history = AsyncMock()
            sender.context = Segment(send_message=AsyncMock(return_value=True))
            self.assertTrue(await sender._send_chain_direct("session", [Segment()]))
            sender.context.send_message.assert_awaited_once()
            flush(center)
            attempts = [
                r
                for r in center.query()["items"]
                if r["event"] == "send.attempt.started"
            ]
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["details"]["route"], "core")
            center.close()


class JournalV2Tests(unittest.TestCase):
    def test_python310_sqlite_classification_accepts_only_canonical_errors(self):
        for message, expected in (
            ("database is locked", "storage_locked"),
            ("database table is locked", "storage_locked"),
            ("database or disk is full", "storage_full"),
            ("database is locked SECRET_PROMPT", "internal_bug"),
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    logmod.error_code(sqlite3.OperationalError(message)), expected
                )

    def test_original_alpha1_columns_upgrade_without_touching_session_data(self):
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            session_file = Path(directory) / "session_data.json"
            original = b'{"session":{"unanswered_count":2}}'
            session_file.write_bytes(original)
            with sqlite3.connect(Path(directory) / "log_center.sqlite3") as conn:
                conn.execute(
                    "CREATE TABLE logs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, severity INTEGER NOT NULL, level TEXT NOT NULL, category TEXT NOT NULL, event TEXT NOT NULL, session_id TEXT NOT NULL, trace_id TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO logs(ts,severity,level,category,event,session_id,trace_id,summary,details) VALUES(?,20,'INFO','runtime','runtime','LEGACY_USER_SECRET','','legacy','{}')",
                    (time.time(),),
                )
            center = LogCenter(directory)
            flush(center)
            item = center.query()["items"][0]
            self.assertEqual(item["schema_version"], 1)
            self.assertTrue(item["details"]["incomplete"])
            self.assertNotIn("LEGACY_USER_SECRET", json.dumps(center.export()))
            self.assertEqual(session_file.read_bytes(), original)
            center.close()

    def test_sqlite_lock_loss_is_visible_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            flush(center)
            with sqlite3.connect(center.path) as locking:
                locking.execute("BEGIN EXCLUSIVE")
                center.record("runtime")
                center._queue.join()
                self.assertEqual(center.storage_error, "storage_locked")
                self.assertEqual(center.dropped, 1)
                locking.rollback()
            center.record("runtime")
            center._queue.join()
            self.assertIsNone(center.storage_error)
            self.assertEqual(center.dropped_total, 1)
            center.close()

    def test_persisted_loss_health_after_restart(self):
        gate = threading.Event()

        class PausedCenter(LogCenter):
            def _worker(self):
                gate.wait(3)
                super()._worker()

        with tempfile.TemporaryDirectory() as directory:
            center = PausedCenter(directory, {"debug_enabled": True})
            for _ in range(1005):
                center.record("runtime", "DEBUG")
            center.record("run.completed", trace_id="b" * 32)
            self.assertEqual(center.dropped, 6)
            gate.set()
            flush(center)
            center.close()
            center = LogCenter(directory)
            flush(center)
            self.assertEqual(center.status()["dropped_total"], 6)
            self.assertIsNotNone(center.status()["loss_window"])
            center.close()

    def test_live_writer_conflict_and_bounded_close_timeout_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            flush(center)
            other = LogCenter(directory)
            self.assertFalse(other.enabled)
            self.assertEqual(other.storage_error, "writer_conflict")
            self.assertTrue(center.status()["writer_alive"])
            other.close()
            center.close()
            fake = LogCenter(directory, {"enabled": False})
            fake._thread = Mock()
            fake._thread.is_alive.return_value = True
            fake.close()
            fake._thread.join.assert_called_once_with(timeout=5)
            self.assertTrue(fake.status()["drain_timed_out"])

    def test_stable_session_alias_after_restart_and_export_randomization(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            center.record(
                "task_started", session_id="RAW_USER_SECRET", trace_id="a" * 32
            )
            center.record(
                "run.completed",
                session_id="RAW_USER_SECRET",
                trace_id="a" * 32,
                details={"execution_outcome": "completed"},
            )
            flush(center)
            alias = center.query()["items"][0]["session_id"]
            first, second = center.export(), center.export()
            self.assertNotEqual(
                first["items"][0]["session_id"], second["items"][0]["session_id"]
            )
            self.assertEqual(len({r["session_id"] for r in first["items"]}), 1)
            center.close()
            center = LogCenter(directory)
            flush(center)
            self.assertEqual(center.alias("session", "RAW_USER_SECRET"), alias)
            self.assertEqual(
                len(center.query(session_id="RAW_USER_SECRET")["items"]), 2
            )
            self.assertNotIn("RAW_USER_SECRET", json.dumps(center.export()))
            center.close()

    def test_priority_and_byte_budget_preserve_terminal_under_saturation(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory, {"enabled": False})
            center.enabled = True
            center.debug_enabled = True
            for _ in range(1000):
                center.record("runtime", "DEBUG")
            center.record("run.completed", details={"execution_outcome": "completed"})
            self.assertEqual(center._queue.qsize(), 1000)
            self.assertEqual(center.dropped, 1)
            self.assertIn(
                "DEBUG:priority_eviction", center.status()["dropped_by_level_reason"]
            )
            self.assertEqual(center._queue.queue[-1][4], "run.completed")
            center._queue.max_bytes = center._queue.bytes
            center.record("runtime", "DEBUG")
            self.assertEqual(center.dropped, 2)
            self.assertLessEqual(center._queue.bytes, center._queue.max_bytes)
            center.close()

    def test_snapshot_freezes_export_and_trace_collects_all_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            for _ in range(220):
                center.record("runtime", trace_id="run")
            flush(center)
            first = center.query(limit=50)
            center.record("runtime", trace_id="run")
            flush(center)
            rows = center.collect(
                cap=10000, trace_id="run", snapshot_max_id=first["snapshot_max_id"]
            )
            self.assertEqual(len(rows["items"]), 220)
            self.assertFalse(rows["truncated"])
            self.assertEqual(len({r["id"] for r in rows["items"]}), 220)
            center.close()

    def test_legacy_schema_migration_and_unknown_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            center.record("runtime")
            flush(center)
            center.close()
            with sqlite3.connect(center.path) as conn:
                conn.execute("UPDATE logs SET schema_version=1")
            center = LogCenter(directory)
            flush(center)
            legacy = center.query()["items"][0]
            self.assertEqual(legacy["schema_version"], 1)
            self.assertTrue(legacy["details"]["incomplete"])
            center.close()
            with sqlite3.connect(center.path) as conn:
                conn.execute("PRAGMA user_version=99")
            center = LogCenter(directory)
            self.assertTrue(center._ready.wait(3))
            self.assertTrue(center.storage_error)
            center.close()

    def test_missing_terminal_recovers_only_as_interrupted_or_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory)
            center.record("task_started", trace_id="a" * 32, session_id="session")
            flush(center)
            center.close()
            center = LogCenter(directory)
            flush(center)
            terminals = [
                r for r in center.query()["items"] if r["event"] == "run.completed"
            ]
            self.assertEqual(len(terminals), 1)
            self.assertEqual(
                terminals[0]["details"]["execution_outcome"], "interrupted_or_unknown"
            )
            self.assertTrue(terminals[0]["details"]["incomplete"])
            center.close()

    def test_value_allowlist_summary_and_console_reject_injected_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            center = LogCenter(directory, {"debug_enabled": True})
            secret = "BODY_SECRET\nERROR <script> URL?token=TOKEN_SECRET"
            center.record(
                "runtime",
                "DEBUG",
                summary=secret,
                details={
                    key: secret
                    for key in [
                        "reason",
                        "outcome",
                        "route",
                        "source",
                        "function",
                        "prompt",
                        "operation_id",
                    ]
                },
            )
            facade.bind_log_center(center)
            received = []

            class Capture(logging.Handler):
                def emit(self, record):
                    received.append(record.getMessage())
                    self_test.assertFalse(record.exc_info)

            self_test = self
            handler = Capture()
            log.addHandler(handler)
            try:
                try:
                    raise ValueError(secret)
                except ValueError:
                    facade.logger.exception(
                        f"diagnostic: {secret}", exc_info=True, stack_info=True
                    )
                flush(center)
                raw = (
                    json.dumps(center.query(min_level="DEBUG"))
                    + json.dumps(received)
                    + json.dumps(center.export(min_level="DEBUG"))
                )
                self.assertNotIn(b"BODY_SECRET", center.path.read_bytes())
                self.assertNotIn(b"TOKEN_SECRET", center.path.read_bytes())
                self.assertNotIn("BODY_SECRET", raw)
                self.assertNotIn("TOKEN_SECRET", raw)
                self.assertNotIn("<script>", raw)
            finally:
                log.removeHandler(handler)
                facade.unbind_log_center(center)
                center.close()


class ApiV2Tests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ApiTests.asyncSetUp
    asyncTearDown = fixtures.ApiTests.asyncTearDown

    async def test_detail_trace_export_permissions_expiry_and_snapshot(self):
        for endpoint in (
            "/api/logs/detail/1",
            "/api/logs/trace/run",
            "/api/logs/export",
        ):
            for authorization in (None, "Bearer no-auth", "Bearer invalid"):
                response = await self.client.get(
                    endpoint,
                    headers={"Authorization": authorization} if authorization else {},
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["cache-control"], "no-store")
        login = await self.client.post(
            "/api/login", json={"password": "fixture-only-password"}
        )
        token = login.json()["token"]
        self.client.headers["Authorization"] = "Bearer " + token
        for _ in range(125):
            self.center.record("runtime", trace_id="run", session_id="RAW_SECRET")
        flush(self.center)
        trace = await self.client.get("/api/logs/trace/run")
        self.assertEqual(trace.status_code, 200)
        self.assertEqual(len(trace.json()["items"]), 125)
        self.assertNotIn("RAW_SECRET", trace.text)
        exported = await self.client.get("/api/logs/export?trace_id=run")
        self.assertEqual(len(exported.json()["items"]), 125)
        self.assertEqual(exported.json()["applied_filters"]["trace_id"], "run")
        self.assertIsNotNone(exported.json()["snapshot_max_id"])
        self.assertNotIn("RAW_SECRET", exported.text)
        self.assertEqual((await self.client.get("/api/logs/detail/1")).status_code, 200)
        self.server._tokens[token] = time.time() - 1
        self.assertEqual((await self.client.get("/api/logs/export")).status_code, 401)
