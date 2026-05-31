"""
跨市場掃描 + 圓桌投資建議

執行台股、虛擬幣掃描，整合送入圓桌會議，產出投資建議報告。

使用方式：
    python run_all_markets.py
    python run_all_markets.py --capital 2000000
    python run_all_markets.py --tw-date 2026-05-06   # 指定台股日期
    python run_all_markets.py --no-tw                # 跳過台股（只跑虛擬幣）
    python run_all_markets.py --market-note "半導體大漲，AI族群爆量"

環境變數：
    ANTHROPIC_API_KEY  : Claude API 金鑰（必須）
    FINMIND_TOKEN      : FinMind Token（台股選填）
"""

import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from datetime import date
from pathlib import Path

from loguru import logger
from src.monitor.logger import setup_logger


def parse_args():
    p = argparse.ArgumentParser(description="跨市場掃描 + 圓桌投資建議")
    p.add_argument("--capital",     default=1_000_000, type=float, help="總資金（元）")
    p.add_argument("--tw-date",     default=None,                  help="台股掃描日期 YYYY-MM-DD")
    p.add_argument("--tw-top",      default=5,         type=int,   help="台股送圓桌的名額")
    p.add_argument("--crypto-top",  default=5,         type=int,   help="虛擬幣送圓桌的名額")
    p.add_argument("--us-top",      default=5,         type=int,   help="美股送圓桌的名額")
    p.add_argument("--no-tw",       action="store_true",           help="跳過台股掃描")
    p.add_argument("--no-crypto",   action="store_true",           help="跳過虛擬幣掃描")
    p.add_argument("--no-us",       action="store_true",           help="跳過美股掃描")
    p.add_argument("--no-track",        action="store_true",       help="不記錄圓桌推薦到 roundtable_history.csv")
    p.add_argument("--no-fundamentals", action="store_true",       help="不注入基本面（ROE/FCF/PEG）")
    p.add_argument("--market-note", default="",                    help="市場備注")
    return p.parse_args()


