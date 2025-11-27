"""
消息处理模块 - 负责消息事件的处理和响应
基于原main.py中的事件监听逻辑重构
"""

import time
from typing import Dict, Any, Optional, Callable


class MessageHandler:
    """
    消息处理器 - 负责消息事件的处理和响应
    
    主要职责：
    1. 私聊消息处理
    2. 群聊消息处理
    3. Bot消息检测
    4. 消息时间记录和状态更新
    5. 事件数据构建和验证
    6. 兼容性处理
    """
    
    def __init__(self, config_manager, session_manager, auto_trigger_manager, silence_timer_manager, logger=None):
        """
        初始化消息处理器
        
        Args:
            config_manager: 配置管理器实例
            session_manager: 会话管理器实例
            auto_trigger_manager: 自动触发管理器实例
            silence_timer_manager: 沉默计时器管理器实例
            logger: 日志记录器
        """
        self.config_manager = config_manager
        self.session_manager = session_manager
        self.auto_trigger_manager = auto_trigger_manager
        self.silence_timer_manager = silence_timer_manager
        self.logger = logger  # 日志记录器，保持与原代码一致的日志效果
    
    async def handle_private_message(self, session_id: str, event_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        处理私聊消息
        
        Args:
            session_id: 会话ID
            event_data: 事件数据（可选）
            
        Returns:
            处理结果字典
        """
        result = {
            "success": False,
            "message": "",
            "actions_taken": [],
            "friend_message_session_id": None,  # 添加FriendMessage格式会话ID
            "sender_id": None,  # 添加发送者ID
            "processing_time_ms": 0  # 处理耗时
        }
        
        start_time = time.time()
        
        try:
            # 记录调试信息 - 复刻原代码第855行逻辑
            if self.logger:
                self.logger.debug(f"[主动消息] 收到私聊消息喵，会话ID: {session_id}")
            
            # 记录消息时间并取消自动触发
            current_time = self.session_manager.record_message_time(session_id)
            
            # 尝试取消自动触发
            auto_trigger_cancelled = await self.auto_trigger_manager.cancel_auto_trigger(session_id)
            if auto_trigger_cancelled:
                result["actions_taken"].append("cancelled_auto_trigger")
                if self.logger:
                    self.logger.info(f"[主动消息] 已取消会话 {session_id} 的自动触发计时器喵")
            
            # 同时尝试取消基于FriendMessage格式的触发器（为了兼容初始化时的设置）
            friend_message_session_id = None
            try:
                # 从会话ID中提取平台名称和用户ID部分
                if ":" in session_id:
                    parts = session_id.split(":")
                    if len(parts) >= 3:  # platform:type:id 格式
                        platform_name = parts[0]  # 第一部分是平台名称
                        user_id = parts[-1]  # 最后一部分是用户ID
                        friend_message_session_id = f"{platform_name}:FriendMessage:{user_id}"
                        result["friend_message_session_id"] = friend_message_session_id
                        
                        # 尝试取消FriendMessage格式的触发器
                        friend_trigger_cancelled = await self.auto_trigger_manager.cancel_auto_trigger(friend_message_session_id)
                        if friend_trigger_cancelled:
                            result["actions_taken"].append("cancelled_friend_message_trigger")
                            
                            if self.logger:
                                self.logger.debug(f"[主动消息] 尝试取消FriendMessage格式触发器喵: {friend_message_session_id}")
            except Exception as e:
                # 记录但不中断主流程
                result["actions_taken"].append(f"friend_message_compat_warning: {e}")
                if self.logger:
                    self.logger.debug(f"[主动消息] 尝试取消FriendMessage格式触发器时出错喵（可忽略）: {e}")
            
            # 获取发送者ID用于日志记录
            sender_id = await self._get_sender_id(event_data)
            result["sender_id"] = sender_id
            
            # 只打印一次日志，避免刷屏，且只针对配置的会话
            # 注意：主插件会负责记录这个日志，这里只标记动作
            session_config = self.config_manager.get_session_config(session_id)
            if session_config and session_config.get("enable", False):
                if self.session_manager.mark_first_message_logged(session_id):
                    result["actions_taken"].append("logged_first_message")
                    # 主插件会负责记录这个日志，这里不再重复记录
            
            # 记录发送者ID - 复刻原代码第300行逻辑
            if sender_id and self.logger:
                self.logger.debug(f"[主动消息] 私聊消息发送者ID喵: {sender_id}")
            
            # 检查会话配置
            if not session_config or not session_config.get("enable", False):
                result["message"] = "会话未启用或配置无效"
                if self.logger:
                    self.logger.debug(f"[主动消息] 会话 {session_id} 未启用或配置无效，跳过处理喵")
                return result
            
            # 取消旧调度任务 - 由外部处理
            result["actions_taken"].append("ready_to_cancel_scheduler_job")
            
            # 重置未回复计数器 - 由外部处理
            result["actions_taken"].append("ready_to_reset_counter")
            
            result["success"] = True
            result["message"] = "私聊消息处理成功"
            result["actions_taken"].append("processed_message")
            
            if self.logger:
                self.logger.info(f"[主动消息] 私聊消息处理成功喵: {session_id}")
            
        except Exception as e:
            result["message"] = f"处理私聊消息失败: {e}"
            result["error"] = str(e)
            if self.logger:
                self.logger.error(f"[主动消息] 处理私聊消息失败喵: {e}")
                self.logger.debug(f"[主动消息] 私聊消息处理异常详情喵: {e}")
        
        finally:
            # 记录处理耗时
            end_time = time.time()
            result["processing_time_ms"] = int((end_time - start_time) * 1000)
            
            if self.logger and result["success"]:
                self.logger.debug(f"[主动消息] 私聊消息处理耗时: {result['processing_time_ms']}ms")
        
        return result
    
    async def handle_group_message(self, session_id: str, event_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        处理群聊消息
        
        Args:
            session_id: 会话ID
            event_data: 事件数据（可选）
            
        Returns:
            处理结果字典
        """
        result = {
            "success": False,
            "message": "",
            "actions_taken": [],
            "group_message_session_id": None,  # 添加GroupMessage格式会话ID
            "sender_id": None,  # 添加发送者ID
            "session_temp_state_updated": False,  # 会话状态更新标记
            "processing_time_ms": 0  # 处理耗时
        }
        
        start_time = time.time()
        
        try:
            # v1.0.0-beta.4修复：使用会话隔离的状态管理
            current_time = time.time()
            self.session_manager.set_session_temp_state(session_id, {"last_user_time": current_time})
            result["session_temp_state_updated"] = True
            
            # 记录调试信息 - 复刻原代码第980行逻辑
            if self.logger:
                self.logger.debug(f"[主动消息] 收到用户消息喵，会话ID: {session_id}")
            
            # 记录消息时间并取消自动触发
            self.session_manager.record_message_time(session_id)
            
            # 同时尝试取消基于GroupMessage格式的触发器（为了兼容初始化时的设置）
            group_message_session_id = None
            try:
                # 从会话ID中提取平台名称和群ID部分
                if ":" in session_id:
                    parts = session_id.split(":")
                    if len(parts) >= 3:  # platform:type:id 格式
                        platform_name = parts[0]  # 第一部分是平台名称
                        group_id = parts[-1]  # 最后一部分是群ID
                        group_message_session_id = f"{platform_name}:GroupMessage:{group_id}"
                        result["group_message_session_id"] = group_message_session_id
                        
                        # 尝试取消GroupMessage格式的触发器
                        group_trigger_cancelled = await self.auto_trigger_manager.cancel_auto_trigger(group_message_session_id)
                        if group_trigger_cancelled:
                            result["actions_taken"].append("cancelled_group_message_trigger")
                            
                            if self.logger:
                                self.logger.debug(f"[主动消息] 尝试取消GroupMessage格式触发器喵: {group_message_session_id}")
            except Exception as e:
                # 记录但不中断主流程
                result["actions_taken"].append(f"group_message_compat_warning: {e}")
                if self.logger:
                    self.logger.debug(f"[主动消息] 尝试取消GroupMessage格式触发器时出错喵（可忽略）: {e}")
            
            # 尝试取消自动触发
            auto_trigger_cancelled = await self.auto_trigger_manager.cancel_auto_trigger(session_id)
            if auto_trigger_cancelled:
                result["actions_taken"].append("cancelled_auto_trigger")
                if self.logger:
                    self.logger.info(f"[主动消息] 已取消会话 {session_id} 的自动触发计时器喵")
            
            # 获取发送者ID用于日志记录
            sender_id = await self._get_sender_id(event_data)
            result["sender_id"] = sender_id
            
            # 只打印一次日志，避免刷屏，且只针对配置的会话
            # 注意：主插件会负责记录这个日志，这里只标记动作
            session_config = self.config_manager.get_session_config(session_id)
            if session_config and session_config.get("enable", False):
                if self.session_manager.mark_first_message_logged(session_id):
                    result["actions_taken"].append("logged_first_message")
                    # 主插件会负责记录这个日志，这里不再重复记录
            
            # 记录发送者ID - 复刻原代码第978行逻辑
            if sender_id and self.logger:
                self.logger.debug(f"[主动消息] 收到用户消息喵，会话ID: {session_id}, 发送者ID: {sender_id}")
            
            # 检查会话配置
            if not session_config or not session_config.get("enable", False):
                result["message"] = "会话未启用或配置无效"
                if self.logger:
                    self.logger.debug(f"[主动消息] 会话 {session_id} 未启用或配置无效，跳过处理喵")
                return result
            
            # 取消已计划的主动消息任务 - 由外部处理
            result["actions_taken"].append("ready_to_cancel_scheduler_job")
            
            # 重置群聊沉默倒计时
            silence_timer_reset = await self.silence_timer_manager.reset_silence_timer(session_id)
            if silence_timer_reset:
                result["actions_taken"].append("reset_silence_timer")
                if self.logger:
                    self.logger.info(f"[主动消息] 已重置群聊 {session_id} 的沉默倒计时喵")
            
            # 重置未回复计数器 - 由外部处理
            result["actions_taken"].append("ready_to_reset_counter")
            
            result["success"] = True
            result["message"] = "群聊消息处理成功"
            result["actions_taken"].append("processed_message")
            
            if self.logger:
                self.logger.info(f"[主动消息] 群聊消息处理成功喵: {session_id}")
            
        except Exception as e:
            result["message"] = f"处理群聊消息失败: {e}"
            result["error"] = str(e)
            if self.logger:
                self.logger.error(f"[主动消息] 处理群聊消息失败喵: {e}")
        
        finally:
            # 记录处理耗时
            end_time = time.time()
            result["processing_time_ms"] = int((end_time - start_time) * 1000)
            
            if self.logger and result["success"]:
                self.logger.debug(f"[主动消息] 群聊消息处理耗时: {result['processing_time_ms']}ms")
        
        return result
    
    async def handle_after_message_sent(self, session_id: str, event_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        处理消息发送后事件（Bot消息检测）
        
        Args:
            session_id: 会话ID
            event_data: 事件数据（可选）
            
        Returns:
            处理结果字典
        """
        result = {
            "success": False,
            "message": "",
            "bot_detected": False,
            "actions_taken": [],
            "processing_time_ms": 0  # 处理耗时
        }
        
        start_time = time.time()
        
        try:
            # 只关注群聊消息
            if "group" not in session_id.lower():
                result["message"] = "非群聊消息，跳过处理"
                if self.logger:
                    self.logger.debug(f"[主动消息] 非群聊消息，跳过Bot检测喵: {session_id}")
                return result
            
            is_bot_message = False
            current_time = time.time()
            
            # 核心检测逻辑1: 时间窗口检测（最可靠）
            session_state = self.session_manager.get_session_temp_state(session_id)
            last_user_time = session_state.get("last_user_time", 0)
            time_since_user = current_time - last_user_time
            
            if last_user_time > 0 and time_since_user < 5.0:  # 5秒时间窗口
                is_bot_message = True
                result["actions_taken"].append("detected_by_time_window")
                # 复刻原代码第1066行日志
                if self.logger:
                    self.logger.debug(f"[主动消息] 🎯 检测到Bot消息喵！时间窗口: {time_since_user:.2f}秒，会话ID: {session_id}")
            
            # 核心检测逻辑2: source属性检测（由外部提供数据）
            if event_data and event_data.get("source") in ["self", "bot", "assistant"]:
                is_bot_message = True
                result["actions_taken"].append("detected_by_source")
                # 复刻原代码第1072-1074行日志
                if self.logger:
                    self.logger.info(f"[主动消息] ✅ 检测到Bot消息喵！source: {event_data['source']}，会话ID: {session_id}")
            
            # 核心检测逻辑3: ID匹配检测（由外部提供数据）
            if event_data and event_data.get("self_id") == event_data.get("user_id"):
                is_bot_message = True
                result["actions_taken"].append("detected_by_id_match")
                # 复刻原代码第1080-1082行日志
                if self.logger:
                    self.logger.info(f"[主动消息] ✅ 检测到Bot消息喵！self_id == user_id: {event_data['self_id']}，会话ID: {session_id}")
            
            if is_bot_message:
                # 重置沉默倒计时
                silence_timer_reset = await self.silence_timer_manager.reset_silence_timer(session_id)
                if silence_timer_reset:
                    result["actions_taken"].append("reset_silence_timer")
                    if self.logger:
                        self.logger.info(f"[主动消息] 已重置群聊 {session_id} 的沉默倒计时喵")
                
                # 清理会话状态
                self.session_manager.clear_session_temp_state(session_id)
                
                # 记录Bot消息时间
                self.session_manager.record_bot_message_time()
                
                result["bot_detected"] = True
                result["message"] = "成功检测到Bot消息"
                # 复刻原代码第1273-1274行日志
                if self.logger:
                    self.logger.info(f"[主动消息] ✅ 成功检测到Bot消息，会话ID: {session_id}")
            else:
                result["message"] = "未检测到Bot消息"
                # 复刻原代码第1291行日志
                if self.logger:
                    self.logger.debug(f"[主动消息] 未检测到Bot消息喵，会话ID: {session_id}")
            
            result["success"] = True
            
        except Exception as e:
            result["message"] = f"Bot消息检测失败: {e}"
            result["error"] = str(e)
            # 复刻原代码第1295-1296行日志
            if self.logger:
                self.logger.error(f"[主动消息] after_message_sent 检测异常喵: {e}, 会话ID: {session_id}")
        
        finally:
            # 记录处理耗时
            end_time = time.time()
            result["processing_time_ms"] = int((end_time - start_time) * 1000)
            
            if self.logger and result["success"]:
                self.logger.debug(f"[主动消息] Bot消息检测处理耗时: {result['processing_time_ms']}ms")
        
        return result
    
    def get_message_handler_info(self, session_id: str) -> Dict[str, Any]:
        """
        获取消息处理器信息
        
        Args:
            session_id: 会话ID
            
        Returns:
            处理器信息字典
        """
        session_config = self.config_manager.get_session_config(session_id)
        
        info = {
            "session_enabled": session_config is not None and session_config.get("enable", False),
            "has_temp_state": self.session_manager.has_session_temp_state(session_id),
            "first_message_logged": self.session_manager.is_first_message_logged(session_id),
            "last_message_time": self.session_manager.get_last_message_time(session_id),
            "config_available": session_config is not None,
            "auto_trigger_enabled": self.config_manager.get_auto_trigger_settings(
                "private" if "friend" in session_id.lower() else "group"
            ).get("enable_auto_trigger", False)
        }
        
        if self.logger:
            self.logger.debug(f"[主动消息] 获取消息处理器信息喵: {session_id} -> {info}")
        
        return info
    
    async def _get_sender_id(self, event_data: Dict[str, Any] = None) -> Optional[str]:
        """
        获取发送者ID - 完整复刻原代码第962-977行逻辑
        
        Args:
            event_data: 事件数据（可选）
            
        Returns:
            发送者ID，失败时返回None
        """
        try:
            sender_id = None
            
            if event_data:
                # 优先从事件数据中获取 - 复刻原代码第965-974行
                if event_data.get("message_obj") and event_data["message_obj"]:
                    message_obj = event_data["message_obj"]
                    if message_obj.get("sender") and message_obj["sender"]:
                        sender = message_obj["sender"]
                        sender_id = sender.get("id") or sender.get("user_id")
                
                # 备用获取方式 - 复刻原代码第971-974行
                if not sender_id:
                    sender_id = event_data.get("user_id") or event_data.get("sender_id")
            
            return str(sender_id) if sender_id else None
            
        except Exception as e:
            # 错误处理 - 复刻原代码第976-977行
            if self.logger:
                self.logger.debug(f"[主动消息] 获取发送者ID失败喵: {e}")
            return None
    
    def _log_debug_info(self, session_id: str, message_type: str, sender_id: Optional[str] = None, 
                       compat_session_id: Optional[str] = None, action: str = "") -> Dict[str, Any]:
        """
        记录调试信息 - 用于详细的调试日志
        
        Args:
            session_id: 会话ID
            message_type: 消息类型 ("private" 或 "group")
            sender_id: 发送者ID（可选）
            compat_session_id: 兼容性会话ID（可选）
            action: 动作描述
            
        Returns:
            调试信息字典
        """
        debug_info = {
            "session_id": session_id,
            "message_type": message_type,
            "action": action,
            "timestamp": time.time()
        }
        
        if sender_id:
            debug_info["sender_id"] = sender_id
        if compat_session_id:
            debug_info["compat_session_id"] = compat_session_id
            
        if self.logger:
            self.logger.debug(f"[主动消息] 调试信息喵: {debug_info}")
            
        return debug_info
    
    def get_handler_stats(self) -> Dict[str, Any]:
        """
        获取处理器统计信息
        
        Returns:
            统计信息字典
        """
        return {
            "config_manager_available": self.config_manager is not None,
            "session_manager_available": self.session_manager is not None,
            "auto_trigger_manager_available": self.auto_trigger_manager is not None,
            "silence_timer_manager_available": self.silence_timer_manager is not None,
            "logger_available": self.logger is not None
        }