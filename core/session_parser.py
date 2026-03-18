"""会话解析与日志格式化模块。"""

from __future__ import annotations

from astrbot.core.platform.platform import PlatformStatus


class SessionMixin:
    """会话解析与日志格式化混入类。"""

    context: any

    def _parse_session_id(self, session_id: str) -> tuple[str, str, str] | None:
        """
        解析会话 UMO，返回 (platform, message_type, target_id)。

        该方法仅用于解析与展示，不对 UMO 做任何修正或重写。
        """
        # 仅接受字符串类型的 UMO
        if not isinstance(session_id, str):
            return None

        # 优先匹配标准消息类型锚点
        known_types = [
            "FriendMessage",
            "GroupMessage",
            "PrivateMessage",
            "GuildMessage",
        ]

        # 先走锚点匹配，避免 platform/target 中包含冒号导致误切分
        for msg_type in known_types:
            search_pattern = f":{msg_type}:"
            idx = session_id.find(search_pattern)
            if idx != -1:
                platform = session_id[:idx]
                after_type = session_id[idx + len(search_pattern) :]
                return platform, msg_type, after_type

        # 兼容普通三段式 UMO
        parts = session_id.split(":")
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]

        # 兼容多段 platform 或 target_id 的情况
        if len(parts) > 3:
            return ":".join(parts[:-2]), parts[-2], parts[-1]

        return None

    def _get_session_log_str(
        self, session_id: str, session_config: dict | None = None
    ) -> str:
        """
        获取统一格式的会话日志字符串。

        格式：私聊/群聊 ID
        """
        parsed = self._parse_session_id(session_id)
        if not parsed:
            return session_id

        # 仅用于日志展示，不参与业务逻辑
        _, msg_type, target_id = parsed
        type_str = "未知类型"
        if "Friend" in msg_type or "Private" in msg_type:
            type_str = "私聊"
        elif "Group" in msg_type or "Guild" in msg_type:
            type_str = "群聊"

        return f"{type_str} {target_id}"

    def _resolve_full_umo(
        self, target_id: str, msg_type: str, preferred_platform: str | None = None
    ) -> str:
        """
        动态解析并验证存活的 UMO。

        优先使用首选平台（若运行中），否则尝试历史平台，再回退到当前运行平台或 default。
        """
        type_keyword = (
            "Friend" if "Friend" in msg_type or "Private" in msg_type else "Group"
        )

        # 仅在“可用平台集合”中选择目标，过滤 webchat 等非目标实例
        active_insts = {
            p.meta().id: p
            for p in self.context.platform_manager.get_insts()
            if p.meta().id and "webchat" not in p.meta().id.lower()
        }

        # 首选平台仍在线时优先复用，保持会话平台一致性
        if (
            preferred_platform
            and preferred_platform in active_insts
            and active_insts[preferred_platform].status == PlatformStatus.RUNNING
        ):
            return f"{preferred_platform}:{msg_type}:{target_id}"

        # 次选：从历史 session_data 中寻找同目标且在线的平台
        for existing_id in getattr(self, "session_data", {}).keys():
            if type_keyword in existing_id and existing_id.endswith(f":{target_id}"):
                p_id = existing_id.split(":")[0]
                if (
                    p_id in active_insts
                    and active_insts[p_id].status == PlatformStatus.RUNNING
                ):
                    return existing_id

        # 再次回退：任取一个当前运行平台
        running_platforms = [
            p for p in active_insts.values() if p.status == PlatformStatus.RUNNING
        ]
        if running_platforms:
            return f"{running_platforms[0].meta().id}:{msg_type}:{target_id}"

        # 最终回退：无运行平台时仅保证 UMO 结构可用
        fallback_p_id = list(active_insts.keys())[0] if active_insts else "default"
        return f"{fallback_p_id}:{msg_type}:{target_id}"

    def _normalize_session_id(self, session_id: str) -> str:
        """
        规范化 UMO，确保使用可运行的平台前缀。
        """
        parsed = self._parse_session_id(session_id)
        if not parsed:
            return session_id

        platform, msg_type, target_id = parsed
        return self._resolve_full_umo(target_id, msg_type, platform)
