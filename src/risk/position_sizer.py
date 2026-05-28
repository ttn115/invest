"""
倉位計算模組 (Position Sizer)

基於凱利公式與投資組合集中度限制，
計算每筆交易的最佳倉位大小。

塔勒布原則：
    - 下行有限（最大單筆損失 = stop_loss_pct × 倉位）
    - 上行無限（讓獲利奔跑）
    - 絕不讓任何單一標的毀掉整個組合

芒格原則：
    - 集中投資少數高確信度機會，但不能集中到破產
    - 現金是一種倉位，不持倉也是決策
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# 資料模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class PositionSizeResult:
    """倉位計算結果"""
    symbol:           str
    total_capital:    float   # 總資金
    position_value:   float   # 建議倉位金額
    position_pct:     float   # 佔總資金百分比
    shares:           int     # 建議股數（張）
    max_loss:         float   # 最大預期損失金額
    stop_loss_price:  float   # 建議停損價
    kelly_fraction:   float   # 原始凱利分數（未縮放）
    method:           str     # 計算方法說明
    warning:          str     = ""  # 警示訊息

    def summary(self) -> str:
        return (
            f"[{self.symbol}] 建議倉位: {self.position_pct:.1f}%  "
            f"金額: ${self.position_value:,.0f}  "
            f"股數: {self.shares}張  "
            f"停損: ${self.stop_loss_price:.2f}  "
            f"最大損失: ${self.max_loss:,.0f}"
            + (f"\n⚠️  {self.warning}" if self.warning else "")
        )


# ═══════════════════════════════════════════════════════════════
# 凱利公式計算器
# ═══════════════════════════════════════════════════════════════

class KellyCriterion:
    """
    凱利公式倉位計算

    公式：f* = (p × b - q) / b
        p = 勝率
        q = 敗率 (1 - p)
        b = 平均獲利 / 平均損失（賠率）

    實際使用建議使用「半凱利」或「四分之一凱利」，
    因為原始凱利假設完美的統計預測，現實中常被高估。
    """

    @staticmethod
    def calculate(
        win_rate: float,
        avg_win:  float,
        avg_loss: float,
    ) -> float:
        """
        計算原始凱利分數

        Args:
            win_rate : 勝率（0~1）
            avg_win  : 平均獲利金額（正數）
            avg_loss : 平均虧損金額（正數）
        Returns:
            f* 凱利分數（建議押注比例，0~1）
        """
        if avg_loss <= 0 or avg_win <= 0:
            return 0.0
        if not 0 < win_rate < 1:
            return 0.0

        b = avg_win / avg_loss        # 賠率
        q = 1 - win_rate              # 敗率
        kelly = (win_rate * b - q) / b

        return max(0.0, min(1.0, kelly))


# ═══════════════════════════════════════════════════════════════
# 投資組合集中度控制器
# ═══════════════════════════════════════════════════════════════

class ConcentrationController:
    """
    投資組合集中度限制

    防止「把所有雞蛋放在同一個籃子」，但也防止過度分散。

    芒格說：「過度分散是愚蠢的」——但毀掉整個組合更愚蠢。
    塔勒布說：「用啞鈴策略：極端保守 + 少量非對稱押注。」
    """

    def __init__(
        self,
        max_single_pct:  float = 0.20,   # 單一個股最高 20%
        max_sector_pct:  float = 0.35,   # 單一產業最高 35%
        min_cash_pct:    float = 0.15,   # 最低現金水位 15%
        max_positions:   int   = 15,     # 最多持股數量
    ):
        """
        Args:
            max_single_pct : 單一個股最大佔比（預設 20%）
            max_sector_pct : 單一產業最大佔比（預設 35%）
            min_cash_pct   : 最低現金保留比例（預設 15%）
            max_positions  : 最多持股數量（預設 15 支）
        """
        self.max_single_pct = max_single_pct
        self.max_sector_pct = max_sector_pct
        self.min_cash_pct   = min_cash_pct
        self.max_positions  = max_positions

    def get_max_allocation(
        self,
        total_capital:       float,
        current_positions:   int   = 0,
        current_cash:        float = None,
        symbol_sector:       str   = "unknown",
        sector_current_pct:  float = 0.0,
    ) -> tuple[float, str]:
        """
        取得單一新倉位最大可用金額

        Args:
            total_capital      : 總資金
            current_positions  : 目前持股數
            current_cash       : 目前可用現金（None 表示不限）
            symbol_sector      : 新標的所屬產業
            sector_current_pct : 該產業目前佔比
        Returns:
            (最大金額, 警示訊息)
        """
        warnings = []

        # 持股數上限
        if current_positions >= self.max_positions:
            return 0.0, f"持股數已達上限 {self.max_positions} 支，請先減倉"

        # 單一個股上限
        max_by_single = total_capital * self.max_single_pct

        # 產業集中度限制
        remaining_sector_pct = max(0, self.max_sector_pct - sector_current_pct)
        max_by_sector = total_capital * remaining_sector_pct
        if sector_current_pct > self.max_sector_pct * 0.8:
            warnings.append(
                f"{symbol_sector} 產業已佔 {sector_current_pct:.0%}，接近上限"
            )

        # 現金水位限制
        if current_cash is not None:
            investable_cash = current_cash - total_capital * self.min_cash_pct
            max_by_cash = max(0, investable_cash)
            if max_by_cash < total_capital * 0.05:
                return 0.0, f"現金水位過低，保留現金 >= {self.min_cash_pct:.0%}"
        else:
            max_by_cash = max_by_single

        final_max = min(max_by_single, max_by_sector, max_by_cash)
        warning = " | ".join(warnings)
        return final_max, warning


# ═══════════════════════════════════════════════════════════════
# 主倉位計算器
# ═══════════════════════════════════════════════════════════════

class PositionSizer:
    """
    主倉位計算器

    整合凱利公式 + 集中度控制，輸出建議倉位。

    Usage:
        sizer = PositionSizer(total_capital=1_000_000)

        result = sizer.calculate(
            symbol="2330",
            current_price=850.0,
            stop_loss_price=800.0,    # 停損價
            win_rate=0.55,            # 策略歷史勝率
            avg_win_pct=0.08,         # 平均獲利 8%
            avg_loss_pct=0.04,        # 平均虧損 4%
            score=75,                 # 掃描評分（影響凱利縮放）
        )
        print(result.summary())
    """

    def __init__(
        self,
        total_capital:       float,
        kelly_fraction:      float = 0.25,   # 凱利縮放（0.25 = 四分之一凱利）
        concentration_ctrl:  Optional[ConcentrationController] = None,
    ):
        """
        Args:
            total_capital      : 總資金（元）
            kelly_fraction     : 凱利縮放係數（建議 0.25~0.5）
            concentration_ctrl : 集中度控制器（None 使用預設設定）
        """
        self.total_capital = total_capital
        self.kelly_fraction = kelly_fraction
        self.ctrl = concentration_ctrl or ConcentrationController()
        self.kelly_calc = KellyCriterion()

    def calculate(
        self,
        symbol:          str,
        current_price:   float,
        stop_loss_price: float,
        win_rate:        float  = 0.50,
        avg_win_pct:     float  = 0.08,
        avg_loss_pct:    float  = 0.04,
        score:           int    = 50,         # 掃描評分（50~100）
        lot_size:        int    = 1000,       # 台股 1張=1000股
        current_positions: int  = 0,
        current_cash:    Optional[float] = None,
        symbol_sector:   str    = "unknown",
        sector_pct:      float  = 0.0,
    ) -> PositionSizeResult:
        """
        計算建議倉位

        Args:
            symbol           : 股票代號
            current_price    : 目前價格
            stop_loss_price  : 停損價格（用於計算最大損失）
            win_rate         : 策略歷史勝率（0~1）
            avg_win_pct      : 平均獲利百分比
            avg_loss_pct     : 平均虧損百分比
            score            : 掃描評分（高分→允許更大倉位）
            lot_size         : 每張股數（台股 1000，美股 1）
            current_positions: 目前持股數
            current_cash     : 目前可用現金
            symbol_sector    : 所屬產業
            sector_pct       : 該產業目前佔比
        Returns:
            PositionSizeResult
        """
        if current_price <= 0 or stop_loss_price <= 0:
            logger.warning(f"[{symbol}] 無效價格，返回零倉位")
            return self._zero_result(symbol, "無效價格")

        # Step 1: 凱利公式計算基礎倉位
        raw_kelly = self.kelly_calc.calculate(win_rate, avg_win_pct, avg_loss_pct)

        # Step 2: 依評分調整凱利縮放（評分越高，允許稍大倉位）
        score_multiplier = 0.5 + (score / 100) * 0.5    # score 50→0.75x, score 100→1.0x
        adjusted_kelly = raw_kelly * self.kelly_fraction * score_multiplier

        kelly_position = self.total_capital * adjusted_kelly

        # Step 3: 集中度上限
        max_by_concentration, conc_warning = self.ctrl.get_max_allocation(
            total_capital=self.total_capital,
            current_positions=current_positions,
            current_cash=current_cash,
            symbol_sector=symbol_sector,
            sector_current_pct=sector_pct,
        )

        if max_by_concentration == 0:
            return self._zero_result(symbol, conc_warning)

        # Step 4: 取最小值（凱利 vs 集中度上限）
        position_value = min(kelly_position, max_by_concentration)

        # Step 5: 換算股數（向下取整為整張）
        shares_raw = position_value / (current_price * lot_size)
        shares = max(0, int(shares_raw))         # 張數

        if shares == 0:
            return self._zero_result(symbol, "建議倉位不足一張")

        actual_value = shares * current_price * lot_size
        position_pct = actual_value / self.total_capital

        # Step 6: 計算停損風險
        stop_loss_pct = (current_price - stop_loss_price) / current_price
        max_loss = actual_value * stop_loss_pct

        warnings = []
        if conc_warning:
            warnings.append(conc_warning)
        if stop_loss_pct > 0.10:
            warnings.append(f"停損距離 {stop_loss_pct:.1%} 偏大，考慮縮小倉位")
        if position_pct > 0.15:
            warnings.append(f"單一倉位 {position_pct:.1%}，屬集中押注，確認信心度")

        logger.info(
            f"[{symbol}] 倉位={position_pct:.1%}  "
            f"金額={actual_value:,.0f}  "
            f"張數={shares}  停損=${stop_loss_price:.2f}"
        )

        return PositionSizeResult(
            symbol=symbol,
            total_capital=self.total_capital,
            position_value=actual_value,
            position_pct=position_pct,
            shares=shares,
            max_loss=max_loss,
            stop_loss_price=stop_loss_price,
            kelly_fraction=raw_kelly,
            method=(
                f"四分之一凱利({raw_kelly:.2%}) × 評分調整({score_multiplier:.2f}) "
                f"→ 凱利倉位: {adjusted_kelly:.2%}，"
                f"集中度上限: {max_by_concentration/self.total_capital:.2%}"
            ),
            warning=" | ".join(warnings),
        )

    def calculate_batch(
        self,
        candidates: list[dict],
        current_positions: int = 0,
        current_cash: Optional[float] = None,
    ) -> list[PositionSizeResult]:
        """
        批量計算多支候選股票的倉位

        Args:
            candidates: list of dict，每個 dict 包含 calculate() 所需參數
        Returns:
            list[PositionSizeResult]
        """
        results = []
        for c in candidates:
            result = self.calculate(
                current_positions=current_positions + len(results),
                current_cash=current_cash,
                **c,
            )
            if result.shares > 0:
                results.append(result)
                if current_cash is not None:
                    current_cash -= result.position_value
        return results

    # ── 工具 ────────────────────────────────────────────────────

    def _zero_result(self, symbol: str, reason: str) -> PositionSizeResult:
        return PositionSizeResult(
            symbol=symbol,
            total_capital=self.total_capital,
            position_value=0,
            position_pct=0,
            shares=0,
            max_loss=0,
            stop_loss_price=0,
            kelly_fraction=0,
            method="zero",
            warning=reason,
        )

    def risk_summary(self, results: list[PositionSizeResult]) -> str:
        """組合風險摘要"""
        if not results:
            return "無倉位"
        total_invested = sum(r.position_value for r in results)
        total_max_loss = sum(r.max_loss for r in results)
        lines = [
            f"{'='*55}",
            f"  💼 組合倉位摘要（總資金: ${self.total_capital:,.0f}）",
            f"{'='*55}",
            f"  持股數:     {len(results)} 支",
            f"  總投入:     ${total_invested:,.0f} ({total_invested/self.total_capital:.1%})",
            f"  現金保留:   ${self.total_capital - total_invested:,.0f} "
            f"({(self.total_capital - total_invested)/self.total_capital:.1%})",
            f"  最大總損失: ${total_max_loss:,.0f} ({total_max_loss/self.total_capital:.1%})",
            f"{'─'*55}",
        ]
        for r in sorted(results, key=lambda x: x.position_pct, reverse=True):
            lines.append(f"  {r.symbol:8s} {r.position_pct:5.1%}  ${r.position_value:>12,.0f}")
        lines.append(f"{'='*55}")
        return "\n".join(lines)
