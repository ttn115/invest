"""
跨市場圓桌顧問 (Multi-Market Roundtable Advisor)

將台股、美股、虛擬幣三個市場的候選標的，
整合送進圓桌會議，產出跨市場投資組合建議。

設計原則：
  - 每位圓桌成員收到全市場候選清單，一次給出跨市場觀點
  - 最後由 Claude 做一次「首席策略師」合成，輸出最終配置建議
  - 每支股票不再個別呼叫 API → 顯著降低 API 費用
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# 通用候選標的（跨市場適配層）
# ═══════════════════════════════════════════════════════════════

@dataclass
class UniversalCandidate:
    """
    跨市場統一格式

    台股 / 美股 / 虛擬幣都轉換成這個格式，
    再送給圓桌成員一起評估。
    """
    asset_id:     str           # 代號（2330 / NVDA / BTC）
    asset_name:   str           # 名稱
    market:       str           # "台股" / "美股" / "虛擬幣"
    price:        float         # 收盤 / 現價（各自幣別）
    change_pct:   float         # 日漲跌 %（crypto 用 24h）
    volume_ratio: float         # 量比
    rsi:          float         # RSI（crypto 用 1h RSI）
    score:        int           # 掃描評分
    signals:      list[str]     # 觸發信號
    risk_flags:   list[str]     # 風險警示
    extra:        dict = field(default_factory=dict)   # 市場特有欄位

    def brief_line(self) -> str:
        """單行摘要（用於組裝 prompt）"""
        risk = f" ⚠️{self.risk_flags[0]}" if self.risk_flags else ""
        sigs = " | ".join(self.signals[:2]) if self.signals else "無特殊信號"
        extra_str = ""
        if self.market == "台股":
            fn = self.extra.get("foreign_net", 0)
            extra_str = f"  外資:{fn:+,}張"
        elif self.market == "美股":
            ma = "MA50多排" if self.extra.get("above_ma50") else ("MA20" if self.extra.get("above_ma20") else "")
            ch5 = self.extra.get("change_5d", 0)
            extra_str = f"  5日:{ch5:+.1f}%  {ma}"
        elif self.market == "虛擬幣":
            ch4 = self.extra.get("change_4h", 0)
            extra_str = f"  4h:{ch4:+.1f}%"
        return (
            f"  [{self.market}] {self.asset_id} {self.asset_name}  "
            f"價格:{self.price}  日漲跌:{self.change_pct:+.1f}%{extra_str}  "
            f"量比:{self.volume_ratio:.1f}x  RSI:{self.rsi:.0f}  "
            f"評分:{self.score}  信號:{sigs}{risk}"
        )


# ─────────────────────────────────────────────────────────────
# 各市場轉換函式
# ─────────────────────────────────────────────────────────────

def from_tw_candidate(c) -> UniversalCandidate:
    """台股 ScanCandidate → UniversalCandidate"""
    return UniversalCandidate(
        asset_id=c.stock_id,
        asset_name=c.stock_name,
        market="台股",
        price=c.close,
        change_pct=c.change_pct,
        volume_ratio=c.volume_ratio,
        rsi=c.rsi,
        score=c.score,
        signals=list(c.signals),
        risk_flags=list(c.risk_flags),
        extra={
            "foreign_net": c.foreign_net,
            "trust_net":   c.trust_net,
            "total_inst":  c.total_inst,
            "above_ma20":  c.above_ma20,
            "above_ma60":  c.above_ma60,
        },
    )


def from_crypto_candidate(c) -> UniversalCandidate:
    """CryptoCandidate → UniversalCandidate"""
    return UniversalCandidate(
        asset_id=c.base,
        asset_name=c.base,
        market="虛擬幣",
        price=c.price,
        change_pct=c.change_24h,
        volume_ratio=c.volume_ratio,
        rsi=c.rsi_1h,
        score=c.score,
        signals=list(c.signals),
        risk_flags=list(c.risk_flags),
        extra={
            "change_1h":    c.change_1h,
            "change_4h":    c.change_4h,
            "rsi_4h":       c.rsi_4h,
            "above_ma20_4h": c.above_ma20_4h,
            "market_cap_rank": c.market_cap_rank,
        },
    )


def from_us_candidate(c) -> UniversalCandidate:
    """USCandidate → UniversalCandidate"""
    return UniversalCandidate(
        asset_id=c.ticker,
        asset_name=c.name or c.ticker,
        market="美股",
        price=c.close,
        change_pct=c.change_pct,
        volume_ratio=c.volume_ratio,
        rsi=c.rsi,
        score=c.score,
        signals=list(c.signals),
        risk_flags=list(c.risk_flags),
        extra={
            "change_5d":  c.change_5d,
            "above_ma20": c.above_ma20,
            "above_ma50": c.above_ma50,
            "sector":     c.sector,
        },
    )


# ═══════════════════════════════════════════════════════════════
# 圓桌成員（跨市場版）
# ═══════════════════════════════════════════════════════════════

MULTI_MARKET_MEMBERS = {
    "munger": {
        "name": "Charlie Munger（查理·芒格）",
        "role": "逆向思考 + 能力圈",
        "prompt": (
            "你是查理·芒格。面對跨市場的候選標的，"
            "先問「什麼因素會讓這些投資虧錢」。"
            "關注：能力圈邊界、過度分散、市場先生情緒。"
            "對虛擬幣持保守態度，對科技股關注壟斷地位。"
            "給出每個市場最多 2 支你認為值得考慮的標的，並說明理由。"
            "最後給出整體資金配置建議（台股/美股/虛擬幣各佔%）。"
            "嚴格控制在 300 字以內，用繁體中文回覆。"
        ),
    },
    "taleb": {
        "name": "Nassim Taleb（塔勒布）",
        "role": "尾部風險 + 反脆弱",
        "prompt": (
            "你是納西姆·塔勒布。評估這批跨市場候選標的的整體風險結構。"
            "指出：市場間相關性風險、黑天鵝暴露、凸性機會。"
            "哪些標的在極端情境下有不對稱回報？哪些會爆倉？"
            "如何建立反脆弱的投資組合？"
            "給出每個市場最多 2 支你看好的標的，以及 1 個你最擔心的風險。"
            "嚴格控制在 300 字以內，用繁體中文回覆。"
        ),
    },
    "naval": {
        "name": "Naval Ravikant（Naval）",
        "role": "非對稱押注 + 科技趨勢",
        "prompt": (
            "你是 Naval Ravikant。從科技趨勢和非對稱押注角度，"
            "評估這批跨市場候選標的。"
            "哪些標的在 AI / 去中心化 / 網路效應上有護城河？"
            "哪些是時代浪潮，哪些是曇花一現？"
            "給出你認為最有不對稱上行空間的 3 支標的（可跨市場），說明理由。"
            "嚴格控制在 300 字以內，用繁體中文回覆。"
        ),
    },
}

CHIEF_STRATEGIST_PROMPT = """
你是首席投資策略師，剛聽完芒格、塔勒布、Naval 三位顧問的跨市場分析。

