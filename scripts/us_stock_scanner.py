"""
美股掃描器 (US Stock Scanner)

功能：
- 掃描 30 支美股標的，產生 BUY/SELL/HOLD 建議
- 每 30 分鐘掃描一次（NYSE 交易時段 09:30~16:00 ET）
- 整合美股市場背景（SPY 趨勢 + VIX 恐慌指數 + 10Y 殖利率）
- 推送 Telegram 通知
- SOL 背景標籤化（與加密幣/台股共用信號追蹤）

用法：
    python scripts/us_stock_scanner.py            # 單次掃描
    python scripts/us_stock_scanner.py --loop      # 持續掃描 (30min)
    python scripts/us_stock_scanner.py --symbols AAPL MSFT NVDA  # 指定標的
"""

import sys
import os
import argparse
import time
import datetime as dt
import pytz
import pandas as pd
from dataclasses import dataclass
from loguru import logger

# Windows UTF-8 fix
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from src.config.settings import Settings
from src.data.collector import StockCollector
from src.data.indicators import IndicatorEngine
from src.main import build_decision_engine
from src.monitor.notifier import TelegramNotifier
from src.monitor.signal_tracker import SignalTracker
from src.analysis.contextual_optimizer import ContextualOptimizer
from src.strategy.fundamental_screener import FundamentalScreener
from report_writer import update_market_section, build_us_lines

logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

# ── 觀測名單：從 data/watchlists.json 讀取（用 watchlist_manager.py 管理）──
def _load_us_watchlist() -> list[str]:
    import json
    wl_file = os.path.join(os.path.dirname(__file__), "..", "data", "watchlists.json")
    try:
        with open(wl_file, encoding="utf-8") as f:
            data = json.load(f)
        symbols = [s["ticker"] for s in data["us_stock"]["symbols"]]
        logger.info(f"📋 讀取觀察名單：{len(symbols)} 支美股（data/watchlists.json）")
        return symbols
    except Exception as e:
        logger.warning(f"⚠️  watchlists.json 讀取失敗，使用內建備用清單：{e}")
        # ── 備用硬編碼清單（watchlists.json 損毀時的保障）────────────────
        return [
    # 科技 / 半導體
    "AAPL",   # Apple — 生態系護城河
    "MSFT",   # Microsoft — 企業軟體 + 雲端
    "NVDA",   # NVIDIA — AI 晶片壟斷
    "AVGO",   # Broadcom — 網路晶片
    "TSM",    # 台積電 ADR — 晶圓代工壟斷
    # 消費 / 零售
    "AMZN",   # Amazon — 物流 + 雲端
    "COST",   # Costco — 蒙格最愛，持股至去世
    "KO",     # Coca-Cola — 巴菲特芒格長期持有
    "WMT",    # Walmart — 規模護城河
    # 金融
    "BRK-B",  # Berkshire — 蒙格自己的公司
    "V",      # Visa — 支付網路
    "MA",     # Mastercard — 支付網路
    "JPM",    # JPMorgan — 大型銀行
    "AXP",    # American Express — 長期持有
    # 醫療 / 生技
    "JNJ",    # J&J — 分散醫療
    "UNH",    # UnitedHealth — 醫療管理
    "LLY",    # Eli Lilly — GLP-1 / 肥胖藥
    # 能源 / 原物料
    "XOM",    # ExxonMobil — 能源巨頭
    "CVX",    # Chevron — 巴菲特持倉
    # 工業 / 基礎設施
    "CAT",    # Caterpillar — 基建受惠
    "DE",     # Deere — 農機壟斷
    "UPS",    # UPS — 物流網絡
    # 通信 / 媒體
    "GOOGL",  # Alphabet — 搜尋壟斷
    "META",   # Meta — 社群廣告
    # ETF（大盤參考）
    "SPY",    # S&P 500
    "QQQ",    # Nasdaq 100
    "XLF",    # 金融板塊 ETF
    "XLE",    # 能源板塊 ETF
    "XLK",    # 科技板塊 ETF
    "GLD",    # 黃金 ETF（避險指標）
]


US_WATCHLIST = _load_us_watchlist()

SCAN_INTERVAL = 1800   # 30 分鐘
ET_TZ = pytz.timezone("America/New_York")


# ── 美股市場背景 ───────────────────────────────────────────────

