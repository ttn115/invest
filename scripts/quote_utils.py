"""
即時報價與資料新鮮度工具 (Quote Freshness Utilities)

解決問題：yfinance 日線 history() 取 Close.iloc[-1] 在美股收盤時段
回傳的是「前一交易日收盤」，與券商即時/盤前報價有落差。本工具補上最新
成交價、市場狀態與落差警告，讓分析腳本能標示資料截止時間並使用最新價。

主要函式：
    get_fresh_quote(symbol_or_ticker) -> dict
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

try:
    import pytz
    _ET_TZ = pytz.timezone("America/New_York")
except ImportError:
    pytz = None
    _ET_TZ = None


def _us_market_status(now_et: dt.datetime) -> str:
    """判斷美股市場狀態：REGULAR / PRE / POST / CLOSED（ET 時區）。"""
    if now_et.weekday() >= 5:          # 週六日
        return "CLOSED"
    t = now_et.time()
    pre_open   = dt.time(4, 0)
    reg_open   = dt.time(9, 30)
    reg_close  = dt.time(16, 0)
    post_close = dt.time(20, 0)
    if reg_open <= t < reg_close:
        return "REGULAR"
    if pre_open <= t < reg_open:
        return "PRE"
    if reg_close <= t < post_close:
        return "POST"
    return "CLOSED"


def _read_fast_info_price(ticker) -> float:
    """從 fast_info 取最新價，相容不同 yfinance 版本（屬性 / dict-like）。"""
    try:
        fi = ticker.fast_info
    except Exception:
        return float("nan")
    # 新版屬性存取
    for attr in ("last_price", "lastPrice"):
        try:
            v = getattr(fi, attr)
            if v:
                return float(v)
        except Exception:
            pass
    # dict-like 存取
    for key in ("lastPrice", "last_price", "regularMarketPrice"):
        try:
            v = fi.get(key)  # type: ignore[attr-defined]
            if v:
                return float(v)
        except Exception:
            pass
    return float("nan")


def get_fresh_quote(symbol_or_ticker, daily_close=None, daily_date=None) -> dict:
    """
    取得某美股標的的「日線收盤」與「最新成交價」並計算落差。

    Args:
        symbol_or_ticker: 股票代碼字串（如 "GOLD"）或 yfinance Ticker 物件
        daily_close: （選填）呼叫端已算出的「指標所用日線收盤」。傳入可確保
                     橫幅顯示的收盤價與指標計算基準完全一致；不傳則自行抓 5d。
        daily_date:  （選填）對應 daily_close 的日期字串 (YYYY-MM-DD)

    Returns:
        dict 含：
          daily_close   float  最後一根已完成日線收盤
          daily_date    str    該日線日期 (YYYY-MM-DD)
          live_price    float  最新成交價（含盤前/盤後；無則回退日線收盤）
          live_ts       str    最新成交時間（本地可讀字串）
          live_source   str    'intraday' / 'fast_info' / 'daily_fallback'
          market_status str    REGULAR / PRE / POST / CLOSED / UNKNOWN
          staleness_min float  最新報價距現在的分鐘數
          divergence_pct float (live - daily_close)/daily_close * 100
          warn          str    需提醒時的字串，否則 ""
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol_or_ticker) if isinstance(symbol_or_ticker, str) else symbol_or_ticker

    out = {
        "daily_close": float("nan"), "daily_date": "N/A",
        "live_price": float("nan"),  "live_ts": "N/A",
        "live_source": "none", "market_status": "UNKNOWN",
        "staleness_min": float("nan"), "divergence_pct": float("nan"),
        "warn": "",
    }

    # ── 1. 已完成日線收盤 ─────────────────────────────────────────
    if daily_close is not None and not pd.isna(daily_close) and daily_close > 0:
        # 呼叫端已提供（與其指標基準一致），直接採用
        out["daily_close"] = float(daily_close)
        out["daily_date"]  = daily_date or "N/A"
    else:
        try:
            d = ticker.history(period="5d", interval="1d")
            if not d.empty:
                # yfinance 偶爾在尾端帶一根尚未完成 / NaN 的日線，先濾掉
                closes = d["Close"].dropna()
                if not closes.empty:
                    out["daily_close"] = float(closes.iloc[-1])
                    out["daily_date"]  = closes.index[-1].strftime("%Y-%m-%d")
        except Exception:
            pass

    # ── 2. 最新成交價（盤中分鐘線 → fast_info → 日線回退）─────────
    live_price = float("nan")
    live_ts_dt = None
    try:
        m = ticker.history(period="2d", interval="1m", prepost=True)
        if not m.empty:
            live_price = float(m["Close"].iloc[-1])
            live_ts_dt = m.index[-1]
            out["live_source"] = "intraday"
    except Exception:
        pass

    if pd.isna(live_price) or live_price <= 0:
        fp = _read_fast_info_price(ticker)
        if not pd.isna(fp) and fp > 0:
            live_price = fp
            live_ts_dt = dt.datetime.now()
            out["live_source"] = "fast_info"

    if pd.isna(live_price) or live_price <= 0:
        live_price = out["daily_close"]
        live_ts_dt = None
        out["live_source"] = "daily_fallback"

    out["live_price"] = live_price

    # ── 3. 市場狀態 + 新鮮度 ─────────────────────────────────────
    if _ET_TZ is not None:
        now_et = dt.datetime.now(_ET_TZ)
        out["market_status"] = _us_market_status(now_et)

    if live_ts_dt is not None:
        # 統一為可讀字串；若帶時區轉成 ET 顯示
        try:
            if live_ts_dt.tzinfo is not None and _ET_TZ is not None:
                et = live_ts_dt.astimezone(_ET_TZ)
                out["live_ts"] = et.strftime("%Y-%m-%d %H:%M ET")
                now_ref = dt.datetime.now(_ET_TZ)
                out["staleness_min"] = (now_ref - et).total_seconds() / 60.0
            else:
                out["live_ts"] = live_ts_dt.strftime("%Y-%m-%d %H:%M")
                out["staleness_min"] = (dt.datetime.now() - live_ts_dt.replace(tzinfo=None)).total_seconds() / 60.0
        except Exception:
            out["live_ts"] = str(live_ts_dt)

    # ── 4. 落差與警告 ─────────────────────────────────────────────
    dc = out["daily_close"]
    if not pd.isna(dc) and dc > 0 and not pd.isna(live_price):
        div = (live_price - dc) / dc * 100
        out["divergence_pct"] = div
        if out["live_source"] != "daily_fallback" and abs(div) > 1.5:
            arrow = "▲" if div > 0 else "▼"
            out["warn"] = (f"即時價較日線收盤 {arrow}{abs(div):.1f}%"
                           f"（指標係以 {out['daily_date']} 收盤計算）")

    return out


def freshness_banner(symbol: str, q: dict) -> list[str]:
    """產生資料新鮮度橫幅（list of lines），供 console 與 .md 共用。"""
    ms = {"REGULAR": "盤中", "PRE": "盤前", "POST": "盤後",
          "CLOSED": "已收盤", "UNKNOWN": "未知"}.get(q["market_status"], q["market_status"])
    live = q["live_price"]
    dc   = q["daily_close"]
    src  = {"intraday": "即時", "fast_info": "即時(fast)",
            "daily_fallback": "日線回退"}.get(q["live_source"], q["live_source"])
    lines = [
        f"📡 資料新鮮度 [{symbol}] | "
        f"{src}價 ${live:.2f} ({ms} {q['live_ts']}) | "
        f"日線收盤 ${dc:.2f} ({q['daily_date']})"
    ]
    if q["warn"]:
        lines.append(f"⚠️ {q['warn']}")
    return lines


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    syms = sys.argv[1:] or ["GOLD", "GLD", "GDX", "NEM"]
    for s in syms:
        q = get_fresh_quote(s)
        print("\n".join(freshness_banner(s, q)))
        print(f"   divergence={q['divergence_pct']:.2f}%  staleness={q['staleness_min']:.0f}min  source={q['live_source']}")
