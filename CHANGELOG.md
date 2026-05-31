# Changelog

## [0.6.8] - 2026-05-01

### Added (進階基本面與宏觀指標升級)
- **核心指標庫 (`FundamentalProfile`)**：
  - 新增 `peg_ratio` (本益成長比) 與 `forward_pe` (預估本益比)
  - 由 `yfinance` 取得資料，整合至估值評分系統
- **宏觀風險指標 (`us_stock_scanner.py`)**：
  - 新增抓取 `^SKEW` (黑天鵝指數)，反映期權市場對極端下行風險的定價
- **加密幣鏈上指標 (`top_20_scanner.py`)**：
  - 串接 DefiLlama API 抓取各協議 TVL (總鎖倉量)
- **報告面板擴充 (`report_writer.py`)**：
  - 美股/台股面板新增 `PEG` 與 `FCF` 欄位
  - 加密幣面板新增 `TVL` 欄位
  - 美股市場環境摘要新增 `SKEW` 指標
- **圓桌會議顧問升級 (`ROUND_TABLE_PROMPT.md`)**：
  - 塔勒布 (Taleb)：新增 SKEW > 140 的黑天鵝預警
  - Paul Graham：要求檢視加密幣 TVL 增長以過濾資金盤
  - 沃倫·巴菲特 (Buffett)：強制檢視 FCF (自由現金流) 與 Forward P/E
  - Peter Lynch：新增 PEG > 2 拒絕買入之嚴格紀律

---

## [0.6.7] - 2026-04-12

### Added (統一市場看板)

#### `scripts/report_writer.py` — 新增看板寫入工具
- `update_market_section(market, lines)` 更新 `data/market_dashboard.md` 中特定市場的 Markdown 區塊
- 支援 `'crypto'` / `'us'` / `'tw'` 三個市場
- 每次更新只替換對應區塊（HTML comment 標記），不影響其他市場
- 自動更新頂部「最後更新」時間戳記
- `build_crypto_lines()` / `build_us_lines()` / `build_tw_lines()` 格式化各市場的 Markdown 表格

#### `data/market_dashboard.md` — 三市場統一看板（自動生成）
- 結構：市場環境摘要 + 信號 Markdown 表格 + 績效行
- 加密幣：短線/長線雙信號 + SOL 有毒/黃金環境列表
- 美股：SPY / VIX / 10Y / 情緒 + 30 支觀測標的
- 台股：加權指數 / RSI / 量比 + 11 支觀測標的

#### 三個掃描器均已整合
- `scripts/top_20_scanner.py` — 加密幣掃描後更新 CRYPTO 區塊
- `scripts/us_stock_scanner.py` — 美股掃描後更新 US 區塊
- `scripts/tw_stock_scanner.py` — 台股掃描後更新 TW 區塊

---

## [0.6.6] - 2026-04-12

### Added (美股掃描器)

#### `scripts/us_stock_scanner.py` — 新增美股掃描腳本
- **30 支觀測標的**：跨 8 個板塊（科技/半導體/消費/金融/醫療/能源/工業/ETF），蒙格護城河優先選股
- **美股市場背景分析** (`UsMarketContextAnalyzer`)：
  - SPY 趨勢（SMA50 / SMA200 / RSI / Phase）
  - VIX 恐慌指數（LOW / NORMAL / ELEVATED / EXTREME 四級）
  - 10Y 殖利率環境（RISING / FALLING / STABLE）
  - 綜合市場情緒（BULLISH / FEAR / CAUTIOUS_OPTIMISM / NEUTRAL）
- **NYSE 交易時段判斷**（09:30–16:00 ET，含盤前/收盤後各一次分析）
- **完整繼承現有框架**：SOL 環境封鎖、Munger Filter（RECOVERY+RSI<25禁SELL）、信號去重、Telegram 推送
- **`--loop`** 持續掃描模式（每 30 分鐘）
- **`--symbols`** 自訂掃描標的
- **`--force`** 忽略交易時段限制（盤後手動分析用）

#### `config.yaml` — 美股策略設定補完
- 從「無覆蓋」補為明確的日線保守設定（SMA 10/50、RSI 14、MACD 12/26/9）
- broker 從 alpaca 改為 yfinance（免費，無需帳號）
- min_agreement = 0.55（與台股同級）

---

## [0.6.5] - 2026-04-12

### Changed (蒙格三刀 — 止血優先)

根據 574 筆歷史信號的統計分析（2026-01-30 ~ 2026-03-04），針對三個負期望值根源做手術：

#### 修正一：擴大有毒環境封鎖範圍 (`contextual_optimizer.py`)
- **`is_toxic` 新增弱毒層**：樣本 >= 20 且勝率 < 44% 且 avg_pnl < -0.05%（十進位 -0.0005）
- 新增封鎖：`BEAR|MIXED|FLAT`（156 筆，勝率 41%，avg -0.088%）
- 新增封鎖：`BEAR|ALT_SEASON|FLAT`（34 筆，勝率 38%，avg -0.201%）
- 原有封鎖 `BEAR|BTC_SEASON|FLAT` 仍保留（18.8% 勝率）
- 預計排除 190 個負期望信號

