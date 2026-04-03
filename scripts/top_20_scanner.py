import sys
import os
import argparse
import time
import datetime as dt
import pandas as pd
import ccxt
import requests
from loguru import logger

# Windows UTF-8 stdout fix (prevents emoji encoding errors when piped)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config.settings import Settings
from src.data.indicators import IndicatorEngine
from src.main import build_decision_engine
from src.strategy.base import SignalType
from src.monitor.notifier import TelegramNotifier
from src.monitor.signal_tracker import SignalTracker
from src.analysis.market_context import MarketContextAnalyzer
from src.analysis.contextual_optimizer import ContextualOptimizer

# Setup logging
logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

# List of stablecoins to exclude
STABLECOINS = ["USDC", "FDUSD", "TUSD", "PAXG", "USDP", "DAI", "EUR", "GBP", "VAI", "AEUR", "USD1"]

def get_top_symbols(exchange, limit=20):
    """Fetch top USDT pairs by 24h volume, excluding stablecoins."""
    try:
        tickers = exchange.fetch_tickers()
        data = []
        for symbol, ticker in tickers.items():
            if symbol.endswith("/USDT"):
                base = symbol.split("/")[0]
                if base not in STABLECOINS:
                    data.append({
                        "symbol": symbol,
                        "volume": ticker["quoteVolume"]
                    })
        
        df = pd.DataFrame(data).sort_values(by="volume", ascending=False)
        return df.head(limit)["symbol"].tolist()
    except Exception as e:
        logger.error(f"Error fetching top symbols: {e}")
        return ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"]

def _combo_label(short_sig, long_sig):
    """生成短長線綜合標籤"""
    s = {"BUY": "多", "SELL": "空", "HOLD": "平"}.get(short_sig, "平")
    l = {"BUY": "多", "SELL": "空", "HOLD": "平"}.get(long_sig, "平")
    combo = f"短{s}長{l}"
    # Add emoji
    if s == "多" and l == "多":
        return f"🟢🟢 {combo}"
    elif s == "空" and l == "空":
        return f"🔴🔴 {combo}"
    elif s == "多" and l == "空":
        return f"⚠️ {combo}"
    elif s == "空" and l == "多":
        return f"⚠️ {combo}"
    return f"⚪ {combo}"


def _macd_hist_desc(hist_now, hist_prev):
    """描述 MACD 柱狀圖狀態"""
    if hist_prev < 0 and hist_now > 0:
        return "翻正🔼"
    if hist_prev > 0 and hist_now < 0:
        return "翻負🔽"
    if hist_now > 0 and hist_now > hist_prev:
        return "正向加速🔼"
    if hist_now > 0 and hist_now <= hist_prev:
        return "正向收縮⏸️"
    if hist_now < 0 and hist_now < hist_prev:
        return "負向擴大🔽"
    if hist_now < 0 and hist_now >= hist_prev:
        return "負向收縮⏸️"
    return "持平⏸️"


def _rsi_desc(rsi_val):
    """RSI 值的直觀描述"""
    if rsi_val < 20:
        return f"{rsi_val:.0f} 極度超跌🔽🔽"
    if rsi_val < 30:
        return f"{rsi_val:.0f} 超跌🔽"
    if rsi_val < 45:
        return f"{rsi_val:.0f} 偏弱🔽"
    if rsi_val < 55:
        return f"{rsi_val:.0f} 中性⏸️"
    if rsi_val < 70:
        return f"{rsi_val:.0f} 偏強🔼"
    if rsi_val < 80:
        return f"{rsi_val:.0f} 超買🔼"
    return f"{rsi_val:.0f} 極度超買🔼🔼"


def _bb_position_desc(pct_b):
    """Bollinger %B 位置描述"""
    if pct_b <= 0:
        return f"{pct_b:.0%} 跌破下軌🔽"
    if pct_b < 0.20:
        return f"{pct_b:.0%} 下軌附近🔽"
    if pct_b < 0.40:
        return f"{pct_b:.0%} 中下段"
    if pct_b < 0.60:
        return f"{pct_b:.0%} 中段⏸️"
    if pct_b < 0.80:
        return f"{pct_b:.0%} 中上段"
    if pct_b < 1.0:
        return f"{pct_b:.0%} 上軌附近🔼"
    return f"{pct_b:.0%} 突破上軌🔼"


