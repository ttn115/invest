"""
成交量確認濾網 (Volume Confirmation Filter)

當成交量低於均量一定比例時，否決 BUY 信號。
低量環境的技術指標容易產生假信號。
"""

import pandas as pd
from loguru import logger

from src.strategy.base import BaseStrategy, Signal, SignalType


class VolumeFilterStrategy(BaseStrategy):
    """
    成交量確認濾網

    Params:
        min_volume_ratio (float): 最低成交量相對於 20 日均量的比例，預設 0.5 (50%)
        vol_sma_period (int): 均量計算週期，預設 20
    """

    def __init__(
        self,
        name: str = "VolumeFilter",
        min_volume_ratio: float = 0.5,
        vol_sma_period: int = 20,
        **kwargs,
    ):
        super().__init__(name=name)
        self.min_volume_ratio = min_volume_ratio
        self.vol_sma_period = vol_sma_period

    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        """
        成交量過低 → Veto (HOLD)
        成交量正常 → Pass (BUY)
        """
        if df.empty or "volume" not in df.columns:
            return Signal(
                signal_type=SignalType.BUY,
                strength=1.0,
                strategy_name=self.name,
                reason="No volume data, default to pass",
            )

        if len(df) < self.vol_sma_period + 1:
            return Signal(
                signal_type=SignalType.BUY,
                strength=1.0,
                strategy_name=self.name,
                reason="Not enough data for volume SMA, default to pass",
            )

        # 計算均量
        vol_sma_col = f"Vol_SMA_{self.vol_sma_period}"
        if vol_sma_col not in df.columns:
            df[vol_sma_col] = df["volume"].rolling(window=self.vol_sma_period).mean()

        current_vol = df["volume"].iloc[-1]
        avg_vol = df[vol_sma_col].iloc[-1]

        if avg_vol is None or avg_vol == 0 or pd.isna(avg_vol):
            return Signal(
                signal_type=SignalType.BUY,
                strength=1.0,
                strategy_name=self.name,
                reason="Avg volume is 0, default to pass",
            )

        vol_ratio = current_vol / avg_vol

        if vol_ratio < self.min_volume_ratio:
            return Signal(
                signal_type=SignalType.HOLD,
                strength=1.0,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Low volume: {vol_ratio:.1%} of avg (threshold: {self.min_volume_ratio:.0%}). Veto BUY.",
            )

        return Signal(
            signal_type=SignalType.BUY,
            strength=1.0,
            symbol=symbol,
            strategy_name=self.name,
            reason=f"Volume OK: {vol_ratio:.1%} of avg",
        )
