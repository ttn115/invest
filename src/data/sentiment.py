"""
市場情緒資料收集器 (Sentiment Data Collector)

收集外部情緒指標，用於判斷市場的恐慌/貪婪程度：

1. Fear & Greed Index (加密幣)
   - 來源: Alternative.me 免費 API
   - 數值: 0 (極度恐慌) ~ 100 (極度貪婪)
   - 更新頻率: 每日更新

2. VIX 指數 (美股恐慌指數)
   - 來源: yfinance (^VIX)
   - 數值: 通常 12~80，越高代表市場越恐慌
   - VIX < 15: 市場平靜 / VIX 20~30: 緊張 / VIX > 30: 高度恐慌

3. 加密幣恐慌貪婪指數 (Crypto Fear & Greed)
   - 0~25: 極度恐慌 (Extreme Fear) — 可能是抄底訊號
   - 25~45: 恐慌 (Fear) — 市場謹慎
   - 45~55: 中性 (Neutral) — 觀望
   - 55~75: 貪婪 (Greed) — 市場樂觀
   - 75~100: 極度貪婪 (Extreme Greed) — 可能是逃頂訊號
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd
from loguru import logger


class SentimentLevel(Enum):
    """情緒等級"""
    EXTREME_FEAR = "extreme_fear"       # 極度恐慌 (0~25)
    FEAR = "fear"                       # 恐慌 (25~45)
    NEUTRAL = "neutral"                 # 中性 (45~55)
    GREED = "greed"                     # 貪婪 (55~75)
    EXTREME_GREED = "extreme_greed"     # 極度貪婪 (75~100)


@dataclass
class SentimentData:
    """
    情緒資料結構

    Attributes:
        value: 情緒數值 (0~100, 0=極度恐慌, 100=極度貪婪)
        level: 情緒等級
        label: 情緒標籤 (文字描述)
        source: 資料來源
        timestamp: 資料時間
    """
    value: float
    level: SentimentLevel
    label: str
    source: str
    timestamp: dt.datetime = field(default_factory=dt.datetime.now)

    @staticmethod
    def classify(value: float) -> SentimentLevel:
        """將數值分類為情緒等級"""
        if value <= 25:
            return SentimentLevel.EXTREME_FEAR
        elif value <= 45:
            return SentimentLevel.FEAR
        elif value <= 55:
            return SentimentLevel.NEUTRAL
        elif value <= 75:
            return SentimentLevel.GREED
        else:
            return SentimentLevel.EXTREME_GREED


class CryptoFearGreedCollector:
    """
    加密幣恐懼與貪婪指數收集器

    使用 Alternative.me 免費 API (無需 API Key)。
    API 文件: https://alternative.me/crypto/fear-and-greed-index/

    回傳 0~100 的數值:
    - 0~25: 極度恐慌 — 投資者過度擔心，可能是買入機會
    - 75~100: 極度貪婪 — 投資者過度樂觀，可能是賣出訊號
    """

    API_URL = "https://api.alternative.me/fng/"

    def get_current(self) -> Optional[SentimentData]:
        """取得最新的恐懼貪婪指數"""
        try:
            import requests
            response = requests.get(self.API_URL, params={"limit": 1}, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("data"):
                entry = data["data"][0]
                value = float(entry["value"])
                label = entry["value_classification"]
                timestamp = dt.datetime.fromtimestamp(int(entry["timestamp"]))

                sentiment = SentimentData(
                    value=value,
                    level=SentimentData.classify(value),
                    label=label,
                    source="crypto_fear_greed",
                    timestamp=timestamp,
                )
                logger.info(f"🧠 Crypto Fear & Greed: {value} ({label})")
                return sentiment

        except ImportError:
            logger.warning("requests library not installed.")
        except Exception as e:
            logger.error(f"Failed to fetch Crypto Fear & Greed: {e}")

        return None

    def get_historical(self, days: int = 365) -> pd.DataFrame:
        """
        取得歷史恐懼貪婪指數

        Args:
            days: 歷史天數 (最多約 2 年)

        Returns:
            DataFrame with columns: [timestamp, value, level, label]
        """
        try:
            import requests
            response = requests.get(
                self.API_URL, params={"limit": days}, timeout=15
            )
            response.raise_for_status()
            data = response.json()

            if data.get("data"):
                records = []
                for entry in data["data"]:
                    value = float(entry["value"])
                    records.append({
                        "timestamp": dt.datetime.fromtimestamp(int(entry["timestamp"])),
                        "sentiment_value": value,
                        "sentiment_level": SentimentData.classify(value).value,
                        "sentiment_label": entry["value_classification"],
                    })

                df = pd.DataFrame(records)
                df = df.sort_values("timestamp").reset_index(drop=True)
                logger.info(f"Fetched {len(df)} days of Crypto Fear & Greed history")
                return df

        except Exception as e:
            logger.error(f"Failed to fetch historical Fear & Greed: {e}")

        return pd.DataFrame()


class VIXCollector:
    """
    VIX 恐慌指數收集器 (CBOE Volatility Index)

    VIX 是衍生自 S&P 500 選擇權的隱含波動率指標:
    - VIX < 15: 市場非常平靜 (低波動)
    - VIX 15~20: 正常市場
    - VIX 20~30: 市場緊張 (中度恐慌)
    - VIX > 30: 高度恐慌 (可能出現大跌或反彈）
    - VIX > 40: 極度恐慌 (2008, 2020 COVID 等級)

    我們將 VIX 轉換為 0~100 的情緒分數 (與 Fear & Greed 同向):
    VIX 高 → 恐慌 → 情緒分數低
    VIX 低 → 平靜/貪婪 → 情緒分數高
    """

    @staticmethod
    def vix_to_sentiment(vix_value: float) -> float:
        """
        將 VIX 值轉換為 0~100 的情緒分數。

        轉換邏輯 (線性映射，反向):
        - VIX 10 → 情緒 90 (極度貪婪)
        - VIX 20 → 情緒 60 (微貪婪)
        - VIX 30 → 情緒 30 (恐慌)
        - VIX 50+ → 情緒 ~5 (極度恐慌)
        """
        # 線性映射: vix [10, 50] → sentiment [90, 5]
        sentiment = max(5, min(95, 90 - (vix_value - 10) * (85 / 40)))
        return round(sentiment, 1)

    def get_current(self) -> Optional[SentimentData]:
        """取得最新 VIX 值並轉換為情緒分數"""
        try:
            import yfinance as yf
            ticker = yf.Ticker("^VIX")
            hist = ticker.history(period="5d")

            if hist.empty:
                logger.warning("VIX data is empty")
                return None

            vix_value = float(hist["Close"].iloc[-1])
            sentiment_value = self.vix_to_sentiment(vix_value)

            sentiment = SentimentData(
                value=sentiment_value,
                level=SentimentData.classify(sentiment_value),
                label=f"VIX={vix_value:.1f}",
                source="vix",
                timestamp=hist.index[-1].to_pydatetime(),
            )
            logger.info(f"🧠 VIX: {vix_value:.1f} → Sentiment: {sentiment_value} ({sentiment.level.value})")
            return sentiment

        except ImportError:
            logger.warning("yfinance library not installed.")
        except Exception as e:
            logger.error(f"Failed to fetch VIX: {e}")

        return None

    def get_historical(self, start: str = "2024-01-01", end: str | None = None) -> pd.DataFrame:
        """
        取得歷史 VIX 資料並轉換為情緒分數

        Args:
            start: 開始日期 (YYYY-MM-DD)
            end: 結束日期

        Returns:
            DataFrame with columns: [timestamp, vix, sentiment_value, sentiment_level]
        """
        try:
            import yfinance as yf
            ticker = yf.Ticker("^VIX")
            hist = ticker.history(start=start, end=end)

            if hist.empty:
                return pd.DataFrame()

            records = []
            for idx, row in hist.iterrows():
                vix_val = float(row["Close"])
                sent_val = self.vix_to_sentiment(vix_val)
                records.append({
                    "timestamp": idx.to_pydatetime(),
                    "vix": vix_val,
                    "sentiment_value": sent_val,
                    "sentiment_level": SentimentData.classify(sent_val).value,
                })

            df = pd.DataFrame(records)
            logger.info(f"Fetched {len(df)} days of VIX history")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch VIX history: {e}")

        return pd.DataFrame()


class SentimentCollector:
    """
    統一情緒收集器 — 根據市場類型自動選擇對應的情緒來源。

    Usage:
        collector = SentimentCollector()
        sentiment = collector.get_sentiment("crypto")   # → Fear & Greed Index
        sentiment = collector.get_sentiment("us_stock")  # → VIX
    """

    def __init__(self):
        self._crypto = CryptoFearGreedCollector()
        self._vix = VIXCollector()

    def get_sentiment(self, market_name: str) -> Optional[SentimentData]:
        """
        取得指定市場的情緒資料。

        Args:
            market_name: "crypto" / "us_stock" / "tw_stock"

        Returns:
            SentimentData 或 None
        """
        if market_name == "crypto":
            return self._crypto.get_current()
        elif market_name in ("us_stock", "tw_stock"):
            return self._vix.get_current()
        else:
            logger.warning(f"No sentiment source for market: {market_name}")
            return None

    def get_historical(self, market_name: str, **kwargs) -> pd.DataFrame:
        """
        取得歷史情緒資料。

        Args:
            market_name: 市場名稱
            **kwargs: days (crypto) 或 start/end (us_stock)

        Returns:
            DataFrame with sentiment columns
        """
        if market_name == "crypto":
            return self._crypto.get_historical(days=kwargs.get("days", 365))
        elif market_name in ("us_stock", "tw_stock"):
            return self._vix.get_historical(
                start=kwargs.get("start", "2024-01-01"),
                end=kwargs.get("end"),
            )
        return pd.DataFrame()
