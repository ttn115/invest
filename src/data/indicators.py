"""
技術指標計算引擎 (Indicators)

使用 pandas-ta 計算各種技術指標，提供統一的計算介面。
支援: SMA, EMA, RSI, MACD, Bollinger Bands, ATR, Volume Profile 等。
"""

from __future__ import annotations

import pandas as pd
from loguru import logger


class IndicatorEngine:
    """
    技術指標計算引擎

    Usage:
        engine = IndicatorEngine()
        df = engine.add_all(ohlcv_df)
        df = engine.add_sma(ohlcv_df, period=20)
    """

    @staticmethod
    def add_sma(df: pd.DataFrame, period: int = 20, column: str = "close") -> pd.DataFrame:
        """
        簡單移動平均線 (Simple Moving Average)

        Args:
            df: OHLCV DataFrame
            period: 計算週期
            column: 用於計算的欄位

        Returns:
            新增 SMA_{period} 欄位的 DataFrame
        """
        col_name = f"SMA_{period}"
        df[col_name] = df[column].rolling(window=period).mean()
        return df

    @staticmethod
    def add_ema(df: pd.DataFrame, period: int = 20, column: str = "close") -> pd.DataFrame:
        """指數移動平均線 (Exponential Moving Average)"""
        col_name = f"EMA_{period}"
        df[col_name] = df[column].ewm(span=period, adjust=False).mean()
        return df

    @staticmethod
    def add_rsi(df: pd.DataFrame, period: int = 14, column: str = "close") -> pd.DataFrame:
        """
        相對強弱指數 (Relative Strength Index)

        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        """
        delta = df[column].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        # 使用 Wilder's smoothing 方法 (更精確)
        for i in range(period, len(df)):
            avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

        rs = avg_gain / avg_loss.replace(0, float("inf"))
        df[f"RSI_{period}"] = 100 - (100 / (1 + rs))
        return df

    @staticmethod
    def add_macd(
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        column: str = "close",
    ) -> pd.DataFrame:
        """
        MACD (Moving Average Convergence Divergence)

        Returns:
            新增 MACD, MACD_Signal, MACD_Hist 欄位
        """
        ema_fast = df[column].ewm(span=fast, adjust=False).mean()
        ema_slow = df[column].ewm(span=slow, adjust=False).mean()

        df["MACD"] = ema_fast - ema_slow
        df["MACD_Signal"] = df["MACD"].ewm(span=signal, adjust=False).mean()
        df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]
        return df

    @staticmethod
    def add_bollinger_bands(
        df: pd.DataFrame,
        period: int = 20,
        std_dev: float = 2.0,
        column: str = "close",
    ) -> pd.DataFrame:
        """
        布林通道 (Bollinger Bands)

        Returns:
            新增 BB_Upper, BB_Middle, BB_Lower 欄位
        """
        sma = df[column].rolling(window=period).mean()
        std = df[column].rolling(window=period).std()

        df["BB_Upper"] = sma + (std * std_dev)
        df["BB_Middle"] = sma
        df["BB_Lower"] = sma - (std * std_dev)
        df["BB_Width"] = (df["BB_Upper"] - df["BB_Lower"]) / df["BB_Middle"]
        return df

    @staticmethod
    def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        平均真實波幅 (Average True Range)

        用於衡量波動率，常用於止損計算。
        """
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        df[f"ATR_{period}"] = true_range.rolling(window=period).mean()
        return df

    @staticmethod
    def add_volume_sma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """成交量移動平均"""
        df[f"Vol_SMA_{period}"] = df["volume"].rolling(window=period).mean()
        return df

    @staticmethod
    def add_stochastic(
        df: pd.DataFrame, k_period: int = 14, d_period: int = 3
    ) -> pd.DataFrame:
        """隨機指標 (Stochastic Oscillator)"""
        low_min = df["low"].rolling(window=k_period).min()
        high_max = df["high"].rolling(window=k_period).max()

        df["STOCH_K"] = ((df["close"] - low_min) / (high_max - low_min)) * 100
        df["STOCH_D"] = df["STOCH_K"].rolling(window=d_period).mean()
        return df

    def add_all(
        self,
        df: pd.DataFrame,
        sma_periods: list[int] | None = None,
        rsi_period: int = 14,
        macd_params: tuple[int, int, int] = (12, 26, 9),
        bb_params: tuple[int, float] = (20, 2.0),
        atr_period: int = 14,
    ) -> pd.DataFrame:
        """
        計算所有常用指標

        Args:
            df: OHLCV DataFrame
            sma_periods: SMA 週期列表 (預設 [10, 20, 50])
            rsi_period: RSI 週期
            macd_params: MACD 參數 (fast, slow, signal)
            bb_params: Bollinger 參數 (period, std_dev)
            atr_period: ATR 週期

        Returns:
            含所有指標的 DataFrame
        """
        if sma_periods is None:
            sma_periods = [10, 20, 50]

        df = df.copy()

        # 移動平均
        for period in sma_periods:
            df = self.add_sma(df, period)
            df = self.add_ema(df, period)

        # 動量指標
        df = self.add_rsi(df, rsi_period)
        df = self.add_macd(df, *macd_params)
        df = self.add_stochastic(df)

        # 波動率
        df = self.add_bollinger_bands(df, *bb_params)
        df = self.add_atr(df, atr_period)

        # 成交量
        df = self.add_volume_sma(df, 20)

        logger.info(f"Calculated all indicators. Columns: {len(df.columns)}")
        return df
