import pandas as pd
from loguru import logger

from src.strategy.base import BaseStrategy, Signal, SignalType

class RegimeFilterStrategy(BaseStrategy):
    """
    大盤環境濾網 (Regime Filter Strategy)
    
    用來判斷更宏觀的市場狀態（例如比特幣 BTC 的趨勢）。
    如果大盤處於下降趨勢，則禁止其他幣種做多。
    """

    def __init__(
        self,
        name: str = "BTCRegimeFilter",
        weight: float = 1.0,
        sma_period: int = 50,
    ):
        """
        Args:
            sma_period: 判斷大盤趨勢的均線週期
        """
        super().__init__(name=name)
        self.sma_period = sma_period

    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        """
        根據大盤 (BTC) 狀態產生訊號。身為濾網，必須回傳 BUY 代表允許做多，回傳 HOLD 代表禁止做多。
        """
        if df.empty or "btc_close" not in df.columns:
            logger.warning("No missing btc_close in DataFrame. Regime filter defaults to BUY (Pass).")
            return Signal(
                signal_type=SignalType.BUY,
                strength=1.0,
                strategy_name=self.name,
                reason="Missing BTC data, default to pass",
            )
            
        if len(df) < self.sma_period:
            return Signal(
                signal_type=SignalType.BUY,
                strength=1.0,
                strategy_name=self.name,
                reason="Not enough data for SMA, default to pass",
            )

        btc_close = df["btc_close"]
        btc_sma = btc_close.rolling(self.sma_period).mean().iloc[-1]
        current_btc = btc_close.iloc[-1]

        if current_btc > btc_sma:
            return Signal(
                signal_type=SignalType.BUY,
                strength=1.0,
                strategy_name=self.name,
                reason=f"BTC > SMA{self.sma_period} (Uptrend, Allow BUY)",
            )
        else:
            return Signal(
                signal_type=SignalType.HOLD,
                strength=1.0,
                strategy_name=self.name,
                reason=f"BTC < SMA{self.sma_period} (Downtrend, Veto BUY)",
            )
