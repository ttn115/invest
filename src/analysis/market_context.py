"""
多角度市場分析引擎 (Multi-Angle Market Context Analyzer)

從 4 個視角分析市場環境：
1. 多周期確認 (Multi-Timeframe: 1h + 4h + 1d)
2. BTC 主導性 (BTC Dominance / Alt Season)
3. 市場階段 (Bull / Bear / Recovery / Distribution)
4. 宏觀環境 (Fear & Greed Trend + DXY)

使用方式：
    from src.analysis.market_context import MarketContextAnalyzer
    analyzer = MarketContextAnalyzer(exchange)
    ctx = analyzer.analyze()
    print(ctx.summary())
"""

from __future__ import annotations
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import numpy as np
import requests
from loguru import logger

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False


# ────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────

@dataclass
class TimeframeSignal:
    """單一時間框架的技術狀態"""
    timeframe: str
    rsi: float
    trend: str          # "UP" / "DOWN" / "NEUTRAL"
    above_sma50: bool
    above_sma200: bool

    @property
    def direction(self) -> str:
        if self.trend == "UP" and self.rsi > 45:
            return "BUY"
        elif self.trend == "DOWN" and self.rsi < 55:
            return "SELL"
        return "HOLD"


@dataclass
class MarketContext:
    """完整的多角度市場背景"""
    timestamp: str = ""

    # 角度 1：多周期
    tf_1h: Optional[TimeframeSignal] = None
    tf_4h: Optional[TimeframeSignal] = None
    tf_1d: Optional[TimeframeSignal] = None
    mtf_alignment: str = "UNKNOWN"      # "STRONG_BUY" / "WEAK_BUY" / "NEUTRAL" / "WEAK_SELL" / "STRONG_SELL"
    mtf_score: int = 0                   # -3 ~ +3

    # 角度 2：BTC 主導性
    btc_7d_pct: float = 0.0
    alt_7d_pct: float = 0.0             # 前 10 山寨平均
    season: str = "UNKNOWN"             # "BTC_SEASON" / "ALT_SEASON" / "MIXED"

    # 角度 3：市場階段
    phase: str = "UNKNOWN"              # "BULL_RUN" / "DISTRIBUTION" / "BEAR" / "RECOVERY"
    phase_emoji: str = "❓"

    # 角度 4：宏觀
    fg_current: float = 50.0
    fg_3d_trend: str = "FLAT"           # "RISING" / "FALLING" / "FLAT"
    fg_3d_values: list = field(default_factory=list)
    dxy: float = 0.0                    # 美元指數
    dxy_trend: str = "UNKNOWN"          # "STRONG" / "WEAK" / "NEUTRAL"

    def summary(self) -> str:
        """生成人類可讀的市場背景摘要（純文字）"""
        lines = []
        lines.append(f"📅 市場背景分析 ({self.timestamp})")
        lines.append("━" * 38)

        # 角度 3：市場階段 (最重要，擺最前)
        phase_desc = {
            "BULL_RUN": "🐂 牛市加速 — 動能強勁",
            "DISTRIBUTION": "📦 分配頂部 — 謹慎操作",
            "BEAR": "🐻 熊市下跌 — 空頭趨勢",
            "RECOVERY": "🌱 底部復甦 — 等待確認",
            "UNKNOWN": "❓ 階段未知",
        }
        lines.append(f"市場階段：{phase_desc.get(self.phase, self.phase)}")

        # 角度 2：BTC 主導性
        season_desc = {
            "BTC_SEASON": f"🟠 BTC 季節 (BTC 7d: {self.btc_7d_pct:+.1f}% vs ALT: {self.alt_7d_pct:+.1f}%)",
            "ALT_SEASON": f"🟢 山寨季節 (BTC 7d: {self.btc_7d_pct:+.1f}% vs ALT: {self.alt_7d_pct:+.1f}%)",
            "MIXED": f"⚪ 混合走勢 (BTC 7d: {self.btc_7d_pct:+.1f}% vs ALT: {self.alt_7d_pct:+.1f}%)",
            "UNKNOWN": "主導性：資料不足",
        }
        lines.append(f"主導性：  {season_desc.get(self.season, self.season)}")

        # 角度 4：宏觀
        fg_arrow = {"RISING": "↗", "FALLING": "↘", "FLAT": "→"}.get(self.fg_3d_trend, "?")
        fg_vals = "→".join(str(int(v)) for v in self.fg_3d_values[-3:]) if self.fg_3d_values else str(int(self.fg_current))
        lines.append(f"恐懼指數：{int(self.fg_current)} {fg_arrow} 趨勢({fg_vals})")
        if self.dxy > 0:
            dxy_desc = "美元偏強，加密幣承壓" if self.dxy_trend == "STRONG" else "美元偏弱，加密幣偏利多" if self.dxy_trend == "WEAK" else "美元中性"
            lines.append(f"美元 DXY： {self.dxy:.1f} — {dxy_desc}")

        # 角度 1：多周期
        mtf_desc = {
            "STRONG_BUY": "✅ 1h/4h/1d 全面看漲，強烈買入信號",
            "WEAK_BUY": "⚡ 多數周期看漲，短線機會",
            "NEUTRAL": "⚠️ 周期方向分歧，建議觀望",
            "WEAK_SELL": "🔴 多數周期看跌，謹慎偏空",
            "STRONG_SELL": "🔴 1h/4h/1d 全面看跌，強烈賣出信號",
            "UNKNOWN": "周期確認：資料不足",
        }
        if self.tf_1h:
            tf_line = f"1h RSI:{self.tf_1h.rsi:.0f}/{self.tf_1h.trend} | "
        else:
            tf_line = ""
        if self.tf_4h:
            tf_line += f"4h RSI:{self.tf_4h.rsi:.0f}/{self.tf_4h.trend} | "
        if self.tf_1d:
            tf_line += f"1d RSI:{self.tf_1d.rsi:.0f}/{self.tf_1d.trend}"
        lines.append(f"周期確認：{mtf_desc.get(self.mtf_alignment, self.mtf_alignment)}")
        if tf_line:
            lines.append(f"  ({tf_line.rstrip(' | ')})")

        # 建議
        lines.append("━" * 38)
        lines.append(f"→ {self._get_recommendation()}")
        return "\n".join(lines)

    def telegram_block(self) -> str:
        """生成 Telegram 格式的市場背景區塊"""
        phase_emoji = {
            "BULL_RUN": "🐂", "DISTRIBUTION": "📦",
            "BEAR": "🐻", "RECOVERY": "🌱", "UNKNOWN": "❓",
        }.get(self.phase, "❓")

        fg_arrow = {"RISING": "↗", "FALLING": "↘", "FLAT": "→"}.get(self.fg_3d_trend, "→")
        season_icon = {"BTC_SEASON": "🟠", "ALT_SEASON": "🟢", "MIXED": "⚪"}.get(self.season, "⚪")
        mtf_icon = {
            "STRONG_BUY": "✅✅", "WEAK_BUY": "✅",
            "NEUTRAL": "⚠️", "WEAK_SELL": "🔴", "STRONG_SELL": "🔴🔴"
        }.get(self.mtf_alignment, "❓")

        lines = [
            "🌐 *市場背景*",
            f"{phase_emoji} 階段：{self.phase.replace('_', ' ')}",
            f"{season_icon} 主導：BTC {self.btc_7d_pct:+.1f}% | ALT {self.alt_7d_pct:+.1f}%",
            f"😱 恐懼指數：{int(self.fg_current)} {fg_arrow}",
            f"{mtf_icon} 周期共識：{self.mtf_alignment.replace('_', ' ')}",
            f"💬 {self._get_recommendation()}",
            "---",
        ]
        return "\n".join(lines)

    def _get_recommendation(self) -> str:
        """根據多角度分析給出操作建議"""
        if self.phase == "BEAR" and self.fg_3d_trend == "FALLING":
            return "當前空頭 + 恐懼加深，不建議追買，等待反轉訊號"
        if self.phase == "BEAR":
            return "大盤空頭，即使有技術訊號也需嚴格篩選"
        if self.phase == "RECOVERY" and self.mtf_alignment in ("WEAK_BUY", "STRONG_BUY"):
            return "復甦初期 + 多周期看漲，可小倉佈局強勢幣種"
        if self.phase == "BULL_RUN" and self.mtf_alignment == "STRONG_BUY":
            return "牛市 + 全面看漲，可積極跟進高信心信號"
        if self.phase == "DISTRIBUTION":
            return "分配頂部，獲利了結為主，不追高"
        if self.mtf_alignment == "NEUTRAL":
            return "周期分歧，建議等待方向確立再行動"
        return "市場信號混合，謹慎操作"


