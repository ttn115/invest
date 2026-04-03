"""
布林通道策略 (Bollinger Bands Strategy)

利用布林通道判斷價格是否偏離均值，進行均值回歸交易。
也可配合突破模式追蹤趨勢。
"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy, Signal, SignalType


class BollingerStrategy(BaseStrategy):
    """
    布林通道策略

    Params:
        period (int): 計算週期，預設 20
        std_dev (float): 標準差倍數，預設 2.0
        mode (str): 模式 - "reversion" 均值回歸 / "breakout" 突破追蹤
    """

    def __init__(self, params: dict | None = None):
        default_params = {"period": 20, "std_dev": 2.0, "mode": "reversion"}
        if params:
            default_params.update(params)
        super().__init__(name="Bollinger", params=default_params)

    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        """產生布林通道信號"""
        period = self.params["period"]
        std_dev = self.params["std_dev"]
        mode = self.params.get("mode", "reversion")

        if len(df) < period + 2:
            return Signal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Insufficient data (need {period + 2}, got {len(df)})",
            )

        # 計算布林通道 (如果尚未計算)
        if "BB_Upper" not in df.columns:
            sma = df["close"].rolling(window=period).mean()
            std = df["close"].rolling(window=period).std()
            df["BB_Upper"] = sma + (std * std_dev)
            df["BB_Middle"] = sma
            df["BB_Lower"] = sma - (std * std_dev)

        current = df.iloc[-1]
        previous = df.iloc[-2]
        price = current["close"]
        upper = current["BB_Upper"]
        middle = current["BB_Middle"]
        lower = current["BB_Lower"]
        band_width = upper - lower

        if mode == "reversion":
            return self._reversion_signal(
                price, upper, middle, lower, band_width, previous, symbol
            )
        else:
            return self._breakout_signal(
                price, upper, middle, lower, band_width, previous, symbol
            )

    def _reversion_signal(
        self, price, upper, middle, lower, band_width, previous, symbol
    ) -> Signal:
        """均值回歸模式：觸及邊界反轉"""
        # 計算價格在通道中的位置 (%B)
        pct_b = (price - lower) / (band_width + 1e-9)

        # 價格觸及或跌破下軌 → BUY
        if price <= lower:
            prev_price = previous["close"]
            # 確認反彈跡象
            strength = min((lower - price) / (band_width * 0.1 + 1e-9), 1.0)
            if prev_price < price:  # 價格回升
                strength = min(strength + 0.2, 1.0)

            return Signal(
                signal_type=SignalType.BUY,
                strength=max(strength, 0.4),
                price=price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Price at lower band: {price:.2f} <= BB_Lower({lower:.2f}), %B={pct_b:.2f}",
                metadata={"bb_upper": upper, "bb_middle": middle, "bb_lower": lower, "pct_b": pct_b},
            )

        # 價格觸及或突破上軌 → SELL
        if price >= upper:
            strength = min((price - upper) / (band_width * 0.1 + 1e-9), 1.0)
            prev_price = previous["close"]
            if prev_price > price:  # 價格回落
                strength = min(strength + 0.2, 1.0)

            return Signal(
                signal_type=SignalType.SELL,
                strength=max(strength, 0.4),
                price=price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Price at upper band: {price:.2f} >= BB_Upper({upper:.2f}), %B={pct_b:.2f}",
                metadata={"bb_upper": upper, "bb_middle": middle, "bb_lower": lower, "pct_b": pct_b},
            )

        # 通道內 → HOLD
        return Signal(
            signal_type=SignalType.HOLD,
            price=price,
            symbol=symbol,
            strategy_name=self.name,
            reason=f"Within bands: %B={pct_b:.2f}, price={price:.2f}",
        )

    def _breakout_signal(
        self, price, upper, middle, lower, band_width, previous, symbol
    ) -> Signal:
        """突破模式：突破邊界追蹤趨勢"""
        prev_price = previous["close"]
        prev_upper = previous.get("BB_Upper", upper)
        prev_lower = previous.get("BB_Lower", lower)

        # 向上突破上軌 → BUY (追蹤上升趨勢)
        if prev_price < prev_upper and price > upper:
            return Signal(
                signal_type=SignalType.BUY,
                strength=0.7,
                price=price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Breakout above upper band: {price:.2f} > {upper:.2f}",
            )

        # 向下突破下軌 → SELL (追蹤下降趨勢)
        if prev_price > prev_lower and price < lower:
            return Signal(
                signal_type=SignalType.SELL,
                strength=0.7,
                price=price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Breakdown below lower band: {price:.2f} < {lower:.2f}",
            )

        return Signal(
            signal_type=SignalType.HOLD,
            price=price,
            symbol=symbol,
            strategy_name=self.name,
            reason=f"No breakout. Price={price:.2f}",
        )
