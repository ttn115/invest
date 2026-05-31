# 自主交易機器人 (Autonomous Trader)

模組化自主交易分析系統，支援**加密幣**、**美股**、**台股**三市場的技術分析與信號掃描。

> 目前版本：v0.6.7 | 詳見 [CHANGELOG.md](CHANGELOG.md)

---

## 目錄

1. [系統特色](#系統特色)
2. [快速開始](#快速開始)
3. [三市場掃描器](#三市場掃描器)
4. [統一市場看板](#統一市場看板)
5. [SOL 自學習系統](#sol-自學習系統)
6. [策略與濾網](#策略與濾網)
7. [設定檔說明](#設定檔說明)
8. [Telegram 通知設定](#telegram-通知設定)
9. [GitHub Actions 自動掃描](#github-actions-自動掃描)
10. [專案結構](#專案結構)
11. [延伸閱讀](#延伸閱讀)
12. [免責聲明](#免責聲明)

---

## 系統特色

| 功能 | 說明 |
|------|------|
| **多策略投票** | SMA 交叉 / RSI / MACD / 布林通道，加權投票出最終信號 |
| **三大市場** | 加密幣（Binance）/ 美股（yfinance）/ 台股（yfinance） |
| **市場背景分析** | 加密幣：BTC 主導性 + 市場階段 + Fear & Greed；美股：SPY + VIX + 10Y；台股：加權指數 + 三大法人 |
| **SOL 自學習** | 分析 500+ 筆歷史信號，自動識別有毒/黃金市場環境，動態調整門檻 |
| **Munger 過濾器** | RECOVERY 階段 + RSI < 25 時，自動將 SELL 轉為 HOLD，避免方向矛盾 |
| **統一市場看板** | 三市場每次掃描後自動寫入同一份 `data/market_dashboard.md` |
| **Telegram 推播** | 掃描完成後即時推送信號報告 |
| **持續掃描模式** | `--loop` 旗標自動定時掃描（加密幣 5 分鐘、美股/台股 30 分鐘） |
| **信號追蹤** | 每筆 BUY/SELL 記錄至 `data/signal_history.csv`，追蹤 1h/4h/24h 報酬 |

---

## 快速開始

### 環境需求

- Python 3.11+
- Binance 帳號（加密幣掃描，唯讀 API Key）
- Telegram Bot（選用，推播用）

### 安裝

```bash
# 1. 進入專案目錄
cd stock_invest

# 2. 建立虛擬環境
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Mac/Linux

# 3. 安裝依賴
pip install -e ".[dev]"

# 4. 複製並填寫環境變數
cp .env.example .env
# 編輯 .env，填入 Binance API Key 和 Telegram Bot Token
```

### 最小設定（`.env`）

```env
# 加密幣（必填）
CCXT_EXCHANGE=binance
CCXT_API_KEY=你的 API Key
CCXT_SECRET_KEY=你的 Secret Key

# Telegram（選用，不填則只輸出到終端）
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 其他
USDT_TWD_RATE=31.4
```

> 美股和台股使用 yfinance，**不需要 API Key**，開箱即用。

---

## 三市場掃描器

所有掃描器都從 `stock_invest/` 根目錄執行。

### 加密幣掃描（Binance Top 20）

```bash
# 單次掃描
python scripts/top_20_scanner.py

# 持續掃描（每 5 分鐘）
python scripts/top_20_scanner.py --loop

# 自訂間隔（秒）
python scripts/top_20_scanner.py --loop --interval 600
```

**掃描內容**：Binance 成交量前 20 大 USDT 交易對

**市場背景**：
- 市場階段（BULL / BEAR / RECOVERY / DISTRIBUTION）
- BTC 主導性（BTC_SEASON / ALT_SEASON / MIXED）
- Fear & Greed 指數與趨勢
- 多時框對齊（1h / 4h / 1d）

**信號格式**：短線（1h）+ 長線（1d）雙信號

---

### 美股掃描（30 支護城河標的）

```bash
# 單次掃描（非交易時段也可執行）
python scripts/us_stock_scanner.py --force

# 交易時段掃描（09:30–16:00 ET，台灣時間 21:30–04:00）
python scripts/us_stock_scanner.py

# 持續掃描
python scripts/us_stock_scanner.py --loop

# 指定標的
python scripts/us_stock_scanner.py --symbols AAPL MSFT NVDA
```

**觀測名單（30 支）**：

| 板塊 | 標的 |
|------|------|
| 科技/半導體 | AAPL MSFT NVDA AVGO TSM |
| 消費/零售 | AMZN COST KO WMT |
| 金融 | BRK-B V MA JPM AXP |
| 醫療 | JNJ UNH LLY |
| 能源 | XOM CVX |
| 工業 | CAT DE UPS |
| 通信 | GOOGL META |
| ETF（大盤參考） | SPY QQQ XLF XLE XLK GLD |

**市場背景**：SPY 階段 / VIX 恐慌指數（4 級）/ 10Y 殖利率趨勢 / 綜合市場情緒

---

### 台股掃描（11 支觀測標的）

```bash
# 單次掃描
python scripts/tw_stock_scanner.py

# 持續掃描（交易時段 09:00–13:30）
python scripts/tw_stock_scanner.py --loop
```

**觀測名單**：

| 代碼 | 名稱 | 板塊 |
|------|------|------|
| 2330 | 台積電 | 半導體 |
| 2317 | 鴻海 | 電子代工 |
| 2454 | 聯發科 | IC 設計 |
| 2882 | 國泰金 | 金融 |
| 2881 | 富邦金 | 金融 |
| 2603 | 長榮 | 航運 |
| 2002 | 中鋼 | 鋼鐵 |
| 3711 | 日月光 | 封測 |
| 2412 | 中華電 | 電信 |
| 6214 | 精誠 | 資訊服務 |
| 2308 | 台達電 | 電源 |

**市場背景**：加權指數趨勢 / RSI / 三大法人買賣超 / 成交量能（量比）

---

## 統一市場看板

每次任一掃描器完成後，結果會自動寫入：

```
data/market_dashboard.md
```

**看板結構**：
```markdown
# 市場掃描總覽
> 最後更新：2026-04-12 20:50

## 🪙 加密幣 (Crypto) — 2026-04-12 20:44
市場環境 + 信號表格 + SOL 有毒/黃金環境

## 🇺🇸 美股 (US Stocks) — 2026-04-12 20:50
市場環境（SPY/VIX/10Y）+ 30 支信號表格

## 🇹🇼 台股 (Taiwan Stocks) — 2026-04-12 20:50
市場環境（加權指數）+ 11 支信號表格
```

三個市場**各自獨立更新**，不會互相覆蓋。

---

## SOL 自學習系統

SOL（Self-Optimization & Learning）是系統的核心保護機制，從信號歷史中自動學習市場環境的好壞。

### 運作流程

```
信號執行 → 記錄到 signal_history.csv → 計算 1h PnL → 標記勝/負
    ↓
SOL 每次掃描分析環境績效 (Phase × Season × FG趨勢 = 環境 Key)
    ↓
識別有毒環境（勝率 < 44% 且 avg PnL < -0.05%）→ BUY 信號自動否決
識別黃金環境（勝率 > 60% 且 avg PnL > 0.1%） → min_agreement 門檻降低
    ↓
更新 data/sol_bias.json → 下次掃描立即生效
```

### 目前有毒環境（已封鎖）

| 環境 Key | 樣本數 | 勝率 | avg PnL |
|----------|--------|------|---------|
| BEAR\|BTC_SEASON\|FLAT | 16 | 18.8% | -0.34% |
| BEAR\|ALT_SEASON\|FLAT | 34 | 38.2% | -0.20% |
| BEAR\|MIXED\|FLAT | 156 | 41.0% | -0.09% |

### 目前黃金環境（門檻放寬）

| 環境 Key | 樣本數 | 勝率 | avg PnL |
|----------|--------|------|---------|
| RECOVERY\|MIXED\|RISING | 9 | 100% | +0.48% |
| BEAR\|ALT_SEASON\|RISING | 10 | 80% | +0.47% |
| BEAR\|MIXED\|RISING | 43 | 62.8% | +0.34% |

### Munger 過濾器

```
RECOVERY 階段 + RSI < 25 → SELL 強制轉為 HOLD
```

根本原因：2026-02-06 五筆最大虧損（-2.9% ~ -5.42%）全來自 RECOVERY + 極度超賣 SELL，與市場方向矛盾。

---

## 策略與濾網

### 四大核心策略

| 策略 | 原理 | 加密幣設定 | 美股/台股設定 |
|------|------|-----------|--------------|
| SMA 交叉 | 快線穿越慢線 | fast=7, slow=25 | fast=10, slow=50 |
| RSI | 超買/超賣反轉 | period=7, 30/70 | period=14, 30/70 |
| MACD | 動量轉折 | 6/13/4 | 12/26/9 |
| 布林通道 | 波動率突破 | period=10, std=2.5 | period=20, std=2.0 |

### 策略權重投票

```
各策略輸出 BUY / SELL / HOLD
    ↓
加權計算同意比例（min_agreement = 0.40 加密幣 / 0.55 美股台股）
    ↓
通過門檻 → 輸出信號
```

### 額外濾網（僅加密幣）

| 濾網 | 作用 |
|------|------|
| Funding Rate | 費率 > 0.03% → 否決 BUY（多頭過熱） |
| BTC Regime | BTC 跌破 SMA50 → 保守模式 |
| Volume Filter | 成交量 < 20 日均量 50% → 否決 BUY |
| SOL 環境封鎖 | 有毒環境 BUY → HOLD |
| Munger Filter | RECOVERY + RSI<25 SELL → HOLD |

---

## 設定檔說明

所有策略參數集中在 `config.yaml`，各市場可覆蓋全域預設：

```yaml
# 全域預設
strategies:
  rsi:
    params:
      period: 7
      oversold: 35
      overbought: 65

markets:
  crypto:
    strategies:
      rsi:
        params:
          period: 7      # 1h 短線，更高靈敏度
    decision_engine:
      min_agreement: 0.40  # 搭配濾網，可放寬

  us_stock:
    decision_engine:
      min_agreement: 0.55  # 日線保守設定

  tw_stock:
    decision_engine:
      min_agreement: 0.55
```

**修改設定無需重啟**，下次掃描即生效。

---

## Telegram 通知設定

### 建立 Bot

1. 在 Telegram 搜尋 **@BotFather**，傳送 `/newbot`
2. 取得 **Bot Token**（格式：`123456789:AAFxxxxxxxx`）
3. 搜尋你的 Bot，點「Start」
4. 前往 `https://api.telegram.org/bot<Token>/getUpdates`，找到 `"chat":{"id":數字}` 取得 **Chat ID**

### 填入 `.env`

```env
TELEGRAM_BOT_TOKEN=123456789:AAFxxxxxxxx
TELEGRAM_CHAT_ID=987654321
```

**未設定 Telegram 時**，掃描結果仍會完整顯示在終端機。

---

## GitHub Actions 自動掃描

### 執行時間表

- **頻率**：每小時整點執行（台灣時間 05:00–20:00）
- **靜默時段**：台灣時間 21:00–04:59（不打擾睡眠）
- **費用**：私人 Repo 每月約 80–160 分鐘，遠低於 2,000 分鐘免費額度

### 設定步驟

**Step 1**：推上 GitHub

```bash
git init && git add .
git commit -m "init"
git remote add origin https://github.com/<你的帳號>/<repo>.git
git push -u origin main
```

> 確認 `.gitignore` 包含 `.env`，**絕對不能**上傳 API Key。

**Step 2**：新增 GitHub Secrets

到 **Settings → Secrets and variables → Actions**，新增：

| Secret | 說明 |
|--------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 你的 Chat ID |
| `CCXT_EXCHANGE` | `binance` |
| `CCXT_API_KEY` | 交易所唯讀 API Key |
| `CCXT_SECRET_KEY` | 交易所 API Secret |

**Step 3**：確認 `.github/workflows/monitor.yml` 存在，手動觸發測試：

**Actions → Crypto Market Scanner → Run workflow**

---

## 專案結構

```
stock_invest/
├── scripts/
│   ├── top_20_scanner.py       # 加密幣掃描器（Binance Top 20）
│   ├── us_stock_scanner.py     # 美股掃描器（30 支護城河標的）
│   ├── tw_stock_scanner.py     # 台股掃描器（11 支觀測標的）
│   ├── report_writer.py        # 統一看板寫入工具
│   ├── sol_backfill.py         # SOL 歷史信號回填工具
│   └── last_*_report.txt       # 最新掃描報告（自動生成）
│
├── src/
│   ├── config/settings.py      # 設定管理（YAML + .env 讀取）
│   ├── data/
│   │   ├── collector.py        # 資料收集（CCXT / yfinance / Shioaji）
│   │   ├── indicators.py       # 技術指標（SMA / RSI / MACD / BB / ATR）
│   │   ├── sentiment.py        # Fear & Greed 指數
│   │   └── funding_rate.py     # Binance 資金費率
│   ├── strategy/
│   │   ├── sma_crossover.py    # SMA 交叉策略
│   │   ├── rsi_strategy.py     # RSI 策略
│   │   ├── macd_strategy.py    # MACD 策略
│   │   ├── bollinger_strategy.py # 布林通道策略
│   │   ├── sentiment_strategy.py # 情緒策略
│   │   ├── funding_rate_strategy.py # 資金費率濾網
│   │   ├── regime_filter.py    # BTC 大盤濾網
│   │   └── volume_filter.py    # 成交量濾網
│   ├── engine/
│   │   ├── decision.py         # 多策略投票決策引擎
│   │   ├── backtester.py       # 事件驅動回測引擎
│   │   └── executor.py         # 訂單執行層
│   ├── analysis/
│   │   ├── market_context.py   # 加密幣市場背景分析
│   │   ├── tw_market_context.py # 台股市場背景分析
│   │   └── contextual_optimizer.py # SOL 自學習系統
│   ├── monitor/
│   │   ├── signal_tracker.py   # 信號追蹤 + PnL 計算
│   │   ├── notifier.py         # Telegram 推播
│   │   └── logger.py           # 結構化日誌
│   ├── risk/manager.py         # 風控（止損/止盈/倉位管理）
│   └── main.py                 # 決策引擎組裝（build_decision_engine）
│
├── data/
│   ├── market_dashboard.md     # 三市場統一看板（自動更新）
│   ├── signal_history.csv      # 信號歷史紀錄（含 PnL）
│   ├── sol_bias.json           # SOL 學習結果快取
│   └── backtest/               # 回測輸出
│
├── config.yaml                 # 策略與風控設定（主設定檔）
├── .env                        # API Keys（不可上傳 Git）
├── .env.example                # API Key 範本
├── CHANGELOG.md                # 版本變更紀錄
├── MUNGER_GUIDE.md             # 蒙格心智模型應用指南
└── pyproject.toml              # 套件依賴
```

---

## 延伸閱讀

| 文件 | 說明 |
|------|------|
| [CHANGELOG.md](CHANGELOG.md) | 每個版本的新增功能與修正紀錄 |
| [MUNGER_GUIDE.md](MUNGER_GUIDE.md) | 蒙格心智模型在此系統的應用與 SOP |
| [data/market_dashboard.md](data/market_dashboard.md) | 三市場最新掃描結果（每次掃描自動更新） |
| [data/sol_bias.json](data/sol_bias.json) | SOL 目前識別的有毒/黃金環境清單 |

---

## 免責聲明

本系統僅供個人學習與研究使用。所有信號均為技術分析參考，**不構成任何投資建議**。投資涉及風險，請依據個人風險承受能力自行判斷。作者不對任何交易損失負責。
