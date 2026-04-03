"""
設定管理器 (Settings Manager)

負責讀取 config.yaml 和 .env，提供統一的設定存取介面。
使用 Pydantic 進行設定驗證。

支援「市場專屬策略覆蓋 (Market-Specific Overrides)」：
每個市場可選擇性覆蓋全域的策略 / 風控 / 決策引擎參數。
未覆蓋的欄位自動回退到全域預設值 (deep merge)。
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


# === 設定模型 (Pydantic Models) ===

class StrategyParams(BaseModel):
    """策略參數"""
    enabled: bool = True
    weight: float = 1.0
    params: dict = Field(default_factory=dict)


class BaseStrategyConfig(BaseModel):
    enabled: bool = True
    weight: float = 1.0
    params: dict = Field(default_factory=dict)
    
class SentimentConfig(BaseStrategyConfig):
    params: dict = Field(default_factory=lambda: {
        "mode": "contrarian", 
        "fear_buy_threshold": 25, 
        "greed_sell_threshold": 75,
        "neutral_low": 40,
        "neutral_high": 60
    })

class FundingRateConfig(BaseStrategyConfig):
    params: dict = Field(default_factory=lambda: {
        "mode": "signal",
        "high_threshold": 0.00015,
        "low_threshold": -0.00015
    })

class BTCRegimeConfig(BaseStrategyConfig):
    params: dict = Field(default_factory=lambda: {
        "sma_period": 50
    })

class StrategiesConfig(BaseModel):
    """策略引擎設定"""
    sma_crossover: BaseStrategyConfig = Field(
        default_factory=lambda: BaseStrategyConfig(
            params={"fast_period": 10, "slow_period": 30}
        )
    )
    rsi: BaseStrategyConfig = Field(
        default_factory=lambda: BaseStrategyConfig(
            params={"period": 14, "oversold": 30, "overbought": 70}
        )
    )
    macd: BaseStrategyConfig = Field(
        default_factory=lambda: BaseStrategyConfig(
            params={"fast": 12, "slow": 26, "signal": 9}
        )
    )
    bollinger: BaseStrategyConfig = Field(
        default_factory=lambda: BaseStrategyConfig(
            weight=0.8,
            params={"period": 20, "std_dev": 2.0},
        )
    )
    sentiment: SentimentConfig = Field(default_factory=lambda: SentimentConfig(enabled=False, weight=0.6))
    funding_rate: FundingRateConfig = Field(default_factory=lambda: FundingRateConfig(enabled=False, weight=0.6))
    btc_regime: BTCRegimeConfig = Field(default_factory=lambda: BTCRegimeConfig(enabled=False, weight=1.0))


class RiskConfig(BaseModel):
    """風險管理設定"""
    max_position_pct: float = 0.10
    max_total_risk_pct: float = 0.30
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15
    max_positions: int = 10
    max_daily_trades: int = 20
    trailing_stop: bool = True
    trailing_stop_pct: float = 0.03


class PanicBuyOverrideConfig(BaseModel):
    """恐慌抄底覆蓋設定 (無視大盤濾網攔截)"""
    enabled: bool = False
    rsi_threshold: float = 15.0
    sentiment_threshold: float = 15.0

class DecisionEngineConfig(BaseModel):
    """自主決策引擎設定"""
    voting_method: str = "weighted"
    min_agreement: float = 0.6
    market_state_detection: bool = True
    auto_rebalance: bool = True
    rebalance_interval: str = "24h"
    panic_buy_override: PanicBuyOverrideConfig = Field(default_factory=PanicBuyOverrideConfig)


# --- 市場設定 (每個市場可選擇性覆蓋全域策略/風控/決策) ---

class CryptoMarketConfig(BaseModel):
    """加密幣市場設定"""
    enabled: bool = True
    exchange: str = "binance"
    sandbox: bool = True
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    timeframe: str = "1h"
    # 市場專屬覆蓋 (Optional, 未設定則使用全域預設)
    strategies: Optional[dict] = None
    risk: Optional[dict] = None
    decision_engine: Optional[dict] = None


class USStockMarketConfig(BaseModel):
    """美股市場設定"""
    enabled: bool = True
    broker: str = "alpaca"
    paper_trading: bool = True
    symbols: list[str] = Field(default_factory=lambda: ["AAPL", "TSLA", "NVDA"])
    timeframe: str = "1d"
    # 市場專屬覆蓋
    strategies: Optional[dict] = None
    risk: Optional[dict] = None
    decision_engine: Optional[dict] = None


class TWStockMarketConfig(BaseModel):
    """台股市場設定"""
    enabled: bool = False
    broker: str = "shioaji"
    simulation: bool = True
    symbols: list[str] = Field(default_factory=lambda: ["2330", "2317"])
    timeframe: str = "1d"
    # 市場專屬覆蓋
    strategies: Optional[dict] = None
    risk: Optional[dict] = None
    decision_engine: Optional[dict] = None


class MarketsConfig(BaseModel):
    """所有市場設定"""
    crypto: CryptoMarketConfig = Field(default_factory=CryptoMarketConfig)
    us_stock: USStockMarketConfig = Field(default_factory=USStockMarketConfig)
    tw_stock: TWStockMarketConfig = Field(default_factory=TWStockMarketConfig)


class BacktestConfig(BaseModel):
    """回測設定"""
    start_date: str = "2025-01-01"
    end_date: str = "2025-12-31"
    commission_pct: float = 0.001
    slippage_pct: float = 0.0005


class MonitorConfig(BaseModel):
    """監控設定"""
    dashboard_port: int = 5000
    enable_alerts: bool = True
    alert_on_trade: bool = True
    alert_on_loss_pct: float = 0.03


class GeneralConfig(BaseModel):
    """全域設定"""
    trading_mode: str = "paper"
    base_currency: str = "USDT"
    initial_capital: float = 100000
    log_level: str = "INFO"


class AppConfig(BaseModel):
    """應用程式完整設定"""
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    markets: MarketsConfig = Field(default_factory=MarketsConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    decision_engine: DecisionEngineConfig = Field(default_factory=DecisionEngineConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)


# === Deep Merge 工具函式 ===

def _deep_merge(base: dict, override: dict) -> dict:
    """
    深度合併兩個字典。override 的值會覆蓋 base 中對應的鍵值。
    如果兩邊的值都是 dict，則遞迴合併。

    Args:
        base: 基礎字典 (全域預設)
        override: 覆蓋字典 (市場專屬)

    Returns:
        合併後的新字典
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# === 設定管理器 ===

