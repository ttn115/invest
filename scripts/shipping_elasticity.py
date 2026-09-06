"""
航運族群循環彈性回測 (Shipping Cycle Elasticity Backtest)

回答一個具體問題：當運價循環上行時，長榮/陽明/萬海誰的股價彈性最大？

背景：使用者 2024 年抓到紅海運價行情，但買了長榮(+34.6%)，
      同期萬海 +102.8%、陽明 +70.3% — 抓對循環卻選錯標的。
      本工具量化「循環中誰彈性最大」，供下次拐點時選股用。

方法：
  1. 用三檔台灣貨櫃航運股建立等權複合指數
  2. Zigzag 演算法偵測歷史上行循環（漲幅 > 門檻）
  3. 計算每個循環中各股報酬 + 相對長榮的倍數
  4. 迴歸法計算彈性係數（對複合指數的 beta）

用法：
    python scripts/shipping_elasticity.py
    python scripts/shipping_elasticity.py 50    # 自訂上行循環門檻 50%
"""

import io, sys, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf

# 台灣貨櫃航運三雄 + 國際對照
TW_CONTAINER = {"2603.TW": "長榮", "2609.TW": "陽明", "2615.TW": "萬海"}
REFS = {"ZIM": "ZIM(國際貨櫃)", "MATX": "Matson(美線)", "^TWII": "台股大盤"}

SWING_THRESHOLD = 40.0   # 上行循環認定門檻（%）


def fetch(tickers, period="max"):
    out = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period=period, auto_adjust=True)["Close"].dropna()
            if len(h) > 100:
                h.index = h.index.tz_localize(None) if h.index.tz else h.index
                out[t] = h
        except Exception:
            pass
    return out


def build_composite(data: dict) -> pd.Series:
    """三檔等權複合指數（以各自起點正規化）"""
    df = pd.DataFrame({t: s for t, s in data.items() if t in TW_CONTAINER}).dropna()
    if df.empty:
        return pd.Series(dtype=float)
    norm = df / df.iloc[0]
    return norm.mean(axis=1)


def detect_upcycles(s: pd.Series, thr: float) -> list:
    """
    Zigzag 偵測上行循環：從谷底漲超過 thr% 視為一次上行循環，
    回檔超過 thr/2 % 確認峰頂。
    """
    cycles = []
    trough_i, trough_v = 0, s.iloc[0]
    peak_i, peak_v = 0, s.iloc[0]
    rising = False

    for i in range(1, len(s)):
        v = s.iloc[i]
        if not rising:
            if v < trough_v:
                trough_i, trough_v = i, v
            elif (v / trough_v - 1) * 100 >= thr:
                rising = True
                peak_i, peak_v = i, v
        else:
            if v > peak_v:
                peak_i, peak_v = i, v
            elif (v / peak_v - 1) * 100 <= -thr / 2:
                cycles.append((s.index[trough_i], s.index[peak_i]))
                rising = False
                trough_i, trough_v = i, v
    if rising:
        cycles.append((s.index[trough_i], s.index[peak_i]))
    return cycles


def ret_between(s: pd.Series, d0, d1):
    seg = s[(s.index >= d0) & (s.index <= d1)]
    if len(seg) < 2:
        return None
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100


def main():
    thr = float(sys.argv[1]) if len(sys.argv) > 1 else SWING_THRESHOLD
    all_t = list(TW_CONTAINER) + list(REFS)
    print("📡 抓取歷史資料...")
    data = fetch(all_t)
    missing = [t for t in all_t if t not in data]
    if missing:
        print(f"   （缺: {', '.join(missing)}）")

    comp = build_composite(data)
    if comp.empty:
        print("無法建立複合指數"); return

    cycles = detect_upcycles(comp, thr)
    print(f"\n資料範圍 {comp.index[0].date()} ~ {comp.index[-1].date()}")
    print(f"偵測到 {len(cycles)} 個上行循環（門檻 +{thr:.0f}%）")

    # ── 逐循環報酬 ──────────────────────────────────────────
    rows = []
    print("\n" + "="*94)
    print("  各上行循環中的報酬對照")
    print("="*94)
    for k, (d0, d1) in enumerate(cycles, 1):
        days = (d1 - d0).days
        print(f"\n▼ 循環 {k}: {d0.date()} → {d1.date()}  ({days} 天)")
        base = ret_between(data.get("2603.TW", pd.Series(dtype=float)), d0, d1)
        line = {}
        for t, nm in {**TW_CONTAINER, **REFS}.items():
            if t not in data:
                continue
            r = ret_between(data[t], d0, d1)
            if r is None:
                continue
            line[nm] = r
            mult = f"（長榮的 {r/base:.1f}x）" if base and base > 0 and t in TW_CONTAINER and t != "2603.TW" else ""
            tag = "★" if t in TW_CONTAINER else " "
            print(f"   {tag} {nm:<14} {r:>+8.1f}%  {mult}")
        rows.append({"cycle": k, "start": d0.date(), "end": d1.date(), **line})

    # ── 彈性排行（跨循環平均）────────────────────────────────
    print("\n" + "="*94)
    print("  📊 循環彈性排行（跨所有上行循環的平均報酬）")
    print("="*94)
    D = pd.DataFrame(rows)
    tw_names = list(TW_CONTAINER.values())
    avail = [n for n in tw_names if n in D.columns]
    if avail:
        avg = D[avail].mean().sort_values(ascending=False)
        base_avg = avg.get("長榮", np.nan)
        print(f"{'排名':<5}{'標的':<10}{'平均循環報酬':>14}{'相對長榮倍數':>14}")
        for i, (nm, v) in enumerate(avg.items(), 1):
            mult = f"{v/base_avg:.2f}x" if base_avg and base_avg > 0 else "-"
            print(f"{i:<5}{nm:<10}{v:>13.1f}%{mult:>14}")

    # ── 迴歸彈性係數（對複合指數的 beta）────────────────────
    print("\n" + "="*94)
    print("  📈 彈性係數（日報酬對航運複合指數迴歸，>1 = 放大器）")
    print("="*94)
    comp_ret = comp.pct_change().dropna()
    print(f"{'標的':<14}{'彈性係數β':>12}{'R²':>10}  解讀")
    for t, nm in TW_CONTAINER.items():
        if t not in data:
            continue
        r = data[t].pct_change().dropna()
        al = pd.concat([r, comp_ret], axis=1).dropna()
        if len(al) < 100:
            continue
        x, y = al.iloc[:, 1].values, al.iloc[:, 0].values
        beta = np.cov(y, x)[0, 1] / np.var(x)
        corr = np.corrcoef(y, x)[0, 1]
        note = "放大器（循環中彈性大）" if beta > 1.05 else ("減震器（彈性小）" if beta < 0.95 else "同步")
        print(f"{nm:<14}{beta:>12.2f}{corr**2:>10.2f}  {note}")

    print("\n" + "="*94)
    print("結論用法：下次 SCFI/ZIM 確認運價翻揚時，優先選彈性係數與平均循環報酬最高者。")


if __name__ == "__main__":
    main()
