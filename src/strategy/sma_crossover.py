"""
SMA 交叉策略 (SMA Crossover Strategy)

當短期均線上穿長期均線時買入，下穿時賣出。
經典的趨勢追蹤策略，適合趨勢明確的市場。
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from .base import BaseStrategy, Signal, SignalType


class SMACrossoverStrategy(BaseStrategy):
    """
    SMA 雙均線交叉策略

    Params:
        fast_period (int): 快線週期，預設 10
        slow_period (int): 慢線週期，預設 30
    """

    def __init__(self, params: dict | None = None):
        default_params = {"fast_period": 10, "slow_period": 30}
        if params:
            default_params.update(params)
        super().__init__(name="SMA_Crossover", params=default_params)

    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        """產生 SMA 交叉信號"""
        fast = self.params["fast_period"]
        slow = self.params["slow_period"]

        if len(df) < slow + 2:
            return Signal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Insufficient data (need {slow + 2}, got {len(df)})",
            )

        # 計算 SMA (如果尚未計算)
        fast_col = f"SMA_{fast}"
        slow_col = f"SMA_{slow}"

        if fast_col not in df.columns:
            df[fast_col] = df["close"].rolling(window=fast).mean()
        if slow_col not in df.columns:
            df[slow_col] = df["close"].rolling(window=slow).mean()

        # 取最近兩根 K 線
        current = df.iloc[-1]
        previous = df.iloc[-2]

        current_fast = current[fast_col]
        current_slow = current[slow_col]
        prev_fast = previous[fast_col]
        prev_slow = previous[slow_col]

        # 金叉 (Golden Cross): 快線從下方穿越慢線
        if prev_fast <= prev_slow and current_fast > current_slow:
            # 信號強度根據穿越幅度
            spread = (current_fast - current_slow) / current_slow
            strength = min(abs(spread) * 100, 1.0)

            return Signal(
                signal_type=SignalType.BUY,
                strength=max(strength, 0.5),
                price=current["close"],
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Golden Cross: SMA{fast}({current_fast:.2f}) > SMA{slow}({current_slow:.2f})",
                metadata={"fast_sma": current_fast, "slow_sma": current_slow},
            )

        # 死叉 (Death Cross): 快線從上方穿越慢線
        if prev_fast >= prev_slow and current_fast < current_slow:
            spread = (current_slow - current_fast) / current_slow
            strength = min(abs(spread) * 100, 1.0)

            return Signal(
                signal_type=SignalType.SELL,
                strength=max(strength, 0.5),
                price=current["close"],
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Death Cross: SMA{fast}({current_fast:.2f}) < SMA{slow}({current_slow:.2f})",
                metadata={"fast_sma": current_fast, "slow_sma": current_slow},
            )

        # 無交叉 → HOLD
        return Signal(
            signal_type=SignalType.HOLD,
            price=current["close"],
            symbol=symbol,
            strategy_name=self.name,
            reason=f"No crossover. SMA{fast}={current_fast:.2f}, SMA{slow}={current_slow:.2f}",
        )
