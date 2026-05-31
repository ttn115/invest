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


# 預設模型（可由 ANTHROPIC_MODEL 環境變數覆寫）
DEFAULT_MODEL = "claude-opus-4-5"


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
            f"{self._fundamental_str()}"
        )

    def _fundamental_str(self) -> str:
        """基本面摘要（僅台股/美股，且已注入時顯示）"""
        if self.market not in ("台股", "美股"):
            return ""
        if not self.extra.get("has_fundamentals"):
            return ""
        roe   = self.extra.get("roe_ttm", 0) or 0
        fcf   = self.extra.get("fcf_ttm", 0) or 0
        peg   = self.extra.get("peg_ratio", 0) or 0
        pe    = self.extra.get("pe_ratio", 0) or 0
        mscore = self.extra.get("munger_score", 0) or 0
        verdict = self.extra.get("fund_verdict", "")
        fcf_b = fcf / 1e9 if fcf else 0.0
        parts = [f"ROE:{roe*100:.0f}%"]
        if fcf:
            parts.append(f"FCF:{fcf_b:+.1f}B")
        if pe > 0:
            parts.append(f"PE:{pe:.0f}")
        if peg > 0:
            parts.append(f"PEG:{peg:.1f}")
        parts.append(f"芒格:{mscore:.0f}/100")
        if verdict:
            parts.append(verdict)
        return "  〔" + " ".join(parts) + "〕"


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
# 結構化輸出（tool-use）
# ═══════════════════════════════════════════════════════════════

@dataclass
class Pick:
    """首席策略師的單一精選標的（結構化）"""
    market:      str
    asset_id:    str
    action:      str           # BUY / WATCH / AVOID
    confidence:  int           # 0~100
    entry_logic: str = ""
    risk:        str = ""
    asset_name:  str = ""


@dataclass
class Allocation:
    """單一市場的資金配置（結構化）"""
    market: str
    pct:    float
    amount: float
    reason: str = ""


# Anthropic tool schema：強制首席策略師以結構化 JSON 回傳
SUBMIT_RECOMMENDATIONS_TOOL = {
    "name": "submit_recommendations",
    "description": "提交跨市場最終投資建議：資金配置表 + 精選標的清單。",
    "input_schema": {
        "type": "object",
        "properties": {
            "allocation": {
                "type": "array",
                "description": "各市場資金配置（台股/美股/虛擬幣/現金），pct 加總應為 100",
                "items": {
                    "type": "object",
                    "properties": {
                        "market": {"type": "string", "enum": ["台股", "美股", "虛擬幣", "現金"]},
                        "pct":    {"type": "number", "description": "配置百分比 0~100"},
                        "amount": {"type": "number", "description": "金額（元）"},
                        "reason": {"type": "string"},
                    },
                    "required": ["market", "pct", "amount", "reason"],
                },
            },
            "picks": {
                "type": "array",
                "description": "精選標的（依優先級排序，最多 6 支）",
                "items": {
                    "type": "object",
                    "properties": {
                        "market":      {"type": "string", "enum": ["台股", "美股", "虛擬幣"]},
                        "asset_id":    {"type": "string", "description": "代號，如 2330 / NVDA / BTC"},
                        "asset_name":  {"type": "string"},
                        "action":      {"type": "string", "enum": ["BUY", "WATCH", "AVOID"]},
                        "confidence":  {"type": "integer", "description": "信心分數 0~100"},
                        "entry_logic": {"type": "string", "description": "進場邏輯"},
                        "risk":        {"type": "string", "description": "風險提示"},
                    },
                    "required": ["market", "asset_id", "action", "confidence", "entry_logic", "risk"],
                },
            },
            "market_view":      {"type": "string", "description": "整體市場觀點 2~3 句"},
            "max_risk_warning": {"type": "string", "description": "最需注意的 1~2 個系統性風險"},
        },
        "required": ["allocation", "picks", "market_view", "max_risk_warning"],
    },
}

