"""
数据管理模块 - 负责会话数据的持久化和管理
基于原main.py中的数据持久化逻辑重构
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
import aiofiles
import aiofiles.os as aio_os


class DataManager:
    """
    数据管理器 - 负责会话数据的持久化和管理
    
    主要职责：
    1. 会话数据的异步加载和保存
    2. 数据验证和清理
    3. 会话隔离的数据管理
    4. 数据备份和恢复
    """
    
    def __init__(self, data_dir: Path, session_data_file: Path, logger=None):
        """
        初始化数据管理器
        
        Args:
            data_dir: 数据目录路径
            session_data_file: 会话数据文件路径
            logger: 日志记录器
        """
        self.data_dir = data_dir
        self.session_data_file = session_data_file
        self.logger = logger
        self.data_lock = asyncio.Lock()
        self._session_data_cache: Optional[Dict[str, Any]] = None
        
        # 不再在这里创建异步任务，改为延迟初始化
    
    async def ensure_data_dir_exists(self) -> bool:
        """
        确保数据目录存在
        
        Returns:
            是否成功创建
        """
        try:
            await aio_os.makedirs(self.data_dir, exist_ok=True)
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 创建数据目录失败喵: {e}")
            return False
    
    def get_data_lock(self) -> asyncio.Lock:
        """
        获取数据锁
        
        Returns:
            数据锁对象
        """
        return self.data_lock
    
    async def load_session_data(self) -> Dict[str, Any]:
        """
        异步加载会话数据
        
        Returns:
            会话数据字典
        """
        if self._session_data_cache is not None:
            if self.logger:
                self.logger.debug(f"[主动消息] 使用缓存的会话数据，共 {len(self._session_data_cache)} 个会话喵。")
            return self._session_data_cache
            
        # 复刻v1.0.0-beta.4的设计：确保数据目录存在
        await self.ensure_data_dir_exists()
        
        async with self.data_lock:
            result = await self._load_data_internal()
            return result
    
    async def _load_data_internal(self) -> Dict[str, Any]:
        """
        内部实现：从文件中加载会话数据（异步无锁内部实现）

        这是数据持久化的核心函数之一，负责：
        1. 检查会话数据文件是否存在
        2. 异步读取文件内容
        3. 解析JSON格式的会话数据
        4. 处理可能的异常情况（文件不存在、JSON解析错误等）

        使用ensure_ascii=False保证中文字符正常保存，indent=4提高可读性。
        此函数必须在持有data_lock的情况下被调用，确保数据一致性。
        """
        try:
            if await aio_os.path.exists(self.session_data_file):
                async with aiofiles.open(self.session_data_file, encoding="utf-8") as f:
                    content = await f.read()
                    if content.strip():  # 检查内容是否为空
                        data = await asyncio.to_thread(json.loads, content)
                        self._session_data_cache = data
                        if self.logger:
                            self.logger.info(f"[主动消息] 成功加载会话数据，共 {len(data)} 个会话喵。")
                        return data
                    else:
                        if self.logger:
                            self.logger.debug(f"[主动消息] 会话数据文件为空，使用空数据启动喵。")
                        self._session_data_cache = {}
                        return {}
            else:
                if self.logger:
                    self.logger.debug(f"[主动消息] 会话数据文件不存在，创建新的数据文件喵。")
                self._session_data_cache = {}
                return {}
                
        except (OSError, json.JSONDecodeError) as e:
            if self.logger:
                self.logger.error(
                    f"[主动消息] 加载会话数据失败喵: {e}，将使用空数据启动喵。"
                )
            self._session_data_cache = {}
            return {}
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 内部数据加载发生未知错误喵: {e}")
            self._session_data_cache = {}
            return {}
    
    async def save_session_data(self, data: Dict[str, Any]) -> bool:
        """
        异步保存会话数据
        
        Args:
            data: 要保存的会话数据
            
        Returns:
            是否成功保存
        """
        async with self.data_lock:
            return await self._save_data_internal(data)
    
    async def _save_data_internal(self, data: Dict[str, Any]) -> bool:
        """
        内部实现：将会话数据保存到文件（异步无锁内部实现）
        
        这是数据持久化的核心函数之一，负责：
        1. 确保数据目录存在（如果不存在则创建）
        2. 异步打开会话数据文件
        3. 将data字典转换为JSON格式
        4. 异步写入文件内容
        5. 处理可能的IO异常
        
        使用ensure_ascii=False保证中文字符正常保存，indent=4提高可读性。
        此函数必须在持有data_lock的情况下被调用，确保数据一致性。
        """
        try:
            # 确保数据目录存在
            await aio_os.makedirs(self.data_dir, exist_ok=True)
            
            # 异步写入文件
            async with aiofiles.open(
                self.session_data_file, "w", encoding="utf-8"
            ) as f:
                content_to_write = await asyncio.to_thread(
                    json.dumps, data, indent=4, ensure_ascii=False
                )
                await f.write(content_to_write)
            
            # 更新缓存
            self._session_data_cache = data.copy()
            
            if self.logger:
                self.logger.debug(f"[主动消息] 成功保存会话数据，共 {len(data)} 个会话喵。")
            return True
            
        except OSError as e:
            if self.logger:
                self.logger.error(f"[主动消息] 保存会话数据失败喵: {e}")
            return False
    
    async def get_session_data(self, session_id: str) -> Dict[str, Any]:
        """
        获取指定会话的数据
        
        Args:
            session_id: 会话ID
            
        Returns:
            会话数据字典，不存在时返回空字典
        """
        session_data = await self.load_session_data()
        return session_data.get(session_id, {})
    
    async def update_session_data(self, session_id: str, data: Dict[str, Any]) -> bool:
        """
        更新指定会话的数据
        
        Args:
            session_id: 会话ID
            data: 要更新的数据
            
        Returns:
            是否成功更新
        """
        session_data = await self.load_session_data()
        session_data[session_id] = data
        return await self.save_session_data(session_data)
    
    async def delete_session_data(self, session_id: str) -> bool:
        """
        删除指定会话的数据
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功删除
        """
        session_data = await self.load_session_data()
        if session_id in session_data:
            del session_data[session_id]
            if self.logger:
                self.logger.debug(f"[主动消息] 已删除会话数据喵: {session_id}")
            return await self.save_session_data(session_data)
        return True
    
    async def cleanup_invalid_session_data(self, session_data: Dict[str, Any]) -> int:
        """
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
            if session_id.startswith("private_message:") or session_id.startswith(
                "group_message:"
            ):
                invalid_sessions.append(session_id)
                cleaned_count += 1
        
        # 删除无效的会话数据
        for session_id in invalid_sessions:
            del session_data[session_id]
            if self.logger:
                self.logger.debug(f"[主动消息] 清理了无效的会话数据喵: {session_id}")
        
        return cleaned_count
    
    async def validate_session_data(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        验证会话数据的有效性
        
        Args:
            session_data: 会话数据字典
            
        Returns:
            验证结果字典，包含错误和警告信息
        """
        errors = []
        warnings = []
        
        for session_id, session_info in session_data.items():
            # 检查必需字段
            if "next_trigger_time" in session_info:
                next_trigger = session_info["next_trigger_time"]
                if not isinstance(next_trigger, (int, float)) or next_trigger <= 0:
                    warnings.append(f"会话 {session_id} 的 next_trigger_time 格式无效喵")
            
            if "unanswered_count" in session_info:
                unanswered_count = session_info["unanswered_count"]
                if not isinstance(unanswered_count, int) or unanswered_count < 0:
                    warnings.append(f"会话 {session_id} 的 unanswered_count 格式无效喵")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "total_sessions": len(session_data)
        }
    
    async def backup_session_data(self, backup_dir: Optional[Path] = None) -> bool:
        """
        备份会话数据
        
        Args:
            backup_dir: 备份目录，默认为数据目录下的backup子目录
            
        Returns:
            是否成功备份
        """
        try:
            if backup_dir is None:
                backup_dir = self.data_dir / "backups"
            
            await aio_os.makedirs(backup_dir, exist_ok=True)
            
            # 生成备份文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = backup_dir / f"session_data_backup_{timestamp}.json"
            
            # 加载当前数据
            session_data = await self.load_session_data()
            
            # 写入备份文件
            async with aiofiles.open(backup_file, "w", encoding="utf-8") as f:
                content = await asyncio.to_thread(
                    json.dumps, session_data, indent=4, ensure_ascii=False
                )
                await f.write(content)
            
            if self.logger:
                self.logger.debug(f"[主动消息] 会话数据已备份到: {backup_file} 喵")
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 备份会话数据失败喵: {e}")
            return False
    
    async def restore_session_data(self, backup_file: Path) -> bool:
        """
        从备份文件恢复会话数据
        
        Args:
            backup_file: 备份文件路径
            
        Returns:
            是否成功恢复
        """
        try:
            if not await aio_os.path.exists(backup_file):
                if self.logger:
                    self.logger.error(f"[主动消息] 备份文件不存在喵: {backup_file}")
                return False
            
            # 读取备份数据
            async with aiofiles.open(backup_file, encoding="utf-8") as f:
                content = await f.read()
                backup_data = await asyncio.to_thread(json.loads, content)
            
            # 验证备份数据
            validation_result = await self.validate_session_data(backup_data)
            if not validation_result["valid"]:
                if self.logger:
                    self.logger.error(f"[主动消息] 备份数据验证失败喵: {validation_result['errors']}")
                return False
            
            # 保存恢复的数据
            success = await self.save_session_data(backup_data)
            if success and self.logger:
                self.logger.debug(f"[主动消息] 成功从备份恢复会话数据喵: {backup_file}")
            
            return success
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 恢复会话数据失败喵: {e}")
            return False
    
    def get_session_stats(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        获取会话统计信息
        
        Args:
            session_data: 会话数据字典
            
        Returns:
            统计信息字典
        """
        total_sessions = len(session_data)
        private_sessions = 0
        group_sessions = 0
        sessions_with_tasks = 0
        total_unanswered = 0
        
        for session_id, session_info in session_data.items():
            if "FriendMessage" in session_id:
                private_sessions += 1
            elif "GroupMessage" in session_id:
                group_sessions += 1
            
            if session_info.get("next_trigger_time"):
                sessions_with_tasks += 1
            
            unanswered_count = session_info.get("unanswered_count", 0)
            total_unanswered += unanswered_count
        
        return {
            "total_sessions": total_sessions,
            "private_sessions": private_sessions,
            "group_sessions": group_sessions,
            "sessions_with_tasks": sessions_with_tasks,
            "total_unanswered": total_unanswered,
            "average_unanswered": total_unanswered / total_sessions if total_sessions > 0 else 0
        }
    
    async def clear_all_session_data(self) -> bool:
        """
        清空所有会话数据
        
        Returns:
            是否成功清空
        """
        try:
            # 先备份当前数据
            await self.backup_session_data()
            
            # 清空数据
            success = await self.save_session_data({})
            if success and self.logger:
                self.logger.debug("[主动消息] 所有会话数据已清空喵！")
            return success
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 清空会话数据失败喵: {e}")
            return False
    
    def invalidate_cache(self):
        """
        使缓存失效，下次加载时会重新从文件读取
        """
        self._session_data_cache = None
