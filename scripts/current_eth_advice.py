import sys
import datetime as dt
sys.path.insert(0, ".")

import pandas as pd
import ccxt
import requests
from loguru import logger
from src.config.settings import Settings
from src.data.indicators import IndicatorEngine
from src.main import build_decision_engine

# Stop log spamming
logger.remove()
logger.add(sys.stdout, level="WARNING")

def get_current_eth_signal():
    settings = Settings()
    engine = build_decision_engine(settings, market_name="crypto")
    
    exchange = ccxt.binance({"enableRateLimit": True})
    
    # K lines (1h)
    ohlcv = exchange.fetch_ohlcv("ETH/USDT", "1h", limit=150)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    
    # BTC Regume
    btc_ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1h", limit=150)
    btc_df = pd.DataFrame(btc_ohlcv, columns=["timestamp", "open", "high", "low", "btc_close", "volume"])
    btc_df["timestamp"] = pd.to_datetime(btc_df["timestamp"], unit="ms")
    
    # Merge BTC
    df = pd.merge(df, btc_df[["timestamp", "btc_close"]], on="timestamp", how="left")
    
    # Add indicators
    ind_engine = IndicatorEngine()
    df = ind_engine.add_all(df)
    
    # Get Funding rate
    fr = exchange.fetch_funding_rate("ETH/USDT:USDT")
    df["funding_rate"] = fr["fundingRate"]
    
    # Get Fear & Greed
    resp = requests.get("https://api.alternative.me/fng/")
    fg_val = float(resp.json()["data"][0]["value"])
    df["sentiment_value"] = fg_val
    
    # Get USDT/TWD approx price
    try:
        max_usd_resp = requests.get('https://tw.rter.info/capi.php')
        ex_rates = max_usd_resp.json()
        usdt_twd = ex_rates['USDTWD']['Exrate']
    except:
        usdt_twd = 32.5
        
    current_usd = df["close"].iloc[-1]
    current_twd = current_usd * usdt_twd
    
    print("=" * 50)
    print(f"📊 ETH 目前市場狀態")
    print(f"價格:    ~{current_usd:.2f} USDT (約 {current_twd:,.0f} TWD)")
    print(f"匯率:    1 USD ≈ {usdt_twd:.2f} TWD")
    print("-" * 50)
    print(f"指標狀態:")
    print(f"  BTC 最新價:  {df['btc_close'].iloc[-1]:.2f}")
    print(f"  BTC 50MA:    {df['btc_close'].rolling(50).mean().iloc[-1]:.2f}")
    print(f"  資金費率:    {df['funding_rate'].iloc[-1]:.5f} (過大代表散戶追多)")
    print(f"  市場恐慌指數: {df['sentiment_value'].iloc[-1]} (0=極度恐慌, 100=極度貪婪)")
    
    # Evaluate strategies
    decision = engine.make_decision(df, "ETH/USDT")
    
    print("-" * 50)
    print(f"🤖 機器人綜合決策訊號: {decision.final_signal.value}")
    print(f"決策強度 (Confidence): {decision.confidence:.2f}")
    if decision.reason:
         print(f"決策/濾網攔截紀錄: {decision.reason}")
    print("各策略投票詳情:")
    for name, sig in decision.strategy_signals.items():
         print(f"  - {name}: {sig.signal_type.value} ({sig.reason})")
    
    print("=" * 50)

if __name__ == "__main__":
    get_current_eth_signal()