class Settings:
    """
    統一設定管理器

    Usage:
        settings = Settings()
        print(settings.config.general.trading_mode)
        print(settings.api_key("ALPACA_API_KEY"))

        # 取得市場專屬的合併設定
        crypto_strategies = settings.get_market_strategies("crypto")
        crypto_risk = settings.get_market_risk("crypto")
    """

    _instance: Optional[Settings] = None

    def __init__(self, config_path: str | Path | None = None):
        """初始化設定"""
        # 載入 .env
        project_root = Path(__file__).parent.parent.parent
        env_path = project_root / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        else:
            # 嘗試從 .env.example 提示
            load_dotenv(project_root / ".env.example")

        # 載入 config.yaml
        if config_path is None:
            config_path = project_root / "config.yaml"

        config_data = {}
        config_path = Path(config_path)
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}

        self.config = AppConfig(**config_data)

    @classmethod
    def get_instance(cls, config_path: str | Path | None = None) -> Settings:
        """取得單例設定 (Singleton)"""
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reset(cls):
        """重設單例 (主要用於測試)"""
        cls._instance = None

    @staticmethod
    def api_key(key_name: str) -> str:
        """從環境變數取得 API Key"""
        value = os.getenv(key_name, "")
        if not value or value.startswith("your_"):
            return ""
        return value

    @property
    def is_paper_mode(self) -> bool:
        """是否為虛擬交易模式"""
        return self.config.general.trading_mode == "paper"

    @property
    def is_live_mode(self) -> bool:
        """是否為實盤交易模式"""
        return self.config.general.trading_mode == "live"

    def get_enabled_markets(self) -> list[str]:
        """取得已啟用的市場列表"""
        markets = []
        if self.config.markets.crypto.enabled:
            markets.append("crypto")
        if self.config.markets.us_stock.enabled:
            markets.append("us_stock")
        if self.config.markets.tw_stock.enabled:
            markets.append("tw_stock")
        return markets

    def get_enabled_strategies(self, market_name: str | None = None) -> dict[str, BaseStrategyConfig]:
        """
        取得已啟用的策略列表。

        Args:
            market_name: 指定市場名稱 (crypto / us_stock / tw_stock)。
                         若指定，則回傳合併後的市場專屬策略設定。
                         若為 None，則回傳全域策略設定。

        Returns:
            dict[策略名稱, BaseStrategyConfig]
        """
        strategies_config = self.get_market_strategies(market_name) if market_name else self.config.strategies
        filtered = {}
        for name, strategy in strategies_config:
            if isinstance(strategy, BaseStrategyConfig) and strategy.enabled:
                filtered[name] = strategy
        
        # Special handling for optional strategies that might be added via dict override
        # This part ensures that if a strategy is enabled in the config_data (which is used to build strategies_config),
        # it is included, even if it's not a direct field of StrategiesConfig or if its type is not BaseStrategyConfig
        # (e.g., if it's a specific config like SentimentConfig).
        # The `strategies_config` object itself should already contain the correctly typed and merged strategies.
        # This block might be redundant if `strategies_config` is always fully populated and correctly typed.
        # However, following the instruction to include it.
        config_dict = strategies_config.model_dump()
        if "sentiment" in config_dict and config_dict["sentiment"]["enabled"]:
            if "sentiment" not in filtered:
                filtered["sentiment"] = getattr(strategies_config, "sentiment")
        if "funding_rate" in config_dict and config_dict["funding_rate"]["enabled"]:
            if "funding_rate" not in filtered:
                filtered["funding_rate"] = getattr(strategies_config, "funding_rate")
        if "btc_regime" in config_dict and config_dict["btc_regime"]["enabled"]:
            if "btc_regime" not in filtered:
                filtered["btc_regime"] = getattr(strategies_config, "btc_regime")
                
        return filtered

    def _get_market_obj(self, market_name: str):
        """取得市場設定物件"""
        market_map = {
            "crypto": self.config.markets.crypto,
            "us_stock": self.config.markets.us_stock,
            "tw_stock": self.config.markets.tw_stock,
        }
        market = market_map.get(market_name)
        if market is None:
            raise ValueError(f"Unknown market: {market_name}. Valid: {list(market_map.keys())}")
        return market

    def get_market_strategies(self, market_name: str | None = None) -> StrategiesConfig:
        """
        取得合併後的策略設定 (全域預設 + 市場專屬覆蓋)。

        流程：將全域 strategies 轉成 dict，再用市場的 strategies override 做 deep merge，
        最後重新建構一個 StrategiesConfig 物件。

        Args:
            market_name: 市場名稱 (crypto / us_stock / tw_stock)。若 None 則回傳全域。

        Returns:
            StrategiesConfig 合併後的策略設定
        """
        if market_name is None:
            return self.config.strategies

        market = self._get_market_obj(market_name)
        override = market.strategies
        if override is None:
            return self.config.strategies

        # Deep merge: 全域設定 + 市場覆蓋
        base_dict = self.config.strategies.model_dump()
        merged = _deep_merge(base_dict, override)
        return StrategiesConfig(**merged)

    def get_market_risk(self, market_name: str | None = None) -> RiskConfig:
        """
        取得合併後的風控設定 (全域預設 + 市場專屬覆蓋)。

        Args:
            market_name: 市場名稱。若 None 則回傳全域。

        Returns:
            RiskConfig 合併後的風控設定
        """
        if market_name is None:
            return self.config.risk

        market = self._get_market_obj(market_name)
        override = market.risk
        if override is None:
            return self.config.risk

        base_dict = self.config.risk.model_dump()
        merged = _deep_merge(base_dict, override)
        return RiskConfig(**merged)

    def get_market_decision_engine(self, market_name: str | None = None) -> DecisionEngineConfig:
        """
        取得合併後的決策引擎設定 (全域預設 + 市場專屬覆蓋)。

        Args:
            market_name: 市場名稱。若 None 則回傳全域。

        Returns:
            DecisionEngineConfig 合併後的決策引擎設定
        """
        if market_name is None:
            return self.config.decision_engine

        market = self._get_market_obj(market_name)
        override = market.decision_engine
        if override is None:
            return self.config.decision_engine

        base_dict = self.config.decision_engine.model_dump()
        merged = _deep_merge(base_dict, override)
        return DecisionEngineConfig(**merged)
