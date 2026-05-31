#!/usr/bin/env python3
"""
候選標的篩選工具 (Candidate Screener)
======================================
從候選宇宙（candidate_universe.json）中篩選出值得加入觀察名單的標的。

篩選流程（圓桌共識）：
  第一層：芒格基本面（護城河 Munger Score ≥ 0 顯示，優先推薦 ≥ 60）
  第二層：技術面（同掃描器引擎：SMA / RSI / MACD / Bollinger）
  排名邏輯：基本面 40% + 技術面 60%（超跌優先，反脆弱 / 不對稱視角）

使用方式：
  python scripts/candidate_screener.py              # 掃描所有市場
  python scripts/candidate_screener.py --market us  # 只掃美股
  python scripts/candidate_screener.py --market tw  # 只掃台股
  python scripts/candidate_screener.py --top 5      # 只顯示前 N 名
  python scripts/candidate_screener.py --min-score 60  # 只看芒格分 ≥ N

執行完後直接用 watchlist_manager.py 新增：
  python scripts/watchlist_manager.py add us PLTR "Palantir AI" --sector 科技/AI
"""

import argparse
import json
import sys
from pathlib import Path

# ── 路徑設定 ────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent.parent
WATCHLISTS  = ROOT / "data" / "watchlists.json"
UNIVERSE    = ROOT / "data" / "candidate_universe.json"
sys.path.insert(0, str(ROOT / "src"))

# ── 延遲 import（避免啟動時報錯） ────────────────────────────────────────────
try:
    from loguru import logger
    logger.remove()
    import sys as _sys
    logger.add(_sys.stdout, level="WARNING",
               format="<yellow>{time:HH:mm:ss}</yellow> | {message}")
except ImportError:
    import logging as logger  # type: ignore

MARKET_KEY_MAP = {"us": "us_stock", "tw": "tw_stock"}


# ── JSON 工具 ─────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def current_watchlist_tickers(market_key: str) -> set[str]:
    data = load_json(WATCHLISTS)
    return {s["ticker"].upper() for s in data[market_key]["symbols"]}


# ── 技術面分析 ────────────────────────────────────────────────────────────────

def run_technical(ticker: str, market_key: str) -> dict:
    """
    對單一標的執行技術面分析，回傳：
      signal     : BUY / SELL / HOLD
      confidence : 0.0 ~ 1.0
      rsi        : 最新 RSI 值
      price      : 最新收盤價
      error      : 錯誤訊息（若失敗）
    """
    try:
        from config.settings import Settings
        from data.collector import StockCollector, TwStockCollector
        from data.indicators import IndicatorEngine
        from main import build_decision_engine

        settings   = Settings()
        ind_engine = IndicatorEngine()
        engine     = build_decision_engine(settings, market_name=market_key)

        if market_key == "us_stock":
            collector = StockCollector()
        else:
            collector = TwStockCollector()

        df = collector.fetch_ohlcv(ticker, timeframe="1d", limit=300)
        if df is None or df.empty:
            return {"error": "no_data"}

        # 填入所需欄位
        df["sentiment_value"] = 50.0
        df["btc_close"]       = df["close"]
        df["funding_rate"]    = 0.0

        df = ind_engine.add_all(
            df,
            rsi_period=14,
            sma_periods=[10, 50],
            macd_params=(12, 26, 9),
            bb_params=(20, 2.0),
        )

        decision = engine.make_decision(df, ticker)

        # RSI 欄位名稱
        rsi_col = "RSI_14" if "RSI_14" in df.columns else [c for c in df.columns if c.startswith("RSI")][0]
        rsi_val = round(float(df[rsi_col].iloc[-1]), 1)
        price   = round(float(df["close"].iloc[-1]), 2)

        return {
            "signal":     decision.final_signal.value,
            "confidence": round(decision.confidence, 2),
            "rsi":        rsi_val,
            "price":      price,
            "error":      None,
        }

    except Exception as e:
        return {"error": str(e)[:60]}


# ── 基本面分析 ────────────────────────────────────────────────────────────────

