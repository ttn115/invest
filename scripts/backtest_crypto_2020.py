"""
長期回測腳本 — 2020 至今 (BTC/USDT + ETH/USDT)

功能:
1. 分頁拉取 CCXT 歷史資料 (1000 bars/次，自動接續)
2. 拉取歷史 Fear & Greed Index 並合併進 OHLCV
3. 修改 Sentiment Strategy 使用歷史情緒資料
4. 輸出詳細回測報告
"""

import sys
import time
import datetime as dt

import pandas as pd
from loguru import logger

sys.stdout.reconfigure(encoding="utf-8")

# ===== 1. 分頁拉取加密幣歷史資料 =====

def fetch_all_ohlcv(
    exchange_id: str = "binance",
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    start_date: str = "2020-01-01",
    end_date: str = "2026-02-28",
    batch_size: int = 1000,
) -> pd.DataFrame:
    """
    分頁拉取所有歷史 K 線資料。

    CCXT 每次最多回傳 ~1000 根 K 線，
    此函式自動用 since 參數接續拉取直到 end_date。
    """
    import ccxt

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})

    since = int(dt.datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ts = int(dt.datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)

    all_data = []
    page = 0

    while since < end_ts:
        page += 1
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=batch_size)
            if not ohlcv:
                break

            all_data.extend(ohlcv)
            last_ts = ohlcv[-1][0]

            if last_ts <= since:
                break

            since = last_ts + 1  # 下一批從最後一根 K 線之後開始

            bars_so_far = len(all_data)
            last_date = dt.datetime.fromtimestamp(last_ts / 1000).strftime("%Y-%m-%d %H:%M")
            if page % 10 == 0:
                logger.info(f"  📥 Page {page}: {bars_so_far} bars fetched, last={last_date}")

            time.sleep(0.1)  # 避免 rate limit

        except Exception as e:
            logger.error(f"  ⚠️ Error at page {page}: {e}")
            time.sleep(2)

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

    # 過濾到 end_date
    df = df[df["timestamp"] <= pd.to_datetime(end_date)]
    df = df.drop_duplicates(subset=["timestamp"]).reset_index(drop=True)

    logger.info(f"✅ {symbol}: {len(df)} bars ({df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]})")
    return df


# ===== 2. 拉取歷史 Fear & Greed Index =====

