"""
GLD 黃金 ETF 深度分析腳本
抓取並計算黃金投資決策所需的完整數據集：
  - 技術面：多時框 RSI / MACD / Bollinger Bands / SMA 支撐壓力
  - 黃金驅動因子：實質利率(TIP) / DXY / GDX 礦商比 / 金銀比
  - 歷史位置：52週高低 / 當前分位
用法：python scripts/gld_deep_analysis.py
"""

import sys
import os
import datetime as dt
import pandas as pd
import numpy as np

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

try:
    import yfinance as yf
except ImportError:
    print("請先安裝 yfinance：pip install yfinance")
    sys.exit(1)


# ── 工具函數 ──────────────────────────────────────────────────────

def calc_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100 - 100 / (1 + rs)
    return float(rsi.iloc[-1])


def calc_macd(series: pd.Series):
    ema12 = series.ewm(span=12).mean()
    ema26 = series.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9).mean()
    histogram = macd_line - signal
    return float(macd_line.iloc[-1]), float(signal.iloc[-1]), float(histogram.iloc[-1])


def calc_bollinger(series: pd.Series, period: int = 20):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    price = float(series.iloc[-1])
    pct_b = float((price - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1] + 1e-9))
    return float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1]), pct_b


def correlation(s1: pd.Series, s2: pd.Series, window: int = 60) -> float:
    s1_ret = s1.pct_change().dropna()
    s2_ret = s2.pct_change().dropna()
    aligned = pd.concat([s1_ret, s2_ret], axis=1).dropna()
    if len(aligned) < window:
        return float("nan")
    return float(aligned.iloc[-window:].corr().iloc[0, 1])


def pct_from(current: float, target: float) -> str:
    pct = (target - current) / current * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


