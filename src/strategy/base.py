"""
策略基礎類別 (Base Strategy)

定義所有交易策略必須遵循的介面。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd


class SignalType(Enum):
    """交易信號類型"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    """
    交易信號

    Attributes:
        signal_type: 信號類型 (BUY / SELL / HOLD)
        strength: 信號強度 (0.0 ~ 1.0)
        price: 建議價格
        symbol: 交易標的
        strategy_name: 產生此信號的策略名稱
        reason: 信號產生原因
        metadata: 附加資訊
    """
    signal_type: SignalType
    strength: float = 0.5
    price: float = 0.0
    symbol: str = ""
    strategy_name: str = ""
    reason: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def is_buy(self) -> bool:
        return self.signal_type == SignalType.BUY

    @property
    def is_sell(self) -> bool:
        return self.signal_type == SignalType.SELL

    @property
    def is_hold(self) -> bool:
        return self.signal_type == SignalType.HOLD

    def __repr__(self) -> str:
        return (
            f"Signal({self.signal_type.value}, strength={self.strength:.2f}, "
            f"strategy={self.strategy_name}, reason={self.reason})"
        )


class BaseStrategy(ABC):
    """
    策略基礎類別

    所有策略必須實作:
    - generate_signal(): 根據資料產生交易信號
    - get_params(): 返回策略參數
    """

    def __init__(self, name: str = "", params: Optional[dict] = None):
        """
        Args:
            name: 策略名稱
            params: 策略參數
        """
        self.name = name or self.__class__.__name__
        self.params = params or {}
        self._performance_score: float = 0.5  # 策略績效分數 (0~1)
        self._total_trades: int = 0
        self._winning_trades: int = 0

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, symbol: str = "") -> Signal:
        """
        根據市場資料產生交易信號

        Args:
            df: OHLCV + 指標的 DataFrame
            symbol: 交易標的

        Returns:
            Signal 交易信號
        """
        pass

    def get_params(self) -> dict:
        """返回策略參數"""
        return self.params.copy()

    def update_performance(self, is_win: bool):
        """更新策略績效"""
        self._total_trades += 1
        if is_win:
            self._winning_trades += 1
        # 指數加權移動平均更新分數
        result = 1.0 if is_win else 0.0
        alpha = 0.1  # 學習率
        self._performance_score = (1 - alpha) * self._performance_score + alpha * result

    @property
    def win_rate(self) -> float:
        """勝率"""
        if self._total_trades == 0:
            return 0.0
        return self._winning_trades / self._total_trades

    @property
    def performance_score(self) -> float:
        """績效分數 (0~1)"""
        return self._performance_score

    def __repr__(self) -> str:
        return f"{self.name}(params={self.params}, score={self._performance_score:.2f})"