def main():
    args = parse_args()
    setup_logger("INFO", "data/logs")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("未設定 ANTHROPIC_API_KEY，無法執行圓桌評估")
        sys.exit(1)

    today = date.today().strftime("%Y-%m-%d")
    sep = "=" * 60

    logger.info(sep)
    logger.info("  🌏 跨市場掃描 + 圓桌會議")
    logger.info(sep)

    tw_candidates     = []
    crypto_candidates = []
    us_candidates     = []
    crypto_scanner    = None      # 保留參考，圓桌環境分析會用到其 exchange

    # ── Step 1: 台股盤後掃描 ─────────────────────────────────────
    if not args.no_tw:
        logger.info("\n[Step 1] 台股盤後掃描...")
        try:
            from src.scanner.post_market_scanner import run_post_market_scan
            tw_result = run_post_market_scan(
                date_str=args.tw_date,
                min_score=40,
                min_volume_ratio=1.5,
                inst_buy_only=False,
                save_csv=True,
                finmind_token=os.environ.get("FINMIND_TOKEN"),
            )
            tw_candidates = tw_result.top[: args.tw_top]
            logger.info(f"  台股候選：{len(tw_candidates)} 支送圓桌")
        except Exception as e:
            logger.error(f"  台股掃描失敗: {e}")
    else:
        logger.info("[Step 1] 台股掃描跳過")

    # ── Step 2: 虛擬幣掃描 ───────────────────────────────────────
    if not args.no_crypto:
        logger.info("\n[Step 2] 虛擬幣即時掃描...")
        try:
            from src.scanner.crypto_scanner import CryptoScanner
            crypto_scanner = CryptoScanner()
            crypto_result  = crypto_scanner.scan(min_score=25)  # noqa: F841 (scanner kept for ctx)
            crypto_candidates = crypto_result.top[: args.crypto_top]
            logger.info(f"  虛擬幣候選：{len(crypto_candidates)} 支送圓桌")
        except Exception as e:
            logger.error(f"  虛擬幣掃描失敗: {e}")
    else:
        logger.info("[Step 2] 虛擬幣掃描跳過")

    # ── Step 3: 美股掃描 ─────────────────────────────────────────
    if not args.no_us:
        logger.info("\n[Step 3] 美股掃描...")
        try:
            from src.scanner.us_scanner import USScanner
            us_scanner = USScanner()
            us_result  = us_scanner.scan(min_score=30)
            us_candidates = us_result.top[: args.us_top]
            logger.info(f"  美股候選：{len(us_candidates)} 支送圓桌")
        except Exception as e:
            logger.error(f"  美股掃描失敗: {e}")
    else:
        logger.info("[Step 3] 美股掃描跳過")

    total = len(tw_candidates) + len(crypto_candidates) + len(us_candidates)
    if total == 0:
        logger.error("三個市場均無候選標的，圓桌中止")
        sys.exit(1)

    logger.info(f"\n共 {total} 支候選送入圓桌：台股 {len(tw_candidates)} + "
                f"虛擬幣 {len(crypto_candidates)} + 美股 {len(us_candidates)}")

    # ── Step 3.5: 市場背景分析（供環境提示 + 圓桌推薦記錄）────────
    tw_ctx = crypto_ctx = None
    logger.info("\n[Step 3.5] 市場背景分析...")
    if tw_candidates or us_candidates:
        try:
            from src.analysis.tw_market_context import TwMarketContextAnalyzer
            tw_ctx = TwMarketContextAnalyzer().analyze()
            logger.info(f"  台股環境：{getattr(tw_ctx, 'taiex_phase', '?')}")
        except Exception as e:
            logger.warning(f"  台股環境分析失敗: {e}")
    if crypto_candidates and crypto_scanner is not None:
        try:
            from src.analysis.market_context import MarketContextAnalyzer
            crypto_ctx = MarketContextAnalyzer(crypto_scanner.exchange).analyze()
            logger.info(f"  虛擬幣環境：{getattr(crypto_ctx, 'phase', '?')}")
        except Exception as e:
            logger.warning(f"  虛擬幣環境分析失敗: {e}")

    # ── Step 4: 跨市場圓桌評估 ───────────────────────────────────
    logger.info("\n[Step 4] 圓桌會議進行中（請稍候）...")
    from src.advisor.multi_market_advisor import run_multi_market_roundtable

    report_md, advisor = run_multi_market_roundtable(
        tw_candidates=tw_candidates,
        crypto_candidates=crypto_candidates,
        us_candidates=us_candidates,
        api_key=api_key,
        total_capital=args.capital,
        market_note=args.market_note,
        save_path=f"data/reports/roundtable_all_{today}.md",
        tw_ctx=tw_ctx,
        crypto_ctx=crypto_ctx,
        enrich_fundamentals=not args.no_fundamentals,
        return_advisor=True,
    )

    # ── Step 4.5: 記錄圓桌推薦 + 回驗舊推薦（回饋迴圈）───────────
    if not args.no_track and advisor is not None and advisor.last_picks:
        logger.info("\n[Step 4.5] 記錄圓桌推薦 + 回驗舊推薦...")
        try:
            from src.advisor.roundtable_tracker import RoundtableTracker
            tracker = RoundtableTracker()

            # 進場價：從各市場候選組 {asset_id: price}
            price_map = {}
            for c in tw_candidates:
                price_map[c.stock_id] = c.close
            for c in us_candidates:
                price_map[c.ticker] = c.close
            for c in crypto_candidates:
                price_map[c.base] = c.price

            ctx_map = {"台股": tw_ctx, "美股": tw_ctx, "虛擬幣": crypto_ctx}
            tracker.record_picks(advisor.last_picks, ctx_map=ctx_map, price_map=price_map)
            tracker.verify_picks(days_after=3)
        except Exception as e:
            logger.warning(f"  圓桌推薦記錄/回驗失敗: {e}")

    # ── Step 5: 輸出 ─────────────────────────────────────────────
    logger.info("\n" + sep)
    logger.info("  ✅ 圓桌會議完成！")
    logger.info(f"  報告：data/reports/roundtable_all_{today}.md")
    logger.info(sep)
    print("\n" + report_md)


if __name__ == "__main__":
    main()
