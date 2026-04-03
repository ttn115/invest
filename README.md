# 自主交易機器人 (Autonomous Trader) 🤖

一個模組化的自主交易機器人，支援**加密幣**、**美股**和**台股**的智能投資。

## 🌟 特色

- **自主決策引擎** — 多策略加權投票 + 市場狀態偵測 + 自適應策略切換
- **四大策略** — SMA 交叉、RSI、MACD、布林通道
- **三大市場** — 加密幣 (CCXT) / 美股 (Alpaca) / 台股 (Shioaji)
- **完整風控** — 止損/止盈、追蹤止損、倉位管理、投資組合控制
- **回測系統** — 事件驅動回測 + Sharpe, Sortino, MDD 等績效分析
- **虛擬交易** — Paper Exchange 模擬器，零風險測試

## 🚀 快速開始

```bash
# 1. 建立虛擬環境
python -m venv .venv
.venv\Scripts\activate       # Windows
source .venv/bin/activate    # Mac/Linux

# 2. 安裝依賴
pip install -e ".[dev]"

# 3. 設定 API Keys
cp .env.example .env
# 編輯 .env 填入您的 API Keys

# 4. 執行回測
python -m src.main --mode backtest

# 5. 虛擬交易
python -m src.main --mode paper
```

## 📁 專案結構

```
src/
├── config/settings.py        # 設定管理
├── data/
│   ├── collector.py          # 資料收集 (CCXT/yfinance/Shioaji)
│   ├── storage.py            # SQLite 儲存
│   └── indicators.py         # 技術指標
├── strategy/
│   ├── base.py               # 策略基礎類別
│   ├── sma_crossover.py      # SMA 交叉
│   ├── rsi_strategy.py       # RSI
│   ├── macd_strategy.py      # MACD
│   └── bollinger_strategy.py # 布林通道
├── risk/manager.py           # 風險管理
├── engine/
│   ├── decision.py           # 自主決策引擎
│   ├── backtester.py         # 回測引擎
│   └── executor.py           # 訂單執行
├── exchange/base.py          # 交易所層
├── monitor/logger.py         # 日誌系統
└── main.py                   # 主程式
```

## 📊 支援策略

| 策略 | 類型 | 適用市場 |
|------|------|----------|
| SMA 交叉 | 趨勢追蹤 | 趨勢盤 |
| RSI | 均值回歸 | 盤整盤 |
| MACD | 動量 | 趨勢盤 |
| 布林通道 | 波動率 | 盤整/突破 |

## ⚠️ 免責聲明

本系統僅供學習和研究用途。投資有風險，請謹慎操作。


Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 1; python scripts/top_20_scanner.py --loop --interval 3600

# 單次掃描
python scripts/tw_stock_scanner.py

# 持續掃描 (每30分鐘)
python scripts/tw_stock_scanner.py --loop