# ────────────────────────────────────────────────────────────
# Analyzer
# ────────────────────────────────────────────────────────────

class MarketContextAnalyzer:
    """多角度市場背景分析器"""

    ALT_COINS = ["ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
                 "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT",
                 "LTC/USDT", "TRX/USDT"]

    def __init__(self, exchange):
        self.exchange = exchange

    def analyze(self, fg_history: list[float] | None = None) -> MarketContext:
        """
        執行全部 4 個角度的分析。

        Args:
            fg_history: 近幾天的恐懼貪婪指數，用於趨勢計算（可選）
        """
        ctx = MarketContext(
            timestamp=dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        )

        # 角度 1：多周期確認
        self._analyze_multi_timeframe(ctx)

        # 角度 2：BTC 主導性
        self._analyze_dominance(ctx)

        # 角度 3：市場階段
        self._analyze_market_phase(ctx)

        # 角度 4：宏觀環境
        self._analyze_macro(ctx, fg_history)

        return ctx

    # ── 角度 1：多周期 ────────────────────────────────────────

    def _analyze_multi_timeframe(self, ctx: MarketContext):
        """分析 BTC 在 1h / 4h / 1d 三個時間框架的狀態"""
        for tf, attr in [("1h", "tf_1h"), ("4h", "tf_4h"), ("1d", "tf_1d")]:
            try:
                ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", tf, limit=220)
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])

                df["rsi"] = self._calc_rsi(df["close"], 14)
                df["sma50"] = df["close"].rolling(50).mean()
                df["sma200"] = df["close"].rolling(200).mean()

                last = df.iloc[-1]
                prev = df.iloc[-2]

                rsi_val = float(last["rsi"]) if not pd.isna(last["rsi"]) else 50.0
                above50 = bool(last["close"] > last["sma50"]) if not pd.isna(last["sma50"]) else False
                above200 = bool(last["close"] > last["sma200"]) if not pd.isna(last["sma200"]) else False

                # 趨勢：最後 3 根平均斜率
                recent_close = df["close"].iloc[-5:].values
                slope = float(np.polyfit(range(len(recent_close)), recent_close, 1)[0])
                if slope > 0 and last["close"] > prev["close"]:
                    trend = "UP"
                elif slope < 0 and last["close"] < prev["close"]:
                    trend = "DOWN"
                else:
                    trend = "NEUTRAL"

                sig = TimeframeSignal(
                    timeframe=tf, rsi=rsi_val, trend=trend,
                    above_sma50=above50, above_sma200=above200
                )
                setattr(ctx, attr, sig)
                logger.debug(f"MTF [{tf}]: RSI={rsi_val:.1f} trend={trend}")

            except Exception as e:
                logger.warning(f"MTF [{tf}] error: {e}")

        # 計算對齊分數
        score = 0
        for attr in ["tf_1h", "tf_4h", "tf_1d"]:
            sig = getattr(ctx, attr)
            if sig is None:
                continue
            d = sig.direction
            if d == "BUY":
                score += 1
            elif d == "SELL":
                score -= 1

        ctx.mtf_score = score
        if score >= 3:
            ctx.mtf_alignment = "STRONG_BUY"
        elif score == 2:
            ctx.mtf_alignment = "WEAK_BUY"
        elif score == -2:
            ctx.mtf_alignment = "WEAK_SELL"
        elif score <= -3:
            ctx.mtf_alignment = "STRONG_SELL"
        else:
            ctx.mtf_alignment = "NEUTRAL"

    # ── 角度 2：BTC 主導性 ────────────────────────────────────

    def _analyze_dominance(self, ctx: MarketContext):
        """比較 BTC 和山寨幣 7 日表現，判斷資金流向"""
        try:
            # BTC 7 日報酬
            btc_ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", "1d", limit=9)
            btc_df = pd.DataFrame(btc_ohlcv, columns=["ts", "o", "h", "l", "close", "v"])
            ctx.btc_7d_pct = float((btc_df["close"].iloc[-1] / btc_df["close"].iloc[-8] - 1) * 100)

            # 山寨幣平均 7 日報酬
            alt_returns = []
            for sym in self.ALT_COINS[:6]:  # 只取 6 個避免太慢
                try:
                    ohlcv = self.exchange.fetch_ohlcv(sym, "1d", limit=9)
                    df = pd.DataFrame(ohlcv, columns=["ts", "o", "h", "l", "close", "v"])
                    ret = (df["close"].iloc[-1] / df["close"].iloc[-8] - 1) * 100
                    alt_returns.append(float(ret))
                except Exception:
                    continue

            if alt_returns:
                ctx.alt_7d_pct = float(np.mean(alt_returns))

            # 判斷季節
            diff = ctx.btc_7d_pct - ctx.alt_7d_pct
            if diff > 5:
                ctx.season = "BTC_SEASON"      # BTC 大幅跑贏山寨
            elif diff < -5:
                ctx.season = "ALT_SEASON"       # 山寨大幅跑贏 BTC
            else:
                ctx.season = "MIXED"

            logger.debug(f"Dominance: BTC 7d={ctx.btc_7d_pct:.1f}% ALT={ctx.alt_7d_pct:.1f}% season={ctx.season}")

        except Exception as e:
            logger.warning(f"Dominance analysis error: {e}")

    # ── 角度 3：市場階段 ──────────────────────────────────────

    def _analyze_market_phase(self, ctx: MarketContext):
        """
        使用 BTC 1d 線判斷市場所在的週期階段。
        
        四種階段：
        - BULL_RUN:      價格 > SMA50 & SMA200，且 RSI > 55
        - DISTRIBUTION:  價格 > SMA50，但 RSI > 70，或價格接近 SMA50 從上方跌破
        - BEAR:          價格 < SMA50 且 < SMA200
        - RECOVERY:      價格 < SMA200 但已反彈，RSI 從超低回升
        """
        try:
            # 用 1d 判斷階段
            if ctx.tf_1d is None:
                ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", "1d", limit=220)
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
                df["rsi"] = self._calc_rsi(df["close"], 14)
                df["sma50"] = df["close"].rolling(50).mean()
                df["sma200"] = df["close"].rolling(200).mean()
                last = df.iloc[-1]
                rsi = float(last["rsi"]) if not pd.isna(last["rsi"]) else 50.0
                above50 = bool(last["close"] > last["sma50"]) if not pd.isna(last["sma50"]) else False
                above200 = bool(last["close"] > last["sma200"]) if not pd.isna(last["sma200"]) else False
            else:
                rsi = ctx.tf_1d.rsi
                above50 = ctx.tf_1d.above_sma50
                above200 = ctx.tf_1d.above_sma200

            if above50 and above200:
                if rsi > 70:
                    ctx.phase = "DISTRIBUTION"
                    ctx.phase_emoji = "📦"
                else:
                    ctx.phase = "BULL_RUN"
                    ctx.phase_emoji = "🐂"
            elif above50 and not above200:
                # SMA50 上方但 SMA200 下方 → 可能是熊市反彈
                ctx.phase = "RECOVERY"
                ctx.phase_emoji = "🌱"
            elif not above50 and not above200:
                if rsi < 35:
                    ctx.phase = "RECOVERY"   # 超跌反彈信號
                    ctx.phase_emoji = "🌱"
                else:
                    ctx.phase = "BEAR"
                    ctx.phase_emoji = "🐻"
            else:
                ctx.phase = "BEAR"
                ctx.phase_emoji = "🐻"

            logger.debug(f"Phase: {ctx.phase} (above50={above50}, above200={above200}, RSI={rsi:.1f})")

        except Exception as e:
            logger.warning(f"Phase analysis error: {e}")

    # ── 角度 4：宏觀環境 ─────────────────────────────────────

    def _analyze_macro(self, ctx: MarketContext, fg_history: list[float] | None = None):
        """分析 Fear & Greed 趨勢 和 美元指數 DXY"""
        # FNG 趨勢
        try:
            resp = requests.get(
                "https://api.alternative.me/fng/?limit=7",
                timeout=10
            )
            data = resp.json()["data"]
            values = [float(d["value"]) for d in reversed(data)]  # 由舊到新

            ctx.fg_current = values[-1]
            ctx.fg_3d_values = values[-3:]

            if len(values) >= 3:
                recent = values[-3:]
                delta = recent[-1] - recent[0]
                if delta > 3:
                    ctx.fg_3d_trend = "RISING"
                elif delta < -3:
                    ctx.fg_3d_trend = "FALLING"
                else:
                    ctx.fg_3d_trend = "FLAT"

        except Exception as e:
            logger.warning(f"FNG trend error: {e}")
            if fg_history:
                ctx.fg_3d_values = fg_history[-3:]
                ctx.fg_current = fg_history[-1]

        # DXY 美元指數（透過 yfinance）
        if HAS_YFINANCE:
            try:
                dxy = yf.Ticker("DX-Y.NYB")
                hist = dxy.history(period="5d")
                if not hist.empty:
                    ctx.dxy = float(hist["Close"].iloc[-1])
                    prev_dxy = float(hist["Close"].iloc[0])
                    dxy_chg = (ctx.dxy - prev_dxy) / prev_dxy * 100
                    if dxy_chg > 0.5:
                        ctx.dxy_trend = "STRONG"
                    elif dxy_chg < -0.5:
                        ctx.dxy_trend = "WEAK"
                    else:
                        ctx.dxy_trend = "NEUTRAL"
                    logger.debug(f"DXY: {ctx.dxy:.1f} trend={ctx.dxy_trend}")
            except Exception as e:
                logger.warning(f"DXY fetch error: {e}")
        else:
            logger.debug("yfinance not available, skipping DXY")

    # ── 工具函數 ─────────────────────────────────────────────

    @staticmethod
    def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """計算 RSI"""
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(window=period).mean()
        loss = (-delta.clip(upper=0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))
