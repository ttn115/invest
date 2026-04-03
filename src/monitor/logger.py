"""
日誌系統 (Logger)

使用 loguru 提供結構化日誌輸出。
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logger(log_level: str = "INFO", log_dir: str | Path | None = None):
    """
    設定全域日誌

    Args:
        log_level: 日誌等級 (DEBUG, INFO, WARNING, ERROR)
        log_dir: 日誌檔案目錄
    """
    # 移除預設 handler
    logger.remove()

    # 控制台輸出 (帶顏色)
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # 檔案輸出
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        # 一般日誌
        logger.add(
            str(log_path / "trader_{time:YYYY-MM-DD}.log"),
            level=log_level,
            rotation="1 day",
            retention="30 days",
            compression="gz",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {module}:{function}:{line} | {message}",
        )

        # 交易日誌 (獨立記錄)
        logger.add(
            str(log_path / "trades_{time:YYYY-MM-DD}.log"),
            level="INFO",
            rotation="1 day",
            retention="90 days",
            filter=lambda record: "trade" in record["extra"].get("category", ""),
            format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
        )

        # 錯誤日誌
        logger.add(
            str(log_path / "errors_{time:YYYY-MM-DD}.log"),
            level="ERROR",
            rotation="1 day",
            retention="90 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}:{function}:{line} | {message}\n{exception}",
        )

    logger.info(f"Logger initialized: level={log_level}")


def get_trade_logger():
    """取得交易專用 logger"""
    return logger.bind(category="trade")
