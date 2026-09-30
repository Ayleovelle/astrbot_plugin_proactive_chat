"""Isolated log-center tests. No AstrBot install, model requests or platform sends.
Run: python -m unittest discover -s tests -v
"""

import asyncio
import importlib
import json
import logging
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]


def module(name, **attrs):
    item = types.ModuleType(name)
    item.__dict__.update(attrs)
    sys.modules[name] = item
    return item


# Stub framework types only. Production modules below are imported unchanged.
pkg = module("proactive_test", __path__=[str(ROOT)])
module("astrbot", __file__=str(ROOT / "__init__.py"))
log = logging.getLogger("isolated_astrbot")
log.addHandler(logging.NullHandler())
module("astrbot.api", logger=log)


class Segment:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


module(
    "astrbot.core.agent.message",
    AssistantMessageSegment=Segment,
    UserMessageSegment=Segment,
    TextPart=Segment,
)
logmod = importlib.import_module("proactive_test.core.log_center")
LogCenter, emit = logmod.LogCenter, logmod.emit
facade = importlib.import_module("proactive_test.core.plugin_logger")
Flow = importlib.import_module("proactive_test.core.chat_flow").ProactiveCoreMixin
Scheduler = importlib.import_module("proactive_test.core.task_scheduler").SchedulerMixin
WebAdminServer = importlib.import_module(
    "proactive_test.core.web_admin_server"
).WebAdminServer


def flush(center):
    assert center._ready.wait(3)
    if center.storage_error:
        raise AssertionError(center.storage_error)
    center._queue.join()


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.center = LogCenter(self.temp.name)

    def tearDown(self):
        self.center.close()
        self.temp.cleanup()

    def test_level_filter_persistence_and_debug_opt_in(self):
        for level in logmod.LEVELS:
            self.center.record("runtime", level)
        flush(self.center)
        self.assertEqual(
            [x["level"] for x in self.center.query(min_level="DEBUG")["items"]],
            ["CRITICAL", "ERROR", "WARNING", "INFO"],
        )
        self.assertEqual(len(self.center.query(min_level="ERROR")["items"]), 2)
        self.center.close()
        self.center = LogCenter(self.temp.name, {"debug_enabled": True})
        self.center.record("runtime", "DEBUG")
        flush(self.center)
        self.assertEqual(
            self.center.query(min_level="DEBUG")["items"][0]["level"], "DEBUG"
        )

    def test_cursor_time_session_and_injection(self):
        for i in range(125):
            self.center.record(
                "decision_skipped",
                session_id="demo" if i % 2 else "x' OR 1=1 --",
                trace_id="t" + str(i % 3),
            )
        flush(self.center)
        one = self.center.query(limit=50)
        two = self.center.query(limit=50, before_id=one["next_cursor"])
        self.assertEqual(len(one["items"]), 50)
        self.assertTrue(
            set(x["id"] for x in one["items"]).isdisjoint(x["id"] for x in two["items"])
        )
        self.assertEqual(
            len(self.center.query(session_id="x' OR 1=1 --", limit=100)["items"]), 63
        )
        self.assertEqual(len(self.center.query(since=time.time() + 10)["items"]), 0)
        with self.assertRaises(ValueError):
            self.center.query(min_level="BUG")
        with self.assertRaises(ValueError):
            self.center.query(since=5, until=1)

    def test_safe_error_and_allowlist(self):
        secret = "PROMPT_BODY_sk-secret-access-token"
        try:
            raise ValueError(secret)
        except ValueError as exc:
            self.center.record(
                "task_error",
                "ERROR",
                exception=exc,
                details={
                    "message": secret,
                    "prompt": secret,
                    "token": secret,
                    "duration_ms": 17,
                },
            )
        flush(self.center)
        row = self.center.query()["items"][0]
        raw = json.dumps(row)
        self.assertNotIn(secret, raw)
        self.assertEqual(row["details"]["exception"]["type"], "ValueError")
        self.assertEqual(row["details"]["duration_ms"], 17)
        self.assertTrue(row["details"]["exception"]["frames"])
        self.assertNotIn("locals", raw)

    def test_retention_row_cap_and_old_rows(self):
        self.center.max_entries = 100
        for _ in range(250):
            self.center.record("runtime")
        flush(self.center)
        with sqlite3.connect(self.center.path) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM logs").fetchone()[0], 100
            )
            conn.execute("UPDATE logs SET ts=1 WHERE id=(SELECT min(id) FROM logs)")
        self.assertEqual(len(self.center.query(limit=100)["items"]), 99)
        self.center.record("runtime")
        flush(self.center)
        with sqlite3.connect(self.center.path) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM logs WHERE ts=1").fetchone()[0], 0
            )

    def test_disabled_and_closed_are_inert(self):
        self.center.close()
        self.center.record("runtime")
        self.assertEqual(self.center._queue.qsize(), 0)
        other = LogCenter(Path(self.temp.name) / "disabled", {"enabled": False})
        other.record("runtime")
        self.assertFalse(other.path.exists())
        other.close()

    def test_local_facade_does_not_capture_other_plugins(self):
        facade.bind_log_center(self.center)
        try:
            log.error("OTHER_PLUGIN_SECRET")
            facade.logger.error("DYNAMIC_SECRET")
            flush(self.center)
            rows = self.center.query()["items"]
            self.assertEqual(len(rows), 1)
            self.assertNotIn("SECRET", json.dumps(rows))
        finally:
            facade.unbind_log_center(self.center)

    def test_storage_failure_is_visible_not_fatal(self):
        blocked = Path(self.temp.name) / "file"
        blocked.write_text("not a directory")
        other = LogCenter(blocked)
        other._ready.wait(3)
        other.record("runtime")
        self.assertTrue(other.query()["meta"]["storage_error"])
        other.close()


