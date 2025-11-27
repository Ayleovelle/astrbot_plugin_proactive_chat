# 文件名: main.py (模块化重构版本)
# 版本: 1.0.0-beta.5 (Modular Architecture)

# 导入标准库
import asyncio
import time
import traceback
import zoneinfo
import random
from datetime import datetime
from typing import Dict, Any

# 导入第三方库
# AsyncIOScheduler 在 core/scheduler.py 中使用，这里不需要导入

# 导入AstrBot的核心API和组件
import astrbot.api.star as star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.agent.message import (
    AssistantMessageSegment,
    TextPart,
    UserMessageSegment,
)
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain

# 导入模块化组件 - 使用相对导入以适应AstrBot的插件加载机制
try:
    # 尝试相对导入（当作为插件加载时）
    from .core import DataManager, ConfigManager, Scheduler, SessionManager, TTSManager
    from .features import AutoTriggerManager, SilenceTimerManager, MessageHandler
    from .llm import LLMClient
    from .utils import is_quiet_time
    from .utils.logger import get_logger
except ImportError:
    # 回退到绝对导入（当直接运行时）
    from core import DataManager, ConfigManager, Scheduler, SessionManager, TTSManager
    from features import AutoTriggerManager, SilenceTimerManager, MessageHandler
    from llm import LLMClient
    from utils import is_quiet_time
    from utils.logger import get_logger

# --- 插件主类 ---


