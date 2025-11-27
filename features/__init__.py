"""
功能模块 - 主动消息插件的业务功能
包含自动触发、沉默计时、消息处理等功能
"""

from .auto_trigger import AutoTriggerManager
from .silence_timer import SilenceTimerManager
from .message_handler import MessageHandler

__all__ = ['AutoTriggerManager', 'SilenceTimerManager', 'MessageHandler']