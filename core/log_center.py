"""Bounded, local-only event journal. No message/prompt/exception text is stored."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import math
import queue
import sqlite3
import threading
import time
import uuid
from pathlib import Path

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
_trace = contextvars.ContextVar("proactive_log_trace", default=None)


def _bounded_int(value, default, low, high):
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def safe_exception(exc):
    """Keep true frame locations, omit locals, source lines and exception messages."""
    if not isinstance(exc, BaseException):
        return None
    exceptions = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen and len(exceptions) < 5:
        seen.add(id(current))
        frames = []
        tb = current.__traceback__
        while tb is not None and len(frames) < 64:
            frames.append(
                {
                    "file": Path(tb.tb_frame.f_code.co_filename).name[:120],
                    "line": tb.tb_lineno,
                    "function": tb.tb_frame.f_code.co_name[:120],
                }
            )
            tb = tb.tb_next
        item = {
            "type": type(current).__name__[:120],
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
        self._queue = queue.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._closed = False
        self.dropped = 0
        self.storage_error = None
        self._thread = None
        if self.enabled:
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
        if trace and trace["center"] is self:
            session_id = session_id or trace["session"]
            trace_id = trace_id or trace["id"]
            if event != "runtime":
                trace["stage"] = event
        safe = {}
        for key, value in (details or {}).items():
            if key in DETAIL_KEYS and isinstance(value, (str, bool, int, float)):
                safe[key] = (
                    None
                    if isinstance(value, float) and not math.isfinite(value)
                    else value[:256]
                    if isinstance(value, str)
                    else value
                )
        if trace and trace["center"] is self:
            safe["stage"] = trace.get("stage", "task_started")
            if event == "send_fallback":
                trace["fallback_index"] = trace.get("fallback_index", 0) + 1
                safe["fallback_index"] = trace["fallback_index"]
        if exception:
            safe["exception"] = safe_exception(exception)
        category, default_summary = EVENTS[event]
        row = (
            time.time(),
            LEVELS[level],
            level,
            category,
            event,
            str(session_id)[:256],
            str(trace_id)[:64],
            (summary or default_summary)[:400],
            json.dumps(safe, ensure_ascii=False, allow_nan=False),
        )
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            self.dropped += 1

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=2)
        conn.execute("PRAGMA busy_timeout=2000")
        return conn

    def _prune(self, conn):
        conn.execute(
            "DELETE FROM logs WHERE ts < ?",
            (time.time() - self.retention_days * 86400,),
        )
        conn.execute(
            "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT ?)",
            (self.max_entries,),
        )

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
            conn.execute("PRAGMA max_page_count=8192")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, severity INTEGER NOT NULL, level TEXT NOT NULL, category TEXT NOT NULL, event TEXT NOT NULL, session_id TEXT NOT NULL, trace_id TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS logs_time ON logs(ts)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS logs_session ON logs(session_id, id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS logs_trace ON logs(trace_id, id)")
            self._prune(conn)
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - storage failure must not stop chat
            self.storage_error = type(exc).__name__
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
                    conn.executemany(
                        "INSERT INTO logs(ts,severity,level,category,event,session_id,trace_id,summary,details) VALUES(?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                if batch or time.monotonic() - last_prune > 30:
                    self._prune(conn)
                    conn.commit()
                    last_prune = time.monotonic()
                    self.storage_error = None
            except Exception as exc:  # noqa: BLE001 - isolate writer failures
                conn.rollback()
                self.storage_error = type(exc).__name__
                self.dropped += len(batch)
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
        conn.close()

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
    ):
        for timestamp in (since, until):
            if timestamp is not None and (
                not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp)
            ):
                raise ValueError("时间筛选必须为有限数值")
        if min_level not in LEVELS:
            raise ValueError("无效日志等级")
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
                terms.append(key + " = ?")
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
            rows = conn.execute(
                "SELECT * FROM logs WHERE "
                + " AND ".join(terms)
                + " ORDER BY id DESC LIMIT ?",
                args + [limit + 1],
            ).fetchall()
        finally:
            conn.close()
        items = []
        for row in rows[:limit]:
            item = dict(row)
            item["details"] = json.loads(item["details"])
            items.append(item)
        return {
            "items": items,
            "next_cursor": items[-1]["id"] if len(rows) > limit else None,
            "meta": self.status(),
        }

    def close(self):
        if self._closed:
            return
        self.record("stopped")
        self._closed = True
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def emit(plugin, event, level="INFO", session_id="", exception=None, **details):
    center = getattr(plugin, "log_center", None)
    if center:
        frame = inspect.currentframe().f_back
        try:
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
        finally:
            del frame


def traced_task(func):
    @functools.wraps(func)
    async def wrapped(self, session_id, *args, **kwargs):
        center = getattr(self, "log_center", None)
        if not center:
            return await func(self, session_id, *args, **kwargs)
        session = self._normalize_session_id(session_id)
        token = _trace.set(
            {"center": center, "session": session, "id": uuid.uuid4().hex}
        )
        start = time.monotonic()
        emit(
            self,
            "task_started",
            trigger="manual"
            if session in getattr(self, "manual_trigger_sessions", set())
            else "scheduled",
        )
        try:
            return await func(self, session_id, *args, **kwargs)
        except Exception as exc:
            emit(self, "task_error", "ERROR", exception=exc)
            raise
        except asyncio.CancelledError:
            emit(self, "task_cancelled", "WARNING")
            raise
        finally:
            emit(
                self,
                "task_finished",
                duration_ms=round((time.monotonic() - start) * 1000),
            )
            _trace.reset(token)

    return wrapped
