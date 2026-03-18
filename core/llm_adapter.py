"""上下文获取与 LLM 调用模块。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from astrbot.api import logger


class LlmMixin:
    """上下文获取与 LLM 调用相关混入类。"""

    context: any
    timezone: any

    def _sanitize_history_content(self, history: list) -> list:
        """清洗历史消息内容，确保所有内容均为纯文本字符串喵。"""
        sanitized_history = []
        for msg in history:
            # 兼容不同类型的历史消息对象
            if hasattr(msg, "to_dict"):
                msg_dict = msg.to_dict()
            elif isinstance(msg, dict):
                msg_dict = msg.copy()
            else:
                logger.debug(
                    f"[主动消息] 历史记录中发现无法识别的消息格式: {type(msg)}，已跳过喵。"
                )
                continue

            content = msg_dict.get("content")
            if isinstance(content, list):
                # AstrBot 多媒体消息结构（只保留文本）
                text_content = ""
                for segment in content:
                    if isinstance(segment, dict):
                        if segment.get("type") == "text":
                            text_content += segment.get("text", "")
                    elif hasattr(segment, "text"):
                        text_content += getattr(segment, "text", "")
                    elif hasattr(segment, "get_text"):
                        text_content += segment.get_text()
                    elif isinstance(segment, str):
                        text_content += segment
                msg_dict["content"] = text_content
            elif not isinstance(content, str):
                # 非字符串内容强制转字符串
                msg_dict["content"] = str(content) if content is not None else ""

            sanitized_history.append(msg_dict)
        return sanitized_history

    async def _prepare_llm_request(self, session_id: str) -> dict | None:
        """准备 LLM 请求所需的上下文、人格和最终 Prompt。"""
        try:
            # 获取当前会话的对话 ID
            # 候选列表：优先原始 session_id，再尝试规范化 ID
            candidate_session_ids = [session_id]
            try:
                normalized_session_id = self._normalize_session_id(session_id)
            except Exception:
                normalized_session_id = session_id

            if (
                normalized_session_id
                and normalized_session_id not in candidate_session_ids
            ):
                candidate_session_ids.append(normalized_session_id)

            conv_id = None
            effective_session_id = session_id
            # 依次尝试候选会话，命中即停止
            for candidate in candidate_session_ids:
                conv_id = (
                    await self.context.conversation_manager.get_curr_conversation_id(
                        candidate
                    )
                )
                if conv_id:
                    effective_session_id = candidate
                    break

            if not conv_id:
                logger.info(
                    f"[主动消息] {self._get_session_log_str(session_id)} 是新会话，尝试创建新对话喵。"
                )
                try:
                    conv_id = await self.context.conversation_manager.new_conversation(
                        session_id
                    )
                    logger.info(f"[主动消息] 新对话创建成功喵，ID: {conv_id}")
                except ValueError:
                    raise
                except Exception as e:
                    logger.error(f"[主动消息] 创建新对话失败喵: {e}", exc_info=True)
                    return None

            if not conv_id:
                logger.warning(
                    f"[主动消息] 无法获取或创建 {self._get_session_log_str(session_id)} 的对话ID，跳过本次任务喵。"
                )
                return None

            # 拉取对话历史（可能是字符串化 JSON，也可能是对象列表）
            conversation = await self.context.conversation_manager.get_conversation(
                effective_session_id, conv_id
            )

            pure_history_messages = []
            if conversation and conversation.history:
                try:
                    if isinstance(conversation.history, str):
                        pure_history_messages = await asyncio.to_thread(
                            json.loads, conversation.history
                        )
                    else:
                        pure_history_messages = conversation.history
                except (json.JSONDecodeError, TypeError):
                    logger.warning("[主动消息] 解析历史记录失败，使用空历史喵。")

            # 获取人格设定：优先会话 persona，再回退默认 persona
            original_system_prompt = ""
            if conversation and conversation.persona_id:
                persona = await self.context.persona_manager.get_persona(
                    conversation.persona_id
                )
                if persona:
                    original_system_prompt = persona.system_prompt
                    logger.info(
                        f"[主动消息] 使用会话人格: '{conversation.persona_id}' 喵"
                    )

            if not original_system_prompt:
                default_persona = (
                    await self.context.persona_manager.get_default_persona_v3(
                        umo=effective_session_id
                    )
                )
                if default_persona:
                    original_system_prompt = default_persona["prompt"]
                    logger.info("[主动消息] 使用默认人格设定喵")

            if not original_system_prompt:
                logger.error(
                    "[主动消息] 呜喵？！关键错误喵：无法加载任何人格设定，放弃喵。"
                )
                return None

            logger.info(
                f"[主动消息] 成功加载上下文喵: 共 {len(pure_history_messages)} 条历史消息喵。"
            )

            return {
                "conv_id": conv_id,
                "history": pure_history_messages,
                "system_prompt": original_system_prompt,
                "session_id": effective_session_id,
            }

        except Exception as e:
            logger.warning(f"[主动消息] 获取上下文或人格失败喵: {e}")
            return None

    async def _generate_llm_response(
        self,
        session_id: str,
        session_config: dict,
        history_messages: list,
        system_prompt: str,
        unanswered_count: int,
    ) -> tuple[str | None, str]:
        """统一 LLM 调用入口，返回(生成文本, 用户提示词)。"""
        motivation_template = session_config.get("proactive_prompt", "")
        now_str = datetime.now(self.timezone).strftime("%Y年%m月%d日 %H:%M")
        final_user_simulation_prompt = motivation_template.replace(
            "{{unanswered_count}}", str(unanswered_count)
        ).replace("{{current_time}}", now_str)

        logger.info("[主动消息] 已生成包含动机和时间的 Prompt 喵。")

        llm_response_obj = None
        try:
            # 优先使用新版统一 LLM 接口（支持 provider_id + contexts）
            provider_id = await self.context.get_current_chat_provider_id(session_id)
            history_messages = self._sanitize_history_content(history_messages)
            llm_response_obj = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=final_user_simulation_prompt,
                contexts=history_messages,
                system_prompt=system_prompt,
            )
            logger.info("[主动消息] 使用新API调用LLM成功喵。")
        except Exception as llm_error:
            logger.error(f"[主动消息] 使用新API调用LLM失败喵: {llm_error}")
            logger.info(f"[主动消息] 错误类型喵: {type(llm_error).__name__}")
            logger.info(f"[主动消息] 错误详情喵: {str(llm_error)}")

            # 回退到旧接口（兼容历史 Provider 实现）
            try:
                provider = self.context.get_using_provider(umo=session_id)
                if provider:
                    llm_response_obj = await provider.text_chat(
                        prompt=final_user_simulation_prompt,
                        contexts=history_messages,
                        system_prompt=system_prompt,
                    )
                    logger.info("[主动消息] 使用传统API回退成功喵。")
                else:
                    logger.warning("[主动消息] 未找到 LLM Provider，放弃并重新调度喵。")
                    return None, final_user_simulation_prompt
            except Exception as fallback_error:
                logger.error(f"[主动消息] 传统API回退也失败喵: {fallback_error}")
                logger.info(
                    f"[主动消息] 回退错误类型喵: {type(fallback_error).__name__}"
                )
                logger.error("[主动消息] 呜喵？！LLM调用完全失败，将重新调度任务喵。")
                return None, final_user_simulation_prompt

        # 仅在确实拿到 completion_text 时视为成功
        if llm_response_obj and llm_response_obj.completion_text:
            response_text = llm_response_obj.completion_text.strip()
            if response_text == "[object Object]":
                logger.error(
                    "[主动消息] 喵呜！LLM 返回了意料之外的 '[object Object]' 字符串喵！"
                )
                logger.warning(
                    "[主动消息] 这通常是因为上下文或 Prompt 中包含了无法解析的对象喵。已拦截本次发送喵。"
                )
                return None, final_user_simulation_prompt
            logger.info(f"[主动消息] LLM 已生成文本喵: '{response_text}'。")
            return response_text, final_user_simulation_prompt

        logger.warning("[主动消息] LLM 调用失败或返回空内容，重新调度喵。")
        return None, final_user_simulation_prompt
