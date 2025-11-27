"""
调度器模块 - 负责任务调度和时间管理
基于原main.py中的调度逻辑重构
"""

import asyncio
import time
import random
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List
from apscheduler.schedulers.asyncio import AsyncIOScheduler


class Scheduler:
    """
    调度器 - 负责任务调度和时间管理
    
    主要职责：
    1. APScheduler任务管理
    2. 时间计算和随机间隔生成
    3. 任务恢复和清理
    4. 异步计时器管理
    5. 任务过期检查和宽限期处理
    """
    
    def __init__(self, timezone=None, logger=None):
        """
        初始化调度器
        
        Args:
            timezone: 时区信息
            logger: 日志记录器
        """
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.timezone = timezone
        self.logger = logger
        self.group_timers: Dict[str, asyncio.TimerHandle] = {}
        self.auto_trigger_timers: Dict[str, asyncio.TimerHandle] = {}
        
    def start(self) -> bool:
        """启动调度器"""
        try:
            self.scheduler.start()
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 启动调度器失败喵: {e}")
            else:
                print(f"[主动消息] 启动调度器失败喵: {e}")
            return False
        
    def shutdown(self) -> bool:
        """关闭调度器"""
        try:
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
                return True
            return False
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 关闭调度器失败喵: {e}")
            else:
                print(f"[主动消息] 关闭调度器失败喵: {e}")
            return False
    
    def add_job(self, func: Callable, trigger: str, run_date: datetime,
                args: list = None, job_id: str = None, **kwargs) -> bool:
        """
        添加调度任务
        
        Args:
            func: 要执行的函数
            trigger: 触发器类型
            run_date: 执行时间
            args: 函数参数
            job_id: 任务ID
            **kwargs: 其他参数
            
        Returns:
            是否成功添加
        """
        try:
            # 构建参数，避免冲突
            job_args = {
                "func": func,
                "trigger": trigger,
                "run_date": run_date,
                "args": args or [],
                "id": job_id,
            }
            
            # 只在kwargs中没有这些参数时才添加默认值
            if "replace_existing" not in kwargs:
                job_args["replace_existing"] = True
            if "misfire_grace_time" not in kwargs:
                job_args["misfire_grace_time"] = 60
                
            # 添加其他kwargs参数
            job_args.update(kwargs)
            
            self.scheduler.add_job(**job_args)
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 添加调度任务失败喵: {e}")
            else:
                print(f"[主动消息] 添加调度任务失败喵: {e}")
            return False
    
    def remove_job(self, job_id: str) -> bool:
        """
        移除调度任务
        
        Args:
            job_id: 任务ID
            
        Returns:
            是否成功移除
        """
        try:
            self.scheduler.remove_job(job_id)
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 移除调度任务失败喵: {e}")
            else:
                print(f"[主动消息] 移除调度任务失败喵: {e}")
            return False
    
    def get_job(self, job_id: str):
        """
        获取调度任务
        
        Args:
            job_id: 任务ID
            
        Returns:
            任务对象或None
        """
        try:
            return self.scheduler.get_job(job_id)
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 获取调度任务失败喵: {e}")
            else:
                print(f"[主动消息] 获取调度任务失败喵: {e}")
            return None
    
    def get_all_jobs(self) -> List:
        """
        获取所有调度任务
        
        Returns:
            任务列表
        """
        try:
            return self.scheduler.get_jobs()
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 获取所有调度任务失败喵: {e}")
            else:
                print(f"[主动消息] 获取所有调度任务失败喵: {e}")
            return []
    
    def calculate_next_trigger_time(self, min_interval_minutes: int, 
                                  max_interval_minutes: int) -> float:
        """
        计算下一次触发时间
        
        Args:
            min_interval_minutes: 最小间隔（分钟）
            max_interval_minutes: 最大间隔（分钟）
            
        Returns:
            下次触发时间戳
        """
        min_interval = min_interval_minutes * 60
        max_interval = max(min_interval, max_interval_minutes * 60)
        random_interval = random.randint(min_interval, max_interval)
        return time.time() + random_interval
    
    def create_run_date(self, trigger_time: float) -> datetime:
        """
        创建运行时间对象
        
        Args:
            trigger_time: 触发时间戳
            
        Returns:
            datetime对象
        """
        return datetime.fromtimestamp(trigger_time, tz=self.timezone)
    
    def is_task_expired(self, next_trigger_time: float, grace_period_seconds: int = 60) -> bool:
        """
        检查任务是否过期（给予宽限期）
        
        Args:
            next_trigger_time: 下次触发时间戳
            grace_period_seconds: 宽限期（秒）
            
        Returns:
            是否已过期
        """
        current_time = time.time()
        trigger_time_with_grace = next_trigger_time + grace_period_seconds
        return current_time >= trigger_time_with_grace
    
    def get_time_until_trigger(self, next_trigger_time: float) -> int:
        """
        获取距离触发还有多少秒
        
        Args:
            next_trigger_time: 下次触发时间戳
            
        Returns:
            剩余秒数，如果已过期则返回0
        """
        current_time = time.time()
        remaining = int(next_trigger_time - current_time)
        return max(0, remaining)
    
    # === 群聊沉默计时器管理 ===
    
    def set_group_timer(self, session_id: str, delay_seconds: int, 
                       callback: Callable) -> bool:
        """
        设置群聊沉默计时器
        
        Args:
            session_id: 会话ID
            delay_seconds: 延迟时间（秒）
            callback: 回调函数
            
        Returns:
            是否成功设置
        """
        try:
            # 取消现有的计时器
            self.cancel_group_timer(session_id)
            
            loop = asyncio.get_running_loop()
            timer = loop.call_later(delay_seconds, callback)
            self.group_timers[session_id] = timer
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 设置群聊沉默计时器失败喵: {e}")
            else:
                print(f"[主动消息] 设置群聊沉默计时器失败喵: {e}")
            return False
    
    def cancel_group_timer(self, session_id: str) -> bool:
        """
        取消群聊沉默计时器
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功取消
        """
        if session_id in self.group_timers:
            try:
                self.group_timers[session_id].cancel()
                del self.group_timers[session_id]
                return True
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[主动消息] 取消群聊沉默计时器失败喵: {e}")
                else:
                    print(f"[主动消息] 取消群聊沉默计时器失败喵: {e}")
                return False
        return True
    
    def has_group_timer(self, session_id: str) -> bool:
        """
        检查是否存在群聊计时器
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否存在
        """
        return session_id in self.group_timers
    
    def clear_all_group_timers(self) -> int:
        """
        清理所有群聊计时器
        
        Returns:
            清理的计时器数量
        """
        count = len(self.group_timers)
        for session_id, timer in list(self.group_timers.items()):
            try:
                timer.cancel()
            except Exception:
                pass
        self.group_timers.clear()
        return count
    
    # === 自动触发计时器管理 ===
    
    def set_auto_trigger_timer(self, session_id: str, delay_seconds: int, 
                              callback: Callable) -> bool:
        """
        设置自动触发计时器
        
        Args:
            session_id: 会话ID
            delay_seconds: 延迟时间（秒）
            callback: 回调函数
            
        Returns:
            是否成功设置
        """
        try:
            # 取消现有的计时器
            self.cancel_auto_trigger_timer(session_id)
            
            loop = asyncio.get_running_loop()
            timer = loop.call_later(delay_seconds, callback)
            self.auto_trigger_timers[session_id] = timer
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 设置自动触发计时器失败喵: {e}")
            else:
                print(f"[主动消息] 设置自动触发计时器失败喵: {e}")
            return False
    
    def cancel_auto_trigger_timer(self, session_id: str) -> bool:
        """
        取消自动触发计时器
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功取消
        """
        if session_id in self.auto_trigger_timers:
            try:
                self.auto_trigger_timers[session_id].cancel()
                del self.auto_trigger_timers[session_id]
                return True
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[主动消息] 取消自动触发计时器失败喵: {e}")
                else:
                    print(f"[主动消息] 取消自动触发计时器失败喵: {e}")
                return False
        return True
    
    def has_auto_trigger_timer(self, session_id: str) -> bool:
        """
        检查是否存在自动触发计时器
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否存在
        """
        return session_id in self.auto_trigger_timers
    
    def clear_all_auto_trigger_timers(self) -> int:
        """
        清理所有自动触发计时器
        
        Returns:
            清理的计时器数量
        """
        count = len(self.auto_trigger_timers)
        for session_id, timer in list(self.auto_trigger_timers.items()):
            try:
                timer.cancel()
            except Exception:
                pass
        self.auto_trigger_timers.clear()
        return count
    
    def get_timer_count(self) -> int:
        """
        获取计时器数量
        
        Returns:
            计时器数量
        """
        return len(self.group_timers) + len(self.auto_trigger_timers)