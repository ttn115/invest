"""
動態停損模組 (Stop Loss)

基於 ATR（平均真實波幅）計算自適應停損價，
隨市場波動率動態調整，避免在正常波動中被觸發。

停損策略：
  1. ATR 停損    : 最主要，依波動率自適應
  2. 百分比停損  : 最簡單，固定比例
  3. 移動停損    : 追蹤最高點，保護利潤
  4. 支撐位停損  : 設在技術支撐位下方（需傳入支撐價）

塔勒布原則：
    停損不是「認輸」，是「控制下行的工具」。
    沒有停損的倉位，是無限下行風險的暴露。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
from loguru import logger


# ═══════════════════════════════════════════════════════════════
# 資料模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class StopLossResult:
    """停損計算結果"""
    symbol:          str
    entry_price:     float   # 建倉價格
    stop_loss_price: float   # 停損價格
    stop_loss_pct:   float   # 停損距離百分比
    method:          str     # 停損方法
    atr:             float   = 0.0   # ATR 值
    atr_multiple:    float   = 0.0   # ATR 倍數
    trailing_high:   float   = 0.0   # 移動停損追蹤最高點
    warning:         str     = ""

    @property
    def is_triggered(self) -> bool:
        """判斷當前價格是否觸發停損（需傳入當前價格時才能判斷）"""
        return False  # 需外部呼叫 check_trigger()

    def summary(self) -> str:
        return (
            f"[{self.symbol}] 停損: ${self.stop_loss_price:.2f}  "
            f"距入場: -{self.stop_loss_pct:.1%}  "
            f"方法: {self.method}"
            + (f"  ⚠️  {self.warning}" if self.warning else "")
        )


# ═══════════════════════════════════════════════════════════════
# ATR 停損計算器
# ═══════════════════════════════════════════════════════════════

class ATRStopLoss:
    """
    ATR-based 動態停損

    停損價 = 入場價 - (ATR × 倍數)
    ATR 倍數建議：
        日線：2.0~3.0（短中期）
        週線：3.0~4.0（中長期）
        趨勢強時用較大倍數，讓利潤奔跑
        震盪市用較小倍數，嚴格控制損失
    """

    def __init__(self, atr_period: int = 14):
        self.atr_period = atr_period

    def calculate_atr(self, df: pd.DataFrame) -> float:
        """
        計算最新的 ATR 值

        Args:
            df: OHLCV DataFrame（index=timestamp，欄位含 high/low/close）
        Returns:
            最新 ATR 值
        """
        if df.empty or len(df) < self.atr_period:
            logger.warning("資料不足，無法計算 ATR")
            return 0.0

        high  = df["high"]
        low   = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low  - close.shift(1)).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = true_range.rolling(window=self.atr_period).mean().iloc[-1]
        return float(atr)

    def calculate(
        self,
        symbol:       str,
        entry_price:  float,
        df:           pd.DataFrame,
        atr_multiple: float = 2.5,
    ) -> StopLossResult:
        """
        計算 ATR 停損價

        Args:
            symbol      : 股票代號
            entry_price : 入場價格
            df          : OHLCV 歷史資料
            atr_multiple: ATR 倍數（建議 2.0~3.0）
        Returns:
            StopLossResult
        """
        atr = self.calculate_atr(df)
        if atr <= 0:
            return self._fallback_pct(symbol, entry_price, pct=0.05)

        stop_price = entry_price - atr * atr_multiple
        stop_pct   = (entry_price - stop_price) / entry_price

        warning = ""
        if stop_pct > 0.12:
            warning = f"停損距離 {stop_pct:.1%} 偏大（ATR={atr:.2f}），考慮縮小倉位"
        elif stop_pct < 0.02:
            warning = f"停損距離 {stop_pct:.1%} 過小，容易被震出"

        logger.debug(
            f"[{symbol}] ATR停損: entry={entry_price:.2f}  "
            f"ATR={atr:.2f} × {atr_multiple} = {atr*atr_multiple:.2f}  "
            f"stop={stop_price:.2f} (-{stop_pct:.1%})"
        )

        return StopLossResult(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss_price=round(stop_price, 2),
            stop_loss_pct=stop_pct,
            method=f"ATR({self.atr_period}) × {atr_multiple}",
            atr=atr,
            atr_multiple=atr_multiple,
            warning=warning,
        )

    @staticmethod
    def _fallback_pct(symbol: str, entry_price: float, pct: float) -> StopLossResult:
        stop_price = entry_price * (1 - pct)
        return StopLossResult(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss_price=round(stop_price, 2),
            stop_loss_pct=pct,
            method=f"固定百分比 {pct:.0%}（ATR 計算失敗）",
            warning="ATR 資料不足，使用固定停損",
        )


# ═══════════════════════════════════════════════════════════════
# 移動停損（Trailing Stop）
# ═══════════════════════════════════════════════════════════════

class TrailingStopLoss:
    """
    移動停損（追蹤最高點）

    建倉後動態追蹤價格創高，停損隨之上移。
    當價格從最高點回落超過設定距離時觸發停損。

    策略：
        trailing_stop = max_price_since_entry × (1 - trail_pct)
        或
        trailing_stop = max_price_since_entry - ATR × multiple
    """

    def __init__(self, atr_stop: Optional[ATRStopLoss] = None):
        self.atr_stop = atr_stop or ATRStopLoss()

    def update(
        self,
        symbol:         str,
        entry_price:    float,
        current_price:  float,
        trailing_high:  float,
        df:             Optional[pd.DataFrame] = None,
        trail_pct:      float  = 0.07,       # 追蹤停損距離（固定百分比）
        use_atr:        bool   = True,        # 是否用 ATR 計算追蹤距離
        atr_multiple:   float  = 2.5,
    ) -> StopLossResult:
        """
        更新移動停損

        Args:
            symbol        : 股票代號
            entry_price   : 入場價格
            current_price : 當前價格
            trailing_high : 上次記錄的最高點（每次更新後儲存）
            df            : OHLCV 歷史資料（use_atr=True 時需要）
            trail_pct     : 固定百分比追蹤距離
            use_atr       : 是否使用 ATR 計算追蹤距離
            atr_multiple  : ATR 倍數（use_atr=True 時使用）
        Returns:
            StopLossResult（包含更新後的 trailing_high）
        """
        # 更新最高點
        new_high = max(trailing_high, current_price)

        # 計算追蹤距離
        if use_atr and df is not None and not df.empty:
            atr = self.atr_stop.calculate_atr(df)
            trail_distance = atr * atr_multiple if atr > 0 else new_high * trail_pct
            method = f"移動停損 ATR × {atr_multiple}"
        else:
            trail_distance = new_high * trail_pct
            atr = 0.0
            method = f"移動停損 {trail_pct:.0%}"

        stop_price = new_high - trail_distance
        # 停損只能上移，不能下移
        stop_price = max(stop_price, entry_price * 0.90)  # 最低不超過入場 -10%

        stop_pct = (entry_price - stop_price) / entry_price

        result = StopLossResult(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss_price=round(stop_price, 2),
            stop_loss_pct=stop_pct,
            method=method,
            atr=atr,
            atr_multiple=atr_multiple,
            trailing_high=new_high,
        )

        logger.debug(
            f"[{symbol}] 移動停損更新: high={new_high:.2f}  "
            f"stop={stop_price:.2f}  current={current_price:.2f}"
        )
        return result


# ═══════════════════════════════════════════════════════════════
# 停損觸發檢查器
# ═══════════════════════════════════════════════════════════════

class StopLossTrigger:
    """
    停損觸發檢查

    Usage:
        trigger = StopLossTrigger()
        is_triggered, reason = trigger.check(current_price=820, stop_result=sl_result)
    """

    @staticmethod
    def check(
        current_price: float,
        stop_result:   StopLossResult,
        use_intraday_low: bool = False,
        intraday_low:     float = 0.0,
    ) -> tuple[bool, str]:
        """
        檢查是否觸發停損

        Args:
            current_price    : 當前收盤價
            stop_result      : 停損計算結果
            use_intraday_low : 是否用盤中最低價判斷（更嚴格）
            intraday_low     : 盤中最低價
        Returns:
            (is_triggered, reason)
        """
        check_price = intraday_low if use_intraday_low and intraday_low > 0 else current_price

        if check_price <= stop_result.stop_loss_price:
            reason = (
                f"停損觸發！{'盤中低點' if use_intraday_low else '收盤價'} "
                f"${check_price:.2f} ≤ 停損價 ${stop_result.stop_loss_price:.2f}"
            )
            logger.warning(f"[{stop_result.symbol}] {reason}")
            return True, reason

        remaining_buffer = (check_price - stop_result.stop_loss_price) / check_price
        if remaining_buffer < 0.02:
            reason = f"接近停損！緩衝僅剩 {remaining_buffer:.1%}"
            logger.info(f"[{stop_result.symbol}] ⚠️  {reason}")
            return False, reason

        return False, ""


# ═══════════════════════════════════════════════════════════════
# 整合停損管理器
# ═══════════════════════════════════════════════════════════════

class StopLossManager:
    """
    統一停損管理器

    Usage:
        manager = StopLossManager()

        # 計算初始停損（用 ATR）
        sl = manager.set_initial_stop(
            symbol="2330",
            entry_price=850.0,
            df=ohlcv_df,
            atr_multiple=2.5,
        )
        print(sl.summary())

        # 每日更新移動停損
        sl = manager.update_trailing_stop(
            previous_result=sl,
            current_price=880.0,
            df=new_ohlcv_df,
        )

        # 檢查是否觸發
        triggered, reason = manager.check(current_price=790.0, stop_result=sl)
        if triggered:
            print(f"⚠️ {reason}，執行平倉")
    """

    def __init__(
        self,
        atr_period:   int   = 14,
        atr_multiple: float = 2.5,
    ):
        self.atr_stop      = ATRStopLoss(atr_period=atr_period)
        self.trailing_stop = TrailingStopLoss(atr_stop=self.atr_stop)
        self.trigger       = StopLossTrigger()
        self.default_atr_multiple = atr_multiple

    def set_initial_stop(
        self,
        symbol:       str,
        entry_price:  float,
        df:           Optional[pd.DataFrame] = None,
        atr_multiple: Optional[float] = None,
        support_price: Optional[float] = None,   # 技術支撐位（若提供，取較保守值）
    ) -> StopLossResult:
        """
        建倉時設定初始停損

        Args:
            symbol        : 股票代號
            entry_price   : 入場價格
            df            : OHLCV 歷史資料
            atr_multiple  : ATR 倍數（None 使用預設）
            support_price : 技術支撐位（可選，優先取 ATR 停損與支撐位的較低者）
        """
        multiple = atr_multiple or self.default_atr_multiple

        if df is not None and not df.empty:
            result = self.atr_stop.calculate(symbol, entry_price, df, multiple)
        else:
            # 無資料：使用固定 5% 停損
            result = ATRStopLoss._fallback_pct(symbol, entry_price, pct=0.05)

        # 若有支撐位，取停損與支撐位下方1%的較低值（更保守）
        if support_price and support_price > 0:
            support_stop = support_price * 0.99
            if support_stop < result.stop_loss_price:
                result.stop_loss_price = round(support_stop, 2)
                result.stop_loss_pct   = (entry_price - support_stop) / entry_price
                result.method          += f" + 支撐位({support_price:.2f})"

        result.trailing_high = entry_price
        logger.info(f"[{symbol}] 設定停損: ${result.stop_loss_price:.2f} ({result.method})")
        return result

    def update_trailing_stop(
        self,
        previous_result: StopLossResult,
        current_price:   float,
        df:              Optional[pd.DataFrame] = None,
    ) -> StopLossResult:
        """更新移動停損（每日收盤後呼叫）"""
        return self.trailing_stop.update(
            symbol=previous_result.symbol,
            entry_price=previous_result.entry_price,
            current_price=current_price,
            trailing_high=previous_result.trailing_high or previous_result.entry_price,
            df=df,
            atr_multiple=self.default_atr_multiple,
        )

    def check(
        self,
        current_price: float,
        stop_result:   StopLossResult,
        **kwargs,
    ) -> tuple[bool, str]:
        """檢查是否觸發停損"""
        return self.trigger.check(current_price, stop_result, **kwargs)
