"""
market_dashboard.md 統一更新工具

每次掃描後呼叫 update_market_section(market, lines) 更新對應區塊。
三個掃描器（加密幣 / 美股 / 台股）共用同一份 data/market_dashboard.md。

用法:
    from report_writer import update_market_section
    update_market_section("crypto", lines)   # 'crypto' | 'us' | 'tw'
"""

import re
import datetime as dt
from pathlib import Path

_BASE = Path(__file__).parent.parent
DASHBOARD_PATH = _BASE / "data" / "market_dashboard.md"

_SECTION_KEYS = {"crypto": "CRYPTO", "us": "US", "tw": "TW"}
_SECTION_TITLES = {
    "CRYPTO": "🪙 加密幣 (Crypto)",
    "US": "🇺🇸 美股 (US Stocks)",
    "TW": "🇹🇼 台股 (Taiwan Stocks)",
}
_INIT_HEADER = (
    "# 市場掃描總覽\n\n"
    "> 三大市場即時掃描匯總（加密幣 / 美股 / 台股）  \n"
)

# ── 信號 icon ──────────────────────────────────────────────────
_SIG_ICON = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "HOLD": "⚪"}

# 芒格分門檻：高分標的 SELL = 減倉（護城河仍在），低分 = 清倉
_MUNGER_SELL_THRESHOLD = 60  # 分數 >= 60 → 減倉，< 60 → 清倉


def _sell_icon(munger_score_str: str) -> str:
    """
    依芒格分數決定 SELL 信號的標籤：
      ✅ >= 60 → 🔴 SELL(減倉)   護城河仍在，只是技術超買
      ❌ <  60 → 🔴 SELL(清倉)   基本面弱 + 技術超買，全出
      —  無分數 → 🔴 SELL         無基本面資料，維持原信號
    """
    if not munger_score_str or munger_score_str == "—":
        return "🔴 SELL"
    # 去除 ✅ / ❌ icon，取純數字
    clean = munger_score_str.replace("✅", "").replace("❌", "").strip()
    try:
        score = float(clean)
        return "🔴 SELL(減倉)" if score >= _MUNGER_SELL_THRESHOLD else "🔴 SELL(清倉)"
    except (ValueError, TypeError):
        return "🔴 SELL"


def update_market_section(market: str, lines: list) -> None:
    """
    更新 data/market_dashboard.md 中特定市場的區塊。

    market : 'crypto' | 'us' | 'tw'
    lines  : 該市場區塊的 Markdown 行清單（不含區塊標題）
    """
    key = _SECTION_KEYS.get(market.lower(), market.upper())
    title = _SECTION_TITLES.get(key, market)
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    body = "\n".join(lines)
    block = (
        f"<!-- SECTION:{key} -->\n"
        f"## {title} — {now}\n\n"
        f"{body}\n\n"
        f"<!-- END:{key} -->"
    )

    # 讀取現有或初始化
    if DASHBOARD_PATH.exists():
        full = DASHBOARD_PATH.read_text(encoding="utf-8")
    else:
        DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        full = _INIT_HEADER

    # 更新頂部「最後更新」時間戳記
    update_line = f"> 最後更新：{now}"
    if re.search(r"^> 最後更新：", full, re.MULTILINE):
        full = re.sub(r"^> 最後更新：.*$", update_line, full, flags=re.MULTILINE)
    else:
        full = full.rstrip("\n") + "\n" + update_line + "\n\n"

    # 替換或附加區塊
    start_tag = f"<!-- SECTION:{key} -->"
    end_tag = f"<!-- END:{key} -->"
    if start_tag in full and end_tag in full:
        si = full.index(start_tag)
        ei = full.index(end_tag) + len(end_tag)
        full = full[:si].rstrip() + "\n\n" + block + "\n" + full[ei:].lstrip("\n")
    else:
        full = full.rstrip() + "\n\n---\n\n" + block + "\n"

    DASHBOARD_PATH.write_text(full, encoding="utf-8")


# ── 績效行格式化 ──────────────────────────────────────────────

