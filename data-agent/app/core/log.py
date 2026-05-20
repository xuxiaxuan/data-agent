import sys
from pathlib import Path

from loguru import logger

from app.conf.app_config import app_config

# 配置日志格式
log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>request_id - {extra[request_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

logger.remove() # 移除默认配置
if app_config.logging.console.enable: # 如果为true则启用
    logger.add(sink=sys.stdout, level=app_config.logging.console.level, format=log_format)
if app_config.logging.file.enable: # 如果为true则启用
    path = Path(app_config.logging.file.path)
    path.mkdir(parents=True, exist_ok=True)
    logger.add(
        sink=path / "app.log",
        level=app_config.logging.file.level,
        format=log_format,
        rotation=app_config.logging.file.rotation,
        retention=app_config.logging.file.retention,
        encoding="utf-8"
    )
