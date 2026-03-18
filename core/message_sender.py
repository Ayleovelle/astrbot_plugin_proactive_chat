"""发送与装饰钩子模块。"""

from __future__ import annotations

import asyncio
import math
import random
import re
import traceback

from astrbot.api import logger
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult
from astrbot.core.platform.astrbot_message import AstrBotMessage, Group, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.star.star_handler import EventType, star_handlers_registry

try:
    from astrbot.api.event import AstrMessageEvent as AstrBotMessageEvent
except ImportError:
    AstrBotMessageEvent = None

try:
    from astrbot.core.platform.astr_message_event import MessageSession as MS
except ImportError:
    from astrbot.core.platform.message_session import MessageSession as MS


class SenderMixin:
    """发送与装饰钩子混入类。"""

    context: any
    session_data: dict

    def _split_text(self, text: str, settings: dict) -> list[str]:
        """根据配置对文本进行分段。"""
        split_mode = settings.get("split_mode", "regex")

        # 分段词列表模式
        # 模式1：按分段词拆分（如句号、问号）
        if split_mode == "words":
            split_words = settings.get("split_words", ["。", "？", "！", "~", "…"])
            if not split_words:
                return [text]

            escaped_words = sorted(
                [re.escape(word) for word in split_words], key=len, reverse=True
            )
            # 保留分隔符，避免语气符号在切分时丢失
            pattern = re.compile(f"(.*?({'|'.join(escaped_words)})|.+$)", re.DOTALL)

            segments = pattern.findall(text)
            result: list[str] = []
            for seg in segments:
                if isinstance(seg, tuple):
                    content = seg[0]
                    if not isinstance(content, str):
                        continue
                    if content.strip():
                        result.append(content)
                elif seg and seg.strip():
                    result.append(seg)
            return result if result else [text]

        # 正则分段模式
        regex_pattern = settings.get("regex", r".*?[。？！~…\n]+|.+$")
        try:
            split_response = re.findall(regex_pattern, text, re.DOTALL | re.MULTILINE)
        except re.error:
            logger.error(
                f"[主动消息] 分段回复正则表达式错误，使用默认分段方式: {traceback.format_exc()}"
            )
            split_response = re.findall(
                r".*?[。？！~…\n]+|.+$", text, re.DOTALL | re.MULTILINE
            )

        return [seg for seg in split_response if seg.strip()]

    async def _calc_interval(self, text: str, settings: dict) -> float:
        """计算分段回复的间隔时间。"""
        interval_method = settings.get("interval_method", "random")

        # 对数间隔模式（模拟打字速度）
        if interval_method == "log":
            log_base = float(settings.get("log_base", 1.8))
            if all(ord(c) < 128 for c in text):
                word_count = len(text.split())
            else:
                word_count = len([c for c in text if c.isalnum()])
            i = math.log(word_count + 1, log_base)
            return random.uniform(i, i + 0.5)

        # 随机区间模式
        interval_str = settings.get("interval", "1.5, 3.5")
        try:
            interval_ls = [float(t) for t in interval_str.replace(" ", "").split(",")]
            interval = interval_ls if len(interval_ls) == 2 else [1.5, 3.5]
        except Exception:
            interval = [1.5, 3.5]

        return random.uniform(interval[0], interval[1])

    async def _trigger_decorating_hooks(self, session_id: str, chain: list) -> list:
        """触发 OnDecoratingResultEvent 钩子。"""
        parsed = self._parse_session_id(session_id)
        if not parsed:
            return chain

        # 解析出平台、消息类型、目标 ID，用于构造事件上下文
        platform_name, msg_type_str, target_id = parsed
        platform_inst = None
        for p in self.context.platform_manager.platform_insts:
            if p.meta().id == platform_name:
                platform_inst = p
                break

        # 兼容按平台显示名匹配（部分平台可能用 name 进行标识）
        if not platform_inst:
            for p in self.context.platform_manager.platform_insts:
                if p.meta().name == platform_name:
                    platform_inst = p
                    break

        if not platform_inst:
            return chain

        # 构造伪造的消息对象以触发装饰链
        message_obj = AstrBotMessage()
        if "Friend" in msg_type_str:
            message_obj.type = MessageType.FRIEND_MESSAGE
        elif "Group" in msg_type_str:
            message_obj.type = MessageType.GROUP_MESSAGE
            message_obj.group = Group(group_id=target_id)
        else:
            message_obj.type = MessageType.FRIEND_MESSAGE

        # 构造最小可用消息对象，让装饰器可在统一事件结构上改写链
        message_obj.session_id = target_id
        message_obj.message = chain
        message_obj.self_id = self.session_data.get(session_id, {}).get(
            "self_id", "bot"
        )
        message_obj.sender = MessageMember(user_id=target_id)
        message_obj.message_str = ""
        message_obj.raw_message = None
        message_obj.message_id = ""

        # 旧版本若无事件类则跳过装饰阶段，直接返回原链
        if not AstrBotMessageEvent:
            return chain

        event = AstrBotMessageEvent(
            message_str="",
            message_obj=message_obj,
            platform_meta=platform_inst.meta(),
            session_id=target_id,
        )

        # 注入结果链以便装饰器修改
        res = MessageEventResult()
        res.chain = chain
        event.set_result(res)

        # 顺序执行所有 OnDecoratingResultEvent 处理器
        handlers = star_handlers_registry.get_handlers_by_event_type(
            EventType.OnDecoratingResultEvent
        )
        for handler in handlers:
            try:
                logger.debug(
                    f"[主动消息] 正在执行装饰钩子: {handler.handler_full_name} ({handler.handler_module_path}) 喵"
                )
                await handler.handler(event)
            except Exception as e:
                error_type = type(e).__name__
                logger.error(
                    f"[主动消息] 执行装饰钩子失败喵！来源: {handler.handler_full_name}, "
                    f"错误类型: {error_type}, 错误详情: {e}"
                )
                if "Available" in error_type:
                    logger.error(
                        f"[主动消息] 抓到可能导致 ApiNotAvailable 的嫌疑人喵！模块: {handler.handler_module_path}"
                    )

        res = event.get_result()
        if res is not None:
            return res.chain if res.chain is not None else []
        return chain

    async def _send_chain_with_hooks(self, session_id: str, components: list) -> None:
        """发送消息链（含装饰钩子）。"""
        processed_chain_list = await self._trigger_decorating_hooks(
            session_id, components
        )
        if not processed_chain_list:
            return

        # 将处理后的组件列表封装为统一消息链对象
        chain = MessageChain(processed_chain_list)
        parsed = self._parse_session_id(session_id)
        if not parsed:
            # 无法解析则使用核心 API 兜底
            await self.context.send_message(session_id, chain)
            return

        p_id, m_type_str, t_id = parsed
        m_type = (
            MessageType.GROUP_MESSAGE
            if "Group" in m_type_str
            else MessageType.FRIEND_MESSAGE
        )

        # 精确匹配平台实例：避免将消息发往错误平台
        platforms = self.context.platform_manager.get_insts()
        target_platform = next((p for p in platforms if p.meta().id == p_id), None)

        if not target_platform:
            logger.warning(
                f"[主动消息] 找不到指定的平台 {p_id} 喵，尝试使用核心 API 兜底喵。"
            )
            await self.context.send_message(session_id, chain)
            return

        if target_platform.status != PlatformStatus.RUNNING:
            logger.warning(f"[主动消息] 平台 {p_id} 未运行喵，跳过主动消息喵。")
            return

        try:
            session_obj = MS(platform_name=p_id, message_type=m_type, session_id=t_id)
            await target_platform.send_by_session(session_obj, chain)
            logger.debug(f"[主动消息] 消息将通过平台 {p_id} 送达喵")
        except Exception as e:
            logger.error(f"[主动消息] 通过平台 {p_id} 发送失败喵: {e}")
            logger.debug(traceback.format_exc())

    async def _send_proactive_message(self, session_id: str, text: str) -> None:
        """发送主动消息（支持TTS与分段）。"""
        session_config = self._get_session_config(session_id)
        if not session_config:
            logger.info(
                f"[主动消息] 无法获取会话配置，跳过 {self._get_session_log_str(session_id)} 的消息发送喵。"
            )
            return

        logger.info(
            f"[主动消息] 开始发送 {self._get_session_log_str(session_id, session_config)} 的主动消息喵。"
        )

        tts_conf = session_config.get("tts_settings", {})
        seg_conf = session_config.get("segmented_reply_settings", {})

        # 先尝试 TTS：成功后是否继续发文本由 always_send_text 控制
        is_tts_sent = False
        if tts_conf.get("enable_tts", True):
            try:
                logger.info("[主动消息] 尝试进行手动TTS喵。")
                tts_provider = self.context.get_using_tts_provider(umo=session_id)
                if tts_provider:
                    audio_path = await tts_provider.get_audio(text)
                    if audio_path:
                        await self.context.send_message(
                            session_id, MessageChain([Record(file=audio_path)])
                        )
                        is_tts_sent = True
                        await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"[主动消息] 手动TTS流程发生异常喵: {e}")

        # 是否继续发送文本：未发出 TTS 或配置要求始终发文本
        should_send_text = not is_tts_sent or tts_conf.get("always_send_text", True)

        if should_send_text:
            enable_seg = seg_conf.get("enable", False)
            threshold = seg_conf.get("words_count_threshold", 150)

            # 启用分段且内容长度未超过阈值时分段发送
            if enable_seg and len(text) <= threshold:
                segments = self._split_text(text, seg_conf)
                if not segments:
                    segments = [text]

                logger.info(
                    f"[主动消息] 分段回复已启用，将发送 {len(segments)} 条消息喵。"
                )

                # 分段顺序发送，段间按策略等待，模拟自然输出节奏
                for idx, seg in enumerate(segments):
                    await self._send_chain_with_hooks(session_id, [Plain(text=seg)])
                    if idx < len(segments) - 1:
                        interval = await self._calc_interval(seg, seg_conf)
                        logger.debug(f"[主动消息] 分段回复等待 {interval:.2f} 秒喵。")
                        await asyncio.sleep(interval)
            else:
                await self._send_chain_with_hooks(session_id, [Plain(text=text)])

        # Bot 在群聊发言后需要重置沉默计时
        if "group" in session_id.lower():
            await self._reset_group_silence_timer(session_id)
            logger.info(
                f"[主动消息] Bot主动消息已发送，已重置 {self._get_session_log_str(session_id, session_config)} 的沉默倒计时喵。"
            )
