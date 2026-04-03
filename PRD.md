# 自主交易機器人 (Autonomous Trader)

## 產品需求文件 (PRD)

### 版本: 0.6.4
### 日期: 2026-03-19

---

### 1. 產品概述

一個模組化的自主交易機器人，支援加密幣、美股和台股的智能投資。系統整合多策略決策引擎、風險管理和虛擬交易模擬器，從虛擬環境測試開始，最終可遷移到真實交易 API。

### 2. 核心功能

| 功能 | 說明 | 狀態 |
|------|------|------|
| 多市場資料收集 | CCXT (加密幣) / yfinance (美股) / Shioaji (台股) | ✅ v0.1.0 |
| 技術指標引擎 | SMA, EMA, RSI, MACD, Bollinger, ATR, Stochastic | ✅ v0.1.0 |
| 策略引擎 | SMA Crossover, RSI, MACD, Bollinger 四策略 | ✅ v0.1.0 |
| 自主決策引擎 | 多策略投票 + 市場狀態偵測 + 自適應切換 | ✅ v0.1.0 |
| 風險管理 | 止損/止盈、倉位管理、追蹤止損、投組風控 | ✅ v0.1.0 |
| 虛擬交易所 | Paper Exchange 模擬器 (滑點/手續費) | ✅ v0.1.0 |
| 回測引擎 | 事件驅動回測 + Sharpe/Sortino/MDD/Win Rate 績效分析 | ✅ v0.1.0 |
| SQLite 儲存 | OHLCV 快取、交易紀錄、績效追蹤 | ✅ v0.1.0 |
| Telegram 通知 | 每小時掃描 + 中文總結 + BUY/SELL 警報推送 | ✅ v0.2.0 |
| 情緒/資金費率策略 | Fear & Greed Index + Funding Rate 濾網 | ✅ v0.2.0 |
| 大盤環境濾網 | BTC SMA50 趨勢判斷 | ✅ v0.2.0 |
| 歷史績效追蹤 | 信號記錄 + 1h 回查 + 營利統計 (統一使用 1h PnL) | ✅ v0.3.0 |
| 多角度市場分析 | 1h/4h/1d 周期確認 + BTC 主導性 + 市場階段 + 宏觀 (DXY/FNG) | ✅ v0.4.0 |
| 自適應參數調優 | RSI 14 / SMA 7-25 / 信心門檻 0.55 (數據驅動) | ✅ v0.4.0 |
| 全能信號報告 | Telegram 一站式報告 (背景 + 信號 + 績效 + 百科) | ✅ v0.4.0 |
| SELL 信號通知 | 賣出警報推送 + RSI 超買警告 | ✅ v0.3.0 |
| 成交量確認濾網 | 低量信號否決 | ✅ v0.3.0 |
| ATR 動態止損 | 依波動率自動調整止損距離 | ✅ v0.3.0 |
| MACD 動量弱信號 | 柱狀圖翻正/翻負/加速產生 BUY/SELL 弱信號 | ✅ v0.6.3 |
| 短線信號敏感度 | RSI 7 + 門檻 0.40，提升 1h 短線偵測能力 | ✅ v0.6.3 |
| 多面向資訊看板 | 5 面向獨立呈現 (動能/趨勢/波動/情緒/大盤)，不做合併判斷 | ✅ v0.6.4 |
| Web Dashboard | Flask + Chart.js 即時監控 | 🔲 v0.7.0 |
| AI/ML 策略 | LSTM 預測、PPO 強化學習 | 🔲 v0.6.0 |

### 3. 支援市場

- **加密幣**: 透過 CCXT 支援 100+ 交易所 (預設 Binance Testnet)
- **美股**: 透過 Alpaca API (免費 Paper Trading, $100K 虛擬資金)
- **台股**: 透過 Shioaji 永豐金 API (需永豐證券帳戶) / yfinance 歷史資料

### 4. 技術架構

```
Config → Data Collector → Indicator Engine → Strategy Engine
→ Decision Engine (+ Filters) → Risk Manager → Order Executor → Exchange
            ↓                       ↕
Market Context Analyzer (v0.4.0)  Signal Tracker (CSV)
            ↓                       ↓
         Telegram Notifier (All-in-One Report)
```

### 5. 非功能需求

- Python 3.11+
- SQLite 零設定資料庫
- 模組化架構，各模組可獨立測試
- Paper / Live 模式無縫切換
