"""
交易所基礎類別與虛擬交易模擬器 (Exchange Layer)

提供統一的交易介面，支援：
- PaperExchange: 本地虛擬交易模擬器
- 之後可擴充 AlpacaExchange, CCXTExchange, ShioajiExchange
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from loguru import logger


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """訂單"""
    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float = 0.0           # limit/stop 價格
    filled_price: float = 0.0    # 實際成交價
    filled_quantity: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    commission: float = 0.0
    timestamp: str = ""
    strategy: str = ""
    reason: str = ""

    @property
    def total_value(self) -> float:
        return self.filled_quantity * self.filled_price


@dataclass
class AccountInfo:
    """帳戶資訊"""
    cash: float = 100000.0
    total_equity: float = 100000.0
    positions_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


class BaseExchange(ABC):
    """交易所基礎介面"""

    @abstractmethod
    def place_order(
        self, symbol: str, side: OrderSide, quantity: float,
        order_type: OrderType = OrderType.MARKET, price: float = 0.0,
        **kwargs,
    ) -> Order:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    def get_account(self) -> AccountInfo:
        pass

    @abstractmethod
    def get_positions(self) -> dict:
        pass

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        pass


class PaperExchange(BaseExchange):
    """
    虛擬交易模擬器

    在本地模擬交易所行為，支援：
    - 市價單 / 限價單
    - 滑點與手續費模擬
    - 多標的持倉管理
    - 即時 P&L 計算
    """

    def __init__(
        self,
        initial_cash: float = 100000.0,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.0005,
    ):
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate

        self._positions: dict[str, dict] = {}  # symbol → {qty, avg_price, side}
        self._orders: list[Order] = []
        self._order_counter: int = 0
        self._prices: dict[str, float] = {}  # symbol → latest price
        self._realized_pnl: float = 0.0

        logger.info(
            f"PaperExchange initialized: cash={initial_cash}, "
            f"commission={commission_rate}, slippage={slippage_rate}"
        )

    def set_price(self, symbol: str, price: float):
        """設定/更新即時價格 (供回測或模擬用)"""
        self._prices[symbol] = price

    def get_current_price(self, symbol: str) -> float:
        """取得即時價格"""
        return self._prices.get(symbol, 0.0)

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        **kwargs,
    ) -> Order:
        """下單"""
        self._order_counter += 1
        order_id = f"PAPER-{self._order_counter:06d}"
        now = datetime.now().isoformat()

        order = Order(
            id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            timestamp=now,
            strategy=kwargs.get("strategy", ""),
            reason=kwargs.get("reason", ""),
        )

        if order_type == OrderType.MARKET:
            self._execute_market_order(order)
        elif order_type == OrderType.LIMIT:
            # 簡化：限價單立即檢查是否可成交
            current_price = self._prices.get(symbol, 0)
            if (side == OrderSide.BUY and current_price <= price) or \
               (side == OrderSide.SELL and current_price >= price):
                self._execute_market_order(order)
            else:
                order.status = OrderStatus.PENDING
                logger.info(f"Limit order pending: {order_id} {side.value} {quantity} {symbol} @ {price}")

        self._orders.append(order)
        return order

    def _execute_market_order(self, order: Order):
        """執行市價單"""
        current_price = self._prices.get(order.symbol, 0)
        if current_price == 0:
            order.status = OrderStatus.REJECTED
            order.reason = "No price data available"
            logger.warning(f"Order rejected: {order.id} - no price for {order.symbol}")
            return

        # 模擬滑點
        if order.side == OrderSide.BUY:
            filled_price = current_price * (1 + self.slippage_rate)
        else:
            filled_price = current_price * (1 - self.slippage_rate)

        # 計算手續費
        commission = filled_price * order.quantity * self.commission_rate

        # 檢查現金是否足夠 (買入時)
        if order.side == OrderSide.BUY:
            total_cost = filled_price * order.quantity + commission
            if total_cost > self.cash:
                order.status = OrderStatus.REJECTED
                order.reason = f"Insufficient cash: {self.cash:.2f} < {total_cost:.2f}"
                logger.warning(f"Order rejected: {order.id} - {order.reason}")
                return

        # 執行交易
        order.filled_price = filled_price
        order.filled_quantity = order.quantity
        order.commission = commission
        order.status = OrderStatus.FILLED

        if order.side == OrderSide.BUY:
            self.cash -= (filled_price * order.quantity + commission)
            self._update_position_buy(order.symbol, order.quantity, filled_price)
        else:
            pnl = self._update_position_sell(order.symbol, order.quantity, filled_price)
            self.cash += (filled_price * order.quantity - commission)
            self._realized_pnl += pnl

        logger.info(
            f"Order filled: {order.id} {order.side.value} {order.quantity:.6f} "
            f"{order.symbol} @ {filled_price:.4f} (commission={commission:.4f})"
        )

    def _update_position_buy(self, symbol: str, quantity: float, price: float):
        """更新持倉 (買入)"""
        if symbol in self._positions:
            pos = self._positions[symbol]
            total_qty = pos["quantity"] + quantity
            pos["avg_price"] = (
                (pos["avg_price"] * pos["quantity"] + price * quantity) / total_qty
            )
            pos["quantity"] = total_qty
        else:
            self._positions[symbol] = {
                "quantity": quantity,
                "avg_price": price,
                "side": "long",
            }

    def _update_position_sell(self, symbol: str, quantity: float, price: float) -> float:
        """更新持倉 (賣出)，返回已實現損益"""
        pnl = 0.0
        if symbol in self._positions:
            pos = self._positions[symbol]
            pnl = (price - pos["avg_price"]) * min(quantity, pos["quantity"])
            pos["quantity"] -= quantity

            if pos["quantity"] <= 1e-9:
                del self._positions[symbol]

        return pnl

    def cancel_order(self, order_id: str) -> bool:
        """取消訂單"""
        for order in self._orders:
            if order.id == order_id and order.status == OrderStatus.PENDING:
                order.status = OrderStatus.CANCELLED
                return True
        return False

    def get_account(self) -> AccountInfo:
        """取得帳戶資訊"""
        positions_value = sum(
            pos["quantity"] * self._prices.get(symbol, pos["avg_price"])
            for symbol, pos in self._positions.items()
        )

        unrealized_pnl = sum(
            (self._prices.get(symbol, pos["avg_price"]) - pos["avg_price"]) * pos["quantity"]
            for symbol, pos in self._positions.items()
        )

        return AccountInfo(
            cash=self.cash,
            total_equity=self.cash + positions_value,
            positions_value=positions_value,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=self._realized_pnl,
        )

    def get_positions(self) -> dict:
        """取得所有持倉"""
        result = {}
        for symbol, pos in self._positions.items():
            current_price = self._prices.get(symbol, pos["avg_price"])
            pnl = (current_price - pos["avg_price"]) * pos["quantity"]
            result[symbol] = {
                **pos,
                "current_price": current_price,
                "market_value": pos["quantity"] * current_price,
                "unrealized_pnl": pnl,
                "pnl_pct": (current_price - pos["avg_price"]) / pos["avg_price"] * 100,
            }
        return result

    def get_order_history(self, limit: int = 50) -> list[Order]:
        """取得訂單歷史"""
        return self._orders[-limit:]

    def reset(self):
        """重設帳戶"""
        self.cash = self.initial_cash
        self._positions.clear()
        self._orders.clear()
        self._order_counter = 0
        self._realized_pnl = 0.0
        logger.info("PaperExchange reset")
