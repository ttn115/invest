"""
台股航運股深度分析模組 (TW Shipping Deep Analysis)

針對貨櫃航運股（長榮 2603、陽明 2609、萬海 2615）生成深度分析區塊，
整合 SCFI/BDI 數據、Dalio 矩陣定位、技術面指標，輸出 Markdown 格式報告。

用法（由 tw_stock_scanner.py 呼叫）：
    from tw_deep_analysis import build_shipping_block, SHIPPING_SYMBOLS

    if symbol in SHIPPING_SYMBOLS:
        block = build_shipping_block(
            symbol=symbol, name=name, price=price,
            rsi=rsi, peg=peg, munger_score=munger_score,
            freight=freight_ctx, rate_10y=None,   # None = 自動抓即時 ^TNX
            cost_basis=184.5,   # 可選，None = 不顯示損益
        )
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from freight_index_fetcher import FreightContext

# 貨櫃航運股代碼（這三支使用 SCFI 深度分析）
SHIPPING_SYMBOLS = {"2603", "2609", "2615"}

_OUTLOOK_LABEL = {
    "BULLISH":        "🚀 強烈看多",
    "MILDLY_BULLISH": "🟢 溫和看多",
    "NEUTRAL":        "⚪ 中性觀望",
    "MILDLY_BEARISH": "🟡 溫和看空",
    "BEARISH":        "🔴 看空",
    "UNKNOWN":        "❓ 資料不足",
}


def _rsi_tag(rsi: float) -> str:
    if rsi >= 70:
        return f"RSI {rsi:.1f} ⚠️超買"
    if rsi <= 30:
        return f"RSI {rsi:.1f} 💡超賣"
    return f"RSI {rsi:.1f}"


def _peg_tag(peg: float) -> str:
    if peg <= 0:
        return "PEG N/A"
    if peg < 1.0:
        return f"PEG {peg:.2f} 💡低估"
    if peg < 1.5:
        return f"PEG {peg:.2f}"
    return f"PEG {peg:.2f} ⚠️偏高"


def _recommendation(
    outlook: str,
    rsi: float,
    peg: float,
    pct_1m_scfi: float,
) -> tuple[str, str]:
    """
    回傳 (建議標籤, 說明)

    邏輯：
    - BULLISH + RSI 超賣 → 強力加碼
    - BULLISH/MILDLY_BULLISH + RSI 正常 → 持有/小加
    - NEUTRAL + RSI 超賣 + PEG 低估 → 持有等確認
    - NEUTRAL/BEARISH + RSI 超買 → 減碼
    - BEARISH → 謹慎持有/出場評估
    """
    oversold = rsi <= 32
    overbought = rsi >= 70
    peg_cheap = 0 < peg < 1.2

    if outlook in ("BULLISH",) and oversold:
        return "🔥 強力加碼", "SCFI 月漲超 10%，RSI 超賣——基本面強 + 技術面打折，林區最愛的組合"
    if outlook in ("BULLISH", "MILDLY_BULLISH") and not overbought:
        return "🟢 持有／小加", "SCFI 上行趨勢支撐，RSI 正常，繼續持有並可小量加碼"
    if outlook == "NEUTRAL" and oversold and peg_cheap:
        return "⚪ 持有等確認", "SCFI 中性但 RSI 超賣、PEG 低估，等待 SCFI 方向確認再加碼"
    if outlook == "NEUTRAL":
        return "⚪ 持有觀察", "SCFI 橫盤中性，持有現有部位，不建議加碼"
    if overbought:
        return "⚠️ 逢高減碼", "RSI 超買，技術面需回調，可考慮部分獲利了結"
    if outlook in ("MILDLY_BEARISH", "BEARISH"):
        return "🔴 謹慎持有", "SCFI 走弱，利率壓制雙殺，評估部位是否過重"
    return "⚪ 持有觀察", "等待更清晰的 SCFI 方向訊號"


def build_shipping_block(
    symbol: str,
    name: str,
    price: float,
    rsi: float,
    peg: float,
    munger_score: Optional[float],
    freight: "FreightContext",
    rate_10y: Optional[float] = None,
    cost_basis: Optional[float] = None,
) -> str:
    """
    生成航運股深度分析 Markdown 區塊。

    Args:
        rate_10y: 10年期公債殖利率(%)。None = 自動抓取即時 ^TNX
                  （原本寫死 4.37，會讓 Dalio 象限用過時利率判斷）

    回傳值：可直接附加到 market_dashboard.md 的 Markdown 字串。
    """
    if rate_10y is None:
        from freight_index_fetcher import fetch_10y_rate
        rate_10y = fetch_10y_rate()

    lines = []
    scfi = freight.scfi
    bdi = freight.bdi

    outlook = freight.evergreen_outlook(rate_10y)
    outlook_label = _OUTLOOK_LABEL.get(outlook, "❓")
    dalio = freight.dalio_position(rate_10y)

    # 損益顯示
    pnl_str = ""
    if cost_basis and cost_basis > 0:
        pnl_pct = (price - cost_basis) / cost_basis * 100
        pnl_icon = "🟢" if pnl_pct >= 0 else "🔴"
        pnl_str = f" | 持倉成本 {cost_basis:.1f} → {pnl_icon} {pnl_pct:+.2f}%"

    rec_label, rec_desc = _recommendation(
        outlook, rsi, peg,
        scfi.pct_1m if scfi.ok else 0,
    )

    lines.append(f"#### 🚢 {name} ({symbol}) 航運深度分析")
    lines.append("")
    lines.append(f"| 項目 | 數值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 現價 | **{price:.1f}** TWD{pnl_str} |")
    lines.append(f"| 技術面 | {_rsi_tag(rsi)} \\| {_peg_tag(peg)}" +
                 (f" \\| 芒格分 {munger_score:.0f}" if munger_score else "") + " |")
    lines.append(f"| SCFI（貨櫃）| {scfi.summary()} |")
    lines.append(f"| BDI（乾散貨）| {bdi.summary()} _(與長榮關聯性低)_ |")
    lines.append(f"| Dalio 矩陣 | 利率 {rate_10y:.2f}% \\| {dalio} |")
    lines.append(f"| 航運展望 | {outlook_label} |")
    lines.append(f"| **圓桌建議** | **{rec_label}** |")
    lines.append("")
    lines.append(f"> {rec_desc}")

    # 關鍵觀察點
    if scfi.ok:
        if scfi.pct_1m > 20:
            lines.append(">")
            lines.append("> ⚡ **SCFI 月漲超 20%**：運費強勁，長榮下季財報有超預期機會")
        elif scfi.pct_1m < -10:
            lines.append(">")
            lines.append("> ⚠️ **SCFI 月跌超 10%**：運費走弱，注意下季財報下修風險")
    if rsi <= 30 and scfi.ok and scfi.pct_1m > 0:
        lines.append(">")
        lines.append("> 💡 **基本面/情緒分歧**：SCFI 上行 + RSI 超賣，是林區「好故事被市場誤解」的典型場景")

    lines.append("")
    return "\n".join(lines)
