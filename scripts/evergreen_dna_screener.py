"""
長榮 DNA 篩選器 — 找出「價值循環波段股」同類

依 docs/evergreen_dna_profile.md 定義的長榮 DNA 七要素，從全市場篩出
「便宜(破淨值) + 高股息 + 低Beta獨立循環 + 高波動 + 當前外資買超」的標的。

資料來源（皆線上 API，免 Claude API）：
  - TWSE MI_INDEX  : 收盤/漲跌/量（scanner 既有）
  - TWSE T86       : 三大法人籌碼（scanner 既有）
  - TWSE BWIBBU_d  : 全市場 殖利率/本益比/股價淨值比（1 次呼叫）
  - yfinance       : Beta / 年化波動 / 市值 / 產業（僅初篩後的小名單）

用法：
    python scripts/evergreen_dna_screener.py            # 最近交易日
    python scripts/evergreen_dna_screener.py 2026-09-02 # 指定日期
"""

import io, sys, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))   # 讓 scripts/ 下也能 import src
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

import urllib3
import requests
import numpy as np
import pandas as pd
from loguru import logger
from src.monitor.logger import setup_logger

urllib3.disable_warnings()

# ── 長榮 DNA 七要素門檻（見 docs/evergreen_dna_profile.md）──────
PB_MAX        = 1.5     # 1. 價值型：股價淨值比 < 1.5
PE_MAX        = 15.0    # 2. 便宜估值：0 < 本益比 < 15
YIELD_MIN     = 4.0     # 3. 高股息：殖利率 > 4%
BETA_LOW      = 0.4     # 4. 低 Beta 獨立行情 下界
BETA_HIGH     = 0.9     #    上界
MCAP_MIN      = 200     # 6. 市值下界（億）
MCAP_MAX      = 2000    #    市值上界（億）
VOL_MIN       = 30.0    # 7. 年化波動 > 30%
FOREIGN_MIN   = 0       # 波段時機：外資買超 > 0

BENCH_BETA    = 0.54    # 長榮基準 Beta（用於契合度）
TWSE_BASE     = "https://www.twse.com.tw/rwd/zh"


def _f(x):
    try:
        return float(str(x).replace(",", ""))
    except (ValueError, TypeError):
        return np.nan


