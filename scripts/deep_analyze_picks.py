import sys
import os
import pandas as pd
import ccxt
from loguru import logger

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.indicators import IndicatorEngine

def analyze_symbol(symbol):
    exchange = ccxt.binance()
    ind_engine = IndicatorEngine()
    
    print(f"\n{'='*50}")
    print(f"🔍 Deep Analysis: {symbol}")
    print(f"{'='*50}")
    
    timeframes = ["1h", "4h", "1d"]
    for tf in timeframes:
        ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=100)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = ind_engine.add_all(df)
        
        last_price = df["close"].iloc[-1]
        rsi = df["RSI_14"].iloc[-1]
        sma8 = df["close"].rolling(window=8).mean().iloc[-1]
        sma20 = df["close"].rolling(window=20).mean().iloc[-1]
        
        # Bollinger Bands for support
        bb_low = df["bollinger_low"].iloc[-1] if "bollinger_low" in df.columns else "N/A"
        
        print(f"\n[{tf} Timeframe]")
        print(f"  Current Price: {last_price}")
        print(f"  RSI: {rsi:.2f}")
        print(f"  SMA8 (Short-term Support): {sma8:.6f}")
        print(f"  SMA20 (Mid-term Support): {sma20:.6f}")
        print(f"  Bollinger Lower Band: {bb_low}")
        
    print(f"{'='*50}\n")

if __name__ == "__main__":
    analyze_symbol("TRX/USDT")
    analyze_symbol("DOT/USDT")
