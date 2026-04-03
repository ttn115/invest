"""
專屬 ETH 回測腳本 — 整合資金費率 (Funding Rate) 指標

包含：
1. ETH/USDT 53,977 小時線 OHLCV (2020 至今)
2. Alternative.me Fear & Greed Index
3. Binance 歷史 Funding Rate
所有資料合併後跑回測。
"""

import sys
import datetime as dt

import pandas as pd
from loguru import logger

sys.path.insert(0, ".")
from scripts.backtest_crypto_2020 import fetch_all_ohlcv, fetch_fear_greed_history, merge_sentiment
from src.data.funding_rate import FundingRateCollector

sys.stdout.reconfigure(encoding="utf-8")

def merge_funding_rate(ohlcv_df: pd.DataFrame, funding_df: pd.DataFrame) -> pd.DataFrame:
    """將 Funding Rate 合併到 OHLCV (向前填補)"""
    if funding_df.empty:
        ohlcv_df["funding_rate"] = 0.0
        return ohlcv_df
        
    df = ohlcv_df.copy()
    
    # 強制轉換時間戳格式為一致的 datetime64[ns]，避免 MergeError
    df["timestamp"] = pd.to_datetime(df["timestamp"]).astype('datetime64[ns]')
    funding_df["timestamp"] = pd.to_datetime(funding_df["timestamp"]).astype('datetime64[ns]')
    
    # 使用 merge_asof，依據 timestamp 向前尋找最近的 funding_rate
    # 因為 funding rate 通常是每 8 小時結算一次
    df = pd.merge_asof(
        df.sort_values("timestamp"),
        funding_df.sort_values("timestamp"),
        on="timestamp",
        direction="backward"
    )
    
    # 原本在 merge_asof 前沒有 funding rate 的會是 NaN，我們填 0 (或是 ffill)
    df["funding_rate"] = df["funding_rate"].fillna(0.0)
    
    filled = (df["funding_rate"] != 0).sum()
    logger.info(f"📊 Funding Rate 覆蓋率: {filled}/{len(df)} 根 K 線")
    
    return df

def merge_btc_regime(ohlcv_df: pd.DataFrame, start_date: str, end_date: str, timeframe: str) -> pd.DataFrame:
    """拉取 BTC/USDT 作為大盤濾網數據併入"""
    btc_df = fetch_all_ohlcv(symbol="BTC/USDT", timeframe=timeframe, start_date=start_date, end_date=end_date)
    if btc_df.empty:
        ohlcv_df["btc_close"] = float("nan")
        return ohlcv_df
        
    btc_sub = btc_df[["timestamp", "close"]].rename(columns={"close": "btc_close"})
    
    df = ohlcv_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).astype('datetime64[ns]')
    btc_sub["timestamp"] = pd.to_datetime(btc_sub["timestamp"]).astype('datetime64[ns]')
    
    df = pd.merge_asof(
        df.sort_values("timestamp"),
        btc_sub.sort_values("timestamp"),
        on="timestamp",
        direction="backward"
    )
    return df

def main():
    from src.config.settings import Settings
    from src.data.indicators import IndicatorEngine
    from src.engine.backtester import Backtester
    from src.engine.decision import DecisionEngine
    from src.strategy.sma_crossover import SMACrossoverStrategy
    from src.strategy.rsi_strategy import RSIStrategy
    from src.strategy.macd_strategy import MACDStrategy
    from src.strategy.bollinger_strategy import BollingerStrategy
    from src.strategy.funding_rate_strategy import FundingRateStrategy
    sys.path.insert(0, ".")
    from scripts.backtest_crypto_2020 import HistoricalSentimentStrategy

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

    filters = {}
    
    if strat_config.sentiment.enabled:
        strategies["Sentiment"] = HistoricalSentimentStrategy(strat_config.sentiment.params)
        weights["Sentiment"] = strat_config.sentiment.weight

    if getattr(strat_config, "funding_rate", None) and strat_config.funding_rate.enabled:
        strategy = FundingRateStrategy(strat_config.funding_rate.params)
        if strategy.mode == "filter":
            filters["FundingRate"] = strategy
        else:
            strategies["FundingRate"] = strategy
            weights["FundingRate"] = strat_config.funding_rate.weight

    if getattr(strat_config, "btc_regime", None) and strat_config.btc_regime.enabled:
        from src.strategy.regime_filter import RegimeFilterStrategy
        filters["BTCRegime"] = RegimeFilterStrategy(**strat_config.btc_regime.params)

    decision_engine = DecisionEngine(
        strategies=strategies,
        weights=weights,
        filters=filters,
        voting_method=de_config.voting_method,
        min_agreement=de_config.min_agreement,
        panic_buy_override=getattr(de_config, "panic_buy_override", None),
    )

    logger.info(f"🤖 策略 ({len(decision_engine.strategies)}): {list(decision_engine.strategies.keys())}")
    logger.info(f"⚖️ 權重: {decision_engine.weights}")

    indicator_engine = IndicatorEngine()

    start_date = "2024-11-01"
    end_date = "2026-02-28"
    symbol = "ETH/USDT"
    funding_symbol = "ETH/USDT:USDT"
    timeframe = "1d"

    logger.info(f"\n{'='*60}")
    logger.info(f"📊 {symbol} 深度回測 (含 Funding Rate, {timeframe})")
    logger.info(f"{'='*60}")

    # 1. 拉取 K 線
    logger.info(f"步驟 1/3: 取得 OHLCV ({timeframe})...")
    ohlcv_df = fetch_all_ohlcv(symbol=symbol, timeframe=timeframe, start_date=start_date, end_date=end_date)
    
    if ohlcv_df.empty:
        logger.error("No OHLCV data fetched!")
        return

    # 2. 拉取並合併情緒指標 (Fear & Greed)
    logger.info("步驟 2/3: 取得 Fear & Greed Index...")
    sentiment_df = fetch_fear_greed_history(days=2500)
    ohlcv_df = merge_sentiment(ohlcv_df, sentiment_df)

    # 3. 拉取並合併資金費率
    logger.info("步驟 3/4: 取得 Funding Rate...")
    fr_collector = FundingRateCollector()
    funding_df = fr_collector.get_historical(symbol=funding_symbol, start_date=start_date, end_date=end_date)
    ohlcv_df = merge_funding_rate(ohlcv_df, funding_df)

    # 4. 拉取大盤指標 (BTC) 用作 Regime Filter
    logger.info("步驟 4/4: 取得大盤資料 (BTC/USDT) ...")
    ohlcv_df = merge_btc_regime(ohlcv_df, start_date, end_date, timeframe=timeframe)

    # 5. 跑回測
    logger.info("🚀 開始回測... (這可能需要幾分鐘，為了加速已關閉詳細日誌)")
    backtester = Backtester(
        initial_capital=settings.config.general.initial_capital,
        commission_pct=settings.config.backtest.commission_pct,
        slippage_pct=settings.config.backtest.slippage_pct,
    )

    # 暫時關閉 INFO 日誌，避免 54000 根 K 線的打印拖慢速度
    logger.remove()
    logger.add(sys.stdout, level="WARNING")
    
    result = backtester.run(ohlcv_df, decision_engine, symbol, indicator_engine)

    # 恢復 INFO 級別
    logger.remove()
    logger.add(sys.stdout, level="INFO")
    
    # 5. 輸出報告
    summary = result.summary()
    print(summary)

    # 6. 存入檔案
    filename = "crypto_ETH_USDT_funding_rate_backtest.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(summary)
    logger.info(f"📄 Report saved: {filename}")


if __name__ == "__main__":
    main()
