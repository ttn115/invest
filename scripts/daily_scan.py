"""
每日三市場掃描 (Daily Market Scanner)

功能：
- 依序執行加密幣、美股、台股單次掃描
- 適合排程工具（Windows 工作排程器 / cron）在每天固定時間呼叫
- 每個市場各產生一次掃描報告並推送 Telegram

用法：
    python scripts/daily_scan.py                   # 掃描全部三個市場
    python scripts/daily_scan.py --market crypto   # 只掃加密幣
    python scripts/daily_scan.py --market us       # 只掃美股
    python scripts/daily_scan.py --market tw       # 只掃台股
"""

import sys
import os
import argparse
import subprocess
import datetime as dt
from loguru import logger

# Windows UTF-8 fix
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR  = os.path.join(PROJECT_ROOT, "scripts")
PYTHON       = sys.executable

# Setup logging
logger.remove()
logger.add(
    sys.stdout,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>"
)
logger.add(
    os.path.join(PROJECT_ROOT, "logs", "daily_scan.log"),
    level="INFO",
    rotation="7 days",
    retention="30 days",
    encoding="utf-8",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)


def run_scanner(name: str, script: str, extra_args: list[str] | None = None) -> bool:
    """執行單一掃描器腳本，回傳是否成功。"""
    script_path = os.path.join(SCRIPTS_DIR, script)
    cmd = [PYTHON, script_path] + (extra_args or [])

    logger.info(f"{'='*50}")
    logger.info(f"▶  開始掃描：{name}")
    logger.info(f"{'='*50}")

    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            timeout=300,          # 最多等 5 分鐘
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            logger.info(f"✅  {name} 掃描完成")
            return True
        else:
            logger.error(f"❌  {name} 掃描失敗 (exit={result.returncode})")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"⏰  {name} 超時（> 5 分鐘），已跳過")
        return False
    except Exception as e:
        logger.error(f"💥  {name} 執行錯誤：{e}")
        return False


MARKETS = {
    "crypto": ("加密幣 (Binance Top 20)", "top_20_scanner.py"),
    "us":     ("美股 (US Stocks)",        "us_stock_scanner.py"),
    "tw":     ("台股 (TW Stocks)",        "tw_stock_scanner.py"),
}


def main():
    parser = argparse.ArgumentParser(description="每日三市場掃描器")
    parser.add_argument(
        "--market",
        choices=["crypto", "us", "tw", "all"],
        default="all",
        help="指定掃描市場 (預設: all)"
    )
    args = parser.parse_args()

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"🚀  每日市場掃描啟動  [{now}]")

    targets = list(MARKETS.keys()) if args.market == "all" else [args.market]
    results = {}

    for market in targets:
        name, script = MARKETS[market]
        results[market] = run_scanner(name, script)

    # 總結
    logger.info(f"{'='*50}")
    logger.info("📊  掃描結果總覽")
    logger.info(f"{'='*50}")
    for market in targets:
        name, _ = MARKETS[market]
        status = "✅ 成功" if results[market] else "❌ 失敗"
        logger.info(f"  {status}  {name}")

    failed = [m for m, ok in results.items() if not ok]
    if failed:
        logger.warning(f"⚠️  以下市場掃描失敗：{', '.join(failed)}")
        sys.exit(1)
    else:
        logger.info("🎉  全部市場掃描完成！")
        sys.exit(0)


if __name__ == "__main__":
    main()
