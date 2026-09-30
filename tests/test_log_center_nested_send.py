"""Actual chat -> sender -> event -> after-hook regression; no network sends."""

import asyncio
import importlib
import json
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_log_center as fixtures
from test_log_center_alpha2 import IntegratedPlugin

sender_module = importlib.import_module("proactive_test.core.message_sender")


class NestedSendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.center = fixtures.LogCenter(self.temp.name)

    async def asyncTearDown(self):
        self.center.close()
        self.temp.cleanup()

    def prepare(self, results, session="session"):
        plugin = IntegratedPlugin(self.center)
        plugin.context.send_message = AsyncMock(return_value=True)
        plugin.config_data["segmented_reply_settings"]["enable"] = False
        platform = types.SimpleNamespace(
            status="running",
            meta=lambda: types.SimpleNamespace(id="demo"),
            send_by_session=AsyncMock(side_effect=results),
        )
        event = fixtures.Event.__new__(fixtures.Event)
        event._proactive_plugin = plugin
        event._proactive_umo = session
        event._proactive_is_group = False
        event._proactive_target_id = "test"
        event.proactive_sent_chains = []
        event.proactive_send_failed = False
        event._resolve_platform = lambda: platform
        event._persist_sent_chain = AsyncMock()
        plugin._build_proactive_event = lambda session: event
        return plugin, event, platform

    async def run_flow(self, plugin, hook, session="session"):
        with (
            patch.object(
                sender_module,
                "EventType",
                types.SimpleNamespace(OnAfterMessageSentEvent="after"),
            ),
            patch.object(sender_module, "dispatch_event_hook", side_effect=hook),
        ):
            await plugin.check_and_chat(session)

    def rows(self):
        fixtures.flush(self.center)
        return self.center.collect(cap=10000, min_level="DEBUG")["items"]

    async def check_pair(self, results, expected):
        plugin, event, platform = self.prepare(results)

        async def hook(received, event_type):
            self.assertIs(received, event)
            await received.send(
                fixtures.Chain([fixtures.Segment(text="SUPPLEMENT_SECRET")])
            )

        await self.run_flow(plugin, hook)
        rows = self.rows()
        for name in ("send.completed", "run.completed"):
            completions = [r for r in rows if r["event"] == name]
            self.assertEqual(len(completions), 1)
            details = completions[0]["details"]
            self.assertEqual(details["delivery_outcome"], "partial_success")
            self.assertEqual(details["accepted_segments"], expected[0])
            self.assertEqual(details["unknown_segments"], expected[1])
        attempts = [r for r in rows if r["event"] == "send.attempt.finished"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len({r["details"]["operation_id"] for r in attempts}), 2)
        main, child = sorted(attempts, key=lambda r: r["id"])
        self.assertEqual(
            child["details"]["parent_operation_id"], main["details"]["operation_id"]
        )
        self.assertEqual(
            child["details"]["parent_span_id"], main["details"]["operation_id"]
        )
        self.assertEqual({r["details"]["attempt_no"] for r in attempts}, {1})
        self.assertEqual(len({r["details"]["segment_id"] for r in attempts}), 2)
        self.assertEqual(platform.send_by_session.await_count, 2)
        self.assertEqual(plugin.session_data["session"]["unanswered_count"], 1)
        self.assertNotIn("SUPPLEMENT_SECRET", json.dumps(rows))

    async def test_main_true_supplement_none_preserves_both_receipts(self):
        await self.check_pair((True, None), (1, 1))

    async def test_main_none_supplement_true_preserves_main_uncertainty(self):
        await self.check_pair((None, True), (1, 1))

    async def test_false_true_and_true_true_nested_sends_are_distinct(self):
        for first, second, expected, outcome in (
            (True, False, (1, 1), "partial_success"),
            (False, True, (1, 1), "partial_success"),
            (True, True, (2, 0), "accepted"),
        ):
            with self.subTest(first=first, second=second):
                plugin, event, platform = self.prepare((first, second))

                async def hook(received, event_type):
                    await received.send(fixtures.Chain([fixtures.Segment()]))

                start_id = self.center.query()["snapshot_max_id"]
                await self.run_flow(plugin, hook)
                rows = [r for r in self.rows() if r["id"] > start_id]
                terminal = next(
                    r["details"] for r in rows if r["event"] == "run.completed"
                )
                self.assertEqual(
                    (terminal["accepted_segments"], terminal["failed_segments"]),
                    expected,
                )
                self.assertEqual(terminal["delivery_outcome"], outcome)
                self.assertEqual(terminal["planned_segments"], 2)
                self.assertEqual(platform.send_by_session.await_count, 2)
                self.assertEqual(plugin.session_data["session"]["unanswered_count"], 1)

    async def test_nested_exception_and_cancellation_keep_main_acceptance(self):
        for exception in (TimeoutError("ERROR_BODY_SECRET"), asyncio.CancelledError()):
            with self.subTest(exception=type(exception).__name__):
                plugin, event, platform = self.prepare((True, exception))

                async def hook(received, event_type):
                    await received.send(fixtures.Chain([fixtures.Segment()]))

                start_id = self.center.query()["snapshot_max_id"]
                if isinstance(exception, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await self.run_flow(plugin, hook)
                else:
                    await self.run_flow(plugin, hook)
                rows = [r for r in self.rows() if r["id"] > start_id]
                for name in ("send.completed", "run.completed"):
                    completion = [r for r in rows if r["event"] == name]
                    self.assertEqual(len(completion), 1)
                    self.assertEqual(
                        completion[0]["details"]["delivery_outcome"], "partial_success"
                    )
                    self.assertEqual(completion[0]["details"]["accepted_segments"], 1)
                    self.assertEqual(completion[0]["details"]["unknown_segments"], 1)
                plugin.context.send_message.assert_not_called()
                self.assertEqual(platform.send_by_session.await_count, 2)
                self.assertNotIn("ERROR_BODY_SECRET", json.dumps(rows))

    async def test_wrapper_delegation_and_pre_api_fallback_count_once(self):
        plugin, event, platform = self.prepare((True,))
        event._resolve_platform = lambda: None
        plugin.context.send_message = AsyncMock(return_value=True)
        await self.run_flow(plugin, AsyncMock())
        rows = self.rows()
        terminal = next(r["details"] for r in rows if r["event"] == "run.completed")
        attempts = [r for r in rows if r["event"] == "send.attempt.finished"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["details"]["route"], "event_core")
        self.assertEqual(terminal["accepted_segments"], 1)
        self.assertEqual(terminal["planned_segments"], 1)
        platform.send_by_session.assert_not_called()
        plugin.context.send_message.assert_awaited_once()

    async def test_explicit_failure_retry_shares_segment_but_not_main_operation(self):
        plugin, event, platform = self.prepare((True,))
        retry_api = AsyncMock(side_effect=(False, True))

        @fixtures.logmod.send_operation
        async def logical_retry(self):
            for _ in range(2):
                await fixtures.logmod.observed_send(self, "event", retry_api)

        async def hook(received, event_type):
            await logical_retry(plugin)

        await self.run_flow(plugin, hook)
        rows = self.rows()
        retries = sorted(
            [
                r["details"]
                for r in rows
                if r["event"] == "send.attempt.finished"
                and r["details"]["route"] == "event"
            ],
            key=lambda d: d["attempt_no"],
        )
        self.assertEqual([r["attempt_no"] for r in retries], [1, 2])
        self.assertEqual(len({r["segment_id"] for r in retries}), 1)
        self.assertEqual(
            [r["receipt_state"] for r in retries], ["explicit_failure", "accepted"]
        )
        main = next(
            r["details"]
            for r in rows
            if r["event"] == "send.attempt.finished"
            and r["details"]["route"] == "event_platform"
        )
        self.assertNotEqual(main["operation_id"], retries[0]["operation_id"])
        terminal = next(r["details"] for r in rows if r["event"] == "run.completed")
        self.assertEqual(terminal["accepted_segments"], 2)
        self.assertEqual(terminal["failed_segments"], 0)
        self.assertEqual(terminal["planned_segments"], 2)
        self.assertEqual(retry_api.await_count, 2)
        self.assertEqual(platform.send_by_session.await_count, 1)

    async def test_independent_core_tool_send_owns_child_operation(self):
        plugin, event, platform = self.prepare((True,))
        plugin.context.send_message = AsyncMock(return_value=None)

        async def hook(received, event_type):
            await plugin._send_chain_via_core_api(
                "session", fixtures.Chain([fixtures.Segment()])
            )

        await self.run_flow(plugin, hook)
        terminal = next(
            r["details"] for r in self.rows() if r["event"] == "run.completed"
        )
        self.assertEqual(terminal["accepted_segments"], 1)
        self.assertEqual(terminal["unknown_segments"], 1)
        self.assertEqual(platform.send_by_session.await_count, 1)
        plugin.context.send_message.assert_awaited_once()

    async def test_two_failed_attempts_are_one_failed_segment(self):
        plugin, event, platform = self.prepare((True,))
        retry_api = AsyncMock(side_effect=(False, False))

        @fixtures.logmod.send_operation
        async def logical_retry(self):
            for _ in range(2):
                await fixtures.logmod.observed_send(self, "event", retry_api)

        async def hook(received, event_type):
            await logical_retry(plugin)

        await self.run_flow(plugin, hook)
        rows = self.rows()
        terminal = next(r["details"] for r in rows if r["event"] == "run.completed")
        self.assertEqual(terminal["accepted_segments"], 1)
        self.assertEqual(terminal["failed_segments"], 1)
        self.assertEqual(terminal["planned_segments"], 2)
        self.assertEqual(terminal["delivery_outcome"], "partial_success")
        retries = [
            r["details"]
            for r in rows
            if r["event"] == "send.attempt.finished"
            and r["details"]["route"] == "event"
        ]
        self.assertEqual(len(retries), 2)
        self.assertEqual(len({d["segment_id"] for d in retries}), 1)
        self.assertEqual({d["attempt_no"] for d in retries}, {1, 2})
        self.assertEqual(platform.send_by_session.await_count, 1)
        self.assertEqual(retry_api.await_count, 2)

    async def test_segments_and_multiple_supplements_have_unique_logical_units(self):
        plugin, event, platform = self.prepare((True, True, None, True))
        plugin.config_data["segmented_reply_settings"]["enable"] = True

        async def hook(received, event_type):
            for _ in range(2):
                await received.send(fixtures.Chain([fixtures.Segment()]))

        await self.run_flow(plugin, hook)
        rows = self.rows()
        attempts = sorted(
            [r["details"] for r in rows if r["event"] == "send.attempt.finished"],
            key=lambda d: (d["parent_operation_id"] is not None, d["attempt_no"]),
        )
        self.assertEqual(len(attempts), 4)
        self.assertEqual(len({d["segment_id"] for d in attempts}), 4)
        self.assertEqual(len({d["operation_id"] for d in attempts}), 3)
        main = [d for d in attempts if d["parent_operation_id"] is None]
        self.assertEqual([d["segment_index"] for d in main], [1, 2])
        self.assertEqual([d["attempt_no"] for d in main], [1, 2])
        self.assertEqual(
            {d["parent_operation_id"] for d in attempts if d["parent_operation_id"]},
            {main[0]["operation_id"]},
        )
        terminal = next(r["details"] for r in rows if r["event"] == "run.completed")
        self.assertEqual(
            (
                terminal["accepted_segments"],
                terminal["unknown_segments"],
                terminal["planned_segments"],
            ),
            (3, 1, 4),
        )
        self.assertEqual(platform.send_by_session.await_count, 4)

    async def test_delegate_is_consumed_before_recursive_independent_send(self):
        plugin, event, platform = self.prepare((True,))
        calls = 0

        async def api(session, chain):
            nonlocal calls
            calls += 1
            if calls == 1:
                await event.send(fixtures.Chain([fixtures.Segment()]))
                return True
            return None

        platform.send_by_session.side_effect = api
        await self.run_flow(plugin, AsyncMock())
        rows = self.rows()
        attempts = [r["details"] for r in rows if r["event"] == "send.attempt.finished"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len({d["operation_id"] for d in attempts}), 2)
        terminal = next(r["details"] for r in rows if r["event"] == "run.completed")
        self.assertEqual(
            (terminal["accepted_segments"], terminal["unknown_segments"]), (1, 1)
        )
        self.assertEqual(platform.send_by_session.await_count, 2)

    async def test_joined_child_tasks_and_concurrent_runs_do_not_mix(self):
        one, event1, platform1 = self.prepare((True, None), "SECRET_SESSION_ONE")
        two, event2, platform2 = self.prepare((True, True), "SECRET_SESSION_TWO")

        async def hook(received, event_type):
            await asyncio.create_task(
                received.send(fixtures.Chain([fixtures.Segment()]))
            )

        with (
            patch.object(
                sender_module,
                "EventType",
                types.SimpleNamespace(OnAfterMessageSentEvent="after"),
            ),
            patch.object(sender_module, "dispatch_event_hook", side_effect=hook),
        ):
            await asyncio.gather(
                one.check_and_chat("SECRET_SESSION_ONE"),
                two.check_and_chat("SECRET_SESSION_TWO"),
            )
        rows = self.rows()
        terminals = [r for r in rows if r["event"] == "run.completed"]
        self.assertEqual(len(terminals), 2)
        for session, accepted, unknown in (
            ("SECRET_SESSION_ONE", 1, 1),
            ("SECRET_SESSION_TWO", 2, 0),
        ):
            terminal = next(
                r
                for r in terminals
                if r["session_ref"] == self.center.alias("session", session)
            )
            self.assertEqual(
                (
                    terminal["details"]["accepted_segments"],
                    terminal["details"]["unknown_segments"],
                ),
                (accepted, unknown),
            )
            attempts = [
                r
                for r in rows
                if r["event"] == "send.attempt.finished"
                and r["run_id"] == terminal["run_id"]
            ]
            self.assertEqual(len(attempts), 2)
            self.assertEqual(len({r["details"]["operation_id"] for r in attempts}), 2)
        self.assertNotIn("SECRET_SESSION_", json.dumps(rows))
        self.assertEqual(platform1.send_by_session.await_count, 2)
        self.assertEqual(platform2.send_by_session.await_count, 2)

    async def test_late_child_receipt_keeps_closed_terminal_and_other_run_isolation(
        self,
    ):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def api(session, chain):
            nonlocal calls
            calls += 1
            if calls == 1:
                return True
            entered.set()
            await release.wait()
            return True

        plugin, event, platform = self.prepare(api)
        child = None

        async def hook(received, event_type):
            nonlocal child
            child = asyncio.create_task(
                received.send(fixtures.Chain([fixtures.Segment()]))
            )
            await asyncio.wait_for(entered.wait(), 2)

        try:
            await self.run_flow(plugin, hook)
            before = next(r for r in self.rows() if r["event"] == "run.completed")
            self.assertEqual(before["details"]["delivery_outcome"], "partial_success")
            self.assertEqual(before["details"]["accepted_segments"], 1)
            self.assertEqual(before["details"]["unknown_segments"], 1)
            self.assertEqual(before["details"]["pending_attempts"], 1)
            self.assertTrue(before["details"]["incomplete"])
            other, _, _ = self.prepare((True,), "OTHER_SESSION")
            await self.run_flow(other, AsyncMock(), "OTHER_SESSION")
            release.set()
            await child
            rows = self.rows()
            same = [
                r
                for r in rows
                if r["event"] == "run.completed" and r["run_id"] == before["run_id"]
            ]
            self.assertEqual(same, [before])
            late = [
                r
                for r in rows
                if r["event"] == "send.attempt.finished"
                and r["details"]["after_run_terminal"]
            ]
            self.assertEqual(len(late), 1)
            self.assertEqual(late[0]["run_id"], before["run_id"])
            self.assertEqual(late[0]["details"]["receipt_state"], "accepted")
            other_terminal = next(
                r
                for r in rows
                if r["event"] == "run.completed" and r["run_id"] != before["run_id"]
            )
            self.assertEqual(other_terminal["details"]["accepted_segments"], 1)
            self.assertEqual(other_terminal["details"]["unknown_segments"], 0)
        finally:
            release.set()
            if child is not None:
                await child

    async def test_send_started_only_after_terminal_is_a_late_audit_fact(self):
        gate = asyncio.Event()
        plugin, event, platform = self.prepare((True, None))
        child = None

        async def delayed():
            await gate.wait()
            await event.send(fixtures.Chain([fixtures.Segment()]))

        async def hook(received, event_type):
            nonlocal child
            child = asyncio.create_task(delayed())

        try:
            await self.run_flow(plugin, hook)
            terminal = next(r for r in self.rows() if r["event"] == "run.completed")
            self.assertEqual(terminal["details"]["delivery_outcome"], "accepted")
            self.assertEqual(terminal["details"]["pending_attempts"], 0)
            gate.set()
            await child
            rows = self.rows()
            self.assertEqual(
                [r for r in rows if r["event"] == "run.completed"], [terminal]
            )
            late = [
                r
                for r in rows
                if r["event"] == "send.attempt.finished"
                and r["details"]["after_run_terminal"]
            ]
            self.assertEqual(len(late), 1)
            self.assertEqual(late[0]["run_id"], terminal["run_id"])
            self.assertEqual(late[0]["details"]["receipt_state"], "delivery_unknown")
            self.assertEqual(platform.send_by_session.await_count, 2)
        finally:
            gate.set()
            if child is not None:
                await child
