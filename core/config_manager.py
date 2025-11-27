"""
配置管理模块 - 负责插件配置的验证和管理
基于原main.py中的配置验证逻辑重构
"""

from typing import Dict, Any, Optional


class ConfigManager:
    """
    配置管理器 - 负责插件配置的验证和管理
    
    主要职责：
    1. 配置验证
    2. 会话配置获取
    3. 配置参数检查
    4. 配置变更通知
    5. 配置备份和恢复
    """
    
    def __init__(self, config: Dict[str, Any], logger=None):
        """
        初始化配置管理器
        
        Args:
            config: 插件配置字典
            logger: 日志记录器
        """
        self.config = config
        self.logger = logger
        self._config_cache: Dict[str, Any] = {}
        self._validation_cache: Dict[str, Dict[str, Any]] = {}
    
    def validate_config(self) -> Dict[str, str]:
        """
        验证插件配置的完整性和有效性
        
        Returns:
            验证结果字典，包含警告和错误信息
        """
        warnings = []
        errors = []
        
        try:
            private_settings = self.config.get("private_settings", {})
            group_settings = self.config.get("group_settings", {})
            
            # 验证私聊配置
            if private_settings.get("enable", False):
                target_user_id = private_settings.get("target_user_id", "")
                if not target_user_id or not str(target_user_id).strip():
                    warnings.append("私聊主动消息已启用但未配置目标用户ID喵")
                    if self.logger:
                        self.logger.warning("[主动消息] 私聊主动消息已启用但未配置目标用户ID喵")
                
                schedule_settings = private_settings.get("schedule_settings", {})
                min_interval = schedule_settings.get("min_interval_minutes", 30)
                max_interval = schedule_settings.get("max_interval_minutes", 900)
                
                if min_interval > max_interval:
                    warnings.append("私聊配置中最小间隔大于最大间隔喵，将自动调整喵")
                    if self.logger:
                        self.logger.warning("[主动消息] 私聊配置中最小间隔大于最大间隔喵，将自动调整喵")
                
                # 验证自动触发设置
                auto_trigger_settings = private_settings.get("auto_trigger_settings", {})
                auto_trigger_minutes = auto_trigger_settings.get("auto_trigger_after_minutes", 5)
                if auto_trigger_minutes < 0:
                    errors.append("私聊自动触发时间不能为负数喵")
                    if self.logger:
                        self.logger.error("[主动消息] 私聊自动触发时间不能为负数喵")
            
            # 验证群聊配置
            if group_settings.get("enable", False):
                target_group_id = group_settings.get("target_group_id", "")
                if not target_group_id or not str(target_group_id).strip():
                    warnings.append("群聊主动消息已启用但未配置目标群聊ID喵")
                    if self.logger:
                        self.logger.warning("[主动消息] 群聊主动消息已启用但未配置目标群聊ID喵")
                
                # 验证沉默触发时间
                idle_minutes = group_settings.get("group_idle_trigger_minutes", 10)
                if idle_minutes < 0:
                    errors.append("群聊沉默触发时间不能为负数喵")
                    if self.logger:
                        self.logger.error("[主动消息] 群聊沉默触发时间不能为负数喵")
                
                # 验证自动触发设置
                auto_trigger_settings = group_settings.get("auto_trigger_settings", {})
                auto_trigger_minutes = auto_trigger_settings.get("auto_trigger_after_minutes", 5)
                if auto_trigger_minutes < 0:
                    errors.append("群聊自动触发时间不能为负数喵")
                    if self.logger:
                        self.logger.error("[主动消息] 群聊自动触发时间不能为负数喵")
            
            # 记录验证结果
            if self.logger:
                if errors:
                    self.logger.error(f"[主动消息] 配置验证发现 {len(errors)} 个错误喵")
                if warnings:
                    self.logger.warning(f"[主动消息] 配置验证发现 {len(warnings)} 个警告喵")
                if not errors and not warnings:
                    self.logger.info("[主动消息] 配置验证完成，未发现明显问题喵")
            
            return {
                "status": "error" if errors else "success",
                "warnings": warnings,
                "errors": errors
            }
            
        except Exception as e:
            error_msg = f"配置验证过程出错喵: {e}"
            errors.append(error_msg)
            if self.logger:
                self.logger.error(f"[主动消息] {error_msg}")
            
            return {
                "status": "error", 
                "warnings": warnings,
                "errors": errors
            }
    
    def get_session_config(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        根据统一消息来源(umo)获取对应的私聊或群聊配置块
        
        Args:
            session_id: 会话ID
            
        Returns:
            配置字典（如果找到且启用）或None（如果未找到或禁用）
        """
        # 检查缓存
        cache_key = f"session_config:{session_id}"
        if cache_key in self._config_cache:
            return self._config_cache[cache_key]
        
        result = None
        if "group" in session_id.lower():
            group_conf = self.config.get("group_settings", {})
            target_group_id = str(group_conf.get("target_group_id", "")).strip()
            # 确保 session_id 属于目标群聊
            if target_group_id and f":{target_group_id}" in session_id:
                result = group_conf
        else:
            private_conf = self.config.get("private_settings", {})
            target_user_id = str(private_conf.get("target_user_id", "")).strip()
            # 确保 session_id 属于目标私聊
            if target_user_id and f":{target_user_id}" in session_id:
                result = private_conf
        
        # 缓存结果
        self._config_cache[cache_key] = result
        
        if self.logger and result:
            self.logger.debug(f"[主动消息] 获取会话配置成功喵: {session_id}")
        
        return result
    
    def get_auto_trigger_settings(self, session_type: str) -> Dict[str, Any]:
        """
        获取自动触发设置
        
        Args:
            session_type: "private" 或 "group"
            
        Returns:
            自动触发设置字典
        """
        if session_type == "private":
            settings = self.config.get("private_settings", {})
        else:
            settings = self.config.get("group_settings", {})
        
        auto_trigger_settings = settings.get("auto_trigger_settings", {})
        
        if self.logger:
            self.logger.debug(f"[主动消息] 获取自动触发设置喵: {session_type} -> {auto_trigger_settings}")
        
        return auto_trigger_settings
    
    def get_schedule_settings(self, session_type: str) -> Dict[str, Any]:
        """
        获取调度设置
        
        Args:
            session_type: "private" 或 "group"
            
        Returns:
            调度设置字典
        """
        if session_type == "private":
            settings = self.config.get("private_settings", {})
        else:
            settings = self.config.get("group_settings", {})
        
        schedule_settings = settings.get("schedule_settings", {})
        
        if self.logger:
            self.logger.debug(f"[主动消息] 获取调度设置喵: {session_type} -> {schedule_settings}")
        
        return schedule_settings
    
    def is_session_enabled(self, session_type: str) -> bool:
        """
        检查指定类型的会话是否启用
        
        Args:
            session_type: "private" 或 "group"
            
        Returns:
            是否启用
        """
        if session_type == "private":
            settings = self.config.get("private_settings", {})
        else:
            settings = self.config.get("group_settings", {})
        
        enabled = settings.get("enable", False)
        
        if self.logger:
            self.logger.debug(f"[主动消息] 检查会话启用状态喵: {session_type} -> {enabled}")
        
        return enabled
    
    def get_tts_settings(self, session_type: str) -> Dict[str, Any]:
        """
        获取TTS设置
        
        Args:
            session_type: "private" 或 "group"
            
        Returns:
            TTS设置字典
        """
        if session_type == "private":
            settings = self.config.get("private_settings", {})
        else:
            settings = self.config.get("group_settings", {})
        
        tts_settings = settings.get("tts_settings", {})
        
        if self.logger:
            self.logger.debug(f"[主动消息] 获取TTS设置喵: {session_type} -> {tts_settings}")
        
        return tts_settings
    
    def get_proactive_prompt(self, session_type: str) -> str:
        """
        获取主动消息提示模板
        
        Args:
            session_type: "private" 或 "group"
            
        Returns:
            提示模板字符串
        """
        if session_type == "private":
            settings = self.config.get("private_settings", {})
        else:
            settings = self.config.get("group_settings", {})
        
        proactive_prompt = settings.get("proactive_prompt", "")
        
        if self.logger:
            self.logger.debug(f"[主动消息] 获取主动消息提示模板喵: {session_type} -> {proactive_prompt[:50]}...")
        
        return proactive_prompt
    
    def update_config(self, new_config: Dict[str, Any]) -> bool:
        """
        更新配置
        
        Args:
            new_config: 新配置字典
            
        Returns:
            是否成功更新
        """
        try:
            old_config = self.config.copy()
            self.config = new_config
            
            # 清空缓存
            self._config_cache.clear()
            self._validation_cache.clear()
            
            # 验证新配置
            validation_result = self.validate_config()
            
            if validation_result["status"] == "error":
                # 验证失败，回滚配置
                self.config = old_config
                if self.logger:
                    self.logger.error(f"[主动消息] 配置更新失败，回滚到旧配置喵: {validation_result['errors']}")
                return False
            
            if self.logger:
                self.logger.info("[主动消息] 配置更新成功喵")
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[主动消息] 配置更新过程出错喵: {e}")
            return False
    
    def get_config_summary(self) -> Dict[str, Any]:
        """
        获取配置摘要信息
        
        Returns:
            配置摘要字典
        """
        private_settings = self.config.get("private_settings", {})
        group_settings = self.config.get("group_settings", {})
        
        return {
            "private_enabled": private_settings.get("enable", False),
            "group_enabled": group_settings.get("enable", False),
            "private_has_target": bool(private_settings.get("target_user_id", "").strip()),
            "group_has_target": bool(group_settings.get("target_group_id", "").strip()),
            "private_auto_trigger": private_settings.get("auto_trigger_settings", {}).get("enable_auto_trigger", False),
            "group_auto_trigger": group_settings.get("auto_trigger_settings", {}).get("enable_auto_trigger", False),
            "total_config_keys": len(self.config)
        }
    
    def clear_cache(self):
        """
        清空配置缓存
        """
        self._config_cache.clear()
        self._validation_cache.clear()
        if self.logger:
            self.logger.debug("[主动消息] 配置缓存已清空喵")