def _bb_width_desc(width_now, width_prev):
    """Bollinger 通道寬度變化"""
    if width_prev == 0:
        return "正常"
    change = (width_now - width_prev) / width_prev
    if change > 0.05:
        return "擴張(波動增大)"
    if change < -0.05:
        return "收窄(波動減小)"
    return "穩定"


def _fg_desc(fg_val):
    """Fear & Greed 描述"""
    if fg_val < 15:
        return f"{int(fg_val)} 極度恐懼🔽"
    if fg_val < 25:
        return f"{int(fg_val)} 恐懼🔽"
    if fg_val < 40:
        return f"{int(fg_val)} 偏恐懼"
    if fg_val < 60:
        return f"{int(fg_val)} 中性⏸️"
    if fg_val < 75:
        return f"{int(fg_val)} 偏貪婪"
    if fg_val < 85:
        return f"{int(fg_val)} 貪婪🔼"
    return f"{int(fg_val)} 極度貪婪🔼"


def _fr_desc(fr_val):
    """Funding Rate 描述"""
    if fr_val > 0.0003:
        return f"{fr_val:.4%} 過熱🔼"
    if fr_val > 0.0001:
        return f"{fr_val:.4%} 偏多"
    if fr_val < -0.0003:
        return f"{fr_val:.4%} 極度偏空🔽"
    if fr_val < -0.0001:
        return f"{fr_val:.4%} 偏空"
    return f"{fr_val:.4%} 正常⏸️"


