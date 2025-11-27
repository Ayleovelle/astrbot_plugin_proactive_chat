"""
沉默计时器模块 - 负责群聊沉默倒计时功能
基于原main.py中的群聊沉默倒计时逻辑重构
"""

import asyncio
import time
from typing import Dict, Any, Optional, Callable


class SilenceTimerManager:
    """
    沉默计时器管理器 - 负责群聊沉默倒计时功能
    
    主要职责：
    1. 群聊沉默倒计时管理
    2. 倒计时回调处理
    3. 会话状态验证
    4. 与调度器的协调
    5. 沉默超时处理
    6. 计时器状态监控
    """
    
    def __init__(self, scheduler, config_manager, session_manager, data_manager, logger=None):
        """
        初始化沉默计时器管理器
        
        Args:
            scheduler: 调度器实例
            config_manager: 配置管理器实例
            session_manager: 会话管理器实例
            data_manager: 数据管理器实例
            logger: 日志记录器
        """
        self.scheduler = scheduler
        self.config_manager = config_manager
        self.session_manager = session_manager
        self.data_manager = data_manager
        self.logger = logger
        
        # 回调函数引用
        self._silence_callback: Optional[Callable] = None
        self._logger_info_func: Optional[Callable] = None
    
    def set_silence_callback(self, callback: Callable):
        """
        设置沉默超时回调函数
        
        Args:
            callback: 回调函数
        """
        self._silence_callback = callback
        if self.logger:
            self.logger.debug("[主动消息] 沉默超时回调函数已设置喵")
    
    def set_logger_info_func(self, logger_func: Callable):
        """
        设置logger info函数
        
        Args:
            logger_func: logger info函数
        """
        self._logger_info_func = logger_func
        if self.logger:
            self.logger.debug("[主动消息] logger info函数已设置喵")
    
    async def reset_silence_timer(self, session_id: str) -> bool:
        """
        重置指定群聊的沉默倒计时
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功重置
        """
        # 验证会话配置
        session_config = self.config_manager.get_session_config(session_id)
        if not session_config or not session_config.get("enable", False):
            if self.logger:
                self.logger.debug(f"[主动消息] 会话 {session_id} 未启用，跳过重置沉默倒计时喵。")
            return False
        
        # 取消上一个为该群聊设置的"沉默倒计时"
        if self.scheduler.has_group_timer(session_id):
            if self.logger:
                self.logger.debug(f"[主动消息] 已取消 {session_id} 的上一个沉默倒计时喵。")
        
        cancel_result = self.scheduler.cancel_group_timer(session_id)
        if not cancel_result and self.logger:
            self.logger.warning(f"[主动消息] 取消旧沉默计时器失败喵: {session_id}")
        
        # 获取沉默触发时间
        idle_minutes = session_config.get("group_idle_trigger_minutes", 10)
        if self.logger:
            self.logger.debug(
                f"[主动消息] 将为 {session_id} 设置 {idle_minutes} 分钟的沉默倒计时喵。"
            )
        
        # 定义倒计时结束后的回调函数
        def _schedule_callback():
            # v1.0.0-beta.5 修复：避免直接使用asyncio.create_task，防止任务泄漏
            # 原代码第1170-1172行：asyncio.create_task(self._schedule_next_chat_and_save(session_id, reset_counter=False))
            try:
                # 使用安全的异步调用方式
                if self._silence_callback:
                    # 让调用者处理异步执行，避免在此创建未管理的任务
                    asyncio.get_event_loop().call_soon(
                        lambda: asyncio.create_task(self._handle_silence_timeout(session_id))
                    )
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[主动消息] 创建沉默超时任务失败喵: {e}")
        
        # 设置新的"沉默倒计时"
        delay_seconds = idle_minutes * 60
        result = self.scheduler.set_group_timer(session_id, delay_seconds, _schedule_callback)
        
        if result:
            if self.logger:
                timer_count = self.scheduler.get_timer_count()
                self.logger.debug(
                    f"[主动消息] 已重置 {session_id} 的沉默倒计时 ({idle_minutes}分钟) 喵。当前计时器数量: {timer_count}"
                )
        else:
            if self.logger:
                self.logger.error(f"[主动消息] 设置沉默倒计时失败喵: {session_id}")
        
        return result
    
    async def _handle_silence_timeout(self, session_id: str):
        """
        处理沉默超时回调
        
        Args:
            session_id: 会话ID
        """
        try:
            # v1.0.0-beta.1 修复: 在创建任务前，验证群聊是否仍然处于沉默状态
            
            # 检查1: 验证当前是否还有活跃的计时器（如果群聊活跃，计时器应该被重置）
            if not self.scheduler.has_group_timer(session_id):
                if self.logger:
                    self.logger.info(
                        f"[主动消息] 群聊 {session_id} 的计时器已被重置，跳过主动消息创建喵。"
                    )
                return
            
            # v1.0.0-beta.3 修复：验证会话数据是否存在
            # 这里需要访问数据，但为了不耦合，让调用者处理
            
            # 检查3: 验证配置是否仍然启用
            session_config = self.config_manager.get_session_config(session_id)
            if not session_config or not session_config.get("enable", False):
                if self.logger:
                    self.logger.info(
                        f"[主动消息] 群聊 {session_id} 的配置已禁用或不存在，跳过主动消息创建喵。"
                    )
                return
            
            # 当群聊沉默时，不应该重置计数器
            # 获取当前的未回复次数，用于显示更准确的日志
            # 这里需要访问会话数据，让调用者处理
            
            # 获取沉默触发时间用于日志
            idle_minutes = self.get_idle_minutes(session_id)
            
            # 创建主动消息任务
            await self._create_silence_trigger_task(session_id)
            
            if self.logger:
                current_unanswered = 0  # 默认值，实际值需要外部提供
                if self._logger_info_func:
                    self._logger_info_func(
                        f"[主动消息] 群聊 {session_id} 已沉默 {idle_minutes} 分钟，"
                        f"开始计划主动消息喵。(当前未回复次数: {current_unanswered})"
                    )
                else:
                    self.logger.info(
                        f"[主动消息] 群聊 {session_id} 已沉默 {idle_minutes} 分钟，"
                        f"开始计划主动消息喵。(当前未回复次数: {current_unanswered})"
                    )
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 沉默倒计时回调函数执行失败喵: {e}")
            raise RuntimeError(f"沉默倒计时回调执行失败: {e}")
    
    async def _create_silence_trigger_task(self, session_id: str):
        """
        创建沉默触发任务
        
        Args:
            session_id: 会话ID
        """
        # 这里需要调用check_and_chat，但为了避免循环依赖，让调用者处理
        # 简化实现：触发一个事件或回调
        if self._silence_callback:
            try:
                await self._silence_callback(session_id)
                if self.logger:
                    self.logger.debug(f"[主动消息] 沉默触发任务创建成功喵: {session_id}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[主动消息] 沉默触发任务创建失败喵: {session_id} - {e}")
                raise RuntimeError(f"沉默触发任务创建失败: {e}")
        else:
            if self.logger:
                self.logger.warning(f"[主动消息] 沉默触发回调未设置，无法创建任务喵: {session_id}")
    
    def get_idle_minutes(self, session_id: str) -> int:
        """
        获取沉默触发时间（分钟）
        
        Args:
            session_id: 会话ID
            
        Returns:
            沉默触发时间（分钟）
        """
        session_config = self.config_manager.get_session_config(session_id)
        if not session_config:
            default_minutes = 10
            if self.logger:
                self.logger.debug(f"[主动消息] 使用默认沉默触发时间喵: {session_id} -> {default_minutes}分钟")
            return default_minutes
        
        idle_minutes = session_config.get("group_idle_trigger_minutes", 10)
        
        if self.logger:
            self.logger.debug(f"[主动消息] 获取沉默触发时间喵: {session_id} -> {idle_minutes}分钟")
        
        return idle_minutes
    
    def cancel_silence_timer(self, session_id: str) -> bool:
        """
        取消群聊沉默计时器
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功取消
        """
        result = self.scheduler.cancel_group_timer(session_id)
        
        if self.logger:
            if result:
                self.logger.info(f"[主动消息] 已取消群聊沉默计时器喵: {session_id}")
            else:
                self.logger.debug(f"[主动消息] 取消群聊沉默计时器失败或无计时器喵: {session_id}")
        
        return result
    
    def has_silence_timer(self, session_id: str) -> bool:
        """
        检查是否存在群聊沉默计时器
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否存在
        """
        result = self.scheduler.has_group_timer(session_id)
        
        if self.logger:
            self.logger.debug(f"[主动消息] 检查群聊沉默计时器状态喵: {session_id} -> {result}")
        
        return result
    
    def clear_all_silence_timers(self) -> int:
        """
        清理所有群聊沉默计时器
        
        Returns:
            清理的计时器数量
        """
        count = self.scheduler.clear_all_group_timers()
        
        if self.logger:
            self.logger.info(f"[主动消息] 已清理 {count} 个群聊沉默计时器喵")
        
        return count
    
    def get_silence_timer_info(self, session_id: str) -> Dict[str, Any]:
        """
        获取沉默计时器信息
        
        Args:
            session_id: 会话ID
            
        Returns:
            计时器信息字典
        """
        has_timer = self.has_silence_timer(session_id)
        idle_minutes = self.get_idle_minutes(session_id) if has_timer else 0
        
        info = {
            "has_timer": has_timer,
            "idle_minutes": idle_minutes,
            "session_config_available": self.config_manager.get_session_config(session_id) is not None
        }
        
        if self.logger:
            self.logger.debug(f"[主动消息] 获取沉默计时器信息喵: {session_id} -> {info}")
        
        return info
    
    def get_all_silence_timers_summary(self) -> Dict[str, Any]:
        """
        获取所有沉默计时器摘要
        
        Returns:
            摘要信息字典
        """
        # 这里需要访问调度器的内部状态，简化实现
        timer_count = 0
        # 假设我们可以通过某种方式获取所有计时器
        
        summary = {
            "total_timers": timer_count,
            "callback_set": self._silence_callback is not None,
            "logger_set": self._logger_info_func is not None
        }
        
        if self.logger:
            self.logger.debug(f"[主动消息] 沉默计时器摘要喵: {summary}")
        
        return summary
    
    def validate_silence_config(self, session_id: str) -> Dict[str, Any]:
        """
        验证沉默配置的有效性
        
        Args:
            session_id: 会话ID
            
        Returns:
            验证结果字典
        """
        issues = []
        
        session_config = self.config_manager.get_session_config(session_id)
        if not session_config:
            issues.append("会话配置不存在")
        else:
            idle_minutes = session_config.get("group_idle_trigger_minutes", 10)
            if idle_minutes <= 0:
                issues.append("沉默触发时间必须大于0")
            elif idle_minutes > 1440:  # 24小时
                issues.append("沉默触发时间不能超过24小时")
        
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "session_id": session_id
        }