#### 修正二：RECOVERY 階段 RSI < 25 禁止 SELL (`top_20_scanner.py`)
- 新增 Munger Filter：RECOVERY 市場 + RSI < 25 → SELL 強制轉 HOLD
- 根本原因：2026-02-06 五筆最大虧損（-2.9% ~ -5.42%）全來自 RECOVERY + 極度超賣 SELL
- RSI 超賣（<25）本身是反彈訊號，在復甦階段賣出是方向矛盾

#### 修正三：無市場背景的信號不記錄 (`signal_tracker.py`)
- `record_signal()` 新增 context guard：`market_ctx is None` 或 `phase` 為空 → 跳過記錄
- 根本原因：33 筆空 context 信號勝率 30.3%、avg -0.342%（全樣本最差類別）

#### 即時生效：`sol_bias.json` 更新
- `blocked_contexts` 新增 `BEAR|ALT_SEASON|FLAT` 和 `BEAR|MIXED|FLAT`
- 下次掃描即生效，無需等待 SOL 重新分析

---

## [0.6.4] - 2026-03-19

### Added (多面向資訊看板)
- **多面向看板**: 每個幣種獨立顯示 5 個面向的原始指標數據，不經投票合併
  - 📈 動能：RSI 值 + MACD 柱狀圖方向 (翻正/翻負/加速/收縮)
  - 📐 趨勢：SMA 快慢線位置 + 價格相對位置 + 斜率
  - 📊 波動：Bollinger %B 位置 + 通道寬度變化
  - 😱 情緒/費率：Fear & Greed + 個幣 Funding Rate
  - 🌐 大盤：市場階段 + BTC/ALT 季 + 多周期共識 (共用)
- **CMD + Telegram 雙輸出**: 看板同時顯示於終端和推送
- **存檔**: 看板另存 `scripts/last_dimension_report.txt`

---

## [0.6.3] - 2026-03-19

### Changed (短線信號敏感度提升)
- **min_agreement**: 0.55 → 0.40，降低共識門檻讓較弱的組合信號通過
  - 搭配現有 5 層濾網 (Funding Rate / BTC Regime / Volume / SOL)，仍有足夠防護
- **RSI period (crypto)**: 14 → 7，1h 短線需要更高靈敏度捕捉 7~10 小時內的超跌/超買
- **MACD 柱狀圖弱信號**: 新增 4 種動量信號 (原本全部回傳 HOLD)
  - 柱狀圖翻正 → BUY (strength 0.4)
  - 柱狀圖翻負 → SELL (strength 0.4)
  - 柱狀圖正向加速 → BUY (strength 0.3)
  - 柱狀圖負向擴大 → SELL (strength 0.3)

---

## [0.6.2] - 2026-03-16

### Added
- **CMD 即時報告**: 每次掃描結果除了推送 Telegram 外，也同步 print 至終端機 (CMD)
  - `top_20_scanner.py`: 完整報告 (市場背景 + 信號 + 績效 + SOL + 名詞說明) 顯示在 CMD
  - `tw_stock_scanner.py`: 完整台股報告 (加權指數 + 法人 + 信號) 顯示在 CMD
  - 即使 Telegram 未開啟，CMD 也能看到報告

---

## [0.6.1] - 2026-03-04

### Changed
- **加密幣雙線報告**: 每個幣種同時顯示短線 (1h) 和長線 (1d) 信號
  - 新增「⚡ 短線信號」、「🏔️ 長線趨勢」、「📋 雙線綜合一覽」三區塊
  - 短長線綜合標籤: 🟢🟢 短多長多 / ⚠️ 短多長空 / 🔴🔴 短空長空
  - 用戶可一稀看清短線操作機會和長線大方向是否一致

---

## [0.6.0] - 2026-03-04

### Added (台股輔助觀測)
- **台股市場背景分析** (`tw_market_context.py`):
  - 加權指數 ^TWII SMA50/200 + RSI + 趨勢分析
  - TWSE 三大法人買賣超 (外資/投信/自營)
  - 成交量能分析 (量比)
- **台股掃描器** (`tw_stock_scanner.py`):
  - 11 支觀測標的 (半導體/金融/航運/傳產/資服)
  - 每 30 分鐘掃描 (09:00~13:30 交易時段)
  - Telegram 推送 + SOL 背景標籤
- **config.yaml**: 台股專屬策略覆蓋 (RSI 14, SMA 10/50)

---

## [0.5.1] - 2026-03-04

### Changed
- **SOL 上線運作**: 學習偏差現在每次掃描都會主動過濾信號
  - 有毒環境 BUY→HOLD 自動否決
  - 黃金環境門檻自動放寬
  - SOL 介入次數顯示在 Telegram 報告中
- **歷史回填**: 利用 180 天 × 10 幣種數據生成 539 筆帶標籤信號讓 SOL 學習

