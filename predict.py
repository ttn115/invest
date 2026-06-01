"""
預測 + 自我校準一鍵流程

流程：掃描台股/虛擬幣 → Predictor 產生「N日漲跌方向+幅度+信心」預測
      → 記錄到 prediction_history.csv → 回驗到期的舊預測（查線上 API 實際價）
      → 資料足夠則校準（信心校準 + 權重微調）→ 印預測清單 + 準確度報告

資料來源全為既有線上 API（TWSE / yfinance / ccxt），台股+虛擬幣不需 Claude API，
可每天自動執行累積資料。

使用方式：
    python predict.py
    python predict.py --no-crypto
    python predict.py --no-tw
    python predict.py --tw-top 15 --crypto-top 10
"""

import argparse
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import os
from loguru import logger
from src.monitor.logger import setup_logger


def parse_args():
    p = argparse.ArgumentParser(description="可驗證的漲跌預測 + 時間累積自我校準")
    p.add_argument("--tw-date",    default=None,            help="台股掃描日期 YYYY-MM-DD")
    p.add_argument("--tw-top",     default=15, type=int,    help="台股取前幾名做預測")
    p.add_argument("--crypto-top", default=10, type=int,    help="虛擬幣取前幾名做預測")
    p.add_argument("--no-tw",      action="store_true",     help="跳過台股")
    p.add_argument("--no-crypto",  action="store_true",     help="跳過虛擬幣")
    p.add_argument("--no-calibrate", action="store_true",   help="不執行校準（只記錄+回驗）")
    return p.parse_args()


def main():
    args = parse_args()
    setup_logger("INFO", "data/logs")
    sep = "=" * 60

    from src.prediction.predictor import Predictor
    from src.prediction.prediction_tracker import PredictionTracker

    predictor = Predictor()
    tracker   = PredictionTracker()
    forecasts = []
    ctx_map   = {}

    logger.info(sep)
    logger.info("  🔮 預測 + 自我校準流程")
    logger.info(sep)

    # ── 台股 ─────────────────────────────────────────────────────
    if not args.no_tw:
        logger.info("\n[1] 台股掃描 + 預測...")
        try:
            from src.scanner.post_market_scanner import PostMarketScanner
            from src.analysis.tw_market_context import TwMarketContextAnalyzer
            scanner = PostMarketScanner()
            result  = scanner.scan(target_date=args.tw_date, min_score=40,
                                   min_volume_ratio=1.5, inst_buy_only=False)
            try:
                ctx_map["tw_stock"] = TwMarketContextAnalyzer().analyze()
            except Exception as e:
                logger.warning(f"  台股環境分析失敗: {e}")
            for c in result.top[: args.tw_top]:
                forecasts.append(predictor.predict(c, "tw_stock", "TW_SCAN"))
            logger.info(f"  台股預測：{len([f for f in forecasts if f.market=='tw_stock'])} 筆")
        except Exception as e:
            logger.error(f"  台股預測失敗: {e}")

    # ── 虛擬幣 ───────────────────────────────────────────────────
    if not args.no_crypto:
        logger.info("\n[2] 虛擬幣掃描 + 預測...")
        try:
            from src.scanner.crypto_scanner import CryptoScanner
            from src.analysis.market_context import MarketContextAnalyzer
            cscanner = CryptoScanner()
            cresult  = cscanner.scan(min_score=25)
            try:
                ctx_map["crypto"] = MarketContextAnalyzer(cscanner.exchange).analyze()
            except Exception as e:
                logger.warning(f"  虛擬幣環境分析失敗: {e}")
            for c in cresult.top[: args.crypto_top]:
                forecasts.append(predictor.predict(c, "crypto", "CRYPTO_SCAN"))
            logger.info(f"  虛擬幣預測：{len([f for f in forecasts if f.market=='crypto'])} 筆")
        except Exception as e:
            logger.error(f"  虛擬幣預測失敗: {e}")

    # ── 記錄 + 回驗 ──────────────────────────────────────────────
    logger.info("\n[3] 記錄預測 + 回驗到期舊預測...")
    tracker.record_predictions(forecasts, ctx_map=ctx_map)
    tracker.verify_due()

    # ── 校準 ─────────────────────────────────────────────────────
    if not args.no_calibrate:
        logger.info("\n[4] 模型校準（信心校準 + 權重微調）...")
        from src.prediction.calibrator import Calibrator
        summary = Calibrator().calibrate()
        if summary.get("status") == "insufficient":
            logger.info(f"  已驗證 {summary['verified']} 筆，未達門檻，暫不校準")

    # ── 輸出 ─────────────────────────────────────────────────────
    print("\n" + sep)
    print("  🔮 今日預測清單")
    print(sep)
    if not forecasts:
        print("  （無候選標的）")
    else:
        for fc in sorted(forecasts, key=lambda f: -f.cal_confidence):
            print(fc.to_line())
    print(sep)


if __name__ == "__main__":
    main()
