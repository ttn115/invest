import sys, os
sys.path.insert(0, os.path.abspath('.'))
from loguru import logger
logger.remove()

from src.config.settings import Settings
from src.data.collector import TwStockCollector
from src.data.indicators import IndicatorEngine
from src.main import build_decision_engine
import pandas as pd

settings = Settings()
collector = TwStockCollector()
ind_engine = IndicatorEngine()
engine = build_decision_engine(settings, market_name="tw_stock")
strat_config = settings.get_market_strategies("tw_stock")

df = collector.fetch_ohlcv(symbol="6214", timeframe="1d", limit=100)
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

decision = engine.make_decision(df, "6214")
last_row = df.iloc[-1]

with open('scripts/eval_output_clean.txt', 'w', encoding='utf-8') as f:
    f.write(f"Symbol: 6214\n")
    f.write(f"Date: {last_row.name}\n")
    f.write(f"Close: {last_row['close']}\n")
    f.write(f"Final Signal: {decision.final_signal.value}\n")
    f.write(f"Confidence: {decision.confidence:.2f}\n")
    f.write(f"Reason: {decision.reason}\n")
    
    rsi_p = strat_config.rsi.params.get("period", 7)
    f.write(f"RSI({rsi_p}): {last_row.get(f'RSI_{rsi_p}')}\n")
    
    sma_f = strat_config.sma_crossover.params.get("fast_period", 5)
    sma_s = strat_config.sma_crossover.params.get("slow_period", 15)
    f.write(f"SMA({sma_f}/{sma_s}): {last_row.get(f'SMA_{sma_f}')} / {last_row.get(f'SMA_{sma_s}')}\n")
    
    if "BB_Upper" in last_row and not pd.isna(last_row["BB_Upper"]):
        bb_pos = (last_row['close'] - last_row['BB_Lower']) / (last_row['BB_Upper'] - last_row['BB_Lower'])
        f.write(f"Bollinger Position: {bb_pos:.2%}\n")
