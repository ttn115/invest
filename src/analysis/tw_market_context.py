"""
台股市場背景分析器 (Taiwan Stock Market Context Analyzer)

從 3 個角度分析台股環境：
1. 加權指數趨勢 (TAIEX: ^TWII SMA + RSI)
2. 三大法人買賣超 (TWSE Institutional Investors)
3. 大盤量能 (Volume vs Average)

使用方式：
    from src.analysis.tw_market_context import TwMarketContextAnalyzer
    analyzer = TwMarketContextAnalyzer()
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


# ── 股票名稱對照表 ─────────────────────────────────────────
TW_STOCK_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科",
    "2882": "國泰金", "2881": "富邦金", "2603": "長榮",
    "2002": "中鋼", "3711": "日月光", "2412": "中華電",
    "6214": "精誠", "2308": "台達電",
}


@dataclass
class TwMarketContext:
    """台股市場背景"""
    timestamp: str = ""

    # 角度 1：加權指數
    taiex_close: float = 0.0
    taiex_rsi: float = 50.0
    taiex_above_sma50: bool = False
    taiex_above_sma200: bool = False
    taiex_trend: str = "UNKNOWN"       # "UP" / "DOWN" / "NEUTRAL"
    taiex_phase: str = "UNKNOWN"       # "BULL" / "BEAR" / "RECOVERY" / "DISTRIBUTION"

    # 角度 2：三大法人
    foreign_buy_sell: float = 0.0      # 外資買賣超 (億元)
    trust_buy_sell: float = 0.0        # 投信買賣超 (億元)
    dealer_buy_sell: float = 0.0       # 自營商買賣超 (億元)
    institutional_sentiment: str = "UNKNOWN"  # "BULLISH" / "BEARISH" / "NEUTRAL"

    # 角度 3：量能
    volume_ratio: float = 1.0          # 今日成交量 / 20日均量
    volume_status: str = "NORMAL"      # "HIGH" / "LOW" / "NORMAL"

    def summary(self) -> str:
        """純文字摘要"""
        lines = [
            f"📅 台股市場背景 ({self.timestamp})",
            "━" * 38,
        ]

        # 加權指數
        phase_desc = {
            "BULL": "🐂 多頭走勢", "BEAR": "🐻 空頭走勢",
            "RECOVERY": "🌱 底部回升", "DISTRIBUTION": "📦 高檔整理",
        }.get(self.taiex_phase, "❓ 未知")
        lines.append(f"加權指數：{self.taiex_close:,.0f} — {phase_desc}")
        lines.append(f"  RSI: {self.taiex_rsi:.1f} | 趨勢: {self.taiex_trend}")

        # 三大法人
        if self.institutional_sentiment != "UNKNOWN":
            sent_desc = {
                "BULLISH": "🟢 法人偏多", "BEARISH": "🔴 法人偏空", "NEUTRAL": "⚪ 法人中性"
            }.get(self.institutional_sentiment, "")
            lines.append(f"三大法人：{sent_desc}")
            lines.append(
                f"  外資: {self.foreign_buy_sell:+.1f}億 | "
                f"投信: {self.trust_buy_sell:+.1f}億 | "
                f"自營: {self.dealer_buy_sell:+.1f}億"
            )

        # 量能
        vol_desc = {"HIGH": "🔥 量增", "LOW": "❄️ 量縮", "NORMAL": "📊 正常"}.get(self.volume_status, "")
        lines.append(f"成交量能：{vol_desc} (量比: {self.volume_ratio:.2f}x)")

        lines.append("━" * 38)
        lines.append(f"→ {self._get_recommendation()}")
        return "\n".join(lines)

    def telegram_block(self) -> str:
        """Telegram 格式區塊"""
        phase_emoji = {"BULL": "🐂", "BEAR": "🐻", "RECOVERY": "🌱", "DISTRIBUTION": "📦"}.get(self.taiex_phase, "❓")
        sent_icon = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}.get(self.institutional_sentiment, "❓")
        vol_icon = {"HIGH": "🔥", "LOW": "❄️", "NORMAL": "📊"}.get(self.volume_status, "📊")

        lines = [
            "🇹🇼 *台股市場背景*",
            f"{phase_emoji} 加權：{self.taiex_close:,.0f} RSI:{self.taiex_rsi:.0f} {self.taiex_trend}",
            f"{sent_icon} 法人：外資 {self.foreign_buy_sell:+.1f}億 | 投信 {self.trust_buy_sell:+.1f}億",
            f"{vol_icon} 量能：{self.volume_ratio:.1f}x 均量",
            f"💬 {self._get_recommendation()}",
            "---",
        ]
        return "\n".join(lines)

    def _get_recommendation(self) -> str:
        if self.taiex_phase == "BEAR" and self.institutional_sentiment == "BEARISH":
            return "空頭 + 法人賣超，建議觀望不追買"
        if self.taiex_phase == "BEAR":
            return "大盤偏空，選股需嚴格篩選"
        if self.taiex_phase == "BULL" and self.institutional_sentiment == "BULLISH":
            return "多頭 + 法人加碼，可積極操作強勢股"
        if self.taiex_phase == "RECOVERY":
            return "底部回升跡象，可留意法人回補標的"
        if self.taiex_phase == "DISTRIBUTION":
            return "高檔量增，注意獲利了結風險"
        return "盤勢混沌，建議輕倉觀察"


class TwMarketContextAnalyzer:
    """台股市場背景分析器"""

    def analyze(self) -> TwMarketContext:
        ctx = TwMarketContext(
            timestamp=dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        self._analyze_taiex(ctx)
        self._analyze_institutional(ctx)
        return ctx

    def _analyze_taiex(self, ctx: TwMarketContext):
        """分析加權指數 (^TWII) 的趨勢與 RSI"""
        if not HAS_YFINANCE:
            logger.warning("yfinance not available, skipping TAIEX analysis")
            return

        try:
            ticker = yf.Ticker("^TWII")
            hist = ticker.history(period="1y")

            if hist.empty:
                logger.warning("TAIEX data empty")
                return

            close = hist["Close"]
            ctx.taiex_close = float(close.iloc[-1])

            # RSI
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi_series = 100 - (100 / (1 + rs))
            ctx.taiex_rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

            # SMA
            sma50 = close.rolling(50).mean()
            sma200 = close.rolling(200).mean()
            ctx.taiex_above_sma50 = bool(close.iloc[-1] > sma50.iloc[-1]) if not pd.isna(sma50.iloc[-1]) else False
            ctx.taiex_above_sma200 = bool(close.iloc[-1] > sma200.iloc[-1]) if not pd.isna(sma200.iloc[-1]) else False

            # Trend (5-bar slope)
            recent = close.iloc[-5:].values
            slope = float(np.polyfit(range(len(recent)), recent, 1)[0])
            ctx.taiex_trend = "UP" if slope > 0 else ("DOWN" if slope < 0 else "NEUTRAL")

            # Phase
            if ctx.taiex_above_sma50 and ctx.taiex_above_sma200:
                ctx.taiex_phase = "DISTRIBUTION" if ctx.taiex_rsi > 70 else "BULL"
            elif ctx.taiex_above_sma50 and not ctx.taiex_above_sma200:
                ctx.taiex_phase = "RECOVERY"
            elif not ctx.taiex_above_sma50 and not ctx.taiex_above_sma200:
                ctx.taiex_phase = "RECOVERY" if ctx.taiex_rsi < 35 else "BEAR"
            else:
                ctx.taiex_phase = "BEAR"

            # Volume ratio
            vol = hist["Volume"]
            avg_vol_20 = vol.rolling(20).mean().iloc[-1]
            if avg_vol_20 > 0:
                ctx.volume_ratio = float(vol.iloc[-1] / avg_vol_20)
                if ctx.volume_ratio > 1.3:
                    ctx.volume_status = "HIGH"
                elif ctx.volume_ratio < 0.7:
                    ctx.volume_status = "LOW"
                else:
                    ctx.volume_status = "NORMAL"

            logger.debug(f"TAIEX: {ctx.taiex_close:.0f} RSI={ctx.taiex_rsi:.1f} phase={ctx.taiex_phase}")

        except Exception as e:
            logger.warning(f"TAIEX analysis error: {e}")

    def _analyze_institutional(self, ctx: TwMarketContext):
        """
        嘗試從 TWSE 取得三大法人買賣超。
        API: https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json
        """
        try:
            today = dt.datetime.now()
            # TWSE API 用 yyyyMMdd 格式
            date_str = today.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?date={date_str}&response=json"

            resp = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "zh-TW,zh;q=0.9",
            })
            data = resp.json()

            if data.get("stat") != "OK" or not data.get("data"):
                # 可能假日或盤前，嘗試前一個交易日
                yesterday = today - dt.timedelta(days=1)
                date_str = yesterday.strftime("%Y%m%d")
                url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?date={date_str}&response=json"
                resp = requests.get(url, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept-Language": "zh-TW,zh;q=0.9",
                })
                data = resp.json()

            if data.get("stat") == "OK" and data.get("data"):
                rows = data["data"]
                # rows 格式: [[名稱, 買進, 賣出, 買賣差額], ...]
                # 通常: 0=自營商(自行買賣), 1=自營商(避險), 2=投信, 3=外資及陸資(不含外資自營商)...
                # 最後一列是合計
                for row in rows:
                    name = row[0].replace(" ", "").replace(",", "")
                    # 買賣差額在最後一個欄位，去除逗號
                    try:
                        net = float(row[-1].replace(",", "")) / 100_000_000  # 轉換為億
                    except (ValueError, IndexError):
                        continue

                    if "外資" in name and "自營" not in name:
                        ctx.foreign_buy_sell = net
                    elif "投信" in name:
                        ctx.trust_buy_sell = net
                    elif "自營商" in name and "避險" not in name and "合計" not in name:
                        ctx.dealer_buy_sell = net

                # 判斷法人情緒
                total = ctx.foreign_buy_sell + ctx.trust_buy_sell + ctx.dealer_buy_sell
                if total > 10:  # 合計買超 10 億以上
                    ctx.institutional_sentiment = "BULLISH"
                elif total < -10:
                    ctx.institutional_sentiment = "BEARISH"
                else:
                    ctx.institutional_sentiment = "NEUTRAL"

                logger.info(
                    f"🏛️ 三大法人: 外資 {ctx.foreign_buy_sell:+.1f}億 | "
                    f"投信 {ctx.trust_buy_sell:+.1f}億 | "
                    f"自營 {ctx.dealer_buy_sell:+.1f}億 → {ctx.institutional_sentiment}"
                )
            else:
                logger.info("🏛️ 三大法人資料尚未公布（盤中或假日）")
                ctx.institutional_sentiment = "UNKNOWN"

        except Exception as e:
            logger.warning(f"Institutional data error: {e}")
            ctx.institutional_sentiment = "UNKNOWN"