@dataclass
class UsMarketContext:
    """美股市場背景快照"""
    timestamp: str = ""

    # SPY 大盤
    spy_close: float = 0.0
    spy_rsi: float = 50.0
    spy_above_sma50: bool = False
    spy_above_sma200: bool = False
    spy_trend: str = "UNKNOWN"           # UP / DOWN / NEUTRAL
    spy_phase: str = "UNKNOWN"           # BULL / BEAR / RECOVERY / DISTRIBUTION

    # 恐慌指數
    vix: float = 20.0
    vix_level: str = "NORMAL"           # LOW / NORMAL / ELEVATED / EXTREME

    # 利率環境
    yield_10y: float = 4.5
    rate_env: str = "UNKNOWN"           # RISING / FALLING / STABLE

    # 黑天鵝指數
    skew: float = 130.0
    skew_level: str = "NORMAL"

    # 綜合情緒
    market_sentiment: str = "NEUTRAL"   # BULLISH / BEARISH / NEUTRAL / FEAR

    def vix_emoji(self) -> str:
        return {"LOW": "😎", "NORMAL": "😐", "ELEVATED": "😟", "EXTREME": "😱"}.get(self.vix_level, "❓")

    def phase_emoji(self) -> str:
        return {"BULL": "🐂", "BEAR": "🐻", "RECOVERY": "🌱", "DISTRIBUTION": "📦"}.get(self.spy_phase, "❓")

    def summary(self) -> str:
        lines = [
            f"美股市場背景 ({self.timestamp})",
            f"  SPY: ${self.spy_close:.2f} | Phase: {self.spy_phase} | Trend: {self.spy_trend}",
            f"  VIX: {self.vix:.1f} ({self.vix_level}) | 10Y: {self.yield_10y:.2f}%",
            f"  Sentiment: {self.market_sentiment}",
        ]
        return "\n".join(lines)

    def telegram_block(self) -> str:
        lines = [
            f"📅 *美股市場背景* ({self.timestamp})",
            "━" * 36,
            f"市場階段：{self.phase_emoji()} {self.spy_phase}",
            f"SPY：${self.spy_close:.2f} | SMA50: {'✅上方' if self.spy_above_sma50 else '❌下方'} | SMA200: {'✅上方' if self.spy_above_sma200 else '❌下方'}",
            f"VIX：{self.vix_emoji()} {self.vix:.1f} ({self.vix_level})",
            f"10Y 殖利率：{self.yield_10y:.2f}% ({self.rate_env})",
            f"整體情緒：{self.market_sentiment}",
            "━" * 36,
        ]
        return "\n".join(lines)


class UsMarketContextAnalyzer:
    """分析美股市場背景（SPY + VIX + 10Y）"""

    def analyze(self) -> UsMarketContext:
        ctx = UsMarketContext(
            timestamp=dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        try:
            import yfinance as yf

            # SPY 大盤分析
            spy_df = yf.Ticker("SPY").history(period="1y", interval="1d")
            if not spy_df.empty:
                close = spy_df["Close"]
                ctx.spy_close = float(close.iloc[-1])
                sma50 = close.rolling(50).mean().iloc[-1]
                sma200 = close.rolling(200).mean().iloc[-1]
                ctx.spy_above_sma50 = ctx.spy_close > sma50
                ctx.spy_above_sma200 = ctx.spy_close > sma200

                # RSI
                delta = close.diff()
                gain = delta.clip(lower=0).rolling(14).mean()
                loss = (-delta.clip(upper=0)).rolling(14).mean()
                rs = gain / (loss + 1e-9)
                ctx.spy_rsi = float(100 - 100 / (1 + rs.iloc[-1]))

                # Phase
                if ctx.spy_above_sma50 and ctx.spy_above_sma200:
                    slope = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20]
                    ctx.spy_phase = "DISTRIBUTION" if slope < 0.01 else "BULL"
                elif not ctx.spy_above_sma50 and not ctx.spy_above_sma200:
                    ctx.spy_phase = "BEAR"
                else:
                    ctx.spy_phase = "RECOVERY"

                # Trend
                slope5 = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]
                if slope5 > 0.01:
                    ctx.spy_trend = "UP"
                elif slope5 < -0.01:
                    ctx.spy_trend = "DOWN"
                else:
                    ctx.spy_trend = "NEUTRAL"

            # VIX
            vix_df = yf.Ticker("^VIX").history(period="5d", interval="1d")
            if not vix_df.empty:
                ctx.vix = float(vix_df["Close"].iloc[-1])
                if ctx.vix < 15:
                    ctx.vix_level = "LOW"
                elif ctx.vix < 20:
                    ctx.vix_level = "NORMAL"
                elif ctx.vix < 30:
                    ctx.vix_level = "ELEVATED"
                else:
                    ctx.vix_level = "EXTREME"

            # 10Y 殖利率
            tnx_df = yf.Ticker("^TNX").history(period="30d", interval="1d")
            if not tnx_df.empty:
                ctx.yield_10y = float(tnx_df["Close"].iloc[-1])
                slope = tnx_df["Close"].iloc[-1] - tnx_df["Close"].iloc[-10]
                if slope > 0.1:
                    ctx.rate_env = "RISING"
                elif slope < -0.1:
                    ctx.rate_env = "FALLING"
                else:
                    ctx.rate_env = "STABLE"

            # SKEW 指數
            skew_df = yf.Ticker("^SKEW").history(period="5d", interval="1d")
            if not skew_df.empty:
                ctx.skew = float(skew_df["Close"].iloc[-1])
                ctx.skew_level = "HIGH RISK" if ctx.skew > 140 else "NORMAL"

            # 綜合情緒
            if ctx.vix_level == "EXTREME" or ctx.spy_phase == "BEAR":
                ctx.market_sentiment = "FEAR"
            elif ctx.vix_level in ("LOW", "NORMAL") and ctx.spy_phase in ("BULL", "DISTRIBUTION"):
                ctx.market_sentiment = "BULLISH" if ctx.spy_trend == "UP" else "NEUTRAL"
            elif ctx.spy_phase == "RECOVERY":
                ctx.market_sentiment = "CAUTIOUS_OPTIMISM"
            else:
                ctx.market_sentiment = "NEUTRAL"

        except Exception as e:
            logger.warning(f"UsMarketContext analysis error: {e}")

        return ctx


