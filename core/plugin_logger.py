"""Plugin-only log facade; Web and AstrBot console share the safe template.

The local journal uses source-code templates, NEVER the evaluated log message.
Dynamic f-string values, %-format arguments, exception text and traceback source
lines are intentionally omitted, including at DEBUG level.
"""

from __future__ import annotations

import ast
import functools
import inspect
import sys
from pathlib import Path

from astrbot.api import logger as astrbot_logger
from .log_center import _trusted_summaries, safe_exception

_center = None


def bind_log_center(center):
    global _center
    _center = center
    _warm_templates()


def unbind_log_center(center):
    global _center
    if _center is center:
        _center = None


@functools.lru_cache(maxsize=128)
def _templates(filename):
    result = {}
    try:
        # Only files shipping inside this plugin can supply log templates.
        path = Path(filename).resolve()
        path.relative_to(Path(__file__).resolve().parent.parent)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not node.args
                or not isinstance(node.func, ast.Attribute)
            ):
                continue
            if (
                not isinstance(node.func.value, ast.Name)
                or node.func.value.id != "logger"
            ):
                continue
            value = node.args[0]
            text = None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                text = value.value
            elif isinstance(value, ast.JoinedStr):
                text = "".join(
                    v.value
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    else "[已隐藏]"
                    for v in value.values
                )
            if text:
                for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    result[line] = text[:400]
    except (
        OSError,
        SyntaxError,
        ValueError,
    ):  # Do not recursively log template errors.
        pass
    return result


def _warm_templates():
    for path in Path(__file__).resolve().parent.parent.rglob("*.py"):
        _trusted_summaries.update(_templates(str(path.resolve())).values())


class PluginLogger:
    def __getattr__(self, name):
        target = getattr(astrbot_logger, name)
        levels = {
            "debug": "DEBUG",
            "info": "INFO",
            "warning": "WARNING",
            "warn": "WARNING",
            "error": "ERROR",
            "exception": "ERROR",
            "critical": "CRITICAL",
            "fatal": "CRITICAL",
        }
        if name not in levels:
            return target

        def log(*args, **kwargs):
            center = _center
            frame = inspect.currentframe().f_back
            summary = None
            try:
                filename, line = frame.f_code.co_filename, frame.f_lineno
                summary = _templates(filename).get(line)
                if (
                    center
                    and center.enabled
                    and (name != "debug" or center.debug_enabled)
                ):
                    center.record(
                        "runtime",
                        levels[name],
                        summary=summary,
                        details={
                            "source": Path(filename).name,
                            "line": line,
                            "function": frame.f_code.co_name,
                        },
                        exception=sys.exc_info()[1]
                        if levels[name] in {"WARNING", "ERROR", "CRITICAL"}
                        else None,
                    )
            except Exception:  # noqa: BLE001 - journal cannot break original logging
                if center:
                    center.dropped += 1
            finally:
                del frame
            # Never forward evaluated f-strings, arguments, exc_info, stack_info,
            # exception messages or traceback source lines to AstrBot console.
            text = summary or "插件运行日志（动态内容已隐藏）"
            exc = sys.exc_info()[1]
            if exc and levels[name] in {"WARNING", "ERROR", "CRITICAL"}:
                diagnostic = safe_exception(exc)
                text += " | " + diagnostic["type"] + " | " + diagnostic["category"]
            return getattr(astrbot_logger, "error" if name == "exception" else name)(
                text
            )

        return log


logger = PluginLogger()
_warm_templates()