CHIEF_STRATEGIST_STRUCTURED_PROMPT = """
你是首席投資策略師，剛聽完芒格、塔勒布、Naval 三位顧問的跨市場分析。
請綜合三位顧問的意見，做出最終投資決策。

決策原則：
- 尊重「環境提示」中的歷史勝率：若當前環境歷史勝率偏低，整體應提高現金比例、降低 picks 信心分數。
- 參考各標的的基本面（ROE/FCF/PEG/芒格分數）：基本面差的標的不應給 BUY。
- 資金配置（allocation）的 pct 加總必須為 100（含現金）。
- picks 依優先級排序，confidence 必須反映真實把握度，不是固定值。

你必須呼叫 submit_recommendations 工具回傳結構化結果，總資金為 {capital} 元。
""".strip()


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
        model:   Optional[str] = None,
        enrich_fundamentals: bool = True,
    ):
        # model 可由參數或 ANTHROPIC_MODEL 環境變數設定，否則用預設
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.enrich_fundamentals = enrich_fundamentals
        self._screener = None            # 延遲初始化 FundamentalScreener
        self.last_picks: list[Pick] = []          # evaluate() 後供 tracker 取用
        self.last_allocation: list[Allocation] = []
        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_key)
            logger.info(f"MultiMarketAdvisor 初始化：{self.model}")
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

    def _chat_structured(
        self, system: str, user: str, tool: dict, max_tokens: int = 1500
    ) -> Optional[dict]:
        """以 tool-use 取回結構化 JSON（失敗回 None）"""
        if not self.client:
            return None
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=0.35,
                system=system,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": user}],
            )
            for block in msg.content:
                if getattr(block, "type", "") == "tool_use":
                    return block.input
            logger.warning("結構化輸出：未取得 tool_use block")
            return None
        except Exception as e:
            logger.error(f"Claude 結構化 API 錯誤: {e}")
            return None

    # ── 基本面注入 ──────────────────────────────────────────────

    def _enrich_with_fundamentals(self, universal: list[UniversalCandidate]) -> None:
        """對台股/美股候選注入基本面（走 yfinance，不耗 Claude API）。失敗靜默跳過。"""
        if not self.enrich_fundamentals:
            return
        try:
            if self._screener is None:
                from src.strategy.fundamental_screener import FundamentalScreener
                self._screener = FundamentalScreener()
        except Exception as e:
            logger.warning(f"FundamentalScreener 無法載入，跳過基本面注入: {e}")
            return

        market_map = {"台股": "tw_stock", "美股": "us_stock"}
        for c in universal:
            mkt = market_map.get(c.market)
            if not mkt:
                continue
            try:
                p = self._screener.screen(c.asset_id, market=mkt)
                c.extra.update({
                    "has_fundamentals": True,
                    "roe_ttm":      p.roe_ttm,
                    "fcf_ttm":      p.fcf_ttm,
                    "pe_ratio":     p.pe_ratio,
                    "peg_ratio":    p.peg_ratio,
                    "munger_score": p.munger_score,
                    "fund_verdict": p.verdict,
                })
            except Exception as e:
                logger.debug(f"  基本面注入跳過 {c.asset_id}: {e}")

    # ── 歷史環境提示 ────────────────────────────────────────────

    @staticmethod
    def _ctx_key(ctx) -> Optional[str]:
        """從 market context 物件抽出 phase|season|fg_trend 組合鍵（容錯）"""
        if ctx is None:
            return None
        phase   = getattr(ctx, "phase", "") or getattr(ctx, "taiex_phase", "")
        season  = getattr(ctx, "season", "") or "NA"
        fg      = getattr(ctx, "fg_3d_trend", "") or "NA"
        if not phase:
            return None
        return f"{phase}|{season}|{fg}"

    def _build_context_hint(self, tw_ctx=None, crypto_ctx=None) -> str:
        """用 SOL 既有分析，組出各市場當前環境的歷史勝率提示"""
        lines = []
        for label, ctx, market_type in [
            ("台股/美股", tw_ctx, "stock"),
            ("虛擬幣",     crypto_ctx, "crypto"),
        ]:
            key = self._ctx_key(ctx)
            if not key:
                continue
            try:
                from src.analysis.contextual_optimizer import ContextualOptimizer
                bias = ContextualOptimizer(market_type=market_type).get_bias()
            except Exception as e:
                logger.debug(f"  環境提示跳過 {label}: {e}")
                continue

            stat = (bias.context_stats or {}).get(key)
            blocked = any(b.get("key") == key for b in bias.blocked_contexts)
            golden  = any(g.get("key") == key for g in bias.golden_contexts)
            tag = "🚫有毒環境" if blocked else ("✨黃金環境" if golden else "")
            if stat:
                wr  = stat.get("win_rate", 0)
                pnl = stat.get("avg_pnl", 0) * 100
                n   = stat.get("total", 0)
                lines.append(
                    f"  • {label} 環境 [{key}]：歷史勝率 {wr:.0f}% "
                    f"(avg {pnl:+.2f}%, n={n}) {tag}"
                )
            elif tag:
                lines.append(f"  • {label} 環境 [{key}]：{tag}")
            else:
                lines.append(f"  • {label} 環境 [{key}]：尚無足夠歷史數據")

        if not lines:
            return ""
        return "📉 環境提示（歷史同環境表現，供保守/積極判斷）：\n" + "\n".join(lines)

    def evaluate(
        self,
        tw_candidates:     list = None,
        crypto_candidates: list = None,
        us_candidates:     list = None,
        total_capital:     float = 1_000_000,
        market_note:       str   = "",
        tw_ctx=None,
        crypto_ctx=None,
    ) -> str:
        """
        跨市場圓桌評估，回傳完整 Markdown 報告。
        結構化結果另存於 self.last_picks / self.last_allocation 供 tracker 取用。

        Args:
            tw_candidates     : 台股 ScanCandidate list
            crypto_candidates : CryptoCandidate list
            us_candidates     : USCandidate list
            total_capital     : 總資金（元）
            market_note       : 市場備注
            tw_ctx            : 台股 market context（含 phase 等，用於環境提示+記錄）
            crypto_ctx        : 虛擬幣 market context
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
            self.last_picks, self.last_allocation = [], []
            return "⚠️ 無候選標的，無法進行圓桌評估。"

        logger.info(
            f"圓桌會議啟動：台股 {len(tw_candidates)} 支 + "
            f"虛擬幣 {len(crypto_candidates)} 支 + "
            f"美股 {len(us_candidates)} 支"
        )

        # 改動1a：基本面注入（台股/美股）
        self._enrich_with_fundamentals(universal)

        # 改動1b：歷史環境提示
        context_hint = self._build_context_hint(tw_ctx=tw_ctx, crypto_ctx=crypto_ctx)

        # 建立候選清單 prompt 段落（環境提示置頂）
        candidates_text = self._build_candidates_text(universal, market_note)
        if context_hint:
            candidates_text = context_hint + "\n\n" + candidates_text

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

        # ── 首席策略師合成（結構化 tool-use）────────────────────
        logger.info("  首席策略師綜合分析中（結構化輸出）...")
        synthesis_user = (
            f"{candidates_text}\n\n"
            f"=== 芒格的觀點 ===\n{opinions.get('munger', '')}\n\n"
            f"=== 塔勒布的觀點 ===\n{opinions.get('taleb', '')}\n\n"
            f"=== Naval 的觀點 ===\n{opinions.get('naval', '')}\n\n"
            f"請綜合以上三位顧問的意見，呼叫 submit_recommendations 給出最終投資建議。"
            f"總資金：{total_capital:,.0f} 元"
        )
        structured = self._chat_structured(
            system=CHIEF_STRATEGIST_STRUCTURED_PROMPT.format(capital=f"{total_capital:,.0f}"),
            user=synthesis_user,
            tool=SUBMIT_RECOMMENDATIONS_TOOL,
            max_tokens=1500,
        )

        picks, allocation = self._parse_structured(structured)
        self.last_picks = picks
        self.last_allocation = allocation

        # 結構化失敗時，退回純文字首席輸出（保底）
        fallback_text = ""
        if structured is None:
            logger.warning("  結構化輸出失敗，退回純文字模式")
            fallback_text = self._chat(
                system=CHIEF_STRATEGIST_PROMPT.format(capital=f"{total_capital:,.0f}"),
                user=synthesis_user,
                max_tokens=900,
            )

        # ── 組裝完整報告 ────────────────────────────────────────
        report = self._build_report(
            universal=universal,
            opinions=opinions,
            structured=structured,
            fallback_text=fallback_text,
            total_capital=total_capital,
            market_note=market_note,
        )
        return report

    @staticmethod
    def _parse_structured(structured: Optional[dict]) -> tuple[list[Pick], list[Allocation]]:
        """把 tool-use 回傳的 dict 轉成 Pick / Allocation dataclass list"""
        if not structured:
            return [], []
        picks = []
        for p in structured.get("picks", []) or []:
            try:
                picks.append(Pick(
                    market=p.get("market", ""),
                    asset_id=str(p.get("asset_id", "")),
                    asset_name=p.get("asset_name", ""),
                    action=p.get("action", "WATCH"),
                    confidence=int(p.get("confidence", 50)),
                    entry_logic=p.get("entry_logic", ""),
                    risk=p.get("risk", ""),
                ))
            except Exception:
                continue
        allocation = []
        for a in structured.get("allocation", []) or []:
            try:
                allocation.append(Allocation(
                    market=a.get("market", ""),
                    pct=float(a.get("pct", 0)),
                    amount=float(a.get("amount", 0)),
                    reason=a.get("reason", ""),
                ))
            except Exception:
                continue
        return picks, allocation

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
        universal:     list[UniversalCandidate],
        opinions:      dict[str, str],
        structured:    Optional[dict],
        fallback_text: str,
        total_capital: float,
        market_note:   str,
    ) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

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
        ]

        if structured:
            lines += MultiMarketAdvisor._render_structured(structured)
        else:
            # 結構化失敗時的保底純文字
            lines += [fallback_text or "（未取得首席建議）", ""]

        lines += [
            "---",
            "",
            "> ⚠️ **免責聲明：** 本報告由 AI 模擬多種思維框架生成，僅供學習參考，",
            "> 非正式投資建議。投資前請結合個人財務狀況與風險承受度審慎評估。",
        ]

        return "\n".join(lines)

    @staticmethod
    def _render_structured(s: dict) -> list[str]:
        """把結構化結果渲染為 Markdown 區塊"""
        out: list[str] = []

        # 資金配置表
        alloc = s.get("allocation", []) or []
        if alloc:
            out += [
                "### 市場配置",
                "",
                "| 市場 | 配置 % | 金額（元） | 理由 |",
                "|------|--------|-----------|------|",
            ]
            for a in alloc:
                out.append(
                    f"| {a.get('market','')} | {a.get('pct',0):.0f}% | "
                    f"{a.get('amount',0):,.0f} | {a.get('reason','')} |"
                )
            out.append("")

        # 精選標的表
        picks = s.get("picks", []) or []
        if picks:
            act_icon = {"BUY": "✅BUY", "WATCH": "👀WATCH", "AVOID": "❌AVOID"}
            out += [
                "### 精選標的",
                "",
                "| 優先級 | 市場 | 代號 | 動作 | 信心 | 進場邏輯 | 風險提示 |",
                "|-------|------|------|------|------|---------|---------|",
            ]
            for i, p in enumerate(picks, 1):
                out.append(
                    f"| {i} | {p.get('market','')} | {p.get('asset_id','')} "
                    f"{p.get('asset_name','')} | {act_icon.get(p.get('action',''), p.get('action',''))} "
                    f"| {p.get('confidence',0)} | {p.get('entry_logic','')} | {p.get('risk','')} |"
                )
            out.append("")

        if s.get("market_view"):
            out += ["### 整體市場觀點", "", s["market_view"], ""]
        if s.get("max_risk_warning"):
            out += ["### ⚠️ 最大風險警告", "", s["max_risk_warning"], ""]

        return out


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
    tw_ctx=None,
    crypto_ctx=None,
    enrich_fundamentals: bool = True,
    return_advisor: bool = False,
):
    """
    一鍵執行跨市場圓桌（推薦入口）

    Returns:
        return_advisor=False（預設）：Markdown 報告字串
        return_advisor=True：(報告字串, MultiMarketAdvisor)  ← 供取用 last_picks 做記錄
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        msg = "⚠️ 未設定 ANTHROPIC_API_KEY，無法執行圓桌評估。"
        return (msg, None) if return_advisor else msg

    advisor = MultiMarketAdvisor(api_key=key, enrich_fundamentals=enrich_fundamentals)
    report  = advisor.evaluate(
        tw_candidates=tw_candidates,
        crypto_candidates=crypto_candidates,
        us_candidates=us_candidates,
        total_capital=total_capital,
        market_note=market_note,
        tw_ctx=tw_ctx,
        crypto_ctx=crypto_ctx,
    )

    # 儲存
    out_path = save_path or f"data/reports/roundtable_all_{date.today()}.md"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(report, encoding="utf-8")
    logger.info(f"📄 跨市場圓桌報告：{out_path}")

    return (report, advisor) if return_advisor else report
