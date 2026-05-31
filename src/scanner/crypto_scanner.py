"""
虛擬幣掃描器 (Crypto Scanner)

透過 CCXT（Binance 公開 API，不需 key）即時掃描：
  - 量比異常（24h 成交量 vs 7日均量）
  - 價格動能（1h / 4h / 24h 漲跌幅）
  - 波動率（ATR-based）
  - RSI 動量
  - 市值排行過濾（只看前 200 大）

評分系統（0~100）：
    量比爆發（volume_ratio > 3x）  +25
    短期動能（1h 漲幅 > 2%）       +15
    中期趨勢（4h 漲幅 > 5%）       +20
    日線多頭（24h > 0 + 站MA20）   +20
    RSI 健康區間（45~70）          +10
    市值前50（流動性保障）          +10
    ── 扣分 ──
    RSI 超買（> 80）               -15
    短期暴跌後反彈（恐慌出逃）      -10
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from loguru import logger


# ─────────────────────────────────────────────
# 掃描目標：主流 + 潛力幣種
# ─────────────────────────────────────────────
DEFAULT_WATCHLIST = [
    # 大型主流
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    # 中型熱門
    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT",
    "MATIC/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "BCH/USDT",
    # AI / RWA 概念
    "FET/USDT", "RENDER/USDT", "WLD/USDT", "GRT/USDT", "INJ/USDT",
    # DeFi 龍頭
    "AAVE/USDT", "MKR/USDT", "CRV/USDT", "COMP/USDT",
    # Layer2
    "ARB/USDT", "OP/USDT", "STRK/USDT",
    # 其他熱門
    "SUI/USDT", "APT/USDT", "SEI/USDT", "TIA/USDT", "JUP/USDT",
    "NEAR/USDT", "FIL/USDT", "ICP/USDT", "SAND/USDT", "MANA/USDT",
]


# ═══════════════════════════════════════════════════════════════
# 資料模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class CryptoCandidate:
    """虛擬幣候選標的"""
    symbol:        str             # 交易對，如 BTC/USDT
    base:          str             # 基礎幣種，如 BTC
    price:         float = 0.0    # 當前價格（USDT）
    change_1h:     float = 0.0    # 1小時漲跌幅（%）
    change_4h:     float = 0.0    # 4小時漲跌幅（%）
    change_24h:    float = 0.0    # 24小時漲跌幅（%）
    volume_24h:    float = 0.0    # 24h 成交量（USDT）
    volume_ratio:  float = 0.0    # 量比（今日24h / 7日均量）
    rsi_1h:        float = 50.0   # 1h RSI
    rsi_4h:        float = 50.0   # 4h RSI
    above_ma20_1h: bool  = False  # 1h K線站上 MA20
    above_ma20_4h: bool  = False  # 4h K線站上 MA20
    market_cap_rank: int = 999    # 市值排行（估算）
    score:         int   = 0
    signals:       list  = field(default_factory=list)
    risk_flags:    list  = field(default_factory=list)

    def summary(self) -> str:
        vol_b = self.volume_24h / 1e9
        rank_tag = f"Top{self.market_cap_rank}" if self.market_cap_rank < 200 else ""
        signal_str = " | ".join(self.signals[:3]) if self.signals else ""
        risk_str = " ⚠️ " + " ".join(self.risk_flags) if self.risk_flags else ""
        return (
            f"[{self.base:<8s}] ${self.price:<12,.4f} "
            f"1h:{self.change_1h:+.1f}% 4h:{self.change_4h:+.1f}% 24h:{self.change_24h:+.1f}%  "
            f"Vol:{vol_b:.2f}B  量比:{self.volume_ratio:.1f}x  "
            f"RSI:{self.rsi_1h:.0f}  評分:{self.score:3d}  "
            f"{rank_tag}  {signal_str}{risk_str}"
        )


@dataclass
class CryptoScanResult:
    """虛擬幣掃描結果"""
    scanned_at:  str
    exchange:    str
    candidates:  list[CryptoCandidate] = field(default_factory=list)

    @property
    def top(self) -> list[CryptoCandidate]:
        return sorted(self.candidates, key=lambda x: x.score, reverse=True)

    def to_dataframe(self) -> pd.DataFrame:
        if not self.candidates:
            return pd.DataFrame()
        return pd.DataFrame([{
            "幣種":       c.base,
            "交易對":     c.symbol,
            "現價(USDT)": c.price,
            "1h漲跌(%)":  c.change_1h,
            "4h漲跌(%)":  c.change_4h,
            "24h漲跌(%)": c.change_24h,
            "24h量(億U)":  round(c.volume_24h / 1e8, 2),
            "量比":        c.volume_ratio,
            "RSI(1h)":    round(c.rsi_1h, 1),
            "RSI(4h)":    round(c.rsi_4h, 1),
            "站MA20(1h)": c.above_ma20_1h,
            "站MA20(4h)": c.above_ma20_4h,
            "評分":        c.score,
            "信號":        " | ".join(c.signals),
            "風險":        " | ".join(c.risk_flags),
        } for c in self.top])


# ═══════════════════════════════════════════════════════════════
# 評分引擎
# ═══════════════════════════════════════════════════════════════

class CryptoScoreEngine:
    """虛擬幣多維度評分"""

    def score(self, c: CryptoCandidate) -> CryptoCandidate:
        total = 0
        signals = []
        risks = []

        # ── 量比 ──────────────────────────────────────────────
        if c.volume_ratio >= 4.0:
            total += 25
            signals.append(f"爆量 {c.volume_ratio:.1f}x")
        elif c.volume_ratio >= 2.5:
            total += 18
            signals.append(f"放量 {c.volume_ratio:.1f}x")
        elif c.volume_ratio >= 1.5:
            total += 8

        # ── 短期動能（1h）────────────────────────────────────
        if c.change_1h >= 3.0:
            total += 15
            signals.append(f"1h急漲 +{c.change_1h:.1f}%")
        elif c.change_1h >= 1.5:
            total += 8
            signals.append(f"1h上漲 +{c.change_1h:.1f}%")
        elif c.change_1h <= -5.0:
            total -= 10
            risks.append(f"1h急跌 {c.change_1h:.1f}%")

        # ── 中期趨勢（4h）────────────────────────────────────
        if c.change_4h >= 8.0:
            total += 20
            signals.append(f"4h強漲 +{c.change_4h:.1f}%")
        elif c.change_4h >= 4.0:
            total += 12
            signals.append(f"4h上漲 +{c.change_4h:.1f}%")
        elif c.change_4h >= 1.5:
            total += 6

        # ── 日線方向 ──────────────────────────────────────────
        if c.change_24h > 0 and c.above_ma20_4h:
            total += 20
            signals.append("日線多頭(MA20上)")
        elif c.change_24h > 0:
            total += 10
        elif c.change_24h < -10:
            total -= 5
            risks.append(f"24h大跌 {c.change_24h:.1f}%")

        # ── RSI ───────────────────────────────────────────────
        if 45 <= c.rsi_1h <= 70:
            total += 10
            signals.append(f"RSI健康 {c.rsi_1h:.0f}")
        elif c.rsi_1h > 80:
            total -= 15
            risks.append(f"RSI超買 {c.rsi_1h:.0f}")
        elif c.rsi_1h < 30:
            risks.append(f"RSI超賣 {c.rsi_1h:.0f}")

        # ── 市值流動性 ────────────────────────────────────────
        if c.market_cap_rank <= 20:
            total += 10
        elif c.market_cap_rank <= 50:
            total += 7
        elif c.market_cap_rank <= 100:
            total += 4

        c.score = max(0, min(100, total))
        c.signals = signals
        c.risk_flags = risks
        return c


# ═══════════════════════════════════════════════════════════════
# 技術指標計算（輕量版，無需 pandas-ta）
# ═══════════════════════════════════════════════════════════════

def _calc_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains[-period:]) / period if gains else 0
    avg_loss = sum(losses[-period:]) / period if losses else 1e-9
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def _calc_ma(closes: list[float], period: int = 20) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0
    return sum(closes[-period:]) / period


# ═══════════════════════════════════════════════════════════════
# 主掃描器
# ═══════════════════════════════════════════════════════════════

class CryptoScanner:
    """
    虛擬幣掃描器

    Usage:
        scanner = CryptoScanner()                   # 使用 Binance 公開 API
        result  = scanner.scan()                     # 掃描預設幣種清單
        scanner.print_summary(result)
        scanner.save_report(result)

        # 自訂清單
        result = scanner.scan(
            symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            min_score=50,
        )
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        score_engine: Optional[CryptoScoreEngine] = None,
    ):
        import ccxt
        self.exchange_id = exchange_id
        self.scorer = score_engine or CryptoScoreEngine()

        # 使用公開 API（不需要 key）
        exchange_class = getattr(ccxt, exchange_id)
        self.exchange = exchange_class({
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        logger.info(f"CryptoScanner 初始化：{exchange_id}")

    # ── 主入口 ──────────────────────────────────────────────────

    def scan(
        self,
        symbols:   Optional[list[str]] = None,
        min_score: int   = 35,
        top_n:     int   = 30,
    ) -> CryptoScanResult:
        """
        執行虛擬幣掃描

        Args:
            symbols   : 要掃描的交易對清單（None 使用預設 DEFAULT_WATCHLIST）
            min_score : 最低評分
            top_n     : 最多回傳幾個
        Returns:
            CryptoScanResult
        """
        symbols = symbols or DEFAULT_WATCHLIST
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        logger.info(f"虛擬幣掃描啟動：{len(symbols)} 個交易對")

        # Step 1: 取得所有 ticker（單次請求）
        ticker_map = self._fetch_all_tickers(symbols)

        # Step 2: 逐一抓 K 線計算指標
        candidates = []
        for i, symbol in enumerate(symbols):
            try:
                c = self._build_candidate(symbol, ticker_map)
                if c is None:
                    continue
                c = self._enrich_with_klines(c)
                c = self.scorer.score(c)
                if c.score >= min_score:
                    candidates.append(c)
                logger.debug(f"  [{i+1}/{len(symbols)}] {symbol} 評分={c.score}")
                time.sleep(0.15)   # 限速
            except Exception as e:
                logger.warning(f"  {symbol} 處理失敗: {e}")
                continue

        candidates.sort(key=lambda x: x.score, reverse=True)
        result = CryptoScanResult(
            scanned_at=now,
            exchange=self.exchange_id,
            candidates=candidates[:top_n],
        )
        logger.info(f"掃描完成：{len(candidates)} 個候選（評分>={min_score}）")
        return result

    # ── 資料收集 ────────────────────────────────────────────────

    def _fetch_all_tickers(self, symbols: list[str]) -> dict:
        """一次性抓所有 ticker（Binance 支援批量）"""
        try:
            all_tickers = self.exchange.fetch_tickers(symbols)
            logger.info(f"Ticker 批量抓取成功：{len(all_tickers)} 個")
            return all_tickers
        except Exception as e:
            logger.warning(f"批量 ticker 失敗，改用單個：{e}")
            result = {}
            for sym in symbols:
                try:
                    result[sym] = self.exchange.fetch_ticker(sym)
                    time.sleep(0.1)
                except Exception:
                    pass
            return result

    def _build_candidate(
        self, symbol: str, ticker_map: dict
    ) -> Optional[CryptoCandidate]:
        """從 ticker 建立基礎候選物件"""
        ticker = ticker_map.get(symbol)
        if ticker is None:
            return None

        base = symbol.split("/")[0]
        price = float(ticker.get("last") or ticker.get("close") or 0)
        if price <= 0:
            return None

        return CryptoCandidate(
            symbol=symbol,
            base=base,
            price=price,
            change_24h=round(float(ticker.get("percentage") or 0), 2),
            volume_24h=float(ticker.get("quoteVolume") or 0),   # USDT 計價
            market_cap_rank=self._estimate_rank(symbol),
        )

    def _enrich_with_klines(self, c: CryptoCandidate) -> CryptoCandidate:
        """抓 1h / 4h K 線計算動能、RSI、均量"""
        # ── 1h K 線（取 50 根）────────────────────────────────
        try:
            ohlcv_1h = self.exchange.fetch_ohlcv(c.symbol, "1h", limit=50)
            if ohlcv_1h and len(ohlcv_1h) >= 10:
                closes_1h = [row[4] for row in ohlcv_1h]
                vols_1h   = [row[5] for row in ohlcv_1h]

                # 1h 漲跌幅
                if len(closes_1h) >= 2:
                    c.change_1h = round(
                        (closes_1h[-1] - closes_1h[-2]) / closes_1h[-2] * 100, 2
                    )
                # 量比：今日最新1根量 vs 過去 7*24 根均量（取最多 48 根作近似）
                recent_vols = vols_1h[:-1]
                if recent_vols:
                    avg_vol = sum(recent_vols[-48:]) / len(recent_vols[-48:])
                    c.volume_ratio = round(vols_1h[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
                # RSI
                c.rsi_1h = _calc_rsi(closes_1h)
                # MA20
                ma20 = _calc_ma(closes_1h, 20)
                c.above_ma20_1h = closes_1h[-1] > ma20
        except Exception as e:
            logger.debug(f"[{c.symbol}] 1h K線失敗: {e}")

        # ── 4h K 線（取 50 根）────────────────────────────────
        try:
            ohlcv_4h = self.exchange.fetch_ohlcv(c.symbol, "4h", limit=50)
            if ohlcv_4h and len(ohlcv_4h) >= 6:
                closes_4h = [row[4] for row in ohlcv_4h]
                # 4h 漲跌幅（最近兩根）
                if len(closes_4h) >= 2:
                    c.change_4h = round(
                        (closes_4h[-1] - closes_4h[-2]) / closes_4h[-2] * 100, 2
                    )
                c.rsi_4h = _calc_rsi(closes_4h)
                ma20_4h = _calc_ma(closes_4h, 20)
                c.above_ma20_4h = closes_4h[-1] > ma20_4h
        except Exception as e:
            logger.debug(f"[{c.symbol}] 4h K線失敗: {e}")

        return c

    @staticmethod
    def _estimate_rank(symbol: str) -> int:
        """根據已知市值排行估算（靜態表，定期更新）"""
        rank_table = {
            "BTC/USDT": 1,   "ETH/USDT": 2,   "BNB/USDT": 5,
            "SOL/USDT": 6,   "XRP/USDT": 4,   "DOGE/USDT": 8,
            "ADA/USDT": 9,   "AVAX/USDT": 12, "DOT/USDT": 14,
            "LINK/USDT": 15, "MATIC/USDT": 18, "UNI/USDT": 20,
            "ATOM/USDT": 22, "LTC/USDT": 19,  "BCH/USDT": 17,
            "FET/USDT": 35,  "RENDER/USDT": 42, "WLD/USDT": 55,
            "GRT/USDT": 48,  "INJ/USDT": 38,  "AAVE/USDT": 30,
            "MKR/USDT": 28,  "ARB/USDT": 32,  "OP/USDT": 45,
            "SUI/USDT": 25,  "APT/USDT": 27,  "NEAR/USDT": 33,
            "FIL/USDT": 40,  "ICP/USDT": 36,  "JUP/USDT": 60,
        }
        return rank_table.get(symbol, 150)

    # ── 輸出 ────────────────────────────────────────────────────

    def print_summary(self, result: CryptoScanResult, top: int = 20) -> None:
        """列印掃描摘要"""
        import sys
        out = sys.stdout
        sep = "=" * 90
        out.write(f"\n{sep}\n")
        out.write(
            f"  [虛擬幣掃描]  {result.scanned_at}  |  "
            f"交易所: {result.exchange.upper()}  |  "
            f"候選: {len(result.candidates)} 個\n"
        )
        out.write(f"{sep}\n")
        out.write(
            f"  {'幣種':<8} {'現價':>12}  {'1h':>6} {'4h':>6} {'24h':>6}  "
            f"{'量(億U)':>8}  {'量比':>5}  {'RSI':>5}  {'評分':>4}  信號\n"
        )
        out.write(f"  {'-'*86}\n")
        for i, c in enumerate(result.top[:top], 1):
            vol_b = c.volume_24h / 1e8
            signals_str = " | ".join(c.signals[:2])
            risk_str = "  ⚠️ " + c.risk_flags[0] if c.risk_flags else ""
            out.write(
                f"  #{i:2d} {c.base:<7} ${c.price:<12,.4f}"
                f" {c.change_1h:+5.1f}% {c.change_4h:+5.1f}% {c.change_24h:+5.1f}%"
                f"  {vol_b:8.2f}  {c.volume_ratio:5.1f}x"
                f"  {c.rsi_1h:5.1f}  {c.score:4d}"
                f"  {signals_str}{risk_str}\n"
            )
        out.write(f"{sep}\n\n")
        out.flush()

    def save_report(
        self,
        result: CryptoScanResult,
        path: Optional[str] = None,
    ) -> str:
        """儲存為 CSV"""
        from pathlib import Path
        from datetime import date
        if path is None:
            p = Path("data/scan_reports")
            p.mkdir(parents=True, exist_ok=True)
            path = str(p / f"crypto_scan_{date.today()}.csv")
        df = result.to_dataframe()
        df.to_csv(path, encoding="utf-8-sig", index=False)
        logger.info(f"虛擬幣報告已儲存：{path}（{len(df)} 個候選）")
        return path


# ═══════════════════════════════════════════════════════════════
# 快速啟動
# ═══════════════════════════════════════════════════════════════

def run_crypto_scan(
    symbols:     Optional[list[str]] = None,
    min_score:   int  = 35,
    exchange_id: str  = "binance",
    save_csv:    bool = True,
) -> CryptoScanResult:
    """
    一鍵執行虛擬幣掃描

    Usage:
        from src.scanner.crypto_scanner import run_crypto_scan
        result = run_crypto_scan()
        for c in result.top[:10]:
            print(c.summary())
    """
    scanner = CryptoScanner(exchange_id=exchange_id)
    result  = scanner.scan(symbols=symbols, min_score=min_score)
    scanner.print_summary(result)
    if save_csv:
        scanner.save_report(result)
    return result
