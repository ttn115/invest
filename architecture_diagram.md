# 自主交易機器人 — 系統架構全景圖

> **實線** `───` = 已實作　｜　**虛線** `- - -` = 尚未實作（規劃中）
>
> 此文件為 **Living Document**，隨功能完善持續更新。

---

## 一、資料來源層（Data Sources）

```mermaid
graph LR
    subgraph Sources["📡 外部資料來源"]
        direction TB
        subgraph Implemented["✅ 已實作"]
            B_CCXT["CCXT / Binance<br/>加密幣 OHLCV + 即時價"]
            B_YF["yfinance<br/>美股/台股 OHLCV + VIX + DXY"]
            B_ALT["Alternative.me<br/>Crypto Fear & Greed"]
            B_BFAPI["Binance fapi<br/>Funding Rate (fallback)"]
            B_TWSE["TWSE 官方 API<br/>三大法人買賣超"]
        end
        subgraph Planned["🔲 規劃中"]
            P_OKX["OKX / Bybit<br/>備援交易所 OHLCV"]
            P_CG["CoinGecko API<br/>BTC Dominance"]
            P_AV["Alpha Vantage<br/>美股備援"]
            P_FM["FinMind<br/>台股備援"]
            P_LC["LunarCrush<br/>社群情緒"]
            P_GN["Glassnode / CryptoQuant<br/>鏈上數據"]
            P_CP["CryptoPanic<br/>加密幣新聞"]
            P_FRED["FRED API<br/>經濟指標"]
            P_TWSE2["TWSE 信用交易<br/>融資融券"]
        end
    end
```

---

# 🏦 自主交易機器人 — 系統架構全景圖

> **實線** `━━` = 已實作 (Done) ｜ **虛線** `- -` = 規劃中 (Pending)
> 💡 *這是一個動態藍圖，會隨著系統開發進度持續更新。*

---

## 一、 核心架構流程

```mermaid
flowchart TB
    %% ━━━━ 1. 資料輸入層 ━━━━
    subgraph DS["📡 資料來源 (Data Sources)"]
        direction LR
        CCXT["🟢 Binance OHLCV"]
        YF["🟢 Yahoo Finance"]
        ALT["🟢 Fear/Greed Index"]
        TWSE["🟢 TWSE 盤後資料"]
        
        OKX["🔘 OKX/Bybit 備援"]
        CG["🔘 CoinGecko 數據"]
        NEWS["🔘 新聞/社群情緒"]
        ONCHAIN["🔘 鏈上大戶數據"]
    end

    %% ━━━━ 2. 收集與預處理層 ━━━━
    subgraph COL["📥 數據收集與清理 (Collectors)"]
        direction LR
        DB_C["🟢 核心收集引擎"]
        VAL["🟢 數據校驗與對齊"]
        PRE["🔲 異常偵測與修復"]
    end

    %% ━━━━ 3. 核心運作引擎層 ━━━━
    subgraph ENG["⚙️ 核心處理模組 (Core Engines)"]
        direction TB
        IND["📊 技術指標引擎<br/>(SMA, RSI, MACD, BB)"]
        STR["🧠 多策略決策引擎<br/>(投票制/加權權重)"]
        CTX["🔬 市場背景分析<br/>(DXY, VIX, 多周期)"]
        
        AI_S["🔲 AI/ML 策略模組<br/>(LSTM/PPO)"]
    end

    %% ━━━━ 4. 風控與執行層 ━━━━
    subgraph RISK["🛡️ 風險控管與執行 (Risk & Execution)"]
        direction LR
        RM["🟢 倉位控制/止損止盈"]
        PE["🟢 Paper Trading<br/>(虛擬交易系統)"]
        
        LIVE["🔲 Live Trading<br/>(真實 API 對接)"]
    end

    %% ━━━━ 5. 輸出與監控層 ━━━━
    subgraph OUT["📢 輸出與視覺化 (Output & Monitor)"]
        direction LR
        TG["🟢 Telegram 警報報告"]
        CMD["🟢 CMD 即時控制台"]
        ST["🟢 Signal Tracker<br/>(CSV 紀錄)"]
        
        WEB["🔲 Web Dashboard<br/>(Flask/Chart.js)"]
    end

    %% ━━ 連接關係 (實線 = 已通) ━━
    DS --- COL
    COL --> ENG
    ENG --> RISK
    RISK --> OUT

    %% ━━ 樣式定義 ━━
    classDef done fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#01579b
    classDef pending fill:#f5f5f5,stroke:#9e9e9e,stroke-width:2px,color:#757575,stroke-dasharray: 5 5
    
    class CCXT,YF,ALT,TWSE,DB_C,VAL,IND,STR,CTX,RM,PE,TG,CMD,ST done
    class OKX,CG,NEWS,ONCHAIN,PRE,AI_S,LIVE,WEB pending
```

---

## 二、 開發進度統計

| 模組分組 | 功能描述 | 狀態 | 進度 |
| :--- | :--- | :---: | :---: |
| **基礎建設** | OHLCV 抓取、指標計算、虛擬交易模擬 | ✅ 已完成 | 100% |
| **策略核心** | 多周期分析、加權投票、情緒濾網 | ✅ 已完成 | 100% |
| **風控模組** | ATR 動態止損、追蹤止損、單筆風險控制 | ✅ 已完成 | 90% |
| **監控與通報** | Telegram 全能報告、CMD 面板、績效 CSV | ✅ 已完成 | 95% |
| **數據多樣化** | 備援 API (OKX, AlphaVantage)、鏈上、新聞 | 🚧 規劃中 | 20% |
| **進階功能** | Web UI 介面、AI 預測、實盤交易銜接 | 🚧 規劃中 | 10% |

---

## 三、 系統擴展優先序 (Roadmap)

1.  **[短] 資料高可用**：實作 `CCXT` 多交易所 failover，確保 Binance 維護時仍有報價。
2.  **[中] Web 前端**：開發 Flask 儀表板，取代目前純文字的 Telegram/CMD 報告。
3.  **[長] AI 優化**：匯入歷史 Signal Tracker 資料，利用 ML 對策略權重進行動態回測優化。


> [!TIP]
> 每次新增或完善一個模組後，更新此圖中對應節點的顏色從 `plan`（灰色虛線）改為 `done`（綠色實線），即可持續追蹤進度。