def fetch_fear_greed_history(days: int = 2500) -> pd.DataFrame:
    """
    拉取 Alternative.me Fear & Greed Index 歷史資料。
    免費 API 提供約 2018 年至今的每日資料。
    """
    import requests

    logger.info("📡 拉取 Fear & Greed 歷史資料...")
    url = "https://api.alternative.me/fng/"
    resp = requests.get(url, params={"limit": days}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    records = []
    for entry in data.get("data", []):
        records.append({
            "date": dt.datetime.fromtimestamp(int(entry["timestamp"])).date(),
            "sentiment_value": float(entry["value"]),
            "sentiment_label": entry["value_classification"],
        })

    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    logger.info(f"✅ Fear & Greed: {len(df)} days ({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})")
    return df


# ===== 3. 合併情緒資料到 OHLCV =====

def merge_sentiment(ohlcv_df: pd.DataFrame, sentiment_df: pd.DataFrame) -> pd.DataFrame:
    """
    將每日情緒資料合併到小時級 OHLCV 資料。
    同一天的所有小時 K 線共用同一個情緒分數。
    """
    df = ohlcv_df.copy()
    df["date"] = df["timestamp"].dt.date
    df = df.merge(sentiment_df, on="date", how="left")

    # 填補缺失 (週末或 API 遺漏): 用前值填補
    df["sentiment_value"] = df["sentiment_value"].ffill()
    df["sentiment_label"] = df["sentiment_label"].ffill()

    filled = df["sentiment_value"].notna().sum()
    total = len(df)
    logger.info(f"📊 情緒資料覆蓋率: {filled}/{total} ({filled/total*100:.1f}%)")

    return df


# ===== 4. 自訂 Sentiment Strategy (讀 DataFrame 欄位) =====

from src.strategy.base import BaseStrategy, Signal, SignalType
from src.data.sentiment import SentimentData, SentimentLevel


class HistoricalSentimentStrategy(BaseStrategy):
    """
    讀取 DataFrame 中的 sentiment_value 欄位而非呼叫即時 API，
    適用於歷史回測。
    """

    def __init__(self, params=None):
        default_params = {
            "mode": "contrarian",
            "fear_buy_threshold": 25,
            "greed_sell_threshold": 75,
            "neutral_low": 40,
            "neutral_high": 60,
        }
        if params:
            default_params.update(params)
        super().__init__(name="Sentiment", params=default_params)

    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        current_price = float(df["close"].iloc[-1])

        # 從 DataFrame 讀取情緒值
        if "sentiment_value" not in df.columns or pd.isna(df["sentiment_value"].iloc[-1]):
            return Signal(SignalType.HOLD, 0.0, current_price, symbol, self.name, "No sentiment data")

        value = float(df["sentiment_value"].iloc[-1])
        mode = self.params.get("mode", "contrarian")
        fear_buy = self.params["fear_buy_threshold"]
        greed_sell = self.params["greed_sell_threshold"]
        neutral_low = self.params["neutral_low"]
        neutral_high = self.params["neutral_high"]

        level = SentimentData.classify(value)
        label = level.value

        if mode == "contrarian":
            if value <= fear_buy:
                strength = min(1.0, (fear_buy - value) / fear_buy + 0.5)
                return Signal(SignalType.BUY, strength, current_price, symbol, self.name,
                              f"Extreme Fear ({value:.0f}), contrarian BUY")
            elif value <= neutral_low:
                strength = 0.3 + (neutral_low - value) / (neutral_low - fear_buy) * 0.3
                return Signal(SignalType.BUY, strength, current_price, symbol, self.name,
                              f"Fear ({value:.0f}), mild BUY")
            elif value <= neutral_high:
                return Signal(SignalType.HOLD, 0.1, current_price, symbol, self.name,
                              f"Neutral ({value:.0f}), HOLD")
            elif value <= greed_sell:
                strength = 0.3 + (value - neutral_high) / (greed_sell - neutral_high) * 0.3
                return Signal(SignalType.SELL, strength, current_price, symbol, self.name,
                              f"Greed ({value:.0f}), mild SELL")
            else:
                strength = min(1.0, (value - greed_sell) / (100 - greed_sell) + 0.5)
                return Signal(SignalType.SELL, strength, current_price, symbol, self.name,
                              f"Extreme Greed ({value:.0f}), contrarian SELL")
        else:
            if value <= fear_buy:
                return Signal(SignalType.SELL, 0.7, current_price, symbol, self.name,
                              f"Extreme Fear ({value:.0f}), momentum SELL")
            elif value >= greed_sell:
                return Signal(SignalType.BUY, 0.7, current_price, symbol, self.name,
                              f"Extreme Greed ({value:.0f}), momentum BUY")
            else:
                return Signal(SignalType.HOLD, 0.1, current_price, symbol, self.name,
                              f"Neutral ({value:.0f}), HOLD")


# ===== 5. 主程式 =====

def main():
    from src.config.settings import Settings
    from src.data.indicators import IndicatorEngine
    from src.engine.backtester import Backtester
    from src.engine.decision import DecisionEngine
    from src.strategy.sma_crossover import SMACrossoverStrategy
    from src.strategy.rsi_strategy import RSIStrategy
    from src.strategy.macd_strategy import MACDStrategy
    from src.strategy.bollinger_strategy import BollingerStrategy

    settings = Settings()

    # 取得虛擬幣專屬策略設定
    strat_config = settings.get_market_strategies("crypto")
    de_config = settings.get_market_decision_engine("crypto")

    # 建立決策引擎 (使用歷史情緒策略)
    strategies = {}
    weights = {}

    if strat_config.sma_crossover.enabled:
        strategies["SMA_Crossover"] = SMACrossoverStrategy(strat_config.sma_crossover.params)
        weights["SMA_Crossover"] = strat_config.sma_crossover.weight

    if strat_config.rsi.enabled:
        strategies["RSI"] = RSIStrategy(strat_config.rsi.params)
        weights["RSI"] = strat_config.rsi.weight

    if strat_config.macd.enabled:
        strategies["MACD"] = MACDStrategy(strat_config.macd.params)
        weights["MACD"] = strat_config.macd.weight

    if strat_config.bollinger.enabled:
        strategies["Bollinger"] = BollingerStrategy(strat_config.bollinger.params)
        weights["Bollinger"] = strat_config.bollinger.weight

    if strat_config.sentiment.enabled:
        strategies["Sentiment"] = HistoricalSentimentStrategy(strat_config.sentiment.params)
        weights["Sentiment"] = strat_config.sentiment.weight

    decision_engine = DecisionEngine(
        strategies=strategies,
        weights=weights,
        voting_method=de_config.voting_method,
        min_agreement=de_config.min_agreement,
    )

    logger.info(f"🤖 策略: {list(strategies.keys())}")
    logger.info(f"⚖️ 權重: {weights}")

    indicator_engine = IndicatorEngine()

    # --- 拉取 Fear & Greed 歷史 ---
    sentiment_df = fetch_fear_greed_history(days=2500)

    # --- 回測每個幣種 ---
    symbols = ["BTC/USDT", "ETH/USDT"]
    start_date = "2020-01-01"
    end_date = "2026-02-28"

    for symbol in symbols:
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 回測: {symbol} ({start_date} ~ {end_date})")
        logger.info(f"{'='*60}")

        # 1. 拉取歷史 K 線
        ohlcv_df = fetch_all_ohlcv(
            symbol=symbol,
            timeframe="1h",
            start_date=start_date,
            end_date=end_date,
        )

        if ohlcv_df.empty:
            logger.error(f"No data for {symbol}")
            continue

        # 2. 合併情緒資料
        ohlcv_df = merge_sentiment(ohlcv_df, sentiment_df)

        # 3. 執行回測
        backtester = Backtester(
            initial_capital=settings.config.general.initial_capital,
            commission_pct=settings.config.backtest.commission_pct,
            slippage_pct=settings.config.backtest.slippage_pct,
        )

        result = backtester.run(ohlcv_df, decision_engine, symbol, indicator_engine)

        # 4. 輸出結果
        summary = result.summary()
        print(summary)

        # 5. 儲存到檔案
        safe_name = symbol.replace("/", "_")
        filename = f"crypto_{safe_name}_2020_backtest.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(summary)
        logger.info(f"📄 Report saved: {filename}")

    logger.info("\n🏁 All backtests complete!")


if __name__ == "__main__":
    main()
