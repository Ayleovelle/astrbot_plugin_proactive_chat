"""Bounded, local-only event journal. No message/prompt/exception text is stored."""

from __future__ import annotations

import asyncio
import ast
import builtins
import contextvars
import functools
import inspect
import json
import math
import hashlib
import hmac
import os
import queue
import re
import zoneinfo
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field, replace
from typing import Any, cast

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
EVENTS = {
    "task_started": ("decision", "开始检查主动消息条件"),
    "task_finished": ("state", "本轮任务已结束（结果见同链路记录）"),
    "task_cancelled": ("state", "任务被取消"),
    "condition_checked": ("decision", "已完成单项触发条件检查"),
    "decision_checked": ("decision", "已检查会话启用状态与免打扰条件"),
    "limit_checked": ("decision", "已检查未回复次数与上限"),
    "context_selected": ("generation", "已选择上下文来源与范围（不含内容）"),
    "provider_selected": ("generation", "已选择模型提供者路径（不含标识与凭据）"),
    "hook_result": ("generation", "生成钩子检查完成"),
    "schedule_selected": ("state", "已计算下一次随机触发间隔"),
    "decision_skipped": ("decision", "本轮主动消息已跳过"),
    "generation_started": ("generation", "开始生成主动消息"),
    "generation_finished": ("generation", "生成完成（未记录正文）"),
    "generation_empty": ("generation", "未获得可发送的生成结果"),
    "send_started": ("send", "开始调用发送流程"),
    "send_result": ("send", "发送流程返回（不代表用户已收到或已读）"),
    "send_api_result": ("send", "发送接口返回（不代表终端送达）"),
    "send_fallback": ("send", "发送异常，执行原有回退路径；注意重复投递风险"),
    "send_blocked": ("send", "装饰钩子阻止了发送"),
    "history_verified": ("state", "对话存档回读校验通过"),
    "history_unverified": ("state", "对话存档回读未确认"),
    "counter_updated": ("state", "未回复计数已更新"),
    "counter_reset": ("state", "收到用户回复，重置未回复计数"),
    "scheduled": ("state", "已安排下一次主动消息任务"),
    "task_error": ("runtime", "主动消息任务异常"),
    "runtime": ("runtime", "插件运行日志（动态内容已隐藏）"),
    "started": ("runtime", "本地日志中心已启动"),
    "stopped": ("runtime", "本地日志中心已关闭"),
}
# Only program-owned scalar metadata is accepted, never arbitrary payloads.
DETAIL_KEYS = {
    "reason",
    "outcome",
    "route",
    "duration_ms",
    "unanswered_count",
    "previous_count",
    "limit",
    "text_length",
    "component_count",
    "next_trigger_time",
    "source",
    "line",
    "function",
    "trigger",
    "stage",
    "allowed",
    "enabled",
    "has_config",
    "source_mode",
    "history_count",
    "platform_records",
    "injected_count",
    "platform_chars",
    "context_count",
    "min_interval_seconds",
    "max_interval_seconds",
    "chosen_interval_seconds",
    "fallback_index",
    "stopped",
    "condition",
    "value",
    "expected",
}
_trace: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "proactive_log_trace", default=None
)

# Version 2 extends the existing journal; old rows remain readable as schema 1.
EVENTS.update(
    {
        "run.completed": ("state", "本轮任务终态：执行与投递证据分别汇总"),
        "context.prepared": ("generation", "上下文准备完成（独立于模型调用耗时）"),
        "model.call.started": ("generation", "开始实际模型调用"),
        "model.call.finished": ("generation", "实际模型调用返回（正文未记录）"),
        "model.call.failed": ("generation", "实际模型调用异常"),
        "model.result.empty": ("generation", "实际模型返回空结果"),
        "send.prepared": ("send", "发送分段准备完成"),
        "send.attempt.started": ("send", "开始实际发送调用"),
        "send.attempt.finished": ("send", "发送调用返回（仅表示接口证据）"),
        "send.attempt.failed": ("send", "发送调用中断，投递状态未知"),
        "send.completed": ("send", "本次发送各段证据汇总"),
        "fallback.suppressed": ("send", "投递状态未知，停止回退以避免重复发送"),
        "fallback.scheduled": ("send", "明确失败后执行既有核心回退"),
        "cancel.requested": ("state", "已请求取消任务"),
        "cancel.observed": ("state", "任务已观察到取消信号"),
        "retry.scheduled": ("state", "失败后的后续任务已安排（不是本轮重发）"),
        "retry.failed": ("state", "失败后的补偿调度失败"),
        "history.failed": ("state", "本次对话存档异常"),
    }
)