# ── 報告產生 ───────────────────────────────────────────────────

def generate_us_report(report_df: pd.DataFrame, us_ctx: UsMarketContext = None,
                       perf_text: str = "", sol_text: str = "") -> str:
    lines = []

    # 區塊 1：市場背景
    if us_ctx:
        lines.append(us_ctx.telegram_block())

    # 區塊 2：信號警報
    buy_opps = report_df[report_df["Signal"] == "BUY"]
    sell_opps = report_df[report_df["Signal"] == "SELL"]

    if buy_opps.empty and sell_opps.empty:
        lines.append("✅ 美股暫無強烈訊號，建議觀望")

    if not buy_opps.empty:
        lines.append(f"🚀 *買入機會 ({len(buy_opps)} 支)*")
        for _, row in buy_opps.iterrows():
            try:
                rsi_val = float(row["RSI"])
                rsi_tag = " ⚠️超跌" if rsi_val < 30 else ""
            except (ValueError, TypeError):
                rsi_tag = ""
            lines.append(
                f"  • *{row['Symbol']}* ${row['Price']:.2f} | "
                f"信心:{row['Confidence']} RSI:{row['RSI']}{rsi_tag}"
            )

    if not sell_opps.empty:
        lines.append(f"🔻 *賣出警報 ({len(sell_opps)} 支)*")
        for _, row in sell_opps.iterrows():
            try:
                rsi_val = float(row["RSI"])
                rsi_tag = " ⚠️超買" if rsi_val > 70 else ""
            except (ValueError, TypeError):
                rsi_tag = ""
            lines.append(
                f"  • *{row['Symbol']}* ${row['Price']:.2f} | "
                f"信心:{row['Confidence']} RSI:{row['RSI']}{rsi_tag}"
            )

    # 區塊 3：全股一覽
    lines.append("")
    lines.append("📋 *美股觀測名單*")
    for _, row in report_df.iterrows():
        sig = row["Signal"]
        sig_icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(sig, "⚪")
        lines.append(
            f"  {sig_icon} *{row['Symbol']}* ${row['Price']:.2f} "
            f"RSI:{row['RSI']} {sig}"
        )

    # 區塊 4：績效 + SOL
    if perf_text:
        lines.append("")
        lines.append(perf_text)
    if sol_text:
        lines.append("")
        lines.append(sol_text)

    # 區塊 5：名詞說明
    lines.append("")
    lines.append("📚 *名詞說明*")
    lines.append("• *VIX*：恐慌指數，>30=市場恐慌，<15=過度樂觀")
    lines.append("• *10Y*：美國 10 年期殖利率，上升=緊縮環境，下降=寬鬆")
    lines.append("• *Phase*：BULL=牛市 / BEAR=熊市 / RECOVERY=復甦 / DISTRIBUTION=分配頂部")

    return "\n".join(lines)