---

## [0.5.0] - 2026-03-04

### Added (SOL - Self-Optimization & Learning)
- **Phase 1 背景標籤化**: 每筆信號自動記錄當時市場階段/BTC主導性/MTF分數/FNG趨勢/DXY趨勢
- **Phase 2 環境績效分析**: 自動分析「什麼環境下信號最準」，識別有毒/黃金環境組合
- **Phase 3 動態門檻**: 根據過去勝率自動調整 `min_agreement`，有毒環境自動否決
- **CSV 遷移**: 舊信號紀錄自動補齊 ctx_ 欄位（向下相容）

---

## [0.4.0] - 2026-03-03

### Added
- **多角度市場背景分析** (`src/analysis/market_context.py`):
  - 角度 1：多周期確認 — BTC 在 1h/4h/1d 三個時框的 RSI + 趨勢對齊
  - 角度 2：BTC 主導性 — BTC vs 山寨幣 7 日漲幅，判斷 BTC 季/山寨季
  - 角度 3：市場階段 — 牛市加速/分配頂部/熊市下跌/底部復甦，依 SMA50/200 + RSI 判斷
  - 角度 4：宏觀環境 — Fear & Greed 7 日趨勢 + 美元 DXY 指數

### Fixed
- **績效統計時框混用**: `get_performance_stats()` 現在統一使用 1h PnL（避免 4h 數據造成數字膨脹）

---



### Changed (Data-Driven Config Tuning, based on 24 real signal analysis)
- **RSI period**: 5 → 14 (標準設定，減少 1h 線噪音)
- **RSI oversold**: 25 → 30 / overbought: 75 → 70 (更合理的閾值)
- **SMA fast**: 3 → 7 / slow: 8 → 25 (減少假穿越，5:1 比例)
- **min_agreement**: 0.40 → 0.55 (數據顯示 ≥0.60 信心度平均 +0.10% vs <0.60 平均 -0.54%)
- **Panic Buy Override 停用**: RSI<20 信號在空頭趨勢中勝率=0%，平均損失 -1.11%

---



### Fixed
- **資金費率濾網過嚴**: 從 `> 0 即否決` 改為 `> 0.03% 才否決`，避免錯殺合理 BUY 機會
- **指標參數不同步**: Scanner 現在從 config.yaml 讀取 RSI/SMA/MACD/BB 參數，報告顯示值與策略一致

### Added
- **SELL 信號通知**: 報告與 Telegram 現在同時推送 BUY 和 SELL 警報
- **歷史績效追蹤器** (`signal_tracker.py`): 自動記錄信號至 CSV，回查 1h/4h/24h 表現，計算營利統計
- **信號去重**: 同一標的同方向 4 小時內不重複記錄，避免統計膨脹
- **成交量確認濾網** (`volume_filter.py`): 低於 20 日均量 50% 的信號被否決
- **ATR 動態止損**: 依波動率自動調整止損距離 (倍數可配置)
- **中文總結含指標定義**: 每份報告自動附帶 RSI、信心度等專有名詞說明

## [0.2.0] - 2026-03-01

### Added
- **Top 20 掃描器**: 每小時掃描前 20 大加密幣，持續監控
- **Telegram 通知**: 自動推送掃描結果和 BUY 機會
- **中文摘要報告**: 包含市場情緒、買入機會等資訊
- **情緒策略**: Fear & Greed Index 整合
- **資金費率策略**: Funding Rate 濾網/信號模式
- **BTC 趨勢濾網**: BTC SMA50 大盤環境判斷
- **台股評估**: 支援 yfinance 評估個股 (如 6214 精誠)

## [0.1.0] - 2026-02-28

### Added
- 專案初始化：模組化架構建立
- **Config**: Pydantic 設定驗證 + YAML + .env API Key 管理
- **Data Layer**: CryptoCollector (CCXT), StockCollector (yfinance), TwStockCollector (Shioaji/yfinance)
- **Indicators**: SMA, EMA, RSI, MACD, Bollinger Bands, ATR, Stochastic
- **Strategies**: SMA Crossover, RSI, MACD, Bollinger Bands 四策略
- **Risk Manager**: 止損/止盈、倉位管理、追蹤止損、投組風控
- **Decision Engine**: 多策略加權投票 + 市場狀態偵測 + 自適應策略切換
- **Backtester**: 事件驅動回測引擎 + Sharpe/Sortino/MDD/Win Rate 績效分析
- **PaperExchange**: 虛擬交易模擬器 (滑點/手續費/持倉管理)
- **Executor**: 統一訂單執行介面 (Paper → Live 切換)
- **Storage**: SQLite 資料庫 (OHLCV 快取/交易紀錄/績效追蹤)
- **Logger**: Loguru 結構化日誌 (控制台/檔案/交易/錯誤分離)
- **CLI**: 支援 backtest / paper 兩種模式
- 支援三大市場：加密幣 / 美股 / 台股
