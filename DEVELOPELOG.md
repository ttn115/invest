# 開發日誌 (Development Log)

## 2026-02-28 - v0.1.0 初始建立

### 架構決策
- 採用模組化架構，各模組可獨立測試和替換
- 使用 Pydantic 做設定驗證，確保參數正確性
- 策略引擎使用抽象基礎類別 (ABC)，方便擴充新策略
- 自主決策引擎採用「多策略加權投票 + 市場狀態偵測 + 自適應切換」架構

### 技術選型理由
- **CCXT**: 統一 100+ 交易所介面，降低未來切換成本
- **yfinance**: 台股/美股免費歷史資料來源
- **Shioaji**: 台灣最成熟的程式交易 API，社群最大
- **pandas-ta**: 130+ 指標，Pandas 原生整合
- **SQLite**: 零設定、輕量，適合初期快速開發
- **Loguru**: 比 logging 更簡潔的日誌管理

### 已建立的模組
```
src/config/settings.py      - 設定管理器
src/data/collector.py        - 資料收集器 (3 市場)
src/data/storage.py          - SQLite 資料存取
src/data/indicators.py       - 技術指標計算
src/strategy/base.py         - 策略基礎類別
src/strategy/sma_crossover.py - SMA 交叉策略
src/strategy/rsi_strategy.py  - RSI 策略
src/strategy/macd_strategy.py - MACD 策略
src/strategy/bollinger_strategy.py - 布林策略
src/risk/manager.py          - 風險管理器
src/engine/decision.py       - 自主決策引擎
src/engine/backtester.py     - 回測引擎
src/engine/executor.py       - 訂單執行器
src/exchange/base.py         - 交易所層 (PaperExchange)
src/monitor/logger.py        - 日誌系統
src/main.py                  - 主程式入口
```

## 2026-03-03 - v0.3.0 營利追蹤與風控
- **Signal Tracker**: 實作信號紀錄與自動回查驗證機制 (1h/4h/24h)
- **SELL 信號**: 增加賣出警報與超買 RSI 警示
- **ATR 止損**: 加入 ATR 動態止損邏輯，適應市場波動
- **Volume Filter**: 實作成交量確認濾網，減少低量噪音

## 2026-03-03 - v0.3.1 參數優化
- **數據分析**: 基於首批 24 筆信號診斷虧損原因
- **Panic Buy**: 停用 RSI<15 的抄底機制，避免空頭市場接刀
- **門檻調優**: `min_agreement` 從 0.40 提高到 0.55
- **周期同步**: 統一 RSI(14) 與 SMA(7/25) 參數，減少雜訊

## 2026-03-04 - v0.4.0 多角度市場分析
- **Market Context**: 新增 4 角度分析模型 (時框、主導性、階段、宏觀)
- **All-in-One Report**: 重新設計 Telegram 報告，整合背景與即時信號
- **1h PnL Fix**: 修正營利統計計算 bug，確保基準一致

### 已建立的模組
```
src/config/settings.py      - 設定管理器
src/data/collector.py        - 資料收集器 (3 市場)
src/data/storage.py          - SQLite 資料存取
src/data/indicators.py       - 技術指標計算
src/strategy/base.py         - 策略基礎類別
src/strategy/sma_crossover.py - SMA 交叉策略
src/strategy/rsi_strategy.py  - RSI 策略
...
src/monitor/signal_tracker.py - 信號追蹤與營利分析
src/analysis/market_context.py - 多角度市場分析引擎 [NEW]
src/main.py                  - 主程式入口
```

## 2026-03-16 - v0.6.2 CMD 即時報告
- **CMD 輸出**: 將 Telegram 報告同步 print 至終端機，方便本地監控
- **生成邏輯外提**: `generate_full_report()` / `generate_tw_report()` 移至 `if notifier.enabled` 外，確保無論 Telegram 設定如何，CMD 都能看到完整報告

## 2026-03-19 - v0.6.4 多面向資訊看板

### 設計思路
v0.6.3 增加信號敏感度後，發現投票系統本身會壓縮資訊，導致使用者無法看到各面向的獨立狀態。
核心改動：**新增獨立看板，每個面向的指標直接呈現原始值 + 方向，不做 BUY/SELL 判斷**。

- 原有投票系統保留不動 (自動交易用)
- 新增 `generate_dimension_report()` 獨立於投票系統外
- 收集 12 個原始指標欄位：RSI、MACD Hist (now/prev)、SMA fast/slow、SMA slope、BB %B、BB Width (now/prev)、Funding Rate
- 每個面向用 emoji 方向箭頭 (🔼🔽⏸️) 直覺呈現，附帶中文描述

## 2026-03-19 - v0.6.3 短線信號敏感度提升

### 設計思路
經過盲點分析，發現系統在 1h 級別幾乎全部回傳 HOLD，原因是多層過嚴的過濾。
三項改動刻意增加信號敏感度，接受更多假信號作為代價，後續根據績效報告再調整：

1. **min_agreement 0.55→0.40**: 回到 v0.3.0 門檻值。現有 5 層濾網 (FR/BTC Regime/Volume/SOL) 已提供足夠保護，不需靠共識門檻過濾
2. **RSI 14→7 (crypto only)**: RSI 14 在 1h 太鈍，40~60 之間徘徊不觸發。RSI 7 能在 7~10 小時內捕捉超跌/超買
3. **MACD 柱狀圖弱信號**: 原本偵測到柱狀圖動量變化卻回傳 HOLD，現在產生 BUY/SELL 弱信號 (strength 0.3~0.4)

### 待辦事項
- [ ] 實作 Web Dashboard (v0.5.0)
- [ ] 模擬實盤下單模擬 (Binance Testnet)
- [ ] AI/ML 策略研發 (v0.6.0)
