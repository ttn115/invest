"""
RSI 策略 (RSI Strategy)

使用相對強弱指數偵測超買超賣區域，進行均值回歸交易。
適合盤整市場。
"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy, Signal, SignalType


class RSIStrategy(BaseStrategy):
    """
    RSI 超買超賣策略

    Params:
        period (int): RSI 計算週期，預設 14
        oversold (int): 超賣閾值，預設 30
        overbought (int): 超買閾值，預設 70
    """

    def __init__(self, params: dict | None = None):
        default_params = {"period": 14, "oversold": 30, "overbought": 70}
        if params:
            default_params.update(params)
        super().__init__(name="RSI", params=default_params)

    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        """產生 RSI 信號"""
        period = self.params["period"]
        oversold = self.params["oversold"]
        overbought = self.params["overbought"]

        rsi_col = f"RSI_{period}"

        if len(df) < period + 2:
            return Signal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Insufficient data (need {period + 2}, got {len(df)})",
            )

        # 計算 RSI (如果尚未計算)
        if rsi_col not in df.columns:
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta).where(delta < 0, 0.0)
            avg_gain = gain.rolling(window=period).mean()
            avg_loss = loss.rolling(window=period).mean()
            rs = avg_gain / avg_loss.replace(0, float("inf"))
            df[rsi_col] = 100 - (100 / (1 + rs))

        current_rsi = df[rsi_col].iloc[-1]
        prev_rsi = df[rsi_col].iloc[-2]
        current_price = df["close"].iloc[-1]

        # 超賣區 → BUY
        if current_rsi < oversold:
            # 信號強度：RSI 越低越強
            strength = (oversold - current_rsi) / oversold
            # 加分：RSI 從下方回升
            if current_rsi > prev_rsi:
                strength = min(strength + 0.2, 1.0)

            return Signal(
                signal_type=SignalType.BUY,
                strength=max(strength, 0.3),
                price=current_price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Oversold: RSI={current_rsi:.1f} < {oversold}",
                metadata={"rsi": current_rsi, "prev_rsi": prev_rsi},
            )

        # 超買區 → SELL
        if current_rsi > overbought:
            strength = (current_rsi - overbought) / (100 - overbought)
            if current_rsi < prev_rsi:
                strength = min(strength + 0.2, 1.0)

            return Signal(
                signal_type=SignalType.SELL,
                strength=max(strength, 0.3),
                price=current_price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Overbought: RSI={current_rsi:.1f} > {overbought}",
                metadata={"rsi": current_rsi, "prev_rsi": prev_rsi},
            )

        # 中間區域 → HOLD
        return Signal(
            signal_type=SignalType.HOLD,
            price=current_price,
            symbol=symbol,
            strategy_name=self.name,
            reason=f"Neutral zone: RSI={current_rsi:.1f}",
        )