def generate_dimension_report(raw_data_list, market_ctx, fg_val, usdt_twd):
    """
    [v0.6.4] 多面向資訊看板
    
    每個幣種獨立呈現 5 個面向的原始指標數據，不做投票/合併判斷。
    讓使用者看到完整資訊自行決策。
    
    5 面向：
    📈 動能 — RSI + MACD 柱狀圖方向
    📐 趨勢 — SMA 快慢線位置 + 斜率
    📊 波動 — Bollinger %B 位置 + 通道寬度
    😱 情緒 — Fear & Greed + Funding Rate
    🌐 大盤 — 市場階段 + 主導性 + 周期共識 (共用)
    """
    lines = []
    lines.append("")
    lines.append("📊 *多面向資訊看板* (v0.6.4)")
    lines.append("每個面向獨立呈現，不做合併判斷")
    lines.append("━" * 38)

    # 🌐 大盤環境 (共用，只顯示一次)
    lines.append("")
    lines.append("🌐 *大盤環境* (所有幣種共用)")
    
    phase_desc = {
        "BULL_RUN": "🐂 牛市加速",
        "DISTRIBUTION": "📦 分配頂部",
        "BEAR": "🐻 熊市下跌",
        "RECOVERY": "🌱 底部復甦",
    }.get(market_ctx.phase, "❓ 未知") if market_ctx else "❓"
    
    season_desc = {
        "BTC_SEASON": "🟠 BTC 季",
        "ALT_SEASON": "🟢 山寨季",
        "MIXED": "⚪ 混合",
    }.get(market_ctx.season, "⚪") if market_ctx else "⚪"
    
    mtf_desc = {
        "STRONG_BUY": "✅ 全面看漲",
        "WEAK_BUY": "⚡ 多數看漲",
        "NEUTRAL": "⚠️ 方向分歧",
        "WEAK_SELL": "🔴 多數看跌",
        "STRONG_SELL": "🔴🔴 全面看跌",
    }.get(market_ctx.mtf_alignment, "❓") if market_ctx else "❓"
    
    lines.append(f"  階段：{phase_desc}")
    lines.append(f"  主導：{season_desc}")
    lines.append(f"  周期：{mtf_desc}")
    lines.append(f"  情緒：{_fg_desc(fg_val)}")
    
    if market_ctx and market_ctx.tf_1h:
        tf_info = f"  BTC: 1h RSI={market_ctx.tf_1h.rsi:.0f}"
        if market_ctx.tf_4h:
            tf_info += f" | 4h RSI={market_ctx.tf_4h.rsi:.0f}"
        if market_ctx.tf_1d:
            tf_info += f" | 1d RSI={market_ctx.tf_1d.rsi:.0f}"
        lines.append(tf_info)

    lines.append("")
    lines.append("━" * 38)
    
    # 每個幣種的 4 面向 (情緒和大盤已在上方共用)
    for raw in raw_data_list:
        sym = raw["symbol"]
        price = raw["price"]
        price_twd = price * usdt_twd
        
        lines.append("")
        lines.append(f"*{sym}* ${price:,.2f} (NT${price_twd:,.0f})")
        
        # 📈 動能
        rsi_text = _rsi_desc(raw["rsi"]) if raw["rsi"] is not None else "N/A"
        macd_text = _macd_hist_desc(raw["macd_hist"], raw["macd_hist_prev"])
        lines.append(f"├ 📈 動能  RSI={rsi_text}  MACD柱={macd_text}")
        
        # 📐 趨勢
        sma_f = raw["sma_fast"]
        sma_s = raw["sma_slow"]
        price_val = raw["price"]
        if sma_f is not None and sma_s is not None:
            fast_above = "✅" if sma_f > sma_s else "❌"
            price_above = "✅" if price_val > sma_s else "❌"
            slope = raw.get("sma_slope", 0)
            slope_desc = "上升🔼" if slope > 0.002 else ("下降🔽" if slope < -0.002 else "平⏸️")
            lines.append(f"├ 📐 趨勢  快>慢線{fast_above}  價>慢線{price_above}  斜率:{slope_desc}")
        else:
            lines.append(f"├ 📐 趨勢  數據不足")
        
        # 📊 波動
        pct_b = raw.get("bb_pct_b")
        if pct_b is not None:
            bb_text = _bb_position_desc(pct_b)
            width_text = _bb_width_desc(raw.get("bb_width", 0), raw.get("bb_width_prev", 0))
            lines.append(f"├ 📊 波動  BB={bb_text}  通道:{width_text}")
        else:
            lines.append(f"├ 📊 波動  數據不足")
        
        # 😱 情緒 (個幣的 Funding Rate)
        fr = raw.get("funding_rate", 0)
        lines.append(f"└ 💰 費率  {_fr_desc(fr)}")

    # 名詞說明
    lines.append("")
    lines.append("━" * 38)
    lines.append("📚 *面向說明*")
    lines.append("• RSI: 動能指標,<30超跌/>70超買")
    lines.append("• MACD柱: 動量方向,翻正=多頭動能出現")
    lines.append("• 快>慢線: SMA短線在長線上方=上升趨勢")
    lines.append("• BB位置: 0%=下軌/100%=上軌")
    lines.append("• F&G: 恐懼貪婪指數,<25=市場恐懼")
    lines.append("• 費率: 期貨資金費率,過高=過熱")
    
    return "\n".join(lines)


