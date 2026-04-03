"""
台股掃描器 (Taiwan Stock Scanner)

功能：
- 掃描 11 支台股標的，產生 BUY/SELL/HOLD 建議
- 每 30 分鐘掃描一次（09:00~13:30 台股交易時段）
- 整合台股市場背景（加權指數 + 三大法人 + 量能）
- 推送 Telegram 通知
- SOL 背景標籤化

用法：
    python scripts/tw_stock_scanner.py            # 單次掃描
    python scripts/tw_stock_scanner.py --loop      # 持續掃描 (30min)
"""

import sys
import os
import argparse
import time
import datetime as dt
import pandas as pd
from loguru import logger

# Windows UTF-8 stdout fix
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config.settings import Settings
from src.data.collector import TwStockCollector
from src.data.indicators import IndicatorEngine
from src.main import build_decision_engine
from src.monitor.notifier import TelegramNotifier
from src.monitor.signal_tracker import SignalTracker
from src.analysis.tw_market_context import TwMarketContextAnalyzer, TW_STOCK_NAMES
from src.analysis.contextual_optimizer import ContextualOptimizer

logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

# ── 台股觀測名單 ────────────────────────────────────────────
TW_WATCHLIST = [
    "2330",  # 台積電 (半導體)
    "2317",  # 鴻海 (電子代工)
    "2454",  # 聯發科 (IC設計)
    "2882",  # 國泰金 (金融)
    "2881",  # 富邦金 (金融)
    "2603",  # 長榮 (航運)
    "2002",  # 中鋼 (鋼鐵)
    "3711",  # 日月光 (封測)
    "2412",  # 中華電 (電信)
    "6214",  # 精誠 (資訊服務)
    "2308",  # 台達電 (電源)
]

SCAN_INTERVAL = 1800  # 30 分鐘


def is_tw_market_hours() -> bool:
    """檢查是否在台股交易時段 (09:00~13:30, 週一至週五)"""
    now = dt.datetime.now()
    if now.weekday() >= 5:  # 週六日
        return False
    market_open = now.replace(hour=9, minute=0, second=0)
    market_close = now.replace(hour=13, minute=30, second=0)
    return market_open <= now <= market_close


def generate_tw_report(report_df, tw_ctx=None, perf_text="", sol_text="") -> str:
    """生成台股 Telegram 報告"""
    lines = []

    # 區塊 1：台股市場背景
    if tw_ctx:
        lines.append(tw_ctx.telegram_block())

    # 區塊 2：信號警報
    buy_opps = report_df[report_df["Signal"] == "BUY"]
    sell_opps = report_df[report_df["Signal"] == "SELL"]

    if buy_opps.empty and sell_opps.empty:
        lines.append("✅ 台股暫無強烈訊號，建議觀望")

    if not buy_opps.empty:
        lines.append(f"🚀 *買入機會 ({len(buy_opps)} 支)*")
        for _, row in buy_opps.iterrows():
            rsi_val = float(row['RSI']) if row['RSI'] != 'N/A' else 50.0
            rsi_tag = " ⚠️超跌" if rsi_val < 30 else ""
            lines.append(
                f"  • *{row['Symbol']} {row['Name']}* ${row['Price']:.1f} | "
                f"信心:{row['Confidence']} RSI:{row['RSI']}{rsi_tag}"
            )

    if not sell_opps.empty:
        lines.append(f"🔻 *賣出警報 ({len(sell_opps)} 支)*")
        for _, row in sell_opps.iterrows():
            rsi_val = float(row['RSI']) if row['RSI'] != 'N/A' else 50.0
            rsi_tag = " ⚠️超買" if rsi_val > 70 else ""
            lines.append(
                f"  • *{row['Symbol']} {row['Name']}* ${row['Price']:.1f} | "
                f"信心:{row['Confidence']} RSI:{row['RSI']}{rsi_tag}"
            )

    # 區塊 3：全股一覽
    lines.append("")
    lines.append("📋 *台股觀測名單*")
    for _, row in report_df.iterrows():
        sig = row['Signal']
        sig_icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(sig, "⚪")
        lines.append(
            f"  {sig_icon} *{row['Symbol']} {row['Name']}* "
            f"${row['Price']:.1f} RSI:{row['RSI']} {sig}"
        )

    # 區塊 4：績效 + SOL
    if perf_text:
        lines.append("")
        lines.append(perf_text)
    if sol_text:
        lines.append("")
        lines.append(sol_text)

    # 區塊 5：名詞
    lines.append("")
    lines.append("📚 *名詞*")
    lines.append("• *三大法人*：外資+投信+自營商，法人偏多=利多")
    lines.append("• *量比*：今日量/20日均量，>1.3=量增")

    return "\n".join(lines)