請綜合三位顧問的意見，輸出以下格式的最終投資建議（繁體中文）：

## 📋 最終投資建議

### 市場配置（總資金 {capital} 元）
| 市場 | 建議配置 % | 金額（元） | 理由 |
|------|-----------|-----------|------|
| 台股 | ?% | ? | |
| 美股 | ?% | ? | |
| 虛擬幣 | ?% | ? | |
| 現金保留 | ?% | ? | |

### 精選標的（前5名）
| 優先級 | 市場 | 代號 | 建議動作 | 進場邏輯 | 風險提示 |
|-------|------|------|---------|---------|---------|

### 整體市場觀點
（2-3 句整體判斷）

### 最大風險警告
（最需要注意的 1-2 個系統性風險）

請確保建議具體可執行，不超過 500 字。
"""


# ═══════════════════════════════════════════════════════════════
# 跨市場圓桌顧問
# ═══════════════════════════════════════════════════════════════

class MultiMarketAdvisor:
    """
    跨市場圓桌投資顧問

    Usage:
        from src.advisor.multi_market_advisor import MultiMarketAdvisor

        advisor = MultiMarketAdvisor(api_key="sk-ant-...")

        report = advisor.evaluate(
            tw_candidates=tw_result.top[:5],
            crypto_candidates=crypto_result.top[:5],
            us_candidates=us_result.top[:5],
            total_capital=1_000_000,
        )
        print(report)
    """

    def __init__(
        self,
        api_key: str,
        model:   str = "claude-opus-4-5",
    ):
        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_key)
            self.model  = model
            logger.info(f"MultiMarketAdvisor 初始化：{model}")
        except ImportError:
            logger.error("anthropic SDK 未安裝，執行: pip install anthropic")
            self.client = None

    def _chat(self, system: str, user: str, max_tokens: int = 800) -> str:
        if not self.client:
            return "[Claude API 未設定]"
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=0.35,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude API 錯誤: {e}")
            return f"[API 錯誤: {e}]"

    def evaluate(
        self,
        tw_candidates:     list = None,
        crypto_candidates: list = None,
        us_candidates:     list = None,
        total_capital:     float = 1_000_000,
        market_note:       str   = "",
    ) -> str:
        """
        跨市場圓桌評估，回傳完整 Markdown 報告

        Args:
            tw_candidates     : 台股 ScanCandidate list
            crypto_candidates : CryptoCandidate list
            us_candidates     : USCandidate list
            total_capital     : 總資金（元）
            market_note       : 市場備注
        Returns:
            Markdown 格式報告字串
        """
        tw_candidates     = tw_candidates     or []
        crypto_candidates = crypto_candidates or []
        us_candidates     = us_candidates     or []

        # 轉換為統一格式
        universal: list[UniversalCandidate] = []
        universal += [from_tw_candidate(c)     for c in tw_candidates]
        universal += [from_crypto_candidate(c) for c in crypto_candidates]
        universal += [from_us_candidate(c)     for c in us_candidates]

        if not universal:
            return "⚠️ 無候選標的，無法進行圓桌評估。"

        logger.info(
            f"圓桌會議啟動：台股 {len(tw_candidates)} 支 + "
            f"虛擬幣 {len(crypto_candidates)} 支 + "
            f"美股 {len(us_candidates)} 支"
        )

        # 建立候選清單 prompt 段落
        candidates_text = self._build_candidates_text(universal, market_note)

        # ── 三位成員各給意見 ────────────────────────────────────
        opinions: dict[str, str] = {}
        for member_id, member_info in MULTI_MARKET_MEMBERS.items():
            logger.info(f"  請教 {member_info['name']}...")
            user_msg = f"{candidates_text}\n\n請給出你的跨市場投資觀點："
            opinion = self._chat(
                system=member_info["prompt"],
                user=user_msg,
                max_tokens=600,
            )
            opinions[member_id] = opinion
            logger.info(f"  {member_info['name']} 回覆完成")

        # ── 首席策略師合成 ──────────────────────────────────────
        logger.info("  首席策略師綜合分析中...")
        synthesis_user = (
            f"{candidates_text}\n\n"
            f"=== 芒格的觀點 ===\n{opinions.get('munger', '')}\n\n"
            f"=== 塔勒布的觀點 ===\n{opinions.get('taleb', '')}\n\n"
            f"=== Naval 的觀點 ===\n{opinions.get('naval', '')}\n\n"
            f"請綜合以上三位顧問的意見，給出最終投資建議。"
            f"總資金：{total_capital:,.0f} 元"
        )
        final_advice = self._chat(
            system=CHIEF_STRATEGIST_PROMPT.format(capital=f"{total_capital:,.0f}"),
            user=synthesis_user,
            max_tokens=900,
        )

        # ── 組裝完整報告 ────────────────────────────────────────
        report = self._build_report(
            universal=universal,
            opinions=opinions,
            final_advice=final_advice,
            total_capital=total_capital,
            market_note=market_note,
        )
        return report

    @staticmethod
    def _build_candidates_text(
        candidates: list[UniversalCandidate], market_note: str
    ) -> str:
        """組裝候選清單為文字"""
        lines = ["【今日跨市場候選標的】"]
        if market_note:
            lines.append(f"市場背景：{market_note}\n")

        for mkt in ["台股", "美股", "虛擬幣"]:
            group = [c for c in candidates if c.market == mkt]
            if not group:
                continue
            lines.append(f"\n--- {mkt}（{len(group)} 支）---")
            for c in group:
                lines.append(c.brief_line())

        return "\n".join(lines)

    @staticmethod
    def _build_report(
        universal:    list[UniversalCandidate],
        opinions:     dict[str, str],
        final_advice: str,
        total_capital: float,
        market_note:  str,
    ) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        tw_count     = sum(1 for c in universal if c.market == "台股")
        crypto_count = sum(1 for c in universal if c.market == "虛擬幣")
        us_count     = sum(1 for c in universal if c.market == "美股")

        lines = [
            "# 🏛️ 跨市場圓桌投資報告",
            f"**生成時間：** {now}  ｜  **總資金：** ${total_capital:,.0f}",
            f"**市場背景：** {market_note or '無'}",
            "",
            "---",
            "",
            "## 📊 候選標的摘要",
            "",
            f"| 市場 | 候選數 | Top 標的 |",
            f"|------|--------|---------|",
        ]

        for mkt in ["台股", "美股", "虛擬幣"]:
            group = [c for c in universal if c.market == mkt]
            top3 = ", ".join(c.asset_id for c in sorted(group, key=lambda x: x.score, reverse=True)[:3])
            lines.append(f"| {mkt} | {len(group)} 支 | {top3 or '無'} |")

        lines += [
            "",
            "---",
            "",
            "## 👥 圓桌成員觀點",
            "",
        ]

        icons = {"munger": "🧠", "taleb": "⚡", "naval": "🚀"}
        for mid, minfo in MULTI_MARKET_MEMBERS.items():
            opinion = opinions.get(mid, "（未取得意見）")
            lines += [
                f"### {icons.get(mid, '💬')} {minfo['name']}",
                f"*角色：{minfo['role']}*",
                "",
                opinion,
                "",
            ]

        lines += [
            "---",
            "",
            "## 🎯 首席策略師最終建議",
            "",
            final_advice,
            "",
            "---",
            "",
            "> ⚠️ **免責聲明：** 本報告由 AI 模擬多種思維框架生成，僅供學習參考，",
            "> 非正式投資建議。投資前請結合個人財務狀況與風險承受度審慎評估。",
        ]

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 快速啟動
# ═══════════════════════════════════════════════════════════════

def run_multi_market_roundtable(
    tw_candidates:     list = None,
    crypto_candidates: list = None,
    us_candidates:     list = None,
    api_key:           str  = None,
    total_capital:     float = 1_000_000,
    market_note:       str   = "",
    save_path:         str   = None,
) -> str:
    """
    一鍵執行跨市場圓桌（推薦入口）

    Returns:
        Markdown 報告字串
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return "⚠️ 未設定 ANTHROPIC_API_KEY，無法執行圓桌評估。"

    advisor = MultiMarketAdvisor(api_key=key)
    report  = advisor.evaluate(
        tw_candidates=tw_candidates,
        crypto_candidates=crypto_candidates,
        us_candidates=us_candidates,
        total_capital=total_capital,
        market_note=market_note,
    )

    # 儲存
    out_path = save_path or f"data/reports/roundtable_all_{date.today()}.md"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(report, encoding="utf-8")
    logger.info(f"📄 跨市場圓桌報告：{out_path}")

    return report
