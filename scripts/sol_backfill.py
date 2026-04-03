"""
SOL 歷史回填腳本 (Historical Backfill for SOL Learning)

使用過去 6 個月的 BTC 1h 數據，模擬信號並計算實際 1h PnL，
為 SOL 系統提供大量帶有市場背景標籤的學習資料。

用法：
    python scripts/sol_backfill.py
"""

import sys
import os
import csv
import datetime as dt
import numpy as np
import pandas as pd
import ccxt
import requests
from loguru import logger

# Windows UTF-8 stdout fix
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.monitor.signal_tracker import HEADERS
from src.analysis.contextual_optimizer import ContextualOptimizer

logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

# ── 設定 ────────────────────────────────────────────────────
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
           "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT"]
TIMEFRAME = "1h"
LOOKBACK_DAYS = 180   # 6 個月
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "signal_history_backfill.csv")
RSI_PERIOD = 14
SMA_FAST = 7
SMA_SLOW = 25
OVERBOUGHT = 70
OVERSOLD = 30
MIN_AGREEMENT = 0.55  # 同 config.yaml


def calc_rsi(series, period=14):
    """計算 RSI"""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_trend(closes_5):
    """根據最近 5 根 K 線判斷趨勢方向"""
    if len(closes_5) < 5:
        return "NEUTRAL"
    slope = float(np.polyfit(range(len(closes_5)), closes_5, 1)[0])
    if slope > 0 and closes_5[-1] > closes_5[-2]:
        return "UP"
    elif slope < 0 and closes_5[-1] < closes_5[-2]:
        return "DOWN"
    return "NEUTRAL"


def determine_phase(above_sma50, above_sma200, rsi):
    """判斷市場階段"""
    if above_sma50 and above_sma200:
        return "DISTRIBUTION" if rsi > 70 else "BULL_RUN"
    elif above_sma50 and not above_sma200:
        return "RECOVERY"
    elif not above_sma50 and not above_sma200:
        return "RECOVERY" if rsi < 35 else "BEAR"
    return "BEAR"


def determine_mtf_direction(rsi, trend):
    """單一時框的方向判斷"""
    if trend == "UP" and rsi > 45:
        return "BUY"
    elif trend == "DOWN" and rsi < 55:
        return "SELL"
    return "HOLD"


def generate_signal(rsi, sma_fast_val, sma_slow_val, trend):
    """
    簡化信號生成：模擬 DecisionEngine 的多策略投票邏輯。
    回傳 (signal, confidence)
    """
    votes_buy = 0
    votes_sell = 0
    total_strats = 4  # RSI, SMA, trend, momentum

    # RSI
    if rsi < OVERSOLD:
        votes_buy += 1
    elif rsi > OVERBOUGHT:
        votes_sell += 1

    # SMA Crossover
    if sma_fast_val > sma_slow_val:
        votes_buy += 1
    elif sma_fast_val < sma_slow_val:
        votes_sell += 1

    # Trend
    if trend == "UP":
        votes_buy += 1
    elif trend == "DOWN":
        votes_sell += 1

    # Momentum (RSI direction)
    if 40 < rsi < 60:
        pass  # neutral
    elif rsi > 50:
        votes_buy += 0.5
    else:
        votes_sell += 0.5

    buy_ratio = votes_buy / total_strats
    sell_ratio = votes_sell / total_strats

    if buy_ratio >= MIN_AGREEMENT:
        return "BUY", round(buy_ratio, 2)
    elif sell_ratio >= MIN_AGREEMENT:
        return "SELL", round(sell_ratio, 2)
    return "HOLD", round(max(buy_ratio, sell_ratio), 2)


def fetch_fng_history():
    """取得過去 180 天的 Fear & Greed 歷史"""
    try:
        resp = requests.get(f"https://api.alternative.me/fng/?limit={LOOKBACK_DAYS + 10}", timeout=15)
        data = resp.json()["data"]
        # data 是由新到舊排列
        fng_map = {}
        for d in data:
            ts = dt.datetime.fromtimestamp(int(d["timestamp"]))
            date_key = ts.strftime("%Y-%m-%d")
            fng_map[date_key] = float(d["value"])
        logger.info(f"📊 Loaded {len(fng_map)} days of FNG history")
        return fng_map
    except Exception as e:
        logger.error(f"FNG fetch error: {e}")
        return {}


def determine_fg_trend(fng_map, date_str):
    """計算某日的 FNG 3 日趨勢"""
    try:
        date = dt.datetime.strptime(date_str, "%Y-%m-%d")
        vals = []
        for i in range(3):
            d = (date - dt.timedelta(days=i)).strftime("%Y-%m-%d")
            if d in fng_map:
                vals.append(fng_map[d])
        if len(vals) >= 2:
            delta = vals[0] - vals[-1]
            if delta > 3:
                return "RISING"
            elif delta < -3:
                return "FALLING"
        return "FLAT"
    except Exception:
        return "UNKNOWN"