def _build_perf_line(win_rate_text: str, perf_stats: dict = None) -> str:
    """
    產生精簡績效摘要行，供各市場區塊使用。

    優先使用 perf_stats dict（含最大回撤、獲利因子等完整數據）；
    若無 perf_stats，則從 win_rate_text 文字解析。
    """
    if perf_stats and perf_stats.get("total", 0) > 0:
        s = perf_stats
        pnl_sign = "+" if s["total_pnl_pct"] >= 0 else ""
        pf_icon = "🟢" if s.get("profit_factor", 0) >= 1.0 else "🔴"
        return (
            f"累計 {pnl_sign}{s['total_pnl_pct']:.2f}% | "
            f"勝率 {s['win_rate']:.0f}% ({s['wins']}勝{s['losses']}敗/{s['total']}筆) | "
            f"最大回撤 {s['max_loss_pct']:+.2f}% | "
            f"{pf_icon} 獲利因子 {s['profit_factor']}"
        )

    # fallback：解析第一行文字
    if not win_rate_text:
        return ""
    first = win_rate_text.strip().split("\n")[0]
    return re.sub(r"[*_]", "", first).strip(" 💰")


# ── 各市場 Markdown 格式化工具 ────────────────────────────────


def build_crypto_lines(report_df, market_ctx, fg_val: float,
                       win_rate_text: str, sol_bias,
                       perf_stats: dict = None,
                       tvl_data: dict = None) -> list:
    """
    加密幣掃描結果 → Markdown 行清單

    report_df 需有欄位: Symbol, Price, Signal, Confidence, RSI, Signal_1d, RSI_1d
    market_ctx: MarketContext（phase / season / mtf_alignment / fg_3d_trend）
    sol_bias: TradingBias（blocked_contexts / golden_contexts）
    perf_stats: SignalTracker.get_performance_stats() 回傳值（可選）
    """
    lines = []

    # 市場背景一行摘要
    mtf = getattr(market_ctx, "mtf_alignment", "?")
    lines.append(
        f"**市場環境**：{market_ctx.phase} | {market_ctx.season} | "
        f"MTF: {mtf} | FG: {fg_val:.0f} | FG趨勢: {market_ctx.fg_3d_trend}"
    )
    lines.append("")

    # 信號表格（短線 + 長線）
    if tvl_data:
        lines.append("| 信號 | 標的 | 價格(USD) | RSI(1h) | TVL | 短線 | 長線 |")
        lines.append("|:----:|------|----------:|:-------:|:---:|:----:|:----:|")
    else:
        lines.append("| 信號 | 標的 | 價格(USD) | RSI(1h) | 短線 | 長線 |")
        lines.append("|:----:|------|----------:|:-------:|:----:|:----:|")

    for _, row in report_df.iterrows():
        sig = row["Signal"]
        icon = _SIG_ICON.get(sig, "⚪")
        s1d = row.get("Signal_1d", "—")
        try:
            price_str = f"{float(row['Price']):,.2f}"
        except (ValueError, TypeError):
            price_str = str(row["Price"])

        tvl_str = "—"
        if tvl_data and row["Symbol"] in tvl_data:
            tvl_str = tvl_data[row["Symbol"]]

        if tvl_data:
            lines.append(
                f"| {icon} | {row['Symbol']} | {price_str} | "
                f"{row['RSI']} | {tvl_str} | {sig} | {s1d} |"
            )
        else:
            lines.append(
                f"| {icon} | {row['Symbol']} | {price_str} | "
                f"{row['RSI']} | {sig} | {s1d} |"
            )

    lines.append("")

    # 績效（優先使用 perf_stats，否則解析文字）
    perf_line = _build_perf_line(win_rate_text, perf_stats)
    if perf_line:
        lines.append(f"**績效**：{perf_line}")

    # SOL 有毒 / 黃金環境
    if sol_bias and sol_bias.blocked_contexts:
        blocked_str = " · ".join(
            b["key"].replace("|", "\\|") for b in sol_bias.blocked_contexts[:4]
        )
        lines.append(f"**SOL 有毒**：{blocked_str}")
    if sol_bias and sol_bias.golden_contexts:
        golden_str = " · ".join(
            g["key"].replace("|", "\\|") for g in sol_bias.golden_contexts[:3]
        )
        lines.append(f"**SOL 黃金**：{golden_str}")

    return lines