class FakePlugin(Flow, Scheduler):
    def __init__(self, center):
        self.log_center = center
        self.data_lock = asyncio.Lock()
        self.session_data = {}
        self.last_message_times = {}
        self.manual_trigger_sessions = set()
        self.web_admin_server = None
        self.telemetry = None
        self.timezone = None
        self._terminating = False
        self.config_data = {
            "enable": True,
            "schedule_settings": {
                "quiet_hours": "0-0",
                "max_unanswered_times": 3,
                "min_interval_minutes": 1,
                "max_interval_minutes": 1,
            },
        }
        self.context = types.SimpleNamespace(
            conversation_manager=types.SimpleNamespace(add_message_pair=AsyncMock())
        )
        self.scheduler = types.SimpleNamespace(
            add_job=lambda *a, **k: None,
            get_jobs=lambda: [],
            remove_job=lambda *a: None,
        )
        self._save_data_internal = AsyncMock()
        self._verify_message_persisted = AsyncMock(return_value=True)
        self._build_proactive_event = lambda session: None
        self._extract_response_text = lambda response: "SYNTHETIC_BODY_SECRET"
        self._extract_response_chain = lambda response: []
        self._prepare_llm_request = AsyncMock(
            return_value={
                "conv_id": "synthetic",
                "history": [],
                "system_prompt": "PROMPT_SECRET",
            }
        )
        self._generate_llm_response = AsyncMock(
            return_value=(object(), "PROMPT_SECRET")
        )
        self._send_proactive_message = AsyncMock(return_value=True)

    def _get_session_config(self, session):
        return self.config_data

    def _normalize_session_id(self, session):
        return session

    def _parse_session_id(self, session):
        return ("demo", "FriendMessage", "test")

    def _get_session_log_str(self, *args):
        return "demo session"

    def _purge_related_jobs(self, *args):
        pass


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.center = LogCenter(self.temp.name)
        self.plugin = FakePlugin(self.center)

    async def asyncTearDown(self):
        self.center.close()
        self.temp.cleanup()

    def rows(self):
        flush(self.center)
        return list(reversed(self.center.query(limit=100)["items"]))

    async def test_success_full_trace(self):
        await self.plugin.check_and_chat("demo:FriendMessage:test")
        rows = self.rows()
        events = [r["event"] for r in rows]
        for expected in [
            "task_started",
            "decision_checked",
            "limit_checked",
            "generation_started",
            "generation_finished",
            "send_started",
            "send_result",
            "history_verified",
            "counter_updated",
            "schedule_selected",
            "scheduled",
            "run.completed",
        ]:
            self.assertIn(expected, events)
        self.assertEqual(len({r["trace_id"] for r in rows}), 1)
        self.assertTrue(rows[0]["trace_id"])
        self.assertNotIn("BODY_SECRET", json.dumps(rows))
        self.assertNotIn("PROMPT_SECRET", json.dumps(rows))
        checked = next(r for r in rows if r["event"] == "limit_checked")["details"]
        self.assertEqual(checked["unanswered_count"], 0)
        self.assertEqual(checked["limit"], 3)
        schedule = next(r for r in rows if r["event"] == "schedule_selected")["details"]
        self.assertEqual(schedule["chosen_interval_seconds"], 60)

    async def test_normal_disabled_skip_no_model_or_send(self):
        self.plugin.config_data["enable"] = False
        await self.plugin.check_and_chat("demo:FriendMessage:test")
        rows = self.rows()
        skip = next(r for r in rows if r["event"] == "decision_skipped")
        self.assertEqual(skip["details"]["reason"], "session_disabled")
        self.plugin._generate_llm_response.assert_not_called()
        self.plugin._send_proactive_message.assert_not_called()

    async def test_limit_skip_values(self):
        self.plugin.session_data = {"demo:FriendMessage:test": {"unanswered_count": 3}}
        await self.plugin.check_and_chat("demo:FriendMessage:test")
        skip = next(r for r in self.rows() if r["event"] == "decision_skipped")
        self.assertEqual(skip["details"]["limit"], 3)
        self.plugin._generate_llm_response.assert_not_called()

    async def test_failure_keeps_exception_and_reschedules(self):
        self.plugin._prepare_llm_request.side_effect = RuntimeError(
            "TOKEN_PROMPT_SECRET"
        )
        await self.plugin.check_and_chat("demo:FriendMessage:test")
        rows = self.rows()
        self.assertIn("task_error", [r["event"] for r in rows])
        self.assertIn("scheduled", [r["event"] for r in rows])
        self.assertNotIn("TOKEN_PROMPT_SECRET", json.dumps(rows))

    async def test_send_failure_does_not_increment(self):
        self.plugin._send_proactive_message.return_value = False
        await self.plugin.check_and_chat("demo:FriendMessage:test")
        rows = self.rows()
        self.assertNotIn("counter_updated", [r["event"] for r in rows])
        self.assertIn("scheduled", [r["event"] for r in rows])

    async def test_concurrent_traces_do_not_mix(self):
        await asyncio.gather(
            self.plugin.check_and_chat("a"), self.plugin.check_and_chat("b")
        )
        rows = self.rows()
        a = {
            r["trace_id"]
            for r in rows
            if r["session_id"] == self.center.alias("session", "a")
        }
        b = {
            r["trace_id"]
            for r in rows
            if r["session_id"] == self.center.alias("session", "b")
        }
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertTrue(a.isdisjoint(b))


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx

        self.temp = tempfile.TemporaryDirectory()
        self.center = LogCenter(self.temp.name)
        self.center.record("started")
        flush(self.center)
        self.plugin = types.SimpleNamespace(
            config={"web_admin": {"password": "fixture-only-password"}},
            log_center=self.center,
        )
        self.server = WebAdminServer(self.plugin)
        self.assertIsNotNone(self.server.app)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.server.app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.center.close()
        self.temp.cleanup()

    async def test_auth_including_no_auth_sentinel(self):
        for headers in [
            {},
            {"Authorization": "Bearer no-auth"},
            {"Authorization": "Bearer invalid"},
        ]:
            self.assertEqual(
                (await self.client.get("/api/logs", headers=headers)).status_code, 401
            )
        login = await self.client.post(
            "/api/login", json={"password": "fixture-only-password"}
        )
        self.client.headers["Authorization"] = "Bearer " + login.json()["token"]
        response = await self.client.get("/api/logs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            (await self.client.get("/api/logs?limit=101")).status_code, 422
        )
        self.assertEqual(
            (await self.client.get("/api/logs?min_level=BUG")).status_code, 400
        )
        self.assertEqual(
            (await self.client.get("/api/logs?since=20&until=10")).status_code, 400
        )

    async def test_no_password_mode(self):
        self.server._auth_enabled = False
        self.assertEqual((await self.client.get("/api/logs")).status_code, 200)


