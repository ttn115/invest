"""
芒格基本面篩選器 (Munger Fundamental Screener)

查理·芒格核心原則：「以合理價格買入優秀企業」——先確認企業優秀，再看技術面。

篩選評分（0–100 分）：
  ROE     30 分：股東資本報酬率，衡量企業能否持續為股東創造價值
  FCF     25 分：自由現金流，確認盈利品質——利潤必須是真實現金，不是會計數字
  負債比  20 分：財務安全性，芒格厭惡高槓桿
  毛利率  15 分：定價能力代理指標，護城河的訊號
  估值    10 分：P/E 合理性

判決：
  PASS     (≥60 分)：優秀企業，允許進入技術分析層
  TOO_HARD (-)     ：資料不足或超出能力圈，進入 Too Hard 筐，跳過
  FAIL     (<60 分)：不符標準，禁止買入，技術信號無效

用法一：作為 DecisionEngine 的 Veto 濾網（推薦）
    screener = FundamentalScreener()
    engine.add_filter("munger", screener)

用法二：獨立基本面分析
    screener = FundamentalScreener()
    profile = screener.screen("AAPL")
    print(profile.report())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from loguru import logger

from .base import BaseStrategy, Signal, SignalType


# ──────────────────────────────────────────────────────────────
# 資料結構
# ──────────────────────────────────────────────────────────────

@dataclass
class FundamentalProfile:
    """基本面分析結果"""
    symbol: str
    company_name: str = ""
    sector: str = ""
    market: str = "us_stock"

    # 判決
    verdict: str = "TOO_HARD"       # PASS / TOO_HARD / FAIL
    munger_score: float = 0.0       # 0 ~ 100

    # 核心指標
    roe_ttm: float = 0.0
    roe_avg: float = 0.0            # 近幾年均值（若有歷史資料）
    fcf_ttm: float = 0.0            # 近 12 月自由現金流（元）
    fcf_positive_years: int = 0     # FCF 正值年數
    fcf_total_years: int = 0        # FCF 有效資料年數
    debt_to_equity: float = -1.0    # -1 = 無資料
    gross_margin: float = 0.0
    operating_margin: float = 0.0
    pe_ratio: float = 0.0
    peg_ratio: float = 0.0          # 新增
    forward_pe: float = 0.0         # 新增

    # 評估說明
    pass_reasons: list[str] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)
    too_hard_reasons: list[str] = field(default_factory=list)

    def report(self) -> str:
        verdict_icon = {"PASS": "✅", "FAIL": "❌", "TOO_HARD": "⬜"}.get(self.verdict, "?")
        filled = int(self.munger_score / 10)
        score_bar = "█" * filled + "░" * (10 - filled)

        fcf_bn = self.fcf_ttm / 1e9 if self.fcf_ttm != 0 else 0.0

        lines = [
            "─" * 58,
            f"  {verdict_icon}  {self.symbol}  {self.company_name}",
            f"     產業: {self.sector:<20s}  市場: {self.market}",
            f"     芒格分數: [{score_bar}] {self.munger_score:.0f} / 100",
            "─" * 58,
            f"     ROE:        {self.roe_avg * 100:>6.1f}%  (TTM: {self.roe_ttm * 100:.1f}%)",
            f"     FCF:        {self.fcf_positive_years}/{self.fcf_total_years} 年正值"
            f"  (TTM: {fcf_bn:+.2f} B)",
        ]

        if self.debt_to_equity >= 0:
            lines.append(f"     負債/淨值:  {self.debt_to_equity:>6.2f}x")
        else:
            lines.append("     負債/淨值:  無資料")

        lines += [
            f"     毛利率:     {self.gross_margin * 100:>6.1f}%",
        ]
        if self.pe_ratio > 0:
            lines.append(f"     P/E:        {self.pe_ratio:>6.1f}x (Forward: {self.forward_pe:>6.1f}x)")
        else:
            lines.append("     P/E:        N/A")
        if self.peg_ratio > 0:
            lines.append(f"     PEG:        {self.peg_ratio:>6.2f}")

        if self.pass_reasons:
            lines.append("")
            for r in self.pass_reasons:
                lines.append(f"     ✓ {r}")
        if self.fail_reasons:
            lines.append("")
            for r in self.fail_reasons:
                lines.append(f"     ✗ {r}")
        if self.too_hard_reasons:
            lines.append("")
            for r in self.too_hard_reasons:
                lines.append(f"     ? {r}")

        lines.append("─" * 58)
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 篩選器本體
# ──────────────────────────────────────────────────────────────

# Too Hard 的金融業 SIC 關鍵字（不同財報結構，標準指標失效）
_FINANCIAL_SECTORS = {"Financial Services", "Banking", "Insurance", "Banks—Regional",
                      "Banks—Diversified", "Insurance—Diversified", "Capital Markets"}

# 用來在 DataFrame 裡尋找財務數據的候選列名（yfinance 版本間名稱不一致）
_ROW_ALIASES: dict[str, list[str]] = {
    "net_income":    ["Net Income", "Net Income Common Stockholders", "NetIncome"],
    "equity":        ["Stockholders Equity", "Total Stockholder Equity",
                      "Common Stock Equity", "Total Equity Gross Minority Interest"],
    "op_cashflow":   ["Operating Cash Flow", "Total Cash From Operating Activities",
                      "Cash Flow From Continuing Operating Activities"],
    "capex":         ["Capital Expenditure", "Capital Expenditures",
                      "Purchase Of Property Plant And Equipment"],
    "free_cashflow": ["Free Cash Flow"],
    "gross_profit":  ["Gross Profit"],
    "total_revenue": ["Total Revenue", "Revenue"],
}


def _get_row(df: pd.DataFrame, key: str) -> Optional[pd.Series]:
    """從 DataFrame 中依候選列名取出一列；找不到回傳 None。"""
    if df is None or df.empty:
        return None
    for alias in _ROW_ALIASES.get(key, [key]):
        if alias in df.index:
            return df.loc[alias]
    return None


def _to_float(val) -> float:
    """安全地將任意值轉為 float，失敗回傳 0.0。"""
    try:
        v = float(val)
        return v if pd.notna(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


class FundamentalScreener(BaseStrategy):
    """
    芒格基本面篩選器

    實作 BaseStrategy 介面，可直接插入 DecisionEngine.add_filter()。
    產生信號意義：
      BUY  → 基本面 PASS，允許後續技術分析
      HOLD → TOO_HARD，跳過此標的
      SELL → FAIL，否決買入信號（Veto）
    """

    def __init__(self, cache_ttl_hours: float = 24.0):
        """
        Args:
            cache_ttl_hours: 基本面資料的快取有效期（小時），避免重複請求
        """
        super().__init__(name="MungerFundamental")
        self._cache: dict[str, tuple[float, FundamentalProfile]] = {}
        self._cache_ttl = cache_ttl_hours * 3600

    # ── 公開介面 ──────────────────────────────────────────────

    def screen(self, symbol: str, market: str = "us_stock") -> FundamentalProfile:
        """
        對單一標的執行完整基本面分析。

        Args:
            symbol: 股票代碼（美股: "AAPL"，台股: "2330" 或 "2330.TW"）
            market: "us_stock" 或 "tw_stock"

        Returns:
            FundamentalProfile 基本面分析結果
        """
        # 台股代碼標準化
        yf_symbol = self._normalize_symbol(symbol, market)

        # 嘗試從快取取得
        cached = self._from_cache(yf_symbol)
        if cached is not None:
            return cached

        profile = self._fetch_and_score(symbol, yf_symbol, market)
        self._to_cache(yf_symbol, profile)
        return profile

    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        """
        BaseStrategy 介面：作為 DecisionEngine Veto 濾網使用。
        df 僅用於讀取市場類型（meta 資訊），主要依賴 yfinance 基本面資料。
        """
        if not symbol:
            return Signal(signal_type=SignalType.HOLD, strategy_name=self.name,
                          reason="No symbol provided")

        # 從 df metadata 判斷市場（若無則預設 us_stock）
        market = getattr(df, "attrs", {}).get("market", "us_stock")
        if symbol.endswith(".TW") or (symbol.isdigit() and len(symbol) == 4):
            market = "tw_stock"

        profile = self.screen(symbol, market)

        if profile.verdict == "PASS":
            return Signal(
                signal_type=SignalType.BUY,
                strength=min(profile.munger_score / 100, 1.0),
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Munger PASS ({profile.munger_score:.0f}/100): "
                       + "; ".join(profile.pass_reasons[:2]),
            )
        elif profile.verdict == "TOO_HARD":
            return Signal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Too Hard: " + "; ".join(profile.too_hard_reasons[:2]),
            )
        else:  # FAIL
            return Signal(
                signal_type=SignalType.SELL,
                strength=1.0,
                symbol=symbol,
                strategy_name=self.name,
                reason=f"Munger FAIL ({profile.munger_score:.0f}/100): "
                       + "; ".join(profile.fail_reasons[:2]),
            )

    # ── 內部實作 ──────────────────────────────────────────────

    @staticmethod
    def _normalize_symbol(symbol: str, market: str) -> str:
        """將台股代碼補上 .TW 後綴。"""
        if market == "tw_stock" and not symbol.endswith(".TW"):
            return f"{symbol}.TW"
        return symbol

    def _from_cache(self, yf_symbol: str) -> Optional[FundamentalProfile]:
        if yf_symbol in self._cache:
            ts, profile = self._cache[yf_symbol]
            if time.time() - ts < self._cache_ttl:
                logger.debug(f"[FundamentalScreener] Cache hit: {yf_symbol}")
                return profile
        return None

    def _to_cache(self, yf_symbol: str, profile: FundamentalProfile) -> None:
        self._cache[yf_symbol] = (time.time(), profile)

    def _fetch_and_score(self, symbol: str, yf_symbol: str, market: str) -> FundamentalProfile:
        """取得 yfinance 資料並評分。"""
        profile = FundamentalProfile(symbol=symbol, market=market)

        try:
            import yfinance as yf
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info or {}
        except Exception as e:
            logger.warning(f"[FundamentalScreener] yfinance 初始化失敗 {yf_symbol}: {e}")
            profile.too_hard_reasons.append("無法連線取得資料")
            return profile

        # 基本資訊
        profile.company_name = info.get("longName", info.get("shortName", ""))
        profile.sector = info.get("sector", info.get("industryDisp", ""))

        # ── Too Hard 早期判斷 ──────────────────────────────
        if not info or not profile.company_name:
            profile.too_hard_reasons.append("無法取得公司資料（可能已下市或代碼有誤）")
            return profile

        if profile.sector in _FINANCIAL_SECTORS:
            profile.too_hard_reasons.append(
                f"金融業（{profile.sector}）：財報結構特殊，標準指標不適用"
            )
            return profile

        # ── 取得財務報表 ──────────────────────────────────
        try:
            income = ticker.financials       # 年報損益表
            cashflow = ticker.cashflow       # 年報現金流量表
            balance = ticker.balance_sheet   # 年報資產負債表
        except Exception as e:
            logger.warning(f"[FundamentalScreener] 財報取得失敗 {yf_symbol}: {e}")
            income = cashflow = balance = None

        if income is None or income.empty:
            profile.too_hard_reasons.append("無歷史財報資料（上市不足或資料源限制）")
            return profile

        # ── 評分 ──────────────────────────────────────────
        score = 0.0
        score += self._score_roe(info, income, balance, profile)
        score += self._score_fcf(info, cashflow, profile)
        score += self._score_debt(info, balance, profile)
        score += self._score_margins(info, income, profile)
        score += self._score_valuation(info, profile)

        profile.munger_score = round(score, 1)
        profile.verdict = "PASS" if score >= 60 else "FAIL"

        # Too Hard 覆蓋：極端負值資料
        if profile.roe_avg < -0.5:
            profile.verdict = "TOO_HARD"
            profile.too_hard_reasons.append("長期嚴重虧損，財務結構不穩定")

        return profile

    # ── 各項評分子函式 ────────────────────────────────────────

    def _score_roe(
        self,
        info: dict,
        income: pd.DataFrame,
        balance: Optional[pd.DataFrame],
        profile: FundamentalProfile,
    ) -> float:
        """ROE 評分（滿分 30）"""
        # TTM ROE（來自 info）
        roe_ttm = _to_float(info.get("returnOnEquity", 0))
        profile.roe_ttm = roe_ttm

        # 多年 ROE（來自年報）
        roe_list: list[float] = []
        ni_series = _get_row(income, "net_income")
        eq_series = _get_row(balance, "equity") if balance is not None else None

        if ni_series is not None and eq_series is not None:
            # 取共同年份（最多 4 年）
            common_cols = [c for c in ni_series.index if c in eq_series.index][:4]
            for col in common_cols:
                ni = _to_float(ni_series.get(col))
                eq = _to_float(eq_series.get(col))
                if eq > 0:
                    roe_list.append(ni / eq)

        if roe_list:
            profile.roe_avg = sum(roe_list) / len(roe_list)
        elif roe_ttm != 0:
            profile.roe_avg = roe_ttm
        else:
            profile.fail_reasons.append("ROE 資料不足")
            return 0.0

        roe = profile.roe_avg
        if roe >= 0.25:
            profile.pass_reasons.append(f"ROE 優秀 ({roe*100:.1f}%，≥25%)")
            return 30.0
        elif roe >= 0.20:
            profile.pass_reasons.append(f"ROE 良好 ({roe*100:.1f}%，≥20%)")
            return 25.0
        elif roe >= 0.15:
            profile.pass_reasons.append(f"ROE 達標 ({roe*100:.1f}%，≥15%)")
            return 20.0
        elif roe >= 0.10:
            profile.fail_reasons.append(f"ROE 偏低 ({roe*100:.1f}%，<15%)")
            return 10.0
        else:
            profile.fail_reasons.append(f"ROE 不足 ({roe*100:.1f}%，<10%)")
            return 0.0

    def _score_fcf(
        self,
        info: dict,
        cashflow: Optional[pd.DataFrame],
        profile: FundamentalProfile,
    ) -> float:
        """FCF 評分（滿分 25）"""
        fcf_ttm = _to_float(info.get("freeCashflow", 0))
        profile.fcf_ttm = fcf_ttm

        # 嘗試計算多年 FCF
        fcf_years: list[float] = []
        if cashflow is not None and not cashflow.empty:
            fcf_series = _get_row(cashflow, "free_cashflow")
            if fcf_series is None:
                # 手動計算：Operating CF - CapEx
                op_series = _get_row(cashflow, "op_cashflow")
                cx_series = _get_row(cashflow, "capex")
                if op_series is not None and cx_series is not None:
                    common = [c for c in op_series.index if c in cx_series.index][:4]
                    for col in common:
                        op = _to_float(op_series.get(col))
                        cx = _to_float(cx_series.get(col))
                        # yfinance capex 通常為負值
                        fcf_years.append(op + cx if cx < 0 else op - cx)
            else:
                fcf_years = [_to_float(v) for v in fcf_series.values[:4]
                             if pd.notna(v) and v != 0]

        profile.fcf_total_years = len(fcf_years)
        profile.fcf_positive_years = sum(1 for f in fcf_years if f > 0)

        # 評分
        has_ttm = fcf_ttm > 0
        hist_ratio = (profile.fcf_positive_years / profile.fcf_total_years
                      if profile.fcf_total_years > 0 else 0)

        if has_ttm and hist_ratio >= 0.75:
            profile.pass_reasons.append(
                f"FCF 品質優良 (TTM 正值，歷史 {profile.fcf_positive_years}/{profile.fcf_total_years} 年)"
            )
            return 25.0
        elif has_ttm and hist_ratio >= 0.5:
            profile.pass_reasons.append(f"FCF 尚可 (TTM 正值，歷史部分年份負值)")
            return 15.0
        elif has_ttm:
            profile.fail_reasons.append(f"FCF 歷史不穩定 (TTM 正值但歷史多負)")
            return 8.0
        else:
            profile.fail_reasons.append(f"FCF 為負 (TTM: {fcf_ttm/1e9:.2f}B)")
            return 0.0

    def _score_debt(
        self,
        info: dict,
        balance: Optional[pd.DataFrame],
        profile: FundamentalProfile,
    ) -> float:
        """負債比評分（滿分 20）"""
        de = _to_float(info.get("debtToEquity", -1))
        # yfinance 回傳的 debtToEquity 有時是百分比（如 150.0 表示 1.5x），需除以 100
        if de > 20:
            de = de / 100.0

        if de < 0:
            # 嘗試從資產負債表計算
            if balance is not None and not balance.empty:
                eq_series = _get_row(balance, "equity")
                if eq_series is not None and len(eq_series) > 0:
                    eq = _to_float(eq_series.iloc[0])
                    td = _to_float(info.get("totalDebt", 0))
                    if eq > 0 and td >= 0:
                        de = td / eq

        profile.debt_to_equity = de

        if de < 0:
            # 無資料，給中性分
            profile.too_hard_reasons.append("負債資料不足，無法評估財務槓桿")
            return 8.0  # 中性
        elif de <= 0.3:
            profile.pass_reasons.append(f"極低負債 (D/E={de:.2f}x)")
            return 20.0
        elif de <= 0.5:
            profile.pass_reasons.append(f"負債健康 (D/E={de:.2f}x)")
            return 15.0
        elif de <= 1.0:
            profile.fail_reasons.append(f"負債偏高 (D/E={de:.2f}x，>0.5x)")
            return 8.0
        else:
            profile.fail_reasons.append(f"高槓桿警示 (D/E={de:.2f}x，>1.0x)")
            return 0.0

    def _score_margins(
        self,
        info: dict,
        income: Optional[pd.DataFrame],
        profile: FundamentalProfile,
    ) -> float:
        """毛利率評分（滿分 15）"""
        gm = _to_float(info.get("grossMargins", 0))
        om = _to_float(info.get("operatingMargins", 0))

        # 若 info 無資料，嘗試從年報計算
        if gm == 0 and income is not None and not income.empty:
            gp_series = _get_row(income, "gross_profit")
            rev_series = _get_row(income, "total_revenue")
            if gp_series is not None and rev_series is not None and len(gp_series) > 0:
                gp = _to_float(gp_series.iloc[0])
                rev = _to_float(rev_series.iloc[0])
                if rev > 0:
                    gm = gp / rev

        profile.gross_margin = gm
        profile.operating_margin = om

        if gm >= 0.50:
            profile.pass_reasons.append(f"高毛利率 ({gm*100:.1f}%，≥50%，護城河訊號)")
            return 15.0
        elif gm >= 0.35:
            profile.pass_reasons.append(f"良好毛利率 ({gm*100:.1f}%，≥35%)")
            return 12.0
        elif gm >= 0.20:
            profile.fail_reasons.append(f"毛利率一般 ({gm*100:.1f}%，≥20%)")
            return 8.0
        elif gm >= 0.10:
            profile.fail_reasons.append(f"毛利率偏低 ({gm*100:.1f}%，<20%)")
            return 4.0
        else:
            profile.fail_reasons.append(f"毛利率過低 ({gm*100:.1f}%，<10%)")
            return 0.0

    def _score_valuation(self, info: dict, profile: FundamentalProfile) -> float:
        """估值評分（滿分 10）"""
        pe = _to_float(info.get("trailingPE", 0))
        f_pe = _to_float(info.get("forwardPE", 0))
        peg = _to_float(info.get("pegRatio", 0))
        
        if pe <= 0:
            pe = f_pe

        profile.pe_ratio = pe
        profile.forward_pe = f_pe
        profile.peg_ratio = peg

        if pe <= 0:
            profile.too_hard_reasons.append("P/E 為負或無資料（可能虧損中）")
            return 0.0
        elif pe <= 15:
            profile.pass_reasons.append(f"估值合理 (P/E={pe:.1f}x，≤15x)")
            return 10.0
        elif pe <= 20:
            profile.pass_reasons.append(f"估值尚可 (P/E={pe:.1f}x，≤20x)")
            return 7.0
        elif pe <= 30:
            profile.fail_reasons.append(f"估值偏高 (P/E={pe:.1f}x，>20x)")
            return 3.0
        else:
            profile.fail_reasons.append(f"估值過高 (P/E={pe:.1f}x，>30x)")
            return 0.0
