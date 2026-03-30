import re
from pathlib import Path

import astrbot
from astrbot.api import logger

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


def get_plugin_root() -> Path:
    """获取插件根目录。"""
    return Path(__file__).resolve().parent.parent


def get_metadata_path() -> Path:
    """获取插件 metadata.yaml 路径。"""
    return get_plugin_root() / "metadata.yaml"


def get_plugin_version(default: str = "unknown", strip_v_prefix: bool = False) -> str:
    """通过读取插件根目录中的 metadata.yaml 获取插件版本号。"""
    try:
        metadata_path = get_metadata_path()
        if metadata_path.exists():
            with open(metadata_path, encoding="utf-8") as f:
                for line in f:
                    match = re.match(r"^\s*version:\s*([^#\n]+)", line)
                    if match:
                        version = match.group(1).strip().strip('"').strip("'")
                        if strip_v_prefix:
                            version = version.lstrip("vV")
                        return version or default
        else:
            logger.debug(f"[主动消息] metadata.yaml 未找到喵: {metadata_path}")
    except Exception as e:
        logger.error(f"[主动消息] 获取插件版本失败喵: {e}")

    return default


def get_astrbot_version(default: str = "unknown") -> str:
    """从 AstrBot 安装目录的 pyproject.toml 中读取 AstrBot 版本号。"""
    try:
        astrbot_path = Path(astrbot.__file__).resolve().parent.parent
        pyproject_path = astrbot_path / "pyproject.toml"

        if not pyproject_path.exists():
            logger.debug(
                f"[主动消息] 无法读取 AstrBot 版本喵，pyproject.toml 不存在: {pyproject_path}"
            )
            return default

        if tomllib is None:
            logger.warning(
                "[主动消息] 未找到 tomllib 或 tomli 模块，无法解析 AstrBot 版本喵。"
            )
            return default

        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        version = str(data.get("project", {}).get("version", default)).strip()
        return version or default
    except Exception as e:
        logger.debug(f"[主动消息] 获取 AstrBot 版本时出错喵: {e}")
        return default