# Send-path tests exercise the production helpers with fake platform adapters.
class Chain:
    def __init__(self, chain):
        self.chain = chain


module("astrbot.core.message.components", Plain=Segment, Record=Segment)
module(
    "astrbot.core.message.message_event_result",
    MessageChain=Chain,
    MessageEventResult=Segment,
    ResultContentType=types.SimpleNamespace(),
)
module(
    "astrbot.core.platform.platform",
    PlatformStatus=types.SimpleNamespace(RUNNING="running"),
)
module(
    "astrbot.core.platform.astrbot_message",
    AstrBotMessage=Segment,
    Group=Segment,
    MessageMember=Segment,
)
module(
    "astrbot.core.platform.message_type",
    MessageType=types.SimpleNamespace(GROUP_MESSAGE="group", FRIEND_MESSAGE="friend"),
)
module(
    "astrbot.core.platform.astr_message_event",
    AstrMessageEvent=Segment,
    MessageSession=Segment,
)
Event = importlib.import_module(
    "proactive_test.core.proactive_event"
).ProactiveMessageEvent
Sender = importlib.import_module("proactive_test.core.message_sender").SenderMixin


class SendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.center = LogCenter(self.temp.name)

    async def asyncTearDown(self):
        self.center.close()
        self.temp.cleanup()

    async def test_unknown_event_result_does_not_claim_receipt(self):
        sender = Sender()
        sender.log_center = self.center
        sender._persist_proactive_message_to_platform_history = AsyncMock()
        event = types.SimpleNamespace(send=AsyncMock(return_value=None))
        self.assertTrue(await sender._send_chain("demo", event, [Segment()]))
        flush(self.center)
        row = self.center.query()["items"][0]
        self.assertEqual(row["details"]["outcome"], "unknown_no_receipt")

    async def test_core_false_reports_failure(self):
        sender = Sender()
        sender.log_center = self.center
        sender.context = types.SimpleNamespace(
            send_message=AsyncMock(return_value=False)
        )
        sender._persist_proactive_message_to_platform_history = AsyncMock()
        self.assertFalse(await sender._send_chain_via_core_api("demo", Chain([])))
        flush(self.center)
        self.assertEqual(
            self.center.query()["items"][0]["details"]["outcome"], "explicit_failure"
        )
        sender._persist_proactive_message_to_platform_history.assert_not_called()

    async def test_real_event_platform_unknown_and_fallback(self):
        plugin = types.SimpleNamespace(
            log_center=self.center,
            context=types.SimpleNamespace(send_message=AsyncMock(return_value=True)),
        )
        event = Event.__new__(Event)
        event._proactive_plugin = plugin
        event._proactive_umo = "demo"
        event._proactive_is_group = False
        event._proactive_target_id = "fixture"
        event.proactive_sent_chains = []
        event._proactive_persist_history = None
        platform = types.SimpleNamespace(
            status="running",
            meta=lambda: types.SimpleNamespace(id="demo"),
            send_by_session=AsyncMock(return_value=None),
        )
        event._resolve_platform = lambda: platform
        self.assertTrue(await event.send(Chain([])))
        flush(self.center)
        self.assertEqual(
            self.center.query()["items"][0]["details"]["outcome"],
            "returned_without_receipt",
        )
        platform.send_by_session.side_effect = TimeoutError("SECRET_RESPONSE")
        self.assertFalse(await event.send(Chain([])))
        plugin.context.send_message.assert_not_called()
        flush(self.center)
        rows = self.center.query()["items"]
        self.assertIn("fallback.suppressed", [r["event"] for r in rows])
        self.assertNotIn("SECRET_RESPONSE", json.dumps(rows))


class ExtendedTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_task_keeps_final_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            center = LogCenter(folder)
            plugin = FakePlugin(center)
            plugin._prepare_llm_request.side_effect = asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await plugin.check_and_chat("demo")
            flush(center)
            events = [r["event"] for r in center.query()["items"]]
            self.assertIn("task_cancelled", events)
            self.assertEqual(events[0], "run.completed")
            center.close()

    async def test_queue_is_bounded_and_reports_loss(self):
        with tempfile.TemporaryDirectory() as folder:
            center = LogCenter(folder, {"enabled": False})
            center.enabled = True
            for _ in range(1005):
                center.record("runtime")
            self.assertEqual(center._queue.qsize(), 1000)
            self.assertEqual(center.dropped, 5)
            center.close()

    async def test_runtime_source_template_hides_fstrings(self):
        with tempfile.TemporaryDirectory() as folder:
            center = LogCenter(folder)
            facade.bind_log_center(center)
            logger = facade.logger
            secret = "body_SECRET_token"
            try:
                logger.warning(f"fixture failure: {secret}")
                flush(center)
                record = center.query()["items"][0]
                self.assertIn("fixture failure", record["summary"])
                self.assertIn("[已隐藏]", record["summary"])
                self.assertNotIn(secret, json.dumps(record))
            finally:
                facade.unbind_log_center(center)
                center.close()

    async def test_debug_does_not_affect_global_logger_threshold(self):
        before = log.level
        with tempfile.TemporaryDirectory() as folder:
            center = LogCenter(folder, {"debug_enabled": True})
            facade.bind_log_center(center)
            try:
                facade.logger.debug("secret dynamic fixture")
                flush(center)
                self.assertEqual(
                    center.query(min_level="DEBUG")["items"][0]["level"], "DEBUG"
                )
                self.assertEqual(log.level, before)
            finally:
                facade.unbind_log_center(center)
                center.close()


