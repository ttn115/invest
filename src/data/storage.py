"""
SQLite 資料儲存 (Data Storage)

負責管理本地資料庫：OHLCV 快取、交易紀錄、績效記錄。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger


class DataStorage:
    """
    SQLite 資料庫管理器

    Tables:
        - ohlcv: K線資料快取
        - trades: 交易紀錄
        - performance: 績效快照
    """

    def __init__(self, db_path: str | Path | None = None):
        """
        Args:
            db_path: 資料庫檔案路徑，預設為 data/trader.db
        """
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "trader.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        logger.info(f"DataStorage initialized: {self.db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        """取得資料庫連線"""
        return sqlite3.connect(str(self.db_path))

    def _init_db(self):
        """初始化資料庫表格"""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    UNIQUE(symbol, market, timeframe, timestamp)
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    total REAL NOT NULL,
                    commission REAL DEFAULT 0,
                    strategy TEXT,
                    signal_reason TEXT,
                    pnl REAL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    total_equity REAL NOT NULL,
                    cash REAL NOT NULL,
                    positions_value REAL NOT NULL,
                    daily_pnl REAL DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    sharpe_ratio REAL,
                    max_drawdown REAL,
                    win_rate REAL,
                    metadata TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol
                    ON ohlcv(symbol, market, timeframe);
                CREATE INDEX IF NOT EXISTS idx_trades_symbol
                    ON trades(symbol, market);
                CREATE INDEX IF NOT EXISTS idx_trades_timestamp
                    ON trades(timestamp);
            """)

    # === OHLCV 資料操作 ===

    def save_ohlcv(
        self, df: pd.DataFrame, symbol: str, market: str, timeframe: str
    ) -> int:
        """
        儲存 OHLCV 資料到資料庫

        Args:
            df: OHLCV DataFrame (index=timestamp)
            symbol: 交易對/股票代碼
            market: 市場類型
            timeframe: K線週期

        Returns:
            插入的筆數
        """
        if df.empty:
            return 0

        records = []
        for ts, row in df.iterrows():
            records.append((
                symbol, market, timeframe, str(ts),
                row["open"], row["high"], row["low"], row["close"], row["volume"],
            ))

        with self._get_conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO ohlcv
                   (symbol, market, timeframe, timestamp, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                records,
            )

        logger.debug(f"Saved {len(records)} OHLCV records for {symbol}")
        return len(records)

    def load_ohlcv(
        self,
        symbol: str,
        market: str,
        timeframe: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """從資料庫載入 OHLCV 資料"""
        query = """
            SELECT timestamp, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = ? AND market = ? AND timeframe = ?
        """
        params: list = [symbol, market, timeframe]

        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)

        query += " ORDER BY timestamp ASC"

        with self._get_conn() as conn:
            df = pd.read_sql_query(query, conn, params=params)

        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")

        return df

    # === 交易紀錄操作 ===

    def record_trade(
        self,
        symbol: str,
        market: str,
        side: str,
        quantity: float,
        price: float,
        strategy: str = "",
        signal_reason: str = "",
        commission: float = 0,
        pnl: float = 0,
    ) -> int:
        """記錄一筆交易"""
        total = quantity * price
        now = datetime.now().isoformat()

        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO trades
                   (timestamp, symbol, market, side, quantity, price, total,
                    commission, strategy, signal_reason, pnl)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, symbol, market, side, quantity, price, total,
                 commission, strategy, signal_reason, pnl),
            )
            trade_id = cursor.lastrowid

        logger.info(
            f"Trade #{trade_id}: {side} {quantity} {symbol} @ {price} "
            f"(total={total:.2f}, pnl={pnl:.2f})"
        )
        return trade_id

    def get_trades(
        self,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        """查詢交易紀錄"""
        query = "SELECT * FROM trades WHERE 1=1"
        params: list = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if market:
            query += " AND market = ?"
            params.append(market)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # === 績效操作 ===

    def save_performance(
        self,
        total_equity: float,
        cash: float,
        positions_value: float,
        daily_pnl: float = 0,
        total_pnl: float = 0,
        sharpe_ratio: Optional[float] = None,
        max_drawdown: Optional[float] = None,
        win_rate: Optional[float] = None,
        metadata: Optional[dict] = None,
    ):
        """記錄績效快照"""
        now = datetime.now().isoformat()
        meta_json = json.dumps(metadata) if metadata else None

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO performance
                   (timestamp, total_equity, cash, positions_value,
                    daily_pnl, total_pnl, sharpe_ratio, max_drawdown, win_rate, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, total_equity, cash, positions_value,
                 daily_pnl, total_pnl, sharpe_ratio, max_drawdown, win_rate, meta_json),
            )

    def get_performance_history(self, days: int = 30) -> pd.DataFrame:
        """取得績效歷史"""
        query = """
            SELECT * FROM performance
            ORDER BY timestamp DESC
            LIMIT ?
        """
        with self._get_conn() as conn:
            return pd.read_sql_query(query, conn, params=[days])

    # === 匯出 ===

    def export_trades_csv(self, filepath: str | Path) -> str:
        """匯出交易紀錄為 CSV"""
        df = self.get_trades(limit=10000)
        filepath = Path(filepath)
        df.to_csv(filepath, index=False)
        logger.info(f"Exported {len(df)} trades to {filepath}")
        return str(filepath)
