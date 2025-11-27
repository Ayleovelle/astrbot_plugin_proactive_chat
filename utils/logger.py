"""
日志工具模块 - 负责日志记录和管理
使用AstrBot官方的日志接口
"""

from typing import Optional, Any, Dict
import traceback
import datetime


class LoggerAdapter:
    """
    日志适配器 - 为模块提供统一的AstrBot日志接口
    包装AstrBot的logger，提供模块化的日志功能
    保持与原代码完全一致的日志格式
    """
    
    def __init__(self, module_name: str = "proactive_chat"):
        """
        初始化日志适配器
        
        Args:
            module_name: 模块名称，用于日志前缀
        """
        self.module_name = module_name
        # 移除双重前缀，保持与原代码一致的单一前缀
        self.prefix = ""
    
    def _get_logger(self):
        """
        获取AstrBot的logger实例
        使用延迟导入避免循环依赖
        """
        try:
            from astrbot.api import logger
            return logger
        except ImportError:
            # 如果AstrBot logger不可用，使用简单的print
            class SimpleLogger:
                def info(self, msg): 
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] [INFO] [主动消息] {msg}")
                def debug(self, msg): 
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] [DEBUG] [主动消息] {msg}")
                def warning(self, msg): 
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] [WARNING] [主动消息] {msg}")
                def error(self, msg): 
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] [ERROR] [主动消息] {msg}")
                def exception(self, msg): 
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] [EXCEPTION] [主动消息] {msg}")
                    print(f"[{timestamp}] [EXCEPTION] [主动消息] 详细信息:")
                    print(traceback.format_exc())
            return SimpleLogger()
    
    def debug(self, msg: str, **kwargs):
        """记录调试日志"""
        logger = self._get_logger()
        logger.debug(f"{self.prefix}{msg}", **kwargs)
    
    def info(self, msg: str, **kwargs):
        """记录信息日志"""
        logger = self._get_logger()
        # 避免双重前缀，如果消息已经包含前缀则不再添加
        if msg.startswith("[主动消息]"):
            logger.info(msg, **kwargs)
        else:
            logger.info(f"{self.prefix}{msg}", **kwargs)
    
    def warning(self, msg: str, **kwargs):
        """记录警告日志"""
        logger = self._get_logger()
        # 避免双重前缀，如果消息已经包含前缀则不再添加
        if msg.startswith("[主动消息]"):
            logger.warning(msg, **kwargs)
        else:
            logger.warning(f"{self.prefix}{msg}", **kwargs)
    
    def error(self, msg: str, **kwargs):
        """记录错误日志"""
        logger = self._get_logger()
        # 避免双重前缀，如果消息已经包含前缀则不再添加
        if msg.startswith("[主动消息]"):
            logger.error(msg, **kwargs)
        else:
            logger.error(f"{self.prefix}{msg}", **kwargs)
    
    def exception(self, msg: str, **kwargs):
        """记录异常日志"""
        logger = self._get_logger()
        # 避免双重前缀，如果消息已经包含前缀则不再添加
        if msg.startswith("[主动消息]"):
            logger.exception(msg, **kwargs)
        else:
            logger.exception(f"{self.prefix}{msg}", **kwargs)
    
    def log_api_call(self, api_name: str, success: bool, duration_ms: Optional[float] = None, 
                    error_msg: Optional[str] = None):
        """
        记录API调用日志
        
        Args:
            api_name: API名称
            success: 是否成功
            duration_ms: 调用耗时（毫秒）
            error_msg: 错误信息（如果有）
        """
        if success:
            if duration_ms:
                self.info(f"[主动消息] API调用成功喵: {api_name} (耗时: {duration_ms:.2f}ms)")
            else:
                self.info(f"[主动消息] API调用成功喵: {api_name}")
        else:
            if error_msg:
                self.error(f"[主动消息] API调用失败喵: {api_name} - {error_msg}")
            else:
                self.error(f"[主动消息] API调用失败喵: {api_name}")
    
    def log_config_validation(self, errors: list, warnings: list):
        """
        记录配置验证日志
        
        Args:
            errors: 错误列表
            warnings: 警告列表
        """
        if errors:
            for error in errors:
                self.error(f"[主动消息] 配置验证错误喵: {error}")
        
        if warnings:
            for warning in warnings:
                self.warning(f"[主动消息] 配置验证警告喵: {warning}")
        
        if not errors and not warnings:
            self.info("[主动消息] 配置验证通过喵，未发现明显问题")
    
    def log_session_event(self, session_id: str, event_type: str, details: Optional[str] = None):
        """
        记录会话事件日志
        
        Args:
            session_id: 会话ID
            event_type: 事件类型
            details: 详细信息
        """
        if details:
            self.info(f"[主动消息] 会话事件喵: {session_id} - {event_type} - {details}")
        else:
            self.info(f"[主动消息] 会话事件喵: {session_id} - {event_type}")
    
    def log_task_scheduled(self, session_id: str, trigger_time: datetime, task_type: str = "normal"):
        """
        记录任务调度日志
        
        Args:
            session_id: 会话ID
            trigger_time: 触发时间
            task_type: 任务类型
        """
        time_str = trigger_time.strftime("%Y-%m-%d %H:%M:%S")
        self.info(f"[主动消息] 任务已调度喵: {session_id} - 时间: {time_str} - 类型: {task_type}")
    
    def log_task_executed(self, session_id: str, success: bool, duration_ms: Optional[float] = None,
                         error_msg: Optional[str] = None):
        """
        记录任务执行日志
        
        Args:
            session_id: 会话ID
            success: 是否成功
            duration_ms: 执行耗时（毫秒）
            error_msg: 错误信息（如果有）
        """
        if success:
            if duration_ms:
                self.info(f"[主动消息] 任务执行成功喵: {session_id} (耗时: {duration_ms:.2f}ms)")
            else:
                self.info(f"[主动消息] 任务执行成功喵: {session_id}")
        else:
            if error_msg:
                self.error(f"[主动消息] 任务执行失败喵: {session_id} - {error_msg}")
            else:
                self.error(f"[主动消息] 任务执行失败喵: {session_id}")
    
    def log_data_operation(self, operation: str, session_id: Optional[str] = None, 
                          success: bool = True, details: Optional[str] = None):
        """
        记录数据操作日志
        
        Args:
            operation: 操作类型
            session_id: 会话ID（可选）
            success: 是否成功
            details: 详细信息
        """
        session_info = f"会话 {session_id}" if session_id else "全局数据"
        
        if success:
            if details:
                self.info(f"[主动消息] 数据操作成功喵: {operation} - {session_info} - {details}")
            else:
                self.info(f"[主动消息] 数据操作成功喵: {operation} - {session_info}")
        else:
            if details:
                self.error(f"[主动消息] 数据操作失败喵: {operation} - {session_info} - {details}")
            else:
                self.error(f"[主动消息] 数据操作失败喵: {operation} - {session_info}")
    
    def log_timer_event(self, timer_type: str, session_id: str, action: str, 
                       delay_seconds: Optional[int] = None):
        """
        记录计时器事件日志
        
        Args:
            timer_type: 计时器类型
            session_id: 会话ID
            action: 动作（设置/取消/触发）
            delay_seconds: 延迟时间（秒）
        """
        if delay_seconds:
            minutes = delay_seconds // 60
            self.info(f"[主动消息] 计时器事件喵: {timer_type} - {session_id} - {action} - {minutes}分钟")
        else:
            self.info(f"[主动消息] 计时器事件喵: {timer_type} - {session_id} - {action}")
    
    def log_llm_interaction(self, session_id: str, stage: str, success: bool = True,
                           details: Optional[str] = None):
        """
        记录LLM交互日志
        
        Args:
            session_id: 会话ID
            stage: 交互阶段（准备/调用/响应/存档）
            success: 是否成功
            details: 详细信息
        """
        if success:
            if details:
                self.info(f"[主动消息] LLM交互喵: {session_id} - {stage} - {details}")
            else:
                self.info(f"[主动消息] LLM交互喵: {session_id} - {stage}")
        else:
            if details:
                self.error(f"[主动消息] LLM交互失败喵: {session_id} - {stage} - {details}")
            else:
                self.error(f"[主动消息] LLM交互失败喵: {session_id} - {stage}")
    
    def get_logger_stats(self) -> Dict[str, Any]:
        """
        获取日志器统计信息
        
        Returns:
            统计信息字典
        """
        return {
            "module_name": self.module_name,
            "prefix": self.prefix,
            "logger_available": self._get_logger() is not None,
            "timestamp": datetime.datetime.now().isoformat()
        }


# 默认日志适配器
default_logger = LoggerAdapter("proactive_chat")


def get_logger(module_name: str = "proactive_chat") -> LoggerAdapter:
    """
    获取日志适配器
    
    Args:
        module_name: 模块名称
        
    Returns:
        日志适配器实例
    """
    return LoggerAdapter(module_name)