def generate_full_report(report_df, fg_val, market_ctx=None, perf_text: str = "", sol_text: str = "") -> str:
    """
    生成完整報告訊息，包含：
    1. 市場背景分析 (4 個角度)
    2. 短線 (1h) 信號
    3. 長線 (1d) 趨勢
    4. 雙線綜合一覽
    5. 績效追蹤 + SOL + 名詞說明
    """
    has_dual = "Signal_1d" in report_df.columns

    buy_opps = report_df[report_df["Signal"] == "BUY"]
    sell_opps = report_df[report_df["Signal"] == "SELL"]

    lines = []

    # ── 區塊 1：市場背景 (4 角度) ──────────────────────────
    if market_ctx:
        lines.append(market_ctx.telegram_block())
    else:
        lines.append(f"📊 市場情緒：*{fg_val}* (恐懼與貪婪指數)")
        lines.append("")

    # ── 區塊 2：短線 (1h) 信號 ────────────────────────────
    lines.append("⚡ *短線信號 (1h)*")
    if buy_opps.empty and sell_opps.empty:
        lines.append("  ✅ 暫無強烈訊號")
    
    if not buy_opps.empty:
        lines.append(f"  🚀 買入 ({len(buy_opps)} 個)")
        for _, row in buy_opps.iterrows():
            rsi_val = float(row['RSI']) if row['RSI'] != 'N/A' else 50.0
            rsi_tag = " ⚠️超跌" if rsi_val < 30 else ""
            lines.append(
                f"    • *{row['Symbol']}* NT${row['Price(TWD)']} | "
                f"信心:{row['Confidence']} RSI:{row['RSI']}{rsi_tag}"
            )

    if not sell_opps.empty:
        lines.append(f"  🔻 賣出 ({len(sell_opps)} 個)")
        for _, row in sell_opps.iterrows():
            rsi_val = float(row['RSI']) if row['RSI'] != 'N/A' else 50.0
            rsi_tag = " ⚠️超買" if rsi_val > 70 else ""
            lines.append(
                f"    • *{row['Symbol']}* NT${row['Price(TWD)']} | "
                f"信心:{row['Confidence']} RSI:{row['RSI']}{rsi_tag}"
            )

    # ── 區塊 3：長線 (1d) 趨勢 ────────────────────────────
    if has_dual:
        lines.append("")
        lines.append("🏔️ *長線趨勢 (1d)*")
        buy_1d = report_df[report_df["Signal_1d"] == "BUY"]
        sell_1d = report_df[report_df["Signal_1d"] == "SELL"]
        if not buy_1d.empty:
            for _, row in buy_1d.iterrows():
                lines.append(
                    f"  🟢 *{row['Symbol']}* 日線看多 "
                    f"(RSI:{row['RSI_1d']} 信心:{row['Confidence_1d']})"
                )
        if not sell_1d.empty:
            for _, row in sell_1d.iterrows():
                lines.append(
                    f"  🔴 *{row['Symbol']}* 日線看空 "
                    f"(RSI:{row['RSI_1d']} 信心:{row['Confidence_1d']})"
                )
        if buy_1d.empty and sell_1d.empty:
            lines.append("  ⚪ 日線全數 HOLD，長線觀望")

    # ── 區塊 4：雙線綜合一覽 ─────────────────────────────
    lines.append("")
    lines.append("📋 *雙線綜合一覽*")
    for _, row in report_df.iterrows():
        if has_dual:
            combo = _combo_label(row['Signal'], row['Signal_1d'])
            lines.append(
                f"  {combo} *{row['Symbol']}* NT${row['Price(TWD)']} "
                f"1h:{row['Signal']} 1d:{row['Signal_1d']}"
            )
        else:
            sig = row['Signal']
            sig_icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(sig, "⚪")
            lines.append(
                f"  {sig_icon} *{row['Symbol']}* NT${row['Price(TWD)']} "
                f"RSI:{row['RSI']} {sig}"
            )

    # ── 區塊 5：績效與 SOL ────────────────────────────────
    if perf_text:
        lines.append("")
        lines.append("📈 *信號績效追蹤 (1h)*")
        lines.append(perf_text)

    if sol_text:
        lines.append("")
        lines.append(sol_text)

    # ── 區塊 6：名詞說明 ────────────────────────────────────
    lines.append("")
    lines.append("📚 *名詞說明*")
    lines.append("• *短線 (1h)*：小時線信號，適合短線操作")
    lines.append("• *長線 (1d)*：日線趨勢，確認大方向")
    lines.append("• *短多長多*：最佳買入時機 (短線和長線都看漲)")
    lines.append("• *⚠️ 短多長空*：短線反彈但大趨勢向下，小心追高")
    lines.append("• *SOL*：自我學習系統，根據過去經驗自動調整交易門檻")

    return "\n".join(lines)


