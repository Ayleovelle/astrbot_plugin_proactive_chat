"""
LLM客户端模块 - 负责与语言模型的交互
基于原main.py中的LLM调用逻辑重构
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, Any, Optional, List


class LLMClient:
    """
    LLM客户端 - 负责与语言模型的交互
    
    主要职责：
    1. LLM请求准备
    2. 双API支持（新API和传统API）
    3. 上下文和人格设定获取
    4. Prompt构造和响应处理
    5. 错误分类和处理
    6. 对话历史存档
    """
    
    def __init__(self, context_getter: callable, logger=None):
        """
        初始化LLM客户端
        
        Args:
            context_getter: 获取AstrBot上下文的函数
            logger: 日志记录器
        """
        self.context_getter = context_getter
        self.logger = logger
        self._api_call_stats = {
            "new_api_calls": 0,
            "fallback_api_calls": 0,
            "total_calls": 0,
            "errors": 0
        }
    
    async def prepare_llm_request(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        准备 LLM 请求所需的上下文、人格和最终 Prompt
        
        Args:
            session_id: 会话ID
            
        Returns:
            包含conv_id、history、system_prompt的请求包，失败时返回None
        """
        try:
            # 获取AstrBot上下文
            context = self.context_getter()
            if not context:
                raise RuntimeError("无法获取AstrBot上下文")
            
            # 获取当前会话的对话ID
            conv_id = await context.conversation_manager.get_curr_conversation_id(session_id)
            if not conv_id:
                if self.logger:
                    self.logger.warning(
                        f"[主动消息] 无法找到会话 {session_id} 的当前对话ID，可能是新会话，跳过本次任务喵。"
                    )
                return None
            
            # 获取对话对象
            conversation = await context.conversation_manager.get_conversation(session_id, conv_id)
            
            # 获取对话历史
            pure_history_messages = []
            if conversation and conversation.history:
                try:
                    # 尝试解析JSON格式的历史记录
                    if isinstance(conversation.history, str):
                        pure_history_messages = await asyncio.to_thread(
                            json.loads, conversation.history
                        )
                    else:
                        pure_history_messages = conversation.history
                except (json.JSONDecodeError, TypeError):
                    # 解析失败时使用空历史
                    pure_history_messages = []
                    if self.logger:
                        self.logger.warning("[主动消息] 解析历史记录失败，使用空历史喵。")
            
            # 获取人格设定
            original_system_prompt = await self._get_persona(conversation, session_id)
            if not original_system_prompt:
                if self.logger:
                    self.logger.error(
                        "[主动消息] 呜喵？！关键错误喵：无法加载任何人格设定，放弃喵。"
                    )
                return None
            
            if self.logger:
                self.logger.info(
                    f"[主动消息] 成功加载上下文喵: 共 {len(pure_history_messages)} 条历史消息喵。"
                )
            
            return {
                "conv_id": conv_id,
                "history": pure_history_messages,
                "system_prompt": original_system_prompt,
            }
            
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[主动消息] 获取上下文或人格失败喵: {e}")
            raise RuntimeError(f"准备LLM请求失败: {e}")
    
    async def _get_persona(self, conversation, session_id: str) -> Optional[str]:
        """
        获取人格设定
        
        Args:
            conversation: 对话对象
            session_id: 会话ID
            
        Returns:
            人格设定文本，失败时返回None
        """
        try:
            # 获取AstrBot上下文
            context = self.context_getter()
            if not context:
                return None
            
            original_system_prompt = ""
            
            # 优先使用会话绑定的persona
            if conversation and conversation.persona_id:
                persona = await context.persona_manager.get_persona(conversation.persona_id)
                if persona:
                    original_system_prompt = persona.system_prompt
                    if self.logger:
                        self.logger.info(
                            f"[主动消息] 使用会话人格: '{conversation.persona_id}' 喵"
                        )
            
            # 如果没有会话persona，使用默认persona
            if not original_system_prompt:
                default_persona = await context.persona_manager.get_default_persona_v3(umo=session_id)
                if default_persona:
                    original_system_prompt = default_persona["prompt"]
                    if self.logger:
                        self.logger.info("[主动消息] 使用默认人格设定喵")
            
            return original_system_prompt
            
        except Exception as e:
            raise RuntimeError(f"获取人格设定失败: {e}")
    
    async def generate_response(self, session_id: str, prompt: str, 
                              contexts: List[Any], system_prompt: str) -> Optional[Dict[str, Any]]:
        """
        使用统一的LLM接口生成回复
        
        Args:
            session_id: 会话ID
            prompt: 用户提示
            contexts: 上下文历史
            system_prompt: 系统提示
            
        Returns:
            LLM响应对象，失败时返回None
        """
        try:
            # 更新统计
            self._api_call_stats["total_calls"] += 1
            self._api_call_stats["new_api_calls"] += 1
            
            # 获取AstrBot上下文
            context = self.context_getter()
            if not context:
                raise RuntimeError("无法获取AstrBot上下文")
            
            # 获取当前会话使用的LLM提供商ID
            provider_id = await context.get_current_chat_provider_id(session_id)
            
            # 使用统一的llm_generate接口调用LLM
            llm_response_obj = await context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                contexts=contexts,
                system_prompt=system_prompt,
            )
            
            if self.logger:
                self.logger.info("[主动消息] 使用统一的context.llm_generate API成功喵！")
            return llm_response_obj
            
        except Exception as e:
            # 更新错误统计
            self._api_call_stats["errors"] += 1
            
            # 详细的错误分类和处理 - 复刻原代码第1635-1646行逻辑
            error_type = type(e).__name__
            error_msg = str(e)
            
            # 记录详细的错误信息 - 复刻原代码第1537-1539行
            if self.logger:
                self.logger.error(f"[主动消息] 新API调用失败喵: {e}")
                self.logger.info(f"[主动消息] 错误类型喵: {error_type}")
                self.logger.info(f"[主动消息] 错误详情喵: {error_msg}")
            else:
                # 备用日志输出
                print(f"[主动消息] 新API调用失败喵: {e}")
                print(f"[主动消息] 错误类型喵: {error_type}")
                print(f"[主动消息] 错误详情喵: {error_msg}")
            
            # 根据错误类型进行不同的处理
            if "RateLimitError" in error_type or "quota" in error_msg.lower():
                if self.logger:
                    self.logger.warning("[主动消息] 检测到API限制错误，将延长重试间隔喵。")
                else:
                    print("[主动消息] 检测到API限制错误，将延长重试间隔喵。")
            elif "Connection" in error_type or "Timeout" in error_type:
                if self.logger:
                    self.logger.warning("[主动消息] 检测到连接错误，可能需要检查网络设置喵。")
                else:
                    print("[主动消息] 检测到连接错误，可能需要检查网络设置喵。")
            elif "Authentication" in error_type or "auth" in error_msg.lower():
                if self.logger:
                    self.logger.error("[主动消息] 认证错误，请检查API密钥配置喵。")
                else:
                    print("[主动消息] 认证错误，请检查API密钥配置喵。")
                # 认证错误通常需要手动干预
                raise RuntimeError(f"认证错误，请检查API配置: {error_msg}")
            
            # 尝试使用传统方式作为回退 - 复刻原代码第1541-1567行
            try:
                return await self._fallback_to_traditional_api(session_id, prompt, contexts, system_prompt)
            except Exception as fallback_error:
                # 记录更详细的回退失败信息 - 复刻原代码第1558-1562行
                if self.logger:
                    self.logger.error(f"[主动消息] 传统API回退也失败喵: {fallback_error}")
                    self.logger.info(f"[主动消息] 回退错误类型喵: {type(fallback_error).__name__}")
                else:
                    print(f"[主动消息] 传统API回退也失败喵: {fallback_error}")
                    print(f"[主动消息] 回退错误类型喵: {type(fallback_error).__name__}")
                raise RuntimeError(f"LLM调用完全失败喵: {fallback_error}")
    
    async def _fallback_to_traditional_api(self, session_id: str, prompt: str, 
                                         contexts: List[Any], system_prompt: str) -> Optional[Dict[str, Any]]:
        """
        回退到传统API
        
        Args:
            session_id: 会话ID
            prompt: 用户提示
            contexts: 上下文历史
            system_prompt: 系统提示
            
        Returns:
            LLM响应对象，失败时返回None
        """
        # 更新统计
        self._api_call_stats["fallback_api_calls"] += 1
        
        # 获取AstrBot上下文
        context = self.context_getter()
        if not context:
            raise RuntimeError("无法获取AstrBot上下文")
        
        provider = context.get_using_provider(umo=session_id)
        if not provider:
            raise RuntimeError("未找到LLM Provider")
        
        llm_response_obj = await provider.text_chat(
            prompt=prompt,
            contexts=contexts,
            system_prompt=system_prompt,
        )
        
        if self.logger:
            self.logger.info("[主动消息] 使用传统API回退成功喵。")
        
        return llm_response_obj
    
    def construct_proactive_prompt(self, motivation_template: str, 
                                 unanswered_count: int, current_time: str) -> str:
        """
        构造主动消息Prompt
        
        Args:
            motivation_template: 动机模板
            unanswered_count: 未回复次数
            current_time: 当前时间字符串
            
        Returns:
            构造好的Prompt
        """
        prompt = motivation_template.replace(
            "{{unanswered_count}}", str(unanswered_count)
        ).replace("{{current_time}}", current_time)
        
        if self.logger:
            self.logger.debug(f"[主动消息] 构造主动消息Prompt喵: 未回复次数={unanswered_count}, 时间={current_time}")
        
        return prompt
    
    def parse_llm_response(self, llm_response_obj) -> Optional[str]:
        """
        解析LLM响应
        
        Args:
            llm_response_obj: LLM响应对象
            
        Returns:
            响应文本，失败时返回None
        """
        if llm_response_obj and hasattr(llm_response_obj, 'completion_text'):
            response_text = llm_response_obj.completion_text
            if response_text:
                response_text = response_text.strip()
                if self.logger:
                    self.logger.info(f"[主动消息] LLM 已生成文本喵: '{response_text}'。")
                return response_text
            else:
                if self.logger:
                    self.logger.warning("[主动消息] LLM响应内容为空喵")
                return None
        else:
            if self.logger:
                self.logger.warning("[主动消息] LLM响应对象无效或缺少completion_text属性喵")
            return None
    
    async def archive_conversation(self, conv_id: str, user_message: Dict[str, Any], 
                                 assistant_message: Dict[str, Any]) -> bool:
        """
        存档对话历史
        
        Args:
            conv_id: 对话ID
            user_message: 用户消息对象
            assistant_message: 助手消息对象
            
        Returns:
            是否成功存档
        """
        try:
            # 获取AstrBot上下文
            context = self.context_getter()
            if not context:
                raise RuntimeError("无法获取AstrBot上下文")
            
            await context.conversation_manager.add_message_pair(
                cid=conv_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )
            
            if self.logger:
                self.logger.info(
                    "[主动消息] 已成功将本次主动对话存档至对话历史喵 (使用新的add_message_pair API)。"
                )
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 存档对话历史失败喵: {e}")
                self.logger.warning("[主动消息] 对话存档失败喵，但会继续执行后续步骤喵。")
            raise RuntimeError(f"存档对话历史失败: {e}")
    
    def get_api_stats(self) -> Dict[str, Any]:
        """
        获取API调用统计
        
        Returns:
            API调用统计字典
        """
        return self._api_call_stats.copy()
    
    def reset_api_stats(self) -> bool:
        """
        重置API调用统计
        
        Returns:
            是否成功重置
        """
        self._api_call_stats = {
            "new_api_calls": 0,
            "fallback_api_calls": 0,
            "total_calls": 0,
            "errors": 0
        }
        
        if self.logger:
            self.logger.info("[主动消息] API调用统计已重置喵")
        
        return True
    
    def validate_llm_response(self, llm_response_obj) -> Dict[str, Any]:
        """
        验证LLM响应的有效性
        
        Args:
            llm_response_obj: LLM响应对象
            
        Returns:
            验证结果字典
        """
        issues = []
        
        if not llm_response_obj:
            issues.append("LLM响应对象为空")
            return {"valid": False, "issues": issues}
        
        if not hasattr(llm_response_obj, 'completion_text'):
            issues.append("LLM响应对象缺少completion_text属性")
        else:
            completion_text = getattr(llm_response_obj, 'completion_text', '')
            if not completion_text or not completion_text.strip():
                issues.append("LLM响应内容为空或仅包含空白字符")
            elif len(completion_text.strip()) < 2:
                issues.append("LLM响应内容过短")
        
        return {
            "valid": len(issues) == 0,
            "issues": issues
        }
    
    def get_llm_client_info(self) -> Dict[str, Any]:
        """
        获取LLM客户端信息
        
        Returns:
            LLM客户端信息字典
        """
        return {
            "api_stats": self._api_call_stats.copy(),
            "context_getter_available": callable(self.context_getter),
            "logger_available": self.logger is not None
        }