class ProactiveChatPlugin(star.Star):
    """
    插件的主类，继承自 astrbot.api.star.Star。
    负责插件的生命周期管理、事件监听和核心逻辑协调。
    
    模块化架构特点：
    1. 各模块职责单一，高内聚低耦合
    2. 通过依赖注入协调各模块
    3. 主类只负责协调和事件分发
    """
    
    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        """
        插件的构造函数。
        初始化所有模块并建立依赖关系。
        """
        super().__init__(context)
        
        # 基础配置
        self.config: AstrBotConfig = config
        self.scheduler = None
        self.timezone = None
        self._is_terminated = False  # v1.0.0-beta.5 新增：插件终止状态标记
        
        # 数据目录配置
        self.data_dir = star.StarTools.get_data_dir("astrbot_plugin_proactive_chat")
        self.session_data_file = self.data_dir / "session_data.json"
        
        # 初始化日志器 - 必须在模块初始化之前
        self.logger = get_logger("proactive_chat")
        self.logger.info("[主动消息] 插件实例已创建喵。")
        
        # 模块初始化
        self._init_modules()
    
    def _init_modules(self):
        """
        初始化所有模块并建立依赖关系
        """
        # 核心模块
        self.data_manager = DataManager(self.data_dir, self.session_data_file, self.logger)
        self.config_manager = ConfigManager(self.config, self.logger)
        self.scheduler = Scheduler(self.timezone, self.logger)
        self.session_manager = SessionManager(self.logger)
        self.tts_manager = TTSManager(lambda: self.context, self.logger)
        
        # 功能模块
        self.auto_trigger_manager = AutoTriggerManager(
            self.scheduler, self.config_manager, self.session_manager,
            self.session_manager.plugin_start_time, self.logger
        )
        self.silence_timer_manager = SilenceTimerManager(
            self.scheduler, self.config_manager, self.session_manager, self.data_manager, self.logger
        )
        self.message_handler = MessageHandler(
            self.config_manager, self.session_manager,
            self.auto_trigger_manager, self.silence_timer_manager, self.logger
        )
        
        # LLM模块
        self.llm_client = LLMClient(lambda: self.context, self.logger)
        
        # 设置模块间的回调引用（避免循环依赖）
        self._setup_module_callbacks()
    
    def _setup_module_callbacks(self):
        """
        设置模块间的回调引用
        """
        # 为自动触发管理器设置回调
        self.auto_trigger_manager._get_check_and_chat_func = lambda: self.check_and_chat
        self.auto_trigger_manager.set_data_manager_getter(lambda: self.data_manager)
        
        # 为沉默计时器管理器设置回调
        self.silence_timer_manager._get_silence_callback = lambda session_id: self._handle_silence_timeout(session_id)
        self.silence_timer_manager._get_logger_info = lambda: self.logger.info
        
        # 为LLM客户端设置上下文获取器
        self.llm_client.context_getter = lambda: self.context
        
        # 为TTS管理器设置上下文获取器
        self.tts_manager.context_getter = lambda: self.context
    
    async def _handle_silence_timeout(self, session_id: str):
        """
        处理沉默超时 - 完整复刻v1.0.0-beta.4的L1137-1177逻辑
        """
        try:
            # v1.0.0-beta.1 修复: 在创建任务前，验证群聊是否仍然处于沉默状态
            # 重要：倒计时结束时，需要检查群聊是否仍然值得发送主动消息

            # 检查1: 验证当前是否还有活跃的计时器（如果群聊活跃，计时器应该被重置）
            if not self.scheduler.has_group_timer(session_id):
                self.logger.info(
                    f"[主动消息] 群聊 {session_id} 的计时器已被重置，跳过主动消息创建喵。"
                )
                return

            # v1.0.0-beta.3 修复：
            # 检查2: 验证会话数据是否存在，如果不存在则创建初始数据
            async with self.data_manager.get_data_lock():
                session_data = await self.data_manager.load_session_data()
                if session_id not in session_data:
                    self.logger.info(
                        f"[主动消息] 群聊 {session_id} 的会话数据不存在，创建初始会话数据喵。"
                    )
                    # 为新会话创建初始数据
                    session_data[session_id] = {"unanswered_count": 0}
                    await self.data_manager.save_session_data(session_data)

            # 检查3: 验证配置是否仍然启用
            session_config = self.config_manager.get_session_config(session_id)
            if not session_config or not session_config.get("enable", False):
                self.logger.info(
                    f"[主动消息] 群聊 {session_id} 的配置已禁用或不存在，跳过主动消息创建喵。"
                )
                return

            # v1.0.0-beta.1 修复: 当群聊沉默时，不应该重置计数器。reset_counter 必须为 False。
            # 这个回调是在主事件循环中被调用的，所以我们可以安全地创建异步任务
            # 获取当前的未回复次数，用于显示更准确的日志
            async with self.data_manager.get_data_lock():
                session_data = await self.data_manager.load_session_data()
                current_unanswered = session_data.get(session_id, {}).get("unanswered_count", 0)
            
            # 获取沉默触发时间用于日志
            idle_minutes = session_config.get("group_idle_trigger_minutes", 10)
            
            # 创建主动消息任务
            await self._schedule_next_chat_and_save(session_id, reset_counter=False)
            
            self.logger.info(
                f"[主动消息] 群聊 {session_id} 已沉默 {idle_minutes} 分钟，"
                f"开始计划主动消息喵。(当前未回复次数: {current_unanswered})"
            )
            
        except Exception as e:
            self.logger.error(f"[主动消息] 沉默倒计时回调函数执行失败喵: {e}")
    
    # --- 插件生命周期函数 ---
    
    async def initialize(self):
        """插件的异步初始化函数。"""
        try:
            self.logger.info("[主动消息] 插件初始化开始喵...")
            
            # 配置验证
            validation_result = self.config_manager.validate_config()
            if validation_result["errors"]:
                self.logger.error(f"[主动消息] 配置验证发现错误: {validation_result['errors']}")
            if validation_result["warnings"]:
                self.logger.warning(f"[主动消息] 配置验证发现警告: {validation_result['warnings']}")
            
            # 数据目录检查（仅错误时记录）
            await self.data_manager.ensure_data_dir_exists()
            
            # 加载会话数据
            try:
                session_data = await self.data_manager.load_session_data()
                
                # 清理无效数据
                if session_data:
                    cleaned_count = await self.data_manager.cleanup_invalid_session_data(session_data)
                    if cleaned_count > 0:
                        await self.data_manager.save_session_data(session_data)
                        self.logger.info(f"[主动消息] 清理了 {cleaned_count} 个无效会话数据条目喵。")
                        
            except Exception as e:
                self.logger.error(f"[主动消息] 加载会话数据失败，使用空数据继续喵: {e}")
                session_data = {}
            
            # 设置时区
            try:
                self.timezone = zoneinfo.ZoneInfo(self.context.get_config("timezone"))
                self.scheduler.timezone = self.timezone
            except (zoneinfo.ZoneInfoNotFoundError, TypeError, KeyError, ValueError) as e:
                self.logger.debug(f"[主动消息] 时区配置无效喵 ({e})，使用系统默认喵。")
                self.timezone = None
            
            # 启动调度器
            self.scheduler.start()
            
            # 从数据恢复任务
            await self._init_jobs_from_data()
            
            # 为启用的会话设置自动主动消息触发器
            enabled_sessions = await self._get_enabled_sessions()
            if enabled_sessions:
                count = await self.auto_trigger_manager.setup_auto_triggers_for_enabled_sessions(enabled_sessions)
                self.logger.info(f"[主动消息] 已为 {count} 个会话设置自动触发器喵。")
            
            self.logger.info("[主动消息] 插件初始化完成喵！")
            
        except Exception as e:
            self.logger.error(f"[主动消息] 插件初始化失败: {e}")
            self.logger.debug(f"[主动消息] 初始化失败详细信息喵: {traceback.format_exc()}")
            raise
    
    async def terminate(self):
        """插件被卸载或停用时调用的清理函数。"""
        try:
            self.logger.info("[主动消息] 开始清理插件资源喵...")
            
            # 标记插件为已终止状态
            self._is_terminated = True
            
            # 首先取消所有自动触发器
            auto_trigger_cancelled = self.auto_trigger_manager.cancel_all_auto_triggers()
            self.logger.info(f"[主动消息] 已取消 {auto_trigger_cancelled} 个自动触发计时器喵。")
            
            # 清理群聊沉默计时器
            timer_count = self.scheduler.clear_all_group_timers()
            self.logger.info(f"[主动消息] 已取消 {timer_count} 个群聊沉默计时器喵。")
            
            # 移除所有调度任务 - v1.0.0-beta.5 修复：确保清理所有任务
            try:
                all_jobs = self.scheduler.get_all_jobs()
                job_count = 0
                for job in all_jobs:
                    try:
                        # 更宽松的任务识别 - 检查任务ID是否包含我们的会话格式
                        job_id = str(job.id)
                        if (job.func == self.check_and_chat or
                            'auto_trigger' in job_id or
                            'FriendMessage' in job_id or
                            'GroupMessage' in job_id):
                            self.scheduler.remove_job(job.id)
                            job_count += 1
                            self.logger.debug(f"[主动消息] 移除任务 {job.id} 成功喵")
                    except Exception as job_e:
                        self.logger.debug(f"[主动消息] 移除任务 {job.id} 失败喵: {job_e}")
                self.logger.info(f"[主动消息] 已移除 {job_count} 个调度任务喵。")
            except Exception as e:
                self.logger.warning(f"[主动消息] 清理调度任务时出错喵: {e}")
            
            # 关闭调度器
            self.scheduler.shutdown()
            self.logger.info("[主动消息] 调度器已关闭喵。")
            
            # 保存数据 - 优化：避免在终止时使用锁，防止阻塞
            try:
                # 清理所有next_trigger_time，避免残留任务
                session_data = await self.data_manager.load_session_data()
                cleaned_sessions = 0
                for session_id in list(session_data.keys()):
                    if "next_trigger_time" in session_data[session_id]:
                        del session_data[session_id]["next_trigger_time"]
                        cleaned_sessions += 1
                
                if cleaned_sessions > 0:
                    await self.data_manager.save_session_data(session_data)
                    self.logger.info(f"[主动消息] 已清理 {cleaned_sessions} 个会话的调度数据喵。")
            except Exception as e:
                self.logger.warning(f"[主动消息] 保存数据时出错喵: {e}")
            
            self.logger.info("[主动消息] 主动消息插件已终止喵。")
            
        except Exception as e:
            self.logger.error(f"[主动消息] 插件终止时出错: {e}")
    
    async def _get_enabled_sessions(self) -> list:
        """
        获取启用的会话列表
        
        Returns:
            启用的会话ID列表
        """
        enabled_sessions = []
        
        # 检查私聊配置
        if self.config_manager.is_session_enabled("private"):
            private_settings = self.config.get("private_settings", {})
            target_user_id = str(private_settings.get("target_user_id", "")).strip()
            if target_user_id:
                # 构建会话ID，需要确定平台名称
                # 从已有的会话数据中提取平台名称，如果没有则使用默认的"default"
                platform_name = "default"
                # 优化：避免在终止过程中使用锁
                try:
                    session_data = await self.data_manager.load_session_data()
                    for existing_session_id in session_data.keys():
                        if f"FriendMessage:{target_user_id}" in existing_session_id:
                            # 提取平台名称（第一部分）
                            platform_name = existing_session_id.split(":")[0]
                            break
                except Exception as e:
                    self.logger.debug(f"[主动消息] 获取平台名称失败喵（使用默认名称）: {e}")
                
                session_id = f"{platform_name}:FriendMessage:{target_user_id}"
                enabled_sessions.append(session_id)
        
        # 检查群聊配置
        if self.config_manager.is_session_enabled("group"):
            group_settings = self.config.get("group_settings", {})
            target_group_id = str(group_settings.get("target_group_id", "")).strip()
            if target_group_id:
                # 构建会话ID，需要确定平台名称
                # 从已有的会话数据中提取平台名称，如果没有则使用默认的"default"
                platform_name = "default"
                # 优化：避免在终止过程中使用锁
                try:
                    session_data = await self.data_manager.load_session_data()
                    for existing_session_id in session_data.keys():
                        if f"GroupMessage:{target_group_id}" in existing_session_id:
                            # 提取平台名称（第一部分）
                            platform_name = existing_session_id.split(":")[0]
                            break
                except Exception as e:
                    self.logger.debug(f"[主动消息] 获取平台名称失败喵（使用默认名称）: {e}")
                
                session_id = f"{platform_name}:GroupMessage:{target_group_id}"
                enabled_sessions.append(session_id)
        
        if enabled_sessions:
            self.logger.info(f"[主动消息] 发现 {len(enabled_sessions)} 个启用的会话喵")
        
        return enabled_sessions
    
    # --- 事件监听 ---
    
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=999)
    async def on_private_message(self, event: AstrMessageEvent):
        """监听私聊消息 - 回归1.0.0-beta.4的简洁模式"""
        # v1.0.0-beta.1 修复: 不再只检查 message_str，而是检查整个消息链，以正确响应图片等富媒体消息
        if not event.get_messages():
            return
        
        session_id = event.unified_msg_origin
        self.logger.debug(f"[主动消息] 收到私聊消息喵，会话ID: {session_id}")
        
        # v1.0.0-beta.2 新增: 记录消息时间并取消自动触发
        current_time = time.time()
        self.session_manager.record_message_time(session_id)
        self.logger.debug(f"[主动消息] 记录私聊消息时间喵: {session_id} -> {current_time}")
        
        # 尝试取消自动触发 - 支持多种会话ID格式
        await self.auto_trigger_manager.cancel_auto_trigger(session_id)
        
        # 同时尝试取消基于FriendMessage格式的触发器（为了兼容初始化时的设置）
        try:
            # 从会话ID中提取平台名称和用户ID部分
            if ":" in session_id:
                parts = session_id.split(":")
                if len(parts) >= 3:  # platform:type:id 格式
                    platform_name = parts[0]  # 第一部分是平台名称
                    user_id = parts[-1]  # 最后一部分是用户ID
                    friend_message_session_id = f"{platform_name}:FriendMessage:{user_id}"
                    await self.auto_trigger_manager.cancel_auto_trigger(friend_message_session_id)
        except Exception as e:
            self.logger.debug(f"[主动消息] 尝试取消FriendMessage格式触发器时出错喵（可忽略）: {e}")
        
        # 只打印一次日志，避免刷屏，且只针对配置的会话
        session_config = self.config_manager.get_session_config(session_id)
        if session_config and session_config.get("enable", False):
            if self.session_manager.mark_first_message_logged(session_id):
                self.logger.info(f"[主动消息] 已记录私聊消息时间并取消自动触发喵，会话ID: {session_id}")
        # 后续消息不再打印日志，保持简洁
        
        if not session_config or not session_config.get("enable", False):
            self.logger.debug(f"[主动消息] 会话 {session_id} 未启用或配置无效，跳过处理喵。")
            return
        
        # v1.0.0-beta.1 修复: 在重新调度前，先尝试取消任何已存在的、由 APScheduler 设置的定时任务
        # v1.0.0-beta.5 优化：先检查任务是否存在，避免错误日志
        # 使用get_job来检查任务是否存在，而不是has_job（不存在这个方法）
        existing_job = self.scheduler.get_job(session_id)
        if existing_job:
            try:
                self.scheduler.remove_job(session_id)
                self.logger.info(f"[主动消息] 用户已回复喵，已取消会话 {session_id} 的预定主动消息任务喵。")
            except Exception as e:
                self.logger.debug(f"[主动消息] 取消会话 {session_id} 的任务时出错（可忽略）: {e}")
        else:
            self.logger.debug(f"[主动消息] 会话 {session_id} 没有待取消的调度任务喵，这是正常情况。")
        
        # 重要：只重置当前会话的计数器，不影响其他会话
        self.logger.info(f"[主动消息] 重置会话 {session_id} 的未回复计数器为0喵。")
        await self._schedule_next_chat_and_save(session_id, reset_counter=True)
        
        # 返回事件，确保Bot能正常回复私聊消息
        return event
    
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=998)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群聊消息流，重置沉默倒计时，并取消已计划的主动消息任务。"""
        # v1.0.0-beta.1 修复: 响应所有类型的消息，而不仅仅是文本消息
        if not event.get_messages():
            return

        session_id = event.unified_msg_origin

        # v1.0.0-beta.4 修复: 使用会话隔离的状态管理，避免竞态条件
        current_time = time.time()
        self.session_manager.set_session_temp_state(session_id, {"last_user_time": current_time})
        self.logger.debug(
            f"[主动消息] 记录用户消息时间戳喵: {current_time}, 会话ID: {session_id}"
        )

        # v1.0.0-beta.2 新增: 记录消息时间并取消自动触发
        self.session_manager.record_message_time(session_id)
        self.logger.debug(f"[主动消息] 记录群聊消息时间喵: {session_id} -> {current_time}")
        await self.auto_trigger_manager.cancel_auto_trigger(session_id)

        # 同时尝试取消基于GroupMessage格式的触发器（为了兼容初始化时的设置）
        try:
            # 从会话ID中提取平台名称和群ID部分
            if ":" in session_id:
                parts = session_id.split(":")
                if len(parts) >= 3:  # platform:type:id 格式
                    platform_name = parts[0]  # 第一部分是平台名称
                    group_id = parts[-1]  # 最后一部分是群ID
                    group_message_session_id = (
                        f"{platform_name}:GroupMessage:{group_id}"
                    )
                    await self.auto_trigger_manager.cancel_auto_trigger(group_message_session_id)
        except Exception as e:
            self.logger.debug(
                f"[主动消息] 尝试取消GroupMessage格式触发器时出错喵（可忽略）: {e}"
            )

        # 只打印一次日志，避免刷屏，且只针对配置的会话
        session_config = self.config_manager.get_session_config(session_id)
        if session_config and session_config.get("enable", False):
            if self.session_manager.mark_first_message_logged(session_id):
                self.logger.info(
                    f"[主动消息] 已记录群聊消息时间并取消自动触发喵，会话ID: {session_id}"
                )
        # 后续消息不再打印日志，保持简洁

        # 获取发送者ID用于日志记录，不再进行Bot检测
        sender_id = None
        try:
            # 只获取发送者ID用于日志记录，不再进行Bot检测
            if hasattr(event, "message_obj") and event.message_obj:
                if hasattr(event.message_obj, "sender") and event.message_obj.sender:
                    sender_id = getattr(
                        event.message_obj.sender, "id", None
                    ) or getattr(event.message_obj.sender, "user_id", None)

            if not sender_id:
                sender_id = getattr(event, "user_id", None) or getattr(
                    event, "sender_id", None
                )

        except Exception as e:
            self.logger.debug(f"[主动消息] 获取发送者ID失败喵: {e}")

        # 简化日志：只记录用户消息检测，Bot检测由after_message_sent处理
        self.logger.debug(
            f"[主动消息] 收到用户消息喵，会话ID: {session_id}, 发送者ID: {sender_id}"
        )

        if not session_config or not session_config.get("enable", False):
            self.logger.debug(f"[主动消息] 会话 {session_id} 未启用或配置无效，跳过处理喵。")
            return

        # v1.0.0-beta.1 修复: 群聊活跃时取消已预定的 APScheduler 任务
        # 注意：这里不再区分Bot消息和用户消息，因为Bot消息检测已迁移到after_message_sent
        # v1.0.0-beta.5 优化：先检查任务是否存在，避免错误日志
        # 使用get_job来检查任务是否存在，而不是has_job（不存在这个方法）
        existing_job = self.scheduler.get_job(session_id)
        if existing_job:
            try:
                self.scheduler.remove_job(session_id)
                self.logger.info(
                    f"[主动消息] 群聊活跃喵，已取消会话 {session_id} 的预定主动消息任务喵。"
                )
            except Exception as e:
                self.logger.debug(f"[主动消息] 取消会话 {session_id} 的任务时出错（可忽略）: {e}")
        else:
            self.logger.debug(f"[主动消息] 会话 {session_id} 没有待取消的调度任务喵，这是正常情况。")

        # v1.0.0-beta.1 架构重构: 将重置沉默倒计时的逻辑，提取到一个可复用的函数中
        # 无论是用户发言还是Bot消息，都应该重置沉默倒计时
        # v1.0.0-beta.1 修复: 群聊用户发言时也应该重置未回复计数器
        self.logger.debug(f"[主动消息] 准备重置群聊沉默倒计时喵，会话ID: {session_id}")
        await self.silence_timer_manager.reset_silence_timer(session_id)

        # 重要修复：群聊用户发言时也应该重置未回复计数器，与私聊保持一致
        # 每个会话(私聊/群聊)有独立的session_id和数据，不会相互影响
        # v1.0.0-beta.1 修复: 现在只处理用户消息，Bot消息检测已迁移到after_message_sent
        try:
            session_data = await self.data_manager.load_session_data()
            if session_id in session_data:
                current_unanswered = session_data[session_id].get(
                    "unanswered_count", 0
                )
                session_data[session_id]["unanswered_count"] = 0
                if current_unanswered > 0:
                    self.logger.debug(
                        f"[主动消息] 群聊用户已回复，会话 {session_id} 未回复计数器已重置喵。"
                    )
                
                # v1.0.0-beta.1 修复: 清理已作废的定时任务数据，避免重复恢复
                # 重要：只清理群聊的定时任务数据，因为群聊使用沉默倒计时机制
                # 私聊使用APScheduler，不应该在这里清理
                if (
                    "group" in session_id.lower()
                    and "next_trigger_time" in session_data[session_id]
                ):
                    del session_data[session_id]["next_trigger_time"]
                    self.logger.debug(
                        f"[主动消息] 因群聊活跃，清理会话 {session_id} 中已作废的定时任务数据喵。"
                    )
                
                await self.data_manager.save_session_data(session_data)
        except Exception as e:
            # 如果出错，记录但不阻止事件处理
            self.logger.debug(f"[主动消息] 重置计数器失败喵（可忽略）: {e}")
        
        # 返回事件，确保Bot能正常回复群聊消息
        return event
    
    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent):
        """
        监听消息发送后事件，检测Bot自己发送的消息。
        这是v1.0.0-beta.1版本的核心改进之一，通过多重检测机制准确识别Bot消息：
        1. 时间窗口检测：Bot回复通常在用户消息5秒内
        2. source属性检测：检查消息来源标识
        3. ID匹配检测：对比self_id和user_id

        检测到Bot消息后，会重置群聊沉默倒计时，确保时序正确性。
        完整复刻v1.0.0-beta.4的L1032-1099逻辑。
        """
        # v1.0.0-beta.5 修复：检查插件是否已终止
        if self._is_terminated:
            self.logger.debug(f"[主动消息] 插件已终止，跳过after_message_sent处理喵: {event.unified_msg_origin}")
            return None
            
        session_id = event.unified_msg_origin

        # 只关注群聊消息
        if "group" not in session_id.lower():
            return None

        # 简化但有效的Bot消息检测 - 基于之前成功的经验
        self.logger.debug(f"[主动消息] after_message_sent事件触发喵，会话ID: {session_id}")

        is_bot_message = False
        current_time = time.time()

        try:
            # 核心检测逻辑1: 时间窗口检测（最可靠）- v1.0.0-beta.4修复：使用会话隔离状态
            session_state = self.session_manager.get_session_temp_state(session_id)
            last_user_time = session_state.get("last_user_time", 0)
            time_since_user = current_time - last_user_time
            if (
                last_user_time > 0 and time_since_user < 5.0  # 5秒时间窗口
            ):
                is_bot_message = True
                self.logger.debug(
                    f"[主动消息] 🎯 检测到Bot消息喵！时间窗口: {time_since_user:.2f}秒，会话ID: {session_id}"
                )

            # 核心检测逻辑2: source属性检测
            elif hasattr(event, "source") and event.source:
                source = str(event.source).lower()
                if source in ["self", "bot", "assistant"]:
                    is_bot_message = True
                    self.logger.info(
                        f"[主动消息] ✅ 检测到Bot消息喵！source: {source}，会话ID: {session_id}"
                    )

            # 核心检测逻辑3: 简单的ID匹配
            elif hasattr(event, "self_id") and hasattr(event, "user_id"):
                if str(event.self_id) == str(event.user_id):
                    is_bot_message = True
                    self.logger.info(
                        f"[主动消息] ✅ 检测到Bot消息喵！self_id == user_id: {event.self_id}，会话ID: {session_id}"
                    )

            if is_bot_message:
                # 重置沉默倒计时
                await self.silence_timer_manager.reset_silence_timer(session_id)
                # 清理会话状态 - v1.0.0-beta.4修复：使用会话隔离状态管理
                self.session_manager.clear_session_temp_state(session_id)
                # 记录Bot发送消息的时间，用于辅助检测Bot消息
                self.session_manager.record_bot_message_time()
            else:
                self.logger.debug(f"[主动消息] 未检测到Bot消息喵，会话ID: {session_id}")

        except Exception as e:
            # v1.0.0-beta.5 修复：确保异常不会阻止事件传播
            self.logger.error(
                f"[主动消息] after_message_sent 检测异常喵: {e}, 会话ID: {session_id}"
            )
            # 不重新抛出异常，让事件继续传播
            return None
        
        # 返回事件，确保事件继续传播
        return event

    # --- 核心调度逻辑 ---
    
    async def _init_jobs_from_data(self):
        """
        从已加载的 session_data 中恢复定时任务
        """
        try:
            async with self.data_manager.get_data_lock():
                session_data = await self.data_manager.load_session_data()
                
                # 清理无效数据
                cleaned_count = await self.data_manager.cleanup_invalid_session_data(session_data)
                if cleaned_count > 0:
                    await self.data_manager.save_session_data(session_data)
                    self.logger.info(f"[主动消息] 清理了 {cleaned_count} 个无效会话数据条目喵。")
                
                restored_count = 0
                
                for session_id, session_info in session_data.items():
                    # 检查会话配置
                    session_config = self.config_manager.get_session_config(session_id)
                    if not session_config or not session_config.get("enable", False):
                        continue
                    
                    next_trigger = session_info.get("next_trigger_time")
                    
                    if next_trigger:
                        # 检查任务是否过期
                        current_time = time.time()
                        trigger_time_with_grace = next_trigger + 60
                        
                        if current_time < trigger_time_with_grace:
                            # 恢复任务
                            run_date = self.scheduler.create_run_date(next_trigger)
                            success = self.scheduler.add_job(
                                self.check_and_chat,
                                "date",
                                run_date=run_date,
                                args=[session_id],
                                id=session_id
                            )
                            
                            if success:
                                restored_count += 1
                                self.logger.debug(
                                    f"[主动消息] 恢复任务喵: {session_id}, 执行时间: {run_date}"
                                )
                
                if restored_count > 0:
                    self.logger.info(f"[主动消息] 任务恢复完成，共恢复 {restored_count} 个定时任务喵。")
                
        except Exception as e:
            self.logger.error(f"[主动消息] 从数据恢复任务失败: {e}")
            self.logger.debug(f"[主动消息] 任务恢复失败详细信息喵: {traceback.format_exc()}")
    
    async def _schedule_next_chat_and_save(self, session_id: str, reset_counter: bool = False):
        """
        安排下一次主动聊天并立即将状态持久化到文件
        """
        try:
            session_config = self.config_manager.get_session_config(session_id)
            if not session_config:
                return
            
            schedule_conf = self.config_manager.get_schedule_settings(
                "private" if "friend" in session_id.lower() else "group"
            )
            
            # 重置计数器
            if reset_counter:
                async with self.data_manager.get_data_lock():
                    session_data = await self.data_manager.load_session_data()
                    session_data.setdefault(session_id, {})["unanswered_count"] = 0
                    await self.data_manager.save_session_data(session_data)
                    
                    self.logger.info(f"[主动消息] 用户已回复喵。会话 {session_id} 的未回复计数已重置喵。")
            
            # 计算下一次触发时间
            min_interval = int(schedule_conf.get("min_interval_minutes", 30))
            max_interval = int(schedule_conf.get("max_interval_minutes", 900))
            next_trigger_time = self.scheduler.calculate_next_trigger_time(min_interval, max_interval)
            run_date = self.scheduler.create_run_date(next_trigger_time)
            
            # 添加调度任务
            success = self.scheduler.add_job(
                self.check_and_chat,
                "date",
                run_date=run_date,
                args=[session_id],
                id=session_id
            )
            
            if success:
                # 保存触发时间
                async with self.data_manager.get_data_lock():
                    session_data = await self.data_manager.load_session_data()
                    session_data.setdefault(session_id, {})["next_trigger_time"] = next_trigger_time
                    await self.data_manager.save_session_data(session_data)
                
                self.logger.info(
                    f"[主动消息] 已为会话 {session_id} 安排下一次主动聊天喵，"
                    f"时间：{run_date.strftime('%Y-%m-%d %H:%M:%S')} 喵。"
                )
            
        except Exception as e:
            self.logger.error(f"[主动消息] 安排下一次聊天失败: {e}")
    
    async def check_and_chat(self, session_id: str, is_auto_trigger: bool = False):
        """
        由定时任务触发的核心函数，负责完成一次完整的主动消息流程
        
        Args:
            session_id: 会话ID
            is_auto_trigger: 是否为自动触发（v1.0.0-beta.5新增）
        """
        try:
            # v1.0.0-beta.5 修复：检查插件是否已终止
            if self._is_terminated:
                self.logger.debug(f"[主动消息] 插件已终止，跳过任务执行喵: {session_id}")
                return
            
            # 检查是否允许聊天
            if not await self._is_chat_allowed(session_id):
                return
            
            session_config = self.config_manager.get_session_config(session_id)
            if not session_config:
                return
            
            # 检查未回复次数上限
            async with self.data_manager.get_data_lock():
                session_data = await self.data_manager.load_session_data()
                unanswered_count = session_data.get(session_id, {}).get("unanswered_count", 0)
                
                # v1.0.0-beta.5 修复：如果是自动触发，确保计数器从0开始
                if is_auto_trigger and unanswered_count > 0:
                    self.logger.info(
                        f"[主动消息] 自动触发检测到计数器为 {unanswered_count}，"
                        f"将其重置为0以确保第一次消息正确喵。"
                    )
                    session_data[session_id]["unanswered_count"] = 0
                    await self.data_manager.save_session_data(session_data)
                    unanswered_count = 0
                
                schedule_conf = self.config_manager.get_schedule_settings(
                    "private" if "friend" in session_id.lower() else "group"
                )
                max_unanswered = schedule_conf.get("max_unanswered_times", 3)
                
                if max_unanswered > 0 and unanswered_count >= max_unanswered:
                    self.logger.info(
                        f"[主动消息] 会话 {session_id} 的未回复次数 ({unanswered_count}) "
                        f"已达到上限 ({max_unanswered})，暂停主动消息喵。"
                    )
                    return
            
            # 准备LLM请求
            request_package = await self.llm_client.prepare_llm_request(session_id)
            if not request_package:
                await self._schedule_next_chat_and_save(session_id)
                return
            
            conv_id = request_package["conv_id"]
            pure_history_messages = request_package["history"]
            original_system_prompt = request_package["system_prompt"]
            
            # 构造Prompt
            motivation_template = session_config.get("proactive_prompt", "")
            now_str = datetime.now(self.timezone).strftime("%Y年%m月%d日 %H:%M")
            final_user_simulation_prompt = self.llm_client.construct_proactive_prompt(
                motivation_template, unanswered_count, now_str
            )
            
            self.logger.info("[主动消息] 已生成包含动机和时间的 Prompt 喵。")
            
            # 调用LLM生成回复
            llm_response_obj = await self.llm_client.generate_response(
                session_id, final_user_simulation_prompt, pure_history_messages, original_system_prompt
            )
            
            if llm_response_obj:
                response_text = self.llm_client.parse_llm_response(llm_response_obj)
                if response_text:
                    self.logger.info(f"[主动消息] LLM 已生成文本喵: '{response_text}'。")
                    
                    # 发送消息
                    await self._send_proactive_message(session_id, response_text)
                    
                    # 存档对话并重新调度
                    await self._finalize_and_reschedule(
                        session_id, conv_id, final_user_simulation_prompt,
                        response_text, unanswered_count,
                        is_auto_trigger=is_auto_trigger  # 传递自动触发标记
                    )
                    return
            
            # LLM调用失败或返回空内容
            self.logger.warning("[主动消息] LLM 调用失败或返回空内容，重新调度喵。")
            await self._schedule_next_chat_and_save(session_id)
            
        except Exception as e:
            # v1.0.0-beta.2 优化: 改进错误日志记录，分类处理不同类型的错误
            error_type = type(e).__name__
            error_msg = str(e)

            self.logger.error("[主动消息] check_and_chat 任务发生致命错误喵:")
            self.logger.error(f"[主动消息] 错误类型喵: {error_type}")
            self.logger.error(f"[主动消息] 错误信息喵: {error_msg}")
            self.logger.debug(f"[主动消息] 详细堆栈信息喵:\n{traceback.format_exc()}")

            # 根据错误类型进行不同的处理 - 复刻原代码第1637-1646行
            error_category = "unknown"
            if "RateLimitError" in error_type or "quota" in error_msg.lower():
                self.logger.warning("[主动消息] 检测到API限制错误，将延长重试间隔喵。")
                # 可以在这里添加更长的延迟逻辑
                error_category = "rate_limit"
            elif "Connection" in error_type or "Timeout" in error_type:
                self.logger.warning("[主动消息] 检测到连接错误，可能需要检查网络设置喵。")
                error_category = "connection"
            elif "Authentication" in error_type or "auth" in error_msg.lower():
                self.logger.error("[主动消息] 认证错误，请检查API密钥配置喵。")
                # 认证错误通常需要手动干预，可以暂停任务
                error_category = "authentication"
                return  # 认证错误不尝试重新调度
            
            self.logger.debug(f"[主动消息] 错误分类结果喵: {error_category}")

            # v1.0.0-beta.1 修复: 任务失败后也清理数据，避免残留
            try:
                async with self.data_manager.get_data_lock():
                    session_data = await self.data_manager.load_session_data()
                    if (
                        session_id in session_data
                        and "next_trigger_time" in session_data[session_id]
                    ):
                        del session_data[session_id]["next_trigger_time"]
                        await self.data_manager.save_session_data(session_data)
                        self.logger.debug(
                            f"[主动消息] 任务失败，清理会话 {session_id} 的定时任务数据喵。"
                        )
            except Exception as clean_e:
                self.logger.debug(f"[主动消息] 清理失败任务数据时出错喵: {clean_e}")

            # 错误恢复：尝试重新调度
            try:
                self.logger.info(f"[主动消息] 尝试重新调度会话 {session_id} 的任务喵。")
                await self._schedule_next_chat_and_save(session_id)
                self.logger.info(f"[主动消息] 会话 {session_id} 的任务重新调度成功喵。")
            except Exception as se:
                self.logger.error(f"[主动消息] 在错误处理中重新调度失败喵: {se}")
                self.logger.error(f"[主动消息] 会话 {session_id} 可能需要手动干预喵。")
    
    async def _is_chat_allowed(self, session_id: str) -> bool:
        """
        检查是否允许进行主动聊天
        """
        session_config = self.config_manager.get_session_config(session_id)
        if not session_config or not session_config.get("enable", False):
            return False
        
        schedule_conf = self.config_manager.get_schedule_settings(
            "private" if "friend" in session_id.lower() else "group"
        )
        
        if is_quiet_time(schedule_conf.get("quiet_hours", "1-7"), self.timezone):
            self.logger.info("[主动消息] 当前为免打扰时段，跳过并重新调度喵。")
            await self._schedule_next_chat_and_save(session_id)
            return False
        
        return True
    
    async def _send_proactive_message(self, session_id: str, text: str):
        """
        负责处理主动消息的发送逻辑，包括TTS语音和文本消息。
        完整复刻v1.0.0-beta.4的L1305-1361逻辑。

        发送流程：
        1. 检查TTS配置，如果启用则尝试生成语音
        2. 如果TTS成功，发送语音消息
        3. 根据配置决定是否同时发送文本原文
        4. 如果TTS失败或禁用，直接发送文本消息

        特别处理：如果是群聊消息，发送后会立即重置沉默倒计时，
        因为Bot发送消息也意味着群聊有活动。
        """
        try:
            session_config = self.config_manager.get_session_config(session_id)
            if not session_config:
                self.logger.info(f"[主动消息] 无法获取会话配置，跳过消息发送喵: {session_id}")
                return

            self.logger.info(f"[主动消息] 开始发送主动消息喵，会话ID: {session_id}")

            tts_conf = session_config.get("tts_settings", {})
            is_tts_sent = False
            
            if tts_conf.get("enable_tts", True):
                try:
                    self.logger.info("[主动消息] 尝试进行手动 TTS 喵。")
                    # 获取TTS提供商
                    tts_provider = await self.context.get_using_tts_provider(umo=session_id)
                    if tts_provider:
                        audio_path = await tts_provider.get_audio(text)
                        if audio_path:
                            await self.context.send_message(
                                session_id, MessageChain([Record(file=audio_path)])
                            )
                            is_tts_sent = True
                            await asyncio.sleep(0.5)  # 确保先收到语音消息
                except Exception as e:
                    # 更友好的TTS错误信息，避免用户困惑
                    error_type = type(e).__name__
                    error_msg = str(e)
                    
                    # 常见的TTS相关错误模式
                    if "await" in error_msg and "expression" in error_msg:
                        self.logger.info(
                            f"[主动消息] TTS服务接口异常喵，将回退到文本消息: {error_type}"
                        )
                    elif any(keyword in error_msg.lower() for keyword in ["language", "lang", "语音", "语言"]):
                        self.logger.info(
                            f"[主动消息] TTS语言处理异常喵，将回退到文本消息: {error_type}"
                        )
                    else:
                        self.logger.info(
                            f"[主动消息] TTS处理异常喵，将回退到文本消息: {error_type}"
                        )
                    
                    self.logger.debug(f"[主动消息] TTS详细错误信息喵: {error_msg}")

            if not is_tts_sent or tts_conf.get("always_send_text", True):
                await self.context.send_message(
                    session_id, MessageChain([Plain(text=text)])
                )

            # v1.0.0-beta.1 修复: Bot 自己发送的消息，也应该被视为一次"活动"，重置群聊的沉默倒计时
            # 注意：这里需要立即重置沉默倒计时，因为Bot发送消息意味着群聊有活动
            if "group" in session_id.lower():
                # 立即重置，不要等待，确保时序正确
                await self.silence_timer_manager.reset_silence_timer(session_id)
                self.logger.info(
                    f"[主动消息] Bot主动消息已发送，已重置群聊 {session_id} 的沉默倒计时喵。"
                )
                
                # v1.0.0-beta.1 修复: 记录Bot发送消息的时间，用于辅助检测Bot消息
                # 有些平台下self_id可能获取不到，我们可以通过发送时间来辅助判断
                self.session_manager.record_bot_message_time()
                # 注意：群聊的next_trigger_time在任务成功完成后会被清理，这是正确的行为
                # 因为群聊使用监控沉默倒计时与APScheduler结合的机制，而不是固定的APScheduler任务
            
        except Exception as e:
            self.logger.error(f"[主动消息] 发送主动消息失败喵: {e}")
    
    async def _finalize_and_reschedule(self, session_id: str, conv_id: str,
                                     user_prompt: str, assistant_response: str,
                                     unanswered_count: int, is_auto_trigger: bool = False):
        """
        负责主动消息任务完成后的收尾工作，包括：
        1. 存档对话历史（使用add_message_pair）
        2. 更新未回复计数器
        3. 重新调度下一个任务（仅私聊）
        4. 保存所有状态到持久化存储

        v1.0.0-beta.1 重要更新：此函数现在区分私聊和群聊的处理逻辑：
        - 私聊：立即重新调度下一个主动消息任务
        - 群聊：清理定时任务数据，使用沉默倒计时机制
        完整复刻v1.0.0-beta.4的L1362-1451逻辑。
        """
        try:
            # 1. 使用新的对话管理API存档对话历史
            try:
                user_msg_obj = UserMessageSegment(content=[TextPart(text=user_prompt)])
                assistant_msg_obj = AssistantMessageSegment(content=[TextPart(text=assistant_response)])

                await self.context.conversation_manager.add_message_pair(
                    cid=conv_id,
                    user_message=user_msg_obj,
                    assistant_message=assistant_msg_obj,
                )
                self.logger.info(
                    "[主动消息] 已成功将本次主动对话存档至对话历史喵 (使用新的add_message_pair API)。"
                )
            except Exception as e:
                self.logger.error(f"[主动消息] 存档对话历史失败喵: {e}")
                # v1.0.0-beta.2 优化: 存档失败时不中断主流程，只记录错误
                self.logger.warning("[主动消息] 对话存档失败喵，但会继续执行后续步骤喵。")

            # 2. 然后再获取锁，执行关键区代码（AI审查建议：优化锁的使用范围）
            async with self.data_manager.get_data_lock():
                # 更新计数器 (对私聊和群聊都适用)
                # v1.0.0-beta.1 修复: 计数器逻辑
                # 只有在Bot成功发送消息给用户后，才增加未回复计数器
                # 每个会话(私聊/群聊)都有独立的计数器，不会相互影响
                
                # v1.0.0-beta.5 修复：如果是自动触发，确保从0开始计数
                if is_auto_trigger and unanswered_count > 0:
                    self.logger.info(
                        f"[主动消息] 自动触发消息，将计数器从 {unanswered_count} 重置为 0 喵。"
                    )
                    unanswered_count = 0
                
                new_unanswered_count = unanswered_count + 1
                session_data = await self.data_manager.load_session_data()
                session_data.setdefault(session_id, {})["unanswered_count"] = new_unanswered_count
                self.logger.info(
                    f"[主动消息] 会话 {session_id} 的第 {new_unanswered_count} 次主动消息已发送完成，当前未回复次数: {new_unanswered_count} 次喵。"
                )

                # 重新调度 (v1.0.0-beta.1 修复: 只对私聊进行立即的、连续的重新调度)
                if "private" in session_id.lower() or "friendmessage" in session_id.lower():
                    session_config = self.config_manager.get_session_config(session_id)
                    if not session_config:
                        return
                    schedule_conf = session_config.get("schedule_settings", {})

                    min_interval = int(schedule_conf.get("min_interval_minutes", 30)) * 60
                    max_interval = max(
                        min_interval,
                        int(schedule_conf.get("max_interval_minutes", 900)) * 60,
                    )
                    random_interval = random.randint(min_interval, max_interval)
                    next_trigger_time = time.time() + random_interval
                    run_date = datetime.fromtimestamp(next_trigger_time, tz=self.timezone)

                    self.scheduler.add_job(
                        self.check_and_chat,
                        "date",
                        run_date=run_date,
                        args=[session_id],
                        id=session_id,
                        replace_existing=True,
                        misfire_grace_time=60,
                    )

                    session_data.setdefault(session_id, {})["next_trigger_time"] = next_trigger_time
                    self.logger.info(
                        f"[主动消息] 已为私聊会话 {session_id} 安排下一次主动聊天喵，时间：{run_date.strftime('%Y-%m-%d %H:%M:%S')} 喵。"
                    )

                # 保存所有状态
                await self.data_manager.save_session_data(session_data)
            
        except Exception as e:
            self.logger.error(f"[主动消息] 收尾工作失败喵: {e}")
    
    def _build_event_data(self, event: AstrMessageEvent) -> Dict[str, Any]:
        """
        构建事件数据，包含发送者信息 - 复刻原代码第962-977行逻辑
        
        Args:
            event: AstrMessageEvent事件对象
            
        Returns:
            事件数据字典
        """
        event_data = {}
        
        try:
            # 获取发送者ID - 复刻原代码第965-974行
            sender_id = None
            if hasattr(event, "message_obj") and event.message_obj:
                if hasattr(event.message_obj, "sender") and event.message_obj.sender:
                    sender = event.message_obj.sender
                    sender_id = getattr(sender, "id", None) or getattr(sender, "user_id", None)
            
            # 备用获取方式 - 复刻原代码第971-974行
            if not sender_id:
                sender_id = getattr(event, "user_id", None) or getattr(event, "sender_id", None)
            
            if sender_id:
                event_data["user_id"] = str(sender_id)
                event_data["sender_id"] = str(sender_id)
            
            # 添加source信息用于Bot检测
            if hasattr(event, "source"):
                event_data["source"] = str(event.source)
            
            # 添加self_id和user_id用于ID匹配检测
            if hasattr(event, "self_id"):
                event_data["self_id"] = str(event.self_id)
            if hasattr(event, "user_id"):
                event_data["user_id"] = str(event.user_id)
                
        except Exception as e:
            self.logger.debug(f"[主动消息] 构建事件数据时出错喵（可忽略）: {e}")
        
        return event_data


# 保持与原插件的兼容性
def is_quiet_time(quiet_hours_str: str, tz: zoneinfo.ZoneInfo) -> bool:
    """检查当前时间是否处于免打扰时段。"""
    try:
        from .utils.time_utils import is_quiet_time as quiet_time_check
    except ImportError:
        from utils.time_utils import is_quiet_time as quiet_time_check
    return quiet_time_check(quiet_hours_str, tz)