def backfill():
    """主回填邏輯"""
    logger.info(f"🚀 Starting SOL backfill: {LOOKBACK_DAYS} days × {len(SYMBOLS)} symbols")
    exchange = ccxt.binance({"enableRateLimit": True})

    # 1. 取得 FNG 歷史
    fng_map = fetch_fng_history()

    # 2. 取得 BTC 1d 數據用於 market phase 判斷
    logger.info("📥 Fetching BTC 1d data for market phase analysis...")
    btc_1d = exchange.fetch_ohlcv("BTC/USDT", "1d", limit=LOOKBACK_DAYS + 220)
    btc_1d_df = pd.DataFrame(btc_1d, columns=["ts", "open", "high", "low", "close", "volume"])
    btc_1d_df["date"] = pd.to_datetime(btc_1d_df["ts"], unit="ms")
    btc_1d_df["rsi"] = calc_rsi(btc_1d_df["close"], RSI_PERIOD)
    btc_1d_df["sma50"] = btc_1d_df["close"].rolling(50).mean()
    btc_1d_df["sma200"] = btc_1d_df["close"].rolling(200).mean()

    # 3. 逐幣種逐小時回填
    all_signals = []
    for sym_idx, symbol in enumerate(SYMBOLS):
        logger.info(f"📥 [{sym_idx+1}/{len(SYMBOLS)}] Fetching {symbol} {TIMEFRAME} data...")
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=LOOKBACK_DAYS * 24)
        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            continue

        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        df["rsi"] = calc_rsi(df["close"], RSI_PERIOD)
        df["sma_fast"] = df["close"].rolling(SMA_FAST).mean()
        df["sma_slow"] = df["close"].rolling(SMA_SLOW).mean()

        sym_signals = 0
        # 從第 200 根開始（確保 SMA200 有值），每 4 小時採樣一次（避免過度擬合）
        sample_indices = range(max(200, SMA_SLOW + 10), len(df) - 1, 4)

        for i in sample_indices:
            row = df.iloc[i]
            next_row = df.iloc[i + 1]  # 1h 後的價格

            rsi = float(row["rsi"]) if not pd.isna(row["rsi"]) else 50.0
            sma_f = float(row["sma_fast"]) if not pd.isna(row["sma_fast"]) else 0
            sma_s = float(row["sma_slow"]) if not pd.isna(row["sma_slow"]) else 0

            if sma_f == 0 or sma_s == 0:
                continue

            # 趨勢
            closes_5 = df["close"].iloc[max(0, i-4):i+1].values
            trend = calc_trend(closes_5)

            # 生成信號
            signal, confidence = generate_signal(rsi, sma_f, sma_s, trend)
            if signal == "HOLD":
                continue  # 只記錄 BUY/SELL

            # 計算實際 1h PnL
            price_at = float(row["close"])
            price_1h = float(next_row["close"])
            pnl_1h = (price_1h - price_at) / price_at
            if signal == "SELL":
                pnl_1h = -pnl_1h

            # 市場階段 (用 BTC 1d)
            sig_date = row["date"].strftime("%Y-%m-%d")
            btc_day = btc_1d_df[btc_1d_df["date"].dt.strftime("%Y-%m-%d") == sig_date]
            if btc_day.empty:
                # 用最近的
                btc_day = btc_1d_df[btc_1d_df["date"] <= row["date"]].iloc[-1:]

            if not btc_day.empty:
                bd = btc_day.iloc[-1]
                above50 = bool(bd["close"] > bd["sma50"]) if not pd.isna(bd["sma50"]) else False
                above200 = bool(bd["close"] > bd["sma200"]) if not pd.isna(bd["sma200"]) else False
                btc_rsi = float(bd["rsi"]) if not pd.isna(bd["rsi"]) else 50.0
                phase = determine_phase(above50, above200, btc_rsi)
            else:
                phase = "UNKNOWN"

            # BTC 7d 表現 vs ALT (簡化：用自身幣種代替)
            if i >= 7 * 24:
                btc_7d_ago = float(df["close"].iloc[i - 7*24]) if symbol == "BTC/USDT" else 0
                self_7d_ago = float(df["close"].iloc[i - 7*24])
                if self_7d_ago > 0:
                    self_7d_pct = (price_at / self_7d_ago - 1) * 100
                else:
                    self_7d_pct = 0
                # 簡化 season 判斷
                season = "BTC_SEASON" if symbol == "BTC/USDT" else ("ALT_SEASON" if self_7d_pct > 5 else "MIXED")
            else:
                season = "UNKNOWN"

            # MTF score (簡化：用 1h RSI + trend)
            mtf_dir = determine_mtf_direction(rsi, trend)
            mtf_score = 1 if mtf_dir == "BUY" else (-1 if mtf_dir == "SELL" else 0)

            # FNG 趨勢
            fg_val = fng_map.get(sig_date, 50.0)
            fg_trend = determine_fg_trend(fng_map, sig_date)

            # DXY 趨勢 (無歷史數據，用 UNKNOWN)
            dxy_trend = "UNKNOWN"

            timestamp = row["date"].strftime("%Y-%m-%d %H:%M:%S")

            signal_row = {
                "timestamp": timestamp,
                "symbol": symbol,
                "signal": signal,
                "price_at_signal": f"{price_at:.6f}",
                "price_twd": f"{price_at * 31.4:.2f}",
                "confidence": f"{confidence:.2f}",
                "rsi": f"{rsi:.1f}",
                "sentiment": f"{fg_val:.1f}",
                "ctx_phase": phase,
                "ctx_season": season,
                "ctx_mtf_score": str(mtf_score),
                "ctx_fg_trend": fg_trend,
                "ctx_dxy_trend": dxy_trend,
                "price_after_1h": f"{price_1h:.6f}",
                "price_after_4h": "",
                "price_after_24h": "",
                "pnl_1h_pct": f"{pnl_1h:.4f}",
                "pnl_4h_pct": "",
                "pnl_24h_pct": "",
                "verified": "true",
            }
            all_signals.append(signal_row)
            sym_signals += 1

        logger.info(f"  ✅ {symbol}: {sym_signals} signals generated")

    # 4. 寫入 CSV
    logger.info(f"\n📝 Writing {len(all_signals)} backfill signals to CSV...")
    output_path = os.path.abspath(OUTPUT_FILE)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(all_signals)

    logger.info(f"✅ Backfill complete: {len(all_signals)} signals saved to {output_path}")

    # 5. 立即執行 SOL 分析
    logger.info("\n🧠 Running SOL analysis on backfill data...")
    optimizer = ContextualOptimizer(history_file=output_path)
    bias = optimizer.analyze_and_update()
    report = optimizer.get_report_text()
    logger.info(report)

    # 6. 統計摘要
    wins = sum(1 for s in all_signals if float(s["pnl_1h_pct"]) > 0)
    losses = len(all_signals) - wins
    total_pnl = sum(float(s["pnl_1h_pct"]) for s in all_signals) * 100
    avg_pnl = total_pnl / len(all_signals) if all_signals else 0

    logger.info(f"\n📊 回填統計摘要:")
    logger.info(f"   信號總數: {len(all_signals)}")
    logger.info(f"   勝率: {wins}/{len(all_signals)} = {wins/len(all_signals)*100:.1f}%")
    logger.info(f"   累計報酬: {total_pnl:+.2f}%")
    logger.info(f"   平均每筆: {avg_pnl:+.4f}%")

    # 環境分類統計
    from collections import Counter
    phase_counts = Counter(s["ctx_phase"] for s in all_signals)
    logger.info(f"\n📊 環境分佈:")
    for phase, count in phase_counts.most_common():
        phase_sigs = [s for s in all_signals if s["ctx_phase"] == phase]
        phase_wr = sum(1 for s in phase_sigs if float(s["pnl_1h_pct"]) > 0) / len(phase_sigs) * 100
        phase_pnl = sum(float(s["pnl_1h_pct"]) for s in phase_sigs) * 100 / len(phase_sigs)
        logger.info(f"   {phase}: {count} signals, WR={phase_wr:.1f}%, avg={phase_pnl:+.3f}%")

    # 7. 合併到主 signal_history.csv
    main_file = os.path.join(os.path.dirname(__file__), "..", "data", "signal_history.csv")
    if os.path.exists(main_file):
        logger.info(f"\n🔄 Merging backfill into main signal_history.csv...")
        existing = []
        with open(main_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing = list(reader)

        # 合併：backfill 先，然後現有（按時間排序）
        merged = all_signals + existing
        merged.sort(key=lambda x: x.get("timestamp", ""))

        with open(main_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(merged)
        logger.info(f"✅ Merged: {len(merged)} total signals ({len(all_signals)} backfill + {len(existing)} existing)")

    # 8. 用合併後的數據重新跑 SOL
    logger.info("\n🧠 Running final SOL analysis on merged data...")
    final_optimizer = ContextualOptimizer()
    final_bias = final_optimizer.analyze_and_update()
    final_report = final_optimizer.get_report_text()
    logger.info(final_report)

    logger.info("\n🎯 SOL 現在已經從過去 6 個月的數據學會了環境模式！")


if __name__ == "__main__":
    backfill()
