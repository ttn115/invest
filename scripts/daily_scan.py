"""
每日掃描流程 (Daily Scan Pipeline)

功能：
- 依序執行加密幣、美股、台股掃描，並推送 Telegram
- 執行台股「盤後全市場掃描」（新世代，含 focus list 與自動回退交易日）
- 執行「預測記錄 + 回驗 + 校準」迴圈，讓系統隨時間自我修正
- 適合排程工具（Windows 工作排程器 / cron）在每天固定時間呼叫

用法：
    python scripts/daily_scan.py                    # 執行預設全部步驟
    python scripts/daily_scan.py --step crypto      # 只跑加密幣
    python scripts/daily_scan.py --step predict     # 只跑預測迴圈
    python scripts/daily_scan.py --step tw,tw-post  # 跑多個步驟（逗號分隔）
    python scripts/daily_scan.py --list             # 列出所有可用步驟

    # 向下相容舊參數
    python scripts/daily_scan.py --market tw

步驟說明：
    crypto   加密幣掃描（Binance Top 20）＋ Telegram ＋ SOL 環境學習
    us       美股掃描 ＋ Telegram
    tw       台股觀察名單掃描 ＋ Telegram ＋ 航運深度分析（SCFI/BDI）
    tw-post  台股盤後全市場掃描（post_market_scanner：70 檔 focus list 繞過量比門檻、
             自動定位最近交易日、含已修正的外資籌碼欄位）
    predict  預測迴圈：對候選標的產生「N日方向+幅度+信心」預測並記錄，
             回驗到期的舊預測，累積足量後自動校準信心與模型權重
             → 這是系統「隨時間變準」的關鍵，缺少它前面的掃描只是每天重來一次

⚠️  注意：GitHub Actions 的排程（.github/workflows/monitor.yml）目前為停用狀態
    （cron 被註解掉），因此雲端不會自動執行；本腳本需由本機排程器呼叫。
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


def run_scanner(name: str, script: str, extra_args: list[str] | None = None,
                timeout: int = 300) -> bool:
    """
    執行單一步驟腳本，回傳是否成功。

    script 以「專案根目錄」為基準的相對路徑（例如 "scripts/top_20_scanner.py"
    或 "predict.py"），以便同時支援 scripts/ 內與專案根目錄的入口。
    """
    script_path = os.path.join(PROJECT_ROOT, script)
    cmd = [PYTHON, script_path] + (extra_args or [])

    logger.info(f"{'='*50}")
    logger.info(f"▶  開始執行：{name}")
    logger.info(f"{'='*50}")

    if not os.path.exists(script_path):
        logger.error(f"❌  找不到腳本：{script}")
        return False

    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            logger.info(f"✅  {name} 完成")
            return True
        else:
            logger.error(f"❌  {name} 失敗 (exit={result.returncode})")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"⏰  {name} 超時（> {timeout//60} 分鐘），已跳過")
        return False
    except Exception as e:
        logger.error(f"💥  {name} 執行錯誤：{e}")
        return False


# 步驟定義：key -> (顯示名稱, 相對專案根目錄的腳本路徑, 額外參數, 逾時秒數)
STEPS: dict[str, tuple[str, str, list[str], int]] = {
    "crypto":  ("加密幣 (Binance Top 20)",      "scripts/top_20_scanner.py",  [], 300),
    "us":      ("美股 (US Stocks)",             "scripts/us_stock_scanner.py", [], 300),
    "tw":      ("台股觀察名單 + 航運深度分析",     "scripts/tw_stock_scanner.py", [], 600),
    "tw-post": ("台股盤後全市場掃描（新世代）",    "run_daily.py",                [], 900),
    "predict": ("預測記錄 + 回驗 + 校準",         "predict.py",                  [], 900),
}

# 預設執行順序：先掃描產生候選，最後跑預測迴圈（需要掃描結果）
DEFAULT_STEPS = ["crypto", "us", "tw", "tw-post", "predict"]

# 舊 --market 參數對應（向下相容）
_MARKET_ALIAS = {"crypto": ["crypto"], "us": ["us"], "tw": ["tw"], "all": DEFAULT_STEPS}


def main():
    parser = argparse.ArgumentParser(
        description="每日掃描流程（掃描 + 預測迴圈）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--step",
        default=None,
        help="要執行的步驟，逗號分隔。可用：" + ", ".join(STEPS) + f"（預設：{','.join(DEFAULT_STEPS)}）",
    )
    parser.add_argument(
        "--market",
        choices=["crypto", "us", "tw", "all"],
        default=None,
        help="[舊參數，向下相容] 指定市場；建議改用 --step",
    )
    parser.add_argument("--list", action="store_true", help="列出所有可用步驟後結束")
    args = parser.parse_args()

    if args.list:
        print("\n可用步驟：")
        for k, (name, script, _, t) in STEPS.items():
            mark = "★" if k in DEFAULT_STEPS else " "
            print(f"  {mark} {k:<9} {name:<28} → {script}  (逾時 {t//60} 分)")
        print(f"\n★ = 預設執行（順序：{' → '.join(DEFAULT_STEPS)}）\n")
        return

    # 決定要跑的步驟
    if args.step:
        targets = [s.strip() for s in args.step.split(",") if s.strip()]
        unknown = [s for s in targets if s not in STEPS]
        if unknown:
            logger.error(f"未知步驟：{', '.join(unknown)}（可用：{', '.join(STEPS)}）")
            sys.exit(2)
    elif args.market:
        targets = _MARKET_ALIAS[args.market]
        logger.warning("--market 為舊參數，建議改用 --step")
    else:
        targets = DEFAULT_STEPS

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"🚀  每日掃描流程啟動  [{now}]")
    logger.info(f"    步驟：{' → '.join(targets)}")

    results = {}
    for step in targets:
        name, script, extra, timeout = STEPS[step]
        results[step] = run_scanner(name, script, extra, timeout)

    # 總結
    logger.info(f"{'='*50}")
    logger.info("📊  執行結果總覽")
    logger.info(f"{'='*50}")
    for step in targets:
        name = STEPS[step][0]
        status = "✅ 成功" if results[step] else "❌ 失敗"
        logger.info(f"  {status}  {step:<9} {name}")

    failed = [s for s, ok in results.items() if not ok]
    if failed:
        logger.warning(f"⚠️  以下步驟失敗：{', '.join(failed)}")
        sys.exit(1)
    else:
        logger.info("🎉  全部步驟完成！")
        sys.exit(0)


if __name__ == "__main__":
    main()
