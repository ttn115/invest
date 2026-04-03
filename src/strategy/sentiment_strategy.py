"""
情緒策略 (Sentiment Strategy)

根據市場情緒指標 (Fear & Greed / VIX) 產生交易信號。

策略邏輯 — 反向思維 (Contrarian)：
  「別人恐慌時我貪婪，別人貪婪時我恐慌。」 — Warren Buffett

- 極度恐慌 (0~25) → BUY 信號 (抄底機會)
- 恐慌 (25~45)     → 偏 BUY (市場過度悲觀)
- 中性 (45~55)     → HOLD (觀望)
- 貪婪 (55~75)     → 偏 SELL (市場可能過熱)
- 極度貪婪 (75~100) → SELL 信號 (逃頂訊號)

此策略同時也可以作為「保護機制」：當市場極度恐慌時，即使技術指標
說要買，情緒策略的 HOLD/SELL 投票也能降低衝動進場的機率。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from loguru import logger

from src.data.sentiment import SentimentCollector, SentimentData, SentimentLevel
from src.strategy.base import BaseStrategy, Signal, SignalType


class SentimentStrategy(BaseStrategy):
    """
    基於市場情緒的策略

    參數 (可透過 config.yaml 設定):
        mode: "contrarian" (反向) 或 "momentum" (順勢)
        fear_buy_threshold: 恐慌買入閾值 (預設 25)
        greed_sell_threshold: 貪婪賣出閾值 (預設 75)
        neutral_zone: 中性區寬度 (在此區間輸出 HOLD)
        market_name: 使用哪個市場的情緒來源
    """

    def __init__(self, params: Optional[dict] = None):
        default_params = {
            "mode": "contrarian",          # contrarian=反向, momentum=順勢
            "fear_buy_threshold": 25,      # 低於此值 → 買入信號
            "greed_sell_threshold": 75,     # 高於此值 → 賣出信號
            "neutral_low": 40,             # 中性區下界
            "neutral_high": 60,            # 中性區上界
            "market_name": "crypto",       # 預設市場
        }
        if params:
            default_params.update(params)

        super().__init__(name="Sentiment", params=default_params)
        self._collector = SentimentCollector()
        self._last_sentiment: Optional[SentimentData] = None

    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        """
        根據當前市場情緒產生交易信號。

        此策略不像其他技術策略直接看 K 線，而是查詢外部情緒 API。
        如果 API 暫時不可用，會使用最近一次快取的情緒資料。

        Args:
            df: OHLCV DataFrame (此策略主要不使用，但遵循介面)
            symbol: 交易標的

        Returns:
            Signal 交易信號
        """
        market_name = self.params.get("market_name", "crypto")
        mode = self.params.get("mode", "contrarian")

        # 取得情緒資料 (帶快取)
        sentiment = self._collector.get_sentiment(market_name)
        if sentiment is not None:
            self._last_sentiment = sentiment
        elif self._last_sentiment is not None:
            sentiment = self._last_sentiment
            logger.warning("Using cached sentiment data")
        else:
            # 完全沒有情緒資料 → 回傳 HOLD
            return Signal(
                signal_type=SignalType.HOLD,
                strength=0.0,
                price=float(df["close"].iloc[-1]) if not df.empty else 0.0,
                symbol=symbol,
                strategy_name=self.name,
                reason="No sentiment data available",
            )

        value = sentiment.value
        current_price = float(df["close"].iloc[-1]) if not df.empty else 0.0

        # 計算信號
        signal_type, strength, reason = self._evaluate_sentiment(value, mode, sentiment)

        return Signal(
            signal_type=signal_type,
            strength=strength,
            price=current_price,
            symbol=symbol,
            strategy_name=self.name,
            reason=reason,
            metadata={
                "sentiment_value": value,
                "sentiment_level": sentiment.level.value,
                "sentiment_label": sentiment.label,
                "sentiment_source": sentiment.source,
                "mode": mode,
            },
        )

    def _evaluate_sentiment(
        self,
        value: float,
        mode: str,
        sentiment: SentimentData,
    ) -> tuple[SignalType, float, str]:
        """
        根據情緒數值評估信號。

        Args:
            value: 情緒分數 (0~100)
            mode: "contrarian" 或 "momentum"
            sentiment: 完整情緒資料

        Returns:
            (signal_type, strength, reason)
        """
        fear_buy = self.params["fear_buy_threshold"]
        greed_sell = self.params["greed_sell_threshold"]
        neutral_low = self.params["neutral_low"]
        neutral_high = self.params["neutral_high"]

        label = sentiment.label

        if mode == "contrarian":
            # 反向策略: 恐慌買、貪婪賣
            if value <= fear_buy:
                # 極度恐慌 → 強烈買入 (越恐慌越強)
                strength = min(1.0, (fear_buy - value) / fear_buy + 0.5)
                return SignalType.BUY, strength, f"Extreme Fear ({label}, {value}), contrarian BUY"

            elif value <= neutral_low:
                # 偏恐慌 → 輕微買入
                strength = 0.3 + (neutral_low - value) / (neutral_low - fear_buy) * 0.3
                return SignalType.BUY, strength, f"Fear ({label}, {value}), mild BUY"

            elif value <= neutral_high:
                # 中性 → 觀望
                return SignalType.HOLD, 0.1, f"Neutral ({label}, {value}), HOLD"

            elif value <= greed_sell:
                # 偏貪婪 → 輕微賣出
                strength = 0.3 + (value - neutral_high) / (greed_sell - neutral_high) * 0.3
                return SignalType.SELL, strength, f"Greed ({label}, {value}), mild SELL"

            else:
                # 極度貪婪 → 強烈賣出
                strength = min(1.0, (value - greed_sell) / (100 - greed_sell) + 0.5)
                return SignalType.SELL, strength, f"Extreme Greed ({label}, {value}), contrarian SELL"

        else:
            # 順勢策略: 恐慌賣、貪婪買 (跟隨大眾)
            if value <= fear_buy:
                strength = min(1.0, (fear_buy - value) / fear_buy + 0.5)
                return SignalType.SELL, strength, f"Extreme Fear ({label}, {value}), momentum SELL"

            elif value >= greed_sell:
                strength = min(1.0, (value - greed_sell) / (100 - greed_sell) + 0.5)
                return SignalType.BUY, strength, f"Extreme Greed ({label}, {value}), momentum BUY"

            else:
                return SignalType.HOLD, 0.1, f"Neutral zone ({label}, {value}), HOLD"

    @property
    def last_sentiment(self) -> Optional[SentimentData]:
        """取得最近一次的情緒資料"""
        return self._last_sentiment
