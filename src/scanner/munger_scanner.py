"""
芒格選股掃描器 (Munger Stock Scanner)

批次掃描台股和美股，找出通過芒格基本面篩選的優質標的。

芒格邏輯：
  先問「哪些股票一定不值得買」，剩下的才值得花時間研究技術面。

內建觀察清單：
  TW_WATCHLIST  — 台股藍籌股（依市值排序的前 20 大非金融股）
  US_WATCHLIST  — 美股護城河企業（巴菲特/芒格持有或認可的類型）

用法：
    from src.scanner.munger_scanner import MungerScanner

    scanner = MungerScanner()

    # 掃描台股
    result = scanner.scan_tw()
    print(result.report())

    # 掃描美股
    result = scanner.scan_us()
    print(result.report())

    # 自訂清單
    result = scanner.scan(["AAPL", "MSFT", "KO"], market="us_stock")
    print(result.report())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from loguru import logger

from src.strategy.fundamental_screener import FundamentalProfile, FundamentalScreener


# ──────────────────────────────────────────────────────────────
# 內建觀察清單
# ──────────────────────────────────────────────────────────────

# 台股：市值前 20 大非金融股（2024 年）
TW_WATCHLIST: list[str] = [
    "2330",  # 台積電
    "2317",  # 鴻海
    "2454",  # 聯發科
    "2308",  # 台達電
    "3711",  # 日月光投控
    "2303",  # 联電
    "2002",  # 中鋼
    "1303",  # 南亞塑膠
    "1301",  # 台塑
    "2412",  # 中華電信
    "2379",  # 瑞昱半導體
    "3008",  # 大立光
    "4938",  # 和碩
    "1216",  # 統一企業
    "2912",  # 統一超商
    "2207",  # 和泰車
    "2395",  # 研華
    "6505",  # 台塑化
    "2357",  # 華碩
    "2382",  # 廣達
]

# 美股：具護城河的優質企業（芒格式選股偏好）
US_WATCHLIST: list[str] = [
    "BRK-B",  # Berkshire Hathaway（芒格自己的公司）
    "KO",     # Coca-Cola（芒格/巴菲特最愛，護城河典範）
    "COST",   # Costco（芒格個人持股，直到去世）
    "AXP",    # American Express（長期持有）
    "MCO",    # Moody's（定價能力極強）
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "V",      # Visa
    "MA",     # Mastercard
    "JNJ",    # Johnson & Johnson
    "PG",     # Procter & Gamble
    "WMT",    # Walmart
    "MCD",    # McDonald's
    "NKE",    # Nike
    "GOOGL",  # Alphabet
    "AMZN",   # Amazon
    "LLY",    # Eli Lilly
    "ABBV",   # AbbVie
    "SBUX",   # Starbucks
    "NVDA",   # NVIDIA
]


# ──────────────────────────────────────────────────────────────
# 掃描結果
# ──────────────────────────────────────────────────────────────

@dataclass
class ScanResult:
    """掃描結果"""
    market: str
    total_scanned: int = 0
    passed: list[FundamentalProfile] = field(default_factory=list)
    failed: list[FundamentalProfile] = field(default_factory=list)
    too_hard: list[FundamentalProfile] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def pass_rate(self) -> float:
        if self.total_scanned == 0:
            return 0.0
        return len(self.passed) / self.total_scanned

    def report(self) -> str:
        """格式化報告"""
        lines = [
            "",
            "=" * 62,
            f"  芒格選股掃描報告 — {self.market}",
            "=" * 62,
            f"  掃描標的: {self.total_scanned} 支  |  "
            f"耗時: {self.elapsed_seconds:.1f}s",
            f"  通過: {len(self.passed)} 支  |  "
            f"不合格: {len(self.failed)} 支  |  "
            f"Too Hard: {len(self.too_hard)} 支",
            "=" * 62,
        ]

        # ── PASS 清單（依分數降序）──────────────────────────
        if self.passed:
            lines.append("\n  ✅  通過基本面篩選（可進行技術分析）")
            lines.append("  " + "─" * 58)
            lines.append(
                f"  {'代碼':<8}  {'公司':<22}  {'芒格分':<8}  "
                f"{'ROE':>6}  {'毛利率':>6}  {'P/E':>6}"
            )
            lines.append("  " + "─" * 58)
            for p in sorted(self.passed, key=lambda x: x.munger_score, reverse=True):
                lines.append(
                    f"  {p.symbol:<8}  {p.company_name[:22]:<22}  "
                    f"{p.munger_score:>5.0f}/100  "
                    f"{p.roe_avg*100:>5.1f}%  "
                    f"{p.gross_margin*100:>5.1f}%  "
                    f"{p.pe_ratio:>5.1f}x"
                )

        # ── FAIL 清單（摘要）────────────────────────────────
        if self.failed:
            lines.append(f"\n  ❌  不合格（{len(self.failed)} 支）")
            lines.append("  " + "─" * 58)
            for p in sorted(self.failed, key=lambda x: x.munger_score, reverse=True):
                reason = p.fail_reasons[0] if p.fail_reasons else "評分不足"
                lines.append(
                    f"  {p.symbol:<8}  {p.munger_score:>3.0f}/100  {reason}"
                )

        # ── TOO HARD 清單（摘要）────────────────────────────
        if self.too_hard:
            lines.append(f"\n  ⬜  Too Hard 筐（{len(self.too_hard)} 支，跳過）")
            for p in self.too_hard:
                reason = p.too_hard_reasons[0] if p.too_hard_reasons else "資料不足"
                lines.append(f"  {p.symbol:<8}  {reason}")

        lines += [
            "",
            "  芒格提醒：",
            "  「通過篩選」只是入場資格，不是買入信號。",
            "  接下來要問：護城河有多深？管理層激勵對齊了嗎？現在的價格合理嗎？",
            "=" * 62,
            "",
        ]

        return "\n".join(lines)

    def get_passed_symbols(self) -> list[str]:
        """取得通過篩選的股票代碼清單（可直接傳入 DecisionEngine）"""
        return [p.symbol for p in sorted(self.passed,
                                         key=lambda x: x.munger_score, reverse=True)]


# ──────────────────────────────────────────────────────────────
# 掃描器
# ──────────────────────────────────────────────────────────────

class MungerScanner:
    """
    芒格選股掃描器

    批次對多支股票執行基本面篩選，輸出通過/不通過/Too Hard 三類清單。
    """

    def __init__(self, cache_ttl_hours: float = 24.0, request_delay: float = 0.5):
        """
        Args:
            cache_ttl_hours: 基本面資料快取有效期（小時）
            request_delay:   每次 yfinance 請求之間的等待秒數，避免被限速
        """
        self.screener = FundamentalScreener(cache_ttl_hours=cache_ttl_hours)
        self.request_delay = request_delay

    def scan(self, symbols: list[str], market: str = "us_stock") -> ScanResult:
        """
        掃描任意股票清單。

        Args:
            symbols: 股票代碼清單
            market:  "us_stock" 或 "tw_stock"

        Returns:
            ScanResult 掃描結果
        """
        result = ScanResult(market=market, total_scanned=len(symbols))
        start = time.time()

        logger.info(f"[MungerScanner] 開始掃描 {len(symbols)} 支 {market} 標的...")

        for i, symbol in enumerate(symbols, 1):
            logger.info(f"[MungerScanner] ({i}/{len(symbols)}) 分析 {symbol}...")
            try:
                profile = self.screener.screen(symbol, market)

                if profile.verdict == "PASS":
                    result.passed.append(profile)
                elif profile.verdict == "TOO_HARD":
                    result.too_hard.append(profile)
                else:
                    result.failed.append(profile)

            except Exception as e:
                logger.error(f"[MungerScanner] {symbol} 分析失敗: {e}")
                profile = FundamentalProfile(symbol=symbol, market=market,
                                             verdict="TOO_HARD")
                profile.too_hard_reasons.append(f"分析異常: {e}")
                result.too_hard.append(profile)

            # 避免被 Yahoo Finance 限速
            if i < len(symbols):
                time.sleep(self.request_delay)

        result.elapsed_seconds = time.time() - start
        logger.info(
            f"[MungerScanner] 掃描完成：通過 {len(result.passed)}，"
            f"不合格 {len(result.failed)}，Too Hard {len(result.too_hard)}，"
            f"耗時 {result.elapsed_seconds:.1f}s"
        )
        return result

    def scan_tw(self, symbols: list[str] | None = None) -> ScanResult:
        """
        掃描台股。

        Args:
            symbols: 自訂清單，None 則使用內建台股觀察清單 TW_WATCHLIST
        """
        return self.scan(symbols or TW_WATCHLIST, market="tw_stock")

    def scan_us(self, symbols: list[str] | None = None) -> ScanResult:
        """
        掃描美股。

        Args:
            symbols: 自訂清單，None 則使用內建美股觀察清單 US_WATCHLIST
        """
        return self.scan(symbols or US_WATCHLIST, market="us_stock")

    def scan_both(
        self,
        tw_symbols: list[str] | None = None,
        us_symbols: list[str] | None = None,
    ) -> tuple[ScanResult, ScanResult]:
        """
        同時掃描台股和美股，回傳兩份結果。
        """
        tw_result = self.scan_tw(tw_symbols)
        us_result = self.scan_us(us_symbols)
        return tw_result, us_result

    def detailed_report(self, symbol: str, market: str = "us_stock") -> str:
        """
        輸出單一標的的完整基本面報告。

        Args:
            symbol: 股票代碼
            market: "us_stock" 或 "tw_stock"
        """
        profile = self.screener.screen(symbol, market)
        return profile.report()
