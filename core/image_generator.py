"""主动消息配图能力。

通过 AstrBot 的工具循环 Agent，让主 LLM 在生成主动消息后自行判断/调用
任意已安装的生图插件（向主 LLM 注册了工具的插件）来生成配图。生成的图片
不会由生图插件直接发往平台，而是被一个“捕获型”合成事件拦截、收集后交还给
本插件，统一走主动消息既有的发送流程（分段、装饰钩子、平台历史持久化）。

设计要点：
- provider 无关：不绑定任何特定生图插件，依赖 ``get_full_tool_set`` 暴露的
  全部已注册工具，由模型自行选择。
- 不改动 AstrBot 源码：仅继承公开基类、调用公开 API。
- 失败静默降级：任何环节出错都只记录日志、回退为纯文本，绝不把错误信息塞进
  发送给用户的消息内容。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from astrbot.api import logger

try:
    from astrbot.core.agent.tool import ToolSet
    from astrbot.core.message.components import Image, Plain
    from astrbot.core.message.message_event_result import MessageChain
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_session import MessageSession
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata

    _IMAGE_AGENT_AVAILABLE = True
except ImportError as _e:  # pragma: no cover - 取决于宿主版本
    _IMAGE_AGENT_AVAILABLE = False
    logger.warning(f"[主动消息] 当前 AstrBot 版本不支持配图 Agent 所需组件喵: {_e}")


# 占位符，便于在依赖缺失时仍能定义类型注解。
_BaseEvent = AstrMessageEvent if _IMAGE_AGENT_AVAILABLE else object


class _ImageCaptureEvent(_BaseEvent):  # type: ignore[misc, valid-type]
    """捕获型合成事件。

    参考 AstrBot 官方的 ``CronMessageEvent`` 构造一个不绑定真实平台连接的合成
    事件，供工具循环 Agent 使用。与之不同的是：本事件重写 ``send`` ，把生图
    插件试图发送的图片 **拦截到内部缓冲区** ，而不是真正发往平台——从而让本
    插件能拿回图片、自行决定如何发送。
    """

    def __init__(
        self,
        *,
        context: Any,
        session: "MessageSession",
        message: str,
        message_type: "MessageType",
    ) -> None:
        platform_meta = PlatformMetadata(
            name="proactive_image",
            description="ProactiveChat 配图合成事件",
            id=session.platform_id,
        )

        msg_obj = AstrBotMessage()
        msg_obj.type = message_type
        msg_obj.self_id = "astrbot"
        msg_obj.session_id = session.session_id
        msg_obj.message_id = uuid.uuid4().hex
        msg_obj.sender = MessageMember(user_id=session.session_id, nickname="主动消息")
        msg_obj.message = [Plain(message)]
        msg_obj.message_str = message
        msg_obj.raw_message = message
        msg_obj.timestamp = int(time.time())

        super().__init__(message, msg_obj, platform_meta, session.session_id)

        # 使用原始会话，保证工具内部读取 unified_msg_origin 等信息时一致。
        self.session = session
        self.context_obj = context
        self.is_at_or_wake_command = True
        self.is_wake = True

        # 收集被拦截的图片组件，供 Agent 结束后取用。
        self.captured_images: list["Image"] = []

    async def send(self, message: "MessageChain") -> None:
        """拦截发送：仅收集图片组件，不真正发往平台。"""
        try:
            if message and getattr(message, "chain", None):
                for comp in message.chain:
                    if isinstance(comp, Image):
                        self.captured_images.append(comp)
        except Exception as e:  # noqa: BLE001 - 拦截阶段不应影响主流程
            logger.debug(f"[主动消息] 捕获配图组件时出现异常喵: {e!r}")
        # 故意不调用 super().send()，避免触发真实平台发送与统计。

    async def send_streaming(self, generator, use_fallback: bool = False) -> None:
        async for chain in generator:
            await self.send(chain)


# 引导模型决定是否配图的系统提示。
_AUTO_SYSTEM_PROMPT = (
    "你刚刚生成了一条要主动发给用户的消息。现在请判断这条消息是否适合配一张图片。\n"
    "- 如果适合（例如描述了某个画面、场景、物品、表情、心情且配图能增强表达），"
    "请调用一个可用的生图工具来生成与消息内容相符的图片。\n"
    "- 如果不适合（例如纯粹的问候、提问、抽象表达），则不要调用任何工具，直接结束。\n"
    "不要输出多余的解释，只在需要时调用生图工具。"
)
_ALWAYS_SYSTEM_PROMPT = (
    "你刚刚生成了一条要主动发给用户的消息。请调用一个可用的生图工具，生成一张"
    "与这条消息内容相符的配图。请直接调用工具，不要输出多余解释。"
)


class ImageMixin:
    """为主动消息提供“配图”能力的 Mixin。"""

    async def _maybe_generate_proactive_images(
        self, session_id: str, text: str, session_config: dict
    ) -> list:
        """根据配置决定并生成主动消息的配图。

        返回一个图片组件列表（可能为空）。任何失败都会被吞掉并返回空列表，
        以保证主动消息至少能以纯文本形式发出。
        """
        image_conf = (session_config or {}).get("image_settings", {})
        mode = str(image_conf.get("mode", "off") or "off").strip().lower()
        if mode not in ("auto", "always"):
            return []

        if not _IMAGE_AGENT_AVAILABLE:
            logger.warning(
                "[主动消息] 当前 AstrBot 版本不支持配图所需的合成事件组件，跳过配图喵。"
            )
            return []

        try:
            return await self._run_image_agent(session_id, text, image_conf, mode)
        except Exception as e:  # noqa: BLE001 - 配图失败不可影响文本发送
            logger.warning(f"[主动消息] 生成主动消息配图失败，将仅发送文本喵: {e!r}")
            return []

    @staticmethod
    def _parse_name_list(raw) -> list[str]:
        """把配置值解析为名字列表。

        兼容 list 与逗号/换行分隔的字符串两种写法，去重并保持顺序。
        """
        names: list[str] = []
        if isinstance(raw, str):
            for piece in raw.replace("\n", ",").split(","):
                piece = piece.strip()
                if piece:
                    names.append(piece)
        elif isinstance(raw, (list, tuple)):
            for item in raw:
                piece = str(item).strip()
                if piece:
                    names.append(piece)
        seen: set[str] = set()
        result: list[str] = []
        for n in names:
            if n not in seen:
                seen.add(n)
                result.append(n)
        return result

    def _select_image_tools(self, tool_manager, image_conf: dict):
        """挑选交给 Agent 的生图工具。

        规则：按关键词（工具名 + 描述）自动识别生图工具；再叠加用户配置的
        extra_tools（强制补充，即使不含关键词）与 exclude_tools（强制排除）。
        返回一个 ToolSet（可能为空）。
        """
        extra = set(self._parse_name_list((image_conf or {}).get("extra_tools", [])))
        exclude = set(self._parse_name_list((image_conf or {}).get("exclude_tools", [])))

        try:
            all_tools = list(getattr(tool_manager, "func_list", []) or [])
        except Exception:  # noqa: BLE001
            all_tools = []

        tool_set = ToolSet()
        for tool in all_tools:
            name = getattr(tool, "name", "") or ""
            if name in exclude:
                continue
            desc = getattr(tool, "description", "") or ""
            hit_keyword = self._looks_like_image_tool(name, desc)
            if hit_keyword or name in extra:
                tool_set.add_tool(tool)

        # extra 里指向但上面没收进来的（理论上已收，双保险按名字补一次）
        for name in extra:
            if tool_set.get_tool(name) is None and name not in exclude:
                t = tool_manager.get_func(name) if hasattr(tool_manager, "get_func") else None
                if t is not None:
                    tool_set.add_tool(t)
        return tool_set

    # 生图相关关键词（小写）。命中工具名或描述即视为候选生图工具。
    # 视频类（video）刻意不收，避免把视频生成工具当配图。
    _IMAGE_KEYWORDS = (
        "draw", "image", "paint", "pic", "photo", "illustr", "t2i", "text2image",
        "text-to-image", "stable", "diffusion", "midjourney", "dalle", "dall-e",
        "flux", "comfyui", "render",
        "生图", "绘图", "绘画", "画图", "配图", "图片", "作画", "出图", "画一", "生成图",
    )
    # 强生图信号：命中负向词后，只要工具名或描述里出现这些词，仍判定为生图工具。
    _IMAGE_STRONG = (
        "draw", "paint", "t2i", "text2image", "text-to-image",
        "generate image", "generate an image", "image generation",
        "生图", "绘图", "绘画", "绘制", "画图", "作画", "配图",
        "生成图片", "生成一张", "画一张", "画一幅",
    )
    # 明显非生图但可能含 image 字样的工具，关键词命中后再排除一层。
    _IMAGE_NEGATIVE = (
        "read", "ocr", "recogn", "识别", "video", "视频", "understand", "analy", "描述图",
    )

    @classmethod
    def _looks_like_image_tool(cls, name: str, description: str) -> bool:
        """按关键词判断一个工具是否像“文生图”工具。

        名字与描述双线匹配：任一命中生图关键词即为候选；命中负向词时，
        只要名字或描述里仍出现强生图信号，依然保留。
        """
        haystack = f"{name} {description}".lower()
        if not any(kw in haystack for kw in cls._IMAGE_KEYWORDS):
            return False
        # 命中负向词时，要求名字或描述里带强生图信号才保留，否则排除。
        if any(neg in haystack for neg in cls._IMAGE_NEGATIVE):
            return any(s in haystack for s in cls._IMAGE_STRONG)
        return True

    # 选不到生图工具的最大累计次数，超过则本次运行内永久回退为纯文本。
    _IMAGE_TOOLS_MAX_ATTEMPTS = 3

    def _ensure_image_tool_names(self, tool_manager, image_conf: dict) -> list[str]:
        """返回已选定并缓存的生图工具名；未选定则尝试选一次。

        - 一旦成功选定就缓存，后续直接复用，不再扫描。
        - 累计选不到达到上限后永久回退（本次运行内不再尝试）。
        """
        if getattr(self, "_image_tools_disabled", False):
            return []
        if getattr(self, "_image_tools_selected", False):
            return list(self._image_tools_cache)

        tool_set = self._select_image_tools(tool_manager, image_conf)
        names = [t.name for t in tool_set.tools] if tool_set else []
        if names:
            self._image_tools_cache = names
            self._image_tools_selected = True
            logger.info("[主动消息] 已找到可用的生图工具喵。")
            return list(names)

        # 没选到：累计计数，达到上限则永久回退。
        self._image_tools_attempts = getattr(self, "_image_tools_attempts", 0) + 1
        if self._image_tools_attempts >= self._IMAGE_TOOLS_MAX_ATTEMPTS:
            self._image_tools_disabled = True
            logger.info(
                "[主动消息] 多次未找到可用生图工具喵，已回退为不生图模式（本次运行内不再尝试）。"
            )
        else:
            logger.info("[主动消息] 暂未找到可用的生图工具喵，稍后重试。")
        return []

    async def prewarm_image_tools(self) -> None:
        """插件加载/空闲时预选一次生图工具，避免发送时才扫描。

        加载阶段生图插件可能尚未就绪，选不到属正常，不计入失败次数。
        """
        if not _IMAGE_AGENT_AVAILABLE:
            return
        if getattr(self, "_image_tools_selected", False) or getattr(
            self, "_image_tools_disabled", False
        ):
            return
        try:
            tool_manager = self.context.get_llm_tool_manager()
        except Exception:  # noqa: BLE001
            return
        # 预热用空配置即可（extra/exclude 在真正发送时按会话配置再算）；
        # 这里只为尽早把“有没有生图工具”探明并缓存。
        tool_set = self._select_image_tools(tool_manager, {})
        names = [t.name for t in tool_set.tools] if tool_set else []
        if names:
            self._image_tools_cache = names
            self._image_tools_selected = True
            logger.info("[主动消息] 已找到可用的生图工具喵。")
        # 预热选不到不记失败、不打 warning，留给发送时按需重试。

    async def _run_image_agent(
        self, session_id: str, text: str, image_conf: dict, mode: str
    ) -> list:
        """用工具循环 Agent 驱动任意已注册的生图工具，收集其产出的图片。"""
        context = self.context

        # 解析会话来源，构造合成事件所需的 MessageSession。
        session = MessageSession.from_str(session_id)

        # 拿到当前会话使用的对话 provider；没有则无法驱动 Agent。
        provider_id = await context.get_current_chat_provider_id(session_id)
        if not provider_id:
            logger.info("[主动消息] 未找到可用对话 provider，跳过配图喵。")
            return []

        # 取已选定（并缓存）的生图工具名；未选定则尝试选一次。
        tool_manager = context.get_llm_tool_manager()
        tool_names = self._ensure_image_tool_names(tool_manager, image_conf)
        if not tool_names:
            # 已选不到（或已永久回退），_ensure_image_tool_names 内已记日志。
            return []

        # 用缓存的工具名重建本次调用的 ToolSet。
        tool_set = ToolSet()
        for name in tool_names:
            tool = tool_manager.get_func(name)
            if tool is not None:
                tool_set.add_tool(tool)
        if tool_set.empty():
            logger.info("[主动消息] 已选定的生图工具当前不可用，跳过配图喵。")
            return []

        capture_event = _ImageCaptureEvent(
            context=context,
            session=session,
            message=text,
            message_type=session.message_type,
        )

        base_prompt = _ALWAYS_SYSTEM_PROMPT if mode == "always" else _AUTO_SYSTEM_PROMPT
        extra = str(image_conf.get("extra_prompt", "") or "").strip()
        system_prompt = f"{base_prompt}\n\n{extra}" if extra else base_prompt

        user_prompt = f"这是要发送的主动消息内容：\n{text}"

        await context.tool_loop_agent(
            event=capture_event,
            chat_provider_id=provider_id,
            prompt=user_prompt,
            tools=tool_set,
            system_prompt=system_prompt,
            max_steps=6,
        )

        images = list(capture_event.captured_images)
        if images:
            logger.info(f"[主动消息] 配图 Agent 共捕获 {len(images)} 张图片喵。")
        else:
            logger.info("[主动消息] 配图 Agent 未产出图片（模型判断无需配图或工具未生图）喵。")
        return images