def run_munger(ticker: str, market_key: str) -> dict:
    """
    執行芒格基本面評分，回傳：
      munger_score : 0 ~ 100（-1 表示無法取得）
      verdict      : PASS / FAIL / TOO_HARD
    """
    try:
        from strategy.fundamental_screener import FundamentalScreener
        screener = FundamentalScreener(cache_ttl_hours=24.0)
        profile  = screener.screen(ticker, market=market_key)
        return {
            "munger_score": round(profile.munger_score, 1),
            "verdict":      profile.verdict,
        }
    except Exception:
        return {"munger_score": -1, "verdict": "N/A"}


# ── 綜合評分 ──────────────────────────────────────────────────────────────────

def composite_score(tech: dict, munger: dict) -> float:
    """
    綜合評分（0 ~ 100）：
      技術面 60% + 基本面 40%
      技術面分數 = 信號分 * 50 + 信心分 * 30 + RSI逆向分 * 20
        - BUY  = 50, HOLD = 25, SELL = 0
        - 信心分 = confidence * 30
        - RSI逆向分：RSI < 30 = 20, RSI < 40 = 12, RSI < 50 = 6, else 0
      基本面分數 = munger_score（0~100）
    """
    # 技術面
    if tech.get("error"):
        tech_score = 0.0
    else:
        sig = tech.get("signal", "HOLD")
        conf = tech.get("confidence", 0.0)
        rsi  = tech.get("rsi", 50.0)

        sig_pts  = {"BUY": 50, "HOLD": 25, "SELL": 0}.get(sig, 25)
        conf_pts = conf * 30
        rsi_pts  = 20 if rsi < 30 else (12 if rsi < 40 else (6 if rsi < 50 else 0))
        tech_score = sig_pts + conf_pts + rsi_pts  # 最高 100 分

    # 基本面
    mscore = munger.get("munger_score", -1)
    fund_score = mscore if mscore >= 0 else 40.0  # N/A 時給中性分

    return round(tech_score * 0.6 + fund_score * 0.4, 1)


# ── 輸出報告 ──────────────────────────────────────────────────────────────────

