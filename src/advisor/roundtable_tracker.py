"""
圓桌推薦追蹤器 (Roundtable Pick Tracker)

把圓桌會議首席策略師的「精選標的」記錄下來，並於數日後回驗，
逐步累積「圓桌在哪種市場環境下準」的數據——讓圓桌接上回饋迴圈。

設計重點：
  - 沿用 signal_tracker.py 的 20 欄 CSV 格式，直接相容 ContextualOptimizer
  - 寫入獨立檔 data/roundtable_history.csv，與掃描器的 signal_history.csv 隔離
  - 沿用 Munger Filter（無市場背景不記錄）與 4 小時去重
  - 圓桌為日線級別：verify_picks 取 N 日後收盤，回填 pnl
    （注意：為讓既有 ContextualOptimizer 採計，N 日報酬同時寫入 pnl_1h_pct 欄）

用法：
    from src.advisor.roundtable_tracker import RoundtableTracker

    tracker = RoundtableTracker()
    tracker.record_picks(advisor.last_picks,
                         ctx_map={"台股": tw_ctx, "虛擬幣": crypto_ctx},
                         price_map={"2330": 1085.0, "NVDA": 131.2})
    tracker.verify_picks(days_after=3)

    # 圓桌專屬環境績效分析（複用既有優化器）
    from src.analysis.contextual_optimizer import ContextualOptimizer
    bias = ContextualOptimizer(
        history_file="data/roundtable_history.csv",
        bias_file="data/roundtable_bias.json",
        market_type="all",
    ).analyze_and_update()
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Optional

from loguru import logger

from src.monitor.signal_tracker import HEADERS


DEFAULT_HISTORY = Path(__file__).parent.parent.parent / "data" / "roundtable_history.csv"

# 市場 → CSV symbol 後綴（讓 ContextualOptimizer 能正確分類 stock/crypto）
_MARKET_SUFFIX = {"台股": ".TW", "美股": ".US"}


def _extract_ctx_tags(ctx) -> dict:
    """從 market context 物件容錯抽出 5 個 ctx_ 標籤"""
    if ctx is None:
        return {}
    phase = getattr(ctx, "phase", "") or getattr(ctx, "taiex_phase", "")
    return {
        "ctx_phase":     phase,
        "ctx_season":    getattr(ctx, "season", "") or "NA",
        "ctx_mtf_score": str(getattr(ctx, "mtf_score", "")),
        "ctx_fg_trend":  getattr(ctx, "fg_3d_trend", "") or "NA",
        "ctx_dxy_trend": getattr(ctx, "dxy_trend", "") or "NA",
    }


def _to_csv_symbol(market: str, asset_id: str) -> str:
    """組出 ContextualOptimizer 可分類的 symbol"""
    if market == "虛擬幣":
        return f"{asset_id}/USDT"
    return f"{asset_id}{_MARKET_SUFFIX.get(market, '')}"


class RoundtableTracker:
    """追蹤圓桌推薦的後續績效"""

    def __init__(self, history_file: str = None):
        self.history_file = Path(history_file) if history_file else DEFAULT_HISTORY
        self._ensure_file()

    def _ensure_file(self):
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.history_file.exists():
            with open(self.history_file, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(HEADERS)
            logger.info(f"📝 建立圓桌歷史檔：{self.history_file}")

    def _has_recent(self, symbol: str, signal: str, hours: int = 4) -> bool:
        cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
        with open(self.history_file, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("symbol") == symbol and row.get("signal") == signal:
                    try:
                        ts = dt.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                        if ts >= cutoff:
                            return True
                    except (ValueError, KeyError):
                        continue
        return False

    # ── 記錄 ────────────────────────────────────────────────────

    def record_picks(
        self,
        picks: list,
        ctx_map: dict = None,
        price_map: dict = None,
        actions: tuple = ("BUY", "WATCH"),
    ) -> int:
        """
        記錄圓桌精選標的。

        Args:
            picks     : list[Pick]（advisor.last_picks）
            ctx_map   : {"台股": tw_ctx, "美股": tw_ctx, "虛擬幣": crypto_ctx}
            price_map : {asset_id: price}，進場價（無則記 0，pnl 回驗時會跳過）
            actions   : 要記錄的動作（預設記 BUY/WATCH，不記 AVOID）
        Returns:
            實際寫入筆數
        """
        ctx_map   = ctx_map or {}
        price_map = price_map or {}
        recorded = skipped = 0

        for p in picks:
            action = getattr(p, "action", "")
            if action not in actions:
                continue

            market   = getattr(p, "market", "")
            asset_id = getattr(p, "asset_id", "")
            symbol   = _to_csv_symbol(market, asset_id)
            ctx      = ctx_map.get(market)
            tags     = _extract_ctx_tags(ctx)

            # Munger Filter：無市場背景不記錄
            if not tags.get("ctx_phase"):
                logger.info(f"⏭️ 圓桌跳過 {symbol}（無市場背景，不記錄）")
                skipped += 1
                continue

            # 去重
            if self._has_recent(symbol, action, hours=4):
                logger.debug(f"⏭️ 圓桌去重 {symbol} {action}")
                skipped += 1
                continue

            price = float(price_map.get(asset_id, 0) or 0)
            row = [
                dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                symbol, action, f"{price:.6f}", "",
                str(getattr(p, "confidence", 50)),       # confidence
                "",                                       # rsi（圓桌不填）
                "50.0",                                   # sentiment 佔位
                tags.get("ctx_phase", ""), tags.get("ctx_season", ""),
                tags.get("ctx_mtf_score", ""), tags.get("ctx_fg_trend", ""),
                tags.get("ctx_dxy_trend", ""),
                "", "", "",                               # price_after_*
                "", "", "",                               # pnl_*
                "false",                                  # verified
            ]
            with open(self.history_file, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)
            recorded += 1
            logger.info(f"📝 圓桌記錄 {symbol} {action} (信心 {getattr(p,'confidence',50)}) "
                        f"[ctx: {tags['ctx_phase']}/{tags['ctx_season']}]")

        if recorded or skipped:
            logger.info(f"📝 圓桌推薦：記錄 {recorded} 筆，跳過 {skipped} 筆")
        return recorded

    # ── 回驗 ────────────────────────────────────────────────────

    def verify_picks(self, days_after: int = 3) -> int:
        """
        回驗 N 日前、尚未驗證且有進場價的推薦，回填後續報酬。

        為讓既有 ContextualOptimizer 採計（它讀 pnl_1h_pct），
        N 日報酬同時寫入 pnl_1h_pct 與 pnl_24h_pct 欄。

        Returns:
            完成驗證的筆數
        """
        if not self.history_file.exists():
            return 0

        with open(self.history_file, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        cutoff = dt.datetime.now() - dt.timedelta(days=days_after)
        verified = 0

        for row in rows:
            if row.get("verified") == "true":
                continue
            try:
                ts = dt.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            if ts > cutoff:
                continue  # 還沒到回驗時間

            entry = float(row.get("price_at_signal", 0) or 0)
            if entry <= 0:
                continue  # 無進場價，無法計算報酬

            after = self._fetch_price_after(row["symbol"], ts, days_after)
            if after is None or after <= 0:
                continue

            pnl = (after - entry) / entry
            # SELL 信號方向相反（圓桌目前只記 BUY/WATCH，保留擴充性）
            if row.get("signal") == "SELL":
                pnl = -pnl

            row["price_after_24h"] = f"{after:.6f}"
            row["pnl_24h_pct"] = f"{pnl:.6f}"
            row["pnl_1h_pct"]  = f"{pnl:.6f}"   # 供 ContextualOptimizer 採計
            row["verified"] = "true"
            verified += 1

        if verified:
            with open(self.history_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerows(rows)
            logger.info(f"✅ 圓桌回驗完成：{verified} 筆")
        return verified

    @staticmethod
    def _fetch_price_after(symbol: str, signal_time: dt.datetime, days_after: int) -> Optional[float]:
        """取得 signal_time 之後約 days_after 日的收盤價（股票用 yfinance、幣用 ccxt）"""
        try:
            if "/" in symbol:        # 虛擬幣 e.g. BTC/USDT
                return RoundtableTracker._fetch_crypto_price(symbol)
            return RoundtableTracker._fetch_stock_price(symbol, signal_time, days_after)
        except Exception as e:
            logger.debug(f"  回驗取價失敗 {symbol}: {e}")
            return None

    @staticmethod
    def _fetch_stock_price(symbol: str, signal_time: dt.datetime, days_after: int) -> Optional[float]:
        import yfinance as yf
        # .TW 保留、.US 去掉（yfinance 美股不帶後綴）
        yf_symbol = symbol[:-3] if symbol.endswith(".US") else symbol
        start = (signal_time + dt.timedelta(days=days_after)).strftime("%Y-%m-%d")
        end   = (signal_time + dt.timedelta(days=days_after + 6)).strftime("%Y-%m-%d")
        hist = yf.Ticker(yf_symbol).history(start=start, end=end, auto_adjust=False)
        if hist.empty:
            return None
        return float(hist["Close"].iloc[0])

    @staticmethod
    def _fetch_crypto_price(symbol: str) -> Optional[float]:
        import ccxt
        ex = ccxt.binance()
        ticker = ex.fetch_ticker(symbol)
        return float(ticker.get("last") or 0) or None
