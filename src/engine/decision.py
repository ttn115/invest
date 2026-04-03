"""
自主決策引擎 (Autonomous Decision Engine)

核心「自主意識」模組：
- 多策略加權投票系統
- 市場狀態偵測 (趨勢/盤整/高波動)
- 自適應策略切換
- 策略績效追蹤與動態權重調整
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from src.strategy.base import BaseStrategy, Signal, SignalType


class MarketState(Enum):
    """市場狀態"""
    TRENDING_UP = "trending_up"        # 上升趨勢
    TRENDING_DOWN = "trending_down"    # 下降趨勢
    RANGING = "ranging"                # 盤整
    HIGH_VOLATILITY = "high_volatility"  # 高波動


@dataclass
class DecisionResult:
    """決策結果"""
    final_signal: SignalType
    confidence: float  # 綜合信心度 (0~1)
    strategy_signals: dict[str, Signal]  # 各策略原始信號
    market_state: MarketState
    vote_summary: dict  # 投票摘要
    reason: str = ""

    def __repr__(self) -> str:
        return (
            f"Decision({self.final_signal.value}, confidence={self.confidence:.2f}, "
            f"market={self.market_state.value})"
        )


class DecisionEngine:
    """
    自主決策引擎

    透過多策略投票和市場狀態分析，做出最終交易決策。
    根據各策略歷史績效自動調整權重，實現自適應策略切換。
    """

    def __init__(
        self,
        strategies: dict[str, BaseStrategy] | None = None,
        weights: dict[str, float] | None = None,
        filters: dict[str, BaseStrategy] | None = None,
        voting_method: str = "weighted",
        min_agreement: float = 0.6,
        panic_buy_override=None,
    ):
        """
        Args:
            strategies: 策略名稱 → 策略實例
            weights: 策略名稱 → 權重
            voting_method: "weighted" (加權) / "majority" (多數決)
            min_agreement: 最低策略同意比例 (0~1)
        """
        self.strategies = strategies or {}
        self.weights = weights or {name: 1.0 for name in self.strategies}
        self.voting_method = voting_method
        self.min_agreement = min_agreement
        self.filters = filters or {}  # 獨立的濾網集合
        self.panic_buy_override = panic_buy_override

        # 各市場狀態下的策略偏好
        self._state_preferences = {
            MarketState.TRENDING_UP: {"SMA_Crossover": 1.5, "MACD": 1.3, "RSI": 0.7, "Bollinger": 0.8},
            MarketState.TRENDING_DOWN: {"SMA_Crossover": 1.3, "MACD": 1.5, "RSI": 0.8, "Bollinger": 0.7},
            MarketState.RANGING: {"RSI": 1.5, "Bollinger": 1.5, "SMA_Crossover": 0.6, "MACD": 0.7},
            MarketState.HIGH_VOLATILITY: {"Bollinger": 1.3, "RSI": 1.0, "SMA_Crossover": 0.5, "MACD": 0.5},
        }

    def add_strategy(self, name: str, strategy: BaseStrategy, weight: float = 1.0):
        """新增策略"""
        self.strategies[name] = strategy
        self.weights[name] = weight

    def add_filter(self, name: str, filter_strategy: BaseStrategy):
        """新增濾網"""
        self.filters[name] = filter_strategy

    def detect_market_state(self, df: pd.DataFrame) -> MarketState:
        """
        偵測市場狀態

        使用 ADX (趨勢強度)、ATR (波動率)、價格與均線關係
        """
        if len(df) < 50:
            return MarketState.RANGING

        close = df["close"]

        # 計算簡易趨勢指標
        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()

        # 計算波動率 (近期標準差 / 均線)
        volatility = close.rolling(20).std().iloc[-1] / sma20.iloc[-1]

        # 高波動判斷 (年化波動率 > 80%)
        if volatility > 0.03:  # 日波動率 3% 以上
            logger.debug(f"Market state: HIGH_VOLATILITY (vol={volatility:.4f})")
            return MarketState.HIGH_VOLATILITY

        # 趨勢判斷
        current_price = close.iloc[-1]
        sma20_now = sma20.iloc[-1]
        sma50_now = sma50.iloc[-1]

        # 計算均線斜率
        sma20_slope = (sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] if len(sma20.dropna()) >= 5 else 0
        sma50_slope = (sma50.iloc[-1] - sma50.iloc[-10]) / sma50.iloc[-10] if len(sma50.dropna()) >= 10 else 0

        # 趨勢條件
        if current_price > sma20_now > sma50_now and sma20_slope > 0.005:
            logger.debug(f"Market state: TRENDING_UP (slope={sma20_slope:.4f})")
            return MarketState.TRENDING_UP

        if current_price < sma20_now < sma50_now and sma20_slope < -0.005:
            logger.debug(f"Market state: TRENDING_DOWN (slope={sma20_slope:.4f})")
            return MarketState.TRENDING_DOWN

        logger.debug(f"Market state: RANGING (slope={sma20_slope:.4f})")
        return MarketState.RANGING

    def make_decision(self, df: pd.DataFrame, symbol: str = "") -> DecisionResult:
        """
        做出交易決策

        流程:
        1. 偵測市場狀態
        2. 收集所有策略信號
        3. 根據市場狀態調整權重
        4. 加權投票
        5. 產生最終決策

        Args:
            df: OHLCV + 指標 DataFrame
            symbol: 交易標的

        Returns:
            DecisionResult 決策結果
        """
        # 1. 偵測市場狀態
        market_state = self.detect_market_state(df)

        # 2. 收集所有策略信號
        strategy_signals: dict[str, Signal] = {}
        for name, strategy in self.strategies.items():
            try:
                signal = strategy.generate_signal(df.copy(), symbol)
                strategy_signals[name] = signal
            except Exception as e:
                logger.error(f"Strategy {name} error: {e}")
                strategy_signals[name] = Signal(
                    signal_type=SignalType.HOLD,
                    strategy_name=name,
                    reason=f"Error: {e}",
                )

        if not strategy_signals:
            return DecisionResult(
                final_signal=SignalType.HOLD,
                confidence=0.0,
                strategy_signals={},
                market_state=market_state,
                vote_summary={},
                reason="No strategies available",
            )

        # 3. 計算調整後權重
        adjusted_weights = self._get_adjusted_weights(market_state)

        # 4. 投票
        if self.voting_method == "weighted":
            result = self._weighted_vote(strategy_signals, adjusted_weights, market_state)
        else:
            result = self._majority_vote(strategy_signals, market_state)

        # 5. 執行濾網檢查 (Veto機制)
        if result.final_signal == SignalType.BUY and self.filters:
            # 檢查是否觸發恐慌抄底覆蓋 (Panic Buy Override)
            override_triggered = False
            if self.panic_buy_override and self.panic_buy_override.enabled:
                current_rsi = df["RSI_14"].iloc[-1] if "RSI_14" in df.columns else (df["RSI_7"].iloc[-1] if "RSI_7" in df.columns else (df["RSI_5"].iloc[-1] if "RSI_5" in df.columns else 50))
                current_sentiment = df["sentiment_value"].iloc[-1] if "sentiment_value" in df.columns else 50
                
                if (current_rsi < self.panic_buy_override.rsi_threshold and 
                    current_sentiment < self.panic_buy_override.sentiment_threshold):
                    
                    override_triggered = True
                    logger.warning(f"🚨 [OVERRIDE] Panic Buy Triggered! (RSI={current_rsi:.1f}, Sentiment={current_sentiment:.1f})")
                    result.reason += " | Panic Buy Override Triggered (Ignored Filters)!"
            
            if not override_triggered:
                for filter_name, filter_strategy in self.filters.items():
                    try:
                        f_signal = filter_strategy.generate_signal(df.copy(), symbol)
                        # 濾網必須回傳 BUY 才能放行，其他視為否決
                        if f_signal.signal_type != SignalType.BUY:
                            logger.info(f"🚫 [Veto] Decision reversed to HOLD by filter: {filter_name}")
                            result.final_signal = SignalType.HOLD
                            result.reason += f" | Vetoed by {filter_name}: {f_signal.reason}"
                            break
                    except Exception as e:
                        logger.error(f"Filter {filter_name} error: {e}")
                        # 濾網出錯安全起見不否決，或可視為否決 (目前選擇不否決)

        logger.info(
            f"Decision for {symbol}: {result.final_signal.value} "
            f"(confidence={result.confidence:.2f}, market={market_state.value})"
        )
        return result

    def _get_adjusted_weights(self, market_state: MarketState) -> dict[str, float]:
        """根據市場狀態和策略績效調整權重"""
        adjusted = {}
        state_prefs = self._state_preferences.get(market_state, {})

        for name, base_weight in self.weights.items():
            # 基礎權重 × 市場狀態偏好 × 績效分數
            state_mult = state_prefs.get(name, 1.0)
            strategy = self.strategies.get(name)
            perf_mult = strategy.performance_score if strategy else 0.5

            adjusted[name] = base_weight * state_mult * (0.5 + perf_mult)

        # 正規化
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}

        return adjusted

    def _weighted_vote(
        self,
        signals: dict[str, Signal],
        weights: dict[str, float],
        market_state: MarketState,
    ) -> DecisionResult:
        """加權投票"""
        buy_score = 0.0
        sell_score = 0.0
        hold_score = 0.0

        for name, signal in signals.items():
            w = weights.get(name, 0.0)
            weighted = w * signal.strength

            if signal.is_buy:
                buy_score += weighted
            elif signal.is_sell:
                sell_score += weighted
            else:
                hold_score += weighted

        total_score = buy_score + sell_score + hold_score + 1e-9

        vote_summary = {
            "buy_score": buy_score,
            "sell_score": sell_score,
            "hold_score": hold_score,
            "weights": weights,
        }

        # 決定最終信號
        max_score = max(buy_score, sell_score, hold_score)

        if max_score == buy_score and (buy_score / total_score) >= self.min_agreement:
            final = SignalType.BUY
            confidence = buy_score / total_score
        elif max_score == sell_score and (sell_score / total_score) >= self.min_agreement:
            final = SignalType.SELL
            confidence = sell_score / total_score
        else:
            final = SignalType.HOLD
            confidence = hold_score / total_score

        # 信號理由
        signal_details = ", ".join(
            f"{name}={s.signal_type.value}({s.strength:.2f})"
            for name, s in signals.items()
        )
        reason = (
            f"Weighted vote: BUY={buy_score:.3f}, SELL={sell_score:.3f}, "
            f"HOLD={hold_score:.3f} | {signal_details}"
        )

        return DecisionResult(
            final_signal=final,
            confidence=confidence,
            strategy_signals=signals,
            market_state=market_state,
            vote_summary=vote_summary,
            reason=reason,
        )

    def _majority_vote(
        self, signals: dict[str, Signal], market_state: MarketState
    ) -> DecisionResult:
        """多數決投票"""
        counts = {SignalType.BUY: 0, SignalType.SELL: 0, SignalType.HOLD: 0}
        for signal in signals.values():
            counts[signal.signal_type] += 1

        total = len(signals)
        final = max(counts, key=counts.get)
        confidence = counts[final] / total

        if confidence < self.min_agreement:
            final = SignalType.HOLD
            confidence = counts[SignalType.HOLD] / total

        return DecisionResult(
            final_signal=final,
            confidence=confidence,
            strategy_signals=signals,
            market_state=market_state,
            vote_summary={k.value: v for k, v in counts.items()},
            reason=f"Majority vote: {counts}",
        )

    def update_strategy_performance(self, strategy_name: str, is_win: bool):
        """更新策略績效 (交易結果回饋)"""
        if strategy_name in self.strategies:
            self.strategies[strategy_name].update_performance(is_win)

    def auto_rebalance_weights(self):
        """自動再平衡策略權重 (根據績效)"""
        for name, strategy in self.strategies.items():
            # 績效好的策略加權，差的減權
            self.weights[name] = max(0.2, strategy.performance_score * 2)

        logger.info(f"Rebalanced weights: {self.weights}")
