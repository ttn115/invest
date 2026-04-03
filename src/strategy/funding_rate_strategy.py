"""
資金費率策略 (Funding Rate Strategy)

邏輯：
資金費率 > high_threshold (例如 0.015%) → 市場極度看多 → 產生 SELL 訊號 (反向)
資金費率 < low_threshold (例如 -0.015%) → 市場極度看空 → 產生 BUY 訊號 (反向)
中間區域則 HOLD。
"""

import pandas as pd
from loguru import logger
from typing import Optional

from src.strategy.base import BaseStrategy, Signal, SignalType

class FundingRateStrategy(BaseStrategy):
    """
    基於資金費率的反向策略
    
    參數:
        high_threshold: (預設 0.00015 = 0.015%)，高於此值 -> SELL
        low_threshold: (預設 -0.00015 = -0.015%)，低於此值 -> BUY
    """

    def __init__(
        self,
        name: str = "FundingRate",
        weight: float = 1.0,
        high_threshold: float = 0.0001,
        low_threshold: float = -0.0001,
        mode: str = "signal",  # 'signal' 或 'filter'
        filter_threshold: float = 0.0003,  # 濾網模式閾值 (0.03%)
    ):
        """
        Args:
            high_threshold: 大於此值視為極端樂觀 (產生賣出訊號或 Veto)
            low_threshold: 小於此值視為極端悲觀 (產生買入訊號)
            mode: 'signal' (一般評分策略) 或 'filter' (絕對濾網)
            filter_threshold: 濾網模式下，費率超過此閾值才否決 BUY
        """
        super().__init__(name=name)
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self.mode = mode
        self.filter_threshold = filter_threshold
        
    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        current_price = float(df["close"].iloc[-1]) if not df.empty else 0.0
        
        # 1. 確認 DataFrame 是否已有合併進來的 funding_rate 欄位
        if "funding_rate" not in df.columns or pd.isna(df["funding_rate"].iloc[-1]):
            return Signal(
                signal_type=SignalType.HOLD, 
                strength=0.0, 
                price=current_price, 
                symbol=symbol, 
                strategy_name=self.name, 
                reason="No funding rate data available"
            )
            
        current_rate = float(df["funding_rate"].iloc[-1])
        
        if self.mode == "filter":
            # 濾網模式：
            # 若資金費率 > filter_threshold (如 0.03%)，禁止買進 (Veto)
            # 否則允許買進 (Pass)
            if current_rate > self.filter_threshold:
                signal_type = SignalType.HOLD
                reason = f"Funding Rate {current_rate:.5f} > {self.filter_threshold:.5f} (Overheated, Veto BUY)"
            else:
                signal_type = SignalType.BUY
                reason = f"Funding Rate {current_rate:.5f} <= {self.filter_threshold:.5f} (Normal, Allow BUY)"
            strength = 1.0
        else:
            # 訊號模式 (原始邏輯)
            if current_rate > self.high_threshold:
                signal_type = SignalType.SELL
                strength = min(1.0, current_rate / (self.high_threshold * 2))
                reason = f"Extreme positive funding rate ({current_rate:.5f})"
            elif current_rate < self.low_threshold:
                signal_type = SignalType.BUY
                strength = min(1.0, abs(current_rate) / (abs(self.low_threshold) * 2))
                reason = f"Extreme negative funding rate ({current_rate:.5f})"
            else:
                signal_type = SignalType.HOLD
                strength = 0.0
                reason = f"Neutral funding rate ({current_rate:.5f})"
                
        return Signal(
            signal_type=signal_type,
            strength=strength,
            price=current_price,
            symbol=symbol,
            strategy_name=self.name,
            reason=reason
        )