def scan_tw_stocks():
    """執行一次台股掃描"""
    logger.info("🇹🇼 開始台股掃描...")

    settings = Settings()
    collector = TwStockCollector()
    ind_engine = IndicatorEngine()
    notifier = TelegramNotifier()
    tracker = SignalTracker()

    # 1. 台股市場背景分析
    logger.info("📊 分析台股市場背景 (加權指數 + 三大法人)...")
    tw_ctx_analyzer = TwMarketContextAnalyzer()
    tw_ctx = tw_ctx_analyzer.analyze()
    logger.info(f"📊 加權指數: {tw_ctx.taiex_close:,.0f} | Phase: {tw_ctx.taiex_phase} | 法人: {tw_ctx.institutional_sentiment}")

    # 2. 建立決策引擎 (使用 tw_stock 市場設定)
    try:
        engine = build_decision_engine(settings, market_name="tw_stock")
    except Exception as e:
        logger.warning(f"無法建立 tw_stock 引擎，使用 crypto 引擎: {e}")
        engine = build_decision_engine(settings, market_name="crypto")

    # 讀取策略參數
    try:
        strat_config = settings.get_market_strategies("tw_stock")
    except Exception:
        strat_config = settings.get_market_strategies("crypto")

    rsi_period = strat_config.rsi.params.get("period", 14)
    sma_fast = strat_config.sma_crossover.params.get("fast_period", 7)
    sma_slow = strat_config.sma_crossover.params.get("slow_period", 25)
    macd_fast = strat_config.macd.params.get("fast", 12)
    macd_slow = strat_config.macd.params.get("slow", 26)
    macd_signal = strat_config.macd.params.get("signal", 9)
    bb_period = strat_config.bollinger.params.get("period", 20)
    bb_std = strat_config.bollinger.params.get("std_dev", 2.0)
    rsi_col = f"RSI_{rsi_period}"

    # 3. 掃描每支股票
    results = []
    for symbol in TW_WATCHLIST:
        try:
            name = TW_STOCK_NAMES.get(symbol, symbol)
            logger.info(f"  處理 {symbol} {name}...")

            df = collector.fetch_ohlcv(symbol=symbol, timeframe="1d", limit=250)
            if df.empty:
                logger.warning(f"  ⚠️ {symbol} 無資料")
                continue

            # 加入 sentiment 和 dummy BTC 欄位 (讓 DecisionEngine 不報錯)
            df["sentiment_value"] = tw_ctx.taiex_rsi  # 用大盤 RSI 代替情緒
            df["btc_close"] = tw_ctx.taiex_close       # 用加權指數代替 BTC
            df["funding_rate"] = 0.0

            # 指標
            df = ind_engine.add_all(
                df,
                rsi_period=rsi_period,
                sma_periods=[sma_fast, sma_slow],
                macd_params=(macd_fast, macd_slow, macd_signal),
                bb_params=(bb_period, bb_std),
            )

            # 決策
            decision = engine.make_decision(df, symbol)

            last_price = float(df["close"].iloc[-1])
            rsi_val = f"{df[rsi_col].iloc[-1]:.1f}" if rsi_col in df.columns and not pd.isna(df[rsi_col].iloc[-1]) else "N/A"

            results.append({
                "Symbol": symbol,
                "Name": name,
                "Price": last_price,
                "Signal": decision.final_signal.value,
                "Confidence": f"{decision.confidence:.2f}",
                "RSI": rsi_val,
                "Reason": decision.reason,
            })

        except Exception as e:
            logger.error(f"  ❌ {symbol} 錯誤: {e}")

    if not results:
        logger.error("無法取得任何台股資料")
        return

    report_df = pd.DataFrame(results)

    # 排序：BUY > SELL > HOLD
    priority = {"BUY": 0, "SELL": 1, "HOLD": 2}
    report_df["priority"] = report_df["Signal"].apply(lambda x: priority.get(x, 2))
    report_df = report_df.sort_values("priority").drop(columns=["priority"])

    # 4. Console 報告
    header = f"\n{'='*60}\n🇹🇼 台股掃描報告 - {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}\n"
    ctx_text = tw_ctx.summary()
    table = report_df[["Symbol", "Name", "Price", "Signal", "Confidence", "RSI"]].to_string(index=False)
    full_report = header + "\n" + ctx_text + "\n\n" + "-"*60 + "\n" + table + "\n" + "="*60
    print(full_report)

    # 保存
    with open("scripts/last_tw_scan_report.txt", "w", encoding="utf-8") as f:
        f.write(full_report)

    # 5. Signal Tracking (用 tw_ctx 建立 mock MarketContext 物件)
    class TwContextBridge:
        """將 TwMarketContext 橋接為 SOL 相容格式"""
        def __init__(self, tw_ctx):
            self.phase = tw_ctx.taiex_phase
            self.season = tw_ctx.institutional_sentiment  # 法人情緒作為 season
            self.mtf_score = 1 if tw_ctx.taiex_trend == "UP" else (-1 if tw_ctx.taiex_trend == "DOWN" else 0)
            self.fg_3d_trend = tw_ctx.volume_status  # 量能作為 FG 趨勢替代
            self.dxy_trend = "UNKNOWN"

    bridge = TwContextBridge(tw_ctx)
    tracker.verify_past_signals(None)  # 台股無 exchange, skip verify
    fg_val = tw_ctx.taiex_rsi  # 用大盤 RSI 作為情緒值

    # 記錄信號
    actionable = report_df[report_df["Signal"].isin(["BUY", "SELL"])]
    for _, row in actionable.iterrows():
        tracker.record_signal(
            symbol=f"{row['Symbol']}.TW",
            signal=row["Signal"],
            price=row["Price"],
            price_twd=f"{row['Price']:.2f}",
            confidence=row["Confidence"],
            rsi=row["RSI"],
            sentiment=fg_val,
            market_ctx=bridge,
        )

    win_rate_text = tracker.get_summary_text()
    logger.info(win_rate_text)

    # 6. SOL
    sol_optimizer = ContextualOptimizer()
    sol_bias = sol_optimizer.analyze_and_update()
    sol_text = sol_optimizer.get_report_text()
    logger.info(sol_text)

    # 7. 完整報告 (CMD 顯示 + Telegram 推送)
    tw_msg = generate_tw_report(
        report_df, tw_ctx=tw_ctx,
        perf_text=win_rate_text,
        sol_text=sol_text,
    )

    # 在 CMD 顯示完整報告
    print("\n" + "=" * 60)
    print("📱 完整台股報告 (與 Telegram 相同內容)")
    print("=" * 60)
    print(tw_msg)
    print("=" * 60 + "\n")

    if notifier.enabled:
        notifier.send_message(tw_msg)
        logger.info("📱 台股報告已推送至 Telegram")

    return report_df


def main():
    parser = argparse.ArgumentParser(description="Taiwan Stock Scanner")
    parser.add_argument("--loop", action="store_true", help="Run in continuous loop (every 30min)")
    args = parser.parse_args()

    if args.loop:
        logger.info(f"🇹🇼 啟動台股持續掃描 (每 {SCAN_INTERVAL//60} 分鐘)")
        while True:
            try:
                now = dt.datetime.now()
                if is_tw_market_hours():
                    scan_tw_stocks()
                    logger.info(f"💤 等待 {SCAN_INTERVAL//60} 分鐘...")
                else:
                    # 非交易時段：每小時做一次盤後分析
                    if now.hour >= 14 and now.minute < 5:
                        logger.info("📊 盤後分析...")
                        scan_tw_stocks()
                    else:
                        next_open = now.replace(hour=9, minute=0, second=0)
                        if now.hour >= 14:
                            next_open += dt.timedelta(days=1)
                        logger.info(f"⏰ 非交易時段。下次開盤: {next_open.strftime('%Y-%m-%d %H:%M')}")

                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                logger.info("台股掃描已停止")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(60)
    else:
        scan_tw_stocks()


if __name__ == "__main__":
    main()