def signal_emoji(signal: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(signal, "⚪")


def verdict_emoji(verdict: str, score: float) -> str:
    if verdict == "PASS":
        return f"✅{int(score)}"
    if verdict == "FAIL":
        return f"❌{int(score)}"
    if verdict == "TOO_HARD":
        return "🔷N/A"
    return f"—"


def print_report(results: list, market: str, top_n: int | None) -> None:
    label = "🇺🇸 美股" if market == "us" else "🇹🇼 台股"

    # 排序：綜合分 降序
    results = sorted(results, key=lambda r: -r["composite"])

    if top_n:
        results = results[:top_n]

    print(f"\n{'='*70}")
    print(f"  {label} 候選標的篩選報告")
    print(f"{'='*70}")
    print(f"  {'代碼':<8} {'名稱':<10} {'信號':^6} {'信心':^6} {'RSI':^6} {'芒格分':^8} {'綜合分':^8} 板塊")
    print(f"  {'-'*65}")

    recommended = []
    watch_only  = []

    for r in results:
        ticker  = r["ticker"]
        name    = r.get("name", "")
        tech    = r["tech"]
        munger  = r["munger"]
        comp    = r["composite"]

        if tech.get("error"):
            sig_str  = "—"
            conf_str = "—"
            rsi_str  = "—"
            emoji    = "⚪"
        else:
            sig_str  = tech["signal"]
            conf_str = f"{tech['confidence']:.2f}"
            rsi_str  = f"{tech['rsi']:.1f}"
            emoji    = signal_emoji(sig_str)

        ms_str = verdict_emoji(munger["verdict"], munger["munger_score"])
        sector = r.get("sector", "")
        display = f"{ticker} {name}".strip()

        line = (f"  {emoji} {display:<16} {sig_str:^6} {conf_str:^6} "
                f"{rsi_str:^6} {ms_str:^8} {comp:^8.1f} {sector}")
        print(line)

        # 分類推薦
        ms = munger["munger_score"]
        if not tech.get("error") and comp >= 50 and (ms >= 60 or ms < 0):
            recommended.append(r)
        else:
            watch_only.append(r)

    # 推薦摘要
    print(f"\n{'─'*70}")
    if recommended:
        print(f"\n  🏆 值得優先考慮新增的標的（綜合分 ≥ 50 且芒格 PASS）：\n")
        for r in recommended[:5]:
            t = r["ticker"]
            name = r.get("name", "")
            note = r.get("note", "")
            sec  = r.get("sector", "")
            display = f"{t} {name}".strip()
            tech = r["tech"]
            rsi_hint = ""
            if not tech.get("error"):
                if tech["rsi"] < 30:
                    rsi_hint = "（RSI 超跌，不對稱機會）"
                elif tech["rsi"] < 40:
                    rsi_hint = "（RSI 偏低，逢低區間）"

            add_cmd = _add_cmd(market, r)
            print(f"    • {display:<14} [{sec}] {note} {rsi_hint}")
            print(f"      👉 {add_cmd}\n")
    else:
        print("\n  目前候選標的無強力推薦，可調整 --min-score 查看更多。")

    print(f"\n{'─'*70}")
    print(f"  新增到候選宇宙：python scripts/watchlist_manager.py universe-add {market} <代碼> <說明> --sector <板塊>")
    print(f"  查看板塊缺口：python scripts/watchlist_manager.py sectors {market}")
    print(f"{'='*70}\n")


def _add_cmd(market: str, r: dict) -> str:
    t    = r["ticker"]
    name = r.get("name", "")
    note = r.get("note", "")
    sec  = r.get("sector", "其他")
    if market == "us":
        return f'python scripts/watchlist_manager.py add us {t} "{note}" --sector {sec}'
    else:
        return f'python scripts/watchlist_manager.py add tw {t} {name} "{note}" --sector {sec}'


# ── 主流程 ────────────────────────────────────────────────────────────────────

def screen_market(market: str, min_munger: int, top_n: int | None) -> None:
    market_key = MARKET_KEY_MAP[market]
    uni_data   = load_json(UNIVERSE)
    candidates = uni_data.get(market_key, [])
    wl_tickers = current_watchlist_tickers(market_key)

    # 過濾掉已在觀察名單的
    candidates = [c for c in candidates if c["ticker"].upper() not in wl_tickers]

    label = "🇺🇸 美股" if market == "us" else "🇹🇼 台股"
    print(f"\n{label} 候選宇宙：{len(candidates)} 支待分析（已排除觀察名單中的標的）")

    results = []
    for i, c in enumerate(candidates, 1):
        ticker = c["ticker"]
        name   = c.get("name", "")
        display = f"{ticker} {name}".strip()
        print(f"  [{i}/{len(candidates)}] 分析 {display}...", end=" ", flush=True)

        tech   = run_technical(ticker, market_key)
        munger = run_munger(ticker, market_key)
        comp   = composite_score(tech, munger)

        ms = munger["munger_score"]
        if ms >= 0 and ms < min_munger:
            print(f"芒格分 {ms} < {min_munger}，略過")
            continue

        print(f"信號:{tech.get('signal','—')} RSI:{tech.get('rsi','—')} 芒格:{ms} 綜合:{comp}")

        results.append({
            "ticker":    ticker,
            "name":      c.get("name", ""),
            "sector":    c.get("sector", ""),
            "note":      c.get("note", ""),
            "tech":      tech,
            "munger":    munger,
            "composite": comp,
        })

    if not results:
        print(f"\n  ⚠️  沒有符合條件的候選標的（min_munger={min_munger}）")
        return

    print_report(results, market, top_n)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="候選標的篩選工具 — 從候選宇宙中發現值得加入觀察名單的標的",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  python scripts/candidate_screener.py                # 掃描全部市場
  python scripts/candidate_screener.py --market us    # 只掃美股
  python scripts/candidate_screener.py --market tw    # 只掃台股
  python scripts/candidate_screener.py --top 5        # 只顯示前5名
  python scripts/candidate_screener.py --min-score 60 # 芒格分 ≥ 60 才顯示
        """,
    )
    parser.add_argument("--market",    choices=["us", "tw", "all"], default="all",
                        help="掃描市場（預設 all）")
    parser.add_argument("--top",       type=int, default=None,
                        help="只顯示前 N 名候選")
    parser.add_argument("--min-score", type=int, default=0, dest="min_score",
                        help="最低芒格分門檻（預設 0，顯示全部）")

    args = parser.parse_args()

    markets = ["us", "tw"] if args.market == "all" else [args.market]
    for m in markets:
        screen_market(m, min_munger=args.min_score, top_n=args.top)


if __name__ == "__main__":
    main()
