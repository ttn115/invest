"""
Newmont (NEM) vs Barrick Gold (GOLD) 深度比較研究腳本
分析維度：
  - 技術面：多時框 RSI / MACD / Bollinger Bands / SMA / 成交量
  - 基本面：PE / EPS / 營收 / 股息 / 殖利率 / Beta / 市值
  - 礦商專項：AISC / 生產量 / 儲量年限
  - 相對強弱：兩者 vs GDX / GLD 的 alpha
  - 歷史超賣回報
用法：python scripts/miner_compare.py
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
    return float((100 - 100 / (1 + rs)).iloc[-1])


def calc_macd(series: pd.Series):
    ema12 = series.ewm(span=12).mean()
    ema26 = series.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9).mean()
    hist = macd_line - signal
    return float(macd_line.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])


def calc_bollinger(series: pd.Series, period: int = 20):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    p = float(series.iloc[-1])
    pct_b = float((p - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1] + 1e-9))
    return float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1]), pct_b


def oversold_stats(close: pd.Series, rsi_entry=30, rsi_exit=50):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi_full = 100 - 100 / (loss / (gain + 1e-9) + 1)
    # fix: standard formula
    rs = gain / (loss + 1e-9)
    rsi_full = 100 - 100 / (1 + rs)

    events = []
    in_os = False
    ep = ed = None
    for i in range(len(rsi_full)):
        r, p, d = rsi_full.iloc[i], close.iloc[i], close.index[i]
        if r < rsi_entry and not in_os:
            in_os, ep, ed = True, p, d
        elif r > rsi_exit and in_os:
            in_os = False
            events.append({"entry_date": ed, "entry_price": ep,
                           "exit_date": d, "exit_price": p,
                           "return_pct": (p - ep) / ep * 100})
    return events


def alpha_vs(ret_stock: pd.Series, ret_bench: pd.Series, window=60) -> float:
    df = pd.concat([ret_stock, ret_bench], axis=1).dropna().tail(window)
    return float((df.iloc[:, 0] - df.iloc[:, 1]).mean() * 252)  # annualised


def fmt_pct(v): return f"{v:+.1f}%" if not np.isnan(v) else "N/A"
def fmt_f(v, d=2): return f"{v:.{d}f}" if not np.isnan(v) else "N/A"


# ── 單股分析 ─────────────────────────────────────────────────────

def analyse(ticker: str, name: str):
    print(f"\n{'='*62}")
    print(f"  {name} ({ticker})")
    print(f"{'='*62}")

    t = yf.Ticker(ticker)

    # ---- 價格歷史 ----
    d1  = t.history(period="2y",  interval="1d")
    dwk = t.history(period="5y",  interval="1wk")
    d1h = t.history(period="30d", interval="1h")

    if d1.empty:
        print(f"  ❌ 無法抓取 {ticker} 數據")
        return {}

    # yfinance 最新一根日線偶爾為 NaN（尚未定版），先濾除避免污染指標
    d1 = d1.dropna(subset=["Close"])
    c1  = d1["Close"];  v1 = d1["Volume"]
    cwk = dwk["Close"].dropna()
    c1h = d1h["Close"].dropna() if not d1h.empty else pd.Series(dtype=float)

    # 指標基準＝最後一根已完成日線；現價＝最新成交價（含盤前/盤後）
    daily_px   = float(c1.iloc[-1])
    daily_date = c1.index[-1].strftime("%Y-%m-%d")
    q = get_fresh_quote(t, daily_close=daily_px, daily_date=daily_date)
    price = q["live_price"] if not pd.isna(q["live_price"]) else daily_px

    p1w     = float(c1.iloc[-6])  if len(c1) >= 6  else daily_px
    p1m     = float(c1.iloc[-22]) if len(c1) >= 22 else daily_px
    p3m     = float(c1.iloc[-66]) if len(c1) >= 66 else daily_px
    p6m     = float(c1.iloc[-130])if len(c1) >= 130 else daily_px

    h52 = float(c1.tail(252).max())
    l52 = float(c1.tail(252).min())
    pos_pct = (price - l52) / (h52 - l52 + 1e-9) * 100

    sma20  = float(c1.rolling(20).mean().iloc[-1])
    sma50  = float(c1.rolling(50).mean().iloc[-1])
    sma200 = float(c1.rolling(200).mean().iloc[-1])

    rsi1d  = calc_rsi(c1)
    rsi1wk = calc_rsi(cwk)
    rsi1h  = calc_rsi(c1h) if len(c1h) >= 15 else float("nan")

    mv, ms, mh = calc_macd(c1)
    bbu, bbm, bbl, pctb = calc_bollinger(c1)

    avg_vol = float(v1.tail(20).mean())
    vol_r   = float(v1.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0

    # 漲跌以最新成交價對 N 日前日線收盤計算
    chg1w = (price - p1w) / p1w * 100
    chg1m = (price - p1m) / p1m * 100
    chg3m = (price - p3m) / p3m * 100
    chg6m = (price - p6m) / p6m * 100

    print()
    for ln in freshness_banner(ticker, q):
        print(f"  {ln}")

    print(f"\n  【技術面】（指標截至 {daily_date} 收盤）")
    print(f"  現價 ${price:.2f}（最新成交）  |  日線收盤 ${daily_px:.2f}（{daily_date}）")
    print(f"  52W: ${h52:.2f} / ${l52:.2f}  |  位置: {pos_pct:.0f}%分位")
    print(f"  漲跌（最新價 vs 前期日線）：1W {fmt_pct(chg1w)}  1M {fmt_pct(chg1m)}  3M {fmt_pct(chg3m)}  6M {fmt_pct(chg6m)}")
    print(f"  RSI  日={rsi1d:.1f}  週={rsi1wk:.1f}  時={rsi1h:.1f}")
    print(f"  MACD 直方={mh:.3f}  {'BULLISH 🟢' if mh > 0 else 'BEARISH 🔴'}")
    print(f"  BB   %B={pctb:.2f}  下軌={bbl:.2f}  中軌={bbm:.2f}  上軌={bbu:.2f}")
    print(f"  SMA  20={sma20:.2f}  50={sma50:.2f}  200={sma200:.2f}")
    print(f"  量比  {vol_r:.2f}x  ({'放量' if vol_r > 1.3 else '縮量' if vol_r < 0.7 else '正常'})")

    # 以最新成交價檢視是否跌破關鍵價位
    def _vs(level, label):
        diff = (price - level) / level * 100
        broke = "✅已跌破" if price < level else "尚未跌破"
        return f"{label} ${level:.2f}（{broke}，距 {diff:+.1f}%）"
    print(f"  以最新價 ${price:.2f} 檢視：{_vs(bbl, 'BB下軌')}；{_vs(l52, '52週低')}；{_vs(sma200, 'SMA200')}")

    # ---- 基本面 ----
    info = {}
    try:
        info = t.info
    except Exception:
        pass

    pe       = info.get("trailingPE",    float("nan"))
    fpe      = info.get("forwardPE",     float("nan"))
    eps      = info.get("trailingEps",   float("nan"))
    feps     = info.get("forwardEps",    float("nan"))
    pb       = info.get("priceToBook",   float("nan"))
    ps       = info.get("priceToSalesTrailing12Months", float("nan"))
    mktcap   = info.get("marketCap",     0)
    div_rate = info.get("dividendRate",  float("nan"))
    div_yld  = info.get("dividendYield", float("nan"))
    beta     = info.get("beta",          float("nan"))
    ev       = info.get("enterpriseValue",0)
    ebitda   = info.get("ebitda",        float("nan"))
    ev_ebitda= ev / ebitda if ebitda and ebitda > 0 else float("nan")
    rev      = info.get("totalRevenue",  float("nan"))
    rev_gr   = info.get("revenueGrowth", float("nan"))
    profit_m = info.get("profitMargins", float("nan"))
    roe      = info.get("returnOnEquity",float("nan"))
    debt_eq  = info.get("debtToEquity",  float("nan"))
    fcf      = info.get("freeCashflow",  float("nan"))
    shares   = info.get("sharesOutstanding", 0)

    mktcap_b = mktcap / 1e9 if mktcap else float("nan")
    rev_b    = rev    / 1e9 if rev and not np.isnan(rev) else float("nan")
    fcf_b    = fcf    / 1e9 if fcf and not np.isnan(fcf) else float("nan")

    print(f"\n  【基本面】")
    print(f"  市值 ${mktcap_b:.1f}B  |  EV/EBITDA={fmt_f(ev_ebitda)}")
    print(f"  PE(TTM)={fmt_f(pe)}  ForwardPE={fmt_f(fpe)}  PB={fmt_f(pb)}  PS={fmt_f(ps)}")
    print(f"  EPS(TTM)=${fmt_f(eps)}  ForwardEPS=${fmt_f(feps)}")
    print(f"  營收 ${fmt_f(rev_b)}B  營收成長={fmt_pct(rev_gr*100 if not np.isnan(rev_gr) else float('nan'))}")
    print(f"  淨利率={fmt_pct(profit_m*100 if not np.isnan(profit_m) else float('nan'))}  ROE={fmt_pct(roe*100 if not np.isnan(roe) else float('nan'))}")
    print(f"  自由現金流 ${fmt_f(fcf_b)}B  |  負債/權益={fmt_f(debt_eq)}")
    print(f"  股息/股 ${fmt_f(div_rate)}  殖利率={fmt_pct(div_yld*100 if div_yld and not np.isnan(div_yld) else float('nan'))}")
    print(f"  Beta={fmt_f(beta)}")

    # ---- 礦商行業數據（靜態參考值，來自最新年報公開資料）----
    MINER_REF = {
        "NEM": {
            "aisc_2024":   1463,   # $/oz  Newmont 2024年報
            "production":  6.8,    # Moz/year
            "reserves":    136.7,  # Moz 儲量
            "reserve_life": 20,    # 年
            "operations":  "全球 11 個國家，含 Cadia(澳洲)、Boddington(澳洲)、Peñasquito(墨西哥)、Cerro Negro(阿根廷)",
            "hedge":       "無黃金對沖（unhedged）",
        },
        "GOLD": {
            "aisc_2024":   1451,   # $/oz  Barrick 2024年報
            "production":  3.9,    # Moz/year（含銅換算）
            "reserves":    76.0,   # Moz
            "reserve_life": 19,    # 年
            "operations":  "全球 18 個礦場，含 Carlin(美國)、Cortez(美國)、Kibali(剛果)、Lumwana(尚比亞-銅礦)",
            "hedge":       "無黃金對沖，部分銅有對沖",
        },
    }
    ref = MINER_REF.get(ticker, {})
    if ref:
        gold_spot = 4112.0  # 最新金價（USD/oz）
        aisc      = ref["aisc_2024"]
        margin    = (gold_spot - aisc) / gold_spot * 100
        print(f"\n  【礦商行業數據（2024年報）】")
        print(f"  AISC: ${aisc}/oz  |  金價 $4,112  |  利潤率: {margin:.0f}%  |  利潤/oz: ${gold_spot-aisc:.0f}")
        print(f"  年產量: {ref['production']} Moz  |  儲量: {ref['reserves']} Moz  |  儲量年限: {ref['reserve_life']}年")
        print(f"  主要礦場: {ref['operations']}")
        print(f"  黃金對沖政策: {ref['hedge']}")

    # ---- 歷史超賣回報 ----
    events = oversold_stats(c1)
    print(f"\n  【歷史超賣回報（RSI<30→RSI>50）】")
    if events:
        rets = [e["return_pct"] for e in events]
        wr   = sum(1 for r in rets if r > 0) / len(rets) * 100
        print(f"  近2年事件：{len(events)}次  |  均報酬：+{np.mean(rets):.1f}%  |  最大：+{max(rets):.1f}%  |  勝率：{wr:.0f}%")
        for e in events[-4:]:
            print(f"    {e['entry_date'].strftime('%Y-%m-%d')} ${e['entry_price']:.2f}"
                  f"  →  {e['exit_date'].strftime('%Y-%m-%d')} ${e['exit_price']:.2f}"
                  f"  {e['return_pct']:+.1f}%")
    else:
        print("  近2年無完整超賣事件")

    return {
        "ticker": ticker, "name": name, "price": price,
        "daily_px": daily_px, "daily_date": daily_date, "quote": q,
        "rsi1d": rsi1d, "rsi1wk": rsi1wk, "macd_hist": mh,
        "pct_b": pctb, "bb_lower": bbl, "bb_mid": bbm,
        "sma20": sma20, "sma50": sma50, "sma200": sma200,
        "vol_ratio": vol_r,
        "chg1w": chg1w, "chg1m": chg1m, "chg3m": chg3m,
        "h52": h52, "l52": l52, "pos_pct": pos_pct,
        "pe": pe, "fpe": fpe, "pb": pb, "mktcap_b": mktcap_b,
        "div_yld": div_yld, "beta": beta, "fcf_b": fcf_b,
        "profit_m": profit_m, "debt_eq": debt_eq, "ev_ebitda": ev_ebitda,
        "aisc": ref.get("aisc_2024", float("nan")),
        "production": ref.get("production", float("nan")),
        "reserves": ref.get("reserves", float("nan")),
        "reserve_life": ref.get("reserve_life", float("nan")),
        "events": events,
        "close": c1,
    }


# ── 主程式 ────────────────────────────────────────────────────────

def run():
    print("=" * 62)
    print("Newmont (NEM) vs Barrick Gold (GOLD) 深度比較")
    print(f"執行時間：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)

    nem  = analyse("NEM",  "Newmont Corporation")
    gold = analyse("GOLD", "Barrick Gold")

    # ── 相對強弱：vs GDX & GLD ────────────────────────────────────
    print(f"\n{'='*62}")
    print("  相對強弱分析（Alpha vs GDX / GLD）")
    print(f"{'='*62}")

    gdx_df = yf.Ticker("GDX").history(period="2y", interval="1d")
    gld_df = yf.Ticker("GLD").history(period="2y", interval="1d")
    gdx_c  = gdx_df["Close"] if not gdx_df.empty else pd.Series(dtype=float)
    gld_c  = gld_df["Close"] if not gld_df.empty else pd.Series(dtype=float)
    gdx_r  = gdx_c.pct_change()
    gld_r  = gld_c.pct_change()

    for d in [nem, gold]:
        if not d:
            continue
        cr = d["close"].pct_change()
        a_gdx = alpha_vs(cr, gdx_r.reindex(cr.index, method="ffill"))
        a_gld = alpha_vs(cr, gld_r.reindex(cr.index, method="ffill"))

        # 60日相關
        df_gdx = pd.concat([cr, gdx_r.reindex(cr.index, method="ffill")], axis=1).dropna().tail(60)
        df_gld = pd.concat([cr, gld_r.reindex(cr.index, method="ffill")], axis=1).dropna().tail(60)
        corr_gdx = float(df_gdx.corr().iloc[0, 1]) if len(df_gdx) > 5 else float("nan")
        corr_gld = float(df_gld.corr().iloc[0, 1]) if len(df_gld) > 5 else float("nan")

        print(f"\n  {d['name']} ({d['ticker']})")
        print(f"    60日 Alpha vs GDX：{a_gdx:+.1f}%/年  |  相關係數：{corr_gdx:.2f}")
        print(f"    60日 Alpha vs GLD：{a_gld:+.1f}%/年  |  相關係數：{corr_gld:.2f}")

    # ── 並排比較表 ────────────────────────────────────────────────
    print(f"\n{'='*62}")
    print("  NEM vs GOLD 並排比較")
    print(f"{'='*62}")

    rows = [
        ("現價(最新)",  f"${nem['price']:.2f}",              f"${gold['price']:.2f}"),
        ("日線收盤",    f"${nem['daily_px']:.2f}({nem['daily_date'][5:]})",  f"${gold['daily_px']:.2f}({gold['daily_date'][5:]})"),
        ("市值",        f"${nem['mktcap_b']:.1f}B",          f"${gold['mktcap_b']:.1f}B"),
        ("1M漲跌",      fmt_pct(nem['chg1m']),               fmt_pct(gold['chg1m'])),
        ("3M漲跌",      fmt_pct(nem['chg3m']),               fmt_pct(gold['chg3m'])),
        ("52W位置",     f"{nem['pos_pct']:.0f}%分位",        f"{gold['pos_pct']:.0f}%分位"),
        ("RSI(日)",     f"{nem['rsi1d']:.1f}",               f"{gold['rsi1d']:.1f}"),
        ("RSI(週)",     f"{nem['rsi1wk']:.1f}",              f"{gold['rsi1wk']:.1f}"),
        ("MACD直方",    f"{nem['macd_hist']:.3f}",           f"{gold['macd_hist']:.3f}"),
        ("BB %B",       f"{nem['pct_b']:.2f}",               f"{gold['pct_b']:.2f}"),
        ("PE(TTM)",     fmt_f(nem['pe']),                    fmt_f(gold['pe'])),
        ("Forward PE",  fmt_f(nem['fpe']),                   fmt_f(gold['fpe'])),
        ("EV/EBITDA",   fmt_f(nem['ev_ebitda']),             fmt_f(gold['ev_ebitda'])),
        ("PB",          fmt_f(nem['pb']),                    fmt_f(gold['pb'])),
        ("股息殖利率",  fmt_pct(nem['div_yld']*100 if nem['div_yld'] and not np.isnan(nem['div_yld']) else float('nan')),
                        fmt_pct(gold['div_yld']*100 if gold['div_yld'] and not np.isnan(gold['div_yld']) else float('nan'))),
        ("Beta",        fmt_f(nem['beta']),                  fmt_f(gold['beta'])),
        ("FCF",         f"${nem['fcf_b']:.1f}B" if not np.isnan(nem['fcf_b']) else "N/A",
                        f"${gold['fcf_b']:.1f}B" if not np.isnan(gold['fcf_b']) else "N/A"),
        ("淨利率",      fmt_pct(nem['profit_m']*100 if nem['profit_m'] and not np.isnan(nem['profit_m']) else float('nan')),
                        fmt_pct(gold['profit_m']*100 if gold['profit_m'] and not np.isnan(gold['profit_m']) else float('nan'))),
        ("負債/權益",   fmt_f(nem['debt_eq']),               fmt_f(gold['debt_eq'])),
        ("AISC",        f"${nem['aisc']:.0f}/oz" if not np.isnan(nem['aisc']) else "N/A",
                        f"${gold['aisc']:.0f}/oz" if not np.isnan(gold['aisc']) else "N/A"),
        ("年產量",      f"{nem['production']} Moz",          f"{gold['production']} Moz"),
        ("儲量",        f"{nem['reserves']} Moz",            f"{gold['reserves']} Moz"),
        ("儲量年限",    f"{nem['reserve_life']}年",          f"{gold['reserve_life']}年"),
    ]

    print(f"  {'指標':<14} {'NEM':>16} {'GOLD(Barrick)':>16}")
    print(f"  {'-'*14} {'-'*16} {'-'*16}")
    for label, v_nem, v_gold in rows:
        print(f"  {label:<14} {v_nem:>16} {v_gold:>16}")

    # ── 儲存 ─────────────────────────────────────────────────────
    now_str  = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out_lines = [f"## NEM vs GOLD 深度比較 — {now_str}", ""]
    # 資料新鮮度橫幅（兩標的）
    for d in [nem, gold]:
        for ln in freshness_banner(d["ticker"], d["quote"]):
            out_lines.append(f"> {ln}")
    out_lines.append("")
    out_lines.append("| 指標 | NEM（Newmont） | GOLD（Barrick） |")
    out_lines.append("|------|--------------|----------------|")
    for label, vn, vg in rows:
        out_lines.append(f"| {label} | {vn} | {vg} |")

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "miner_compare.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    print(f"\n✅ 已儲存至 {os.path.abspath(out_path)}")


if __name__ == "__main__":
    run()