module("astrbot.api.provider", ProviderRequest=Segment)
Llm = importlib.import_module("proactive_test.core.llm_adapter").LlmMixin


class LlmDetailTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_context_and_provider_decision_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            center = LogCenter(folder)
            llm = Llm()
            llm.log_center = center
            settings = {
                "source_mode": "hybrid",
                "platform_history_count": 10,
                "include_bot_messages": False,
                "bot_identifiers": set(),
                "platform_context_max_chars": 1000,
            }
            llm._load_platform_message_history_records = AsyncMock(return_value=([], 0))
            llm._format_platform_history_as_context = lambda *a, **k: ("", 0, 0)
            contexts, platform = await llm._build_effective_history_context(
                "demo", [{"content": "HISTORY_SECRET"}], settings
            )
            self.assertEqual(len(contexts), 1)
            self.assertEqual(platform, "")
            provider = object()
            llm.context = types.SimpleNamespace(
                get_current_chat_provider_id=AsyncMock(return_value="PROVIDER_SECRET"),
                provider_manager=types.SimpleNamespace(
                    get_provider_by_id=AsyncMock(return_value=provider)
                ),
            )
            self.assertIs(await llm._resolve_chat_provider("demo"), provider)
            flush(center)
            rows = center.query()["items"]
            serialized = json.dumps(rows)
            self.assertNotIn("HISTORY_SECRET", serialized)
            self.assertNotIn("PROVIDER_SECRET", serialized)
            context = next(r for r in rows if r["event"] == "context_selected")[
                "details"
            ]
            self.assertEqual(context["context_count"], 1)
            self.assertEqual(context["history_count"], 1)
            self.assertEqual(context["platform_records"], 0)
            self.assertEqual(rows[0]["details"]["route"], "current_provider_id")
            center.close()


class FiniteNumberTests(unittest.TestCase):
    def test_nonfinite_metadata_is_unknown_and_json_is_standard(self):
        with tempfile.TemporaryDirectory() as folder:
            center = LogCenter(folder)
            center.record(
                "runtime",
                details={
                    "duration_ms": float("nan"),
                    "limit": float("inf"),
                    "previous_count": float("-inf"),
                },
            )
            flush(center)
            row = center.query()["items"][0]
            for field in ("duration_ms", "limit", "previous_count"):
                self.assertIsNone(row["details"][field])
            json.dumps(row, allow_nan=False)
            center.close()

    def test_nonfinite_time_filters_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            center = LogCenter(folder)
            for value in (float("nan"), float("inf"), float("-inf")):
                for key in ("since", "until"):
                    with self.assertRaises(ValueError):
                        center.query(**{key: value})
            center.close()


if __name__ == "__main__":
    unittest.main()
