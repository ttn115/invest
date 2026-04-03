"""
自主交易機器人 - 主程式 (Main Entry Point)

Usage:
    python -m src.main                    # 預設: Paper Trading
    python -m src.main --mode backtest    # 回測模式
    python -m src.main --mode paper       # 虛擬交易模式
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from loguru import logger

from src.config.settings import Settings, StrategiesConfig, RiskConfig, DecisionEngineConfig
from src.data.collector import CryptoCollector, StockCollector, TwStockCollector, create_collector
from src.data.indicators import IndicatorEngine
from src.data.storage import DataStorage
from src.engine.backtester import Backtester
from src.engine.decision import DecisionEngine
from src.engine.executor import Executor
from src.exchange.base import PaperExchange
from src.monitor.logger import setup_logger
from src.risk.manager import RiskManager
from src.strategy.bollinger_strategy import BollingerStrategy
from src.strategy.macd_strategy import MACDStrategy
from src.strategy.rsi_strategy import RSIStrategy
from src.strategy.sentiment_strategy import SentimentStrategy
from src.strategy.funding_rate_strategy import FundingRateStrategy
from src.strategy.sma_crossover import SMACrossoverStrategy


def build_decision_engine(
    settings: Settings,
    market_name: str | None = None,
) -> DecisionEngine:
    """
    根據設定建立決策引擎。

    Args:
        settings: 設定管理器
        market_name: 市場名稱 (crypto / us_stock / tw_stock)。
                     若指定，則使用該市場的專屬策略/決策參數 (已與全域 deep merge)。
                     若為 None，則使用全域設定。

    Returns:
        DecisionEngine 實例
    """
    strategies = {}
    weights = {}
    filters = {}

    # 取得合併後的策略設定 (全域 + 市場覆蓋)
    strat_config = settings.get_market_strategies(market_name)

    if strat_config.sma_crossover.enabled:
        strategies["SMA_Crossover"] = SMACrossoverStrategy(strat_config.sma_crossover.params)
        weights["SMA_Crossover"] = strat_config.sma_crossover.weight

    if strat_config.rsi.enabled:
        strategies["RSI"] = RSIStrategy(strat_config.rsi.params)
        weights["RSI"] = strat_config.rsi.weight

    if strat_config.macd.enabled:
        strategies["MACD"] = MACDStrategy(strat_config.macd.params)
        weights["MACD"] = strat_config.macd.weight

    if strat_config.bollinger.enabled:
        strategies["Bollinger"] = BollingerStrategy(strat_config.bollinger.params)
        weights["Bollinger"] = strat_config.bollinger.weight

    if strat_config.sentiment.enabled:
        # 情緒策略: 自動注入 market_name 到參數中
        sent_params = strat_config.sentiment.params.copy()
        if market_name:
            sent_params["market_name"] = market_name
        strategies["Sentiment"] = SentimentStrategy(sent_params)
        weights["Sentiment"] = strat_config.sentiment.weight

    if getattr(strat_config, "funding_rate", None) and strat_config.funding_rate.enabled:
        mode = strat_config.funding_rate.params.get("mode", "signal")
        strategy = FundingRateStrategy(**strat_config.funding_rate.params)
        if mode == "filter":
            filters["FundingRate"] = strategy
        else:
            strategies["FundingRate"] = strategy
            weights["FundingRate"] = strat_config.funding_rate.weight

    if getattr(strat_config, "btc_regime", None) and strat_config.btc_regime.enabled:
        from src.strategy.regime_filter import RegimeFilterStrategy
        filters["BTCRegime"] = RegimeFilterStrategy(**strat_config.btc_regime.params)

    if getattr(strat_config, "volume_filter", None) and strat_config.volume_filter.enabled:
        from src.strategy.volume_filter import VolumeFilterStrategy
        filters["VolumeFilter"] = VolumeFilterStrategy(**strat_config.volume_filter.params)

    # 取得合併後的決策引擎設定
    de_config = settings.get_market_decision_engine(market_name)

    market_label = f" [{market_name}]" if market_name else ""
    logger.info(
        f"🏗️ 建立決策引擎{market_label}: "
        f"策略={list(strategies.keys())}, "
        f"濾網={list(filters.keys())}, "
        f"min_agreement={de_config.min_agreement}"
    )

    engine = DecisionEngine(
        strategies=strategies,
        weights=weights,
        voting_method=de_config.voting_method,
        min_agreement=de_config.min_agreement,
        panic_buy_override=getattr(de_config, "panic_buy_override", None),
    )
    
    # 註冊濾網
    for fk, fv in filters.items():
        engine.add_filter(fk, fv)
        
    return engine


def run_backtest(settings: Settings):
    """執行回測模式"""
    logger.info("=" * 60)
    logger.info("🔬 回測模式 (Backtest Mode)")
    logger.info("=" * 60)

    indicator_engine = IndicatorEngine()
    bt_config = settings.config.backtest

    # 對每個啟用的市場執行回測 — 每個市場有獨立的決策引擎
    markets = settings.config.markets

    if markets.crypto.enabled:
        logger.info("\n🪙 === 虛擬幣市場 (Crypto) ===")
        decision_engine = build_decision_engine(settings, market_name="crypto")
        backtester = Backtester(
            initial_capital=settings.config.general.initial_capital,
            commission_pct=bt_config.commission_pct,
            slippage_pct=bt_config.slippage_pct,
        )
        collector = CryptoCollector(
            exchange_id=markets.crypto.exchange,
            sandbox=True,
        )
        for symbol in markets.crypto.symbols:
            logger.info(f"\n📊 回測: {symbol}")
            df = collector.fetch_ohlcv(
                symbol=symbol,
                timeframe=markets.crypto.timeframe,
                start=bt_config.start_date,
                end=bt_config.end_date,
                limit=5000,
            )
            if not df.empty:
                result = backtester.run(df, decision_engine, symbol, indicator_engine)
                print(result.summary())

    if markets.us_stock.enabled:
        logger.info("\n🇺🇸 === 美股市場 (US Stock) ===")
        decision_engine = build_decision_engine(settings, market_name="us_stock")
        backtester = Backtester(
            initial_capital=settings.config.general.initial_capital,
            commission_pct=bt_config.commission_pct,
            slippage_pct=bt_config.slippage_pct,
        )
        collector = StockCollector()
        for symbol in markets.us_stock.symbols:
            logger.info(f"\n📊 回測: {symbol}")
            df = collector.fetch_ohlcv(
                symbol=symbol,
                timeframe=markets.us_stock.timeframe,
                start=bt_config.start_date,
                end=bt_config.end_date,
            )
            if not df.empty:
                result = backtester.run(df, decision_engine, symbol, indicator_engine)
                print(result.summary())

    if markets.tw_stock.enabled:
        logger.info("\n🇹🇼 === 台股市場 (TW Stock) ===")
        decision_engine = build_decision_engine(settings, market_name="tw_stock")
        backtester = Backtester(
            initial_capital=settings.config.general.initial_capital,
            commission_pct=bt_config.commission_pct,
            slippage_pct=bt_config.slippage_pct,
        )
        collector = TwStockCollector()
        for symbol in markets.tw_stock.symbols:
            logger.info(f"\n📊 回測: {symbol}")
            df = collector.fetch_ohlcv(
                symbol=symbol,
                timeframe=markets.tw_stock.timeframe,
                start=bt_config.start_date,
                end=bt_config.end_date,
            )
            if not df.empty:
                result = backtester.run(df, decision_engine, symbol, indicator_engine)
                print(result.summary())


def run_paper_trading(settings: Settings):
    """執行虛擬交易模式"""
    logger.info("=" * 60)
    logger.info("📝 虛擬交易模式 (Paper Trading)")
    logger.info("=" * 60)

    indicator_engine = IndicatorEngine()
    storage = DataStorage()

    exchange = PaperExchange(
        initial_cash=settings.config.general.initial_capital,
        commission_rate=settings.config.backtest.commission_pct,
        slippage_rate=settings.config.backtest.slippage_pct,
    )

    # 為每個市場建立獨立的決策引擎和風控管理器
    market_engines: dict[str, tuple[DecisionEngine, RiskManager]] = {}
    markets = settings.config.markets

    for market_name in settings.get_enabled_markets():
        de = build_decision_engine(settings, market_name=market_name)
        risk_config = settings.get_market_risk(market_name)
        rm = RiskManager(
            max_position_pct=risk_config.max_position_pct,
            max_total_risk_pct=risk_config.max_total_risk_pct,
            stop_loss_pct=risk_config.stop_loss_pct,
            take_profit_pct=risk_config.take_profit_pct,
            max_positions=risk_config.max_positions,
            max_daily_trades=risk_config.max_daily_trades,
            trailing_stop=risk_config.trailing_stop,
            trailing_stop_pct=risk_config.trailing_stop_pct,
        )
        market_engines[market_name] = (de, rm)

    logger.info("開始虛擬交易循環... (Ctrl+C 停止)")

    try:
        cycle = 0
        while True:
            cycle += 1
            logger.info(f"\n--- 交易循環 #{cycle} ---")

            # 收集各市場資料並產生決策
            if markets.crypto.enabled and "crypto" in market_engines:
                decision_engine, risk_manager = market_engines["crypto"]
                executor = Executor(exchange, risk_manager, decision_engine)
                try:
                    collector = CryptoCollector(
                        exchange_id=markets.crypto.exchange, sandbox=True
                    )
                    for symbol in markets.crypto.symbols:
                        df = collector.fetch_ohlcv(
                            symbol=symbol,
                            timeframe=markets.crypto.timeframe,
                            limit=100,
                        )
                        if not df.empty:
                            df = indicator_engine.add_all(df)
                            exchange.set_price(symbol, df["close"].iloc[-1])

                            # 檢查止損
                            executor.check_stop_conditions(symbol)

                            # 產生決策
                            decision = decision_engine.make_decision(df, symbol)
                            result = executor.execute_decision(decision, symbol, "crypto")
                            if result:
                                storage.record_trade(
                                    symbol=symbol,
                                    market="crypto",
                                    side=result["action"].lower(),
                                    quantity=result["quantity"],
                                    price=result["price"],
                                    commission=result.get("commission", 0),
                                    pnl=result.get("pnl", 0),
                                )
                except Exception as e:
                    logger.error(f"Crypto trading error: {e}")

            if markets.us_stock.enabled and "us_stock" in market_engines:
                decision_engine, risk_manager = market_engines["us_stock"]
                executor = Executor(exchange, risk_manager, decision_engine)
                try:
                    collector = StockCollector()
                    for symbol in markets.us_stock.symbols:
                        df = collector.fetch_ohlcv(symbol=symbol, limit=100)
                        if not df.empty:
                            df = indicator_engine.add_all(df)
                            exchange.set_price(symbol, df["close"].iloc[-1])
                            executor.check_stop_conditions(symbol)
                            decision = decision_engine.make_decision(df, symbol)
                            executor.execute_decision(decision, symbol, "us_stock")
                except Exception as e:
                    logger.error(f"US stock trading error: {e}")

            # 顯示帳戶狀態
            account = exchange.get_account()
            positions = exchange.get_positions()
            logger.info(
                f"💰 帳戶: 總資產={account.total_equity:,.2f}, "
                f"現金={account.cash:,.2f}, "
                f"持倉={account.positions_value:,.2f}, "
                f"未實現損益={account.unrealized_pnl:,.2f}"
            )
            if positions:
                for sym, pos in positions.items():
                    logger.info(
                        f"  📌 {sym}: qty={pos['quantity']:.6f}, "
                        f"avg={pos['avg_price']:.2f}, "
                        f"pnl={pos['unrealized_pnl']:.2f} ({pos['pnl_pct']:.1f}%)"
                    )

            # 儲存績效快照
            storage.save_performance(
                total_equity=account.total_equity,
                cash=account.cash,
                positions_value=account.positions_value,
                daily_pnl=account.unrealized_pnl,
                total_pnl=account.realized_pnl + account.unrealized_pnl,
            )

            # 等待下一個循環
            logger.info("等待 60 秒後進入下一個循環...")
            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("\n⛔ 交易已停止")
        account = exchange.get_account()
        logger.info(
            f"最終資產: {account.total_equity:,.2f} "
            f"(報酬率: {(account.total_equity / settings.config.general.initial_capital - 1):.2%})"
        )


def main():
    """主程式進入點"""
    parser = argparse.ArgumentParser(description="自主交易機器人 (Autonomous Trader)")
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "live"],
        default="paper",
        help="運行模式: backtest=回測, paper=虛擬交易, live=實盤",
    )
    parser.add_argument("--config", default=None, help="設定檔路徑")
    args = parser.parse_args()

    # 初始化
    settings = Settings(args.config)
    project_root = Path(__file__).parent.parent
    setup_logger(
        log_level=settings.config.general.log_level,
        log_dir=project_root / "logs",
    )

    logger.info("🤖 自主交易機器人 v0.2.0")
    logger.info(f"模式: {args.mode}")
    enabled_markets = settings.get_enabled_markets()
    logger.info(f"啟用市場: {enabled_markets}")
    for m in enabled_markets:
        strats = settings.get_enabled_strategies(m)
        logger.info(f"  📋 {m} 策略: {list(strats.keys())}")

    if args.mode == "backtest":
        run_backtest(settings)
    elif args.mode == "paper":
        run_paper_trading(settings)
    elif args.mode == "live":
        logger.warning("⚠️ 實盤模式尚未啟用，請使用 paper 模式測試")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
