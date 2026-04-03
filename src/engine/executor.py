"""
訂單執行器 (Order Executor)

統一的訂單執行介面，支援 Paper → Live 無縫切換。
負責協調決策引擎、風險管理器和交易所之間的互動。
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from src.engine.decision import DecisionEngine, DecisionResult
from src.exchange.base import BaseExchange, OrderSide, OrderType, PaperExchange
from src.risk.manager import Position, RiskManager
from src.strategy.base import SignalType


class Executor:
    """
    訂單執行器

    整合決策引擎、風險管理、交易所模組，
    實現完整的交易執行流程。
    """

    def __init__(
        self,
        exchange: BaseExchange,
        risk_manager: RiskManager,
        decision_engine: Optional[DecisionEngine] = None,
    ):
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.decision_engine = decision_engine
        self._trade_history: list[dict] = []

    def execute_decision(
        self,
        decision: DecisionResult,
        symbol: str,
        market: str = "crypto",
    ) -> Optional[dict]:
        """
        根據決策結果執行交易

        Args:
            decision: 決策引擎產生的結果
            symbol: 交易標的
            market: 市場類型

        Returns:
            交易結果 dict 或 None (未執行)
        """
        if decision.final_signal == SignalType.HOLD:
            return None

        account = self.exchange.get_account()
        current_price = self.exchange.get_current_price(symbol)

        if current_price <= 0:
            logger.warning(f"Cannot execute: no price for {symbol}")
            return None

        positions = self.exchange.get_positions()

        # BUY: 開倉或加倉
        if decision.final_signal == SignalType.BUY:
            # 計算倉位大小
            quantity = self.risk_manager.calculate_position_size(
                total_equity=account.total_equity,
                price=current_price,
                risk_per_trade=0.02,
            )

            if quantity <= 0:
                return None

            # 風控檢查
            risk_check = self.risk_manager.check_order(
                symbol=symbol,
                side="buy",
                price=current_price,
                quantity=quantity,
                total_equity=account.total_equity,
                cash=account.cash,
                market=market,
            )

            if not risk_check.approved:
                logger.warning(
                    f"Order rejected by risk manager: {risk_check.reasons}"
                )
                return None

            adjusted_qty = risk_check.adjusted_quantity
            order = self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=adjusted_qty,
                order_type=OrderType.MARKET,
                strategy=decision.reason[:100],
            )

            if order.status.value == "filled":
                # 建立持倉並設定止損止盈
                position = Position(
                    symbol=symbol,
                    market=market,
                    side="long",
                    quantity=order.filled_quantity,
                    entry_price=order.filled_price,
                    current_price=order.filled_price,
                    stop_loss=self.risk_manager.calculate_stop_loss(order.filled_price),
                    take_profit=self.risk_manager.calculate_take_profit(order.filled_price),
                    highest_price=order.filled_price,
                )
                self.risk_manager.add_position(position)

                result = {
                    "action": "BUY",
                    "symbol": symbol,
                    "quantity": order.filled_quantity,
                    "price": order.filled_price,
                    "total": order.total_value,
                    "commission": order.commission,
                    "confidence": decision.confidence,
                    "market_state": decision.market_state.value,
                    "stop_loss": position.stop_loss,
                    "take_profit": position.take_profit,
                }
                self._trade_history.append(result)
                logger.info(f"✅ BUY executed: {result}")
                return result

        # SELL: 平倉
        elif decision.final_signal == SignalType.SELL:
            if symbol not in positions:
                logger.debug(f"No position to sell for {symbol}")
                return None

            qty = positions[symbol]["quantity"]
            order = self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=qty,
                order_type=OrderType.MARKET,
                strategy=decision.reason[:100],
            )

            if order.status.value == "filled":
                # 計算損益
                pos_info = positions[symbol]
                pnl = (order.filled_price - pos_info["avg_price"]) * qty

                # 更新策略績效
                is_win = pnl > 0
                for name in decision.strategy_signals:
                    if self.decision_engine:
                        self.decision_engine.update_strategy_performance(name, is_win)

                self.risk_manager.remove_position(symbol)

                result = {
                    "action": "SELL",
                    "symbol": symbol,
                    "quantity": order.filled_quantity,
                    "price": order.filled_price,
                    "total": order.total_value,
                    "commission": order.commission,
                    "pnl": pnl,
                    "pnl_pct": pnl / (pos_info["avg_price"] * qty) * 100,
                    "is_win": is_win,
                }
                self._trade_history.append(result)
                logger.info(f"{'✅' if is_win else '❌'} SELL executed: {result}")
                return result

        return None

    def check_stop_conditions(self, symbol: str) -> Optional[dict]:
        """
        檢查持倉的止損/止盈條件

        Returns:
            如果觸發則返回平倉結果
        """
        positions = self.risk_manager.get_positions()
        if symbol not in positions:
            return None

        position = positions[symbol]
        current_price = self.exchange.get_current_price(symbol)
        if current_price <= 0:
            return None

        position.current_price = current_price
        trigger_reason = self.risk_manager.check_stop_conditions(position)

        if trigger_reason:
            logger.warning(f"⚠️ {trigger_reason}")
            # 強制平倉
            qty = position.quantity
            order = self.exchange.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=qty,
                strategy=f"stop_trigger: {trigger_reason[:80]}",
            )

            if order.status.value == "filled":
                pnl = (order.filled_price - position.entry_price) * qty
                self.risk_manager.remove_position(symbol)

                result = {
                    "action": "STOP_SELL",
                    "symbol": symbol,
                    "quantity": qty,
                    "price": order.filled_price,
                    "pnl": pnl,
                    "reason": trigger_reason,
                }
                self._trade_history.append(result)
                return result

        return None

    def get_trade_history(self) -> list[dict]:
        """取得交易歷史"""
        return self._trade_history.copy()
