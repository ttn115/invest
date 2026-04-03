"""Test MACD weak signals (v0.6.3) - verifies 4 new histogram momentum signal types."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import numpy as np
from src.strategy.macd_strategy import MACDStrategy
from src.strategy.base import SignalType

def make_df(n=40, last_hist_prev=0.0, last_hist_now=0.0):
    """Create a minimal DataFrame with MACD columns set up."""
    close = [100 + np.sin(i/5) for i in range(n)]
    df = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="h"),
        "open": close,
        "high": [c + 1 for c in close],
        "low": [c - 1 for c in close],
        "close": close,
        "volume": [1000] * n,
    })
    fast_ema = df["close"].ewm(span=12, adjust=False).mean()
    slow_ema = df["close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = fast_ema - slow_ema
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]
    
    # Override last two bars to control histogram values
    df.loc[df.index[-2], "MACD_Hist"] = last_hist_prev
    df.loc[df.index[-1], "MACD_Hist"] = last_hist_now
    # Keep MACD/Signal equal (no crossover)
    df.loc[df.index[-2], "MACD"] = 0.0
    df.loc[df.index[-2], "MACD_Signal"] = 0.0
    df.loc[df.index[-1], "MACD"] = 0.0
    df.loc[df.index[-1], "MACD_Signal"] = 0.0
    return df

strategy = MACDStrategy()
passed = 0
failed = 0

def check(name, df, expected_type, expected_min_strength=0.0):
    global passed, failed
    sig = strategy.generate_signal(df, "TEST")
    ok = sig.signal_type == expected_type and sig.strength >= expected_min_strength
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}: got {sig.signal_type.value} (str={sig.strength:.2f}) | expected {expected_type.value} (str>={expected_min_strength})")
    if not ok:
        print(f"      reason: {sig.reason}")
        failed += 1
    else:
        passed += 1

print("=== MACD Weak Signal Tests ===\n")

check("Hist flip positive (-0.5 -> +0.3)", 
      make_df(last_hist_prev=-0.5, last_hist_now=0.3), 
      SignalType.BUY, 0.4)

check("Hist flip negative (+0.5 -> -0.3)", 
      make_df(last_hist_prev=0.5, last_hist_now=-0.3), 
      SignalType.SELL, 0.4)

check("Hist expanding positive (+0.1 -> +0.5)", 
      make_df(last_hist_prev=0.1, last_hist_now=0.5), 
      SignalType.BUY, 0.3)

check("Hist expanding negative (-0.1 -> -0.5)", 
      make_df(last_hist_prev=-0.1, last_hist_now=-0.5), 
      SignalType.SELL, 0.3)

check("Hist contracting (+0.5 -> +0.1) -> HOLD", 
      make_df(last_hist_prev=0.5, last_hist_now=0.1), 
      SignalType.HOLD)

check("Hist flat (0 -> 0) -> HOLD", 
      make_df(last_hist_prev=0.0, last_hist_now=0.0), 
      SignalType.HOLD)

print(f"\n=== Results: {passed} passed, {failed} failed ===")
if failed > 0:
    sys.exit(1)
print("All tests passed!")
