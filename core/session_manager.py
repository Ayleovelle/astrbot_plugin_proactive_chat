#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会话管理器 - 负责会话状态管理
基于v1.0.0-beta.4的会话状态管理逻辑重构
"""

import time
from typing import Dict, Any, Optional
from datetime import datetime


class SessionManager:
    """会话管理器 - 管理会话状态和临时数据"""
    
    def __init__(self, logger=None):
        """
        初始化会话管理器
        
        Args:
            logger: 日志记录器
        """
        self.logger = logger
        
        # v1.0.0-beta.4 修复: 使用会话隔离的状态管理，避免竞态条件
        # 将全局状态改为以session_id为键的字典，确保每个群聊会话状态独立
        self.session_temp_state: Dict[str, Dict] = {}  # 存储每个会话的临时状态
        
        # v1.0.0-beta.1 修复: 用于辅助检测Bot消息的时间戳
        # 记录最后一次检测到Bot消息的时间，用于时间窗口检测算法
        self.last_bot_message_time = 0
        
        # v1.0.0-beta.2 新增: 用于控制相关日志只打印一次
        self.first_message_logged: set[str] = set()  # 记录已经打印过首次消息日志的会话
        
        # v1.0.0-beta.2 新增: 记录每个会话的最后消息时间
        self.last_message_times: Dict[str, float] = {}  # 记录每个会话的最后消息时间
        
        # 插件启动时间
        self.plugin_start_time = time.time()
    
    def record_message_time(self, session_id: str) -> float:
        """
        记录消息时间
        
        Args:
            session_id: 会话ID
            
        Returns:
            记录的时间戳
        """
        current_time = time.time()
        self.last_message_times[session_id] = current_time
        
        if self.logger:
            self.logger.debug(f"[主动消息] 记录消息时间喵: {session_id} -> {current_time}")
        
        return current_time
    
    def get_last_message_time(self, session_id: str) -> float:
        """
        获取会话的最后消息时间
        
        Args:
            session_id: 会话ID
            
        Returns:
            最后消息时间戳，不存在时返回0
        """
        return self.last_message_times.get(session_id, 0)
    
    def set_session_temp_state(self, session_id: str, state_data: Dict[str, Any]) -> None:
        """
        设置会话临时状态
        
        Args:
            session_id: 会话ID
            state_data: 状态数据
        """
        self.session_temp_state[session_id] = state_data
        
        if self.logger:
            self.logger.debug(
                f"[主动消息] 设置会话临时状态喵: {session_id} -> {state_data}"
            )
    
    def get_session_temp_state(self, session_id: str) -> Dict[str, Any]:
        """
        获取会话临时状态
        
        Args:
            session_id: 会话ID
            
        Returns:
            状态数据，不存在时返回空字典
        """
        return self.session_temp_state.get(session_id, {})
    
    def has_session_temp_state(self, session_id: str) -> bool:
        """
        检查是否存在会话临时状态
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否存在临时状态
        """
        return session_id in self.session_temp_state
    
    def clear_session_temp_state(self, session_id: str) -> bool:
        """
        清理会话临时状态
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功清理
        """
        if session_id in self.session_temp_state:
            del self.session_temp_state[session_id]
            
            if self.logger:
                self.logger.debug(f"[主动消息] 清理会话临时状态喵: {session_id}")
            return True
        return False
    
    def record_bot_message_time(self) -> None:
        """
        记录Bot消息时间
        用于辅助检测Bot消息的时间窗口算法
        """
        self.last_bot_message_time = time.time()
        
        if self.logger:
            self.logger.debug(f"[主动消息] 记录Bot消息时间喵: {self.last_bot_message_time}")
    
    def get_bot_message_time(self) -> float:
        """
        获取最后一次Bot消息时间
        
        Returns:
            Bot消息时间戳
        """
        return self.last_bot_message_time
    
    def mark_first_message_logged(self, session_id: str) -> bool:
        """
        标记会话的首次消息已记录
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否是首次记录（True表示是首次，False表示已记录过）
        """
        if session_id not in self.first_message_logged:
            self.first_message_logged.add(session_id)
            return True
        return False
    
    def is_first_message_logged(self, session_id: str) -> bool:
        """
        检查会话的首次消息是否已记录
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否已记录过首次消息
        """
        return session_id in self.first_message_logged
    
    def get_plugin_start_time(self) -> float:
        """
        获取插件启动时间
        
        Returns:
            插件启动时间戳
        """
        return self.plugin_start_time
    
    def get_time_since_plugin_start(self) -> float:
        """
        获取插件运行时间（秒）
        
        Returns:
            插件运行时间（秒）
        """
        return time.time() - self.plugin_start_time
    
    def get_all_sessions_with_data(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有有数据的会话
        
        Returns:
            会话数据字典
        """
        sessions = {}
        
        # 合并所有会话数据
        for session_id in set(list(self.last_message_times.keys()) + 
                             list(self.session_temp_state.keys()) +
                             list(self.first_message_logged)):
            sessions[session_id] = {
                "last_message_time": self.last_message_times.get(session_id, 0),
                "has_temp_state": session_id in self.session_temp_state,
                "first_message_logged": session_id in self.first_message_logged,
                "plugin_start_time": self.plugin_start_time
            }
        
        return sessions
    
    def cleanup_session_data(self, session_id: str) -> bool:
        """
        清理会话的所有数据
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功清理
        """
        cleaned = False
        
        # 清理最后消息时间
        if session_id in self.last_message_times:
            del self.last_message_times[session_id]
            cleaned = True
        
        # 清理临时状态
        if session_id in self.session_temp_state:
            del self.session_temp_state[session_id]
            cleaned = True
        
        # 清理首次消息记录
        if session_id in self.first_message_logged:
            self.first_message_logged.discard(session_id)
            cleaned = True
        
        if cleaned and self.logger:
            self.logger.debug(f"[主动消息] 清理会话所有数据喵: {session_id}")
        
        return cleaned
    
    def get_session_info(self, session_id: str) -> Dict[str, Any]:
        """
        获取会话详细信息
        
        Args:
            session_id: 会话ID
            
        Returns:
            会话信息字典
        """
        return {
            "session_id": session_id,
            "last_message_time": self.get_last_message_time(session_id),
            "has_temp_state": self.has_session_temp_state(session_id),
            "first_message_logged": self.is_first_message_logged(session_id),
            "plugin_start_time": self.plugin_start_time,
            "time_since_plugin_start": self.get_time_since_plugin_start()
        }
    
    def check_time_window_for_bot_detection(self, session_id: str, time_window: float = 5.0) -> tuple[bool, float]:
        """
        检查时间窗口用于Bot消息检测
        
        Args:
            session_id: 会话ID
            time_window: 时间窗口（秒）
            
        Returns:
            (是否在时间窗口内, 距离上次用户消息的时间)
        """
        session_state = self.get_session_temp_state(session_id)
        last_user_time = session_state.get("last_user_time", 0)
        current_time = time.time()
        time_since_user = current_time - last_user_time
        
        is_in_window = last_user_time > 0 and time_since_user < time_window
        
        if self.logger:
            self.logger.debug(
                f"[主动消息] Bot检测时间窗口检查喵: 会话={session_id}, "
                f"最后用户时间={last_user_time}, 当前时间={current_time}, "
                f"时间差={time_since_user:.2f}秒, 是否在窗口内={is_in_window}"
            )
        
        return is_in_window, time_since_user
    
    def get_session_summary(self) -> Dict[str, Any]:
        """
        获取会话摘要信息
        
        Returns:
            会话摘要字典
        """
        return {
            "total_sessions_with_data": len(self.last_message_times),
            "total_sessions_with_temp_state": len(self.session_temp_state),
            "total_first_messages_logged": len(self.first_message_logged),
            "plugin_start_time": self.plugin_start_time,
            "plugin_runtime_seconds": self.get_time_since_plugin_start(),
            "last_bot_message_time": self.last_bot_message_time
        }
    
    def reset_all_sessions(self) -> int:
        """
        重置所有会话数据
        
        Returns:
            重置的会话数量
        """
        count = 0
        
        # 清理所有会话数据
        all_sessions = set(list(self.last_message_times.keys()) + 
                          list(self.session_temp_state.keys()) +
                          list(self.first_message_logged))
        
        for session_id in all_sessions:
            if self.cleanup_session_data(session_id):
                count += 1
        
        if self.logger:
            self.logger.info(f"[主动消息] 已重置 {count} 个会话的所有数据喵。")
        
        return count
    
    def validate_session_state(self, session_id: str) -> Dict[str, Any]:
        """
        验证会话状态的有效性
        
        Args:
            session_id: 会话ID
            
        Returns:
            验证结果字典
        """
        issues = []
        
        # 检查最后消息时间
        last_time = self.get_last_message_time(session_id)
        if last_time < 0:
            issues.append("最后消息时间为负数")
        
        # 检查临时状态
        temp_state = self.get_session_temp_state(session_id)
        if temp_state:
            last_user_time = temp_state.get("last_user_time", 0)
            if last_user_time < 0:
                issues.append("临时状态中的用户时间为负数")
        
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "session_id": session_id
        }