def generate_chinese_summary(report_df, fg_val):
    """舊版精簡摘要（相容保留，供純文字報告使用）"""
    buy_opps = report_df[report_df["Signal"] == "BUY"]
    sell_opps = report_df[report_df["Signal"] == "SELL"]
    
    summary = []
    summary.append("🤖 *Antigravity 市場掃描週報*")
    summary.append(f"📊 市場情緒：*{fg_val}* (恐懼與貪婪指數)")
    summary.append("----------------------------")
    
    if buy_opps.empty and sell_opps.empty:
        summary.append("✅ 目前大盤暫無強烈訊號，建議先觀望。")
    
    if not buy_opps.empty:
        summary.append(f"🚀 偵測到 *{len(buy_opps)}* 個潛在買入機會：")
        for _, row in buy_opps.iterrows():
            summary.append(f"• *{row['Symbol']}* | 價格: NT${row['Price(TWD)']} | 信心度: {row['Confidence']}")
            if row['RSI'] != 'N/A' and float(row['RSI']) < 30:
                summary.append(f"  (⚠️ 指標超跌: RSI {row['RSI']})")
    
    if not sell_opps.empty:
        summary.append(f"\n🔻 偵測到 *{len(sell_opps)}* 個賣出警報：")
        for _, row in sell_opps.iterrows():
            summary.append(f"• *{row['Symbol']}* | 價格: NT${row['Price(TWD)']} | 信心度: {row['Confidence']}")
            if row['RSI'] != 'N/A' and float(row['RSI']) > 70:
                summary.append(f"  (⚠️ 指標超買: RSI {row['RSI']})")
    
    summary.append("----------------------------")
    summary.append("📚 *專有名詞小百科*：")
    summary.append("• *信心度 (Confidence)*：系統參謀(均線、MACD等)的共識強度。越接近 1 代表越看好。")
    summary.append("• *RSI*：市場的體力值。< 30 代表「超跌」(可能反彈)；> 70 代表「超買」(可能回檔)。")
    summary.append("----------------------------")
    
    return "\n".join(summary)

