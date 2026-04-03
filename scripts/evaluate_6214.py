import sys
import os
import pandas as pd
from loguru import logger
import datetime as dt

if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config.settings import Settings
from src.data.collector import TwStockCollector
from src.data.indicators import IndicatorEngine
from src.main import build_decision_engine

def evaluate_stock(symbol="6214"):
    logger.info(f"🚀 開始評估台股標的: {symbol} (精誠)")
    
    settings = Settings()
    collector = TwStockCollector()
    ind_engine = IndicatorEngine()
    
    # 3. 建立決策引擎 (使用 tw_stock 設定)
    engine = build_decision_engine(settings, market_name="tw_stock")
    strat_config = settings.get_market_strategies("tw_stock")
    
    # 1. 抓取資料
    logger.info(f"正在抓取 {symbol}.TW 的歷史資料...")
    df = collector.fetch_ohlcv(symbol=symbol, timeframe="1d", limit=500)
    
    if df.empty:
        logger.error(f"無法取得 {symbol} 的資料，請檢查網路或代碼。")
        return
    
    # 2. 計算指標 (遵循 config.yaml 中的週期)
    logger.info("計算技術指標...")
    df = ind_engine.add_all(
        df,
        sma_periods=[strat_config.sma_crossover.params.get("fast_period", 5), 
                     strat_config.sma_crossover.params.get("slow_period", 15)],
        rsi_period=strat_config.rsi.params.get("period", 7),
        macd_params=(strat_config.macd.params.get("fast", 8), 
                     strat_config.macd.params.get("slow", 21), 
                     strat_config.macd.params.get("signal", 5)),
        bb_params=(strat_config.bollinger.params.get("period", 14), 
                   strat_config.bollinger.params.get("std_dev", 1.5))
    )
    
    # 4. 執行決策分析
    logger.info(f"執行決策分析...")
    decision = engine.make_decision(df, symbol)
    
    # 5. 輸出報告
    last_row = df.iloc[-1]
    
    # 安全獲取值的 Helper
    def get_val(key, fmt=".2f"):
        val = last_row.get(key)
        if val is None or pd.isna(val):
            return "N/A"
        if isinstance(val, (int, float)):
            return f"{val:{fmt}}"
        return str(val)

    print("\n" + "="*50)
    print(f"📊 台股評估報告: {symbol}.TW (精誠)")
    print(f"日期: {last_row.name.strftime('%Y-%m-%d')}")
    print(f"收盤價: {get_val('close')}")
    print("-" * 50)
    print(f"💡 最終建議: {decision.final_signal.value} (信心度: {decision.confidence:.2f})")
    print(f"📝 理由: {decision.reason}")
    print("-" * 50)
    print("📈 技術指標詳情:")
    
    rsi_p = strat_config.rsi.params.get("period", 7)
    sma_f = strat_config.sma_crossover.params.get("fast_period", 5)
    sma_s = strat_config.sma_crossover.params.get("slow_period", 15)
    
    print(f"  • RSI({rsi_p}): {get_val(f'RSI_{rsi_p}', '.1f')}")
    print(f"  • SMA({sma_f}/{sma_s}): {get_val(f'SMA_{sma_f}')} / {get_val(f'SMA_{sma_s}')}")
    
    if "BB_Upper" in last_row and not pd.isna(last_row["BB_Upper"]):
        bb_pos = (last_row['close'] - last_row['BB_Lower']) / (last_row['BB_Upper'] - last_row['BB_Lower'])
        print(f"  • Bollinger 位置: {bb_pos:.2%}")
    else:
        print("  • Bollinger 位置: N/A")
    
    print("="*50 + "\n")

if __name__ == "__main__":
    evaluate_stock("6214")
