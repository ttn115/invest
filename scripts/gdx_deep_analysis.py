"""
GDX 黃金礦商 ETF 深度分析腳本
分析維度：
  - 技術面：多時框 RSI / MACD / Bollinger Bands / SMA
  - 礦商特有：GDX/GLD 槓桿倍率 / 歷史 beta / 成分股狀態
  - 宏觀驅動：金價 / DXY / 實質利率 / 礦商利潤空間估算
  - 歷史超賣回報統計
用法：python scripts/gdx_deep_analysis.py
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

# 確保可 import 同目錄的 quote_utils
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from quote_utils import get_fresh_quote, freshness_banner


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


def pct_from(current: float, target: float) -> str:
    pct = (target - current) / current * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def rolling_beta(ret_gdx: pd.Series, ret_gld: pd.Series, window: int = 60) -> float:
    df = pd.concat([ret_gdx, ret_gld], axis=1).dropna()
    df.columns = ["gdx", "gld"]
    if len(df) < window:
        return float("nan")
    tail = df.tail(window)
    cov = tail.cov().iloc[0, 1]
    var = tail["gld"].var()
    return cov / var if var > 1e-9 else float("nan")


# ── 主分析 ────────────────────────────────────────────────────────

def run():
    print("=" * 62)
    print("GDX 黃金礦商 ETF 深度分析")
    print(f"執行時間：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)

    # ── 1. GDX 多時框技術指標 ─────────────────────────────────────
    print("\n[1/5] 抓取 GDX 多時框數據...")
    gdx_1d  = yf.Ticker("GDX").history(period="2y",  interval="1d")
    gdx_1wk = yf.Ticker("GDX").history(period="5y",  interval="1wk")
    gdx_1h  = yf.Ticker("GDX").history(period="30d", interval="1h")

    if gdx_1d.empty:
        print("❌ 無法取得 GDX 數據")
        return

    # yfinance 最新一根日線偶爾為 NaN（尚未定版），先濾除避免污染指標
    gdx_1d = gdx_1d.dropna(subset=["Close"])
    close_1d  = gdx_1d["Close"]
    vol_1d    = gdx_1d["Volume"]
    close_1wk = gdx_1wk["Close"].dropna()
    close_1h  = gdx_1h["Close"].dropna() if not gdx_1h.empty else pd.Series(dtype=float)

    # 指標基準＝最後一根已完成日線；現價＝最新成交價（含盤前/盤後）
    daily_px   = float(close_1d.iloc[-1])
    daily_date = close_1d.index[-1].strftime("%Y-%m-%d")
    q = get_fresh_quote("GDX", daily_close=daily_px, daily_date=daily_date)
    price = q["live_price"] if not pd.isna(q["live_price"]) else daily_px

    price_1w_ago = float(close_1d.iloc[-6])  if len(close_1d) >= 6  else daily_px
    price_1m_ago = float(close_1d.iloc[-22]) if len(close_1d) >= 22 else daily_px
    price_3m_ago = float(close_1d.iloc[-66]) if len(close_1d) >= 66 else daily_px

    high_52w     = float(close_1d.tail(252).max())
    low_52w      = float(close_1d.tail(252).min())
    pct_from_high = (price - high_52w) / high_52w * 100
    pct_from_low  = (price - low_52w)  / low_52w  * 100
    position_pct  = (price - low_52w)  / (high_52w - low_52w + 1e-9) * 100

    sma20  = float(close_1d.rolling(20).mean().iloc[-1])
    sma50  = float(close_1d.rolling(50).mean().iloc[-1])
    sma200 = float(close_1d.rolling(200).mean().iloc[-1])

    rsi_1d  = calc_rsi(close_1d, 14)
    rsi_1wk = calc_rsi(close_1wk, 14)
    rsi_1h  = calc_rsi(close_1h, 14) if len(close_1h) >= 15 else float("nan")

    macd_val, macd_sig, macd_hist = calc_macd(close_1d)
    macd_cross = "BULLISH" if macd_hist > 0 else "BEARISH"

    bb_upper, bb_mid, bb_lower, pct_b = calc_bollinger(close_1d)

    avg_vol_20 = float(vol_1d.tail(20).mean())
    today_vol  = float(vol_1d.iloc[-1])
    vol_ratio  = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

    for ln in freshness_banner("GDX", q):
        print(f"  {ln}")
    print(f"  GDX 現價：${price:.2f}（最新成交）  |  日線收盤 ${daily_px:.2f}（{daily_date}）")
    print(f"  ※ 以下指標係以 {daily_date} 收盤計算")
    print(f"  52週 高/低：${high_52w:.2f} / ${low_52w:.2f}  距高：{pct_from_high:.1f}%  距低：+{pct_from_low:.1f}%  位置：{position_pct:.0f}%分位")
    print(f"  RSI 日/週/時：{rsi_1d:.1f} / {rsi_1wk:.1f} / {rsi_1h:.1f}")
    print(f"  MACD 直方：{macd_hist:.3f} ({macd_cross})")
    print(f"  BB %B：{pct_b:.2f}  下軌={bb_lower:.2f}  中軌={bb_mid:.2f}  上軌={bb_upper:.2f}")
    print(f"  SMA20/50/200：{sma20:.2f} / {sma50:.2f} / {sma200:.2f}")
    print(f"  成交量比：{vol_ratio:.2f}x")
    _broke = lambda lv: "✅已跌破" if price < lv else "未跌破"
    print(f"  以最新價 ${price:.2f} 檢視：BB下軌 ${bb_lower:.2f}（{_broke(bb_lower)}）；"
          f"52週低 ${low_52w:.2f}（{_broke(low_52w)}）；SMA200 ${sma200:.2f}（{_broke(sma200)}）")

    # ── 2. GDX vs GLD 礦商槓桿分析 ───────────────────────────────
    print("\n[2/5] 分析 GDX/GLD 槓桿關係...")
    gld_1d  = yf.Ticker("GLD").history(period="2y", interval="1d")
    gld_close = gld_1d["Close"].dropna() if not gld_1d.empty else pd.Series(dtype=float)

    gld_price = float(gld_close.iloc[-1]) if not gld_close.empty else float("nan")
    # 比值用日線收盤對日線收盤，與下方歷史分位 (daily/daily) 基準一致
    gdx_gld_ratio     = daily_px / gld_price if gld_price > 0 else float("nan")

    # 歷史比值分位
    ratio_hist = close_1d / gld_close.reindex(close_1d.index, method="ffill")
    ratio_now  = gdx_gld_ratio
    ratio_min  = float(ratio_hist.min())
    ratio_max  = float(ratio_hist.max())
    ratio_pct  = (ratio_now - ratio_min) / (ratio_max - ratio_min + 1e-9) * 100

    # 滾動 beta（GDX 相對 GLD 的槓桿倍率）
    gdx_ret = close_1d.pct_change()
    gld_ret = gld_close.reindex(close_1d.index, method="ffill").pct_change()
    beta_60  = rolling_beta(gdx_ret, gld_ret, 60)
    beta_20  = rolling_beta(gdx_ret, gld_ret, 20)
    beta_252 = rolling_beta(gdx_ret, gld_ret, 252)

    # GLD 1M/1W 表現 vs GDX
    gld_1w = float(gld_close.iloc[-6])  if len(gld_close) >= 6  else gld_price
    gld_1m = float(gld_close.iloc[-22]) if len(gld_close) >= 22 else gld_price
    gld_chg_1w = (gld_price - gld_1w) / gld_1w * 100
    gld_chg_1m = (gld_price - gld_1m) / gld_1m * 100
    gdx_chg_1w = (price - price_1w_ago) / price_1w_ago * 100
    gdx_chg_1m = (price - price_1m_ago) / price_1m_ago * 100
    actual_lev_1w = gdx_chg_1w / gld_chg_1w if abs(gld_chg_1w) > 0.1 else float("nan")
    actual_lev_1m = gdx_chg_1m / gld_chg_1m if abs(gld_chg_1m) > 0.1 else float("nan")

    print(f"  GLD 現價：${gld_price:.2f}  GDX/GLD 比值：{gdx_gld_ratio:.3f}")
    print(f"  GDX/GLD 比值歷史分位：{ratio_pct:.0f}%（0=礦商最便宜，100=礦商最貴）")
    print(f"  GDX beta vs GLD：20日={beta_20:.2f}  60日={beta_60:.2f}  252日={beta_252:.2f}")
    print(f"  1週表現：GLD {gld_chg_1w:+.1f}%  vs  GDX {gdx_chg_1w:+.1f}%  實際槓桿={actual_lev_1w:.2f}x")
    print(f"  1月表現：GLD {gld_chg_1m:+.1f}%  vs  GDX {gdx_chg_1m:+.1f}%  實際槓桿={actual_lev_1m:.2f}x")

    # ── 3. 主要成分股狀態 ─────────────────────────────────────────
    print("\n[3/5] 抓取主要成分股...")
    # GDX 前10大成分股（含大約權重）
    components = [
        ("NEM",  "Newmont",          14.0),
        ("GOLD", "Barrick Gold",     11.0),
        ("AEM",  "Agnico Eagle",      9.5),
        ("WPM",  "Wheaton Precious",  7.5),
        ("GFI",  "Gold Fields",       5.5),
        ("AU",   "AngloGold",         5.0),
        ("KGC",  "Kinross Gold",      4.5),
        ("AGI",  "Alamos Gold",       3.5),
        ("EQX",  "Equinox Gold",      3.0),
        ("OR",   "Osisko Gold",       2.5),
    ]

    comp_data = []
    for ticker, name, weight in components:
        try:
            df = yf.Ticker(ticker).history(period="6mo", interval="1d")
            if df.empty:
                continue
            c = df["Close"]
            p = float(c.iloc[-1])
            r = calc_rsi(c)
            p1m = float(c.iloc[-22]) if len(c) >= 22 else p
            chg1m = (p - p1m) / p1m * 100
            sma50_v = float(c.rolling(50).mean().iloc[-1])
            above_sma50 = p > sma50_v
            bb_u, bb_m, bb_l, pctb = calc_bollinger(c)
            comp_data.append({
                "ticker": ticker, "name": name, "weight": weight,
                "price": p, "rsi": r, "chg1m": chg1m,
                "above_sma50": above_sma50, "pct_b": pctb,
            })
            status = "🔴超賣" if r < 30 else ("🟢超買" if r > 70 else "⚪中性")
            sma_flag = "✅SMA50上" if above_sma50 else "❌SMA50下"
            print(f"  {ticker:<6} {name:<20} 權重{weight:4.1f}%  ${p:7.2f}  RSI={r:5.1f} {status}  1M={chg1m:+5.1f}%  {sma_flag}")
        except Exception as e:
            print(f"  {ticker} 抓取失敗：{e}")

    # 計算加權平均 RSI
    total_w = sum(d["weight"] for d in comp_data)
    avg_rsi_weighted = sum(d["rsi"] * d["weight"] for d in comp_data) / total_w if total_w > 0 else float("nan")
    oversold_count = sum(1 for d in comp_data if d["rsi"] < 35)
    print(f"\n  加權平均 RSI：{avg_rsi_weighted:.1f}  |  RSI<35 成分股：{oversold_count}/{len(comp_data)}")

    # ── 4. 礦商利潤空間估算 ───────────────────────────────────────
    print("\n[4/5] 估算礦商利潤空間...")
    # 黃金現貨價格（用 GC=F 或從 GLD 換算）
    gc_df = yf.Ticker("GC=F").history(period="5d", interval="1d")
    if not gc_df.empty:
        gold_spot = float(gc_df["Close"].iloc[-1])
    else:
        gold_spot = gld_price / 0.0926  # GLD 換算

    # 礦商平均全維持成本（AISC）參考區間
    aisc_low   = 1200.0   # 低成本礦商（如 Agnico, Barrick 優質礦）
    aisc_mid   = 1400.0   # 產業平均
    aisc_high  = 1800.0   # 高成本礦商
    margin_low  = (gold_spot - aisc_low)  / gold_spot * 100
    margin_mid  = (gold_spot - aisc_mid)  / gold_spot * 100
    margin_high = (gold_spot - aisc_high) / gold_spot * 100

    # 盈虧平衡金價
    breakeven_low  = aisc_low  * 1.10   # 含 10% 資本回報
    breakeven_mid  = aisc_mid  * 1.10
    breakeven_high = aisc_high * 1.10

    print(f"  黃金現貨：${gold_spot:.0f}/盎司")
    print(f"  低成本礦商 AISC ${aisc_low:.0f}  利潤率 {margin_low:.0f}%  盈虧平衡 ${breakeven_low:.0f}")
    print(f"  平均礦商  AISC ${aisc_mid:.0f}  利潤率 {margin_mid:.0f}%  盈虧平衡 ${breakeven_mid:.0f}")
    print(f"  高成本礦商 AISC ${aisc_high:.0f}  利潤率 {margin_high:.0f}%  盈虧平衡 ${breakeven_high:.0f}")
    print(f"  → 當前金價下，{'大多數礦商有正利潤' if gold_spot > aisc_high else '高成本礦商虧損'}")

    # ── 5. 歷史超賣回報統計 ───────────────────────────────────────
    print("\n[5/5] 分析歷史超賣回報...")
    delta = close_1d.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / (loss + 1e-9)
    rsi_full = 100 - 100 / (1 + rs)

    oversold_events = []
    in_oversold = False
    entry_price = entry_date = None

    for i in range(len(rsi_full)):
        r = rsi_full.iloc[i]
        p = close_1d.iloc[i]
        d = close_1d.index[i]
        if r < 30 and not in_oversold:
            in_oversold   = True
            entry_price   = p
            entry_date    = d
        elif r > 50 and in_oversold:
            in_oversold   = False
            ret = (p - entry_price) / entry_price * 100
            oversold_events.append({
                "entry_date": entry_date, "entry_price": entry_price,
                "exit_date": d, "exit_price": p, "return_pct": ret,
            })

    if oversold_events:
        returns  = [e["return_pct"] for e in oversold_events]
        win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
        print(f"  近2年 RSI<30 超賣事件：{len(oversold_events)} 次")
        print(f"  平均回報：+{np.mean(returns):.1f}%  最大：+{max(returns):.1f}%  最小：{min(returns):.1f}%  勝率：{win_rate:.0f}%")
        for e in oversold_events[-4:]:
            print(f"    {e['entry_date'].strftime('%Y-%m-%d')} ${e['entry_price']:.2f} → "
                  f"{e['exit_date'].strftime('%Y-%m-%d')} ${e['exit_price']:.2f}  {e['return_pct']:+.1f}%")
    else:
        print("  近2年無完整超賣回彈事件")

    # ── 輸出彙整摘要 ─────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("GDX 深度分析摘要")
    print("=" * 62)

    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"## GDX 黃金礦商 ETF 深度分析 — {now_str}",
        "",
    ]
    for ln in freshness_banner("GDX", q):
        lines.append(f"> {ln}")
    lines += [
        "",
        f"### 技術面指標（指標截至 {daily_date} 收盤）",
        "| 指標 | 數值 | 解讀 |",
        "|------|------|------|",
        f"| 現價(最新) | ${price:.2f} | 日線收盤 ${daily_px:.2f}（{daily_date}）· 1W前 ${price_1w_ago:.2f} ({pct_from(price_1w_ago, price)}) · 1M前 ${price_1m_ago:.2f} ({pct_from(price_1m_ago, price)}) |",
        f"| 52週高低 | ${high_52w:.2f} / ${low_52w:.2f} | 距高 {pct_from_high:.1f}% · 距低 +{pct_from_low:.1f}% · 位置 {position_pct:.0f}%分位 |",
        f"| RSI 日線 | {rsi_1d:.1f} | {'🔴 超賣' if rsi_1d < 30 else '🟡 中性' if rsi_1d < 70 else '🔴 超買'} |",
        f"| RSI 週線 | {rsi_1wk:.1f} | {'🔴 超賣' if rsi_1wk < 30 else '🟡 中性' if rsi_1wk < 70 else '🔴 超買'} |",
        f"| RSI 小時 | {rsi_1h:.1f} | {'🔴 超賣' if rsi_1h < 30 else '🟡 中性' if rsi_1h < 70 else '🔴 超買'} |",
        f"| MACD 直方 | {macd_hist:.3f} | {macd_cross} |",
        f"| Bollinger %B | {pct_b:.2f} | {'跌破下軌（極度超賣）' if pct_b < 0 else '下軌附近' if pct_b < 0.2 else '中性'} |",
        f"| BB 下/中/上軌 | {bb_lower:.2f}/{bb_mid:.2f}/{bb_upper:.2f} | 現價{'低於下軌' if price < bb_lower else '在下軌上方'} |",
        f"| SMA20/50/200 | {sma20:.2f}/{sma50:.2f}/{sma200:.2f} | {'全在現價上方（空頭排列）' if price < min(sma20,sma50,sma200) else '混合排列'} |",
        f"| 成交量比 | {vol_ratio:.2f}x | {'放量拋售' if vol_ratio > 1.3 else '縮量' if vol_ratio < 0.7 else '正常'} |",
        "",
        "### GDX/GLD 礦商槓桿關係",
        "| 指標 | 數值 | 解讀 |",
        "|------|------|------|",
        f"| GDX/GLD 比值 | {gdx_gld_ratio:.3f} | 近2年 {ratio_pct:.0f}%分位（越低礦商越相對便宜） |",
        f"| 20日 Beta | {beta_20:.2f} | 金價每漲1%，GDX 預期漲 {beta_20:.2f}% |",
        f"| 60日 Beta | {beta_60:.2f} | 金價每漲1%，GDX 預期漲 {beta_60:.2f}% |",
        f"| 1W 實際槓桿 | {actual_lev_1w:.2f}x | GLD {gld_chg_1w:+.1f}% vs GDX {gdx_chg_1w:+.1f}% |",
        f"| 1M 實際槓桿 | {actual_lev_1m:.2f}x | GLD {gld_chg_1m:+.1f}% vs GDX {gdx_chg_1m:+.1f}% |",
        "",
        "### 主要成分股 RSI 狀態",
        "| 代號 | 公司 | 權重 | RSI | 1M漲跌 | SMA50 |",
        "|------|------|------|-----|--------|-------|",
    ]
    for d in comp_data:
        sma_flag = "✅上方" if d["above_sma50"] else "❌下方"
        status = "🔴超賣" if d["rsi"] < 30 else ("🟡中性" if d["rsi"] < 70 else "🟢超買")
        lines.append(f"| {d['ticker']} | {d['name']} | {d['weight']:.1f}% | {d['rsi']:.1f} {status} | {d['chg1m']:+.1f}% | {sma_flag} |")
    lines.append(f"| **加權平均** | — | 100% | **{avg_rsi_weighted:.1f}** | — | — |")

    lines += [
        "",
        "### 礦商利潤空間（黃金現貨 ${:.0f}/盎司）".format(gold_spot),
        "| 礦商類型 | AISC | 利潤率 | 盈虧平衡金價 |",
        "|---------|------|--------|------------|",
        f"| 低成本礦商 | ${aisc_low:.0f} | {margin_low:.0f}% | ${breakeven_low:.0f} |",
        f"| 產業平均 | ${aisc_mid:.0f} | {margin_mid:.0f}% | ${breakeven_mid:.0f} |",
        f"| 高成本礦商 | ${aisc_high:.0f} | {margin_high:.0f}% | ${breakeven_high:.0f} |",
    ]

    if oversold_events:
        avg_ret  = np.mean([e["return_pct"] for e in oversold_events])
        win_rate = sum(1 for e in oversold_events if e["return_pct"] > 0) / len(oversold_events) * 100
        lines += [
            "",
            "### 歷史超賣回報（RSI<30 入場 → RSI>50 出場）",
            f"- 事件次數：{len(oversold_events)} 次（近2年）",
            f"- 平均報酬：+{avg_ret:.1f}%",
            f"- 勝率：{win_rate:.0f}%",
            f"- 最大報酬：+{max(e['return_pct'] for e in oversold_events):.1f}%",
            f"- 最小報酬：{min(e['return_pct'] for e in oversold_events):.1f}%",
        ]
        for e in oversold_events[-4:]:
            lines.append(f"  - {e['entry_date'].strftime('%Y-%m-%d')} ${e['entry_price']:.2f} → {e['exit_date'].strftime('%Y-%m-%d')} ${e['exit_price']:.2f}  {e['return_pct']:+.1f}%")

    print("\n".join(lines))

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "gdx_analysis.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n✅ 已儲存至 {os.path.abspath(out_path)}")


if __name__ == "__main__":
    run()