def scan_markets():
    settings = Settings()
    exchange = ccxt.binance({"enableRateLimit": True})
    engine = build_decision_engine(settings, market_name="crypto")
    ind_engine = IndicatorEngine()
    tracker = SignalTracker()
    ctx_analyzer = MarketContextAnalyzer(exchange)

    # 0. 多角度市場背景分析
    logger.info("🔍 Analyzing market context (multi-timeframe, dominance, phase, macro)...")
    market_ctx = ctx_analyzer.analyze()
    logger.info(f"📊 Market Phase: {market_ctx.phase} | Season: {market_ctx.season} | MTF: {market_ctx.mtf_alignment}")

    # SOL: 載入學習偏差並判斷當前環境
    sol_optimizer = ContextualOptimizer()
    sol_bias = sol_optimizer.get_bias()
    is_blocked, block_reason = sol_bias.should_block(
        market_ctx.phase, market_ctx.season, market_ctx.fg_3d_trend
    )
    sol_agreement = sol_bias.get_dynamic_agreement(
        market_ctx.phase, market_ctx.season, market_ctx.fg_3d_trend
    )
    env_key = f"{market_ctx.phase}|{market_ctx.season}|{market_ctx.fg_3d_trend}"
    if is_blocked:
        logger.warning(f"🚫 SOL: 當前環境 [{env_key}] 被標記為有毒，BUY 信號將被否決")
    elif sol_agreement != 0.55:
        logger.info(f"🧠 SOL: 當前環境 [{env_key}] 動態門檻 = {sol_agreement:.2f}")
    else:
        logger.info(f"🧠 SOL: 當前環境 [{env_key}] 維持預設門檻")
    sol_interventions = 0  # 追蹤 SOL 介入次數

    # Read strategy params from config for indicator sync
    strat_config = settings.get_market_strategies("crypto")
    rsi_period = strat_config.rsi.params.get("period", 14)
    sma_fast = strat_config.sma_crossover.params.get("fast_period", 10)
    sma_slow = strat_config.sma_crossover.params.get("slow_period", 50)
    macd_fast = strat_config.macd.params.get("fast", 12)
    macd_slow = strat_config.macd.params.get("slow", 26)
    macd_signal = strat_config.macd.params.get("signal", 9)
    bb_period = strat_config.bollinger.params.get("period", 20)
    bb_std = strat_config.bollinger.params.get("std_dev", 2.0)
    rsi_col = f"RSI_{rsi_period}"
    
    # 1. Fetch data shared across all markets
    # BTC for Regime Filter
    btc_ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1h", limit=150)
    btc_df = pd.DataFrame(btc_ohlcv, columns=["timestamp", "open", "high", "low", "btc_close", "volume"])
    btc_df["timestamp"] = pd.to_datetime(btc_df["timestamp"], unit="ms")
    
    # Sentiment (Fear & Greed)
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=10)
        fg_val = float(resp.json()["data"][0]["value"])
    except:
        fg_val = 50.0 # Default neutral
        
    # 2. Get Targets
    top_symbols = get_top_symbols(exchange)
    logger.info(f"🔍 Starting scan for {len(top_symbols)} symbols...")
    
    # Get USDT/TWD rate
    try:
        usdt_twd = float(os.getenv("USDT_TWD_RATE", 31.4))
    except:
        usdt_twd = 31.4
        
    notifier = TelegramNotifier()
    results = []
    raw_data_list = []  # [v0.6.4] 收集原始指標數據用於多面向看板
    
    # 長線指標參數 (1d 用更寬的均線週期)
    long_rsi_period = 14
    long_sma_fast = 10
    long_sma_slow = 50
    long_rsi_col = f"RSI_{long_rsi_period}"

    for symbol in top_symbols:
        try:
            logger.info(f"Processing {symbol}...")
            # ── 短線 (1h) ──────────────────────────────────
            ohlcv = exchange.fetch_ohlcv(symbol, "1h", limit=100)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            
            df = pd.merge(df, btc_df[["timestamp", "btc_close"]], on="timestamp", how="left")
            df["sentiment_value"] = fg_val
            
            fr_market = symbol + ":USDT" if ":" not in symbol else symbol
            try:
                fr = exchange.fetch_funding_rate(fr_market)
                df["funding_rate"] = fr["fundingRate"]
            except:
                df["funding_rate"] = 0.0
                
            df = ind_engine.add_all(
                df,
                rsi_period=rsi_period,
                sma_periods=[sma_fast, sma_slow],
                macd_params=(macd_fast, macd_slow, macd_signal),
                bb_params=(bb_period, bb_std),
            )
            
            decision = engine.make_decision(df, symbol)

            # SOL: 環境偏差過濾 (只過濾短線信號)
            final_signal = decision.final_signal.value
            sol_note = ""
            if final_signal == "BUY" and is_blocked:
                final_signal = "HOLD"
                sol_note = " [SOL:blocked]"
                sol_interventions += 1
                logger.warning(f"🚫 SOL overrode {symbol} BUY→HOLD ({block_reason})")
            elif final_signal == "BUY" and decision.confidence < sol_agreement:
                final_signal = "HOLD"
                sol_note = f" [SOL:low-conf<{sol_agreement:.2f}]"
                sol_interventions += 1
                logger.info(f"🧠 SOL overrode {symbol} BUY→HOLD (confidence {decision.confidence:.2f} < SOL threshold {sol_agreement:.2f})")

            last_price = df["close"].iloc[-1]
            last_price_twd = last_price * usdt_twd

            # ── 長線 (1d) ──────────────────────────────────
            signal_1d = "HOLD"
            conf_1d = "0.00"
            rsi_1d = "N/A"
            try:
                ohlcv_1d = exchange.fetch_ohlcv(symbol, "1d", limit=120)
                df_1d = pd.DataFrame(ohlcv_1d, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df_1d["timestamp"] = pd.to_datetime(df_1d["timestamp"], unit="ms")
                df_1d = pd.merge(df_1d, btc_df[["timestamp", "btc_close"]].drop_duplicates("timestamp"), on="timestamp", how="left")
                # Forward-fill BTC close for daily bars that don't match hourly timestamps
                df_1d["btc_close"] = df_1d["btc_close"].fillna(method="ffill").fillna(btc_df["btc_close"].iloc[-1])
                df_1d["sentiment_value"] = fg_val
                df_1d["funding_rate"] = 0.0

                df_1d = ind_engine.add_all(
                    df_1d,
                    rsi_period=long_rsi_period,
                    sma_periods=[long_sma_fast, long_sma_slow],
                    macd_params=(12, 26, 9),
                    bb_params=(20, 2.0),
                )
                dec_1d = engine.make_decision(df_1d, symbol)
                signal_1d = dec_1d.final_signal.value
                conf_1d = f"{dec_1d.confidence:.2f}"
                if long_rsi_col in df_1d.columns and not pd.isna(df_1d[long_rsi_col].iloc[-1]):
                    rsi_1d = f"{df_1d[long_rsi_col].iloc[-1]:.1f}"
            except Exception as e:
                logger.debug(f"  1d fetch for {symbol} skipped: {e}")

            results.append({
                "Symbol": symbol,
                "Price": last_price,
                "Price(TWD)": f"{last_price_twd:.2f}",
                "Signal": final_signal,
                "Confidence": f"{decision.confidence:.2f}",
                "RSI": f"{df[rsi_col].iloc[-1]:.1f}" if rsi_col in df.columns else "N/A",
                "Reason": decision.reason + sol_note,
                "Signal_1d": signal_1d,
                "Confidence_1d": conf_1d,
                "RSI_1d": rsi_1d,
            })

            # [v0.6.4] 收集原始指標數據
            sma_fast_col = f"SMA_{sma_fast}"
            sma_slow_col = f"SMA_{sma_slow}"
            sma_fast_val = float(df[sma_fast_col].iloc[-1]) if sma_fast_col in df.columns and not pd.isna(df[sma_fast_col].iloc[-1]) else None
            sma_slow_val = float(df[sma_slow_col].iloc[-1]) if sma_slow_col in df.columns and not pd.isna(df[sma_slow_col].iloc[-1]) else None
            # SMA 斜率 (最近 5 根的變化率)
            sma_slope = 0.0
            if sma_slow_col in df.columns and len(df[sma_slow_col].dropna()) >= 5:
                s5 = df[sma_slow_col].iloc[-5]
                s1 = df[sma_slow_col].iloc[-1]
                if s5 and not pd.isna(s5) and s5 != 0:
                    sma_slope = (s1 - s5) / s5

            rsi_val = float(df[rsi_col].iloc[-1]) if rsi_col in df.columns and not pd.isna(df[rsi_col].iloc[-1]) else None
            macd_hist_now = float(df["MACD_Hist"].iloc[-1]) if "MACD_Hist" in df.columns else 0.0
            macd_hist_prev = float(df["MACD_Hist"].iloc[-2]) if "MACD_Hist" in df.columns and len(df) > 1 else 0.0

            bb_upper = float(df["BB_Upper"].iloc[-1]) if "BB_Upper" in df.columns and not pd.isna(df["BB_Upper"].iloc[-1]) else None
            bb_lower = float(df["BB_Lower"].iloc[-1]) if "BB_Lower" in df.columns and not pd.isna(df["BB_Lower"].iloc[-1]) else None
            bb_pct_b = None
            if bb_upper is not None and bb_lower is not None and (bb_upper - bb_lower) > 0:
                bb_pct_b = (last_price - bb_lower) / (bb_upper - bb_lower)
            
            bb_width_now = float(df["BB_Width"].iloc[-1]) if "BB_Width" in df.columns and not pd.isna(df["BB_Width"].iloc[-1]) else 0
            bb_width_prev = float(df["BB_Width"].iloc[-2]) if "BB_Width" in df.columns and len(df) > 1 and not pd.isna(df["BB_Width"].iloc[-2]) else 0

            fr_val = float(df["funding_rate"].iloc[-1]) if "funding_rate" in df.columns else 0.0

            raw_data_list.append({
                "symbol": symbol,
                "price": last_price,
                "rsi": rsi_val,
                "macd_hist": macd_hist_now,
                "macd_hist_prev": macd_hist_prev,
                "sma_fast": sma_fast_val,
                "sma_slow": sma_slow_val,
                "sma_slope": sma_slope,
                "bb_pct_b": bb_pct_b,
                "bb_width": bb_width_now,
                "bb_width_prev": bb_width_prev,
                "funding_rate": fr_val,
            })
            
        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}")
            
    # 3. Output Report
    report_df = pd.DataFrame(results)
    
    # Sort: BUY first, then SELL, then HOLD
    priority_map = {"BUY": 0, "SELL": 1, "HOLD": 2}
    report_df["priority"] = report_df["Signal"].apply(lambda x: priority_map.get(x, 2))
    report_df = report_df.sort_values(by="priority").drop(columns=["priority"])
    
    # Generate Chinese Summary for all outputs
    chinese_summary = generate_chinese_summary(report_df, fg_val)
    market_ctx_text = market_ctx.summary()
    market_ctx_tg = market_ctx.telegram_block()
    
    report_header = f"\n{'='*80}\n📊 CRYPTO TOP 20 SCANNER REPORT - {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nMarket Sentiment: {fg_val} (Fear & Greed)\n{'='*80}\n"
    report_table = report_df.to_string(index=False)
    report_footer = "\n" + "="*80 + "\n"

    # Market context block appears first in all outputs
    full_report = (
        report_header
        + "\n" + market_ctx_text + "\n"
        + "\n" + chinese_summary + "\n\n"
        + "-"*80 + "\n" + report_table + report_footer
    )
    print(full_report)
    
    # Save to file
    with open("scripts/last_scan_report.txt", "w", encoding="utf-8") as f:
        f.write(full_report)
    
    # 4. Signal Tracking + SOL (must run BEFORE notification so texts are available)
    tracker.verify_past_signals(exchange)
    tracker.record_signals_from_report(report_df, fg_val, market_ctx=market_ctx)
    win_rate_text = tracker.get_summary_text()
    logger.info(win_rate_text)

    # 5. SOL: 持續學習 + 更新偏差 (reuse existing optimizer)
    sol_bias = sol_optimizer.analyze_and_update()
    sol_text = sol_optimizer.get_report_text()
    if sol_interventions > 0:
        sol_text += f"\n⚡ 本輪 SOL 介入：{sol_interventions} 筆信號被調整"
    logger.info(sol_text)

    # 6. Generate full report (always print to CMD + optionally send to Telegram)
    full_telegram_msg = generate_full_report(
        report_df, fg_val,
        market_ctx=market_ctx,
        perf_text=win_rate_text,
        sol_text=sol_text,
    )

    # 在 CMD 顯示完整報告
    print("\n" + "=" * 80)
    print("📱 完整掃描報告 (與 Telegram 相同內容)")
    print("=" * 80)
    print(full_telegram_msg)
    print("=" * 80 + "\n")

    # [v0.6.4] 多面向資訊看板 (CMD + Telegram)
    dimension_msg = generate_dimension_report(raw_data_list, market_ctx, fg_val, usdt_twd)
    print("\n" + "=" * 80)
    print("📊 多面向資訊看板")
    print("=" * 80)
    print(dimension_msg)
    print("=" * 80 + "\n")

    # 存檔多面向看板
    with open("scripts/last_dimension_report.txt", "w", encoding="utf-8") as f:
        f.write(dimension_msg)

    if notifier.enabled:
        notifier.send_message(full_telegram_msg)
        # [v0.6.4] 也發送多面向看板到 Telegram
        notifier.send_message(dimension_msg)
        
        buy_opps = report_df[report_df["Signal"] == "BUY"]
        sell_opps = report_df[report_df["Signal"] == "SELL"]
        has_signals = not buy_opps.empty or not sell_opps.empty
        
        if not buy_opps.empty:
            logger.warning(f"🚀 Found {len(buy_opps)} BUY opportunities: {buy_opps['Symbol'].tolist()}")
        if not sell_opps.empty:
            logger.warning(f"🔻 Found {len(sell_opps)} SELL alerts: {sell_opps['Symbol'].tolist()}")
        
        if has_signals:
            notifier.send_report("scripts/last_scan_report.txt")
    
    return report_df

def main():
    parser = argparse.ArgumentParser(description="Multi-Asset Crypto Scanner")
    parser.add_argument("--loop", action="store_true", help="Run in continuous loop mode")
    parser.add_argument("--interval", type=int, default=300, help="Interval in seconds (default 300s)")
    args = parser.parse_args()
    
    if args.loop:
        logger.info(f"🔄 Starting Continuous Monitoring Loop (Interval: {args.interval}s)")
        while True:
            try:
                scan_markets()
                logger.info(f"Sleeping for {args.interval}s...")
                time.sleep(args.interval)
            except KeyboardInterrupt:
                logger.info("Monitoring stopped by user.")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(60)
    else:
        scan_markets()

if __name__ == "__main__":
    main()
