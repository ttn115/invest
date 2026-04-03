"""
風險管理器 (Risk Manager)

負責止損/止盈、倉位計算、投資組合風控。
確保每筆交易和整體投資組合在可控風險之內。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


@dataclass
class Position:
    """持倉資訊"""
    symbol: str
    market: str
    side: str  # "long" / "short"
    quantity: float
    entry_price: float
    current_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trailing_stop: float = 0.0
    highest_price: float = 0.0  # 追蹤止損用

    @property
    def unrealized_pnl(self) -> float:
        """未實現損益"""
        if self.current_price == 0:
            return 0.0
        if self.side == "long":
            return (self.current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.current_price) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        """未實現損益百分比"""
        if self.entry_price == 0:
            return 0.0
        if self.side == "long":
            return (self.current_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - self.current_price) / self.entry_price

    @property
    def market_value(self) -> float:
        """市值"""
        return self.quantity * self.current_price


@dataclass
class RiskCheckResult:
    """風險檢查結果"""
    approved: bool = True
    adjusted_quantity: float = 0.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class RiskManager:
    """
    風險管理器

    功能:
    - 止損/止盈檢查
    - 倉位大小計算
    - 投資組合風險控制
    - 交易頻率限制
    - 台股漲跌幅限制
    """

    def __init__(
        self,
        max_position_pct: float = 0.10,
        max_total_risk_pct: float = 0.30,
        stop_loss_pct: float = 0.05,
        take_profit_pct: float = 0.15,
        max_positions: int = 10,
        max_daily_trades: int = 20,
        trailing_stop: bool = True,
        trailing_stop_pct: float = 0.03,
        atr_multiplier: float = 0.0,  # 0 = 不使用 ATR，> 0 = 啟用動態止損
    ):
        self.max_position_pct = max_position_pct
        self.max_total_risk_pct = max_total_risk_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_positions = max_positions
        self.max_daily_trades = max_daily_trades
        self.trailing_stop = trailing_stop
        self.trailing_stop_pct = trailing_stop_pct
        self.atr_multiplier = atr_multiplier

        self._daily_trades: int = 0
        self._positions: dict[str, Position] = {}

    def check_order(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        total_equity: float,
        cash: float,
        market: str = "crypto",
    ) -> RiskCheckResult:
        """
        檢查訂單是否符合風控規則

        Args:
            symbol: 交易標的
            side: "buy" / "sell"
            price: 價格
            quantity: 數量
            total_equity: 總資產
            cash: 可用現金
            market: 市場類型

        Returns:
            RiskCheckResult 風控結果
        """
        result = RiskCheckResult(approved=True, adjusted_quantity=quantity)
        order_value = price * quantity

        # 1. 檢查每日交易次數
        if self._daily_trades >= self.max_daily_trades:
            result.approved = False
            result.reasons.append(
                f"Daily trade limit reached ({self._daily_trades}/{self.max_daily_trades})"
            )
            return result

        # 2. 檢查現金是否足夠 (買入時)
        if side == "buy":
            if order_value > cash:
                # 調整數量以適應可用現金
                max_qty = cash / price * 0.99  # 留 1% 緩衝
                if max_qty <= 0:
                    result.approved = False
                    result.reasons.append(f"Insufficient cash: {cash:.2f} < {order_value:.2f}")
                    return result
                result.adjusted_quantity = max_qty
                result.warnings.append(
                    f"Quantity adjusted to {max_qty:.6f} (cash constraint)"
                )
                order_value = price * max_qty

        # 3. 檢查單一標的佔比
        max_position_value = total_equity * self.max_position_pct
        existing_value = 0
        if symbol in self._positions:
            existing_value = self._positions[symbol].market_value

        if side == "buy" and (existing_value + order_value) > max_position_value:
            allowed_value = max(max_position_value - existing_value, 0)
            if allowed_value <= 0:
                result.approved = False
                result.reasons.append(
                    f"Position limit: {symbol} already at max "
                    f"({existing_value:.2f}/{max_position_value:.2f})"
                )
                return result
            result.adjusted_quantity = allowed_value / price
            result.warnings.append(
                f"Quantity adjusted for position limit: max value={max_position_value:.2f}"
            )

        # 4. 檢查持倉數量限制
        if side == "buy" and symbol not in self._positions:
            if len(self._positions) >= self.max_positions:
                result.approved = False
                result.reasons.append(
                    f"Max positions reached ({len(self._positions)}/{self.max_positions})"
                )
                return result

        # 5. 檢查總風險
        total_positions_value = sum(p.market_value for p in self._positions.values())
        if side == "buy":
            new_total = total_positions_value + order_value
            if new_total > total_equity * self.max_total_risk_pct:
                result.warnings.append(
                    f"Total exposure high: {new_total:.2f} "
                    f"({new_total / total_equity * 100:.1f}% of equity)"
                )

        logger.debug(
            f"Risk check: {side} {result.adjusted_quantity:.6f} {symbol} @ {price} "
            f"→ {'APPROVED' if result.approved else 'REJECTED'}"
        )
        return result

    def calculate_stop_loss(self, entry_price: float, side: str = "long", df=None) -> float:
        """
        計算止損價
        
        如果 atr_multiplier > 0 且 df 提供了 ATR 數據，使用動態止損。
        否則使用固定百分比止損。
        """
        if self.atr_multiplier > 0 and df is not None:
            atr_stop = self._calculate_atr_stop(entry_price, side, df)
            if atr_stop is not None:
                return atr_stop
        
        # Fallback: 固定百分比止損
        if side == "long":
            return entry_price * (1 - self.stop_loss_pct)
        else:
            return entry_price * (1 + self.stop_loss_pct)

    def _calculate_atr_stop(self, entry_price: float, side: str, df) -> float | None:
        """
        ATR 動態止損 — 根據資產波動率自動調整止損距離。
        
        原理：
        - ATR (Average True Range) 代表最近 N 根 K 線的平均波動幅度
        - 用 ATR × 倍數 決定止損距離
        - 高波動幣種 (如 KITE) 會有較寬的止損，避免被正常震盪洗出
        - 低波動幣種 (如 BTC) 會有較窄的止損，及早保護資金
        """
        # 尋找 ATR 欄位
        atr_col = None
        if df is not None:
            for col in df.columns:
                if col.startswith("ATR_"):
                    atr_col = col
                    break
        
        if atr_col is None or df[atr_col].iloc[-1] is None:
            return None
        
        import pandas as pd
        atr_value = df[atr_col].iloc[-1]
        if pd.isna(atr_value) or atr_value <= 0:
            return None
        
        stop_distance = atr_value * self.atr_multiplier
        
        if side == "long":
            atr_stop = entry_price - stop_distance
        else:
            atr_stop = entry_price + stop_distance
        
        # 安全帽：ATR 止損不得遠於固定百分比止損的 2 倍
        max_stop_distance = entry_price * self.stop_loss_pct * 2
        if stop_distance > max_stop_distance:
            logger.warning(
                f"ATR stop distance ({stop_distance:.4f}) exceeds 2x fixed stop. "
                f"Capping at {max_stop_distance:.4f}"
            )
            if side == "long":
                atr_stop = entry_price - max_stop_distance
            else:
                atr_stop = entry_price + max_stop_distance
        
        logger.debug(
            f"ATR stop: entry={entry_price:.4f}, ATR={atr_value:.4f}, "
            f"mult={self.atr_multiplier}, stop={atr_stop:.4f}"
        )
        return atr_stop

    def calculate_take_profit(self, entry_price: float, side: str = "long") -> float:
        """計算止盈價"""
        if side == "long":
            return entry_price * (1 + self.take_profit_pct)
        else:
            return entry_price * (1 - self.take_profit_pct)

    def calculate_position_size(
        self,
        total_equity: float,
        price: float,
        risk_per_trade: float = 0.02,
        stop_loss_distance: Optional[float] = None,
    ) -> float:
        """
        計算建議的倉位大小

        Args:
            total_equity: 總資產
            price: 當前價格
            risk_per_trade: 每筆交易風險比例 (預設 2%)
            stop_loss_distance: 止損距離 (價差)

        Returns:
            建議的數量
        """
        if stop_loss_distance is None:
            stop_loss_distance = price * self.stop_loss_pct

        risk_amount = total_equity * risk_per_trade
        quantity = risk_amount / max(stop_loss_distance, 1e-9)

        # 不超過最大持倉比例
        max_value = total_equity * self.max_position_pct
        max_quantity = max_value / price
        quantity = min(quantity, max_quantity)

        return max(quantity, 0)

    def check_stop_conditions(self, position: Position) -> Optional[str]:
        """
        檢查是否觸發止損/止盈/追蹤止損

        Returns:
            觸發原因 (None 表示未觸發)
        """
        if position.current_price == 0:
            return None

        pnl_pct = position.unrealized_pnl_pct

        # 止損
        if position.stop_loss > 0:
            if position.side == "long" and position.current_price <= position.stop_loss:
                return f"Stop loss triggered: {position.current_price:.2f} <= {position.stop_loss:.2f}"
            if position.side == "short" and position.current_price >= position.stop_loss:
                return f"Stop loss triggered: {position.current_price:.2f} >= {position.stop_loss:.2f}"

        # 止盈
        if position.take_profit > 0:
            if position.side == "long" and position.current_price >= position.take_profit:
                return f"Take profit triggered: {position.current_price:.2f} >= {position.take_profit:.2f}"
            if position.side == "short" and position.current_price <= position.take_profit:
                return f"Take profit triggered: {position.current_price:.2f} <= {position.take_profit:.2f}"

        # 追蹤止損
        if self.trailing_stop and position.side == "long":
            if position.current_price > position.highest_price:
                position.highest_price = position.current_price
            trailing_stop_price = position.highest_price * (1 - self.trailing_stop_pct)
            if position.current_price <= trailing_stop_price:
                return (
                    f"Trailing stop triggered: {position.current_price:.2f} <= "
                    f"{trailing_stop_price:.2f} (peak: {position.highest_price:.2f})"
                )

        return None

    def add_position(self, position: Position):
        """新增持倉"""
        self._positions[position.symbol] = position
        self._daily_trades += 1

    def remove_position(self, symbol: str) -> Optional[Position]:
        """移除持倉"""
        self._daily_trades += 1
        return self._positions.pop(symbol, None)

    def get_positions(self) -> dict[str, Position]:
        """取得所有持倉"""
        return self._positions.copy()

    def reset_daily_counter(self):
        """重設每日交易計數器"""
        self._daily_trades = 0
