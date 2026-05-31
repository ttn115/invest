"""
美股盤後掃描器 (US Stock Post-Market Scanner)

收盤後自動執行複合條件篩選，整合：
  - 成交量異常（量比 > 閾值）
  - 價格動能（當日/5日漲幅）
  - 技術面確認（均線排列、RSI 區間）
  - 族群輪動（分類標籤）

資料來源：yfinance（免費，無需 API Key）
掃描範圍：約 130 支主力美股，涵蓋 AI/半導體/金融/醫療/消費等

評分系統（0~100）：
    爆量（量比 >= 3x）          ：+30 分
    放量（量比 >= 2x）          ：+20 分
    強勢漲幅（日線 >= 5%）      ：+20 分
    普通漲幅（日線 >= 3%）      ：+12 分
    多頭排列（MA20 + MA50）     ：+25 分
    站上 MA20                   ：+12 分
    RSI 健康區間（40~65）       ：+10 分
    ── 扣分 ──
    RSI 超買（>75）             ：-10 分
    爆量大跌（量比>=2x, <-3%）  ：-20 分
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from loguru import logger

# ─────────────────────────────────────────────
# 觀察名單（依族群分類）
# ─────────────────────────────────────────────

WATCHLIST: dict[str, list[str]] = {
    "AI / 大型科技": [
        "NVDA", "MSFT", "GOOGL", "META", "AAPL", "AMZN",
        "PLTR", "AI", "DELL", "SMCI",
    ],
    "半導體": [
        "AMD", "INTC", "QCOM", "AVGO", "MU", "AMAT",
        "LRCX", "KLAC", "MRVL", "ON", "TXN",
        "TSM", "ASML", "SOXL",
    ],
    "雲端 / 軟體": [
        "CRM", "SNOW", "DDOG", "NET", "ZS", "PANW",
        "ORCL", "SAP", "UBER", "DASH",
    ],
    "金融": [
        "JPM", "BAC", "GS", "MS", "WFC", "C",
        "V", "MA", "AXP", "BX", "KKR", "BRK-B",
    ],
    "醫療 / 生技": [
        "LLY", "UNH", "ABBV", "MRK", "JNJ", "PFE",
        "AMGN", "BMY", "GILD", "VRTX", "REGN",
    ],
    "消費 / 零售": [
        "TSLA", "COST", "WMT", "AMZN", "TGT",
        "HD", "NKE", "SBUX", "MCD", "BABA",
    ],
    "能源": [
        "XOM", "CVX", "COP", "SLB", "HAL",
    ],
    "工業 / 國防": [
        "CAT", "DE", "GE", "HON", "RTX", "LMT", "BA",
    ],
    "ETF / 大盤": [
        "SPY", "QQQ", "IWM", "TQQQ", "SOXS", "ARKK",
    ],
}

# 所有 ticker 扁平列表（去重）
ALL_TICKERS: list[str] = list(dict.fromkeys(
    t for tickers in WATCHLIST.values() for t in tickers
))

# ticker → 族群 映射
TICKER_SECTOR: dict[str, str] = {
    t: sector
    for sector, tickers in WATCHLIST.items()
    for t in tickers
}


# ═══════════════════════════════════════════════════════════════
# 資料模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class USCandidate:
    """
    美股候選個股

    Attributes:
        ticker       : 股票代號（e.g. "NVDA"）
        name         : 公司名稱
        sector       : 所屬族群
        close        : 收盤價（USD）
        change_pct   : 當日漲跌幅（%）
        change_5d    : 近5日漲跌幅（%）
        volume       : 今日成交量
        volume_avg20 : 20日均量
        volume_ratio : 量比
        rsi          : RSI(14)
        above_ma20   : 是否站上 MA20
        above_ma50   : 是否站上 MA50
        ma20         : MA20 價格
        ma50         : MA50 價格
        score        : 綜合評分（0~100）
        signals      : 觸發信號
        risk_flags   : 風險警示
    """
    ticker:       str
    name:         str   = ""
    sector:       str   = ""
    close:        float = 0.0
    change_pct:   float = 0.0
    change_5d:    float = 0.0
    volume:       int   = 0
    volume_avg20: float = 0.0
    volume_ratio: float = 0.0
    rsi:          float = 50.0
    above_ma20:   bool  = False
    above_ma50:   bool  = False
    ma20:         float = 0.0
    ma50:         float = 0.0
    score:        int   = 0
    signals:      list  = field(default_factory=list)
    risk_flags:   list  = field(default_factory=list)

    def summary(self) -> str:
        direction = "▲" if self.change_pct >= 0 else "▼"
        ma_tag = ""
        if self.above_ma20 and self.above_ma50:
            ma_tag = "MA多排"
        elif self.above_ma20:
            ma_tag = "MA20上"
        return (
            f"[{self.ticker:6s}] {self.name[:18]:18s} "
            f"${self.close:>10,.2f} {direction}{abs(self.change_pct):.1f}%  "
            f"量比:{self.volume_ratio:.1f}x  RSI:{self.rsi:.0f}  "
            f"{ma_tag:6s}  評分:{self.score}"
        )


@dataclass
class USScanResult:
    """美股掃描結果"""
    scan_date:    str
    scan_time:    str
    total_tickers: int
    candidates:   list[USCandidate] = field(default_factory=list)

    @property
    def top(self) -> list[USCandidate]:
        return sorted(self.candidates, key=lambda x: x.score, reverse=True)

    def to_dataframe(self) -> pd.DataFrame:
        if not self.candidates:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "代號":       c.ticker,
                "名稱":       c.name,
                "族群":       c.sector,
                "收盤($)":    round(c.close, 2),
                "日漲跌(%)":  round(c.change_pct, 2),
                "5日漲跌(%)": round(c.change_5d, 2),
                "成交量":     c.volume,
                "量比":       round(c.volume_ratio, 2),
                "RSI":        round(c.rsi, 1),
                "MA20上":     c.above_ma20,
                "MA50上":     c.above_ma50,
                "MA20":       round(c.ma20, 2),
                "MA50":       round(c.ma50, 2),
                "綜合評分":   c.score,
                "信號":       " | ".join(c.signals),
                "風險警示":   " | ".join(c.risk_flags),
            }
            for c in self.top
        ])


# ═══════════════════════════════════════════════════════════════
# 評分引擎
# ═══════════════════════════════════════════════════════════════

class USScoreEngine:
    """美股多維度評分系統"""

    WEIGHTS = {
        "volume_3x":       30,   # 量比 >= 3x
        "volume_2x":       20,   # 量比 >= 2x
        "change_5pct":     20,   # 日漲 >= 5%
        "change_3pct":     12,   # 日漲 >= 3%
        "ma_full":         25,   # MA20 + MA50 多排
        "ma_20only":       12,   # 只站上 MA20
        "rsi_sweet":       10,   # RSI 40~65
        "rsi_overbought": -10,   # RSI > 75
        "dump_on_volume": -20,   # 爆量跌 >3%
    }

    def score(self, c: USCandidate) -> USCandidate:
        total = 0
        signals: list[str] = []
        risks: list[str] = []

        # ── 量能 ──────────────────────────────────────────────────
        if c.volume_ratio >= 3.0:
            total += self.WEIGHTS["volume_3x"]
            signals.append(f"爆量 {c.volume_ratio:.1f}x")
        elif c.volume_ratio >= 2.0:
            total += self.WEIGHTS["volume_2x"]
            signals.append(f"放量 {c.volume_ratio:.1f}x")

        # ── 價格動能 ──────────────────────────────────────────────
        if c.change_pct >= 5.0:
            total += self.WEIGHTS["change_5pct"]
            signals.append(f"強勢 +{c.change_pct:.1f}%")
        elif c.change_pct >= 3.0:
            total += self.WEIGHTS["change_3pct"]
            signals.append(f"漲幅 +{c.change_pct:.1f}%")

        # ── 均線排列 ──────────────────────────────────────────────
        if c.above_ma20 and c.above_ma50:
            total += self.WEIGHTS["ma_full"]
            signals.append("多頭排列(MA20+MA50)")
        elif c.above_ma20:
            total += self.WEIGHTS["ma_20only"]
            signals.append("站上MA20")

        # ── RSI ───────────────────────────────────────────────────
        if 40 <= c.rsi <= 65:
            total += self.WEIGHTS["rsi_sweet"]
            signals.append(f"RSI健康 {c.rsi:.0f}")
        elif c.rsi > 75:
            total += self.WEIGHTS["rsi_overbought"]
            risks.append(f"RSI超買 {c.rsi:.0f}")

        # ── 爆量收黑（出貨警訊）──────────────────────────────────
        if c.volume_ratio >= 2.0 and c.change_pct <= -3.0:
            total += self.WEIGHTS["dump_on_volume"]
            risks.append(f"爆量跌 {c.change_pct:.1f}%（疑似出貨）")

        c.score = max(0, min(100, total))
        c.signals = signals
        c.risk_flags = risks
        return c


# ═══════════════════════════════════════════════════════════════
# 指標計算工具
# ═══════════════════════════════════════════════════════════════

def _calc_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def _calc_ma(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return 0.0
    return sum(closes[-period:]) / period


# ═══════════════════════════════════════════════════════════════
# 掃描引擎
# ═══════════════════════════════════════════════════════════════

class USScanner:
    """
    美股盤後掃描引擎

    Usage:
        scanner = USScanner()
        result  = scanner.scan(min_score=40)
        print_summary(result)
    """

    def __init__(self, tickers: Optional[list[str]] = None):
        self.tickers = tickers or ALL_TICKERS
        self.scorer  = USScoreEngine()

    # ── 主入口 ────────────────────────────────────────────────────

    def scan(
        self,
        min_score:        int   = 35,
        min_volume_ratio: float = 0.0,   # 不預設量比門檻，由評分決定
        days:             int   = 90,    # 抓歷史天數（供 MA50 + RSI，至少 90 曆天≈63 交易日）
        top_n:            int   = 50,
    ) -> USScanResult:
        """
        執行美股盤後掃描

        Args:
            min_score        : 最低綜合評分
            min_volume_ratio : 最低量比門檻
            days             : 歷史資料天數
            top_n            : 最多回傳幾支
        """
        now_utc = datetime.now(timezone.utc)
        scan_date = now_utc.strftime("%Y-%m-%d")
        scan_time = now_utc.strftime("%H:%M UTC")

        logger.info(f"🔍 美股掃描啟動：{scan_date}  共 {len(self.tickers)} 支")

        # Step 1: 批量抓取歷史資料
        df_all = self._fetch_batch(self.tickers, days=days)
        if df_all is None or df_all.empty:
            logger.error("yfinance 批量下載失敗")
            return USScanResult(scan_date=scan_date, scan_time=scan_time, total_tickers=0)

        # Step 2: 逐支計算指標 + 評分
        candidates: list[USCandidate] = []
        for ticker in self.tickers:
            try:
                c = self._build_candidate(ticker, df_all)
                if c is None:
                    continue
                c = self.scorer.score(c)
                if c.score >= min_score and c.volume_ratio >= min_volume_ratio:
                    candidates.append(c)
            except Exception as e:
                logger.debug(f"[{ticker}] 計算失敗: {e}")

        candidates.sort(key=lambda x: x.score, reverse=True)
        candidates = candidates[:top_n]

        logger.info(f"✅ 美股掃描完成：{len(candidates)} 支候選（評分>={min_score}）")
        return USScanResult(
            scan_date=scan_date,
            scan_time=scan_time,
            total_tickers=len(self.tickers),
            candidates=candidates,
        )

    # ── 資料抓取 ──────────────────────────────────────────────────

    def _fetch_batch(self, tickers: list[str], days: int) -> Optional[pd.DataFrame]:
        """用 yfinance 批量下載歷史 OHLCV"""
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance 未安裝，執行: pip install yfinance")
            return None

        ticker_str = " ".join(tickers)
        end   = datetime.now()
        start = end - timedelta(days=days)
        # yfinance 的 end 為排他（exclusive），+1 天確保平日收盤後不漏掉當天 K 棒
        # （與 src/data/collector.py 的修法一致）
        end_exclusive = end + timedelta(days=1)
        logger.info(f"  批量下載 {len(tickers)} 支，{start.date()} ~ {end.date()}")

        try:
            raw = yf.download(
                ticker_str,
                start=start.strftime("%Y-%m-%d"),
                end=end_exclusive.strftime("%Y-%m-%d"),
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if raw.empty:
                logger.warning("yfinance 回傳空資料")
                return None
            logger.info(f"  下載成功：{raw.shape[0]} 個交易日")
            return raw
        except Exception as e:
            logger.error(f"yfinance 下載失敗: {e}")
            return None

    # ── 指標計算 ──────────────────────────────────────────────────

    def _build_candidate(
        self, ticker: str, df_all: pd.DataFrame
    ) -> Optional[USCandidate]:
        """從批量 DataFrame 中提取單一 ticker 的 OHLCV 並計算指標"""
        try:
            # yfinance 多 ticker 下載後為 MultiIndex columns: (field, ticker)
            if isinstance(df_all.columns, pd.MultiIndex):
                if ticker not in df_all.columns.get_level_values(1):
                    return None
                close_s  = df_all["Close"][ticker].dropna()
                volume_s = df_all["Volume"][ticker].dropna()
            else:
                # 單一 ticker 下載
                if "Close" not in df_all.columns:
                    return None
                close_s  = df_all["Close"].dropna()
                volume_s = df_all["Volume"].dropna()

            if len(close_s) < 10:
                return None

            closes  = close_s.tolist()
            volumes = volume_s.tolist()

            # 基本價格資訊
            close_now  = closes[-1]
            close_prev = closes[-2] if len(closes) >= 2 else close_now
            close_5d   = closes[-6] if len(closes) >= 6 else closes[0]

            change_pct = (close_now - close_prev) / close_prev * 100 if close_prev else 0
            change_5d  = (close_now - close_5d)   / close_5d   * 100 if close_5d   else 0

            # 成交量
            vol_today  = int(volumes[-1]) if volumes else 0
            vol_avg20  = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else (sum(volumes[:-1]) / max(len(volumes) - 1, 1))
            vol_ratio  = vol_today / vol_avg20 if vol_avg20 > 0 else 0.0

            # 技術指標
            rsi     = _calc_rsi(closes, 14)
            ma20    = _calc_ma(closes, 20)
            ma50    = _calc_ma(closes, 50)

            return USCandidate(
                ticker=ticker,
                name=ticker,                    # 用 ticker 代替，避免多一次 API 請求
                sector=TICKER_SECTOR.get(ticker, "其他"),
                close=round(close_now, 2),
                change_pct=round(change_pct, 2),
                change_5d=round(change_5d, 2),
                volume=vol_today,
                volume_avg20=round(vol_avg20, 0),
                volume_ratio=round(vol_ratio, 2),
                rsi=rsi,
                above_ma20=(ma20 > 0 and close_now > ma20),
                above_ma50=(ma50 > 0 and close_now > ma50),
                ma20=round(ma20, 2),
                ma50=round(ma50, 2),
            )

        except Exception as e:
            logger.debug(f"[{ticker}] _build_candidate 失敗: {e}")
            return None


# ═══════════════════════════════════════════════════════════════
# 輸出工具
# ═══════════════════════════════════════════════════════════════

def print_summary(result: USScanResult, top: int = 25) -> None:
    out = sys.stdout
    sep = "=" * 100
    out.write(f"\n{sep}\n")
    out.write(
        f"  [美股掃描]  {result.scan_date} {result.scan_time}  |  "
        f"掃描 {result.total_tickers} 支  |  候選: {len(result.candidates)} 支\n"
    )
    out.write(f"{sep}\n")
    out.write(
        f"  {'#':>3}  {'代號':7s}  {'族群':14s}  {'收盤($)':>10s}  "
        f"{'日漲跌':>7s}  {'5日':>7s}  {'量比':>5s}  {'RSI':>4s}  {'MA':5s}  {'評分':>4s}  信號\n"
    )
    out.write(f"  {'-'*96}\n")

    shown = result.top[:top]
    for i, c in enumerate(shown, 1):
        ma_tag = "MA50+" if (c.above_ma20 and c.above_ma50) else ("MA20 " if c.above_ma20 else "     ")
        sig = " | ".join(c.signals[:2])
        risk = f" ⚠️{c.risk_flags[0]}" if c.risk_flags else ""
        line = (
            f"  #{i:2d}  {c.ticker:7s}  {c.sector[:14]:14s}  "
            f"${c.close:>10,.2f}  "
            f"{c.change_pct:>+6.1f}%  "
            f"{c.change_5d:>+6.1f}%  "
            f"{c.volume_ratio:>4.1f}x  "
            f"{c.rsi:>4.0f}  "
            f"{ma_tag}  "
            f"{c.score:>4d}  "
            f"{sig}{risk}\n"
        )
        out.write(line)

    out.write(f"{sep}\n\n")
    out.flush()


def save_report(result: USScanResult, output_dir: str = "data/scan_reports") -> str:
    """儲存 CSV 報告"""
    from pathlib import Path
    df = result.to_dataframe()
    if df.empty:
        return ""
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    fpath = path / f"us_scan_{result.scan_date}.csv"
    df.to_csv(fpath, encoding="utf-8-sig", index=False)
    logger.info(f"美股報告已儲存：{fpath}（{len(df)} 支候選）")
    return str(fpath)


# ═══════════════════════════════════════════════════════════════
# 快速啟動
# ═══════════════════════════════════════════════════════════════

def run_us_scan(
    min_score:        int   = 35,
    min_volume_ratio: float = 0.0,
    top_n:            int   = 50,
    save_csv:         bool  = True,
) -> USScanResult:
    """
    一鍵執行美股掃描（推薦入口）

    Usage:
        from src.scanner.us_scanner import run_us_scan
        result = run_us_scan(min_score=40)
    """
    scanner = USScanner()
    result  = scanner.scan(min_score=min_score, min_volume_ratio=min_volume_ratio, top_n=top_n)
    print_summary(result)
    if save_csv and result.candidates:
        save_report(result)
    return result
