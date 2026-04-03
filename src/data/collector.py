"""
資料收集器 (Data Collector)

負責從各交易所和資料源收集市場資料：
- CryptoCollector: CCXT 加密幣資料
- StockCollector: yfinance / Alpaca 美股資料
- TwStockCollector: Shioaji / TWSE 台股資料
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd
from loguru import logger


class BaseCollector(ABC):
    """資料收集器基礎類別"""

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        取得 OHLCV K線資料

        Args:
            symbol: 交易對/股票代碼 (e.g. "BTC/USDT", "AAPL", "2330")
            timeframe: K線週期 (1m, 5m, 15m, 1h, 4h, 1d)
            start: 起始日期 (YYYY-MM-DD)
            end: 結束日期 (YYYY-MM-DD)
            limit: 最大資料筆數

        Returns:
            DataFrame with columns: [timestamp, open, high, low, close, volume]
        """
        pass

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """取得即時價格"""
        pass

    @staticmethod
    def _standardize_df(df: pd.DataFrame) -> pd.DataFrame:
        """標準化 DataFrame 欄位"""
        required_cols = ["timestamp", "open", "high", "low", "close", "volume"]
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")

        df = df[required_cols].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()

        # 轉換為浮點數
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.dropna()


class CryptoCollector(BaseCollector):
    """
    加密幣資料收集器 (透過 CCXT)

    支援 100+ 交易所，預設使用 Binance。
    """

    def __init__(self, exchange_id: str = "binance", sandbox: bool = True):
        """
        Args:
            exchange_id: 交易所ID (binance, bybit, okx, etc.)
            sandbox: 是否使用 Testnet
        """
        try:
            import ccxt
            exchange_class = getattr(ccxt, exchange_id)
            self.exchange = exchange_class({
                "sandbox": sandbox,
                "enableRateLimit": True,
            })
            if sandbox:
                self.exchange.set_sandbox_mode(True)
            logger.info(f"CryptoCollector initialized: {exchange_id} (sandbox={sandbox})")
        except ImportError:
            logger.warning("ccxt not installed. Run: pip install ccxt")
            self.exchange = None
        except Exception as e:
            logger.error(f"Failed to init CryptoCollector: {e}")
            self.exchange = None

    def fetch_ohlcv(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """取得加密幣 OHLCV 資料"""
        if self.exchange is None:
            logger.error("Exchange not initialized")
            return pd.DataFrame()

        try:
            since = None
            if start:
                since = int(
                    dt.datetime.strptime(start, "%Y-%m-%d").timestamp() * 1000
                )

            ohlcv = self.exchange.fetch_ohlcv(
                symbol, timeframe, since=since, limit=limit
            )

            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

            if end:
                end_dt = pd.to_datetime(end)
                df = df[df["timestamp"] <= end_dt]

            logger.info(f"Fetched {len(df)} bars for {symbol} ({timeframe})")
            return self._standardize_df(df)

        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol}: {e}")
            return pd.DataFrame()

    def get_current_price(self, symbol: str = "BTC/USDT") -> float:
        """取得即時加密幣價格"""
        if self.exchange is None:
            return 0.0
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return float(ticker.get("last", 0))
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return 0.0


class StockCollector(BaseCollector):
    """
    美股資料收集器 (透過 yfinance)

    使用 yfinance 取得免費歷史資料。
    """

    def fetch_ohlcv(
        self,
        symbol: str = "AAPL",
        timeframe: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """取得美股 OHLCV 資料"""
        try:
            import yfinance as yf

            # 映射 timeframe
            tf_map = {
                "1m": "1m", "5m": "5m", "15m": "15m",
                "1h": "1h", "4h": "1h", "1d": "1d",
            }
            yf_interval = tf_map.get(timeframe, "1d")

            # 設定預設日期範圍
            if not end:
                end = dt.datetime.now().strftime("%Y-%m-%d")
            if not start:
                # 根據 limit 和 timeframe 推算起始日期
                days = limit if timeframe == "1d" else limit // 24
                start_dt = dt.datetime.now() - dt.timedelta(days=max(days, 30))
                start = start_dt.strftime("%Y-%m-%d")

            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start, end=end, interval=yf_interval)

            if df.empty:
                logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()

            # 重新命名欄位
            df = df.reset_index()
            df = df.rename(columns={
                "Date": "timestamp",
                "Datetime": "timestamp",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            })

            logger.info(f"Fetched {len(df)} bars for {symbol} ({timeframe})")
            return self._standardize_df(df)

        except ImportError:
            logger.warning("yfinance not installed. Run: pip install yfinance")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error fetching stock data for {symbol}: {e}")
            return pd.DataFrame()

    def get_current_price(self, symbol: str = "AAPL") -> float:
        """取得美股即時價格"""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            return float(info.get("lastPrice", 0))
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return 0.0


class TwStockCollector(BaseCollector):
    """
    台股資料收集器

    優先使用 Shioaji (永豐金)，備用 yfinance (台股代碼加 .TW 後綴)。
    """

    def __init__(self, use_shioaji: bool = False):
        """
        Args:
            use_shioaji: 是否使用 Shioaji API (需要永豐帳戶)
        """
        self.use_shioaji = use_shioaji
        self.shioaji_api = None

        if use_shioaji:
            try:
                import shioaji as sj
                self.shioaji_api = sj.Shioaji()
                logger.info("TwStockCollector initialized with Shioaji")
            except ImportError:
                logger.warning("Shioaji not installed. Run: pip install shioaji")
                self.use_shioaji = False

    def fetch_ohlcv(
        self,
        symbol: str = "2330",
        timeframe: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """取得台股 OHLCV 資料 (透過 yfinance 作為免費替代方案)"""
        try:
            import yfinance as yf

            # 台股代碼轉換: 2330 → 2330.TW
            yf_symbol = f"{symbol}.TW" if not symbol.endswith(".TW") else symbol

            if not end:
                end = dt.datetime.now().strftime("%Y-%m-%d")
            if not start:
                start_dt = dt.datetime.now() - dt.timedelta(days=max(limit, 365))
                start = start_dt.strftime("%Y-%m-%d")

            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(start=start, end=end)

            if df.empty:
                logger.warning(f"No data returned for {yf_symbol}")
                return pd.DataFrame()

            df = df.reset_index()
            df = df.rename(columns={
                "Date": "timestamp",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            })

            logger.info(f"Fetched {len(df)} bars for {symbol} (TW Stock)")
            return self._standardize_df(df)

        except Exception as e:
            logger.error(f"Error fetching TW stock data for {symbol}: {e}")
            return pd.DataFrame()

    def get_current_price(self, symbol: str = "2330") -> float:
        """取得台股即時價格"""
        try:
            import yfinance as yf
            yf_symbol = f"{symbol}.TW" if not symbol.endswith(".TW") else symbol
            ticker = yf.Ticker(yf_symbol)
            info = ticker.fast_info
            return float(info.get("lastPrice", 0))
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return 0.0


def create_collector(market: str, **kwargs) -> BaseCollector:
    """
    工廠方法：根據市場類型建立對應的收集器

    Args:
        market: 市場類型 ("crypto", "us_stock", "tw_stock")

    Returns:
        BaseCollector 實例
    """
    collectors = {
        "crypto": CryptoCollector,
        "us_stock": StockCollector,
        "tw_stock": TwStockCollector,
    }

    collector_class = collectors.get(market)
    if collector_class is None:
        raise ValueError(f"Unknown market: {market}. Choose from: {list(collectors.keys())}")

    return collector_class(**kwargs)