STRING_VALUES = {
    "reason": {
        "allowed",
        "session_config_missing",
        "session_disabled",
        "quiet_hours",
        "unanswered_limit",
        "new_user_message",
        "plugin_stopping",
        "context_unavailable",
        "no_response",
        "empty_result",
        "decorating_hook",
        "provider_missing",
        "request_hook_stop",
        "response_hook_stop",
        "delivery_unknown",
        "explicit_failure",
        "cancelled",
        "exception",
        "unavailable",
        "not_configured",
        "shutdown",
        "user_reply",
        "manual_cancel",
    },
    "outcome": {
        "available",
        "unavailable",
        "explicit_success",
        "explicit_failure",
        "returned_without_receipt",
        "unknown_no_receipt",
        "unknown_after_exception",
        "flow_returned_true",
        "failed_or_blocked",
        "pass",
        "stop",
        "error",
        "accepted",
        "delivery_unknown",
        "partial_success",
        "not_attempted",
        "completed",
        "skipped",
        "failed",
        "cancelled",
    },
    "route": {
        "current_provider_id",
        "session_fallback",
        "request_hook",
        "response_hook",
        "decorating_hook",
        "text_chat",
        "platform",
        "core",
        "event",
        "event_platform",
        "event_core",
        "platform_to_core",
        "event_to_platform",
    },
    "condition": {
        "session_config_present",
        "session_enabled",
        "quiet_hours_active",
        "silence_threshold",
        "unanswered_limit",
    },
    "source_mode": {"conversation_history", "platform_message_history", "hybrid"},
    "trigger": {"manual", "scheduled", "group_silence", "startup"},
    "execution_outcome": {
        "completed",
        "skipped",
        "failed",
        "cancelled",
        "interrupted_or_unknown",
    },
    "delivery_outcome": {
        "not_attempted",
        "accepted",
        "explicit_failure",
        "partial_success",
        "delivery_unknown",
    },
    "history_outcome": {
        "not_attempted",
        "verified",
        "unverified",
        "failed",
        "skipped_no_text",
    },
    "counter_outcome": {"not_changed", "changed"},
    "schedule_outcome": {"not_created", "created", "failed", "group_silence"},
    "receipt_state": {"accepted", "explicit_failure", "delivery_unknown"},
    "return_kind": {
        "true",
        "false",
        "none",
        "other",
        "exception",
        "cancelled",
        "object",
    },
    "completion_evidence": {
        "api_return_true",
        "api_return_false",
        "no_receipt",
        "exception",
        "cancelled",
        "flow_return_only",
        "phase_events",
    },
    "error_code": {
        "timeout",
        "rate_limited",
        "authentication_failed",
        "platform_unavailable",
        "invalid_response",
        "serialization_error",
        "storage_full",
        "storage_locked",
        "internal_bug",
        "cancelled",
    },
    "operator": {"lt", "gte", "eq"},
    "unavailable_reason": {
        "not_configured",
        "not_measured",
        "legacy_schema",
        "no_receipt",
    },
    "cancel_source": {
        "user_reply",
        "manual_cancel",
        "plugin_shutdown",
        "reload",
        "deadline",
        "unknown",
    },
    "retry_scope": {"next_run"},
}
DETAIL_KEYS.update(STRING_VALUES)
DETAIL_KEYS.update(
    {
        "operation_id",
        "span_id",
        "parent_span_id",
        "provider_ref",
        "model_ref",
        "segment_id",
        "attempt_no",
        "segment_index",
        "segment_count",
        "accepted_segments",
        "failed_segments",
        "unknown_segments",
        "delivery_unknown",
        "duplicate_risk",
        "timeout_ms",
        "error_id",
        "cause_event_id",
        "previous_run_id",
        "failed_stage",
        "last_completed_stage",
        "in_flight_operation",
        "dropped_before",
        "dropped_after",
        "incomplete",
        "quiet_start",
        "quiet_end",
        "timezone",
        "last_activity_age_seconds",
        "threshold_seconds",
        "remaining_seconds",
        "retry_delay_ms",
        "retry_budget",
        "same_operation",
        "flow_returned_true",
        "planned_segments",
        "not_attempted_segments",
        "parent_operation_id",
        "after_run_terminal",
        "pending_attempts",
    }
)
_operation: contextvars.ContextVar[OperationContext | None] = contextvars.ContextVar(
    "proactive_log_operation", default=None
)
_hook_failures = contextvars.ContextVar("proactive_hook_failures", default=0)
_send_continuation: contextvars.ContextVar[tuple[object, object] | None] = (
    contextvars.ContextVar("proactive_send_continuation", default=None)
)


def hook_error_count():
    return _hook_failures.get()


def hook_failed(plugin, route, exception):
    _hook_failures.set(_hook_failures.get() + 1)
    emit(
        plugin,
        "hook_result",
        "WARNING",
        route=route,
        outcome="error",
        exception=exception,
    )


