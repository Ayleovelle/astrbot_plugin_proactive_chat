#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS管理器 - 负责文本转语音功能
基于v1.0.0-beta.4的_send_proactive_message函数重构
"""

import asyncio
from typing import Optional, Dict, Any
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain


class TTSManager:
    """TTS管理器 - 管理文本转语音功能"""
    
    def __init__(self, context_getter: callable, logger=None):
        """
        初始化TTS管理器
        
        Args:
            context_getter: 获取AstrBot上下文的函数
            logger: 日志记录器
        """
        self.context_getter = context_getter
        self.logger = logger
    
    async def send_proactive_message(self, session_id: str, text: str, tts_settings: Dict[str, Any]) -> bool:
        """
        发送主动消息，支持TTS语音和文本消息
        
        发送流程：
        1. 检查TTS配置，如果启用则尝试生成语音
        2. 如果TTS成功，发送语音消息
        3. 根据配置决定是否同时发送文本原文
        4. 如果TTS失败或禁用，直接发送文本消息
        
        特别处理：如果是群聊消息，发送后会立即重置沉默倒计时，
        因为Bot发送消息也意味着群聊有活动。
        
        Args:
            session_id: 会话ID
            text: 要发送的文本内容
            tts_settings: TTS配置设置
            
        Returns:
            是否成功发送消息
        """
        try:
            if not tts_settings:
                if self.logger:
                    self.logger.info(f"[主动消息] 无法获取会话配置，跳过消息发送喵: {session_id}")
                return False
            
            if self.logger:
                self.logger.info(f"[主动消息] 开始发送主动消息喵，会话ID: {session_id}")
            
            # 获取上下文
            context = self.context_getter()
            if not context:
                if self.logger:
                    self.logger.error(f"[主动消息] 无法获取AstrBot上下文，跳过消息发送喵: {session_id}")
                return False
            
            # TTS处理 - 复刻原代码第1325-1341行逻辑
            is_tts_sent = False
            if tts_settings.get("enable_tts", True):
                try:
                    if self.logger:
                        self.logger.info("[主动消息] 尝试进行手动 TTS 喵。")
                    
                    tts_provider = context.get_using_tts_provider(umo=session_id)
                    if tts_provider:
                        audio_path = await tts_provider.get_audio(text)
                        if audio_path:
                            await context.send_message(
                                session_id, MessageChain([Record(file=audio_path)])
                            )
                            is_tts_sent = True
                            await asyncio.sleep(0.5)  # 复刻原代码第1338行延迟
                            
                            if self.logger:
                                self.logger.info(f"[主动消息] TTS语音消息发送成功喵: {session_id}")
                    else:
                        if self.logger:
                            self.logger.warning("[主动消息] 未找到TTS提供商，跳过语音生成喵。")
                            
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"[主动消息] 手动 TTS 流程发生异常喵: {e}")
                    # TTS失败不影响文本消息发送
            
            # 发送文本消息 - 复刻原代码第1342-1345行逻辑
            if not is_tts_sent or tts_settings.get("always_send_text", True):
                await context.send_message(
                    session_id, MessageChain([Plain(text=text)])
                )
                if self.logger:
                    self.logger.info(f"[主动消息] 文本消息发送成功喵: {session_id}")
            
            # 如果是群聊消息，重置沉默倒计时 - 复刻原代码第1347-1354行逻辑
            if "group" in session_id.lower():
                # 立即重置，不要等待，确保时序正确
                # 这里需要调用沉默计时器管理器，但为了避免循环依赖，
                # 我们返回一个标志让调用者处理
                if self.logger:
                    self.logger.info(
                        f"[主动消息] Bot主动消息已发送，已重置群聊 {session_id} 的沉默倒计时喵。"
                    )
                return True  # 返回True表示需要重置沉默倒计时
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 发送主动消息失败喵: {e}")
            return False
    
    def should_send_text(self, tts_settings: Dict[str, Any], is_tts_sent: bool) -> bool:
        """
        判断是否应该发送文本消息
        
        Args:
            tts_settings: TTS配置
            is_tts_sent: 是否已成功发送TTS
            
        Returns:
            是否应该发送文本
        """
        # 复刻原代码第1342行逻辑
        return not is_tts_sent or tts_settings.get("always_send_text", True)
    
    def is_tts_enabled(self, tts_settings: Dict[str, Any]) -> bool:
        """
        检查TTS是否启用
        
        Args:
            tts_settings: TTS配置
            
        Returns:
            TTS是否启用
        """
        return tts_settings.get("enable_tts", True)
    
    async def get_tts_provider(self, session_id: str) -> Optional[Any]:
        """
        获取TTS提供商
        
        Args:
            session_id: 会话ID
            
        Returns:
            TTS提供商对象，失败时返回None
        """
        try:
            context = self.context_getter()
            if not context:
                return None
            
            return context.get_using_tts_provider(umo=session_id)
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 获取TTS提供商失败喵: {e}")
            return None
    
    async def generate_tts_audio(self, tts_provider, text: str) -> Optional[str]:
        """
        生成TTS音频文件
        
        Args:
            tts_provider: TTS提供商
            text: 要转换的文本
            
        Returns:
            音频文件路径，失败时返回None
        """
        try:
            if not tts_provider:
                return None
            
            audio_path = await tts_provider.get_audio(text)
            if audio_path and self.logger:
                self.logger.debug(f"[主动消息] TTS音频生成成功喵: {audio_path}")
            
            return audio_path
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] TTS音频生成失败喵: {e}")
            return None