def build_us_lines(report_df, us_ctx, win_rate_text: str,
                   munger_scores: dict = None,
                   munger_profiles: dict = None,
                   perf_stats: dict = None) -> list:
    """
    美股掃描結果 → Markdown 行清單

    report_df 需有欄位: Symbol, Price, Signal, Confidence, RSI
    us_ctx: UsMarketContext
    munger_scores: {symbol: "78"} 或 {symbol: "N/A"} （可選，若提供則顯示芒格分欄位）
    munger_profiles: {symbol: FundamentalProfile} (可選，用於顯示 PEG 和 FCF)
    perf_stats: SignalTracker.get_performance_stats() 回傳值（可選）
    """
    lines = []

    # 市場背景
    skew_str = ""
    if hasattr(us_ctx, "skew"):
        skew_str = f"SKEW: {us_ctx.skew:.1f} {us_ctx.skew_level} | "

    lines.append(
        f"**市場環境**：SPY ${us_ctx.spy_close:.2f} | Phase: {us_ctx.spy_phase} | "
        f"Trend: {us_ctx.spy_trend} | VIX: {us_ctx.vix:.1f} {us_ctx.vix_level} | {skew_str}"
        f"10Y: {us_ctx.yield_10y:.2f}% {us_ctx.rate_env} | 情緒: {us_ctx.market_sentiment}"
    )
    lines.append("")

    # 信號表格（有無芒格分欄位）
    if munger_profiles:
        lines.append("| 信號 | 標的 | 價格(USD) | RSI | 芒格分 | PEG | FCF | 信心 |")
        lines.append("|:----:|------|----------:|:---:|:------:|:---:|:---:|:----:|")
    elif munger_scores:
        lines.append("| 信號 | 標的 | 價格(USD) | RSI | 芒格分 | 信心 |")
        lines.append("|:----:|------|----------:|:---:|:------:|:----:|")
    else:
        lines.append("| 信號 | 標的 | 價格(USD) | RSI | 信心 |")
        lines.append("|:----:|------|----------:|:---:|:----:|")

    for _, row in report_df.iterrows():
        sig = row["Signal"]
        score_str = munger_scores.get(row["Symbol"], "—") if munger_scores else "—"
        
        peg_str = "—"
        fcf_str = "—"
        if munger_profiles and row["Symbol"] in munger_profiles:
            prof = munger_profiles[row["Symbol"]]
            peg_str = f"{prof.peg_ratio:.2f}" if prof.peg_ratio > 0 else "—"
            fcf_str = f"{prof.fcf_ttm/1e9:.1f}B" if prof.fcf_ttm != 0 else "—"

        # SELL 信號依芒格分分級
        if sig == "SELL":
            icon = _sell_icon(score_str) if munger_scores else "🔴 SELL"
        else:
            icon = _SIG_ICON.get(sig, "⚪")
        try:
            price_str = f"${float(row['Price']):,.2f}"
        except (ValueError, TypeError):
            price_str = str(row["Price"])

        if munger_profiles:
            lines.append(
                f"| {icon} | {row['Symbol']} | {price_str} | "
                f"{row['RSI']} | {score_str} | {peg_str} | {fcf_str} | {row['Confidence']} |"
            )
        elif munger_scores:
            lines.append(
                f"| {icon} | {row['Symbol']} | {price_str} | "
                f"{row['RSI']} | {score_str} | {row['Confidence']} |"
            )
        else:
            lines.append(
                f"| {icon} | {row['Symbol']} | {price_str} | "
                f"{row['RSI']} | {row['Confidence']} |"
            )

    lines.append("")

    # 績效
    perf_line = _build_perf_line(win_rate_text, perf_stats)
    if perf_line:
        lines.append(f"**績效**：{perf_line}")

    return lines