_trusted_summaries: set[str] = set()
_writers: dict[Path, LogCenter] = {}
_writers_lock = threading.Lock()
_sources: set[str] = set()
_functions: set[str] = set()
_timezones = zoneinfo.available_timezones() | {"system_local"}
for _path in Path(__file__).resolve().parent.parent.rglob("*.py"):
    # Initialization only; never perform AST/file I/O on each log event.
    try:
        _sources.add(_path.name)
        _functions.update(
            n.name
            for n in ast.walk(ast.parse(_path.read_text(encoding="utf-8-sig")))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    except (OSError, SyntaxError, UnicodeError):
        pass


@dataclass
class DeliveryCollector:
    """One asyncio run's receipt ledger; shared by copied task contexts.

    Only evidence is shared, never workflow stage or business state. Closing
    freezes the terminal snapshot; late events remain attributable audit facts.
    """

    receipts: dict[str, str] = field(default_factory=dict)
    plans: dict[str, int] = field(default_factory=dict)
    parents: dict[str, str] = field(default_factory=dict)
    pending: set[str] = field(default_factory=set)
    closed: bool = False

    def prepare(self, operation: OperationContext) -> None:
        self.parents[operation.id] = operation.parent_id
        if not self.closed and isinstance(operation.count, int):
            self.plans[operation.id] = operation.count

    def observe(self, operation: OperationContext, state: str | None) -> None:
        if self.closed:
            return
        self.prepare(operation)
        key = f"{operation.id}:{operation.segment}"
        if state is None:
            self.pending.add(key)
            self.plans[operation.id] = max(
                self.plans.get(operation.id, 0), operation.segment
            )
        else:
            self.pending.discard(key)
            # Explicit-failure -> accepted is one successful logical retry.
            # Preserve prior uncertainty, and acceptance over explicit failure.
            prior = self.receipts.get(key)
            self.receipts[key] = (
                "delivery_unknown"
                if "delivery_unknown" in (prior, state)
                else "accepted"
                if "accepted" in (prior, state)
                else state
            )

    def summary(self, scope: str | None = None, *, close: bool = False) -> dict:
        if close:
            self.closed = True

        def included(operation_id):
            if scope is None:
                return True
            seen = set()
            while operation_id and operation_id not in seen:
                if operation_id == scope:
                    return True
                seen.add(operation_id)
                operation_id = self.parents.get(operation_id, "")
            return False

        receipts = {
            key: state
            for key, state in self.receipts.items()
            if included(key.split(":")[0])
        }
        pending = {key for key in self.pending if included(key.split(":")[0])}
        for key in pending:
            receipts[key] = "delivery_unknown"
        return {
            **delivery_summary(
                tuple(receipts.items()),
                tuple((op, count) for op, count in self.plans.items() if included(op)),
            ),
            "pending_attempts": len(pending),
        }


@dataclass(frozen=True)
class RunContext:
    center: object
    session: str
    id: str
    stage: str = "task_started"
    execution_outcome: str = "completed"
    reason: str = "allowed"
    failed_stage: str = ""
    last_completed_stage: str = ""
    history_outcome: str = "not_attempted"
    counter_outcome: str = "not_changed"
    schedule_outcome: str = "not_created"
    delivery: DeliveryCollector = field(default_factory=DeliveryCollector)
    error_id: str = ""
    errors: tuple = ()
    dropped_before: int = 0
    in_flight_operation: str = ""


@dataclass(frozen=True)
class OperationContext:
    id: str
    attempt: int = 0
    segment: int = 1
    count: int = 1
    parent_id: str = ""
    delivery: DeliveryCollector = field(default_factory=DeliveryCollector)


def run_update(**changes: Any) -> None:
    run = _trace.get()
    if run:
        _trace.set(replace(run, **changes))


def delivery_summary(receipts, plans=()):
    states = dict(receipts).values()
    accepted = sum(s == "accepted" for s in states)
    failed = sum(s == "explicit_failure" for s in states)
    unknown = sum(s == "delivery_unknown" for s in states)
    received = dict(receipts)
    not_attempted = sum(
        f"{operation}:{index}" not in received
        for operation, count in plans
        if isinstance(count, int)
        for index in range(1, count + 1)
    )
    planned = sum(count for _, count in plans if isinstance(count, int))
    outcome = (
        "partial_success"
        if accepted and (failed or unknown or not_attempted)
        else "delivery_unknown"
        if unknown
        else "accepted"
        if accepted
        else "explicit_failure"
        if failed
        else "not_attempted"
    )
    return {
        "delivery_outcome": outcome,
        "accepted_segments": accepted,
        "failed_segments": failed,
        "unknown_segments": unknown,
        "delivery_unknown": bool(unknown),
        "planned_segments": planned,
        "not_attempted_segments": not_attempted,
    }


def error_code(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "authentication_failed"
    if isinstance(exc, ConnectionError):
        return "platform_unavailable"
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, "sqlite_errorcode", None)
        # Python 3.10 lacks sqlite_errorcode. Match only SQLite's canonical
        # messages for classification; never retain or display the message.
        if code is None:
            message = exc.args[0] if exc.args else None
            if message == "database or disk is full":
                return "storage_full"
            if isinstance(message, str) and message in {
                "database is locked",
                "database table is locked",
            }:
                return "storage_locked"
        return (
            "storage_full"
            if code == getattr(sqlite3, "SQLITE_FULL", 13)
            else "storage_locked"
            if code
            in (
                getattr(sqlite3, "SQLITE_BUSY", 5),
                getattr(sqlite3, "SQLITE_LOCKED", 6),
            )
            else "internal_bug"
        )
    return "internal_bug"


def alias(plugin, kind, value):
    center = getattr(plugin, "log_center", None)
    return center.alias(kind, value) if center and value is not None else None


def send_operation(func):
    @functools.wraps(func)
    async def wrapped(self, *args, **kwargs):
        continuation = _send_continuation.get()
        if continuation and continuation[0] is self and continuation[1] is wrapped:
            # Consume once: an unrelated send inside the delegate owns a child.
            _send_continuation.set(None)
            return await func(self, *args, **kwargs)
        parent = _operation.get()
        run = _trace.get()
        collector = (
            parent.delivery if parent else run.delivery if run else DeliveryCollector()
        )
        operation = OperationContext(
            uuid.uuid4().hex,
            parent_id=parent.id if parent else "",
            delivery=collector,
        )
        collector.parents[operation.id] = operation.parent_id
        token = _operation.set(operation)
        try:
            return await func(self, *args, **kwargs)
        finally:
            if func.__name__ == "_send_proactive_message":
                result = collector.summary(operation.id)
                emit(
                    self,
                    "send.completed",
                    "WARNING"
                    if result["delivery_outcome"]
                    in {"partial_success", "delivery_unknown", "explicit_failure"}
                    else "INFO",
                    **result,
                )
            _operation.reset(token)

    return wrapped


async def delegated_send(callback, *args, **kwargs):
    """Explicitly continue the current logical segment through one wrapper."""
    target = (
        getattr(callback, "__self__", None),
        getattr(callback, "__func__", callback),
    )
    token = _send_continuation.set(target)
    try:
        return await callback(*args, **kwargs)
    finally:
        _send_continuation.reset(token)


def send_segment(index, count):
    operation = _operation.get()
    if operation:
        _operation.set(replace(operation, segment=index, count=count))


async def observed_send(plugin, route, call):
    """Observe the actual API boundary. Unknown outcomes must never be replayed."""
    operation = _operation.get() or OperationContext(uuid.uuid4().hex)
    operation = replace(operation, attempt=operation.attempt + 1)
    _operation.set(operation)
    emit(plugin, "send.attempt.started", route=route)
    started = time.monotonic()
    try:
        result = await call()
    except BaseException as exc:
        if not isinstance(exc, (Exception, asyncio.CancelledError)):
            raise
        emit(
            plugin,
            "send.attempt.failed",
            "INFO" if isinstance(exc, asyncio.CancelledError) else "WARNING",
            route=route,
            receipt_state="delivery_unknown",
            outcome="delivery_unknown",
            return_kind="cancelled"
            if isinstance(exc, asyncio.CancelledError)
            else "exception",
            duration_ms=round((time.monotonic() - started) * 1000),
            exception=exc,
            error_code=error_code(exc),
            delivery_unknown=True,
            completion_evidence="cancelled"
            if isinstance(exc, asyncio.CancelledError)
            else "exception",
        )
        raise
    receipt = (
        "accepted"
        if result is True
        else "explicit_failure"
        if result is False
        else "delivery_unknown"
    )
    emit(
        plugin,
        "send.attempt.finished",
        "INFO" if result is True else "WARNING",
        route=route,
        receipt_state=receipt,
        outcome=receipt,
        return_kind="true"
        if result is True
        else "false"
        if result is False
        else "none"
        if result is None
        else "other",
        duration_ms=round((time.monotonic() - started) * 1000),
        delivery_unknown=receipt == "delivery_unknown",
        completion_evidence="api_return_true"
        if result is True
        else "api_return_false"
        if result is False
        else "no_receipt",
    )
    return result


def _bounded_int(value, default, low, high):
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def safe_exception(exc: BaseException | None) -> dict[str, Any] | None:
    """Keep true frame locations, omit locals, source lines and exception messages."""
    if not isinstance(exc, BaseException):
        return None
    exceptions: list[dict[str, Any]] = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(exceptions) < 5:
        seen.add(id(current))
        frames: list[dict[str, Any]] = []
        tb = current.__traceback__
        while tb is not None and len(frames) < 64:
            frames.append(
                {
                    "file": Path(tb.tb_frame.f_code.co_filename).name
                    if Path(tb.tb_frame.f_code.co_filename).name in _sources
                    else "external_module",
                    "line": tb.tb_lineno,
                    "function": tb.tb_frame.f_code.co_name
                    if tb.tb_frame.f_code.co_name in _functions
                    else "external_function",
                }
            )
            tb = tb.tb_next
        item = {
            "type": type(current).__name__
            if getattr(builtins, type(current).__name__, None) is type(current)
            or isinstance(current, sqlite3.Error)
            else "ExternalException",
            "frames": frames,
            "frames_truncated": tb is not None,
            "message": "原始异常消息已隐藏，避免泄露正文、凭据或远端响应",
        }
        if isinstance(current, OSError) and isinstance(current.errno, int):
            item["errno"] = current.errno
        exceptions.append(item)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    result = exceptions[0]
    result["category"] = error_code(exc)
    result["fingerprint"] = hashlib.sha256(
        json.dumps(exceptions, sort_keys=True).encode()
    ).hexdigest()[:24]
    if len(exceptions) > 1:
        result["causes"] = exceptions[1:]
    result["chain_truncated"] = current is not None
    # Bounded metadata is explicit about truncation instead of emitting broken JSON.
    if len(json.dumps(result, ensure_ascii=False)) > 10000:
        result.pop("causes", None)
        result["frames_truncated"] = (
            result["frames_truncated"] or len(result["frames"]) > 32
        )
        result["frames"] = result["frames"][:32]
        result["chain_truncated"] = True
    return result


class JournalQueue(queue.Queue):
    """Bound both count and bytes, with a hard cap even for critical events."""

    def __init__(self):
        super().__init__(maxsize=1000)
        self.bytes = 0
        self.max_bytes = 8 * 1024 * 1024

    @staticmethod
    def size(row):
        return len(row[8].encode("utf-8")) + 1024

    @staticmethod
    def priority(row):
        return (
            3
            if row[4] == "run.completed" or row[1] >= 40
            else 2
            if row[3] == "state"
            else 1
            if row[1] >= 20
            else 0
        )

    def _put(self, row):
        self.bytes += self.size(row)
        super()._put(row)

    def _get(self):
        row = super()._get()
        self.bytes -= self.size(row)
        return row

    def put_event(self, row):
        evicted: list[tuple[Any, ...]] = []
        with self.not_full:
            while (
                self._qsize() >= self.maxsize
                or self.bytes + self.size(row) > self.max_bytes
            ):
                victim = next(
                    (x for x in self.queue if self.priority(x) < self.priority(row)),
                    None,
                )
                if victim is None:
                    return False, evicted
                self.queue.remove(victim)
                self.bytes -= self.size(victim)
                self.unfinished_tasks -= 1
                evicted.append(victim)
            self._put(row)
            self.unfinished_tasks += 1
            self.not_empty.notify()
        return True, evicted


class LogCenter:
    """Single-writer bounded queue; failures never interrupt the chat task.

    Retention: at most 10,000 rows by default and 7 days; DB capped at 32 MiB.
    SQLite DELETE journaling avoids an unbounded WAL on a long-running reader.
    """

    def __init__(self, data_dir, config=None):
        config = config if isinstance(config, dict) else {}
        self.enabled = config.get("enabled", True) is not False
        self.debug_enabled = config.get("debug_enabled", False) is True
        self.max_entries = _bounded_int(config.get("max_entries"), 10000, 100, 50000)
        self.retention_days = _bounded_int(config.get("retention_days"), 7, 1, 30)
        self.path = Path(data_dir) / "log_center.sqlite3"
        self._queue = JournalQueue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._closed = False
        self.dropped = 0
        self.storage_error = None
        self.boot_id = uuid.uuid4().hex
        self._alias_key = os.urandom(32)
        self._health_lock = threading.Lock()
        self._run_loss = {}
        self._drop_counts = {}
        self.loss_window = None
        self.last_commit_at = None
        self.dropped_total = 0
        self.pruned_total = 0
        self.drain_timed_out = False
        self._active_runs = {}
        self._previous_runs = {}
        self._cancel_sources = {}
        self._thread = None
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                key_path = self.path.with_suffix(".key")
                try:
                    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    self._alias_key = key_path.read_bytes()
                    if len(self._alias_key) != 32:
                        raise ValueError("Invalid alias key")
                else:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(self._alias_key)
            except (OSError, ValueError):
                self.storage_error = "alias_key_unavailable"
                self.enabled = False
        if self.enabled:
            with _writers_lock:
                previous = _writers.get(self.path.resolve())
                if previous and previous._thread and previous._thread.is_alive():
                    self.storage_error = "writer_conflict"
                    self.enabled = False
                    self._ready.set()
                else:
                    _writers[self.path.resolve()] = self
                    self._thread = threading.Thread(
                        target=self._worker, name="proactive-log-writer", daemon=True
                    )
                    self._thread.start()
        else:
            self._ready.set()

    def record(
        self,
        event,
        level="INFO",
        session_id="",
        trace_id="",
        details=None,
        exception=None,
        summary=None,
    ):
        if not self.enabled or self._closed or event not in EVENTS:
            return
        if level not in LEVELS or (level == "DEBUG" and not self.debug_enabled):
            return
        trace = _trace.get()
        if trace and trace.center is self:
            session_id = session_id or trace.session
            trace_id = trace_id or trace.id
        safe: dict[str, Any] = {}
        for key, value in (details if isinstance(details, dict) else {}).items():
            if key not in DETAIL_KEYS:
                continue
            if value is None:
                safe[key] = None
            elif isinstance(value, str):
                if (
                    value in STRING_VALUES.get(key, set())
                    or key == "timezone"
                    and value in _timezones
                    or key in {"stage", "failed_stage", "last_completed_stage"}
                    and value in EVENTS
                    or key == "source"
                    and value in _sources
                    or key == "function"
                    and value in _functions
                    or key
                    in {
                        "operation_id",
                        "span_id",
                        "parent_span_id",
                        "error_id",
                        "cause_event_id",
                        "previous_run_id",
                        "in_flight_operation",
                        "parent_operation_id",
                    }
                    and re.fullmatch(r"[a-f0-9]{32}", value)
                    or key in {"provider_ref", "model_ref"}
                    and re.fullmatch(r"[PM]-[a-f0-9]{12}", value)
                    or key == "segment_id"
                    and re.fullmatch(r"[a-f0-9]{32}:[0-9]{1,5}", value)
                ):
                    safe[key] = value
            elif isinstance(value, (bool, int, float)) and key not in STRING_VALUES:
                safe[key] = (
                    None
                    if isinstance(value, float) and not math.isfinite(value)
                    else min(32503680000, max(-32503680000, value))
                    if not isinstance(value, bool)
                    else value
                )
        if trace and trace.center is self:
            changes = {"stage": event} if event != "runtime" else {}
            if event in {"model.call.started", "send.attempt.started"}:
                changes["in_flight_operation"] = safe.get(
                    "operation_id",
                    cast(OperationContext, _operation.get()).id
                    if _operation.get()
                    else "",
                )
            if event in {"model.call.finished", "send.attempt.finished"}:
                changes["in_flight_operation"] = ""
            if event == "decision_skipped" and trace.execution_outcome != "failed":
                changes.update(
                    execution_outcome="skipped",
                    reason=safe.get("reason", "unavailable"),
                )
            if event in {"task_error", "model.call.failed", "model.result.empty"}:
                changes.update(
                    execution_outcome="failed",
                    failed_stage=trace.failed_stage or event,
                    reason="exception",
                )
            if event == "generation_empty" and trace.execution_outcome == "completed":
                changes.update(
                    execution_outcome="failed",
                    failed_stage=event,
                    reason=safe.get("reason", "no_response"),
                )
            if event == "send_blocked":
                changes.update(execution_outcome="skipped", reason="decorating_hook")
            if (
                event == "send_result"
                and safe.get("outcome") == "failed_or_blocked"
                and trace.execution_outcome == "completed"
            ):
                changes.update(
                    execution_outcome="failed",
                    failed_stage="send_result",
                    reason="explicit_failure",
                )
            if (
                event == "send_result"
                and not trace.delivery.receipts
                and not trace.delivery.pending
                and safe.get("outcome") == "flow_returned_true"
            ):
                # A boolean from orchestration alone is not an API receipt.
                if not trace.delivery.closed:
                    trace.delivery.receipts["flow_return_only"] = "delivery_unknown"
            if event == "history_verified":
                changes["history_outcome"] = "verified"
            if event == "history_unverified":
                changes["history_outcome"] = "unverified"
            if event == "history.failed":
                changes["history_outcome"] = "failed"
            if event in {"counter_updated", "counter_reset"}:
                changes["counter_outcome"] = "changed"
            if event == "scheduled":
                changes["schedule_outcome"] = "created"
            if event in {
                "context.prepared",
                "generation_finished",
                "send.completed",
                "history_verified",
                "counter_updated",
                "scheduled",
            }:
                changes["last_completed_stage"] = event
            run_update(**changes)
            safe["stage"] = cast(RunContext, _trace.get()).stage
        operation = _operation.get()
        if operation and event.startswith("send."):
            if event == "send.prepared" and isinstance(safe.get("segment_count"), int):
                operation = replace(operation, count=safe["segment_count"])
                _operation.set(operation)
                operation.delivery.prepare(operation)
            safe.update(
                operation_id=operation.id,
                attempt_no=operation.attempt,
                segment_index=operation.segment,
                segment_count=operation.count,
                segment_id=f"{operation.id}:{operation.segment}",
                parent_operation_id=operation.parent_id or None,
                after_run_terminal=operation.delivery.closed,
            )
            safe["span_id"] = operation.id
            if event == "send.attempt.started":
                operation.delivery.observe(operation, None)
            if "receipt_state" in safe:
                operation.delivery.observe(operation, safe["receipt_state"])
        if exception:
            prior = (
                dict(cast(RunContext, _trace.get()).errors).get(id(exception))
                if trace and trace.center is self
                else None
            )
            safe["error_id"] = prior or uuid.uuid4().hex
            safe["error_code"] = error_code(exception)
            if not prior:
                safe["exception"] = safe_exception(exception)
                if trace and trace.center is self:
                    run_update(
                        error_id=safe["error_id"],
                        errors=(
                            cast(RunContext, _trace.get()).errors
                            + ((id(exception), safe["error_id"]),)
                        )[-16:],
                    )
        category, default_summary = EVENTS[event]
        if trace and trace.center is self:
            safe.setdefault("span_id", safe.get("operation_id", trace.id))
            safe["parent_span_id"] = (
                operation.parent_id
                if operation and operation.parent_id and event.startswith("send.")
                else trace.id
                if safe["span_id"] != trace.id
                else None
            )
        safe["redaction_policy_version"] = 2
        serialized = json.dumps(safe, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode()) > 15000:
            safe.pop("exception", None)
            safe["incomplete"] = True
            safe["truncated_fields"] = ["exception"]
            serialized = json.dumps(safe, ensure_ascii=False, allow_nan=False)
        row = (
            time.time(),
            LEVELS[level],
            level,
            category,
            event,
            self.alias("session", session_id) if session_id else "",
            str(trace_id)[:64]
            if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", str(trace_id))
            else "",
            summary[:400] if summary in _trusted_summaries else default_summary,
            serialized,
            uuid.uuid4().hex,
            self.boot_id,
        )
        if self._ready.is_set() and self._thread and not self._thread.is_alive():
            self._loss(row, "writer_unavailable")
            return
        accepted, evicted = self._queue.put_event(row)
        for victim in evicted:
            self._loss(victim, "priority_eviction")
        if not accepted:
            self._loss(row, "queue_full")

    def alias(self, kind, value):
        prefix = {"session": "S", "provider": "P", "model": "M"}[kind]
        text = str(value)
        if re.fullmatch(prefix + r"-[a-f0-9]{12}", text):
            return text
        return (
            prefix
            + "-"
            + hmac.new(
                self._alias_key, (kind + ":" + text).encode(), hashlib.sha256
            ).hexdigest()[:12]
        )

    def _loss(self, row, reason):
        with self._health_lock:
            self.dropped += 1
            self.dropped_total += 1
            key = row[2] + ":" + reason
            self._drop_counts[key] = self._drop_counts.get(key, 0) + 1
            now = time.time()
            self.loss_window = (
                [self.loss_window[0], now] if self.loss_window else [now, now]
            )
            if row[6]:
                if len(self._run_loss) >= 1000:
                    self._run_loss.pop(next(iter(self._run_loss)))
                self._run_loss[row[6]] = self._run_loss.get(row[6], 0) + 1

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=2)
        conn.execute("PRAGMA busy_timeout=2000")
        return conn

    def _prune(self, conn):
        before = conn.total_changes
        conn.execute(
            "DELETE FROM logs WHERE ts < ?",
            (time.time() - self.retention_days * 86400,),
        )
        conn.execute(
            "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT ?)",
            (self.max_entries,),
        )
        self.pruned_total += conn.total_changes - before

    def _worker(self):
        conn = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation sets restrictive permissions without altering the plugin data directory.
            if not self.path.exists():
                import os

                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            conn = self._connect()
            conn.execute("PRAGMA journal_mode=DELETE")
            schema = conn.execute("PRAGMA user_version").fetchone()[0]
            if schema > 2:
                raise ValueError("Unsupported journal schema")
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            conn.execute(f"PRAGMA max_page_count={32 * 1024 * 1024 // page_size}")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, severity INTEGER NOT NULL, level TEXT NOT NULL, category TEXT NOT NULL, event TEXT NOT NULL, session_id TEXT NOT NULL, trace_id TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS logs_time ON logs(ts)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS logs_session ON logs(session_id, id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS logs_trace ON logs(trace_id, id)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(logs)")}
            for name, definition in {
                "schema_version": "INTEGER NOT NULL DEFAULT 1",
                "event_id": "TEXT",
                "observed_at": "REAL",
                "boot_id": "TEXT",
            }.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE logs ADD COLUMN {name} {definition}")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS logs_terminal ON logs(trace_id) WHERE event='run.completed'"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS logs_event ON logs(event, id)")
            conn.execute("PRAGMA user_version=2")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS journal_health (id INTEGER PRIMARY KEY CHECK(id=1), details TEXT NOT NULL)"
            )
            previous_health = conn.execute(
                "SELECT details FROM journal_health WHERE id=1"
            ).fetchone()
            if previous_health:
                health = json.loads(previous_health[0])
                self.dropped_total += health.get("dropped_total", 0)
                self.pruned_total += health.get("pruned_total", 0)
                self.loss_window = health.get("loss_window")
            # A missing terminal is evidence of an interruption or journal loss,
            # never proof that the old task succeeded or was cancelled.
            pending = conn.execute(
                "SELECT s.trace_id, s.session_id FROM logs s WHERE s.event='task_started' AND s.schema_version=2 AND NOT EXISTS (SELECT 1 FROM logs f WHERE f.trace_id=s.trace_id AND f.event='run.completed') GROUP BY s.trace_id, s.session_id"
            ).fetchall()
            for run_id, session_ref in pending:
                conn.execute(
                    "INSERT OR IGNORE INTO logs(ts,severity,level,category,event,session_id,trace_id,summary,details,schema_version,event_id,observed_at,boot_id) VALUES(?,?,?,?,?,?,?,?,?,2,?,?,?)",
                    (
                        time.time(),
                        30,
                        "WARNING",
                        "state",
                        "run.completed",
                        session_ref,
                        run_id,
                        EVENTS["run.completed"][1],
                        json.dumps(
                            {
                                "execution_outcome": "interrupted_or_unknown",
                                "delivery_outcome": "delivery_unknown",
                                "incomplete": True,
                                "completion_evidence": "phase_events",
                            }
                        ),
                        uuid.uuid4().hex,
                        time.time(),
                        self.boot_id,
                    ),
                )
            self._prune(conn)
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - storage failure must not stop chat
            self.storage_error = (
                "unsupported_schema" if isinstance(exc, ValueError) else error_code(exc)
            )
        finally:
            self._ready.set()
        if conn is None or self.storage_error:
            if conn:
                conn.close()
            return
        last_prune = time.monotonic()
        while not self._stop.is_set() or not self._queue.empty():
            batch = []
            try:
                batch.append(self._queue.get(timeout=0.25))
            except queue.Empty:
                pass
            while len(batch) < 50:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            try:
                if batch:
                    # Free oldest rows before insert so a full database can recover.
                    conn.execute(
                        "DELETE FROM logs WHERE id IN (SELECT id FROM logs ORDER BY id LIMIT max(0, (SELECT count(*) FROM logs) + ? - ?))",
                        (len(batch), self.max_entries),
                    )
                    self.pruned_total += conn.execute("SELECT changes()").fetchone()[0]
                    conn.executemany(
                        "INSERT OR IGNORE INTO logs(ts,severity,level,category,event,session_id,trace_id,summary,details,event_id,boot_id,schema_version,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,2,?)",
                        [row + (time.time(),) for row in batch],
                    )
                if batch or time.monotonic() - last_prune > 30:
                    self._prune(conn)
                    self._persist_health(conn)
                    conn.commit()
                    self.last_commit_at = time.time()
                    last_prune = time.monotonic()
                    self.storage_error = None
            except Exception as exc:  # noqa: BLE001 - isolate writer failures
                conn.rollback()
                self.storage_error = error_code(exc)
                for row in batch:
                    self._loss(row, "storage_failure")
                if (
                    isinstance(exc, sqlite3.OperationalError)
                    and "full" in str(exc).lower()
                ):
                    try:
                        conn.execute(
                            "DELETE FROM logs WHERE id IN (SELECT id FROM logs ORDER BY id LIMIT 100)"
                        )
                        conn.commit()
                    except sqlite3.Error:
                        conn.rollback()
            finally:
                for _ in batch:
                    self._queue.task_done()
        try:
            self._persist_health(conn)
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
        conn.close()

    def _persist_health(self, conn):
        with self._health_lock:
            health = {
                "dropped_total": self.dropped_total,
                "pruned_total": self.pruned_total,
                "loss_window": self.loss_window,
            }
        conn.execute(
            "INSERT OR REPLACE INTO journal_health(id, details) VALUES(1, ?)",
            (json.dumps(health),),
        )

    def status(self):
        return {
            "enabled": self.enabled,
            "debug_enabled": self.debug_enabled,
            "retention_days": self.retention_days,
            "max_entries": self.max_entries,
            "dropped": self.dropped,
            "queued": self._queue.qsize(),
            "storage_error": self.storage_error,
            "ready": self._ready.is_set(),
            "schema_version": 2,
            "writer_alive": bool(self._thread and self._thread.is_alive()),
            "queue_bytes": self._queue.bytes,
            "queue_byte_limit": self._queue.max_bytes,
            "last_commit_at": self.last_commit_at,
            "dropped_total": self.dropped_total,
            "dropped_by_level_reason": dict(self._drop_counts),
            "loss_window": self.loss_window,
            "pruned_total": self.pruned_total,
            "drain_timed_out": self.drain_timed_out,
            "capture_mode": "DEBUG+" if self.debug_enabled else "INFO+",
            "boot_id": self.boot_id,
            "privacy": "不保存正文、提示词、动态日志参数、异常消息和局部变量",
        }

    def query(
        self,
        *,
        min_level="INFO",
        category="",
        session_id="",
        trace_id="",
        since=None,
        until=None,
        before_id=None,
        limit=50,
        snapshot_max_id=None,
        event_name="",
        outcome="",
        reason_code="",
        provider_ref="",
        operation_id="",
        error_category="",
    ):
        for timestamp in (since, until):
            if timestamp is not None and (
                not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp)
            ):
                raise ValueError("时间筛选必须为有限数值")
        if min_level not in LEVELS:
            raise ValueError("无效日志等级")
        if (
            outcome
            and outcome
            not in STRING_VALUES["outcome"]
            | STRING_VALUES["execution_outcome"]
            | STRING_VALUES["delivery_outcome"]
        ):
            raise ValueError("无效结果筛选")
        if reason_code and reason_code not in STRING_VALUES["reason"]:
            raise ValueError("无效原因筛选")
        if error_category and error_category not in STRING_VALUES["error_code"]:
            raise ValueError("无效异常分类")
        if provider_ref and not re.fullmatch(r"P-[a-f0-9]{12}", provider_ref):
            raise ValueError("无效提供者别名")
        if operation_id and not re.fullmatch(r"[a-f0-9]{32}", operation_id):
            raise ValueError("无效操作标识")
        if category and category not in {value[0] for value in EVENTS.values()}:
            raise ValueError("无效事件类型")
        if since is not None and until is not None and since > until:
            raise ValueError("开始时间不能晚于结束时间")
        if (
            not self._ready.wait(2)
            or not self.enabled
            or not self.path.exists()
            or self.storage_error
        ):
            return {"items": [], "next_cursor": None, "meta": self.status()}
        terms, args = (
            ["severity >= ?", "ts >= ?"],
            [LEVELS[min_level], time.time() - self.retention_days * 86400],
        )
        for key, value in (
            ("category", category),
            ("session_id", session_id),
            ("trace_id", trace_id),
        ):
            if value:
                if key == "session_id":
                    terms.append("session_id IN (?, ?)")
                    args.extend([value, self.alias("session", value)])
                else:
                    terms.append(key + " = ?")
                    args.append(value)
        if event_name:
            if event_name not in EVENTS:
                raise ValueError("无效事件名")
            terms.append("event = ?")
            args.append(event_name)
        for key, value in (
            ("execution_outcome", outcome),
            ("reason", reason_code),
            ("provider_ref", provider_ref),
            ("operation_id", operation_id),
            ("error_code", error_category),
        ):
            if value:
                terms.append(f"json_extract(details, '$.{key}') = ?")
                args.append(value)
        for key, op, value in (
            ("ts", ">=", since),
            ("ts", "<=", until),
            ("id", "<", before_id),
        ):
            if value is not None:
                terms.append(key + " " + op + " ?")
                args.append(value)
        limit = _bounded_int(limit, 50, 1, 100)
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            snapshot_max_id = (
                snapshot_max_id
                if snapshot_max_id is not None
                else conn.execute("SELECT COALESCE(max(id),0) FROM logs").fetchone()[0]
            )
            terms.append("id <= ?")
            args.append(snapshot_max_id)
            rows = conn.execute(
                "SELECT * FROM logs WHERE "
                + " AND ".join(terms)
                + " ORDER BY id DESC LIMIT ?",
                args + [limit + 1],
            ).fetchall()
            oldest_available_at = conn.execute("SELECT min(ts) FROM logs").fetchone()[0]
        finally:
            conn.close()
        items = []
        for row in rows[:limit]:
            item = dict(row)
            item["details"] = json.loads(item["details"])
            item["session_id"] = (
                self.alias("session", item["session_id"]) if item["session_id"] else ""
            )
            item["session_ref"] = item["session_id"]
            item["run_id"] = item["trace_id"] or None
            item["ingest_seq"] = item["id"]
            item["occurred_at"] = item["ts"]
            item["event_name"] = item["event"]
            item["reason_code"] = item["details"].get("reason")
            item["outcome"] = item["details"].get(
                "execution_outcome", item["details"].get("outcome")
            )
            item["origin"] = "proactive_task" if item["trace_id"] else "plugin_runtime"
            item["details"]["incomplete"] = bool(
                item["details"].get("incomplete")
                or item["schema_version"] < 2
                or self._run_loss.get(item["trace_id"])
            )
            items.append(item)
        return {
            "items": items,
            "next_cursor": items[-1]["id"] if len(rows) > limit else None,
            "meta": {
                **self.status(),
                "oldest_available_at": oldest_available_at,
                "main_db_bytes": self.path.stat().st_size,
                "journal_bytes": Path(str(self.path) + "-journal").stat().st_size
                if Path(str(self.path) + "-journal").exists()
                else 0,
            },
            "snapshot_max_id": snapshot_max_id,
            "retained_range": {
                "oldest_id": min((item["id"] for item in items), default=None),
                "newest_id": max((item["id"] for item in items), default=None),
            },
        }

    def collect(self, *, cap=1000, **filters):
        """Collect a fixed snapshot, with explicit retention and truncation evidence."""
        items: list[dict[str, Any]] = []
        cursor = None
        snapshot = filters.pop("snapshot_max_id", None)
        initial_pruned = self.pruned_total
        while len(items) < cap:
            result = self.query(
                **filters,
                snapshot_max_id=snapshot,
                before_id=cursor,
                limit=min(100, cap - len(items)),
            )
            snapshot = result.get("snapshot_max_id", snapshot)
            items.extend(result["items"])
            cursor = result["next_cursor"]
            if cursor is None:
                break
        return {
            "items": items,
            "next_cursor": cursor,
            "snapshot_max_id": snapshot,
            "applied_filters": {
                key: self.alias("session", value)
                if key == "session_id" and value
                else value
                for key, value in filters.items()
            },
            "captured_range": {
                "oldest_id": min((item["id"] for item in items), default=None),
                "newest_id": max((item["id"] for item in items), default=None),
            },
            "truncated": cursor is not None,
            "truncation_reason": "entry_limit" if cursor is not None else None,
            "retention_changed": self.pruned_total != initial_pruned,
            "meta": result["meta"],
        }

    def export(self, **filters):
        result = self.collect(cap=1000, **filters)
        aliases: dict[str, str] = {}
        for item in result["items"]:
            session = item["session_id"]
            if session:
                aliases.setdefault(session, "S-" + uuid.uuid4().hex[:12])
                item["session_ref"] = item["session_id"] = aliases[session]
        if result["applied_filters"].get("session_id"):
            session = result["applied_filters"]["session_id"]
            result["applied_filters"]["session_id"] = aliases.get(
                session, "filtered_session"
            )
        result["privacy"] = "无会话映射、正文、凭据或原始异常消息"
        return result

    def close(self):
        if self._closed:
            return
        self.record("stopped")
        self._closed = True
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self.drain_timed_out = self._thread.is_alive()
            if not self.drain_timed_out:
                with _writers_lock:
                    if _writers.get(self.path.resolve()) is self:
                        _writers.pop(self.path.resolve(), None)


