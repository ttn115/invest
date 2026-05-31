"""
圓桌投資顧問 (Roundtable Advisor)

透過 Claude API，模擬多位思維框架對候選股票進行二次評估，
產出帶有「多角度觀點」的投資建議報告。

圓桌成員（可自訂）：
    芒格 (Munger)   : 逆向思考、認知偏誤偵測、能力圈
    塔勒布 (Taleb)  : 尾部風險、反脆弱、黑天鵝警告
    Naval           : 非對稱押注、科技視角

使用方式：
    advisor = RoundtableAdvisor(api_key="your_claude_api_key")

    result = advisor.evaluate(
        candidates=scan_result.candidates[:10],
        market_context="台股今日爆天量，記憶體族群全面漲停",
    )
    print(result.report)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from loguru import logger

# ─────────────────────────────────────────────
# 圓桌成員定義
# ─────────────────────────────────────────────

ROUNDTABLE_MEMBERS = {
    "munger": {
        "name": "Charlie Munger（查理·芒格）",
        "role": "逆向思考 + 認知偏誤偵測",
        "prompt": (
            "你是查理·芒格。使用逆向思考，先問「這支股票怎麼會讓我虧錢」。"
            "檢查是否有 Lollapalooza 效應（多重偏誤叠加）。"
            "用極短句，否定句優先，不超過 150 字。"
        ),
    },
    "taleb": {
        "name": "Nassim Taleb（塔勒布）",
        "role": "尾部風險 + 反脆弱",
        "prompt": (
            "你是納西姆·塔勒布。重點評估這支股票的尾部風險和黑天鵝事件。"
            "指出市場可能低估的風險，建議如何做到反脆弱（凸性）。"
            "不超過 150 字。"
        ),
    },
    "naval": {
        "name": "Naval Ravikant（Naval）",
        "role": "非對稱押注 + 科技視角",
        "prompt": (
            "你是 Naval Ravikant。評估這支股票是否有不對稱上行空間。"
            "看護城河、網路效應、技術壟斷。找出別人沒看到的角度。"
            "不超過 150 字。"
        ),
    },
}

# ─────────────────────────────────────────────
# 資料模型
# ─────────────────────────────────────────────

@dataclass
class MemberOpinion:
    """單一圓桌成員的意見"""
    member_id:   str
    member_name: str
    role:        str
    opinion:     str
    verdict:     str   # "BUY" / "WATCH" / "AVOID"
    confidence:  int   # 0~100


@dataclass
class StockEvaluation:
    """單一股票的圓桌評估結果"""
    stock_id:    str
    stock_name:  str
    scan_score:  int                    # 掃描引擎評分
    opinions:    list[MemberOpinion] = field(default_factory=list)
    final_verdict: str  = "WATCH"       # BUY / WATCH / AVOID
    consensus_score: int = 0            # 圓桌共識分數（0~100）
    summary:     str    = ""

    def format_opinions(self) -> str:
        lines = []
        for op in self.opinions:
            icon = {"BUY": "✅", "WATCH": "👀", "AVOID": "❌"}.get(op.verdict, "❓")
            lines.append(
                f"\n### {icon} {op.member_name}（{op.role}）\n{op.opinion}"
            )
        return "\n".join(lines)


@dataclass
class RoundtableReport:
    """圓桌會議完整報告"""
    generated_at:   str
    market_context: str
    evaluations:    list[StockEvaluation] = field(default_factory=list)
    report:         str = ""

    @property
    def buy_list(self) -> list[StockEvaluation]:
        return [e for e in self.evaluations if e.final_verdict == "BUY"]

    @property
    def watch_list(self) -> list[StockEvaluation]:
        return [e for e in self.evaluations if e.final_verdict == "WATCH"]

    @property
    def avoid_list(self) -> list[StockEvaluation]:
        return [e for e in self.evaluations if e.final_verdict == "AVOID"]


# ═══════════════════════════════════════════════════════════════
# Claude API 客戶端
# ═══════════════════════════════════════════════════════════════

class ClaudeClient:
    """輕量 Claude API 封裝（使用 anthropic SDK）"""

    def __init__(self, api_key: str, model: str = "claude-opus-4-5"):
        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_key)
            self.model  = model
            logger.info(f"Claude client initialized: {model}")
        except ImportError:
            logger.warning("anthropic SDK 未安裝，執行: pip install anthropic")
            self.client = None

    def chat(
        self,
        system_prompt: str,
        user_message:  str,
        max_tokens:    int = 512,
        temperature:   float = 0.3,
    ) -> str:
        """
        發送單輪對話，回傳助手文字回覆

        Args:
            system_prompt : 系統提示詞（角色設定）
            user_message  : 使用者訊息
            max_tokens    : 最大回覆 token 數
            temperature   : 溫度（0=確定性，1=創意）
        Returns:
            回覆文字（失敗時回傳空字串）
        """
        if self.client is None:
            return "[Claude API 未設定]"

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return message.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude API 錯誤: {e}")
            return f"[API 錯誤: {e}]"


# ═══════════════════════════════════════════════════════════════
# 圓桌顧問主體
# ═══════════════════════════════════════════════════════════════

class RoundtableAdvisor:
    """
    圓桌投資顧問

    Usage:
        from src.advisor.roundtable_advisor import RoundtableAdvisor

        advisor = RoundtableAdvisor(api_key="sk-ant-...")

        # 評估掃描後的候選名單
        report = advisor.evaluate(
            candidates=scan_result.candidates[:10],
            market_context="台積電尾盤爆量突襲，資金轉進記憶體族群",
        )

        # 查看結果
        print(report.report)
        print(f"建議買進: {[e.stock_id for e in report.buy_list]}")
    """

    def __init__(
        self,
        api_key:  str,
        model:    str = "claude-opus-4-5",
        members:  Optional[dict] = None,
    ):
        """
        Args:
            api_key : Anthropic API Key
            model   : Claude 模型名稱
            members : 自訂圓桌成員（None 使用預設）
        """
        self.claude  = ClaudeClient(api_key=api_key, model=model)
        self.members = members or ROUNDTABLE_MEMBERS

    # ── 主評估入口 ──────────────────────────────────────────────

    def evaluate(
        self,
        candidates,             # list[ScanCandidate]
        market_context: str = "",
        top_n: int = 10,        # 最多評估幾支
    ) -> RoundtableReport:
        """
        對掃描候選名單執行圓桌評估

        Args:
            candidates    : 掃描結果候選股票（ScanCandidate list）
            market_context: 今日市場背景（大盤情況、產業趨勢）
            top_n         : 最多評估幾支（API 費用考量）
        Returns:
            RoundtableReport
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        logger.info(f"圓桌會議啟動：評估 {min(len(candidates), top_n)} 支候選股")

        evaluations = []
        for c in candidates[:top_n]:
            logger.info(f"  評估中：[{c.stock_id}] {c.stock_name}（評分 {c.score}）")
            ev = self._evaluate_stock(c, market_context)
            evaluations.append(ev)

        report = RoundtableReport(
            generated_at=now,
            market_context=market_context,
            evaluations=evaluations,
        )
        report.report = self._generate_report(report)

        logger.info(
            f"圓桌評估完成：買進 {len(report.buy_list)} 支  "
            f"觀察 {len(report.watch_list)} 支  "
            f"迴避 {len(report.avoid_list)} 支"
        )
        return report

    # ── 單一股票評估 ────────────────────────────────────────────

    def _evaluate_stock(self, candidate, market_context: str) -> StockEvaluation:
        """對單一股票執行所有圓桌成員評估"""
        stock_brief = self._build_stock_brief(candidate, market_context)
        opinions = []

        for member_id, member_info in self.members.items():
            opinion_text = self.claude.chat(
                system_prompt=member_info["prompt"],
                user_message=stock_brief,
                max_tokens=300,
                temperature=0.3,
            )
            verdict, confidence = self._parse_verdict(opinion_text)
            opinions.append(MemberOpinion(
                member_id=member_id,
                member_name=member_info["name"],
                role=member_info["role"],
                opinion=opinion_text,
                verdict=verdict,
                confidence=confidence,
            ))

        final_verdict, consensus_score = self._aggregate_verdicts(opinions)
        summary = self._build_stock_summary(candidate, opinions, final_verdict)

        return StockEvaluation(
            stock_id=candidate.stock_id,
            stock_name=candidate.stock_name,
            scan_score=candidate.score,
            opinions=opinions,
            final_verdict=final_verdict,
            consensus_score=consensus_score,
            summary=summary,
        )

    @staticmethod
    def _build_stock_brief(candidate, market_context: str) -> str:
        """建立給圓桌成員閱讀的股票摘要"""
        signals_str   = "、".join(candidate.signals)   if candidate.signals   else "無"
        risk_flags_str = "、".join(candidate.risk_flags) if candidate.risk_flags else "無"

        return f"""
請評估以下股票是否值得投資：

【股票資訊】
代號：{candidate.stock_id}  名稱：{candidate.stock_name}
收盤價：${candidate.close:.1f}  漲跌幅：{candidate.change_pct:+.1f}%
成交量比（今/均）：{candidate.volume_ratio:.1f}x

【法人籌碼】
外資：{candidate.foreign_net:+,}張
投信：{candidate.trust_net:+,}張
自營商：{candidate.dealer_net:+,}張
三大法人合計：{candidate.total_inst:+,}張

【技術面】
RSI：{candidate.rsi:.0f}
站上MA20：{'是' if candidate.above_ma20 else '否'}
站上MA60：{'是' if candidate.above_ma60 else '否'}

【掃描信號】{signals_str}
【風險警示】{risk_flags_str}
【今日市場背景】{market_context or '無'}

請給出你的評估（包含具體理由），並在最後一行明確寫出：
「建議：BUY」「建議：WATCH」或「建議：AVOID」
""".strip()

    @staticmethod
    def _parse_verdict(opinion_text: str) -> tuple[str, int]:
        """從回覆文字中解析最終建議和信心分數"""
        text_upper = opinion_text.upper()

        if "建議：BUY" in opinion_text or "建議:BUY" in opinion_text or "BUY" in text_upper[-50:]:
            verdict = "BUY"
            confidence = 75
        elif "建議：AVOID" in opinion_text or "建議:AVOID" in opinion_text or "AVOID" in text_upper[-50:]:
            verdict = "AVOID"
            confidence = 70
        else:
            verdict = "WATCH"
            confidence = 50

        return verdict, confidence

    @staticmethod
    def _aggregate_verdicts(opinions: list[MemberOpinion]) -> tuple[str, int]:
        """統計所有成員投票，決定最終裁決"""
        if not opinions:
            return "WATCH", 0

        vote_scores = {"BUY": 0, "WATCH": 0, "AVOID": 0}
        for op in opinions:
            vote_scores[op.verdict] = vote_scores.get(op.verdict, 0) + op.confidence

        # AVOID 有否決權：任一成員強烈建議 AVOID，最終設為 WATCH
        avoid_opinions = [op for op in opinions if op.verdict == "AVOID"]
        if len(avoid_opinions) >= 2:
            return "AVOID", 30

        total_score = sum(vote_scores.values())
        consensus_score = int(vote_scores["BUY"] / max(total_score, 1) * 100)

        if consensus_score >= 60:
            return "BUY", consensus_score
        elif consensus_score >= 35:
            return "WATCH", consensus_score
        else:
            return "AVOID", consensus_score

    @staticmethod
    def _build_stock_summary(candidate, opinions: list[MemberOpinion], verdict: str) -> str:
        icon = {"BUY": "✅ 建議買進", "WATCH": "👀 觀察等待", "AVOID": "❌ 建議迴避"}.get(verdict, "❓")
        return (
            f"[{candidate.stock_id}] {candidate.stock_name}  "
            f"評分:{candidate.score}  {icon}  "
            f"外資:{candidate.foreign_net:+,}張  量比:{candidate.volume_ratio:.1f}x"
        )

    # ── 報告生成 ────────────────────────────────────────────────

    def _generate_report(self, report: RoundtableReport) -> str:
        """生成 Markdown 格式的完整投資報告"""
        lines = [
            f"# 🏛️ 圓桌投資報告",
            f"**生成時間：** {report.generated_at}",
            f"**市場背景：** {report.market_context or '無'}",
            "",
            "---",
            "",
            "## 📊 評估摘要",
            "",
            f"| 結論 | 數量 | 標的 |",
            f"|------|------|------|",
            f"| ✅ 建議買進 | {len(report.buy_list)} | "
            f"{', '.join(f'[{e.stock_id}]{e.stock_name}' for e in report.buy_list) or '無'} |",
            f"| 👀 觀察等待 | {len(report.watch_list)} | "
            f"{', '.join(f'[{e.stock_id}]{e.stock_name}' for e in report.watch_list) or '無'} |",
            f"| ❌ 建議迴避 | {len(report.avoid_list)} | "
            f"{', '.join(f'[{e.stock_id}]{e.stock_name}' for e in report.avoid_list) or '無'} |",
            "",
            "---",
            "",
        ]

        # 詳細評估（只展開 BUY + WATCH，AVOID 簡化）
        for section_title, section_list in [
            ("✅ 建議買進", report.buy_list),
            ("👀 觀察等待", report.watch_list),
            ("❌ 建議迴避", report.avoid_list),
        ]:
            if not section_list:
                continue
            lines.append(f"## {section_title}")
            lines.append("")
            for ev in section_list:
                lines.append(f"### [{ev.stock_id}] {ev.stock_name}")
                lines.append(f"- 掃描評分：{ev.scan_score}  共識分數：{ev.consensus_score}")
                lines.append(ev.format_opinions())
                lines.append("")

        lines.append("---")
        lines.append("")
        lines.append(
            "> ⚠️ **免責聲明：** 本報告由 AI 模擬多種思維框架生成，僅供參考，"
            "非投資建議。投資前請結合個人財務狀況與專業顧問意見。"
        )

        return "\n".join(lines)

    def save_report(self, report: RoundtableReport, path: str) -> None:
        """儲存報告為 Markdown 檔案"""
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report.report, encoding="utf-8")
        logger.info(f"📄 圓桌報告已儲存：{path}")


# ═══════════════════════════════════════════════════════════════
# 快速啟動
# ═══════════════════════════════════════════════════════════════

def run_roundtable(
    candidates,
    api_key:        str,
    market_context: str = "",
    top_n:          int = 10,
    save_path:      Optional[str] = None,
) -> RoundtableReport:
    """
    一鍵執行圓桌評估（推薦入口）

    Usage:
        from src.scanner.post_market_scanner import run_post_market_scan
        from src.advisor.roundtable_advisor import run_roundtable

        scan = run_post_market_scan(inst_buy_only=True)
        report = run_roundtable(
            candidates=scan.candidates,
            api_key="sk-ant-...",
            market_context="台股今日爆天量",
        )
        print(report.report)
    """
    advisor = RoundtableAdvisor(api_key=api_key)
    report  = advisor.evaluate(candidates, market_context=market_context, top_n=top_n)

    if save_path:
        advisor.save_report(report, save_path)
    else:
        from pathlib import Path
        from datetime import date
        path = f"data/reports/roundtable_{date.today()}.md"
        advisor.save_report(report, path)

    return report