# ── 交易時段判斷 ────────────────────────────────────────────────

def is_us_market_hours() -> bool:
    """檢查是否在 NYSE 交易時段 (09:30~16:00 ET，週一至週五)"""
    now_et = dt.datetime.now(ET_TZ)
    if now_et.weekday() >= 5:
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def next_market_open_str() -> str:
    """取得下一個 NYSE 開盤時間（台灣時間）"""
    now_et = dt.datetime.now(ET_TZ)
    next_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_et >= next_open or now_et.weekday() >= 5:
        days_ahead = 1
        while (now_et + dt.timedelta(days=days_ahead)).weekday() >= 5:
            days_ahead += 1
        next_open = (now_et + dt.timedelta(days=days_ahead)).replace(
            hour=9, minute=30, second=0, microsecond=0)
    tw_tz = pytz.timezone("Asia/Taipei")
    next_open_tw = next_open.astimezone(tw_tz)
    return next_open_tw.strftime("%Y-%m-%d %H:%M (台灣時間)")


# ── 主掃描邏輯 ─────────────────────────────────────────────────

def scan_us_stocks(symbols: list[str] = None):
    """執行一次美股掃描"""
    watchlist = symbols or US_WATCHLIST
    logger.info(f"🇺🇸 開始美股掃描 ({len(watchlist)} 支)...")

    settings = Settings()
    collector = StockCollector()
    ind_engine = IndicatorEngine()
    notifier = TelegramNotifier()
    tracker = SignalTracker()

    # 1. 美股市場背景分析
    logger.info("📊 分析美股市場背景 (SPY + VIX + 10Y)...")
    us_ctx_analyzer = UsMarketContextAnalyzer()
    us_ctx = us_ctx_analyzer.analyze()
    logger.info(
        f"📊 SPY: ${us_ctx.spy_close:.2f} | Phase: {us_ctx.spy_phase} | "
        f"VIX: {us_ctx.vix:.1f} ({us_ctx.vix_level}) | 10Y: {us_ctx.yield_10y:.2f}%"
    )

    # SOL：載入學習偏差
    sol_optimizer = ContextualOptimizer(market_type="stock")
    sol_bias = sol_optimizer.get_bias()
    is_blocked, block_reason = sol_bias.should_block(
        us_ctx.spy_phase, "MIXED", us_ctx.spy_trend
    )
    sol_agreement = sol_bias.get_dynamic_agreement(
        us_ctx.spy_phase, "MIXED", us_ctx.spy_trend
    )
    env_key = f"{us_ctx.spy_phase}|MIXED|{us_ctx.spy_trend}"
    if is_blocked:
        logger.warning(f"🚫 SOL: 當前環境 [{env_key}] 被標記為有毒，BUY 信號將被否決")
    else:
        logger.info(f"🧠 SOL: 環境 [{env_key}] 門檻={sol_agreement:.2f}")
    sol_interventions = 0

    # 1b. 批次收集芒格基本面分數（非阻塞，失敗不影響主流程）
    munger_scores: dict = {}
    munger_profiles: dict = {}
    try:
        logger.info("🧐 收集芒格基本面分數...")
        screener = FundamentalScreener(cache_ttl_hours=24.0)
        for sym in watchlist:
            try:
                profile = screener.screen(sym, market="us_stock")
                munger_profiles[sym] = profile
                if profile.verdict == "TOO_HARD":
                    munger_scores[sym] = "—"
                else:
                    verdict_icon = "✅" if profile.verdict == "PASS" else "❌"
                    munger_scores[sym] = f"{verdict_icon}{profile.munger_score:.0f}"
            except Exception:
                munger_scores[sym] = "—"
        logger.info(f"🧐 芒格分數收集完成: {len(munger_scores)} 支")
    except Exception as e:
        logger.warning(f"芒格分數收集失敗（不影響主掃描）: {e}")

    # 2. 決策引擎（使用 us_stock 設定）
    try:
        engine = build_decision_engine(settings, market_name="us_stock")
    except Exception as e:
        logger.warning(f"無法建立 us_stock 引擎，使用全域預設: {e}")
        engine = build_decision_engine(settings)

    # 讀取策略參數
    try:
        strat_config = settings.get_market_strategies("us_stock")
    except Exception:
        strat_config = settings.get_market_strategies("crypto")

    rsi_period = strat_config.rsi.params.get("period", 14)
    sma_fast = strat_config.sma_crossover.params.get("fast_period", 10)
    sma_slow = strat_config.sma_crossover.params.get("slow_period", 50)
    macd_fast = strat_config.macd.params.get("fast", 12)
    macd_slow = strat_config.macd.params.get("slow", 26)
    macd_signal_p = strat_config.macd.params.get("signal", 9)
    bb_period = strat_config.bollinger.params.get("period", 20)
    bb_std = strat_config.bollinger.params.get("std_dev", 2.0)
    rsi_col = f"RSI_{rsi_period}"

    # 3. 掃描每支股票
    results = []
    for symbol in watchlist:
        try:
            logger.info(f"  處理 {symbol}...")
            df = collector.fetch_ohlcv(symbol=symbol, timeframe="1d", limit=300)
            if df.empty:
                logger.warning(f"  ⚠️ {symbol} 無資料")
                continue

            # 填入決策引擎需要的欄位
            df["sentiment_value"] = us_ctx.vix          # VIX 作為情緒代理
            df["btc_close"] = us_ctx.spy_close           # SPY 作為大盤代理
            df["funding_rate"] = 0.0

            # 計算指標
            df = ind_engine.add_all(
                df,
                rsi_period=rsi_period,
                sma_periods=[sma_fast, sma_slow],
                macd_params=(macd_fast, macd_slow, macd_signal_p),
                bb_params=(bb_period, bb_std),
            )

            decision = engine.make_decision(df, symbol)
            final_signal = decision.final_signal.value
            sol_note = ""

            # SOL 環境過濾
            if final_signal == "BUY" and is_blocked:
                final_signal = "HOLD"
                sol_note = " [SOL:blocked]"
                sol_interventions += 1
                logger.warning(f"🚫 SOL: {symbol} BUY→HOLD ({block_reason})")
            elif final_signal == "BUY" and decision.confidence < sol_agreement:
                final_signal = "HOLD"
                sol_note = f" [SOL:low-conf<{sol_agreement:.2f}]"
                sol_interventions += 1
                logger.info(f"🧠 SOL: {symbol} BUY→HOLD (conf {decision.confidence:.2f} < {sol_agreement:.2f})")

            # Munger Filter：RECOVERY + RSI < 25 禁止 SELL
            if final_signal == "SELL" and us_ctx.spy_phase == "RECOVERY":
                current_rsi = float(df[rsi_col].iloc[-1]) if rsi_col in df.columns else 50.0
                if current_rsi < 25.0:
                    final_signal = "HOLD"
                    sol_note += f" [Munger:RECOVERY+RSI{current_rsi:.0f}<25→HOLD]"
                    sol_interventions += 1
                    logger.warning(f"🧠 Munger: {symbol} SELL→HOLD (RECOVERY+RSI={current_rsi:.1f}<25)")

            last_price = float(df["close"].iloc[-1])
            rsi_val = (
                f"{df[rsi_col].iloc[-1]:.1f}"
                if rsi_col in df.columns and not pd.isna(df[rsi_col].iloc[-1])
                else "N/A"
            )

            results.append({
                "Symbol": symbol,
                "Price": last_price,
                "Signal": final_signal,
                "Confidence": f"{decision.confidence:.2f}",
                "RSI": rsi_val,
                "Reason": decision.reason + sol_note,
            })

        except Exception as e:
            logger.error(f"  ❌ {symbol} 錯誤: {e}")

    if not results:
        logger.error("無法取得任何美股資料")
        return

    report_df = pd.DataFrame(results)

    # 排序：BUY > SELL > HOLD
    priority = {"BUY": 0, "SELL": 1, "HOLD": 2}
    report_df["_p"] = report_df["Signal"].map(priority).fillna(2)
    report_df = report_df.sort_values("_p").drop(columns=["_p"])

    # 4. Console 報告
    header = (
        f"\n{'='*70}\n"
        f"🇺🇸 美股掃描報告 - {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{'='*70}\n"
    )
    table = report_df[["Symbol", "Price", "Signal", "Confidence", "RSI"]].to_string(index=False)
    full_report = header + us_ctx.summary() + "\n\n" + "-"*70 + "\n" + table + "\n" + "="*70
    print(full_report)

    # 保存
    report_path = os.path.join(os.path.dirname(__file__), "last_us_scan_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(full_report)
    logger.info(f"📄 報告已保存: {report_path}")

    # 5. Signal Tracking
    class UsContextBridge:
        """將 UsMarketContext 橋接為 SOL 相容格式"""
        def __init__(self, ctx: UsMarketContext):
            self.phase = ctx.spy_phase
            self.season = "MIXED"
            self.mtf_score = 1 if ctx.spy_trend == "UP" else (-1 if ctx.spy_trend == "DOWN" else 0)
            self.fg_3d_trend = ctx.spy_trend
            self.dxy_trend = ctx.rate_env

    bridge = UsContextBridge(us_ctx)
    tracker.verify_past_signals(None)

    actionable = report_df[report_df["Signal"].isin(["BUY", "SELL"])]
    for _, row in actionable.iterrows():
        tracker.record_signal(
            symbol=f"{row['Symbol']}.US",
            signal=row["Signal"],
            price=row["Price"],
            price_twd=f"{row['Price']:.2f}",
            confidence=row["Confidence"],
            rsi=row["RSI"],
            sentiment=us_ctx.vix,
            market_ctx=bridge,
        )

    win_rate_text = tracker.get_summary_text()
    logger.info(win_rate_text)

    # 6. SOL 報告
    sol_bias = sol_optimizer.analyze_and_update()
    sol_text = sol_optimizer.get_report_text()
    logger.info(sol_text)

    if sol_interventions:
        logger.info(f"🧠 SOL/Munger 共介入 {sol_interventions} 次")

    # 7. Telegram 報告
    us_msg = generate_us_report(
        report_df, us_ctx=us_ctx,
        perf_text=win_rate_text,
        sol_text=sol_text,
    )

    print("\n" + "=" * 70)
    print("📱 完整美股報告 (與 Telegram 相同內容)")
    print("=" * 70)
    print(us_msg)
    print("=" * 70 + "\n")

    if notifier.enabled:
        notifier.send_message(us_msg)
        logger.info("📱 美股報告已推送至 Telegram")

    # 更新統一市場看板
    try:
        perf_stats = tracker.get_performance_stats()
        md_lines = build_us_lines(
            report_df, us_ctx, win_rate_text,
            munger_scores=munger_scores or None,
            munger_profiles=munger_profiles or None,
            perf_stats=perf_stats,
        )
        update_market_section("us", md_lines)
        logger.info(f"📄 市場總覽已更新: data/market_dashboard.md")
    except Exception as e:
        logger.warning(f"Dashboard update failed: {e}")

    return report_df


# ── 主程式 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="US Stock Scanner")
    parser.add_argument("--loop", action="store_true",
                        help="持續掃描模式（NYSE 開盤時每 30 分鐘一次）")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="指定掃描標的，例如 AAPL MSFT NVDA")
    parser.add_argument("--force", action="store_true",
                        help="強制執行，忽略交易時段限制")
    args = parser.parse_args()

    symbols = args.symbols

    if args.loop:
        logger.info(f"🇺🇸 啟動美股持續掃描 (每 {SCAN_INTERVAL//60} 分鐘)")
        while True:
            try:
                in_hours = is_us_market_hours()
                now_et = dt.datetime.now(ET_TZ)

                if in_hours or args.force:
                    scan_us_stocks(symbols)
                    logger.info(f"💤 等待 {SCAN_INTERVAL//60} 分鐘...")
                else:
                    # 非交易時段：開盤前 5 分鐘做一次盤前分析
                    now_et_naive = now_et.replace(tzinfo=None)
                    premarket = now_et.replace(hour=9, minute=25, second=0, microsecond=0)
                    after_close = now_et.replace(hour=16, minute=5, second=0, microsecond=0)

                    if abs((now_et - premarket).total_seconds()) < 300:
                        logger.info("📊 盤前分析...")
                        scan_us_stocks(symbols)
                    elif abs((now_et - after_close).total_seconds()) < 300:
                        logger.info("📊 收盤後分析...")
                        scan_us_stocks(symbols)
                    else:
                        logger.info(f"⏰ 非交易時段。下次開盤: {next_market_open_str()}")

                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                logger.info("美股掃描已停止")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(60)
    else:
        scan_us_stocks(symbols)


if __name__ == "__main__":
    main()