# ── 主分析 ────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("GLD 黃金 ETF 深度分析")
    print(f"執行時間：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    errors = []

    # ── 1. GLD 多時框數據 ─────────────────────────────────────────
    print("\n[1/5] 抓取 GLD 多時框數據...")
    gld_1d = yf.Ticker("GLD").history(period="2y", interval="1d")
    gld_1wk = yf.Ticker("GLD").history(period="5y", interval="1wk")
    gld_1h = yf.Ticker("GLD").history(period="30d", interval="1h")

    if gld_1d.empty:
        print("❌ 無法取得 GLD 數據")
        return

    close_1d = gld_1d["Close"]
    vol_1d = gld_1d["Volume"]
    close_1wk = gld_1wk["Close"]
    close_1h = gld_1h["Close"] if not gld_1h.empty else pd.Series(dtype=float)

    price = float(close_1d.iloc[-1])
    price_1w_ago = float(close_1d.iloc[-6]) if len(close_1d) >= 6 else price
    price_1m_ago = float(close_1d.iloc[-22]) if len(close_1d) >= 22 else price
    price_3m_ago = float(close_1d.iloc[-66]) if len(close_1d) >= 66 else price

    high_52w = float(close_1d.tail(252).max())
    low_52w  = float(close_1d.tail(252).min())
    pct_from_high = (price - high_52w) / high_52w * 100
    pct_from_low  = (price - low_52w)  / low_52w  * 100
    position_pct  = (price - low_52w)  / (high_52w - low_52w + 1e-9) * 100

    # SMA
    sma20  = float(close_1d.rolling(20).mean().iloc[-1])
    sma50  = float(close_1d.rolling(50).mean().iloc[-1])
    sma200 = float(close_1d.rolling(200).mean().iloc[-1])

    # RSI
    rsi_1d  = calc_rsi(close_1d, 14)
    rsi_1wk = calc_rsi(close_1wk, 14)
    rsi_1h  = calc_rsi(close_1h, 14) if len(close_1h) >= 15 else float("nan")

    # MACD (日線)
    macd_val, macd_sig, macd_hist = calc_macd(close_1d)
    macd_cross = "BULLISH" if macd_hist > 0 else "BEARISH"

    # Bollinger Bands (日線)
    bb_upper, bb_mid, bb_lower, pct_b = calc_bollinger(close_1d)

    # 成交量分析
    avg_vol_20 = float(vol_1d.tail(20).mean())
    today_vol  = float(vol_1d.iloc[-1])
    vol_ratio  = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

    # 週線 Bollinger
    bb_wk_upper, bb_wk_mid, bb_wk_lower, pct_b_wk = calc_bollinger(close_1wk, 20)

    print(f"  GLD 現價：${price:.2f}")
    print(f"  52週 高/低：${high_52w:.2f} / ${low_52w:.2f}  |  距高：{pct_from_high:.1f}%  |  距低：+{pct_from_low:.1f}%")
    print(f"  RSI 日/週/時：{rsi_1d:.1f} / {rsi_1wk:.1f} / {rsi_1h:.1f}")
    print(f"  MACD 直方：{macd_hist:.3f} ({macd_cross})")
    print(f"  BB %B：{pct_b:.2f}  (0=下軌, 1=上軌)")
    print(f"  SMA20/50/200：{sma20:.2f} / {sma50:.2f} / {sma200:.2f}")
    print(f"  成交量比：{vol_ratio:.2f}x")

    # ── 2. 黃金驅動因子 ───────────────────────────────────────────
    print("\n[2/5] 抓取黃金驅動因子...")

    # DXY（美元指數）
    dxy_df = yf.Ticker("DX-Y.NYB").history(period="1y", interval="1d")
    if dxy_df.empty:
        dxy_df = yf.Ticker("UUP").history(period="1y", interval="1d")  # fallback
    dxy_close = dxy_df["Close"] if not dxy_df.empty else pd.Series(dtype=float)
    dxy = float(dxy_close.iloc[-1]) if not dxy_close.empty else float("nan")
    dxy_rsi = calc_rsi(dxy_close) if len(dxy_close) >= 15 else float("nan")
    dxy_1m = float(dxy_close.iloc[-22]) if len(dxy_close) >= 22 else dxy
    dxy_chg_1m = (dxy - dxy_1m) / dxy_1m * 100 if dxy_1m else float("nan")
    gld_dxy_corr = correlation(close_1d, dxy_close) if not dxy_close.empty else float("nan")

    # 實質利率代理：TIP ETF（通膨連結債券，TIP↑=實質利率↓=黃金↑）
    tip_df = yf.Ticker("TIP").history(period="1y", interval="1d")
    tip_close = tip_df["Close"] if not tip_df.empty else pd.Series(dtype=float)
    tip = float(tip_close.iloc[-1]) if not tip_close.empty else float("nan")
    tip_rsi = calc_rsi(tip_close) if len(tip_close) >= 15 else float("nan")
    tip_1m = float(tip_close.iloc[-22]) if len(tip_close) >= 22 else tip
    tip_chg_1m = (tip - tip_1m) / tip_1m * 100 if tip_1m else float("nan")
    gld_tip_corr = correlation(close_1d, tip_close) if not tip_close.empty else float("nan")

    # GDX（黃金礦商 ETF）
    gdx_df = yf.Ticker("GDX").history(period="1y", interval="1d")
    gdx_close = gdx_df["Close"] if not gdx_df.empty else pd.Series(dtype=float)
    gdx = float(gdx_close.iloc[-1]) if not gdx_close.empty else float("nan")
    gdx_rsi = calc_rsi(gdx_close) if len(gdx_close) >= 15 else float("nan")
    gdx_gld_ratio = gdx / price if price > 0 else float("nan")
    # 歷史 GDX/GLD 比值（越低代表礦商相對黃金更被低估）
    gdx_gld_hist = gdx_close / close_1d.reindex(gdx_close.index, method="ffill")
    gdx_gld_ratio_pct = float((gdx_gld_ratio - gdx_gld_hist.min()) / (gdx_gld_hist.max() - gdx_gld_hist.min() + 1e-9) * 100) if not gdx_gld_hist.empty else float("nan")

    # SLV（白銀 ETF）→ 金銀比
    slv_df = yf.Ticker("SLV").history(period="1y", interval="1d")
    slv_close = slv_df["Close"] if not slv_df.empty else pd.Series(dtype=float)
    slv = float(slv_close.iloc[-1]) if not slv_close.empty else float("nan")
    gold_silver_ratio = price / slv if slv and slv > 0 else float("nan")
    # 近1年金銀比分位
    gsr_hist = close_1d / slv_close.reindex(close_1d.index, method="ffill")
    gsr_pct = float((gold_silver_ratio - gsr_hist.min()) / (gsr_hist.max() - gsr_hist.min() + 1e-9) * 100) if not gsr_hist.empty else float("nan")

    # 10Y 殖利率
    tnx_df = yf.Ticker("^TNX").history(period="1y", interval="1d")
    tnx_close = tnx_df["Close"] if not tnx_df.empty else pd.Series(dtype=float)
    yield_10y = float(tnx_close.iloc[-1]) if not tnx_close.empty else float("nan")
    yield_1m  = float(tnx_close.iloc[-22]) if len(tnx_close) >= 22 else yield_10y
    yield_chg_1m = yield_10y - yield_1m

    # 5Y 盈虧平衡通膨率（RINF ETF 作為代理）
    rinf_df = yf.Ticker("RINF").history(period="6m", interval="1d")
    rinf = float(rinf_df["Close"].iloc[-1]) if not rinf_df.empty else float("nan")

    print(f"  DXY：{dxy:.2f}  RSI={dxy_rsi:.1f}  1M變動：{dxy_chg_1m:+.1f}%")
    print(f"  TIP(通脹債)：${tip:.2f}  RSI={tip_rsi:.1f}  1M變動：{tip_chg_1m:+.1f}%")
    print(f"  10Y 殖利率：{yield_10y:.2f}%  1M變動：{yield_chg_1m:+.2f}%")
    print(f"  GDX 礦商：${gdx:.2f}  RSI={gdx_rsi:.1f}  GDX/GLD比={gdx_gld_ratio:.3f}")
    print(f"  金銀比(GLD/SLV)：{gold_silver_ratio:.1f}  (近1年{gsr_pct:.0f}%分位)")
    print(f"  GLD vs DXY 60日相關：{gld_dxy_corr:.2f}")
    print(f"  GLD vs TIP 60日相關：{gld_tip_corr:.2f}")

    # ── 3. 支撐壓力位 ─────────────────────────────────────────────
    print("\n[3/5] 計算支撐壓力位...")

    # 近 6 個月局部高低點（Swing levels）
    window = 5
    local_highs = []
    local_lows  = []
    c = close_1d.tail(130)
    for i in range(window, len(c) - window):
        v = c.iloc[i]
        if v == c.iloc[i-window:i+window+1].max():
            local_highs.append(v)
        if v == c.iloc[i-window:i+window+1].min():
            local_lows.append(v)

    # 取最近5個關鍵位
    key_resistances = sorted(set([round(x, 1) for x in local_highs if x > price]), reverse=False)[:3]
    key_supports    = sorted(set([round(x, 1) for x in local_lows  if x < price]), reverse=True)[:3]

    # 加入 SMA 作為動態支撐壓力
    support_levels    = sorted(set(key_supports + [round(sma20, 1), round(sma50, 1), round(sma200, 1)]), reverse=True)
    resistance_levels = sorted(set(key_resistances + []), reverse=False)

    print(f"  當前價格：${price:.2f}")
    print(f"  關鍵壓力：{resistance_levels}")
    print(f"  關鍵支撐：{support_levels}")
    print(f"  SMA20={sma20:.2f}  SMA50={sma50:.2f}  SMA200={sma200:.2f}")
    print(f"  BB 下軌={bb_lower:.2f}  BB 中軌={bb_mid:.2f}  BB 上軌={bb_upper:.2f}")

    # ── 4. 歷史超賣回溯 ───────────────────────────────────────────
    print("\n[4/5] 分析歷史超賣回報...")

    rsi_series = close_1d.copy()
    delta = rsi_series.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    rsi_full = 100 - 100 / (1 + rs)

    oversold_events = []
    in_oversold = False
    entry_price = None
    entry_date = None

    for i in range(len(rsi_full)):
        r = rsi_full.iloc[i]
        p = close_1d.iloc[i]
        d = close_1d.index[i]
        if r < 30 and not in_oversold:
            in_oversold = True
            entry_price = p
            entry_date = d
        elif r > 50 and in_oversold:
            in_oversold = False
            ret = (p - entry_price) / entry_price * 100
            oversold_events.append({
                "entry_date": entry_date,
                "entry_price": entry_price,
                "exit_date": d,
                "exit_price": p,
                "return_pct": ret,
                "entry_rsi": float(rsi_full.iloc[max(0, i - int((d - entry_date).days))])
            })

    if oversold_events:
        returns = [e["return_pct"] for e in oversold_events]
        print(f"  近2年 RSI<30 超賣事件：{len(oversold_events)} 次")
        print(f"  平均回報：+{np.mean(returns):.1f}%  |  最大：+{max(returns):.1f}%  |  最小：{min(returns):.1f}%")
        print(f"  勝率（正報酬）：{sum(1 for r in returns if r > 0)}/{len(returns)}")
        last_3 = oversold_events[-3:]
        for e in last_3:
            print(f"    {e['entry_date'].strftime('%Y-%m-%d')} 入場 ${e['entry_price']:.2f} → "
                  f"{e['exit_date'].strftime('%Y-%m-%d')} 出場 ${e['exit_price']:.2f}  "
                  f"報酬：{e['return_pct']:+.1f}%")
    else:
        print("  (近2年無完整 RSI<30 超賣回彈事件記錄)")

    # ── 5. 彙整報告 ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("GLD 深度分析摘要")
    print("=" * 60)

    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    summary_lines = [
        f"## GLD 黃金 ETF 深度分析 — {now_str}",
        "",
        "### 技術面指標",
        f"| 指標 | 數值 | 解讀 |",
        f"|------|------|------|",
        f"| 現價 | ${price:.2f} | 1W前 ${price_1w_ago:.2f} ({pct_from(price_1w_ago, price)}) · 1M前 ${price_1m_ago:.2f} ({pct_from(price_1m_ago, price)}) |",
        f"| 52週高低 | ${high_52w:.2f} / ${low_52w:.2f} | 距高 {pct_from_high:.1f}% · 距低 +{pct_from_low:.1f}% · 位置 {position_pct:.0f}% 分位 |",
        f"| RSI (日線) | {rsi_1d:.1f} | {'🔴 超賣區' if rsi_1d < 30 else '🟡 中性' if rsi_1d < 70 else '🔴 超買區'} |",
        f"| RSI (週線) | {rsi_1wk:.1f} | {'🔴 超賣區' if rsi_1wk < 30 else '🟡 中性' if rsi_1wk < 70 else '🔴 超買區'} |",
        f"| RSI (小時) | {rsi_1h:.1f} | {'🔴 超賣' if rsi_1h < 30 else '🟡 中性' if rsi_1h < 70 else '🔴 超買'} |",
        f"| MACD 直方 | {macd_hist:.3f} | {macd_cross} — {'黃金看漲' if macd_hist > 0 else '黃金看跌，待翻正'} |",
        f"| Bollinger %B | {pct_b:.2f} | {'下軌附近（超賣）' if pct_b < 0.2 else '上軌附近（超買）' if pct_b > 0.8 else '中性區間'} |",
        f"| BB 下/中/上軌 | {bb_lower:.2f} / {bb_mid:.2f} / {bb_upper:.2f} | 現價{'低於下軌 — 極度超賣' if price < bb_lower else '在下軌上方'} |",
        f"| SMA 20/50/200 | {sma20:.2f} / {sma50:.2f} / {sma200:.2f} | 現價{'在SMA200下方' if price < sma200 else '在SMA200上方'} |",
        f"| 成交量比 | {vol_ratio:.2f}x | {'放量（確認信號）' if vol_ratio > 1.3 else '縮量（信心不足）' if vol_ratio < 0.7 else '正常量能'} |",
        "",
        "### 黃金驅動因子",
        f"| 因子 | 數值 | 方向 | 對黃金影響 |",
        f"|------|------|------|-----------|",
        f"| DXY 美元指數 | {dxy:.2f} (RSI {dxy_rsi:.1f}) | 1M {dxy_chg_1m:+.1f}% | 與GLD 60日相關 {gld_dxy_corr:.2f}（負相關有利黃金） |",
        f"| 10Y 殖利率 | {yield_10y:.2f}% | 1M {yield_chg_1m:+.2f}% | 殖利率{'上升' if yield_chg_1m > 0 else '下降'}{'不利' if yield_chg_1m > 0.1 else '有利' if yield_chg_1m < -0.1 else '中性'}黃金 |",
        f"| TIP 通脹連結債 | ${tip:.2f} (RSI {tip_rsi:.1f}) | 1M {tip_chg_1m:+.1f}% | 與GLD 60日相關 {gld_tip_corr:.2f}（正相關有利黃金） |",
        f"| GDX 礦商ETF | ${gdx:.2f} (RSI {gdx_rsi:.1f}) | GDX/GLD={gdx_gld_ratio:.3f} | 礦商近1年比值{gdx_gld_ratio_pct:.0f}%分位（越低礦商越相對被低估） |",
        f"| 金銀比 | {gold_silver_ratio:.1f} | 近1年{gsr_pct:.0f}%分位 | {'金銀比偏高，白銀相對更有吸引力' if gsr_pct > 70 else '金銀比偏低，黃金相對有優勢' if gsr_pct < 30 else '金銀比中性'} |",
        "",
        "### 關鍵價位",
        f"| 類型 | 價位 | 距現價 |",
        f"|------|------|--------|",
    ]

    for r in resistance_levels[:3]:
        summary_lines.append(f"| 壓力 | ${r:.1f} | {pct_from(price, r)} |")
    for s in support_levels[:4]:
        if s < price:
            summary_lines.append(f"| 支撐 | ${s:.1f} | {pct_from(price, s)} |")

    if oversold_events:
        avg_ret = np.mean([e["return_pct"] for e in oversold_events])
        win_rate = sum(1 for e in oversold_events if e["return_pct"] > 0) / len(oversold_events) * 100
        summary_lines += [
            "",
            "### 歷史超賣回報統計（RSI<30 入場 → RSI>50 出場）",
            f"- 事件次數：{len(oversold_events)} 次（近2年）",
            f"- 平均報酬：+{avg_ret:.1f}%",
            f"- 勝率：{win_rate:.0f}%",
            f"- 最大報酬：+{max(e['return_pct'] for e in oversold_events):.1f}%",
            f"- 最小報酬：{min(e['return_pct'] for e in oversold_events):.1f}%",
        ]

    print("\n".join(summary_lines))

    # 儲存分析結果到 data/gld_analysis.md
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "gld_analysis.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))
    print(f"\n✅ 已儲存至 {os.path.abspath(out_path)}")


if __name__ == "__main__":
    run()
