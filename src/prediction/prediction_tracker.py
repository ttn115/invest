"""
預測記錄 + 回驗 (Prediction Tracker)

把 Predictor 產生的預測寫入本地快取 data/prediction_history.csv（方便計算，
非雲端 DB）；N 日後回頭向線上 API（yfinance / ccxt）查實際價格做驗證。

沿用 signal_tracker 的 Munger Filter（無市場背景不記錄）+ 4 小時去重慣例。
取價直接複用 RoundtableTracker 的 yfinance/ccxt 取價函式。
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Optional

from loguru import logger

from src.advisor.roundtable_tracker import RoundtableTracker

DEFAULT_HISTORY = Path(__file__).parent.parent.parent / "data" / "prediction_history.csv"

PRED_HEADERS = [
    "timestamp", "market", "symbol", "asset_name", "source",
    "score", "raw_conf", "cal_conf",
    "forecast_direction", "forecast_magnitude_pct", "horizon_days", "entry_price",
    "features_json",
    "ctx_phase", "ctx_season", "ctx_fg_trend",
    # 回驗欄位
    "price_after", "actual_return_pct", "actual_direction",
    "direction_hit", "magnitude_error_pct", "pnl_1h_pct", "verified",
]

# 市場 → CSV symbol 後綴（取價時辨識 stock/crypto）
_SUFFIX = {"tw_stock": ".TW", "us_stock": ".US"}


def _extract_ctx_tags(ctx) -> dict:
    if ctx is None:
        return {}
    phase = getattr(ctx, "phase", "") or getattr(ctx, "taiex_phase", "")
    return {
        "ctx_phase":    phase,
        "ctx_season":   getattr(ctx, "season", "") or "NA",
        "ctx_fg_trend": getattr(ctx, "fg_3d_trend", "") or "NA",
    }


def _to_csv_symbol(market: str, symbol: str) -> str:
    if market == "crypto":
        return f"{symbol}/USDT" if "/" not in symbol else symbol
    return f"{symbol}{_SUFFIX.get(market, '')}"


class PredictionTracker:
    """預測記錄與回驗"""

    def __init__(self, history_file: str = None):
        self.history_file = Path(history_file) if history_file else DEFAULT_HISTORY
        self._ensure_file()

    def _ensure_file(self):
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.history_file.exists():
            with open(self.history_file, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(PRED_HEADERS)
            logger.info(f"📝 建立預測歷史檔：{self.history_file}")

    def _has_recent(self, csv_symbol: str, hours: int = 4) -> bool:
        cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
        with open(self.history_file, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("symbol") == csv_symbol:
                    try:
                        ts = dt.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                        if ts >= cutoff:
                            return True
                    except (ValueError, KeyError):
                        continue
        return False

    # ── 記錄 ────────────────────────────────────────────────────

    def record_predictions(self, forecasts: list, ctx_map: dict = None) -> int:
        """
        記錄預測清單。

        Args:
            forecasts : list[Forecast]
            ctx_map   : {"tw_stock": tw_ctx, "crypto": crypto_ctx}
        Returns:
            實際寫入筆數
        """
        ctx_map = ctx_map or {}
        recorded = skipped = 0

        for fc in forecasts:
            csv_symbol = _to_csv_symbol(fc.market, fc.symbol)
            tags = _extract_ctx_tags(ctx_map.get(fc.market))

            # Munger Filter：無市場背景不記錄
            if not tags.get("ctx_phase"):
                logger.info(f"⏭️ 預測跳過 {csv_symbol}（無市場背景，不記錄）")
                skipped += 1
                continue
            # 去重
            if self._has_recent(csv_symbol, hours=4):
                logger.debug(f"⏭️ 預測去重 {csv_symbol}")
                skipped += 1
                continue

            row = [
                dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                fc.market, csv_symbol, fc.asset_name, fc.source,
                fc.score, f"{fc.raw_confidence:.1f}", f"{fc.cal_confidence:.1f}",
                fc.direction, f"{fc.expected_return_pct:.3f}", fc.horizon_days,
                f"{fc.entry_price:.6f}",
                json.dumps(fc.features, ensure_ascii=False),
                tags.get("ctx_phase", ""), tags.get("ctx_season", ""), tags.get("ctx_fg_trend", ""),
                "", "", "", "", "", "", "false",
            ]
            with open(self.history_file, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)
            recorded += 1
            logger.info(f"📝 預測記錄 {csv_symbol} {fc.direction} {fc.expected_return_pct:+.1f}% "
                        f"(信心 {fc.cal_confidence:.0f}) [ctx: {tags['ctx_phase']}]")

        if recorded or skipped:
            logger.info(f"📝 預測：記錄 {recorded} 筆，跳過 {skipped} 筆")
        return recorded

    # ── 回驗 ────────────────────────────────────────────────────

    def verify_due(self) -> int:
        """
        回驗已到期（經過 horizon_days）且未驗證的預測，回填實際結果。

        Returns:
            完成驗證的筆數
        """
        if not self.history_file.exists():
            return 0
        with open(self.history_file, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        now = dt.datetime.now()
        verified = 0

        for row in rows:
            if row.get("verified") == "true":
                continue
            try:
                ts = dt.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                horizon = int(row.get("horizon_days", 5) or 5)
            except (ValueError, KeyError):
                continue
            if (now - ts).days < horizon:
                continue  # 尚未到期

            entry = float(row.get("entry_price", 0) or 0)
            if entry <= 0:
                continue

            after = self._fetch_after(row["symbol"], ts, horizon)
            if after is None or after <= 0:
                continue

            actual_ret = (after - entry) / entry * 100  # %
            actual_dir = "UP" if actual_ret > 0.5 else ("DOWN" if actual_ret < -0.5 else "FLAT")
            fc_dir = row.get("forecast_direction", "")
            fc_mag = float(row.get("forecast_magnitude_pct", 0) or 0)

            row["price_after"] = f"{after:.6f}"
            row["actual_return_pct"] = f"{actual_ret:.3f}"
            row["actual_direction"] = actual_dir
            row["direction_hit"] = "true" if fc_dir == actual_dir else "false"
            row["magnitude_error_pct"] = f"{abs(fc_mag - actual_ret):.3f}"
            row["pnl_1h_pct"] = f"{actual_ret / 100:.6f}"  # 供 ContextualOptimizer 採計
            row["verified"] = "true"
            verified += 1

        if verified:
            with open(self.history_file, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=PRED_HEADERS)
                w.writeheader()
                w.writerows(rows)
            logger.info(f"✅ 預測回驗完成：{verified} 筆")
        return verified

    @staticmethod
    def _fetch_after(csv_symbol: str, signal_time: dt.datetime, horizon: int) -> Optional[float]:
        """複用 RoundtableTracker 的取價邏輯（線上 API 查 N 日後價）"""
        try:
            if "/" in csv_symbol:
                return RoundtableTracker._fetch_crypto_price(csv_symbol)
            return RoundtableTracker._fetch_stock_price(csv_symbol, signal_time, horizon)
        except Exception as e:
            logger.debug(f"  預測回驗取價失敗 {csv_symbol}: {e}")
            return None
