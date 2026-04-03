"""
回測引擎 (Backtester)

事件驅動回測系統，支援：
- 歷史資料回測
- 交易成本模擬 (手續費 + 滑點)
- 完整績效分析 (Sharpe, Drawdown, Win Rate, etc.)
- 回測報告生成
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from src.data.indicators import IndicatorEngine
from src.engine.decision import DecisionEngine, MarketState
from src.exchange.base import OrderSide, PaperExchange
from src.strategy.base import SignalType


@dataclass
class BacktestResult:
    """回測結果"""
    # 基本資訊
    symbol: str = ""
    start_date: str = ""
    end_date: str = ""
    total_bars: int = 0

    # 績效指標
    initial_capital: float = 100000
    final_equity: float = 100000
    total_return: float = 0.0         # 總報酬率
    annual_return: float = 0.0        # 年化報酬率
    sharpe_ratio: float = 0.0         # Sharpe Ratio
    sortino_ratio: float = 0.0        # Sortino Ratio
    max_drawdown: float = 0.0         # 最大回撤
    max_drawdown_pct: float = 0.0     # 最大回撤百分比

    # 交易統計
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0

    # 每日權益曲線
    equity_curve: list[dict] = field(default_factory=list)
    trades_log: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        """產生回測摘要"""
        return f"""