def build_tw_lines(report_df, tw_ctx, win_rate_text: str,
                   munger_scores: dict = None,
                   munger_profiles: dict = None,
                   perf_stats: dict = None) -> list:
    """
    台股掃描結果 → Markdown 行清單

    report_df 需有欄位: Symbol, Name, Price, Signal, Confidence, RSI
    tw_ctx: TwMarketContext
    munger_scores: {symbol: "72"} 或 {symbol: "N/A"} （可選）
    munger_profiles: {symbol: FundamentalProfile} (可選，用於顯示 PEG 和 FCF)
    perf_stats: SignalTracker.get_performance_stats() 回傳值（可選）
    """
    lines = []

    # 量比異常偵測
    vol_ratio = getattr(tw_ctx, "volume_ratio", None)
    if vol_ratio is not None:
        if vol_ratio < 0.01:
            vol_str = f"量比: {vol_ratio:.2f}x ❓"  # 資料異常或假日
        elif vol_ratio < 0.5:
            vol_str = f"量比: {vol_ratio:.2f}x ⚠️"  # 量能萎縮
        else:
            vol_str = f"量比: {vol_ratio:.2f}x"
    else:
        vol_str = getattr(tw_ctx, "volume_status", "量比: N/A")

    lines.append(
        f"**市場環境**：加權 {tw_ctx.taiex_close:,.0f} | Phase: {tw_ctx.taiex_phase} | "
        f"RSI: {tw_ctx.taiex_rsi:.1f} {tw_ctx.taiex_trend} | {vol_str}"
    )

    # 三大法人（有資料才顯示）
    inst_sent = getattr(tw_ctx, "institutional_sentiment", "UNKNOWN")
    if inst_sent != "UNKNOWN":
        sent_icon = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}.get(inst_sent, "❓")
        foreign   = getattr(tw_ctx, "foreign_buy_sell",  0.0)
        trust     = getattr(tw_ctx, "trust_buy_sell",    0.0)
        dealer    = getattr(tw_ctx, "dealer_buy_sell",   0.0)
        total     = foreign + trust + dealer
        lines.append(
            f"**三大法人**：{sent_icon} 外資 {foreign:+.1f}億 | "
            f"投信 {trust:+.1f}億 | 自營商 {dealer:+.1f}億 | "
            f"合計 {total:+.1f}億"
        )
    else:
        lines.append("**三大法人**：❓ 資料尚未公布（盤中 / 假日）")

    lines.append("")

    # 信號表格（有無芒格分欄位）
    if munger_profiles:
        lines.append("| 信號 | 代碼 | 名稱 | 價格(TWD) | RSI | 芒格分 | PEG | FCF | 信心 |")
        lines.append("|:----:|:----:|------|----------:|:---:|:------:|:---:|:---:|:----:|")
    elif munger_scores:
        lines.append("| 信號 | 代碼 | 名稱 | 價格(TWD) | RSI | 芒格分 | 信心 |")
        lines.append("|:----:|:----:|------|----------:|:---:|:------:|:----:|")
    else:
        lines.append("| 信號 | 代碼 | 名稱 | 價格(TWD) | RSI | 信心 |")
        lines.append("|:----:|:----:|------|----------:|:---:|:----:|")

    for _, row in report_df.iterrows():
        sig = row["Signal"]
        score_str = munger_scores.get(row["Symbol"], "—") if munger_scores else "—"
        
        peg_str = "—"
        fcf_str = "—"
        if munger_profiles and row["Symbol"] in munger_profiles:
            prof = munger_profiles[row["Symbol"]]
            peg_str = f"{prof.peg_ratio:.2f}" if prof.peg_ratio > 0 else "—"
            fcf_str = f"{prof.fcf_ttm/1e9:.1f}B" if prof.fcf_ttm != 0 else "—"

        # SELL 信號依芒格分分級
        if sig == "SELL":
            icon = _sell_icon(score_str) if munger_scores else "🔴 SELL"
        else:
            icon = _SIG_ICON.get(sig, "⚪")
        try:
            price_str = f"{float(row['Price']):,.1f}"
        except (ValueError, TypeError):
            price_str = str(row["Price"])
        name = row.get("Name", "")

        if munger_profiles:
            lines.append(
                f"| {icon} | {row['Symbol']} | {name} | {price_str} | "
                f"{row['RSI']} | {score_str} | {peg_str} | {fcf_str} | {row['Confidence']} |"
            )
        elif munger_scores:
            lines.append(
                f"| {icon} | {row['Symbol']} | {name} | {price_str} | "
                f"{row['RSI']} | {score_str} | {row['Confidence']} |"
            )
        else:
            lines.append(
                f"| {icon} | {row['Symbol']} | {name} | {price_str} | "
                f"{row['RSI']} | {row['Confidence']} |"
            )

    lines.append("")

    # 績效
    perf_line = _build_perf_line(win_rate_text, perf_stats)
    if perf_line:
        lines.append(f"**績效**：{perf_line}")

    return lines
