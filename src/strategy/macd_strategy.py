"""
MACD 策略 (MACD Strategy)

使用 MACD 指標偵測動量變化和趨勢方向。
MACD 線上穿信號線表示做多動量增強，下穿表示做空動量增強。
"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy, Signal, SignalType


class MACDStrategy(BaseStrategy):
    """
    MACD 動量策略

    Params:
        fast (int): 快速 EMA 週期，預設 12
        slow (int): 慢速 EMA 週期，預設 26
        signal (int): 信號線 EMA 週期，預設 9
    """

    def __init__(self, params: dict | None = None):
        default_params = {"fast": 12, "slow": 26, "signal": 9}
        if params:
            default_params.update(params)
        super().__init__(name="MACD", params=default_params)

    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        """產生 MACD 信號"""
        fast = self.params["fast"]
        slow = self.params["slow"]
        signal_period = self.params["signal"]

        min_data = slow + signal_period + 2
        if len(df) < min_data:
            return Signal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Insufficient data (need {min_data}, got {len(df)})",
            )

        # 計算 MACD (如果尚未計算)
        if "MACD" not in df.columns:
            ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
            ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
            df["MACD"] = ema_fast - ema_slow
            df["MACD_Signal"] = df["MACD"].ewm(span=signal_period, adjust=False).mean()
            df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

        current = df.iloc[-1]
        previous = df.iloc[-2]
        current_price = current["close"]

        macd_now = current["MACD"]
        signal_now = current["MACD_Signal"]
        hist_now = current["MACD_Hist"]
        macd_prev = previous["MACD"]
        signal_prev = previous["MACD_Signal"]
        hist_prev = previous["MACD_Hist"]

        # MACD 上穿信號線 (看多交叉)
        if macd_prev <= signal_prev and macd_now > signal_now:
            # 信號強度根據柱狀圖大小
            strength = min(abs(hist_now) / (abs(current_price) * 0.01 + 1e-9), 1.0)
            # 零軸上方的交叉更有力
            if macd_now > 0:
                strength = min(strength + 0.2, 1.0)

            return Signal(
                signal_type=SignalType.BUY,
                strength=max(strength, 0.4),
                price=current_price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"MACD Bullish Cross: MACD={macd_now:.4f} > Signal={signal_now:.4f}",
                metadata={
                    "macd": macd_now,
                    "signal": signal_now,
                    "histogram": hist_now,
                },
            )

        # MACD 下穿信號線 (看空交叉)
        if macd_prev >= signal_prev and macd_now < signal_now:
            strength = min(abs(hist_now) / (abs(current_price) * 0.01 + 1e-9), 1.0)
            if macd_now < 0:
                strength = min(strength + 0.2, 1.0)

            return Signal(
                signal_type=SignalType.SELL,
                strength=max(strength, 0.4),
                price=current_price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"MACD Bearish Cross: MACD={macd_now:.4f} < Signal={signal_now:.4f}",
                metadata={
                    "macd": macd_now,
                    "signal": signal_now,
                    "histogram": hist_now,
                },
            )

        # [v0.6.3] 柱狀圖動量弱信號 — 不再只回傳 HOLD
        # 柱狀圖翻正 (動量反轉看多, strength 0.4)
        if hist_prev < 0 and hist_now > 0:
            return Signal(
                signal_type=SignalType.BUY,
                strength=0.4,
                price=current_price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"MACD Histogram flip positive ({hist_prev:.4f} → {hist_now:.4f})",
                metadata={"macd": macd_now, "signal": signal_now, "histogram": hist_now},
            )

        # 柱狀圖翻負 (動量反轉看空, strength 0.4)
        if hist_prev > 0 and hist_now < 0:
            return Signal(
                signal_type=SignalType.SELL,
                strength=0.4,
                price=current_price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"MACD Histogram flip negative ({hist_prev:.4f} → {hist_now:.4f})",
                metadata={"macd": macd_now, "signal": signal_now, "histogram": hist_now},
            )

        # 柱狀圖正向且加速 (趨勢持續看多, strength 0.3)
        if hist_now > 0 and hist_now > hist_prev:
            return Signal(
                signal_type=SignalType.BUY,
                strength=0.3,
                price=current_price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"MACD Histogram expanding positive ({hist_prev:.4f} → {hist_now:.4f})",
                metadata={"macd": macd_now, "signal": signal_now, "histogram": hist_now},
            )

        # 柱狀圖負向且擴大 (趨勢持續看空, strength 0.3)
        if hist_now < 0 and hist_now < hist_prev:
            return Signal(
                signal_type=SignalType.SELL,
                strength=0.3,
                price=current_price,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"MACD Histogram expanding negative ({hist_prev:.4f} → {hist_now:.4f})",
                metadata={"macd": macd_now, "signal": signal_now, "histogram": hist_now},
            )

        # 其他情況 → HOLD (柱狀圖收縮或不變)
        return Signal(
            signal_type=SignalType.HOLD,
            price=current_price,
            symbol=symbol,
            strategy_name=self.name,
            reason=f"MACD neutral (Hist={hist_now:.4f})",
        )
