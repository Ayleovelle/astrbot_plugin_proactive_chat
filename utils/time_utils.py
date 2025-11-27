"""
时间工具模块 - 负责时间相关的工具函数
基于原main.py中的时间工具函数重构
"""

import zoneinfo
from datetime import datetime
from typing import Optional


def is_quiet_time(quiet_hours_str: str, tz: Optional[zoneinfo.ZoneInfo]) -> bool:
    """
    检查当前时间是否处于免打扰时段
    
    Args:
        quiet_hours_str: 免打扰时段字符串，格式如 "1-7"
        tz: 时区信息
        
    Returns:
        是否处于免打扰时段
    """
    try:
        start_str, end_str = quiet_hours_str.split("-")
        start_hour, end_hour = int(start_str), int(end_str)
        now = datetime.now(tz) if tz else datetime.now()
        
        # 处理跨天的情况 (例如 23-7)
        if start_hour <= end_hour:
            return start_hour <= now.hour < end_hour
        else:
            return now.hour >= start_hour or now.hour < end_hour
            
    except (ValueError, TypeError):
        return False


def calculate_time_intervals(minutes: int) -> int:
    """
    将分钟转换为秒
    
    Args:
        minutes: 分钟数
        
    Returns:
        秒数
    """
    return minutes * 60


def format_datetime(dt: datetime, format_str: str = "%Y年%m月%d日 %H:%M") -> str:
    """
    格式化日期时间
    
    Args:
        dt: 日期时间对象
        format_str: 格式字符串
        
    Returns:
        格式化后的字符串
    """
    return dt.strftime(format_str)


def parse_timezone(tz_str: str) -> Optional[zoneinfo.ZoneInfo]:
    """
    解析时区字符串
    
    Args:
        tz_str: 时区字符串
        
    Returns:
        时区对象，失败时返回None
    """
    try:
        return zoneinfo.ZoneInfo(tz_str)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return None


def get_current_time(tz: Optional[zoneinfo.ZoneInfo] = None) -> datetime:
    """
    获取当前时间
    
    Args:
        tz: 时区信息
        
    Returns:
        当前时间对象
    """
    return datetime.now(tz) if tz else datetime.now()


def is_valid_time_range(time_range: str) -> bool:
    """
    检查时间范围格式是否有效
    
    Args:
        time_range: 时间范围字符串，格式如 "1-7"
        
    Returns:
        是否有效
    """
    try:
        start_str, end_str = time_range.split("-")
        start_hour, end_hour = int(start_str), int(end_str)
        
        # 检查小时数是否在有效范围内
        if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
            return False
            
        return True
        
    except (ValueError, AttributeError):
        return False


def calculate_quiet_time_remaining(quiet_hours_str: str, tz: Optional[zoneinfo.ZoneInfo]) -> int:
    """
    计算距离免打扰时段结束还有多少分钟
    
    Args:
        quiet_hours_str: 免打扰时段字符串，格式如 "1-7"
        tz: 时区信息
        
    Returns:
        剩余分钟数，如果当前不在免打扰时段则返回0
    """
    if not is_quiet_time(quiet_hours_str, tz):
        return 0
    
    try:
        start_str, end_str = quiet_hours_str.split("-")
        start_hour, end_hour = int(start_str), int(end_str)
        now = datetime.now(tz) if tz else datetime.now()
        
        if start_hour <= end_hour:
            # 不跨天的情况
            if now.hour < end_hour:
                return (end_hour - now.hour) * 60 - now.minute
            else:
                return 0
        else:
            # 跨天的情况
            if now.hour >= start_hour:
                # 当前时间在第一天
                return (24 - now.hour + end_hour) * 60 - now.minute
            elif now.hour < end_hour:
                # 当前时间在第二天
                return (end_hour - now.hour) * 60 - now.minute
            else:
                return 0
                
    except (ValueError, TypeError):
        return 0


def get_time_until_next_hour(hour: int, tz: Optional[zoneinfo.ZoneInfo] = None) -> int:
    """
    获取距离指定小时还有多少分钟
    
    Args:
        hour: 目标小时（0-23）
        tz: 时区信息
        
    Returns:
        剩余分钟数
    """
    try:
        now = datetime.now(tz) if tz else datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        
        if hour == current_hour:
            return 0
        elif hour > current_hour:
            return (hour - current_hour) * 60 - current_minute
        else:
            # 跨天的情况
            return (24 - current_hour + hour) * 60 - current_minute
            
    except (ValueError, TypeError):
        return 0


def is_time_in_range(start_hour: int, end_hour: int, check_hour: int) -> bool:
    """
    检查指定小时是否在时间范围内
    
    Args:
        start_hour: 开始小时
        end_hour: 结束小时
        check_hour: 要检查的小时
        
    Returns:
        是否在范围内
    """
    try:
        # 处理跨天的情况
        if start_hour <= end_hour:
            return start_hour <= check_hour < end_hour
        else:
            return check_hour >= start_hour or check_hour < end_hour
            
    except (ValueError, TypeError):
        return False


def format_time_duration(seconds: int) -> str:
    """
    格式化时间持续时间为易读的字符串
    
    Args:
        seconds: 秒数
        
    Returns:
        格式化的时间字符串
    """
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        if remaining_seconds == 0:
            return f"{minutes}分钟"
        else:
            return f"{minutes}分钟{remaining_seconds}秒"
    else:
        hours = seconds // 3600
        remaining_minutes = (seconds % 3600) // 60
        if remaining_minutes == 0:
            return f"{hours}小时"
        else:
            return f"{hours}小时{remaining_minutes}分钟"


def parse_time_string(time_str: str) -> Optional[int]:
    """
    解析时间字符串为小时数
    
    Args:
        time_str: 时间字符串，格式如 "14:30" 或 "14"
        
    Returns:
        小时数（0-23），失败时返回None
    """
    try:
        if ":" in time_str:
            # 格式: "14:30"
            hour_str, _ = time_str.split(":")
            hour = int(hour_str)
        else:
            # 格式: "14"
            hour = int(time_str)
        
        if 0 <= hour <= 23:
            return hour
        else:
            return None
            
    except (ValueError, AttributeError):
        return None