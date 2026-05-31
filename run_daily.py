"""
每日盤後自動執行腳本

使用方式：
    python run_daily.py                          # 今日掃描（不含圓桌，無 API Key）
    python run_daily.py --roundtable             # 含圓桌評估（需設定 ANTHROPIC_API_KEY）
    python run_daily.py --date 2026-05-07        # 指定日期
    python run_daily.py --capital 2000000        # 指定總資金（元）

環境變數：
    ANTHROPIC_API_KEY   : Claude API 金鑰（圓桌評估需要）
    FINMIND_TOKEN       : FinMind API Token（提高請求上限）
"""

import argparse
import io
import os
import sys
from datetime import date

# 強制 stdout/stderr 使用 UTF-8（修正 Windows cp950 emoji 問題）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from loguru import logger

from src.monitor.logger import setup_logger
from src.report.investment_report import InvestmentReportGenerator, MarketSnapshot
from src.scanner.post_market_scanner import run_post_market_scan


def parse_args():
    parser = argparse.ArgumentParser(description="每日盤後投資掃描 + 圓桌報告")
    parser.add_argument("--date",       default=None,    help="掃描日期 YYYY-MM-DD")
    parser.add_argument("--capital",    default=1000000, type=float, help="總資金（元）")
    parser.add_argument("--min-score",  default=40,      type=int,   help="最低掃描評分")
    parser.add_argument("--vol-ratio",  default=2.0,     type=float, help="最低量比")
    parser.add_argument("--inst-only",  action="store_true",         help="只看法人買超")
    parser.add_argument("--roundtable", action="store_true",         help="啟用圓桌評估")
    parser.add_argument("--top-n",      default=10,      type=int,   help="圓桌最多評估幾支")
    parser.add_argument("--market-note", default="",                 help="市場備注（大盤情況）")
    return parser.parse_args()


def main():
    args = parse_args()
    setup_logger("INFO", "data/logs")

    scan_date = args.date or date.today().strftime("%Y-%m-%d")
    logger.info(f"{'='*60}")
    logger.info(f"  🚀 每日投資掃描啟動  {scan_date}")
    logger.info(f"{'='*60}")

    # ── Step 1: 盤後掃描 ─────────────────────────────────────────
    finmind_token = os.environ.get("FINMIND_TOKEN")
    scan_result = run_post_market_scan(
        date_str=scan_date,
        min_score=args.min_score,
        min_volume_ratio=args.vol_ratio,
        inst_buy_only=args.inst_only,
        save_csv=True,
        finmind_token=finmind_token,
    )

    if not scan_result.candidates:
        logger.warning("今日無候選股，報告生成跳過")
        sys.exit(0)

    # ── Step 2: 圓桌評估（可選）─────────────────────────────────
    roundtable_report = None
    if args.roundtable:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY 未設定，跳過圓桌評估")
        else:
            from src.advisor.roundtable_advisor import run_roundtable
            roundtable_report = run_roundtable(
                candidates=scan_result.candidates,
                api_key=api_key,
                market_context=args.market_note,
                top_n=args.top_n,
            )

    # ── Step 3: 生成完整報告 ─────────────────────────────────────
    market_snapshot = MarketSnapshot(
        date=scan_date,
        market_note=args.market_note,
    )

    generator = InvestmentReportGenerator(total_capital=args.capital)
    report = generator.generate(
        scan_result=scan_result,
        roundtable_report=roundtable_report,
        market_snapshot=market_snapshot,
    )

    # ── Step 4: 儲存 ─────────────────────────────────────────────
    paths = generator.save(report, output_dir="data/reports")

    logger.info(f"{'='*60}")
    logger.info(f"  ✅ 完成！報告已儲存")
    logger.info(f"     Markdown : {paths['md']}")
    logger.info(f"     CSV      : {paths['csv']}")
    logger.info(f"{'='*60}")

    # 印出摘要到 console
    print(report.markdown[:2000])   # 只印前 2000 字


if __name__ == "__main__":
    main()