╔══════════════════════════════════════════════════╗
║            回測結果 (Backtest Report)              ║
╠══════════════════════════════════════════════════╣
║  標的: {self.symbol:<40s}  ║
║  期間: {self.start_date} ~ {self.end_date:<24s}  ║
║  K線數: {self.total_bars:<39d}  ║
╠══════════════════════════════════════════════════╣
║  📊 績效指標                                      ║
║  初始資金:    {self.initial_capital:>14,.2f}                    ║
║  最終權益:    {self.final_equity:>14,.2f}                    ║
║  總報酬率:    {self.total_return:>13.2%}                     ║
║  年化報酬率:  {self.annual_return:>13.2%}                     ║
║  Sharpe Ratio: {self.sharpe_ratio:>11.4f}                    ║
║  Sortino Ratio: {self.sortino_ratio:>10.4f}                   ║
║  最大回撤:    {self.max_drawdown_pct:>13.2%}                  ║
╠══════════════════════════════════════════════════╣
║  📈 交易統計                                      ║
║  總交易次數:  {self.total_trades:>14d}                    ║
║  勝利次數:    {self.winning_trades:>14d}                    ║
║  虧損次數:    {self.losing_trades:>14d}                    ║
║  勝率:        {self.win_rate:>13.2%}                     ║
║  盈虧比:      {self.profit_factor:>14.4f}                   ║
║  平均獲利:    {self.avg_win:>14,.2f}                    ║
║  平均虧損:    {self.avg_loss:>14,.2f}                    ║
║  最大單筆獲利: {self.largest_win:>13,.2f}                   ║
║  最大單筆虧損: {self.largest_loss:>13,.2f}                   ║
╚══════════════════════════════════════════════════╝
"""


class Backtester:
    """
    回測引擎

    Usage:
        backtester = Backtester(initial_capital=100000)
        result = backtester.run(df, decision_engine, symbol="BTC/USDT")
        print(result.summary())
    """

    def __init__(
        self,
        initial_capital: float = 100000,
        commission_pct: float = 0.001,
        slippage_pct: float = 0.0005,
        risk_per_trade: float = 0.02,
    ):
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.risk_per_trade = risk_per_trade

    def run(
        self,
        df: pd.DataFrame,
        decision_engine: DecisionEngine,
        symbol: str = "BTC/USDT",
        indicator_engine: Optional[IndicatorEngine] = None,
    ) -> BacktestResult:
        """
        執行回測

        Args:
            df: OHLCV DataFrame (index=timestamp)
            decision_engine: 決策引擎
            symbol: 交易標的
            indicator_engine: 指標引擎 (可選，自動計算指標)

        Returns:
            BacktestResult 回測結果
        """
        logger.info(f"Starting backtest: {symbol}, bars={len(df)}")

        if df.empty or len(df) < 50:
            logger.warning("Insufficient data for backtest")
            return BacktestResult(symbol=symbol)

        # 計算指標
        if indicator_engine:
            df = indicator_engine.add_all(df)

        # 初始化模擬交易所
        exchange = PaperExchange(
            initial_cash=self.initial_capital,
            commission_rate=self.commission_pct,
            slippage_rate=self.slippage_pct,
        )

        equity_curve = []
        trades_log = []
        position_open = False

        # 逐根 K 線回測
        lookback = 50  # 最少需要的歷史資料量
        for i in range(lookback, len(df)):
            # 取歷史資料切片
            historical = df.iloc[:i + 1].copy()
            current_bar = df.iloc[i]
            current_price = current_bar["close"]
            timestamp = str(df.index[i])

            # 更新價格
            exchange.set_price(symbol, current_price)

            # 取得決策
            try:
                decision = decision_engine.make_decision(historical, symbol)
            except Exception as e:
                logger.error(f"Decision error at {timestamp}: {e}")
                continue

            # 執行交易
            if decision.final_signal == SignalType.BUY and not position_open:
                # 計算倉位大小
                account = exchange.get_account()
                max_value = account.cash * self.risk_per_trade * 10  # 風險比例轉倉位
                quantity = min(max_value / current_price, account.cash * 0.95 / current_price)

                if quantity > 0:
                    order = exchange.place_order(
                        symbol, OrderSide.BUY, quantity,
                        strategy=", ".join(
                            f"{n}={s.signal_type.value}"
                            for n, s in decision.strategy_signals.items()
                        ),
                        reason=decision.reason[:200],
                    )
                    if order.status.value == "filled":
                        position_open = True
                        trades_log.append({
                            "timestamp": timestamp,
                            "side": "buy",
                            "price": order.filled_price,
                            "quantity": order.filled_quantity,
                            "confidence": decision.confidence,
                            "market_state": decision.market_state.value,
                        })

            elif decision.final_signal == SignalType.SELL and position_open:
                positions = exchange.get_positions()
                if symbol in positions:
                    qty = positions[symbol]["quantity"]
                    order = exchange.place_order(
                        symbol, OrderSide.SELL, qty,
                        strategy="sell_signal",
                        reason=decision.reason[:200],
                    )
                    if order.status.value == "filled":
                        position_open = False
                        pnl = positions[symbol].get("unrealized_pnl", 0)
                        trades_log.append({
                            "timestamp": timestamp,
                            "side": "sell",
                            "price": order.filled_price,
                            "quantity": order.filled_quantity,
                            "pnl": pnl,
                            "confidence": decision.confidence,
                        })

            # 記錄權益
            account = exchange.get_account()
            equity_curve.append({
                "timestamp": timestamp,
                "equity": account.total_equity,
                "cash": account.cash,
                "positions_value": account.positions_value,
            })

        # 計算績效指標
        result = self._calculate_metrics(
            symbol, df, equity_curve, trades_log, exchange
        )
        logger.info(f"Backtest complete: {result.total_return:.2%} return, {result.total_trades} trades")
        return result

    def _calculate_metrics(
        self,
        symbol: str,
        df: pd.DataFrame,
        equity_curve: list[dict],
        trades_log: list[dict],
        exchange: PaperExchange,
    ) -> BacktestResult:
        """計算績效指標"""
        account = exchange.get_account()
        result = BacktestResult(
            symbol=symbol,
            start_date=str(df.index[0]),
            end_date=str(df.index[-1]),
            total_bars=len(df),
            initial_capital=self.initial_capital,
            final_equity=account.total_equity,
            equity_curve=equity_curve,
            trades_log=trades_log,
        )

        # 總報酬率
        result.total_return = (account.total_equity - self.initial_capital) / self.initial_capital

        # 年化報酬率 (假設 252 個交易日)
        if len(equity_curve) > 1:
            days = len(equity_curve)
            result.annual_return = (1 + result.total_return) ** (252 / max(days, 1)) - 1

        # Sharpe Ratio
        if equity_curve:
            equities = [e["equity"] for e in equity_curve]
            returns = pd.Series(equities).pct_change().dropna()
            if len(returns) > 1 and returns.std() > 0:
                result.sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(252)

                # Sortino Ratio (只考慮下行風險)
                downside_returns = returns[returns < 0]
                if len(downside_returns) > 0 and downside_returns.std() > 0:
                    result.sortino_ratio = (returns.mean() / downside_returns.std()) * np.sqrt(252)

        # 最大回撤
        if equity_curve:
            equities = pd.Series([e["equity"] for e in equity_curve])
            peak = equities.expanding().max()
            drawdown = (equities - peak) / peak
            result.max_drawdown_pct = drawdown.min()
            result.max_drawdown = (equities - peak).min()

        # 交易統計
        sell_trades = [t for t in trades_log if t.get("side") == "sell"]
        result.total_trades = len(sell_trades)

        if sell_trades:
            pnls = [t.get("pnl", 0) for t in sell_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            result.winning_trades = len(wins)
            result.losing_trades = len(losses)
            result.win_rate = len(wins) / len(sell_trades) if sell_trades else 0

            result.avg_win = np.mean(wins) if wins else 0
            result.avg_loss = np.mean(losses) if losses else 0
            result.largest_win = max(wins) if wins else 0
            result.largest_loss = min(losses) if losses else 0

            total_profit = sum(wins) if wins else 0
            total_loss = abs(sum(losses)) if losses else 0
            result.profit_factor = total_profit / max(total_loss, 1e-9)

        return result
