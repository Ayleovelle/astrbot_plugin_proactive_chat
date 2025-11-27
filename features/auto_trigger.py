#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动触发管理器 - 完整复刻v1.0.0-beta.4的自动触发逻辑
"""

import asyncio
import time
import random
from datetime import datetime
from typing import Dict, Any, Optional, List


class AutoTriggerManager:
    """自动触发管理器 - 管理自动主动消息触发功能"""
    
    def __init__(self, scheduler, config_manager, session_manager, plugin_start_time, logger=None):
        """
        初始化自动触发管理器
        
        Args:
            scheduler: 调度器实例
            config_manager: 配置管理器实例
            session_manager: 会话管理器实例
            plugin_start_time: 插件启动时间
            logger: 日志记录器
        """
        self.scheduler = scheduler
        self.config_manager = config_manager
        self.session_manager = session_manager
        self.plugin_start_time = plugin_start_time
        self.logger = logger
        
        # 自动主动消息功能的数据结构
        self.auto_trigger_timers: Dict[str, asyncio.TimerHandle] = {}  # 自动触发计时器
    
    async def setup_auto_trigger(self, session_id: str) -> bool:
        """
        为指定会话设置自动主动消息触发器
        
        这是自动主动消息功能的核心方法，负责：
        1. 检查会话是否启用了自动触发功能
        2. 设置自动触发计时器
        3. 当计时器到期时，创建主动消息任务（不是直接发送消息）
        
        注意：这个功能只在插件启动后的一段时间内有效，一旦收到消息就会取消自动触发。
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功设置自动触发器
        """
        try:
            session_config = self.config_manager.get_session_config(session_id)
            if not session_config:
                if self.logger:
                    self.logger.debug(f"[主动消息] 会话 {session_id} 未启用，跳过自动触发器设置喵。")
                return False
            
            auto_trigger_settings = session_config.get("auto_trigger_settings", {})
            
            # 检查是否启用了自动触发功能
            if not auto_trigger_settings.get("enable_auto_trigger", False):
                if self.logger:
                    self.logger.debug(f"[主动消息] 会话 {session_id} 未启用自动主动消息功能喵。")
                return False
            
            auto_trigger_minutes = auto_trigger_settings.get("auto_trigger_after_minutes", 5)
            if auto_trigger_minutes <= 0:
                if self.logger:
                    self.logger.debug(
                        f"[主动消息] 会话 {session_id} 的自动触发时间设置为0，禁用自动触发喵。"
                    )
                return False
            
            # 取消现有的自动触发计时器
            if session_id in self.auto_trigger_timers:
                try:
                    self.auto_trigger_timers[session_id].cancel()
                    if self.logger:
                        self.logger.debug(
                            f"[主动消息] 已取消会话 {session_id} 的现有自动触发计时器喵。"
                        )
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"[主动消息] 取消自动触发计时器时出错喵: {e}")
                finally:
                    del self.auto_trigger_timers[session_id]
            
            # 定义自动触发回调函数
            def _auto_trigger_callback():
                try:
                    # 检查是否仍然需要自动触发（避免重复触发）
                    if session_id not in self.auto_trigger_timers:
                        if self.logger:
                            self.logger.debug(
                                f"[主动消息] 会话 {session_id} 的自动触发已被取消，跳过喵。"
                            )
                        return
                    
                    # 检查配置是否仍然有效
                    current_config = self.config_manager.get_session_config(session_id)
                    if not current_config or not current_config.get("enable", False):
                        if self.logger:
                            self.logger.info(
                                f"[主动消息] 会话 {session_id} 的配置已禁用，取消自动触发喵。"
                            )
                        return
                    
                    # 检查是否已经有活动（收到过消息）
                    last_message_time = self.session_manager.get_last_message_time(session_id)
                    current_time = time.time()
                    time_since_plugin_start = current_time - self.plugin_start_time
                    
                    # 调试信息：帮助理解自动触发条件判断
                    if self.logger:
                        self.logger.debug(
                            f"[主动消息] 自动触发检查 - 会话: {session_id}, "
                            f"最后消息时间: {last_message_time}, "
                            f"插件启动时间: {self.plugin_start_time}, "
                            f"当前时间: {current_time}, "
                            f"插件运行时间: {time_since_plugin_start:.0f}秒, "
                            f"需要等待时间: {auto_trigger_minutes * 60}秒"
                        )
                    
                    # 只有在插件启动后且没有收到过消息时才触发
                    if last_message_time == 0 and time_since_plugin_start >= (auto_trigger_minutes * 60):
                        if self.logger:
                            self.logger.info(
                                f"[主动消息] 🚀 会话 {session_id} 满足自动触发条件，创建主动消息任务喵！"
                            )
                        
                        # 重要：创建任务而不是直接发送消息，但避免持久化
                        # 自动触发的任务不应该被持久化，避免与正常任务冲突
                        try:
                            # 获取调度回调函数
                            check_and_chat_func = getattr(self, '_get_check_and_chat_func', None)
                            if not check_and_chat_func or not callable(check_and_chat_func):
                                if self.logger:
                                    self.logger.error(
                                        f"[主动消息] 无法获取check_and_chat函数，取消自动触发喵: {session_id}"
                                    )
                                return
                            
                            # 获取会话配置
                            session_config = self.config_manager.get_session_config(session_id)
                            if not session_config:
                                if self.logger:
                                    self.logger.warning(
                                        f"[主动消息] 无法获取会话配置，取消自动触发喵: {session_id}"
                                    )
                                return
                            
                            # v1.0.0-beta.5 修复：自动触发时重置计数器，确保第一次消息的计数器正确
                            # 自动触发应该是第一次主动消息，计数器应该从0开始
                            if self.logger:
                                self.logger.info(
                                    f"[主动消息] 自动触发即将重置会话 {session_id} 的计数器为0喵。"
                                )
                            
                            # 创建特殊的回调函数，在调用check_and_chat前重置计数器
                            def _auto_trigger_wrapper():
                                try:
                                    # v1.0.0-beta.5 修复：避免创建未管理的异步任务
                                    # 重置计数器为0，确保自动触发是第一次消息
                                    if hasattr(self, '_get_data_manager'):
                                        data_manager = self._get_data_manager()
                                        if data_manager:
                                            # 使用调度器来管理异步任务，避免直接创建任务
                                            # 让调用者处理异步执行，避免在此创建未管理的任务
                                            asyncio.get_event_loop().call_soon(
                                                lambda: self._reset_counter_for_auto_trigger(session_id)
                                            )
                                     
                                    # 调用原始的check_and_chat函数
                                    check_and_chat_func = self._get_check_and_chat_func()
                                    if check_and_chat_func:
                                        # 传递特殊标记，表示这是自动触发
                                        return check_and_chat_func(session_id, is_auto_trigger=True)
                                except Exception as e:
                                    if self.logger:
                                        self.logger.error(f"[主动消息] 自动触发包装器失败喵: {e}")
                                    # 回退到原始函数
                                    check_and_chat_func = self._get_check_and_chat_func()
                                    if check_and_chat_func:
                                        return check_and_chat_func(session_id)
                            
                            # 计算调度时间
                            schedule_conf = session_config.get("schedule_settings", {})
                            min_interval = int(schedule_conf.get("min_interval_minutes", 30)) * 60
                            max_interval = max(
                                min_interval,
                                int(schedule_conf.get("max_interval_minutes", 900)) * 60,
                            )
                            random_interval = random.randint(min_interval, max_interval)
                            next_trigger_time = time.time() + random_interval
                            run_date = datetime.fromtimestamp(next_trigger_time)
                            
                            # 直接添加到调度器，但不保存到session_data
                            check_and_chat_func = check_and_chat_func()
                            if check_and_chat_func:
                                self.scheduler.add_job(
                                    _auto_trigger_wrapper,  # 使用包装器函数
                                    "date",
                                    run_date=run_date,
                                    args=[],
                                    job_id=f"auto_trigger_{session_id}",  # 使用不同的job_id避免冲突
                                    replace_existing=True,
                                    misfire_grace_time=60,
                                )
                                
                                if self.logger:
                                    self.logger.info(
                                        f"[主动消息] 自动触发任务已创建喵: {session_id}, "
                                        f"执行时间 (非持久化): {run_date.strftime('%Y-%m-%d %H:%M:%S')} 喵"
                                    )
                            else:
                                if self.logger:
                                    self.logger.error(
                                        f"[主动消息] 无法获取check_and_chat函数，任务创建失败喵: {session_id}"
                                    )
                        
                        except Exception as e:
                            if self.logger:
                                self.logger.error(f"[主动消息] 自动触发任务创建失败喵: {e}")
                        
                        # 清理自动触发计时器（只触发一次）
                        if session_id in self.auto_trigger_timers:
                            del self.auto_trigger_timers[session_id]
                    
                    else:
                        if self.logger:
                            self.logger.debug(
                                f"[主动消息] 会话 {session_id} 不满足自动触发条件喵："
                                f"最后消息时间={last_message_time}, 插件启动时间={self.plugin_start_time}"
                            )
                
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"[主动消息] 自动触发回调函数执行失败喵: {e}")
            
            # 设置自动触发计时器
            try:
                loop = asyncio.get_running_loop()
                # 转换分钟为秒，设置延迟调用
                delay_seconds = auto_trigger_minutes * 60
                
                self.auto_trigger_timers[session_id] = loop.call_later(
                    delay_seconds, _auto_trigger_callback
                )
                
                if self.logger:
                    self.logger.info(
                        f"[主动消息] 已为会话 {session_id} 设置自动主动消息触发器喵，"
                        f"将在 {auto_trigger_minutes} 分钟后检查是否需要自动触发喵。"
                    )
                
                return True
                
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[主动消息] 设置自动触发计时器失败喵: {e}")
                return False
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 设置自动触发器时发生异常喵: {e}")
            return False
    
    async def cancel_auto_trigger(self, session_id: str) -> bool:
        """
        取消指定会话的自动主动消息触发器
        
        当收到消息时调用，确保不会重复触发。
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功取消
        """
        if session_id in self.auto_trigger_timers:
            try:
                self.auto_trigger_timers[session_id].cancel()
                if self.logger:
                    self.logger.debug(f"[主动消息] 已取消会话 {session_id} 的自动触发计时器喵。")  # 改为debug级别，避免刷屏
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[主动消息] 取消自动触发计时器时出错喵: {e}")
            finally:
                del self.auto_trigger_timers[session_id]
                return True
        else:
            # v1.0.0-beta.5 修复：计时器不存在时不记录日志，避免混淆
            if self.logger:
                self.logger.debug(f"[主动消息] 会话 {session_id} 没有自动触发计时器，无需取消喵。")
            return False
    
    def cleanup_invalid_session_data(self, session_data: Dict[str, Any]) -> int:
        """
        清理无效的会话数据
        
        清理无效的会话数据，包括：
        1. 删除通用格式的会话ID（如 private_message:xxx, group_message:xxx）
        2. 这些是由早期版本的自动触发功能错误创建的
        
        Args:
            session_data: 会话数据字典
            
        Returns:
            清理的条目数量
        """
        cleaned_count = 0
        invalid_sessions = []
        
        for session_id in list(session_data.keys()):
            # 检查是否是通用格式的错误会话ID
            if session_id.startswith("private_message:") or session_id.startswith("group_message:"):
                invalid_sessions.append(session_id)
                cleaned_count += 1
        
        # 删除无效的会话数据
        for session_id in invalid_sessions:
            del session_data[session_id]
            if self.logger:
                self.logger.info(f"[主动消息] 清理了无效的会话数据喵: {session_id}")
        
        return cleaned_count
    
    async def setup_auto_triggers_for_enabled_sessions(self, enabled_sessions: List[str]) -> int:
        """
        为所有启用了自动触发功能的会话设置自动主动消息触发器
        
        在插件初始化时调用，完整复刻原代码的所有逻辑。
        
        Args:
            enabled_sessions: 启用的会话列表
            
        Returns:
            设置的自动触发器数量
        """
        if self.logger:
            self.logger.info("[主动消息] 开始检查并设置自动主动消息触发器喵...")
        
        auto_trigger_count = 0
        
        # 检查私聊配置
        private_settings = self.config_manager.config.get("private_settings", {})
        if private_settings.get("enable", False):
            auto_trigger_settings = private_settings.get("auto_trigger_settings", {})
            if auto_trigger_settings.get("enable_auto_trigger", False):
                target_user_id = str(private_settings.get("target_user_id", "")).strip()
                if target_user_id:
                    # 检查是否已经有持久化的主动消息任务
                    has_existing_task = False
                    current_time = time.time()
                    
                    # 获取会话数据
                    session_data = self.session_manager.get_all_sessions_with_data()
                    for existing_session_id, session_info in session_data.items():
                        if (
                            session_info.get("next_trigger_time")
                            and f"FriendMessage:{target_user_id}" in existing_session_id
                        ):
                            next_trigger = session_info.get("next_trigger_time")
                            # 检查任务是否过期（给1分钟宽限期，与恢复逻辑保持一致）
                            trigger_time_with_grace = next_trigger + 60
                            is_not_expired = current_time < trigger_time_with_grace
                            
                            if self.logger:
                                self.logger.debug(f"[主动消息] 检查私聊持久化任务喵: {existing_session_id}")
                                self.logger.debug(
                                    f"[主动消息] 触发时间喵: {next_trigger}, 当前时间喵: {current_time}"
                                )
                                self.logger.debug(f"[主动消息] 是否未过期喵: {is_not_expired}")
                            
                            if is_not_expired:
                                if self.logger:
                                    self.logger.debug(
                                        f"[主动消息] 找到有效的私聊持久化任务喵: {existing_session_id}"
                                    )
                                has_existing_task = True
                                break
                            else:
                                if self.logger:
                                    self.logger.debug(
                                        f"[主动消息] 私聊任务已过期，不视为有效任务喵: {existing_session_id}"
                                    )
                    
                    if has_existing_task:
                        if self.logger:
                            self.logger.info(
                                f"[主动消息] 私聊会话 {target_user_id} 已存在持久化的主动消息任务，"
                                f"跳过自动触发器设置以避免冲突喵。"
                            )
                    else:
                        # 使用FriendMessage格式的会话ID，但需要先确定平台名称
                        # 从已有的会话数据中提取平台名称，如果没有则使用默认的"default"
                        platform_name = "default"
                        for existing_session_id in enabled_sessions:
                            if f"FriendMessage:{target_user_id}" in existing_session_id:
                                # 提取平台名称（第一部分）
                                platform_name = existing_session_id.split(":")[0]
                                break
                        
                        friend_session_id = f"{platform_name}:FriendMessage:{target_user_id}"
                        
                        # 设置自动触发器
                        success = await self.setup_auto_trigger(friend_session_id)
                        if success:
                            auto_trigger_count += 1
                            if self.logger:
                                self.logger.info(
                                    f"[主动消息] 已为私聊会话 {target_user_id} 设置自动触发器喵。"
                                )
                else:
                    if self.logger:
                        self.logger.warning(
                            "[主动消息] 私聊启用了自动触发但未配置目标用户ID喵。"
                        )
            else:
                if self.logger:
                    self.logger.info("[主动消息] 私聊未启用自动主动消息功能喵。")
        
        # 检查群聊配置 - 复刻原代码第548-622行
        group_settings = self.config_manager.config.get("group_settings", {})
        if group_settings.get("enable", False):
            auto_trigger_settings = group_settings.get("auto_trigger_settings", {})
            if auto_trigger_settings.get("enable_auto_trigger", False):
                target_group_id = str(group_settings.get("target_group_id", "")).strip()
                if target_group_id:
                    # 检查是否已经有持久化的主动消息任务
                    has_existing_task = False
                    current_time = time.time()
                    
                    # 获取会话数据
                    session_data = self.session_manager.get_all_sessions_with_data()
                    for existing_session_id, session_info in session_data.items():
                        if (
                            session_info.get("next_trigger_time")
                            and f"GroupMessage:{target_group_id}" in existing_session_id
                        ):
                            next_trigger = session_info.get("next_trigger_time")
                            # 检查任务是否过期（给1分钟宽限期，与恢复逻辑保持一致）
                            trigger_time_with_grace = next_trigger + 60
                            is_not_expired = current_time < trigger_time_with_grace
                            
                            if self.logger:
                                self.logger.debug(
                                    f"[主动消息] 检查群聊持久化任务喵: {existing_session_id}"
                                )
                                self.logger.debug(
                                    f"[主动消息] 触发时间喵: {next_trigger} 喵, 当前时间喵: {current_time} 喵"
                                )
                                self.logger.debug(f"[主动消息] 是否未过期喵: {is_not_expired}")
                            
                            if is_not_expired:
                                if self.logger:
                                    self.logger.debug(
                                        f"[主动消息] 找到有效的群聊持久化任务喵: {existing_session_id}"
                                    )
                                has_existing_task = True
                                break
                            else:
                                if self.logger:
                                    self.logger.debug(
                                        f"[主动消息] 群聊任务已过期，不视为有效任务喵: {existing_session_id}"
                                    )
                    
                    if has_existing_task:
                        if self.logger:
                            self.logger.info(
                                f"[主动消息] 群聊会话 {target_group_id} 已存在持久化的主动消息任务，"
                                f"跳过自动触发器设置以避免冲突喵。"
                            )
                    else:
                        # 使用GroupMessage格式的会话ID，但需要先确定平台名称
                        # 从已有的会话数据中提取平台名称，如果没有则使用默认的"default"
                        platform_name = "default"
                        for existing_session_id in enabled_sessions:
                            if f"GroupMessage:{target_group_id}" in existing_session_id:
                                # 提取平台名称（第一部分）
                                platform_name = existing_session_id.split(":")[0]
                                break
                        
                        group_session_id = f"{platform_name}:GroupMessage:{target_group_id}"
                        
                        if self.logger:
                            self.logger.debug(
                                f"[主动消息] 为群聊设置自动触发器喵: {group_session_id}"
                            )
                        
                        # 设置自动触发器
                        success = await self.setup_auto_trigger(group_session_id)
                        if success:
                            auto_trigger_count += 1
                            if self.logger:
                                self.logger.info(
                                    f"[主动消息] 已为群聊会话 {target_group_id} 设置自动触发器喵。"
                                )
                else:
                    if self.logger:
                        self.logger.warning(
                            "[主动消息] 群聊启用了自动触发但未配置目标群聊ID喵。"
                        )
            else:
                if self.logger:
                    self.logger.info("[主动消息] 群聊未启用自动主动消息功能喵。")
        else:
            if self.logger:
                self.logger.debug("[主动消息] 群聊主动消息功能未启用喵。")
        
        # 最终结果记录
        if auto_trigger_count == 0:
            if self.logger:
                self.logger.info("[主动消息] 没有会话启用自动主动消息功能喵。")
        else:
            if self.logger:
                self.logger.info(
                    f"[主动消息] 已为 {auto_trigger_count} 个会话设置自动主动消息触发器喵。"
                )
        
        return auto_trigger_count
    
    def get_auto_trigger_timer_count(self) -> int:
        """
        获取当前自动触发计时器数量
        
        Returns:
            计时器数量
        """
        return len(self.auto_trigger_timers)
    
    def get_auto_trigger_sessions(self) -> List[str]:
        """
        获取所有有自动触发计时器的会话
        
        Returns:
            会话ID列表
        """
        return list(self.auto_trigger_timers.keys())
    
    def cancel_all_auto_triggers(self) -> int:
        """
        取消所有自动触发计时器
        
        Returns:
            取消的计时器数量
        """
        cancelled_count = 0
        for session_id in list(self.auto_trigger_timers.keys()):
            try:
                self.auto_trigger_timers[session_id].cancel()
                cancelled_count += 1
                if self.logger:
                    self.logger.debug(f"[主动消息] 已取消自动触发计时器喵: {session_id}")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[主动消息] 取消自动触发计时器时出错喵: {e}")
            finally:
                if session_id in self.auto_trigger_timers:
                    del self.auto_trigger_timers[session_id]
        
        if self.logger:
            self.logger.info(f"[主动消息] 已取消 {cancelled_count} 个自动触发计时器喵。")
        
        return cancelled_count
    
    async def _reset_counter_for_auto_trigger(self, session_id: str) -> bool:
        """
        为自动触发重置计数器 - v1.0.0-beta.5 修复计数器问题
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功重置
        """
        try:
            if self.logger:
                self.logger.info(f"[主动消息] 正在为自动触发重置会话 {session_id} 的计数器喵。")
            
            # 这里需要访问数据管理器，但我们没有直接引用
            # 需要通过回调函数获取
            get_data_manager = getattr(self, '_get_data_manager', None)
            if get_data_manager and callable(get_data_manager):
                data_manager = get_data_manager()
                if data_manager:
                    async with data_manager.get_data_lock():
                        session_data = await data_manager.load_session_data()
                        if session_id in session_data:
                            old_count = session_data[session_id].get("unanswered_count", 0)
                            session_data[session_id]["unanswered_count"] = 0
                            await data_manager.save_session_data(session_data)
                            
                            if self.logger:
                                self.logger.info(
                                    f"[主动消息] 会话 {session_id} 的计数器已从 {old_count} 重置为 0 喵。"
                                )
                            return True
                        else:
                            if self.logger:
                                self.logger.debug(
                                    f"[主动消息] 会话 {session_id} 不存在，无需重置计数器喵。"
                                )
                            return True
                else:
                    if self.logger:
                        self.logger.warning(
                            f"[主动消息] 无法获取数据管理器，无法重置计数器喵: {session_id}"
                        )
                    return False
            else:
                if self.logger:
                    self.logger.warning(
                        f"[主动消息] 无法获取数据管理器回调，无法重置计数器喵: {session_id}"
                    )
                return False
                
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"[主动消息] 重置自动触发计数器失败喵: {e}"
                )
            return False
    
    def set_data_manager_getter(self, getter_func: callable) -> bool:
        """
        设置数据管理器获取器回调
        
        Args:
            getter_func: 获取数据管理器的函数
            
        Returns:
            是否成功设置
        """
        try:
            self._get_data_manager = getter_func
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 设置数据管理器获取器失败喵: {e}")
            return False