def fetch_valuation(date_str: str) -> pd.DataFrame:
    """TWSE BWIBBU_d：全市場 殖利率/本益比/股價淨值比"""
    r = requests.get(f"{TWSE_BASE}/afterTrading/BWIBBU_d",
                     params={"response": "json", "date": date_str, "selectType": "ALL"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=15, verify=False)
    j = r.json()
    data = j.get("data") or (j.get("tables", [{}])[0].get("data") if j.get("tables") else None)
    if not data:
        return pd.DataFrame()
    rows = []
    for d in data:
        rows.append({"stock_id": str(d[0]).strip(), "div_yield": _f(d[3]),
                     "pe": _f(d[5]), "pb": _f(d[6])})
    return pd.DataFrame(rows).set_index("stock_id")


def main():
    setup_logger("ERROR", "data/logs")
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None

    from src.scanner.post_market_scanner import PostMarketScanner
    s = PostMarketScanner()

    # 1) 價量 + 籌碼
    if date_arg:
        scan_date = date_arg
        df_price = s.vol_collector.fetch_daily_all(scan_date)
    else:
        scan_date, df_price = s._resolve_latest_trading_day(max_back=7)
    if df_price.empty:
        print("查無收盤資料"); return
    df_chip = s.chip_collector.fetch_chip_snapshot(scan_date)
    df = s._merge_all(df_price, df_chip)
    df.index = df.index.astype(str)
    print(f"掃描日期: {scan_date}  全市場 {len(df)} 支")

    # 2) 全市場估值
    val = fetch_valuation(scan_date.replace("-", ""))
    if val.empty:
        print("查無估值資料"); return
    df = df.join(val, how="inner")

    # 3) 套用 DNA 要素 1/2/3 + 外資買超（初篩，降低 yfinance 請求量）
    df = df[df.index.str.match(r"^[1-9]\d{3}$")]              # 排除 ETF
    m = (
        (df["pb"] < PB_MAX) & (df["pb"] > 0) &
        (df["pe"] > 0) & (df["pe"] < PE_MAX) &
        (df["div_yield"] > YIELD_MIN) &
        (df.get("foreign_net", 0) > FOREIGN_MIN)
    )
    short = df[m].copy()
    print(f"初篩(P/B<{PB_MAX} + P/E<{PE_MAX} + 殖利率>{YIELD_MIN}% + 外資買超): {len(short)} 支")
    if short.empty:
        print("今日無符合估值+籌碼初篩的標的"); return

    # 4) yfinance 批次算 Beta / 年化波動；個股取市值/產業
    import yfinance as yf
    tickers = [f"{sid}.TW" for sid in short.index]
    raw = yf.download(" ".join(tickers + ["^TWII"]), period="2y",
                      interval="1d", auto_adjust=True, progress=False, threads=True)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    twii_ret = close["^TWII"].pct_change().dropna()

    rows = []
    for sid in short.index:
        col = f"{sid}.TW"
        if col not in close.columns:
            continue
        px = close[col].dropna()
        if len(px) < 60:
            continue
        ret = px.pct_change().dropna()
        vol = ret.std() * np.sqrt(252) * 100
        aligned = pd.concat([ret, twii_ret], axis=1).dropna()
        beta = (np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1])[0, 1] /
                np.var(aligned.iloc[:, 1])) if len(aligned) > 40 else np.nan
        if np.isnan(beta):
            continue
        # DNA 要素 4/7
        if not (BETA_LOW <= beta <= BETA_HIGH and vol > VOL_MIN):
            continue
        # 市值 / 產業（個股 info）
        mcap = np.nan; sector = ""
        try:
            info = yf.Ticker(col).info
            mcap = (info.get("marketCap") or 0) / 1e8
            sector = info.get("sector") or ""
        except Exception:
            pass
        r = short.loc[sid]
        rows.append({
            "sid": sid, "name": str(r.get("stock_name", "")),
            "close": _f(r.get("close", 0)), "chg": _f(r.get("change_pct", 0)),
            "pb": r["pb"], "pe": r["pe"], "yield": r["div_yield"],
            "foreign": int(r.get("foreign_net", 0) or 0),
            "beta": beta, "vol": vol, "mcap": mcap, "sector": sector,
        })

    D = pd.DataFrame(rows)
    if D.empty:
        print("套用 Beta/波動/市值 後無符合標的"); return
    # 市值區間（軟性：有資料才篩）
    D = D[(D["mcap"].isna()) | ((D["mcap"] >= MCAP_MIN) & (D["mcap"] <= MCAP_MAX))]

    # 5) 長榮契合度評分
    def nz(col):
        x = D[col]; return (x - x.min()) / (x.max() - x.min() + 1e-9)
    D["beta_fit"] = 1 - (D["beta"] - BENCH_BETA).abs() / (BETA_HIGH - BETA_LOW)
    D["score"] = (0.25 * nz("yield") + 0.20 * (1 - nz("pb")) +
                  0.25 * nz("foreign") + 0.15 * nz("vol") +
                  0.15 * D["beta_fit"]) * 100
    D = D.sort_values("score", ascending=False)

    print("\n" + "=" * 100)
    print("  🚢 長榮 DNA 篩選結果 — 價值循環波段股（適合搭外資）")
    print("=" * 100)
    print(f"{'代號':<6}{'名稱':<9}{'收盤':>8}{'漲跌%':>7}{'P/B':>6}{'P/E':>6}{'殖利%':>7}"
          f"{'外資(張)':>10}{'Beta':>6}{'波動%':>7}{'市值億':>8}{'契合分':>7}  產業")
    for _, r in D.head(15).iterrows():
        mc = f"{r['mcap']:.0f}" if not np.isnan(r["mcap"]) else "-"
        print(f"{r['sid']:<6}{r['name']:<9}{r['close']:>8.1f}{r['chg']:>+7.1f}{r['pb']:>6.2f}"
              f"{r['pe']:>6.1f}{r['yield']:>7.2f}{r['foreign']:>+10,}{r['beta']:>6.2f}"
              f"{r['vol']:>7.0f}{mc:>8}{r['score']:>7.0f}  {r['sector']}")
    print("=" * 100)
    print("篩選門檻：P/B<1.5, 0<P/E<15, 殖利率>4%, Beta 0.4~0.9, 年化波動>30%, 市值200~2000億, 外資買超")


if __name__ == "__main__":
    main()