def emit(plugin, event, level="INFO", session_id="", exception=None, **details):
    center = getattr(plugin, "log_center", None)
    if center:
        current_frame = inspect.currentframe()
        frame = current_frame.f_back if current_frame else None
        try:
            if frame:
                details.setdefault("source", Path(frame.f_code.co_filename).name)
                details.setdefault("line", frame.f_lineno)
                details.setdefault("function", frame.f_code.co_name)
            center.record(
                event,
                level,
                session_id=session_id,
                details=details,
                exception=exception,
            )
        except Exception:  # noqa: BLE001 - telemetry must never interrupt chat
            center.dropped += 1
        finally:
            del frame


def traced_task(func):
    @functools.wraps(func)
    async def wrapped(self, session_id, *args, **kwargs):
        center = getattr(self, "log_center", None)
        if not center:
            return await func(self, session_id, *args, **kwargs)
        session = self._normalize_session_id(session_id)
        run_id = uuid.uuid4().hex
        token = _trace.set(
            RunContext(center, session, run_id, dropped_before=center.dropped)
        )
        operation_token = _operation.set(None)
        center._active_runs[run_id] = (session, asyncio.current_task())
        start = time.monotonic()
        emit(
            self,
            "task_started",
            trigger="manual"
            if session in getattr(self, "manual_trigger_sessions", set())
            else "scheduled",
            previous_run_id=center._previous_runs.get(session),
        )
        try:
            return await func(self, session_id, *args, **kwargs)
        except Exception as exc:
            emit(self, "task_error", "ERROR", exception=exc)
            raise
        except asyncio.CancelledError:
            run_update(execution_outcome="cancelled", reason="cancelled")
            emit(
                self,
                "cancel.observed",
                cancel_source=center._cancel_sources.get(run_id, "unknown"),
                in_flight_operation=cast(RunContext, _trace.get()).in_flight_operation
                or None,
            )
            emit(self, "task_cancelled")
            raise
        finally:
            run = cast(RunContext, _trace.get())
            delivery = run.delivery.summary(close=True)
            level = (
                "ERROR"
                if run.execution_outcome == "failed"
                else "WARNING"
                if delivery["delivery_outcome"]
                in {"partial_success", "delivery_unknown", "explicit_failure"}
                else "INFO"
            )
            center.record(
                "run.completed",
                level,
                details={
                    "execution_outcome": run.execution_outcome,
                    "reason": run.reason,
                    **delivery,
                    "history_outcome": run.history_outcome,
                    "counter_outcome": run.counter_outcome,
                    "schedule_outcome": run.schedule_outcome,
                    "failed_stage": run.failed_stage,
                    "last_completed_stage": run.last_completed_stage,
                    "error_id": run.error_id,
                    "duration_ms": round((time.monotonic() - start) * 1000),
                    "dropped_before": run.dropped_before,
                    "dropped_after": center.dropped,
                    "incomplete": bool(
                        center._run_loss.get(run_id) or delivery["pending_attempts"]
                    ),
                    "completion_evidence": "phase_events",
                },
            )
            center._previous_runs[session] = run_id
            if len(center._previous_runs) > 1000:
                center._previous_runs.pop(next(iter(center._previous_runs)))
            center._active_runs.pop(run_id, None)
            center._cancel_sources.pop(run_id, None)
            _operation.reset(operation_token)
            _trace.reset(token)

    return wrapped


def cancel_requested(plugin, session_id="", source="unknown"):
    center = getattr(plugin, "log_center", None)
    if not center:
        return
    for run_id, (session, task) in list(center._active_runs.items()):
        if not session_id or session == session_id:
            center._cancel_sources[run_id] = source
            center.record(
                "cancel.requested",
                session_id=session,
                trace_id=run_id,
                details={"cancel_source": source},
            )
