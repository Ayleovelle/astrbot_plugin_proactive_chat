"""AstrBot Dashboard Pages 适配层。

将插件现有的 WebAdminServer API 通过 AstrBot 内置 web 服务器的
register_web_api 机制暴露，使管理端可以在 AstrBot Dashboard 的
插件页面中直接访问，无需依赖独立端口。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

if TYPE_CHECKING:
    from ..main import ProactiveChatPlugin

PLUGIN_NAME = "astrbot_plugin_proactive_chat"


class PagesAdapter:
    """将 WebAdminServer 的 API 桥接到 AstrBot register_web_api。"""

    def __init__(self, plugin: "ProactiveChatPlugin") -> None:
        self._plugin = plugin
        self._pages_dir = Path(__file__).resolve().parent.parent / "pages"

    def register(self, context: Any) -> None:
        if not hasattr(context, "register_web_api"):
            logger.debug("[主动消息] AstrBot 版本不支持 register_web_api，跳过 Pages 注册。")
            return

        P = PLUGIN_NAME
        routes: list[tuple[str, Any, list[str], str]] = [
            (f"/{P}/dashboard", self._page_handler, ["GET"], "主动消息 Dashboard 页面"),
            (f"/{P}/api/status", self._status_handler, ["GET"], "主动消息状态 API"),
            (f"/{P}/api/config", self._config_get_handler, ["GET"], "主动消息配置读取"),
            (f"/{P}/api/config", self._config_post_handler, ["POST"], "主动消息配置写入"),
            (f"/{P}/api/config-schema", self._config_schema_handler, ["GET"], "主动消息配置 Schema"),
            (f"/{P}/api/jobs", self._jobs_handler, ["GET"], "主动消息任务列表"),
            (f"/{P}/api/jobs/trigger", self._job_trigger_handler, ["POST"], "主动消息立即触发"),
            (f"/{P}/api/jobs/cancel", self._job_cancel_handler, ["POST"], "主动消息取消任务"),
            (f"/{P}/api/jobs/reschedule", self._job_reschedule_handler, ["POST"], "主动消息重新调度"),
            (f"/{P}/api/notifications", self._notifications_handler, ["GET"], "主动消息通知列表"),
            (f"/{P}/api/notifications/read", self._notification_read_handler, ["POST"], "主动消息标记已读"),
            (f"/{P}/api/notifications/read-all", self._notification_read_all_handler, ["POST"], "主动消息全部已读"),
            (f"/{P}/api/notifications/refresh", self._notification_refresh_handler, ["POST"], "主动消息刷新通知"),
            (f"/{P}/api/session-config/sessions", self._sessions_list_handler, ["GET"], "主动消息会话列表"),
            (f"/{P}/api/session-config", self._session_config_get_handler, ["GET"], "主动消息会话配置读取"),
            (f"/{P}/api/session-config", self._session_config_post_handler, ["POST"], "主动消息会话配置写入"),
            (f"/{P}/api/session-config/reset", self._session_config_reset_handler, ["POST"], "主动消息会话配置重置"),
            (f"/{P}/api/markdown-files", self._markdown_files_handler, ["GET"], "主动消息文档列表"),
            (f"/{P}/api/markdown-file", self._markdown_file_handler, ["GET"], "主动消息文档内容"),
            (f"/{P}/logo.png", self._logo_handler, ["GET"], "主动消息插件 Logo"),
        ]

        for path, handler, methods, desc in routes:
            try:
                context.register_web_api(path, handler, methods, desc)
            except Exception as e:
                logger.warning(f"[主动消息] Pages 路由注册失败 ({path}): {e}")

        logger.info("[主动消息] AstrBot Dashboard Pages 路由已注册。")

    @property
    def _server(self):
        return self._plugin.web_admin_server

    async def _page_handler(self, **kwargs) -> Any:
        from quart import Response

        page_path = self._pages_dir / "index.html"
        if not page_path.exists():
            return Response("Dashboard page not found", status=404)
        html = page_path.read_text(encoding="utf-8")
        return Response(html, content_type="text/html; charset=utf-8")

    async def _logo_handler(self, **kwargs) -> Any:
        from quart import Response

        logo_path = Path(__file__).resolve().parent.parent / "logo.png"
        if not logo_path.exists():
            return Response("Not found", status=404)
        data = logo_path.read_bytes()
        return Response(data, content_type="image/png")

    async def _status_handler(self, **kwargs) -> Any:
        if not self._server:
            return {"error": "Web 管理端未初始化"}
        return self._server._build_status_payload()

    async def _config_get_handler(self, **kwargs) -> Any:
        config = self._plugin.config
        web_admin = {
            k: v for k, v in config.get("web_admin", {}).items() if k != "password"
        }
        return {
            "friend_settings": dict(config.get("friend_settings", {})),
            "group_settings": dict(config.get("group_settings", {})),
            "web_admin": web_admin,
            "notification_settings": dict(config.get("notification_settings", {})),
        }

    async def _config_post_handler(self, **kwargs) -> Any:
        from quart import request

        payload = await request.get_json()
        if not payload:
            return {"error": "请求体为空"}

        allowed_keys = {"friend_settings", "group_settings", "web_admin"}
        config = self._plugin.config
        for key in allowed_keys:
            if key not in payload:
                continue
            if key == "web_admin":
                old = dict(config.get("web_admin", {}))
                old.update(payload.get("web_admin", {}))
                if "password" in payload.get("web_admin", {}):
                    old["password"] = payload["web_admin"]["password"]
                config["web_admin"] = old
            else:
                config[key] = payload[key]

        if self._server:
            self._server._save_plugin_config()
        return {"ok": True}

    async def _config_schema_handler(self, **kwargs) -> Any:
        schema_path = Path(__file__).resolve().parent.parent / "_conf_schema.json"
        if schema_path.exists():
            try:
                return json.loads(schema_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    async def _jobs_handler(self, **kwargs) -> Any:
        if not self._server:
            return {"jobs": []}
        return {"jobs": self._server._collect_jobs()}

    async def _job_trigger_handler(self, **kwargs) -> Any:
        import asyncio
        from quart import request

        payload = await request.get_json()
        session = payload.get("session", "") if payload else ""
        if not session:
            return {"error": "缺少 session 参数"}

        normalized = self._plugin._normalize_session_id(session)
        if normalized in self._plugin.manual_trigger_sessions:
            return {"ok": False, "message": "该任务正在执行中"}

        self._plugin.manual_trigger_sessions.add(normalized)
        asyncio.create_task(self._plugin.check_and_chat(normalized))
        return {"ok": True, "session": normalized, "message": "已开始立即触发"}

    async def _job_cancel_handler(self, **kwargs) -> Any:
        from quart import request

        payload = await request.get_json()
        session = payload.get("session", "") if payload else ""
        if not session:
            return {"error": "缺少 session 参数"}

        normalized = self._plugin._normalize_session_id(session)
        removed = False
        try:
            self._plugin.scheduler.remove_job(normalized)
            removed = True
        except Exception:
            pass

        async with self._plugin.data_lock:
            if normalized in self._plugin.session_data:
                self._plugin.session_data[normalized].pop("next_trigger_time", None)
                await self._plugin._save_data_internal()

        return {"ok": True, "session": normalized, "removed": removed}

    async def _job_reschedule_handler(self, **kwargs) -> Any:
        from quart import request

        payload = await request.get_json()
        session = payload.get("session", "") if payload else ""
        if not session:
            return {"error": "缺少 session 参数"}

        normalized = self._plugin._normalize_session_id(session)
        session_config = self._plugin._get_session_config(normalized)
        if not session_config or not session_config.get("enable", False):
            return {"ok": False, "error": "会话未启用或配置不存在"}

        await self._plugin._schedule_next_chat_and_save(normalized, reset_counter=False)
        return {"ok": True, "session": normalized}

    async def _notifications_handler(self, **kwargs) -> Any:
        if not self._server:
            return {"items": [], "meta": {"unread_count": 0}}
        return await self._server._build_notification_payload()

    async def _notification_read_handler(self, **kwargs) -> Any:
        from quart import request

        payload = await request.get_json()
        notification_id = payload.get("id") if payload else None
        if notification_id is None:
            return {"error": "缺少 id 参数"}

        nc = getattr(self._plugin, "notification_center", None)
        if not nc:
            return {"error": "通知系统不可用"}
        return await nc.mark_as_read(int(notification_id))

    async def _notification_read_all_handler(self, **kwargs) -> Any:
        nc = getattr(self._plugin, "notification_center", None)
        if not nc:
            return {"error": "通知系统不可用"}
        return await nc.mark_all_as_read()

    async def _notification_refresh_handler(self, **kwargs) -> Any:
        nc = getattr(self._plugin, "notification_center", None)
        if not nc:
            return {"error": "通知系统不可用"}
        changed = await nc.refresh()
        payload = await nc.get_payload()
        return {"ok": True, "changed": changed, **payload}

    async def _sessions_list_handler(self, **kwargs) -> Any:
        if not self._server:
            return {"sessions": []}
        sessions = self._server._list_known_sessions()
        result = []
        for session in sessions:
            override = self._plugin.session_override_manager.get_override(session)
            effective = self._plugin._get_session_config(session)
            result.append({
                "session": session,
                "session_name": self._plugin._get_session_name(session, effective),
                "session_display_name": self._plugin._get_session_display_name(session, effective),
                "has_override": bool(override),
                "enabled": bool(effective and effective.get("enable", False)),
                "unanswered_count": self._plugin.session_data.get(session, {}).get("unanswered_count", 0),
            })
        return {"sessions": result}

    async def _session_config_get_handler(self, **kwargs) -> Any:
        from quart import request

        session = request.args.get("session", "")
        if not session:
            return {"error": "缺少 session 参数"}

        normalized = self._plugin._normalize_session_id(session)
        base = self._plugin._get_base_session_config(normalized)
        return {
            "session": normalized,
            "base": base,
            "override": self._plugin.session_override_manager.get_override(normalized),
            "effective": self._plugin._get_session_config(normalized),
        }

    async def _session_config_post_handler(self, **kwargs) -> Any:
        from quart import request

        payload = await request.get_json()
        if not payload:
            return {"error": "请求体为空"}

        session = payload.get("session", "")
        if not session:
            return {"error": "缺少 session 参数"}

        normalized = self._plugin._normalize_session_id(session)
        mode = payload.get("mode", "effective")

        if mode == "override":
            override = payload.get("override", {})
            if not isinstance(override, dict):
                return {"error": "override 必须是对象"}
            await self._plugin.session_override_manager.set_override(normalized, override)
        else:
            effective = payload.get("effective", {})
            if not isinstance(effective, dict):
                return {"error": "effective 必须是对象"}
            base = self._plugin._get_base_session_config(normalized)
            if not base:
                return {"error": "会话未命中全局配置，无法保存 effective"}
            await self._plugin.session_override_manager.update_session_from_effective(
                normalized, base, effective
            )

        return {
            "ok": True,
            "session": normalized,
            "override": self._plugin.session_override_manager.get_override(normalized),
            "effective": self._plugin._get_session_config(normalized),
        }

    async def _session_config_reset_handler(self, **kwargs) -> Any:
        from quart import request

        payload = await request.get_json()
        session = payload.get("session", "") if payload else ""
        if not session:
            return {"error": "缺少 session 参数"}

        normalized = self._plugin._normalize_session_id(session)
        await self._plugin.session_override_manager.delete_override(normalized)
        return {
            "ok": True,
            "session": normalized,
            "override": {},
            "effective": self._plugin._get_session_config(normalized),
        }

    async def _markdown_files_handler(self, **kwargs) -> Any:
        if not self._server:
            return {"items": []}
        return {"items": self._server._list_markdown_documents()}

    async def _markdown_file_handler(self, **kwargs) -> Any:
        from quart import request

        file_path = request.args.get("path", "")
        if not file_path:
            return {"error": "缺少 path 参数"}

        if not self._server:
            return {"error": "服务未初始化"}

        resolved = self._server._resolve_markdown_document(file_path)
        if not resolved:
            return {"error": "文档不存在或不允许访问"}

        try:
            content = resolved.read_text(encoding="utf-8")
        except Exception as e:
            return {"error": f"读取文档失败: {e}"}

        return {
            "path": self._server._to_workspace_relative_path(resolved),
            "title": resolved.stem,
            "content": content,
            "content_format": "markdown",
        }
