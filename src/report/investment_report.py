"""
投資報告生成器 (Investment Report Generator)

整合掃描結果 + 圓桌評估 + 倉位建議，
產出可閱讀的 Markdown / CSV 雙格式報告。

報告結構：
    1. 市場快照（大盤情況）
    2. 掃描摘要（量比/法人排行）
    3. 圓桌評估結論
    4. 具體倉位建議
    5. 風險清單
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger


# ═══════════════════════════════════════════════════════════════
# 報告資料模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketSnapshot:
    """今日市場快照"""
    date:            str
    taiex_close:     float = 0.0
    taiex_change_pct: float = 0.0
    taiex_volume_b:  float = 0.0    # 成交金額（億元）
    is_high_volume:  bool  = False   # 是否爆量（> 5000億）
    sector_leaders:  list  = field(default_factory=list)   # 強勢族群
    market_note:     str   = ""


@dataclass
class PositionAdvice:
    """單一個股倉位建議"""
    stock_id:        str
    stock_name:      str
    action:          str       # BUY / WATCH / AVOID
    entry_range:     str       # 建議進場價格區間（e.g. "850~870"）
    stop_loss:       float     # 停損價
    target_price:    float     # 目標價（選填）
    position_pct:    float     # 建議倉位佔比
    shares:          int       # 建議張數
    max_loss:        float     # 最大預期損失
    reason:          str       # 進場理由（來自圓桌共識）
    risk_note:       str       # 風險提示


@dataclass
class FullReport:
    """完整投資報告"""
    generated_at:    str
    report_date:     str
    market_snapshot: Optional[MarketSnapshot]
    scan_summary:    str
    roundtable_summary: str
    position_advices: list[PositionAdvice] = field(default_factory=list)
    risk_section:    str = ""
    markdown:        str = ""
    csv_data:        Optional[pd.DataFrame] = None


# ═══════════════════════════════════════════════════════════════
# 報告生成器
# ═══════════════════════════════════════════════════════════════

class InvestmentReportGenerator:
    """
    投資報告生成器

    Usage:
        from src.report.investment_report import InvestmentReportGenerator

        generator = InvestmentReportGenerator(total_capital=1_000_000)

        report = generator.generate(
            scan_result=scan_result,
            roundtable_report=roundtable_report,
            market_snapshot=MarketSnapshot(
                date="2026-05-07",
                taiex_close=40705,
                taiex_change_pct=1.5,
                taiex_volume_b=1900,
                is_high_volume=True,
                sector_leaders=["記憶體", "IC封測"],
                market_note="台股爆天量再創新高，記憶體族群全面漲停",
            ),
        )

        # 輸出
        generator.save(report, output_dir="data/reports")
        print(report.markdown)
    """

    def __init__(
        self,
        total_capital: float = 1_000_000,
        win_rate:      float = 0.55,
        avg_win_pct:   float = 0.08,
        avg_loss_pct:  float = 0.04,
    ):
        """
        Args:
            total_capital : 總資金（元）
            win_rate      : 策略歷史勝率（用於倉位計算）
            avg_win_pct   : 平均獲利百分比
            avg_loss_pct  : 平均虧損百分比
        """
        self.total_capital = total_capital
        self.win_rate      = win_rate
        self.avg_win_pct   = avg_win_pct
        self.avg_loss_pct  = avg_loss_pct

        # 懶加載 PositionSizer（避免循環導入）
        self._sizer = None

    def _get_sizer(self):
        if self._sizer is None:
            from src.risk.position_sizer import PositionSizer
            self._sizer = PositionSizer(total_capital=self.total_capital)
        return self._sizer

    # ── 主入口 ──────────────────────────────────────────────────

    def generate(
        self,
        scan_result,                    # ScanResult
        roundtable_report=None,         # RoundtableReport（可選）
        market_snapshot: Optional[MarketSnapshot] = None,
    ) -> FullReport:
        """
        生成完整投資報告

        Args:
            scan_result       : 盤後掃描結果（ScanResult）
            roundtable_report : 圓桌評估結果（可選）
            market_snapshot   : 今日市場快照（可選）
        """
        now       = datetime.now().strftime("%Y-%m-%d %H:%M")
        rep_date  = scan_result.scan_date

        # 1. 掃描摘要
        scan_summary = self._build_scan_summary(scan_result)

        # 2. 圓桌摘要
        roundtable_summary = ""
        if roundtable_report:
            roundtable_summary = self._build_roundtable_summary(roundtable_report)

        # 3. 倉位建議（結合圓桌結果）
        position_advices = self._build_position_advices(
            scan_result, roundtable_report
        )

        # 4. 風險清單
        risk_section = self._build_risk_section(scan_result, market_snapshot)

        # 5. CSV 資料
        csv_data = self._build_csv(scan_result, roundtable_report)

        # 6. 完整 Markdown
        report = FullReport(
            generated_at=now,
            report_date=rep_date,
            market_snapshot=market_snapshot,
            scan_summary=scan_summary,
            roundtable_summary=roundtable_summary,
            position_advices=position_advices,
            risk_section=risk_section,
            csv_data=csv_data,
        )
        report.markdown = self._render_markdown(report)

        logger.info(f"📄 投資報告生成完成：{rep_date}（倉位建議 {len(position_advices)} 支）")
        return report

    # ── 各區段建構 ──────────────────────────────────────────────

    @staticmethod
    def _build_scan_summary(scan_result) -> str:
        if not scan_result.candidates:
            return "今日無符合條件的候選股。"

        top5 = scan_result.top[:5]    # 直接用 ScanCandidate 物件，避免 DataFrame 欄位編碼問題
        lines = [
            f"掃描 **{scan_result.total_stocks}** 支個股，",
            f"篩出 **{len(scan_result.candidates)}** 支候選（依評分排序）：",
            "",
            "| # | 代號 | 名稱 | 評分 | 量比 | 外資 | 投信 | 漲跌 |",
            "|---|------|------|------|------|------|------|------|",
        ]
        for i, c in enumerate(top5, 1):
            lines.append(
                f"| {i} | {c.stock_id} | {c.stock_name} | {c.score} | "
                f"{c.volume_ratio:.1f}x | {c.foreign_net:+,} | {c.trust_net:+,} | "
                f"{c.change_pct:+.1f}% |"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_roundtable_summary(rt_report) -> str:
        lines = [
            f"圓桌評估 **{len(rt_report.evaluations)}** 支候選：",
            "",
            f"- ✅ **建議買進**：{len(rt_report.buy_list)} 支  "
            f"—— {', '.join(f'[{e.stock_id}]{e.stock_name}' for e in rt_report.buy_list) or '無'}",
            f"- 👀 **觀察等待**：{len(rt_report.watch_list)} 支  "
            f"—— {', '.join(f'[{e.stock_id}]' for e in rt_report.watch_list) or '無'}",
            f"- ❌ **建議迴避**：{len(rt_report.avoid_list)} 支  "
            f"—— {', '.join(f'[{e.stock_id}]' for e in rt_report.avoid_list) or '無'}",
        ]
        return "\n".join(lines)

    def _build_position_advices(
        self,
        scan_result,
        rt_report=None,
    ) -> list[PositionAdvice]:
        """對 BUY 標的計算具體倉位建議"""
        sizer = self._get_sizer()
        advices = []

        # 取得 BUY 清單（優先從圓桌，否則用掃描 top5）
        if rt_report and rt_report.buy_list:
            buy_ids = {e.stock_id: e for e in rt_report.buy_list}
        else:
            buy_ids = {}

        candidates_map = {c.stock_id: c for c in scan_result.candidates}

        # 若有圓桌 BUY 清單，用之；否則取掃描前 5 名
        target_ids = list(buy_ids.keys()) or [
            c.stock_id for c in scan_result.top[:5]
        ]

        for stock_id in target_ids:
            candidate = candidates_map.get(stock_id)
            if candidate is None or candidate.close <= 0:
                continue

            # ATR 停損估算（無歷史資料時用 5% 近似）
            stop_loss_pct = 0.05
            stop_price    = round(candidate.close * (1 - stop_loss_pct), 2)

            # 倉位計算
            try:
                pos_result = sizer.calculate(
                    symbol=stock_id,
                    current_price=candidate.close,
                    stop_loss_price=stop_price,
                    win_rate=self.win_rate,
                    avg_win_pct=self.avg_win_pct,
                    avg_loss_pct=self.avg_loss_pct,
                    score=candidate.score,
                )
            except Exception as e:
                logger.warning(f"[{stock_id}] 倉位計算失敗: {e}")
                continue

            # 圓桌理由
            rt_eval = buy_ids.get(stock_id)
            reason  = rt_eval.summary if rt_eval else "掃描信號觸發"
            risk_note = " / ".join(candidate.risk_flags) if candidate.risk_flags else "無特別風險"

            advices.append(PositionAdvice(
                stock_id=stock_id,
                stock_name=candidate.stock_name,
                action="BUY",
                entry_range=f"{candidate.close * 0.99:.1f}~{candidate.close * 1.01:.1f}",
                stop_loss=stop_price,
                target_price=round(candidate.close * (1 + self.avg_win_pct), 1),
                position_pct=pos_result.position_pct,
                shares=pos_result.shares,
                max_loss=pos_result.max_loss,
                reason=reason,
                risk_note=risk_note,
            ))

        return advices

    @staticmethod
    def _build_risk_section(scan_result, market_snapshot=None) -> str:
        risk_items = []

        # 從候選股收集風險旗標
        all_risks = set()
        for c in scan_result.candidates:
            for r in c.risk_flags:
                all_risks.add(r)

        if all_risks:
            risk_items.append("**個股風險：**")
            for r in sorted(all_risks):
                risk_items.append(f"- {r}")

        # 市場層面風險
        if market_snapshot:
            risk_items.append("")
            risk_items.append("**市場風險：**")
            if market_snapshot.is_high_volume:
                risk_items.append("- 市場爆天量，高點爆量須留意主力出貨可能")
            if market_snapshot.taiex_change_pct > 2.0:
                risk_items.append("- 大盤急漲，追高風險增加，等回調再布局")

        # 系統性風險提醒
        risk_items += [
            "",
            "**系統性風險（長期）：**",
            "- Powell 任期交接，聯準會政策不確定性",
            "- 中東地緣政治對油價的連鎖影響",
            "- AI 資本支出回報尚未充分驗證",
        ]

        return "\n".join(risk_items) if risk_items else "無特別風險警示"

    @staticmethod
    def _build_csv(scan_result, rt_report=None) -> pd.DataFrame:
        """建立 CSV 格式資料"""
        df = scan_result.to_dataframe().copy()

        # 加入圓桌結論
        if rt_report:
            verdict_map = {e.stock_id: e.final_verdict for e in rt_report.evaluations}
            consensus_map = {e.stock_id: e.consensus_score for e in rt_report.evaluations}
            df["圓桌結論"] = df["股票代號"].map(verdict_map).fillna("未評估")
            df["共識分數"] = df["股票代號"].map(consensus_map).fillna(0)
        else:
            df["圓桌結論"] = "未評估"
            df["共識分數"] = 0

        return df

    # ── Markdown 渲染 ───────────────────────────────────────────

    def _render_markdown(self, report: FullReport) -> str:
        ms = report.market_snapshot

        lines = [
            f"# 📊 每日投資報告",
            f"**日期：** {report.report_date}  ｜  **生成：** {report.generated_at}",
            "",
        ]

        # 市場快照
        if ms:
            vol_tag = "🔥 爆天量" if ms.is_high_volume else "正常量"
            sectors = "、".join(ms.sector_leaders) if ms.sector_leaders else "無特別強勢"
            lines += [
                "## 🌏 市場快照",
                "",
                f"| 指標 | 數值 |",
                f"|------|------|",
                f"| 加權指數 | {ms.taiex_close:,.0f} ({ms.taiex_change_pct:+.1f}%) |",
                f"| 成交金額 | {ms.taiex_volume_b:,.0f} 億元 {vol_tag} |",
                f"| 強勢族群 | {sectors} |",
                f"| 備註 | {ms.market_note} |",
                "",
                "---",
                "",
            ]

        # 掃描摘要
        lines += [
            "## 🔍 盤後掃描",
            "",
            report.scan_summary,
            "",
            "---",
            "",
        ]

        # 圓桌摘要
        if report.roundtable_summary:
            lines += [
                "## 🏛️ 圓桌評估",
                "",
                report.roundtable_summary,
                "",
                "---",
                "",
            ]

        # 倉位建議
        if report.position_advices:
            lines += [
                "## 💼 倉位建議",
                f"> 總資金：${self.total_capital:,.0f}",
                "",
                "| 代號 | 名稱 | 動作 | 進場區間 | 停損 | 目標 | 倉位 | 張數 | 最大損失 |",
                "|------|------|------|----------|------|------|------|------|----------|",
            ]
            for pa in report.position_advices:
                icon = {"BUY": "✅", "WATCH": "👀", "AVOID": "❌"}.get(pa.action, "")
                lines.append(
                    f"| {pa.stock_id} | {pa.stock_name} | {icon}{pa.action} | "
                    f"{pa.entry_range} | {pa.stop_loss:.1f} | {pa.target_price:.1f} | "
                    f"{pa.position_pct:.1%} | {pa.shares}張 | ${pa.max_loss:,.0f} |"
                )
            lines.append("")

            # 詳細理由
            lines.append("### 進場理由")
            for pa in report.position_advices:
                lines.append(f"- **[{pa.stock_id}] {pa.stock_name}**：{pa.reason}")
                if pa.risk_note and pa.risk_note != "無特別風險":
                    lines.append(f"  > ⚠️ 風險：{pa.risk_note}")
            lines += ["", "---", ""]
        else:
            lines += [
                "## 💼 倉位建議",
                "",
                "> 今日無符合條件的買進建議，建議持盈保泰。",
                "",
                "---",
                "",
            ]

        # 風險清單
        lines += [
            "## ⚠️ 風險清單",
            "",
            report.risk_section,
            "",
            "---",
            "",
            "> **免責聲明：** 本報告由量化掃描 + AI 分析自動生成，僅供研究參考，",
            "> 非投資建議。市場有風險，投資需謹慎。",
        ]

        return "\n".join(lines)

    # ── 儲存 ────────────────────────────────────────────────────

    def save(
        self,
        report: FullReport,
        output_dir: str = "data/reports",
    ) -> dict[str, str]:
        """
        儲存報告（Markdown + CSV）

        Returns:
            dict: {"md": 路徑, "csv": 路徑}
        """
        p = Path(output_dir)
        p.mkdir(parents=True, exist_ok=True)

        md_path  = p / f"report_{report.report_date}.md"
        csv_path = p / f"report_{report.report_date}.csv"

        md_path.write_text(report.markdown, encoding="utf-8")
        logger.info(f"📄 Markdown 報告：{md_path}")

        if report.csv_data is not None and not report.csv_data.empty:
            report.csv_data.to_csv(str(csv_path), encoding="utf-8-sig", index=False)
            logger.info(f"📊 CSV 報告：{csv_path}")

        return {"md": str(md_path), "csv": str(csv_path)}
