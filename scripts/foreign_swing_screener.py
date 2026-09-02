#!/usr/bin/env python3
"""
外資波段篩選器 (Foreign-Flow Swing Screener)
==============================================
尋找「像長榮(2603)一樣」的標的：股價有明顯波段（大幅擺盪），
且外資買賣超頻繁進出（不是單純長期持有、也不是完全沒有外資理會）。

名詞定義（給不熟悉的讀者）：
    波段          股價在數週到數月間出現的大幅擺盪（例如兩三個月內漲30%、又跌20%），
                  不同於當沖或每天的小幅震盪。
    外資          外國機構投資人（外國法人），與投信、自營商合稱「三大法人」。
    買賣超        買進張數減去賣出張數；> 0 稱「買超」，< 0 稱「賣超」。
    外資成交比重  當日外資買進+賣出張數，佔當日該股總成交量的比例；
                  比重越高，代表股價越容易被外資的動作牽動。
    翻轉率        外資「買超日→賣超日」或「賣超日→買超日」的切換頻率；
                  翻轉率越高，代表外資是頻繁進出做波段，而非單純長期持有不動。
    ZigZag        一種抓「有意義轉折」的方法：股價從高點回落（或從低點反彈）
                  超過設定門檻（預設 8%）才算確認一次擺動，藉此濾掉雜訊小波動。

方法：
    1. 回溯最近 N 個交易日（預設 60，約 3 個月），逐日向 TWSE 抓取全市場：
         - 三大法人買賣超（T86）             → 外資買進/賣出/買賣超（張）
         - 收盤價／最高／最低／成交量／成交金額（MI_INDEX）
       （這兩支 API 是 src/data/chip_collector.py 與
        src/scanner/post_market_scanner.py 既有在用的資料源，本腳本直接復用）
    2. 對每檔通過流動性門檻的個股計算：
         波段構面：avg_swing_pct（平均擺動幅度%）、swings_per_month（每月擺動次數）、
                   daily_range_pct（日均高低振幅%）
         外資構面：foreign_turnover_ratio（外資成交比重）、flip_rate（翻轉率）
    3. 各構面分別做百分位常態化評分（0~100），波段分數 50% + 外資分數 50% = 綜合分數，
       並標示 2603 長榮 作為對照基準列，方便判斷其他標的是否「像長榮」。

使用方式：
    python scripts/foreign_swing_screener.py                       # 全市場、近60交易日
    python scripts/foreign_swing_screener.py --days 90              # 拉長回顧期間
    python scripts/foreign_swing_screener.py --universe watchlist   # 只顯示觀察名單內的標的
    python scripts/foreign_swing_screener.py --top 20 --min-turnover 30000

⚠️ 執行需要能連線 www.twse.com.tw（全市場每日 2 個請求 × N 天，TWSE 限速
   3 req/5s，底層 collector 已內建節流；預設 60 天約需 1~2 分鐘）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.data.chip_collector import ChipCollector
from src.scanner.post_market_scanner import VolumeDataCollector

logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | {message}")

_WATCHLISTS_PATH = ROOT / "data" / "watchlists.json"
_REFERENCE_STOCK = "2603"  # 長榮：本篩選器的對照基準
_STOCK_CODE_RE = re.compile(r"^[1-9]\d{3}$")  # 排除 ETF / 權證 / 非個股代碼


# ══════════════════════════════════════════════════════════════════
# ZigZag 波段擺動偵測（純函式，不依賴網路，方便單獨測試）
# ══════════════════════════════════════════════════════════════════

def detect_swings(closes: list[float], threshold_pct: float = 8.0) -> list[float]:
    """
    簡易 ZigZag 演算法：從一個轉折點起算，價格反向回撤超過 threshold_pct%
    才確認一次擺動，回傳每次確認擺動的漲跌幅絕對值（%）清單。
    """
    if len(closes) < 2:
        return []

    pivots = [closes[0]]
    extreme = closes[0]
    trend: Optional[int] = None  # None=方向未定, 1=上升段, -1=下降段

    for price in closes[1:]:
        if trend is None:
            change = (price - closes[0]) / closes[0] * 100
            if abs(change) >= threshold_pct:
                trend = 1 if change > 0 else -1
                extreme = price
            continue

        if trend == 1:
            if price > extreme:
                extreme = price
            elif price <= extreme * (1 - threshold_pct / 100):
                pivots.append(extreme)
                trend, extreme = -1, price
        else:
            if price < extreme:
                extreme = price
            elif price >= extreme * (1 + threshold_pct / 100):
                pivots.append(extreme)
                trend, extreme = 1, price

    pivots.append(extreme if trend is not None else closes[-1])
    return [
        abs((pivots[i + 1] - pivots[i]) / pivots[i] * 100)
        for i in range(len(pivots) - 1)
    ]


def _load_watchlist_tickers() -> set[str]:
    try:
        data = json.loads(_WATCHLISTS_PATH.read_text(encoding="utf-8"))
        return {s["ticker"] for s in data.get("tw_stock", {}).get("symbols", [])}
    except Exception as e:
        logger.warning(f"讀取觀察名單失敗：{e}")
        return set()


# ══════════════════════════════════════════════════════════════════
# 篩選器主體
# ══════════════════════════════════════════════════════════════════

class ForeignSwingScreener:
    """
    回溯抓取全市場「收盤價/成交量」與「三大法人買賣超」歷史，
    計算波段擺動與外資頻繁進出程度，篩出「長榮型」標的。
    """

    def __init__(self):
        self.chip = ChipCollector()
        self.vol = VolumeDataCollector()

    # ── 資料收集 ────────────────────────────────────────────────

    def collect_history(
        self,
        days: int = 60,
        max_calendar_lookback: int = 150,
    ) -> pd.DataFrame:
        """
        回溯 calendar 天數，逐日抓取全市場資料，直到收集滿 `days` 個有效交易日
        （或觸及 max_calendar_lookback 上限為止，避免遇到長假無限往前找）。

        Returns:
            長格式 DataFrame，每列 = 某股票在某交易日的價量 + 法人資料
        """
        records = []
        collected = 0
        checked = 0
        d = date.today()

        while collected < days and checked < max_calendar_lookback:
            checked += 1
            if d.weekday() < 5:  # 只嘗試平日
                date_str = d.strftime("%Y-%m-%d")
                df_price = self.vol.fetch_daily_all(date_str)
                if not df_price.empty:
                    df_chip = self.chip.fetch_institutional_today(date_str)
                    chip_cols = [
                        c for c in ("foreign_buy", "foreign_sell", "foreign_net")
                        if c in df_chip.columns
                    ]
                    if chip_cols:
                        merged = df_price.join(df_chip[chip_cols], how="left")
                        merged["trade_date"] = date_str
                        records.append(merged.reset_index())
                        collected += 1
                        logger.info(f"  ✓ {date_str} 收集完成（{collected}/{days}）")
                    else:
                        logger.debug(f"  ↷ {date_str} 三大法人資料缺欄位，略過")
            d -= timedelta(days=1)

        if not records:
            logger.warning("未能取得任何交易日資料（請確認可連線 TWSE）")
            return pd.DataFrame()

        hist = pd.concat(records, ignore_index=True)
        logger.info(f"📊 共收集 {collected} 個交易日、{hist['stock_id'].nunique()} 檔股票")
        return hist

    # ── 特徵計算 ────────────────────────────────────────────────

    def compute_metrics(
        self,
        hist: pd.DataFrame,
        min_avg_turnover_wan: float = 20_000,
        swing_threshold_pct: float = 8.0,
        min_trading_days: int = 15,
    ) -> pd.DataFrame:
        """對每檔股票計算波段與外資特徵，回傳一列一檔股票的彙總表"""
        if hist.empty:
            return pd.DataFrame()

        rows = []
        for stock_id, g in hist.groupby("stock_id"):
            stock_id = str(stock_id).strip()
            if not _STOCK_CODE_RE.match(stock_id):
                continue

            g = g.sort_values("trade_date")
            if len(g) < min_trading_days:
                continue

            turnover_wan = (g["turnover"].mean() / 10_000) if "turnover" in g.columns else 0
            if turnover_wan < min_avg_turnover_wan:
                continue

            closes = g["close"].dropna().tolist()
            if len(closes) < min_trading_days:
                continue

            # ── 波段構面 ──
            swings = detect_swings(closes, threshold_pct=swing_threshold_pct)
            n_days = len(g)
            swings_per_month = (len(swings) / n_days * 21) if n_days else 0.0
            avg_swing_pct = (sum(swings) / len(swings)) if swings else 0.0

            if {"high", "low", "close"}.issubset(g.columns):
                daily_range_pct = ((g["high"] - g["low"]) / g["close"] * 100).mean()
            else:
                daily_range_pct = 0.0

            # ── 外資構面 ──
            # 翻轉率與外資成交比重共用同一組「相對成交量」基準，避免極小的雜訊買賣
            # （例如 30,000 張成交量中的 10 張買賣差）被誤判成一次有意義的方向翻轉。
            if {"foreign_net", "foreign_buy", "foreign_sell", "volume"}.issubset(g.columns):
                vol = g["volume"].replace(0, pd.NA)
                fn = g["foreign_net"]
                fb = g["foreign_buy"].fillna(0)
                fs = g["foreign_sell"].fillna(0)

                ratio_series = ((fb + fs) / vol).dropna()
                foreign_turnover_ratio = float(ratio_series.mean()) if len(ratio_series) else 0.0

                valid = fn.notna() & vol.notna()
                materiality = (fn.abs() / vol)
                # 淨買賣超需達當日成交量 0.5% 以上，才算「有感」的一天
                sign = pd.Series(0, index=g.index)
                sign[valid & (fn > 0) & (materiality >= 0.005)] = 1
                sign[valid & (fn < 0) & (materiality >= 0.005)] = -1
                sign = sign[valid]

                active_days = int((sign != 0).sum())
                flips = int((sign.diff().abs() == 2).sum())
                flip_rate = (flips / active_days) if active_days else 0.0
                foreign_net_mean = float(fn.dropna().mean()) if fn.notna().any() else 0.0
            else:
                flip_rate = 0.0
                foreign_turnover_ratio = 0.0
                foreign_net_mean = 0.0

            rows.append({
                "stock_id": stock_id,
                "stock_name": str(g["stock_name"].iloc[-1]) if "stock_name" in g.columns else "",
                "trading_days": n_days,
                "avg_turnover_wan": round(turnover_wan, 0),
                "last_close": closes[-1],
                "avg_swing_pct": round(avg_swing_pct, 1),
                "swings_per_month": round(swings_per_month, 2),
                "daily_range_pct": round(daily_range_pct, 2),
                "flip_rate": round(flip_rate, 3),
                "foreign_turnover_ratio": round(foreign_turnover_ratio, 3),
                "foreign_net_mean_qian": round(foreign_net_mean, 0),
            })

        return pd.DataFrame(rows)

    # ── 評分 ────────────────────────────────────────────────────

    @staticmethod
    def _normalize(s: pd.Series) -> pd.Series:
        """以百分位排名常態化到 0~100（比 min-max 更不受極端值干擾）"""
        if s.nunique() <= 1:
            return pd.Series(50.0, index=s.index)
        return s.rank(pct=True) * 100

    def score(self, metrics: pd.DataFrame) -> pd.DataFrame:
        """加入波段分數 / 外資分數 / 綜合分數，並依綜合分數排序"""
        if metrics.empty:
            return metrics

        df = metrics.copy()
        df["swing_score"] = (
            self._normalize(df["avg_swing_pct"]) * 0.5
            + self._normalize(df["daily_range_pct"]) * 0.3
            + self._normalize(df["swings_per_month"]) * 0.2
        )
        df["foreign_score"] = (
            self._normalize(df["foreign_turnover_ratio"]) * 0.6
            + self._normalize(df["flip_rate"]) * 0.4
        )
        df["composite"] = df["swing_score"] * 0.5 + df["foreign_score"] * 0.5
        return df.sort_values("composite", ascending=False).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════
# 報告輸出
# ══════════════════════════════════════════════════════════════════

def _display_frame(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    show = df.head(top_n).copy()
    show["外資比重%"] = (show["foreign_turnover_ratio"] * 100).round(1)
    show["翻轉率%"] = (show["flip_rate"] * 100).round(0)
    return show.rename(columns={
        "stock_id": "代號", "stock_name": "名稱", "last_close": "收盤",
        "avg_swing_pct": "平均擺動%", "swings_per_month": "月擺動次",
        "daily_range_pct": "日均振幅%", "swing_score": "波段分",
        "foreign_score": "外資分", "composite": "綜合分",
    })[[
        "代號", "名稱", "收盤", "平均擺動%", "月擺動次", "日均振幅%",
        "外資比重%", "翻轉率%", "波段分", "外資分", "綜合分",
    ]]


def print_report(df: pd.DataFrame, top_n: int, days: int) -> None:
    if df.empty:
        print("\n⚠️  沒有符合條件的標的，請調降 --min-turnover 或加長 --days 再試一次。\n")
        return

    print(f"\n{'='*90}")
    print(f"  外資波段篩選報告　(近 {days} 個交易日｜共 {len(df)} 檔通過流動性門檻)")
    print(f"{'='*90}")

    ref_idx = df.index[df["stock_id"] == _REFERENCE_STOCK]
    if len(ref_idx):
        r = df.loc[ref_idx[0]]
        rank = ref_idx[0] + 1
        print(
            f"  📌 對照基準 [2603 長榮] 排名 #{rank}/{len(df)}｜"
            f"平均擺動 {r['avg_swing_pct']:.1f}%｜每月擺動 {r['swings_per_month']:.1f} 次｜"
            f"外資成交比重 {r['foreign_turnover_ratio']*100:.1f}%｜"
            f"翻轉率 {r['flip_rate']*100:.0f}%｜綜合分 {r['composite']:.1f}"
        )
    else:
        print("  📌 對照基準 [2603 長榮]：本次未通過流動性門檻或無資料")

    print(f"{'-'*90}")
    print(_display_frame(df, top_n).to_string(index=False))
    print(f"{'='*90}\n")


def save_report(df: pd.DataFrame) -> Optional[Path]:
    if df.empty:
        return None
    out_dir = ROOT / "data" / "scan_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"foreign_swing_screen_{date.today().strftime('%Y-%m-%d')}.csv"
    df.to_csv(path, encoding="utf-8-sig", index=False)
    logger.info(f"📄 完整報告已儲存：{path}")
    return path


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="外資波段篩選器 — 找出像長榮(2603)一樣「波段大 + 外資頻繁進出」的台股標的",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--days", type=int, default=60, help="回顧交易日數（預設60，約3個月）")
    parser.add_argument(
        "--universe", choices=["all", "watchlist"], default="all",
        help="all=全市場（預設）；watchlist=只顯示 data/watchlists.json 觀察名單內的標的"
             "（資料仍為全市場回溯，僅篩選顯示範圍，速度相同）",
    )
    parser.add_argument(
        "--min-turnover", type=float, default=20_000, dest="min_turnover",
        help="最低平均每日成交金額（萬元，預設20000＝約2億，過濾流動性不足的標的）",
    )
    parser.add_argument(
        "--swing-threshold", type=float, default=8.0, dest="swing_threshold",
        help="ZigZag 擺動確認門檻（百分比，預設8）",
    )
    parser.add_argument("--top", type=int, default=30, help="顯示前 N 名（預設30）")
    parser.add_argument("--no-save", action="store_true", help="不儲存 CSV 報告")

    args = parser.parse_args()

    screener = ForeignSwingScreener()
    hist = screener.collect_history(days=args.days)
    if hist.empty:
        return

    if args.universe == "watchlist":
        tickers = _load_watchlist_tickers()
        tickers.add(_REFERENCE_STOCK)
        hist = hist[hist["stock_id"].astype(str).isin(tickers)]
        logger.info(f"🔎 限定觀察名單顯示範圍：{len(tickers)} 檔")

    metrics = screener.compute_metrics(
        hist,
        min_avg_turnover_wan=args.min_turnover,
        swing_threshold_pct=args.swing_threshold,
    )
    scored = screener.score(metrics)

    print_report(scored, top_n=args.top, days=args.days)
    if not args.no_save:
        save_report(scored)


if __name__ == "__main__":
    main()
